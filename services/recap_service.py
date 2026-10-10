"""The monthly recap email: what the user created last month, how it compares
with the month before, and what to try next.

The numbers come from the same place as the Content Calendar
(db.posts_created_between: one post per brief, refinements don't count), so
the two always agree. "What to try next" is two ideas from the user's feed,
each opening Studio Chat with its brief loaded, plus up to two plain tips
read off the month's numbers. Months are calendar months in UTC.

Sent once per user per month (User.recap_email_last_month), only to users
who created at least one post that month, and by the scheduler only when
MONTHLY_RECAP_EMAIL=true.
"""

import logging
from datetime import date, datetime, timedelta, timezone

from config import Config

logger = logging.getLogger(__name__)

_PLATFORMS = {"linkedin": "LinkedIn", "instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}
IDEAS_PER_RECAP = 2


def previous_month(today: date) -> tuple[int, int]:
    first = today.replace(day=1) - timedelta(days=1)
    return first.year, first.month


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=timezone.utc)
    return start, end


def _count(posts: list[dict]) -> dict:
    platforms: dict[str, int] = {}
    for post in posts:
        for platform in post["platforms"]:
            platforms[platform] = platforms.get(platform, 0) + 1
    return {
        "posts": len(posts),
        "images": sum(1 for p in posts if p["has_image"]),
        "videos": sum(1 for p in posts if p["has_video"]),
        "platforms": dict(sorted(platforms.items(), key=lambda kv: -kv[1])),
    }


def month_stats(user_id: int, year: int, month: int) -> dict:
    """What the user did in a month, with the month before for comparison."""
    from db import get_weekly_post_goal, posts_created_between, scheduled_posts_between

    start, end = _month_bounds(year, month)
    posts = posts_created_between(user_id, start, end)
    stats = _count(posts)
    prev_year, prev_month = previous_month(start.date())
    stats["previous_posts"] = len(posts_created_between(user_id, *_month_bounds(prev_year, prev_month)))
    stats["published"] = sum(1 for s in scheduled_posts_between(user_id, start, end) if s["status"] == "published")

    # Weeks (Monday to Sunday) that start in the month, and how many reached the weekly goal
    goal = get_weekly_post_goal(user_id)
    per_week: dict[date, int] = {}
    for post in posts:
        day = post["created_at"].date()
        monday = day - timedelta(days=day.weekday())
        per_week[monday] = per_week.get(monday, 0) + 1
    monday = start.date() + timedelta(days=(7 - start.weekday()) % 7)
    weeks = []
    while monday < end.date():
        weeks.append(monday)
        monday += timedelta(days=7)
    stats.update(goal=goal, weeks=len(weeks), weeks_goal_met=sum(1 for w in weeks if per_week.get(w, 0) >= goal),
                 month_name=start.strftime("%B"), month=month_key(year, month))
    return stats


def tips(stats: dict) -> list[str]:
    """Up to two plain suggestions read off the month's numbers."""
    out = []
    if stats["posts"] and not stats["images"] and not stats["videos"]:
        out.append("All your posts were text only. Try adding an image to one this month.")
    used = list(stats["platforms"])
    if len(used) == 1:
        others = [name for key, name in _PLATFORMS.items() if key != used[0]][:2]
        out.append(f"Everything went to {_PLATFORMS.get(used[0], used[0])}. The same brief can be written for "
                   f"{' or '.join(others)} in one go.")
    if stats["weeks"] and stats["weeks_goal_met"] < stats["weeks"]:
        out.append(f"You reached your goal of {stats['goal']} posts a week in {stats['weeks_goal_met']} of "
                   f"{stats['weeks']} weeks. The Content Calendar shows which days to post on.")
    return out[:2]


def comparison(stats: dict) -> str:
    now, before = stats["posts"], stats["previous_posts"]
    if not before:
        return "That's your first full month of posts."
    if now > before:
        return f"That's {now - before} more than the month before."
    if now < before:
        return f"That's {before - now} fewer than the month before."
    return "That's the same as the month before."


