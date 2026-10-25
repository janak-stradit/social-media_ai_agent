"""Billing notices (services/billing_notice_service.py): receipts, renewal and
plan-ending reminders, failed renewals - each once, as an email and a bell item."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

import db
from config import Config
from services import billing_notice_service, billing_service


@pytest.fixture
def outbox(monkeypatch):
    from services.email_service import EmailService

    mails = []
    monkeypatch.setattr(EmailService, "send_billing_email", lambda self, **kw: mails.append(kw) or {"success": True})
    monkeypatch.setattr(Config, "APP_BASE_URL", "https://app.example")
    return mails


def _user():
    return db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")


def _ends_in(user, days, slug="growth"):
    with Session(db.engine) as session:
        session.query(db.User).filter(db.User.id == user["id"]).update(
            {
                "plan_slug": slug,
                "plan_paid_until": (datetime.now(timezone.utc) + timedelta(days=days)).replace(tzinfo=None),
            }
        )
        session.commit()


def _bell(user):
    return db.list_notifications(user["id"])["items"]


def test_every_payment_gets_a_receipt_with_its_invoice(outbox):
    user = _user()
    order = f"order_{uuid.uuid4().hex[:8]}"
    db.create_payment(user["id"], "razorpay", order, "pack:image10", "10 images", 153282, 23382, "INR")
    applied = billing_service.fulfil(order, "pay_1", "test")
    assert outbox[0]["to_email"] == user["email"] and "₹1,532.82" in outbox[0]["subject"]
    assert outbox[0]["url"] == f"https://app.example/invoices/{applied['invoice']}"
    assert _bell(user)[0]["kind"] == "billing"


def test_renewal_reminder_three_days_before_once(outbox):
    user = _user()
    _ends_in(user, 2.5)
    sub_id = f"sub_{uuid.uuid4().hex[:8]}"
    db.create_subscription(user["id"], "razorpay", sub_id, "growth", "month", 471882, "INR")
    db.update_subscription(sub_id, status="active")

    billing_notice_service.run_billing_reminders()
    billing_notice_service.run_billing_reminders()  # the next hourly run

    mine = [m for m in outbox if m["to_email"] == user["email"]]
    assert len(mine) == 1 and "renews on" in mine[0]["headline"] and "₹4,718.82" in mine[0]["lines"][0]


def test_plan_ending_reminder_for_plans_paid_once(outbox):
    user = _user()
    _ends_in(user, 4)
    billing_notice_service.run_billing_reminders()
    mine = [m for m in outbox if m["to_email"] == user["email"]]
    assert len(mine) == 1 and "ends on" in mine[0]["headline"] and "Free plan" in mine[0]["lines"][0]


def test_nothing_sent_when_the_end_is_far_away(outbox):
    user = _user()
    _ends_in(user, 20)
    billing_notice_service.run_billing_reminders()
    assert not [m for m in outbox if m["to_email"] == user["email"]]


def test_failed_renewal_is_notified_and_audited(outbox):
    user = _user()
    sub_id = f"sub_{uuid.uuid4().hex[:8]}"
    db.create_subscription(user["id"], "stripe", sub_id, "pro", "month", 12900, "USD")
    billing_service._renewal_failed(sub_id, None)
    billing_service._renewal_failed(sub_id, None)  # the provider retries the same day: one notice
    mine = [m for m in outbox if m["to_email"] == user["email"]]
    assert len(mine) == 1 and mine[0]["headline"] == "Your plan couldn't be renewed"
    assert any(e["action"] == "subscription.payment_failed" for e in db.list_audit_log(10, user["id"]))


def test_a_mail_failure_still_leaves_the_bell_item(monkeypatch):
    from services.email_service import EmailService

    def boom(self, **kw):
        raise RuntimeError("SMTP down")

    monkeypatch.setattr(EmailService, "send_billing_email", boom)
    monkeypatch.setattr(Config, "APP_BASE_URL", "https://app.example")
    user = _user()
    _ends_in(user, 4)
    billing_notice_service.run_billing_reminders()
    assert _bell(user)[0]["title"].startswith("Your Growth plan ends on")
