"""Buying plans and packs in USD with Stripe Checkout (services/billing_service.py).
Stripe is faked - no request leaves the machine."""

import hashlib
import hmac
import json
import time
import uuid

import pytest

import db
from config import Config
from services import billing_service

WHSEC = "whsec_test"


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def stripe(monkeypatch):
    """Fake Stripe: sessions are created unpaid; tests mark them paid."""
    monkeypatch.setattr(Config, "STRIPE_SECRET_KEY", "sk_test_key")
    monkeypatch.setattr(Config, "STRIPE_WEBHOOK_SECRET", WHSEC)
    state = {"sessions": {}, "created": []}

    class Response:
        ok, status_code = True, 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    def post(url, auth=None, data=None, timeout=None):
        sid = f"cs_test_{uuid.uuid4().hex[:12]}"
        session = {
            "id": sid,
            "url": f"https://checkout.stripe.com/c/pay/{sid}",
            "status": "open",
            "payment_status": "unpaid",
            "amount_total": data["line_items[0][price_data][unit_amount]"],
            "payment_intent": f"pi_{uuid.uuid4().hex[:10]}",
        }
        state["sessions"][sid] = session
        state["created"].append(data)
        return Response(session)

    def get(url, auth=None, timeout=None):
        return Response(state["sessions"][url.rsplit("/", 1)[1]])

    monkeypatch.setattr(billing_service.requests, "post", post)
    monkeypatch.setattr(billing_service.requests, "get", get)

    def pay(sid):
        state["sessions"][sid].update(status="complete", payment_status="paid")

    state["pay"] = pay
    return state


def _user(plan=None):
    user = db.create_user("Sam", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    if plan:
        db.assign_plan(user["id"], plan)
    return user


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def _webhook(client, session, event_type="checkout.session.completed", event_id=None, stamp=None, secret=WHSEC):
    body = json.dumps(
        {"id": event_id or f"evt_{uuid.uuid4().hex[:10]}", "type": event_type, "data": {"object": session}}
    ).encode()
    stamp = str(stamp or int(time.time()))
    sig = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/billing/stripe/webhook",
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": f"t={stamp},v1={sig}"},
    )


def test_usd_prices_have_no_gst():
    priced = billing_service.price_item(_user()["id"], "plan:growth:month", "USD")
    assert (priced["base"], priced["tax"], priced["amount"], priced["currency"]) == (4900, 0, 4900, "USD")
    assert billing_service.price_item(_user()["id"], "pack:video1", "USD")["amount"] == 1699
    assert billing_service.price_item(_user("pro")["id"], "pack:video5", "USD")["amount"] == 6320  # $79 less 20%


def test_users_outside_india_get_stripe(app, stripe):
    options = _login(app, _user()).get("/api/billing/options").get_json()
    assert options["currency"] == "USD" and options["provider"] == "stripe" and options["enabled"] is True
    assert {"item": "plan:starter:month", "amount": 1900} in [
        {"item": i["item"], "amount": i["amount"]} for i in options["items"]
    ]


def test_buying_a_plan_with_stripe(app, stripe):
    user = _user()
    client = _login(app, user)
    res = client.post("/api/billing/stripe/checkout", json={"item": "plan:pro:year", "amount": 1}).get_json()
    assert res["url"].startswith("https://checkout.stripe.com/")
    sent = stripe["created"][0]
    assert sent["line_items[0][price_data][unit_amount]"] == 129000 and sent["metadata[item]"] == "plan:pro:year"
    assert "{CHECKOUT_SESSION_ID}" in sent["success_url"]

    # back from Stripe before paying: nothing applied
    assert client.post("/api/billing/stripe/confirm", json={"session_id": res["session_id"]}).status_code == 400
    stripe["pay"](res["session_id"])
    done = client.post("/api/billing/stripe/confirm", json={"session_id": res["session_id"]}).get_json()
    assert done["usage"]["plan"]["slug"] == "pro" and done["usage"]["paid_until"]
    # the webhook afterwards changes nothing
    assert _webhook(app.test_client(), stripe["sessions"][res["session_id"]]).get_json()["status"] == "already_applied"


def test_webhook_applies_once_and_checks_the_signature(app, stripe):
    user = _user()
    res = _login(app, user).post("/api/billing/stripe/checkout", json={"item": "pack:image10"}).get_json()
    stripe["pay"](res["session_id"])
    session = stripe["sessions"][res["session_id"]]
    outsider = app.test_client()

    assert _webhook(outsider, session, secret="whsec_wrong").status_code == 400
    assert _webhook(outsider, session, stamp=int(time.time()) - 3600).status_code == 400  # replayed old event
    first = _webhook(outsider, session, event_id="evt_same")
    repeat = _webhook(outsider, session, event_id="evt_same")
    assert first.get_json()["status"] == "applied" and repeat.get_json()["status"] == "duplicate"
    assert db.get_plan_usage(user["id"])["images"]["pack_remaining"] == 10


def test_a_tampered_amount_is_not_applied(app, stripe):
    user = _user()
    res = _login(app, user).post("/api/billing/stripe/checkout", json={"item": "pack:image50"}).get_json()
    stripe["pay"](res["session_id"])
    session = dict(stripe["sessions"][res["session_id"]], amount_total=100)
    assert _webhook(app.test_client(), session).get_json()["status"] == "already_applied"
    assert db.get_plan_usage(user["id"])["images"]["pack_remaining"] == 0
    assert any(e["action"] == "payment.amount_mismatch" for e in db.list_audit_log(10, user["id"]))


def test_someone_elses_session_is_refused(app, stripe):
    res = _login(app, _user()).post("/api/billing/stripe/checkout", json={"item": "pack:image10"}).get_json()
    stripe["pay"](res["session_id"])
    assert (
        _login(app, _user()).post("/api/billing/stripe/confirm", json={"session_id": res["session_id"]}).status_code
        == 400
    )


def test_stripe_is_off_without_a_key(app, monkeypatch):
    monkeypatch.setattr(Config, "STRIPE_SECRET_KEY", "")
    client = _login(app, _user())
    assert client.get("/api/billing/options").get_json()["enabled"] is False
    assert client.post("/api/billing/stripe/checkout", json={"item": "pack:image10"}).status_code == 400
