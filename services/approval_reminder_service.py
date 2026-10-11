"""Reminder emails for approval requests nobody has decided yet.

A reviewer who hasn't answered gets one reminder a day after the request was
sent, and a last one after three days. A request older than MAX_AGE_DAYS is
left alone (so switching this on doesn't mail about long-forgotten requests),
and a decided request never gets one.

Sent by the scheduler, hourly, only when APPROVAL_REMINDER_EMAILS=true or the
switch in Admin > Emails is on. Each attempt is in the admin's email history
(kind "approval_reminder").
"""

import logging
from datetime import datetime, timezone

from config import Config

logger = logging.getLogger(__name__)

REMIND_AFTER_HOURS = (24, 72)
MAX_AGE_DAYS = 14


def waiting_text(created_at: str | None, now: datetime) -> str:
    """"1 day" / "3 days" - how long the request has been waiting."""
    try:
        days = (now - datetime.fromisoformat(created_at)).days
    except (TypeError, ValueError):
        days = 1
    days = max(days, 1)
    return f"{days} day{'s' if days != 1 else ''}"


def _requester(req: dict) -> tuple[str, str | None]:
    """(who asked, their company) for the email's wording."""
    from db import get_user_brand_profile, get_user_by_id

    if not req.get("user_id"):
        return "A colleague", None
    user = get_user_by_id(req["user_id"])
    company = (get_user_brand_profile(req["user_id"]) or {}).get("company_name") or None
    return (user.name if user and user.name else "A colleague"), company


def run_approval_reminders(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    result = {"sent": 0, "failed": 0, "ran": False}
    from db import (
        approvals_awaiting_reminder,
        email_automation_enabled,
        mark_approval_reminder,
    )

    if not email_automation_enabled("approval_reminders"):
        return result
    if not Config.APP_BASE_URL:
        logger.warning("Approval reminders not sent: APP_BASE_URL is not set (the email's link needs it).")
        return result
    from services.email_service import EmailService, email_context

    mailer = EmailService()
    if not mailer.enabled:
        return result
    result["ran"] = True
    base_url = Config.APP_BASE_URL.rstrip("/")
    for req in approvals_awaiting_reminder(now, REMIND_AFTER_HOURS, MAX_AGE_DAYS):
        number = req["reminder_count"] + 1
        platforms = [i.get("platform") for i in req["items"]] if req.get("items") else [req.get("platform")]
        name, company = _requester(req)
        try:
            with email_context(kind="approval_reminder", triggered_by="scheduler",
                               details={"request_id": req["id"], "reminder": number}):
                mailer.send_approval_reminder(
                    to_email=req["reviewer_email"], approval_url=f"{base_url}/approve/{req['id']}",
                    requester=name, company=company, platforms=[p for p in platforms if p and p != "multi"],
                    waiting=waiting_text(req["created_at"], now), last=number >= len(REMIND_AFTER_HOURS),
                )
            mark_approval_reminder(req["id"], now, sent=True)
            result["sent"] += 1
        except Exception as err:  # noqa: BLE001 - one bad address must not stop the others
            mark_approval_reminder(req["id"], now, sent=False)  # tried: wait before trying again
            result["failed"] += 1
            logger.warning(f"Approval reminder failed for request {req['id']}: {str(err)[:200]}")
    if result["sent"] or result["failed"]:
        logger.info(f"Approval reminders: {result}")
    return result
