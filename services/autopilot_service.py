"""Autopilot: plans a user's posts for the next two weeks, writes each one (and
its image) two days before its slot, and asks the user for their OK. Nothing is
published without that OK: an approved post goes on the Content Calendar as a
normal scheduled post (services/schedule_service.py) and the existing publisher
sends it; a post not approved by its deadline is held, never posted.

Life of a post (db.AutopilotPost.status):
    planned  -> a slot with a kind (tips / offers / news / festival) and a reason
    needs_ok -> written, waiting for the user (approve_by is the deadline)
    approved -> on the calendar (scheduled_post_id)
    posted / failed  <- what the publisher did with it
    skipped  -> the user said no
    held     -> not approved in time; nothing was posted

run_autopilot() is called hourly by scheduler_thread.py.
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone

from config import Config

logger = logging.getLogger(__name__)

PLATFORMS = ("linkedin", "instagram", "facebook")
NAMES = {"linkedin": "LinkedIn", "instagram": "Instagram", "facebook": "Facebook"}
KINDS = ("tips", "offers", "news")
KIND_NAMES = {"tips": "tips & how-tos", "offers": "products & offers", "news": "industry news", "festival": "a festival"}
PLAN_DAYS = 14                      # how far ahead slots are planned
WRITE_AHEAD = timedelta(hours=48)   # posts are written this long before their slot
DEADLINE_BEFORE = timedelta(hours=6)  # the OK is needed this long before the slot
MIN_DEADLINE = timedelta(hours=1)   # ...but never sooner than this from when it's written
MAX_WRITES_PER_RUN = 3              # per user per hourly run, so one user can't hold up the rest
DEFAULT_HOUR = 10                   # local time, when no best time is known
MAX_POSTS_PER_WEEK = 7


class AutopilotError(ValueError):
    """A problem the user can fix (shown to them as is)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Settings ────────────────────────────────────────────────────────────────
def default_mix(posts_per_week: int) -> dict:
    """An even split of tips / offers / news that adds up to posts_per_week."""
    mix = {"tips": 0, "offers": 0, "news": 0}
    order = ("tips", "offers", "tips", "news", "offers", "tips", "news")
    for i in range(max(1, posts_per_week)):
        mix[order[i % len(order)]] += 1
    return mix


def clean_settings(raw: dict) -> dict:
    """The fields a user may save, checked. Raises AutopilotError."""
    out = {}
    try:
        per_week = int(raw.get("posts_per_week", 3))
    except (TypeError, ValueError):
        raise AutopilotError("Choose how many posts a week.") from None
    if not 1 <= per_week <= MAX_POSTS_PER_WEEK:
        raise AutopilotError(f"Choose between 1 and {MAX_POSTS_PER_WEEK} posts a week.")
    out["posts_per_week"] = per_week
    platforms = [p for p in (raw.get("platforms") or []) if p in PLATFORMS]
    if not platforms:
        raise AutopilotError("Choose at least one platform.")
    out["platforms"] = list(dict.fromkeys(platforms))
    mix = raw.get("mix") if isinstance(raw.get("mix"), dict) else {}
    mix = {k: max(0, min(MAX_POSTS_PER_WEEK, int(mix.get(k) or 0))) for k in KINDS}
    out["mix"] = mix if sum(mix.values()) else default_mix(per_week)
    out["festivals"] = bool(raw.get("festivals", True))
    out["topics"] = _clean_words(raw.get("topics"))
    out["avoid"] = _clean_words(raw.get("avoid"))
    days_off = sorted({int(d) for d in (raw.get("days_off") or []) if str(d).isdigit() and 0 <= int(d) <= 6})
    if 7 - len(days_off) < 1:
        raise AutopilotError("Leave at least one day to post on.")
    out["days_off"] = days_off
    out["images"] = bool(raw.get("images", True))
    try:
        out["image_cap_month"] = max(0, min(200, int(raw.get("image_cap_month", 12))))
    except (TypeError, ValueError):
        out["image_cap_month"] = 12
    from services.language_service import normalize

    out["language"] = normalize(raw.get("language") or "en")
    try:
        out["tz_minutes"] = max(-14 * 60, min(14 * 60, int(raw.get("tz_minutes", 0))))
    except (TypeError, ValueError):
        out["tz_minutes"] = 0
    return out


