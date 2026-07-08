from services.llm_service import LLMService
from services.memory_service import MemoryService

class HashtagAgent:
    """Agent that generates optimized hashtags with trend awareness"""
    
    PLATFORM_HASHTAG_LIMITS = {
        'facebook': 3,
        'instagram': 30,
        'linkedin': 5
    }
    
    def __init__(self):
        self.llm = LLMService()
        self.memory = MemoryService()
    
    def generate_hashtags(self, platform, story_analysis, vision_analysis=None, count=None):
        """Generate platform-optimized hashtags"""
        max_tags = count or self.PLATFORM_HASHTAG_LIMITS.get(platform, 10)
        
        # Get trending context from memory
        trending = self.memory.get_trending_hashtags(
            category=story_analysis.get('themes', ['general'])[0]
        )
        
        system_prompt = f"""You are a Hashtag Strategy Expert for {platform.capitalize()}.
        Rules:
        - Generate exactly {max_tags} hashtags
        - Mix: 40% broad reach, 40% niche, 20% branded/trending
        - Research shows: {platform} posts with {max_tags//2} hashtags get best engagement
        - Avoid banned or overused spam hashtags
        - Include 1-2 location-based if relevant
        
        Return JSON with: hashtags (list), categories (dict), engagement_prediction (score 1-10)"""
        
        user_prompt = f"""Story Themes: {story_analysis.get('themes', [])}
        Emotions: {story_analysis.get('emotions', [])}
        Visual Elements: {vision_analysis.get('colors', []) if vision_analysis else 'N/A'}
        Trending Context: {trending}
        
        Generate optimized hashtag set."""
        
        result = self.llm.generate_json(system_prompt, user_prompt)
        
        # Store for future trend analysis
        self.memory.store_content(
            f"hashtag_{platform}_{hash(str(result))}",
            ' '.join(result.get('hashtags', [])),
            {'type': 'hashtag', 'platform': platform}
        )
        
        return result
    
    def generate_all_platforms(self, story_analysis, vision_analysis=None):
        """Generate hashtags for all platforms"""
        results = {}
        for platform in ['facebook', 'instagram', 'linkedin']:
            results[platform] = self.generate_hashtags(
                platform, story_analysis, vision_analysis
            )
        return results