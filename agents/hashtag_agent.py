from services.llm_service import LLMService
from services.memory_service import MemoryService


class HashtagAgent:
    """Agent that generates optimized hashtags with trend awareness and usage tracking"""

    PLATFORM_HASHTAG_LIMITS = {"facebook": 3, "instagram": 30, "linkedin": 5}

    def __init__(self):
        self.llm = LLMService()
        self.memory = MemoryService()

    def generate_hashtags(self, platform, story_analysis, vision_analysis=None, count=None, memory_context=None):
        """Generate platform-optimized hashtags"""
        max_tags = count or self.PLATFORM_HASHTAG_LIMITS.get(platform, 10)

        # Get trending context from memory
        trending = self.memory.get_trending_hashtags(category=story_analysis.get("themes", ["general"])[0])

        system_prompt = f"""You are a Hashtag Strategy Expert for {platform.capitalize()}.
        Rules:
        - Generate exactly {max_tags} hashtags per variation.
        - Avoid banned or overused spam hashtags.
        - Include 1-2 location-based if relevant.

        Provide THREE distinct hashtag variations:
        - reach_hashtags: Broad hashtags for maximum visibility.
        - niche_hashtags: Highly targeted hashtags.
        - branded_hashtags: Brand-specific and trending hashtags.

        Return JSON with: reach_hashtags (list), niche_hashtags (list), branded_hashtags (list), engagement_prediction (score 1-10)"""

        user_prompt = f"""Story Themes: {story_analysis.get("themes", [])}
        Emotions: {story_analysis.get("emotions", [])}
        Visual Elements: {vision_analysis.get("colors", []) if vision_analysis else "N/A"}
        Trending Context: {trending}
        """
        if memory_context:
            user_prompt += f"\n{memory_context}"

        result, usage = self.llm.generate_json(system_prompt, user_prompt, return_usage=True)

        if not isinstance(result, dict):
            result = {}

        reach_list = result.get("reach_hashtags")
        if not isinstance(reach_list, list) or not reach_list:
            themes = (
                story_analysis.get("themes", ["Marketing", "AI"])
                if isinstance(story_analysis, dict)
                else ["Marketing", "AI"]
            )
            clean_themes = [f"#{str(t).replace(' ', '').replace('-', '')}" for t in themes[:3]]
            base_tags = clean_themes + [
                f"#{platform.capitalize()}Strategy",
                "#VortexSocial",
                "#ContentAI",
            ]
            result["reach_hashtags"] = base_tags + ["#TrendingNow", "#Viral"]
            result["niche_hashtags"] = base_tags + ["#DeepDive", "#Specialist"]
            result["branded_hashtags"] = base_tags + ["#BrandVoice", "#Official"]

        # Store for future trend analysis
        try:
            self.memory.store_content(
                f"hashtag_{platform}_{hash(str(result))}",
                " ".join(result.get("reach_hashtags", [])),
                {"type": "hashtag", "platform": platform},
            )
        except Exception as mem_err:
            print(f"[HashtagAgent] Memory store notice: {mem_err}")

        result["_usage"] = usage
        return result

    def generate_all_platforms(self, story_analysis, vision_analysis=None, memory_context=None):
        """Generate hashtags for all platforms"""
        results = {}
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}

        for platform in ["facebook", "instagram", "linkedin"]:
            res = self.generate_hashtags(platform, story_analysis, vision_analysis, memory_context=memory_context)
            u = res.pop("_usage", {})
            total_usage["input_tokens"] += u.get("input_tokens", 0)
            total_usage["output_tokens"] += u.get("output_tokens", 0)
            total_usage["total_tokens"] += u.get("total_tokens", 0)
            total_usage["cost_usd"] += u.get("cost_usd", 0.0)
            results[platform] = res

        results["_usage"] = total_usage
        return results
