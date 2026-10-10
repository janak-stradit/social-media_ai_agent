"""The weekly ideas email (services/idea_digest_service.py): who gets it, what
it links to, the unsubscribe link and the settings switch. SMTP, Google and
the LLM are faked - nothing is sent or billed."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

import db
from config import Config
from services import idea_digest_service as digest

FEED = {"groups": {
    "trending": [
        {"title": "Explain the new F&O rules", "summary": "A plain explainer.", "format": "image", "platform": "instagram",
         "prompt": "Create a post about the new F&O rules.", "source_title": "Mint"},
        {"title": "Record SIP inflows", "summary": "What the data means.", "format": "text", "platform": "linkedin",
         "prompt": "Create a post about SIP inflows."},
        {"title": "Third trend", "summary": "", "format": "text", "platform": "linkedin", "prompt": "Third."},
    ],
    "playbook": [{"title": "Read a chart", "summary": "", "format": "video", "platform": "youtube", "prompt": "Chart.",
                  "post_type": "Explainer: one concept"}],
    "dates": [{"title": "Diwali review", "summary": "", "format": "image", "platform": "facebook", "prompt": "Diwali.",
               "occasion": "Diwali (2026-11-08, India)"}],
}}
MONDAY_9AM = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)


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
    monkeypatch.setattr(EmailService, "send_weekly_ideas_email", lambda self, **kw: mails.append(kw) or {"success": True})
    monkeypatch.setattr(trend_service, "generate_idea_feed", lambda user_id, force=False: (FEED, {}))
    monkeypatch.setattr(Config, "WEEKLY_IDEAS_EMAIL", True)
    monkeypatch.setattr(Config, "APP_BASE_URL", "https://app.example")
    monkeypatch.setattr(Config, "IDEAS_EMAIL_WEEKDAY", 0)
    monkeypatch.setattr(Config, "IDEAS_EMAIL_HOUR_UTC", 4)
    # only this test's users: everyone left over from other tests has already been sent one
    with Session(db.engine) as session:
        session.query(db.User).update({"ideas_email_last_sent_at": datetime.now(timezone.utc)})
        session.commit()
    return mails


def _user(verified=True, profile=True):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    with Session(db.engine) as session:
        session.query(db.User).filter(db.User.id == user["id"]).update(
            {"email_verified": verified, "onboarding_completed": True})
        if profile:
            session.add(db.UserBrandProfile(user_id=user["id"], website="https://example.com", company_name="Acme Invest",
                                            industry_category="investment_wealth", compliance_regions=json.dumps(["India"])))
        session.commit()
    return user


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def test_email_has_three_ideas_led_by_what_is_timely(outbox):
    user = _user()

    assert digest.run_weekly_digest(MONDAY_9AM)["sent"] == 1

    mail = outbox[0]
    assert mail["to_email"] == user["email"] and mail["company_name"] == "Acme Invest"
    assert [i["title"] for i in mail["ideas"]] == ["Explain the new F&O rules", "Record SIP inflows", "Diwali review"]
    assert mail["ideas"][0]["origin"] == "In the news · Mint" and mail["ideas"][0]["makes"] == "Image post for Instagram"
    assert mail["ideas"][0]["url"].startswith("https://app.example/dashboard?idea=")
    assert mail["settings_url"] == "https://app.example/settings#notifications"


def test_each_user_gets_it_once_a_week(outbox):
    _user()
    assert digest.run_weekly_digest(MONDAY_9AM)["sent"] == 1
    assert digest.run_weekly_digest(MONDAY_9AM + timedelta(hours=1))["sent"] == 0  # the next hourly check
    assert len(outbox) == 1


def test_nothing_is_sent_when_switched_off_or_on_another_day(outbox, monkeypatch):
    _user()
    assert digest.run_weekly_digest(MONDAY_9AM + timedelta(days=1))["ran"] is False  # Tuesday
    assert digest.run_weekly_digest(MONDAY_9AM.replace(hour=2))["ran"] is False  # before the send hour
    monkeypatch.setattr(Config, "WEEKLY_IDEAS_EMAIL", False)
    assert digest.run_weekly_digest(MONDAY_9AM)["ran"] is False
    assert not outbox


def test_who_is_left_out(outbox):
    _user(verified=False)
    _user(profile=False)
    opted_out = _user()
    db.set_ideas_email_enabled(opted_out["id"], False)
    busy = _user()
    db.save_run(story="A post", tone="", platforms=["linkedin"], content={}, user_id=busy["id"])  # active today

    assert digest.run_weekly_digest(MONDAY_9AM)["sent"] == 0 and not outbox


def test_the_button_loads_the_idea_only_for_its_user(app, outbox):
    user = _user()
    digest.run_weekly_digest(MONDAY_9AM)
    token = outbox[0]["ideas"][0]["url"].split("idea=")[1]

    idea = _login(app, user).get(f"/api/ideas/link/{token}").get_json()["idea"]
    assert idea["prompt"] == "Create a post about the new F&O rules." and idea["platform"] == "instagram"
    assert _login(app, _user()).get(f"/api/ideas/link/{token}").status_code == 404


def test_logged_out_click_keeps_the_idea_until_after_login(app):
    client = app.test_client()
    assert client.get("/dashboard?idea=abc123").status_code == 302  # to the landing page
    user = _user()
    with client.session_transaction() as s:  # they log in
        s["user_id"] = user["id"]
    res = client.get("/dashboard")
    assert res.status_code == 302 and "idea=abc123" in res.headers["Location"]


def test_unsubscribe_link_asks_first_then_switches_it_off(app, outbox):
    user = _user()
    digest.run_weekly_digest(MONDAY_9AM)
    path = outbox[0]["unsubscribe_url"].replace("https://app.example", "")
    client = app.test_client()  # not logged in

    asked = client.get(path)
    assert asked.status_code == 200 and db.get_ideas_email_settings(user["id"])["enabled"] is True  # a scanner's GET changes nothing
    done = client.post(path)
    assert b"unsubscribed" in done.data and db.get_ideas_email_settings(user["id"])["enabled"] is False
    assert client.get("/api/notifications/ideas-email/unsubscribe/not-a-token").status_code == 400


def test_settings_switch_and_send_now(app, outbox):
    user = _user()
    client = _login(app, user)
    assert client.get("/api/me/notifications").get_json()["ideas_email"]["enabled"] is True

    off = client.put("/api/me/notifications", json={"ideas_email_enabled": False}).get_json()
    assert off["ideas_email"]["enabled"] is False

    sent = client.post("/api/me/notifications/ideas-email/send-now").get_json()  # their own request: sent even when off
    assert sent["success"] and sent["ideas"] == 3 and outbox[0]["to_email"] == user["email"]
    assert client.post("/api/me/notifications/ideas-email/send-now").status_code == 429  # not twice in a row


def test_the_email_html_carries_the_links():
    from services.email_service import _build_weekly_ideas_html

    html = _build_weekly_ideas_html(
        "Priya", "Acme Invest",
        [{"title": "Explain <F&O>", "summary": "Plain.", "origin": "In the news", "makes": "Image post for Instagram",
          "url": "https://app.example/dashboard?idea=t1"}],
        "https://app.example/dashboard", "https://app.example/settings#notifications", "https://app.example/u/x")
    assert "1 post ideas for Acme Invest" in html and "Explain &lt;F&amp;O&gt;" in html
    assert "https://app.example/dashboard?idea=t1" in html and "https://app.example/u/x" in html
