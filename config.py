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
    # What HeyRoute charges per generated/edited image (its API doesn't report
    # cost). Added to the post's cost, so it counts against the user's credits.
    # Set it to your image model's price from the HeyRoute dashboard.
    HEYROUTE_IMAGE_COST_USD = float(os.getenv("HEYROUTE_IMAGE_COST_USD", "0.55"))
    HEYROUTE_LLM_MODEL = os.getenv("HEYROUTE_LLM_MODEL", "gpt-5.6-terra")
    # Seconds one LLM request may take before it is abandoned (1 retry).
    HEYROUTE_LLM_TIMEOUT = int(os.getenv("HEYROUTE_LLM_TIMEOUT", "120"))
    # Model for image analysis (must accept image input). Uses HEYROUTE_API_KEY.
    HEYROUTE_VISION_MODEL = os.getenv("HEYROUTE_VISION_MODEL", "") or os.getenv("HEYROUTE_LLM_MODEL", "gpt-5.6-terra")
    # none / minimal / low / medium / high - "low" keeps agent calls fast; the
    # model still reasons before answering.
    HEYROUTE_REASONING_EFFORT = os.getenv("HEYROUTE_REASONING_EFFORT", "medium")
    HEYROUTE_IMAGE_MODEL = os.getenv("HEYROUTE_IMAGE_MODEL", "gemini-3.1-flash-image")
    #gemini-3.1-flash-lite-preview , gemini-3.1-flash-image
    # Video is generated ONLY through HeyRoute (HEYROUTE_VIDEO_API_KEY) - no
    # other video provider is tried. minimax-h3-original-768p: 4-15 s, always
    # 1376x768 landscape, one reference image, well over ten minutes per clip
    # (per-model rules: media_service._heyroute_video_body). Optionally a
    # second HeyRoute model to try if the first fails; empty = none.
    HEYROUTE_VIDEO_MODEL = os.getenv("HEYROUTE_VIDEO_MODEL", "minimax-h3-original-768p")
    HEYROUTE_VIDEO_FALLBACK_MODEL = os.getenv("HEYROUTE_VIDEO_FALLBACK_MODEL", "")
    HEYROUTE_VIDEO_RESOLUTION = os.getenv("HEYROUTE_VIDEO_RESOLUTION", "720p")
    HEYROUTE_VIDEO_SECONDS = int(os.getenv("HEYROUTE_VIDEO_SECONDS", "8"))
    HEYROUTE_VIDEO_TIMEOUT = int(os.getenv("HEYROUTE_VIDEO_TIMEOUT", "900"))
    # What HeyRoute charges per second of generated video (its API doesn't
    # report cost). Added to the post's cost, so it counts against the user's
    # credits. 0.70 is minimax-h3-original-768p's price - change it with the
    # model (HeyRoute help -> Generate videos -> Billing).
    HEYROUTE_VIDEO_COST_PER_SECOND_USD = float(os.getenv("HEYROUTE_VIDEO_COST_PER_SECOND_USD", "0.70"))

    # SMTP - approval-notification emails
    SMTP_HOST = os.getenv("SMTP_HOST")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL") or os.getenv("SMTP_USERNAME")
    APPROVAL_NOTIFY_EMAIL = os.getenv("APPROVAL_NOTIFY_EMAIL")
    # Inbox that receives "Contact Sales" leads from the Enterprise onboarding step
    SALES_EMAIL = os.getenv("SALES_EMAIL", "")

    # Meta Graph API (Facebook + Instagram publishing). Meta retires each
    # version about two years after release - keep this on a current one.
    META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v23.0")
    # LinkedIn's versioned API (services/linkedin_service.py), YYYYMM. LinkedIn
    # retires a version about a year after its release - bump it then.
    LINKEDIN_API_VERSION = os.getenv("LINKEDIN_API_VERSION", "202609")
    # The "Schedule" button in Studio Chat and the Image Library. Off: the
    # button is shown as "Soon" and can't be clicked. The scheduling itself
    # (calendar, Settings > Scheduled posts, the scheduler) is unaffected.
    STUDIO_SCHEDULING = os.getenv("STUDIO_SCHEDULING", "true").lower() == "true"  # "Schedule post" under each post
    # One-click "Connect with Facebook / LinkedIn" in Settings
    # (services/social_oauth_service.py, docs/social-connect-setup.md). Each
    # button appears once its app's ID and secret are set; until then accounts
    # are connected by entering their details by hand.
    META_APP_ID = os.getenv("META_APP_ID", "")
    META_APP_SECRET = os.getenv("META_APP_SECRET", "")
    # Facebook Login for Business: the configuration that holds the permissions (replaces the scope list)
    META_LOGIN_CONFIG_ID = os.getenv("META_LOGIN_CONFIG_ID", "")
    META_OAUTH_SCOPES = os.getenv(
        "META_OAUTH_SCOPES", "pages_show_list,pages_manage_posts,pages_read_engagement,instagram_basic,instagram_content_publish")
    LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "")
    LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "")
    # Add "w_organization_social r_organization_admin" once LinkedIn has approved the app for company pages
    LINKEDIN_OAUTH_SCOPES = os.getenv("LINKEDIN_OAUTH_SCOPES", "openid profile w_member_social")
    # "Instagram API with Instagram Login": Instagram professional accounts
    # without a Facebook Page. The Instagram app ID and secret are separate from
    # META_APP_ID (Meta app -> Instagram -> API setup with Instagram login).
    INSTAGRAM_APP_ID = os.getenv("INSTAGRAM_APP_ID", "")
    INSTAGRAM_APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET", "")
    INSTAGRAM_OAUTH_SCOPES = os.getenv("INSTAGRAM_OAUTH_SCOPES", "instagram_business_basic,instagram_business_content_publish")
    # New comments pushed by Meta as they happen (/api/webhooks/meta,
    # services/comment_service.py handle_webhook), instead of waiting for the
    # next check. Any random string; type the same one as the "Verify token"
    # in the Meta app -> Webhooks. Empty: the webhook is off and comments are
    # only fetched on the schedule.
    META_WEBHOOK_VERIFY_TOKEN = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "")
    # Public HTTPS address Instagram downloads post images from (Meta fetches
    # them itself). Defaults to APP_BASE_URL; set it if images are served elsewhere.
    PUBLIC_MEDIA_BASE_URL = os.getenv("PUBLIC_MEDIA_BASE_URL", "")
    # Optional override for absolute links in emails (e.g. approval request
    # links) when the app isn't reachable at the request's own host (behind a
    # reverse proxy, etc). Falls back to the incoming request's own host.
    APP_BASE_URL = os.getenv("APP_BASE_URL")

    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    # Free credit every new account starts with, in USD (credits are kept in
    # USD because generation is billed in USD). Existing accounts keep theirs.
    DEFAULT_CREDIT_USD = float(os.getenv("DEFAULT_CREDIT_USD", "2"))
    # Admins must turn on two-step sign-in (Settings -> Security) before the
    # Admin Control Center and admin APIs work. Set false only for local testing.
    ADMIN_2FA_REQUIRED = os.getenv("ADMIN_2FA_REQUIRED", "true").lower() == "true"
    # Bearer tokens for API clients (auth/jwt_tokens.py). Signed with
    # JWT_SECRET_KEY, else SECRET_KEY. Access tokens last an hour by default;
    # refresh tokens (which get a new pair) 30 days.
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
    JWT_ACCESS_TTL_SECONDS = int(os.getenv("JWT_ACCESS_TTL_SECONDS", str(60 * 60)))
    JWT_REFRESH_TTL_SECONDS = int(os.getenv("JWT_REFRESH_TTL_SECONDS", str(30 * 24 * 60 * 60)))
    # Legal pages (legal/*.md, services/legal_pages.py): the company details
    # filled into them. LEGAL_GRIEVANCE_OFFICER is required for Indian users
    # (IT Rules 2021). Change LEGAL_UPDATED and LEGAL_VERSION together whenever
    # a policy changes; each sign-up records the version it agreed to.
    LEGAL_COMPANY_NAME = os.getenv("LEGAL_COMPANY_NAME") or os.getenv("INVOICE_SELLER_NAME") or "StradIT"
    LEGAL_ADDRESS = os.getenv("LEGAL_ADDRESS") or os.getenv("INVOICE_SELLER_ADDRESS", "")
    LEGAL_EMAIL = os.getenv("LEGAL_EMAIL") or "sales@stradit.com"
    LEGAL_GRIEVANCE_OFFICER = os.getenv("LEGAL_GRIEVANCE_OFFICER") or "the Grievance Officer"
    LEGAL_GRIEVANCE_EMAIL = os.getenv("LEGAL_GRIEVANCE_EMAIL", "")
    LEGAL_JURISDICTION = os.getenv("LEGAL_JURISDICTION") or "Gujarat, India"
    LEGAL_UPDATED = os.getenv("LEGAL_UPDATED") or "26 October 2026"
    LEGAL_VERSION = os.getenv("LEGAL_VERSION") or "2026-10-26"
    # GST added to INR prices (services/billing_service.py)
    GST_RATE = float(os.getenv("GST_RATE", "0.18"))
    # Stripe: the only payment gateway, for every country - INR for Indian
    # brands, USD for everyone else (services/billing_service.py). Buying is
    # hidden until the secret key is set. Test keys start sk_test_. Webhook endpoint in the Stripe dashboard:
    # <APP_BASE_URL>/api/billing/stripe/webhook (event checkout.session.completed,
    # checkout.session.async_payment_succeeded); its signing secret starts whsec_.
    # GST invoices (services/invoice_service.py): the seller's details printed on
    # every invoice. Invoice numbers are PREFIX/YY-YY/NNNNN, one series per
    # financial year (April-March). SAC 997331 = licensing the right to use
    # software; confirm the code and the LUT wording with your accountant.
    INVOICE_SELLER_NAME = os.getenv("INVOICE_SELLER_NAME", "StradIT")
    INVOICE_SELLER_ADDRESS = os.getenv("INVOICE_SELLER_ADDRESS", "")
    INVOICE_SELLER_GSTIN = os.getenv("INVOICE_SELLER_GSTIN", "")
    INVOICE_SELLER_STATE = os.getenv("INVOICE_SELLER_STATE", "Gujarat")
    INVOICE_PREFIX = os.getenv("INVOICE_PREFIX", "AVIR")
    INVOICE_SAC = os.getenv("INVOICE_SAC", "997331")
    INVOICE_EXPORT_NOTE = os.getenv(
        "INVOICE_EXPORT_NOTE", "Export of services: supply meant for export under LUT without payment of IGST.")
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    # Plan for accounts no admin has assigned one to (Enterprise accounts get
    # "enterprise"). See db.DEFAULT_PLANS and PRICING.md.
    DEFAULT_PLAN = os.getenv("DEFAULT_PLAN", "free")
    # Users whose brand is in India see credits in rupees at this rate
    # (display only - the stored amounts stay in USD). Review it regularly.
    USD_INR_RATE = float(os.getenv("USD_INR_RATE", "95"))
    # "Ideas for you" (services/trend_service.py): this week's topics per
    # industry come from Gemini with Google Search (needs a GOOGLE_API_KEY
    # whose plan includes search grounding); when that call fails, from
    # Google News headlines. Looked up once per industry per TRENDS_MAX_AGE_HOURS.
    TRENDS_MODEL = os.getenv("TRENDS_MODEL", "gemini-flash-latest")
    # Weekly ideas email (services/idea_digest_service.py): three ideas from
    # the user's feed, each opening Studio Chat with its brief filled in.
    # Off until WEEKLY_IDEAS_EMAIL=true - nothing is mailed by the scheduler
    # before that. Needs SMTP and APP_BASE_URL (the links in the email).
    # Sent on IDEAS_EMAIL_WEEKDAY (0 = Monday) from IDEAS_EMAIL_HOUR_UTC.
    WEEKLY_IDEAS_EMAIL = os.getenv("WEEKLY_IDEAS_EMAIL", "false").lower() == "true"
    IDEAS_EMAIL_WEEKDAY = int(os.getenv("IDEAS_EMAIL_WEEKDAY", "0"))
    IDEAS_EMAIL_HOUR_UTC = int(os.getenv("IDEAS_EMAIL_HOUR_UTC", "4"))
    # Timely nudges (services/nudge_service.py): an occasion NUDGE_OCCASION_DAYS
    # away, this week's top story in the user's industry, a reminder after
    # NUDGE_INACTIVE_DAYS without a post. They always appear under the header
    # bell; they are also emailed only when NUDGE_EMAILS=true (needs SMTP and
    # APP_BASE_URL). A user gets at most IDEA_EMAILS_PER_WEEK idea emails a
    # week, the weekly one included.
    NUDGE_EMAILS = os.getenv("NUDGE_EMAILS", "false").lower() == "true"
    NUDGE_OCCASION_DAYS = int(os.getenv("NUDGE_OCCASION_DAYS", "5"))
    NUDGE_INACTIVE_DAYS = int(os.getenv("NUDGE_INACTIVE_DAYS", "10"))
    IDEA_EMAILS_PER_WEEK = int(os.getenv("IDEA_EMAILS_PER_WEEK", "2"))
    # Monthly recap email (services/recap_service.py): what the user created
    # last month and what to try next. Off until MONTHLY_RECAP_EMAIL=true.
    # Sent from day RECAP_EMAIL_DAY of the month, at IDEAS_EMAIL_HOUR_UTC,
    # only to users who created at least one post that month.
    MONTHLY_RECAP_EMAIL = os.getenv("MONTHLY_RECAP_EMAIL", "false").lower() == "true"
    RECAP_EMAIL_DAY = int(os.getenv("RECAP_EMAIL_DAY", "1"))
    # Weekly results email (services/results_email_service.py): last week's
    # reach and engagement, the best post against the user's usual, what works
    # and comments waiting. Off until WEEKLY_RESULTS_EMAIL=true. Sent on
    # RESULTS_EMAIL_WEEKDAY (0 = Monday) from IDEAS_EMAIL_HOUR_UTC, only to
    # users with something to report (a published post or a waiting comment).
    WEEKLY_RESULTS_EMAIL = os.getenv("WEEKLY_RESULTS_EMAIL", "false").lower() == "true"
    RESULTS_EMAIL_WEEKDAY = int(os.getenv("RESULTS_EMAIL_WEEKDAY", "0"))
    # Reminder to a reviewer whose approval request is still pending after a
    # day, and once more after three (services/approval_reminder_service.py).
    # Off until APPROVAL_REMINDER_EMAILS=true or switched on in Admin > Emails.
    APPROVAL_REMINDER_EMAILS = os.getenv("APPROVAL_REMINDER_EMAILS", "false").lower() == "true"
    # Invitation outreach (Admin -> Invitations). The postal address shown at
    # the bottom of invitation emails (US CAN-SPAM requires one in marketing
    # email) - also editable on the Invitations page.
    COMPANY_POSTAL_ADDRESS = os.getenv("COMPANY_POSTAL_ADDRESS", "")
    # Lead previews: images are made when a lead opens their preview page; at
    # most this many in 24 hours (each preview has up to 3).
    LEAD_PREVIEW_IMAGES_PER_DAY = int(os.getenv("LEAD_PREVIEW_IMAGES_PER_DAY", "60"))
    TRENDS_MAX_AGE_HOURS = int(os.getenv("TRENDS_MAX_AGE_HOURS", "24"))
    GEMINI_VIDEO_MODEL = os.getenv("GEMINI_VIDEO_MODEL", "veo-3.1-generate-preview")
    # Veo 3.1 accepts only 4, 6 or 8 seconds (8 when a reference image is
    # given); any other value is rejected, e.g. the 5 previously hard-coded.
    GEMINI_VIDEO_DURATION = int(os.getenv("GEMINI_VIDEO_DURATION", "8"))
    GENERATE_NATIVE_AUDIO = os.getenv("GENERATE_NATIVE_AUDIO", "true").lower() == "true"
    # The video model makes the sound itself (minimax-h3 and grok-imagine
    # return video + audio in one file; the narration is part of their
    # prompt). true = when a clip still comes back silent, add a separate
    # text-to-speech voiceover; false = leave it silent.
    VIDEO_TTS_FALLBACK = os.getenv("VIDEO_TTS_FALLBACK", "false").lower() == "true"
    # That fallback voiceover: Gemini speech (GOOGLE_API_KEY), a
    # natural human-sounding voice. Without the key, or if the call fails, the
    # old gTTS voice is used. Voices: Sulafat (warm), Kore (firm), Puck
    # (upbeat), Charon (informative), Achird (friendly), Aoede (breezy), ...
    VOICEOVER_TTS_MODEL = os.getenv("VOICEOVER_TTS_MODEL", "gemini-2.5-flash-preview-tts")
    VOICEOVER_VOICE = os.getenv("VOICEOVER_VOICE", "Sulafat")

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
    # The shared embedding service (scripts/embedding_server.py): one copy of
    # the model for every web worker. Empty: each process loads its own.
    EMBEDDING_URL = os.getenv("EMBEDDING_URL", "")
    EMBEDDING_TOKEN = os.getenv("EMBEDDING_TOKEN", "")


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    # The session cookie only travels over HTTPS in production
    SESSION_COOKIE_SECURE = True


config_map = {"development": DevelopmentConfig, "production": ProductionConfig}
