import logging

from services.llm_service import LLMService
from services.memory_service import MemoryService

logger = logging.getLogger(__name__)


class HashtagAgent:
    """Agent that generates optimized hashtags with trend awareness and usage tracking"""

    PLATFORM_HASHTAG_LIMITS = {"facebook": 3, "instagram": 30, "linkedin": 5}

    def __init__(self):
        self.llm = LLMService()
        self.memory = MemoryService()

    def generate_hashtags(
        self, platform, story_analysis, vision_analysis=None, count=None, memory_context=None, brand_profile_block=None
    ):
        """Generate platform-optimized hashtags.
        brand_profile_block (see services/brand_profile_service.py): a Studio
        Chat user's own onboarding-derived brand context - when present, the
        branded-hashtag rule below defers to their actual company instead of
        asserting StradIT."""
        max_tags = count or self.PLATFORM_HASHTAG_LIMITS.get(platform, 10)

        # Get trending context from memory
        trending = self.memory.get_trending_hashtags(category=story_analysis.get("themes", ["general"])[0])

        company_rule = self._company_rule(brand_profile_block)
        system_prompt = f"""You are a Hashtag Strategy Expert for {platform.capitalize()}.
        Rules:
        - Generate exactly {max_tags} hashtags, mixing broad-reach, niche/targeted, and one or two
          brand-specific tags into a single well-rounded set - not separate variations.
        - Avoid banned or overused spam hashtags.
        - Include 1-2 location-based if relevant.
        - {company_rule}

        Return JSON with: hashtags (list), engagement_prediction (score 1-10)"""

        user_prompt = self._user_prompt(story_analysis, vision_analysis, trending, memory_context, brand_profile_block)

        # A hashtag list needs no deep reasoning - "minimal" answers in seconds
        result, usage = self.llm.generate_json(
            system_prompt, user_prompt, return_usage=True, reasoning_effort="minimal"
        )

        if not isinstance(result, dict):
            result = {}

        tag_list = result.get("hashtags")
        if not isinstance(tag_list, list) or not tag_list:
            themes = (
                story_analysis.get("themes", ["Marketing", "AI"])
                if isinstance(story_analysis, dict)
                else ["Marketing", "AI"]
            )
            clean_themes = [f"#{str(t).replace(' ', '').replace('-', '')}" for t in themes[:3]]
            result["hashtags"] = clean_themes + [
                f"#{platform.capitalize()}Strategy",
                "#AVIRAI",
                "#ContentAI",
            ]

        # Store for future trend analysis
        try:
            self.memory.store_content(
                f"hashtag_{platform}_{hash(str(result))}",
                " ".join(result.get("hashtags", [])),
                {"type": "hashtag", "platform": platform},
            )
        except Exception as mem_err:
            logger.warning(f"Memory store notice: {mem_err}")

        result["_usage"] = usage
        return result

    @staticmethod
    def _company_rule(brand_profile_block):
        return (
            'The company is whoever is named as "Company name" in the Brand Context below - build '
            "any brand-specific hashtag off that actual name, never off a brand-voice/tone label."
            if brand_profile_block
            else 'The company is StradIT. Any brand-specific hashtag must be built off "StradIT" itself '
            "(e.g. #StradIT, #StradITAI) - never off a brand-voice/tone label or anything found in the "
            '"Memory Context" below, even if that text refers to a different company name. Memory '
            "Context is past examples for tone/theme reference ONLY, not a source of the company name."
        )

    @staticmethod
    def _user_prompt(story_analysis, vision_analysis, trending, memory_context, brand_profile_block):
        user_prompt = f"""Story Themes: {story_analysis.get("themes", [])}
        Emotions: {story_analysis.get("emotions", [])}
        Visual Elements: {vision_analysis.get("colors", []) if vision_analysis else "N/A"}
        Trending Context: {trending}
        """
        if memory_context:
            user_prompt += f"\n{memory_context}"
        if brand_profile_block:
            user_prompt += f"\n{brand_profile_block}"
        return user_prompt

    def generate_hashtags_batch(
        self, platforms, story_analysis, vision_analysis=None, memory_context=None, brand_profile_block=None
    ):
        """Every platform's hashtag set in ONE call - same inputs and rules as
        generate_hashtags(). Each AI call carries ~4k tokens of fixed provider
        overhead, so one call instead of one per platform saves that per extra
        platform. A platform missing from the answer falls back to its own
        call. Returns {platform: result}; the call's usage is on the first."""
        platforms = list(platforms)
        if len(platforms) <= 1:
            return {p: self.generate_hashtags(p, story_analysis, vision_analysis, memory_context=memory_context,
                                              brand_profile_block=brand_profile_block) for p in platforms}
        trending = self.memory.get_trending_hashtags(category=story_analysis.get("themes", ["general"])[0])
        per_platform = "\n".join(
            f"        - {p}: exactly {self.PLATFORM_HASHTAG_LIMITS.get(p, 10)} hashtags" for p in platforms
        )
        system_prompt = f"""You are a Hashtag Strategy Expert for {", ".join(p.capitalize() for p in platforms)}.
        For EACH platform below, write one well-rounded hashtag set suited to that platform:
{per_platform}
        Rules for every set:
        - Mix broad-reach, niche/targeted, and one or two brand-specific tags into a single set - not separate variations.
        - Avoid banned or overused spam hashtags.
        - Include 1-2 location-based if relevant.
        - {self._company_rule(brand_profile_block)}

        Return JSON with one key per platform above:
        {{"<platform>": {{"hashtags": [list], "engagement_prediction": <score 1-10>}}}}"""
        user_prompt = self._user_prompt(story_analysis, vision_analysis, trending, memory_context, brand_profile_block)
        try:
            batch, usage = self.llm.generate_json(system_prompt, user_prompt, return_usage=True, reasoning_effort="minimal")
        except Exception as err:  # noqa: BLE001 - fall back to one call per platform
            logger.warning(f"Batched hashtags failed, using one call per platform: {err}")
            batch, usage = {}, {}
        batch = {str(k).lower(): v for k, v in batch.items()} if isinstance(batch, dict) else {}
        results = {}
        for platform in platforms:
            entry = batch.get(platform) if isinstance(batch.get(platform), dict) else None
            tags = entry.get("hashtags") if entry else None
            if not isinstance(tags, list) or not tags:
                results[platform] = self.generate_hashtags(platform, story_analysis, vision_analysis,
                                                           memory_context=memory_context, brand_profile_block=brand_profile_block)
                continue
            result = {"hashtags": [str(t) for t in tags], "engagement_prediction": entry.get("engagement_prediction"),
                      "_usage": {}}
            try:
                self.memory.store_content(
                    f"hashtag_{platform}_{hash(str(result['hashtags']))}",
                    " ".join(result["hashtags"]),
                    {"type": "hashtag", "platform": platform},
                )
            except Exception as mem_err:  # noqa: BLE001
                logger.warning(f"Memory store notice: {mem_err}")
            results[platform] = result
        # The batch call's cost, counted once (alongside any fallback calls' own)
        first = results[platforms[0]]
        first_usage = dict(first.get("_usage") or {})
        for key in ("input_tokens", "output_tokens", "total_tokens", "cost_usd"):
            first_usage[key] = (first_usage.get(key) or 0) + ((usage or {}).get(key) or 0)
        first["_usage"] = first_usage
        return results

    def generate_all_platforms(
        self, story_analysis, vision_analysis=None, memory_context=None, platforms=None, brand_profile_block=None
    ):
        """Generate hashtags for all platforms"""
        results = {}
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}

        if not platforms:
            platforms = ["facebook", "instagram", "linkedin"]

        for platform in platforms:
            res = self.generate_hashtags(
                platform, story_analysis, vision_analysis, memory_context=memory_context, brand_profile_block=brand_profile_block
            )
            u = res.pop("_usage", {})
            total_usage["input_tokens"] += u.get("input_tokens", 0)
            total_usage["output_tokens"] += u.get("output_tokens", 0)
            total_usage["total_tokens"] += u.get("total_tokens", 0)
            total_usage["cost_usd"] += u.get("cost_usd", 0.0)
            results[platform] = res

        results["_usage"] = total_usage
        return results
