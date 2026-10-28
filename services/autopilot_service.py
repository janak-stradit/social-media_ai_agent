"""Autopilot (AUTOPILOT_PLAN.md): plans the user's posts, writes each one a
couple of days ahead and sends it for approval - nothing is published until it
is approved. Off by default; set up and run from the /autopilot page.

  1. The plan: from the user's set-up (posts a week, days off, the mix of
     tips / offers / news, festivals) the next two weeks' days get a "planned"
     row each, at the first platform's best time (best_time_service).
  2. Every hour (scheduler_thread.py -> run()), planned days within
     `days_ahead` are written with the same pipeline as Studio Chat
     (content_pipeline.generate_post: brand voice, language, plan and credit
     limits), plus an image when the user wants images and the monthly cap and
     plan allow one. The post goes out as an approval request (the user's
     reviewer, else the user) with a note under the bell and an email.
  3. When the request is decided (on_approval_decided, from the decide
     endpoint) the approved platforms are scheduled at the day's slot and the
     usual publisher sends them. A post not approved before its slot expires;
     it is never published on its own.

Autopilot pauses itself (paused_reason) when the plan or credit runs out, when
nothing is connected, or after 2 failures in a row; the user can pause it too.

Post statuses: planned -> preparing -> awaiting_approval -> scheduled -> posted,
or rejected (skipped / not approved), expired, cancelled, failed.
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone

from config import Config

logger = logging.getLogger(__name__)

PLATFORMS = ("linkedin", "facebook", "instagram")
TEXT_PLATFORMS = ("linkedin", "facebook")  # Instagram posts need an image
PLATFORM_NAMES = {"linkedin": "LinkedIn", "facebook": "Facebook", "instagram": "Instagram"}
KINDS = ("tips", "offers", "news")
KIND_NAMES = {"tips": "tips & how-tos", "offers": "products & offers", "news": "industry news", "festival": "a festival"}
PLAN_DAYS = 14  # how far ahead days are planned
MAX_DAYS_AHEAD = 7
MAX_POSTS_PER_WEEK = 7
MAX_POSTS_PER_RUN = 2  # per user per hour: a first run doesn't write a whole week at once
FAILURES_BEFORE_PAUSE = 2  # preparations failing in a row pause Autopilot
MIN_LEAD = timedelta(hours=2)  # time to approve before the slot
DEFAULT_HOUR = 10
RECENT_IDEAS = 40  # titles not used again
PAUSED_BY_USER = "Paused by you."


class AutopilotError(ValueError):
    """Why Autopilot can't be turned on or changed; shown to the user as is."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Settings ──────────────────────────────────────────────────────────────


def available_platforms(images: bool) -> tuple:
    return PLATFORMS if images else TEXT_PLATFORMS


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


def default_mix(posts_per_week: int) -> dict:
    """An even split of tips / offers / news that adds up to posts_per_week."""
    mix = {"tips": 0, "offers": 0, "news": 0}
    order = ("tips", "offers", "tips", "news", "offers", "tips", "news")
    for i in range(max(1, posts_per_week)):
        mix[order[i % len(order)]] += 1
    return mix


def _clean_words(value) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[,\n]", str(value or ""))
    out = []
    for item in items:
        word = " ".join(str(item).split())[:60]
        if word and word.lower() not in (w.lower() for w in out):
            out.append(word)
    return out[:15]


