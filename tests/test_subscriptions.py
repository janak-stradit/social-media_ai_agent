"""Automatic renewal (services/billing_service.py): Stripe subscriptions, in
INR (GST added) for Indian brands and USD for everyone else. Stripe is
faked."""

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

import db
from config import Config
from services import billing_service

STRIPE_WHSEC = "whsec_s"


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setattr(Config, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(Config, "STRIPE_WEBHOOK_SECRET", STRIPE_WHSEC)
    state = {"calls": [], "sessions": {}}

    class Response:
        ok, status_code = True, 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    def post(url, auth=None, data=None, timeout=None):
        state["calls"].append((url, data))
        if url.endswith("/checkout/sessions"):
            sid = f"cs_{uuid.uuid4().hex[:10]}"
            state["sessions"][sid] = {
                "id": sid,
                "url": f"https://checkout.stripe.com/{sid}",
                "mode": data["mode"],
                "status": "complete",
                "payment_status": "paid",
                "subscription": f"sub_{sid}",
                "invoice": f"in_{sid}",
                "amount_total": data["line_items[0][price_data][unit_amount]"],
            }
            return Response(state["sessions"][sid])
        return Response({"id": "ok"})

    def get(url, auth=None, timeout=None):
        return Response(state["sessions"][url.rsplit("/", 1)[1]])

    monkeypatch.setattr(billing_service.requests, "post", post)
    monkeypatch.setattr(billing_service.requests, "get", get)
    return state


def _user(india=False):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    if india:
        with Session(db.engine) as session:
            session.add(
                db.UserBrandProfile(
                    user_id=user["id"], website="https://x.in", compliance_regions=json.dumps(["India"])
                )
            )
            session.commit()
    return user


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def _stripe_webhook(app, event_type, obj):
    body = json.dumps({"id": f"evt_{uuid.uuid4().hex[:8]}", "type": event_type, "data": {"object": obj}}).encode()
    stamp = str(int(time.time()))
    sig = hmac.new(STRIPE_WHSEC.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return app.test_client().post(
        "/api/billing/stripe/webhook",
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": f"t={stamp},v1={sig}"},
    )


def _paid_until(user):
    return datetime.fromisoformat(db.get_plan_usage(user["id"])["paid_until"])


def _subscribe(app, user, plan, period):
    """Start renewal and come back from Stripe paid -> (client, Stripe subscription id)."""
    client = _login(app, user)
    started = client.post("/api/billing/subscribe", json={"plan": plan, "period": period}).get_json()
    client.post("/api/billing/stripe/confirm", json={"session_id": started["session_id"]})
    return client, f"sub_{started['session_id']}"


def test_indian_subscription_is_in_inr_with_gst(app, providers):
    user = _user(india=True)
    client, sub_id = _subscribe(app, user, "growth", "month")
    sent = next(c[1] for c in providers["calls"] if c[0].endswith("/checkout/sessions"))
    assert sent["mode"] == "subscription" and sent["line_items[0][price_data][currency]"] == "inr"
    assert sent["line_items[0][price_data][unit_amount]"] == 471882  # Rs 3,999 + GST, set by the server
    sub = db.current_subscription(user["id"])
    assert (sub["provider"], sub["currency"], sub["amount"]) == ("stripe", "INR", 471882)
    first_end = _paid_until(user)
    # next month's charge extends the plan by 30 days and gets a GST invoice
    renewal = {"id": "in_inr_2", "subscription": sub_id, "amount_paid": 471882, "billing_reason": "subscription_cycle"}
    assert _stripe_webhook(app, "invoice.paid", renewal).get_json()["status"] == "applied"
    assert _paid_until(user) - first_end == timedelta(days=30)
    assert db.get_payment("in_inr_2")["tax"] == 71982
    assert len([i for i in db.invoices_for_order("in_inr_2") if i["kind"] == "invoice"]) == 1


def test_cancel_keeps_the_paid_time_and_stops_renewing(app, providers):
    user = _user(india=True)
    client, sub_id = _subscribe(app, user, "growth", "month")
    end = _paid_until(user)

    res = client.post("/api/billing/subscription/cancel").get_json()
    assert res["subscription"]["status"] == "cancelled" and res["subscription"]["cancel_at_period_end"] is True
    url, data = providers["calls"][-1]
    assert url.endswith(f"/subscriptions/{sub_id}") and data == {"cancel_at_period_end": "true"}
    assert _paid_until(user) == end and db.get_plan_usage(user["id"])["plan"]["slug"] == "growth"
    assert client.post("/api/billing/subscription/cancel").status_code == 400
    assert any(e["action"] == "subscription.cancelled" for e in db.list_audit_log(10, user["id"]))


def test_one_subscription_at_a_time(app, providers):
    client, _ = _subscribe(app, _user(india=True), "growth", "month")
    again = client.post("/api/billing/subscribe", json={"plan": "pro", "period": "month"})
    assert again.status_code == 400 and "already" in again.get_json()["error"]


def test_stripe_subscription_first_period_renewal_and_end(app, providers):
    user = _user()
    client = _login(app, user)
    started = client.post("/api/billing/subscribe", json={"plan": "starter", "period": "month"}).get_json()
    sent = next(c[1] for c in providers["calls"] if c[0].endswith("/checkout/sessions"))
    assert sent["mode"] == "subscription" and sent["line_items[0][price_data][recurring][interval]"] == "month"
    assert sent["line_items[0][price_data][unit_amount]"] == 1900

    done = client.post("/api/billing/stripe/confirm", json={"session_id": started["session_id"]}).get_json()
    assert done["usage"]["plan"]["slug"] == "starter"
    sub_id = f"sub_{started['session_id']}"
    assert db.current_subscription(user["id"])["provider_subscription_id"] == sub_id
    first_end = _paid_until(user)

    # Stripe's own invoice for the first period: not applied twice
    assert (
        _stripe_webhook(
            app,
            "invoice.paid",
            {"id": "in_first", "subscription": sub_id, "amount_paid": 1900, "billing_reason": "subscription_create"},
        ).get_json()["status"]
        == "first_period_via_checkout"
    )
    # a renewal
    assert (
        _stripe_webhook(
            app,
            "invoice.paid",
            {"id": "in_2", "subscription": sub_id, "amount_paid": 1900, "billing_reason": "subscription_cycle"},
        ).get_json()["status"]
        == "applied"
    )
    assert _paid_until(user) - first_end == timedelta(days=30)
    # a renewal for a different amount is not applied
    assert (
        _stripe_webhook(
            app,
            "invoice.paid",
            {"id": "in_3", "subscription": sub_id, "amount_paid": 100, "billing_reason": "subscription_cycle"},
        ).get_json()["status"]
        == "already_applied"
    )
    # the subscription ends at Stripe
    _stripe_webhook(app, "customer.subscription.deleted", {"id": sub_id})
    assert db.get_subscription(sub_id)["status"] == "ended" and db.current_subscription(user["id"]) is None
