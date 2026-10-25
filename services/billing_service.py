"""Buying plans and top-up packs: Razorpay for INR, Stripe for USD.

How a purchase works
  1. The browser asks for an item ("plan:growth:month", "pack:image10").
     The server looks up its price (plans table / PACKS below), adds GST and
     creates a Razorpay order. The browser never sends an amount.
  2. Razorpay's own checkout window takes the payment (cards, UPI, net
     banking); card details never reach this server.
  3. The browser sends back Razorpay's payment id and signature. The
     signature (HMAC-SHA256 of "order_id|payment_id" with the key secret)
     proves Razorpay confirmed the payment; then the purchase is applied.
  4. Razorpay's webhook reports the same payment server-to-server (signed
     with the webhook secret), in case the browser closed before step 3.
Each payment is applied once (db.claim_payment_for_fulfilment), whichever of
steps 3 and 4 arrives first, and recorded in the audit log.

Stripe (USD) follows the same rules with Stripe Checkout: the server creates a
Checkout Session at its own price and sends the browser to Stripe's page;
afterwards the server asks Stripe directly whether the session was paid
(confirm_stripe_session), and Stripe's signed webhook reports it too. No GST
is added to USD prices.
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

RAZORPAY_API = "https://api.razorpay.com/v1"
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


def enabled(currency: str = "INR") -> bool:
    if currency == "USD":
        return bool(Config.STRIPE_SECRET_KEY)
    return bool(Config.RAZORPAY_KEY_ID and Config.RAZORPAY_KEY_SECRET)


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


def create_order(user_id: int, item: str) -> dict:
    """Create a Razorpay order for an item and remember it. Returns what the
    checkout window needs."""
    from db import create_payment, get_user_by_id

    if not enabled():
        raise BillingError("Online payment isn't available yet.")
    priced = price_item(user_id, item)
    response = requests.post(
        f"{RAZORPAY_API}/orders",
        auth=(Config.RAZORPAY_KEY_ID, Config.RAZORPAY_KEY_SECRET),
        json={
            "amount": priced["amount"],
            "currency": "INR",
            "receipt": f"u{user_id}-{item}"[:40],
            "notes": {"user_id": str(user_id), "item": item},
        },
        timeout=20,
    )
    if not response.ok:
        logger.warning(f"Razorpay order failed: {response.status_code} {response.text[:200]}")
        raise BillingError("Payment could not be started. Please try again.")
    order = response.json()
    create_payment(
        user_id, "razorpay", order["id"], item, priced["description"], priced["amount"], priced["tax"], "INR"
    )
    user = get_user_by_id(user_id)
    return {
        "order_id": order["id"],
        "key_id": Config.RAZORPAY_KEY_ID,
        "amount": priced["amount"],
        "currency": "INR",
        "description": priced["description"],
        "tax": priced["tax"],
        "prefill": {"name": user.name if user else "", "email": user.email if user else ""},
    }


def valid_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    expected = hmac.new(
        Config.RAZORPAY_KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


def valid_webhook_signature(body: bytes, signature: str) -> bool:
    if not Config.RAZORPAY_WEBHOOK_SECRET:
        return False
    expected = hmac.new(Config.RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


def fulfil(order_id: str, payment_id: str | None, source: str, paid_amount: int | None = None) -> dict | None:
    """Apply a paid order (once): extend the plan or add the pack. Returns
    what was applied, or None when it was already applied or is unknown."""
    from db import claim_payment_for_fulfilment, extend_paid_plan, get_payment, grant_pack, log_audit

    payment = get_payment(order_id)
    if payment is None:
        logger.warning(f"Payment for unknown order {order_id} ({source})")
        return None
    if paid_amount is not None and int(paid_amount) != int(payment["amount"]):
        # Razorpay reports a different amount than the order was created for: don't apply it
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
    """Admin: refund an order (all of it when amount is None) through its
    provider, then record it. Raises BillingError."""
    from db import get_payment

    payment = get_payment(order_id)
    if not payment or payment["status"] not in ("paid", "partially_refunded") or not payment["payment_id"]:
        raise BillingError("Only a paid payment can be refunded.")
    left = payment["amount"] - (payment["refunded_amount"] or 0)
    amount = left if amount is None else int(amount)
    if not 0 < amount <= left:
        raise BillingError(f"The refund must be between 1 and {left} (smallest currency unit).")
    if payment["provider"] == "razorpay":
        response = requests.post(
            f"{RAZORPAY_API}/payments/{payment['payment_id']}/refund",
            auth=(Config.RAZORPAY_KEY_ID, Config.RAZORPAY_KEY_SECRET),
            json={"amount": amount, "notes": {"order_id": order_id}},
            timeout=20,
        )
    else:
        response = requests.post(
            f"{STRIPE_API}/refunds",
            auth=(Config.STRIPE_SECRET_KEY, ""),
            data={"payment_intent": payment["payment_id"], "amount": amount, "metadata[order_id]": order_id},
            timeout=20,
        )
    if not response.ok:
        logger.warning(f"Refund failed for {order_id}: {response.status_code} {response.text[:200]}")
        raise BillingError("The payment provider refused the refund. Check its dashboard for details.")
    result = apply_refund(order_id, (payment["refunded_amount"] or 0) + amount, "admin", actor_user_id)
    return result or {"refunded": 0}


def handle_webhook(body: bytes, signature: str, event_id: str | None) -> str:
    """Razorpay webhook: verify, ignore repeats, apply paid orders. Returns a
    short status for the log."""
    from db import mark_payment_failed, record_payment_event

    if not valid_webhook_signature(body, signature):
        return "bad_signature"
    if event_id and not record_payment_event(event_id):
        return "duplicate"
    event = json.loads(body or b"{}")
    kind = event.get("event")
    payment = ((event.get("payload") or {}).get("payment") or {}).get("entity") or {}
    order_id, payment_id = payment.get("order_id"), payment.get("id")
    subscription = ((event.get("payload") or {}).get("subscription") or {}).get("entity") or {}
    if kind == "subscription.charged" and subscription.get("id") and payment_id:
        applied = charge_subscription(
            subscription["id"], f"rzp_sub:{payment_id}", payment.get("amount"), "razorpay_webhook"
        )
        return "applied" if applied else "already_applied"
    if kind == "subscription.pending" and subscription.get("id"):
        _renewal_failed(subscription["id"], "the bank declined it or the mandate wasn't approved")
        return "renewal_failed"
    if kind in ("subscription.cancelled", "subscription.completed", "subscription.halted") and subscription.get("id"):
        subscription_ended(
            subscription["id"],
            {"subscription.cancelled": "cancelled", "subscription.completed": "ended", "subscription.halted": "ended"}[
                kind
            ],
        )
        return "subscription_updated"
    if kind in ("payment.captured", "order.paid") and order_id:
        return "applied" if fulfil(order_id, payment_id, "webhook", payment.get("amount")) else "already_applied"
    if kind == "payment.failed" and order_id:
        mark_payment_failed(order_id, payment_id)
        return "failed"
    if kind in ("refund.processed", "refund.created") and order_id and payment.get("amount_refunded") is not None:
        return (
            "refund_recorded"
            if apply_refund(order_id, int(payment["amount_refunded"]), "razorpay_webhook")
            else "refund_known"
        )
    return "ignored"


# ── Stripe (USD) ──────────────────────────────────────────────────────────


def create_stripe_session(user_id: int, item: str, base_url: str) -> dict:
    """A Stripe Checkout Session at the server's USD price. Returns {url} to send the browser to."""
    from db import create_payment, get_user_by_id

    if not enabled("USD"):
        raise BillingError("Online payment isn't available yet.")
    priced = price_item(user_id, item, "USD")
    user = get_user_by_id(user_id)
    base = base_url.rstrip("/")
    response = requests.post(
        f"{STRIPE_API}/checkout/sessions",
        auth=(Config.STRIPE_SECRET_KEY, ""),
        data={
            "mode": "payment",
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": priced["amount"],
            "line_items[0][price_data][product_data][name]": f"AVIR AI - {priced['description']}",
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
    create_payment(user_id, "stripe", session["id"], item, priced["description"], priced["amount"], 0, "USD")
    return {"url": session["url"], "session_id": session["id"]}


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
    create_subscription(payment["user_id"], "stripe", sub_id, slug, period, payment["amount"], "USD")
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
# Razorpay (INR): a Razorpay "plan" per AVIR plan, period and price (created
# once, remembered in app_settings), then a subscription the user authorises
# in Razorpay's window (cards, UPI AutoPay). Every charge - the first one too -
# arrives as the signed subscription.charged webhook.
# Stripe (USD): Checkout in subscription mode. The first period is applied
# like a one-time payment when the user comes back; every renewal arrives as
# the signed invoice.paid webhook.
# Each charge is a Payment ("rzp_sub:<payment id>" / the Stripe invoice id),
# applied once through fulfil(), so it extends the plan, gets an invoice and is
# audited. Cancelling stops future charges; the paid time is kept.

SUBSCRIPTION_CYCLES = {"month": 120, "year": 10}  # Razorpay needs a total number of charges


def _subscription_price(user_id: int, plan_slug: str, period: str, currency: str) -> dict:
    if period not in PERIOD_DAYS:
        raise BillingError("Choose monthly or yearly.")
    return price_item(user_id, f"plan:{plan_slug}:{period}", currency)


def _razorpay_plan_id(plan_slug: str, period: str, amount: int, description: str) -> str:
    """Razorpay's plan for this AVIR plan, period and price (new price -> new Razorpay plan)."""
    from db import get_setting, save_setting

    key = f"razorpay_plan:{plan_slug}:{period}:{amount}"
    known = get_setting(key, "")
    if known:
        return known
    response = requests.post(
        f"{RAZORPAY_API}/plans",
        auth=(Config.RAZORPAY_KEY_ID, Config.RAZORPAY_KEY_SECRET),
        json={
            "period": "monthly" if period == "month" else "yearly",
            "interval": 1,
            "item": {"name": f"AVIR AI - {description}", "amount": amount, "currency": "INR"},
        },
        timeout=20,
    )
    if not response.ok:
        logger.warning(f"Razorpay plan failed: {response.status_code} {response.text[:200]}")
        raise BillingError("Automatic renewal could not be started. Please try again.")
    plan_id = response.json()["id"]
    save_setting(key, plan_id)
    return plan_id


def start_subscription(user_id: int, plan_slug: str, period: str, currency: str, base_url: str) -> dict:
    """Start an automatically renewing plan. INR: returns what Razorpay's window
    needs; USD: returns {url} of Stripe Checkout."""
    from db import create_payment, create_subscription, current_subscription, get_user_by_id

    if not enabled(currency):
        raise BillingError("Online payment isn't available yet.")
    if current_subscription(user_id):
        raise BillingError("You already have automatic renewal on. Cancel it first to switch plans.")
    priced = _subscription_price(user_id, plan_slug, period, currency)
    description = f"{priced['description'].replace(', 1 ' + period, '')}, renews every {period}"
    user = get_user_by_id(user_id)
    if currency == "INR":
        plan_id = _razorpay_plan_id(plan_slug, period, priced["amount"], description)
        response = requests.post(
            f"{RAZORPAY_API}/subscriptions",
            auth=(Config.RAZORPAY_KEY_ID, Config.RAZORPAY_KEY_SECRET),
            json={
                "plan_id": plan_id,
                "total_count": SUBSCRIPTION_CYCLES[period],
                "customer_notify": 1,
                "notes": {"user_id": str(user_id), "plan": plan_slug, "period": period},
            },
            timeout=20,
        )
        if not response.ok:
            logger.warning(f"Razorpay subscription failed: {response.status_code} {response.text[:200]}")
            raise BillingError("Automatic renewal could not be started. Please try again.")
        sub = response.json()
        create_subscription(user_id, "razorpay", sub["id"], plan_slug, period, priced["amount"], "INR")
        return {
            "provider": "razorpay",
            "subscription_id": sub["id"],
            "key_id": Config.RAZORPAY_KEY_ID,
            "description": description,
            "amount": priced["amount"],
            "prefill": {"name": user.name if user else "", "email": user.email if user else ""},
        }
    base = base_url.rstrip("/")
    response = requests.post(
        f"{STRIPE_API}/checkout/sessions",
        auth=(Config.STRIPE_SECRET_KEY, ""),
        data={
            "mode": "subscription",
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": priced["amount"],
            "line_items[0][price_data][recurring][interval]": period,
            "line_items[0][price_data][product_data][name]": f"AVIR AI - {description}",
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
    create_payment(user_id, "stripe", session["id"], priced["item"], description, priced["amount"], 0, "USD")
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


def verify_razorpay_subscription(user_id: int, subscription_id: str, payment_id: str, signature: str) -> dict | None:
    """The result of Razorpay's window for a subscription: signature is an
    HMAC of "payment_id|subscription_id". Applies the first charge."""
    from db import get_subscription

    sub = get_subscription(subscription_id)
    if not sub or sub["user_id"] != user_id:
        raise BillingError("Unknown subscription.")
    expected = hmac.new(
        Config.RAZORPAY_KEY_SECRET.encode(), f"{payment_id}|{subscription_id}".encode(), hashlib.sha256
    ).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise BillingError("The payment could not be confirmed.")
    return charge_subscription(subscription_id, f"rzp_sub:{payment_id}", None, "subscription_checkout")


def cancel_subscription(user_id: int, actor_user_id: int | None = None) -> dict:
    """Stop automatic renewal at the end of the paid period. Raises BillingError."""
    from db import current_subscription, log_audit, update_subscription

    sub = current_subscription(user_id)
    if not sub or sub["status"] != "active":
        raise BillingError("Automatic renewal isn't on.")
    if sub["provider"] == "razorpay":
        response = requests.post(
            f"{RAZORPAY_API}/subscriptions/{sub['provider_subscription_id']}/cancel",
            auth=(Config.RAZORPAY_KEY_ID, Config.RAZORPAY_KEY_SECRET),
            json={"cancel_at_cycle_end": 1},
            timeout=20,
        )
    else:
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
