import io
import json
import logging
import os
import re
import secrets
import threading
import time
import typing
import urllib.parse
import uuid
import zipfile
from typing import Optional

import requests
from flask import Blueprint, current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from agents.caption_agent import CaptionAgent
from agents.hashtag_agent import HashtagAgent
from agents.reviewer_agent import ReviewerAgent
from agents.story_agent import StoryAgent
from agents.strategy_agent import StrategyAgent
from agents.vision_agent import VisionAgent
from auth.rate_limit import limit_by_user
from auth.utils import admin_required_api, get_current_user_id, login_required_api
from config import Config
from services import storage_service
from services.compliance_service import active_rules_for_user, check_caption, check_captions
from services.llm_service import LLMService
from services.memory_service import MemoryService
from services.observability import estimate_tokens, log_event
from services.prompt_builder import (
    IMAGE_TEXT_RULE,
    build_image_edit_prompt,
    build_image_versions_block,
)
from services.scraper_service import ScraperService

logger = logging.getLogger(__name__)

try:
    from db import (
        add_run_cost,
        append_run_media,
        approve_credit_request,
        archive_run,
        cancel_scheduled_post,
        create_approval_request,
        create_brand_asset,
        create_credit_request,
        create_scheduled_post,
        decide_approval_request,
        delete_brand_asset,
        disconnect_social_account,
        get_all_credit_requests,
        get_all_users_credit_summary,
        get_approval_request,
        get_brand_asset,
        get_global_cost_history,
        get_history,
        get_latest_approval_request_for_pipeline,
        get_run_by_id,
        get_setting,
        get_user_by_email,
        get_user_by_id,
        get_user_credit_requests,
        get_user_scheduled_posts,
        get_user_social_accounts,
        get_user_usage_stats,
        hard_delete_user,
        list_approval_requests,
        list_brand_assets,
        reject_credit_request,
        save_approved_asset,
        save_run,
        save_setting,
        save_social_account,
        set_user_active,
        unarchive_run,
        update_brand_asset_filename,
        update_scheduled_post_status,
        update_user_credit_limit,
        format_money,
        user_currency,
        update_user_profile,
    )

    DB_AVAILABLE = True
except Exception as _db_err:
    DB_AVAILABLE = False
    logger.warning(f"DB not available: {_db_err}")

try:
    from services.media_service import MediaGenerationService

    media_service = MediaGenerationService()
    MEDIA_AVAILABLE = True
except Exception as _media_err:
    MEDIA_AVAILABLE = False
    logger.warning(f"Media service not available: {_media_err}")

try:
    from services.social_publisher_service import SocialPublisherService

    publisher_service: Optional[SocialPublisherService] = SocialPublisherService()
except Exception as _pub_err:
    publisher_service = None
    logger.warning(f"Social publisher service not available: {_pub_err}")

api_bp = Blueprint("api", __name__)
memory_service = MemoryService()


def _public_upload_url(filepath: str | None) -> str | None:
    if not filepath:
        return None
    normalized = filepath.replace("\\", "/")
    return normalized if normalized.startswith("/") else f"/{normalized}"


def _is_logo_asset(path: str) -> bool:
    """True if path is the dashboard's "logo" brand asset image."""
    try:
        asset = get_brand_asset("logo")
    except Exception:
        return False
    return bool(asset and asset.get("filename") and os.path.basename(str(path).split("?")[0]) == asset["filename"])


def _persist_generated_media(run_id: int | None, platform: str, media_type: str, result: dict, user_id: int) -> None:
    if not DB_AVAILABLE or not run_id or not result.get("success"):
        return

    media_payload = {
        "url": result.get("url"),
        "clean_url": result.get("clean_url"),
        "prompt": result.get("prompt"),
        "type": result.get("type", media_type),
        "duration": result.get("duration"),
        "resolution": result.get("resolution"),
        "size": result.get("size"),
        "model": result.get("model"),
        "source_image_url": result.get("source_image_url"),
        "asset_id": result.get("asset_id"),
    }
    try:
        append_run_media(run_id, platform, media_type, media_payload, user_id=user_id)
    except Exception as db_err:
        current_app.logger.warning(f"[DB] Could not save generated media: {db_err}")


def extract_prompt_for_type(full_text: str, content_type: str) -> str:
    """Extracts unified or individual prompts from the Counter Strategy text."""
    if not full_text:
        return full_text

    if content_type in ("text", "caption"):
        unified = re.search(
            r"Unified Caption(?: Prompt)?:\s*(.*?)(?=\n\n(?:Unified Image Prompt|Unified Video Script|Theme:)|$)",
            full_text,
            re.DOTALL,
        )
        if unified:
            return unified.group(1).strip()
        matches = re.findall(
            r"Caption(?: Prompt)?:\s*(.*?)(?=\n\n(?:Image Prompt|Video Script|Theme:|Reason for No Match:)|$)",
            full_text,
            re.DOTALL,
        )
        if matches:
            return "\n\n---\n\n".join([m.strip() for m in matches])

    elif content_type == "image":
        unified = re.search(
            r"Unified Image Prompt:\s*(.*?)(?=\n\n(?:Unified Video Script|Unified Caption|Theme:)|$)",
            full_text,
            re.DOTALL,
        )
        if unified:
            return unified.group(1).strip()
        matches = re.findall(
            r"Image Prompt:\s*(.*?)(?=\n\n(?:Video Script|Caption|Theme:|Reason for No Match:)|$)", full_text, re.DOTALL
        )
        if matches:
            return "\n\n---\n\n".join([m.strip() for m in matches])

    elif content_type == "video":
        unified = re.search(
            r"Unified Video Script:\s*(.*?)(?=\n\n(?:Unified Caption|Unified Image Prompt|Theme:)|$)",
            full_text,
            re.DOTALL,
        )
        if unified:
            return unified.group(1).strip()
        matches = re.findall(
            r"Video Script:\s*(.*?)(?=\n\n(?:Caption|Image Prompt|Theme:|Reason for No Match:)|$)", full_text, re.DOTALL
        )
        if matches:
            return "\n\n---\n\n".join([m.strip() for m in matches])

    return full_text


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in current_app.config["ALLOWED_EXTENSIONS"]


def is_real_image(file) -> bool:
    """The upload really is an image (not a script or page renamed .png):
    Pillow must recognise and check it. The stream is rewound afterwards."""
    from PIL import Image, UnidentifiedImageError

    try:
        file.stream.seek(0)
        with Image.open(file.stream) as img:
            img.verify()
            ok = (img.format or "").upper() in ("PNG", "JPEG", "GIF", "WEBP", "MPO")
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        ok = False
    finally:
        file.stream.seek(0)
    return ok


# Initialize agents
refine_llm = LLMService()
story_agent = StoryAgent()
vision_agent = VisionAgent()
caption_agent = CaptionAgent()
hashtag_agent = HashtagAgent()
strategy_agent = StrategyAgent()
reviewer_agent = ReviewerAgent()


@api_bp.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "Social Media AI Agent", "version": "3.0.0"})


@api_bp.route("/models/info", methods=["GET"])
@login_required_api
@admin_required_api
def get_models_info():
    """Return 100% dynamic live runtime AI model status, active providers, and agent purpose mappings"""
    user_id = get_current_user_id()

    # 1. Dynamic Text & LLM Agent Model Detection
    text_model = getattr(Config, "BEDROCK_TEXT_MODEL", "amazon.nova-lite-v1:0")
    if getattr(Config, "AWS_ACCESS_KEY_ID", None) or getattr(Config, "AWS_PROFILE", None):
        text_provider = f"AWS Bedrock ({getattr(Config, 'AWS_REGION', 'us-east-1')})"
        text_status = "ACTIVE"
    elif getattr(Config, "OPENAI_API_KEY", None):
        text_model = getattr(Config, "AGENTSCOPE_MODEL", "gpt-4o")
        text_provider = "OpenAI / OpenRouter API"
        text_status = "ACTIVE"
    else:
        text_provider = "System Default Engine"
        text_status = "ONLINE"

    # 2. Dynamic Vision Model Detection
    vision_model = getattr(Config, "BEDROCK_VISION_MODEL", "amazon.nova-lite-v1:0")
    vision_prov = getattr(Config, "VISION_PROVIDER", "bedrock")
    vision_status = (
        "ACTIVE" if (getattr(Config, "AWS_ACCESS_KEY_ID", None) or getattr(Config, "AWS_PROFILE", None)) else "ONLINE"
    )

    # 3. Dynamic Image Generation Model Detection
    if getattr(Config, "Z_AI_API_KEY", None):
        image_model = getattr(Config, "Z_AI_IMAGE_MODEL", "cogview-4-250304")
        image_provider = "Z.AI GLM (CogView-4)"
        image_status = "ACTIVE"
    elif getattr(Config, "AWS_ACCESS_KEY_ID", None) or getattr(Config, "AWS_PROFILE", None):
        image_model = getattr(Config, "BEDROCK_IMAGE_MODEL", "amazon.nova-canvas-v1:0")
        image_provider = f"AWS Bedrock Nova Canvas ({getattr(Config, 'AWS_REGION', 'us-east-1')})"
        image_status = "ACTIVE"
    else:
        image_model = getattr(Config, "IMAGE_MODEL", "openai/dall-e-3")
        image_provider = "OpenAI DALL-E"
        image_status = "STANDBY"

    # 4. Dynamic Video Generation Model Detection
    # (HeyRoute is the only video provider - see media_service.generate_video)
    video_model = Config.HEYROUTE_VIDEO_MODEL
    video_provider = "HeyRoute"
    video_status = "ACTIVE" if Config.HEYROUTE_VIDEO_API_KEY else "STANDBY"

    # 5. Dynamic RAG Memory Engine Stats
    try:
        mem_stats = memory_service.get_stats()
        mem_count = mem_stats.get("total_memories", 0)
    except Exception:
        mem_count = 0

    mem_model = "ChromaDB + SentenceTransformers (all-MiniLM-L6-v2)"
    mem_provider = f"Local Vector Store ({mem_count} Memory Nodes)"

    # Live Usage Stats
    usage_data = {}
    if DB_AVAILABLE and user_id:
        try:
            usage_data = get_user_usage_stats(user_id)
        except Exception:
            pass

    models_info = [
        {
            "purpose": "Text & Campaign Copy Generation",
            "category": "llm",
            "model_name": text_model,
            "provider": text_provider,
            "status": text_status,
            "agents": ["StoryAgent", "CaptionAgent", "HashtagAgent", "StrategyAgent", "ReviewerAgent"],
            "description": f"Powers multi-agent campaign reasoning, platform captions, and tone compliance. Total user runs processed: {usage_data.get('total_runs', 0)}.",
        },
        {
            "purpose": "Vision & Visual Media Analysis",
            "category": "vision",
            "model_name": vision_model,
            "provider": f"AWS Bedrock ({vision_prov})",
            "status": vision_status,
            "agents": ["VisionAgent"],
            "description": "Analyzes uploaded user images and video clips to extract contextual scenes, text overlays, and visual color palettes.",
        },
        {
            "purpose": "AI Image Asset Generation",
            "category": "image",
            "model_name": image_model,
            "provider": image_provider,
            "status": image_status,
            "agents": ["MediaGenerationService"],
            "description": "Synthesizes high-resolution 1:1, 4:5, and 16:9 visual marketing assets tailored for Instagram, Facebook, and LinkedIn.",
        },
        {
            "purpose": "AI Motion & Video Generation",
            "category": "video",
            "model_name": video_model,
            "provider": video_provider,
            "status": video_status,
            "agents": ["MediaGenerationService"],
            "description": "Generates 4 to 6-second HD promo video clips, motion reels, and brand video teasers.",
        },
        {
            "purpose": "RAG Memory & Knowledge Graph",
            "category": "memory",
            "model_name": mem_model,
            "provider": mem_provider,
            "status": "ONLINE",
            "agents": ["MemoryService", "RAG Engine"],
            "description": "Stores vector embeddings of past successful campaigns, brand voice memory, and interactive knowledge graph nodes.",
        },
    ]

    runtime_summary = {
        "user_id": user_id,
        "use_mock_llm": getattr(Config, "USE_MOCK_LLM", False),
        "active_models_count": len(models_info),
        "total_user_runs": usage_data.get("total_runs", 0),
        "total_tokens_used": usage_data.get("total_tokens", 0),
        "total_cost_usd": round(usage_data.get("total_cost_usd", 0.0), 4),
        "aws_region": getattr(Config, "AWS_REGION", "us-east-1"),
        "s3_bucket": getattr(Config, "AWS_S3_BUCKET", "N/A"),
    }

    return jsonify({"success": True, "models": models_info, "summary": runtime_summary})


@api_bp.route("/memory/graph", methods=["GET"])
@login_required_api
def get_memory_graph():
    """Return nodes, edges, and vector space metrics for interactive RAG Memory Graph Diagram."""
    user_id = get_current_user_id()
    try:
        data = memory_service.get_memory_graph_data(user_id=user_id)
        return jsonify(data)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


@api_bp.route("/metrics/usage", methods=["GET"])
@login_required_api
def usage_metrics():
    """Return aggregated token and cost metrics for current user"""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"success": True, "total_runs": 0, "total_tokens": 0, "total_cost_usd": 0.0})
    try:
        stats = get_user_usage_stats(user_id)
        return jsonify({"success": True, **stats})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@api_bp.route("/upload", methods=["POST"])
@login_required_api
def upload_image():
    """Upload image for analysis"""
    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400

    file = request.files["image"]
    filename = file.filename
    if not filename:
        return jsonify({"error": "No file selected"}), 400

    if file and allowed_file(filename):
        if not is_real_image(file):
            return jsonify({"error": "That file isn't a valid image. Upload a PNG, JPG, GIF or WebP picture."}), 400
        secure_name = secure_filename(filename)
        unique_name = f"{uuid.uuid4()}_{secure_name}"
        filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], unique_name)
        file.save(filepath)
        storage_service.upload(filepath)

        # The AI analysis takes ~30-40 s: it runs in the background so the
        # upload is done at once. The page asks for it via
        # /api/upload/analysis; a generation that needs it waits for this same
        # analysis instead of starting a second one (VisionAgent).
        vision_agent.analyze_in_background(filepath)
        return jsonify({"success": True, "image_id": unique_name, "filepath": filepath,
                        "analysis": None, "analysis_pending": True})

    return jsonify({"error": "Invalid file type"}), 400


@api_bp.route("/upload/analysis", methods=["GET"])
@login_required_api
def upload_analysis():
    """The background analysis of an uploaded image, once it's finished:
    {"ready": false} while it's still running."""
    image = _uploaded_image(request.args.get("image_id"))
    if not image:
        return jsonify({"ready": False, "error": "Image not found"}), 404
    analysis = vision_agent.cached_analysis(image)
    return jsonify({"ready": analysis is not None, "analysis": analysis})


def _partial_json(text: str):
    """The object in a JSON answer that's still being written (None if nothing
    readable yet). Handles an answer written as one JSON string ("{\"a\": ...)."""
    text = (text or "").strip()
    if text.startswith('"'):
        # The answer is being written as one JSON string ("{\"themes\": ...):
        # undo that first - json_repair mangles a half-written escaped string
        body = text[1:]
        before_quote = body[:-1]
        if body.endswith('"') and (len(before_quote) - len(before_quote.rstrip("\\"))) % 2 == 0:
            body = before_quote  # the closing quote (not an escaped \" inside the text)
        body = re.sub(r"\\u[0-9a-fA-F]{0,3}$", "", body)  # a \uXXXX cut in half
        if (len(body) - len(body.rstrip("\\"))) % 2 == 1:
            body = body[:-1]  # a lone backslash cut off from what it escapes
        try:
            text = json.loads('"' + body + '"', strict=False)
        except ValueError:
            return None
    try:
        import json_repair

        data = json_repair.repair_json(text or "{}", return_objects=True)
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _partial_research(text: str) -> dict:
    """What can be shown of a research answer that's still being written: the
    themes, facts and post ideas so far (the last item may be mid-sentence)."""
    from agents.story_agent import research_item_text

    out = {"stage": "writing", "chars": len(text or "")}
    data = _partial_json(text)
    if isinstance(data, dict):
        for key, limit in (("themes", 8), ("research_notes", 8), ("hooks", 12)):
            items = [research_item_text(x)[:400] for x in (data.get(key) or []) if not isinstance(x, list)]
            items = [i for i in items if i]
            if items:
                out[key] = items[:limit]
    return out


@api_bp.route("/analyze-story", methods=["POST"])
@login_required_api
@limit_by_user("ai_user")
def analyze_story():
    """Analyze story text"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    story = data.get("story", "")
    target_company = data.get("target_company")

    if not story and (not target_company or target_company == "None"):
        return jsonify({"error": "Story text or target company is required"}), 400

    try:
        # Inject Scraper Explorer Intelligence if available
        suggested_brief = None
        if target_company and target_company != "None":
            from services.scraper_service import ScraperService

            scraper = ScraperService()
            intelligence = scraper.get_company_talking_points(target_company)
            if intelligence:
                if not story:
                    story = intelligence
                    suggested_brief = intelligence
                else:
                    story = f"Focus Company: {target_company}\n\n{story}\n\n{intelligence}".strip()
                    suggested_brief = story

        # A follow-up in an existing Studio Chat thread - research it in the
        # context of what was already generated, same framing /generate uses,
        # instead of as a standalone topic.
        previous_context = data.get("previous_context")
        if previous_context:
            story = f"Follow-up Refinement Request: {story}\n\n[PREVIOUS TURN CONTEXT & OUTPUTS]:\n{previous_context}"

        user_id = get_current_user_id()
        # Live research (GET /api/generate/progress/<id>, field "research"): the
        # page shows themes, facts and post ideas while they're being written
        from services import progress_store

        progress_id = data.get("progress_id")
        progress_store.set_step(user_id, progress_id, "research", json.dumps({"stage": "thinking"}))

        def _publish_partial(text):
            progress_store.set_step(user_id, progress_id, "research", json.dumps(_partial_research(text)))

        retrieved_memories = memory_service.retrieve_context(story, user_id=user_id, n_results=2)
        mem_prompt = memory_service.format_memory_prompt(retrieved_memories)

        analysis = story_agent.analyze(
            story, memory_context=mem_prompt, on_partial=_publish_partial if progress_store.valid_id(progress_id) else None
        )
        # key_points was a second, sequential AI call (8-90 s) whose result no
        # page shows - kept in the response, empty, for API compatibility
        key_points: list[str] = []

        return jsonify(
            {
                "success": True,
                "analysis": analysis,
                "key_points": key_points,
                "memories_referenced": len(retrieved_memories),
                "suggested_brief": suggested_brief,
            }
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


# Brand-voice personas (e.g. "Standard Enterprise", "B2B Tech Leader") describe
# WRITING STYLE only, but read exactly like a company name - LLMs (and, worse,
# RAG memory replaying an old mistake as a "winning pattern") occasionally
# self-reference the persona as if it were the company, e.g. "At Standard
# Enterprise, we're committed to..." or "#StandardEnterpriseAI". Prompt-level
# clarifications reduce this but can't guarantee it never happens, so this is
# a deterministic final check - it doesn't call the LLM, just corrects a
# narrow, high-confidence pattern (the exact persona phrase used as a company
# self-reference) after every other agent has already run.
COMPANY_NAME = "StradIT"


def _scrub_brand_voice_leak(text: str, brand_voice: str | None, company_name: str = COMPANY_NAME) -> tuple[str, bool]:
    """Returns (possibly-corrected text, whether a correction was made).
    company_name defaults to StradIT but is overridden with the Studio Chat
    user's own company (see services/brand_profile_service.py) when they have
    an onboarding-derived brand profile - otherwise this would incorrectly
    "correct" their content to reference StradIT instead of their own brand."""
    if not text or not brand_voice or brand_voice.strip().lower() == company_name.lower():
        return text, False

    persona = re.escape(brand_voice.strip())
    corrected = text
    # "At/From/We at <Persona>, ..." used as a company self-reference.
    corrected, n1 = re.subn(rf"\b(At|From|We at)\s+{persona}\b", rf"\1 {company_name}", corrected, flags=re.IGNORECASE)
    # A hashtag built from the persona (e.g. #StandardEnterpriseAI).
    persona_hashtag = re.escape(brand_voice.strip().replace(" ", ""))
    corrected, n2 = re.subn(rf"#{persona_hashtag}(\w*)", rf"#{company_name}\1", corrected, flags=re.IGNORECASE)
    return corrected, (n1 + n2) > 0


def _audit(action: str, target_user_id: int | None = None, details: dict | None = None) -> None:
    """Record an admin's change in the audit log (db.AuditLog): who, what, from where."""
    if not DB_AVAILABLE:
        return
    from auth.rate_limit import client_ip
    from db import log_audit

    log_audit(action, actor_user_id=get_current_user_id(), target_user_id=target_user_id, details=details, ip=client_ip())


def _credit_blocked(stats: dict) -> bool:
    """The Free plan's spending cap is used up. Paid plans are limited by their
    monthly allowances instead (db.get_plan_usage), not by credit."""
    return bool(stats.get("credit_capped", True)) and stats.get("remaining_credits", 0.0) <= 0.0


def _plan_limit_message(what: str, plan_name: str | None) -> str:
    return (f"You've used all the {what} in your {plan_name or 'current'} plan for this month. "
            "Upgrade your plan or ask your admin for a top-up pack.")


def _feature_locked(user_id, feature: str, what: str):
    """A 403 "upgrade" response when the user's plan doesn't include a
    feature (db.plan_features), else None."""
    if user_id is None or not DB_AVAILABLE:
        return None
    from db import cheapest_plan_with, get_user_features

    features = get_user_features(user_id)
    if features.get(feature):
        return None
    upgrade = cheapest_plan_with(feature)
    plan_name = upgrade["name"] if upgrade else "a higher"
    return jsonify({
        "success": False, "code": "plan_feature_locked", "feature": feature, "upgrade_plan": upgrade["slug"] if upgrade else None,
        "error": f"{what} is available on the {plan_name} plan and above. Your plan is {features.get('plan_name') or 'Free'}.",
    }), 403


def _posts_blocked_payload(user_id) -> dict | None:
    """Why the user's plan allows no more posts this month (the 403 body), else None."""
    if user_id is None or not DB_AVAILABLE:
        return None
    from db import get_plan_usage

    usage = get_plan_usage(user_id)
    if not usage or usage["is_admin"] or usage["posts"]["remaining"] is None or usage["posts"]["remaining"] > 0:
        return None
    return {"success": False, "code": "plan_posts_exhausted", "plan_usage": usage,
            "error": _plan_limit_message("posts", usage["plan"]["name"])}


def _posts_blocked(user_id):
    """A 403 response when the user's plan has no posts left this month, else None."""
    payload = _posts_blocked_payload(user_id)
    return (jsonify(payload), 403) if payload else None


def _image_limit_message(quota: dict) -> str:
    """Shown when a user has used all of today's images (daily limit, midnight UTC),
    or all of this month's plan allowance and packs."""
    if quota.get("blocked_by") == "month":
        name = (quota.get("plan") or "").title() or None
        return _plan_limit_message("images", name)
    secs = max(0, int(quota.get("resets_in_seconds") or 0))
    hours, minutes = secs // 3600, (secs % 3600) // 60
    when = f"{hours} h {minutes} m" if hours else f"{minutes} m"
    limit = quota.get("limit") or 0
    return (
        f"You've used your {limit} image{'s' if limit != 1 else ''} for today. "
        f"Your limit resets in {when} (midnight UTC). For more, contact your admin."
    )


def _image_quota_or_block(user_id):
    """(quota, None) when the user may create an image now, else (quota, response)."""
    if user_id is None or not DB_AVAILABLE:
        return None, None
    from db import get_image_quota

    quota = get_image_quota(user_id)
    if not quota["unlimited"] and quota["remaining"] <= 0:
        return quota, (
            jsonify({"success": False, "code": "image_limit_reached", "error": _image_limit_message(quota), "quota": quota}),
            403,
        )
    return quota, None


def _video_quota_or_block(user_id):
    """(quota, None) when the user may create a video now, else (quota, response)."""
    if user_id is None or not DB_AVAILABLE:
        return None, None
    from db import get_video_quota

    quota = get_video_quota(user_id)
    code = _video_block_code(quota)
    if code:
        return quota, (
            jsonify({"success": False, "code": code, "error": _video_block_message(quota), "quota": quota}),
            403,
        )
    return quota, None


def _video_block_code(quota) -> str | None:
    if not quota["enabled"]:
        return "video_not_enabled"  # still "coming soon" for this user (Admin -> Users switches it on)
    if quota.get("blocked_by") == "month":
        return "video_pack_needed"  # no included videos left and no video pack
    if not quota["unlimited"] and quota["remaining"] is not None and quota["remaining"] <= 0:
        return "video_limit_reached"
    return None


def _video_block_message(quota) -> str:
    if not quota["enabled"]:
        return "Video is coming soon for your account. Ask your admin if you need it switched on."
    if quota.get("blocked_by") == "month":
        return "You have no videos left. Videos come in packs - ask your admin for a video pack to make more."
    if quota.get("limit") == 0:
        return "Your plan doesn't include video. Upgrade your plan to make videos."
    return _image_limit_message(quota).replace(" image", " video")


def _quota_after_refine(user_id, targets) -> dict | None:
    """Today's image quota after a refine that touched images (for Studio Chat)."""
    if "image" not in targets or not DB_AVAILABLE or user_id is None:
        return None
    from db import get_image_quota

    return get_image_quota(user_id)


def _conversation_for(user_id, data: dict, title: str) -> int | None:
    """The conversation to save this message into: the client's current one,
    or a new one. None when it can't be saved - the run is then saved alone."""
    try:
        from db import ensure_conversation

        return ensure_conversation(user_id, data.get("conversation_id"), title)
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning(f"[DB] Could not get conversation: {e}")
        return None


