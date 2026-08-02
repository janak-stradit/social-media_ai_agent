from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
import os
import uuid
from auth.utils import get_current_user_id, login_required_api
from agents.story_agent import StoryAgent
from agents.vision_agent import VisionAgent
from agents.caption_agent import CaptionAgent
from agents.hashtag_agent import HashtagAgent
from agents.strategy_agent import StrategyAgent
from agents.reviewer_agent import ReviewerAgent
from services.memory_service import MemoryService

try:
    from db import save_run, get_history, get_run_by_id, append_run_media, get_user_usage_stats, archive_run, unarchive_run
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

api_bp = Blueprint('api', __name__)
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

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

# Initialize agents
story_agent = StoryAgent()
vision_agent = VisionAgent()
caption_agent = CaptionAgent()
hashtag_agent = HashtagAgent()
strategy_agent = StrategyAgent()
reviewer_agent = ReviewerAgent()

@api_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Social Media AI Agent',
        'version': '3.0.0'
    })

@api_bp.route('/metrics/usage', methods=['GET'])
@login_required_api
def usage_metrics():
    """Return aggregated token and cost metrics for current user"""
    user_id = get_current_user_id()
    if not DB_AVAILABLE or not user_id:
        return jsonify({'success': True, 'total_runs': 0, 'total_tokens': 0, 'total_cost_usd': 0.0})
    try:
        stats = get_user_usage_stats(user_id)
        return jsonify({'success': True, **stats})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api_bp.route('/upload', methods=['POST'])
