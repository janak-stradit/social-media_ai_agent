"""Formats a user's stored brand profile (see db.UserBrandProfile,
agents/website_analysis_agent.py) into a prompt section for Studio Chat
generation - the per-user equivalent of agents/story_agent.py's
_build_guidelines_block(). Only wired into the Studio Chat path
(api/routes.py's generate_content()), not the StradIT-specific
competitor-dashboard flow."""


import logging
import threading

logger = logging.getLogger(__name__)

# User-facing explanation for each scan failure code (see
# website_scraper_service.FAILURE_REASONS) - shown on onboarding and on the
# "My Brand Configuration" page instead of a silent empty profile.
SCRAPE_FAILURE_MESSAGES = {
    "invalid_url": "That doesn't look like a valid website address. Check the URL and try again.",
    "unsafe_url": "That address isn't a public website we can read.",
    "unreachable": "We couldn't reach your website. Check the URL and try again.",
    "timeout": "Your website took too long to respond. Try again in a minute.",
    "blocked": (
        "Your website's bot protection blocked our automated reader. "
        "Paste a short description of your business instead and we'll analyze that."
    ),
    "http_error": "Your website returned an error page. Check the URL and try again.",
    "not_html": "That URL didn't return a web page.",
    "thin_content": (
        "We reached your website but found almost no readable text (it may load its content with JavaScript). "
        "Paste a short description of your business instead."
    ),
    "analysis_failed": "We read your website but the AI analysis failed. Please try again in a minute.",
}

# user_ids with a scan currently running in this process - prevents a double
# submit (or onboarding + a manual re-scan) from running two scans at once.
_running_scans: set[int] = set()
_running_scans_lock = threading.Lock()


def failure_message(reason: str | None) -> str:
    return SCRAPE_FAILURE_MESSAGES.get(reason or "", SCRAPE_FAILURE_MESSAGES["analysis_failed"])


def start_brand_analysis_async(user_id: int, website: str) -> bool:
    """Runs run_brand_analysis in a background thread, so onboarding doesn't
    wait on the crawl + LLM analysis. Progress/outcome is readable via
    db.get_brand_scan_status. Returns False if a scan for this user is
    already running."""
    with _running_scans_lock:
        if user_id in _running_scans:
            return False
        _running_scans.add(user_id)

    from db import set_brand_scan_status

    set_brand_scan_status(user_id, "running")

    def _worker():
        try:
            run_brand_analysis(user_id, website, _already_claimed=True)
        except Exception:  # never let a background thread die silently (traceback is logged)
            logger.exception(f"Background brand analysis crashed for user {user_id}")
            set_brand_scan_status(user_id, "failed", "analysis_failed")
        finally:
            with _running_scans_lock:
                _running_scans.discard(user_id)

    threading.Thread(target=_worker, name=f"brand-scan-{user_id}", daemon=True).start()
    return True


def run_brand_analysis(
    user_id: int, website: str, manual_text: str | None = None, _already_claimed: bool = False
) -> tuple[bool, str | None]:
    """Scrapes `website` (or, with manual_text, analyzes the user's own
    pasted business description instead - the fallback when their site
    blocks automated access) and saves the resulting brand profile.

    Shared by onboarding (via start_brand_analysis_async) and the "My Brand
    Configuration" page's re-scan (POST /api/brand-profile/rescan). Records
    running/ready/failed on the user (db.set_brand_scan_status) either way.
    Returns (True, None) on success or (False, reason) - reason is a
    website_scraper_service.FAILURE_REASONS code or "analysis_failed";
    failure_message(reason) turns it into user-facing text."""
    from agents.website_analysis_agent import WebsiteAnalysisAgent
    from db import save_user_brand_profile, set_brand_scan_status
    from services.website_scraper_service import scrape_website

    if not _already_claimed:
        with _running_scans_lock:
            if user_id in _running_scans:
                return False, "already_running"
            _running_scans.add(user_id)
    try:
        set_brand_scan_status(user_id, "running")

        if manual_text and manual_text.strip():
            scraped = {"text_excerpt": manual_text.strip()[:15000], "pages_scraped": [], "fetch_method": "manual"}
        else:
            scraped, reason = scrape_website(website)
            if not scraped:
                set_brand_scan_status(user_id, "failed", reason)
                return False, reason

        try:
            profile = WebsiteAnalysisAgent().analyze(scraped)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Brand analysis failed for {website}: {e}")
            set_brand_scan_status(user_id, "failed", "analysis_failed")
            return False, "analysis_failed"

        _save_profile(save_user_brand_profile, user_id, website, profile)
        set_brand_scan_status(user_id, "ready")
        return True, None
    finally:
        if not _already_claimed:
            with _running_scans_lock:
                _running_scans.discard(user_id)