def _clean_words(value) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[,\n]", str(value or ""))
    out = []
    for item in items:
        word = " ".join(str(item).split())[:60]
        if word and word.lower() not in (w.lower() for w in out):
            out.append(word)
    return out[:15]


# ── Planning ────────────────────────────────────────────────────────────────
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
    pool = {k: int(mix.get(k) or 0) for k in KINDS}
    if not sum(pool.values()):
        pool = default_mix(count)
    out = []
    while len(out) < count:
        for kind in KINDS:
            if pool[kind] > 0 and len(out) < count:
                out.append(kind)
                pool[kind] -= 1
        if not sum(pool.values()) and len(out) < count:  # mix adds up to fewer posts: start it again
            pool = {k: int(mix.get(k) or 0) for k in KINDS} if sum(int(mix.get(k) or 0) for k in KINDS) else default_mix(count)
    return out


def _best_hour(best: dict, platform: str) -> int:
    hours = (best.get(platform) or {}).get("hours") or []
    return int(hours[0]) if hours else DEFAULT_HOUR


def plan_slots(settings: dict, best: dict, now: datetime, regions: list[str] | None = None,
               days: int = PLAN_DAYS) -> list[dict]:
    """[{slot_at (UTC), platform, kind, reason, topic}] for the next `days` days."""
    tz = timezone(timedelta(minutes=settings.get("tz_minutes") or 0))
    local_now = now.astimezone(tz)
    weekdays = posting_days(settings["posts_per_week"], settings.get("days_off") or [])
    platforms = settings["platforms"]
    festival = _upcoming_festival(regions, local_now.date(), days) if settings.get("festivals", True) else None
    slots, platform_turn = [], 0
    week_kinds: dict[int, list[str]] = {}
    for offset in range(days + 1):
        day = local_now.date() + timedelta(days=offset)
        if day.weekday() not in weekdays:
            continue
        week = day.isocalendar()[1]
        kinds = week_kinds.setdefault(week, kind_sequence(settings.get("mix") or {}, len(weekdays)))
        index = sum(1 for s in slots if s["_week"] == week)
        kind = kinds[index % len(kinds)]
        platform = platforms[platform_turn % len(platforms)]
        platform_turn += 1
        hour = _best_hour(best, platform)
        when_local = datetime(day.year, day.month, day.day, hour, 0, tzinfo=tz)
        if when_local <= local_now + timedelta(hours=1):
            continue
        reason = (f"Your {KIND_NAMES[kind]} slot" if kind != "offers" else "Your weekly products & offers slot")
        if best.get(platform, {}).get("label"):
            reason += f", at a good time for {NAMES[platform]}"
        slots.append({"slot_at": when_local.astimezone(timezone.utc), "platform": platform, "kind": kind,
                      "reason": reason, "topic": None, "_week": week, "_day": day})
    if festival:  # the posting day closest before it (1-3 days ahead) becomes the festival post
        window = [s for s in slots if festival["date"] - timedelta(days=3) <= s["_day"] < festival["date"]]
        if window:
            slot = window[-1]
            slot.update(kind="festival", topic=festival["name"],
                        reason=f"{festival['name']} is on {festival['date'].strftime('%a %d %b')}")
    for slot in slots:
        slot.pop("_week", None)
        slot.pop("_day", None)
    return slots


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
    """Creates the missing planned slots for the next two weeks. Returns how many."""
    from db import create_autopilot_post, get_autopilot_settings, get_user_brand_profile
    from services.best_time_service import best_times

    now = now or _now()
    settings = get_autopilot_settings(user_id)
    if not settings["enabled"] or settings["paused"] or not settings["platforms"]:
        return 0
    try:
        best = best_times(user_id, settings.get("tz_minutes") or 0, now=now)
    except Exception as err:  # noqa: BLE001 - typical times are fine
        logger.warning(f"Autopilot: best times unavailable for user {user_id}: {err}")
        best = {}
    profile = get_user_brand_profile(user_id) or {}
    regions = (profile.get("website_signals") or {}).get("regions_detected") or []
    created = 0
    for slot in plan_slots(settings, best, now, regions):
        if create_autopilot_post(user_id, slot["slot_at"], slot["platform"], slot["kind"], slot["reason"], slot["topic"]):
            created += 1
    return created