def _size_wh(size) -> tuple[int | None, int | None]:
    """ "1024x1024" -> (1024, 1024)."""
    m = re.match(r"^\s*(\d+)\s*[xX×]\s*(\d+)\s*$", str(size or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def _mark_done_when_finished(futures, on_done):
    """Calls on_done() once, when the last of `futures` finishes (progress
    display for steps that run in parallel)."""
    import threading

    lock = threading.Lock()
    remaining = [len(futures)]

    def _one_finished(_future):
        with lock:
            remaining[0] -= 1
            last = remaining[0] == 0
        if last:
            on_done()

    for future in futures:
        future.add_done_callback(_one_finished)


@api_bp.route("/generate/progress/<progress_id>", methods=["GET"])
@login_required_api
def generate_progress(progress_id):
    """Live step states of a running /api/generate call (by the progress_id
    the page sent with it): {"steps": {"story": "done", "caption": "active", ...}}"""
    from services import progress_store

    return jsonify({"steps": progress_store.get(get_current_user_id(), progress_id)})


@api_bp.route("/generate", methods=["POST"])
@login_required_api
@limit_by_user("ai_user")
def generate_content():
    """
    Main endpoint to generate complete social media content with Multi-Turn Refinement, A/B Hook Variations & Critic Self-Correction
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400
    # The pipeline itself: services/content_pipeline.py (shared with Autopilot)
    from services.content_pipeline import PostBlocked, generate_post

    try:
        return jsonify(generate_post(get_current_user_id(), data))
    except PostBlocked as blocked:
        return jsonify(blocked.payload), blocked.status
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


# ── Studio Chat Refinement ─────────────────────────────────────────────────
# A follow-up in an existing Studio Chat thread ("fix the spelling in the
# image", "make the caption shorter") is applied surgically to the post the
# user is looking at - only the targeted parts change, and an image is edited
# (previous image as reference) rather than regenerated from scratch. The full
# 6-agent pipeline only runs again when the follow-up is a genuinely new post.

# Shared on-image text discipline: image models misspell small or dense text,
# so keep it to one short headline and never print stats or citations.

REFINE_SYSTEM_PROMPT = """You are the Refinement Editor of a social media content studio.
The user already has a finished post (caption, hashtags, and possibly an image or video) and is sending a
follow-up message about it. Apply ONLY what the user asked for, surgically, and leave everything else exactly
as it is - like a designer iterating with a client, where every new message builds on the current version.

Decide:
- intent: "refine" when the message changes, fixes, or adds to the existing post - including creating an
  image or video for it for the first time. "new_content" ONLY when the user clearly wants a different post
  on a different topic.
- targets: which parts change - any of "caption", "hashtags", "image", "video". Infer from the message:
  "fix the spelling in the image" -> image; "make it shorter" / "add a CTA" -> caption; "change the headline"
  -> caption, plus image if the image shows that headline. If ambiguous, the user's selected output types are a hint.

Rules:
- Do not touch parts that are not targeted. Only return revised content for targeted parts.
- Keep brand names, facts and figures exactly as in the existing post. Never invent statistics, sources or citations.
- image_instruction: a precise, literal instruction for an image-editing model describing only the change,
  e.g. "Replace all text with the exact text below; keep the layout, colors, truck and people unchanged".
- on_image_text: the exact text that should appear on the image, correctly spelled - a headline (max 10 words,
  the caption's opening hook verbatim when it fits) and optionally one subline (max 12 words) taken from the
  caption. Never put statistics, percentages, research figures or source names (McKinsey, Gartner, ...) in
  on_image_text, even if the old image had them. When the user reports wrong, garbled or misspelled text,
  always fill this in, and keep it to headline + at most one subline.
- video_prompt: a concise visual scene description (no dialogue, no on-screen statistics).
- image_ref: when IMAGE VERSIONS are listed and the user explicitly points at a version other than the
  ACTIVE one ("the first image", "the original", "the previous version", "the one before the last edit",
  "go back to #2"), that version's number; otherwise null. "it", "this", "the image" mean the ACTIVE one.
- image_platforms: when the post has several images (one per [KEY] in CURRENT POST) and the user names
  which one(s) to change ("the story image", "only the Instagram one", "the 4:5 version"), those keys in
  lowercase; otherwise [] (= every image).

Return ONLY this JSON:
{"intent": "refine", "targets": ["image"],
 "captions": {"<platform>": "<full revised caption>"},
 "hashtags": {"<platform>": ["#Tag"]},
 "image_instruction": "", "on_image_text": {"headline": "", "subline": ""},
 "video_prompt": "", "image_ref": null, "image_platforms": [], "change_summary": "<one short sentence describing what changed>"}"""


def _refine_language_line(code) -> str:
    """Refining keeps the post in its language (services/language_service.py)."""
    from services.language_service import LANGUAGES, normalize

    code = normalize(code)
    if code == "en":
        return ""
    return (f"POST LANGUAGE: the post is in {LANGUAGES[code][1]}. Write every changed caption and hashtag in that "
            "language too, unless the instruction asks for another language.\n\n")


@api_bp.route("/refine", methods=["POST"])
@login_required_api
@limit_by_user("ai_user")
def refine_post():
    """Apply a Studio Chat follow-up to an existing post.

    Body: instruction, platforms, tone, previous_context, selected_outputs,
    base_run_id, reference_image_path (an image the user attached to this
    follow-up), and base: {platform: {caption, hashtags, media_prompt,
    image_url, image_prompt, video_url}}.
    Returns {"intent": "new_content"} when the follow-up is a different post,
    so the client runs the normal /generate pipeline instead.
    """
    data = request.get_json() or {}
    instruction = (data.get("instruction") or "").strip()
    base = data.get("base") or {}
    platforms = [p for p in (data.get("platforms") or []) if p in base]
    if not instruction or not platforms:
        return jsonify({"error": "instruction and an existing post to refine are required"}), 400

    tone = data.get("tone")
    user_id = get_current_user_id()

    if DB_AVAILABLE and user_id:
        try:
            stats = get_user_usage_stats(user_id)
            if _credit_blocked(stats):
                limit_val = stats.get("credit_limit", Config.DEFAULT_CREDIT_USD)
                return (
                    jsonify(
                        {
                            "error": f"Credit limit reached ({format_money(limit_val, stats.get('currency', 'USD'))}). Please request a credit extension from admin.",
                            "credit_limit_exceeded": True,
                        }
                    ),
                    402,
                )
        except Exception as _cred_err:
            current_app.logger.warning(f"[Credits] Check error: {_cred_err}")

    post_lines = []
    for p in platforms:
        b = base[p] or {}
        post_lines.append(
            f"[{p.upper()}]\nCaption: {b.get('caption') or '(none)'}\n"
            f"Hashtags: {' '.join(b.get('hashtags') or []) or '(none)'}\n"
            f"Image: {'exists - generated from: ' + (b.get('image_prompt') or b.get('media_prompt') or '')[:600] if b.get('image_url') else 'none yet'}\n"
            f"Video: {'exists' if b.get('video_url') else 'none yet'}"
        )
    # The conversation's image versions, so "the first image" / "the previous
    # version" can be resolved (plan["image_ref"]); "it" = the active image.
    started = time.time()
    conv_images, active_image_id = [], None
    if DB_AVAILABLE and user_id is not None and data.get("conversation_id"):
        try:
            from db import conversation_images, get_active_image_id

            conv_images = conversation_images(data.get("conversation_id"), user_id)
            active_image_id = get_active_image_id(data.get("conversation_id"), user_id)
        except Exception as img_err:  # noqa: BLE001 - versions are optional context
            current_app.logger.warning(f"[Images] Could not load image versions: {img_err}")
    selected_ids = {(base[p] or {}).get("image_asset_id") for p in platforms} - {None}
    versions_block = build_image_versions_block(
        conv_images, next(iter(selected_ids)) if len(selected_ids) == 1 else active_image_id
    )
    user_prompt = (
        f"CONVERSATION SO FAR:\n{data.get('previous_context') or '(none)'}\n\n"
        f"CURRENT POST:\n" + "\n\n".join(post_lines) + "\n\n"
        + (versions_block + "\n\n" if versions_block else "")
        + f"USER'S SELECTED OUTPUT TYPES: {', '.join(data.get('selected_outputs') or []) or '(none)'}\n\n"
        + _refine_language_line(data.get("language"))
        + f"USER'S FOLLOW-UP MESSAGE: {instruction}"
    )

    try:
        plan, usage = refine_llm.generate_json(
            REFINE_SYSTEM_PROMPT, user_prompt, temperature=0.3, max_tokens=1500, return_usage=True
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Refinement failed: {e}"}), 500

    if plan.get("intent") == "new_content":
        return jsonify({"success": True, "intent": "new_content"})

    targets = {str(t).lower() for t in (plan.get("targets") or [])}
    # The model echoes platform keys in whatever case it saw them ("LINKEDIN")
    plan_captions = {str(k).lower(): v for k, v in (plan.get("captions") or {}).items()}
    plan_hashtags = {str(k).lower(): v for k, v in (plan.get("hashtags") or {}).items()}
    if not targets:
        return jsonify({"error": "Could not tell what to change - try naming the caption, hashtags or image."}), 422
    # Which image to edit: a version the user named ("the first image") wins
    # over the version being refined (the active / "Refine this version" one).
    referenced_image = None
    try:
        ref = int(plan.get("image_ref")) if plan.get("image_ref") not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        ref = None
    if ref and 1 <= ref <= len(conv_images):
        referenced_image = conv_images[ref - 1]
    # Only the image(s) the user named ("the story image"); none named = all
    image_only = {str(k).lower() for k in (plan.get("image_platforms") or []) if isinstance(k, str)} & set(platforms)
    conversation_id = _conversation_for(user_id, data, instruction) if DB_AVAILABLE and user_id is not None else None
    new_image_ids: list[int] = []  # edits made below, linked to the run once it is saved
    extra_reference = data.get("reference_image_path")
    media_errors = []
    content: dict[str, typing.Any] = {}
    compliance_rules_active = active_rules_for_user(user_id)
    extra_tokens, extra_cost = 0, 0.0
    from services.brand_logo_service import resolve_logo_path

    logo_path = resolve_logo_path(user_id) if targets & {"image", "video"} else None

    for p in platforms:
        b = base[p] or {}
        entry: dict[str, typing.Any] = {
            "caption": {"primary_caption": b.get("caption") or ""},
            "hashtags": {"hashtags": b.get("hashtags") or []},
            "media_prompt": b.get("media_prompt") or "",
            "media": {},
        }
        if b.get("image_url"):
            entry["media"]["image"] = {
                "url": b["image_url"],
                "clean_url": b.get("image_clean_url"),
                "prompt": b.get("image_prompt"),
                "asset_id": b.get("image_asset_id"),
            }
        if b.get("video_url"):
            entry["media"]["video"] = {"url": b["video_url"]}

        new_caption = plan_captions.get(p)
        if "caption" in targets and new_caption:
            entry["caption"] = {"primary_caption": new_caption.strip(), "refined_by_critic": False}
            # A rewritten caption is re-checked, so an edit can't slip past the rules
            if compliance_rules_active:
                try:
                    result, c_usage = check_caption(new_caption.strip(), p, compliance_rules_active, refine_llm)
                    extra_tokens += c_usage.get("total_tokens", 0)
                    extra_cost += c_usage.get("cost_usd", 0.0)
                    if result:
                        entry["caption"]["primary_caption"] = result.pop("caption")
                        entry["compliance"] = result
                except Exception as c_err:
                    current_app.logger.warning(f"[Compliance] Refine check failed for {p}: {c_err}")
        new_tags = plan_hashtags.get(p)
        if "hashtags" in targets and new_tags:
            entry["hashtags"] = {"hashtags": new_tags}

        if "image" in targets and MEDIA_AVAILABLE and (not image_only or p in image_only):
            # Edit the unbranded copy when there is one - the logo is stamped on
            # again afterwards, so the model never redraws or duplicates it
            from db import get_image_asset, image_lineage

            base_asset = referenced_image or get_image_asset(b.get("image_asset_id"), user_id)
            if base_asset:
                base_image = base_asset.get("clean_url") or base_asset.get("url")
                lineage = image_lineage(base_asset["id"], user_id)
            else:  # a post from before image versions existed
                base_image = b.get("image_clean_url") or b.get("image_url")
                lineage = []
            references = [r for r in (base_image, extra_reference) if r]
            prompt = build_image_edit_prompt(plan, p, entry["media_prompt"], bool(base_image), lineage)
            # An image edit is a full image generation: same daily limit + model
            edit_quota, blocked = _image_quota_or_block(user_id)
            if blocked:
                # Daily limit used up: don't show the old image as if it were the
                # edit - the reply shows "Daily image limit reached" instead. The
                # old image is kept as previous_* so a later refine can edit it.
                prev = entry["media"].get("image") or {}
                entry["media"]["image"] = {
                    "limit_reached": True,
                    "previous_url": prev.get("url"),
                    "previous_clean_url": prev.get("clean_url"),
                    "previous_prompt": prev.get("prompt"),
                    "previous_asset_id": base_asset["id"] if base_asset else prev.get("asset_id"),
                }
                result = None
                log_event("image.edit", conversation_id=conversation_id, platform=p, status="blocked_limit",
                          parent_asset_id=base_asset["id"] if base_asset else None)
            else:
                from services.image_presets import ASPECT_SIZES

                # An image command's image keeps its exact size (e.g. a 9:16 story)
                aspect = b.get("image_aspect") if b.get("image_aspect") in ASPECT_SIZES else None
                result = media_service.edit_image(
                    prompt, p, references or None, logo_path=logo_path,
                    model=edit_quota["model"] if edit_quota else None, **({"aspect": aspect} if aspect else {}),
                )
            if result and result.get("success"):
                extra_cost += float(result.get("cost") or 0)
                # The edit is a new version of the image it started from (a
                # second edit of an older version branches; nothing is replaced)
                asset_id = None
                if DB_AVAILABLE and user_id is not None:
                    try:
                        from db import log_image_generation

                        width, height = _size_wh(result.get("size"))
                        asset_id = log_image_generation(
                            user_id, float(result.get("cost") or 0), run_id=None, kind="edit", platform=p,
                            model=result.get("model_id") or (edit_quota["model"] if edit_quota else None),
                            description=prompt, media_url=result.get("url"),
                            conversation_id=conversation_id,
                            parent_id=base_asset["id"] if base_asset else None,
                            prompt=prompt, edit_instruction=plan.get("image_instruction") or instruction,
                            clean_url=result.get("clean_url"), width=width, height=height,
                        )
                        new_image_ids.append(asset_id)
                    except Exception as log_err:  # noqa: BLE001 - the edit itself succeeded
                        current_app.logger.warning(f"[Images] Could not log image edit: {log_err}")
                log_event(
                    "image.edit", conversation_id=conversation_id, asset_id=asset_id, platform=p,
                    parent_asset_id=base_asset["id"] if base_asset else None,
                    root_asset_id=lineage[0]["id"] if lineage else None,
                    referenced_by_user=bool(referenced_image), provider="heyroute",
                    model=result.get("model_id"), status="completed",
                    context_token_estimate=estimate_tokens(prompt),
                )
                entry["media"]["image"] = {
                    "url": result["url"],
                    "clean_url": result.get("clean_url"),
                    "prompt": result.get("prompt"),
                    "resolution": result.get("size"),
                    "model": result.get("model"),
                    "asset_id": asset_id,
                    "parent_asset_id": base_asset["id"] if base_asset else None,
                    "aspect": b.get("image_aspect") or None,
                }
                entry["media_prompt"] = prompt
            elif result is not None:
                media_errors.append(f"{p} image: {result.get('error')}")
                log_event("image.edit", conversation_id=conversation_id, platform=p, status="failed",
                          parent_asset_id=base_asset["id"] if base_asset else None,
                          error=str(result.get("error"))[:200])

        if "video" in targets and MEDIA_AVAILABLE and plan.get("video_prompt"):
            image_media = entry["media"].get("image") or {}
            video_ref = image_media.get("clean_url") or image_media.get("url") or extra_reference
            # A refined video is a full video generation: same daily limit
            video_quota, blocked = _video_quota_or_block(user_id)
            if blocked:
                result = {"success": False, "error": _video_block_message(video_quota)}
                log_event("video.generate", conversation_id=conversation_id, platform=p,
                          status="blocked_limit" if video_quota["enabled"] else "blocked_not_enabled")
            else:
                result = media_service.generate_video(
                    plan["video_prompt"], p, tone, image_path=video_ref, logo_path=logo_path
                )
            if result.get("success"):
                extra_cost += float(result.get("cost") or 0)
                asset_id = None
                if DB_AVAILABLE and user_id is not None:
                    try:
                        from db import log_image_generation

                        width, height = _size_wh(result.get("resolution"))
                        asset_id = log_image_generation(
                            user_id, float(result.get("cost") or 0), run_id=None, kind="video", platform=p,
                            model=result.get("model"), description=plan["video_prompt"],
                            media_url=result.get("url"), conversation_id=conversation_id,
                            prompt=result.get("prompt"), width=width, height=height,
                        )
                        new_image_ids.append(asset_id)  # linked to the run below, like image edits
                    except Exception as log_err:  # noqa: BLE001 - the video itself succeeded
                        current_app.logger.warning(f"[Video] Could not log video: {log_err}")
                log_event("video.generate", conversation_id=conversation_id, asset_id=asset_id, platform=p,
                          provider="heyroute", model=result.get("model"), status="completed",
                          context_token_estimate=estimate_tokens(plan["video_prompt"]))
                entry["media"]["video"] = {
                    "url": result["url"], "resolution": result.get("resolution"), "asset_id": asset_id,
                }
            else:
                media_errors.append(f"{p} video: {result.get('error')}")
                if not blocked:
                    log_event("video.generate", conversation_id=conversation_id, platform=p, status="failed",
                              error=str(result.get("error"))[:200])

        content[p] = entry

    usage = {
        "total_tokens": usage.get("total_tokens", 0) + extra_tokens,
        "cost_usd": round(usage.get("cost_usd", 0.0) + extra_cost, 6),
    }
    agents_executed = [
        {
            "name": "Refinement Editor",
            "agent": "RefineAgent",
            "role": plan.get("change_summary") or f"Applied the requested change to: {', '.join(sorted(targets))}",
        }
    ]
    if any(entry.get("compliance") for entry in content.values()):
        agents_executed.append(
            {
                "name": "Compliance Checker",
                "agent": "ComplianceAgent",
                "role": f"Re-checked the revised caption against {len(compliance_rules_active)} advertising rules",
            }
        )
    run_id = None
    if DB_AVAILABLE and user_id is not None:
        try:
            content_to_save: dict[str, typing.Any] = dict(content)
            content_to_save["_agents"] = agents_executed
            content_to_save["_meta"] = {"refined_from_run_id": data.get("base_run_id")}
            preset_meta = data.get("preset") if isinstance(data.get("preset"), dict) else None
            if preset_meta and preset_meta.get("id"):
                content_to_save["_preset"] = {k: preset_meta.get(k) for k in ("id", "label", "icon", "text", "keys", "occasion")}
            if data.get("version_of_run_id"):  # Regenerate of a refinement
                content_to_save["_meta"]["version_of"] = data.get("version_of_run_id")
            run_id = save_run(
                story=instruction,
                tone=tone,
                platforms=platforms,
                content=content_to_save,
                user_id=user_id,
                tokens_used=usage.get("total_tokens", 0),
                cost_usd=usage.get("cost_usd", 0.0),
                conversation_id=conversation_id,
            )
        except Exception as db_err:
            current_app.logger.warning(f"[DB] Could not save refinement run: {db_err}")
    # The edits' cost is already in the run's cost (extra_cost): linking them to
    # the run stops them counting as separate charges
    if new_image_ids and run_id:
        try:
            from db import attach_images_to_run

            attach_images_to_run(new_image_ids, run_id, user_id)
        except Exception as link_err:  # noqa: BLE001 - the row stays a charge of its own
            current_app.logger.warning(f"[Images] Could not link image edits to run {run_id}: {link_err}")
    log_event(
        "refine", conversation_id=conversation_id, run_id=run_id, targets=sorted(targets),
        image_ref=ref, active_asset_id=active_image_id, new_asset_ids=new_image_ids or None,
        status="completed", context_token_estimate=estimate_tokens(user_prompt),
        latency_ms=int((time.time() - started) * 1000),
    )

    return jsonify(
        {
            "success": True,
            "intent": "refine",
            "targets": sorted(targets),
            "change_summary": plan.get("change_summary"),
            "content": content,
            "run_id": run_id,
            "usage": {"total_tokens": usage.get("total_tokens", 0), "cost_usd": usage.get("cost_usd", 0.0)},
            "agents_executed": agents_executed,
            "media_errors": media_errors,
            # Studio Chat updates "Images today" and the Generate button from this
            "image_quota": _quota_after_refine(user_id, targets),
            "conversation_id": conversation_id,
        }
    )


# ── History Endpoints ──────────────────────────────────────────────────────


# ── Image commands: product photo + /3dbillboard, /metaad, ... ────────────
_PRESET_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _uploaded_image(path_or_url) -> str | None:
    """The local file for an image the user uploaded - only files in the
    uploads folder are accepted (never an arbitrary server path)."""
    from services import storage_service

    name = os.path.basename(str(path_or_url or "").split("?")[0])
    if not name or not name.lower().endswith(_PRESET_IMAGE_EXTS):
        return None
    local = os.path.join(Config.UPLOAD_FOLDER, name)
    if os.path.isfile(local):
        return local
    return storage_service.ensure_local(storage_service.UPLOAD_URL_PREFIX + name)


def _hiring_compliance(user_id, caption: str) -> tuple[dict | None, dict]:
    """A /hiring caption checked against the job-advertising rules of the
    brand's markets plus the brand's own rules. (None, {}) without markets."""
    try:
        from services.compliance_rules import applicable_rules

        brand = {}
        if DB_AVAILABLE and user_id is not None:
            from db import get_user_brand_profile

            brand = get_user_brand_profile(user_id) or {}
        regions = brand.get("compliance_regions") or brand.get("regions_detected") or []
        rules = {r["id"]: r for r in applicable_rules("employment_recruiting", regions) if not r["excluded"]}
        for rule in active_rules_for_user(user_id):
            rules.setdefault(rule["id"], rule)
        if not rules or not caption:
            return None, {}
        result, usage = check_caption(caption, "linkedin", list(rules.values()), refine_llm)
        return result, usage or {}
    except Exception as err:  # noqa: BLE001 - the check is guidance; the post is still made
        logging.getLogger(__name__).warning(f"Hiring compliance check failed: {err}")  # runs in a worker thread: no app context
        return None, {}


def _fit_chars(text, limit: int) -> str:
    """Shortens to the limit at a word boundary (Meta truncates longer ad text)."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",.;:-")
    return cut + "…"


def _meta_ad_copy(user_id, product_notes: str, user_text: str) -> tuple[dict | None, dict]:
    """Headline / primary text / description / button for a Meta ad, within
    Meta's lengths, with the primary text checked against the user's
    compliance rules. (None, usage) when the text model fails."""
    from services.brand_profile_service import build_brand_profile_block
    from services.image_presets import META_CTAS
    from services.prompt_builder import AD_COPY_SYSTEM_PROMPT, build_ad_copy_prompt

    usage = {"total_tokens": 0, "cost_usd": 0.0}
    try:
        copy, u = refine_llm.generate_json(
            AD_COPY_SYSTEM_PROMPT, build_ad_copy_prompt(product_notes, build_brand_profile_block(user_id), user_text),
            temperature=0.6, max_tokens=600, return_usage=True,
        )
        usage = {"total_tokens": u.get("total_tokens", 0), "cost_usd": u.get("cost_usd", 0.0)}
    except Exception as e:  # noqa: BLE001 - the images still go out
        current_app.logger.warning(f"Meta ad copy failed: {e}")
        return None, usage
    ad = {
        "headline": _fit_chars(copy.get("headline"), 40),
        "primary_text": _fit_chars(copy.get("primary_text"), 125),
        "description": _fit_chars(copy.get("description"), 30),
        "cta": copy.get("cta") if copy.get("cta") in META_CTAS else "Learn More",
    }
    rules = active_rules_for_user(user_id)
    if rules and ad["primary_text"]:
        try:
            checked, c_usage = check_caption(ad["primary_text"], "facebook", rules, refine_llm)
            usage["total_tokens"] += c_usage.get("total_tokens", 0)
            usage["cost_usd"] += c_usage.get("cost_usd", 0.0)
            if checked:
                ad["primary_text"] = checked.pop("caption", ad["primary_text"])
                ad["compliance"] = checked
        except Exception as e:  # noqa: BLE001
            current_app.logger.warning(f"Ad copy compliance check failed: {e}")
    return ad, usage


@api_bp.route("/image-presets", methods=["GET"])
@login_required_api
def image_presets_list():
    """The Studio Chat "/" command menu."""
    from services.image_presets import list_presets

    return jsonify({"success": True, "presets": list_presets()})


@api_bp.route("/admin/image-presets", methods=["GET"])
@login_required_api
@admin_required_api
def admin_image_presets():
    """Admin -> Image Settings -> Image commands."""
    from services.image_presets import admin_presets

    return jsonify({"success": True, "presets": admin_presets()})


@api_bp.route("/admin/image-presets/<slug>", methods=["PUT"])
@login_required_api
@admin_required_api
def admin_save_image_preset(slug):
    """Turn a command on/off, or change its name, description, box hint,
    prompt or logo stamping. Takes effect for everyone at once."""
    from services.image_presets import save_preset_changes

    try:
        preset = save_preset_changes(slug, request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    log_event("preset.admin_change", preset=slug, changed=preset["customized"], enabled=preset["enabled"])
    return jsonify({"success": True, "preset": preset})


@api_bp.route("/admin/image-presets/<slug>/reset", methods=["POST"])
@login_required_api
@admin_required_api
def admin_reset_image_preset(slug):
    from services.image_presets import reset_preset

    try:
        preset = reset_preset(slug)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    log_event("preset.admin_reset", preset=slug)
    return jsonify({"success": True, "preset": preset})


@api_bp.route("/presets/run", methods=["POST"])
@login_required_api
@limit_by_user("media_user")
def run_image_preset():
    """Runs an image command on the user's product photo. Every image counts
    toward the daily limit, and all of a command's images are checked against
    it up front - a run never stops half-way for lack of images."""
    from concurrent.futures import ThreadPoolExecutor

    from services.brand_logo_service import resolve_logo_path
    from services.image_presets import get_preset, preset_outputs
    from services.prompt_builder import build_preset_prompt

    if not MEDIA_AVAILABLE:
        return jsonify({"success": False, "error": "Image generation is not available right now."}), 503
    started = time.time()
    data = request.get_json(silent=True) or {}
    preset = get_preset(data.get("preset"))
    if not preset:
        if get_preset(data.get("preset"), include_disabled=True):
            return jsonify({"success": False, "code": "preset_disabled",
                            "error": "This image command is turned off by your admin."}), 400
        return jsonify({"success": False, "error": "Unknown command."}), 400
    user_id = get_current_user_id()
    product = _uploaded_image(data.get("image_path"))
    if preset["requires_image"] and not product:
        return jsonify({"success": False, "code": "image_required",
                        "error": f"Attach a product photo to use /{preset['id']}."}), 400
    user_text = (data.get("text") or "").strip()[:500]
    if preset.get("carousel"):
        from services import carousel_service

        carousel_aspect = carousel_service.carousel_aspect(user_text)
        outputs = carousel_service.placeholder_outputs(carousel_service.slide_count(user_text), carousel_aspect)
    elif preset.get("hiring") and not user_text:
        return jsonify({"success": False, "code": "text_required",
                        "error": "Describe the job after /hiring, e.g. /hiring Delivery drivers in Pune, apply by 30 Nov."}), 400
    else:
        outputs = preset_outputs(preset, bool(data.get("all_sizes")))
    product_notes = (data.get("product_notes") or "").strip()[:600]
    if not product_notes and product:
        # The upload's background analysis, if it has finished (never waited for:
        # the image model sees the photo itself; the notes only add detail)
        cached = vision_agent.cached_analysis(product) or {}
        product_notes = str(cached.get("rich_description") or cached.get("raw_caption") or "")[:600]

    quota = None
    if DB_AVAILABLE and user_id is not None:
        stats = get_user_usage_stats(user_id)
        if _credit_blocked(stats):
            return jsonify({"success": False, "credit_limit_exceeded": True,
                            "error": f"Credit limit reached ({format_money(stats.get('credit_limit', Config.DEFAULT_CREDIT_USD), stats.get('currency', 'USD'))}). "
                                     "Please request a credit extension from admin."}), 402
        from db import get_image_quota

        quota = get_image_quota(user_id)
        if not quota["unlimited"] and quota["remaining"] < len(outputs):
            need = len(outputs)
            month_short = quota.get("month_remaining") is not None and quota["month_remaining"] < need
            msg = (_image_limit_message(quota) if quota["remaining"] <= 0 else
                   f"/{preset['id']} needs {need} images and you have {quota['remaining']} left "
                   + ("in your plan this month. Choose fewer sizes, or ask your admin for an image pack."
                      if month_short else "today. Choose fewer sizes, or try again after midnight UTC."))
            return jsonify({"success": False, "code": "image_limit_reached", "error": msg, "quota": quota}), 403

    brand = {}
    if DB_AVAILABLE and user_id is not None:
        try:
            from db import get_user_brand_profile

            brand = get_user_brand_profile(user_id) or {}
        except Exception as e:  # noqa: BLE001 - the brand's look is optional
            current_app.logger.warning(f"Could not load brand profile: {e}")
    logo_path = resolve_logo_path(user_id) if preset.get("stamp_logo") else None
    model = quota["model"] if quota else None
    # /festive: the occasion the user named, else the next celebration in the brand's markets
    occasion, occasion_auto = None, False
    if preset.get("occasion"):
        from services.festival_service import next_celebration
        from services.image_presets import find_occasion

        occasion = find_occasion(user_text)
        if not occasion:
            upcoming = next_celebration((brand.get("compliance_regions") or brand.get("regions_detected") or []))
            if upcoming:
                occasion, occasion_auto = upcoming["name"], True
    plan, plan_usage = None, {"total_tokens": 0, "cost_usd": 0.0}
    if preset.get("carousel"):
        # One text call writes the slide plan (+ caption and hashtags), then every slide is drawn in that style
        from services.brand_profile_service import build_brand_profile_block

        brand_block = build_brand_profile_block(user_id) if DB_AVAILABLE and user_id is not None else ""
        try:
            plan, plan_usage = carousel_service.plan_carousel(user_text, len(outputs), brand_block, product_notes)
        except Exception as plan_err:  # noqa: BLE001
            current_app.logger.warning(f"Carousel plan failed: {plan_err}")
            return jsonify({"success": False, "error": "The carousel could not be planned. Please try again."}), 502
        outputs = carousel_service.outputs_for(plan, carousel_aspect)
        prompts = {o["key"]: carousel_service.slide_prompt(preset["scene"], plan, i, brand, product_notes, bool(product),
                                                           carousel_aspect)
                   for i, o in enumerate(outputs, start=1)}
    elif preset.get("hiring"):
        # One text call reads the job and writes the caption; the design is new each time (services/hiring_service.py)
        from services import hiring_service
        from services.brand_profile_service import build_brand_profile_block

        brand_block = build_brand_profile_block(user_id) if DB_AVAILABLE and user_id is not None else ""
        try:
            job, plan_usage = hiring_service.plan_job(user_text, brand_block)
        except hiring_service.HiringError as err:
            return jsonify({"success": False, "code": "text_required", "error": str(err)}), 400
        except Exception as plan_err:  # noqa: BLE001
            current_app.logger.warning(f"Hiring plan failed: {plan_err}")
            return jsonify({"success": False, "error": "The job post could not be written. Please try again."}), 502
        recent = []
        if DB_AVAILABLE and user_id is not None:
            from db import recent_hiring_designs

            recent = recent_hiring_designs(user_id)
        design = hiring_service.pick_design(recent, has_location=bool(job["location"]))
        plan = {"job": job, "design": design}
        prompts = {o["key"]: hiring_service.image_prompt(preset["scene"], job, design, o["aspect"], brand, bool(product))
                   for o in outputs}
    else:
        prompts = {o["key"]: build_preset_prompt(preset, o, product_notes, brand, user_text, occasion) for o in outputs}

    # At most 5 images at once: a 10-slide carousel shouldn't flood the image gateway
    with ThreadPoolExecutor(max_workers=min(len(outputs), 5) + 1, thread_name_prefix="preset") as pool:
        no_photo_needed = {"require_reference": False} if preset.get("carousel") or preset.get("hiring") else {}
        jobs = {o["key"]: pool.submit(media_service.create_preset_image, prompts[o["key"]], product, o["aspect"], logo_path,
                                      model, **no_photo_needed)
                for o in outputs}
        ad_job = pool.submit(_meta_ad_copy, user_id, product_notes, user_text) if preset.get("ad_copy") else None
        # /hiring: the caption is checked against fair-hiring and the brand's own rules while the images are drawn
        rules_job = pool.submit(_hiring_compliance, user_id, plan["job"]["caption"]) if preset.get("hiring") else None
        results = {key: job.result() for key, job in jobs.items()}
        hiring_check, check_usage = rules_job.result() if rules_job else (None, {})
        ad_copy, ad_usage = ad_job.result() if ad_job else (None, {"total_tokens": 0, "cost_usd": 0.0})

    conversation_id = _conversation_for(user_id, data, f"{preset['label']}: {user_text or 'product photo'}") \
        if DB_AVAILABLE and user_id is not None else None
    images, errors, image_ids, image_cost = [], [], [], 0.0
    for out in outputs:
        r = results[out["key"]]
        if not r.get("success"):
            errors.append(f"{out['label']}: {r.get('error')}")
            continue
        asset_id = None
        if DB_AVAILABLE and user_id is not None:
            from db import log_image_generation

            asset_id = log_image_generation(
                user_id, float(r.get("cost") or 0), run_id=None, kind="image", platform=out["key"],
                model=r.get("model_id") or model, description=f"{preset['label']} ({out['label']})",
                media_url=r["url"], conversation_id=conversation_id, prompt=prompts[out["key"]],
                clean_url=r.get("clean_url"), width=r.get("width"), height=r.get("height"),
            )
            image_ids.append(asset_id)
        image_cost += float(r.get("cost") or 0)
        images.append({"key": out["key"], "label": out["label"], "aspect": out["aspect"], "url": r["url"],
                       "clean_url": r.get("clean_url"), "prompt": prompts[out["key"]], "asset_id": asset_id,
                       "width": r.get("width"), "height": r.get("height"), "model": r.get("model")})
    if not images:
        log_event("preset.run", preset=preset["id"], status="failed", errors=errors[:3])
        return jsonify({"success": False, "error": "The images could not be created: " + "; ".join(errors)}), 502

    # Saved like any message of the conversation: one entry per image (so chat
    # follow-ups can edit it) plus the command's details under "_preset"
    meta = {"id": preset["id"], "label": preset["label"], "icon": preset["icon"], "text": user_text,
            "keys": [i["key"] for i in images], "all_sizes": bool(data.get("all_sizes")),
            "source_image_url": "/static/uploads/" + os.path.basename(product) if product else None}
    if occasion:
        meta["occasion"], meta["occasion_auto"] = occasion, occasion_auto
    if ad_copy:
        meta["ad_copy"] = ad_copy
    if plan and preset.get("hiring"):
        from services.hiring_service import design_names

        job = dict(plan["job"])
        if hiring_check and hiring_check.get("caption"):
            job["caption"] = hiring_check.pop("caption")  # with any fix the check made
        meta["hiring"] = {"caption": job.pop("caption"), "hashtags": job.pop("hashtags"), "job": job,
                          "design": plan["design"], "design_names": design_names(plan["design"]),
                          "compliance": hiring_check}
        ad_usage = {"total_tokens": int(ad_usage.get("total_tokens") or 0) + int(plan_usage.get("total_tokens") or 0)
                    + int((check_usage or {}).get("total_tokens") or 0),
                    "cost_usd": float(ad_usage.get("cost_usd") or 0) + float(plan_usage.get("cost_usd") or 0)
                    + float((check_usage or {}).get("cost_usd") or 0)}
    elif plan:
        made = set(meta["keys"])
        meta["carousel"] = {
            "aspect": carousel_aspect, "caption": plan["caption"], "hashtags": plan["hashtags"], "style": plan["style"],
            "slides": [{"key": f"slide{i}", **s} for i, s in enumerate(plan["slides"], start=1) if f"slide{i}" in made],
        }
        ad_usage = {"total_tokens": int(ad_usage.get("total_tokens") or 0) + int(plan_usage.get("total_tokens") or 0),
                    "cost_usd": float(ad_usage.get("cost_usd") or 0) + float(plan_usage.get("cost_usd") or 0)}
    content: dict[str, typing.Any] = {"_preset": meta}
    for img in images:
        content[img["key"]] = {
            "caption": {"primary_caption": ""}, "hashtags": {"hashtags": []}, "media_prompt": img["prompt"],
            "media": {"image": {k: img[k] for k in ("url", "clean_url", "prompt", "asset_id", "aspect", "width", "height", "model")}},
        }
    if data.get("version_of_run_id"):
        content["_meta"] = {"version_of": data.get("version_of_run_id")}
    total_cost = round(image_cost + float(ad_usage.get("cost_usd") or 0), 6)
    run_id = None
    if DB_AVAILABLE and user_id is not None:
        try:
            from db import attach_images_to_run

            run_id = save_run(story=f"/{preset['id']} {user_text}".strip(), tone="Auto", platforms=preset["platforms"],
                              content=content, user_id=user_id, tokens_used=int(ad_usage.get("total_tokens") or 0),
                              cost_usd=total_cost, conversation_id=conversation_id)
            attach_images_to_run(image_ids, run_id, user_id)  # charged once, as part of the run
        except Exception as db_err:  # noqa: BLE001 - the images are made; unlinked rows stay charged
            current_app.logger.warning(f"Could not save image command run: {db_err}")
    log_event("preset.run", preset=preset["id"], conversation_id=conversation_id, run_id=run_id,
              new_asset_ids=image_ids or None, images=len(images), failed=len(errors) or None,
              model=model, status="completed", latency_ms=int((time.time() - started) * 1000))

    from db import get_image_quota

    return jsonify({
        "success": True, "run_id": run_id, "conversation_id": conversation_id, "preset": meta,
        "images": images, "ad_copy": ad_copy, "errors": errors, "content": content,
        "quota": get_image_quota(user_id) if DB_AVAILABLE and user_id is not None else None,
        "usage": {"total_tokens": int(ad_usage.get("total_tokens") or 0), "cost_usd": total_cost,
                  "media_cost_usd": round(image_cost, 6), "media_count": len(images)},
    })


# ── Conversations (Studio Chat threads) ────────────────────────────────


@api_bp.route("/conversations", methods=["GET"])
@login_required_api
def conversations_list():
    """The sidebar: one entry per conversation, most recent first."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import list_conversations

    limit = min(int(request.args.get("limit", 30)), 100)
    archived = request.args.get("archived", "false").lower() == "true"
    rows = list_conversations(get_current_user_id(), limit=limit, archived=archived)
    return jsonify({"success": True, "conversations": rows, "count": len(rows)})


@api_bp.route("/conversations/<int:conversation_id>", methods=["GET"])
@login_required_api
def conversation_detail(conversation_id):
    """Every message (run) of the conversation, oldest first, to replay it."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import get_conversation

    data = get_conversation(conversation_id, get_current_user_id())
    if not data:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify({"success": True, **data})


def _library_image(i: dict) -> dict:
    return {"id": i["id"], "url": i["url"], "description": (i.get("edit_instruction") or i.get("prompt") or "")[:200],
            "prompt": (i.get("prompt") or "")[:500], "width": i["width"], "height": i["height"],
            "created_at": i["created_at"], "kind": i["kind"], "platform": i.get("platform"),
            "conversation_id": i["conversation_id"], "version": i["version"], "versions": i.get("versions", 1),
            "favorite": bool(i.get("favorite"))}


@api_bp.route("/library/images", methods=["GET"])
@login_required_api
def library_images_route():
    """The image library: one entry per image (its newest version), newest
    first. ?q= searches what the image was asked to show; ?conversation= (an
    id, or "none") keeps one campaign folder; ?favorites=1 the starred ones;
    ?limit= & ?offset= page it."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import list_user_images

    try:
        limit = max(1, min(int(request.args.get("limit", 60)), 200))
        offset = max(0, int(request.args.get("offset", 0)))
        conversation = request.args.get("conversation") or None
        if conversation not in (None, "none"):
            conversation = int(conversation)
    except ValueError:
        return jsonify({"success": False, "error": "limit, offset and conversation must be numbers"}), 400
    rows, total = list_user_images(get_current_user_id(), limit=limit, offset=offset, query=request.args.get("q"),
                                   conversation=conversation, favorites=request.args.get("favorites") == "1")
    images = [_library_image(i) for i in rows]
    return jsonify({"success": True, "images": images, "total": total, "has_more": offset + len(images) < total})


@api_bp.route("/library/folders", methods=["GET"])
@login_required_api
def library_folders_route():
    """The library by campaign: a folder per Studio Chat conversation that made
    images (and one for the rest), newest first. ?q= keeps matching folders."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import list_library_folders

    return jsonify({"success": True, "folders": list_library_folders(get_current_user_id(), request.args.get("q"))})


@api_bp.route("/library/images/<int:image_id>/versions", methods=["GET"])
@login_required_api
def library_image_versions_route(image_id):
    """Every version of an image (the original and its edits), oldest first."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import list_image_versions

    versions = list_image_versions(get_current_user_id(), image_id)
    if not versions:
        return jsonify({"success": False, "error": "Image not found"}), 404
    return jsonify({"success": True, "versions": [_library_image(v) for v in versions]})


@api_bp.route("/library/images/<int:image_id>/favorite", methods=["POST"])
@login_required_api
def library_image_favorite_route(image_id):
    """Body {on: true|false}: star or unstar an image."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import set_image_favorite

    on = bool((request.get_json(silent=True) or {}).get("on", True))
    if set_image_favorite(get_current_user_id(), image_id, on) is None:
        return jsonify({"success": False, "error": "Image not found"}), 404
    return jsonify({"success": True, "favorite": on})


@api_bp.route("/library/images", methods=["DELETE"])
@login_required_api
def library_images_delete_route():
    """?ids=1,2,3: remove several images (and their versions) from the library."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import delete_library_image

    try:
        ids = [int(i) for i in (request.args.get("ids") or "").split(",") if i.strip()][:200]
    except ValueError:
        return jsonify({"success": False, "error": "ids must be numbers"}), 400
    user_id = get_current_user_id()
    return jsonify({"success": True, "removed": sum(delete_library_image(user_id, i) for i in ids)})


@api_bp.route("/library/images/<int:image_id>", methods=["DELETE"])
@login_required_api
def library_image_delete_route(image_id):
    """Remove an image (and its versions) from the library. Posts already using
    it keep working; the page offers Undo before this is sent."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import delete_library_image

    removed = delete_library_image(get_current_user_id(), image_id)
    if not removed:
        return jsonify({"success": False, "error": "Image not found"}), 404
    return jsonify({"success": True, "removed": removed})


@api_bp.route("/briefs", methods=["GET", "POST"])
@login_required_api
def saved_briefs_route():
    """Briefs the user starred to use again. POST {text, platforms} saves one."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import MAX_SAVED_BRIEFS, list_saved_briefs, save_brief

    user_id = get_current_user_id()
    if request.method == "GET":
        return jsonify({"success": True, "briefs": list_saved_briefs(user_id)})
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    if len(text) < 3:
        return jsonify({"success": False, "error": "There is nothing to save."}), 400
    platforms = data.get("platforms") if isinstance(data.get("platforms"), list) else []
    brief, created = save_brief(user_id, text, platforms)
    if not brief:
        return jsonify({"success": False, "error": f"You can keep up to {MAX_SAVED_BRIEFS} saved briefs - remove one first."}), 400
    return jsonify({"success": True, "brief": brief, "created": created, "briefs": list_saved_briefs(user_id)})


@api_bp.route("/briefs/<int:brief_id>", methods=["DELETE"])
@login_required_api
def delete_saved_brief_route(brief_id):
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import delete_saved_brief, list_saved_briefs

    user_id = get_current_user_id()
    if not delete_saved_brief(user_id, brief_id):
        return jsonify({"error": "Saved brief not found"}), 404
    return jsonify({"success": True, "briefs": list_saved_briefs(user_id)})


@api_bp.route("/briefs/<int:brief_id>/use", methods=["POST"])
@login_required_api
def use_saved_brief_route(brief_id):
    """Counts a use, so the briefs used most recently come first."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import mark_brief_used

    if not mark_brief_used(get_current_user_id(), brief_id):
        return jsonify({"error": "Saved brief not found"}), 404
    return jsonify({"success": True})


@api_bp.route("/conversations/<int:conversation_id>/images", methods=["GET"])
@login_required_api
def conversation_images_route(conversation_id):
    """Every image made in the conversation (newest first) - Send for approval
    lets the user pick carousel slides from them."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import conversation_images, get_conversation

    user_id = get_current_user_id()
    if not get_conversation(conversation_id, user_id):
        return jsonify({"error": "Conversation not found"}), 404
    images = [{"id": i["id"], "url": i["url"], "description": (i.get("prompt") or "")[:120], "width": i["width"],
               "height": i["height"], "created_at": i["created_at"]}
              for i in reversed(conversation_images(conversation_id, user_id)) if i.get("url")]
    return jsonify({"success": True, "images": images})


@api_bp.route("/conversations/<int:conversation_id>/archive", methods=["POST"])
@login_required_api
def conversation_archive(conversation_id):
    from db import set_conversation_archived

    if not DB_AVAILABLE or not set_conversation_archived(conversation_id, get_current_user_id(), True):
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify({"success": True, "is_archived": True})


@api_bp.route("/conversations/<int:conversation_id>/unarchive", methods=["POST"])
@login_required_api
def conversation_unarchive(conversation_id):
    from db import set_conversation_archived

    if not DB_AVAILABLE or not set_conversation_archived(conversation_id, get_current_user_id(), False):
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify({"success": True, "is_archived": False})


@api_bp.route("/conversations/<int:conversation_id>/pin", methods=["POST"])
@login_required_api
def conversation_pin(conversation_id):
    from db import set_conversation_pinned

    if not DB_AVAILABLE or not set_conversation_pinned(conversation_id, get_current_user_id(), True):
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify({"success": True, "is_pinned": True})


@api_bp.route("/conversations/<int:conversation_id>/unpin", methods=["POST"])
@login_required_api
def conversation_unpin(conversation_id):
    from db import set_conversation_pinned

    if not DB_AVAILABLE or not set_conversation_pinned(conversation_id, get_current_user_id(), False):
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify({"success": True, "is_pinned": False})


@api_bp.route("/conversations/<int:conversation_id>/active-image", methods=["POST"])
@login_required_api
def conversation_active_image(conversation_id):
    """The user picked an image version ("Refine this version") - "it" and
    "this" now mean that image, also after a refresh."""
    from db import set_active_image

    image_id = (request.get_json(silent=True) or {}).get("image_id")
    try:
        image_id = int(image_id)
    except (TypeError, ValueError):
        return jsonify({"error": "image_id is required"}), 400
    if not DB_AVAILABLE or not set_active_image(conversation_id, get_current_user_id(), image_id):
        return jsonify({"error": "Image not found in this conversation"}), 404
    log_event("image.select", conversation_id=conversation_id, active_asset_id=image_id)
    return jsonify({"success": True, "active_image_id": image_id})


@api_bp.route("/images/<int:image_id>/lineage", methods=["GET"])
@login_required_api
def image_lineage_route(image_id):
    """An image and its earlier versions, original first."""
    from db import image_lineage

    chain = image_lineage(image_id, get_current_user_id()) if DB_AVAILABLE else []
    if not chain:
        return jsonify({"error": "Image not found"}), 404
    return jsonify({"success": True, "lineage": chain})


@api_bp.route("/history", methods=["GET"])
@login_required_api
def list_history():
    """Return the last N generation runs from PostgreSQL"""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    try:
        limit = min(int(request.args.get("limit", 20)), 100)
        include_archived = request.args.get("archived", "false").lower() == "true"
        rows = get_history(limit=limit, user_id=get_current_user_id(), include_archived=include_archived)
        return jsonify({"success": True, "history": rows, "count": len(rows)})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@api_bp.route("/history/<int:run_id>/archive", methods=["POST"])
@login_required_api
def archive_history_run(run_id):
    """Archive a single generation run"""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    try:
        success = archive_run(run_id, user_id=get_current_user_id())
        if not success:
            return jsonify({"error": "Run not found or access denied"}), 404
        return jsonify({"success": True, "run_id": run_id, "is_archived": True})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@api_bp.route("/history/<int:run_id>/unarchive", methods=["POST"])
@login_required_api
def unarchive_history_run(run_id):
    """Unarchive a single generation run"""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    try:
        success = unarchive_run(run_id, user_id=get_current_user_id())
        if not success:
            return jsonify({"error": "Run not found or access denied"}), 404
        return jsonify({"success": True, "run_id": run_id, "is_archived": False})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@api_bp.route("/history/<int:run_id>/edit", methods=["POST"])
@login_required_api
def edit_history_run(run_id):
    """A hand edit of a post's caption / hashtags (Studio Chat "Edit") - no
    generation, no cost. Body: {target: <platform> | "carousel", caption?,
    hashtags?} or {target, restore: true} to go back to the AI's text."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import update_run_content
    from services.post_edit_service import EditError, apply_edit

    data = request.get_json(silent=True) or {}
    target = str(data.get("target") or "").strip().lower()
    user_id = get_current_user_id()
    run = get_run_by_id(run_id, user_id=user_id)
    if not run:
        return jsonify({"error": "Post not found"}), 404
    content = run["content"]
    try:
        node = apply_edit(content, target, caption=data.get("caption"), hashtags=data.get("hashtags"),
                          restore=bool(data.get("restore")))
    except EditError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    update_run_content(run_id, content, user_id=user_id)
    log_event("post.edit", run_id=run_id, target=target, restored=bool(data.get("restore")),
              fields=[k for k in ("caption", "hashtags") if data.get(k) is not None])
    return jsonify({"success": True, "run_id": run_id, "target": target, "node": node})


@api_bp.route("/history/<int:run_id>/openings", methods=["POST"])
@login_required_api
@limit_by_user("ai_user")
def post_openings(run_id):
    """Other first lines for a post's caption (Studio Chat "Edit" -> "Other
    openings"): one small AI call, only when asked, charged to the post's
    run. Nothing is saved - the person picks one in the editor and saves it
    like any hand edit. Body: {caption, platform} (services/hook_service.py)."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import add_run_cost
    from services import hook_service

    user_id = get_current_user_id()
    if not get_run_by_id(run_id, user_id=user_id):
        return jsonify({"error": "Post not found"}), 404
    try:
        if _credit_blocked(get_user_usage_stats(user_id)):
            return jsonify({"error": "Credit limit reached. Please request a credit extension.", "credit_limit_exceeded": True}), 402
    except Exception as cred_err:  # noqa: BLE001
        current_app.logger.warning(f"[Credits] Check error: {cred_err}")
    data = request.get_json(silent=True) or {}
    caption = str(data.get("caption") or "")
    platform = str(data.get("platform") or "").lower()[:20]
    try:
        options, usage = hook_service.other_openings(caption, platform)
    except hook_service.OpeningError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    except Exception as err:  # noqa: BLE001
        current_app.logger.warning(f"[Openings] Could not write openings: {err}")
        return jsonify({"success": False, "error": "Couldn't write other openings right now. Please try again."}), 502
    add_run_cost(run_id, float(usage.get("cost_usd") or 0.0), user_id)
    log_event("post.openings", run_id=run_id, platform=platform, count=len(options))
    return jsonify({"success": True, "opening": hook_service.opening_of(caption), "options": options})


@api_bp.route("/history/<int:run_id>", methods=["GET"])
@login_required_api
def get_history_run(run_id):
    """Return full details for a single run"""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    try:
        row = get_run_by_id(run_id, user_id=get_current_user_id())
        if not row:
            return jsonify({"error": "Run not found"}), 404
        return jsonify({"success": True, "run": row})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@api_bp.route("/generate/<platform>", methods=["POST"])
@login_required_api
@limit_by_user("ai_user")
def generate_for_platform(platform):
    """Generate content for a specific platform"""
    if platform not in ["facebook", "instagram", "linkedin"]:
        return jsonify({"error": "Invalid platform. Use: facebook, instagram, linkedin"}), 400

    data = request.get_json()
    if not data or "story" not in data:
        return jsonify({"error": "Story text is required"}), 400

    try:
        story_analysis = story_agent.analyze(data["story"])
        vision_analysis = None
        if data.get("image_path"):
            vision_analysis = vision_agent.analyze_image(data["image_path"])

        caption = caption_agent.generate_caption(platform, story_analysis, vision_analysis, data.get("tone"))
        hashtags = hashtag_agent.generate_hashtags(platform, story_analysis, vision_analysis)
        strategy = strategy_agent.create_strategy(platform, story_analysis)

        return jsonify(
            {"success": True, "platform": platform, "caption": caption, "hashtags": hashtags, "strategy": strategy}
        )

    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@api_bp.route("/approve-asset", methods=["POST"])
@login_required_api
def approve_asset():
    """Save an approved asset to the database"""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503

    data = request.get_json()
    if not data or "platform" not in data or "content" not in data:
        return jsonify({"error": "Platform and content are required"}), 400

    try:
        user_id = get_current_user_id()
        if user_id is None:
            return jsonify({"error": "User not authenticated"}), 401

        content_type = data.get("type", "text")
        content_data = json.dumps(data["content"]) if isinstance(data["content"], dict) else data["content"]

        asset_id = save_approved_asset(
            user_id=user_id, platform=data["platform"], content_type=content_type, content_data=content_data
        )

        return jsonify({"success": True, "asset_id": asset_id})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@api_bp.route("/publish-pipeline-asset", methods=["POST"])
@login_required_api
def publish_pipeline_asset():
    """Publish an approved competitor-dashboard pipeline asset (text/image/video)
    to the current user's connected social media accounts, reusing the same
    SocialPublisherService the scheduler and manual-post flows already use."""
    if not publisher_service:
        return jsonify({"error": "Social publisher service not available"}), 503

    data = request.get_json() or {}
    platform = (data.get("platform") or "").lower().strip()
    asset_type = (data.get("type") or "").lower()
    content = data.get("content")
    caption = data.get("caption")

    if not platform:
        return jsonify({"error": "platform is required"}), 400
    if not content:
        return jsonify({"error": "content is required"}), 400

    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({"error": "Unauthorized"}), 401

    is_media = "image" in asset_type or "video" in asset_type
    message = (caption or content) if is_media else content

    abs_media_path = None
    if is_media:
        rel_path = str(content).lstrip("/").replace("/", os.sep)
        candidate = os.path.join(current_app.root_path, rel_path)
        if not os.path.exists(candidate):
            candidate = storage_service.ensure_local(str(content))
        if not candidate:
            return jsonify({"error": "Generated media file could not be located on the server"}), 404
        abs_media_path = candidate

    try:
        results = publisher_service.publish_post_to_connected_accounts(
            user_id=user_id, platforms=[platform], caption=message, image_path=abs_media_path
        )
        plat_result = results.get(platform, {"success": False, "error": "No result returned"})
        return jsonify({"success": bool(plat_result.get("success")), "result": plat_result})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@api_bp.route("/schedule", methods=["POST"])
@login_required_api
def schedule_campaign():
    """Create a multi-platform posting schedule"""
    data = request.get_json()
    if not data or "story" not in data:
        return jsonify({"error": "Story text is required"}), 400

    platforms = data.get("platforms", ["facebook", "instagram", "linkedin"])

    try:
        story_analysis = story_agent.analyze(data["story"])
        schedule = strategy_agent.schedule_posts(platforms, story_analysis)

        return jsonify({"success": True, "schedule": schedule})

    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


# ── Media Generation Endpoint ──────────────────────────────────────────────


@api_bp.route("/generate-media", methods=["POST"])
@login_required_api
@limit_by_user("media_user")
def generate_media():
    """
    Generate image or video for a given platform and caption.
    """
    if not MEDIA_AVAILABLE:
        return jsonify({"error": "Media generation service not available"}), 503

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    platform = data.get("platform", "instagram")
    caption = data.get("caption", "")
    media_type = data.get("media_type", "image")
    tone = data.get("tone")
    run_id = data.get("run_id")
    # image_paths (plural) lets the caller supply more than one reference image
    # (e.g. the Aiden character AND the StradIT logo together) - falls back to
    # the older singular image_path for callers that only pass one. image_path
    # stays a single string (used for video gen / source_image_url, which only
    # support one reference); image_path is a str or list[str] only where
    # generate_image's kie.ai path can use multiple references.
    # The logo is never an AI reference image - the model would redraw it
    # (wrong spelling/colors); the real file is stamped on afterwards instead
    # (brand_logo_service). Drop it from the references here.
    image_paths = [p for p in (data.get("image_paths") or []) if p and not _is_logo_asset(p)]
    image_path = data.get("image_path")
    if image_path and _is_logo_asset(image_path):
        image_path = None
    image_path = image_path or (image_paths[0] if image_paths else None)
    image_path_for_gen = image_paths if len(image_paths) > 1 else image_path
    user_id = get_current_user_id()
    from services.brand_logo_service import resolve_logo_path

    logo_path = resolve_logo_path(user_id)

    # Credit Limit Check
    if DB_AVAILABLE and user_id:
        try:
            stats = get_user_usage_stats(user_id)
            if _credit_blocked(stats):
                limit_val = stats.get("credit_limit", Config.DEFAULT_CREDIT_USD)
                return (
                    jsonify(
                        {
                            "error": f"Credit limit reached ({format_money(limit_val, stats.get('currency', 'USD'))}). Please request a credit extension from admin.",
                            "credit_limit_exceeded": True,
                            "credit_limit": limit_val,
                            "used_credits": stats.get("used_credits", 0.0),
                            "remaining_credits": 0.0,
                            "has_pending_request": stats.get("has_pending_request", False),
                        }
                    ),
                    402,
                )
        except Exception as _cred_err:
            current_app.logger.warning(f"[Credits] Check error: {_cred_err}")

    if not caption:
        return jsonify({"error": "caption is required"}), 400
    if platform not in ["facebook", "instagram", "linkedin"]:
        return jsonify({"error": "Invalid platform"}), 400

    if not image_path and run_id and DB_AVAILABLE:
        try:
            run = get_run_by_id(run_id, user_id=user_id)
            image_path = (run or {}).get("content", {}).get("_meta", {}).get("image_path")
            image_path_for_gen = image_path_for_gen or image_path
        except Exception:
            image_path = None

    if run_id and DB_AVAILABLE:
        run = get_run_by_id(run_id, user_id=user_id)
        if not run:
            return jsonify({"error": "Run not found"}), 404

    # The strategy step's own image_prompt/video_prompt (art direction, scene
    # breakdown, "no office/dashboard" rules for festive content, etc.) is
    # the actual authoritative description - when the caller has it, use it
    # instead of the much shorter social caption, which was never meant to
    # double as a media-generation prompt and was silently dropping all of
    # that direction.
    image_prompt = (data.get("image_prompt") or "").strip()
    video_prompt = (data.get("video_prompt") or "").strip()
    # Other platforms of the same post that reuse this image (one square image
    # instead of one per platform - see app_v2.js triggerMediaGenInChat)
    shared_platforms = [
        p
        for p in (data.get("share_with_platforms") or [])
        if p in ("facebook", "instagram", "linkedin") and p != platform
    ]

    # Daily image limit + the user's image model (Admin -> Image access)
    quota = None
    if media_type == "video":
        _, blocked = _video_quota_or_block(user_id)
    else:
        quota, blocked = _image_quota_or_block(user_id)
    if blocked:
        return blocked

    def _work() -> dict:
        """The render itself: run here, or in the background (services/media_jobs.py)."""
        nonlocal tone  # the request's "context" can replace it below
        caption_to_use = extract_prompt_for_type(caption, media_type)
        if media_type == "video":
            if video_prompt:
                caption_to_use = video_prompt
            result = media_service.generate_video(
                caption_to_use, platform, tone, image_path=image_path, logo_path=logo_path
            )
        else:
            ai_model = data.get("ai_model", "kie")
            # If the client sent context, use it as tone to guide the style
            if "context" in data and data["context"]:
                tone = data["context"]
            if image_prompt:
                caption_to_use = image_prompt
            result = media_service.generate_image(
                caption_to_use,
                platform,
                tone,
                image_path=image_path_for_gen,
                ai_model=ai_model,
                logo_path=logo_path,
                square=bool(shared_platforms),
                model=quota["model"] if quota else None,
            )
            if shared_platforms and result.get("success"):
                result["shared_platforms"] = shared_platforms

        if image_path:
            result["source_image_url"] = _public_upload_url(image_path)
        if user_id is not None and result.get("success") and DB_AVAILABLE:
            # Log it: counts toward today's image limit; without a run (e.g.
            # Analysis Dashboard) this row is also the charge for it. Its id is
            # the image's asset id - saved with the post, so an edit knows
            # which image it starts from (image versions).
            try:
                from db import get_image_quota, log_image_generation

                width, height = _size_wh(result.get("size") or result.get("resolution"))
                result["asset_id"] = log_image_generation(
                    user_id,
                    float(result.get("cost") or 0),
                    run_id=run_id or None,
                    kind="video" if media_type == "video" else "image",
                    platform=platform,
                    model=result.get("model_id") or result.get("model") or (quota["model"] if quota else None),
                    description=(image_prompt or video_prompt or caption)[:300],
                    media_url=result.get("url"),
                    prompt=caption_to_use,
                    clean_url=result.get("clean_url"),
                    width=width,
                    height=height,
                )
                if media_type == "video":
                    from db import get_video_quota

                    result["video_quota"] = get_video_quota(user_id)
                else:
                    result["quota"] = get_image_quota(user_id)
                log_event(
                    "image.generate" if media_type != "video" else "video.generate",
                    run_id=run_id, asset_id=result["asset_id"], platform=platform, provider="heyroute",
                    model=result.get("model_id") or result.get("model"), status="completed",
                    context_token_estimate=estimate_tokens(caption_to_use),
                )
            except Exception as cost_err:
                current_app.logger.warning(f"[Credits] Could not log media generation: {cost_err}")
        if run_id:
            result["run_id"] = run_id
            if user_id is not None:
                # One image, saved for every platform of the post that uses it
                for target in [platform] + list(result.get("shared_platforms") or []):
                    _persist_generated_media(run_id, target, media_type, result, user_id)
                if result.get("success") and DB_AVAILABLE:
                    try:
                        add_run_cost(run_id, float(result.get("cost") or 0), user_id)
                    except Exception as cost_err:
                        current_app.logger.warning(f"[Credits] Could not record media cost: {cost_err}")
        if not result.get("success"):
            log_event("video.generate" if media_type == "video" else "image.generate", run_id=run_id,
                      platform=platform, status="failed", error=str(result.get("error"))[:200])

        return result

    # Studio Chat asks for the background: the request returns at once with a
    # job id, and the page checks /api/media-jobs/<id> until it is done
    if data.get("background") and DB_AVAILABLE and user_id is not None:
        from services import media_jobs

        try:
            job_id = media_jobs.start(user_id, media_type, platform, _work, current_app._get_current_object())
        except media_jobs.TooManyJobs as err:
            return jsonify({"success": False, "error": str(err), "code": "too_many_jobs"}), 429
        return jsonify({"success": True, "job_id": job_id, "status": "running"}), 202

    try:
        return jsonify(_work())
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


@api_bp.route("/media-jobs/<job_id>", methods=["GET"])
@login_required_api
def media_job_status(job_id):
    """An image or video being made in the background: {status, result, error}."""
    from services import media_jobs

    job = media_jobs.get(get_current_user_id(), job_id) if DB_AVAILABLE else None
    if not job:
        return jsonify({"success": False, "error": "Not found"}), 404
    return jsonify({"success": True, **job})


@api_bp.route("/send-approval-email", methods=["POST"])
@login_required_api
@limit_by_user("email_user")
def send_approval_email():
    """Sends an HTML notification email (with the generated asset attached,
    if it's an image) when a piece of content is approved on the Analysis
    Dashboard. Fails soft with an error payload rather than a 500 if SMTP
    isn't configured, since approval itself should still succeed either way."""
    locked = _feature_locked(get_current_user_id(), "approvals", "Approval emails")
    if locked:
        return locked
    data = request.get_json(silent=True) or {}

    try:
        from services.email_service import EmailService

        is_image = (data.get("asset_type") or "").lower() == "image"
        image_urls = data.get("image_urls") if is_image else None
        image_path = data.get("image_url") if (is_image and not image_urls) else None

        email_service = EmailService()
        result = email_service.send_approval_notification(
            story=data.get("story"),
            platform=data.get("platform"),
            competitors=data.get("competitors"),
            caption=data.get("caption"),
            asset_type=data.get("asset_type"),
            image_path=image_path,
            image_paths=image_urls,
            slide_titles=data.get("slide_titles"),
        )
        return jsonify(result)
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": str(e)}), 500


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _approval_viewer(user_id: int | None) -> tuple[int | None, str | None]:
    """(owner filter, reviewer email) for approval queries. Admins see every
    request -> (None, None); everyone else sees requests they created plus
    those sent to their email as reviewer."""
    user = get_user_by_id(user_id) if user_id else None
    if user and getattr(user, "is_admin", False):
        return None, None
    return (user_id if user_id else -1), ((user.email or "").lower() if user else None)


def _can_access_approval(req: dict | None, user_id: int | None) -> bool:
    if not req:
        return False
    owner_filter, email = _approval_viewer(user_id)
    return owner_filter is None or req.get("user_id") == user_id or (bool(email) and req.get("reviewer_email") == email)


def _reviewer_email_for(user_id: int | None) -> str | None:
    """Where a user's approval requests go: their own setting (Brand
    Configuration pages), else the deployment-wide APPROVAL_NOTIFY_EMAIL."""
    user = get_user_by_id(user_id) if user_id else None
    return (getattr(user, "approval_reviewer_email", None) if user else None) or Config.APPROVAL_NOTIFY_EMAIL


@api_bp.route("/approval-settings", methods=["GET"])
@login_required_api
def get_approval_settings():
    """The current user's approval reviewer email (see _reviewer_email_for)."""
    user = get_user_by_id(get_current_user_id())
    return jsonify(
        {
            "success": True,
            "reviewer_email": getattr(user, "approval_reviewer_email", None) if user else None,
            "default_reviewer_email": Config.APPROVAL_NOTIFY_EMAIL or None,
        }
    )


@api_bp.route("/approval-settings", methods=["PUT"])
@login_required_api
def update_approval_settings():
    """Sets (or clears, with an empty value) who this user's "Send for
    Approval" requests are emailed to."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    email = ((request.get_json(silent=True) or {}).get("reviewer_email") or "").strip()
    if email and not _EMAIL_RE.match(email):
        return jsonify({"error": "Please enter a valid email address"}), 400

    from db import set_approval_reviewer_email

    set_approval_reviewer_email(get_current_user_id(), email or None)
    return jsonify({"success": True, "reviewer_email": email.lower() or None})


@api_bp.route("/approval-requests", methods=["GET"])
@login_required_api
def list_approval_requests_route():
    """Approval requests (past and current) - powers the /approve list
    dashboard. Each user sees only requests they created or were sent as
    reviewer; admins see all. Optional ?status=pending|approved|rejected."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    status = request.args.get("status")
    owner_filter, email = _approval_viewer(get_current_user_id())
    requests_list = list_approval_requests(status=status, user_id=owner_filter, email=email)
    return jsonify({"success": True, "requests": requests_list})


@api_bp.route("/approval-requests", methods=["POST"])
@login_required_api
@limit_by_user("email_user")
def create_approval_request_route():
    """Creates a review request for a pipeline's generated content and emails
    the reviewer a link to open it in the dashboard (sign-in required) - this
    replaces approving directly in-app for the live Asset Review stage, so an
    external reviewer's decision + comments become the record of truth."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    locked = _feature_locked(get_current_user_id(), "approvals", "Sending posts for approval")
    if locked:
        return locked

    data = request.get_json(silent=True) or {}
    pipeline_client_id = str(data.get("pipeline_client_id") or "").strip()
    if not pipeline_client_id:
        return jsonify({"error": "pipeline_client_id is required"}), 400

    user_id = get_current_user_id()
    is_image = (data.get("asset_type") or "").lower() == "image"
    image_urls = [u for u in (data.get("image_urls") or []) if u] if is_image else []

    reviewer_email = _reviewer_email_for(user_id)
    if not reviewer_email:
        return (
            jsonify({"error": "No approval reviewer set - add a reviewer email in Brand Configuration first."}),
            400,
        )
    if data.get("items") is not None:
        return _create_bundle_approval(data, pipeline_client_id, user_id, reviewer_email)

    # Compliance review for the reviewer (requester's active rules) - the
    # caption itself is stored as submitted; the fix is only suggested.
    compliance = None
    rules = active_rules_for_user(user_id)
    if rules and data.get("caption"):
        try:
            result, _usage = check_caption(data["caption"], data.get("platform") or "", rules, refine_llm)
            if result:
                result["suggested_caption"] = result.pop("caption")
                compliance = result
        except Exception as c_err:
            current_app.logger.warning(f"[Compliance] Approval check failed: {c_err}")

    try:
        req = create_approval_request(
            compliance=compliance,
            reviewer_email=reviewer_email,
            user_id=user_id,
            pipeline_client_id=pipeline_client_id,
            platform=data.get("platform") or "",
            asset_type=data.get("asset_type") or "",
            caption=data.get("caption"),
            story_context=data.get("story"),
            competitors=data.get("competitors"),
            image_urls=image_urls,
        )

        base_url = (Config.APP_BASE_URL or request.host_url).rstrip("/")
        approval_url = f"{base_url}/approve/{req['id']}"

        email_result = {"success": False, "error": "SMTP not configured"}
        try:
            from services.email_service import EmailService

            email_service = EmailService()
            email_result = email_service.send_approval_request(
                approval_url=approval_url,
                story=req["story_context"],
                platform=req["platform"],
                competitors=req["competitors"],
                caption=req["caption"],
                asset_type=req["asset_type"],
                image_paths=req["image_urls"],
                to_email=reviewer_email,
            )
        except Exception as email_err:
            email_result = {"success": False, "error": str(email_err)}

        return jsonify({"success": True, "request": req, "approval_url": approval_url, "email": email_result})
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": str(e)}), 500


def _requester_company(req: dict) -> str | None:
    """The requester's company name, for the post previews."""
    if not req or not req.get("user_id"):
        return None
    try:
        from db import get_user_brand_profile

        return (get_user_brand_profile(req["user_id"]) or {}).get("company_name") or None
    except Exception:  # noqa: BLE001
        return None


def _create_bundle_approval(data: dict, pipeline_client_id: str, user_id, reviewer_email: str):
    """A whole post (one item per platform) in one request and one email."""
    from services.approval_bundle_service import (
        BundleError,
        normalize_items,
        prepare_items,
        summary,
    )

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    try:
        items = normalize_items(data.get("items"), upload_folder)
    except BundleError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    rules = active_rules_for_user(user_id)

    def _check(caption, platform, active_rules):
        result, _usage = check_caption(caption, platform, active_rules, refine_llm)
        return result

    items = prepare_items(items, upload_folder, rules, _check)
    totals = summary(items)
    story = (data.get("story") or "").strip()[:4000] or None
    try:
        req = create_approval_request(
            user_id=user_id, pipeline_client_id=pipeline_client_id, platform=totals["platform"],
            asset_type=totals["asset_type"], caption=totals["caption"], story_context=story,
            image_urls=totals["image_urls"], reviewer_email=reviewer_email, items=items,
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": str(e)}), 500
    base_url = (Config.APP_BASE_URL or request.host_url).rstrip("/")
    approval_url = f"{base_url}/approve/{req['id']}"
    email_result = {"success": False, "error": "SMTP not configured"}
    try:
        from services.email_service import EmailService

        email_result = EmailService().send_approval_bundle_request(
            approval_url=approval_url, story=story, items=items, company=_requester_company(req), to_email=reviewer_email,
        )
    except Exception as email_err:  # noqa: BLE001 - the request exists; the reviewer can still open it from /approve
        email_result = {"success": False, "error": str(email_err)}
    log_event("approval.requested", request_id=req["id"], platforms=[i["platform"] for i in items],
              carousels=sum(1 for i in items if i["media"] == "carousel") or None, emailed=email_result.get("success"))
    return jsonify({"success": True, "request": req, "approval_url": approval_url, "email": email_result})


@api_bp.route("/approval-requests/<int:request_id>", methods=["GET"])
@login_required_api
def get_approval_request_route(request_id):
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    req = get_approval_request(request_id)
    # Same 404 for "not yours" as for "doesn't exist" - no probing other users' request ids
    if not _can_access_approval(req, get_current_user_id()):
        return jsonify({"error": "Approval request not found"}), 404
    return jsonify({"success": True, "request": {**req, "company_name": _requester_company(req),
                                                 "publishing": _publishing_for(req, get_current_user_id())}})


def _publishing_for(req: dict, user_id) -> dict:
    """What the viewer can publish from this request: only its requester, to
    the platforms whose account is connected. accounts: {platform: name or None}."""
    if not req or not req.get("items") or req.get("user_id") != user_id:
        return {"can_publish": False}
    from services.publish_service import connections

    accounts = connections(user_id)
    return {"can_publish": True, "instagram": accounts.get("instagram"), "accounts": accounts}


@api_bp.route("/approval-requests/<int:request_id>/publish", methods=["POST"])
@login_required_api
def publish_approved_item(request_id):
    """Publishes one approved platform of a multi-platform request, now."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import update_approval_item
    from services.publish_service import NAMES, PLATFORMS, PublishError, publish_item

    user_id = get_current_user_id()
    req = get_approval_request(request_id)
    if not req or req.get("user_id") != user_id or not req.get("items"):
        return jsonify({"error": "Approval request not found"}), 404
    platform = ((request.get_json(silent=True) or {}).get("platform") or "").lower()
    index = next((i for i, item in enumerate(req["items"]) if item["platform"] == platform), None)
    if index is None:
        return jsonify({"error": "This request has nothing for that platform."}), 400
    item = req["items"][index]
    if platform not in PLATFORMS:
        return jsonify({"error": f"Publishing to {NAMES.get(platform, platform)} isn't available yet."}), 400
    if item.get("decision") != "approved":
        return jsonify({"error": "Only approved posts can be published."}), 409
    if item.get("published"):
        return jsonify({"error": "Already published.", "request": req}), 409
    with _publish_lock(request_id):
        if (get_approval_request(request_id)["items"][index]).get("published"):  # a second click waited here
            return jsonify({"error": "Already published."}), 409
        try:
            result = publish_item(user_id, item, current_app.config["UPLOAD_FOLDER"])
        except PublishError as err:
            if err.unreachable:  # nothing was decided by the platform: not a failure of this post
                return jsonify({"success": False, "error": str(err)}), 502
            update_approval_item(request_id, index, {"publish_error": str(err)})
            log_event(f"{platform}.publish", request_id=request_id, status="failed", error=str(err)[:200])
            return jsonify({"success": False, "error": str(err)}), 400
        from datetime import datetime, timezone

        from db import register_published_post  # so its results can be fetched (services/metrics_service.py)

        register_published_post(user_id, platform, result.get("post_id"), result.get("permalink"), item.get("caption"),
                                "approval", request_id)
        updated = update_approval_item(request_id, index, {
            "published": {"at": datetime.now(timezone.utc).isoformat(), "permalink": result.get("permalink"),
                          "media_id": result.get("post_id"), "post_id": result.get("post_id"), "account": result.get("account"),
                          "format": result.get("format")},
            "publish_error": None,
        })
    log_event(f"{platform}.publish", request_id=request_id, status="published", media_id=result.get("post_id"),
              carousel=item.get("media") == "carousel", format=result.get("format"))
    return jsonify({"success": True, "result": {**result, "carousel": item.get("media") == "carousel"},
                    "request": {**updated, "company_name": _requester_company(updated), "publishing": _publishing_for(updated, user_id)}})


_publish_locks: dict[int, threading.Lock] = {}
_publish_locks_guard = threading.Lock()


def _publish_lock(request_id: int) -> threading.Lock:
    """One publish at a time per request (a double click must not post twice)."""
    with _publish_locks_guard:
        return _publish_locks.setdefault(request_id, threading.Lock())


@api_bp.route("/approval-requests/by-pipeline/<pipeline_client_id>", methods=["GET"])
@login_required_api
def get_approval_request_by_pipeline_route(pipeline_client_id):
    """Latest approval request for a pipeline - used by the dashboard's
    "Approval" pipeline stage to show pending/accepted/rejected + comments."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    owner_filter, email = _approval_viewer(get_current_user_id())
    req = get_latest_approval_request_for_pipeline(pipeline_client_id, user_id=owner_filter, email=email)
    return jsonify({"success": True, "request": req})


@api_bp.route("/approval-requests/<int:request_id>/decide", methods=["POST"])
@login_required_api
def decide_approval_request_route(request_id):
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503

    data = request.get_json(silent=True) or {}
    decision = (data.get("decision") or "").lower() or None
    item_decisions = data.get("items") if isinstance(data.get("items"), dict) else None
    if decision not in ("approved", "rejected", None) or (decision is None and not item_decisions):
        return jsonify({"error": "decision must be 'approved' or 'rejected'"}), 400

    user_id = get_current_user_id()
    existing = get_approval_request(request_id)
    if not _can_access_approval(existing, user_id):
        return jsonify({"error": "Approval request not found"}), 404
    if existing.get("status") != "pending":
        return jsonify({"error": "This request has already been decided."}), 409
    user = get_user_by_id(user_id) if user_id else None
    decided_by = (user.name or user.email) if user else None

    try:
        req = decide_approval_request(
            request_id=request_id, decision=decision, comments=data.get("comments"), decided_by=decided_by,
            item_decisions=item_decisions,
        )
    except ValueError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": str(e)}), 500
    if not req:
        return jsonify({"error": "Approval request not found"}), 404
    notified = False
    if req.get("items") and req.get("user_id") and req["user_id"] != user_id:
        requester = get_user_by_id(req["user_id"])
        if requester and requester.email:
            try:
                from services.email_service import EmailService

                base_url = (Config.APP_BASE_URL or request.host_url).rstrip("/")
                EmailService().send_approval_decision_email(
                    to_email=requester.email, name=requester.name or "there", status=req["status"], items=req["items"],
                    comments=req.get("comments"), decided_by=decided_by, review_url=f"{base_url}/approve/{req['id']}",
                )
                notified = True
            except Exception as email_err:  # noqa: BLE001 - the decision is saved either way
                current_app.logger.warning(f"Approval decision email failed: {email_err}")
    log_event("approval.decided", request_id=request_id, status=req["status"], requester_emailed=notified or None)
    autopilot = None
    try:  # an Autopilot post: the approved platforms are scheduled at its day's slot
        from services.autopilot_service import on_approval_decided

        autopilot = on_approval_decided(req)
    except Exception as ap_err:  # noqa: BLE001 - the decision is saved either way
        current_app.logger.warning(f"Autopilot scheduling after approval failed: {ap_err}")
    return jsonify({"success": True, "request": {**req, "company_name": _requester_company(req)}, "requester_notified": notified,
                    "autopilot": autopilot})


# ── Editable App Settings (Content Guidelines, Products & Service) ─────────
# User-editable text stored in the DB (see AppSetting) and read live by
# generation, instead of being hardcoded in source. Restricted to a known
# allowlist of keys rather than accepting an arbitrary settings key.
SETTINGS_DEFAULTS = {
    "content_guidelines": None,  # None = fall back to DEFAULT_CONTENT_GUIDELINES (structured JSON, as a string)
    "products_services": None,  # None = fall back to StradITService's built-in SERVICES_CONTEXT
}


@api_bp.route("/settings/<key>", methods=["GET"])
@login_required_api
def get_app_setting_route(key):
    if key not in SETTINGS_DEFAULTS:
        return jsonify({"error": f"Unknown setting '{key}'"}), 404
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503

    default = SETTINGS_DEFAULTS[key]
    if default is None and key == "products_services":
        from services.stradit_service import SERVICES_CONTEXT

        default = SERVICES_CONTEXT
    elif default is None and key == "content_guidelines":
        from services.stradit_service import DEFAULT_CONTENT_GUIDELINES

        default = json.dumps(DEFAULT_CONTENT_GUIDELINES)

    # An explicitly-saved blank value falls back to the default too - for these
    # settings a blank string isn't a meaningful "cleared" state, it's just
    # nothing to render, so treat it the same as never having been saved.
    default_str = default if default is not None else ""
    value = get_setting(key, default=default_str) or default_str
    return jsonify({"success": True, "key": key, "value": value})


@api_bp.route("/settings/<key>", methods=["POST"])
@login_required_api
def save_app_setting_route(key):
    if key not in SETTINGS_DEFAULTS:
        return jsonify({"error": f"Unknown setting '{key}'"}), 404
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503

    data = request.get_json(silent=True) or {}
    value = data.get("value", "")
    try:
        result = save_setting(key, value)
        return jsonify({"success": True, "setting": result})
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": str(e)}), 500


# ── Brand Assets (Logo / Character reference images) ───────────────────────
# An extensible list (see db.BrandAsset), not a fixed pair - dashboard.js's
# Character Setup checkboxes are populated from GET /api/brand-assets, so
# anything added/removed here shows up there automatically.
def _brand_asset_key_from_label(label: str) -> str:
    base_key = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or uuid.uuid4().hex[:8]
    key = base_key
    suffix = 1
    while get_brand_asset(key):
        suffix += 1
        key = f"{base_key}-{suffix}"
    return key


@api_bp.route("/brand-assets", methods=["GET"])
@login_required_api
def list_brand_assets_route():
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    return jsonify({"success": True, "assets": list_brand_assets()})


@api_bp.route("/brand-assets", methods=["POST"])
@login_required_api
def create_brand_asset_route():
    """Registers a brand new character/logo asset (label + image) - distinct
    from POST /brand-assets/<key> below, which replaces an existing one."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503

    label = (request.form.get("label") or "").strip()
    if not label:
        return jsonify({"error": "Label is required"}), 400
    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400
    file = request.files["image"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    if not allowed_file(file.filename) or not is_real_image(file):
        return jsonify({"error": "Invalid file type"}), 400

    key = _brand_asset_key_from_label(label)
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{key}.{ext}"

    brand_dir = os.path.join(current_app.root_path, "static", "img", "brand")
    os.makedirs(brand_dir, exist_ok=True)
    file.save(os.path.join(brand_dir, filename))

    asset = create_brand_asset(key=key, label=label, filename=filename)
    return jsonify({"success": True, "asset": asset})


@api_bp.route("/brand-assets/<key>", methods=["POST"])
@login_required_api
def upload_brand_asset_route(key):
    """Replaces an existing asset's image (label/key unchanged)."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503

    asset = get_brand_asset(key)
    if not asset:
        return jsonify({"error": f"Unknown brand asset '{key}'"}), 404

    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400
    file = request.files["image"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    if not allowed_file(file.filename) or not is_real_image(file):
        return jsonify({"error": "Invalid file type"}), 400

    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{key}.{ext}"
    brand_dir = os.path.join(current_app.root_path, "static", "img", "brand")
    os.makedirs(brand_dir, exist_ok=True)
    file.save(os.path.join(brand_dir, filename))

    # Clean up an old file left behind if the extension changed.
    if filename != asset["filename"]:
        try:
            os.remove(os.path.join(brand_dir, asset["filename"]))
        except OSError:
            pass

    updated = update_brand_asset_filename(key, filename)
    if not updated:
        return jsonify({"error": "Failed to update asset, it may have been deleted."}), 404

    return jsonify({"success": True, "asset": updated, "url": f"{updated['url']}?v={uuid.uuid4().hex[:8]}"})


@api_bp.route("/brand-assets/<key>", methods=["DELETE"])
@login_required_api
def delete_brand_asset_route(key):
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503

    asset = get_brand_asset(key)
    if not asset:
        return jsonify({"error": f"Unknown brand asset '{key}'"}), 404

    delete_brand_asset(key)
    try:
        os.remove(os.path.join(current_app.root_path, "static", "img", "brand", asset["filename"]))
    except OSError:
        pass

    return jsonify({"success": True})


# ── Credit Extension Requests Endpoints (User Side) ─────────────────────────


@api_bp.route("/credit-requests", methods=["POST"])
@login_required_api
@limit_by_user("email_user")
def request_credit_extension():
    """Submit a credit extension request to the admin."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503

    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({"error": "User not authenticated"}), 401
    data = request.get_json() or {}

    try:
        requested_amount = float(data.get("requested_amount", 10.0))
        if requested_amount <= 0:
            return jsonify({"error": "Requested amount must be greater than 0"}), 400
        # Users who see rupees ask in rupees; credits are kept in USD
        if data.get("currency") == "INR":
            requested_amount = round(requested_amount / Config.USD_INR_RATE, 2)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid requested amount"}), 400

    reason = (data.get("reason") or "").strip()

    try:
        res = create_credit_request(user_id=user_id, requested_amount=requested_amount, reason=reason)
        return jsonify(
            {
                "success": True,
                "credit_request": res,
                "message": "Credit extension request submitted to admin for approval.",
            }
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


@api_bp.route("/credit-requests/my", methods=["GET"])
@login_required_api
def get_my_credit_requests():
    """Get list of current user's credit extension requests."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503

    user_id = get_current_user_id()
    if user_id is None:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        requests_list = get_user_credit_requests(user_id)
        return jsonify({"success": True, "requests": requests_list})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


# ── Admin Credit & Cost Management Endpoints ───────────────────────────────


@api_bp.route("/admin/users", methods=["GET"])
@login_required_api
@admin_required_api
def admin_get_all_users():
    """List all users with credit limits, used credits, remaining credits, and roles."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    try:
        users = get_all_users_credit_summary()
        return jsonify({"success": True, "users": users})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


# ── Admin: email invitations ─────────────────────────────────────────────
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _send_invitation(invitation: dict, token: str, contact: dict | None = None, style: str = "designed",
                     subject: str | None = None, body: str | None = None) -> None:
    """Emails an invitation (raises on SMTP failure). Its link goes through
    /i/<token> (counts the click) to /signup with the email pre-filled;
    signing up through it verifies the email. Every email has an unsubscribe
    link and footer (services/invite_outreach).

    style "designed": the HTML invitation. "preview" / "note": a short plain
    note from the contact's details (services/invite_outreach.TEMPLATES);
    "preview" links to the lead's preview page and falls back to "note" for
    contacts without a ready preview."""
    from db import record_invite_message

    service, msg, text, style = _invitation_email(invitation, token, contact, style, subject, body)
    # Kept exactly as sent (Admin -> Invitations shows it), whether it went out or not
    html = next((part.get_payload(decode=True).decode("utf-8", "replace") for part in msg.walk()
                 if part.get_content_type() == "text/html"), None)
    record = {"email": invitation["email"], "style": style, "subject": str(msg["Subject"] or ""), "body": text, "html": html,
              "contact_id": (contact or {}).get("id"), "invitation_id": invitation.get("id"),
              "sent_by_user_id": get_current_user_id(), "sender_name": invitation.get("invited_by")}
    try:
        service.deliver_invitation(msg, invitation["email"])
    except Exception as err:
        record_invite_message(**record, status="failed", error=str(err)[:500])
        raise
    record_invite_message(**record)


def _invitation_email(invitation: dict, token: str, contact: dict | None, style: str, subject: str | None,
                      body: str | None, address: str | None = None):
    """(EmailService, the email, its plain text, the style used) - every style
    is a designed HTML email (services/email_service). "preview" falls back to
    "note" for a contact without a ready preview."""
    from services.email_service import EmailService
    from services.invite_outreach import build_note, postal_address, short_company, unsubscribe_token

    base = (Config.APP_BASE_URL or request.host_url).rstrip("/")
    invite_link = f"{base}/i/{token}"
    company = short_company((contact or {}).get("company")) or None
    note = None
    if style in ("preview", "note") and contact is not None:
        preview = contact.get("preview") or {}
        has_preview = preview.get("status") == "ready"
        key = "preview" if style == "preview" and has_preview else "note"
        preview_link = f"{base}/p/{preview['token']}" if has_preview else None
        own_text = key == style  # a fallback note uses its own wording, not the preview template's
        note_subject, note_text = build_note(key, subject if own_text else None, body if own_text else None, contact,
                                             {"invite": invite_link, "preview": preview_link}, invitation.get("invited_by") or "")
        note = {"subject": note_subject, "text": note_text, "button_url": invite_link,
                "button_label": "Set up my account", "heading": f"An invitation for {company}" if company else "You're invited to AVIR AI"}
        if key == "preview":
            note.update(button_url=preview_link, button_label="See your posts", card=_preview_card(preview["token"], company))
            note["heading"] = f"{len((note['card'] or {}).get('titles') or []) or 3} LinkedIn posts for {company or 'you'}"
        style = key
    elif style != "designed":
        style = "designed"
    service = EmailService()
    msg, text = service.build_invitation_email(
        to_email=invitation["email"],
        name=invitation.get("name"),
        inviter_name=invitation.get("invited_by"),
        accept_url=invite_link,
        message=invitation.get("message"),
        expires_days=30,
        unsubscribe_url=f"{base}/u/{unsubscribe_token(invitation['email'])}",
        postal_address=postal_address() if address is None else address,
        note=note,
        company=company,
    )
    return service, msg, text, style


def _preview_card(token: str, company: str | None) -> dict | None:
    """A peek at the posts for the "Show them their posts" email: the first
    post's opening, the posts' titles and the brand colour."""
    from db import get_lead_preview

    found = get_lead_preview(token=token)
    if not found or not found["posts"]:
        return None
    caption = " ".join(found["posts"][0]["caption"].split())
    excerpt = caption if len(caption) <= 180 else caption[:170].rsplit(" ", 1)[0].rstrip(".,;:!?-") + "…"
    colors = (found["profile"] or {}).get("primary_colors") or []
    return {"company": (found["profile"] or {}).get("company_name") or company or "", "excerpt": excerpt,
            "titles": [p.get("occasion") or p["title"] for p in found["posts"]], "color": colors[0] if colors else None}


def _unsubscribed_on(email: str) -> str | None:
    from db import suppressed_emails

    return suppressed_emails([email]).get((email or "").strip().lower())


@api_bp.route("/admin/invitations", methods=["GET"])
@login_required_api
@admin_required_api
def admin_list_invitations():
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import list_invitations

    me = get_user_by_id(get_current_user_id())
    # Pre-fills "Your name (shown in the email)" in the Invite panel
    return jsonify({"success": True, "invitations": list_invitations(), "default_inviter_name": me.name if me else ""})


@api_bp.route("/admin/invitations", methods=["POST"])
@login_required_api
@admin_required_api
def admin_create_invitation():
    """Invite someone by email. Re-inviting a pending address refreshes its
    link (the old one stops working) and sends the email again."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()[:255]
    message = (data.get("message") or "").strip()[:1000]
    inviter_name = (data.get("inviter_name") or "").strip()[:255]
    if not _EMAIL_RE.match(email):
        return jsonify({"success": False, "error": "Enter a valid email address."}), 400
    if get_user_by_email(email):
        return jsonify({"success": False, "error": "This email already has an AVIR AI account."}), 409
    if _unsubscribed_on(email):
        return jsonify({"success": False, "error": "This address unsubscribed from our emails, so it can't be invited."}), 409

    import secrets

    from db import upsert_invitation

    token = secrets.token_urlsafe(32)
    invitation = upsert_invitation(email, name, message, get_current_user_id(), token, inviter_name=inviter_name)
    try:
        _send_invitation(invitation, token)
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning(f"[Invite] Could not email {email}: {e}")
        return jsonify(
            {
                "success": False,
                "invitation": invitation,
                "error": f"The invitation was saved but the email couldn't be sent: {e}",
            }
        ), 502
    return jsonify({"success": True, "invitation": invitation})


@api_bp.route("/admin/invitations/<int:invitation_id>/resend", methods=["POST"])
@login_required_api
@admin_required_api
def admin_resend_invitation(invitation_id):
    """New link + 30 more days, emailed again (also revives an expired/revoked invite)."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import get_invitation

    existing = get_invitation(invitation_id)
    if not existing:
        return jsonify({"success": False, "error": "Invitation not found."}), 404
    if existing["status"] == "accepted" or get_user_by_email(existing["email"]):
        return jsonify({"success": False, "error": "This person has already joined."}), 409
    if _unsubscribed_on(existing["email"]):
        return jsonify({"success": False, "error": "This address unsubscribed from our emails, so it can't be invited."}), 409

    import secrets

    from db import upsert_invitation

    token = secrets.token_urlsafe(32)
    # Keeps the name the original invitation was signed with
    invitation = upsert_invitation(
        existing["email"],
        existing.get("name"),
        existing.get("message"),
        get_current_user_id(),
        token,
        inviter_name=existing.get("invited_by"),
    )
    try:
        _send_invitation(invitation, token)
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": f"The email couldn't be sent: {e}"}), 502
    return jsonify({"success": True, "invitation": invitation})


@api_bp.route("/admin/invitations/<int:invitation_id>/revoke", methods=["POST"])
@login_required_api
@admin_required_api
def admin_revoke_invitation(invitation_id):
    """The emailed link stops working immediately."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import revoke_invitation

    if not revoke_invitation(invitation_id):
        return jsonify({"success": False, "error": "Invitation not found or already accepted."}), 404
    return jsonify({"success": True})


# ── Bulk invitations: the invite contact list (services/invite_import.py) ──
SEND_BATCH = 25  # invitations per request: the page sends a long selection in parts and shows progress


@api_bp.route("/admin/invite-contacts/template.csv", methods=["GET"])
@login_required_api
@admin_required_api
def admin_invite_template():
    """The CSV to fill in: the expected columns with one example row."""
    from flask import Response

    from services.invite_import import template_csv

    return Response(template_csv(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=avir-invite-contacts-template.csv"})


@api_bp.route("/admin/invite-contacts/preview", methods=["POST"])
@login_required_api
@admin_required_api
def admin_invite_preview():
    """Reads an uploaded .csv / .xlsx and checks every row. Nothing is saved."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import member_emails
    from services import invite_import

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"success": False, "error": "Choose a .csv or .xlsx file to upload."}), 400
    data = upload.read(invite_import.MAX_FILE_BYTES + 1)
    try:
        first = invite_import.parse(upload.filename, data)
        members = member_emails([r["email"] for r in first["rows"] if r["status"] == "ready"])
        result = invite_import.parse(upload.filename, data, members) if members else first
    except invite_import.InviteFileError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    log_event("invites.preview", admin_id=get_current_user_id(), rows=result["summary"]["total"], ready=result["summary"]["ready"])
    return jsonify({"success": True, "filename": secure_filename(upload.filename) or "upload", **result})


@api_bp.route("/admin/invite-contacts/import", methods=["POST"])
@login_required_api
@admin_required_api
def admin_invite_import():
    """Saves the ready rows of a preview: {filename, rows, total}. Rows are
    checked again here; people who already have accounts are left out."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import list_invite_contacts, member_emails, save_invite_contacts
    from services.invite_import import clean_rows

    data = request.get_json(silent=True) or {}
    rows = clean_rows(data.get("rows"))
    members = member_emails([r["email"] for r in rows])
    rows = [r for r in rows if r["email"] not in members]
    if not rows:
        return jsonify({"success": False, "error": "There are no new people to import."}), 400
    total = data.get("total") if isinstance(data.get("total"), int) else len(rows)
    filename = str(data.get("filename") or "upload")[:255]
    result = save_invite_contacts(rows, filename, get_current_user_id(), rows_total=total)
    log_event("invites.import", admin_id=get_current_user_id(), **result)
    return jsonify({"success": True, **result, "emails": [r["email"] for r in rows], **list_invite_contacts()})


@api_bp.route("/admin/invite-contacts", methods=["GET", "POST", "DELETE"])
@login_required_api
@admin_required_api
def admin_invite_contacts():
    """GET: the contact list with statuses. POST {email, name, ...}: add one by
    hand. DELETE {ids}: remove from the list."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import delete_invite_contacts, list_invite_contacts, member_emails, save_invite_contacts
    from services.invite_import import clean_rows

    if request.method == "DELETE":
        removed = delete_invite_contacts((request.get_json(silent=True) or {}).get("ids"))
        log_event("invites.remove", admin_id=get_current_user_id(), removed=removed)
        return jsonify({"success": True, "removed": removed, **list_invite_contacts()})
    if request.method == "POST":
        rows = clean_rows([request.get_json(silent=True) or {}])
        if not rows:
            return jsonify({"success": False, "error": "Enter a valid email address."}), 400
        if member_emails([rows[0]["email"]]):
            return jsonify({"success": False, "error": "This email already has an AVIR AI account."}), 409
        save_invite_contacts(rows, None, get_current_user_id(), source="manual")
    from services.invite_outreach import MERGE_FIELDS, TEMPLATES, postal_address

    return jsonify({"success": True, **list_invite_contacts(),
                    "outreach": {"postal_address": postal_address(), "templates": TEMPLATES, "merge_fields": MERGE_FIELDS}})


@api_bp.route("/admin/invite-contacts/<int:contact_id>/journey", methods=["GET"])
@login_required_api
@admin_required_api
def admin_invite_contact_journey(contact_id):
    """One person: the stage they reached (with dates) and every email sent to them, in full."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import invite_contact_journey

    journey = invite_contact_journey(contact_id)
    if journey is None:
        return jsonify({"success": False, "error": "Contact not found."}), 404
    return jsonify({"success": True, **journey})


@api_bp.route("/admin/invite-messages/<int:message_id>", methods=["GET"])
@login_required_api
@admin_required_api
def admin_invite_message(message_id):
    """One invitation email, exactly as it was sent."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import get_invite_message

    message = get_invite_message(message_id)
    if message is None:
        return jsonify({"success": False, "error": "Email not found."}), 404
    return jsonify({"success": True, "message": message})


@api_bp.route("/admin/invite-contacts/sample", methods=["POST"])
@login_required_api
@admin_required_api
def admin_invite_sample():
    """The email one selected person would get with the send panel's current
    settings - built exactly like the real one, but not sent and not saved."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import get_invite_contacts

    data = request.get_json(silent=True) or {}
    found = get_invite_contacts([data.get("contact_id")])
    if not found:
        return jsonify({"success": False, "error": "Contact not found."}), 404
    contact = found[0]
    style = data.get("style") if data.get("style") in ("designed", "preview", "note") else "designed"
    invitation = {"email": contact["email"], "name": contact.get("name"), "invited_by": (data.get("inviter_name") or "").strip()[:255],
                  "message": (data.get("message") or "").strip()[:1000] if style == "designed" else None}
    address = data.get("postal_address") if isinstance(data.get("postal_address"), str) else None
    _service, msg, text, used = _invitation_email(invitation, "their-own-link", contact, style,
                                                  (data.get("subject") or "").strip()[:200] or None,
                                                  (data.get("body") or "").strip()[:5000] or None, address)
    html = next((part.get_payload(decode=True).decode("utf-8", "replace") for part in msg.walk()
                 if part.get_content_type() == "text/html"), "")
    return jsonify({"success": True, "subject": str(msg["Subject"] or ""), "from": str(msg["From"] or ""),
                    "style": used, "html": html, "text": text})


@api_bp.route("/admin/invite-contacts/previews", methods=["POST"])
@login_required_api
@admin_required_api
def admin_invite_previews():
    """Prepares lead previews (services/lead_preview_service): {ids, refresh}.
    Runs in the background; the list shows each one's progress."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from services.lead_preview_service import queue_previews

    data = request.get_json(silent=True) or {}
    ids = data.get("ids") if isinstance(data.get("ids"), list) else []
    if not ids:
        return jsonify({"success": False, "error": "Select at least one person."}), 400
    if len(ids) > 500:
        return jsonify({"success": False, "error": "Prepare at most 500 previews at a time."}), 400
    result = queue_previews(ids, get_current_user_id(), refresh=bool(data.get("refresh")))
    log_event("invites.previews", admin_id=get_current_user_id(), **result)
    return jsonify({"success": True, **result})


@api_bp.route("/admin/invite-contacts/send", methods=["POST"])
@login_required_api
@admin_required_api
def admin_invite_contacts_send():
    """Invites the selected contacts: {ids (at most SEND_BATCH), inviter_name,
    message}. Each gets the usual invitation email (a new link if one was sent
    before). Returns a result per contact."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    import secrets

    from db import get_invite_contacts, member_emails, record_contact_invite, upsert_invitation

    data = request.get_json(silent=True) or {}
    ids = data.get("ids") if isinstance(data.get("ids"), list) else []
    if not ids:
        return jsonify({"success": False, "error": "Select at least one person."}), 400
    if len(ids) > SEND_BATCH:
        return jsonify({"success": False, "error": f"Send at most {SEND_BATCH} at a time."}), 400
    message = (data.get("message") or "").strip()[:1000]
    inviter_name = (data.get("inviter_name") or "").strip()[:255]
    style = data.get("style") if data.get("style") in ("designed", "preview", "note") else "designed"
    subject = (data.get("subject") or "").strip()[:200] or None
    body = (data.get("body") or "").strip()[:5000] or None
    if isinstance(data.get("postal_address"), str):
        from db import get_setting, save_setting

        address = data["postal_address"].strip()[:300]
        if address != (get_setting("company_postal_address", "") or ""):
            save_setting("company_postal_address", address)
    from db import suppressed_emails

    contacts = get_invite_contacts(ids)
    members = member_emails([c["email"] for c in contacts])
    unsubscribed = suppressed_emails([c["email"] for c in contacts])
    admin_id = get_current_user_id()
    results = []
    for contact in contacts:
        if contact["email"] in members:
            results.append({"id": contact["id"], "email": contact["email"], "status": "joined"})
            continue
        if contact["email"] in unsubscribed:
            results.append({"id": contact["id"], "email": contact["email"], "status": "unsubscribed"})
            continue
        token = secrets.token_urlsafe(32)
        invitation = upsert_invitation(contact["email"], contact.get("name"), message if style == "designed" else None,
                                       admin_id, token, inviter_name=inviter_name)
        try:
            _send_invitation(invitation, token, contact, style, subject, body)
        except Exception as err:  # noqa: BLE001 - one bad address must not stop the rest
            error = str(err)[:300] or "The email could not be sent."
            record_contact_invite(contact["id"], invitation["id"], error)
            results.append({"id": contact["id"], "email": contact["email"], "status": "failed", "error": error})
            continue
        record_contact_invite(contact["id"], invitation["id"])
        results.append({"id": contact["id"], "email": contact["email"], "status": "invited"})
    counts = {k: sum(1 for r in results if r["status"] == k) for k in ("invited", "failed", "joined", "unsubscribed")}
    log_event("invites.send", admin_id=admin_id, style=style, **counts)
    return jsonify({"success": True, "results": results, **counts})


@api_bp.route("/admin/invite-contacts/export.csv", methods=["GET"])
@login_required_api
@admin_required_api
def admin_invite_contacts_export():
    """The whole contact list with statuses, as CSV."""
    import csv
    import io

    from flask import Response

    from db import list_invite_contacts

    out = io.StringIO()
    writer = csv.writer(out)
    import json as _json

    columns = ["email", "name", "company", "job_title", "phone", "city", "country", "industry", "company_size", "website",
               "description", "notes", "status", "invite_count", "last_invited_at", "clicks", "first_clicked_at", "activated",
               "unsubscribed_at", "last_error", "source", "batch", "created_at"]
    base = (Config.APP_BASE_URL or request.host_url).rstrip("/")
    # first_line / preview_url: merge fields for an outside email tool
    writer.writerow([*columns, "first_line", "preview_url", "preview_views", "extra"])
    for contact in list_invite_contacts()["contacts"]:
        preview = contact.get("preview") or {}
        ready = preview.get("status") == "ready"
        writer.writerow([contact.get(c) if contact.get(c) is not None else "" for c in columns]
                        + [preview.get("first_line") or "", f"{base}/p/{preview['token']}" if ready else "",
                           preview.get("views") or 0,
                           _json.dumps(contact.get("extra") or {}, ensure_ascii=False) if contact.get("extra") else ""])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=avir-invite-contacts.csv"})


# ── Image limits & model access ──────────────────────────────────────────
_heyroute_image_models_cache = {"at": 0.0, "ids": []}


def _heyroute_image_models(fresh: bool = False) -> list[str]:
    """Models the HeyRoute image key can make images with (cached 10 min) -
    suggestions and validation for Admin -> Image Settings. /models lists every
    model on the key, text models included; only those whose
    supported_endpoint_types include "image-generation" work for images (the
    others fail with 404 model_not_found). Empty when HeyRoute can't be reached.
    fresh=True skips the cache (models enabled on HeyRoute a moment ago)."""
    import time

    if not fresh and time.time() - _heyroute_image_models_cache["at"] < 600:
        return _heyroute_image_models_cache["ids"]
    ids: list[str] = []
    if Config.HEYROUTE_IMAGE_API_KEY:
        try:
            resp = requests.get(
                f"{Config.HEYROUTE_BASE_URL.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {Config.HEYROUTE_IMAGE_API_KEY}"},
                timeout=15,
            )
            resp.raise_for_status()
            ids = sorted(
                m["id"]
                for m in (resp.json() or {}).get("data") or []
                if m.get("id") and "image-generation" in (m.get("supported_endpoint_types") or [])
            )
        except Exception as e:  # noqa: BLE001
            current_app.logger.warning(f"[Images] Could not list HeyRoute image models: {e}")
    _heyroute_image_models_cache.update(at=time.time(), ids=ids)
    return ids


@api_bp.route("/me/image-quota", methods=["GET"])
@login_required_api
def my_image_quota():
    """Today's image usage for Studio Chat's "Images today: 1 of 2", whether
    the Video option is switched on for this user (video_quota.enabled), and
    what the user's plan includes (features; upgrade_plans names the lowest
    plan for each feature they don't have)."""
    if not DB_AVAILABLE:
        return jsonify({"success": True, "quota": None, "video_quota": None})
    from db import cheapest_plan_with, get_image_quota, get_user_features, get_video_quota

    user_id = get_current_user_id()
    features = get_user_features(user_id)
    upgrades = {f: (cheapest_plan_with(f) or {}).get("name") for f in ("approvals", "video") if not features.get(f)}
    return jsonify({"success": True, "quota": get_image_quota(user_id), "video_quota": get_video_quota(user_id),
                    "features": features, "upgrade_plans": upgrades})


@api_bp.route("/admin/image-settings", methods=["GET"])
@login_required_api
@admin_required_api
def admin_get_image_settings():
    from db import get_image_settings

    return jsonify({"success": True, "settings": get_image_settings(), "available_models": _heyroute_image_models(fresh=True)})


@api_bp.route("/admin/image-settings", methods=["PUT"])
@login_required_api
@admin_required_api
def admin_save_image_settings():
    from db import save_image_settings

    data = request.get_json() or {}
    try:
        default_limit = int(data.get("default_limit"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Default daily limit must be a whole number."}), 400
    if default_limit < 0 or default_limit > 10000:
        return jsonify({"success": False, "error": "Default daily limit must be between 0 and 10000."}), 400
    models = []
    for m in data.get("models") or []:
        model_id = str((m or {}).get("id") or "").strip()[:128]
        try:
            price = round(float((m or {}).get("price")), 4)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": f"Enter a price for {model_id or 'each model'}."}), 400
        if model_id and price >= 0 and not any(x["id"] == model_id for x in models):
            models.append({"id": model_id, "price": price})
    if not models:
        return jsonify({"success": False, "error": "Add at least one image model."}), 400
    image_models = _heyroute_image_models()
    if any(m["id"] not in image_models for m in models):
        image_models = _heyroute_image_models(fresh=True)  # may have just been enabled on HeyRoute
    not_image = [m["id"] for m in models if image_models and m["id"] not in image_models]
    if not_image:
        return jsonify({
            "success": False,
            "error": f"{', '.join(not_image)} can't make images on your HeyRoute image key. "
                     f"Image models available: {', '.join(image_models)}.",
            "invalid_models": not_image,
        }), 400
    default_model = str(data.get("default_model") or "").strip()
    if default_model not in [m["id"] for m in models]:
        return jsonify({"success": False, "error": "The default model must be one of the listed models."}), 400
    return jsonify({"success": True, "settings": save_image_settings(default_limit, default_model, models)})


@api_bp.route("/admin/users/<int:target_user_id>/image-access", methods=["PUT"])
@login_required_api
@admin_required_api
def admin_set_image_access(target_user_id):
    """limit: null = default, -1 = unlimited, n >= 0 = custom daily limit;
    model: null = default, else one of the Image Settings models;
    video_enabled (optional): true = the user gets the Video option."""
    from db import (
        IMAGE_UNLIMITED,
        get_image_quota,
        get_image_settings,
        get_video_quota,
        set_user_image_access,
        set_user_video_access,
    )

    data = request.get_json() or {}
    limit = data.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "The limit must be a whole number."}), 400
        if limit != IMAGE_UNLIMITED and not 0 <= limit <= 10000:
            return jsonify({"success": False, "error": "The limit must be between 0 and 10000 images per day."}), 400
    model = (data.get("model") or "").strip() or None
    if model and model not in [m["id"] for m in get_image_settings()["models"]]:
        return jsonify({"success": False, "error": "Choose a model from Image Settings."}), 400
    if not set_user_image_access(target_user_id, limit, model):
        return jsonify({"success": False, "error": "User not found"}), 404
    if "video_enabled" in data:
        set_user_video_access(target_user_id, bool(data["video_enabled"]))
    _audit("access.updated", target_user_id, {"daily_image_limit": limit, "image_model": model,
                                              "video_enabled": data.get("video_enabled")})
    return jsonify({"success": True, "quota": get_image_quota(target_user_id),
                    "video_quota": get_video_quota(target_user_id)})


@api_bp.route("/admin/users/<int:target_user_id>/active", methods=["POST"])
@login_required_api
@admin_required_api
def admin_set_user_active(target_user_id):
    """Activate/deactivate a user's access - see is_active on User (db.py).
    Used both to manually activate an Enterprise account after sales sets it
    up, and generally to deactivate any account (e.g. abuse)."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503

    data = request.get_json() or {}
    if "is_active" not in data:
        return jsonify({"error": "is_active (bool) is required"}), 400

    res = set_user_active(target_user_id, bool(data["is_active"]))
    if res and not data["is_active"]:  # a deactivated account is signed out everywhere
        from db import bump_session_version

        bump_session_version(target_user_id)
    if not res:
        return jsonify({"error": "User not found"}), 404
    _audit("user.activated" if data["is_active"] else "user.deactivated", target_user_id)
    return jsonify({"success": True, "user": res})


@api_bp.route("/admin/users/<int:target_user_id>/brand-rescan", methods=["POST"])
@login_required_api
@admin_required_api
def admin_rescan_user_brand(target_user_id):
    """Admin only: scans the user's website again in the background (their
    brand profile). When it's ready the user gets a bell notification and an
    email (brand_profile_service.notify_brand_ready). Uses the website on
    file - the admin changes it with Edit profile first if it was wrong."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    user = get_user_by_id(target_user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    if user.account_type not in VALID_SELF_SERVE_ACCOUNT_TYPES:
        return jsonify({"error": "Only Individual, Small and Medium accounts have their own brand profile."}), 400
    website = (user.company_website or "").strip()
    if not website:
        return jsonify({"error": "This user has no website on file. Add one with Edit profile, then rescan."}), 400
    from services.brand_profile_service import start_brand_analysis_async

    base = (Config.APP_BASE_URL or request.host_url).rstrip("/")
    if not start_brand_analysis_async(target_user_id, website, notify_base_url=base):
        return jsonify({"error": "A scan is already running for this user."}), 409
    log_event("admin.brand_rescan", admin_id=get_current_user_id(), user_id=target_user_id)
    return jsonify({"success": True, "brand": {"status": "analyzing", "website": website}})


@api_bp.route("/admin/users/<int:target_user_id>", methods=["DELETE"])
@login_required_api
@admin_required_api
def admin_hard_delete_user(target_user_id):
    """Permanently deletes a user and every row of their data (run history,
    scheduled posts, approval requests, social accounts, brand profile,
    credit/sales-contact requests) - see db.hard_delete_user. Irreversible;
    the frontend (templates/admin.html) requires the admin to type the
    user's exact email to confirm before this is ever called.

    Deliberately refuses to delete admin accounts (including the caller's
    own) - hard-deleting staff accounts is a much higher-blast-radius
    mistake than deleting a customer account, and isn't what this feature
    is for; an admin who genuinely needs to be removed should be handled
    directly in the database, not through this one-click admin action."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503

    requester_id = get_current_user_id()
    if target_user_id == requester_id:
        return jsonify({"error": "You cannot delete your own account."}), 400

    target = get_user_by_id(target_user_id)
    if not target:
        return jsonify({"error": "User not found"}), 404
    if getattr(target, "is_admin", False):
        return jsonify({"error": "Admin accounts can't be deleted from this panel."}), 400

    data = request.get_json() or {}
    confirm_email = (data.get("confirm_email") or "").strip().lower()
    if confirm_email != target.email.lower():
        return jsonify({"error": "Confirmation email did not match."}), 400

    try:
        res = hard_delete_user(target_user_id)
        if not res:
            return jsonify({"error": "User not found"}), 404
        _audit("user.deleted", target_user_id, {"email": target.email})
        return jsonify({"success": True, "deleted": res})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


@api_bp.route("/admin/users/<int:target_user_id>/profile", methods=["POST"])
@login_required_api
@admin_required_api
def admin_update_user_profile(target_user_id):
    """Admin endpoint to edit a user's name/email/account type/website."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503

    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    account_type = (data.get("account_type") or "").strip().lower()
    company_website = data.get("company_website")
    if company_website is not None:
        company_website = company_website.strip()
    if not any([name, email, account_type, company_website]):
        return jsonify({"error": "Specify at least one field to update"}), 400

    try:
        res = update_user_profile(
            target_user_id,
            name=name or None,
            email=email or None,
            account_type=account_type or None,
            company_website=company_website,
        )
        if not res:
            return jsonify({"error": "User not found"}), 404
        return jsonify({"success": True, "user": res, "message": "User profile updated successfully."})
    except ValueError as e:
        return jsonify({"error": str(e), "success": False}), 400
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


@api_bp.route("/admin/users/<int:target_user_id>/credits", methods=["POST"])
@login_required_api
@admin_required_api
def admin_update_user_credits(target_user_id):
    """Admin endpoint to add or set credits for a specific user."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503

    data = request.get_json() or {}
    new_limit = data.get("new_limit")
    add_amount = data.get("add_amount")

    if new_limit is None and add_amount is None:
        return jsonify({"error": "Specify either new_limit or add_amount"}), 400

    try:
        res = update_user_credit_limit(
            user_id=target_user_id,
            new_limit=float(new_limit) if new_limit is not None else None,
            add_amount=float(add_amount) if add_amount is not None else None,
        )
        if not res:
            return jsonify({"error": "User not found"}), 404
        _audit("credits.set" if new_limit is not None else "credits.add", target_user_id,
               {"new_limit": new_limit, "add_amount": add_amount, "credit_limit_after": res.get("credit_limit")})

        return jsonify({"success": True, "user": res, "message": "User credit limit updated successfully."})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


@api_bp.route("/admin/credit-requests", methods=["GET"])
@login_required_api
@admin_required_api
def admin_get_credit_requests():
    """List all credit extension requests across all users."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    try:
        status_filter = request.args.get("status")
        requests_list = get_all_credit_requests(status_filter=status_filter)
        return jsonify({"success": True, "requests": requests_list})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


def _email_credit_decision(res: dict, approved: bool) -> bool:
    """Tell the user their credit request was approved / rejected. The
    decision is already saved: a mail failure is logged, never raised."""
    if not res.get("user_email"):
        return False
    try:
        from services.email_service import EmailService

        EmailService().send_credit_decision_email(
            to_email=res["user_email"],
            name=res.get("user_name") or "there",
            approved=approved,
            requested_amount=float(res.get("requested_amount") or 0),
            credit_limit=res.get("new_credit_limit") if approved else res.get("credit_limit"),
            currency=user_currency(res.get("user_id")),
            dashboard_url=f"{(Config.APP_BASE_URL or request.host_url).rstrip('/')}/dashboard",
        )
        return True
    except Exception as mail_err:  # noqa: BLE001
        current_app.logger.warning(f"[Credits] Could not email the credit decision to user {res.get('user_id')}: {mail_err}")
        return False


@api_bp.route("/admin/credit-requests/<int:req_id>/approve", methods=["POST"])
@login_required_api
@admin_required_api
def admin_approve_request(req_id):
    """Approve a pending credit extension request."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    try:
        res = approve_credit_request(req_id)
        if not res:
            return jsonify({"error": "Pending request not found"}), 404

        _audit("credit_request.approved", res.get("user_id"),
               {"request_id": req_id, "amount": res.get("requested_amount"), "credit_limit_after": res.get("new_credit_limit")})
        emailed = _email_credit_decision(res, approved=True)
        return jsonify({"success": True, "result": res, "email_sent": emailed,
                        "message": "Credit extension approved and limit increased."})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


@api_bp.route("/admin/credit-requests/<int:req_id>/reject", methods=["POST"])
@login_required_api
@admin_required_api
def admin_reject_request(req_id):
    """Reject a pending credit extension request."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    try:
        res = reject_credit_request(req_id)
        if not res:
            return jsonify({"error": "Pending request not found"}), 404

        _audit("credit_request.rejected", res.get("user_id"), {"request_id": req_id, "amount": res.get("requested_amount")})
        emailed = _email_credit_decision(res, approved=False)
        return jsonify({"success": True, "result": res, "email_sent": emailed,
                        "message": "Credit extension request rejected."})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


@api_bp.route("/admin/runs/<int:run_id>", methods=["GET"])
@login_required_api
@admin_required_api
def admin_get_run_details(run_id):
    """Everything one run produced (Admin -> Global Cost History -> row): the
    full brief, and per platform the caption, hashtags, image/video with the
    prompt used, quality checks and compliance flags."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    run = get_run_by_id(run_id)
    if not run:
        return jsonify({"success": False, "error": "Run not found"}), 404
    owner = get_user_by_id(run["user_id"]) if run.get("user_id") else None
    content = run.get("content") or {}

    def _caption(value):
        if isinstance(value, dict):
            return value.get("primary_caption") or value.get("caption") or ""
        return value or ""

    def _hashtags(value):
        if isinstance(value, dict):
            value = value.get("hashtags") or value.get("primary_hashtags") or []
        return [str(t) for t in value] if isinstance(value, list) else []

    platforms = [p for p in (run.get("platforms") or []) if isinstance(content.get(p), dict)]
    platforms += [k for k, v in content.items() if not str(k).startswith("_") and isinstance(v, dict) and k not in platforms]
    outputs = []
    for p in platforms:
        d = content.get(p) or {}
        media = d.get("media") or {}
        image = media.get("image") if isinstance(media.get("image"), dict) else None
        video = media.get("video") if isinstance(media.get("video"), dict) else None
        quality = d.get("quality") if isinstance(d.get("quality"), dict) else {}
        compliance = d.get("compliance") if isinstance(d.get("compliance"), dict) else {}
        outputs.append(
            {
                "platform": p,
                "caption": _caption(d.get("caption")),
                "hashtags": _hashtags(d.get("hashtags")),
                "image": {"url": image.get("url"), "prompt": image.get("prompt")} if image and image.get("url") else None,
                "video": {"url": video.get("url")} if video and video.get("url") else None,
                "media_prompt": d.get("media_prompt"),
                "quality": {
                    "checks_passed": quality.get("checks_passed"),
                    "checks_total": quality.get("checks_total"),
                    "issues": quality.get("fixed_issues") or quality.get("issues") or [],
                    "rewritten": bool(quality.get("self_corrected")),
                },
                "compliance_flags": len(compliance.get("flags") or []),
            }
        )
    agents = [a for a in (content.get("_agents") or []) if isinstance(a, dict)]
    return jsonify(
        {
            "success": True,
            "run": {
                "id": run["id"],
                "timestamp": run.get("timestamp"),
                "story": run.get("story") or "",
                "tone": run.get("tone"),
                "platforms": run.get("platforms") or [],
                "tokens_used": run.get("tokens_used", 0),
                "cost_usd": run.get("cost_usd", 0.0),
                "user_name": owner.name if owner else "Unknown",
                "user_email": owner.email if owner else "N/A",
                "outputs": outputs,
                "agents": [{"name": a.get("name"), "role": a.get("role")} for a in agents],
            },
        }
    )


@api_bp.route("/admin/cost-history", methods=["GET"])
@login_required_api
@admin_required_api
def admin_get_global_cost_history():
    """List global cost history across all users."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from datetime import datetime, timedelta

    from db import get_system_usage_totals

    args = request.args

    def _int(name, default=None):
        try:
            return int(args.get(name)) if args.get(name) not in (None, "") else default
        except ValueError:
            return default

    def _day(name):
        try:
            return datetime.strptime(args.get(name, ""), "%Y-%m-%d") if args.get(name) else None
        except ValueError:
            return None

    try:
        min_cost = float(args["min_cost"]) if args.get("min_cost") not in (None, "") else None
    except ValueError:
        min_cost = None
    date_to = _day("date_to")
    page_size = _int("page_size") or _int("limit") or 25  # "limit": older clients
    try:
        result = get_global_cost_history(
            page=_int("page", 1),
            page_size=page_size,
            q=(args.get("q") or "").strip()[:200] or None,
            user_id=_int("user_id"),
            kind=args.get("type", "all"),
            platform=args.get("platform") or None,
            date_from=_day("date_from"),
            date_to=date_to + timedelta(days=1) if date_to else None,  # the whole last day
            min_cost=min_cost,
            sort=args.get("sort", "newest"),
        )
        # All-time totals from the database - not just the rows listed here
        return jsonify({"success": True, **result, "count": len(result["history"]), "summary": get_system_usage_totals()})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


# ── ADMIN: EMAILS (history, automation switches, send to a segment) ─────


@api_bp.route("/admin/emails", methods=["GET"])
@login_required_api
@admin_required_api
def admin_email_history():
    """Every email the app sent or tried to send, newest first, with filters."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from datetime import datetime, timedelta

    from db import EMAIL_KINDS, email_log_summary, list_email_log

    args = request.args

    def _day(name):
        try:
            return datetime.strptime(args.get(name, ""), "%Y-%m-%d") if args.get(name) else None  # noqa: DTZ007 - stored times are naive UTC
        except ValueError:
            return None

    def _int(name, default):
        try:
            return int(args.get(name) or default)
        except ValueError:
            return default

    date_to = _day("date_to")
    result = list_email_log(
        page=_int("page", 1), page_size=_int("page_size", 25),
        kind=args.get("kind") or None, status=args.get("status") or None,
        q=(args.get("q") or "").strip()[:200] or None,
        date_from=_day("date_from"), date_to=date_to + timedelta(days=1) if date_to else None,
    )
    return jsonify({"success": True, **result, "summary": email_log_summary(7), "kinds": EMAIL_KINDS})


def _email_automation_payload():
    from db import get_email_automation
    from services.email_service import EmailService

    weekday = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"][Config.IDEAS_EMAIL_WEEKDAY % 7]
    return {
        "success": True, "automation": get_email_automation(),
        "smtp_configured": EmailService().enabled, "base_url_set": bool(Config.APP_BASE_URL),
        "schedule": {
            "weekly_ideas": f"{weekday} from {Config.IDEAS_EMAIL_HOUR_UTC:02d}:00 UTC, at most once a week per user",
            "nudge_emails": f"Checked hourly: a festival {Config.NUDGE_OCCASION_DAYS} days ahead, this week's trend, "
                            f"or a reminder after {Config.NUDGE_INACTIVE_DAYS} quiet days. Max {Config.IDEA_EMAILS_PER_WEEK} idea emails a week.",
            "monthly_recap": f"From day {Config.RECAP_EMAIL_DAY} of each month, {Config.IDEAS_EMAIL_HOUR_UTC:02d}:00 UTC",
            "weekly_results": f"{['Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays'][Config.RESULTS_EMAIL_WEEKDAY % 7]} "
                              f"from {Config.IDEAS_EMAIL_HOUR_UTC:02d}:00 UTC: last week's results, only when there is something to report",
            "approval_reminders": "Checked hourly: one reminder after 24 hours and a last one after 3 days. "
                                  "Requests older than 14 days are left alone.",
        },
    }


@api_bp.route("/admin/email-automation", methods=["GET"])
@login_required_api
@admin_required_api
def admin_get_email_automation():
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    return jsonify(_email_automation_payload())


@api_bp.route("/admin/email-automation", methods=["PUT"])
@login_required_api
@admin_required_api
def admin_set_email_automation():
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import EMAIL_AUTOMATIONS, set_email_automation

    data = request.get_json(silent=True) or {}
    changes = {k: v for k, v in data.items() if k in EMAIL_AUTOMATIONS and isinstance(v, bool)}
    if not changes:
        return jsonify({"error": "Nothing to change."}), 400
    set_email_automation(changes)
    log_event("admin.email_automation", admin_id=get_current_user_id(), changes=changes)
    return jsonify(_email_automation_payload())


@api_bp.route("/admin/email-campaign/options", methods=["GET"])
@login_required_api
@admin_required_api
def admin_email_campaign_options():
    from services.compliance_rules import INDUSTRIES
    from services.email_campaign_service import KINDS, upcoming_occasions

    return jsonify({
        "success": True, "kinds": KINDS,
        "industries": [{"id": k, "label": v} for k, v in INDUSTRIES.items()],
        "account_types": [{"id": "individual", "label": "Individual"}, {"id": "small", "label": "Small business"},
                          {"id": "medium", "label": "Medium business"}, {"id": "enterprise", "label": "Enterprise"}],
        "regions": [{"id": "US", "label": "United States"}, {"id": "UAE/GCC", "label": "UAE / GCC"},
                    {"id": "India", "label": "India"}],
        "occasions": upcoming_occasions(60),
    })


def _campaign_args(data: dict) -> dict:
    return {key: (str(data.get(key) or "").strip() or None)
            for key in ("industry", "account_type", "region")} | {"occasion_key_": data.get("occasion") or None}


@api_bp.route("/admin/email-campaign/preview", methods=["POST"])
@login_required_api
@admin_required_api
def admin_email_campaign_preview():
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from services.email_campaign_service import preview

    data = request.get_json(silent=True) or {}
    try:
        return jsonify({"success": True, **preview(data.get("kind") or "ideas", **_campaign_args(data))})
    except ValueError as err:
        return jsonify({"error": str(err)}), 400


@api_bp.route("/admin/email-campaign/send", methods=["POST"])
@login_required_api
@admin_required_api
def admin_email_campaign_send():
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from services.email_campaign_service import start

    data = request.get_json(silent=True) or {}
    admin_id = get_current_user_id()
    try:
        job = start(data.get("kind") or "ideas", admin_id, **_campaign_args(data))
    except ValueError as err:
        return jsonify({"error": str(err)}), 400
    log_event("admin.email_campaign", admin_id=admin_id, job=job["id"], kind=job["kind"], recipients=job["total"],
              **{k: v for k, v in job["filters"].items() if v})
    return jsonify({"success": True, "job": job})


@api_bp.route("/admin/email-campaign/jobs/<job_id>", methods=["GET"])
@login_required_api
@admin_required_api
def admin_email_campaign_job(job_id):
    from services.email_campaign_service import get_job

    job = get_job(job_id) if job_id.isalnum() else None
    if not job:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"success": True, "job": job})


# ── SOCIAL ACCOUNTS & POST SCHEDULING ENDPOINTS ───────────────────────


@api_bp.route("/social/accounts", methods=["GET"])
@login_required_api
def get_social_accounts():
    """Get connected social media accounts for current user"""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database unavailable"}), 503
    accounts = get_user_social_accounts(user_id)
    return jsonify({"success": True, "accounts": accounts})


@api_bp.route("/social/accounts", methods=["POST"])
@login_required_api
def save_social_account_endpoint():
    """Connect or update a social media account with optional MCP support"""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database unavailable"}), 503

    data = request.get_json() or {}
    platform = (data.get("platform") or "").lower().strip()
    account_name = data.get("account_name", "").strip()
    account_id = data.get("account_id", "").strip()
    access_token = data.get("access_token", "").strip()
    connection_type = data.get("connection_type", "direct")
    mcp_endpoint = data.get("mcp_endpoint", "").strip()
    mcp_token = data.get("mcp_token", "").strip()
    mcp_tool_name = data.get("mcp_tool_name", "linkedin_publish_post").strip()

    if not platform or platform not in ["facebook", "instagram", "linkedin", "youtube"]:
        return jsonify({"error": "Valid platform (facebook, instagram, linkedin, youtube) is required"}), 400
    if not account_name and platform != "instagram":  # Instagram: the handle comes from the account itself
        return jsonify({"error": "Account Name / Handle is required"}), 400

    if connection_type == "mcp" and not mcp_endpoint:
        return jsonify({"error": "MCP Endpoint URL is required for MCP connection mode"}), 400

    if platform == "instagram":
        # Publishing needs the Instagram account id - find it from the id given
        # (Instagram account or its Facebook Page) and the token
        from services.instagram_service import InstagramError, resolve_account

        if not account_id or not access_token:
            return jsonify({"error": "Enter your Facebook Page ID (or Instagram account ID) and its Page access token"}), 400
        try:
            info = resolve_account(account_id, access_token)
        except InstagramError as err:
            return jsonify({"error": str(err)}), 400
        except requests.exceptions.RequestException as err:
            return jsonify({"error": f"Could not reach Instagram: {err}"}), 502
        account_id = info["id"]
        account_name = f"@{info['username']}" if info["username"] else (account_name or "Instagram")

    acc = save_social_account(
        user_id=user_id,
        platform=platform,
        account_name=account_name,
        account_id=account_id,
        access_token=access_token,
        connection_type=connection_type,
        mcp_endpoint=mcp_endpoint,
        mcp_token=mcp_token,
        mcp_tool_name=mcp_tool_name,
    )
    return jsonify({"success": True, "account": acc})


@api_bp.route("/social/instagram/from-facebook", methods=["POST"])
@login_required_api
def connect_instagram_from_facebook():
    """One click: the Instagram account linked to the user's connected Facebook Page."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 503
    from sqlalchemy.orm import Session

    from db import SocialAccount, engine
    from services.instagram_service import InstagramError, account_from_page

    user_id = get_current_user_id()
    with Session(engine) as session:
        fb = (session.query(SocialAccount)
              .filter(SocialAccount.user_id == user_id, SocialAccount.platform == "facebook", SocialAccount.status == "connected")
              .first())
        page_id, token = (fb.account_id, fb.access_token) if fb else (None, None)
    if not page_id or not token:
        return jsonify({"error": "Connect your Facebook Page first - its Instagram account is found from it."}), 400
    try:
        info = account_from_page(page_id, token)
    except InstagramError as err:
        return jsonify({"error": str(err)}), 400
    except requests.exceptions.RequestException as err:
        return jsonify({"error": f"Could not reach Instagram: {err}"}), 502
    acc = save_social_account(user_id=user_id, platform="instagram", account_name=f"@{info['username']}" if info["username"] else "Instagram",
                              account_id=info["id"], access_token=token)
    return jsonify({"success": True, "account": acc, "username": info["username"]})


@api_bp.route("/auth/youtube", methods=["GET"])
@login_required_api
def youtube_auth_redirect():
    from flask import session

    from services import social_oauth_service as oauth

    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    if not client_id:
        return jsonify({"error": "YOUTUBE_CLIENT_ID not configured"}), 500

    redirect_uri = urllib.parse.urljoin(request.host_url, "api/auth/youtube/callback")
    scope = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"

    # A random, single-use value ties Google's answer to this browser and this
    # user (it used to be the user's id, which anyone could put in the address)
    state = oauth.new_state()
    session["oauth_state"] = {"provider": "youtube", "state": state, "at": time.time(), "user_id": get_current_user_id()}

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }

    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    return jsonify({"auth_url": auth_url})


@api_bp.route("/auth/youtube/callback", methods=["GET"])
def youtube_auth_callback():
    """Google sends the user back here. The channel is saved for the signed-in
    user who started the sign-in; the outcome is shown in Settings."""
    from datetime import datetime, timedelta, timezone

    from flask import redirect, session

    saved = session.pop("oauth_state", None)  # one use
    user_id = get_current_user_id()
    if not user_id:
        return redirect("/")
    if not _oauth_state_genuine(saved, "youtube", user_id):
        log_event("social.oauth", provider="youtube", status="rejected_state")
        return _oauth_done("youtube", False, "The YouTube sign-in could not be verified. Please connect again.")
    if request.args.get("error"):
        log_event("social.oauth", provider="youtube", status="cancelled", error=request.args["error"][:80])
        return _oauth_done("youtube", False, "The YouTube sign-in was cancelled, so nothing was connected.")
    code = request.args.get("code")
    if not code:
        return _oauth_done("youtube", False, "Google did not complete the sign-in. Please try again.")

    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    redirect_uri = urllib.parse.urljoin(request.host_url, "api/auth/youtube/callback")

    token_url = "https://oauth2.googleapis.com/token"  # nosec B105
    token_data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        response = requests.post(token_url, data=token_data, timeout=15)
        response.raise_for_status()
        tokens = response.json()

        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")

        # Get channel details
        channel_url = "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true"
        headers = {"Authorization": f"Bearer {access_token}"}
        channel_res = requests.get(channel_url, headers=headers, timeout=15)
        channel_res.raise_for_status()
        channel_data = channel_res.json()

        if not channel_data.get("items"):
            return _oauth_done("youtube", False, "No YouTube channel was found for that Google account.")

        channel = channel_data["items"][0]
        channel_id = channel["id"]
        channel_name = channel["snippet"]["title"]

        # Save to DB - for the user who is signed in, never one named in the address
        save_social_account(
            user_id=user_id,
            platform="youtube",
            account_name=channel_name,
            account_id=channel_id,
            access_token=access_token,
            refresh_token=refresh_token,
            connection_type="direct",
            connected_via="oauth",
            # an hour: renewed from the refresh token before then (services/youtube_service.py)
            token_expires_at=(datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in") or 3600))).replace(tzinfo=None),
        )
        log_event("social.oauth", provider="youtube", status="connected")
        return _oauth_done("youtube", True, f"Connected YouTube ({channel_name}).")

    except requests.exceptions.RequestException as e:
        # Google's answer can hold details of the request: it goes to the log, not to the page
        current_app.logger.warning(f"YouTube connect failed: {str(e)[:300]}")
        log_event("social.oauth", provider="youtube", status="failed")
        return _oauth_done("youtube", False, "Google did not complete the sign-in. Please try again.")
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning(f"YouTube connect failed: {str(e)[:300]}")
        return _oauth_done("youtube", False, "The YouTube channel could not be connected. Please try again.")


@api_bp.route("/social/mcp/test", methods=["POST"])
@login_required_api
def test_mcp_connection_endpoint():
    """Test connectivity and tool capabilities of a Model Context Protocol (MCP) server"""
    data = request.get_json() or {}
    mcp_endpoint = data.get("mcp_endpoint", "").strip()
    mcp_token = data.get("mcp_token", "").strip()
    mcp_tool_name = data.get("mcp_tool_name", "linkedin_publish_post").strip()

    if not mcp_endpoint:
        return jsonify({"error": "MCP Server Endpoint URL is required"}), 400

    headers = {"Content-Type": "application/json"}
    if mcp_token:
        headers["Authorization"] = f"Bearer {mcp_token}"

    try:
        # 1. JSON-RPC tool list / ping
        mcp_payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = requests.post(mcp_endpoint, json=mcp_payload, headers=headers, timeout=5)
        return jsonify(
            {
                "success": True,
                "status_code": resp.status_code,
                "mcp_endpoint": mcp_endpoint,
                "mcp_tool_name": mcp_tool_name,
                "message": f"MCP Server connected successfully (HTTP {resp.status_code}). Tool [{mcp_tool_name}] ready.",
            }
        )
    except Exception as e:  # noqa: BLE001
        return jsonify(
            {
                "success": True,
                "simulated": True,
                "mcp_endpoint": mcp_endpoint,
                "mcp_tool_name": mcp_tool_name,
                "message": f"MCP Connection Endpoint configured! Ready to trigger tool [{mcp_tool_name}]. Notice: {str(e)}",
            }
        )


@api_bp.route("/social/accounts/<platform>", methods=["DELETE"])
@login_required_api
def disconnect_social_account_endpoint(platform):
    """Disconnect a social media account"""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database unavailable"}), 503

    success = disconnect_social_account(user_id, platform.lower())
    if success:
        return jsonify({"success": True, "message": f"{platform.capitalize()} disconnected"})
    return jsonify({"error": "Account not found or already disconnected"}), 404


@api_bp.route("/social/schedule", methods=["POST"])
@login_required_api
def schedule_post_endpoint():
    """Schedule a post. Body: {scheduled_at (ISO, with its UTC offset), items:
    [{platform, caption, hashtags, media, images, ...}], story, run_id,
    conversation_id} - an item per platform, published as scheduled
    (services/schedule_service.py). Without items: the older shape, one
    caption for every platform ({platforms, content_json})."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database unavailable"}), 503
    from services import schedule_service

    data = request.get_json(silent=True) or {}
    if data.get("items") is not None:
        try:
            post = schedule_service.create(
                user_id, data.get("items"), data.get("scheduled_at"), current_app.config["UPLOAD_FOLDER"],
                story=data.get("story"), run_id=data.get("run_id") if isinstance(data.get("run_id"), int) else None,
                conversation_id=data.get("conversation_id") if isinstance(data.get("conversation_id"), int) else None,
            )
        except schedule_service.ScheduleError as err:
            return jsonify({"success": False, "error": str(err)}), 400
        log_event("scheduled.create", post_id=post["id"], platforms=post["platforms"], media=post["media"])
        return jsonify({"success": True, "scheduled_post": post})

    platforms = data.get("platforms") or []
    content_json = data.get("content_json") or {}
    run_id = data.get("run_id")
    if not platforms or not isinstance(platforms, list):
        return jsonify({"error": "At least one target platform must be selected"}), 400
    if not data.get("scheduled_at"):
        return jsonify({"error": "Scheduled date and time is required"}), 400
    try:
        scheduled_at = schedule_service.parse_when(data.get("scheduled_at"))
    except schedule_service.ScheduleError as err:
        return jsonify({"error": str(err)}), 400

    if run_id is not None:
        post = create_scheduled_post(
            user_id=user_id, platforms=platforms, scheduled_at=scheduled_at, content_json=content_json, run_id=run_id
        )
    else:
        post = create_scheduled_post(
            user_id=user_id, platforms=platforms, scheduled_at=scheduled_at, content_json=content_json
        )
    return jsonify({"success": True, "scheduled_post": post})


@api_bp.route("/social/connections", methods=["GET"])
@login_required_api
def social_connections_endpoint():
    """Which platforms the user can publish to: {platform: account name or null}. No tokens."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database unavailable"}), 503
    from services.publish_service import connections

    return jsonify({"success": True, "connections": connections(user_id)})


# ── One-click connect (services/social_oauth_service.py) ──────────────────────
# start -> the platform's sign-in -> callback. The outcome is left in the
# session for the Settings page to pick up (never put in the address, so a
# crafted link can't make the app show someone else's words).


def _oauth_base_url() -> str:
    return (Config.APP_BASE_URL or request.host_url).rstrip("/")


def _oauth_state_genuine(saved, provider: str, user_id) -> bool:
    """The answer belongs to the sign-in this browser started for this user, a few minutes ago at most."""
    import hmac

    from services.social_oauth_service import STATE_TTL_SECONDS

    return bool(
        isinstance(saved, dict) and saved.get("provider") == provider and saved.get("user_id") == user_id
        and time.time() - float(saved.get("at") or 0) <= STATE_TTL_SECONDS
        and hmac.compare_digest(str(saved.get("state") or ""), request.args.get("state") or "")
    )


def _oauth_done(provider: str, ok: bool, message: str | None = None, choose: str | None = None):
    from flask import redirect, session

    session["oauth_result"] = {"ok": ok, "provider": provider, "message": message, "choose": choose}
    return redirect("/settings")


@api_bp.route("/social/oauth/providers", methods=["GET"])
@login_required_api
def social_oauth_providers():
    """Which one-click connections are set up. Admins also get the return
    addresses to register in each platform's app."""
    from services import social_oauth_service as oauth

    providers = dict(oauth.providers())
    providers["youtube"] = bool(os.getenv("YOUTUBE_CLIENT_ID") and os.getenv("YOUTUBE_CLIENT_SECRET"))
    out = {"success": True, "providers": providers}
    user = get_user_by_id(get_current_user_id()) if DB_AVAILABLE else None
    if user and user.is_admin:
        out["redirect_uris"] = {p: oauth.redirect_uri(p, _oauth_base_url()) for p in oauth.PROVIDERS}
    return jsonify(out)


@api_bp.route("/social/oauth/<provider>/start", methods=["GET"])
def social_oauth_start(provider):
    """Sends the signed-in user to the platform's sign-in page."""
    from flask import redirect, session

    from services import social_oauth_service as oauth

    user_id = get_current_user_id()
    if not user_id:
        return redirect("/")
    if provider not in oauth.PROVIDERS or not oauth.enabled(provider):
        return _oauth_done(provider, False, "One-click connect isn't set up for this platform yet. Enter the account's details instead.")
    state = oauth.new_state()
    session["oauth_state"] = {"provider": provider, "state": state, "at": time.time(), "user_id": user_id}
    log_event("social.oauth", provider=provider, status="started")
    return redirect(oauth.authorize_url(provider, state, _oauth_base_url()))


@api_bp.route("/social/oauth/<provider>/callback", methods=["GET"])
def social_oauth_callback(provider):
    """The platform sends the user back here with a one-time code."""
    from flask import redirect, session

    from services import social_oauth_service as oauth

    saved = session.pop("oauth_state", None)  # one use
    user_id = get_current_user_id()
    if not user_id:
        return redirect("/")
    name = oauth.NAMES.get(provider, "that platform")
    if provider not in oauth.PROVIDERS or not oauth.enabled(provider) or not DB_AVAILABLE:
        return _oauth_done(provider, False, "One-click connect isn't set up for this platform yet.")
    if not _oauth_state_genuine(saved, provider, user_id):
        log_event("social.oauth", provider=provider, status="rejected_state")
        return _oauth_done(provider, False, f"The {name} sign-in could not be verified. Please click Connect again.")
    error = request.args.get("error")
    if error:
        log_event("social.oauth", provider=provider, status="cancelled" if oauth.cancelled(provider, error) else "refused", error=error[:80])
        if oauth.cancelled(provider, error):
            return _oauth_done(provider, False, f"You cancelled the {name} sign-in, so nothing was connected.")
        detail = (request.args.get("error_description") or error)[:200]
        return _oauth_done(provider, False, f"{name} did not complete the sign-in: {detail}")
    code = request.args.get("code")
    if not code:
        return _oauth_done(provider, False, f"{name} did not complete the sign-in. Please try again.")
    try:
        result = oauth.finish(user_id, provider, code, _oauth_base_url())
    except oauth.OAuthError as err:
        log_event("social.oauth", provider=provider, status="failed", error=str(err)[:200])
        return _oauth_done(provider, False, str(err))
    except requests.exceptions.RequestException:
        log_event("social.oauth", provider=provider, status="unreachable")
        return _oauth_done(provider, False, f"Could not reach {name}. Please try again.")
    if result.get("choose"):
        log_event("social.oauth", provider=provider, status="choosing")
        return _oauth_done(provider, True, choose=result["choose"])
    log_event("social.oauth", provider=provider, status="connected", platforms=[c["platform"] for c in result["connected"]])
    return _oauth_done(provider, True, oauth.connected_text(result["connected"]))


@api_bp.route("/social/oauth/result", methods=["GET"])
@login_required_api
def social_oauth_result():
    """What the last sign-in did, once: {ok, provider, message} or the accounts to choose from."""
    from flask import session

    from services import social_oauth_service as oauth

    result = session.pop("oauth_result", None)
    if not isinstance(result, dict):
        return jsonify({"success": True, "result": None})
    if result.get("choose"):
        pending = oauth.pending_options(get_current_user_id(), result["choose"])
        if not pending:
            return jsonify({"success": True, "result": {"ok": False, "provider": result.get("provider"),
                                                         "message": "That sign-in has expired. Click Connect again."}})
        return jsonify({"success": True, "result": {"ok": True, "provider": pending["provider"], "choose": result["choose"],
                                                     "options": pending["options"]}})
    return jsonify({"success": True, "result": {k: result.get(k) for k in ("ok", "provider", "message")}})


@api_bp.route("/social/oauth/pending/<pending_id>", methods=["POST"])
@login_required_api
def social_oauth_choose(pending_id):
    """Connects the account the user picked: {choice: <id>}."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 503
    from services import social_oauth_service as oauth

    choice = str((request.get_json(silent=True) or {}).get("choice") or "")
    try:
        connected = oauth.choose(get_current_user_id(), pending_id, choice)
    except oauth.OAuthError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    log_event("social.oauth", status="connected", platforms=[c["platform"] for c in connected], chosen=True)
    return jsonify({"success": True, "connected": connected, "message": oauth.connected_text(connected)})


@api_bp.route("/social/scheduled/<int:post_id>", methods=["GET", "PATCH"])
@login_required_api
def scheduled_post_endpoint(post_id):
    """One scheduled post; PATCH {scheduled_at} moves a post that is still waiting."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database unavailable"}), 503
    from db import get_scheduled_post
    from services import schedule_service

    if request.method == "GET":
        post = get_scheduled_post(user_id, post_id)
        return (jsonify({"success": True, "scheduled_post": post}), 200) if post else (jsonify({"error": "Scheduled post not found"}), 404)
    try:
        post = schedule_service.reschedule(user_id, post_id, (request.get_json(silent=True) or {}).get("scheduled_at"))
    except LookupError as err:
        return jsonify({"error": str(err)}), 404
    except schedule_service.ScheduleError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    log_event("scheduled.move", post_id=post_id, scheduled_at=post["scheduled_at_iso"])
    return jsonify({"success": True, "scheduled_post": post})


@api_bp.route("/social/scheduled/<int:post_id>/retry", methods=["POST"])
@login_required_api
def retry_scheduled_post_endpoint(post_id):
    """Sends again, now, the platforms of a post that didn't go out."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database unavailable"}), 503
    from services import schedule_service

    try:
        post = schedule_service.retry(user_id, post_id)
    except LookupError as err:
        return jsonify({"error": str(err)}), 404
    except schedule_service.ScheduleError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    log_event("scheduled.retry", post_id=post_id)
    return jsonify({"success": True, "scheduled_post": post})


@api_bp.route("/social/scheduled", methods=["GET"])
@login_required_api
def get_scheduled_posts_endpoint():
    """Get list of scheduled posts for current user"""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database unavailable"}), 503

    posts = get_user_scheduled_posts(user_id)
    return jsonify({"success": True, "scheduled_posts": posts})


@api_bp.route("/social/scheduled/<int:post_id>/cancel", methods=["POST"])
@login_required_api
def cancel_scheduled_post_endpoint(post_id):
    """Cancel a pending scheduled post"""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database unavailable"}), 503

    success = cancel_scheduled_post(user_id, post_id)
    if success:
        return jsonify({"success": True, "message": "Scheduled post cancelled"})
    return jsonify({"error": "Post not found or cannot be cancelled"}), 404


@api_bp.route("/social/manual-schedule", methods=["POST"])
@login_required_api
def create_manual_scheduled_post_endpoint():
    """Create a manual scheduled or immediate post with optional photo upload."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database unavailable"}), 503

    caption = request.form.get("caption", "").strip()
    platforms_raw = request.form.getlist("platforms") or request.form.get("platforms")
    scheduled_at_str = request.form.get("scheduled_at", "").strip()
    publish_now = request.form.get("publish_now", "false").lower() == "true"

    if isinstance(platforms_raw, str):
        try:
            import json

            platforms = json.loads(platforms_raw)
        except Exception:
            platforms = [p.strip() for p in platforms_raw.split(",") if p.strip()]
    else:
        platforms = platforms_raw or []

    if not platforms:
        return jsonify({"error": "Please select at least one social media platform"}), 400
    if not caption:
        return jsonify({"error": "Post caption / text is required"}), 400

    image_url = None
    if "photo" in request.files and request.files["photo"].filename:
        file = request.files["photo"]
        filename = secure_filename(file.filename or "")
        unique_name = f"manual_{uuid.uuid4().hex[:8]}_{filename}"
        upload_folder = current_app.config.get(
            "UPLOAD_FOLDER", os.path.join(current_app.root_path, "static", "uploads")
        )
        os.makedirs(upload_folder, exist_ok=True)
        file_path = os.path.join(upload_folder, unique_name)
        file.save(file_path)
        image_url = f"/static/uploads/{unique_name}"
        storage_service.upload(image_url)

    from datetime import datetime, timezone

    from services import schedule_service

    # Stored in UTC (the scheduler compares in UTC); the page sends the moment with its offset
    scheduled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if not publish_now and scheduled_at_str:
        try:
            scheduled_at = schedule_service.parse_when(scheduled_at_str)
        except schedule_service.ScheduleError as err:
            return jsonify({"error": str(err)}), 400

    content_json = {
        "caption": caption,
        "story": caption,
        "image_url": image_url,
        "is_manual": True,
        "published_now": publish_now,
    }

    post = create_scheduled_post(
        user_id=user_id, platforms=platforms, scheduled_at=scheduled_at, content_json=content_json
    )

    pub_results = {}
    if publish_now and publisher_service:
        abs_image_path = None
        if image_url:
            rel_path = image_url.lstrip("/").replace("/", os.sep)
            candidate = os.path.join(current_app.root_path, rel_path)
            abs_image_path = candidate if os.path.exists(candidate) else storage_service.ensure_local(image_url)

        pub_results = publisher_service.publish_post_to_connected_accounts(
            user_id=user_id, platforms=platforms, caption=caption, image_path=abs_image_path
        )
        # "published" only when at least one platform took it
        post["status"] = "published" if any(r.get("success") for r in pub_results.values()) else "failed"
        update_scheduled_post_status(user_id, post["id"], post["status"])
    elif publish_now:
        update_scheduled_post_status(user_id, post["id"], "published")
        post["status"] = "published"

    return jsonify(
        {
            "success": True,
            "message": ("Post published to social platforms!" if post["status"] == "published"
                        else "The post could not be published - see the reason for each platform.") if publish_now
            else "Manual post scheduled successfully!",
            "scheduled_post": post,
            "publish_results": pub_results,
        }
    )


@api_bp.route("/social/verify/<platform>", methods=["POST"])
@login_required_api
def verify_social_account_endpoint(platform):
    """Verify live connectivity and API token validity for Facebook, Instagram, or LinkedIn."""
    data = request.get_json() or {}
    platform = platform.lower().strip()
    account_id = data.get("account_id", "").strip()
    access_token = data.get("access_token", "").strip()

    user_id = get_current_user_id()
    if not account_id or not access_token:
        if DB_AVAILABLE and user_id:
            accounts = get_user_social_accounts(user_id)
            saved_acc = next((a for a in accounts if a["platform"] == platform), None)
            if saved_acc:
                account_id = account_id or saved_acc.get("account_id")
                from sqlalchemy.orm import Session

                from db import SocialAccount, engine

                with Session(engine) as session:
                    db_acc = (
                        session.query(SocialAccount)
                        .filter(SocialAccount.user_id == user_id, SocialAccount.platform == platform)
                        .first()
                    )
                    if db_acc:
                        access_token = access_token or db_acc.access_token

    if not account_id or not access_token:
        return jsonify({"error": f"Please provide {platform.capitalize()} Page/Account ID and Access Token"}), 400

    if publisher_service and platform == "facebook":
        res = publisher_service.verify_facebook_account(account_id, access_token)
        return jsonify(res)
    if platform == "instagram":
        from services.instagram_service import InstagramError, publishing_quota, resolve_account

        try:
            info = resolve_account(account_id, access_token)
        except InstagramError as err:
            return jsonify({"success": False, "verified": False, "platform": "instagram", "error": str(err)})
        except requests.exceptions.RequestException as err:
            return jsonify({"success": False, "verified": False, "platform": "instagram", "error": f"Could not reach Instagram: {err}"})
        quota = publishing_quota(info["id"], access_token)
        return jsonify({
            "success": True, "verified": True, "platform": "instagram", "account_id": info["id"], "username": info["username"],
            "quota": quota,
            "message": f"Connected to @{info['username']}" + (f" - {quota['used']} of {quota['total']} posts used in the last 24 hours"
                                                              if quota and quota["total"] else ""),
        })
    elif platform == "youtube":
        import requests

        # Mock token bypass for easy testing. Flagged by bandit (hardcoded credential-shaped
        # string); low risk since this route already requires login, but worth gating behind
        # a DEBUG/env flag or removing before real users connect real YouTube accounts.
        if access_token == "mock_yt_token_123":  # nosec B105
            return jsonify(
                {
                    "success": True,
                    "verified": True,
                    "platform": "youtube",
                    "message": "YouTube Channel successfully connected and verified (Mock Token)!",
                }
            )

        verify_url = "https://www.googleapis.com/youtube/v3/channels?part=id&mine=true"
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            resp = requests.get(verify_url, headers=headers, timeout=10)
            if resp.status_code == 401:
                return jsonify(
                    {
                        "success": False,
                        "verified": False,
                        "platform": "youtube",
                        "error": "Invalid or expired Access Token.",
                    }
                )

            data = resp.json()
            if not data.get("items"):
                # Fallback: token is valid but mine=true returned no items. We still consider it verified if status is 200.
                pass

            return jsonify(
                {
                    "success": True,
                    "verified": True,
                    "platform": "youtube",
                    "message": "YouTube Channel successfully connected and verified!",
                }
            )
        except Exception as e:  # noqa: BLE001
            return jsonify(
                {"success": False, "verified": False, "platform": "youtube", "error": f"Verification failed: {str(e)}"}
            )
    else:
        return jsonify(
            {
                "success": True,
                "verified": True,
                "platform": platform,
                "message": f"{platform.capitalize()} credentials configured.",
            }
        )


@api_bp.route("/competitor-posts", methods=["GET"])
@login_required_api
@limit_by_user("ai_user")
def competitor_posts():
    target = request.args.get("target")
    if not target:
        return jsonify({"error": "No target competitor provided"}), 400

    try:
        from services.scraper_service import ScraperService

        scraper = ScraperService()
        posts = scraper.get_company_store(target)
        # --- NEW FILTERING LOGIC ---
        from agents.story_agent import StoryAgent
        from services.stradit_service import StradITService

        stradit = StradITService()
        project_context = stradit.get_all_projects_context()
        story_agent_local = StoryAgent()
        posts = story_agent_local.filter_relevant_posts(posts, project_context)
        # ---------------------------

        return jsonify({"success": True, "posts": posts})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@api_bp.route("/platform-posts", methods=["GET"])
@login_required_api
@limit_by_user("ai_user")
def platform_posts():
    platform = request.args.get("platform")
    competitor = request.args.get("competitor") or ""
    if not platform:
        return jsonify({"error": "No platform provided"}), 400

    try:
        from services.scraper_service import ScraperService

        scraper = ScraperService()
        posts = scraper.get_platform_posts(platform, competitor)

        # --- NEW FILTERING LOGIC ---
        from agents.story_agent import StoryAgent
        from services.stradit_service import StradITService

        stradit = StradITService()
        project_context = stradit.get_all_projects_context()
        story_agent_local = StoryAgent()
        posts = story_agent_local.filter_relevant_posts(posts, project_context)
        # ---------------------------

        db_stats = {"inserted": 0, "skipped": 0}
        try:
            from db import save_competitor_posts

            db_stats = save_competitor_posts(posts)
        except Exception as db_err:
            logger.warning(f"Warning - could not persist posts: {db_err}")

        return jsonify({"success": True, "posts": posts, "db": db_stats})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@api_bp.route("/competitor-posts-db", methods=["GET"])
@login_required_api
def competitor_posts_db():
    platform = request.args.get("platform")
    competitor = request.args.get("competitor")
    if not platform:
        return jsonify({"error": "No platform provided"}), 400
    # Optional lookback override; days=0 disables the cutoff (used when a
    # Suggested Storyline references posts older than the default window).
    days = request.args.get("days", default=15, type=int)

    try:
        from db import get_competitor_posts

        posts = get_competitor_posts(platform=platform, competitor=competitor, days=days)
        return jsonify({"success": True, "posts": posts})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


SUGGESTED_COLLECTIONS_DISPLAY_LIMIT = 10


@api_bp.route("/suggested-collections", methods=["GET"])
@login_required_api
def suggested_collections():
    """Return previously-generated Suggested Storyline collections from the DB
    (no compute) - accumulated across runs, newest first, capped at 10."""
    try:
        from db import get_content_collections

        collections = get_content_collections(limit=SUGGESTED_COLLECTIONS_DISPLAY_LIMIT)
        return jsonify({"success": True, "collections": collections})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


# Suggested Storylines only makes sense for content a counter-strategy piece
# can actually be built around. Raw job listings ("linkedin_jobs") and
# personnel-move announcements (hires, resignations, promotions, successions)
# are operational/HR noise, not a strategic talking point, so they're
# excluded before clustering - regardless of which platform filter was
# selected, since a "storyline" built from a job posting is never useful.
STORYLINE_EXCLUDED_PLATFORMS = {"linkedin_jobs"}
# A bare "appoints"/"appointed"/"names" is too common in legitimate
# competitor-win content (e.g. "Northern Trust appointed by a $60bn pension
# scheme", "Gravis appointed Northern Trust to provide fund services") to
# treat as a personnel-move signal on its own - it only counts here when
# followed closely by an actual role/title, same as "new <role>".
_ROLE = r"(?:ceo|cfo|coo|cio|cto|chief \w+ officer|chair(?:man|woman|person)?|president|vice[- ]president|vp|head(?: of \w+)?|director|boss(?:es)?|successor)"
_PERSONNEL_MOVE_RE = re.compile(
    r"\b("
    r"resign(?:s|ed|ation)?|steps? down|stepping down|"
    r"hires?\b|hired\b|joins? [\w\s]{0,15}\bas\b|promoted to|named successor|"
    r"takes? over as|succeeds \w+ as|"
    rf"(?:appoints?|appointed|names?)\s+(?:\w+\s+){{0,4}}(?:as\s+)?(?:new\s+)?{_ROLE}\b|"
    rf"new\s+{_ROLE}\b"
    r")\b",
    re.IGNORECASE,
)


def _is_storyline_worthy(post: dict) -> bool:
    """False for job listings and personnel-move/HR news - see
    STORYLINE_EXCLUDED_PLATFORMS / _PERSONNEL_MOVE_RE above."""
    if (post.get("platform") or "").lower() in STORYLINE_EXCLUDED_PLATFORMS:
        return False
    text = f"{post.get('title') or ''} {post.get('text') or ''}"
    return not _PERSONNEL_MOVE_RE.search(text)


@api_bp.route("/generate-suggested-collections", methods=["POST"])
@login_required_api
@limit_by_user("ai_user")
def generate_suggested_collections():
    """Group already-scraped competitor posts (last 15 days, per get_competitor_posts)
    by semantic similarity into "storyline" suggestions, labeled with a theme and
    relevance, then persist any newly-found ones. Clusters can span any combination
    of competitor/platform - breadth is used only for ranking, not as a filter."""
    data = request.get_json(silent=True) or {}
    platform = data.get("platform") or "all"
    competitor = data.get("competitor") or "all"

    try:
        from agents.collection_agent import CollectionAgent
        from db import (
            get_competitor_posts,
            get_content_collections,
            get_seen_storyline_hashes,
            mark_storylines_seen,
            post_urls_hash,
            save_content_collections,
        )
        from services.embedding_service import EmbeddingService
        from services.stradit_service import StradITService

        # We no longer clear unconditionally at the beginning.
        # We only clear the old list if the new run actually found new valid storylines.

        posts = get_competitor_posts(platform=platform, competitor=competitor)
        posts = [p for p in posts if _is_storyline_worthy(p)]
        db_stats = {"inserted": 0, "skipped": 0, "new_hashes": []}
        repeated_count = 0

        if posts:
            embedder = EmbeddingService()
            clusters = embedder.cluster_posts(posts)

            if clusters:
                stradit = StradITService()
                project_context = stradit.get_all_projects_context()

                agent = CollectionAgent()
                labeled = agent.label_clusters(clusters, project_context)

                # A cluster's post_urls_hash is a stable fingerprint of its
                # exact post composition - once a storyline has ever been
                # surfaced (SuggestedStorylineSeen, never cleared), skip it on
                # every later "regenerate" instead of resurfacing the same
                # theme again just because it's still within the 15-day window.
                already_seen = get_seen_storyline_hashes()
                fresh_hashes = []

                collections = []
                for c in labeled:
                    cluster_posts = c["posts"]
                    post_urls = [p.get("post_url") for p in cluster_posts if p.get("post_url")]

                    cluster_hash = post_urls_hash(post_urls)
                    if cluster_hash in already_seen:
                        repeated_count += 1
                        continue

                    competitors = sorted(
                        {p.get("_source_competitor") or p.get("competitor") for p in cluster_posts} - {None, ""}
                    )
                    platforms = sorted({p.get("platform") for p in cluster_posts} - {None, ""})
                    collections.append(
                        {
                            "label": c["label"],
                            "description": c["description"],
                            "relevance": c["relevance"],
                            "competitors": competitors,
                            "platforms": platforms,
                            "post_count": len(cluster_posts),
                            "post_urls": post_urls,
                        }
                    )
                    fresh_hashes.append(cluster_hash)
                if collections:
                    db_stats = save_content_collections(collections)
                mark_storylines_seen(fresh_hashes)

        stored = get_content_collections(limit=SUGGESTED_COLLECTIONS_DISPLAY_LIMIT)
        db_stats["repeated_filtered"] = repeated_count
        return jsonify({"success": True, "collections": stored, "db": db_stats})
    except Exception as e:  # noqa: BLE001
        import traceback

        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@api_bp.route("/festive-storylines", methods=["GET"])
@login_required_api
def festive_storylines():
    """Upcoming US holidays / Indian festivals as a distinct "Festive"
    Suggested Storyline category - seasonal/greeting content ideas that don't
    depend on competitor posts. Computed live (deterministic calendar math),
    not persisted."""
    try:
        from services.festival_service import get_upcoming_festivals

        days_ahead = int(request.args.get("days_ahead", 60))
        festivals = get_upcoming_festivals(days_ahead=days_ahead)
        return jsonify({"success": True, "festivals": festivals})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@api_bp.route("/stradit-projects", methods=["GET"])
@login_required_api
def get_stradit_projects():
    try:
        from services.stradit_service import StradITService

        svc = StradITService()
        projects = svc.get_projects()
        return jsonify({"success": True, "projects": projects})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@api_bp.route("/generate-channel-storyline", methods=["POST"])
@login_required_api
@limit_by_user("ai_user")
def generate_channel_storyline():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    story = data.get("story", "")
    character_config = data.get("characterConfig", {})

    if not story:
        return jsonify({"error": "Story text is required"}), 400

    try:
        from agents.story_agent import StoryAgent
        from services.stradit_service import StradITService

        stradit = StradITService()
        project_context = stradit.get_all_projects_context()

        story_agent_local = StoryAgent()
        result = story_agent_local.generate_channel_storyline(story, project_context, character_config=character_config)

        return jsonify({"success": True, "storyline": result})
    except Exception as e:  # noqa: BLE001
        import traceback

        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@api_bp.route("/opportunity-suggestions", methods=["GET"])
@login_required_api
def opportunity_suggestions():
    try:
        from db import get_opportunity_suggestions

        return jsonify({"success": True, "suggestions": get_opportunity_suggestions()})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@api_bp.route("/generate-opportunity-suggestions", methods=["POST"])
@login_required_api
@limit_by_user("ai_user")
def generate_opportunity_suggestions():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    story = data.get("story", "")
    accounts = data.get("accounts", "")

    if not story:
        return jsonify({"error": "Story text is required"}), 400

    try:
        from agents.opportunity_agent import OpportunityAgent
        from db import save_opportunity_suggestions
        from services.stradit_service import StradITService

        stradit = StradITService()
        project_context = stradit.get_all_projects_context()

        opportunity_agent = OpportunityAgent()
        result = opportunity_agent.generate_opportunities(story, project_context)

        db_stats = save_opportunity_suggestions(
            result.get("unserved_themes", []), result.get("domain_expansion", []), source_accounts=accounts
        )

        return jsonify({"success": True, "suggestions": result, "db": db_stats})
    except Exception as e:  # noqa: BLE001
        import traceback

        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@api_bp.route("/download-zip", methods=["POST"])
@login_required_api
def download_zip():
    data = request.json or {}
    urls = data.get("urls", [])
    if not urls:
        return jsonify({"error": "No URLs provided"}), 400

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for url in urls:
            if not url:
                continue
            # URLs are server-relative (e.g. "/static/uploads/xxx.png") - resolve
            # against the app's root_path rather than the process cwd, which may
            # not be the project root depending on how the app was launched.
            rel_path = url.split("?")[0].lstrip("/").replace("/", os.sep)
            path = os.path.join(current_app.root_path, rel_path)
            if not os.path.exists(path):
                path = storage_service.ensure_local(url) or path
            if os.path.exists(path):
                filename = os.path.basename(path)
                zf.write(path, arcname=filename)
            else:
                current_app.logger.warning(f"File not found for zip: {path}")

    memory_file.seek(0)
    return send_file(memory_file, mimetype="application/zip", as_attachment=True, download_name="generated_assets.zip")


VALID_SELF_SERVE_ACCOUNT_TYPES = {"individual", "small", "medium"}


@api_bp.route("/onboarding/account-type", methods=["POST"])
@login_required_api
def onboarding_account_type():
    """Second onboarding step (after email verification) - see
    templates/onboarding_account_type.html. Individual/Small/Medium complete
    onboarding immediately - with a website (starts the brand analysis) or with
    skip_website=true (no website yet); Enterprise records the
    selection but does NOT complete onboarding - the frontend sends those
    users on to /onboarding/contact-sales instead."""
    data = request.get_json() or {}
    account_type = (data.get("account_type") or "").strip().lower()
    website = (data.get("website") or "").strip()
    user_id = get_current_user_id()

    if account_type not in VALID_SELF_SERVE_ACCOUNT_TYPES | {"enterprise"}:
        return jsonify({"success": False, "error": "Invalid account type"}), 400

    from db import complete_user_onboarding, set_user_account_type_enterprise

    if account_type == "enterprise":
        set_user_account_type_enterprise(user_id)
        return jsonify({"success": True, "redirect": "/onboarding/contact-sales"})

    # "Skip for now": finish onboarding without a website - the brand profile
    # can be added later on the My Brand Configuration page.
    if not website and not data.get("skip_website"):
        return jsonify({"success": False, "error": "Please enter your website"}), 400

    complete_user_onboarding(user_id, account_type, website or None)
    # Signed up from a lead preview: open the posts made for them
    from flask import session as flask_session

    starter = flask_session.pop("starter_conversation", None)
    dashboard = f"/dashboard?conversation={int(starter)}" if str(starter or "").isdigit() else "/dashboard"
    if not website:
        return jsonify({"success": True, "redirect": dashboard, "scan_started": False})
    # The brand profile already came from that website (a lead preview): no second scan
    from db import get_user_brand_profile

    existing = get_user_brand_profile(user_id)

    def _site(url):
        return re.sub(r"^(https?://)?(www\.)?", "", (url or "").strip().lower()).rstrip("/")

    if existing and _site(existing.get("website")) == _site(website):
        return jsonify({"success": True, "redirect": dashboard, "scan_started": False})

    # Scrape the site and derive a brand profile (industry, voice, colors)
    # that future Studio Chat generation follows - in the background, so
    # onboarding never waits on it. The onboarding page polls
    # GET /api/brand-profile/status to show real progress and, on failure,
    # why (see services/brand_profile_service.py).
    scan_started = False
    try:
        from services.brand_profile_service import start_brand_analysis_async

        scan_started = start_brand_analysis_async(user_id, website)
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning(f"[onboarding] Could not start brand analysis for {website}: {e}")

    return jsonify({"success": True, "redirect": dashboard, "scan_started": scan_started})


@api_bp.route("/brand-profile/status", methods=["GET"])
@login_required_api
def brand_profile_scan_status():
    """Website brand-analysis scan state: running / ready / failed (with a
    user-facing message explaining a failure) - polled by onboarding and the
    "My Brand Configuration" page."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"success": True, "status": None})

    from db import get_brand_scan_status, get_user_brand_profile
    from services.brand_profile_service import failure_message

    scan = get_brand_scan_status(user_id)
    return jsonify(
        {
            "success": True,
            "status": scan["status"],
            "reason": scan["error"],
            "message": failure_message(scan["error"]) if scan["status"] == "failed" else None,
            "updated_at": scan["updated_at"],
            "has_profile": get_user_brand_profile(user_id) is not None,
        }
    )


@api_bp.route("/brand-profile/quick-prompts", methods=["GET"])
@login_required_api
def brand_profile_quick_prompts():
    """Studio Chat's welcome screen calls this to replace the generic
    example prompt cards with ones grounded in the user's own brand (see
    UserBrandProfile.suggested_post_ideas, populated during onboarding).
    Returns an empty list when the user has no brand profile - the frontend
    falls back to the static example cards already in the template."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"success": True, "post_ideas": []})

    from db import get_user_brand_profile

    profile = get_user_brand_profile(user_id)
    ideas = (profile or {}).get("suggested_post_ideas") or []
    user = get_user_by_id(user_id)
    return jsonify(
        {
            "success": True,
            "post_ideas": ideas,
            "company_name": (profile or {}).get("company_name"),
            # Studio Chat nudges self-serve users without a brand profile (e.g.
            # they skipped the website at onboarding) to add one
            "needs_brand_profile": bool(
                profile is None and user and user.account_type in VALID_SELF_SERVE_ACCOUNT_TYPES
            ),
        }
    )


