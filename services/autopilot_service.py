"""Autopilot (AUTOPILOT_PLAN.md): posts prepared for the calendar's open days,
in Review first mode - nothing is published until the user approves it.

Off by default; the user turns it on in Settings -> Autopilot. Every hour the
background scheduler calls run(), which for each user with Autopilot on:

  1. takes the calendar's suggested days (services/calendar_service.py) in the
     next `days_ahead` days that have no post yet, at the platform's best
     time (services/best_time_service.py);
  2. picks an idea per day - the day's own suggestion, else the next unused
     one from Ideas for you - and writes the post with the same pipeline as
     Studio Chat (services/content_pipeline.py), text only;
  3. sends it for approval (the user's reviewer, else the user) and puts a
     note under the bell.

When the request is approved (on_approval_decided, called by the decide
endpoint) the approved platforms are scheduled at the day's slot. A post not
approved before its slot expires; it is never published on its own.

Text only: Instagram needs an image, so Autopilot writes for LinkedIn and
Facebook.
"""

import logging
from datetime import date, datetime, timedelta, timezone

from config import Config

logger = logging.getLogger(__name__)

TEXT_PLATFORMS = ("linkedin", "facebook")  # Instagram posts need an image
PLATFORM_NAMES = {"linkedin": "LinkedIn", "facebook": "Facebook", "instagram": "Instagram"}
MAX_DAYS_AHEAD = 7
MAX_POSTS_PER_RUN = 2  # per user per hour: a first run doesn't write a whole week at once
FAILURES_BEFORE_PAUSE = 2  # preparations failing in a row pause Autopilot
MIN_LEAD = timedelta(hours=2)  # time to approve before the slot
DEFAULT_HOUR = 10
RECENT_IDEAS = 40  # titles not used again


class AutopilotError(ValueError):
    """Why Autopilot can't be turned on or changed; shown to the user as is."""


# ── Settings ──────────────────────────────────────────────────────────────


def problems(user_id: int, platforms: list) -> list[str]:
    """What stops Autopilot from working for this user, in words for the page."""
    from db import get_user_brand_profile
    from services.publish_service import connections

    found = []
    if not get_user_brand_profile(user_id):
        found.append("Add your website on your brand profile first, so posts are written for your brand.")
    if not platforms:
        found.append("Choose at least one platform.")
    else:
        connected = connections(user_id)
        missing = [PLATFORM_NAMES[p] for p in platforms if not connected.get(p)]
        if missing and len(missing) == len(platforms):
            found.append(f"Connect {' or '.join(missing)} in Settings -> Social accounts first.")
    return found


def state(user_id: int) -> dict:
    """Everything Settings -> Autopilot shows."""
    from db import get_autopilot_settings, list_autopilot_posts
    from services.publish_service import connections

    settings = get_autopilot_settings(user_id)
    connected = connections(user_id)
    return {
        **settings,
        "platform_choices": [{"id": p, "name": PLATFORM_NAMES[p], "connected": bool(connected.get(p)),
                              "available": p in TEXT_PLATFORMS} for p in ("linkedin", "facebook", "instagram")],
        "problems": problems(user_id, settings["platforms"]) if settings["enabled"] else [],
        "posts": list_autopilot_posts(user_id, limit=10),
    }


def save(user_id: int, data: dict) -> dict:
    """Validated changes from the page; turning it off cancels what is waiting for approval."""
    from db import get_autopilot_settings, log_audit, save_autopilot_settings

    current = get_autopilot_settings(user_id)
    fields = {}
    if "platforms" in data:
        fields["platforms"] = [p for p in (data.get("platforms") or []) if p in TEXT_PLATFORMS]
    if "days_ahead" in data:
        try:
            fields["days_ahead"] = max(1, min(MAX_DAYS_AHEAD, int(data["days_ahead"])))
        except (TypeError, ValueError) as err:
            raise AutopilotError("Choose how many days ahead to prepare posts.") from err
    if "language" in data:
        from services.language_service import normalize

        fields["language"] = normalize(data.get("language"))
    if "tz_minutes" in data:
        try:
            fields["tz_minutes"] = max(-840, min(840, int(data["tz_minutes"])))
        except (TypeError, ValueError):
            pass
    if "enabled" in data:
        enabling = bool(data["enabled"])
        if enabling and not current["enabled"]:
            found = problems(user_id, fields.get("platforms", current["platforms"]))
            if found:
                raise AutopilotError(found[0])
        fields["enabled"] = enabling
    saved = save_autopilot_settings(user_id, **fields)
    if "enabled" in fields and fields["enabled"] != current["enabled"]:
        log_audit("autopilot.turned_on" if fields["enabled"] else "autopilot.turned_off", actor_user_id=user_id,
                  target_user_id=user_id, details={"platforms": saved["platforms"], "days_ahead": saved["days_ahead"]})
        if not fields["enabled"]:
            cancel_waiting(user_id, "Autopilot was turned off.")
    return saved


