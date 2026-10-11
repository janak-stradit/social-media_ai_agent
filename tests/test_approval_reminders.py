"""Reminder emails for approval requests still waiting for a decision: one
after a day, a last one after three, only while pending and only when the
switch is on. SMTP is faked - nothing is sent."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import ClassVar

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

import db
from services import approval_reminder_service as reminders

NOW = datetime.now(timezone.utc)


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
        FakeSMTP.sent.append((recipients[0], body))


def _wipe():
    with Session(db.engine) as session:
        session.execute(delete(db.EmailLog))
        session.execute(delete(db.AppSetting).where(db.AppSetting.key.like("email_%")))
        session.commit()


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    """Reminders switched on, SMTP faked. The switch lives in the shared test
    database, so it is wiped before and after."""
    import services.email_service as es
    from config import Config

    _wipe()
    FakeSMTP.sent, FakeSMTP.fail = [], None
    monkeypatch.setattr(es.smtplib, "SMTP", FakeSMTP)
    for name, value in (("SMTP_HOST", "smtp.test"), ("SMTP_USERNAME", "u"), ("SMTP_PASSWORD", "p"),
                        ("SMTP_FROM_EMAIL", "noreply@test"), ("APP_BASE_URL", "https://app.test/"),
                        ("APPROVAL_REMINDER_EMAILS", True)):
        monkeypatch.setattr(Config, name, value)
    yield FakeSMTP
    _wipe()


def _request(age_hours, status="pending", reviewer=True, last_reminder_hours=None, reminders_sent=0):
    """A request created age_hours ago. Returns (id, the reviewer's address)."""
    user = db.create_user("Priya Shah", f"{uuid.uuid4().hex[:10]}@example.com", "h")
    with Session(db.engine) as session:
        session.add(db.UserBrandProfile(user_id=user["id"], website="https://acme.test", company_name="Acme Logistics"))
        session.commit()
    email = f"rev-{uuid.uuid4().hex[:8]}@example.com" if reviewer else None
    req = db.create_approval_request(
        user_id=user["id"], pipeline_client_id=f"studio-{uuid.uuid4().hex[:8]}", platform="multi", asset_type="image",
        reviewer_email=email, items=[
            {"platform": "linkedin", "caption": "Live tracking", "images": [], "media": "none", "decision": "pending"},
            {"platform": "instagram", "caption": "Live tracking", "images": [], "media": "none", "decision": "pending"}])
    with Session(db.engine) as session:
        row = session.get(db.ApprovalRequest, req["id"])
        row.created_at = NOW - timedelta(hours=age_hours)
        row.status = status
        row.reminder_count = reminders_sent
        row.last_reminder_at = NOW - timedelta(hours=last_reminder_hours) if last_reminder_hours is not None else None
        session.commit()
    return req["id"], email


def _mails_to(email):
    return [body for to, body in FakeSMTP.sent if to == email]


def _state(request_id):
    req = db.get_approval_request(request_id)
    return req["reminder_count"], req["last_reminder_at"]


def test_a_request_waiting_a_day_gets_one_reminder():
    request_id, reviewer = _request(age_hours=25)
    fresh_id, fresh_reviewer = _request(age_hours=5)

    result = reminders.run_approval_reminders(NOW)
    assert result["ran"] and result["sent"] >= 1 and result["failed"] == 0
    [body] = _mails_to(reviewer)
    assert "Acme Logistics is waiting for your review" in body and f"https://app.test/approve/{request_id}" in body
    assert "LinkedIn, Instagram" in body and "Priya Shah (Acme Logistics)" in body and "Sent 1 day ago" in body
    assert "one more reminder" in body
    assert _state(request_id)[0] == 1 and _state(request_id)[1]
    assert not _mails_to(fresh_reviewer) and _state(fresh_id) == (0, None)  # under a day: left alone

    reminders.run_approval_reminders(NOW + timedelta(hours=1))  # the next hourly check
    assert len(_mails_to(reviewer)) == 1

    with Session(db.engine) as session:  # in the admin's email history
        log = session.query(db.EmailLog).filter(db.EmailLog.to_email == reviewer).one()
        assert log.kind == "approval_reminder" and log.status == "sent" and log.triggered_by == "scheduler"
        assert f'"request_id": {request_id}' in log.details and '"reminder": 1' in log.details


def test_the_second_reminder_is_the_last():
    request_id, reviewer = _request(age_hours=73, reminders_sent=1, last_reminder_hours=48)
    reminders.run_approval_reminders(NOW)
    [body] = _mails_to(reviewer)
    assert "This is the last reminder" in body and "Sent 3 days ago" in body
    assert _state(request_id)[0] == 2

    with Session(db.engine) as session:  # much later: still no third
        session.get(db.ApprovalRequest, request_id).last_reminder_at = NOW - timedelta(days=5)
        session.commit()
    reminders.run_approval_reminders(NOW)
    assert len(_mails_to(reviewer)) == 1


def test_a_reminder_after_two_days_is_still_the_first():
    request_id, reviewer = _request(age_hours=50)  # reminders were switched on late
    reminders.run_approval_reminders(NOW)
    assert "one more reminder" in _mails_to(reviewer)[0] and _state(request_id)[0] == 1


@pytest.mark.parametrize("kwargs", [
    {"age_hours": 30, "status": "approved"}, {"age_hours": 30, "status": "partial"}, {"age_hours": 30, "status": "rejected"},
    {"age_hours": 24 * 15}, {"age_hours": 30, "reviewer": False}, {"age_hours": 30, "last_reminder_hours": 2},
])
def test_these_requests_get_no_reminder(kwargs):
    request_id, reviewer = _request(**kwargs)
    reminders.run_approval_reminders(NOW)
    assert not _mails_to(reviewer) and _state(request_id)[0] == 0


def test_nothing_is_sent_while_the_switch_is_off(monkeypatch):
    from config import Config

    _, reviewer = _request(age_hours=30)
    monkeypatch.setattr(Config, "APPROVAL_REMINDER_EMAILS", False)
    assert reminders.run_approval_reminders(NOW) == {"sent": 0, "failed": 0, "ran": False}
    assert not _mails_to(reviewer)

    db.set_email_automation({"approval_reminders": True})  # the admin switch overrides .env
    assert db.email_automation_enabled("approval_reminders")
    reminders.run_approval_reminders(NOW)
    assert len(_mails_to(reviewer)) == 1


def test_nothing_is_sent_without_a_public_address_or_smtp(monkeypatch):
    from config import Config

    _, reviewer = _request(age_hours=30)
    monkeypatch.setattr(Config, "APP_BASE_URL", "")
    assert reminders.run_approval_reminders(NOW)["ran"] is False
    monkeypatch.setattr(Config, "APP_BASE_URL", "https://app.test")
    monkeypatch.setattr(Config, "SMTP_HOST", "")
    assert reminders.run_approval_reminders(NOW)["ran"] is False
    assert not _mails_to(reviewer)


def test_a_failed_send_is_tried_again_later_not_every_hour():
    request_id, reviewer = _request(age_hours=30)
    FakeSMTP.fail = RuntimeError("mailbox unavailable")
    assert reminders.run_approval_reminders(NOW)["failed"] >= 1
    count, tried_at = _state(request_id)
    assert count == 0 and tried_at  # tried, not counted

    FakeSMTP.fail = None
    reminders.run_approval_reminders(NOW + timedelta(hours=1))
    assert not _mails_to(reviewer)  # too soon
    reminders.run_approval_reminders(NOW + timedelta(hours=13))
    assert len(_mails_to(reviewer)) == 1 and _state(request_id)[0] == 1
    with Session(db.engine) as session:
        statuses = [r.status for r in session.query(db.EmailLog).filter(db.EmailLog.to_email == reviewer).order_by(db.EmailLog.id)]
    assert statuses == ["failed", "sent"]


def test_one_bad_address_does_not_stop_the_others(monkeypatch):
    from services.email_service import EmailService

    _, bad = _request(age_hours=40)
    _, good = _request(age_hours=30)
    real = EmailService.send_approval_reminder

    def flaky(self, to_email, **kw):
        if to_email == bad:
            raise RuntimeError("rejected")
        return real(self, to_email=to_email, **kw)

    monkeypatch.setattr(EmailService, "send_approval_reminder", flaky)
    result = reminders.run_approval_reminders(NOW)
    assert result["failed"] >= 1 and len(_mails_to(good)) == 1


def test_waiting_text():
    assert reminders.waiting_text((NOW - timedelta(hours=25)).isoformat(), NOW) == "1 day"
    assert reminders.waiting_text((NOW - timedelta(hours=73)).isoformat(), NOW) == "3 days"
    assert reminders.waiting_text(None, NOW) == "1 day"


def test_the_admin_can_switch_reminders():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    admin = db.create_user("Admin", f"{uuid.uuid4().hex[:10]}@example.com", "h", is_admin=True)
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = admin["id"]

    body = client.get("/api/admin/email-automation").get_json()
    assert body["automation"]["approval_reminders"] == {"enabled": True, "source": "env"}
    assert "24 hours" in body["schedule"]["approval_reminders"]
    body = client.put("/api/admin/email-automation", json={"approval_reminders": False}).get_json()
    assert body["automation"]["approval_reminders"] == {"enabled": False, "source": "admin"}
    assert "approval_reminder" in db.EMAIL_KINDS
