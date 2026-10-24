"""Admin -> Credit Extension Requests: the user is emailed the decision.
SMTP is faked - nothing is sent."""

import uuid

import pytest

import db


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def sent(monkeypatch):
    from services.email_service import EmailService

    from config import Config

    mails = []
    monkeypatch.setattr(EmailService, "send_credit_decision_email", lambda self, **kw: mails.append(kw) or {"success": True})
    monkeypatch.setattr(Config, "APP_BASE_URL", None)  # links follow the request's own host
    return mails


def _client(app, is_admin=False):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash", is_admin=is_admin)
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return user, client


def _request(app, amount=25.0):
    user, client = _client(app)
    created = client.post("/api/credit-requests", json={"requested_amount": amount, "reason": "More posts this month"})
    assert created.get_json()["success"]
    return user, db.get_user_credit_requests(user["id"])[0]["id"]


def test_approving_emails_the_user_the_new_limit(app, sent):
    user, req_id = _request(app)
    before = db.get_user_usage_stats(user["id"])["credit_limit"]
    _, admin = _client(app, is_admin=True)

    res = admin.post(f"/api/admin/credit-requests/{req_id}/approve").get_json()

    assert res["success"] and res["email_sent"] is True
    assert sent == [{
        "to_email": user["email"], "name": "Priya", "approved": True, "requested_amount": 25.0,
        "credit_limit": pytest.approx(before + 25.0), "currency": "USD", "dashboard_url": "http://localhost/dashboard",
    }]


def test_rejecting_emails_the_user_and_leaves_the_limit(app, sent):
    user, req_id = _request(app)
    before = db.get_user_usage_stats(user["id"])["credit_limit"]
    _, admin = _client(app, is_admin=True)

    res = admin.post(f"/api/admin/credit-requests/{req_id}/reject").get_json()

    assert res["success"] and res["email_sent"] is True
    assert sent[0]["approved"] is False and sent[0]["to_email"] == user["email"]
    assert sent[0]["credit_limit"] == pytest.approx(before)
    assert db.get_user_usage_stats(user["id"])["credit_limit"] == pytest.approx(before)


def test_a_mail_failure_does_not_undo_the_approval(app, monkeypatch):
    from services.email_service import EmailService

    def boom(self, **kw):
        raise RuntimeError("SMTP is down")

    monkeypatch.setattr(EmailService, "send_credit_decision_email", boom)
    user, req_id = _request(app)
    _, admin = _client(app, is_admin=True)

    res = admin.post(f"/api/admin/credit-requests/{req_id}/approve").get_json()

    assert res["success"] and res["email_sent"] is False
    assert db.get_user_credit_requests(user["id"])[0]["status"] == "approved"


def test_the_emails_say_what_was_decided():
    from services.email_service import _build_credit_decision_html

    approved = _build_credit_decision_html("Priya", True, 25, 35, "https://app.example/dashboard")
    rejected = _build_credit_decision_html("Priya", False, 25, 10, "https://app.example/dashboard")
    assert "was approved" in approved and "$25.00" in approved and "$35.00" in approved
    assert "not approved" in rejected and "$10.00" in rejected and "https://app.example/dashboard" in rejected


def test_new_accounts_start_with_two_dollars_shown_in_rupees_for_india(app):
    import json

    from sqlalchemy.orm import Session

    from config import Config

    user, client = _client(app)
    usage = client.get("/api/metrics/usage").get_json()
    assert usage["credit_limit"] == Config.DEFAULT_CREDIT_USD == 2.0 and usage["currency"] == "USD"

    with Session(db.engine) as session:  # an Indian brand
        session.add(db.UserBrandProfile(user_id=user["id"], website="https://example.in", compliance_regions=json.dumps(["India"])))
        session.commit()
    usage = client.get("/api/metrics/usage").get_json()
    assert usage["currency"] == "INR" and usage["usd_inr_rate"] == Config.USD_INR_RATE
    assert db.format_money(2.0, "INR") == f"₹{2.0 * Config.USD_INR_RATE:,.2f}"


def test_a_rupee_request_is_stored_in_dollars(app):
    from config import Config

    user, client = _client(app)
    client.post("/api/credit-requests", json={"requested_amount": 950, "currency": "INR", "reason": "Diwali posts"})
    assert db.get_user_credit_requests(user["id"])[0]["requested_amount"] == pytest.approx(950 / Config.USD_INR_RATE, abs=0.01)