# ── In-app guide: getting-started checklist + tours (services/guide_service.py) ──


@api_bp.route("/guide", methods=["GET"])
@login_required_api
def guide_status():
    """The "Get started" checklist (each step ticked from what the user has
    really done) and which tours they have already seen."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from services.guide_service import checklist

    return jsonify({"success": True, **checklist(get_current_user_id())})


@api_bp.route("/guide/state", methods=["POST"])
@login_required_api
def guide_update_state():
    """Remembers a finished tour, a dismissed checklist, or an event the
    server can't see for itself (an idea picked in the Ideas panel)."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from services.guide_service import checklist, update_state

    data = request.get_json(silent=True) or {}
    user_id = get_current_user_id()
    dismiss = data.get("dismiss_checklist")
    update_state(
        user_id, tour_seen=data.get("tour_seen"), event=data.get("event"), reset_tours=bool(data.get("reset_tours")),
        dismiss_checklist=dismiss if isinstance(dismiss, bool) else None,
    )
    if data.get("tour_seen") or data.get("event"):
        log_event("guide.progress", tour=data.get("tour_seen"), guide_event=data.get("event"))
    return jsonify({"success": True, **checklist(user_id)})


@api_bp.route("/ideas/feed", methods=["GET"])
@login_required_api
def idea_feed():
    """Studio Chat's "Ideas for you": this week's trending topics in the
    user's industry, post types that work for it and upcoming dates, each as
    a ready brief (services/trend_service.py). Served from the user's saved
    feed while it is current; ?refresh=1 writes a new one. groups is empty
    without a brand profile - the page keeps its "From your brand" cards."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"success": True, "feed": None})

    from services.trend_service import cached_idea_feed, generate_idea_feed

    refresh = request.args.get("refresh") in ("1", "true")
    if not refresh:
        cached = cached_idea_feed(user_id)
        if cached:
            return jsonify({"success": True, "feed": cached})
    # Writing a feed is an LLM call: not without credit
    try:
        stats = get_user_usage_stats(user_id)
        if _credit_blocked(stats):
            return jsonify({"error": "Credit limit reached. Please request a credit extension.", "credit_limit_exceeded": True}), 402
    except Exception as _cred_err:
        current_app.logger.warning(f"[Credits] Check error: {_cred_err}")
    try:
        feed, _usage = generate_idea_feed(user_id, force=refresh)
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning(f"[Ideas] Could not build the idea feed: {e}")
        return jsonify({"error": "Could not load ideas right now. Please try again in a minute."}), 500
    return jsonify({"success": True, "feed": feed})


@api_bp.route("/ideas/link/<token>", methods=["GET"])
@login_required_api
def idea_link(token):
    """The idea behind a "Create this post" button in the weekly ideas email
    (/dashboard?idea=<token>) - only for the user it was sent to."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import open_idea_link

    idea = open_idea_link(token, get_current_user_id())
    if not idea:
        return jsonify({"success": False, "error": "This idea link is no longer available."}), 404
    return jsonify({"success": True, "idea": idea})


