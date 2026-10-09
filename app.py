import logging
import os
import re
import time
import uuid

from logging_setup import configure_logging, redact_query

# Before the app's own imports, so what they log while loading (provider
# clients, models) already uses the same format. Also forces UTF-8 stdout:
# LLM text can contain characters the Windows console can't encode.
configure_logging()

from flask import (
    Flask,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_cors import CORS

from api.routes import api_bp
from auth.captcha import captcha_bp
from auth.routes import auth_bp
from auth.utils import (
    admin_required_page,
    enterprise_required_page,
    get_current_user_id,
    login_required_page,
    self_serve_required_page,
)
from config import config_map

logger = logging.getLogger(__name__)
http_logger = logging.getLogger("avir.http")

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{6,64}$")

# Browser caching of static files. Every url_for('static', ...) link carries a
# fingerprint of the file's content (?v=...): a changed file gets a new link,
# so browsers may keep each version for a year and still always see the
# latest after a deploy. Uploads/generated images get unique file names and
# never change, so they're cached for 30 days - except brand_logos/, which are
# refreshed in place (services/brand_logo_service.py).
STATIC_CACHE_VERSIONED = "public, max-age=31536000, immutable"
STATIC_CACHE_UPLOADS = "public, max-age=2592000"
_static_fingerprints: dict[str, tuple[float, str]] = {}


def _static_fingerprint(static_folder: str, filename: str) -> str | None:
    """First 10 hex characters of the file's MD5, recomputed only when the
    file changes (its modification time). None for a missing file."""
    import hashlib

    path = os.path.join(static_folder, filename)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _static_fingerprints.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, "rb") as f:
        digest = hashlib.md5(f.read(), usedforsecurity=False).hexdigest()[:10]
    _static_fingerprints[path] = (mtime, digest)
    return digest
# Pages search engines may index (see sitemap.xml); every other page gets
# X-Robots-Tag: noindex - they're behind login or one-off (reset links etc.)
_INDEXABLE_PATHS = ("/", "/signup", "/login")


def _site_url() -> str:
    """Public base URL for canonical links and the sitemap: APP_BASE_URL, else
    this request's host with the scheme nginx saw (https in production)."""
    from config import Config

    if Config.APP_BASE_URL:
        return Config.APP_BASE_URL.rstrip("/")
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    return f"{scheme}://{request.host}"

# Logged at DEBUG (hidden at the default INFO): assets, health checks and the
# once-a-second generation progress polling - they drown out real traffic
_QUIET_PREFIXES = ("/static/", "/favicon.ico", "/api/generate/progress/", "/api/health")
_PROBE_AGENTS = ("curl/", "wget/", "elb-healthchecker", "kube-probe", "uptimerobot", "healthcheck")


