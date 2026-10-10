"""The weekly ideas email: three ideas from a user's "Ideas for you" feed
(services/trend_service.py), each with a button that opens Studio Chat with
the brief filled in (db.IdeaLink -> /dashboard?idea=<token>).

Who gets it and how often is decided in db.users_due_ideas_email: once a
week at most, never to users who switched it off or who were active in the
last two days. The scheduler only sends when WEEKLY_IDEAS_EMAIL=true.
"""

import logging
from datetime import datetime, timezone

from itsdangerous import BadSignature, URLSafeSerializer

from config import Config

logger = logging.getLogger(__name__)

IDEAS_PER_EMAIL = 3
_FORMATS = {"text": "Text post", "image": "Image post", "video": "Video"}
_PLATFORMS = {"linkedin": "LinkedIn", "instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}


# ── Unsubscribe links: signed, so they work without logging in ───────────────


def _signer(kind: str) -> URLSafeSerializer:
    return URLSafeSerializer(Config.SECRET_KEY, salt=f"{kind}-email-unsubscribe")


def unsubscribe_token(user_id: int, kind: str = "ideas") -> str:
    """kind: "ideas" (the weekly email) or "nudges" - a link only switches off its own kind."""
    return _signer(kind).dumps(user_id)


def user_from_unsubscribe_token(token: str, kind: str = "ideas") -> int | None:
    try:
        return int(_signer(kind).loads(token))
    except (BadSignature, TypeError, ValueError):
        return None


# ── Building and sending one email ───────────────────────────────────────────


def pick_ideas(feed: dict, count: int = IDEAS_PER_EMAIL) -> list[tuple[str, dict]]:
    """(group, idea) pairs for the email: what's timely first (trending, then
    an upcoming date), topped up with post types that always work."""
    groups = (feed or {}).get("groups") or {}
    trending, dates, playbook = (list(groups.get(g) or []) for g in ("trending", "dates", "playbook"))
    picked = [("trending", i) for i in trending[: count - 1]]
    picked += [("dates", i) for i in dates[:1]]
    picked += [("playbook", i) for i in playbook]
    picked += [("trending", i) for i in trending[count - 1:]]
    return picked[:count]


def _origin(group: str, idea: dict) -> str:
    if group == "trending":
        return "In the news" + (f" · {idea['source_title']}" if idea.get("source_title") else "")
    if group == "dates":
        return idea.get("occasion") or "Coming up"
    return (idea.get("post_type") or "Post type that works").split(":")[0]


def send_ideas_email(user_id: int, base_url: str) -> dict:
    """Write (or reuse) the user's idea feed and email them three ideas.
    {"sent": True, "ideas": n} or {"sent": False, "reason": ...}."""
    from db import (
        create_idea_link,
        get_ideas_email_settings,
        get_user_brand_profile,
        get_user_by_id,
        mark_ideas_email_sent,
    )
    from services.email_service import EmailService, email_context
    from services.trend_service import generate_idea_feed

    user = get_user_by_id(user_id)
    settings = get_ideas_email_settings(user_id)
    if not user or not settings:
        return {"sent": False, "reason": "user_not_found"}
    feed, _usage = generate_idea_feed(user_id)
    picked = pick_ideas(feed)
    if not picked:
        return {"sent": False, "reason": "no_ideas"}  # no brand profile, or nothing could be written

    base = base_url.rstrip("/")
    ideas = []
    for group, idea in picked:
        token = create_idea_link(user_id, {**idea, "group": group})
        ideas.append({
            "title": idea["title"],
            "summary": idea.get("summary") or idea.get("why_now") or "",
            "origin": _origin(group, idea),
            "makes": f"{_FORMATS.get(idea.get('format'), 'Text post')} for {_PLATFORMS.get(idea.get('platform'), 'LinkedIn')}",
            "url": f"{base}/dashboard?idea={token}",
        })
    details = {"industry": feed.get("industry"), "market": feed.get("region"),
               "trend_source": feed.get("trend_source"), "ideas": [i["title"] for i in ideas]}
    with email_context(user_id=user_id, details=details):
        EmailService().send_weekly_ideas_email(
            to_email=user.email,
            name=user.name or "there",
            company_name=(get_user_brand_profile(user_id) or {}).get("company_name"),
            ideas=ideas,
            dashboard_url=f"{base}/dashboard",
            settings_url=f"{base}/settings#notifications",
            unsubscribe_url=f"{base}/api/notifications/ideas-email/unsubscribe/{unsubscribe_token(user_id)}",
        )
    mark_ideas_email_sent(user_id)
    return {"sent": True, "ideas": len(ideas)}


# ── The weekly run (scheduler) ───────────────────────────────────────────────


def is_send_time(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return now.weekday() == Config.IDEAS_EMAIL_WEEKDAY and now.hour >= Config.IDEAS_EMAIL_HOUR_UTC


def run_weekly_digest(now: datetime | None = None) -> dict:
    """Send this week's email to everyone who is due one. Does nothing unless
    the weekly ideas email is switched on (Admin -> Emails, default
    WEEKLY_IDEAS_EMAIL) and it is the send day. One user's failure never
    stops the rest; a user who was sent one is not due again for six days."""
    from db import email_automation_enabled

    if not email_automation_enabled("weekly_ideas") or not is_send_time(now):
        return {"sent": 0, "skipped": 0, "failed": 0, "ran": False}
    if not Config.APP_BASE_URL:
        logger.warning("Weekly ideas email not sent: APP_BASE_URL is not set (the email's links need it).")
        return {"sent": 0, "skipped": 0, "failed": 0, "ran": False}
    from db import users_due_ideas_email

    result = {"sent": 0, "skipped": 0, "failed": 0, "ran": True}
    for user in users_due_ideas_email():
        try:
            outcome = send_ideas_email(user["id"], Config.APP_BASE_URL)
            result["sent" if outcome["sent"] else "skipped"] += 1
        except Exception as err:  # noqa: BLE001
            result["failed"] += 1
            logger.warning(f"Weekly ideas email failed for user {user['id']}: {str(err)[:200]}")
    if result["sent"] or result["failed"]:
        logger.info(f"Weekly ideas email: {result}")
    return result
