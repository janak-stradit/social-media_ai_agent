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

        # Festive/seasonal greeting content (see services/festival_service.py)
        # isn't meant to promote a specific product/service, so it skips the
        # strict project-matching gate entirely instead of being blocked as
        # "No Strong Match" for not connecting to any StradIT capability.
        if "--- FESTIVE GREETING ---" in posts_text:
            return self._generate_festive_storyline(posts_text, character_config, return_usage)

        mode = character_config.get("mode", "without_character")

        char_rules_prompt = ""
        image_prompt = ""
        video_prompt = ""
        validation_prompt = ""

        if mode == "with_character":
            # "characters" (plural) supports selecting more than one brand asset at
            # once (e.g. Aiden AND the StradIT logo together). Falls back to the
            # older singular "character" field for callers that haven't switched.
            raw_selection = character_config.get("characters")
            if raw_selection is None:
                legacy = character_config.get("character", "auto")
                raw_selection = [legacy] if legacy else []
            selected_assets = [c for c in raw_selection if c and c != "auto"]

            asset_labels = {"aiden": "Aiden", "logo": "the StradIT logo"}
            human_assets = [a for a in selected_assets if a != "logo"]
            include_logo = "logo" in selected_assets

            if human_assets:
                selected_char = " and ".join(asset_labels.get(a, a) for a in human_assets)
                logo_instruction = (
                    "\nAlso feature the StradIT logo mark naturally integrated into the composition "
                    "(e.g. on a screen, badge, document header, or corner element) alongside the character."
                    if include_logo
                    else ""
                )
                char_rules_prompt = f"""### CHARACTER GENERATION
The user has explicitly requested to include a specific brand character: '{selected_char}'.
You MUST use this exact character in your visual prompts (both image and video). Do not invent a new character.
Instead of describing a random professional (e.g., 'A 42-year-old Compliance Risk Officer'), describe the brand character '{selected_char}'.
Ensure the character '{selected_char}' is performing a meaningful business-related action and fits logically into the storyline.
{logo_instruction}

#### Character Profile & Guardrails
- Professional appearance and business-appropriate clothing (e.g., tailored suit, corporate attire)
- Maintain the correct gender identity of the selected brand character (e.g., if the character is 'Aiden', explicitly describe him as a male professional).
- The character should look like a credible professional working in the relevant business environment.
- Avoid overly casual clothing (no t-shirts, sweatpants, etc.).
- Avoid exaggerated expressions or treating the character as merely decorative."""

                image_prompt = f"""### IMAGE GENERATION
Create a highly detailed prompt for a multi-slide Carousel (e.g., 3-5 slides) that directly represents the specific storyline. Each slide must be text-oriented, deeply informative, and visually connected to the others.
Mimic high-end, colorful, professional layouts (clean typography, data visualization, cohesive vibrant color palette).

Create the image prompt using the specified brand character: '{selected_char}'. The character must be relevant to the storyline, perform a meaningful business-related action, interact naturally with the environment, technology, data, or product.
Integrate their description directly into the relevant slide descriptions (e.g., Slide 1 or 2).
{logo_instruction}

For Carousels, follow this exact formatting style. Integrate the character description directly into the relevant slide descriptions:

--- EXAMPLE CAROUSEL FORMAT ---
Overall Aesthetic/Style: Premium institutional financial technology...
Slide 1 (Title/Hook): Deep navy background... [Describe '{selected_char}' here]
Slide 2 (Context/Problem): Split-screen layout...
Slide 3 (Solution/Capabilities): Full-bleed dark-mode UI dashboard...
Slide 4 (Outcome/CTA): Deep navy background...
----------------------

BRANDING RULE:
- Aspect Ratio: Every image/carousel slide must be 1080x1080 (1:1 aspect ratio).
- Text Overlays & Typography: Include the Slide Title and a short summary sentence directly in the image. The typography MUST be highly professional, soft, minimalist, and pleasant to the eye (mimicking refined corporate fonts like Inter or Helvetica). Keep the font size small and elegant; do NOT make the text massive or overly vibrant. The text MUST be placed carefully in empty negative space (e.g., in a clean corner or side) and MUST NOT overlap the character or key visual elements. Use a soft, sophisticated color palette that meets high-end company standards. It must look like a premium, restrained corporate slide.
Ensure these specific styling and positioning rules are explicitly mentioned in every slide description.
"""

                video_prompt = f"""### VIDEO GENERATION
Create a video narrative directly derived from the storyline.

Use the specified brand character '{selected_char}' consistently throughout the video.
Create a 15-second cinematic institutional-finance video script featuring the character. Ensure the scenes progress the storyline logically.

Follow this exact formatting style:
--- EXAMPLE CHARACTER VIDEO FORMAT ---
Create a 15-second cinematic institutional-finance video based on the storyline...

CHARACTER:
{selected_char} [Include any specific actions or personality here]. Maintain the same character appearance throughout all scenes.

SCENE 1 — MARKET ENVIRONMENT
[Detailed description of '{selected_char}' observing the environment]

SCENE 2 — ANALYTICAL CHALLENGE
[Detailed description of '{selected_char}' encountering the specific problem/challenge]

SCENE 3 — INTELLIGENT ANALYSIS
[Detailed description of the solution interface and '{selected_char}' interacting with/observing it]

SCENE 4 — DECISION
[Detailed description of '{selected_char}' confidently taking action based on the insights]

VISUAL STYLE:
Premium institutional financial technology, cinematic professional lighting, sophisticated financial-data visualization, restrained and credible. Match the exact visual style (e.g., stylized 3D animation) of the provided reference character image. Do not make the character photorealistic if the reference image is stylized.

CHARACTER CONSISTENCY:
The same character '{selected_char}' must appear consistently throughout all scenes.
{logo_instruction}

AUDIO:
A calm, authoritative voiceover saying: "[Voiceover script tailored to the storyline]". Subtle ambient room tone; no dialogue, no sound effects.

BRANDING RULE:
DO NOT generate any text, logos, or brand names (like "StradIT" or the tagline) in the video. The video must be completely free of text overlays, as branding will be added programmatically post-generation.
----------------------"""

                validation_prompt = f"""### FINAL CHARACTER VALIDATION
* The specific brand character '{selected_char}' is present
* Character performs a meaningful action
* Character fits the business environment
* Character is consistent across video scenes{"" if not include_logo else chr(10) + "* The StradIT logo mark is naturally integrated into the composition"}"""

            elif include_logo:
                # Logo only - no human character selected. Feature the brand
                # mark itself rather than inventing a decorative person.
                char_rules_prompt = """### BRAND MARK GENERATION
The user has requested the StradIT logo be featured as a reference visual element, without a human character.
Do not invent or describe any human character. Integrate the StradIT logo naturally into the composition (e.g. on a screen, document header, badge, or subtle corner placement) as the visual anchor instead."""

                image_prompt = """### IMAGE GENERATION
Create a highly detailed prompt for a multi-slide Carousel (e.g., 3-5 slides) that directly represents the specific storyline. Each slide must be text-oriented, deeply informative, and visually connected to the others.
Mimic high-end, colorful, professional layouts (clean typography, data visualization, cohesive vibrant color palette).

Do not include human characters in the image. Integrate the StradIT logo naturally into the composition (e.g. on a screen, document header, badge, or corner element) as the visual anchor for the brand.
Use appropriate: Business environments, Financial data, Technology, Market visualizations, Documents, Product interfaces, Objects, Abstract visual metaphors.

For Carousels, follow this exact formatting style:

--- EXAMPLE CAROUSEL FORMAT ---
Overall Aesthetic/Style: Premium institutional financial technology...
Slide 1 (Title/Hook): Deep navy background... [reference the StradIT logo placement here]
Slide 2 (Context/Problem): Split-screen layout...
Slide 3 (Solution/Capabilities): Full-bleed dark-mode UI dashboard...
Slide 4 (Outcome/CTA): Deep navy background...
----------------------

BRANDING RULE:
- Aspect Ratio: Every image/carousel slide must be 1080x1080 (1:1 aspect ratio).
- Text Overlays & Typography: Include the Slide Title and a short summary sentence directly in the image. The typography MUST be highly professional, sleek, and premium (mimicking modern corporate fonts like Inter, Roboto, or Helvetica). Use proper visual hierarchy: bold, clean titles with smaller, elegant subtitle text. Ensure text is perfectly aligned, uses appropriate negative space, and blends harmoniously with the color palette. It must look like a high-end agency-designed graphic.
Ensure these specific styling and positioning rules are explicitly mentioned in every slide description.
"""

                video_prompt = """### VIDEO GENERATION
Create a video narrative directly derived from the storyline.

Create a 10-second premium corporate technology video in a single continuous narrative flow.
Do not introduce human characters. Integrate the StradIT logo naturally into the visual composition. Build the narrative using environments, objects, data, technology, or visual metaphors.

Follow this exact formatting style:
--- EXAMPLE NON-CHARACTER VIDEO FORMAT ---
[Overall style description] A premium corporate technology video...

0:00-0:04 [Extremely detailed shot description of a visual metaphor, referencing the StradIT logo placement...]
0:04-0:08 [Extremely detailed shot description progressing the metaphor...]
0:08-0:10 [Extremely detailed shot description concluding the metaphor...]

Audio: A calm, authoritative voiceover saying: "[Voiceover script]".

BRANDING RULE:
DO NOT generate any additional text or brand names beyond the referenced logo mark itself. The video must be otherwise free of text overlays, as further branding will be added programmatically post-generation.
----------------------"""

                validation_prompt = """### FINAL CHARACTER VALIDATION
* Are there absolutely no human characters?
* Is the StradIT logo naturally integrated into the composition?"""

            else:
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
----------------------

