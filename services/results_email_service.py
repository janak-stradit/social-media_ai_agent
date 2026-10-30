"""The weekly results email: how last week's published posts did, in one look.

  - reach and engagement for the week, against the week before
  - the best post, as "x your usual" (scored at 48 hours, db.post_analytics)
  - new followers (db.follower_growth, counted daily)
  - the brand's clearest "what works" finding (services/insights_service.py)
  - comments waiting for a reply in the Inbox

Weeks run Monday to Sunday in UTC; the email covers the one that just ended.
Sent once per user per week (User.results_email_last_week), only when there
is something to report - a post published that week or a comment waiting -
and by the scheduler only when WEEKLY_RESULTS_EMAIL is on (Admin -> Emails).
No AI call: everything in it is already worked out.
"""

import logging
from datetime import datetime, timedelta, timezone

from config import Config

logger = logging.getLogger(__name__)

_PLATFORMS = {"linkedin": "LinkedIn", "instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}


def last_week(now: datetime) -> tuple[datetime, datetime, str]:
    """(Monday 00:00, the next Monday 00:00, "2026-W44") of the week before `now`'s, UTC."""
    today = now.astimezone(timezone.utc).date()
    monday = today - timedelta(days=today.weekday() + 7)
    start = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
    year, week, _ = monday.isocalendar()
    return start, start + timedelta(days=7), f"{year}-W{week:02d}"


def _change(now: int, before: int) -> str | None:
    if not before:
        return None
    pct = round(100 * (now - before) / before)
    return f"{'+' if pct >= 0 else ''}{pct}% on the week before"


def week_results(user_id: int, start: datetime, end: datetime) -> dict:
    """Everything the email shows for the week [start, end)."""
    from db import list_post_comments, post_analytics

    week = post_analytics(user_id, start, end)
    before = post_analytics(user_id, start - timedelta(days=7), start)
    scored = [p for p in week["all_posts"] if p.get("vs_usual")]
    best = max(scored, key=lambda p: p["vs_usual"]) if scored else (week["top_posts"][0] if week["top_posts"] else None)
    followers = [f["change"] for f in (week.get("followers") or {}).values() if f.get("change") is not None]
    waiting = list_post_comments(user_id, "new", limit=1)["counts"]
    finding = None
    try:  # the email is still worth sending without it
        from services.insights_service import what_works

        found = what_works(user_id)["insights"]
        finding = found[0]["text"] if found else None
    except Exception as err:  # noqa: BLE001
        logger.info(f"Weekly results for user {user_id}: no insight ({str(err)[:150]})")
    return {
        "posts": week["posts"],
        "reach": week["totals"]["reach"],
        "engagement": week["engagement"],
        "engagement_rate": week["engagement_rate"],
        "reach_change": _change(week["totals"]["reach"], before["totals"]["reach"]),
        "engagement_change": _change(week["engagement"], before["engagement"]),
        "followers": sum(followers) if followers else None,
        "best": {
            "id": best["id"], "title": best["title"] or "Your post", "platform": _PLATFORMS.get(best["platform"], best["platform"]),
            "vs_usual": best.get("vs_usual"), "engagement": best["engagement"],
        } if best else None,
        "finding": finding,
        "comments_waiting": waiting.get("new", 0),
        "leads_waiting": (waiting.get("kinds") or {}).get("lead", 0),
        "week_label": f"{start.day} {start:%b} – {(end - timedelta(days=1)).day} {(end - timedelta(days=1)):%b}",
    }


def send_results_email(user_id: int, base_url: str, start: datetime, end: datetime, week_key: str) -> dict:
    """Email the user their results for the week. {"sent": True} or
    {"sent": False, "reason": "nothing"} when there is nothing to report."""
    from db import get_user_brand_profile, get_user_by_id, mark_results_email_sent
    from services.email_service import EmailService, email_context
    from services.idea_digest_service import unsubscribe_token

    user = get_user_by_id(user_id)
    if not user:
        return {"sent": False, "reason": "user_not_found"}
    results = week_results(user_id, start, end)
    if not results["posts"] and not results["comments_waiting"]:
        return {"sent": False, "reason": "nothing"}
    base = base_url.rstrip("/")
    with email_context(user_id=user_id, details={"week": week_key, "posts": results["posts"]}):
        EmailService().send_weekly_results_email(
            to_email=user.email,
            name=user.name or "there",
            company_name=(get_user_brand_profile(user_id) or {}).get("company_name"),
            results=results,
            performance_url=f"{base}/performance",
            best_url=f"{base}/performance/posts/{results['best']['id']}" if results["best"] else f"{base}/performance",
            inbox_url=f"{base}/inbox",
            settings_url=f"{base}/settings#notifications",
            unsubscribe_url=f"{base}/api/notifications/results-email/unsubscribe/{unsubscribe_token(user_id, 'results')}",
        )
    mark_results_email_sent(user_id, week_key)
    return {"sent": True, "posts": results["posts"], "comments_waiting": results["comments_waiting"]}


def run_weekly_results(now: datetime | None = None) -> dict:
    """Scheduler: send last week's results to everyone due them. Does nothing
    unless the automation is on and it is RESULTS_EMAIL_WEEKDAY from the send
    hour. A user with nothing to report is marked done for the week, not retried."""
    now = now or datetime.now(timezone.utc)
    result = {"sent": 0, "skipped": 0, "failed": 0, "ran": False}
    from db import email_automation_enabled

    if not email_automation_enabled("weekly_results") or now.weekday() != Config.RESULTS_EMAIL_WEEKDAY or \
            now.hour < Config.IDEAS_EMAIL_HOUR_UTC:
        return result
    if not Config.APP_BASE_URL:
        logger.warning("Weekly results not sent: APP_BASE_URL is not set (the email's links need it).")
        return result
    from db import mark_results_email_sent, users_due_results

    start, end, key = last_week(now)
    result["ran"] = True
    for user in users_due_results(key):
        try:
            outcome = send_results_email(user["id"], Config.APP_BASE_URL, start, end, key)
            if outcome["sent"]:
                result["sent"] += 1
            else:
                result["skipped"] += 1
                mark_results_email_sent(user["id"], key)  # nothing to report: don't look again this week
        except Exception as err:  # noqa: BLE001
            result["failed"] += 1
            logger.warning(f"Weekly results failed for user {user['id']}: {str(err)[:200]}")
    if result["sent"] or result["failed"]:
        logger.info(f"Weekly results {key}: {result}")
    return result