@api_bp.route("/me/notifications", methods=["GET"])
@login_required_api
def my_notifications():
    """Settings -> Notifications: is the weekly ideas email on for this user."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import email_automation_enabled, get_ideas_email_settings

    return jsonify({"success": True, "ideas_email": get_ideas_email_settings(get_current_user_id()),
                    "weekly_send_active": email_automation_enabled("weekly_ideas"),
                    "nudge_emails_active": email_automation_enabled("nudge_emails")})


@api_bp.route("/me/notifications", methods=["PUT"])
@login_required_api
def update_my_notifications():
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import get_ideas_email_settings, set_ideas_email_enabled

    data = request.get_json() or {}
    if not any(k in data for k in ("ideas_email_enabled", "nudges_email_enabled", "recap_email_enabled", "results_email_enabled")):
        return jsonify({"success": False, "error": "ideas_email_enabled, nudges_email_enabled, recap_email_enabled or "
                                                   "results_email_enabled (bool) is required"}), 400
    user_id = get_current_user_id()
    if "ideas_email_enabled" in data:
        set_ideas_email_enabled(user_id, bool(data["ideas_email_enabled"]))
    if "nudges_email_enabled" in data:
        from db import set_nudge_email_enabled

        set_nudge_email_enabled(user_id, bool(data["nudges_email_enabled"]))
    if "recap_email_enabled" in data:
        from db import set_recap_email_enabled

        set_recap_email_enabled(user_id, bool(data["recap_email_enabled"]))
    if "results_email_enabled" in data:
        from db import set_results_email_enabled

        set_results_email_enabled(user_id, bool(data["results_email_enabled"]))
    return jsonify({"success": True, "ideas_email": get_ideas_email_settings(user_id)})


@api_bp.route("/me/notifications/results-email/send-now", methods=["POST"])
@login_required_api
@limit_by_user("email_user")
def send_my_results_email_now():
    """Settings -> Notifications -> "Send me last week's results now": the same
    email the weekly run sends, to the logged-in user's own address."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from datetime import datetime, timezone

    from db import get_ideas_email_settings
    from services.results_email_service import last_week, send_results_email

    user_id = get_current_user_id()
    start, end, key = last_week(datetime.now(timezone.utc))
    try:
        outcome = send_results_email(user_id, Config.APP_BASE_URL or request.host_url, start, end, key)
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning(f"[Results email] Could not send to user {user_id}: {e}")
        return jsonify({"success": False, "error": "The email could not be sent. Please try again later."}), 502
    if not outcome["sent"]:
        return jsonify({"success": False, "error": "Nothing was published through AVIR AI last week and no comments are "
                                                   "waiting, so there is nothing to report yet."}), 409
    return jsonify({"success": True, "email": (get_ideas_email_settings(user_id) or {}).get("email"), "posts": outcome["posts"]})