def clean_settings(raw: dict, current: dict | None = None) -> dict:
    """The fields given in `raw`, checked. Raises AutopilotError."""
    current = current or {}
    out = {}
    if "images" in raw:
        out["images"] = bool(raw["images"])
    images = out.get("images", current.get("images", False))
    if "posts_per_week" in raw:
        try:
            per_week = int(raw["posts_per_week"])
        except (TypeError, ValueError):
            raise AutopilotError("Choose how many posts a week.") from None
        if not 1 <= per_week <= MAX_POSTS_PER_WEEK:
            raise AutopilotError(f"Choose between 1 and {MAX_POSTS_PER_WEEK} posts a week.")
        out["posts_per_week"] = per_week
    if "platforms" in raw:
        out["platforms"] = list(dict.fromkeys(p for p in (raw.get("platforms") or []) if p in available_platforms(images)))
    elif "images" in out and not images:  # images turned off: Instagram can't be posted to
        out["platforms"] = [p for p in current.get("platforms", []) if p in TEXT_PLATFORMS]
    if "mix" in raw:
        mix = raw["mix"] if isinstance(raw["mix"], dict) else {}
        try:
            mix = {k: max(0, min(MAX_POSTS_PER_WEEK, int(mix.get(k) or 0))) for k in KINDS}
        except (TypeError, ValueError):
            mix = {}
        out["mix"] = mix if sum(mix.values()) else default_mix(out.get("posts_per_week", current.get("posts_per_week", 3)))
    if "festivals" in raw:
        out["festivals"] = bool(raw["festivals"])
    for key in ("topics", "avoid"):
        if key in raw:
            out[key] = _clean_words(raw[key])
    if "days_off" in raw:
        days_off = sorted({int(d) for d in (raw.get("days_off") or []) if str(d).isdigit() and 0 <= int(d) <= 6})
        if len(days_off) >= 7:
            raise AutopilotError("Leave at least one day to post on.")
        out["days_off"] = days_off
    if "image_cap_month" in raw:
        try:
            out["image_cap_month"] = max(0, min(200, int(raw["image_cap_month"])))
        except (TypeError, ValueError):
            raise AutopilotError("Enter how many images a month, or 0.") from None
    if "days_ahead" in raw:
        try:
            out["days_ahead"] = max(1, min(MAX_DAYS_AHEAD, int(raw["days_ahead"])))
        except (TypeError, ValueError) as err:
            raise AutopilotError("Choose how many days ahead to prepare posts.") from err
    if "language" in raw:
        from services.language_service import normalize

        out["language"] = normalize(raw.get("language"))
    if "tz_minutes" in raw:
        try:
            out["tz_minutes"] = max(-840, min(840, int(raw["tz_minutes"])))
        except (TypeError, ValueError):
            pass
    return out


PLAN_FIELDS = ("posts_per_week", "platforms", "mix", "festivals", "topics", "avoid", "days_off", "tz_minutes")


def save(user_id: int, data: dict, now: datetime | None = None) -> dict:
    """Validated changes from the page. Turning it on checks it can work;
    turning it off withdraws what is waiting; a changed plan is planned again;
    {"paused": true/false} pauses or resumes it."""
    from db import get_autopilot_settings, log_audit, save_autopilot_settings

    current = get_autopilot_settings(user_id)
    fields = clean_settings(data, current)
    if "enabled" in data:
        enabling = bool(data["enabled"])
        if enabling and not current["enabled"]:
            found = problems(user_id, fields.get("platforms", current["platforms"]))
            if found:
                raise AutopilotError(found[0])
        fields["enabled"] = enabling
    if "paused" in data:
        if data["paused"]:
            fields["paused_reason"] = PAUSED_BY_USER
        else:
            found = problems(user_id, fields.get("platforms", current["platforms"]))
            if found:
                raise AutopilotError(found[0])
            fields["paused_reason"] = None
    saved = save_autopilot_settings(user_id, **fields)
    if "enabled" in fields and fields["enabled"] != current["enabled"]:
        log_audit("autopilot.turned_on" if fields["enabled"] else "autopilot.turned_off", actor_user_id=user_id,
                  target_user_id=user_id, details={"platforms": saved["platforms"], "posts_per_week": saved["posts_per_week"]})
        if not fields["enabled"]:
            cancel_waiting(user_id, "Autopilot was turned off.")
    elif "paused_reason" in fields and fields["paused_reason"] != current["paused_reason"]:
        log_audit("autopilot.paused" if fields["paused_reason"] else "autopilot.resumed", actor_user_id=user_id,
                  target_user_id=user_id)
    if saved["enabled"] and not saved["paused_reason"] and (
            any(k in fields for k in PLAN_FIELDS) or fields.get("enabled") or "paused_reason" in fields):
        replan(user_id, now)
    return saved