def replan(user_id: int, now: datetime | None = None) -> int:
    """After the settings change: the not-yet-written slots are planned again."""
    from db import delete_future_autopilot_posts

    delete_future_autopilot_posts(user_id, ("planned",))
    return plan_ahead(user_id, now)


# ── Writing ─────────────────────────────────────────────────────────────────
WRITE_SYSTEM_PROMPT = """You write ONE social media post for a specific company, as the company ("we"), in its
brand voice. You are given the company's brand profile (from its own website) and what this post is about.

Rules:
- Platform: {platform}. {platform_rules}
- Use only facts in the brand profile or the topic given. Never invent statistics, prices, discounts, client
  names, awards, guarantees or dates.
- A strong first line, short paragraphs, one soft call to action at the end. Plain text, no markdown.
- Never mention these topics: {avoid}.
- hashtags: 3-5 relevant hashtags, each starting with #.
- image_prompt: a photo-style scene from the company's world for this post, with the brand colours as accents.
  No text, words, letters, logos or brand names in the image.
- title: 2-5 words naming the post.
{language}
Return ONLY JSON: {{"title": "", "caption": "", "hashtags": [], "image_prompt": ""}}"""

PLATFORM_RULES = {
    "linkedin": "Professional and useful; 80-180 words.",
    "instagram": "Warm and visual; 40-120 words; the hook in the first line.",
    "facebook": "Friendly and conversational; 40-120 words.",
}

KIND_BRIEFS = {
    "tips": "A practical tip, how-to or insight that the company's customers would find genuinely useful{topic}.",
    "offers": "One of the company's own products or services and the problem it solves for customers{topic}. "
              "No discounts or prices unless they are in the brand profile.",
    "news": "A short take on something current in the company's industry{topic}, and what it means for its "
            "customers. If no specific news is given, a timely observation about the industry - never invented news.",
    "festival": "A warm, respectful post for {festival} that connects the occasion to the company in a genuine way "
                "- not a generic greeting.",
}


def _topic_hint(post: dict, settings: dict, recent: list[str]) -> str:
    """The subject for this slot: the festival, else one of the user's topics (rotating), else open."""
    if post["kind"] == "festival":
        return ""
    topics = settings.get("topics") or []
    if not topics:
        return ""
    unused = [t for t in topics if t.lower() not in " ".join(recent).lower()] or topics
    return f", about {unused[post['id'] % len(unused)]}"


def write_post(post: dict, settings: dict, profile: dict, llm=None) -> tuple[dict, dict]:
    """({title, caption, hashtags, image_prompt}, usage) - one text-model call."""
    import json

    from services.language_service import instruction
    from services.llm_service import LLMService

    brand = {key: profile.get(key) for key in ("company_name", "industry", "target_audience", "brand_voice_summary",
                                              "tagline", "key_themes", "core_products", "primary_colors", "visual_style",
                                              "content_dos", "content_donts")}
    kind = post["kind"]
    brief = KIND_BRIEFS[kind].format(topic=_topic_hint(post, settings, []), festival=post.get("topic") or "the festival")
    system = WRITE_SYSTEM_PROMPT.format(
        platform=NAMES.get(post["platform"], post["platform"]), platform_rules=PLATFORM_RULES.get(post["platform"], ""),
        avoid=", ".join(settings.get("avoid") or []) or "nothing in particular",
        language=instruction(settings.get("language"), "caption"))
    # The brand profile leads (it is the same for every post of this user, so it can be cached)
    user = "Brand profile:\n" + json.dumps(brand, ensure_ascii=False, indent=1) + f"\n\nThis post: {brief}"
    result, usage = (llm or LLMService()).generate_json(system, user, temperature=0.6, max_tokens=900,
                                                        return_usage=True, reasoning_effort="low")
    result = result if isinstance(result, dict) else {}
    caption = str(result.get("caption") or "").strip()[:3000]
    if not caption:
        raise RuntimeError("The writer returned no caption.")
    tags = result.get("hashtags") if isinstance(result.get("hashtags"), list) else []
    tags = [t if t.startswith("#") else f"#{t}" for t in (str(t).strip().replace(" ", "") for t in tags) if t][:5]
    return {"title": str(result.get("title") or "").strip()[:60], "caption": caption, "hashtags": tags,
            "image_prompt": str(result.get("image_prompt") or "").strip()[:800]}, usage or {}