def _save_profile(save_user_brand_profile, user_id: int, website: str, profile: dict) -> None:
    save_user_brand_profile(
        user_id,
        website=website,
        company_name=profile.get("company_name"),
        industry=profile.get("industry"),
        target_audience=profile.get("target_audience"),
        brand_voice_summary=profile.get("brand_voice_summary"),
        key_themes=profile.get("key_themes"),
        core_products=profile.get("core_products"),
        primary_colors=profile.get("primary_colors"),
        content_dos=profile.get("content_dos"),
        content_donts=profile.get("content_donts"),
        suggested_post_ideas=profile.get("suggested_post_ideas"),
        tagline=profile.get("tagline"),
        visual_style=profile.get("visual_style"),
        fonts=profile.get("fonts"),
        logo_url=profile.get("logo_url"),
        website_signals=profile.get("website_signals"),
        industry_category_detected=profile.get("industry_category"),
    )


POST_IDEA_CATEGORIES = ("product", "thought_leadership", "story", "promo", "event", "tips")
_MAX_IDEA_POOL = 24  # most recent ideas kept on the profile (newest first)

POST_IDEA_SYSTEM_PROMPT = """You are a senior social media strategist for ONE specific company. Using its brand
context below, write {count} fresh, ready-to-use post ideas - the kind a strategist who deeply knows this
business would pitch this week.

Rules:
- Ground every idea in the company's ACTUAL products/services, audience, markets and themes - name them.
  Nothing generic that could fit any company.
- Cover {count} DIFFERENT angles - no two ideas in the same category. Categories: product,
  thought_leadership, story, promo, event, tips.
- Do not repeat or lightly reword any idea listed under ALREADY SHOWN, or topics under RECENTLY POSTED.
- If an upcoming occasion in their markets is listed and genuinely fits the brand, at most ONE idea may use it.
- Follow the brand's Do/Don't rules and every compliance rule. Never invent statistics, results,
  awards or certifications - only cite credentials listed as verified.
- greeting: one short, warm welcome line (max 10 words) addressed to the company by name, specific to
  its world - not a generic "ready to create content?".

Each idea: category, title (2-4 words), summary (one sentence preview), prompt (a complete 2-4 sentence
campaign brief naming the specific product/service, audience and goal - pasteable straight into a content
generator).

Return ONLY JSON: {{"greeting": "", "ideas": [{{"category": "", "title": "", "summary": "", "prompt": ""}}]}}"""


def generate_post_ideas(user_id: int, exclude_titles: list[str] | None = None, count: int = 4) -> tuple[list, str | None, dict]:
    """Fresh "Start from an idea" cards from the user's WHOLE brand profile
    (products, audience, voice, dos/don'ts, markets, credentials, compliance
    rules), avoiding ideas already shown and topics recently posted, with
    upcoming occasions in their markets as optional hooks. New ideas are
    prepended to the profile's idea pool, so a reload shows them too.

    Returns (ideas, greeting, usage) - ([], None, {}) without a profile."""
    from db import get_history, get_user_brand_profile, set_brand_post_ideas
    from services.festival_service import get_upcoming_festivals
    from services.llm_service import LLMService

    profile = get_user_brand_profile(user_id)
    if not profile:
        return [], None, {}

    brand_block = build_brand_profile_block(user_id)
    shown = [t for t in (exclude_titles or []) if t] + [i.get("title", "") for i in profile.get("suggested_post_ideas") or []]
    recent = [r.get("story", "")[:140] for r in get_history(limit=10, user_id=user_id) if r.get("story")]

    # festival_service labels regions "USA"/"India"; it has no UAE/GCC calendar yet
    festival_regions = {"US": "USA", "India": "India"}
    markets = {festival_regions[r] for r in profile.get("compliance_regions") or profile.get("regions_detected") or [] if r in festival_regions}
    occasions = [f"{f['name']} ({f['date']}, {f['region']})" for f in get_upcoming_festivals(days_ahead=30) if f["region"] in markets]

    user_prompt = (
        f"{brand_block}\n\n"
        f"ALREADY SHOWN (do not repeat): {'; '.join(dict.fromkeys(shown)) or 'none'}\n"
        f"RECENTLY POSTED (avoid these topics): {' | '.join(recent) or 'none'}\n"
        f"UPCOMING OCCASIONS IN THEIR MARKETS (next 30 days): {'; '.join(occasions[:6]) or 'none'}"
    )
    result, usage = LLMService().generate_json(
        POST_IDEA_SYSTEM_PROMPT.format(count=count), user_prompt, temperature=0.9, max_tokens=2500, return_usage=True
    )

    shown_keys = {s.strip().lower() for s in shown}
    ideas = []
    for idea in result.get("ideas") or []:
        if not isinstance(idea, dict) or not idea.get("prompt"):
            continue
        title = (idea.get("title") or "Content Idea").strip()
        if title.lower() in shown_keys:
            continue
        ideas.append(
            {
                "category": idea.get("category") if idea.get("category") in POST_IDEA_CATEGORIES else "tips",
                "title": title,
                "summary": (idea.get("summary") or "").strip(),
                "prompt": idea["prompt"].strip(),
            }
        )
    ideas = ideas[:count]

    if ideas:
        pool = ideas + [i for i in profile.get("suggested_post_ideas") or [] if i.get("title") not in {n["title"] for n in ideas}]
        set_brand_post_ideas(user_id, pool[:_MAX_IDEA_POOL])

    greeting = (result.get("greeting") or "").strip() or None
    return ideas, greeting, usage or {}


