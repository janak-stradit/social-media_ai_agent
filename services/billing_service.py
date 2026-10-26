"""Buying plans and top-up packs, for every country, through Stripe.

Indian brands pay in INR with GST added (db.user_currency); everyone else
pays in USD without it.

How a purchase works
  1. The browser asks for an item ("plan:growth:month", "pack:image10").
     The server looks up its price (plans table / PACKS below), adds GST for
     INR and creates a Stripe Checkout Session at that amount. The browser
     never sends an amount.
  2. Stripe's own page takes the payment; card details never reach this
     server.
  3. When Stripe sends the browser back, the server asks Stripe directly
     whether the session was paid (confirm_stripe_session).
  4. Stripe's signed webhook reports the same payment server-to-server, in
     case the browser closed before step 3.
Each payment is applied once (db.claim_payment_for_fulfilment), whichever of
steps 3 and 4 arrives first, and recorded in the audit log.
"""

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone

import requests

from config import Config

logger = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com/v1"
STRIPE_SIGNATURE_TOLERANCE = 5 * 60  # seconds; older webhook signatures are refused (replays)
PERIOD_DAYS = {"month": 30, "year": 365}

# Top-up packs (PRICING.md): what each gives and its price before GST
PACKS = {
    "image10": {"kind": "image", "quantity": 10, "name": "10 images", "price_inr": 1299, "price_usd": 15},
    "image50": {"kind": "image", "quantity": 50, "name": "50 images", "price_inr": 5999, "price_usd": 69},
    "video1": {"kind": "video", "quantity": 1, "name": "1 video", "price_inr": 1499, "price_usd": 16.99},
    "video5": {"kind": "video", "quantity": 5, "name": "5 videos", "price_inr": 6999, "price_usd": 79},
}


class BillingError(Exception):
    """A purchase that can't go ahead; the message is safe to show the user."""


def enabled() -> bool:
    return bool(Config.STRIPE_SECRET_KEY)


def _paise(amount: float) -> int:
    """Rupees to paise, or dollars to cents."""
    return int(round(float(amount) * 100))


def price_item(user_id: int, item: str, currency: str = "INR") -> dict:
    """What an item costs this user, from the server's own prices:
    {item, description, base, tax, amount, currency} in paise / cents. GST
    only on INR. Raises BillingError."""
    from db import get_plan_usage, list_plans

    parts = item.split(":")
    if len(parts) == 3 and parts[0] == "plan" and parts[2] in PERIOD_DAYS:
        plan = next((p for p in list_plans() if p["slug"] == parts[1]), None)
        field = ("price" if parts[2] == "month" else "annual") + ("_usd" if currency == "USD" else "_inr")
        price = (plan or {}).get(field)
        if not plan or not price or price <= 0:
            raise BillingError("This plan can't be bought online. Contact us for it.")
        description = f"{plan['name']} plan, 1 {parts[2]}"
        base = _paise(price)
    elif len(parts) == 2 and parts[0] == "pack" and parts[1] in PACKS:
        pack = PACKS[parts[1]]
        usage = get_plan_usage(user_id) or {}
        plan = usage.get("plan") or {}
        if pack["kind"] == "video" and not (usage.get("features") or {}).get("video"):
            raise BillingError("Your plan doesn't include video. Upgrade your plan first.")
        discount = (plan.get("video_pack_discount") or 0) if pack["kind"] == "video" else 0
        base = _paise(pack["price_usd" if currency == "USD" else "price_inr"] * (100 - discount) / 100)
        description = pack["name"] + (f" ({discount}% plan discount)" if discount else "")
    else:
        raise BillingError("Unknown item.")
    tax = int(round(base * Config.GST_RATE)) if currency == "INR" else 0
    return {
        "item": item,
        "description": description,
        "base": base,
        "tax": tax,
        "amount": base + tax,
        "currency": currency,
    }


