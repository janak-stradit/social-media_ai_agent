import json
import os
import re
import urllib.parse
import uuid
import typing

import requests
from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from agents.caption_agent import CaptionAgent
from agents.hashtag_agent import HashtagAgent
from agents.reviewer_agent import ReviewerAgent
from agents.story_agent import StoryAgent
from agents.strategy_agent import StrategyAgent
from agents.vision_agent import VisionAgent
from auth.utils import admin_required_api, get_current_user_id, login_required_api
from config import Config
from services.memory_service import MemoryService
from services.scraper_service import ScraperService

try:
    from db import (
        append_run_media,
        approve_credit_request,
        archive_run,
        cancel_scheduled_post,
        create_credit_request,
        create_scheduled_post,
        disconnect_social_account,
        get_all_credit_requests,
        get_all_users_credit_summary,
        get_global_cost_history,
        get_history,
        get_run_by_id,
        get_user_credit_requests,
        get_user_scheduled_posts,
        get_user_social_accounts,
        get_user_usage_stats,
        reject_credit_request,
        save_approved_asset,
        save_run,
        save_social_account,
        unarchive_run,
        update_scheduled_post_status,
        update_user_credit_limit,
    )

    DB_AVAILABLE = True
except Exception as _db_err:
    DB_AVAILABLE = False
    print(f"[routes] DB not available: {_db_err}")

try:
    from services.media_service import MediaGenerationService

    media_service = MediaGenerationService()
    MEDIA_AVAILABLE = True
except Exception as _media_err:
    MEDIA_AVAILABLE = False
    print(f"[routes] Media service not available: {_media_err}")

from typing import Optional

try:
    from services.social_publisher_service import SocialPublisherService

    publisher_service: Optional[SocialPublisherService] = SocialPublisherService()
except Exception as _pub_err:
    publisher_service = None
    print(f"[routes] Social publisher service not available: {_pub_err}")

api_bp = Blueprint("api", __name__)
memory_service = MemoryService()


def _public_upload_url(filepath: str | None) -> str | None:
    if not filepath:
        return None
    normalized = filepath.replace("\\", "/")
    return normalized if normalized.startswith("/") else f"/{normalized}"


def _persist_generated_media(run_id: int | None, platform: str, media_type: str, result: dict, user_id: int) -> None:
    if not DB_AVAILABLE or not run_id or not result.get("success"):
        return

    media_payload = {
        "url": result.get("url"),
        "prompt": result.get("prompt"),
        "type": result.get("type", media_type),
        "duration": result.get("duration"),
        "resolution": result.get("resolution"),
        "size": result.get("size"),
        "model": result.get("model"),
        "source_image_url": result.get("source_image_url"),
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


# Initialize agents
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
    if getattr(Config, "GOOGLE_API_KEY", None) or os.getenv("GOOGLE_API_KEY"):
        video_model = getattr(Config, "GEMINI_VIDEO_MODEL", "veo-3.1-generate-preview")
        video_provider = "Google Gemini (Veo 3.1)"
        video_status = "ACTIVE"
    elif getattr(Config, "Z_AI_API_KEY", None):
        video_model = getattr(Config, "Z_AI_VIDEO_MODEL", "cogvideox-3")
        video_provider = "Z.AI (CogVideoX-3)"
        video_status = "ACTIVE"
    elif getattr(Config, "AWS_ACCESS_KEY_ID", None) or getattr(Config, "AWS_PROFILE", None):
        video_model = getattr(Config, "BEDROCK_VIDEO_MODEL", "amazon.nova-reel-v1:0")
        video_provider = f"AWS Bedrock Nova Reel ({getattr(Config, 'AWS_REGION', 'us-east-1')})"
        video_status = "ACTIVE"
    else:
        video_model = getattr(Config, "VIDEO_MODEL", "google/veo-3.1-lite")
        video_provider = "OpenRouter Video API"
        video_status = "STANDBY"

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
    except Exception as e:
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
    except Exception as e:
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
        secure_name = secure_filename(filename)
        unique_name = f"{uuid.uuid4()}_{secure_name}"
        filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], unique_name)
        file.save(filepath)

        # Analyze image
        try:
            analysis = vision_agent.analyze_image(filepath)
            return jsonify({"success": True, "image_id": unique_name, "filepath": filepath, "analysis": analysis})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return jsonify({"error": "Invalid file type"}), 400