def _image_prompt(written: dict, profile: dict) -> str:
    colors = ", ".join(str(c) for c in (profile.get("primary_colors") or [])[:3])
    style = profile.get("visual_style") or "clean, modern, professional"
    return (f"A high-quality, photo-realistic square social media image for a {profile.get('industry') or 'business'} "
            f"company. Scene: {written.get('image_prompt') or written.get('title')}. Style: {style}."
            + (f" Use these brand colours as accents: {colors}." if colors else "")
            + " No text, words, letters or numbers anywhere in the image.")


def image_allowance(user_id: int, settings: dict, now: datetime | None = None) -> tuple[bool, str]:
    """(may make an image, why not). Respects the plan's limits and the Autopilot monthly cap."""
    from db import autopilot_images_since, get_image_quota

    if not settings.get("images", True):
        return False, "Images are off in Autopilot"
    now = now or _now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if autopilot_images_since(user_id, month_start) >= int(settings.get("image_cap_month") or 0):
        return False, "This month's Autopilot images are used"
    quota = get_image_quota(user_id)
    if not quota.get("unlimited") and int(quota.get("remaining") or 0) <= 0:
        return False, "No images left on your plan right now"
    return True, ""


def _checks(written: dict, platform: str, user_id: int, llm=None) -> tuple[str, list[dict]]:
    """(caption, [{label, ok, note}]): the code-based quality check, and the
    user's compliance rules when they have any (which may fix the caption)."""
    checks = []
    caption = written["caption"]
    try:
        from agents.reviewer_agent import ReviewerAgent

        review = ReviewerAgent().evaluate(platform, caption, written["hashtags"])
        issues = review.get("issues") or []
        checks.append({"label": "Quality", "ok": not issues,
                       "note": "Length, format and hashtags look right" if not issues else issues[0]})
    except Exception as err:  # noqa: BLE001
        logger.warning(f"Autopilot: quality check skipped: {err}")
    try:
        from services.compliance_service import active_rules_for_user, check_caption
        from services.llm_service import LLMService

        rules = active_rules_for_user(user_id)
        if rules:
            result, _usage = check_caption(caption, platform, rules, llm or LLMService())
            if result:
                caption = result.get("caption") or caption
                attention = int(result.get("needs_attention") or 0)
                checks.append({"label": "Rules", "ok": not attention,
                               "note": f"{result.get('rules_checked', len(rules))} checked"
                                       + (f", {attention} need{'s' if attention == 1 else ''} your attention" if attention else ", no issues")})
    except Exception as err:  # noqa: BLE001
        logger.warning(f"Autopilot: compliance check skipped: {err}")
    if re.search(r"\d+\s*%|\boff\b|\bfree\b|₹|\$", caption, re.IGNORECASE):
        checks.append({"label": "Check the facts", "ok": False, "note": "It mentions a price or offer: is it still true?"})
    return caption, checks