def fulfil(order_id: str, payment_id: str | None, source: str, paid_amount: int | None = None) -> dict | None:
    """Apply a paid order (once): extend the plan or add the pack. Returns
    what was applied, or None when it was already applied or is unknown."""
    from db import claim_payment_for_fulfilment, extend_paid_plan, get_payment, grant_pack, log_audit

    payment = get_payment(order_id)
    if payment is None:
        logger.warning(f"Payment for unknown order {order_id} ({source})")
        return None
    if paid_amount is not None and int(paid_amount) != int(payment["amount"]):
        # Stripe reports a different amount than the order was created for: don't apply it
        logger.error(f"SECURITY: order {order_id} paid {paid_amount}, expected {payment['amount']} - not applied")
        log_audit(
            "payment.amount_mismatch",
            target_user_id=payment["user_id"],
            details={"order_id": order_id, "paid": paid_amount, "expected": payment["amount"]},
        )
        return None
    claimed = claim_payment_for_fulfilment(order_id, payment_id)
    if claimed is None:
        return None
    parts = claimed["item"].split(":")
    if parts[0] == "plan":
        applied = extend_paid_plan(claimed["user_id"], parts[1], PERIOD_DAYS[parts[2]])
    else:
        pack = PACKS[parts[1]]
        applied = grant_pack(
            claimed["user_id"], pack["kind"], pack["quantity"], f"Bought online ({payment_id})", order_id
        )
    log_audit(
        "payment.received",
        target_user_id=claimed["user_id"],
        details={
            "item": claimed["item"],
            "amount": claimed["amount"],
            "currency": claimed["currency"],
            "order_id": order_id,
            "payment_id": payment_id,
            "via": source,
        },
    )
    invoice = None
    try:
        from services.invoice_service import issue_invoice

        invoice = issue_invoice(claimed)
    except Exception as err:  # noqa: BLE001 - the purchase stands; the invoice can be issued again later
        logger.error(f"Invoice for order {order_id} failed: {err}")
    try:
        from services.billing_notice_service import payment_receipt

        payment_receipt(claimed, invoice["number"] if invoice else None)
    except Exception as err:  # noqa: BLE001
        logger.warning(f"Receipt for order {order_id} failed: {err}")
    return {
        "item": claimed["item"],
        "description": claimed["description"],
        "applied": applied,
        "invoice": invoice["number"] if invoice else None,
    }


# ── Refunds ───────────────────────────────────────────────────────────────


def apply_refund(order_id: str, refunded_total: int, source: str, actor_user_id: int | None = None) -> dict | None:
    """Record that `refunded_total` (paise / cents, the order's running total)
    has been refunded: a credit note for the new part, and - once fully
    refunded - take back what was bought. Safe to call repeatedly."""
    from db import log_audit, record_refund, revoke_purchase
    from services.invoice_service import issue_credit_note

    payment = record_refund(order_id, refunded_total)
    if payment is None or not payment["newly_refunded"]:
        return None
    credit_note = issue_credit_note(payment, payment["newly_refunded"])
    revoked = {}
    if payment["refunded_amount"] >= payment["amount"]:
        parts = payment["item"].split(":")
        revoked = revoke_purchase(
            order_id, payment["item"], payment["user_id"], PERIOD_DAYS.get(parts[2]) if parts[0] == "plan" else None
        )
    log_audit(
        "payment.refunded",
        actor_user_id=actor_user_id,
        target_user_id=payment["user_id"],
        details={
            "order_id": order_id,
            "refunded": payment["newly_refunded"],
            "refunded_total": payment["refunded_amount"],
            "currency": payment["currency"],
            "credit_note": credit_note["number"] if credit_note else None,
            "taken_back": revoked,
            "via": source,
        },
    )
    return {
        "refunded": payment["newly_refunded"],
        "refunded_total": payment["refunded_amount"],
        "status": payment["status"],
        "credit_note": credit_note["number"] if credit_note else None,
        "taken_back": revoked,
    }


def refund(order_id: str, amount: int | None, actor_user_id: int) -> dict:
    """Admin: refund an order (all of it when amount is None) through
    Stripe, then record it. Raises BillingError."""
    from db import get_payment

    payment = get_payment(order_id)
    if not payment or payment["status"] not in ("paid", "partially_refunded") or not payment["payment_id"]:
        raise BillingError("Only a paid payment can be refunded.")
    left = payment["amount"] - (payment["refunded_amount"] or 0)
    amount = left if amount is None else int(amount)
    if not 0 < amount <= left:
        raise BillingError(f"The refund must be between 1 and {left} (smallest currency unit).")
    response = requests.post(
        f"{STRIPE_API}/refunds",
        auth=(Config.STRIPE_SECRET_KEY, ""),
        data={"payment_intent": payment["payment_id"], "amount": amount, "metadata[order_id]": order_id},
        timeout=20,
    )
    if not response.ok:
        logger.warning(f"Refund failed for {order_id}: {response.status_code} {response.text[:200]}")
        raise BillingError("Stripe refused the refund. Check its dashboard for details.")
    result = apply_refund(order_id, (payment["refunded_amount"] or 0) + amount, "admin", actor_user_id)
    return result or {"refunded": 0}


