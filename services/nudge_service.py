"""Timely nudges: a reason to come back between the weekly ideas emails.

  occasion - a festival or holiday in the user's markets is a few days away
  inactive - nothing generated for a while
  trend    - this week's top story in the user's industry (mid-week)

Every nudge carries one idea and a link that opens Studio Chat with its brief
loaded (db.IdeaLink, like the weekly email). Nudges always appear under the
header bell (db.Notification). They are emailed too only when NUDGE_EMAILS is
on, the user hasn't switched nudge emails off, they haven't been active in the
last two days and they are under the weekly cap of idea emails.

At most one nudge per user per day, and each one only once (dedupe_key), so
the hourly scheduler run can't repeat itself.
"""

import logging
from datetime import datetime, timedelta, timezone

from config import Config

logger = logging.getLogger(__name__)

_QUIET_DAYS_AFTER_ACTIVITY = 2  # no email to someone who was just here
_MAX_UNREAD = 5


def _due_nudge(user: dict, profile: dict, now: datetime) -> dict | None:
    """The one nudge this user is due right now, most timely first, or None.
    Cheap: no LLM call - the idea is attached later, only for a nudge that is sent."""
    from db import has_notification, last_activity_at
    from services.festival_service import PROFILE_REGION_MAP, get_upcoming_festivals

    user_id = user["id"]
    markets = {PROFILE_REGION_MAP[r] for r in profile.get("compliance_regions") or profile.get("regions_detected") or []
               if r in PROFILE_REGION_MAP}
    for festival in get_upcoming_festivals(days_ahead=Config.NUDGE_OCCASION_DAYS, today=now.date()):
        if festival["region"] not in markets or festival["days_until"] < 1:
            continue
        key = f"occasion:{festival['name']}:{festival['date']}"
        if not has_notification(user_id, key):
            days = festival["days_until"]
            return {"kind": "occasion", "key": key, "festival": festival,
                    "title": f"{festival['name']} is {'tomorrow' if days == 1 else f'in {days} days'}",
                    "intro": "Here's a post you could have ready in time."}

    last_active = last_activity_at(user_id) or user.get("created_at")
    if last_active and now - last_active >= timedelta(days=Config.NUDGE_INACTIVE_DAYS):
        key = f"inactive:{last_active.date().isoformat()}"  # once per quiet spell
        if not has_notification(user_id, key):
            days = (now - last_active).days
            return {"kind": "inactive", "key": key,
                    "title": f"It's been {days} days since your last post",
                    "intro": "Here's an easy one to get going again."}

    # Mid-week, three days after the weekly email, so the two don't land together
    if now.weekday() == (Config.IDEAS_EMAIL_WEEKDAY + 3) % 7:
        year, week, _ = now.isocalendar()
        key = f"trend:{year}-W{week:02d}"
        if not has_notification(user_id, key):
            return {"kind": "trend", "key": key, "title": "Trending in your industry this week",
                    "intro": "Your industry is talking about this right now."}
    return None


def _idea_for(nudge: dict, user_id: int, profile: dict) -> dict | None:
    """The idea a nudge carries, from the user's feed (written now if they have none today)."""
    from services.idea_digest_service import pick_ideas
    from services.trend_service import generate_idea_feed

    feed, _usage = generate_idea_feed(user_id)
    groups = (feed or {}).get("groups") or {}
    if nudge["kind"] == "occasion":
        name = nudge["festival"]["name"]
        for idea in groups.get("dates") or []:
            if name.lower() in (idea.get("occasion") or "").lower():
                return {**idea, "group": "dates"}
        company = profile.get("company_name") or "our company"
        return {  # the feed has no idea for this occasion: a plain brief still gets them started
            "group": "dates", "title": f"A post for {name}", "occasion": name, "format": "image", "platform": "instagram",
            "summary": f"Mark {name} in a way that fits {company}.",
            "prompt": f"Create a respectful post for {name} ({nudge['festival']['date']}) from {company}. Connect the "
                      "occasion to what we do for our audience, in our brand voice, without a hard sell.",
        }
    if nudge["kind"] == "trend":
        trending = groups.get("trending") or []
        return {**trending[0], "group": "trending"} if trending else None
    picked = pick_ideas(feed, count=1)
    return {**picked[0][1], "group": picked[0][0]} if picked else None