def draft(post: dict, now: datetime | None = None, llm=None, media=None) -> dict:
    """Writes one planned post (and its image when allowed); it then needs the user's OK."""
    from db import get_autopilot_settings, get_image_quota, get_user_brand_profile, log_image_generation, update_autopilot_post

    now = now or _now()
    user_id = post["user_id"]
    settings = get_autopilot_settings(user_id)
    profile = get_user_brand_profile(user_id) or {}
    update_autopilot_post(post["id"], status="drafting", error=None)
    try:
        written, _usage = write_post(post, settings, profile, llm=llm)
        caption, checks = _checks(written, post["platform"], user_id, llm=llm)
    except Exception as err:  # noqa: BLE001
        logger.warning(f"Autopilot: writing post {post['id']} failed: {err}")
        return update_autopilot_post(post["id"], status="planned", error="Couldn't write it yet; trying again soon")
    image_url, cost = None, 0.0
    allowed, why_not = image_allowance(user_id, settings, now)
    if allowed:
        try:
            from services.brand_logo_service import _cached_logo
            from services.media_service import MediaGenerationService

            logo_path = _cached_logo(profile["logo_url"]) if profile.get("logo_url") else None
            result = (media or MediaGenerationService()).create_preset_image(
                _image_prompt(written, profile), None, "1:1", logo_path=logo_path,
                model=get_image_quota(user_id).get("model"), require_reference=False)
            if result.get("success"):
                image_url, cost = result["url"], float(result.get("cost") or 0.0)
                log_image_generation(user_id, cost, kind="image", platform=post["platform"], model=result.get("model"),
                                     description=f"Autopilot: {written.get('title') or post['kind']}"[:500],
                                     media_url=image_url, prompt=result.get("prompt"), clean_url=result.get("clean_url"),
                                     width=result.get("width"), height=result.get("height"))
            else:
                checks.append({"label": "Image", "ok": False, "note": "The image couldn't be made; it can go out as text"})
        except Exception as err:  # noqa: BLE001
            logger.warning(f"Autopilot: image for post {post['id']} failed: {err}")
            checks.append({"label": "Image", "ok": False, "note": "The image couldn't be made; it can go out as text"})
    elif settings.get("images", True):
        checks.append({"label": "Image", "ok": True, "note": f"Text only: {why_not.lower()}"})
    if post["platform"] == "instagram" and not image_url:
        checks.append({"label": "Image", "ok": False, "note": "Instagram needs an image; add one or skip this post"})
    slot = datetime.fromisoformat(post["slot_at"])
    approve_by = max(slot - DEADLINE_BEFORE, now + MIN_DEADLINE)
    updated = update_autopilot_post(post["id"], status="needs_ok", caption=caption, hashtags=written["hashtags"],
                                    image_url=image_url, checks=checks, approve_by=approve_by, cost_usd=cost,
                                    topic=post.get("topic") or written.get("title") or None)
    _notify_needs_ok(updated)
    return updated


def _notify_needs_ok(post: dict) -> None:
    from db import create_notification

    when = datetime.fromisoformat(post["slot_at"]).strftime("%a %d %b")
    try:
        create_notification(post["user_id"], "autopilot", f"Autopilot: a {NAMES.get(post['platform'], '')} post needs your OK",
                            f"Planned for {when}. It won't go out until you approve it.", "/autopilot",
                            f"autopilot-ok-{post['id']}")
    except Exception as err:  # noqa: BLE001
        logger.warning(f"Autopilot: notification failed: {err}")


# ── The user's decisions ─────────────────────────────────────────────────────
def approve(user_id: int, post_id: int, caption: str | None = None, hashtags: list | None = None,
            upload_folder: str | None = None, now: datetime | None = None) -> dict:
    """Puts the post on the calendar for its slot. Raises AutopilotError."""
    from db import get_autopilot_post, update_autopilot_post
    from services.schedule_service import ScheduleError, create

    now = now or _now()
    post = get_autopilot_post(user_id, post_id)
    if post is None:
        raise AutopilotError("That post wasn't found.")
    if post["status"] not in ("needs_ok", "held"):
        raise AutopilotError("This post isn't waiting for your OK.")
    caption = (caption if caption is not None else post["caption"] or "").strip()
    tags = hashtags if hashtags is not None else post["hashtags"]
    slot = datetime.fromisoformat(post["slot_at"])
    when = slot if slot > now + timedelta(minutes=2) else now + timedelta(minutes=5)  # a held post goes out now
    item = {"platform": post["platform"], "caption": caption, "hashtags": tags,
            "media": "single" if post["image_url"] else "none", "images": [post["image_url"]] if post["image_url"] else []}
    try:
        scheduled = create(user_id, [item], when.isoformat(), upload_folder or Config.UPLOAD_FOLDER,
                           story=f"Autopilot: {post.get('topic') or post['kind']}", now=now)
    except ScheduleError as err:
        raise AutopilotError(str(err)) from err
    return update_autopilot_post(post_id, status="approved", caption=caption, hashtags=tags,
                                 scheduled_post_id=scheduled["id"], slot_at=when, error=None)


