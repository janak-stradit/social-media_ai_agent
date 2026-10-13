"""Lead previews (Admin -> Invitations -> "Prepare previews"): before
inviting a business, AVIR AI reads its website and drafts 3 LinkedIn posts
for it, plus an opening line for the email. The lead sees them on a page that
needs no login (/p/<token>); signing up from there copies their brand profile
and the drafts into the new account, so the product is already working for
them when they arrive.

Cost control:
- Preparing a preview costs two text-model calls (the website analysis the
  onboarding scan already uses, and one call for the posts).
- Images cost more, so they are made only when someone opens the page (a
  person with a browser: the page asks for them from JavaScript, which mail
  link-scanners don't run), at most Config.LEAD_PREVIEW_IMAGES_PER_DAY a day.
  Each image is charged to the admin who prepared the preview.
- The real logo is stamped on the images; the image model never draws one.
"""

import logging
import queue
import threading

from config import Config

logger = logging.getLogger(__name__)

WORKERS = 2  # previews prepared at the same time
_queue: "queue.Queue[int]" = queue.Queue()
_workers: list[threading.Thread] = []
_workers_lock = threading.Lock()

# Free mail providers: their domain is not the lead's website
_FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com", "live.com",
              "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "rediffmail.com", "zoho.com", "gmx.com"}

POSTS_SYSTEM_PROMPT = """You write sample LinkedIn posts for ONE specific company, to show them what an AI content
tool can do for them before they have signed up. You are given their brand profile (from their own website).

Write exactly 3 posts, each a different angle:
1. Their core product or service and the problem it solves for their customers.
2. Expertise: a practical tip, insight or behind-the-scenes look that their customers would find useful.
3. {third_angle}

Rules:
- Write as the company ("we"), in its brand voice. 60-130 words per caption. A strong first line, short
  paragraphs, one soft call to action at the end.
- Use only facts in the brand profile. Never invent statistics, client names, awards, prices or claims.
- Follow the brand's do/don't rules.
- hashtags: 3-5 relevant hashtags, each starting with #.
- image_prompt: a description of a photo-style image for the post - a real-world scene from their business
  or their customers' world, with the brand colours as accents. No text, words, letters, logos or brand names
  in the image.
- title: 2-4 words naming the post's angle.
- first_line: ONE sentence (max 25 words) to open a short email to this company, showing we looked at what
  they do - a specific, accurate observation based only on the profile, e.g. "Saw that Synkrama builds custom
  mobile apps for startups across Pune." No flattery, no question, no exclamation mark.

Return ONLY JSON: {{"first_line": "", "posts": [{{"title": "", "caption": "", "hashtags": [], "image_prompt": ""}}]}}"""

_REGION_WORDS = {"india": "India", "in": "India", "uae": "UAE/GCC", "united arab emirates": "UAE/GCC", "dubai": "UAE/GCC",
                 "abu dhabi": "UAE/GCC", "us": "US", "usa": "US", "united states": "US"}


# ── Preparing ───────────────────────────────────────────────────────────────
def queue_previews(contact_ids, admin_id: int, refresh: bool = False) -> dict:
    """Queues contacts for a preview; returns {"queued": n, "already_ready": n}."""
    from db import queue_lead_previews

    result = queue_lead_previews(contact_ids, admin_id, refresh=refresh)
    for preview_id in result["queued"]:
        _queue.put(preview_id)
    if result["queued"]:
        _ensure_workers()
    return {"queued": len(result["queued"]), "already_ready": result["already_ready"]}


def _ensure_workers() -> None:
    with _workers_lock:
        _workers[:] = [w for w in _workers if w.is_alive()]
        while len(_workers) < WORKERS:
            worker = threading.Thread(target=_work, name=f"lead-preview-{len(_workers)}", daemon=True)
            worker.start()
            _workers.append(worker)


