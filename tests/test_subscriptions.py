"""Automatic renewal (services/billing_service.py): Razorpay subscriptions for
INR, Stripe subscriptions for USD. Providers are faked."""

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

KEY_SECRET, RZP_WHSEC, STRIPE_WHSEC = "ks", "rzp_whsec", "whsec_s"


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setattr(Config, "RAZORPAY_KEY_ID", "rzp_test")
    monkeypatch.setattr(Config, "RAZORPAY_KEY_SECRET", KEY_SECRET)
    monkeypatch.setattr(Config, "RAZORPAY_WEBHOOK_SECRET", RZP_WHSEC)
    monkeypatch.setattr(Config, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(Config, "STRIPE_WEBHOOK_SECRET", STRIPE_WHSEC)
    state = {"calls": [], "sessions": {}}

    class Response:
        ok, status_code = True, 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    def post(url, auth=None, json=None, data=None, timeout=None):
        state["calls"].append((url, json or data))
        if url.endswith("/plans"):
            return Response({"id": f"plan_{uuid.uuid4().hex[:8]}"})
        if url.endswith("/subscriptions"):
            return Response({"id": f"sub_{uuid.uuid4().hex[:10]}"})
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


def _rzp_webhook(app, event, sub_id, payment_id=None, amount=None, event_id=None):
    payload = {"subscription": {"entity": {"id": sub_id}}}
    if payment_id:
        payload["payment"] = {"entity": {"id": payment_id, "amount": amount}}
    body = json.dumps({"event": event, "payload": payload}).encode()
    sig = hmac.new(RZP_WHSEC.encode(), body, hashlib.sha256).hexdigest()
    return app.test_client().post(
        "/api/billing/razorpay/webhook",
        data=body,
        content_type="application/json",
        headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id or uuid.uuid4().hex},
    )


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


def test_razorpay_subscription_first_charge_and_renewals(app, providers):
    user = _user(india=True)
    client = _login(app, user)
    started = client.post("/api/billing/subscribe", json={"plan": "growth", "period": "month"}).get_json()
    sub_id = started["subscription_id"]
    assert started["amount"] == 471882  # Rs 3,999 + GST, set by the server
    plan_call = next(c for c in providers["calls"] if c[0].endswith("/plans"))
    assert plan_call[1]["period"] == "monthly" and plan_call[1]["item"]["amount"] == 471882

    sig = hmac.new(KEY_SECRET.encode(), f"pay_1|{sub_id}".encode(), hashlib.sha256).hexdigest()
    res = client.post(
        "/api/billing/subscribe/verify",
        json={"razorpay_subscription_id": sub_id, "razorpay_payment_id": "pay_1", "razorpay_signature": sig},
    ).get_json()
    assert res["usage"]["plan"]["slug"] == "growth"
    first_end = _paid_until(user)
    # the same first charge reported by webhook: applied once
    assert _rzp_webhook(app, "subscription.charged", sub_id, "pay_1", 471882).get_json()["status"] == "already_applied"
    # next month's charge extends the plan by 30 days and gets an invoice
    assert _rzp_webhook(app, "subscription.charged", sub_id, "pay_2", 471882).get_json()["status"] == "applied"
    assert _paid_until(user) - first_end == timedelta(days=30)
    assert len([i for i in db.invoices_for_order("rzp_sub:pay_2") if i["kind"] == "invoice"]) == 1


def test_a_razorpay_plan_is_created_once_per_price(app, providers):
    for _ in range(2):
        user = _user(india=True)
        _login(app, user).post("/api/billing/subscribe", json={"plan": "starter", "period": "year"})
    assert len([c for c in providers["calls"] if c[0].endswith("/plans")]) == 1


def test_a_forged_subscription_signature_is_refused(app, providers):
    user = _user(india=True)
    client = _login(app, user)
    sub_id = client.post("/api/billing/subscribe", json={"plan": "pro", "period": "month"}).get_json()[
        "subscription_id"
    ]
    res = client.post(
        "/api/billing/subscribe/verify",
        json={"razorpay_subscription_id": sub_id, "razorpay_payment_id": "pay_x", "razorpay_signature": "0" * 64},
    )
    assert res.status_code == 400 and db.get_plan_usage(user["id"])["plan"]["slug"] != "pro"


def test_cancel_keeps_the_paid_time_and_stops_renewing(app, providers):
    user = _user(india=True)
    client = _login(app, user)
    sub_id = client.post("/api/billing/subscribe", json={"plan": "growth", "period": "month"}).get_json()[
        "subscription_id"
    ]
    _rzp_webhook(app, "subscription.charged", sub_id, "pay_a", 471882)
    end = _paid_until(user)

    res = client.post("/api/billing/subscription/cancel").get_json()
    assert res["subscription"]["status"] == "cancelled" and res["subscription"]["cancel_at_period_end"] is True
    assert providers["calls"][-1][0].endswith(f"/subscriptions/{sub_id}/cancel")
    assert _paid_until(user) == end and db.get_plan_usage(user["id"])["plan"]["slug"] == "growth"
    assert client.post("/api/billing/subscription/cancel").status_code == 400
    assert any(e["action"] == "subscription.cancelled" for e in db.list_audit_log(10, user["id"]))


def test_one_subscription_at_a_time(app, providers):
    user = _user(india=True)
    client = _login(app, user)
    sub_id = client.post("/api/billing/subscribe", json={"plan": "growth", "period": "month"}).get_json()[
        "subscription_id"
    ]
    _rzp_webhook(app, "subscription.charged", sub_id, "pay_b", 471882)
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