def cancel_waiting(user_id: int, reason: str) -> int:
    """Posts still waiting for approval are withdrawn (their requests closed),
    and the days nothing was written for yet come off the plan."""
    from db import (
        decide_approval_request,
        delete_planned_autopilot_posts,
        get_approval_request,
        list_autopilot_posts,
        update_autopilot_post,
    )

    count = delete_planned_autopilot_posts(user_id)
    for post in list_autopilot_posts(user_id, limit=50, statuses=("preparing", "awaiting_approval")):
        if post["approval_request_id"]:
            _close_request(post["approval_request_id"], reason, get_approval_request, decide_approval_request)
        update_autopilot_post(post["id"], status="cancelled", error=reason)
        count += 1
    return count


def _close_request(request_id: int, reason: str, get_request, decide) -> None:
    req = get_request(request_id)
    if req and req.get("status") == "pending":
        try:
            decide(request_id=req["id"], decision="rejected", comments=reason, decided_by="Autopilot")
        except Exception as err:  # noqa: BLE001 - the post is withdrawn either way
            logger.warning(f"Autopilot could not close approval request {req['id']}: {err}")


# ── Planning: which days, when, and what kind ─────────────────────────────


def posting_days(per_week: int, days_off: list[int]) -> list[int]:
    """Which weekdays (0 = Monday) to post on: spread evenly over the open days."""
    open_days = [d for d in range(7) if d not in set(days_off or [])]
    count = min(per_week, len(open_days))
    if count >= len(open_days):
        return open_days
    step = len(open_days) / count
    return sorted({open_days[int(i * step)] for i in range(count)})


def kind_sequence(mix: dict, count: int) -> list[str]:
    """The week's kinds in an interleaved order (not all tips first)."""
    base = {k: int(mix.get(k) or 0) for k in KINDS}
    if not sum(base.values()):
        base = default_mix(count)
    pool, out = dict(base), []
    while len(out) < count:
        for kind in KINDS:
            if pool[kind] > 0 and len(out) < count:
                out.append(kind)
                pool[kind] -= 1
        if not sum(pool.values()):  # the mix adds up to fewer posts: start it again
            pool = dict(base)
    return out


def _best_hour(best: dict, platforms: list) -> int:
    for platform in platforms:
        hours = (best.get(platform) or {}).get("hours") or []
        if hours:
            return int(hours[0])
    return DEFAULT_HOUR


def plan_days(settings: dict, best: dict, now: datetime, regions: list[str] | None = None,
              days: int = PLAN_DAYS) -> list[dict]:
    """[{date, slot_at (UTC), kind, reason, topic}] for the next `days` days."""
    tz = timezone(timedelta(minutes=settings.get("tz_minutes") or 0))
    local_now = now.astimezone(tz)
    weekdays = posting_days(settings["posts_per_week"], settings.get("days_off") or [])
    hour = _best_hour(best, settings["platforms"])
    festival = _upcoming_festival(regions, local_now.date(), days) if settings.get("festivals", True) else None
    planned, week_kinds = [], {}
    for offset in range(days + 1):
        day = local_now.date() + timedelta(days=offset)
        if day.weekday() not in weekdays:
            continue
        week = day.isocalendar()[1]
        kinds = week_kinds.setdefault(week, kind_sequence(settings.get("mix") or {}, len(weekdays)))
        kind = kinds[weekdays.index(day.weekday()) % len(kinds)]
        when_local = datetime(day.year, day.month, day.day, hour, 0, tzinfo=tz)
        if when_local - local_now < MIN_LEAD:
            continue
        reason = f"Your {KIND_NAMES[kind]} day" + (", at a good time to post" if best else "")
        planned.append({"date": day, "slot_at": when_local.astimezone(timezone.utc), "kind": kind,
                        "reason": reason, "topic": None})
    if festival:  # the posting day closest before it (1-3 days ahead) becomes the festival post
        window = [p for p in planned if festival["date"] - timedelta(days=3) <= p["date"] < festival["date"]]
        if window:
            window[-1].update(kind="festival", topic=festival["name"],
                              reason=f"{festival['name']} is on {festival['date'].strftime('%a %d %b')}")
    return planned