def _work() -> None:
    while True:
        try:
            preview_id = _queue.get(timeout=60)
        except queue.Empty:
            return  # idle: the next queue_previews starts a new worker
        try:
            build_preview(preview_id)
        except Exception:
            logger.exception(f"Lead preview {preview_id} failed")
            try:
                from db import update_lead_preview

                update_lead_preview(preview_id, status="failed", error="Something went wrong. Try again.")
            except Exception:  # noqa: BLE001 - no database: nothing more to record
                logger.warning(f"Could not mark lead preview {preview_id} as failed")
        finally:
            _queue.task_done()


def wait_until_idle() -> None:
    """Tests: blocks until every queued preview is done."""
    _queue.join()


def website_for(contact: dict) -> str | None:
    """The lead's website: the sheet's Website column, else their email's
    domain when it's a company domain (not gmail.com etc.)."""
    site = (contact.get("website") or "").strip()
    if site:
        return site if site.lower().startswith(("http://", "https://")) else f"https://{site}"
    domain = (contact.get("email") or "").rsplit("@", 1)[-1].lower()
    if domain and "." in domain and domain not in _FREE_MAIL:
        return f"https://{domain}"
    return None


def _regions(profile: dict, contact: dict) -> list[str]:
    detected = ((profile.get("website_signals") or {}).get("regions_detected")) or []
    if detected:
        return detected
    for value in (contact.get("country"), contact.get("city")):
        region = _REGION_WORDS.get((value or "").strip().lower())
        if region:
            return [region]
    tld = (contact.get("email") or "").rsplit(".", 1)[-1].lower()
    return {"in": ["India"], "ae": ["UAE/GCC"]}.get(tld, [])


def build_preview(preview_id: int) -> None:
    """Website -> brand profile -> 3 draft posts + an email opening line."""
    from agents.website_analysis_agent import WebsiteAnalysisAgent
    from db import get_lead_preview, update_lead_preview
    from services.brand_profile_service import failure_message
    from services.website_scraper_service import scrape_website

    preview = get_lead_preview(preview_id)
    if preview is None or preview["contact"] is None:
        return
    contact = preview["contact"]
    update_lead_preview(preview_id, status="running", error=None)
    website = website_for(contact)
    scraped, reason = scrape_website(website) if website else (None, "invalid_url")
    if not scraped:
        # The site blocked us or has no text: the lead sheet's own description will do
        about = (contact.get("description") or "").strip()
        if len(about) < 40:
            update_lead_preview(preview_id, status="failed", website=website,
                                error=failure_message(reason) if website else "No website for this contact.")
            return
        scraped = {"title": contact.get("company") or "", "pages_scraped": [], "fetch_method": "manual",
                   "text_excerpt": "\n".join(filter(None, [
                       f"Company: {contact.get('company')}" if contact.get("company") else "",
                       f"Industry: {contact.get('industry')}" if contact.get("industry") else "",
                       f"Location: {contact.get('city')}" if contact.get("city") else "",
                       f"About: {about}"]))}
    try:
        profile = WebsiteAnalysisAgent().analyze(scraped)
    except Exception as err:  # noqa: BLE001
        logger.warning(f"Lead preview analysis failed for {website}: {err}")
        update_lead_preview(preview_id, status="failed", website=website, error=failure_message("analysis_failed"))
        return
    profile["company_name"] = profile.get("company_name") or contact.get("company") or None
    written, usage = write_posts(profile, contact)
    if not written["posts"]:
        update_lead_preview(preview_id, status="failed", website=website, profile=profile,
                            error="The posts could not be written. Try again.")
        return
    from db import _utcnow

    update_lead_preview(preview_id, status="ready", website=website, profile=profile, posts=written["posts"],
                        first_line=written["first_line"], cost_usd=float(usage.get("cost_usd") or 0.0),
                        generated_at=_utcnow(), images_started_at=None)


