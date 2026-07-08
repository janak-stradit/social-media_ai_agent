import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from config import config_map
from api.routes import api_bp

def create_app(config_name='development'):
    app = Flask(__name__, 
                template_folder='templates',
                static_folder='static')
    app.config.from_object(config_map[config_name])
    CORS(app)
    
    # Ensure upload directory exists
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Initialise PostgreSQL schema + tables
    try:
        from db import init_db
        init_db()
        print("[DB] Schema 'social_media_agent' initialised.")
    except Exception as e:
        print(f"[DB] Warning – could not initialise DB: {e}")
    
    # Register blueprints
    app.register_blueprint(api_bp, url_prefix='/api')
    
    @app.route('/')
    def index():
        from flask import render_template
        return render_template('index.html')
    
    @app.errorhandler(413)
    def too_large(e):
        return jsonify({'error': 'File too large. Max 16MB.'}), 413
    
    @app.errorhandler(500)
    def server_error(e):
        return jsonify({'error': 'Internal server error'}), 500
    
    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)