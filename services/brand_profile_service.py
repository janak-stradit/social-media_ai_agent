"""Formats a user's stored brand profile (see db.UserBrandProfile,
agents/website_analysis_agent.py) into a prompt section for Studio Chat
generation - the per-user equivalent of agents/story_agent.py's
_build_guidelines_block(). Only wired into the Studio Chat path
(api/routes.py's generate_content()), not the StradIT-specific
competitor-dashboard flow."""


def run_brand_analysis(user_id: int, website: str) -> bool:
    """Scrapes `website` and saves the resulting brand profile for user_id -
    shared by onboarding (api/routes.py's onboarding_account_type(), where a
    failure is best-effort/silent) and the "My Brand Configuration" page's
    manual re-scan action (POST /api/brand-profile/rescan, where the caller
    surfaces failure to the user). Returns True on success, False if the
    fetch or analysis failed - callers decide how to report that."""
    from agents.website_analysis_agent import WebsiteAnalysisAgent
    from db import save_user_brand_profile
    from services.website_scraper_service import fetch_website

    scraped = fetch_website(website)
    if not scraped:
        return False

    profile = WebsiteAnalysisAgent().analyze(scraped)
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
    )
    return True


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