def _upcoming_festival(regions: list[str] | None, today: date, days: int) -> dict | None:
    """The next celebration in the brand's markets: {name, date}."""
    try:
        from services.festival_service import next_celebration

        fest = next_celebration(regions or [], today=today, days_ahead=days + 3)
    except Exception as err:  # noqa: BLE001 - planning goes on without it
        logger.warning(f"Autopilot: festivals unavailable: {err}")
        return None
    if not fest:
        return None
    when = fest["date"] if isinstance(fest["date"], date) else date.fromisoformat(str(fest["date"]))
    return {"name": fest["name"], "date": when} if when > today else None


def plan_ahead(user_id: int, now: datetime | None = None) -> int:
    """Puts the next two weeks' missing days on the plan. -> days added."""
    from db import claim_autopilot_slot, get_autopilot_settings, get_user_brand_profile
    from services.best_time_service import best_times

    now = now or _now()
    settings = get_autopilot_settings(user_id)
    if not settings["enabled"] or settings["paused_reason"] or not settings["platforms"]:
        return 0
    try:
        best = best_times(user_id, settings["tz_minutes"], now)
    except Exception as err:  # noqa: BLE001 - a default time beats no post
        logger.warning(f"Autopilot best times unavailable for user {user_id}: {err}")
        best = {}
    profile = get_user_brand_profile(user_id) or {}
    regions = (profile.get("website_signals") or {}).get("regions_detected") or []
    added = 0
    for day in plan_days(settings, best, now, regions):
        if claim_autopilot_slot(user_id, day["date"].isoformat(), day["slot_at"], settings["platforms"], day["topic"],
                                status="planned", kind=day["kind"], reason=day["reason"]):
            added += 1
    return added


def replan(user_id: int, now: datetime | None = None) -> int:
    """After the set-up changes: the days nothing was written for are planned again."""
    from db import delete_planned_autopilot_posts

    delete_planned_autopilot_posts(user_id)
    return plan_ahead(user_id, now)


# ── What to write about ───────────────────────────────────────────────────

KIND_BRIEFS = {
    "tips": "Write a post with a practical tip, how-to or insight our customers would find genuinely useful{topic}.",
    "offers": "Write a post about one of our own products or services and the problem it solves for customers{topic}. "
              "No discounts or prices unless they are in our brand profile.",
    "news": "Write a short take on something current in our industry{topic} and what it means for our customers. "
            "Never invent news.",
    "festival": "Write a warm, respectful post for {festival} that connects the occasion to our business in a genuine "
                "way - not a generic greeting.",
}


def _plan_ideas(user_id: int, settings: dict, now: datetime) -> list[dict]:
    """Ideas for you (trending, post types that work, upcoming dates), via the calendar."""
    try:
        from services.calendar_service import calendar_month

        local = now + timedelta(minutes=settings["tz_minutes"])
        data = calendar_month(user_id, local.year, local.month, settings["tz_minutes"], now)
        return data.get("plan", {}).get("ideas") or []
    except Exception as err:  # noqa: BLE001 - the kind's own brief is used instead
        logger.warning(f"Autopilot ideas unavailable for user {user_id}: {err}")
        return []


def pick_idea(day_idea: dict | None, plan_ideas: list[dict], used: set[str]) -> dict | None:
    """The day's own idea, else the first one from Ideas for you not used lately."""
    for idea in ([day_idea] if day_idea else []) + list(plan_ideas):
        title = (idea or {}).get("title") or ""
        if title and title.lower() not in used and (idea.get("prompt") or title):
            return idea
    return None