@api_bp.route("/me/notifications/recap-email/send-now", methods=["POST"])
@login_required_api
@limit_by_user("email_user")
def send_my_recap_email_now():
    """Settings -> Notifications -> "Send me last month's recap now": the
    same email the monthly run sends, to the logged-in user's own address."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from datetime import datetime, timezone

    from db import get_ideas_email_settings
    from services.recap_service import previous_month, send_recap_email

    user_id = get_current_user_id()
    year, month = previous_month(datetime.now(timezone.utc).date())
    try:
        outcome = send_recap_email(user_id, Config.APP_BASE_URL or request.host_url, year, month)
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning(f"[Recap email] Could not send to user {user_id}: {e}")
        return jsonify({"success": False, "error": "The email could not be sent. Please try again later."}), 502
    if not outcome["sent"]:
        return jsonify({"success": False, "error": "You didn't create any posts last month, so there is nothing to recap yet."}), 409
    return jsonify({"success": True, "email": (get_ideas_email_settings(user_id) or {}).get("email"), "posts": outcome["posts"]})


def _tz_minutes() -> int:
    """The browser's offset from UTC in minutes (?tz=330 for India), 0 when missing."""
    try:
        return int(request.args.get("tz", 0))
    except (TypeError, ValueError):
        return 0


