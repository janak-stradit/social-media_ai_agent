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

    def generate_channel_storyline(self, posts_text, project_context, return_usage=False, character_config=None):
        """Generate a structured channel storyline based on competitor posts and our project context"""
        if not character_config:
            character_config = {"mode": "auto", "details": ""}

        mode = character_config.get("mode", "without_character")

        char_rules_prompt = ""
        image_prompt = ""
        video_prompt = ""
        validation_prompt = ""

        if mode == "with_character":
            char_rules_prompt = """### CHARACTER GENERATION

The user has explicitly requested to include a character.
You MUST automatically create a suitable character based on the storyline, business context, company context, and intended audience.
The AI must determine these details automatically.

#### Character Selection Rules
Select a character whose professional role naturally fits the storyline.
Examples:
- Investment/portfolio storyline -> investment professional, portfolio manager, investment analyst, or advisor
- Fund analysis storyline -> fund analyst, portfolio manager, or investment professional
- AML/compliance storyline -> compliance professional, AML analyst, or risk officer
- Due diligence storyline -> investment analyst, due diligence professional, or compliance professional
- Wealth management storyline -> financial advisor, wealth manager, or client-facing investment professional
- Executive/business strategy storyline -> senior financial executive or business leader
- Technology/AI transformation storyline -> appropriate financial-services professional interacting with the technology

Do not force the same character role into every storyline. The selected character must be appropriate to the actual business problem being presented.

#### Character Profile
Before generating the image and video prompts, internally determine:
- Professional role
- Appropriate age range
- Professional appearance
- Business-appropriate clothing
- Relevant environment
- Natural personality/expression
- Relevant actions
- Purpose of the character in the storyline

The character should look like a credible professional working in the relevant business environment.
Avoid generic stock-photo people, random models, unrelated professions, overly casual clothing, exaggerated expressions, characters that do not logically interact with the storyline, or decorative characters with no meaningful purpose."""

            image_prompt = """### IMAGE GENERATION
Create a highly detailed prompt for a multi-slide Carousel (e.g., 3-5 slides) that directly represents the specific storyline. Each slide must be text-oriented, deeply informative, and visually connected to the others.
Mimic high-end, colorful, professional layouts (clean typography, data visualization, cohesive vibrant color palette).

Create the image prompt using the automatically generated character profile. The character must be relevant to the storyline, perform a meaningful business-related action, interact naturally with the environment, technology, data, or product, and look credible for the company/business context.
Do not simply place a person next to a dashboard. The character should help communicate the business problem, solution, or outcome.
Integrate their description directly into the relevant slide descriptions (e.g., Slide 1 or 2).

For Carousels, follow this exact formatting style. Integrate the character description (e.g. "A 35-year-old Institutional Investment Analyst wearing a charcoal-grey tailored suit...") directly into the relevant slide descriptions:

--- EXAMPLE CAROUSEL FORMAT ---
Overall Aesthetic/Style: Premium institutional financial technology...
Slide 1 (Title/Hook): Deep navy background... [Describe character here]
Slide 2 (Context/Problem): Split-screen layout...
Slide 3 (Solution/Capabilities): Full-bleed dark-mode UI dashboard...
Slide 4 (Outcome/CTA): Deep navy background...
----------------------"""

            video_prompt = """### VIDEO GENERATION
Create a video narrative directly derived from the storyline.

Use the same AI-generated character consistently throughout the video.
Maintain: Same professional identity, general appearance, clothing, hairstyle, age range, role.
The character's actions should evolve with the storyline.
Do not use the same character type or the same "stressed employee -> AI dashboard -> confident employee" sequence for every video.
Create a 15-second cinematic institutional-finance video script featuring the character. Ensure the scenes progress the storyline logically (e.g. Environment -> Challenge -> Intelligent Analysis -> Decision).

Follow this exact formatting style:
--- EXAMPLE CHARACTER VIDEO FORMAT ---
Create a 15-second cinematic institutional-finance video based on the storyline...

CHARACTER:
A [Age]-year-old [Role] in [Clothing]. [Personality]. Maintain the same character appearance, clothing, hairstyle, and overall identity throughout all scenes.

SCENE 1 — MARKET ENVIRONMENT
[Detailed description of the character observing the environment]

SCENE 2 — ANALYTICAL CHALLENGE
[Detailed description of the character encountering the specific problem/challenge]

SCENE 3 — INTELLIGENT ANALYSIS
[Detailed description of the solution interface and the character interacting with/observing it]

SCENE 4 — DECISION
[Detailed description of the character confidently taking action based on the insights]

VISUAL STYLE:
Premium institutional financial technology, photorealistic, cinematic professional lighting, realistic corporate environment, sophisticated financial-data visualization, restrained and credible.

CHARACTER CONSISTENCY:
The same analyst must appear consistently throughout all scenes. Do not change the person's age, clothing, appearance, or role.

AUDIO:
A calm, authoritative voiceover saying: "[Voiceover script tailored to the storyline]". Subtle ambient room tone; no dialogue, no sound effects.

BRANDING RULE:
If there is a visual of StradIT, the text "Strad" must be strictly ORANGE and "IT" must be strictly WHITE. The tagline is "Automate.Elevate.Accelerate" and the tagline color must also be WHITE. Ensure this specific styling is explicitly mentioned in the prompt whenever StradIT is visualized.
----------------------"""

            validation_prompt = """### FINAL CHARACTER VALIDATION
* At least one relevant character is present
* Character role matches the storyline
* Character performs a meaningful action
* Character fits the business environment
* Character is consistent across video scenes
* Character is not merely decorative"""

        else:
            char_rules_prompt = """### CHARACTER GENERATION
The user has explicitly requested WITHOUT CHARACTER. Do not generate or define any characters."""

            image_prompt = """### IMAGE GENERATION
Create a highly detailed prompt for a multi-slide Carousel (e.g., 3-5 slides) that directly represents the specific storyline. Each slide must be text-oriented, deeply informative, and visually connected to the others.
Mimic high-end, colorful, professional layouts (clean typography, data visualization, cohesive vibrant color palette).

Do not include human characters in the image.
Use appropriate: Business environments, Financial data, Technology, Market visualizations, Documents, Product interfaces, Objects, Abstract visual metaphors.

For Carousels, follow this exact formatting style:

--- EXAMPLE CAROUSEL FORMAT ---
Overall Aesthetic/Style: Premium institutional financial technology...
Slide 1 (Title/Hook): Deep navy background...
Slide 2 (Context/Problem): Split-screen layout...
Slide 3 (Solution/Capabilities): Full-bleed dark-mode UI dashboard...
Slide 4 (Outcome/CTA): Deep navy background...
----------------------"""

            video_prompt = """### VIDEO GENERATION
Create a video narrative directly derived from the storyline.

Create a 10-second premium corporate technology video in a single continuous narrative flow.
Do not introduce human characters. Build the narrative using environments, objects, data, technology, or visual metaphors.

Follow this exact formatting style:
--- EXAMPLE NON-CHARACTER VIDEO FORMAT ---
[Overall style description] A premium corporate technology video...

0:00-0:04 [Extremely detailed shot description of a visual metaphor...]
0:04-0:08 [Extremely detailed shot description progressing the metaphor...]
0:08-0:10 [Extremely detailed shot description concluding the metaphor...]

Audio: A calm, authoritative voiceover saying: "[Voiceover script]".

BRANDING RULE:
If there is a visual of StradIT, the text "Strad" must be strictly ORANGE and "IT" must be strictly WHITE. The tagline is "Automate.Elevate.Accelerate" and the tagline color must also be WHITE. Ensure this specific styling is explicitly mentioned in the prompt whenever StradIT is visualized.
----------------------"""

            validation_prompt = """### FINAL CHARACTER VALIDATION
* Are there absolutely no human characters?"""

        system = f"""You are an expert strategic analyst and Content Generation Agent.
You will be provided with a Storyline or Context (in <COMPETITOR_POSTS>) and a list of StradIT projects (in <OUR_PROJECT_CONTEXT>).

## CONTENT GENERATION FLOW

Follow this sequence strictly:

### STEP 1 — STORYLINE

Use the provided storyline as the **single source of truth**.

Extract:
* Main topic
* Industry context
* Business problem
* Key message
* Selected project
* Project capabilities
* Desired business outcome
* Competitor context, if relevant

Do not generate the image or video prompt yet.

{char_rules_prompt}

### STEP 3 — PROMPT GENERATION

Only after completing the character selection should you generate the content prompts.
Generate independently:
1. Caption Prompt
2. Image Prompt
3. Video Prompt / Video Script

All three must originate from the same storyline, but they must be independently designed.

{image_prompt}

{video_prompt}

### BUSINESS CONTEXT RULE
The character must always feel appropriate for a professional B2B/company video. The visual should communicate: "Here is a real professional dealing with this specific business problem.", not: "Here is a random person placed into a corporate image."

CAPTION PROMPT
Provide a detailed prompt instructing the social media writer on exactly what to write.
Outline the specific hook, the core strategic topic, the exact product capabilities to highlight, and the tone.
Do NOT write the actual caption here. Only provide the instructions/context for the writer.
If characters are selected, characters may be referenced when naturally relevant.
Do not invent fictional customer experiences, quotes, names, testimonials, personal claims, or unsupported facts.

### STEP 4 — GUARDRAILS

After generating the prompts, validate all outputs against the following guardrails:

Storyline Alignment
* Does the image represent the actual storyline?
* Does the video represent the actual storyline?
* Does the caption communicate the actual storyline?
* Is the selected project used accurately?

Character Consistency
{validation_prompt}

Product Accuracy
* Do not invent product capabilities.
* Do not show functionality that the project does not provide.
* Do not make unsupported claims.

Final Validation
Before returning the result, ensure:
1. Is this clearly derived from the storyline?
2. Are the characters intentional rather than decorative?
3. Is the image visually different from previous storylines?
4. Is the video structurally different from previous videos?
5. Is the project capability accurately represented?
6. Are all claims supported by the storyline?
7. Are the guardrails satisfied?
If any answer is NO, regenerate the affected output internally.

### OUTPUT FORMAT

You must perform Steps 1, 2, and 4 internally.
Your final response MUST be a valid JSON object containing ONLY the final generated content (Step 3), as well as a VERY SHORT list of facts you observed (maximum 2 or 3 facts total, keep them minimal but informative). Do not include your internal reasoning in the final JSON.

Respond with exactly this JSON structure and nothing else:

{{
  "observed_facts": ["concise fact 1", "concise fact 2"],
  "caption": "The instructions for the social media writer here (do NOT write the actual caption)...",
  "image_prompt": "The highly detailed multi-slide carousel prompt here...",
  "video_prompt": "The highly detailed 10-second cinematic video script here..."
}}
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