def brief_for(post: dict, settings: dict, plan_ideas: list[dict], used: set[str]) -> tuple[str, str]:
    """(the brief for the pipeline, a short title) for one planned day: the
    festival; else one of the user's topics (rotating); else an unused idea
    from Ideas for you (tips and news); else the kind's own brief."""
    kind = post.get("kind") or "tips"
    avoid = settings.get("avoid") or []
    tail = f" Do not mention: {', '.join(avoid)}." if avoid else ""
    if kind == "festival":
        name = post.get("idea_title") or "the festival"
        return KIND_BRIEFS["festival"].format(festival=name) + tail, name
    topics = settings.get("topics") or []
    if topics:
        fresh = [t for t in topics if t.lower() not in used] or topics
        topic = fresh[post["id"] % len(fresh)]
        return KIND_BRIEFS[kind].format(topic=f", about {topic}") + tail, topic
    if kind in ("tips", "news"):
        idea = pick_idea(None, plan_ideas, used)
        if idea:
            return (idea.get("prompt") or idea["title"]) + tail, idea["title"]
    return KIND_BRIEFS[kind].format(topic="") + tail, KIND_NAMES[kind].capitalize()


# ── Preparing one post ────────────────────────────────────────────────────


def _reviewer_for(user_id: int) -> str | None:
    from db import get_user_by_id

    user = get_user_by_id(user_id)
    return (getattr(user, "approval_reviewer_email", None) or Config.APPROVAL_NOTIFY_EMAIL
            or (user.email if user else None))


def _items_from(result: dict, platforms: list, image_url: str | None) -> list[dict]:
    items = []
    for platform in platforms:
        entry = (result.get("content") or {}).get(platform) or {}
        caption = ((entry.get("caption") or {}).get("primary_caption") or "").strip()
        if not caption:
            continue
        hashtags = (entry.get("hashtags") or {}).get("hashtags") or []
        if image_url:
            items.append({"platform": platform, "caption": caption, "media": "single", "images": [image_url],
                          "hashtags": hashtags})
        elif platform in TEXT_PLATFORMS:  # Instagram without an image can't be posted
            items.append({"platform": platform, "caption": caption, "media": "none", "hashtags": hashtags})
    return items


def image_allowance(user_id: int, settings: dict, slot_at: datetime) -> tuple[bool, str]:
    """(may make an image, why not): the user's choice, the Autopilot monthly
    cap (for the month the post goes out in) and the plan's image allowance."""
    from db import autopilot_images_since, get_image_quota

    if not settings.get("images"):
        return False, "Images are off"
    month_start = slot_at.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if autopilot_images_since(user_id, month_start) >= int(settings.get("image_cap_month") or 0):
        return False, "This month's Autopilot images are used"
    quota = get_image_quota(user_id)
    if not quota.get("unlimited") and int(quota.get("remaining") or 0) <= 0:
        return False, "No images left on your plan right now"
    return True, ""


def _make_image(user_id: int, result: dict, platforms: list, title: str, media=None) -> tuple[str | None, float]:
    """One square image for the post from the pipeline's image prompt (logged like any image)."""
    from db import get_image_quota, get_user_brand_profile, log_image_generation
    from services.media_service import MediaGenerationService

    prompt = ""
    for platform in platforms:
        prompt = ((result.get("content") or {}).get(platform) or {}).get("media_prompt") or ""
        if prompt:
            break
    profile = get_user_brand_profile(user_id) or {}
    colors = ", ".join(str(c) for c in (profile.get("primary_colors") or [])[:3])
    full = (f"A high-quality, photo-realistic square social media image. Scene: {prompt or title}. "
            f"Style: {profile.get('visual_style') or 'clean, modern, professional'}."
            + (f" Brand colours as accents: {colors}." if colors else "")
            + " No text, words, letters or numbers anywhere in the image.")
    logo_path = None
    if profile.get("logo_url"):
        from services.brand_logo_service import _cached_logo

        logo_path = _cached_logo(profile["logo_url"])
    made = (media or MediaGenerationService()).create_preset_image(
        full, None, "1:1", logo_path=logo_path, model=get_image_quota(user_id).get("model"), require_reference=False)
    if not made.get("success"):
        return None, 0.0
    cost = float(made.get("cost") or 0.0)
    log_image_generation(user_id, cost, kind="image", platform=platforms[0], model=made.get("model"),
                         description=f"Autopilot: {title}"[:500], media_url=made["url"], prompt=made.get("prompt"),
                         clean_url=made.get("clean_url"), width=made.get("width"), height=made.get("height"))
    return made["url"], cost


