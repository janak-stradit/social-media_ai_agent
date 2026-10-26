"""Billing notices: an email and a header-bell item for each.

  receipt        - every applied payment, with its invoice link
  renewal_soon   - RENEWAL_REMINDER_DAYS before an automatic renewal charges
  expiry_soon    - EXPIRY_REMINDER_DAYS before a plan paid once runs out
  renewal_failed - a renewal charge failed (Stripe webhook)

Each notice is sent once (db.Notification dedupe_key). Billing emails are
account emails: they go out whatever the user's marketing email settings,
but only when SMTP and APP_BASE_URL are set. A failed email never undoes
anything; the bell still shows the notice.
"""

import logging
from datetime import datetime, timedelta, timezone

from config import Config

logger = logging.getLogger(__name__)

RENEWAL_REMINDER_DAYS = 3
EXPIRY_REMINDER_DAYS = 5


def _money(amount: int, currency: str) -> str:
    return f"₹{amount / 100:,.2f}" if currency == "INR" else f"${amount / 100:,.2f}"


def _date(value) -> str:
    when = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return f"{when.day} {when:%B %Y}"  # not %-d: Linux-only


def notify(
    user_id: int, key: str, title: str, body: str, path: str, subject: str, lines: list[str], button: str, footer: str
) -> bool:
    """Bell item + email, once per key. True when this call created it."""
    from db import create_notification, get_user_by_id

    notification_id = create_notification(user_id, "billing", title, body, path, key)
    if not notification_id:
        return False
    user = get_user_by_id(user_id)
    if user and Config.APP_BASE_URL:
        try:
            from services.email_service import EmailService

            EmailService().send_billing_email(
                to_email=user.email,
                name=user.name or "there",
                subject=subject,
                headline=title,
                lines=lines,
                button=button,
                url=Config.APP_BASE_URL.rstrip("/") + path,
                footer=footer,
            )
        except Exception as err:  # noqa: BLE001
            logger.warning(f"Billing email '{key}' to user {user_id} failed: {err}")
    return True


def payment_receipt(payment: dict, invoice_number: str | None) -> None:
    amount = _money(payment["amount"], payment["currency"])
    path = f"/invoices/{invoice_number}" if invoice_number else "/settings#plan"
    notify(
        payment["user_id"],
        f"receipt:{payment['order_id']}",
        f"Payment received: {amount}",
        payment.get("description") or "",
        path,
        f"Receipt for {amount}",
        [
            f"Thank you. We received {amount} for {payment.get('description') or 'your purchase'}.",
            f"Your invoice is {invoice_number}." if invoice_number else "Your invoice will be in Settings shortly.",
        ],
        "View invoice" if invoice_number else "Open Settings",
        "You get this email because you paid for AVIR AI.",
    )


def renewal_failed(user_id: int, provider_subscription_id: str, plan_name: str, reason: str | None = None) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    notify(
        user_id,
        f"renewal_failed:{provider_subscription_id}:{today}",
        "Your plan couldn't be renewed",
        f"The payment for your {plan_name} plan didn't go through.",
        "/settings#plan",
        "Renewal payment failed",
        [
            f"We tried to renew your {plan_name} plan, but the payment didn't go through"
            + (f" ({reason})." if reason else "."),
            "Please check your card or UPI mandate with your bank. The payment will be tried again; "
            "if it keeps failing, your plan stops at the end of the time already paid for.",
        ],
        "Check your plan",
        "You get this email because automatic renewal is on for your AVIR AI plan.",
    )


def run_billing_reminders(now: datetime | None = None) -> dict:
    """Scheduler (hourly): renewal and expiry reminders, once each."""
    from db import plans_ending_between

    now = now or datetime.now(timezone.utc)
    sent = {"renewal_soon": 0, "expiry_soon": 0}
    for row in plans_ending_between(now, now + timedelta(days=max(RENEWAL_REMINDER_DAYS, EXPIRY_REMINDER_DAYS))):
        days_left = (row["paid_until"] - now).total_seconds() / 86400
        end = _date(row["paid_until"])
        if row["renewing"] and days_left <= RENEWAL_REMINDER_DAYS:
            amount = _money(row["amount"], row["currency"])
            if notify(
                row["user_id"],
                f"renewal_soon:{row['subscription_id']}:{row['paid_until'].date()}",
                f"Your {row['plan_name']} plan renews on {end}",
                f"{amount} will be charged automatically.",
                "/settings#plan",
                f"Your plan renews on {end}",
                [
                    f"Your {row['plan_name']} plan renews automatically on {end}, and {amount} will be charged "
                    "to the card or UPI mandate you used.",
                    "Nothing to do if you want to keep it. To stop it, cancel automatic renewal in Settings -> Plan & usage "
                    "before then; you keep the time already paid for.",
                ],
                "Manage my plan",
                "You get this email because automatic renewal is on for your AVIR AI plan.",
            ):
                sent["renewal_soon"] += 1
        elif not row["renewing"] and days_left <= EXPIRY_REMINDER_DAYS:
            if notify(
                row["user_id"],
                f"expiry_soon:{row['user_id']}:{row['paid_until'].date()}",
                f"Your {row['plan_name']} plan ends on {end}",
                "Renew it to keep your limits and features.",
                "/settings#plan",
                f"Your plan ends on {end}",
                [
                    f"Your {row['plan_name']} plan ends on {end}. After that your account moves to the Free plan.",
                    "Renew it, or turn on automatic renewal so it never runs out.",
                ],
                "Renew my plan",
                "You get this email because you paid for an AVIR AI plan.",
            ):
                sent["expiry_soon"] += 1
    if any(sent.values()):
        logger.info(f"Billing reminders: {sent}")
    return sent
