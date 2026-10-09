"""The monthly recap email (services/recap_service.py): the month's numbers,
who gets it and when. SMTP, Google and the LLM are faked - nothing is sent."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

import db
from config import Config
from services import recap_service

OCT_1 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
FEED = {"groups": {"trending": [
    {"title": "Explain the new F&O rules", "summary": "A plain explainer.", "format": "image", "platform": "instagram",
     "prompt": "Create a post about the new F&O rules.", "source_title": "Mint"}],
    "playbook": [{"title": "Read a chart", "summary": "", "format": "text", "platform": "linkedin", "prompt": "Chart."}],
    "dates": []}}


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def outbox(monkeypatch):
    from services import trend_service
    from services.email_service import EmailService

    mails = []
    monkeypatch.setattr(EmailService, "send_monthly_recap_email", lambda self, **kw: mails.append(kw) or {"success": True})
    monkeypatch.setattr(trend_service, "generate_idea_feed", lambda user_id, force=False: (FEED, {}))
    monkeypatch.setattr(Config, "MONTHLY_RECAP_EMAIL", True)
    monkeypatch.setattr(Config, "APP_BASE_URL", "https://app.example")
    monkeypatch.setattr(Config, "RECAP_EMAIL_DAY", 1)
    monkeypatch.setattr(Config, "IDEAS_EMAIL_HOUR_UTC", 4)
    with Session(db.engine) as session:  # only this test's users: everyone else already has September's
        session.query(db.User).update({"recap_email_last_month": "2026-09"})
        session.commit()
    return mails


def _user(verified=True):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    with Session(db.engine) as session:
        session.query(db.User).filter(db.User.id == user["id"]).update({"email_verified": verified})
        session.commit()
    return user


def _post(user, day, month=9, platforms=("linkedin",), content=None, year=2026):
    run_id = db.save_run(story="A post", tone="", platforms=list(platforms), content=content or {"linkedin": {"caption": "Hi"}},
                         user_id=user["id"])
    with Session(db.engine) as session:
        session.query(db.RunHistory).filter(db.RunHistory.id == run_id).update({"created_at": datetime(year, month, day, 10, 0)})
        session.commit()
    return run_id


def test_month_stats_count_posts_media_platforms_and_goal_weeks():
    user = _user()
    db.set_weekly_post_goal(user["id"], 2)
    _post(user, 7)                                                         # Mon 7 Sep
    _post(user, 8, platforms=("linkedin", "instagram"),
          content={"linkedin": {"media": {"image": {"url": "/i.png"}}}, "instagram": {}})
    first = _post(user, 16, content={"linkedin": {"media": {"video": {"url": "/v.mp4"}}}})
    _post(user, 17, content={"linkedin": {}, "_meta": {"refined_from_run_id": first}})  # an edit: not a new post
    _post(user, 20, month=8)                                               # August: only the comparison
    _post(user, 2, month=10)                                               # October: not this recap

    stats = recap_service.month_stats(user["id"], 2026, 9)

    assert (stats["posts"], stats["images"], stats["videos"], stats["previous_posts"]) == (3, 1, 1, 1)
    assert stats["platforms"] == {"linkedin": 3, "instagram": 1} and stats["month_name"] == "September"
    assert (stats["weeks"], stats["weeks_goal_met"]) == (4, 1)  # weeks starting 7, 14, 21, 28 Sep; only the first had 2
    assert recap_service.comparison(stats) == "That's 2 more than the month before."


def test_tips_come_from_the_numbers():
    text_only = {"posts": 4, "images": 0, "videos": 0, "platforms": {"linkedin": 4}, "weeks": 4, "weeks_goal_met": 4, "goal": 1}
    assert recap_service.tips(text_only) == [
        "All your posts were text only. Try adding an image to one this month.",
        "Everything went to LinkedIn. The same brief can be written for Instagram or Facebook in one go.",
    ]
    varied = {"posts": 4, "images": 2, "videos": 0, "platforms": {"linkedin": 2, "instagram": 2}, "weeks": 4,
              "weeks_goal_met": 4, "goal": 1}
    assert recap_service.tips(varied) == []


def test_recap_goes_once_to_users_who_posted_last_month(outbox):
    poster = _user()
    _post(poster, 7)
    quiet = _user()       # created nothing in September
    _user(verified=False)

    assert recap_service.run_monthly_recap(OCT_1) == {"sent": 1, "skipped": 1, "failed": 0, "ran": True}
    assert recap_service.run_monthly_recap(OCT_1.replace(hour=10))["sent"] == 0  # the next hourly check

    mail = outbox[0]
    assert len(outbox) == 1 and mail["to_email"] == poster["email"]
    assert mail["stats"]["posts"] == 1 and mail["stats"]["month_name"] == "September"
    assert mail["stats"]["comparison"] == "That's your first full month of posts."
    assert mail["ideas"][0]["url"].startswith("https://app.example/dashboard?idea=") and len(mail["ideas"]) == 2
    assert mail["calendar_url"] == "https://app.example/calendar" and "/recap-email/unsubscribe/" in mail["unsubscribe_url"]
    assert db.get_ideas_email_settings(quiet["id"])["recap_last_month"] == "2026-09"  # not looked at again this month


def test_nothing_is_sent_when_off_or_before_the_send_hour(outbox, monkeypatch):
    _post(_user(), 7)
    assert recap_service.run_monthly_recap(OCT_1.replace(hour=2))["ran"] is False
    monkeypatch.setattr(Config, "MONTHLY_RECAP_EMAIL", False)
    assert recap_service.run_monthly_recap(OCT_1)["ran"] is False
    assert not outbox


def test_opted_out_users_are_skipped_and_the_recap_still_sends_without_ideas(outbox, monkeypatch):
    from services import trend_service

    opted_out = _user()
    _post(opted_out, 7)
    db.set_recap_email_enabled(opted_out["id"], False)
    poster = _user()
    _post(poster, 7)

    def no_feed(user_id, force=False):
        raise RuntimeError("LLM is down")

    monkeypatch.setattr(trend_service, "generate_idea_feed", no_feed)

    assert recap_service.run_monthly_recap(OCT_1)["sent"] == 1
    assert outbox[0]["to_email"] == poster["email"] and outbox[0]["ideas"] == []


def test_settings_switch_send_now_and_unsubscribe(app, outbox):
    from services.idea_digest_service import unsubscribe_token

    user = _user()
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]

    assert client.get("/api/me/notifications").get_json()["ideas_email"]["recap_enabled"] is True
    nothing = client.post("/api/me/notifications/recap-email/send-now")
    assert nothing.status_code == 409  # no posts last month

    year, month = recap_service.previous_month(datetime.now(timezone.utc).date())  # send-now recaps the real last month
    _post(user, 10, month=month, year=year)
    assert client.post("/api/me/notifications/recap-email/send-now").get_json()["posts"] == 1

    off = client.put("/api/me/notifications", json={"recap_email_enabled": False}).get_json()
    assert off["ideas_email"]["recap_enabled"] is False and off["ideas_email"]["enabled"] is True
    db.set_recap_email_enabled(user["id"], True)
    path = f"/api/notifications/recap-email/unsubscribe/{unsubscribe_token(user['id'], 'recap')}"
    assert app.test_client().post(path).status_code == 200
    settings = db.get_ideas_email_settings(user["id"])
    assert settings["recap_enabled"] is False and settings["nudges_enabled"] is True


def test_the_email_html_shows_the_numbers_and_links():
    from services.email_service import _build_monthly_recap_html

    html = _build_monthly_recap_html(
        "Priya", "Acme Invest",
        {"posts": 3, "images": 1, "videos": 0, "published": 2, "platform_names": ["LinkedIn (3)"], "weeks": 4,
         "weeks_goal_met": 1, "month_name": "September", "comparison": "That's 2 more than the month before."},
        ["Try adding an image."],
        [{"title": "Explain <F&O>", "summary": "Plain.", "origin": "In the news", "makes": "Image post for Instagram",
          "url": "https://app.example/dashboard?idea=t1"}],
        "https://app.example/calendar", "https://app.example/settings#notifications", "https://app.example/u/x")
    assert "Your September at Acme Invest" in html and "<strong>3 posts</strong>" in html
    assert "1 of 4" in html and "LinkedIn (3)" in html and "Try adding an image." in html
    assert "Explain &lt;F&amp;O&gt;" in html and "https://app.example/dashboard?idea=t1" in html
