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
You will be provided with specific text from multiple competitor posts in the <COMPETITOR_POSTS> block and a list of StradIT projects in the <OUR_PROJECT_CONTEXT> block.

REQUIRED BEHAVIOR:
For EACH competitor post independently:
1. Understand what the competitor is actually talking about in the specific post provided. Do not combine unrelated competitor posts.
2. Identify the underlying BUSINESS / INVESTMENT / OPERATIONAL / RESEARCH / COMPLIANCE problem related to that specific topic.
3. Compare that underlying problem against ALL available projects in <OUR_PROJECT_CONTEXT>.
4. Follow the Project Selection Rule below to determine the connection strength and select the project.
5. Create a storyline following a 55:45 ratio: 
   - ~55% focusing on the competitor's actual topic, the underlying business/investment problem, why the problem matters, and the context surrounding it.
   - ~45% focusing on the selected project, how the project addresses the underlying problem, and the business/investment impact.
   The competitor/problem portion MUST remain the larger portion of the storyline. Do not simply mention the competitor and then become a product advertisement. First establish a strong understanding of the problem.

PROJECT SELECTION RULE:
- Evaluate the competitor topic against ALL available projects before selecting a project.
- Select a project only when there is a clear and defensible connection between:
  1. The actual topic/problem discussed by the competitor.
  2. The business or industry challenge implied by that topic.
  3. A specific capability of one of our projects.
- Do not select a project merely because the competitor topic can be loosely associated with it.
- Do not force project diversity. Multiple competitors may select the same project if it is genuinely the strongest match.
- Do not select a different project merely to ensure every project is represented.
- If the connection requires multiple unsupported assumptions, select "No Strong Match."
- Select "No Strong Match" when no project has a defensible connection.
- When selecting a project, use the project whose core capability most directly addresses the competitor's actual topic.

EVIDENCE RULE:
- Never invent competitor pain points.
- Do not state that a competitor "faces", "struggles with", "needs", or "has a problem with" something unless it is explicitly stated or strongly supported by the competitor content.
- When making an industry-level inference, use language such as:
  "this reflects a broader industry opportunity"
  "this creates an opportunity for"
  "this can create challenges for firms"
- Do not present an inference as a competitor-specific problem.

CONTENT CONSISTENCY RULE:
- Use only the selected projects from STEP 1.
- Every feature, capability, metric, workflow, or outcome mentioned in the caption, image prompt, or video must be supported by the verified capabilities of those selected projects.
- Do not borrow capabilities from other projects.
- Do not invent:
  - processing times
  - real-time capabilities
  - accuracy claims
  - databases/integrations
  - reports
  - risk indicators
  - business outcomes
  - performance improvements
- Competitor names are internal context only and must never appear in public-facing content.

GENERAL CRITICAL WRITING RULES:
1. NEVER change the meaning of the competitor's post or criticize the competitor.
2. NO SELLING: Do not include ANY Call-To-Action (CTA) or links.
3. NO MARKDOWN OR BULLET POINTS in the storyline: Write in flowing paragraphs.
4. PROFESSIONAL TONE: Keep the tone highly professional, analytical, institutional, and credible. Write like an authoritative industry expert.
5. Do not connect two unrelated competitor topics just to create one storyline. Each competitor is analyzed independently.

You MUST return a single JSON object with two keys:
{
    "observed_facts": ["Headline for post 1", "Headline for post 2", "... one for EVERY post provided"],
    "prompt": "A single continuous string containing the structured storylines for ALL provided posts."
}

CRITICAL REQUIREMENT: You MUST generate a storyline for EVERY SINGLE post provided in the input. Do not group them. If 5 posts are provided, there must be 5 distinct storylines separated by '\n\n---\n\n'.

The `prompt` string MUST be formatted exactly like this:

[--- STEP 1: INDIVIDUAL ANALYSIS ---]
(Repeat this block for EVERY post provided, separated by \n\n---\n\n)
Competitor: [Competitor Name]
Counter Strategy Headline: [A concise headline describing the competitor's actual topic and the underlying challenge]
Selected Project: [Best matching project or "No Strong Match"]
Connection Strength: [Strong / Moderate / Weak / No Strong Match]
Storyline:
[One cohesive storyline following the required 55:45 ratio, plain text paragraphs]

[IF Connection Strength is Weak or No Strong Match, include:]
Reason for No Match:
[Briefly explain why this competitor topic does not strongly align with any internal projects.]

[--- STEP 2: CONTENT GENERATION ---]
(Review all the Strong/Moderate matches from Step 1. Group them by underlying problem/theme. For each distinct theme, generate ONE set of prompts. If posts are unrelated, they get their own Theme block.)

CRITICAL: Step 2 must inherit capabilities ONLY from the projects selected in Step 1.

STRICT VALIDATOR: Before generating the content below, you MUST ensure you are not inventing specific processing times (e.g., "in minutes"), data volumes (e.g., "thousands of pages"), or specific UI recommendations (e.g., "PASS recommendation") unless explicitly documented in the project context. The image prompt MUST accurately reflect the actual selected project(s).

Theme: [Description of the shared problem, or the single problem if not shared]
Competitors Covered: [List the competitor names that fall under this theme]

Caption Prompt:
[A detailed prompt instructing the social media writer on exactly what to write. Outline the specific hook, the core strategic topic, the exact product capabilities to highlight, and the tone. Do NOT write the actual caption here. Give instructions for writing it.]

Image Prompt:
[Write a highly detailed image prompt TAILORED TO THIS SPECIFIC THEME and the selected project's capabilities.
Use a sophisticated split-screen composition.
LEFT SIDE: Show the specific manual problem, fragmented data, or challenge described in the theme.
RIGHT SIDE: Show the automated AI solution provided by the selected project (e.g., a structured compliance dashboard, sentiment analysis tools).
Show a subtle visual transition from fragmented manual information to organized, automated intelligence.
Style: premium institutional financial technology, realistic corporate environment, sophisticated enterprise UI, clean composition, photorealistic, cinematic professional lighting, high-end B2B aesthetic.
IMPORTANT: Do not include competitor names, competitor logos, or competitor branding. Do not portray unsupported claims or unrealistic financial outcomes.]

Video Script:
[Write a 15-second premium cinematic B2B financial-technology video script TAILORED TO THIS SPECIFIC THEME and the selected project's capabilities.
SCENE 1 — 0:00–0:05
Visual: Show the specific manual problem/fragmented workflows related to this theme.
Voiceover: Introduce the problem based on the theme.
SCENE 2 — 0:05–0:10
Visual: Transition into the sophisticated AI-powered financial platform, highlighting the selected project's specific features. Show the information becoming structured.
Voiceover: Explain how the selected project automates the research/due diligence for this specific challenge.
SCENE 3 — 0:10–0:15
Visual: Show a professional investment analyst confidently evaluating the consolidated results and focusing on strategic decision-making.
Voiceover: Summarize the strategic benefit for the team.
END FRAME: Minimal premium background with StradIT branding and the tagline: "Intelligence. Automated."
VISUAL STYLE: Premium institutional financial technology, Photorealistic, Cinematic corporate lighting, Sophisticated enterprise software interfaces, Clean and minimal composition, High-end B2B aesthetic.
IMPORTANT RULES: Do not mention or display any competitor names/logos. Do not invent product capabilities or claim specific processing times. Do not show guaranteed outcomes or "PASS/BUY" recommendations unless verified.]

(Repeat the Theme block for each distinct theme you found among the Strong/Moderate matches)"""
        # Place the massive project context FIRST, and the small competitor posts LAST so the LLM doesn't ignore them.
        user = f"<OUR_PROJECT_CONTEXT>\n{project_context}\n</OUR_PROJECT_CONTEXT>\n\n<COMPETITOR_POSTS>\n{posts_text}\n</COMPETITOR_POSTS>"
        
        result, usage = self.llm.generate_json(system, user, temperature=0.3, max_tokens=4000, return_usage=True)
        
        if result and "prompt" in result:
            result["prompt"] = result["prompt"].strip()
            
            
        if return_usage:
            return result, usage
        return result

    def filter_relevant_posts(self, posts: list, project_context: str) -> list:
        """Filter a list of scraped competitor posts to only those relevant to our projects."""
        if not posts:
            return []
        
        system_prompt = """You are an expert strategic analyst.
Your task is to review a list of scraped competitor social media posts and determine which ones are RELEVANT to our company's projects/capabilities.

Relevance is defined as: The post discusses a topic, industry challenge, or technology that our projects can address or provide a counter-strategy for.
Unrelated topics include: Generic HR updates, internships, employee volunteering, generic holidays, purely internal company news without industry insight.

Return a JSON object with a single key "relevant_indices" containing a list of integers representing the indices of the posts that ARE relevant.
Example: {"relevant_indices": [0, 2, 5]}
"""
        
        posts_text = ""
        for i, p in enumerate(posts):
            text = (p.get("text") or "")[:500]
            posts_text += f"[{i}] {text}\n---\n"
            
        user_prompt = f"<OUR_PROJECT_CONTEXT>\n{project_context}\n</OUR_PROJECT_CONTEXT>\n\n<COMPETITOR_POSTS>\n{posts_text}\n</COMPETITOR_POSTS>\n\nIdentify the relevant indices."
        
        try:
            result = self.llm.generate_json(system_prompt, user_prompt, temperature=0.1)
            relevant_indices = result.get("relevant_indices", [])
            valid_indices = []
            for idx in relevant_indices:
                try:
                    valid_indices.append(int(idx))
                except (ValueError, TypeError):
                    pass
                    
            filtered_posts = [posts[i] for i in valid_indices if 0 <= i < len(posts)]
            return filtered_posts
        except Exception as e:
            import traceback
            print(f"[StoryAgent] Error filtering posts: {e}")
            traceback.print_exc()
            return posts