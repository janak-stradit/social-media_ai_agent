"""Scheduled posts: Studio Chat "Schedule", the Content Calendar and the
scheduler that publishes them when their time comes.

A scheduled post holds one item per platform - its own caption, hashtags and
media (services/approval_bundle_service.py shapes and checks them) - so what
goes out is exactly what was scheduled:

    content_json = {"v": 2, "story", "items": [...], "source": {run_id, conversation_id},
                    "results": {platform: {"ok", "permalink", "account", "error", "at"}}}

Times are stored in UTC. The browser sends the moment with its offset; a time
without one is taken as UTC.

Status: pending -> publishing -> published (every platform went out),
partial (some did) or failed (none did); cancelled. "publishing" is set
before anything is sent, so a post is never sent twice if the scheduler stops
half-way - it stays "publishing" and can be retried by hand.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

MAX_AHEAD_DAYS = 366
PAST_SLACK = timedelta(minutes=2)  # "now" picked a moment ago still counts


class ScheduleError(ValueError):
    """A problem the person scheduling can fix (shown to them as is)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_when(value, now: datetime | None = None) -> datetime:
    """The moment to publish, as a naive UTC datetime (how it is stored)."""
    now = now or _now()
    try:
        when = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        raise ScheduleError("Choose a date and time.") from None
    when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)
    if when < now - PAST_SLACK:
        raise ScheduleError("That time has already passed - choose a later one.")
    if when > now + timedelta(days=MAX_AHEAD_DAYS):
        raise ScheduleError("Posts can be scheduled up to a year ahead.")
    return when.replace(tzinfo=None)


def check_items(user_id: int, items: list[dict]) -> None:
    """What would stop the post going out, found now instead of at publish time."""
    from services.post_edit_service import MAX_HASHTAGS, limit_for
    from services.publish_service import NAMES, PLATFORMS, connections, post_text

    connected = connections(user_id)
    for item in items:
        platform, name = item["platform"], NAMES.get(item["platform"], item["platform"])
        if platform not in PLATFORMS:
            raise ScheduleError(f"{name} posts can't be scheduled yet.")
        if not connected.get(platform):
            raise ScheduleError(f"Connect your {name} account in Settings before scheduling to it.")
        length, limit = len(post_text(item)), limit_for(platform)
        if length > limit:
            raise ScheduleError(f"{name}: the caption and hashtags are {length - limit} characters over the limit of {limit:,}.")
        if len(item.get("hashtags") or []) > MAX_HASHTAGS:
            raise ScheduleError(f"{name}: use at most {MAX_HASHTAGS} hashtags.")


def create(user_id: int, raw_items, when, upload_folder: str, story: str | None = None, run_id: int | None = None,
           conversation_id: int | None = None, now: datetime | None = None) -> dict:
    from db import create_scheduled_post, get_scheduled_post
    from services.approval_bundle_service import (
        BundleError,
        normalize_items,
        prepare_items,
    )

    scheduled_at = parse_when(when, now)
    try:
        items = normalize_items(raw_items, upload_folder)
    except BundleError as err:
        raise ScheduleError(str(err).replace("to send.", "to schedule.")) from err
    check_items(user_id, items)
    items = prepare_items(items, upload_folder)  # the PDF of a LinkedIn document carousel
    for item in items:  # these belong to an approval request, not to a scheduled post
        item.pop("decision", None)
        item.pop("comment", None)
    content = {"v": 2, "story": (story or "").strip()[:4000], "items": items, "results": {},
               "source": {"run_id": run_id, "conversation_id": conversation_id}}
    post = create_scheduled_post(user_id=user_id, platforms=[i["platform"] for i in items], scheduled_at=scheduled_at,
                                 content_json=content, run_id=run_id)
    return get_scheduled_post(user_id, post["id"])


def reschedule(user_id: int, post_id: int, when, now: datetime | None = None) -> dict:
    from db import get_scheduled_post, move_scheduled_post

    post = get_scheduled_post(user_id, post_id)
    if not post:
        raise LookupError("Scheduled post not found.")
    if post["status"] != "pending":
        raise ScheduleError("Only a post that is still waiting can be moved.")
    move_scheduled_post(user_id, post_id, parse_when(when, now))
    return get_scheduled_post(user_id, post_id)


