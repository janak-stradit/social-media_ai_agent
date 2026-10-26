"""GST invoices, credit notes and refunds (services/invoice_service.py,
services/billing_service.py). Stripe is faked."""

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime

import pytest
from sqlalchemy.orm import Session

import db
from config import Config
from services import billing_service, invoice_service

WHSEC = "whsec_test"


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def providers(monkeypatch):
    """Fake Stripe: Checkout sessions are paid at once; refunds succeed."""
    monkeypatch.setattr(Config, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(Config, "STRIPE_WEBHOOK_SECRET", WHSEC)
    monkeypatch.setattr(Config, "INVOICE_SELLER_STATE", "Gujarat")
    calls, sessions = [], {}

    class Response:
        ok, status_code = True, 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    def post(url, auth=None, data=None, timeout=None):
        calls.append((url, data))
        if url.endswith("/checkout/sessions"):
            sid = f"cs_{uuid.uuid4().hex[:12]}"
            sessions[sid] = {
                "id": sid,
                "url": f"https://checkout.stripe.com/{sid}",
                "status": "complete",
                "payment_status": "paid",
                "amount_total": data["line_items[0][price_data][unit_amount]"],
                "payment_intent": f"pi_{sid}",
            }
            return Response(sessions[sid])
        return Response({"id": f"re_{uuid.uuid4().hex[:8]}"})

    def get(url, auth=None, timeout=None):
        return Response(sessions[url.rsplit("/", 1)[1]])

    monkeypatch.setattr(billing_service.requests, "post", post)
    monkeypatch.setattr(billing_service.requests, "get", get)
    return calls


def _user(plan=None, state=None, gstin=None):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    with Session(db.engine) as session:  # a fully set-up account, so its pages open
        session.query(db.User).filter(db.User.id == user["id"]).update(
            {"email_verified": True, "onboarding_completed": True}
        )
        session.add(  # an Indian brand: pays in INR and gets GST invoices
            db.UserBrandProfile(user_id=user["id"], website="https://x.in", compliance_regions=json.dumps(["India"]))
        )
        session.commit()
    if plan:
        db.assign_plan(user["id"], plan)
    if state or gstin:
        db.save_billing_profile(user["id"], "Acme Pvt Ltd", gstin, "1 MG Road", state)
    return user


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def _buy(app, user, item):
    """Stripe Checkout, paid, and back -> (the payment, the applied purchase)."""
    client = _login(app, user)
    session_id = client.post("/api/billing/stripe/checkout", json={"item": item}).get_json()["session_id"]
    res = client.post("/api/billing/stripe/confirm", json={"session_id": session_id})
    return db.get_payment(session_id), res.get_json()


def test_gst_split_by_state(monkeypatch):
    monkeypatch.setattr(Config, "INVOICE_SELLER_STATE", "Gujarat")
    same = invoice_service.split_tax(471882, "Gujarat")
    other = invoice_service.split_tax(471882, "Maharashtra")
    assert (same["taxable"], same["cgst"], same["sgst"], same["igst"]) == (399900, 35991, 35991, 0)
    assert (other["taxable"], other["igst"], other["cgst"]) == (399900, 71982, 0)
    assert invoice_service.split_tax(471882, None)["intra_state"] is True  # no state on record: the seller's state


def test_financial_year_and_gstin_checks():
    assert invoice_service.financial_year(datetime(2027, 3, 31)) == "26-27"
    assert invoice_service.financial_year(datetime(2027, 4, 1)) == "27-28"
    assert invoice_service.valid_gstin("24ABCDE1234F1Z5") and not invoice_service.valid_gstin("99ABCDE1234F1Z5")


def test_every_payment_gets_a_numbered_invoice(app, providers):
    user = _user(state="Maharashtra")
    order, res = _buy(app, user, "plan:growth:month")
    number = res["applied"]["invoice"]
    assert number.startswith("AVIR/") and len(number) <= 16
    invoice = db.get_invoice(number)
    assert (invoice["taxable"], invoice["igst"], invoice["total"]) == (399900, 71982, 471882)
    assert (
        invoice["details"]["buyer"]["name"] == "Acme Pvt Ltd" and invoice["details"]["place_of_supply"] == "Maharashtra"
    )

    _, second = _buy(app, _user(), "pack:image10")
    a, b = int(number.rsplit("/", 1)[1]), int(second["applied"]["invoice"].rsplit("/", 1)[1])
    assert b == a + 1  # consecutive


def test_invoice_page_is_only_for_its_customer_and_admins(app, providers):
    user = _user()
    _, res = _buy(app, user, "pack:image10")
    path = f"/invoices/{res['applied']['invoice']}"
    page = _login(app, user).get(path)
    assert page.status_code == 200 and b"Tax invoice" in page.data and b"CGST 9%" in page.data
    assert _login(app, _user()).get(path).status_code == 404
    admin = db.create_user("Admin", f"{uuid.uuid4().hex[:8]}@example.com", "h", is_admin=True)
    assert _login(app, admin).get(path).status_code == 200


def test_full_refund_takes_back_the_pack_and_issues_a_credit_note(app, providers):
    user = _user()
    order, _ = _buy(app, user, "pack:image10")
    admin = _login(app, db.create_user("Admin", f"{uuid.uuid4().hex[:8]}@example.com", "h", is_admin=True))

    res = admin.post(f"/api/admin/payments/{order['order_id']}/refund", json={}).get_json()
    assert res["status"] == "refunded" and res["refunded"] == order["amount"]
    assert res["credit_note"].startswith("CN/") and res["taken_back"]["pack_units_removed"] == 10
    assert providers[-1][0].endswith("/refunds") and providers[-1][1]["payment_intent"] == order["payment_id"]
    assert db.get_plan_usage(user["id"])["images"]["pack_remaining"] == 0
    note = db.get_invoice(res["credit_note"])
    assert (
        note["kind"] == "credit_note"
        and note["total"] == order["amount"]
        and note["original_number"].startswith("AVIR/")
    )
    # nothing left to refund
    assert admin.post(f"/api/admin/payments/{order['order_id']}/refund", json={}).status_code == 400


def test_partial_refund_keeps_the_purchase(app, providers):
    user = _user()
    order, _ = _buy(app, user, "plan:starter:month")
    admin = _login(app, db.create_user("Admin", f"{uuid.uuid4().hex[:8]}@example.com", "h", is_admin=True))
    res = admin.post(f"/api/admin/payments/{order['order_id']}/refund", json={"amount": 500}).get_json()
    assert res["status"] == "partially_refunded" and res["refunded"] == 50000 and res["taken_back"] == {}
    assert db.get_plan_usage(user["id"])["plan"]["slug"] == "starter"


def test_full_plan_refund_ends_the_paid_time(app, providers):
    user = _user()
    order, _ = _buy(app, user, "plan:growth:month")
    billing_service.refund(order["order_id"], None, actor_user_id=None)
    assert db.get_plan_usage(user["id"])["plan"]["slug"] != "growth"


def test_refund_made_in_stripes_dashboard_syncs_once(app, providers):
    user = _user()
    order, _ = _buy(app, user, "pack:image10")

    def webhook():
        charge = {"id": "ch_1", "payment_intent": order["payment_id"], "amount_refunded": order["amount"]}
        body = json.dumps({"id": f"evt_{uuid.uuid4().hex[:8]}", "type": "charge.refunded", "data": {"object": charge}})
        stamp = str(int(time.time()))
        sig = hmac.new(WHSEC.encode(), f"{stamp}.{body}".encode(), hashlib.sha256).hexdigest()
        return app.test_client().post(
            "/api/billing/stripe/webhook",
            data=body,
            content_type="application/json",
            headers={"Stripe-Signature": f"t={stamp},v1={sig}"},
        )

    assert webhook().get_json()["status"] == "refund_recorded"
    assert webhook().get_json()["status"] == "refund_known"  # a second event for the same refund
    assert len([i for i in db.invoices_for_order(order["order_id"]) if i["kind"] == "credit_note"]) == 1


def test_billing_details_take_the_state_from_the_gstin(app):
    client = _login(app, _user())
    saved = client.put(
        "/api/me/billing-profile", json={"name": "Acme", "gstin": "27abcde1234f1z5", "state": "Goa"}
    ).get_json()
    assert saved["profile"]["gstin"] == "27ABCDE1234F1Z5" and saved["profile"]["state"] == "Maharashtra"
    assert client.put("/api/me/billing-profile", json={"gstin": "NOTAGSTIN"}).status_code == 400
    assert client.put("/api/me/billing-profile", json={"state": "Atlantis"}).status_code == 400
