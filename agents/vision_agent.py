from services.hf_service import HuggingFaceService
from services.llm_service import LLMService


class VisionAgent:
    """Agent that analyzes images and generates rich descriptions"""

    SYSTEM_PROMPT = """You are a Vision Content Agent. Given an image caption and visual features, create:
    1. A rich, engaging description (2-3 sentences)
    2. Mood/atmosphere analysis
    3. Color palette description
    4. Composition notes
    5. Storytelling angles (3 different narrative approaches)
    6. Best platform fit assessment

    Return as JSON with keys: rich_description, mood, colors, composition, story_angles, platform_fit"""

    def __init__(self):
        self.hf = HuggingFaceService()
        self.llm = LLMService()

    def analyze_image(self, image_path):
        """Full image analysis pipeline"""
        if self.hf.vision_provider == "heyroute":
            return self._analyze_image_heyroute(image_path)

        # Step 1: Get HF caption
        caption = self.hf.get_image_caption(image_path)

        # Step 2: Get visual features
        features = self.hf.get_image_features(image_path)

        # Step 3: Enrich with LLM
        user_prompt = f"""Image Caption: {caption}
        Visual Features: {features}

        Create a comprehensive analysis for social media content creation."""

        analysis = self.llm.generate_json(self.SYSTEM_PROMPT, user_prompt)
        analysis["raw_caption"] = caption
        return analysis

    def _analyze_image_heyroute(self, image_path):
        """The model sees the image itself, so one call replaces the caption +
        features + enrichment calls. A failure returns an empty analysis - the
        upload/post still works, just without image insights."""
        instruction = (
            self.SYSTEM_PROMPT
            + "\n\nAlso include raw_caption: one sentence describing exactly what is in the image. "
            "Return ONLY the JSON object."
        )
        try:
            text = self.hf.describe_image_heyroute(image_path, instruction, json_output=True)
            analysis = self.llm._robust_parse_json(text)  # pylint: disable=protected-access
            if not isinstance(analysis, dict):
                raise ValueError("vision model did not return a JSON object")
            analysis.setdefault("raw_caption", analysis.get("rich_description", ""))
            return analysis
        except Exception as err:
            print(f"[VisionAgent] HeyRoute image analysis failed: {err}")
            return {
                "rich_description": "",
                "raw_caption": "",
                "error": "Image analysis is unavailable right now - the post is written without it.",
            }

    def get_alt_text(self, image_path):
        """Generate accessibility-friendly alt text"""
        caption = self.hf.get_image_caption(image_path)
        system = "Create concise, descriptive alt text for this image (under 125 characters for screen readers):"
        return self.llm.generate(system, f"Caption: {caption}")