BRANDING RULE:
- Aspect Ratio: Every image/carousel slide must be 1080x1080 (1:1 aspect ratio).
- Text Overlays & Typography: Include the Slide Title and a short summary sentence directly in the image. The typography MUST be highly professional, soft, minimalist, and pleasant to the eye (mimicking refined corporate fonts like Inter or Helvetica). Keep the font size small and elegant; do NOT make the text massive or overly vibrant. The text MUST be placed carefully in empty negative space (e.g., in a clean corner or side) and MUST NOT overlap the character or key visual elements. Use a soft, sophisticated color palette that meets high-end company standards. It must look like a premium, restrained corporate slide.
Ensure these specific styling and positioning rules are explicitly mentioned in every slide description.
"""

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
DO NOT generate any text, logos, or brand names (like "StradIT" or the tagline) in the video. The video must be completely free of text overlays, as branding will be added programmatically post-generation.
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
----------------------

BRANDING RULE:
- Aspect Ratio: Every image/carousel slide must be 1080x1080 (1:1 aspect ratio).
- Text Overlays & Typography: Include the Slide Title and a short summary sentence directly in the image. The typography MUST be highly professional, sleek, and premium (mimicking modern corporate fonts like Inter, Roboto, or Helvetica). Use proper visual hierarchy: bold, clean titles with smaller, elegant subtitle text. Ensure text is perfectly aligned, uses appropriate negative space, and blends harmoniously with the color palette (e.g., crisp white or gold accents on dark navy backgrounds). Avoid basic, clumsy, or overly thick fonts. It must look like a high-end agency-designed graphic.
Ensure these specific styling and positioning rules are explicitly mentioned in every slide description.
"""

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
DO NOT generate any text, logos, or brand names (like "StradIT" or the tagline) in the video. The video must be completely free of text overlays, as branding will be added programmatically post-generation.
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
* Desired business outcome
* Competitor context, if relevant

