"""Timely nudges and the header bell (services/nudge_service.py). SMTP,
Google and the LLM are faked - nothing is sent or billed."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

import db
from config import Config
from services import nudge_service

MONDAY = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
THURSDAY = MONDAY + timedelta(days=3)
FEED = {"groups": {
    "trending": [{"title": "Explain the new F&O rules", "summary": "A plain explainer.", "format": "image",
                  "platform": "instagram", "prompt": "Create a post about the new F&O rules."}],
    "playbook": [{"title": "Read a chart", "summary": "", "format": "text", "platform": "linkedin", "prompt": "Chart."}],
    "dates": [{"title": "Diwali portfolio review", "summary": "A festive review.", "format": "image", "platform": "facebook",
               "prompt": "Create a Diwali post.", "occasion": "Diwali (2026-11-08, India)"}],
}}


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def world(monkeypatch):
    """No festivals unless a test adds one; the feed and the mail server are faked."""
    from services import festival_service, trend_service
    from services.email_service import EmailService

    state = {"festivals": [], "mails": []}
    monkeypatch.setattr(festival_service, "get_upcoming_festivals", lambda days_ahead=60, today=None: state["festivals"])
    monkeypatch.setattr(trend_service, "generate_idea_feed", lambda user_id, force=False: (FEED, {}))
    monkeypatch.setattr(EmailService, "send_nudge_email", lambda self, **kw: state["mails"].append(kw) or {"success": True})
    monkeypatch.setattr(Config, "NUDGE_EMAILS", False)
    monkeypatch.setattr(Config, "APP_BASE_URL", "https://app.example")
    monkeypatch.setattr(Config, "IDEAS_EMAIL_WEEKDAY", 0)
    return state


def _user(created=MONDAY - timedelta(days=1)):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    with Session(db.engine) as session:
        session.query(db.User).filter(db.User.id == user["id"]).update(
            {"email_verified": True, "onboarding_completed": True, "created_at": created.replace(tzinfo=None)})
        session.add(db.UserBrandProfile(user_id=user["id"], website="https://example.com", company_name="Acme Invest",
                                        industry_category="investment_wealth", compliance_regions=json.dumps(["India"])))
        session.commit()
    return next(u for u in db.users_for_nudges() if u["id"] == user["id"])


def _diwali(days_until):
    return {"name": "Diwali", "date": "2026-11-08", "region": "India", "days_until": days_until}


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def test_an_occasion_a_few_days_away_shows_under_the_bell_once(world):
    user = _user()
    world["festivals"] = [_diwali(3)]

    first = nudge_service.nudge_user(user, MONDAY)
    again = nudge_service.nudge_user(user, MONDAY + timedelta(days=1))  # still 2 days away tomorrow

    assert first["kind"] == "occasion" and first["emailed"] is False and again is None
    bell = db.list_notifications(user["id"])
    assert bell["unread"] == 1 and bell["items"][0]["title"] == "Diwali is in 3 days"
    assert bell["items"][0]["body"] == "Diwali portfolio review" and bell["items"][0]["url"].startswith("/dashboard?idea=")
    token = bell["items"][0]["url"].split("idea=")[1]
    assert db.open_idea_link(token, user["id"])["prompt"] == "Create a Diwali post."


def test_an_occasion_the_feed_has_no_idea_for_still_gets_a_brief(world):
    user = _user()
    world["festivals"] = [{"name": "Onam", "date": "2026-10-15", "region": "India", "days_until": 1}]

    nudge_service.nudge_user(user, MONDAY)

    item = db.list_notifications(user["id"])["items"][0]
    assert item["title"] == "Onam is tomorrow"
    assert "Onam" in db.open_idea_link(item["url"].split("idea=")[1], user["id"])["prompt"]


def test_festivals_in_other_markets_are_ignored(world):
    user = _user()
    world["festivals"] = [{"name": "Thanksgiving", "date": "2026-11-26", "region": "USA", "days_until": 2}]
    assert nudge_service.nudge_user(user, MONDAY) is None


def test_a_quiet_spell_gets_one_reminder(world):
    user = _user(created=MONDAY - timedelta(days=12))  # signed up 12 days ago, never posted

    first = nudge_service.nudge_user(user, MONDAY)
    later = nudge_service.nudge_user(user, MONDAY + timedelta(days=1))

    assert first["kind"] == "inactive" and later is None
    assert db.list_notifications(user["id"])["items"][0]["title"] == "It's been 12 days since your last post"


def test_trending_nudge_comes_mid_week_once(world):
    user = _user()
    assert nudge_service.nudge_user(user, MONDAY) is None  # the weekly email's day
    assert nudge_service.nudge_user(user, THURSDAY)["kind"] == "trend"
    assert nudge_service.nudge_user(user, THURSDAY + timedelta(hours=1)) is None
    assert db.list_notifications(user["id"])["items"][0]["body"] == "Explain the new F&O rules"


def test_nudges_stop_piling_up_when_nobody_reads_them(world):
    user = _user()
    for i in range(5):
        db.create_notification(user["id"], "trend", f"Old {i}", "", "/dashboard", f"old:{i}")
    with Session(db.engine) as session:  # all older than a day
        session.query(db.Notification).filter(db.Notification.user_id == user["id"]).update(
            {"created_at": (THURSDAY - timedelta(days=3)).replace(tzinfo=None)})
        session.commit()
    assert nudge_service.nudge_user(user, THURSDAY) is None


def test_emailed_only_when_switched_on_and_under_the_weekly_cap(world, monkeypatch):
    monkeypatch.setattr(Config, "NUDGE_EMAILS", True)
    user = _user()
    world["festivals"] = [_diwali(3)]

    assert nudge_service.nudge_user(user, MONDAY)["emailed"] is True
    mail = world["mails"][0]
    assert mail["to_email"] == user["email"] and mail["headline"] == "Diwali is in 3 days"
    assert mail["idea"]["url"].startswith("https://app.example/dashboard?idea=")
    assert "/nudges-email/unsubscribe/" in mail["unsubscribe_url"]

    # second idea email this week: allowed (cap is 2) ...
    assert nudge_service.nudge_user(user, THURSDAY)["emailed"] is True
    # ... a third is not: it only shows under the bell
    world["festivals"] = [{"name": "Bhai Dooj", "date": "2026-11-10", "region": "India", "days_until": 2}]
    assert nudge_service.nudge_user(user, THURSDAY + timedelta(days=1))["emailed"] is False
    assert len(world["mails"]) == 2 and db.list_notifications(user["id"])["unread"] == 3


def test_no_email_for_users_who_opted_out_or_were_just_here(world, monkeypatch):
    monkeypatch.setattr(Config, "NUDGE_EMAILS", True)
    world["festivals"] = [_diwali(3)]
    opted_out = _user()
    db.set_nudge_email_enabled(opted_out["id"], False)
    opted_out = next(u for u in db.users_for_nudges() if u["id"] == opted_out["id"])
    busy = _user()
    db.save_run(story="A post", tone="", platforms=["linkedin"], content={}, user_id=busy["id"])

    now = datetime.now(timezone.utc)  # save_run stamps the real time
    assert nudge_service.nudge_user(opted_out, now)["emailed"] is False
    assert nudge_service.nudge_user(busy, now)["emailed"] is False
    assert not world["mails"]


def test_bell_endpoints_list_and_mark_read(app, world):
    user = _user()
    world["festivals"] = [_diwali(3)]
    nudge_service.nudge_user(user, MONDAY)
    client = _login(app, user)

    bell = client.get("/api/me/bell").get_json()
    assert bell["unread"] == 1 and bell["items"][0]["read"] is False

    stranger = _login(app, _user())
    assert stranger.post("/api/me/bell/read", json={"ids": [bell["items"][0]["id"]]}).get_json()["unread"] == 0
    assert client.get("/api/me/bell").get_json()["unread"] == 1  # someone else can't mark it read

    assert client.post("/api/me/bell/read", json={"ids": [bell["items"][0]["id"]]}).get_json()["unread"] == 0
    assert client.get("/api/me/bell").get_json()["items"][0]["read"] is True


def test_nudge_unsubscribe_leaves_the_weekly_email_on(app):
    from services.idea_digest_service import unsubscribe_token

    user = _user()
    client = app.test_client()
    path = f"/api/notifications/nudges-email/unsubscribe/{unsubscribe_token(user['id'], 'nudges')}"

    assert client.post(path).status_code == 200
    settings = db.get_ideas_email_settings(user["id"])
    assert settings["nudges_enabled"] is False and settings["enabled"] is True
    # a weekly-email token is not accepted on the nudge link
    assert client.post(f"/api/notifications/nudges-email/unsubscribe/{unsubscribe_token(user['id'])}").status_code == 400


def test_settings_switch_for_reminder_emails(app):
    user = _user()
    client = _login(app, user)
    res = client.put("/api/me/notifications", json={"nudges_email_enabled": False}).get_json()
    assert res["ideas_email"]["nudges_enabled"] is False and res["ideas_email"]["enabled"] is True