# ── Stripe Checkout ──────────────────────────────────────────────────────────


def create_stripe_session(user_id: int, item: str, base_url: str, currency: str = "USD") -> dict:
    """A Stripe Checkout Session at the server's price in the user's currency
    (INR includes GST). Returns {url} to send the browser to."""
    from db import create_payment, get_user_by_id

    if not enabled():
        raise BillingError("Online payment isn't available yet.")
    priced = price_item(user_id, item, currency)
    user = get_user_by_id(user_id)
    base = base_url.rstrip("/")
    response = requests.post(
        f"{STRIPE_API}/checkout/sessions",
        auth=(Config.STRIPE_SECRET_KEY, ""),
        data={
            "mode": "payment",
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": currency.lower(),
            "line_items[0][price_data][unit_amount]": priced["amount"],
            "line_items[0][price_data][product_data][name]": _product_name(priced["description"], priced["tax"]),
            "client_reference_id": str(user_id),
            "metadata[user_id]": str(user_id),
            "metadata[item]": item,
            "customer_email": user.email if user else None,
            "success_url": f"{base}/settings?checkout=success&session_id={{CHECKOUT_SESSION_ID}}#plan",
            "cancel_url": f"{base}/settings?checkout=cancelled#plan",
        },
        timeout=20,
    )
    if not response.ok:
        logger.warning(f"Stripe session failed: {response.status_code} {response.text[:200]}")
        raise BillingError("Payment could not be started. Please try again.")
    session = response.json()
    create_payment(
        user_id, "stripe", session["id"], item, priced["description"], priced["amount"], priced["tax"], currency
    )
    return {"url": session["url"], "session_id": session["id"]}


def _product_name(description: str, tax: int) -> str:
    """What Stripe's page shows; GST is already in the INR amount."""
    return f"AVIR AI - {description}" + (f" (incl. {round(Config.GST_RATE * 100)}% GST)" if tax else "")


def _stripe_session_paid(session: dict) -> bool:
    return session.get("payment_status") == "paid" and session.get("status") == "complete"


def confirm_stripe_session(user_id: int, session_id: str) -> dict | None:
    """After Stripe sends the browser back: ask Stripe (server to server)
    whether the session was paid, then apply it. Raises BillingError."""
    from db import get_payment

    payment = get_payment(session_id)
    if not payment or payment["user_id"] != user_id or payment["provider"] != "stripe":
        raise BillingError("Unknown payment.")
    response = requests.get(
        f"{STRIPE_API}/checkout/sessions/{session_id}", auth=(Config.STRIPE_SECRET_KEY, ""), timeout=20
    )
    if not response.ok:
        raise BillingError("We could not check the payment with Stripe. It will be applied within a few minutes.")
    session = response.json()
    if not _stripe_session_paid(session):
        raise BillingError("The payment hasn't completed yet.")
    _remember_stripe_subscription(payment, session)
    return fulfil(
        session_id,
        session.get("payment_intent") or session.get("invoice"),
        "stripe_return",
        session.get("amount_total"),
    )


def _remember_stripe_subscription(payment: dict, session: dict) -> None:
    """A subscription-mode Checkout session: keep its subscription so renewals can find it."""
    from db import create_subscription, get_subscription

    sub_id = session.get("subscription")
    if session.get("mode") != "subscription" or not sub_id or get_subscription(sub_id):
        return
    _, slug, period = payment["item"].split(":")
    create_subscription(payment["user_id"], "stripe", sub_id, slug, period, payment["amount"], payment["currency"])
    from db import update_subscription

    update_subscription(sub_id, status="active")