def write_posts(profile: dict, contact: dict, llm=None) -> tuple[dict, dict]:
    """({"first_line", "posts": [...]}, usage) - one text-model call."""
    import json

    from services.festival_service import next_celebration
    from services.llm_service import LLMService

    occasion = next_celebration(_regions(profile, contact), days_ahead=45)
    third = (f"A post for {occasion['name']} ({occasion['date']}) that connects the occasion to this company in a "
             "genuine, respectful way - not a generic greeting." if occasion
             else "A post that introduces the team or the people behind the company and what drives them.")
    brand = {key: profile.get(key) for key in ("company_name", "industry", "target_audience", "brand_voice_summary",
                                              "tagline", "key_themes", "core_products", "primary_colors", "visual_style",
                                              "content_dos", "content_donts")}
    brand["city"] = contact.get("city") or None
    user_prompt = "Brand profile:\n" + json.dumps(brand, ensure_ascii=False, indent=1)
    result, usage = (llm or LLMService()).generate_json(
        POSTS_SYSTEM_PROMPT.format(third_angle=third), user_prompt, temperature=0.6, max_tokens=1800,
        return_usage=True, reasoning_effort="low", on_partial=lambda _text: None)
    return normalize_posts(result, occasion), usage or {}


def normalize_posts(result, occasion: dict | None = None) -> dict:
    result = result if isinstance(result, dict) else {}
    posts = []
    for raw in (result.get("posts") or [])[:3]:
        if not isinstance(raw, dict) or not str(raw.get("caption") or "").strip():
            continue
        tags = raw.get("hashtags") if isinstance(raw.get("hashtags"), list) else []
        tags = [t if str(t).startswith("#") else f"#{t}" for t in (str(t).strip().replace(" ", "") for t in tags) if t][:5]
        posts.append({
            "title": str(raw.get("title") or "Post").strip()[:60],
            "caption": str(raw.get("caption")).strip()[:3000],
            "hashtags": tags,
            "image_prompt": str(raw.get("image_prompt") or "").strip()[:800],
            "image_url": None,
            "image_status": "waiting",
        })
    if occasion and len(posts) == 3:
        posts[2]["occasion"] = occasion["name"]
    first_line = " ".join(str(result.get("first_line") or "").split())[:240]
    return {"first_line": first_line, "posts": posts}


# ── Images, when the page is opened ─────────────────────────────────────────
def start_images(preview: dict) -> bool:
    """Starts making the images (in the background) the first time a person
    opens the preview. False when they're already made/being made, or the
    daily cap is reached."""
    from db import claim_lead_preview_images, lead_preview_images_today

    if preview.get("status") != "ready" or preview.get("images_started"):
        return False
    if lead_preview_images_today() >= Config.LEAD_PREVIEW_IMAGES_PER_DAY:
        return False
    if not claim_lead_preview_images(preview["id"]):
        return False
    threading.Thread(target=make_images, args=(preview["id"],), name=f"lead-preview-images-{preview['id']}",
                     daemon=True).start()
    return True


def _image_prompt(post: dict, profile: dict) -> str:
    colors = ", ".join(str(c) for c in (profile.get("primary_colors") or [])[:3])
    style = profile.get("visual_style") or "clean, modern, professional"
    return (f"A high-quality, photo-realistic square social media image for a {profile.get('industry') or 'business'} "
            f"company. Scene: {post.get('image_prompt') or post.get('title')}. Style: {style}."
            + (f" Use these brand colours as accents: {colors}." if colors else "")
            + " No text, words, letters or numbers anywhere in the image.")


