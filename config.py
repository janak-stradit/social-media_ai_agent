import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    UPLOAD_FOLDER = "static/uploads"
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 7  # 7 days
    # API Keys
    YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
    YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
    HF_API_TOKEN = os.getenv("HF_API_TOKEN")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    Z_AI_API_KEY = os.getenv("Z_AI_API_KEY")
    Z_AI_BASE_URL = os.getenv("Z_AI_BASE_URL", "https://api.z.ai/api/paas/v4/")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    GEMINI_VIDEO_MODEL = os.getenv("GEMINI_VIDEO_MODEL", "veo-3.1-generate-preview")
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
    VISION_PROVIDER = os.getenv("VISION_PROVIDER", "bedrock" if os.getenv("MEDIA_PROVIDER") == "bedrock" else "local")

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
