"""The weekly results email (services/results_email_service.py): the week's
numbers, who gets it and when, the settings switch, send-now and unsubscribe.
SMTP is faked - nothing is sent."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

import db
from config import Config
from services import results_email_service
from services.email_service import _build_weekly_results_html

MONDAY = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)  # covers Mon 26 Oct - Sun 1 Nov
LAST_WEEK = "2026-W44"


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def outbox(monkeypatch):
    from services.email_service import EmailService

    mails = []
    monkeypatch.setattr(EmailService, "send_weekly_results_email", lambda self, **kw: mails.append(kw) or {"success": True})
    monkeypatch.setattr(Config, "WEEKLY_RESULTS_EMAIL", True)
    monkeypatch.setattr(Config, "APP_BASE_URL", "https://app.example")
    monkeypatch.setattr(Config, "RESULTS_EMAIL_WEEKDAY", 0)
    monkeypatch.setattr(Config, "IDEAS_EMAIL_HOUR_UTC", 4)
    with Session(db.engine) as session:  # only this test's users: everyone else already has this week's
        session.query(db.User).update({"results_email_last_week": LAST_WEEK})
        session.commit()
    return mails


def _user(verified=True):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    with Session(db.engine) as session:
        session.query(db.User).filter(db.User.id == user["id"]).update({"email_verified": verified})
        session.commit()
    return user


def _published(user, when, engagement, platform="instagram", title="A post", score48=None):
    post_id = f"{platform[:2]}_{uuid.uuid4().hex[:8]}"
    db.register_published_post(user["id"], platform, post_id, "https://example.com/p", title, "scheduled", None)
    with Session(db.engine) as session:
        row = session.query(db.PublishedPost).filter(db.PublishedPost.platform_post_id == post_id).one()
        row.published_at = when.astimezone(timezone.utc).replace(tzinfo=None)
        row.metrics = json.dumps({"likes": engagement, "reach": engagement * 10})
        row.engagement = engagement
        row.metrics_at = row.published_at
        if score48 is not None:
            session.add(db.PostMetricSnapshot(post_id=row.id, age_hours=48, metrics="{}", engagement=score48))
        session.commit()
        return row.id


def test_last_week_runs_monday_to_monday():
    start, end, key = results_email_service.last_week(MONDAY)
    assert start == datetime(2026, 10, 26, tzinfo=timezone.utc) and end == datetime(2026, 11, 2, tzinfo=timezone.utc)
    assert key == LAST_WEEK
    assert results_email_service.last_week(MONDAY + timedelta(days=6))[2] == LAST_WEEK  # any day of this week


def test_week_results_best_post_change_and_comments():
    user = _user()
    for n in range(5):  # the usual on Instagram: 100 at 48 hours
        _published(user, MONDAY - timedelta(days=30 + n), 100, score48=100)
    _published(user, MONDAY - timedelta(days=10), 50)  # the week before: 50
    best = _published(user, MONDAY - timedelta(days=5), 200, title="Clinic story", score48=240)
    _published(user, MONDAY - timedelta(days=3), 100, score48=90)
    with Session(db.engine) as session:
        session.add(db.PostComment(user_id=user["id"], post_id=best, platform="instagram", comment_id=f"c_{uuid.uuid4().hex}",
                                   text="Price?", kind="lead"))
        session.commit()
    start, end, _ = results_email_service.last_week(MONDAY)
    r = results_email_service.week_results(user["id"], start, end)
    assert r["posts"] == 2 and r["engagement"] == 300 and r["reach"] == 3000
    assert r["engagement_change"] == "+500% on the week before"
    assert r["best"]["id"] == best and r["best"]["vs_usual"] == 2.4 and r["best"]["platform"] == "Instagram"
    assert r["comments_waiting"] == 1 and r["leads_waiting"] == 1
    assert r["week_label"] == "26 Oct – 1 Nov"


def test_sent_once_a_week_only_with_something_to_report(outbox):
    active, quiet, unverified = _user(), _user(), _user(verified=False)
    for u in (active, quiet, unverified):
        with Session(db.engine) as session:
            session.query(db.User).filter(db.User.id == u["id"]).update({"results_email_last_week": None})
            session.commit()
    _published(active, MONDAY - timedelta(days=4), 80)
    _published(unverified, MONDAY - timedelta(days=4), 80)

    result = results_email_service.run_weekly_results(MONDAY)
    assert result["sent"] == 1 and result["skipped"] == 1  # the quiet user: nothing to report, marked done
    mail = outbox[0]
    assert mail["results"]["posts"] == 1 and mail["inbox_url"] == "https://app.example/inbox"
    assert "/api/notifications/results-email/unsubscribe/" in mail["unsubscribe_url"]
    assert results_email_service.run_weekly_results(MONDAY + timedelta(hours=2))["sent"] == 0  # once a week
    assert db.users_due_results(LAST_WEEK) == []


def test_nothing_is_sent_when_off_on_another_day_or_before_the_hour(outbox, monkeypatch):
    user = _user()
    with Session(db.engine) as session:
        session.query(db.User).filter(db.User.id == user["id"]).update({"results_email_last_week": None})
        session.commit()
    _published(user, MONDAY - timedelta(days=4), 80)
    assert not results_email_service.run_weekly_results(MONDAY.replace(hour=3))["ran"]
    assert not results_email_service.run_weekly_results(MONDAY + timedelta(days=1))["ran"]
    monkeypatch.setattr(Config, "WEEKLY_RESULTS_EMAIL", False)
    assert not results_email_service.run_weekly_results(MONDAY)["ran"] and outbox == []


def test_settings_switch_send_now_and_unsubscribe(app, outbox):
    user = _user()
    _published(user, datetime.now(timezone.utc) - timedelta(days=datetime.now(timezone.utc).weekday() + 3), 60)
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    assert client.get("/api/me/notifications").get_json()["ideas_email"]["results_enabled"] is True
    body = client.post("/api/me/notifications/results-email/send-now").get_json()
    assert body["success"] and body["posts"] == 1 and len(outbox) == 1
    off = client.put("/api/me/notifications", json={"results_email_enabled": False}).get_json()
    assert off["ideas_email"]["results_enabled"] is False
    db.set_results_email_enabled(user["id"], True)

    token = outbox[0]["unsubscribe_url"].rsplit("/", 1)[1]
    anonymous = app.test_client()
    assert b"Stop the weekly results email?" in anonymous.get(f"/api/notifications/results-email/unsubscribe/{token}").data
    anonymous.post(f"/api/notifications/results-email/unsubscribe/{token}")
    assert db.get_ideas_email_settings(user["id"])["results_enabled"] is False
    assert anonymous.post(f"/api/notifications/recap-email/unsubscribe/{token}").status_code == 400  # its own kind only


def test_the_email_html_shows_the_week():
    html = _build_weekly_results_html(
        "Priya", "Acme Clinics",
        {"posts": 3, "reach": 4200, "engagement": 310, "reach_change": "+18% on the week before", "engagement_change": None,
         "followers": 42, "best": {"id": 7, "title": "Clinic <story>", "platform": "Instagram", "vs_usual": 2.4, "engagement": 200},
         "finding": "Carousels get 2.4× the engagement of your other Instagram posts", "comments_waiting": 3,
         "leads_waiting": 1, "week_label": "26 Oct – 1 Nov"},
        "https://app.example/performance", "https://app.example/performance/posts/7", "https://app.example/inbox",
        "https://app.example/settings#notifications", "https://app.example/unsub")
    assert "Your week at Acme Clinics" in html and "26 Oct – 1 Nov" in html
    assert "4,200" in html and "+18% on the week before" in html and "+42" in html
    assert "Clinic &lt;story&gt;" in html and "2.4&times; your usual" in html  # escaped
    assert "3 comments waiting for a reply</strong> (1 possible lead)" in html
    assert "https://app.example/performance/posts/7" in html and "Carousels get 2.4×" in html