def cancel_waiting(user_id: int, reason: str) -> int:
    """Posts still waiting for approval are withdrawn (their requests closed)."""
    from db import decide_approval_request, get_approval_request, list_autopilot_posts, update_autopilot_post

    count = 0
    for post in list_autopilot_posts(user_id, limit=50, statuses=("preparing", "awaiting_approval")):
        if post["approval_request_id"]:
            req = get_approval_request(post["approval_request_id"])
            if req and req.get("status") == "pending":
                try:
                    decide_approval_request(request_id=req["id"], decision="rejected", comments=reason, decided_by="Autopilot")
                except Exception as err:  # noqa: BLE001 - the post is withdrawn either way
                    logger.warning(f"Autopilot could not close approval request {req['id']}: {err}")
        update_autopilot_post(post["id"], status="cancelled", error=reason)
        count += 1
    return count


# ── Planning: which days, when, and what ──────────────────────────────────


def _local_today(tz_minutes: int, now: datetime) -> date:
    return (now + timedelta(minutes=tz_minutes)).date()


def open_days(user_id: int, settings: dict, now: datetime) -> tuple[list[dict], list[dict]]:
    """The calendar's suggested days from today to `days_ahead` days out with
    nothing scheduled yet, in date order ([{date, idea}]), and the ideas from
    Ideas for you to fill days that have none."""
    from services.calendar_service import calendar_month

    today = _local_today(settings["tz_minutes"], now)
    last = today + timedelta(days=settings["days_ahead"])
    months = sorted({(today.year, today.month), (last.year, last.month)})
    found, seen = [], set()
    for year, month in months:
        data = calendar_month(user_id, year, month, settings["tz_minutes"], now)
        for day in data["days"]:
            when = date.fromisoformat(day["date"])
            if day["date"] in seen or not (today <= when <= last) or not day["suggestion"] or day["scheduled"]:
                continue
            seen.add(day["date"])
            found.append({"date": when, "idea": (day["suggestion"] or {}).get("idea")})
        plan_ideas = data.get("plan", {}).get("ideas") or []
    return sorted(found, key=lambda d: d["date"]), plan_ideas if months else []


def slot_time(user_id: int, settings: dict, day: date, now: datetime) -> datetime:
    """When the day's post goes out (UTC): the first platform's best hour that
    day, else 10:00 - in the user's own clock."""
    from services.best_time_service import best_times

    hour = DEFAULT_HOUR
    try:
        best = best_times(user_id, settings["tz_minutes"], now)
        for platform in settings["platforms"]:
            hours = (best.get(platform) or {}).get("hours") or []
            if hours:
                hour = int(hours[0])
                break
    except Exception as err:  # noqa: BLE001 - a default time beats no post
        logger.warning(f"Autopilot best times unavailable for user {user_id}: {err}")
    local = datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone(timedelta(minutes=settings["tz_minutes"])))
    return local.astimezone(timezone.utc)


def pick_idea(day_idea: dict | None, plan_ideas: list[dict], used: set[str]) -> dict | None:
    """The day's own idea, else the first one from Ideas for you not used lately."""
    for idea in ([day_idea] if day_idea else []) + list(plan_ideas):
        title = (idea or {}).get("title") or ""
        if title and title.lower() not in used and (idea.get("prompt") or title):
            return idea
    return None


# ── Preparing one post ────────────────────────────────────────────────────


def _reviewer_for(user_id: int) -> str | None:
    from db import get_user_by_id

    user = get_user_by_id(user_id)
    return (getattr(user, "approval_reviewer_email", None) or Config.APPROVAL_NOTIFY_EMAIL
            or (user.email if user else None))


def _items_from(result: dict, platforms: list) -> list[dict]:
    items = []
    for platform in platforms:
        entry = (result.get("content") or {}).get(platform) or {}
        caption = ((entry.get("caption") or {}).get("primary_caption") or "").strip()
        if caption:
            items.append({"platform": platform, "caption": caption, "media": "none",
                          "hashtags": (entry.get("hashtags") or {}).get("hashtags") or []})
    return items