def _day_taken(user_id: int, post: dict, tz_minutes: int) -> bool:
    """The user already scheduled something themselves for that day."""
    from db import scheduled_posts_between

    day = date.fromisoformat(post["slot_date"])
    tz = timezone(timedelta(minutes=tz_minutes))
    start = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc)
    return any(not p.get("autopilot") for p in scheduled_posts_between(user_id, start, start + timedelta(days=1)))


def prepare(user_id: int, settings: dict, post: dict, now: datetime, plan_ideas: list[dict] | None = None,
            used: set[str] | None = None, media=None) -> dict | None:
    """Writes one planned day's post and sends it for approval. None when it
    can't be done in time or the day was taken meanwhile. Raises
    content_pipeline.PostBlocked (plan or credit)."""
    from api import routes as r
    from db import create_approval_request, create_notification, log_audit, update_autopilot_post
    from services.approval_bundle_service import normalize_items, prepare_items, summary
    from services.content_pipeline import PostBlocked, generate_post

    slot_at = datetime.fromisoformat(post["slot_at"])
    if slot_at - now < MIN_LEAD:
        update_autopilot_post(post["id"], status="expired", error="It was too late to write it in time.")
        return None
    if _day_taken(user_id, post, settings["tz_minutes"]):
        update_autopilot_post(post["id"], status="cancelled", error="You already have a post scheduled that day.")
        return None
    brief, title = brief_for(post, settings, plan_ideas or [], used or set())
    platforms = [p for p in settings["platforms"] if p in available_platforms(settings.get("images"))]
    update_autopilot_post(post["id"], status="preparing", idea_title=title, platforms=platforms, error=None)
    try:
        result = generate_post(user_id, {"story": brief, "platforms": platforms, "language": settings["language"],
                                         "selected_outputs": ["text"]})
        image_url, cost = None, 0.0
        allowed, _why_not = image_allowance(user_id, settings, slot_at)
        if allowed:
            try:
                image_url, cost = _make_image(user_id, result, platforms, title, media=media)
            except Exception as err:  # noqa: BLE001 - the post can still go out as text
                logger.warning(f"Autopilot image for post {post['id']} failed: {err}")
        upload_folder = Config.UPLOAD_FOLDER
        items = normalize_items(_items_from(result, platforms, image_url), upload_folder)
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
        update_autopilot_post(post["id"], status="planned")  # nothing was made: the day stays on the plan
        raise
    except Exception as err:  # noqa: BLE001 - kept as failed so the day isn't retried every hour
        logger.warning(f"Autopilot could not prepare {post['slot_date']} for user {user_id}: {err}")
        update_autopilot_post(post["id"], status="failed", error=str(err))
        return None

    post = update_autopilot_post(post["id"], status="awaiting_approval", run_id=result.get("run_id"),
                                 approval_request_id=req["id"], image_url=image_url, cost_usd=cost,
                                 platforms=[i["platform"] for i in items])
    local = slot_at.astimezone(timezone(timedelta(minutes=settings["tz_minutes"])))
    when = local.strftime("%a %d %b, %H:%M")
    create_notification(user_id, "autopilot", f"Autopilot prepared a post for {when}",
                        f'"{title}" is ready for review. Approve it to schedule it; nothing is posted until you do.',
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
              details={"day": post["slot_date"], "idea": title, "approval_request_id": req["id"]})
    return post


# ── The hourly run ────────────────────────────────────────────────────────


def expire_overdue(now: datetime) -> int:
    """Posts still waiting when their slot passed: never published on their own."""
    from db import AutopilotPost, Session, create_notification, engine, update_autopilot_post

    cutoff = now.astimezone(timezone.utc).replace(tzinfo=None)
    with Session(engine) as session:
        overdue = [(p.id, p.user_id, p.slot_date, p.status) for p in session.query(AutopilotPost).filter(
            AutopilotPost.status.in_(("awaiting_approval", "planned")), AutopilotPost.slot_at <= cutoff)]
    for post_id, user_id, slot_date, status in overdue:
        if status == "planned":  # never written (paused, or out of credit): just off the plan
            update_autopilot_post(post_id, status="cancelled", error="It wasn't written in time.")
            continue
        update_autopilot_post(post_id, status="expired", error="Not approved before its time.")
        create_notification(user_id, "autopilot", "An Autopilot post wasn't approved in time",
                            f"The post for {slot_date} was not published. You can still publish it from its approval request.",
                            "/autopilot", f"autopilot-expired:{post_id}")
    return len(overdue)


