from datetime import datetime

from services.llm_service import LLMService


class StrategyAgent:
    """Agent that determines optimal posting strategy with usage tracking"""

    PLATFORM_BEST_TIMES = {
        "facebook": {
            "weekdays": ["9:00 AM", "1:00 PM", "3:00 PM"],
            "weekends": ["12:00 PM", "1:00 PM"],
            "best_days": ["Tuesday", "Wednesday", "Thursday"],
        },
        "instagram": {
            "weekdays": ["11:00 AM", "1:00 PM", "7:00 PM"],
            "weekends": ["10:00 AM", "2:00 PM"],
            "best_days": ["Tuesday", "Wednesday", "Friday"],
        },
        "linkedin": {
            "weekdays": ["8:00 AM", "12:00 PM", "5:00 PM"],
            "weekends": [],  # Minimal weekend engagement
            "best_days": ["Tuesday", "Wednesday", "Thursday"],
        },
    }

    def __init__(self):
        self.llm = LLMService()

    def create_strategy(self, platform, story_analysis, content_type="standard", memory_context=None):
        """Generate comprehensive post strategy"""
        best_times = self.PLATFORM_BEST_TIMES.get(platform, {})

        system_prompt = f"""You are a Social Media Strategy Expert for {platform.capitalize()}.
        Create a detailed posting strategy including:
        1. Optimal posting time with timezone considerations
        2. Content format recommendations (carousel, reel, story, etc.)
        3. Engagement tactics (polls, questions, CTAs)
        4. Follow-up post suggestions
        5. Cross-posting recommendations
        6. Expected engagement metrics

        Return as JSON with keys: optimal_time, format, engagement_tactics, follow_up, cross_post, metrics_forecast"""

        user_prompt = f"""Content Analysis: {story_analysis}
        Platform: {platform}
        Content Type: {content_type}
        Current Time: {datetime.now().isoformat()}
        """
        if memory_context:
            user_prompt += f"\n{memory_context}"

        strategy, usage = self.llm.generate_json(system_prompt, user_prompt, return_usage=True)
        strategy["platform_best_practices"] = best_times
        strategy["_usage"] = usage
        return strategy

    def generate_all_strategies(self, story_analysis, memory_context=None):
        """Generate strategies for all platforms"""
        return self.schedule_posts(["facebook", "instagram", "linkedin"], story_analysis, memory_context)

    def schedule_posts(self, platforms, story_analysis, memory_context=None):
        """Generate strategies for specified platforms"""
        results = {}
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}

        for platform in platforms:
            res = self.create_strategy(platform, story_analysis, memory_context=memory_context)
            u = res.pop("_usage", {})
            total_usage["input_tokens"] += u.get("input_tokens", 0)
            total_usage["output_tokens"] += u.get("output_tokens", 0)
            total_usage["total_tokens"] += u.get("total_tokens", 0)
            total_usage["cost_usd"] += u.get("cost_usd", 0.0)
            results[platform] = res

        results["_usage"] = total_usage
        return results
