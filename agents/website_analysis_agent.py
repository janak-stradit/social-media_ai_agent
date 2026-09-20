from services.llm_service import LLMService


class WebsiteAnalysisAgent:
    """Turns a scraped homepage (see services/website_scraper_service.py)
    into a compact brand profile used to ground Studio Chat generation for
    that user - the per-user equivalent of StradIT's own Content Guidelines.
    """

    SYSTEM_PROMPT = """You are a Brand Analyst. You're given a company's homepage title, meta
description, a text excerpt, and a list of hex colors found on the page (some of which may be
incidental UI colors, not real brand colors - use judgment).

From this, infer:
1. company_name: the actual brand/company/person name (from the title or text - e.g. "Acme
   Florist", not a generic description). This is used elsewhere as the literal company name in
   generated content, so get the real name, not a category.
2. industry: a short label for what this company/person does (e.g. "B2B SaaS - project
   management", "Independent photographer", "Boutique skincare brand")
3. target_audience: who they're speaking to (1 sentence)
4. brand_voice_summary: how they write/speak - tone, formality, personality (1-2 sentences)
5. key_themes: 3-5 recurring topics/values this brand talks about
6. primary_colors: pick 2-4 hex colors from the provided candidates that most plausibly ARE the
   brand's real colors (skip obviously generic ones like pure white/black/gray if better options
   exist) - if no candidates look like real brand colors, return an empty list rather than
   guessing
7. content_dos: 2-4 concrete things future social content for this brand SHOULD do
8. content_donts: 2-4 concrete things future social content for this brand should AVOID
9. suggested_post_ideas: exactly 4 concrete, ready-to-use post ideas for THIS specific brand
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
best-effort guess from what's given rather than leaving fields empty - only primary_colors
should ever legitimately be empty.

Return ONLY a JSON object with keys: company_name, industry, target_audience,
brand_voice_summary, key_themes (list), primary_colors (list), content_dos (list),
content_donts (list), suggested_post_ideas (list of the objects described above)"""

    def __init__(self):
        self.llm = LLMService()

    def analyze(self, scraped: dict) -> dict:
        user_prompt = f"""Title: {scraped.get("title") or "N/A"}
Meta description: {scraped.get("meta_description") or "N/A"}
Candidate colors found on page: {scraped.get("colors") or []}

Homepage text excerpt:
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

        return {
            "company_name": result.get("company_name") or None,
            "industry": result.get("industry") or None,
            "target_audience": result.get("target_audience") or None,
            "brand_voice_summary": result.get("brand_voice_summary") or None,
            "key_themes": result.get("key_themes") if isinstance(result.get("key_themes"), list) else [],
            "primary_colors": result.get("primary_colors") if isinstance(result.get("primary_colors"), list) else [],
            "content_dos": result.get("content_dos") if isinstance(result.get("content_dos"), list) else [],
            "content_donts": result.get("content_donts") if isinstance(result.get("content_donts"), list) else [],
            "suggested_post_ideas": post_ideas[:4],
        }
