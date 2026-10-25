"""Buying plans and packs with Razorpay (services/billing_service.py).
Razorpay is faked - no request leaves the machine."""

import hashlib
import hmac
import json
import uuid

import pytest

import db
from config import Config
from services import billing_service

KEY_SECRET, WEBHOOK_SECRET = "test_key_secret", "test_webhook_secret"


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def razorpay(monkeypatch):
    """Fake Razorpay order creation; records what was sent."""
    monkeypatch.setattr(Config, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(Config, "RAZORPAY_KEY_SECRET", KEY_SECRET)
    monkeypatch.setattr(Config, "RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)
    sent = []

    class Response:
        ok = True
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    def post(url, auth=None, json=None, timeout=None):
        sent.append(json)
        return Response({"id": f"order_{uuid.uuid4().hex[:14]}", **json})

    monkeypatch.setattr(billing_service.requests, "post", post)
    return sent


def _user(plan=None):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    if plan:
        db.assign_plan(user["id"], plan)
    return user


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def _sign(order_id, payment_id):
    return hmac.new(KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def _webhook(client, order_id, payment_id, amount, event="payment.captured", event_id=None, secret=WEBHOOK_SECRET):
    body = json.dumps(
        {"event": event, "payload": {"payment": {"entity": {"id": payment_id, "order_id": order_id, "amount": amount}}}}
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/billing/razorpay/webhook",
        data=body,
        content_type="application/json",
        headers={"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": event_id or uuid.uuid4().hex},
    )


def test_prices_come_from_the_server_with_gst(razorpay):
    user = _user()
    priced = billing_service.price_item(user["id"], "plan:growth:month")
    assert (priced["base"], priced["tax"], priced["amount"]) == (399900, 71982, 471882)  # Rs 3,999 + 18% GST
    assert billing_service.price_item(user["id"], "plan:starter:year")["base"] == 1599000
    with pytest.raises(billing_service.BillingError):
        billing_service.price_item(user["id"], "plan:enterprise:month")  # on request only
    with pytest.raises(billing_service.BillingError):
        billing_service.price_item(_user("free")["id"], "pack:video1")  # Free has no video


def test_pro_gets_its_video_pack_discount():
    priced = billing_service.price_item(_user("pro")["id"], "pack:video1")
    assert priced["base"] == 119920 and "20% plan discount" in priced["description"]  # Rs 1,499 less 20%


def test_buying_a_plan_end_to_end(app, razorpay):
    user = _user()
    client = _login(app, user)
    order = client.post("/api/billing/checkout", json={"item": "plan:growth:month", "amount": 1}).get_json()
    assert razorpay[0]["amount"] == 471882 and order["amount"] == 471882  # the browser's amount is ignored

    res = client.post(
        "/api/billing/verify",
        json={
            "razorpay_order_id": order["order_id"],
            "razorpay_payment_id": "pay_1",
            "razorpay_signature": _sign(order["order_id"], "pay_1"),
        },
    ).get_json()
    assert res["success"] and res["usage"]["plan"]["slug"] == "growth" and res["usage"]["paid_until"]
    # the webhook for the same payment changes nothing
    assert _webhook(client, order["order_id"], "pay_1", 471882).get_json()["status"] == "already_applied"
    assert [e["action"] for e in db.list_audit_log(10, user["id"])].count("payment.received") == 1


def test_a_forged_signature_is_refused(app, razorpay):
    user = _user()
    client = _login(app, user)
    order = client.post("/api/billing/checkout", json={"item": "pack:image10"}).get_json()
    res = client.post(
        "/api/billing/verify",
        json={"razorpay_order_id": order["order_id"], "razorpay_payment_id": "pay_x", "razorpay_signature": "0" * 64},
    )
    assert res.status_code == 400 and db.get_plan_usage(user["id"])["images"]["pack_remaining"] == 0


def test_someone_elses_order_is_refused(app, razorpay):
    order = _login(app, _user()).post("/api/billing/checkout", json={"item": "pack:image10"}).get_json()
    res = _login(app, _user()).post(
        "/api/billing/verify",
        json={
            "razorpay_order_id": order["order_id"],
            "razorpay_payment_id": "p",
            "razorpay_signature": _sign(order["order_id"], "p"),
        },
    )
    assert res.status_code == 404


def test_webhook_applies_a_pack_once_and_checks_everything(app, razorpay):
    user = _user()
    client = _login(app, user)
    order = client.post("/api/billing/checkout", json={"item": "pack:image10"}).get_json()
    outsider = app.test_client()

    assert _webhook(outsider, order["order_id"], "pay_2", order["amount"], secret="wrong").status_code == 400
    assert (
        _webhook(outsider, order["order_id"], "pay_2", order["amount"] - 100).get_json()["status"] == "already_applied"
    )
    assert db.get_plan_usage(user["id"])["images"]["pack_remaining"] == 0  # wrong amount: not applied

    first = _webhook(outsider, order["order_id"], "pay_2", order["amount"], event_id="evt_1")
    repeat = _webhook(outsider, order["order_id"], "pay_2", order["amount"], event_id="evt_1")
    assert first.get_json()["status"] == "applied" and repeat.get_json()["status"] == "duplicate"
    assert db.get_plan_usage(user["id"])["images"]["pack_remaining"] == 10


def test_paid_plans_end_and_renewing_extends_them(razorpay):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import Session

    user = _user()
    first = db.extend_paid_plan(user["id"], "starter", 30)
    again = db.extend_paid_plan(user["id"], "starter", 30)
    assert datetime.fromisoformat(again["paid_until"]) - datetime.fromisoformat(first["paid_until"]) == timedelta(
        days=30
    )
    with Session(db.engine) as session:
        session.query(db.User).filter(db.User.id == user["id"]).update(
            {"plan_paid_until": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)}
        )
        session.commit()
    assert db.get_plan_usage(user["id"])["plan"]["slug"] == "enterprise"  # back to the default (enterprise in tests)


def test_buying_is_off_without_keys(app, monkeypatch):
    monkeypatch.setattr(Config, "RAZORPAY_KEY_ID", "")
    client = _login(app, _user())
    assert client.get("/api/billing/options").get_json()["enabled"] is False
    assert client.post("/api/billing/checkout", json={"item": "pack:image10"}).status_code == 400
