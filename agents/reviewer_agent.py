import re

from agents.caption_agent import CaptionAgent


class ReviewerAgent:
    """
    Quality checks for a generated post - deterministic code, no LLM call.

    It replaced an LLM "critic" that took ~80 s per platform, scored posts on
    "CTA effectiveness" although the Caption Agent is told never to include a
    CTA (so it penalised captions for following their own rules and triggered
    needless rewrites), and returned made-up scores when it failed.

    Each check either passes, is fixed here directly (hashtag count), or yields
    concrete feedback for ONE caption rewrite (caption_agent.refine_caption).
    """

    MIN_CAPTION_LENGTH = 40
    # Same numbers as HashtagAgent.PLATFORM_HASHTAG_LIMITS
    MAX_HASHTAGS = {"facebook": 3, "instagram": 30, "linkedin": 5}

    # The Caption Agent's own rules: no sales CTAs, links or placeholders
    _CTA_PATTERNS = [
        r"\bvisit our (web)?site\b",
        r"\bclick (the|this) link\b",
        r"\blink in (the )?bio\b",
        r"\bbook a (demo|call)\b",
        r"\b(request|schedule) a demo\b",
        r"\bsign up (now|today)\b",
        r"\bdm (us|me)\b",
        r"\breach out to (us|me)\b",
        r"\bdownload (the|our) (whitepaper|guide|document)\b",
        r"\bcontact us\b",
    ]
    _PLACEHOLDER = re.compile(r"\[(insert|link|cta|your|company|name)[^\]]*\]", re.IGNORECASE)
    _MARKDOWN = re.compile(r"\*\*|__|^#{1,6}\s", re.MULTILINE)

    def evaluate(self, platform, caption, hashtags, story_analysis=None, brand_voice=None):
        """Run the checks. Returns:
        {checks_total, checks_passed, passed, issues: [str],
         needs_refinement: bool, reviewer_feedback: str|None, hashtags: [str]}
        `hashtags` is the (possibly trimmed) list to use. story_analysis and
        brand_voice are accepted for call compatibility."""
        caption = caption or ""
        hashtags = list(hashtags or [])
        issues = []  # shown to the user
        feedback = []  # sent to the caption rewrite (only for caption problems)
        checks = 0

        if "CONTENT GENERATION BLOCKED" in caption:
            # Intentional block message - nothing to check or rewrite
            return self._result(1, 1, [], [], hashtags)

        checks += 1
        # Same limit the Caption Agent was prompted with
        max_len = CaptionAgent.PLATFORM_CONFIGS.get(platform, {}).get("max_length")
        if max_len and len(caption) > max_len:
            issues.append(f"Caption is {len(caption)} characters; {platform} allows {max_len}.")
            feedback.append(f"Shorten the caption to under {max_len} characters, keeping the key point.")

        checks += 1
        if len(caption.strip()) < self.MIN_CAPTION_LENGTH:
            issues.append("Caption is too short to be a complete post.")
            feedback.append("Write a complete post of at least two or three sentences.")

        checks += 1
        found_cta = [p for p in self._CTA_PATTERNS if re.search(p, caption, re.IGNORECASE)]
        if found_cta:
            issues.append("Caption contains a sales call-to-action.")
            feedback.append("Remove every call-to-action, link request or sales pitch; end on the insight instead.")

        checks += 1
        if self._PLACEHOLDER.search(caption):
            issues.append("Caption contains a placeholder like [Insert link].")
            feedback.append("Remove all bracketed placeholders such as [Insert link] or [Company name].")

        checks += 1
        if self._MARKDOWN.search(caption):
            issues.append("Caption contains Markdown formatting.")
            feedback.append("Use plain text only - no Markdown, asterisks or headings.")

        checks += 1
        max_tags = self.MAX_HASHTAGS.get(platform)
        if max_tags and len(hashtags) > max_tags:
            issues.append(f"{len(hashtags)} hashtags trimmed to {platform}'s {max_tags}.")
            hashtags = hashtags[:max_tags]  # fixed here, no rewrite needed

        failed_checks = len(issues)  # one issue per failed check
        return self._result(checks, checks - failed_checks, issues, feedback, hashtags)

    @staticmethod
    def _result(total, passed, issues, feedback, hashtags):
        return {
            "checks_total": total,
            "checks_passed": passed,
            "passed": not issues,
            "issues": issues,
            "needs_refinement": bool(feedback),
            "reviewer_feedback": " ".join(feedback) if feedback else None,
            "hashtags": hashtags,
        }