def valid_stripe_signature(body: bytes, header: str, now: float | None = None) -> bool:
    """Stripe-Signature: "t=<time>,v1=<hmac>" - HMAC-SHA256 of "<time>.<body>"
    with the webhook secret, and not older than the tolerance."""
    if not Config.STRIPE_WEBHOOK_SECRET or not header:
        return False
    fields = [part.split("=", 1) for part in header.split(",") if "=" in part]
    stamp = next((v for k, v in fields if k == "t"), None)
    signatures = [v for k, v in fields if k == "v1"]
    if not stamp or not signatures or not stamp.isdigit():
        return False
    if abs((now if now is not None else time.time()) - int(stamp)) > STRIPE_SIGNATURE_TOLERANCE:
        return False
    expected = hmac.new(Config.STRIPE_WEBHOOK_SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in signatures)


def handle_stripe_webhook(body: bytes, signature: str) -> str:
    from db import record_payment_event

    if not valid_stripe_signature(body, signature):
        return "bad_signature"
    event = json.loads(body or b"{}")
    if event.get("id") and not record_payment_event(f"stripe:{event['id']}"):
        return "duplicate"
    session = (event.get("data") or {}).get("object") or {}
    if event.get("type") in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        if not _stripe_session_paid(session):
            return "not_paid_yet"  # e.g. a bank transfer still pending: async_payment_succeeded follows
        from db import get_payment

        payment = get_payment(session.get("id") or "")
        if payment:
            _remember_stripe_subscription(payment, session)
        applied = fulfil(
            session.get("id"),
            session.get("payment_intent") or session.get("invoice"),
            "stripe_webhook",
            session.get("amount_total"),
        )
        return "applied" if applied else "already_applied"
    if event.get("type") == "invoice.paid" and session.get("subscription"):
        invoice = session
        if invoice.get("billing_reason") == "subscription_create":
            return "first_period_via_checkout"  # already applied from the Checkout session
        applied = charge_subscription(
            invoice["subscription"], invoice["id"], invoice.get("amount_paid"), "stripe_webhook"
        )
        return "applied" if applied else "already_applied"
    if event.get("type") == "invoice.payment_failed" and session.get("subscription"):
        _renewal_failed(session["subscription"], None)
        return "renewal_failed"
    if event.get("type") == "customer.subscription.deleted":
        subscription_ended(session.get("id", ""), "ended")
        return "subscription_updated"
    if event.get("type") == "customer.subscription.updated" and session.get("cancel_at_period_end"):
        subscription_ended(session.get("id", ""), "cancelled")
        return "subscription_updated"
    if event.get("type") == "charge.refunded":
        from db import get_payment_by_provider_id

        charge = session  # data.object is the charge here
        payment = get_payment_by_provider_id(charge.get("payment_intent") or "")
        if payment and charge.get("amount_refunded") is not None:
            recorded = apply_refund(payment["order_id"], int(charge["amount_refunded"]), "stripe_webhook")
            return "refund_recorded" if recorded else "refund_known"
    return "ignored"


# ── Automatic renewal (subscriptions) ─────────────────────────────────────
#
# Stripe Checkout in subscription mode, in the user's currency. The first
# period is applied like a one-time payment when the user comes back; every
# renewal arrives as the signed invoice.paid webhook. Each charge is a Payment
# (the Stripe invoice id), applied once through fulfil(), so it extends the
# plan, gets an invoice and is audited. Cancelling stops future charges; the
# paid time is kept.


def _subscription_price(user_id: int, plan_slug: str, period: str, currency: str) -> dict:
    if period not in PERIOD_DAYS:
        raise BillingError("Choose monthly or yearly.")
    return price_item(user_id, f"plan:{plan_slug}:{period}", currency)


