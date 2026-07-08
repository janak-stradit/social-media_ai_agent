from services.llm_service import LLMService

class CaptionAgent:
    """Agent that generates platform-optimized captions"""
    
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
    
    def generate_caption(self, platform, story_analysis, vision_analysis=None, tone=None):
        """Generate platform-specific caption"""
        config = self.PLATFORM_CONFIGS.get(platform, self.PLATFORM_CONFIGS['instagram'])
        
        system_prompt = f"""You are a {platform.capitalize()} Caption Expert.
        Platform Rules:
        - Max length: {config['max_length']} chars
        - Tone: {config['tone']}
        - Optimal length: {config['optimal_length']}
        - Must include: {', '.join(config['features'])}
        
        Create an engaging caption that drives engagement. Include a strong hook in the first line."""
        
        user_prompt = self._build_prompt(story_analysis, vision_analysis, tone)
        
        caption = self.llm.generate(system_prompt, user_prompt, temperature=0.8)
        
        # Generate 3 variants for A/B testing
        variants = self._generate_variants(system_prompt, user_prompt)
        
        return {
            'platform': platform,
            'primary_caption': caption,
            'variants': variants,
            'character_count': len(caption),
            'estimated_read_time': f"{len(caption.split()) // 200 + 1} min read"
        }
    
    def _build_prompt(self, story_analysis, vision_analysis, tone):
        parts = [f"Story Analysis: {story_analysis}"]
        if vision_analysis:
            parts.append(f"Image Analysis: {vision_analysis}")
        if tone:
            parts.append(f"Desired Tone: {tone}")
        return "\n\n".join(parts)
    
    def _generate_variants(self, system_prompt, user_prompt):
        """Generate A/B test variants"""
        variants = []
        for temp in [0.6, 0.9]:
            variant = self.llm.generate(system_prompt, user_prompt, temperature=temp)
            variants.append(variant)
        return variants
    
    def generate_all_platforms(self, story_analysis, vision_analysis=None, tone=None):
        """Generate captions for all platforms"""
        results = {}
        for platform in ['facebook', 'instagram', 'linkedin']:
            results[platform] = self.generate_caption(
                platform, story_analysis, vision_analysis, tone
            )
        return results