import os

from flask import Flask, jsonify, redirect, render_template, session, url_for
from flask_cors import CORS

from api.routes import api_bp
from auth.routes import auth_bp
from auth.utils import get_current_user_id, login_required_page
from config import config_map


def create_app(config_name="development"):
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Disable static file caching entirely to avoid client-side update issues
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config.from_object(config_map[config_name])
    CORS(app, supports_credentials=True)

    # Ensure upload directory exists
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # Initialise PostgreSQL schema + tables
    try:
        from db import init_db

        init_db()
        print("[DB] Schema 'social_media_agent' initialised.")

        # Start background scheduler thread (skip the reloader's monitor process,
        # otherwise app.py runs twice under debug=True and posts get published twice)
        if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            from scheduler_thread import start_background_scheduler

            start_background_scheduler(app.root_path)
    except Exception as e:
        print(f"[DB] Warning – could not initialise DB: {e}")

    # Register blueprints
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(auth_bp, url_prefix="/api/auth")

    @app.route("/")
    @login_required_page
    def index():
        return render_template("index.html")

    @app.route("/settings")
    @login_required_page
    def settings_route():
        return render_template("settings.html")

    @app.route("/competitor-dashboard")
    @login_required_page
    def competitor_dashboard():
        return render_template("competitor_dashboard.html")

    @app.route("/login")
    def login_route():
        if get_current_user_id():
            return redirect(url_for("index"))
        return render_template("login.html")

    @app.route("/logout")
    def logout_route():
        session.clear()
        session.modified = True
        return redirect(url_for("login_route"))

    @app.route("/favicon.ico")
    def favicon():
        return "", 204

    @app.errorhandler(413)
    def too_large(e):
        return jsonify({"error": "File too large. Max 50MB."}), 413

    @app.errorhandler(500)
    def server_error(e):
        return jsonify({"error": "Internal server error"}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)