def skip(user_id: int, post_id: int) -> dict:
    from db import get_autopilot_post, update_autopilot_post

    post = get_autopilot_post(user_id, post_id)
    if post is None:
        raise AutopilotError("That post wasn't found.")
    if post["status"] in ("approved", "posted"):
        raise AutopilotError("This post is already on the calendar; cancel it there.")
    return update_autopilot_post(post_id, status="skipped")


def edit(user_id: int, post_id: int, caption: str | None = None, hashtags: list | None = None) -> dict:
    from db import get_autopilot_post, update_autopilot_post

    post = get_autopilot_post(user_id, post_id)
    if post is None or post["status"] not in ("needs_ok", "held"):
        raise AutopilotError("Only a post waiting for your OK can be edited here.")
    fields = {}
    if caption is not None:
        fields["caption"] = str(caption).strip()[:3000]
    if hashtags is not None:
        fields["hashtags"] = [t if str(t).startswith("#") else f"#{t}" for t in hashtags if str(t).strip()][:10]
    return update_autopilot_post(post_id, **fields)


# ── The hourly run ──────────────────────────────────────────────────────────
def hold_missed(now: datetime | None = None) -> int:
    """Posts still waiting after their deadline are held: never posted."""
    from db import autopilot_posts_with_status, create_notification, update_autopilot_post

    now = now or _now()
    held = 0
    for post in autopilot_posts_with_status(("needs_ok",)):
        if post["approve_by"] and datetime.fromisoformat(post["approve_by"]) < now:
            update_autopilot_post(post["id"], status="held")
            held += 1
            try:
                create_notification(post["user_id"], "autopilot", "Autopilot: a post wasn't approved in time",
                                    "It didn't go out. You can still post it or pick a new time.", "/autopilot",
                                    f"autopilot-held-{post['id']}")
            except Exception as err:  # noqa: BLE001
                logger.warning(f"Autopilot: notification failed: {err}")
    return held


def sync_published() -> int:
    """Approved posts follow what the publisher did with their scheduled post."""
    from db import autopilot_posts_with_status, get_scheduled_post, update_autopilot_post

    changed = 0
    for post in autopilot_posts_with_status(("approved",)):
        if not post["scheduled_post_id"]:
            continue
        scheduled = get_scheduled_post(post["user_id"], post["scheduled_post_id"]) or {}
        status = scheduled.get("status")
        new = {"published": "posted", "partial": "posted", "failed": "failed", "cancelled": "skipped"}.get(status)
        if new:
            update_autopilot_post(post["id"], status=new)
            changed += 1
    return changed


def run_for_user(user_id: int, now: datetime | None = None, llm=None, media=None) -> dict:
    from db import autopilot_posts_with_status

    now = now or _now()
    planned = plan_ahead(user_id, now)
    written, ready = 0, []
    for post in autopilot_posts_with_status(("planned",), user_id=user_id):
        if written >= MAX_WRITES_PER_RUN:
            break
        if datetime.fromisoformat(post["slot_at"]) - now <= WRITE_AHEAD:
            result = draft(post, now, llm=llm, media=media)
            written += 1
            if result and result["status"] == "needs_ok":
                ready.append(result)
    emailed = email_needs_ok(user_id, ready)
    return {"planned": planned, "written": written, "emailed": emailed}


