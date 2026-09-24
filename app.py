import os
import sys

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_cors import CORS

from api.routes import api_bp
from auth.routes import auth_bp
from auth.utils import (
    admin_required_page,
    enterprise_required_page,
    get_current_user_id,
    login_required_page,
    self_serve_required_page,
)
from config import config_map

# LLM responses (captions, image/video prompts, error messages) can contain
# Unicode punctuation the Windows console's default cp1252 stdout can't encode
# (e.g. non-breaking hyphens) - print() would then raise UnicodeEncodeError and
# abort whatever was mid-execution, including provider fallback loops meant to
# recover from exactly this kind of failure. Force UTF-8 stdout/stderr so
# logging output never crashes the process it's trying to report on.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


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

    @app.route("/dashboard")
    @login_required_page
    def index():
        return render_template("index.html")

    @app.route("/settings")
    @login_required_page
    def settings_route():
        return render_template("settings.html")

    @app.route("/brand-profile")
    @login_required_page
    @self_serve_required_page
    def brand_profile_page():
        """"My Brand Configuration" - shows/edits the per-user brand
        profile scraped from an Individual/Small/Medium account's website
        during onboarding (see db.UserBrandProfile,
        services/brand_profile_service.py). Not to be confused with
        /brand-configuration below, which edits StradIT's own global
        Content Guidelines."""
        return render_template("brand_profile.html")

    @app.route("/brand-configuration")
    @login_required_page
    @enterprise_required_page
    def brand_configuration_page():
        """Editable Content Guidelines + Products & Service text (see
        AppSetting in db.py) read live by generation, so edits here actually
        change what gets generated without touching code. This is StradIT's
        own single global brand profile (AppSetting is a global key-value
        store, not per-user) - Enterprise/admin only. Individual/Small/Medium
        accounts have their own per-user equivalent at /brand-profile
        instead (see db.UserBrandProfile)."""
        return render_template("brand_configuration.html")

    @app.route("/competitor-dashboard")
    @login_required_page
    @enterprise_required_page
    def competitor_dashboard():
        # Backward compatibility: approval-request emails sent before the
        # dedicated /approve/<id> page existed link here as ?approve=<id>.
        approve_id = request.args.get("approve")
        if approve_id and approve_id.isdigit():
            return redirect(url_for("approval_review_page", request_id=int(approve_id)))
        return render_template("competitor_dashboard.html")

    @app.route("/admin")
    @login_required_page
    @admin_required_page
    def admin_page():
        """Admin Control Center - user credit management, extension
        requests, global cost history. Was previously a modal on the Studio
        Chat page (unreachable via any button that actually existed in the
        header, and the one working entry point - the user-menu dropdown -
        never triggered its data-loading calls either); now a standalone,
        admin-gated page."""
        return render_template("admin.html")

    @app.route("/approve")
    @login_required_page
    def approval_list_page():
        """Dashboard of every approval request (past and current) with its
        status - pending/accepted/rejected - for reviewers who want an
        overview instead of following a one-off email link."""
        return render_template("approval_list.html")

    @app.route("/approve/<int:request_id>")
    @login_required_page
    def approval_review_page(request_id):
        """Standalone page opened from an approval-request email's "Review &
        Decide" link - a focused preview + accept/reject/comments view,
        rather than dropping the reviewer into the full dashboard."""
        return render_template("approval_review.html", request_id=request_id)

    @app.route("/login")
    def login_route():
        if get_current_user_id():
            return redirect(url_for("index"))
        return render_template("login.html")

    @app.route("/signup")
    def signup_route():
        """Dedicated registration page (posts to /api/auth/register, same
        endpoint login.html's old register-mode toggle used) - a real page
        instead of a same-page mode switch, with its own client-side
        validation (name/email format/password strength/confirm-password
        match) layered on top of the server-side checks in
        auth/routes.py's register(), which remain the actual source of
        truth."""
        if get_current_user_id():
            return redirect(url_for("index"))
        return render_template("signup.html")

    @app.route("/forgot-password")
    def forgot_password_page():
        """Asks for an email and posts to /api/auth/forgot-password, which
        emails a single-use reset link (see auth/routes.py)."""
        return render_template("forgot_password.html")

    @app.route("/reset-password/<token>")
    def reset_password_page(token):
        """Opened from the reset email. The token is checked up front so an
        expired/used link shows that straight away instead of after the user
        has typed a new password; /api/auth/reset-password re-checks it."""
        from auth.routes import get_user_for_reset_token

        return render_template(
            "reset_password.html", token=token, token_valid=get_user_for_reset_token(token) is not None
        )

    @app.route("/verify-pending")
    def verify_pending_page():
        """Shown right after registration - reachable with no session, since
        registration no longer logs the user in (see auth/routes.py's
        register()). Also rendered directly (with an error/email context) by
        auth/routes.py's verify_email() on an invalid/expired token."""
        return render_template("verify_pending.html", email=request.args.get("email"))

    @app.route("/onboarding")
    @login_required_page
    def onboarding_page():
        """Account-type selection (Individual/Small/Medium/Enterprise) - the
        first step after email verification. See
        POST /api/onboarding/account-type."""
        return render_template("onboarding_account_type.html")

    @app.route("/onboarding/contact-sales")
    @login_required_page
    def onboarding_contact_sales_page():
        """Enterprise's path instead of self-serve dashboard access. See
        POST /api/onboarding/contact-sales."""
        return render_template("onboarding_contact_sales.html")

    @app.route("/account-pending")
    @login_required_page
    def account_pending_page():
        """Shown for any onboarded-but-inactive account - Enterprise users
        awaiting sales activation, or any account an admin has deactivated
        (see is_active / set_user_active in db.py)."""
        return render_template("account_pending.html")

    @app.route("/upgrade-required")
    @login_required_page
    def upgrade_required_page():
        """Shown when a non-Enterprise account tries to reach the Analysis
        Dashboard (see @enterprise_required_page in auth/utils.py)."""
        return render_template("upgrade_required.html")

    @app.route("/")
    def landing_page():
        if get_current_user_id():
            return redirect(url_for("index"))
        return render_template("landing.html")

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
    # Local dev entrypoint only -- production runs via gunicorn (see Dockerfile), which never
    # executes this block. B201 (debug=True) and B104 (bind-all) are both dev-only here.
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)  # nosec B201 B104
