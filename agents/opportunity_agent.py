from services.llm_service import LLMService

class OpportunityAgent:
    """Agent that identifies content whitespace and product/domain expansion opportunities
    by comparing competitor posts against our own project context."""

    def __init__(self):
        self.llm = LLMService()

    def generate_opportunities(self, posts_text, project_context, return_usage=False):
        """Analyze competitor posts vs our project context and surface two categories of opportunity."""
        system = """You are an enterprise sales & product strategy analyst.
Given a set of competitor social/content posts and our own project/product context, identify two distinct categories of opportunity:

1. Unserved Content Themes & Growth Whitespace: topics, angles, or audience needs that competitors are NOT covering (or covering poorly) in their content, which represent a content/marketing growth opportunity for us.
2. Emerging Domain Expansion & Custom Integration: adjacent domains, product capabilities, or custom integrations that competitor activity signals demand for, which we could realistically build or extend our project to cover.

For each item, give a short punchy title (under 12 words) and a 1-3 sentence description explaining the opportunity and why it matters.

Return ONLY a valid JSON object with this schema:
{
    "unserved_themes": [{"title": "...", "description": "..."}],
    "domain_expansion": [{"title": "...", "description": "..."}]
}
Provide 3-5 items per category."""

        user = f"<COMPETITOR_POSTS>\n{posts_text}\n</COMPETITOR_POSTS>\n\n<OUR_PROJECT_CONTEXT>\n{project_context}\n</OUR_PROJECT_CONTEXT>"

        result, usage = self.llm.generate_json(system, user, return_usage=True)
        if return_usage:
            return result, usage
        return result