def sync_posted() -> int:
    """Scheduled posts follow what the publisher did with them (for the page's counts)."""
    from db import autopilot_posts_with_status, get_scheduled_post, update_autopilot_post

    changed = 0
    for post in autopilot_posts_with_status(("scheduled",)):
        scheduled = get_scheduled_post(post["user_id"], post["scheduled_post_id"]) if post["scheduled_post_id"] else None
        new = {"published": "posted", "partial": "posted", "failed": "failed", "cancelled": "cancelled"}.get(
            (scheduled or {}).get("status"))
        if new:
            update_autopilot_post(post["id"], status=new)
            changed += 1
    return changed


def run_for_user(user_id: int, now: datetime, media=None) -> int:
    """Plans this user's next two weeks and writes the days now due. -> posts prepared."""
    from db import autopilot_posts_with_status, get_autopilot_settings, list_autopilot_posts, save_autopilot_settings
    from services.content_pipeline import PostBlocked

    settings = get_autopilot_settings(user_id)
    if not settings["enabled"] or settings["paused_reason"]:
        return 0
    found = problems(user_id, settings["platforms"])
    if found:
        save_autopilot_settings(user_id, paused_reason=found[0])
        return 0
    plan_ahead(user_id, now)
    today = (now + timedelta(minutes=settings["tz_minutes"])).date()
    due = [p for p in autopilot_posts_with_status(("planned",), user_id=user_id)
           if date.fromisoformat(p["slot_date"]) <= today + timedelta(days=settings["days_ahead"])]
    plan_ideas = _plan_ideas(user_id, settings, now) if due else []
    used = {(p["idea_title"] or "").lower() for p in list_autopilot_posts(user_id, limit=RECENT_IDEAS)
            if p["status"] != "planned"}
    made = tried = 0
    for post in due:
        if tried >= MAX_POSTS_PER_RUN:  # failures count too: a broken AI service isn't retried for every day
            break
        tried += 1
        try:
            prepared = prepare(user_id, settings, post, now, plan_ideas, used, media=media)
        except PostBlocked as blocked:
            save_autopilot_settings(user_id, paused_reason=str(blocked)[:300])
            break
        if prepared:
            made += 1
            used.add((prepared["idea_title"] or "").lower())
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
        last = [(r.status, r.error) for r in session.query(AutopilotPost).filter(
            AutopilotPost.user_id == user_id, AutopilotPost.status != "planned")
            .order_by(AutopilotPost.updated_at.desc(), AutopilotPost.id.desc()).limit(FAILURES_BEFORE_PAUSE)]
    if len(last) < FAILURES_BEFORE_PAUSE or any(status != "failed" for status, _ in last):
        return
    if get_autopilot_settings(user_id)["paused_reason"]:
        return
    reason = f"The last {FAILURES_BEFORE_PAUSE} posts couldn't be prepared ({(last[0][1] or 'unknown error')[:120]})."
    save_autopilot_settings(user_id, paused_reason=reason)
    create_notification(user_id, "autopilot", "Autopilot is paused", reason + " Check the Autopilot page.",
                        "/autopilot", f"autopilot-paused:{datetime.now(timezone.utc):%Y-%m-%d}")
    log_audit("autopilot.paused", target_user_id=user_id, details={"reason": reason})


def run(now: datetime | None = None) -> dict:
    """The scheduler's hourly call. -> {"prepared": n, "expired": n, "posted": n}"""
    from db import autopilot_user_ids

    now = now or _now()
    prepared = 0
    expired = expire_overdue(now)
    posted = sync_posted()
    for user_id in autopilot_user_ids():
        try:
            prepared += run_for_user(user_id, now)
        except Exception as err:  # noqa: BLE001 - one user's problem must not stop the others
            logger.warning(f"Autopilot run failed for user {user_id}: {err}")
    return {"prepared": prepared, "expired": expired, "posted": posted}