def prepare(user_id: int, settings: dict, day: date, idea: dict, now: datetime) -> dict | None:
    """Writes one day's post and sends it for approval. None when that day
    was taken meanwhile. Raises content_pipeline.PostBlocked (plan or credit)."""
    from api import routes as r
    from db import (
        claim_autopilot_slot,
        create_approval_request,
        create_notification,
        delete_autopilot_post,
        log_audit,
        update_autopilot_post,
    )
    from services.approval_bundle_service import normalize_items, prepare_items, summary
    from services.content_pipeline import PostBlocked, generate_post

    slot_at = slot_time(user_id, settings, day, now)
    if slot_at - now < MIN_LEAD:
        return None  # too soon to approve in time
    post = claim_autopilot_slot(user_id, day.isoformat(), slot_at, settings["platforms"], idea.get("title"))
    if post is None:
        return None
    try:
        brief = idea.get("prompt") or idea.get("title")
        result = generate_post(user_id, {"story": brief, "platforms": settings["platforms"], "language": settings["language"],
                                         "selected_outputs": ["text"]})
        upload_folder = Config.UPLOAD_FOLDER
        items = normalize_items(_items_from(result, settings["platforms"]), upload_folder)
        rules = r.active_rules_for_user(user_id)

        def _check(caption, platform, active_rules):
            checked, _usage = r.check_caption(caption, platform, active_rules, r.refine_llm)
            return checked

        items = prepare_items(items, upload_folder, rules, _check)
        totals = summary(items)
        reviewer = _reviewer_for(user_id)
        req = create_approval_request(
            user_id=user_id, pipeline_client_id=f"autopilot-{post['id']}", platform=totals["platform"],
            asset_type=totals["asset_type"], caption=totals["caption"], story_context=brief,
            image_urls=totals["image_urls"], reviewer_email=reviewer, items=items,
        )
    except PostBlocked:
        delete_autopilot_post(post["id"])  # nothing was made: the day is free again
        raise
    except Exception as err:  # noqa: BLE001 - kept as failed so the day isn't retried every hour
        logger.warning(f"Autopilot could not prepare {day} for user {user_id}: {err}")
        update_autopilot_post(post["id"], status="failed", error=str(err))
        return None

    post = update_autopilot_post(post["id"], status="awaiting_approval", run_id=result.get("run_id"),
                                 approval_request_id=req["id"])
    local = slot_at.astimezone(timezone(timedelta(minutes=settings["tz_minutes"])))
    when = local.strftime("%a %d %b, %H:%M")
    create_notification(user_id, "autopilot", f"Autopilot prepared a post for {when}",
                        f'"{idea.get("title")}" is ready for review. Approve it to schedule it; nothing is posted until you do.',
                        f"/approve/{req['id']}", f"autopilot:{post['id']}")
    if reviewer and Config.APP_BASE_URL:
        try:
            from services.email_service import EmailService

            EmailService().send_approval_bundle_request(
                approval_url=f"{Config.APP_BASE_URL.rstrip('/')}/approve/{req['id']}", story=brief, items=items,
                company=None, to_email=reviewer)
        except Exception as err:  # noqa: BLE001 - the request is under the bell either way
            logger.warning(f"Autopilot approval email failed: {err}")
    log_audit("autopilot.prepared", actor_user_id=user_id, target_user_id=user_id,
              details={"day": day.isoformat(), "idea": idea.get("title"), "approval_request_id": req["id"]})
    return post


# ── The hourly run ────────────────────────────────────────────────────────


def expire_overdue(now: datetime) -> int:
    """Posts still waiting when their slot passed: never published on their own."""
    from db import AutopilotPost, Session, create_notification, engine, update_autopilot_post

    with Session(engine) as session:
        overdue = [(p.id, p.user_id, p.slot_date) for p in session.query(AutopilotPost).filter(
            AutopilotPost.status == "awaiting_approval",
            AutopilotPost.slot_at <= now.astimezone(timezone.utc).replace(tzinfo=None))]
    for post_id, user_id, slot_date in overdue:
        update_autopilot_post(post_id, status="expired", error="Not approved before its time.")
        create_notification(user_id, "autopilot", "An Autopilot post wasn't approved in time",
                            f"The post for {slot_date} was not published. You can still publish it from its approval request.",
                            "/approve", f"autopilot-expired:{post_id}")
    return len(overdue)