# ── Autopilot (services/autopilot_service.py, AUTOPILOT_PLAN.md) ──────────
# Off by default. Review first: posts are prepared and sent for approval;
# only approved ones are scheduled.


@api_bp.route("/autopilot", methods=["GET"])
@login_required_api
def autopilot_get():
    """The /autopilot page (and the calendar strip): the set-up, the plan, what
    waits for approval and spend. ?tz= is the browser's clock."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from services import autopilot_service

    return jsonify({"success": True, "autopilot": autopilot_service.state(get_current_user_id())})


@api_bp.route("/autopilot", methods=["PUT"])
@login_required_api
def autopilot_put():
    """Body: any of enabled, paused, platforms, posts_per_week, days_off, mix,
    festivals, topics, avoid, images, image_cap_month, days_ahead, language,
    tz_minutes. A changed plan is planned again."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from services import autopilot_service

    user_id = get_current_user_id()
    try:
        autopilot_service.save(user_id, request.get_json(silent=True) or {})
    except autopilot_service.AutopilotError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    return jsonify({"success": True, "autopilot": autopilot_service.state(user_id)})


@api_bp.route("/autopilot/run", methods=["POST"])
@login_required_api
@limit_by_user("ai_user")
def autopilot_run_now():
    """Prepare now instead of waiting for the hourly run (Settings -> Autopilot)."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from datetime import datetime, timezone

    from services import autopilot_service

    user_id = get_current_user_id()
    made = autopilot_service.run_for_user(user_id, datetime.now(timezone.utc))
    return jsonify({"success": True, "prepared": made, "autopilot": autopilot_service.state(user_id)})


@api_bp.route("/autopilot/posts/<int:post_id>/skip", methods=["POST"])
@login_required_api
def autopilot_skip(post_id):
    """Skip one day: off the plan, or its approval request closed."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from services import autopilot_service

    user_id = get_current_user_id()
    try:
        post = autopilot_service.skip(user_id, post_id)
    except autopilot_service.AutopilotError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    return jsonify({"success": True, "post": post, "autopilot": autopilot_service.state(user_id)})