def build_brand_profile_block(user_id: int | None) -> str:
    """Returns "" when the user has no saved profile (never scraped, scrape
    failed, or no user_id) so callers can skip the section entirely."""
    if not user_id:
        return ""

    try:
        from db import get_user_brand_profile

        profile = get_user_brand_profile(user_id)
    except Exception:
        return ""

    if not profile:
        return ""

    lines = ["\n--- USER'S BRAND CONTEXT (derived from their website) ---"]
    if profile.get("company_name"):
        name = profile["company_name"]
        lines.append(
            f'Company name: {name} - this IS the actual company; refer to it by this name '
            f'(e.g. "At {name}, we..."), never by any brand-voice/tone label and never as StradIT.'
        )
    if profile.get("tagline"):
        lines.append(f'Tagline: "{profile["tagline"]}"')
    if profile.get("industry"):
        lines.append(f"Industry: {profile['industry']}")
    if profile.get("target_audience"):
        lines.append(f"Target audience: {profile['target_audience']}")
    if profile.get("brand_voice_summary"):
        lines.append(f"Brand voice: {profile['brand_voice_summary']}")
    if profile.get("visual_style"):
        lines.append(f"Visual style: {profile['visual_style']}")
    if profile.get("key_themes"):
        lines.append(f"Recurring themes: {', '.join(profile['key_themes'])}")
    if profile.get("core_products"):
        lines.append(f"Core Products/Services: {', '.join(profile['core_products'])}")
    if profile.get("primary_colors"):
        lines.append(f"Brand colors: {', '.join(profile['primary_colors'])}")
    if profile.get("content_dos"):
        lines.append("Do: " + "; ".join(profile["content_dos"]))
    if profile.get("content_donts"):
        lines.append("Don't: " + "; ".join(profile["content_donts"]))
    if profile.get("regions_detected"):
        lines.append(f"Markets: {', '.join(profile['regions_detected'])}")
    if profile.get("certifications"):
        lines.append(
            "Verified credentials (published on their own website - the ONLY certifications/accreditations "
            f"content may cite): {', '.join(profile['certifications'])}"
        )
    if profile.get("site_disclaimers"):
        lines.append(
            "Disclaimers this company already publishes - when a post touches the same topic (returns, "
            "health outcomes, offers), reuse the relevant one verbatim rather than writing a new one: "
            + " | ".join(profile["site_disclaimers"][:4])
        )
    lines.append(
        "Never invent statistics, research figures, certifications or awards - only use facts from the "
        "brief or this brand context."
    )
    from services.compliance_service import active_rules, build_compliance_block

    compliance_block = build_compliance_block(active_rules(profile))
    if compliance_block:
        lines.append(compliance_block)
    lines.append(
        "Write and design content consistent with this brand context - the underlying "
        "request/topic still comes first, this is guidance on tone and style, not a "
        "replacement for it."
    )
    lines.append("--- END BRAND CONTEXT ---\n")

    # Only the header/footer lines means nothing substantive was actually
    # found - equivalent to no profile.
    if len(lines) <= 2:
        return ""

    return "\n".join(lines)