# ── The user's decisions ──────────────────────────────────────────────────


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
    now = now or _now()
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


def skip(user_id: int, post_id: int) -> dict:
    """The user doesn't want this day's post: off the plan, or its request closed."""
    from db import decide_approval_request, get_approval_request, get_autopilot_post, update_autopilot_post

    post = get_autopilot_post(user_id, post_id)
    if post is None:
        raise AutopilotError("That post wasn't found.")
    if post["status"] not in ("planned", "awaiting_approval"):
        raise AutopilotError("Only a planned post or one waiting for approval can be skipped.")
    if post["approval_request_id"]:
        _close_request(post["approval_request_id"], "Skipped on the Autopilot page.", get_approval_request,
                       decide_approval_request)
    return update_autopilot_post(post_id, status="rejected", error="Skipped by you.")


# ── What the pages show ───────────────────────────────────────────────────


def state(user_id: int, now: datetime | None = None) -> dict:
    """Everything the /autopilot page (and the calendar strip) shows."""
    from db import (
        autopilot_images_since,
        autopilot_posts_between,
        get_approval_request,
        get_autopilot_settings,
        list_autopilot_posts,
    )
    from services.publish_service import connections

    now = now or _now()
    settings = get_autopilot_settings(user_id)
    connected = connections(user_id)
    tz = timezone(timedelta(minutes=settings["tz_minutes"]))
    local = now.astimezone(tz)
    week_start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    plan = autopilot_posts_between(user_id, now - timedelta(days=30), week_start + timedelta(days=PLAN_DAYS + 1))
    for post in plan:  # the written caption, from its approval request
        post["caption"] = None
        if post["approval_request_id"]:
            req = get_approval_request(post["approval_request_id"]) or {}
            items = req.get("items") or []
            post["caption"] = (items[0].get("caption") if items else req.get("caption")) or None
    this_week = [p for p in plan if week_start <= datetime.fromisoformat(p["slot_at"]) < week_start + timedelta(days=7)]
    past = [p for p in plan if datetime.fromisoformat(p["slot_at"]) < now]
    upcoming = [p for p in plan if p["status"] in ("planned", "preparing", "awaiting_approval", "scheduled")
                and datetime.fromisoformat(p["slot_at"]) >= now]
    image_price = 0.0
    try:
        from db import get_image_quota, image_model_price

        image_price = image_model_price(get_image_quota(user_id).get("model"))
    except Exception:  # noqa: BLE001 - the page just doesn't show a price
        pass
    month_start = now.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        **settings,
        "platform_choices": [{"id": p, "name": PLATFORM_NAMES[p], "connected": bool(connected.get(p)),
                              "available": p in available_platforms(settings["images"])} for p in PLATFORMS],
        "problems": problems(user_id, settings["platforms"]) if settings["enabled"] else [],
        "posts": list_autopilot_posts(user_id, limit=10, statuses=("preparing", "awaiting_approval", "scheduled",
                                                                       "posted", "rejected", "expired", "failed")),
        "plan": plan,
        "needs_ok": [p for p in plan if p["status"] == "awaiting_approval"],
        "next": upcoming[0] if upcoming else None,
        "week": {"start": week_start.isoformat(), "goal": settings["posts_per_week"],
                 "done": sum(1 for p in this_week if p["status"] == "posted"),
                 "planned": sum(1 for p in this_week if p["status"] not in ("rejected", "cancelled"))},
        "image_use": {"used": autopilot_images_since(user_id, month_start), "cap": settings["image_cap_month"],
                      "price": image_price},
        "last30": {"posted": sum(1 for p in past if p["status"] == "posted"),
                   "approved": sum(1 for p in past if p["status"] in ("posted", "scheduled")),
                   "skipped": sum(1 for p in past if p["status"] in ("rejected", "expired"))},
    }
