"""Admin -> Emails: every email is logged, the scheduled emails can be switched
on/off, UAE users get their occasions, and an admin can email a segment.
SMTP and the LLM are faked - nothing is sent or billed."""

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import ClassVar

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

import db
from services import email_campaign_service as campaign
from services.email_service import EmailService, email_context


def _wipe():
    with Session(db.engine) as session:
        session.execute(delete(db.EmailLog))
        session.execute(delete(db.AppSetting).where(db.AppSetting.key.like("email_%")))
        session.commit()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """Before and after: the admin switches live in the shared test database,
    and other test files expect the .env defaults."""
    from config import Config

    _wipe()
    for name in ("WEEKLY_IDEAS_EMAIL", "NUDGE_EMAILS", "MONTHLY_RECAP_EMAIL"):
        monkeypatch.setattr(Config, name, False)
    yield
    _wipe()


class FakeSMTP:
    sent: ClassVar[list] = []
    fail: ClassVar[Exception | None] = None

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        if FakeSMTP.fail:
            raise FakeSMTP.fail

    def sendmail(self, sender, recipients, body):
        FakeSMTP.sent.append(recipients)


@pytest.fixture
def smtp(monkeypatch):
    import services.email_service as es
    from config import Config

    FakeSMTP.sent, FakeSMTP.fail = [], None
    monkeypatch.setattr(es.smtplib, "SMTP", FakeSMTP)
    for name, value in (("SMTP_HOST", "smtp.test"), ("SMTP_USERNAME", "u"), ("SMTP_PASSWORD", "p"),
                        ("SMTP_FROM_EMAIL", "noreply@test"), ("APP_BASE_URL", "https://app.test")):
        monkeypatch.setattr(Config, name, value)
    return FakeSMTP


def make_user(industry="retail_ecommerce", regions=("US",), account_type="small", ideas=True, nudges=True,
              is_admin=False):
    user = db.create_user("Pat", f"{uuid.uuid4().hex[:10]}@example.com", "hash", is_admin=is_admin)
    with Session(db.engine) as session:
        row = session.get(db.User, user["id"])
        row.email_verified, row.account_type = True, account_type
        row.ideas_email_enabled = None if ideas else False
        row.nudge_email_enabled = None if nudges else False
        session.add(db.UserBrandProfile(user_id=user["id"], website="https://acme.test", company_name="Acme",
                                        industry="Retail", industry_category=industry,
                                        compliance_regions=json.dumps(list(regions))))
        session.commit()
    return user


# ── Email history ───────────────────────────────────────────────────────────


def test_every_email_is_logged_with_its_context(smtp):
    user = make_user()
    with email_context(kind="festival_idea", details={"occasion": "Diwali"}, triggered_by="admin:1"):
        EmailService().send_password_reset_email(user["email"], "Pat", "https://app.test/reset", 30)
    EmailService().send_welcome_verification_email(user["email"], "Pat", "https://app.test/verify")

    rows = db.list_email_log()["emails"]
    assert [r["kind"] for r in rows] == ["verification", "festival_idea"]  # newest first
    assert rows[1]["details"] == {"occasion": "Diwali"} and rows[1]["triggered_by"] == "admin:1"
    assert rows[1]["user_id"] == user["id"] and rows[1]["status"] == "sent" and rows[1]["subject"]


def test_failed_emails_are_logged_with_the_error_and_still_raise(smtp):
    smtp.fail = RuntimeError("535 authentication failed")
    with pytest.raises(RuntimeError):
        EmailService().send_password_reset_email("x@example.com", "X", "https://app.test/r", 30)
    [row] = db.list_email_log(status="failed")["emails"]
    assert "535" in row["error"] and row["kind"] == "password_reset"
    assert db.email_log_summary()["failed"] == 1


