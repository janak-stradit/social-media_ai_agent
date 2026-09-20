from services.llm_service import LLMService


class WebsiteAnalysisAgent:
    """Turns a scraped site (homepage + a couple of same-origin About/
    Services-style pages - see services/website_scraper_service.py) into a
    compact brand profile used to ground Studio Chat generation for that
    user - the per-user equivalent of StradIT's own Content Guidelines.
    """

    SYSTEM_PROMPT = """You are a Brand Analyst. You're given a company's website: title, meta/
Open Graph description, a text excerpt combining the homepage and (when found) an About/Services-
style page, a logo/social-preview image URL, and color/font signals extracted from the site's CSS -
split into "declared brand colors" (assigned to a CSS variable literally named things like
--primary/--brand/--accent - high confidence these ARE real brand colors) and "other colors on
page" (any other hex value found - may include incidental UI colors, use judgment).

From this, infer:
1. company_name: the actual brand/company/person name (from the title or text - e.g. "Acme
   Florist", not a generic description). This is used elsewhere as the literal company name in
   generated content, so get the real name, not a category.
2. industry: a short label for what this company/person does (e.g. "B2B SaaS - project
   management", "Independent photographer", "Boutique skincare brand")
3. target_audience: who they're speaking to (1 sentence)
4. brand_voice_summary: how they write/speak - tone, formality, personality (1-2 sentences)
5. tagline: their actual slogan/tagline if one is evident in the title, headline, or meta
   description (verbatim if found) - null if none is apparent, don't invent one
6. visual_style: a short descriptor (3-6 words) of the site's visual/design personality inferred
   from the detected colors and fonts together with the copy's tone - e.g. "minimal, modern,
   high-contrast", "bold, playful, saturated", "corporate, restrained, trustworthy"
7. key_themes: 3-5 recurring topics/values this brand talks about
8. primary_colors: 2-4 hex colors that most plausibly ARE the brand's real colors. Strongly
   prefer the "declared brand colors" list when it has usable entries - those were explicitly
   named as brand/primary/accent colors in the site's own CSS. Only fall back to picking from
   "other colors on page" if no declared colors were found, and even then skip obviously generic
   ones (pure white/black/gray) if better options exist. Return an empty list rather than
   guessing if nothing plausible is available.
9. content_dos: 2-4 concrete things future social content for this brand SHOULD do
10. content_donts: 2-4 concrete things future social content for this brand should AVOID
11. suggested_post_ideas: exactly 4 concrete, ready-to-use post ideas for THIS specific brand
    (not generic marketing advice) - each one a distinct angle (e.g. a product/service highlight,
    a thought-leadership/industry-insight angle, a customer story or social proof angle, and a
    promotional/seasonal/announcement angle). Each idea has:
    - category: one of "product", "thought_leadership", "story", "promo", "event", "tips" (pick
      whichever fits each idea best)
    - title: a short 2-4 word label (e.g. "Product Highlight")
    - summary: one short sentence describing the idea, shown as a preview
    - prompt: a full, detailed campaign brief (2-4 sentences) specific to this brand's actual
      products/services/industry (from the page content) that could be pasted directly into a
      content generator - not a placeholder, an actually usable brief

If the page content is too thin/generic to infer something confidently, make a reasonable
best-effort guess from what's given rather than leaving fields empty - only primary_colors and
tagline should ever legitimately be empty/null.

Return ONLY a JSON object with keys: company_name, industry, target_audience,
brand_voice_summary, tagline, visual_style, key_themes (list), primary_colors (list),
content_dos (list), content_donts (list), suggested_post_ideas (list of the objects described
above)"""

    def __init__(self):
        self.llm = LLMService()

    def analyze(self, scraped: dict) -> dict:
        description = scraped.get("meta_description") or scraped.get("og_description") or "N/A"
        site_name = scraped.get("og_site_name") or ""
        pages_note = (
            f" (from {len(scraped['pages_scraped'])} pages: {', '.join(scraped['pages_scraped'])})"
            if scraped.get("pages_scraped")
            else ""
        )

        user_prompt = f"""Title: {scraped.get("title") or "N/A"}
Site name (Open Graph): {site_name or "N/A"}
Description: {description}
Logo/preview image: {scraped.get("og_image") or "N/A"}
Theme color meta tag (if the site declared one explicitly): {scraped.get("theme_color") or "N/A"}
Declared brand colors (from CSS variables like --primary/--brand): {scraped.get("declared_colors") or []}
Other colors found on page: {scraped.get("colors") or []}
Fonts detected in the site's CSS: {scraped.get("fonts") or []}

Website text excerpt{pages_note}:
{scraped.get("text_excerpt") or "N/A"}
"""
        result = self.llm.generate_json(self.SYSTEM_PROMPT, user_prompt, temperature=0.3)
        if not isinstance(result, dict):
            result = {}

        valid_categories = {"product", "thought_leadership", "story", "promo", "event", "tips"}
        raw_ideas = result.get("suggested_post_ideas")
        post_ideas = []
        if isinstance(raw_ideas, list):
            for idea in raw_ideas:
                if not isinstance(idea, dict) or not idea.get("prompt"):
                    continue
                category = idea.get("category") if idea.get("category") in valid_categories else "tips"
                post_ideas.append(
                    {
                        "category": category,
                        "title": idea.get("title") or "Content Idea",
                        "summary": idea.get("summary") or "",
                        "prompt": idea["prompt"],
                    }
                )

        # Colors: trust the LLM's picks if it returned any (it was told to
        # prefer declared_colors), otherwise fall back to the raw declared
        # list directly rather than losing a strong signal to a parsing miss.
        primary_colors = result.get("primary_colors") if isinstance(result.get("primary_colors"), list) else []
        if not primary_colors:
            primary_colors = scraped.get("declared_colors") or []

        return {
            "company_name": result.get("company_name") or None,
            "industry": result.get("industry") or None,
            "target_audience": result.get("target_audience") or None,
            "brand_voice_summary": result.get("brand_voice_summary") or None,
            "tagline": result.get("tagline") or None,
            "visual_style": result.get("visual_style") or None,
            "key_themes": result.get("key_themes") if isinstance(result.get("key_themes"), list) else [],
            "primary_colors": primary_colors,
            "content_dos": result.get("content_dos") if isinstance(result.get("content_dos"), list) else [],
            "content_donts": result.get("content_donts") if isinstance(result.get("content_donts"), list) else [],
            "suggested_post_ideas": post_ideas[:4],
            "fonts": scraped.get("fonts") or [],
            "logo_url": scraped.get("og_image") or scraped.get("favicon") or None,
        }
