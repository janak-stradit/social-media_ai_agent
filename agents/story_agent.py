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

    def generate_channel_storyline(self, posts_text, project_context, return_usage=False):
        """Generate a structured channel storyline based on competitor posts and our project context"""
        system = """You are an expert strategic analyst. 
For each competitor's recent post of all platforms, identify the core industry problem/topic they discuss. 
Create a storyline with a 45:55 balance: 45% industry/competitor-topic context and 55% how StradIT projects addresses or solves that problem. 
Do not simply summarize the competitor post. Use it as the problem/context and transition naturally into our solution. 

CRITICAL WRITING RULES:
1. NO SELLING: Do not include ANY Call-To-Action (CTA). Do not say "Ready to modernize?", "Visit our website", or "Book a demo". Do not include ANY links.
2. NO MARKDOWN OR BULLET POINTS: The prompt MUST be plain text. Do not use **bold**, *italics*, or bullet points. Write in flowing paragraphs.
3. HUMAN TONE: Keep the tone analytical, institutional, credible, and purely informational. Sound like a human industry professional.
4. ONLY show the industry problem and the objective qualities/capabilities of the StradIT project.

Return ONLY a valid JSON object with the following schema:
{
    "observed_facts": ["fact 1", "fact 2", "fact 3"],
    "prompt": "A detailed storyline and prompt to be used for generating media/captions based on the instructions above."
}"""
        user = f"<COMPETITOR_POSTS>\n{posts_text}\n</COMPETITOR_POSTS>\n\n<OUR_PROJECT_CONTEXT>\n{project_context}\n</OUR_PROJECT_CONTEXT>"
        
        result, usage = self.llm.generate_json(system, user, return_usage=True)
        if return_usage:
            return result, usage
        return result