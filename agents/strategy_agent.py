from services.llm_service import LLMService
from datetime import datetime, timedelta

class StrategyAgent:
    """Agent that determines optimal posting strategy"""
    
    PLATFORM_BEST_TIMES = {
        'facebook': {
            'weekdays': ['9:00 AM', '1:00 PM', '3:00 PM'],
            'weekends': ['12:00 PM', '1:00 PM'],
            'best_days': ['Tuesday', 'Wednesday', 'Thursday']
        },
        'instagram': {
            'weekdays': ['11:00 AM', '1:00 PM', '7:00 PM'],
            'weekends': ['10:00 AM', '2:00 PM'],
            'best_days': ['Tuesday', 'Wednesday', 'Friday']
        },
        'linkedin': {
            'weekdays': ['8:00 AM', '12:00 PM', '5:00 PM'],
            'weekends': [],  # Minimal weekend engagement
            'best_days': ['Tuesday', 'Wednesday', 'Thursday']
        }
    }
    
    def __init__(self):
        self.llm = LLMService()
    
    def create_strategy(self, platform, story_analysis, content_type='standard'):
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
        
        Create the optimal posting strategy."""
        
        strategy = self.llm.generate_json(system_prompt, user_prompt)
        strategy['platform_best_practices'] = best_times
        return strategy
    
    def schedule_posts(self, platforms, story_analysis, start_date=None):
        """Create a multi-platform posting schedule"""
        if start_date is None:
            start_date = datetime.now() + timedelta(days=1)
        
        schedule = {}
        for i, platform in enumerate(platforms):
            # Stagger posts by 2-4 hours for cross-platform
            post_time = start_date + timedelta(hours=i*3)
            strategy = self.create_strategy(platform, story_analysis)
            strategy['scheduled_time'] = post_time.isoformat()
            schedule[platform] = strategy
        
        return {
            'schedule': schedule,
            'total_posts': len(platforms),
            'campaign_duration': f"{len(platforms) * 3} hours",
            'recommended_tools': ['Buffer', 'Hootsuite', 'Later', 'Meta Business Suite']
        }
    
    def generate_all_strategies(self, story_analysis):
        """Generate strategies for all platforms"""
        results = {}
        for platform in ['facebook', 'instagram', 'linkedin']:
            results[platform] = self.create_strategy(platform, story_analysis)
        return results