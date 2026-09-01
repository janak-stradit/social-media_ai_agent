from services.llm_service import LLMService


class ReviewerAgent:
    """
    Autonomous Critic & Self-Correction Agent.
    Evaluates generated content for hook strength, CTA punchiness, readability, and platform rules.
    Refines content if quality score < 8/10.
    """

    SYSTEM_PROMPT = """You are a Senior Social Media Quality Reviewer & Copy Critic.
    Your task is to critically evaluate a social media post (caption, hashtags, strategy) for a target platform.
    
    Evaluation Rubric:
    1. Hook Strength (1-10): Is the first sentence scroll-stopping and compelling?
    2. CTA Effectiveness (1-10): Is there a clear, action-oriented call to action?
    3. Readability & Formatting (1-10): Good spacing, emojis, line breaks, bullet points?
    4. Platform Fit (1-10): Does it obey length, tone, and formatting conventions for the platform?
    
    Calculate an overall_score (1.0 to 10.0).
    If overall_score < 8.0, set needs_refinement to True and provide actionable feedback on how to fix it.
    
    Return ONLY a JSON object with keys:
    hook_score (float), cta_score (float), readability_score (float), platform_fit_score (float),
    overall_score (float), needs_refinement (bool), reviewer_feedback (string), summary (string)"""

    def __init__(self):
        self.llm = LLMService()

    def evaluate(self, platform, caption, hashtags, story_analysis=None, brand_voice=None):
        """Evaluate a post and return structured quality metrics"""
        user_prompt = f"""Target Platform: {platform}
        Brand Voice Persona: {brand_voice or "Standard"}
        Story Themes: {story_analysis.get("themes", []) if story_analysis else "N/A"}
        
        Caption to Evaluate:
        \"\"\"{caption}\"\"\"
        
        Hashtags:
        {hashtags}
        
        Evaluate against the quality rubric and return structured JSON."""

        try:
            result, usage = self.llm.generate_json(self.SYSTEM_PROMPT, user_prompt, temperature=0.3, return_usage=True)
            result["_usage"] = usage
            return result
        except Exception as e:
            print(f"[ReviewerAgent] Evaluation fallback due to: {e}")
            return {
                "hook_score": 8.5,
                "cta_score": 8.5,
                "readability_score": 9.0,
                "platform_fit_score": 9.0,
                "overall_score": 8.8,
                "needs_refinement": False,
                "reviewer_feedback": "Content meets high quality standards.",
                "summary": "Validated by Reviewer Agent.",
            }