def _client_ip() -> str | None:
    """The visitor's address (nginx passes it on), not the Docker gateway's."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    return request.headers.get("X-Real-IP") or (forwarded.split(",")[0].strip() if forwarded else request.remote_addr)


def _install_request_logging(app: Flask) -> None:
    """One access-log line per request, replacing gunicorn's: method, path
    (secrets in the query redacted), status, duration, request id and user.
    The request id (nginx's X-Request-ID, or a new one) is on every line logged
    during the request and returned in the X-Request-ID response header."""

    @app.url_defaults
    def _version_static_links(endpoint, values):
        if endpoint == "static" and values.get("filename") and "v" not in values:
            version = _static_fingerprint(app.static_folder, values["filename"])
            if version:
                values["v"] = version

    @app.before_request
    def _start_request():
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex[:12]
        g.request_started = time.perf_counter()

    @app.after_request
    def _log_request(response):
        request_id = getattr(g, "request_id", None)
        if request_id:
            response.headers["X-Request-ID"] = request_id
        if response.mimetype == "text/html" and request.path not in _INDEXABLE_PATHS:
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        if request.path.startswith("/static/") and response.status_code in (200, 304):
            if request.args.get("v"):
                response.headers["Cache-Control"] = STATIC_CACHE_VERSIONED
            elif request.path.startswith("/static/uploads/") and "/brand_logos/" not in request.path:
                response.headers["Cache-Control"] = STATIC_CACHE_UPLOADS
        started = getattr(g, "request_started", None)
        duration_ms = int((time.perf_counter() - started) * 1000) if started else -1
        path, status = request.path, response.status_code
        agent = (request.user_agent.string or "").lower()
        quiet = path.startswith(_QUIET_PREFIXES) or (
            request.method in ("GET", "HEAD") and path in ("/", "/login") and agent.startswith(_PROBE_AGENTS)
        )
        if status >= 500:
            level = logging.ERROR
        elif quiet:
            level = logging.DEBUG
        elif status >= 400 and status not in (401, 404):
            level = logging.WARNING
        else:
            level = logging.INFO
        if http_logger.isEnabledFor(level):
            query = redact_query(request.query_string.decode("utf-8", "replace"))
            fields = {"ip": _client_ip(), "bytes": response.calculate_content_length()}
            if status >= 400:
                fields["ua"] = (request.user_agent.string or "")[:120] or None
            http_logger.log(
                level,
                f"{request.method} {path}{'?' + query if query else ''} {status} {duration_ms}ms",
                extra={"fields": fields},
            )
        return response


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
        logger.info("Database schema initialised.")

        # Start background scheduler thread (skip the reloader's monitor process,
        # otherwise app.py runs twice under debug=True and posts get published twice).
        # Under gunicorn every worker would start its own thread and publish each post
        # once per worker, so production sets SCHEDULER_ENABLED=false and runs the
        # scheduler as its own single process instead (scripts/run_scheduler.py).
        scheduler_enabled = os.environ.get("SCHEDULER_ENABLED", "true").lower() in ("1", "true", "yes")
        if scheduler_enabled and (not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"):
            from scheduler_thread import start_background_scheduler

            start_background_scheduler(app.root_path)
    except Exception as e:
        logger.error(f"Could not initialise the database: {e}")

    # Register blueprints
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(captcha_bp, url_prefix="/api/auth/captcha")
    _install_request_logging(app)

    @app.route("/dashboard")
    @login_required_page
    def index():
        # An emailed idea clicked while logged out (auth/utils.login_required_page)
        pending_idea = session.pop("pending_idea", None)
        if pending_idea and not request.args.get("idea"):
            return redirect(url_for("index", idea=pending_idea))
        return render_template("index.html")

    @app.route("/settings")
    @login_required_page
    def settings_route():
        return render_template("settings.html")

    @app.route("/calendar")
    @login_required_page
    def calendar_page():
        """Content Calendar: the weekly posting goal and what was created,
        scheduled and suggested on each day (services/calendar_service.py)."""
        return render_template("calendar.html")

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
        # /signup?invite=<token> - from an admin's invitation email
        invite_token = (request.args.get("invite") or "").strip()
        invite = None
        if invite_token:
            from db import get_open_invitation_by_token

            invite = get_open_invitation_by_token(invite_token)
        return render_template(
            "signup.html",
            captcha_enabled=app.config.get("CAPTCHA_ENABLED", True),
            invite=invite,
            invite_token=invite_token if invite else None,
            invite_invalid=bool(invite_token and not invite),
        )

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
        return render_template("landing.html", site_url=_site_url())

    @app.route("/logout")
    def logout_route():
        session.clear()
        session.modified = True
        return redirect(url_for("login_route"))

    @app.route("/static/uploads/<path:filename>")
    def uploaded_file(filename):
        """Generated content and uploads. Same as Flask's own /static route,
        except that a file missing on this server's disk (new or rebuilt
        instance) is fetched from its S3 copy first (services/storage_service.py).
        In production nginx serves files that exist and only sends misses here."""
        from services import storage_service

        if not storage_service.ensure_local(f"{storage_service.UPLOAD_URL_PREFIX}{filename}"):
            abort(404)
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename, max_age=0)

    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(app.static_folder, "favicon.ico", mimetype="image/vnd.microsoft.icon", max_age=86400)

    @app.route("/robots.txt")
    def robots_txt():
        site = _site_url()
        body = "\n".join([
            "User-agent: *",
            "Allow: /",
            # API responses and user-generated media are not content to index
            "Disallow: /api/",
            "Disallow: /static/uploads/",
            "",
            f"Sitemap: {site}/sitemap.xml",
            "",
        ])
        return app.response_class(body, mimetype="text/plain", headers={"Cache-Control": "public, max-age=3600"})

    @app.route("/sitemap.xml")
    def sitemap_xml():
        site = _site_url()
        # lastmod = when the landing page itself last changed (its file in this build)
        stamp = os.path.getmtime(os.path.join(app.root_path, "templates", "landing.html"))
        lastmod = time.strftime("%Y-%m-%d", time.gmtime(stamp))
        pages = [("/", "weekly", "1.0"), ("/signup", "monthly", "0.8"), ("/login", "monthly", "0.5")]
        urls = "".join(
            f"<url><loc>{site}{path}</loc><lastmod>{lastmod}</lastmod>"
            f"<changefreq>{freq}</changefreq><priority>{prio}</priority></url>"
            for path, freq, prio in pages
        )
        body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n')
        return app.response_class(body, mimetype="application/xml", headers={"Cache-Control": "public, max-age=3600"})

    @app.errorhandler(413)
    def too_large(e):
        return jsonify({"error": "File too large. Max 50MB."}), 413

    @app.errorhandler(500)
    def server_error(e):
        # Flask has already logged the exception with its traceback; the
        # request id lets a user's report be matched to that log entry
        return jsonify({"error": "Internal server error", "request_id": getattr(g, "request_id", None)}), 500

    return app


def _quiet_windows_reload_error() -> None:
    """On Windows, each auto-reload (a .py file changed) ends the old server
    process while its listener thread is still waiting on the closed socket,
    which prints a harmless "OSError: [WinError 10038]" traceback. Hide only
    that one error; anything else in a thread is still reported."""
    import threading

    default_hook = threading.excepthook

    def hook(args):
        if isinstance(args.exc_value, OSError) and getattr(args.exc_value, "winerror", None) == 10038:
            return
        default_hook(args)

    threading.excepthook = hook


if __name__ == "__main__":
    # Local dev entrypoint only -- production runs via gunicorn (see Dockerfile), which never
    # executes this block. B201 (debug=True) and B104 (bind-all) are both dev-only here.
    if os.name == "nt":
        _quiet_windows_reload_error()
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)  # nosec B201 B104