def email_needs_ok(user_id: int, posts: list[dict]) -> bool:
    """One email for the posts this run wrote: they won't go out without the
    user's OK, so the user has to hear about them outside the app. Sent like an
    account email (not marketing), and only when the app's address is set."""
    if not posts or not Config.APP_BASE_URL:
        return False
    from db import get_autopilot_settings, get_user_by_id

    user = get_user_by_id(user_id)
    if not user or not user.email:
        return False
    tz = timezone(timedelta(minutes=get_autopilot_settings(user_id).get("tz_minutes") or 0))

    def local(iso: str) -> str:
        when = datetime.fromisoformat(iso).astimezone(tz)
        return f"{when:%a} {when.day} {when:%b}, {when:%I:%M %p}".replace(" 0", " ")

    count = len(posts)
    lines = [f"{NAMES.get(p['platform'], p['platform'])} post for {local(p['slot_at'])} - "
             f"approve by {local(p['approve_by'])}" for p in posts]
    lines.append("Nothing goes out until you approve it. A post that isn't approved in time is held, never posted.")
    try:
        from services.email_service import EmailService

        EmailService().send_billing_email(
            to_email=user.email, name=user.name or "there",
            subject=f"{count} post{'s' if count > 1 else ''} need{'' if count > 1 else 's'} your OK",
            headline=f"Autopilot wrote {count} post{'s' if count > 1 else ''} for you",
            lines=lines, button="Review and approve", url=Config.APP_BASE_URL.rstrip("/") + "/autopilot",
            footer="You get this because Autopilot is on. Pause or turn it off on the Autopilot page.")
        return True
    except Exception as err:  # noqa: BLE001 - the bell notification is still there
        logger.warning(f"Autopilot email to user {user_id} failed: {err}")
        return False


def run_autopilot(now: datetime | None = None) -> dict:
    from db import autopilot_user_ids

    now = now or _now()
    totals = {"users": 0, "planned": 0, "written": 0, "emailed": 0, "held": hold_missed(now), "synced": sync_published()}
    for user_id in autopilot_user_ids():
        try:
            result = run_for_user(user_id, now)
        except Exception as err:  # noqa: BLE001 - one user's problem never stops the rest
            logger.warning(f"Autopilot run failed for user {user_id}: {err}")
            continue
        totals["users"] += 1
        totals["planned"] += result["planned"]
        totals["written"] += result["written"]
        totals["emailed"] += int(result["emailed"])
    return totals


# ── The page's data ──────────────────────────────────────────────────────────
def overview(user_id: int, now: datetime | None = None) -> dict:
    from db import autopilot_images_since, get_autopilot_settings, list_autopilot_posts

    now = now or _now()
    settings = get_autopilot_settings(user_id)
    tz = timezone(timedelta(minutes=settings.get("tz_minutes") or 0))
    local = now.astimezone(tz)
    week_start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    posts = list_autopilot_posts(user_id, now - timedelta(days=30), week_start + timedelta(days=PLAN_DAYS + 1))
    this_week = [p for p in posts if week_start <= datetime.fromisoformat(p["slot_at"]) < week_start + timedelta(days=7)]
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last30 = [p for p in posts if datetime.fromisoformat(p["slot_at"]) < now]
    images_used = autopilot_images_since(user_id, month_start)
    image_price = 0.0
    try:
        from db import get_image_quota, image_model_price

        image_price = image_model_price(get_image_quota(user_id).get("model"))
    except Exception:  # noqa: BLE001
        pass
    upcoming = [p for p in posts if p["status"] in ("approved", "needs_ok", "planned")
                and datetime.fromisoformat(p["slot_at"]) >= now]
    return {
        "settings": settings,
        "posts": posts,
        "needs_ok": [p for p in posts if p["status"] in ("needs_ok", "held")],
        "next": upcoming[0] if upcoming else None,
        "week": {"start": week_start.isoformat(), "goal": settings["posts_per_week"],
                 "done": sum(1 for p in this_week if p["status"] == "posted"),
                 "planned": sum(1 for p in this_week if p["status"] not in ("skipped",))},
        "images": {"used": images_used, "cap": settings["image_cap_month"], "price": image_price},
        "last30": {"posted": sum(1 for p in last30 if p["status"] == "posted"),
                   "approved": sum(1 for p in last30 if p["status"] in ("posted", "approved")),
                   "skipped": sum(1 for p in last30 if p["status"] in ("skipped", "held"))},
    }