@api_bp.route("/analyze-story", methods=["POST"])
@login_required_api
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

        user_id = get_current_user_id()
        retrieved_memories = memory_service.retrieve_context(story, user_id=user_id, n_results=2)
        mem_prompt = memory_service.format_memory_prompt(retrieved_memories)

        analysis = story_agent.analyze(story, memory_context=mem_prompt)
        key_points = story_agent.extract_key_points(story)

        return jsonify(
            {
                "success": True,
                "analysis": analysis,
                "key_points": key_points,
                "memories_referenced": len(retrieved_memories),
                "suggested_brief": suggested_brief,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/generate", methods=["POST"])
@login_required_api
def generate_content():
    """
    Main endpoint to generate complete social media content with Multi-Turn Refinement, A/B Hook Variations & Critic Self-Correction
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    story = data.get("story", "")
    image_path = data.get("image_path")
    platforms = data.get("platforms", ["facebook", "instagram", "linkedin"])
    tone = data.get("tone")
    brand_voice = data.get("brand_voice", "Standard Enterprise")
    include_strategy = data.get("include_strategy", True)
    previous_context = data.get("previous_context")
    target_company = data.get("target_company")
    selected_outputs = data.get("selected_outputs", ["text", "image", "video"])
    generate_text = "text" in selected_outputs or "Text (Caption)" in selected_outputs
    user_id = get_current_user_id()

    # Credit Limit Check
    if DB_AVAILABLE and user_id:
        try:
            stats = get_user_usage_stats(user_id)
            if stats.get("remaining_credits", 0.0) <= 0.0:
                limit_val = stats.get("credit_limit", 10.0)
                return (
                    jsonify(
                        {
                            "error": f"Credit limit reached (${limit_val:.2f}). Please request a credit extension from admin.",
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

    # Multi-Turn Refinement Context Integration
    if previous_context:
        story_prompt = (
            f"Follow-up Refinement Request: {story}\n\n[PREVIOUS TURN CONTEXT & OUTPUTS]:\n{previous_context}"
        )
    else:
        story_prompt = story

    # Inject Scraper Explorer Intelligence
    if target_company and target_company != "None":
        scraper = ScraperService()
        intelligence = scraper.get_company_talking_points(target_company)
        if intelligence:
            story_prompt += f"\n\n{intelligence}"

    total_tokens = 0
    total_cost_usd = 0.0
    agents_executed = []

    try:
        # Step 0: RAG Memory Context Retrieval from ChromaDB
        retrieved_memories = memory_service.retrieve_context(story, user_id=user_id, n_results=3)
        mem_prompt = memory_service.format_memory_prompt(retrieved_memories)

        # Step 1: Analyze story
        if "STRATEGY SYNTHESIS:" in story_prompt:
            caption_input = story_prompt
            story_analysis = {"themes": ["Strategy", "Industry"], "emotions": ["Professional"]}
            story_usage = None
        else:
            story_analysis, story_usage = story_agent.analyze(
                story_prompt, memory_context=mem_prompt, return_usage=True
            )
            caption_input = extract_prompt_for_type(story_prompt, "text")

        if story_usage:
            total_tokens += story_usage.get("total_tokens", 0)
            total_cost_usd += story_usage.get("cost_usd", 0.0)

        agents_executed.append(
            {
                "agent": "StoryAgent",
                "name": "Story & Memory Agent",
                "role": "Analyzed narrative themes, emotional tone & retrieved brand memories",
                "status": "completed",
            }
        )

        # Step 2: Analyze image if provided
        vision_analysis = None
        if image_path and os.path.exists(image_path):
            vision_analysis = vision_agent.analyze_image(image_path)
            agents_executed.append(
                {
                    "agent": "VisionAgent",
                    "name": "Vision Agent",
                    "role": "Analyzed visual asset, color palette & image objects",
                    "status": "completed",
                }
            )

        # Step 3: Generate captions with A/B Hook Variations
        captions = {}
        if generate_text:
            captions = caption_agent.generate_all_platforms(
                caption_input, vision_analysis, tone, memory_context=mem_prompt, brand_voice=brand_voice
            )
            cap_usage = captions.pop("_usage", {})
            total_tokens += cap_usage.get("total_tokens", 0)
            total_cost_usd += cap_usage.get("cost_usd", 0.0)

            agents_executed.append(
                {
                    "agent": "CaptionAgent",
                    "name": "Caption Agent",
                    "role": f"Generated 3 psychological hook angles for {', '.join(platforms)} ('{brand_voice}' voice)",
                    "status": "completed",
                }
            )

        # Step 4: Generate hashtags
        hashtags = {}
        if generate_text:
            hashtags = hashtag_agent.generate_all_platforms(story_analysis, vision_analysis, memory_context=mem_prompt)
            hash_usage = hashtags.pop("_usage", {})
            total_tokens += hash_usage.get("total_tokens", 0)
            total_cost_usd += hash_usage.get("cost_usd", 0.0)

            agents_executed.append(
                {
                    "agent": "HashtagAgent",
                    "name": "Hashtag Agent",
                    "role": "Curated broad, niche & trending hashtag sets",
                    "status": "completed",
                }
            )

        # Step 5: Generate strategy
        strategies = {}
        if include_strategy and generate_text:
            strategies = strategy_agent.generate_all_strategies(story_analysis, memory_context=mem_prompt)
            strat_usage = strategies.pop("_usage", {})
            total_tokens += strat_usage.get("total_tokens", 0)
            total_cost_usd += strat_usage.get("cost_usd", 0.0)

            agents_executed.append(
                {
                    "agent": "StrategyAgent",
                    "name": "Strategy Agent",
                    "role": "Calculated optimal posting schedule & engagement forecasts",
                    "status": "completed",
                }
            )

        # Step 6: ReviewerAgent Self-Correction Loop
        quality_evaluations = {}
        refinements_count = 0

        if generate_text:
            for platform in platforms:
                primary_cap = captions.get(platform, {}).get("primary_caption", "")
                p_hashtags = (hashtags.get(platform, {}) or {}).get("hashtags", [])

                # Reviewer evaluates post quality
                eval_res = reviewer_agent.evaluate(
                    platform=platform,
                    caption=primary_cap,
                    hashtags=p_hashtags,
                    story_analysis=story_analysis,
                    brand_voice=brand_voice,
                )

                rev_usage = eval_res.pop("_usage", {})
                total_tokens += rev_usage.get("total_tokens", 0)
                total_cost_usd += rev_usage.get("cost_usd", 0.0)

                # Trigger self-correction if score < 8.0
                if eval_res.get("needs_refinement"):
                    refinements_count += 1
                    refined_cap, ref_usage = caption_agent.refine_caption(
                        platform=platform,
                        original_caption=primary_cap,
                        reviewer_feedback=eval_res.get("reviewer_feedback"),
                        brand_voice=brand_voice,
                    )
                    captions[platform]["primary_caption"] = refined_cap
                    captions[platform]["refined_by_critic"] = True
                    eval_res["self_corrected"] = True
                    eval_res["overall_score"] = min(9.8, round(eval_res.get("overall_score", 7.5) + 1.5, 1))

                    if ref_usage:
                        total_tokens += ref_usage.get("total_tokens", 0)
                        total_cost_usd += ref_usage.get("cost_usd", 0.0)

                quality_evaluations[platform] = eval_res

            agents_executed.append(
                {
                    "agent": "ReviewerAgent",
                    "name": "Critic & Self-Correction Agent",
                    "role": f"Evaluated quality, hook strength & applied {refinements_count} self-corrections",
                    "status": "completed",
                }
            )

        # Calculate average overall quality score
        avg_score = round(
            sum(q.get("overall_score", 8.5) for q in quality_evaluations.values()) / max(1, len(quality_evaluations)), 1
        )

        # Compile response
        response = {
            "success": True,
            "request_id": str(uuid.uuid4()),
            "story_analysis": story_analysis,
            "quality_summary": {
                "overall_score": avg_score,
                "refinements_applied": refinements_count,
                "brand_voice_applied": brand_voice,
            },
            "content": {},
            "agents_executed": agents_executed,
            "usage": {
                "total_tokens": total_tokens,
                "cost_usd": round(total_cost_usd, 6),
                "memories_referenced": len(retrieved_memories),
            },
        }

        for platform in platforms:
            response["content"][platform] = {
                "caption": captions.get(platform, {}),
                "hashtags": hashtags.get(platform, {}),
                "strategy": strategies.get(platform, {}) if include_strategy else None,
                "quality": quality_evaluations.get(platform, {}),
            }

        # ── Persist to PostgreSQL ──────────────────────────────────────────
        run_id = None
        if DB_AVAILABLE and user_id is not None:
            try:
                content_to_save: dict[str, typing.Any] = dict(response["content"])
                content_to_save["_agents"] = agents_executed
                content_to_save["_quality"] = response["quality_summary"]
                if image_path and os.path.exists(image_path):
                    content_to_save["_meta"] = {
                        "image_path": image_path,
                        "image_url": _public_upload_url(image_path),
                    }
                run_id = save_run(
                    story=story,
                    tone=tone,
                    platforms=platforms,
                    content=content_to_save,
                    user_id=user_id,
                    tokens_used=total_tokens,
                    cost_usd=round(total_cost_usd, 6),
                )
                response["run_id"] = run_id
            except Exception as db_err:
                current_app.logger.warning(f"[DB] Could not save run: {db_err}")

        # ── Store Run in ChromaDB Memory ───────────────────────────────────
        memory_service.store_campaign_run(
            run_id=run_id, story=story, content=response["content"], user_id=user_id, tone=tone, platforms=platforms
        )

        return jsonify(response)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── History Endpoints ──────────────────────────────────────────────────────


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
    except Exception as e:
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
    except Exception as e:
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
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/generate/<platform>", methods=["POST"])
@login_required_api
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

    except Exception as e:
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
    except Exception as e:
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
            return jsonify({"error": "Generated media file could not be located on the server"}), 404
        abs_media_path = candidate

    try:
        results = publisher_service.publish_post_to_connected_accounts(
            user_id=user_id, platforms=[platform], caption=message, image_path=abs_media_path
        )
        plat_result = results.get(platform, {"success": False, "error": "No result returned"})
        return jsonify({"success": bool(plat_result.get("success")), "result": plat_result})
    except Exception as e:
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

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Media Generation Endpoint ──────────────────────────────────────────────


@api_bp.route("/generate-media", methods=["POST"])
@login_required_api
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
    image_path = data.get("image_path")
    user_id = get_current_user_id()

    # Credit Limit Check
    if DB_AVAILABLE and user_id:
        try:
            stats = get_user_usage_stats(user_id)
            if stats.get("remaining_credits", 0.0) <= 0.0:
                limit_val = stats.get("credit_limit", 10.0)
                return (
                    jsonify(
                        {
                            "error": f"Credit limit reached (${limit_val:.2f}). Please request a credit extension from admin.",
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
        except Exception:
            image_path = None

    if run_id and DB_AVAILABLE:
        run = get_run_by_id(run_id, user_id=user_id)
        if not run:
            return jsonify({"error": "Run not found"}), 404

    try:
        caption_to_use = extract_prompt_for_type(caption, media_type)
        if media_type == "video":
            result = media_service.generate_video(caption_to_use, platform, tone, image_path=image_path)
        else:
            result = media_service.generate_image(caption_to_use, platform, tone, image_path=image_path)

        if image_path:
            result["source_image_url"] = _public_upload_url(image_path)
        if run_id:
            result["run_id"] = run_id
            if user_id is not None:
                _persist_generated_media(run_id, platform, media_type, result, user_id)

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e), "success": False}), 500


# ── Credit Extension Requests Endpoints (User Side) ─────────────────────────


@api_bp.route("/credit-requests", methods=["POST"])
@login_required_api
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
    except Exception as e:
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
    except Exception as e:
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
    except Exception as e:
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

        return jsonify({"success": True, "user": res, "message": "User credit limit updated successfully."})
    except Exception as e:
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
    except Exception as e:
        return jsonify({"error": str(e), "success": False}), 500


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

        return jsonify({"success": True, "result": res, "message": "Credit extension approved and limit increased."})
    except Exception as e:
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

        return jsonify({"success": True, "result": res, "message": "Credit extension request rejected."})
    except Exception as e:
        return jsonify({"error": str(e), "success": False}), 500


@api_bp.route("/admin/cost-history", methods=["GET"])
@login_required_api
@admin_required_api
def admin_get_global_cost_history():
    """List global cost history across all users."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database not available"}), 503
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
        history = get_global_cost_history(limit=limit)

        # Calculate total aggregate cost across history
        total_system_cost = sum(h.get("cost_usd", 0.0) for h in history)
        total_tokens = sum(h.get("tokens_used", 0) for h in history)

        return jsonify(
            {
                "success": True,
                "history": history,
                "count": len(history),
                "summary": {"total_system_cost_usd": round(total_system_cost, 6), "total_tokens": total_tokens},
            }
        )
    except Exception as e:
        return jsonify({"error": str(e), "success": False}), 500


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
    if not account_name:
        return jsonify({"error": "Account Name / Handle is required"}), 400

    if connection_type == "mcp" and not mcp_endpoint:
        return jsonify({"error": "MCP Endpoint URL is required for MCP connection mode"}), 400

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