Do not generate the image or video prompt yet.

### STEP 2 — PROJECT / SERVICE MATCHING (STRICT)

<OUR_PROJECT_CONTEXT> lists the ONLY real StradIT offerings that exist - both specific software
products (each introduced by a "=== PROJECT: <NAME> ===" heading) and consulting/engineering
service lines (each introduced by a "=== SERVICE: <NAME> ===" heading, e.g. Applied AI, Data
Analytics, Cyber Security, Cloud & Infrastructure, Automated AI Testing, Digital Assets &
Blockchain, Global Capability Center). A "=== COMPANY OVERVIEW ===" section lists the industries
and regions StradIT actually operates in - use it to judge plausibility, not as a selectable item
itself. Together, the named projects and named services are the complete set of valid selections.

Compare the storyline's actual topic/business problem against what each project or service's
documentation says it genuinely does. Select the ONE project or service whose real, documented
capabilities most directly address the storyline's topic - a broad consulting service (e.g. "Cyber
Security" or "Data Analytics") is just as valid a selection as a named product when it's the
better fit.

If NONE of them genuinely address the storyline's topic - do not force a connection just because
the topic is in the same broad industry (e.g. "finance"). A tax-advisory or wealth-management
storyline is NOT automatically about AML risk assessment, fund screening, alternative-investment
due diligence, or any listed service just because they're all financial-services topics - only
select something if its documented capabilities would let StradIT credibly speak to this specific
problem.

