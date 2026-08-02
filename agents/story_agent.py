from services.llm_service import LLMService

class StoryAgent:
    """Agent that analyzes story text and extracts key themes, emotions, and hooks"""
    
    SYSTEM_PROMPT = """You are a Story Analysis Agent. Your job is to deeply analyze a given story or text and extract:
    1. Core themes (3-5 main themes)
    2. Emotional tone (joy, sadness, excitement, inspiration, etc.)
    3. Key hooks (attention-grabbing elements)
    4. Target audience segments
    5. Visual imagery descriptions
    6. Call-to-action opportunities
    
    Return ONLY a JSON object with these keys: themes, emotions, hooks, audience, imagery, cta_opportunities"""
    
    def __init__(self):
        self.llm = LLMService()
    
    def analyze(self, story_text, memory_context=None, return_usage=False):
        """Analyze story and return structured insights + usage"""
        user_prompt = f"Analyze this story and return structured insights:\n\n{story_text}"
        if memory_context:
            user_prompt += f"\n\n{memory_context}"
            
        result, usage = self.llm.generate_json(self.SYSTEM_PROMPT, user_prompt, return_usage=True)
        if return_usage:
            return result, usage
        return result
    
    def extract_key_points(self, story_text, max_points=5):
        """Extract key narrative points for social media adaptation"""
        system = """Extract the top key points from this story that would work best for social media posts.
        Each point should be concise (1-2 sentences) and impactful."""
        user = f"Story:\n{story_text}\n\nExtract {max_points} key points."
        response = self.llm.generate(system, user)
        return [p.strip() for p in response.split('\n') if p.strip()]