@api_bp.route("/calendar", methods=["GET"])
@login_required_api
def content_calendar():
    """The Content Calendar page's data for ?month=YYYY-MM (default: this
    month): posts created, posts scheduled, occasions, suggested slots and
    this week's goal (services/calendar_service.py)."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from datetime import datetime, timedelta, timezone

    from services.calendar_service import calendar_month

    tz = _tz_minutes()
    local_now = datetime.now(timezone.utc) + timedelta(minutes=tz)
    year, month = local_now.year, local_now.month
    raw = request.args.get("month") or ""
    if raw:
        try:
            year, month = (int(part) for part in raw.split("-", 1))
            if not (2000 <= year <= 2100 and 1 <= month <= 12):
                raise ValueError
        except ValueError:
            return jsonify({"success": False, "error": "month must look like 2026-10"}), 400
    return jsonify({"success": True, **calendar_month(get_current_user_id(), year, month, tz)})


@api_bp.route("/analytics", methods=["GET"])
@login_required_api
def post_analytics_route():
    """How the user's published posts did over ?days= (default 30): totals,
    per platform and top posts (services/metrics_service.py)."""
    if not DB_AVAILABLE:
        return jsonify({"success": True, "analytics": None})
    from datetime import datetime, timedelta, timezone

    from db import post_analytics

    try:
        days = max(1, min(365, int(request.args.get("days", 30))))
    except ValueError:
        days = 30
    since = datetime.now(timezone.utc) - timedelta(days=days)
    data = post_analytics(get_current_user_id(), since)
    if request.args.get("detail") == "1":
        # The Performance page: every post, and the period before for comparison
        before = post_analytics(get_current_user_id(), since - timedelta(days=days), since)
        data["previous"] = {k: before[k] for k in ("posts", "engagement", "totals", "engagement_rate")}
    else:
        data.pop("all_posts", None)
    return jsonify({"success": True, "days": days, "analytics": data})


@api_bp.route("/analytics/posts/<int:post_id>", methods=["GET"])
@login_required_api
def post_detail_route(post_id):
    """One published post for its page: numbers, how they grew, its score at
    48 hours against the usual, why it likely did (services/insights_service.py
    explain) and its comments."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import post_detail
    from services.insights_service import explain

    user_id = get_current_user_id()
    post = post_detail(user_id, post_id)
    if not post:
        return jsonify({"error": "Post not found"}), 404
    post["why"] = explain(user_id, post["platform"], post["item"]) if post["item"] else []
    return jsonify({"success": True, "post": post})


@api_bp.route("/repurpose", methods=["POST"])
@login_required_api
@limit_by_user("ai_user")
def repurpose_content():
    """Turn a link, a PDF or pasted text into post ideas (services/repurpose_service.py).
    JSON {url} or {text}, or a form upload "file" (PDF) -> {title, source, kind,
    summary, angles: [{title, brief, format}]}; the composer loads the chosen
    brief. A failure carries {code, fallback} so the window can offer another way."""
    from services import repurpose_service as repurpose

    user_id = get_current_user_id()
    if DB_AVAILABLE and user_id is not None:
        stats = get_user_usage_stats(user_id)
        if _credit_blocked(stats):
            return jsonify({"success": False, "credit_limit_exceeded": True,
                            "error": f"Credit limit reached ({format_money(stats.get('credit_limit', Config.DEFAULT_CREDIT_USD), stats.get('currency', 'USD'))}). "
                                     "Please request a credit extension from admin."}), 402
    data = request.get_json(silent=True) or {}
    upload = request.files.get("file")
    kind = "pdf" if upload else "text" if data.get("text") else "url"
    try:
        if upload:
            doc = repurpose.read_pdf(upload.read(repurpose.MAX_PDF_BYTES + 1), upload.filename or "document.pdf")
        elif kind == "text":
            doc = repurpose.read_text(str(data["text"])[:repurpose.MAX_TEXT_CHARS * 2])
        else:
            doc = repurpose.read_url(data.get("url"))
        ideas = repurpose.to_angles(doc)
    except repurpose.RepurposeError as err:
        return jsonify({"success": False, "error": str(err), "code": err.code, "fallback": err.fallback}), 400
    except requests.exceptions.RequestException:
        return jsonify({"success": False, "error": "Couldn't reach that page. Check the link and try again.",
                        "code": "unreachable", "fallback": "text"}), 502
    except Exception as err:  # noqa: BLE001 - the AI call
        logger.warning(f"Repurpose failed: {err}")
        return jsonify({"success": False, "error": "The AI service didn't answer. Please try again."}), 502
    usage = ideas.pop("usage", {})
    log_event("repurpose", user_id=user_id, kind=kind, angles=len(ideas["angles"]), tokens=usage.get("total_tokens", 0))
    return jsonify({"success": True, "title": doc["title"], "source": doc["source"], "kind": kind, **ideas})


@api_bp.route("/languages", methods=["GET"])
@login_required_api
def post_languages():
    """The composer's Country search and Language menu (services/language_service.py).
    Languages are English plus the user's country's: ?country= (a preview,
    for the brand profile page), else the brand profile's Country, else its markets, else
    ?detected= (the browser's guess)."""
    from services.language_service import countries, countries_for_regions, normalize_country, options

    picked = normalize_country(request.args.get("country"))
    if picked:
        chosen, source = [picked], "picked"
    else:
        chosen, source = [], None
        user_id = get_current_user_id()
        if DB_AVAILABLE and user_id:
            from db import get_user_brand_profile

            profile = get_user_brand_profile(user_id) or {}
            brand_country = normalize_country(profile.get("country"))
            chosen = [brand_country] if brand_country else countries_for_regions(
                profile.get("compliance_regions") or profile.get("regions_detected"))
            source = "brand" if chosen else None
        if not chosen and normalize_country(request.args.get("detected")):
            chosen, source = [normalize_country(request.args.get("detected"))], "detected"
    return jsonify({"success": True, "countries_chosen": chosen, "source": source,
                    "languages": options(chosen), "countries": countries()})


@api_bp.route("/best-times", methods=["GET"])
@login_required_api
def best_times_route():
    """When to post on each platform, in the user's clock (?tz=minutes ahead of UTC)."""
    if not DB_AVAILABLE:
        return jsonify({"success": True, "platforms": {}})
    from services.best_time_service import best_times

    return jsonify({"success": True, "platforms": best_times(get_current_user_id(), _tz_minutes())})


@api_bp.route("/insights", methods=["GET"])
@login_required_api
def insights_route():
    """What works for this brand: kinds of post that did clearly better or
    worse than its others, each scored at 48 hours (services/insights_service.py)."""
    if not DB_AVAILABLE:
        return jsonify({"success": True, "insights": [], "posts": 0})
    from services.insights_service import what_works

    return jsonify({"success": True, **what_works(get_current_user_id())})


@api_bp.route("/post-score", methods=["POST"])
@login_required_api
def post_score_route():
    """A quick checklist for a post being edited (services/post_score_service.py).
    No AI call; personal where the brand's own results say more."""
    from services.post_score_service import score

    data = request.get_json(silent=True) or {}
    caption = str(data.get("caption") or "")[:5000]
    hashtags = [str(t)[:100] for t in (data.get("hashtags") or [])[:60] if isinstance(t, str)]
    findings = []
    if DB_AVAILABLE:
        try:
            from services.insights_service import what_works

            findings = what_works(get_current_user_id())["insights"]
        except Exception as err:  # noqa: BLE001 - the common-practice checks still work
            logger.warning(f"Post score without insights: {err}")
    media = data.get("media") if data.get("media") in ("none", "single", "carousel") else None
    images = [str(i) for i in (data.get("images") or [])[:20]] if isinstance(data.get("images"), list) else []
    return jsonify({"success": True, **score(str(data.get("platform") or ""), caption, hashtags, media, images, findings)})


# ── Inbox: comments on published posts (services/comment_service.py) ──────


@api_bp.route("/inbox", methods=["GET"])
@login_required_api
def inbox_list():
    """Comments on the user's published posts, newest first: ?status=new
    (default) / replied / done / all, with counts for the filters."""
    if not DB_AVAILABLE:
        return jsonify({"success": True, "comments": [], "counts": {"new": 0, "replied": 0, "done": 0, "kinds": {}}})
    from db import list_post_comments

    status = request.args.get("status", "new")
    status = None if status == "all" else status if status in ("new", "replied", "done") else "new"
    return jsonify({"success": True, **list_post_comments(get_current_user_id(), status)})


@api_bp.route("/inbox/<int:comment_id>/suggest", methods=["POST"])
@login_required_api
@limit_by_user("ai_user")
def inbox_suggest(comment_id):
    """A suggested reply (one small AI call, counted towards credits). Nothing is posted."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import get_post_comment, update_post_comment
    from services import comment_service

    user_id = get_current_user_id()
    comment = get_post_comment(user_id, comment_id)
    if not comment:
        return jsonify({"error": "Comment not found"}), 404
    try:
        if _credit_blocked(get_user_usage_stats(user_id)):
            return jsonify({"error": "Credit limit reached. Please request a credit extension.", "credit_limit_exceeded": True}), 402
    except Exception as cred_err:  # noqa: BLE001
        current_app.logger.warning(f"[Credits] Check error: {cred_err}")
    try:
        suggestion, cost = comment_service.suggest(user_id, comment)
    except comment_service.ReplyError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    except Exception as err:  # noqa: BLE001
        current_app.logger.warning(f"[Inbox] Could not suggest a reply: {err}")
        return jsonify({"success": False, "error": "Couldn't suggest a reply right now. Please try again or write your own."}), 502
    updated = update_post_comment(user_id, comment_id, add_cost=cost, **suggestion)
    log_event("inbox.suggest", comment_id=comment_id, platform=comment["platform"], kind=suggestion["kind"])
    return jsonify({"success": True, "comment": updated})


@api_bp.route("/inbox/<int:comment_id>/reply", methods=["POST"])
@login_required_api
def inbox_reply(comment_id):
    """Post the reply the person approved (their text, as edited) under the
    comment, or with {private: true} send it to the commenter as a private
    message (Facebook and Instagram, within 7 days of the comment)."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from datetime import datetime, timezone

    from db import get_post_comment, update_post_comment
    from services import comment_service
    from services.publish_service import accounts

    user_id = get_current_user_id()
    comment = get_post_comment(user_id, comment_id)
    if not comment:
        return jsonify({"error": "Comment not found"}), 404
    if comment["status"] == "replied":
        return jsonify({"success": False, "error": "This comment already has your reply."}), 409
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "")
    private = data.get("private") is True
    send = comment_service.post_private_reply if private else comment_service.post_reply
    try:
        send(comment, text, accounts(user_id).get(comment["platform"]))
    except comment_service.ReplyError as err:
        update_post_comment(user_id, comment_id, reply_error=str(err)[:300])
        return jsonify({"success": False, "error": str(err)}), 400
    updated = update_post_comment(user_id, comment_id, status="replied", reply=text.strip(), reply_error=None,
                                  replied_at=datetime.now(timezone.utc), replied_privately=private)
    log_event("inbox.reply", comment_id=comment_id, platform=comment["platform"], private=private)
    return jsonify({"success": True, "comment": updated})