def test_history_filters_and_pages():
    for i in range(7):
        db.log_email(f"user{i}@example.com", "weekly_ideas" if i % 2 else "nudge", f"Subject {i}", "sent")
    db.log_email("bad@example.com", "nudge", "Oops", "failed", error="timeout")
    assert db.list_email_log(kind="nudge")["total"] == 5
    assert db.list_email_log(q="user3")["emails"][0]["to_email"] == "user3@example.com"
    page = db.list_email_log(page=2, page_size=5)
    assert page["total"] == 8 and page["pages"] == 2 and len(page["emails"]) == 3
    tomorrow = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    assert db.list_email_log(date_from=tomorrow)["total"] == 0
    summary = db.email_log_summary()
    assert summary["sent"] == 7 and summary["failed"] == 1 and summary["by_kind"] == {"weekly_ideas": 3, "nudge": 4}


# ── Scheduled-email switches ────────────────────────────────────────────────


def test_switches_default_to_env_and_admin_overrides(monkeypatch):
    from config import Config

    monkeypatch.setattr(Config, "WEEKLY_IDEAS_EMAIL", True)
    auto = db.get_email_automation()
    assert auto["weekly_ideas"] == {"enabled": True, "source": "env"} and not auto["nudge_emails"]["enabled"]

    db.set_email_automation({"weekly_ideas": False, "nudge_emails": True, "bogus": True})
    auto = db.get_email_automation()
    assert auto["weekly_ideas"] == {"enabled": False, "source": "admin"} and db.email_automation_enabled("nudge_emails")


def test_weekly_digest_obeys_the_admin_switch(monkeypatch):
    from services import idea_digest_service as digest

    monkeypatch.setattr(digest, "is_send_time", lambda now=None: True)
    assert digest.run_weekly_digest()["ran"] is False  # off by default here
    db.set_email_automation({"weekly_ideas": True})
    monkeypatch.setattr("db.users_due_ideas_email", lambda **kw: [])
    assert digest.run_weekly_digest()["ran"] is True


# ── Calendar: UAE markets and Islamic festivals ─────────────────────────────


def test_uae_and_islamic_occasions_reach_their_markets():
    from services.festival_service import get_upcoming_festivals

    near_national_day = get_upcoming_festivals(days_ahead=10, today=date(2026, 11, 26))
    assert ("UAE National Day", "UAE") in {(f["name"], f["region"]) for f in near_national_day}
    eid = [f for f in get_upcoming_festivals(days_ahead=5, today=date(2027, 3, 7)) if f["name"] == "Eid al-Fitr"]
    assert {f["region"] for f in eid} == {"UAE", "India"}


def test_uae_users_get_festival_nudges(monkeypatch):
    from services import nudge_service

    monkeypatch.setattr("db.has_notification", lambda uid, key: False)
    monkeypatch.setattr("db.last_activity_at", lambda uid: datetime.now(timezone.utc))
    nudge = nudge_service._due_nudge({"id": 1}, {"compliance_regions": ["UAE/GCC"]},
                                     datetime(2026, 11, 30, 9, tzinfo=timezone.utc))  # Commemoration Day is today
    assert nudge and nudge["kind"] == "occasion" and nudge["festival"]["name"] == "UAE National Day"


# ── Send to a segment ───────────────────────────────────────────────────────


@pytest.fixture
def industry():
    """A per-test industry: users created by other tests stay in the database."""
    return f"test_{uuid.uuid4().hex[:8]}"


def test_segment_respects_filters_opt_outs_and_recent_emails(industry):
    small = make_user(industry=industry, account_type="small")
    make_user(industry=industry, account_type="enterprise")
    make_user(industry="healthcare")
    opted_out = make_user(industry=industry, ideas=False)
    recent = make_user(industry=industry)
    db.log_email(recent["email"], "weekly_ideas", "Ideas", "sent")

    result = campaign.plan("ideas", industry, "small", "US", None)
    assert [u["id"] for u in result["eligible"]] == [small["id"]]
    assert result["skipped"]["opted_out"] == 1 and result["skipped"]["emailed_recently"] == 1
    assert opted_out["id"] not in [u["id"] for u in result["eligible"]]