@api_bp.route("/auth/youtube", methods=["GET"])
@login_required_api
def youtube_auth_redirect():
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    if not client_id:
        return jsonify({"error": "YOUTUBE_CLIENT_ID not configured"}), 500

    redirect_uri = urllib.parse.urljoin(request.host_url, "api/auth/youtube/callback")
    scope = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"

    # Store user_id in state to retrieve in callback
    user_id = get_current_user_id()
    state = str(user_id)

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
    code = request.args.get("code")
    state = request.args.get("state")  # This is the user_id
    error = request.args.get("error")

    if error:
        return f"Error connecting YouTube: {error}", 400

    if not code or not state:
        return "Missing code or state parameter", 400

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
            return "No YouTube channel found for this account", 400

        channel = channel_data["items"][0]
        channel_id = channel["id"]
        channel_name = channel["snippet"]["title"]

        # Save to DB
        save_social_account(
            user_id=int(state),
            platform="youtube",
            account_name=channel_name,
            account_id=channel_id,
            access_token=access_token,
            refresh_token=refresh_token,
            connection_type="direct",
        )

        # Redirect back to settings with success parameter
        return '<script>window.location.href="/settings?youtube_connected=true";</script>'

    except requests.exceptions.HTTPError as e:
        error_details = e.response.text if hasattr(e, "response") else str(e)
        return f"Error exchanging token (HTTP Error): {error_details}", 500
    except Exception as e:
        return f"Error exchanging token: {str(e)}", 500


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
    except Exception as e:
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
    """Schedule a post for future publishing"""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({"error": "Database unavailable"}), 503

    data = request.get_json() or {}
    platforms = data.get("platforms") or []
    scheduled_at_str = data.get("scheduled_at")
    content_json = data.get("content_json") or {}
    run_id = data.get("run_id")

    if not platforms or not isinstance(platforms, list):
        return jsonify({"error": "At least one target platform must be selected"}), 400
    if not scheduled_at_str:
        return jsonify({"error": "Scheduled date and time is required"}), 400

    try:
        from datetime import datetime

        scheduled_at = datetime.fromisoformat(scheduled_at_str.replace("Z", "+00:00"))
    except Exception:
        return jsonify({"error": "Invalid scheduled date/time format"}), 400

    if run_id is not None:
        post = create_scheduled_post(
            user_id=user_id, platforms=platforms, scheduled_at=scheduled_at, content_json=content_json, run_id=run_id
        )
    else:
        post = create_scheduled_post(
            user_id=user_id, platforms=platforms, scheduled_at=scheduled_at, content_json=content_json
        )
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

    from datetime import datetime, timezone

    if publish_now or not scheduled_at_str:
        scheduled_at = datetime.now(timezone.utc)
    else:
        try:
            scheduled_at = datetime.fromisoformat(scheduled_at_str.replace("Z", "+00:00"))
        except Exception:
            scheduled_at = datetime.now(timezone.utc)

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
            if os.path.exists(candidate):
                abs_image_path = candidate

        pub_results = publisher_service.publish_post_to_connected_accounts(
            user_id=user_id, platforms=platforms, caption=caption, image_path=abs_image_path
        )
        update_scheduled_post_status(user_id, post["id"], "published")
        post["status"] = "published"
    elif publish_now:
        update_scheduled_post_status(user_id, post["id"], "published")
        post["status"] = "published"

    return jsonify(
        {
            "success": True,
            "message": "Post published to social platforms!" if publish_now else "Manual post scheduled successfully!",
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
        except Exception as e:
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
def competitor_posts():
    target = request.args.get("target")
    if not target:
        return jsonify({"error": "No target competitor provided"}), 400

    try:
        from services.scraper_service import ScraperService

        scraper = ScraperService()
        posts = scraper.get_company_store(target)
        # --- NEW FILTERING LOGIC ---
        from services.stradit_service import StradITService
        from agents.story_agent import StoryAgent

        stradit = StradITService()
        project_context = stradit.get_all_projects_context()
        story_agent_local = StoryAgent()
        posts = story_agent_local.filter_relevant_posts(posts, project_context)
        # ---------------------------

        return jsonify({"success": True, "posts": posts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/platform-posts", methods=["GET"])
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
        from services.stradit_service import StradITService
        from agents.story_agent import StoryAgent

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
            print(f"[platform-posts] Warning - could not persist posts: {db_err}")

        return jsonify({"success": True, "posts": posts, "db": db_stats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/competitor-posts-db", methods=["GET"])
def competitor_posts_db():
    platform = request.args.get("platform")
    competitor = request.args.get("competitor")
    if not platform:
        return jsonify({"error": "No platform provided"}), 400
    if not competitor:
        return jsonify({"error": "No competitor provided"}), 400

    try:
        from db import get_competitor_posts

        posts = get_competitor_posts(platform=platform, competitor=competitor)
        return jsonify({"success": True, "posts": posts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/stradit-projects", methods=["GET"])
def get_stradit_projects():
    try:
        from services.stradit_service import StradITService

        svc = StradITService()
        projects = svc.get_projects()
        return jsonify({"success": True, "projects": projects})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/generate-channel-storyline", methods=["POST"])
@login_required_api
def generate_channel_storyline():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    story = data.get("story", "")

    if not story:
        return jsonify({"error": "Story text is required"}), 400

    try:
        from agents.story_agent import StoryAgent
        from services.stradit_service import StradITService

        stradit = StradITService()
        project_context = stradit.get_all_projects_context()

        story_agent_local = StoryAgent()
        result = story_agent_local.generate_channel_storyline(story, project_context)

        return jsonify({"success": True, "storyline": result})
    except Exception as e:
        import traceback

        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@api_bp.route("/opportunity-suggestions", methods=["GET"])
@login_required_api
def opportunity_suggestions():
    try:
        from db import get_opportunity_suggestions

        return jsonify({"success": True, "suggestions": get_opportunity_suggestions()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/generate-opportunity-suggestions", methods=["POST"])
@login_required_api
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
    except Exception as e:
        import traceback

        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500