def start_subscription(user_id: int, plan_slug: str, period: str, currency: str, base_url: str) -> dict:
    """Start an automatically renewing plan. Returns {url} of Stripe Checkout."""
    from db import create_payment, current_subscription, get_user_by_id

    if not enabled():
        raise BillingError("Online payment isn't available yet.")
    if current_subscription(user_id):
        raise BillingError("You already have automatic renewal on. Cancel it first to switch plans.")
    priced = _subscription_price(user_id, plan_slug, period, currency)
    description = f"{priced['description'].replace(', 1 ' + period, '')}, renews every {period}"
    user = get_user_by_id(user_id)
    base = base_url.rstrip("/")
    response = requests.post(
        f"{STRIPE_API}/checkout/sessions",
        auth=(Config.STRIPE_SECRET_KEY, ""),
        data={
            "mode": "subscription",
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": currency.lower(),
            "line_items[0][price_data][unit_amount]": priced["amount"],
            "line_items[0][price_data][recurring][interval]": period,
            "line_items[0][price_data][product_data][name]": _product_name(description, priced["tax"]),
            "client_reference_id": str(user_id),
            "metadata[user_id]": str(user_id),
            "metadata[item]": priced["item"],
            "subscription_data[metadata][user_id]": str(user_id),
            "subscription_data[metadata][item]": priced["item"],
            "customer_email": user.email if user else None,
            "success_url": f"{base}/settings?checkout=success&session_id={{CHECKOUT_SESSION_ID}}#plan",
            "cancel_url": f"{base}/settings?checkout=cancelled#plan",
        },
        timeout=20,
    )
    if not response.ok:
        logger.warning(f"Stripe subscription session failed: {response.status_code} {response.text[:200]}")
        raise BillingError("Automatic renewal could not be started. Please try again.")
    session = response.json()
    # the first period is paid through this session, applied on return like a one-time purchase
    create_payment(
        user_id, "stripe", session["id"], priced["item"], description, priced["amount"], priced["tax"], currency
    )
    return {"provider": "stripe", "url": session["url"], "session_id": session["id"]}


def charge_subscription(provider_subscription_id: str, charge_id: str, amount: int | None, source: str) -> dict | None:
    """One paid charge of a subscription (first or renewal): record it as a
    payment and apply it once. Returns what fulfil() returns."""
    from db import create_payment, get_payment, get_subscription, update_subscription

    sub = get_subscription(provider_subscription_id)
    if sub is None:
        logger.warning(f"Charge for unknown subscription {provider_subscription_id} ({source})")
        return None
    if get_payment(charge_id) is None:
        create_payment(
            sub["user_id"],
            sub["provider"],
            charge_id,
            f"plan:{sub['plan_slug']}:{sub['period']}",
            f"{sub['plan_slug'].title()} plan, renews every {sub['period']}",
            sub["amount"],
            int(round(sub["amount"] - sub["amount"] / (1 + Config.GST_RATE))) if sub["currency"] == "INR" else 0,
            sub["currency"],
        )
    if sub["status"] == "created":
        update_subscription(provider_subscription_id, status="active")
    return fulfil(charge_id, charge_id, source, amount)


def cancel_subscription(user_id: int, actor_user_id: int | None = None) -> dict:
    """Stop automatic renewal at the end of the paid period. Raises BillingError."""
    from db import current_subscription, log_audit, update_subscription

    sub = current_subscription(user_id)
    if not sub or sub["status"] != "active":
        raise BillingError("Automatic renewal isn't on.")
    response = requests.post(
        f"{STRIPE_API}/subscriptions/{sub['provider_subscription_id']}",
        auth=(Config.STRIPE_SECRET_KEY, ""),
        data={"cancel_at_period_end": "true"},
        timeout=20,
    )
    if not response.ok:
        logger.warning(
            f"Cancel failed for {sub['provider_subscription_id']}: {response.status_code} {response.text[:200]}"
        )
        raise BillingError("Automatic renewal could not be cancelled. Please try again or contact us.")
    updated = update_subscription(
        sub["provider_subscription_id"],
        status="cancelled",
        cancel_at_period_end=True,
        cancelled_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    log_audit(
        "subscription.cancelled",
        actor_user_id=actor_user_id or user_id,
        target_user_id=user_id,
        details={"subscription": sub["provider_subscription_id"], "plan": sub["plan_slug"], "period": sub["period"]},
    )
    return updated


def _renewal_failed(provider_subscription_id: str, reason: str | None) -> None:
    from db import get_subscription, log_audit
    from services.billing_notice_service import renewal_failed

    sub = get_subscription(provider_subscription_id)
    if not sub:
        return
    log_audit(
        "subscription.payment_failed",
        target_user_id=sub["user_id"],
        details={"subscription": provider_subscription_id, "plan": sub["plan_slug"], "reason": reason},
    )
    renewal_failed(sub["user_id"], provider_subscription_id, sub["plan_slug"].title(), reason)


def subscription_ended(provider_subscription_id: str, status: str) -> None:
    from db import get_subscription, log_audit, update_subscription

    sub = get_subscription(provider_subscription_id)
    if sub and sub["status"] != status:
        update_subscription(provider_subscription_id, status=status)
        log_audit(
            f"subscription.{status}",
            target_user_id=sub["user_id"],
            details={"subscription": provider_subscription_id, "plan": sub["plan_slug"]},
        )