def send_recap_email(user_id: int, base_url: str, year: int, month: int) -> dict:
    """Email the user their recap for the month. {"sent": True} or
    {"sent": False, "reason": "no_posts"} when they created nothing that month."""
    from db import create_idea_link, get_user_brand_profile, get_user_by_id, mark_recap_email_sent
    from services.email_service import EmailService
    from services.idea_digest_service import _FORMATS, _origin, pick_ideas, unsubscribe_token
    from services.idea_digest_service import _PLATFORMS as PLATFORM_NAMES

    user = get_user_by_id(user_id)
    if not user:
        return {"sent": False, "reason": "user_not_found"}
    stats = month_stats(user_id, year, month)
    if not stats["posts"]:
        return {"sent": False, "reason": "no_posts"}

    base = base_url.rstrip("/")
    ideas = []
    try:  # the recap is still worth sending without ideas
        from services.trend_service import generate_idea_feed

        feed, _usage = generate_idea_feed(user_id)
        for group, idea in pick_ideas(feed, count=IDEAS_PER_RECAP):
            token = create_idea_link(user_id, {**idea, "group": group})
            ideas.append({
                "title": idea["title"],
                "summary": idea.get("summary") or idea.get("why_now") or "",
                "origin": _origin(group, idea),
                "makes": f"{_FORMATS.get(idea.get('format'), 'Text post')} for {PLATFORM_NAMES.get(idea.get('platform'), 'LinkedIn')}",
                "url": f"{base}/dashboard?idea={token}",
            })
    except Exception as err:  # noqa: BLE001
        logger.warning(f"Recap for user {user_id}: no ideas ({str(err)[:150]})")

    EmailService().send_monthly_recap_email(
        to_email=user.email,
        name=user.name or "there",
        company_name=(get_user_brand_profile(user_id) or {}).get("company_name"),
        stats={**stats, "platform_names": [f"{_PLATFORMS.get(k, k)} ({v})" for k, v in stats["platforms"].items()],
               "comparison": comparison(stats)},
        tips=tips(stats),
        ideas=ideas,
        calendar_url=f"{base}/calendar",
        settings_url=f"{base}/settings#notifications",
        unsubscribe_url=f"{base}/api/notifications/recap-email/unsubscribe/{unsubscribe_token(user_id, 'recap')}",
    )
    mark_recap_email_sent(user_id, stats["month"])
    return {"sent": True, "posts": stats["posts"], "ideas": len(ideas)}


def run_monthly_recap(now: datetime | None = None) -> dict:
    """Scheduler: send last month's recap to everyone due one. Does nothing
    unless MONTHLY_RECAP_EMAIL=true and it is RECAP_EMAIL_DAY or later, from
    the send hour. A user with no posts that month is marked done, not retried."""
    now = now or datetime.now(timezone.utc)
    result = {"sent": 0, "skipped": 0, "failed": 0, "ran": False}
    if not Config.MONTHLY_RECAP_EMAIL or now.day < Config.RECAP_EMAIL_DAY or \
            (now.day == Config.RECAP_EMAIL_DAY and now.hour < Config.IDEAS_EMAIL_HOUR_UTC):
        return result
    if not Config.APP_BASE_URL:
        logger.warning("Monthly recap not sent: APP_BASE_URL is not set (the email's links need it).")
        return result
    from db import mark_recap_email_sent, users_due_recap

    year, month = previous_month(now.date())
    key = month_key(year, month)
    result["ran"] = True
    for user in users_due_recap(key):
        try:
            outcome = send_recap_email(user["id"], Config.APP_BASE_URL, year, month)
            if outcome["sent"]:
                result["sent"] += 1
            else:
                result["skipped"] += 1
                mark_recap_email_sent(user["id"], key)  # nothing to recap: don't look again this month
        except Exception as err:  # noqa: BLE001
            result["failed"] += 1
            logger.warning(f"Monthly recap failed for user {user['id']}: {str(err)[:200]}")
    if result["sent"] or result["failed"]:
        logger.info(f"Monthly recap {key}: {result}")
    return result