def test_festival_emails_go_to_that_market_once(industry):
    in_uae = make_user(industry=industry, regions=("UAE/GCC",))
    make_user(industry=industry, regions=("US",))
    had_it = make_user(industry=industry, regions=("UAE/GCC",))
    db.log_email(had_it["email"], "festival_idea", "UAE National Day", "sent", details={"occasion": "UAE National Day"})
    with Session(db.engine) as session:  # older than the 24h rule, so only the occasion rule applies
        session.query(db.EmailLog).update({"created_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)})
        session.commit()
    occasion = {"name": "UAE National Day", "date": "2026-12-02", "region": "UAE"}

    result = campaign.plan("festival", industry, None, None, occasion)
    assert [u["id"] for u in result["eligible"]] == [in_uae["id"]]
    assert result["skipped"]["already_had_this_occasion"] == 1
    assert campaign.plan("festival", industry, None, "US", occasion)["eligible"] == []  # wrong market


def test_send_runs_every_user_and_counts_outcomes(smtp, monkeypatch, industry):
    users = [make_user(industry=industry) for _ in range(3)]
    outcomes = iter(["sent", "skipped", "boom"])

    def fake_send(user, admin_id):
        outcome = next(outcomes)
        if outcome == "boom":
            raise RuntimeError("feed failed")
        return outcome

    monkeypatch.setattr(campaign, "_send_ideas", fake_send)
    job = campaign.start("ideas", admin_id=1, industry=industry, background=False)
    assert job["total"] == 3 and (job["sent"], job["skipped"], job["failed"]) == (1, 1, 1)
    assert campaign.get_job(job["id"])["status"] == "done"
    assert len(users) == 3


def test_send_refuses_without_smtp(monkeypatch):
    from config import Config

    monkeypatch.setattr(Config, "SMTP_HOST", None)
    with pytest.raises(ValueError, match="SMTP"):
        campaign.start("ideas", admin_id=1, background=False)


def test_festival_send_writes_a_branded_email_and_a_bell_item(smtp, monkeypatch, industry):
    from services import nudge_service

    user = make_user(industry=industry, regions=("India",))
    occasion = {"name": "Diwali", "date": (datetime.now(timezone.utc).date() + timedelta(days=4)).isoformat(),
                "region": "India"}
    monkeypatch.setattr(nudge_service, "_idea_for", lambda nudge, uid, profile: {
        "title": "Light up Diwali with Acme", "summary": "A warm greeting.", "format": "image", "platform": "instagram",
        "prompt": "Create a Diwali post.", "group": "dates"})
    [planned] = campaign.plan("festival", industry, None, None, occasion)["eligible"]

    assert campaign._send_festival(planned, occasion, admin_id=9) == "sent"
    [row] = db.list_email_log()["emails"]
    assert row["kind"] == "festival_idea" and row["triggered_by"] == "admin:9" and row["user_id"] == user["id"]
    assert row["details"]["occasion"] == "Diwali" and row["subject"] == "Diwali is in 4 days - AVIR AI"
    assert db.has_notification(user["id"], f"occasion:Diwali:{occasion['date']}")
    assert db.occasion_emailed(user["id"], "Diwali")


# ── Admin API ───────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app.test_client()


def _login(client, user):
    with client.session_transaction() as s:
        s["user_id"] = user["id"]


def test_admin_endpoints_require_an_admin(client):
    _login(client, make_user())
    for method, url in (("get", "/api/admin/emails"), ("get", "/api/admin/email-automation"),
                        ("post", "/api/admin/email-campaign/preview")):
        assert getattr(client, method)(url).status_code == 403


def test_admin_can_read_history_switch_automation_and_preview(client):
    _login(client, make_user(is_admin=True))
    make_user()
    db.log_email("someone@example.com", "nudge", "Diwali is in 3 days", "sent", details={"occasion": "Diwali"})

    history = client.get("/api/admin/emails?kind=nudge").get_json()
    assert history["total"] == 1 and history["summary"]["sent"] == 1 and "nudge" in history["kinds"]

    r = client.put("/api/admin/email-automation", json={"nudge_emails": True}).get_json()
    assert r["automation"]["nudge_emails"] == {"enabled": True, "source": "admin"} and "schedule" in r

    preview = client.post("/api/admin/email-campaign/preview", json={"kind": "ideas", "industry": "retail_ecommerce"}).get_json()
    assert preview["count"] >= 1 and preview["sample"][0]["company"] == "Acme"
    assert client.post("/api/admin/email-campaign/preview", json={"kind": "festival"}).status_code == 400