def retry(user_id: int, post_id: int, now: datetime | None = None) -> dict:
    """Sends the platforms that didn't go out again, now."""
    from db import get_scheduled_post, requeue_scheduled_post

    post = get_scheduled_post(user_id, post_id)
    if not post:
        raise LookupError("Scheduled post not found.")
    if post["status"] not in ("failed", "partial", "publishing"):
        raise ScheduleError("This post has nothing left to send.")
    if not post["items"]:
        raise ScheduleError("This post was created before posts could be retried - create it again.")
    requeue_scheduled_post(user_id, post_id, (now or _now()).replace(tzinfo=None))
    return get_scheduled_post(user_id, post_id)


def _notify(post: dict, status: str, results: dict) -> None:
    from db import create_notification
    from services.publish_service import NAMES

    done = [NAMES.get(p, p) for p, r in results.items() if r.get("ok")]
    failed = {NAMES.get(p, p): r.get("error") for p, r in results.items() if not r.get("ok")}
    if status == "published":
        title, body = "Your scheduled post is live", f"Published on {', '.join(done)}."
    elif status == "partial":
        title = "Your scheduled post went out on some platforms"
        body = f"Published on {', '.join(done)}. Not published on " + "; ".join(f"{p}: {e}" for p, e in failed.items())
    else:
        title, body = "Your scheduled post could not be published", "; ".join(f"{p}: {e}" for p, e in failed.items())
    try:
        create_notification(post["user_id"], "scheduled_post", title, body[:500], "/calendar",
                            f"scheduled:{post['id']}:{status}:{len(done)}")
    except Exception as err:  # noqa: BLE001 - the bell is a courtesy; the post's status is the record
        logger.warning(f"Could not notify user {post['user_id']} about scheduled post {post['id']}: {err}")


def publish(post: dict, upload_folder: str, now: datetime | None = None) -> str:
    """Publishes a claimed post (status "publishing") platform by platform and
    records each result. Platforms already published (a retry) are skipped.
    Returns the new status."""
    from db import finish_scheduled_post
    from services.observability import log_event
    from services.publish_service import PublishError, publish_item

    content = post["content"]
    results = dict(content.get("results") or {})
    for item in content["items"]:
        platform = item["platform"]
        if (results.get(platform) or {}).get("ok"):
            continue
        at = (now or _now()).isoformat()
        try:
            out = publish_item(post["user_id"], item, upload_folder)
            results[platform] = {"ok": True, "permalink": out.get("permalink"), "post_id": out.get("post_id"),
                                 "account": out.get("account"), "format": out.get("format"), "at": at}
        except PublishError as err:
            results[platform] = {"ok": False, "error": str(err)[:400], "at": at}
        except Exception as err:  # one platform's surprise must not lose the others' results
            logger.exception(f"Scheduled post {post['id']}: unexpected error publishing to {platform}")
            results[platform] = {"ok": False, "error": f"Unexpected error: {str(err)[:200]}", "at": at}
        log_event("scheduled.publish", post_id=post["id"], platform=platform, status="published" if results[platform]["ok"] else "failed",
                  error=results[platform].get("error"))
    outcome = [r.get("ok") for r in results.values()]
    status = "published" if all(outcome) else "partial" if any(outcome) else "failed"
    content["results"] = results
    finish_scheduled_post(post["id"], status, content)
    _notify(post, status, results)
    return status


def publish_due(upload_folder: str, now: datetime | None = None) -> dict:
    """Scheduler: publish every multi-platform post whose time has come."""
    from db import claim_scheduled_post, due_scheduled_posts

    now = now or _now()
    counts = {"published": 0, "partial": 0, "failed": 0}
    for post in due_scheduled_posts(now.replace(tzinfo=None)):
        if not claim_scheduled_post(post["id"]):
            continue  # cancelled, moved or taken a moment ago
        try:
            counts[publish(post, upload_folder, now)] += 1
        except Exception:  # stays "publishing"; the next post still goes out
            logger.exception(f"Scheduled post {post['id']} could not be processed")
    return counts
