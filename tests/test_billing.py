"""Buying plans and packs in INR (Indian brands, GST added) through Stripe,
the only payment gateway (services/billing_service.py). Stripe is faked - no
request leaves the machine. USD purchases: tests/test_billing_stripe.py."""

import json
import uuid

import pytest
from sqlalchemy.orm import Session

import db
from config import Config
from services import billing_service


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
    state = {"sessions": {}, "created": []}

    class Response:
        ok, status_code = True, 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    def post(url, auth=None, data=None, timeout=None):
        sid = f"cs_test_{uuid.uuid4().hex[:12]}"
        state["sessions"][sid] = {
            "id": sid,
            "url": f"https://checkout.stripe.com/c/pay/{sid}",
            "status": "open",
            "payment_status": "unpaid",
            "amount_total": data["line_items[0][price_data][unit_amount]"],
            "payment_intent": f"pi_{uuid.uuid4().hex[:10]}",
        }
        state["created"].append(data)
        return Response(state["sessions"][sid])

    def get(url, auth=None, timeout=None):
        return Response(state["sessions"][url.rsplit("/", 1)[1]])

    monkeypatch.setattr(billing_service.requests, "post", post)
    monkeypatch.setattr(billing_service.requests, "get", get)
    state["pay"] = lambda sid: state["sessions"][sid].update(status="complete", payment_status="paid")
    return state


def _user(plan=None, india=True):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    if india:  # an Indian brand pays in INR (db.user_currency)
        with Session(db.engine) as session:
            session.add(
                db.UserBrandProfile(user_id=user["id"], website="https://x.in", compliance_regions=json.dumps(["India"]))
            )
            session.commit()
    if plan:
        db.assign_plan(user["id"], plan)
    return user


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def test_prices_come_from_the_server_with_gst():
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


def test_indian_brands_pay_in_inr_through_stripe(app, stripe):
    options = _login(app, _user()).get("/api/billing/options").get_json()
    assert options["currency"] == "INR" and options["provider"] == "stripe" and options["enabled"] is True
    assert options["gst_rate"] == Config.GST_RATE


def test_buying_a_plan_end_to_end(app, stripe):
    user = _user()
    client = _login(app, user)
    res = client.post("/api/billing/stripe/checkout", json={"item": "plan:growth:month", "amount": 1}).get_json()
    sent = stripe["created"][0]
    # the browser's amount is ignored: Rs 3,999 + GST, in rupees
    assert sent["line_items[0][price_data][currency]"] == "inr"
    assert sent["line_items[0][price_data][unit_amount]"] == 471882
    assert "incl. 18% GST" in sent["line_items[0][price_data][product_data][name]"]
    payment = db.get_payment(res["session_id"])
    assert (payment["provider"], payment["currency"], payment["tax"]) == ("stripe", "INR", 71982)

    stripe["pay"](res["session_id"])
    done = client.post("/api/billing/stripe/confirm", json={"session_id": res["session_id"]}).get_json()
    assert done["usage"]["plan"]["slug"] == "growth" and done["usage"]["paid_until"]
    assert [e["action"] for e in db.list_audit_log(10, user["id"])].count("payment.received") == 1


def test_razorpay_is_gone(app, stripe):
    client = _login(app, _user())
    for path in ("/api/billing/checkout", "/api/billing/verify", "/api/billing/razorpay/webhook",
                 "/api/billing/subscribe/verify"):
        assert client.post(path, json={}).status_code == 404, path
    assert not hasattr(Config, "RAZORPAY_KEY_ID")


def test_paid_plans_end_and_renewing_extends_them():
    from datetime import datetime, timedelta, timezone

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


def test_buying_is_off_without_the_stripe_key(app, monkeypatch):
    monkeypatch.setattr(Config, "STRIPE_SECRET_KEY", "")
    client = _login(app, _user())
    assert client.get("/api/billing/options").get_json()["enabled"] is False
    assert client.post("/api/billing/stripe/checkout", json={"item": "pack:image10"}).status_code == 400
