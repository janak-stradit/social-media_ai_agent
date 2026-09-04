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
        return [p.strip() for p in response.split("\n") if p.strip()]

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

UNIQUENESS RULE:
Generate the image prompt and video script based specifically on the storyline provided. Do not reuse a generic visual template across different storylines.
For every storyline, first identify:
1. The main topic/problem discussed.
2. The specific business challenge.
3. The selected project and its actual capability.
4. The key transformation or outcome.
5. The most appropriate visual metaphor for that specific topic.

Theme: [Description of the shared problem, or the single problem if not shared]
Competitors Covered: [List the competitor names that fall under this theme]

Caption Prompt:
[A detailed prompt instructing the social media writer on exactly what to write. Outline the specific hook, the core strategic topic, the exact product capabilities to highlight, and the tone. Do NOT write the actual caption here. Give instructions for writing it.]

Image Prompt:
[Create a highly detailed prompt for a multi-slide Carousel (e.g., 3-5 slides) that directly represents the specific storyline. Each slide must be text-oriented, deeply informative, and visually connected to the others.
The carousel MUST mimic the high-end, colorful, and highly professional layout used by top-tier consulting and financial technology firms. It must rely heavily on clean typography, data visualization, and a cohesive, vibrant color palette (e.g., deep navy, vibrant orange, or slate grey) rather than just abstract graphics.

To understand the exact level of detail and formatting required, here is a PERFECT example of what you must generate. You must adapt this exact level of granular detail, color selection, and typography specification to the new storyline:

--- EXAMPLE FORMAT ---
Overall Aesthetic/Style: Premium institutional financial technology, text-oriented data visualization, vibrant and cohesive brand palette of deep navy (#0B1B33), slate grey (#454B54), and vibrant orange (#E0703A) as the singular accent color, clean modern sans-serif typography for UI/data elements paired with a serif display face for headlines, professional corporate presentation style consistent with top-tier wealth management and consulting decks. Every slide carries a persistent thin orange underline rule beneath its headline as a recurring brand device, tying the carousel together visually.

Slide 1 (Title/Hook): Deep navy background (#0B1B33) with a subtle diagonal gradient darkening toward the bottom-right corner. Layout is left-aligned (not centered) — a text block sits in the left 60% of the frame, vertically centered, leaving the right 40% for a graphic. Headline in large bold white serif font: "[Title]". Beneath it, a thin horizontal orange rule (4px, #E0703A, 180px wide). Below the rule, a smaller sans-serif subhead in slate-blue grey: "[Subhead]". On the right third of the frame: [Extremely detailed description of graphic, e.g. a minimalist balance-beam graphic rendered in thin white and orange linework].

Slide 2 (Context/Problem): Split-screen layout, left side slate grey (#454B54), right side deep navy (#0B1B33), divided by a 3px vertical orange rule. Eyebrow label centered above the divide in small tracked-out orange caps: "THE PROBLEM." Left panel: [Detailed description of data viz/metric]. Right panel: [Detailed description of UI element or workflow graphic]. Body copy centered beneath both panels in light grey sans-serif: "[Context copy]".

Slide 3 (Solution/StradIT Capabilities): Full-bleed dark-mode UI dashboard on deep navy (#0B1B33), styled as a live product screen. Top-left header text: "[StradIT Project Name]" in bold white sans-serif, with smaller slate-grey subtext "[Subhead]". Top-right pill badge in orange-outlined rounded rectangle reading "LIVE MONITORING." Three metric cards arranged in a horizontal row beneath the header... [Extremely detailed description of the metrics and line chart representing the specific StradIT capability]. Below the cards, bold white text: "[Solution Copy]".

Slide 4 (Outcome/CTA): Deep navy background (#0B1B33) with two soft glowing circles. Centered graphic: [Detailed description of final graphic]. Beneath the graphic, large bold white sans-serif headline centered: "[Headline]". Beneath that, a smaller tracked-out orange caps tagline: "[Tagline]".

Negative Constraints: No logos, no competitor branding or names, no cluttered stock photography, no generic abstract art unconnected to the data narrative, no photographic human figures, no overly minimalist compositions devoid of text or metrics — every slide must remain text-oriented and data-driven, with graphics functioning as supporting infographic elements rather than standalone decoration.
----------------------

IMPORTANT RULES: 
- The carousel MUST be text-oriented and data-driven. Visuals should support the text (like a premium infographic or presentation slide), not the other way around.
- IF a specific StradIT project was selected, at least one slide MUST visually integrate highly detailed elements representing that project's exact capabilities (e.g., if AltsIQ, show a slide with a glowing 233-point compliance report layout).
- Do NOT reuse the exact same visual metaphor for every storyline. Tailor the format to the specific competitor's post and StradIT project.
- Do NOT include competitor names or logos.
- Make the prompt rich, highly detailed, text-heavy, informative, and visually stunning, exactly like top-tier professional corporate posts.]

Video Script:
[Create a video narrative that is directly derived from the storyline.
Do NOT use the same generic scenes for every storyline. Design each video's scenes around a unique, compelling visual metaphor.

To understand the exact level of cinematic vision, pacing, lighting, and detail required, here is a PERFECT example of what you must generate. You must adapt this exact level of granular detail and professional tone to the new storyline:

--- EXAMPLE FORMAT ---
[Overall style description] A premium corporate technology video in a single continuous narrative flow, shot with the restrained cinematography of an institutional investment film — shallow depth of field, cool navy and slate color grading with a single warm orange accent light source, slow deliberate camera moves, no jump cuts, no handheld shake. The film treats "[Insert core theme metaphor, e.g. drift]" as its visual throughline: the opening shot begins slightly off-balance, and by the final shot everything has settled into alignment.

0:00-0:04 [Extremely detailed shot description: e.g. Extreme close-up of a physical desk-model gyroscope balanced on a dark slate surface, spinning slowly and tilting a few degrees off its vertical axis, rack focus pulling from the tilted spindle to soft bokeh in the background, then slowly sharpening again as the spindle drifts back toward vertical. Low, directional lighting from the upper left casts a long shadow across the table, with a faint warm orange rim light catching the gyroscope's edge. Environment is a minimal, unbranded office surface — no visible logos, papers, or screens.]

0:04-0:08 [Extremely detailed shot description: e.g. Slow overhead tracking shot moving across a glass conference table where a single tablet lies face-up beside a relaxed, open hand — not reaching for it. The screen glows a calm, steady navy-white, its light spilling softly across the tabletop. A single warm orange indicator pulses once on the tablet's edge, then holds steady. Cool ambient light dominates, with the screen the brightest element in frame; the background is a softly blurred, unbranded skyline, deliberately out of focus.]

0:08-0:10 [Extremely detailed shot description: e.g. Slow pull-back and slight rise, revealing the gyroscope now spinning perfectly upright in the near foreground and the glowing tablet steady in the background, both elements in quiet equilibrium within the same frame. Lighting warms subtly as the camera settles, as though the room itself has found its balance.]

Audio: A calm, authoritative voiceover saying: "[Voiceover script tailored to the storyline]". Subtle ambient room tone with a faint low synth pad, swelling gently as the camera settles in the final shot; no dialogue, no sound effects, no ticking clocks or alarm tones. No subtitles. No text overlays.

End frame: Minimal premium navy background with StradIT branding and the tagline: "Intelligence. Automated."
----------------------

IMPORTANT RULES: 
- Do not mention or display competitor names or logos. 
- Do not invent product capabilities or claim specific processing times. 
- The video MUST have its own visual storytelling concept (like the gyroscope metaphor above) and not simply describe or animate the image prompt.
- Make the cinematography rich, highly detailed, and deeply professional.]

(Repeat the Theme block for each distinct theme you found among the Strong/Moderate matches)

FINAL GUARDRAILS AND SAFETY CHECK:
Before outputting, ensure:
1. NO competitor names or logos appear anywhere in the Caption Prompt, Image Prompt, or Video Script.
2. NO exaggerated performance claims ("instant", "100% accurate", "in seconds") are used.
3. Every feature mentioned EXACTLY matches a capability provided in the StradIT project context.
4. The visual concepts for the Image and Video are highly UNIQUE to this specific storyline and NOT generic templates.
"""
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