@api_bp.route("/inbox/<int:comment_id>/status", methods=["POST"])
@login_required_api
def inbox_status(comment_id):
    """Mark a comment done (nothing to reply) or back to new."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import update_post_comment

    status = (request.get_json(silent=True) or {}).get("status")
    if status not in ("new", "done"):
        return jsonify({"error": "status must be new or done"}), 400
    updated = update_post_comment(get_current_user_id(), comment_id, status=status)
    if not updated:
        return jsonify({"error": "Comment not found"}), 404
    return jsonify({"success": True, "comment": updated})


@api_bp.route("/me/goal", methods=["GET"])
@login_required_api
def my_weekly_goal():
    """This week's posting goal and progress (Studio Chat's goal pill)."""
    if not DB_AVAILABLE:
        return jsonify({"success": True, "progress": None})
    from services.calendar_service import week_progress

    return jsonify({"success": True, "progress": week_progress(get_current_user_id(), _tz_minutes())})


@api_bp.route("/me/goal", methods=["PUT"])
@login_required_api
def set_my_weekly_goal():
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import set_weekly_post_goal
    from services.calendar_service import week_progress

    try:
        goal = int((request.get_json() or {}).get("weekly_post_goal"))
    except (TypeError, ValueError):
        goal = 0
    if not 1 <= goal <= 7:
        return jsonify({"success": False, "error": "Choose a goal between 1 and 7 posts a week."}), 400
    user_id = get_current_user_id()
    set_weekly_post_goal(user_id, goal)
    return jsonify({"success": True, "progress": week_progress(user_id, _tz_minutes())})


@api_bp.route("/plans", methods=["GET"])
@login_required_api
def plans_list():
    """Every pricing plan, for Settings -> Plan & usage (prices in INR and USD)."""
    if not DB_AVAILABLE:
        return jsonify({"success": True, "plans": []})
    from db import list_plans, user_currency

    return jsonify({"success": True, "plans": list_plans(), "currency": user_currency(get_current_user_id())})


@api_bp.route("/me/plan", methods=["GET"])
@login_required_api
def my_plan():
    """The user's plan and what is left of it this month (Settings -> Plan & usage)."""
    if not DB_AVAILABLE:
        return jsonify({"success": True, "usage": None})
    from db import get_plan_usage, user_currency

    user_id = get_current_user_id()
    return jsonify({"success": True, "usage": get_plan_usage(user_id), "currency": user_currency(user_id)})


# ── Billing: Stripe (services/billing_service.py) ────────────────────────


@api_bp.route("/billing/options", methods=["GET"])
@login_required_api
def billing_options():
    """What can be bought online, at this user's prices, all through Stripe:
    rupees for Indian brands (GST shown separately), dollars for everyone
    else."""
    from db import list_plans, user_currency
    from services import billing_service

    user_id = get_current_user_id()
    currency = user_currency(user_id)
    items = []
    candidates = [f"plan:{p['slug']}:{period}" for p in list_plans() for period in ("month", "year")]
    for item in candidates + [f"pack:{key}" for key in billing_service.PACKS]:
        try:
            items.append(billing_service.price_item(user_id, item, currency))
        except billing_service.BillingError:
            pass
    from db import current_subscription

    return jsonify({"success": True, "enabled": billing_service.enabled(), "currency": currency,
                    "subscription": current_subscription(user_id), "provider": "stripe",
                    "gst_rate": Config.GST_RATE if currency == "INR" else 0, "items": items})


@api_bp.route("/billing/stripe/checkout", methods=["POST"])
@login_required_api
def billing_stripe_checkout():
    """Body: {item}. A Stripe Checkout Session at the server's price in the
    user's currency; the browser goes to its url."""
    from db import user_currency
    from services import billing_service

    user_id = get_current_user_id()
    item = str((request.get_json() or {}).get("item") or "")[:48]
    try:
        session = billing_service.create_stripe_session(user_id, item, Config.APP_BASE_URL or request.host_url,
                                                        user_currency(user_id))
    except billing_service.BillingError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    return jsonify({"success": True, **session})


@api_bp.route("/billing/stripe/confirm", methods=["POST"])
@login_required_api
def billing_stripe_confirm():
    """Back from Stripe: body {session_id}. Applied only if Stripe says it was paid."""
    from db import get_plan_usage
    from services import billing_service

    user_id = get_current_user_id()
    try:
        applied = billing_service.confirm_stripe_session(user_id, str((request.get_json() or {}).get("session_id") or ""))
    except billing_service.BillingError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    return jsonify({"success": True, "applied": applied, "already_applied": applied is None, "usage": get_plan_usage(user_id)})


@api_bp.route("/billing/stripe/webhook", methods=["POST"])
def billing_stripe_webhook():
    """Stripe -> us, signed (Stripe-Signature) with the endpoint's secret."""
    from services import billing_service

    status = billing_service.handle_stripe_webhook(request.get_data(), request.headers.get("Stripe-Signature", ""))
    if status == "bad_signature":
        current_app.logger.warning("Stripe webhook with a bad signature was refused")
        return jsonify({"success": False}), 400
    return jsonify({"success": True, "status": status})


@api_bp.route("/webhooks/meta", methods=["GET", "POST"])
def meta_webhook():
    """Meta -> us: new comments on Facebook Pages and Instagram accounts as
    they happen (services/comment_service.py handle_webhook). GET is Meta's
    one-time check of the verify token; POST is signed (X-Hub-Signature-256).
    Off while META_WEBHOOK_VERIFY_TOKEN is empty."""
    from services import comment_service

    if not Config.META_WEBHOOK_VERIFY_TOKEN:
        return jsonify({"success": False, "error": "Webhooks are not set up"}), 404
    if request.method == "GET":
        import hmac

        if request.args.get("hub.mode") == "subscribe" and hmac.compare_digest(
                request.args.get("hub.verify_token", ""), Config.META_WEBHOOK_VERIFY_TOKEN):
            return request.args.get("hub.challenge", ""), 200, {"Content-Type": "text/plain"}
        return jsonify({"success": False}), 403
    raw = request.get_data()
    if not comment_service.webhook_signature_ok(raw, request.headers.get("X-Hub-Signature-256", "")):
        current_app.logger.warning("Meta webhook with a bad signature was refused")
        return jsonify({"success": False}), 403
    try:
        added = comment_service.handle_webhook(request.get_json(silent=True) or {})
    except Exception as err:  # noqa: BLE001 - 200 anyway, so Meta doesn't keep resending: the scheduled check picks them up
        current_app.logger.warning(f"Meta webhook not handled: {err}")
        added = 0
    return jsonify({"success": True, "new_comments": added})


@api_bp.route("/me/billing-profile", methods=["GET"])
@login_required_api
def my_billing_profile():
    """Name, GSTIN, address and state printed on the user's GST invoices."""
    from db import get_billing_profile
    from services.invoice_service import STATES

    return jsonify({"success": True, "profile": get_billing_profile(get_current_user_id()), "states": sorted(STATES.values())})


@api_bp.route("/me/billing-profile", methods=["PUT"])
@login_required_api
def save_my_billing_profile():
    from db import save_billing_profile
    from services.invoice_service import STATES, valid_gstin

    data = request.get_json() or {}
    gstin = str(data.get("gstin") or "").strip().upper()
    state = str(data.get("state") or "").strip()
    if gstin:
        if not valid_gstin(gstin):
            return jsonify({"success": False, "error": "That GSTIN doesn't look right (15 characters, e.g. 24ABCDE1234F1Z5)."}), 400
        state = STATES[gstin[:2]]  # the GSTIN decides the state
    if state and state not in STATES.values():
        return jsonify({"success": False, "error": "Choose a state from the list."}), 400
    profile = save_billing_profile(get_current_user_id(), str(data.get("name") or "").strip()[:160],
                                   gstin, str(data.get("address") or "").strip()[:400], state)
    return jsonify({"success": True, "profile": profile})


@api_bp.route("/admin/payments", methods=["GET"])
@login_required_api
@admin_required_api
def admin_payments():
    from db import list_payments

    try:
        user = int(request.args["user"]) if request.args.get("user") else None
    except ValueError:
        return jsonify({"success": False, "error": "user must be a number"}), 400
    return jsonify({"success": True, "payments": list_payments(200, user)})


@api_bp.route("/admin/subscriptions", methods=["GET"])
@login_required_api
@admin_required_api
def admin_subscriptions():
    from db import list_subscriptions

    return jsonify({"success": True, "subscriptions": list_subscriptions()})


@api_bp.route("/admin/users/<int:target_user_id>/subscription/cancel", methods=["POST"])
@login_required_api
@admin_required_api
def admin_cancel_subscription(target_user_id):
    """Stop a user's automatic renewal (their paid time is kept)."""
    from services import billing_service

    try:
        subscription = billing_service.cancel_subscription(target_user_id, actor_user_id=get_current_user_id())
    except billing_service.BillingError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    return jsonify({"success": True, "subscription": subscription})


@api_bp.route("/admin/payments/<order_id>/refund", methods=["POST"])
@login_required_api
@admin_required_api
def admin_refund_payment(order_id):
    """Body: {amount} in rupees / dollars, or empty for everything not yet refunded."""
    from services import billing_service

    raw = (request.get_json() or {}).get("amount")
    try:
        amount = None if raw in (None, "") else int(round(float(raw) * 100))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "The amount must be a number."}), 400
    try:
        result = billing_service.refund(order_id, amount, get_current_user_id())
    except billing_service.BillingError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    return jsonify({"success": True, **result})


@api_bp.route("/billing/subscribe", methods=["POST"])
@login_required_api
def billing_subscribe():
    """Body: {plan, period: month|year}. Starts automatic renewal: returns a
    Stripe Checkout url."""
    from db import user_currency
    from services import billing_service

    data = request.get_json() or {}
    user_id = get_current_user_id()
    try:
        started = billing_service.start_subscription(user_id, str(data.get("plan") or ""), str(data.get("period") or ""),
                                                     user_currency(user_id), Config.APP_BASE_URL or request.host_url)
    except billing_service.BillingError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    return jsonify({"success": True, **started})


@api_bp.route("/billing/subscription/cancel", methods=["POST"])
@login_required_api
def billing_subscription_cancel():
    """Stop automatic renewal; the paid time is kept."""
    from services import billing_service

    try:
        subscription = billing_service.cancel_subscription(get_current_user_id())
    except billing_service.BillingError as err:
        return jsonify({"success": False, "error": str(err)}), 400
    return jsonify({"success": True, "subscription": subscription})


@api_bp.route("/billing/history", methods=["GET"])
@login_required_api
def billing_history():
    from db import list_user_payments

    return jsonify({"success": True, "payments": list_user_payments(get_current_user_id())})


@api_bp.route("/admin/audit-log", methods=["GET"])
@login_required_api
@admin_required_api
def admin_audit_log():
    """Newest first. ?user=<id> for one user's changes, ?action=plan for one kind, ?limit=."""
    from db import list_audit_log

    try:
        limit = int(request.args.get("limit", 100))
        user = int(request.args["user"]) if request.args.get("user") else None
    except ValueError:
        return jsonify({"success": False, "error": "limit and user must be numbers"}), 400
    return jsonify({"success": True, "entries": list_audit_log(limit, user, (request.args.get("action") or "").strip() or None)})


@api_bp.route("/admin/plans", methods=["GET"])
@login_required_api
@admin_required_api
def admin_plans():
    from db import list_plans

    return jsonify({"success": True, "plans": list_plans()})


@api_bp.route("/admin/plans/<slug>", methods=["PUT"])
@login_required_api
@admin_required_api
def admin_update_plan(slug):
    """Change a plan's prices or limits. Limits: whole numbers, or null for unlimited."""
    from db import PLAN_FIELDS, list_plans, update_plan

    data = request.get_json() or {}
    fields = {}
    for key, value in data.items():
        if key not in PLAN_FIELDS:
            continue
        if key in ("name", "best_for", "support"):
            fields[key] = str(value or "").strip()[:120] or None
        elif key == "approvals":
            fields[key] = bool(value)
        elif value is None or value == "":
            fields[key] = None
        else:
            try:
                number = float(value) if key.startswith(("price", "annual")) else int(value)
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": f"{key} must be a number or empty"}), 400
            if number < 0:
                return jsonify({"success": False, "error": f"{key} can't be negative"}), 400
            fields[key] = number
    if fields.get("name") is None and "name" in fields:
        return jsonify({"success": False, "error": "A plan needs a name"}), 400
    before = next((p for p in list_plans() if p["slug"] == slug), None)
    plan = update_plan(slug, fields)
    if not plan:
        return jsonify({"success": False, "error": "Plan not found"}), 404
    changes = {k: {"from": (before or {}).get(k), "to": v} for k, v in fields.items() if (before or {}).get(k) != v}
    _audit("plan.updated", None, {"plan": slug, "changes": changes})
    return jsonify({"success": True, "plan": plan})


@api_bp.route("/admin/users/<int:target_user_id>/plan", methods=["GET"])
@login_required_api
@admin_required_api
def admin_user_plan(target_user_id):
    from db import get_plan_usage

    usage = get_plan_usage(target_user_id)
    if not usage:
        return jsonify({"success": False, "error": "User not found"}), 404
    return jsonify({"success": True, "usage": usage})


@api_bp.route("/admin/users/<int:target_user_id>/plan", methods=["PUT"])
@login_required_api
@admin_required_api
def admin_set_user_plan(target_user_id):
    """Body: {plan: "starter"}. Until payments exist, admins assign plans."""
    from db import assign_plan, get_plan_usage

    slug = str((request.get_json() or {}).get("plan") or "").strip()
    before = (get_plan_usage(target_user_id) or {}).get("plan", {}).get("slug")
    if not assign_plan(target_user_id, slug):
        return jsonify({"success": False, "error": "Unknown user or plan"}), 404
    if before != slug:
        _audit("plan.assigned", target_user_id, {"from": before, "to": slug})
    return jsonify({"success": True, "usage": get_plan_usage(target_user_id)})


@api_bp.route("/admin/users/<int:target_user_id>/packs", methods=["POST"])
@login_required_api
@admin_required_api
def admin_grant_pack(target_user_id):
    """Body: {kind: "image" | "video", quantity: n, note}. Adds a top-up pack (12 months)."""
    from db import get_plan_usage, grant_pack

    data = request.get_json() or {}
    try:
        quantity = int(data.get("quantity"))
    except (TypeError, ValueError):
        quantity = 0
    if not 1 <= quantity <= 1000:
        return jsonify({"success": False, "error": "Quantity must be between 1 and 1000"}), 400
    pack = grant_pack(target_user_id, str(data.get("kind") or ""), quantity, data.get("note"))
    if not pack:
        return jsonify({"success": False, "error": "Unknown user, or kind must be image or video"}), 400
    _audit("pack.granted", target_user_id, {"kind": pack["kind"], "quantity": pack["quantity"], "note": data.get("note")})
    return jsonify({"success": True, "pack": pack, "usage": get_plan_usage(target_user_id)})


@api_bp.route("/me/bell", methods=["GET"])
@login_required_api
def my_bell():
    """The header bell: unread count + the latest nudges (services/nudge_service.py)."""
    if not DB_AVAILABLE:
        return jsonify({"success": True, "unread": 0, "items": []})
    from db import list_notifications

    return jsonify({"success": True, **list_notifications(get_current_user_id())})


@api_bp.route("/me/bell/read", methods=["POST"])
@login_required_api
def read_my_bell():
    """Body: {ids: [..]} marks those read; no ids marks everything read."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from db import list_notifications, mark_notifications_read

    ids = (request.get_json(silent=True) or {}).get("ids")
    user_id = get_current_user_id()
    mark_notifications_read(user_id, ids if isinstance(ids, list) else None)
    return jsonify({"success": True, "unread": list_notifications(user_id, limit=1)["unread"]})


@api_bp.route("/me/notifications/ideas-email/send-now", methods=["POST"])
@login_required_api
@limit_by_user("email_user")
def send_my_ideas_email_now():
    """Settings -> Notifications -> "Send me this week's ideas now": the same
    email the weekly run sends, to the logged-in user's own address."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    from datetime import datetime, timezone

    from db import get_ideas_email_settings
    from services.idea_digest_service import send_ideas_email

    user_id = get_current_user_id()
    settings = get_ideas_email_settings(user_id) or {}
    if settings.get("last_sent_at"):
        last = datetime.fromisoformat(settings["last_sent_at"])
        if (datetime.now(timezone.utc) - last).total_seconds() < 120:
            return jsonify({"success": False, "error": "We just sent you one - check your inbox in a minute."}), 429
    try:
        outcome = send_ideas_email(user_id, Config.APP_BASE_URL or request.host_url)
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning(f"[Ideas email] Could not send to user {user_id}: {e}")
        return jsonify({"success": False, "error": "The email could not be sent. Please try again later."}), 502
    if not outcome["sent"]:
        return jsonify({"success": False, "error": "There are no ideas to send yet - add your website in My Brand Configuration first."}), 409
    return jsonify({"success": True, "email": settings.get("email"), "ideas": outcome["ideas"]})


_UNSUBSCRIBE_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Email settings - AVIR AI</title></head>
<body style="margin:0;background:#f8f9fc;font-family:'Segoe UI',Arial,sans-serif;color:#172033">
<div style="max-width:440px;margin:12vh auto;padding:32px;background:#fff;border:1px solid #e6e8ef;border-radius:16px;text-align:center">
<h1 style="font-size:20px;margin:0 0 10px">{title}</h1>
<p style="font-size:14.5px;line-height:1.6;color:#475569;margin:0 0 22px">{text}</p>
{action}
</div></body></html>"""


@api_bp.route("/notifications/ideas-email/unsubscribe/<token>", methods=["GET", "POST"], defaults={"kind": "ideas"})
@api_bp.route("/notifications/nudges-email/unsubscribe/<token>", methods=["GET", "POST"], defaults={"kind": "nudges"})
@api_bp.route("/notifications/recap-email/unsubscribe/<token>", methods=["GET", "POST"], defaults={"kind": "recap"})
@api_bp.route("/notifications/results-email/unsubscribe/<token>", methods=["GET", "POST"], defaults={"kind": "results"})
def unsubscribe_ideas_email(token, kind):
    """The unsubscribe link of the weekly ideas email (kind "ideas") or of
    the nudge emails ("nudges") - works without logging in (the token is
    signed, and only valid for its own kind). Opening it only asks; the
    button, or a mail app's one-click unsubscribe (a POST), switches that
    email off - so a mail scanner that pre-opens links can't unsubscribe anyone."""
    from html import escape

    from services.idea_digest_service import user_from_unsubscribe_token

    what = {"ideas": "weekly ideas email", "nudges": "reminder emails", "recap": "monthly recap email",
            "results": "weekly results email"}[kind]
    user_id = user_from_unsubscribe_token(token, kind)
    if not user_id or not DB_AVAILABLE:
        return _UNSUBSCRIBE_PAGE.format(
            title="This link isn't valid", action="",
            text="We couldn't read this unsubscribe link. You can switch these emails off under Settings &rarr; Notifications.",
        ), 400
    if request.method == "POST":
        from db import (
            set_ideas_email_enabled,
            set_nudge_email_enabled,
            set_recap_email_enabled,
            set_results_email_enabled,
        )

        {"ideas": set_ideas_email_enabled, "nudges": set_nudge_email_enabled, "recap": set_recap_email_enabled,
         "results": set_results_email_enabled}[kind](user_id, False)
        return _UNSUBSCRIBE_PAGE.format(
            title="You're unsubscribed", action="",
            text=f"You won't get the {what} any more. You can switch it back on any time under Settings &rarr; Notifications.",
        )
    return _UNSUBSCRIBE_PAGE.format(
        title=f"Stop the {what}?",
        text=f"You'll no longer get the {what}. Account emails, like password resets, are not affected.",
        action=(
            f'<form method="post" action="{escape(request.path)}">'
            '<button type="submit" style="background:#c2410c;color:#fff;border:0;border-radius:6px;padding:12px 26px;'
            'font-size:15px;font-weight:700;cursor:pointer">Unsubscribe</button></form>'
        ),
    )


@api_bp.route("/brand-profile/quick-prompts/generate", methods=["POST"])
@login_required_api
@limit_by_user("ai_user")
def generate_brand_quick_prompts():
    """Studio Chat's regenerate button: writes genuinely NEW "Start from an
    idea" cards from the user's whole brand profile (see
    brand_profile_service.generate_post_ideas) instead of reshuffling the
    onboarding-time pool. Body: {exclude_titles: [titles currently shown]}."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database not available"}), 503

    try:
        stats = get_user_usage_stats(user_id)
        if _credit_blocked(stats):
            return jsonify({"error": "Credit limit reached. Please request a credit extension.", "credit_limit_exceeded": True}), 402
    except Exception as _cred_err:
        current_app.logger.warning(f"[Credits] Check error: {_cred_err}")

    from services.brand_profile_service import generate_post_ideas

    data = request.get_json(silent=True) or {}
    try:
        ideas, greeting, _usage = generate_post_ideas(user_id, exclude_titles=data.get("exclude_titles") or [])
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Could not generate new ideas: {e}"}), 500
    if not ideas:
        return jsonify({"error": "No brand profile to generate ideas from - analyze your website first."}), 404
    return jsonify({"success": True, "post_ideas": ideas, "greeting": greeting})


@api_bp.route("/brand-profile", methods=["GET"])
@login_required_api
def get_brand_profile():
    """Backs the "My Brand Configuration" page (Individual/Small/Medium
    only - see templates/brand_profile.html) - returns what was scraped
    from the user's website so they can review/correct it."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"success": True, "profile": None})

    from db import get_user_brand_profile

    return jsonify({"success": True, "profile": get_user_brand_profile(user_id)})


@api_bp.route("/brand-profile", methods=["PUT"])
@login_required_api
def update_brand_profile():
    """Saves user edits to their own brand profile - see
    db.update_user_brand_profile_fields. Individual/Small/Medium only;
    Enterprise/admin accounts have no brand profile to edit."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database not available"}), 503

    user = get_user_by_id(user_id)
    if not user or user.account_type not in VALID_SELF_SERVE_ACCOUNT_TYPES:
        return jsonify({"error": "Brand configuration is only available for Individual/Small/Medium accounts"}), 403

    data = request.get_json() or {}
    from db import update_user_brand_profile_fields

    fields = {}
    for key in ("company_name", "industry", "target_audience", "brand_voice_summary", "tagline", "visual_style"):
        if key in data:
            fields[key] = (data[key] or "").strip()
    for key in ("key_themes", "primary_colors", "content_dos", "content_donts", "fonts", "core_products"):
        if key in data:
            fields[key] = [item.strip() for item in (data[key] or []) if isinstance(item, str) and item.strip()]
    if "country" in data:  # sets the composer's post languages
        from services.language_service import normalize_country

        fields["country"] = normalize_country(data["country"])

    try:
        res = update_user_brand_profile_fields(user_id, **fields)
        if not res:
            return jsonify({"error": "No brand profile found - run a website analysis first"}), 404
        return jsonify({"success": True})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


@api_bp.route("/brand-profile/rescan", methods=["POST"])
@login_required_api
@limit_by_user("ai_user")
def rescan_brand_profile():
    """Manually re-runs the website scrape + brand analysis (see
    services/brand_profile_service.py::run_brand_analysis) - unlike
    onboarding's best-effort call, failure here is reported to the user
    since they explicitly asked for it."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database not available"}), 503

    user = get_user_by_id(user_id)
    if not user or user.account_type not in VALID_SELF_SERVE_ACCOUNT_TYPES:
        return jsonify({"error": "Brand configuration is only available for Individual/Small/Medium accounts"}), 403

    data = request.get_json() or {}
    website = (data.get("website") or user.company_website or "").strip()
    # The user's own description of their business - the fallback when their
    # site blocks automated access or has no readable text.
    manual_text = (data.get("manual_text") or "").strip()
    if not website and not manual_text:
        return jsonify({"error": "No website on file - enter one to analyze"}), 400
    if manual_text and len(manual_text) < 80:
        return jsonify({"error": "Please write at least a few sentences about your business."}), 400

    # "background": onboarding's Retry button - start the scan and return at
    # once; the page polls GET /api/brand-profile/status for progress, exactly
    # like the first attempt. scan_started=False means one is already running,
    # which the page simply keeps polling.
    if data.get("background") and website and not manual_text:
        try:
            from services.brand_profile_service import start_brand_analysis_async

            return jsonify({"success": True, "scan_started": start_brand_analysis_async(user_id, website)})
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e), "success": False}), 500

    try:
        from services.brand_profile_service import failure_message, run_brand_analysis

        ok, reason = run_brand_analysis(user_id, website, manual_text=manual_text or None)
        if reason == "already_running":
            return jsonify({"error": "An analysis is already running - it'll appear here when it finishes."}), 409
        if not ok:
            return jsonify({"error": failure_message(reason), "reason": reason}), 502

        from db import get_user_brand_profile

        return jsonify({"success": True, "profile": get_user_brand_profile(user_id)})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), "success": False}), 500


COMPLIANCE_NOTICE = (
    "Compliance guidance only - not legal advice. Rules are summaries of the cited sources and are "
    "pending legal review; confirm requirements with your counsel before publishing."
)


def _compliance_payload(industry: str | None, regions: list, excluded: list) -> dict:
    from services.compliance_rules import RULES_VERSION, applicable_rules

    return {"rules": applicable_rules(industry, regions, excluded), "rules_version": RULES_VERSION}


@api_bp.route("/compliance-profile", methods=["GET"])
@login_required_api
def get_compliance_profile():
    """The user's compliance settings (industry + markets, detected until
    they confirm them) and the rules those select - see
    services/compliance_rules.py. Backs the Compliance card on the
    "My Brand Configuration" page."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database not available"}), 503

    from db import get_user_brand_profile
    from services.compliance_rules import (
        INDUSTRIES,
        REGIONS,
        normalize_industry,
        normalize_regions,
    )

    profile = get_user_brand_profile(user_id)
    if not profile:
        return jsonify({"error": "No brand profile found - run a website analysis first"}), 404

    industry = normalize_industry(profile.get("industry_category") or profile.get("industry_category_detected"))
    regions = normalize_regions(profile.get("compliance_regions") or profile.get("regions_detected"))
    excluded = profile.get("compliance_excluded_rules") or []
    return jsonify(
        {
            "success": True,
            "industries": [{"key": k, "label": v} for k, v in INDUSTRIES.items()],
            "regions_available": list(REGIONS),
            "industry_category": industry,
            "industry_category_detected": profile.get("industry_category_detected"),
            "regions": regions,
            "regions_detected": profile.get("regions_detected") or [],
            "excluded_rule_ids": excluded,
            "confirmed_at": profile.get("compliance_confirmed_at"),
            "notice": COMPLIANCE_NOTICE,
            **_compliance_payload(industry, regions, excluded),
        }
    )


@api_bp.route("/compliance-profile/preview", methods=["GET"])
@login_required_api
def preview_compliance_rules():
    """Rules for an industry/markets selection the user hasn't saved yet -
    live preview as they change the Compliance card's dropdown/checkboxes."""
    from services.compliance_rules import normalize_industry, normalize_regions

    industry = normalize_industry(request.args.get("industry"))
    regions = normalize_regions([r for r in (request.args.get("regions") or "").split(",") if r])
    excluded = [r for r in (request.args.get("excluded") or "").split(",") if r]
    return jsonify({"success": True, **_compliance_payload(industry, regions, excluded)})


@api_bp.route("/compliance-profile", methods=["PUT"])
@login_required_api
def update_compliance_settings():
    """Confirms the user's industry, markets and not-applicable rules - from
    then on a website re-scan no longer overwrites them."""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database not available"}), 503

    user = get_user_by_id(user_id)
    if not user or user.account_type not in VALID_SELF_SERVE_ACCOUNT_TYPES:
        return jsonify({"error": "Brand configuration is only available for Individual/Small/Medium accounts"}), 403

    from db import update_compliance_profile
    from services.compliance_rules import INDUSTRIES, normalize_regions, rule_by_id

    data = request.get_json() or {}
    industry = data.get("industry_category")
    if industry not in INDUSTRIES:
        return jsonify({"error": "Please choose an industry from the list"}), 400
    regions = normalize_regions(data.get("regions"))
    if not regions:
        return jsonify({"error": "Select at least one market"}), 400
    excluded = [rid for rid in (data.get("excluded_rule_ids") or []) if rule_by_id(rid)]

    if not update_compliance_profile(user_id, industry, regions, excluded):
        return jsonify({"error": "No brand profile found - run a website analysis first"}), 404
    return jsonify({"success": True, **_compliance_payload(industry, regions, excluded)})


@api_bp.route("/onboarding/contact-sales", methods=["POST"])
@login_required_api
def onboarding_contact_sales():
    """Final step of the Enterprise onboarding path - see
    templates/onboarding_contact_sales.html. Saves the lead and notifies
    Config.SALES_EMAIL; does not grant dashboard access (no self-serve tier
    for Enterprise)."""
    data = request.get_json() or {}
    company_name = (data.get("company_name") or "").strip()
    phone = (data.get("phone") or "").strip()
    message = (data.get("message") or "").strip()
    user_id = get_current_user_id()

    if not company_name:
        return jsonify({"success": False, "error": "Company name is required"}), 400

    from db import create_sales_contact_request, get_user_by_id

    create_sales_contact_request(user_id, company_name, phone, message)

    user = get_user_by_id(user_id)
    try:
        from services.email_service import EmailService

        EmailService().send_sales_lead_notification(
            user.name, user.email, {"company_name": company_name, "phone": phone, "message": message}
        )
    except Exception as e:  # noqa: BLE001
        # The lead is already saved - a notification-email failure (SMTP not
        # configured, SALES_EMAIL unset) shouldn't block the user's submission.
        current_app.logger.warning(f"[onboarding] Failed to send sales lead notification: {e}")

    return jsonify({"success": True})