def _may_email(user: dict, now: datetime) -> bool:
    from db import email_automation_enabled, emails_sent_since, last_activity_at

    if not email_automation_enabled("nudge_emails") or not Config.APP_BASE_URL or not user.get("email_ok"):
        return False
    last_active = last_activity_at(user["id"])
    if last_active and now - last_active < timedelta(days=_QUIET_DAYS_AFTER_ACTIVITY):
        return False
    return emails_sent_since(user["id"], now - timedelta(days=7)) < Config.IDEA_EMAILS_PER_WEEK


def nudge_user(user: dict, now: datetime | None = None) -> dict | None:
    """Create the nudge this user is due (bell + maybe email). None when
    nothing is due. Returns {"kind", "notification_id", "emailed"}."""
    from db import (
        create_idea_link,
        create_notification,
        get_user_brand_profile,
        last_notification_at,
        list_notifications,
        mark_notification_emailed,
    )

    now = now or datetime.now(timezone.utc)
    profile = get_user_brand_profile(user["id"])
    if not profile:
        return None
    latest = last_notification_at(user["id"])
    if latest and now - latest < timedelta(hours=20):  # one a day at most
        return None
    if list_notifications(user["id"], limit=1)["unread"] >= _MAX_UNREAD:  # they aren't reading them: stop piling up
        return None
    nudge = _due_nudge(user, profile, now)
    if not nudge:
        return None
    idea = _idea_for(nudge, user["id"], profile)
    if not idea:
        return None

    path = f"/dashboard?idea={create_idea_link(user['id'], idea)}"
    notification_id = create_notification(
        user["id"], nudge["kind"], nudge["title"], idea["title"], path, nudge["key"], when=now
    )
    if not notification_id:
        return None
    emailed = False
    if _may_email(user, now):
        from services.email_service import EmailService, email_context
        from services.idea_digest_service import _FORMATS, _PLATFORMS, unsubscribe_token

        base = Config.APP_BASE_URL.rstrip("/")
        try:
            details = {"nudge": nudge["kind"], "occasion": (nudge.get("festival") or {}).get("name"),
                       "idea": idea["title"]}
            with email_context(user_id=user["id"], details=details):
                EmailService().send_nudge_email(
                    to_email=user["email"], name=user.get("name") or "there", headline=nudge["title"], intro=nudge["intro"],
                    idea={
                        "title": idea["title"],
                        "summary": idea.get("summary") or idea.get("why_now") or "",
                        "makes": f"{_FORMATS.get(idea.get('format'), 'Text post')} for {_PLATFORMS.get(idea.get('platform'), 'LinkedIn')}",
                        "url": base + path,
                    },
                    settings_url=f"{base}/settings#notifications",
                    unsubscribe_url=f"{base}/api/notifications/nudges-email/unsubscribe/{unsubscribe_token(user['id'], 'nudges')}",
                )
            mark_notification_emailed(notification_id, when=now)
            emailed = True
        except Exception as err:  # noqa: BLE001 - the bell still has it
            logger.warning(f"Nudge email failed for user {user['id']}: {str(err)[:200]}")
    return {"kind": nudge["kind"], "notification_id": notification_id, "emailed": emailed}


def run_nudges(now: datetime | None = None) -> dict:
    """Scheduler: give every user the nudge they are due. One user's failure never stops the rest."""
    from db import users_for_nudges

    result = {"created": 0, "emailed": 0, "failed": 0}
    for user in users_for_nudges():
        try:
            outcome = nudge_user(user, now)
            if outcome:
                result["created"] += 1
                result["emailed"] += int(outcome["emailed"])
        except Exception as err:  # noqa: BLE001
            result["failed"] += 1
            logger.warning(f"Nudge failed for user {user['id']}: {str(err)[:200]}")
    if result["created"] or result["failed"]:
        logger.info(f"Nudges: {result}")
    return result
