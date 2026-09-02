from services.llm_service import LLMService
import re


class CaptionAgent:
    """Agent that generates platform-optimized captions with
    memory awareness, brand voice, and self-correction capability.
    """

    PLATFORM_CONFIGS = {
        "facebook": {
            "max_length": 2200,
            "tone": "conversational, community-focused",
            "features": ["hashtags", "emojis", "questions", "CTA"],
            "optimal_length": "80-150 words",
        },
        "instagram": {
            "max_length": 2200,
            "tone": "visual, aspirational, hashtag-heavy",
            "features": ["emojis", "line breaks", "hashtags separated", "engagement hooks"],
            "optimal_length": "125-150 words",
        },
        "linkedin": {
            "max_length": 3000,
            "tone": "professional, insightful, value-driven",
            # IMPORTANT:
            # Bullets have been removed because the required output
            # must be plain text without subpoints.
            "features": ["statistics", "professional hashtags", "thought leadership"],
            "optimal_length": "100-200 words",
        },
    }

    def __init__(self):
        self.llm = LLMService()

    def _clean_caption(self, caption):
        """
        Force the final caption into clean plain text.

        Removes:
        - Markdown bold **
        - Markdown italic *
        - Underscore formatting
        - Backticks
        - Markdown headings
        - Bullet points
        - Numbered lists
        - Nested/sub-points
        - URLs
        - Common CTA/sales lines
        """

        if not caption:
            return ""

        # ---------------------------------------------------------
        # 1. Remove Markdown formatting symbols
        # ---------------------------------------------------------

        caption = caption.replace("**", "")
        caption = caption.replace("__", "")
        caption = caption.replace("*", "")
        caption = caption.replace("`", "")

        # ---------------------------------------------------------
        # 2. Process line by line
        # ---------------------------------------------------------

        lines = caption.splitlines()
        clean_lines = []

        for line in lines:

            line = line.strip()

            # Preserve paragraph spacing
            if not line:
                if clean_lines and clean_lines[-1] != "":
                    clean_lines.append("")
                continue

            lower = line.lower()

            # -----------------------------------------------------
            # 3. Remove unwanted CTA / sales / link lines
            # -----------------------------------------------------

            unwanted_phrases = [
                "[link]",
                "[insert link]",
                "[link/cta]",
                "http://",
                "https://",
                "www.",
                "book a demo",
                "schedule a demo",
                "request a demo",
                "request a call",
                "click the link",
                "click here",
                "learn more at",
                "contact us",
                "get started today",
                "sign up today",
                "try it today",
                "ready to",
                "whitepaper",
                "visit our website",
                "download the document",
                "learn more about how to streamline",
                "reach out to me directly",
                "brief demonstration",
                "integrates into your existing processes",
                "integrate with your current",
                "schedule a brief walkthrough",
                "direct message",
                "send me a message"
            ]

            if any(phrase in lower for phrase in unwanted_phrases):
                continue

            # -----------------------------------------------------
            # 4. Remove unwanted markdown characters (keep standard bullets)
            # -----------------------------------------------------

            line = line.lstrip("* \t")

            # -----------------------------------------------------
            # 5. (Removed numbered list stripping to allow lists)
            # -----------------------------------------------------

            # -----------------------------------------------------
            # 6. Remove remaining Markdown formatting
            # -----------------------------------------------------

            line = line.replace("**", "")
            line = line.replace("*", "")
            line = line.replace("__", "")
            line = line.replace("_", "")

            # Remove excessive spaces
            line = re.sub(r"[ \t]+", " ", line)

            line = line.strip()

            if line:
                clean_lines.append(line)

        # ---------------------------------------------------------
        # 7. Join paragraphs
        # ---------------------------------------------------------

        result = "\n".join(clean_lines)

        # Remove excessive blank lines
        result = re.sub(r"\n{3,}", "\n\n", result)

        # ---------------------------------------------------------
        # 8. Final safety cleanup
        # ---------------------------------------------------------

        # Absolutely ensure ** cannot exist
        result = result.replace("**", "")

        # Absolutely ensure standalone * cannot exist
        result = result.replace("*", "")

        return result.strip()

    def generate_caption(
        self,
        platform,
        story_analysis,
        vision_analysis=None,
        tone=None,
        memory_context=None,
        brand_voice=None,
    ):
        """Generate a platform-specific caption."""

        config = self.PLATFORM_CONFIGS.get(
            platform,
            self.PLATFORM_CONFIGS["instagram"]
        )

        system_prompt = f"""
You are a {platform.capitalize()} Content Specialist.

Generate ONE final narrative social media post based STRICTLY
on the instructions in the Story Analysis.

PRIMARY REQUIREMENT:
The final caption must be natural, human-written, and suitable
for the selected platform.

CRITICAL PLAIN-TEXT FORMATTING RULES:

1. OUTPUT MUST BE PLAIN TEXT ONLY.

SOURCE OF TRUTH ENFORCEMENT:
If the provided Story Analysis states that the "Selected Project" is "No Strong Match" or the "Connection Strength" is "No Strong Match", you MUST NOT generate a project-promotional caption.
Instead, return EXACTLY this text for the caption:
"CONTENT GENERATION BLOCKED

Reason:
No Strong Match was identified between this competitor topic and the available projects."

2. The characters "**" are FORBIDDEN.
   NEVER generate "**" anywhere in the response.

3. The character "*" is FORBIDDEN.
   NEVER generate "*" anywhere in the response.

4. Do NOT use Markdown.

5. Do NOT use bold formatting.

6. Do NOT use italic formatting.

7. You MAY use bullet points (like •) for objectives or lists in the middle of the text if it makes sense, but NEVER use markdown asterisks (*) for bullets.

8. Do NOT use formatted headings or special Markdown symbols.

9. Do NOT ask the reader to "visit our website", "read our whitepaper", "reach out to me", "see a demonstration", or "download the document".

10. You SHOULD include hashtags at the very end of the post. You MUST ensure every hashtag starts with a '#' symbol (e.g. #Technology #Innovation). Do NOT just list words without the '#' symbol.

FINAL VALIDATION:
Before returning the answer, scan the complete caption.

If "**", "*", or markdown formatting exist, REMOVE THEM before returning the answer.

NO SELLING:
Do not include [Insert Link], [Link], [Link/CTA], CTAs, sales
pitches, demo requests, or promotional calls to action.

IGNORE any part of the Story Analysis that asks you to include
a link, CTA, demo request, or sales pitch.

CONTENT AND PRIVACY RULES:
1. NEVER mention competitor names in public-facing content. Competitor names are for internal reference only.
2. PRESERVE THE SPECIFIC TOPIC: Do NOT lose the underlying strategic topic (e.g., fundamental research, private market due diligence, portfolio construction) when removing the competitor name. You MUST discuss the exact strategic problems mentioned in the Story Analysis. Do not replace the specific topic with a generic "operational efficiency" or "workflow automation" storyline.
3. The final caption must remain faithful to the selected projects and their verified capabilities.
4. STRICT BAN ON GENERIC BUZZWORDS: Do not introduce unrelated themes such as "headcount reduction", "operational efficiency", "workflow automation", "operational friction", or "manual workflows" unless they are explicitly the core subject of the Story Analysis. Focus on the specific financial or technical challenge provided.

For example:
BAD: "BlackRock and Northern Trust have highlighted..."
BAD: "Following BlackRock's approach..."
BAD: "Like Northern Trust, leading firms..."

GOOD: "Across today's investment landscape..."
GOOD: "As investment teams navigate increasingly complex markets..."
GOOD: "Modern investment firms are placing greater emphasis on..."

The final caption must stand on its own as StradIT's thought leadership and must not reveal which competitors were used as source inspiration.

PUBLIC CONTENT RULE:
The competitor analysis is an internal strategic input.
Do not expose:
- competitor names
- competitor-specific post references
- competitor-specific claims
- statements such as "Competitor X recently..."
- comparisons that explicitly identify a competitor

Use the competitor's topic or industry insight, but rewrite it as a broader market trend or industry challenge.
CRITICAL: You must hide competitor identities WITHOUT replacing their actual strategic topics with a generic operational-efficiency storyline. Ensure the original strategic meaning is preserved.

TONE:
Write like a real human industry professional sharing an insight.

Be objective when describing the StradIT project.

Do not sound like a marketer.

PLATFORM RULES:
Maximum length: {config["max_length"]} characters
Tone: {config["tone"]}
Optimal length: {config["optimal_length"]}

Return ONLY a JSON object with a single key:

primary_caption
"""

        user_prompt = self._build_prompt(
            story_analysis,
            vision_analysis,
            tone,
            memory_context,
            brand_voice,
        )

        try:
            parsed, usage = self.llm.generate_json(
                system_prompt,
                user_prompt,
                temperature=0.8,
                return_usage=True,
            )

            primary = parsed.get("primary_caption", "")

        except Exception as e:

            print(f"[CaptionAgent] JSON generation fallback: {e}")

            primary, usage = self.llm.generate(
                system_prompt,
                user_prompt,
                temperature=0.8,
                return_usage=True,
            )

        # ---------------------------------------------------------
        # FINAL CLEANING
        # ---------------------------------------------------------

        primary = self._clean_caption(primary)

        # ---------------------------------------------------------
        # Return result
        # ---------------------------------------------------------

        return {
            "platform": platform,
            "primary_caption": primary,
            "story_hook_caption": primary,
            "contrarian_hook_caption": primary,
            "character_count": len(primary),
            "estimated_read_time": f"{len(primary.split()) // 200 + 1} min read",
            "usage": usage,
        }

    def refine_caption(
        self,
        platform,
        original_caption,
        reviewer_feedback,
        brand_voice=None,
    ):
        """Refine caption based on ReviewerAgent feedback."""

        config = self.PLATFORM_CONFIGS.get(
            platform,
            self.PLATFORM_CONFIGS["instagram"]
        )

        system_prompt = f"""
You are a meticulous Content Refinement Editor for {platform.capitalize()}.

Your job is to refine an existing social media caption based on the reviewer's feedback and ensure it strictly follows the brand voice: "{brand_voice}".

SOURCE OF TRUTH ENFORCEMENT:
If the original caption or the instructions indicate "No Strong Match", or if the original caption is "CONTENT GENERATION BLOCKED", you MUST NOT generate a project-promotional caption.
Instead, return EXACTLY this text:
"CONTENT GENERATION BLOCKED

Reason:
No Strong Match was identified between this competitor topic and the available projects."

NEVER use Markdown.

NEVER use "**".

NEVER use "*".

Do NOT use bold formatting.

Do NOT use italic formatting.

Do NOT use formatted headings.

You MAY use bullet points (like •) if needed for objectives, but NEVER use markdown asterisks (*).

Convert all other information into natural sentences and paragraphs.

Do not add CTAs, sales pitches, demo requests, links, or promotional
language. Do NOT ask the reader to "visit our website", "read our whitepaper", "reach out to me", "see a demonstration", or "download the document".

Write like a real human industry professional.

If you include hashtags at the end, you MUST ensure every hashtag starts with a '#' symbol (e.g. #Technology #Innovation). Do NOT just list words without the '#' symbol.

Before returning the caption, verify that the characters "**"
and "*" do not appear anywhere in the final response.

Platform maximum length: {config["max_length"]} characters.
"""

        user_prompt = f"""
Original Caption:

\"\"\"
{original_caption}
\"\"\"

Critic Feedback:

{reviewer_feedback}

Brand Persona:

{brand_voice or "Standard"}

Rewrite and return ONLY the improved plain-text caption.
"""

        refined_caption, usage = self.llm.generate(
            system_prompt,
            user_prompt,
            temperature=0.6,
            return_usage=True,
        )

        # ---------------------------------------------------------
        # IMPORTANT:
        # Clean refined caption too.
        # This was missing in your original code.
        # ---------------------------------------------------------

        refined_caption = self._clean_caption(refined_caption)

        return refined_caption, usage

    def _build_prompt(
        self,
        story_analysis,
        vision_analysis,
        tone,
        memory_context,
        brand_voice,
    ):
        parts = [
            f"Story Analysis: {story_analysis}"
        ]

        if brand_voice:
            parts.append(
                f"Brand Voice Persona: {brand_voice}"
            )

        if vision_analysis:
            parts.append(
                f"Image Analysis: {vision_analysis}"
            )

        if tone:
            parts.append(
                f"Desired Tone: {tone}"
            )

        if memory_context:
            parts.append(memory_context)

        return "\n\n".join(parts)

    def generate_all_platforms(
        self,
        story_analysis,
        vision_analysis=None,
        tone=None,
        memory_context=None,
        brand_voice=None,
    ):
        """Generate captions for all platforms."""

        results = {}

        total_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }

        for platform in [
            "facebook",
            "instagram",
            "linkedin",
        ]:

            res = self.generate_caption(
                platform,
                story_analysis,
                vision_analysis,
                tone,
                memory_context,
                brand_voice,
            )

            usage = res.pop("usage", {})

            total_usage["input_tokens"] += usage.get(
                "input_tokens",
                0
            )

            total_usage["output_tokens"] += usage.get(
                "output_tokens",
                0
            )

            total_usage["total_tokens"] += usage.get(
                "total_tokens",
                0
            )

            total_usage["cost_usd"] += usage.get(
                "cost_usd",
                0.0
            )

            results[platform] = res

        results["_usage"] = total_usage

        return results