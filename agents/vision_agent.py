import hashlib
import json
import logging
import os
import tempfile

from services.hf_service import HuggingFaceService
from services.llm_service import LLMService

logger = logging.getLogger(__name__)


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

    # Analyses of the same image are reused: a photo is analysed when it's
    # uploaded and was analysed again when the post was generated - the same
    # paid multimodal call twice. Keyed by the file's content (plus provider
    # and prompt), shared by all workers on the host; failures aren't cached.
    _CACHE_DIR = os.path.join(tempfile.gettempdir(), "avir-vision-cache")

    def _cache_path(self, image_path):
        try:
            with open(image_path, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return None
        setup = hashlib.sha256(f"{self.hf.vision_provider}|{self.SYSTEM_PROMPT}".encode()).hexdigest()[:12]
        return os.path.join(self._CACHE_DIR, f"{digest}_{setup}.json")

    # An analysis already running for the same image (e.g. started in the
    # background at upload) is waited for instead of paying for a second one.
    # The marker file is shared by all workers; a stale one (crashed worker)
    # stops counting after _PENDING_STALE seconds.
    _PENDING_STALE = 150
    # Entries are removed after this long (checked at most once an hour per worker)
    _CACHE_MAX_AGE = 7 * 24 * 3600
    _last_prune = 0.0

    @classmethod
    def _prune_cache(cls):
        """Removes analyses older than _CACHE_MAX_AGE and leftover temp/marker
        files from crashed workers. Best-effort, at most once an hour."""
        import time as _time

        now = _time.time()
        if now - cls._last_prune < 3600:
            return
        cls._last_prune = now
        try:
            for name in os.listdir(cls._CACHE_DIR):
                path = os.path.join(cls._CACHE_DIR, name)
                limit = cls._CACHE_MAX_AGE if name.endswith(".json") else 3600
                try:
                    if now - os.path.getmtime(path) > limit:
                        os.remove(path)
                except OSError:
                    pass
        except OSError:
            pass

    @staticmethod
    def _read_cache(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def cached_analysis(self, image_path):
        """The finished analysis of this image if there is one - never calls the AI."""
        cache = self._cache_path(image_path) if image_path else None
        return self._read_cache(cache) if cache and os.path.exists(cache) else None

    def analyze_image(self, image_path, wait_seconds: float = 120):
        """Full image analysis pipeline (cached per image - see _cache_path).
        If another worker is already analysing the same image, waits up to
        wait_seconds for its result before analysing it itself."""
        import time as _time

        cache = self._cache_path(image_path) if image_path else None
        if not cache:
            return self._analyze_uncached(image_path)
        if os.path.exists(cache):
            cached = self._read_cache(cache)
            if cached is not None:
                return cached
        pending = f"{cache}.pending"
        deadline = _time.time() + wait_seconds
        while self._fresh(pending) and _time.time() < deadline:
            _time.sleep(0.5)
            if os.path.exists(cache):
                cached = self._read_cache(cache)
                if cached is not None:
                    return cached
        try:
            os.makedirs(self._CACHE_DIR, exist_ok=True)
            with open(pending, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        except OSError:
            pending = None
        try:
            analysis = self._analyze_uncached(image_path)
            if isinstance(analysis, dict) and not analysis.get("error"):
                try:
                    tmp = f"{cache}.{os.getpid()}.tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(analysis, f)
                    os.replace(tmp, cache)  # atomic: another worker never reads half a file
                except OSError as err:
                    logger.warning(f"Could not cache image analysis: {err}")
                self._prune_cache()
            return analysis
        finally:
            if pending:
                try:
                    os.remove(pending)
                except OSError:
                    pass

    def _fresh(self, path):
        try:
            import time as _time

            return _time.time() - os.path.getmtime(path) < self._PENDING_STALE
        except OSError:
            return False

    def analyze_in_background(self, image_path):
        """Starts the analysis without waiting for it (upload returns at once)."""
        import threading

        threading.Thread(target=self._background, args=(image_path,), daemon=True, name="vision-bg").start()

    def _background(self, image_path):
        try:
            self.analyze_image(image_path, wait_seconds=0)
        except Exception as err:  # noqa: BLE001 - a background analysis must never crash the worker
            logger.warning(f"Background image analysis failed: {err}")

    def _analyze_uncached(self, image_path):
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
            logger.warning(f"HeyRoute image analysis failed: {err}")
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