Set:
* selected_project = the exact project or service name from <OUR_PROJECT_CONTEXT>, OR the literal
  string "No Strong Match" if nothing genuinely fits.
* connection_strength = "Strong", "Moderate", or "No Strong Match" (matching selected_project when
  there's no fit).

If selected_project is "No Strong Match", the caption instructions (Step 3) MUST say so explicitly
and MUST NOT invent a connection to any project or service - downstream generation blocks entirely
in that case.

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
If connection_strength is "No Strong Match", the caption prompt must be exactly:
"No Strong Match was identified between this competitor topic and the available projects." -
nothing else, and do not proceed to describe a topic or product/service angle.
Otherwise, explicitly name selected_project by its real project or service name and state its
actual documented capability being highlighted (pulled from <OUR_PROJECT_CONTEXT>, not invented) -
along with the specific hook, core strategic topic, and tone.
Do NOT write the actual caption here. Only provide the instructions/context for the writer.
If characters are selected, characters may be referenced when naturally relevant.
Do not invent fictional customer experiences, quotes, names, testimonials, personal claims, or unsupported facts.

### STEP 4 — GUARDRAILS

After generating the prompts, validate all outputs against the following guardrails:

Storyline Alignment
* Does the image represent the actual storyline?
* Does the video represent the actual storyline?
* Does the caption communicate the actual storyline?
* Is the selected project or service used accurately?

Character Consistency
{validation_prompt}

Capability Accuracy
* Do not invent product or service capabilities.
* Do not show functionality that the selected project or service does not provide.
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

Perform Step 1 and Step 4 internally - do not include that reasoning in the final JSON.
Step 2's outcome (selected_project, connection_strength) MUST be reported explicitly in the JSON below,
since it's what downstream generation uses to block output when there's no real fit.

Respond with exactly this JSON structure and nothing else:

{{
  "selected_project": "The exact project or service name from OUR_PROJECT_CONTEXT, or \\"No Strong Match\\"",
  "connection_strength": "Strong, Moderate, or No Strong Match",
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

    def _generate_festive_storyline(self, posts_text, character_config, return_usage=False):
        """Warm, on-brand seasonal/festive greeting content - deliberately
        does not promote any StradIT product/service or reference
        competitors, and is never gated by "No Strong Match" the way
        counter-strategy content is, since a holiday greeting isn't meant to
        sell a specific capability."""
        mode = character_config.get("mode", "without_character") if character_config else "without_character"
        character_line = (
            "You may include a warm, appropriately dressed character celebrating the occasion if it suits the visual "
            "- do not describe them performing any product-related or work task."
            if mode == "with_character"
            else "Do not include human characters; use festive visual motifs, colors, and StradIT branding instead."
        )

        system = f"""You are a Content Generation Agent creating a warm, professional seasonal/festive greeting post.

This is a HOLIDAY OR FESTIVAL GREETING, not a competitive counter-strategy. Do NOT reference any
StradIT product, service, or competitor, and do not force a product pitch into a greeting - the
whole point is that it's warm and human, not an ad.

{character_line}

BRANDING RULE: If there is a visual of StradIT, the text "Strad" must be strictly ORANGE and "IT"
must be strictly WHITE. The tagline "Automate.Elevate.Accelerate." may appear subtly in WHITE, but
only if it doesn't make the greeting feel like an advertisement.

Respond with exactly this JSON structure and nothing else:
{{
  "selected_project": "N/A (Festive Greeting)",
  "connection_strength": "N/A",
  "observed_facts": ["the festival/holiday name and date, and its cultural/business significance"],
  "caption": "Instructions for the social media writer: warm, culturally appropriate greeting tone, mention the occasion by name, genuine and not promotional, no CTA or sales language.",
  "image_prompt": "One rich, festive scene description - colors, motifs, setting appropriate to the occasion - with a small STRAD IT wordmark. No product UI, dashboards, or office/work environments.",
  "video_prompt": "A short 8-10 second warm festive video script - no product pitch, no work environment."
}}
"""

        result, usage = self.llm.generate_json(system, posts_text, temperature=0.6, max_tokens=2000, return_usage=True)
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
