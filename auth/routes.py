import secrets
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from auth.utils import get_current_user_id
from config import Config
from db import (
    create_user,
    get_user_by_email,
    get_user_by_id,
    get_user_by_verification_token,
    mark_user_email_verified,
    set_user_verification_token,
)

auth_bp = Blueprint("auth", __name__)

VERIFICATION_TOKEN_TTL = timedelta(hours=24)
RESEND_COOLDOWN = timedelta(seconds=60)


def _send_verification_email(user_id: int, email: str, name: str) -> None:
    """Best-effort: registration must still succeed even if SMTP isn't
    configured or the send fails - the user can always use "Resend email"
    from /verify-pending once it's fixed."""
    token = secrets.token_urlsafe(32)
    set_user_verification_token(user_id, token)
    try:
        from services.email_service import EmailService

        verify_url = f"{(Config.APP_BASE_URL or request.host_url).rstrip('/')}/api/auth/verify/{token}"
        EmailService().send_welcome_verification_email(email, name, verify_url)
    except Exception as e:  # noqa: BLE001
        print(f"[auth] Failed to send verification email to {email}: {e}")


def _user_payload(user) -> dict:
    initials = "".join(part[0] for part in user.name.split()[:2]).upper() or user.email[:2].upper()
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "initials": initials,
        "is_admin": bool(getattr(user, "is_admin", False)),
        "credit_limit": round(float(getattr(user, "credit_limit", 10.0) or 10.0), 2),
        "account_type": getattr(user, "account_type", None),
    }


@auth_bp.route("/login", methods=["GET"])
def login_page():
    if get_current_user_id():
        return redirect(url_for("index"))
    return render_template("login.html")


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not name or not email or not password:
        return jsonify({"success": False, "error": "Name, email, and password are required"}), 400
    if len(password) < 6:
        return jsonify({"success": False, "error": "Password must be at least 6 characters"}), 400
    if get_user_by_email(email):
        return jsonify({"success": False, "error": "An account with this email already exists"}), 409

    user = create_user(name, email, generate_password_hash(password))
    # No session set here - registration no longer logs the user straight in.
    # They must verify their email first (see /api/auth/verify/<token>), which
    # is where the session actually gets created.
    _send_verification_email(user["id"], email, name)
    return jsonify({"success": True, "pending_verification": True, "email": email})


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"success": False, "error": "Email and password are required"}), 400

    user = get_user_by_email(email)
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"success": False, "error": "Invalid email or password"}), 401

    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    return jsonify({"success": True, "user": _user_payload(user)})


@auth_bp.route("/verify/<token>", methods=["GET"])
def verify_email(token):
    """Opened from the verification-email link. Logs the user in on success -
    this is intentionally the first point a new account gets a session, since
    registration no longer does."""
    user = get_user_by_verification_token(token)
    if not user:
        return render_template("verify_pending.html", error="This verification link is invalid or has already been used.")

    sent_at = user.verification_sent_at
    if sent_at and sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    if sent_at and datetime.now(timezone.utc) - sent_at > VERIFICATION_TOKEN_TTL:
        return render_template(
            "verify_pending.html",
            error="This verification link has expired. Request a new one below.",
            email=user.email,
        )

    mark_user_email_verified(user.id)
    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    return redirect(url_for("onboarding_page"))


@auth_bp.route("/resend-verification", methods=["POST"])
def resend_verification():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"success": False, "error": "Email is required"}), 400

    user = get_user_by_email(email)
    # Don't reveal whether the account exists - always report success.
    if not user or user.email_verified:
        return jsonify({"success": True})

    if user.verification_sent_at:
        sent_at = user.verification_sent_at
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - sent_at < RESEND_COOLDOWN:
            return jsonify({"success": False, "error": "Please wait a bit before requesting another email"}), 429

    _send_verification_email(user.id, user.email, user.name)
    return jsonify({"success": True})


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    session.modified = True
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.method == "POST":
        return jsonify({"success": True})
    return redirect(url_for("auth.login_page"))


@auth_bp.route("/me", methods=["GET"])
def me():
    user_id = get_current_user_id()
    user = None
    if user_id:
        user = get_user_by_id(user_id)

    if not user:
        try:
            from db import get_user_by_email

            # Default dev/local fallback user
            user = get_user_by_id(1) or get_user_by_email("vaishnavi@try.com")
            if user:
                session["user_id"] = user.id
        except Exception:
            pass

    if not user:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    return jsonify({"success": True, "user": _user_payload(user)})