def run_for_user(user_id: int, now: datetime) -> int:
    """Prepares this user's next open days. -> posts prepared."""
    from db import get_autopilot_settings, list_autopilot_posts, save_autopilot_settings
    from services.content_pipeline import PostBlocked

    settings = get_autopilot_settings(user_id)
    if not settings["enabled"] or settings["paused_reason"]:
        return 0
    found = problems(user_id, settings["platforms"])
    if found:
        save_autopilot_settings(user_id, paused_reason=found[0])
        return 0
    days, plan_ideas = open_days(user_id, settings, now)
    taken = {p["slot_date"] for p in list_autopilot_posts(user_id, limit=60)}
    used = {(p["idea_title"] or "").lower() for p in list_autopilot_posts(user_id, limit=RECENT_IDEAS)}
    made = 0
    for day in days:
        if made >= MAX_POSTS_PER_RUN:
            break
        if day["date"].isoformat() in taken:
            continue
        idea = pick_idea(day["idea"], plan_ideas, used)
        if not idea:
            continue
        try:
            post = prepare(user_id, settings, day["date"], idea, now)
        except PostBlocked as blocked:
            save_autopilot_settings(user_id, paused_reason=str(blocked)[:300])
            break
        if post:
            made += 1
            used.add((idea.get("title") or "").lower())
    save_autopilot_settings(user_id, last_run_at=now.astimezone(timezone.utc).replace(tzinfo=None))
    _pause_after_failures(user_id)
    return made


def _pause_after_failures(user_id: int) -> None:
    """The last preparations all failed: stop and tell the user, rather than retry every hour."""
    from db import (
        AutopilotPost,
        Session,
        create_notification,
        engine,
        get_autopilot_settings,
        log_audit,
        save_autopilot_settings,
    )

    with Session(engine) as session:
        last = [(r.status, r.error) for r in session.query(AutopilotPost).filter(AutopilotPost.user_id == user_id)
                .order_by(AutopilotPost.created_at.desc(), AutopilotPost.id.desc()).limit(FAILURES_BEFORE_PAUSE)]
    if len(last) < FAILURES_BEFORE_PAUSE or any(status != "failed" for status, _ in last):
        return
    if get_autopilot_settings(user_id)["paused_reason"]:
        return
    reason = f"The last {FAILURES_BEFORE_PAUSE} posts couldn't be prepared ({(last[0][1] or 'unknown error')[:120]})."
    save_autopilot_settings(user_id, paused_reason=reason)
    create_notification(user_id, "autopilot", "Autopilot is paused", reason + " Check Settings -> Autopilot.",
                        "/settings#autopilot", f"autopilot-paused:{datetime.now(timezone.utc):%Y-%m-%d}")
    log_audit("autopilot.paused", target_user_id=user_id, details={"reason": reason})


def run(now: datetime | None = None) -> dict:
    """The scheduler's hourly call. -> {"prepared": n, "expired": n}"""
    from db import autopilot_user_ids

    now = now or datetime.now(timezone.utc)
    prepared = 0
    expired = expire_overdue(now)
    for user_id in autopilot_user_ids():
        try:
            prepared += run_for_user(user_id, now)
        except Exception as err:  # noqa: BLE001 - one user's problem must not stop the others
            logger.warning(f"Autopilot run failed for user {user_id}: {err}")
    return {"prepared": prepared, "expired": expired}


# ── Approval decided: schedule what was approved ──────────────────────────


def on_approval_decided(req: dict, now: datetime | None = None) -> dict | None:
    """Called after an approval request is decided. For an Autopilot post the
    approved platforms are scheduled at the day's slot. -> the updated post."""
    from db import autopilot_post_for_approval, log_audit, update_autopilot_post
    from services import schedule_service

    if not req or not str(req.get("pipeline_client_id") or "").startswith("autopilot-"):
        return None
    post = autopilot_post_for_approval(req["id"])
    if not post or post["status"] != "awaiting_approval":
        return None
    approved = [i for i in req.get("items") or [] if i.get("decision") == "approved"]
    if not approved:
        return update_autopilot_post(post["id"], status="rejected")
    now = now or datetime.now(timezone.utc)
    slot_at = datetime.fromisoformat(post["slot_at"])
    if slot_at - now < timedelta(minutes=2):
        return update_autopilot_post(post["id"], status="expired", error="Approved after its time.")
    try:
        scheduled = schedule_service.create(post["user_id"], [{k: i.get(k) for k in ("platform", "caption", "hashtags", "media", "images")}
                                                              for i in approved], slot_at.isoformat(), Config.UPLOAD_FOLDER,
                                            story=req.get("story_context"), run_id=post["run_id"], now=now)
    except schedule_service.ScheduleError as err:
        return update_autopilot_post(post["id"], status="failed", error=str(err))
    log_audit("autopilot.scheduled", actor_user_id=post["user_id"], target_user_id=post["user_id"],
              details={"day": post["slot_date"], "scheduled_post_id": scheduled["id"],
                       "platforms": [i["platform"] for i in approved]})
    return update_autopilot_post(post["id"], status="scheduled", scheduled_post_id=scheduled["id"])