def make_images(preview_id: int) -> None:
    from db import (
        get_lead_preview,
        lead_preview_images_today,
        log_image_generation,
        set_lead_preview_post,
        update_lead_preview,
    )
    from services.brand_logo_service import _cached_logo
    from services.media_service import MediaGenerationService

    preview = get_lead_preview(preview_id)
    if preview is None:
        return
    profile = preview["profile"]
    company = profile.get("company_name") or (preview["contact"] or {}).get("company") or "lead"
    logo_path = None
    if profile.get("logo_url"):
        try:
            logo_path = _cached_logo(profile["logo_url"])
        except Exception as err:  # noqa: BLE001 - no logo is fine; never let the AI draw one
            logger.warning(f"Lead preview logo for {company} unavailable: {err}")
    service = MediaGenerationService()
    cost = float(preview.get("cost_usd") or 0.0)
    for index, post in enumerate(preview["posts"]):
        if post.get("image_url"):
            continue
        if lead_preview_images_today() >= Config.LEAD_PREVIEW_IMAGES_PER_DAY:
            set_lead_preview_post(preview_id, index, image_status="skipped")
            continue
        set_lead_preview_post(preview_id, index, image_status="making")
        try:
            result = service.create_preset_image(_image_prompt(post, profile), None, "1:1", logo_path=logo_path,
                                                 require_reference=False)
        except Exception as err:  # noqa: BLE001
            result = {"success": False, "error": str(err)}
        if not result.get("success"):
            logger.warning(f"Lead preview image failed for {company}: {result.get('error')}")
            set_lead_preview_post(preview_id, index, image_status="failed")
            continue
        set_lead_preview_post(preview_id, index, image_url=result["url"], clean_url=result.get("clean_url"),
                              image_status="ready")
        cost += float(result.get("cost") or 0.0)
        if preview.get("created_by_user_id"):
            try:
                log_image_generation(preview["created_by_user_id"], float(result.get("cost") or 0.0), kind="image",
                                     platform="linkedin", model=result.get("model"),
                                     description=f"Lead preview: {company}"[:500], media_url=result["url"],
                                     prompt=result.get("prompt"), clean_url=result.get("clean_url"),
                                     width=result.get("width"), height=result.get("height"))
            except Exception as err:  # noqa: BLE001
                logger.warning(f"Could not record the lead preview image cost: {err}")
    update_lead_preview(preview_id, cost_usd=round(cost, 6))


# ── Signing up from a preview ───────────────────────────────────────────────
def claim_preview(token: str, user_id: int, email: str) -> dict | None:
    """Copies a lead's preview into their new account: the brand profile
    (so there's no website scan to wait for) and the 3 posts as a Studio Chat
    conversation. Only for the email the preview was made for, and only once.
    Returns {"conversation_id"} or None."""
    from db import (
        STARTER_TONE,
        create_notification,
        ensure_conversation,
        get_lead_preview,
        mark_lead_preview_claimed,
        save_run,
        save_user_brand_profile,
        set_brand_scan_status,
        update_user_profile,
    )

    preview = get_lead_preview(token=token) if token else None
    if not preview or preview["status"] != "ready" or not preview["contact"]:
        return None
    if preview["contact"]["email"] != (email or "").strip().lower():
        return None
    if not mark_lead_preview_claimed(preview["id"], user_id):
        return None
    profile = preview["profile"] or {}
    website = preview.get("website") or ""
    if profile and website:
        from services.brand_profile_service import _save_profile

        _save_profile(save_user_brand_profile, user_id, website, profile)
        set_brand_scan_status(user_id, "ready")
        update_user_profile(user_id, company_website=website)
    company = profile.get("company_name") or preview["contact"].get("company") or "your company"
    conversation_id = ensure_conversation(user_id, None, f"Starter posts for {company}")
    for post in preview["posts"]:
        entry = {"caption": {"primary_caption": post["caption"]}, "hashtags": {"hashtags": post.get("hashtags") or []}}
        if post.get("image_url"):
            entry["media"] = {"image": {"url": post["image_url"], "clean_url": post.get("clean_url"), "aspect": "1:1"}}
        save_run(story=f"{post['title']}: a LinkedIn post for {company}", tone=STARTER_TONE, platforms=["linkedin"],
                 content={"linkedin": entry}, user_id=user_id, conversation_id=conversation_id)
    create_notification(user_id, "brand_ready", f"Your starter posts for {company} are ready",
                        "We set up your brand profile from your website and saved the 3 posts we drafted for you. "
                        "Edit them, or create more.", f"/dashboard?conversation={conversation_id}",
                        f"starter:{preview['id']}")
    return {"conversation_id": conversation_id}
