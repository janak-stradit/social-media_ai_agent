from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
import os
import uuid
from agents.story_agent import StoryAgent
from agents.vision_agent import VisionAgent
from agents.caption_agent import CaptionAgent
from agents.hashtag_agent import HashtagAgent
from agents.strategy_agent import StrategyAgent

try:
    from db import save_run, get_history, get_run_by_id
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

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

# Initialize agents
story_agent = StoryAgent()
vision_agent = VisionAgent()
caption_agent = CaptionAgent()
hashtag_agent = HashtagAgent()
strategy_agent = StrategyAgent()

@api_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Social Media AI Agent',
        'version': '1.0.0'
    })

@api_bp.route('/upload', methods=['POST'])
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
def analyze_story():
    """Analyze story text"""
    data = request.get_json()
    if not data or 'story' not in data:
        return jsonify({'error': 'Story text is required'}), 400
    
    try:
        analysis = story_agent.analyze(data['story'])
        key_points = story_agent.extract_key_points(data['story'])
        
        return jsonify({
            'success': True,
            'analysis': analysis,
            'key_points': key_points
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api_bp.route('/generate', methods=['POST'])
def generate_content():
    """
    Main endpoint to generate complete social media content
    Request body:
    {
        "story": "Your story text here",
        "image_path": "optional/path/to/image",
        "platforms": ["facebook", "instagram", "linkedin"],
        "tone": "optional tone override",
        "include_strategy": true
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body required'}), 400
    
    story = data.get('story', '')
    image_path = data.get('image_path')
    platforms = data.get('platforms', ['facebook', 'instagram', 'linkedin'])
    tone = data.get('tone')
    include_strategy = data.get('include_strategy', True)
    
    try:
        # Step 1: Analyze story
        story_analysis = story_agent.analyze(story)
        
        # Step 2: Analyze image if provided
        vision_analysis = None
        if image_path and os.path.exists(image_path):
            vision_analysis = vision_agent.analyze_image(image_path)
        
        # Step 3: Generate captions
        captions = caption_agent.generate_all_platforms(
            story_analysis, vision_analysis, tone
        )
        
        # Step 4: Generate hashtags
        hashtags = hashtag_agent.generate_all_platforms(
            story_analysis, vision_analysis
        )
        
        # Step 5: Generate strategy
        strategies = {}
        if include_strategy:
            strategies = strategy_agent.generate_all_strategies(story_analysis)
        
        # Compile response
        response = {
            'success': True,
            'request_id': str(uuid.uuid4()),
            'story_analysis': story_analysis,
            'content': {}
        }
        
        for platform in platforms:
            response['content'][platform] = {
                'caption': captions.get(platform, {}),
                'hashtags': hashtags.get(platform, {}),
                'strategy': strategies.get(platform, {}) if include_strategy else None
            }
        
        # ── Persist to PostgreSQL ──────────────────────────────────────────
        if DB_AVAILABLE:
            try:
                run_id = save_run(
                    story=story,
                    tone=tone,
                    platforms=platforms,
                    content=response['content']
                )
                response['run_id'] = run_id
            except Exception as db_err:
                current_app.logger.warning(f"[DB] Could not save run: {db_err}")

        return jsonify(response)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── History Endpoints ──────────────────────────────────────────────────────

@api_bp.route('/history', methods=['GET'])
def list_history():
    """Return the last N generation runs from PostgreSQL"""
    if not DB_AVAILABLE:
        return jsonify({'error': 'Database not available'}), 503
    try:
        limit = min(int(request.args.get('limit', 20)), 100)
        rows = get_history(limit=limit)
        return jsonify({'success': True, 'history': rows, 'count': len(rows)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/history/<int:run_id>', methods=['GET'])
def get_history_run(run_id):
    """Return full details for a single run"""
    if not DB_AVAILABLE:
        return jsonify({'error': 'Database not available'}), 503
    try:
        row = get_run_by_id(run_id)
        if not row:
            return jsonify({'error': 'Run not found'}), 404
        return jsonify({'success': True, 'run': row})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/generate/<platform>', methods=['POST'])
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
def generate_media():
    """
    Generate image or video storyboard for a given platform and caption.
    Request body:
    {
        "platform": "instagram",
        "caption": "Your caption text here",
        "media_type": "image" | "video",
        "tone": "professional"
    }
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

    if not caption:
        return jsonify({'error': 'caption is required'}), 400
    if platform not in ['facebook', 'instagram', 'linkedin']:
        return jsonify({'error': 'Invalid platform'}), 400

    try:
        if media_type == 'video':
            result = media_service.generate_video_storyboard(caption, platform, tone)
        else:
            result = media_service.generate_image(caption, platform, tone)

        return jsonify(result)

    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500