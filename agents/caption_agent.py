from services.llm_service import LLMService

class CaptionAgent:
    """Agent that generates platform-optimized captions with 3 psychological hook variations, memory awareness, brand voice, and self-correction capability"""
    
    PLATFORM_CONFIGS = {
        'facebook': {
            'max_length': 2200,
            'tone': 'conversational, community-focused',
            'features': ['hashtags', 'emojis', 'questions', 'CTA'],
            'optimal_length': '80-150 words'
        },
        'instagram': {
            'max_length': 2200,
            'tone': 'visual, aspirational, hashtag-heavy',
            'features': ['emojis', 'line breaks', 'hashtags separated', 'engagement hooks'],
            'optimal_length': '125-150 words'
        },
        'linkedin': {
            'max_length': 3000,
            'tone': 'professional, insightful, value-driven',
            'features': ['bullets', 'statistics', 'professional hashtags', 'thought leadership'],
            'optimal_length': '100-200 words'
        }
    }
    
    def __init__(self):
        self.llm = LLMService()
    
    def generate_caption(self, platform, story_analysis, vision_analysis=None, tone=None, memory_context=None, brand_voice=None):
        """Generate platform-specific captions with 3 psychological hook variations"""
        config = self.PLATFORM_CONFIGS.get(platform, self.PLATFORM_CONFIGS['instagram'])
        
        system_prompt = f"""You are a {platform.capitalize()} Copywriting Expert.
        Generate 3 distinct psychological hook variations for a social media post:
        1. "primary_caption": Standard high-converting engagement hook with strong call-to-action.
        2. "story_hook_caption": Storytelling & emotion-driven hook that creates personal connection.
        3. "contrarian_hook_caption": Bold claim, statistic, or pattern-interrupt hook that creates curiosity.

        Platform Rules:
        - Max length: {config['max_length']} chars
        - Tone: {config['tone']}
        - Optimal length: {config['optimal_length']}
        
        Return ONLY a JSON object with keys: primary_caption, story_hook_caption, contrarian_hook_caption"""
        
        user_prompt = self._build_prompt(story_analysis, vision_analysis, tone, memory_context, brand_voice)
        
        try:
            parsed, usage = self.llm.generate_json(system_prompt, user_prompt, temperature=0.8, return_usage=True)
            primary = parsed.get('primary_caption', '')
            story_hook = parsed.get('story_hook_caption', primary)
            contrarian_hook = parsed.get('contrarian_hook_caption', primary)
        except Exception as e:
            print(f"[CaptionAgent] JSON generation fallback: {e}")
            primary, usage = self.llm.generate(system_prompt, user_prompt, temperature=0.8, return_usage=True)
            story_hook = primary
            contrarian_hook = primary
        
        return {
            'platform': platform,
            'primary_caption': primary,
            'story_hook_caption': story_hook,
            'contrarian_hook_caption': contrarian_hook,
            'character_count': len(primary),
            'estimated_read_time': f"{len(primary.split()) // 200 + 1} min read",
            'usage': usage
        }

    def refine_caption(self, platform, original_caption, reviewer_feedback, brand_voice=None):
        """Refine caption based on ReviewerAgent feedback (Self-Correction Loop)"""
        config = self.PLATFORM_CONFIGS.get(platform, self.PLATFORM_CONFIGS['instagram'])
        
        system_prompt = f"""You are a Master Copy Editor for {platform.capitalize()}.
        Refine and improve the caption based on specific critic feedback.
        Enhance the hook, sharpen the call-to-action (CTA), and ensure high readability."""
        
        user_prompt = f"""Original Caption:
        \"\"\"{original_caption}\"\"\"

        Critic Feedback:
        {reviewer_feedback}

        Brand Persona: {brand_voice or 'Standard'}

        Rewrite and return ONLY the improved, polished caption."""
        
        refined_caption, usage = self.llm.generate(system_prompt, user_prompt, temperature=0.6, return_usage=True)
        return refined_caption, usage

    def _build_prompt(self, story_analysis, vision_analysis, tone, memory_context, brand_voice):
        parts = [f"Story Analysis: {story_analysis}"]
        if brand_voice:
            parts.append(f"Brand Voice Persona: {brand_voice}")
        if vision_analysis:
            parts.append(f"Image Analysis: {vision_analysis}")
        if tone:
            parts.append(f"Desired Tone: {tone}")
        if memory_context:
            parts.append(memory_context)
        return "\n\n".join(parts)
    
    def generate_all_platforms(self, story_analysis, vision_analysis=None, tone=None, memory_context=None, brand_voice=None):
        """Generate captions for all platforms"""
        results = {}
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
        
        for platform in ['facebook', 'instagram', 'linkedin']:
            res = self.generate_caption(platform, story_analysis, vision_analysis, tone, memory_context, brand_voice)
            u = res.pop('usage', {})
            total_usage["input_tokens"] += u.get("input_tokens", 0)
            total_usage["output_tokens"] += u.get("output_tokens", 0)
            total_usage["total_tokens"] += u.get("total_tokens", 0)
            total_usage["cost_usd"] += u.get("cost_usd", 0.0)
            results[platform] = res

        results['_usage'] = total_usage
        return results