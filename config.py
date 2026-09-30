import os

from dotenv import load_dotenv

load_dotenv()


def _key_prefix(value: str) -> str:
    """S3 key prefix with one trailing slash: 'uploads' -> 'uploads/', '' -> '' (bucket root)."""
    value = (value or "").strip().strip("/")
    return f"{value}/" if value else ""


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    UPLOAD_FOLDER = "static/uploads"
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 7  # 7 days

    # Slider-puzzle check before signup (auth/captcha.py)
    CAPTCHA_ENABLED = os.getenv("CAPTCHA_ENABLED", "true").lower() in ("1", "true", "yes")
    CAPTCHA_TOLERANCE_PX = int(os.getenv("CAPTCHA_TOLERANCE_PX", "6"))
    # API Keys
    YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
    YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
    HF_API_TOKEN = os.getenv("HF_API_TOKEN")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    Z_AI_API_KEY = os.getenv("Z_AI_API_KEY")
    Z_AI_BASE_URL = os.getenv("Z_AI_BASE_URL", "https://api.z.ai/api/paas/v4/")
    KIE_API_KEY = os.getenv("KIE_API_KEY")

    # HeyRoute (https://heyroute.ai) - OpenAI-compatible gateway. A HeyRoute
    # key can only call the models of the group it was created in, so each
    # purpose has its own key: SMAI (reasoning, codex-plus group), SMAI-Image
    # (gemini group) and SMAI-Video (grok group). Each is used only when set;
    # otherwise the existing providers (kie.ai, OpenRouter, Gemini, Bedrock)
    # are used exactly as before.
    HEYROUTE_BASE_URL = os.getenv("HEYROUTE_BASE_URL", "https://heyroute.ai/v1").rstrip("/")
    HEYROUTE_API_KEY = os.getenv("HEYROUTE_API_KEY")  # reasoning / text (SMAI)
    HEYROUTE_IMAGE_API_KEY = os.getenv("HEYROUTE_IMAGE_API_KEY")  # SMAI-Image
    HEYROUTE_VIDEO_API_KEY = os.getenv("HEYROUTE_VIDEO_API_KEY")  # SMAI-Video
    HEYROUTE_LLM_MODEL = os.getenv("HEYROUTE_LLM_MODEL", "gpt-5.6-terra")
    # Seconds one LLM request may take before it is abandoned (1 retry).
    HEYROUTE_LLM_TIMEOUT = int(os.getenv("HEYROUTE_LLM_TIMEOUT", "120"))
    # Model for image analysis (must accept image input). Uses HEYROUTE_API_KEY.
    HEYROUTE_VISION_MODEL = os.getenv("HEYROUTE_VISION_MODEL", "") or os.getenv("HEYROUTE_LLM_MODEL", "gpt-5.6-terra")
    # none / minimal / low / medium / high - "low" keeps agent calls fast; the
    # model still reasons before answering.
    HEYROUTE_REASONING_EFFORT = os.getenv("HEYROUTE_REASONING_EFFORT", "medium")
    HEYROUTE_IMAGE_MODEL = os.getenv("HEYROUTE_IMAGE_MODEL", "gemini-3.1-flash-lite-preview")
    #gemini-3.1-flash-lite-preview , gemini-3.1-flash-image
    # Video is generated ONLY through HeyRoute (HEYROUTE_VIDEO_API_KEY) - no
    # other video provider is tried. grok-video: text-to-video, 6/10/15 s only,
    # no reference image. Optionally a second HeyRoute model to try if the
    # first fails (e.g. grok-imagine-video-1.5); empty = none.
    HEYROUTE_VIDEO_MODEL = os.getenv("HEYROUTE_VIDEO_MODEL", "gemini-3.1-flash-lite")
    HEYROUTE_VIDEO_FALLBACK_MODEL = os.getenv("HEYROUTE_VIDEO_FALLBACK_MODEL", "")
    HEYROUTE_VIDEO_RESOLUTION = os.getenv("HEYROUTE_VIDEO_RESOLUTION", "720p")
    HEYROUTE_VIDEO_SECONDS = int(os.getenv("HEYROUTE_VIDEO_SECONDS", "8"))
    HEYROUTE_VIDEO_TIMEOUT = int(os.getenv("HEYROUTE_VIDEO_TIMEOUT", "900"))

    # SMTP - approval-notification emails
    SMTP_HOST = os.getenv("SMTP_HOST")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL") or os.getenv("SMTP_USERNAME")
    APPROVAL_NOTIFY_EMAIL = os.getenv("APPROVAL_NOTIFY_EMAIL")
    # Inbox that receives "Contact Sales" leads from the Enterprise onboarding step
    SALES_EMAIL = os.getenv("SALES_EMAIL", "")
    # Optional override for absolute links in emails (e.g. approval request
    # links) when the app isn't reachable at the request's own host (behind a
    # reverse proxy, etc). Falls back to the incoming request's own host.
    APP_BASE_URL = os.getenv("APP_BASE_URL")

    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    GEMINI_VIDEO_MODEL = os.getenv("GEMINI_VIDEO_MODEL", "veo-3.1-generate-preview")
    # Veo 3.1 accepts only 4, 6 or 8 seconds (8 when a reference image is
    # given); any other value is rejected, e.g. the 5 previously hard-coded.
    GEMINI_VIDEO_DURATION = int(os.getenv("GEMINI_VIDEO_DURATION", "8"))
    GENERATE_NATIVE_AUDIO = os.getenv("GENERATE_NATIVE_AUDIO", "true").lower() == "true"

    # Mock LLM Mode toggle (true/false)
    USE_MOCK_LLM = os.getenv("USE_MOCK_LLM", "false").lower() in ("true", "1", "yes")

    # AWS Bedrock Config
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_PROFILE = os.getenv("AWS_PROFILE")
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
    AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET")
    AWS_BUCKET_OWNER = os.getenv("AWS_BUCKET_OWNER")  # Optional 12-digit account ID

    # S3 copy of generated content + user uploads (services/storage_service.py).
    # Files stay under static/uploads/ locally and keep their /static/uploads/
    # URLs; S3 is the durable copy and refills the disk when a file is missing.
    # Empty = disabled.
    S3_MEDIA_BUCKET = os.getenv("S3_MEDIA_BUCKET", "")
    S3_MEDIA_PREFIX = _key_prefix(os.getenv("S3_MEDIA_PREFIX", "uploads"))

    # Media provider selection: 'bedrock', 'zai', 'openrouter', 'openai'
    MEDIA_PROVIDER = os.getenv("MEDIA_PROVIDER")

    # AgentScope Config
    AGENTSCOPE_MODEL = os.getenv("AGENTSCOPE_MODEL", "gpt-4o")
    AGENTSCOPE_MODEL_Z_AI = os.getenv("AGENTSCOPE_MODEL_Z_AI", "glm-4.7-flash").lower()

    # Z.AI media generation (preferred when Z_AI_API_KEY is set)
    # Note: Z.AI has no free image/video API models. Defaults use the lowest-cost options:
    #   image — cogview-4-250304 ($0.01/image), quality standard
    #   video — cogvideox-3 ($0.20/video), quality speed
    Z_AI_IMAGE_MODEL = os.getenv("Z_AI_IMAGE_MODEL", "cogview-4-250304")
    Z_AI_IMAGE_QUALITY = os.getenv("Z_AI_IMAGE_QUALITY", "standard")
    Z_AI_VIDEO_MODEL = os.getenv("Z_AI_VIDEO_MODEL", "cogvideox-3")
    Z_AI_VIDEO_QUALITY = os.getenv("Z_AI_VIDEO_QUALITY", "speed")
    Z_AI_VIDEO_FPS = int(os.getenv("Z_AI_VIDEO_FPS", "30"))

    # AWS Bedrock media generation (low cost models)
    BEDROCK_IMAGE_MODEL = os.getenv("BEDROCK_IMAGE_MODEL", "amazon.nova-canvas-v1:0")
    BEDROCK_VIDEO_MODEL = os.getenv("BEDROCK_VIDEO_MODEL", "amazon.nova-reel-v1:0")
    BEDROCK_VISION_MODEL = os.getenv("BEDROCK_VISION_MODEL", "amazon.nova-lite-v1:0")
    BEDROCK_TEXT_MODEL = os.getenv("BEDROCK_TEXT_MODEL", "amazon.nova-lite-v1:0")
    # Image analysis (agents/vision_agent.py): "heyroute" (default when a HeyRoute
    # key is set - one multimodal call on HEYROUTE_VISION_MODEL), "bedrock", or
    # "local" (downloads a ~1 GB captioning model; not for the server).
    VISION_PROVIDER = os.getenv(
        "VISION_PROVIDER",
        "heyroute"
        if os.getenv("HEYROUTE_API_KEY")
        else ("bedrock" if os.getenv("MEDIA_PROVIDER") == "bedrock" else "local"),
    ).lower()

    # Image generation (OpenRouter model slug or OpenAI dall-e-3)
    IMAGE_MODEL = os.getenv("IMAGE_MODEL", "openai/gpt-image-1")

    # Video generation (OpenRouter text-to-video)
    VIDEO_MODEL = os.getenv("VIDEO_MODEL", "google/veo-3.1-lite")
    VIDEO_DURATION = int(os.getenv("VIDEO_DURATION", "4"))
    VIDEO_RESOLUTION = os.getenv("VIDEO_RESOLUTION", "720p")
    VIDEO_POLL_TIMEOUT = int(os.getenv("VIDEO_POLL_TIMEOUT", "300"))
    VIDEO_POLL_INTERVAL = int(os.getenv("VIDEO_POLL_INTERVAL", "5"))

    # ChromaDB
    CHROMA_PERSIST_DIR = "./chroma_db"

    # Redis (for Celery)
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_map = {"development": DevelopmentConfig, "production": ProductionConfig}