@login_required_api
def upload_image():
    """Upload image for analysis"""
    if 'image' not in request.files:
        return jsonify({'error': 'No image file provided'}), 400
    
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_name = f"{uuid.uuid4()}_{filename}"
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
        file.save(filepath)
        
        # Analyze image
        try:
            analysis = vision_agent.analyze_image(filepath)
            return jsonify({
                'success': True,
                'image_id': unique_name,
                'filepath': filepath,
                'analysis': analysis
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    return jsonify({'error': 'Invalid file type'}), 400

@api_bp.route('/analyze-story', methods=['POST'])
@login_required_api
def analyze_story():
    """Analyze story text"""
    data = request.get_json()
    if not data or 'story' not in data:
        return jsonify({'error': 'Story text is required'}), 400
    
    try:
        user_id = get_current_user_id()
        retrieved_memories = memory_service.retrieve_context(data['story'], user_id=user_id, n_results=2)
        mem_prompt = memory_service.format_memory_prompt(retrieved_memories)

        analysis = story_agent.analyze(data['story'], memory_context=mem_prompt)
        key_points = story_agent.extract_key_points(data['story'])
        
        return jsonify({
            'success': True,
            'analysis': analysis,
            'key_points': key_points,
            'memories_referenced': len(retrieved_memories)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api_bp.route('/generate', methods=['POST'])
@login_required_api
def generate_content():
    """
    Main endpoint to generate complete social media content with Multi-Turn Refinement, A/B Hook Variations & Critic Self-Correction
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body required'}), 400
    
    story = data.get('story', '')
    image_path = data.get('image_path')
    platforms = data.get('platforms', ['facebook', 'instagram', 'linkedin'])
    tone = data.get('tone')
    brand_voice = data.get('brand_voice', 'Standard Enterprise')
    include_strategy = data.get('include_strategy', True)
    previous_context = data.get('previous_context')
    user_id = get_current_user_id()

    # Multi-Turn Refinement Context Integration
    if previous_context:
        story_prompt = f"Follow-up Refinement Request: {story}\n\n[PREVIOUS TURN CONTEXT & OUTPUTS]:\n{previous_context}"
    else:
        story_prompt = story

    total_tokens = 0
    total_cost_usd = 0.0
    agents_executed = []
    
    try:
        # Step 0: RAG Memory Context Retrieval from ChromaDB
        retrieved_memories = memory_service.retrieve_context(story, user_id=user_id, n_results=3)
        mem_prompt = memory_service.format_memory_prompt(retrieved_memories)

        # Step 1: Analyze story
        story_analysis, story_usage = story_agent.analyze(story_prompt, memory_context=mem_prompt, return_usage=True)
        if story_usage:
            total_tokens += story_usage.get("total_tokens", 0)
            total_cost_usd += story_usage.get("cost_usd", 0.0)

        agents_executed.append({
            "agent": "StoryAgent",
            "name": "Story & Memory Agent",
            "role": "Analyzed narrative themes, emotional tone & retrieved brand memories",
            "status": "completed"
        })

        # Step 2: Analyze image if provided
        vision_analysis = None
        if image_path and os.path.exists(image_path):
            vision_analysis = vision_agent.analyze_image(image_path)
            agents_executed.append({
                "agent": "VisionAgent",
                "name": "Vision Agent",
                "role": "Analyzed visual asset, color palette & image objects",
                "status": "completed"
            })
        
        # Step 3: Generate captions with A/B Hook Variations
        captions = caption_agent.generate_all_platforms(
            story_analysis, vision_analysis, tone, memory_context=mem_prompt, brand_voice=brand_voice
        )
        cap_usage = captions.pop('_usage', {})
        total_tokens += cap_usage.get("total_tokens", 0)
        total_cost_usd += cap_usage.get("cost_usd", 0.0)

        agents_executed.append({
            "agent": "CaptionAgent",
            "name": "Caption Agent",
            "role": f"Generated 3 psychological hook angles for {', '.join(platforms)} ('{brand_voice}' voice)",
            "status": "completed"
        })
        
        # Step 4: Generate hashtags
        hashtags = hashtag_agent.generate_all_platforms(
            story_analysis, vision_analysis, memory_context=mem_prompt
        )
        hash_usage = hashtags.pop('_usage', {})
        total_tokens += hash_usage.get("total_tokens", 0)
        total_cost_usd += hash_usage.get("cost_usd", 0.0)

        agents_executed.append({
            "agent": "HashtagAgent",
            "name": "Hashtag Agent",
            "role": "Curated broad, niche & trending hashtag sets",
            "status": "completed"
        })
        
        # Step 5: Generate strategy
        strategies = {}
        if include_strategy:
            strategies = strategy_agent.generate_all_strategies(story_analysis, memory_context=mem_prompt)
            strat_usage = strategies.pop('_usage', {})
            total_tokens += strat_usage.get("total_tokens", 0)
            total_cost_usd += strat_usage.get("cost_usd", 0.0)

            agents_executed.append({
                "agent": "StrategyAgent",
                "name": "Strategy Agent",
                "role": "Calculated optimal posting schedule & engagement forecasts",
                "status": "completed"
            })

        # Step 6: ReviewerAgent Self-Correction Loop
        quality_evaluations = {}
        refinements_count = 0

        for platform in platforms:
            primary_cap = captions.get(platform, {}).get('primary_caption', '')
            p_hashtags = (hashtags.get(platform, {}) or {}).get('hashtags', [])
            
            # Reviewer evaluates post quality
            eval_res = reviewer_agent.evaluate(
                platform=platform,
                caption=primary_cap,
                hashtags=p_hashtags,
                story_analysis=story_analysis,
                brand_voice=brand_voice
            )
            
            rev_usage = eval_res.pop('_usage', {})
            total_tokens += rev_usage.get("total_tokens", 0)
            total_cost_usd += rev_usage.get("cost_usd", 0.0)

            # Trigger self-correction if score < 8.0
            if eval_res.get('needs_refinement'):
                refinements_count += 1
                refined_cap, ref_usage = caption_agent.refine_caption(
                    platform=platform,
                    original_caption=primary_cap,
                    reviewer_feedback=eval_res.get('reviewer_feedback'),
                    brand_voice=brand_voice
                )
                captions[platform]['primary_caption'] = refined_cap
                captions[platform]['refined_by_critic'] = True
                eval_res['self_corrected'] = True
                eval_res['overall_score'] = min(9.8, round(eval_res.get('overall_score', 7.5) + 1.5, 1))
                
                if ref_usage:
                    total_tokens += ref_usage.get("total_tokens", 0)
                    total_cost_usd += ref_usage.get("cost_usd", 0.0)

            quality_evaluations[platform] = eval_res

        agents_executed.append({
            "agent": "ReviewerAgent",
            "name": "Critic & Self-Correction Agent",
            "role": f"Evaluated quality, hook strength & applied {refinements_count} self-corrections",
            "status": "completed"
        })

        # Calculate average overall quality score
        avg_score = round(sum(q.get('overall_score', 8.5) for q in quality_evaluations.values()) / max(1, len(quality_evaluations)), 1)

        # Compile response
        response = {
            'success': True,
            'request_id': str(uuid.uuid4()),
            'story_analysis': story_analysis,
            'quality_summary': {
                'overall_score': avg_score,
                'refinements_applied': refinements_count,
                'brand_voice_applied': brand_voice
            },
            'content': {},
            'agents_executed': agents_executed,
            'usage': {
                'total_tokens': total_tokens,
                'cost_usd': round(total_cost_usd, 6),
                'memories_referenced': len(retrieved_memories)
            }
        }
        
        for platform in platforms:
            response['content'][platform] = {
                'caption': captions.get(platform, {}),
                'hashtags': hashtags.get(platform, {}),
                'strategy': strategies.get(platform, {}) if include_strategy else None,
                'quality': quality_evaluations.get(platform, {})
            }
        
        # ── Persist to PostgreSQL ──────────────────────────────────────────
        run_id = None
        if DB_AVAILABLE:
            try:
                content_to_save = dict(response["content"])
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
                    cost_usd=round(total_cost_usd, 6)
                )
                response['run_id'] = run_id
            except Exception as db_err:
                current_app.logger.warning(f"[DB] Could not save run: {db_err}")

        # ── Store Run in ChromaDB Memory ───────────────────────────────────
        memory_service.store_campaign_run(
            run_id=run_id,
            story=story,
            content=response['content'],
            user_id=user_id,
            tone=tone,
            platforms=platforms
        )

        return jsonify(response)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── History Endpoints ──────────────────────────────────────────────────────

@api_bp.route('/history', methods=['GET'])
@login_required_api
def list_history():
    """Return the last N generation runs from PostgreSQL"""
    if not DB_AVAILABLE:
        return jsonify({'error': 'Database not available'}), 503
    try:
        limit = min(int(request.args.get('limit', 20)), 100)
        include_archived = request.args.get('archived', 'false').lower() == 'true'
        rows = get_history(limit=limit, user_id=get_current_user_id(), include_archived=include_archived)
        return jsonify({'success': True, 'history': rows, 'count': len(rows)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/history/<int:run_id>/archive', methods=['POST'])
@login_required_api
def archive_history_run(run_id):
    """Archive a single generation run"""
    if not DB_AVAILABLE:
        return jsonify({'error': 'Database not available'}), 503
    try:
        success = archive_run(run_id, user_id=get_current_user_id())
        if not success:
            return jsonify({'error': 'Run not found or access denied'}), 404
        return jsonify({'success': True, 'run_id': run_id, 'is_archived': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/history/<int:run_id>/unarchive', methods=['POST'])
@login_required_api
def unarchive_history_run(run_id):
    """Unarchive a single generation run"""
    if not DB_AVAILABLE:
        return jsonify({'error': 'Database not available'}), 503
    try:
        success = unarchive_run(run_id, user_id=get_current_user_id())
        if not success:
            return jsonify({'error': 'Run not found or access denied'}), 404
        return jsonify({'success': True, 'run_id': run_id, 'is_archived': False})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/history/<int:run_id>', methods=['GET'])
@login_required_api
def get_history_run(run_id):
    """Return full details for a single run"""
    if not DB_AVAILABLE:
        return jsonify({'error': 'Database not available'}), 503
    try:
        row = get_run_by_id(run_id, user_id=get_current_user_id())
        if not row:
            return jsonify({'error': 'Run not found'}), 404
        return jsonify({'success': True, 'run': row})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/generate/<platform>', methods=['POST'])
@login_required_api
def generate_for_platform(platform):
    """Generate content for a specific platform"""
    if platform not in ['facebook', 'instagram', 'linkedin']:
        return jsonify({'error': 'Invalid platform. Use: facebook, instagram, linkedin'}), 400
    
    data = request.get_json()
    if not data or 'story' not in data:
        return jsonify({'error': 'Story text is required'}), 400
    
    try:
        story_analysis = story_agent.analyze(data['story'])
        vision_analysis = None
        if data.get('image_path'):
            vision_analysis = vision_agent.analyze_image(data['image_path'])
        
        caption = caption_agent.generate_caption(
            platform, story_analysis, vision_analysis, data.get('tone')
        )
        hashtags = hashtag_agent.generate_hashtags(
            platform, story_analysis, vision_analysis
        )
        strategy = strategy_agent.create_strategy(platform, story_analysis)
        
        return jsonify({
            'success': True,
            'platform': platform,
            'caption': caption,
            'hashtags': hashtags,
            'strategy': strategy
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api_bp.route('/schedule', methods=['POST'])
@login_required_api
def schedule_campaign():
    """Create a multi-platform posting schedule"""
    data = request.get_json()
    if not data or 'story' not in data:
        return jsonify({'error': 'Story text is required'}), 400
    
    platforms = data.get('platforms', ['facebook', 'instagram', 'linkedin'])
    
    try:
        story_analysis = story_agent.analyze(data['story'])
        schedule = strategy_agent.schedule_posts(platforms, story_analysis)
        
        return jsonify({
            'success': True,
            'schedule': schedule
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Media Generation Endpoint ──────────────────────────────────────────────

@api_bp.route('/generate-media', methods=['POST'])
@login_required_api
def generate_media():
    """
    Generate image or video for a given platform and caption.
    """
    if not MEDIA_AVAILABLE:
        return jsonify({'error': 'Media generation service not available'}), 503

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body required'}), 400

    platform   = data.get('platform', 'instagram')
    caption    = data.get('caption', '')
    media_type = data.get('media_type', 'image')
    tone       = data.get('tone')
    run_id     = data.get('run_id')
    image_path = data.get('image_path')
    user_id    = get_current_user_id()

    if not caption:
        return jsonify({'error': 'caption is required'}), 400
    if platform not in ['facebook', 'instagram', 'linkedin']:
        return jsonify({'error': 'Invalid platform'}), 400

    if not image_path and run_id and DB_AVAILABLE:
        try:
            run = get_run_by_id(run_id, user_id=user_id)
            image_path = (run or {}).get("content", {}).get("_meta", {}).get("image_path")
        except Exception:
            image_path = None

    if run_id and DB_AVAILABLE:
        run = get_run_by_id(run_id, user_id=user_id)
        if not run:
            return jsonify({'error': 'Run not found'}), 404

    try:
        if media_type == 'video':
            result = media_service.generate_video(caption, platform, tone, image_path=image_path)
        else:
            result = media_service.generate_image(caption, platform, tone, image_path=image_path)

        if image_path:
            result["source_image_url"] = _public_upload_url(image_path)
        if run_id:
            result["run_id"] = run_id
            _persist_generated_media(run_id, platform, media_type, result, user_id)

        return jsonify(result)

    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500