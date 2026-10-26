import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from auth.captcha import consume_pass_token
from auth.rate_limit import RULES, client_ip, count, hit, limit_by_ip, reset, too_many
from auth.utils import get_current_user_id, start_session
from config import Config
from db import (
    create_user,
    get_open_invitation_by_token,
    get_user_by_email,
    get_user_by_id,
    get_user_by_password_reset_token_hash,
    get_user_by_verification_token,
    mark_invitation_accepted,
    mark_user_email_verified,
    reset_user_password,
    set_user_password_reset_token,
    set_user_verification_token,
    user_currency,
)
from logging_setup import mask_email

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

VERIFICATION_TOKEN_TTL = timedelta(hours=24)
RESEND_COOLDOWN = timedelta(seconds=60)
PASSWORD_RESET_TTL = timedelta(hours=1)
MIN_PASSWORD_LENGTH = 8  # sign-up and password reset; existing shorter passwords still work

# The passwords attackers try first (from public breach lists). Checked as-is
# and with trailing digits/symbols removed, so "Password123!" is caught too.
_COMMON_PASSWORDS = frozenset("""
password passw0rd p@ssword p@ssw0rd 12345678 123456789 1234567890 qwerty qwertyuiop qwerty123 iloveyou
letmein welcome welcome1 admin administrator abc12345 abcd1234 football baseball monkey dragon sunshine
princess trustno1 superman batman master shadow michael jordan liverpool chelsea arsenal india123
login starwars whatever freedom hello123 changeme default secret computer internet google facebook
instagram linkedin avirai stradit 11111111 00000000 88888888 12341234 11223344 asdfghjk asdfasdf
zxcvbnm zxcvbnm1 1q2w3e4r 1qaz2wsx q1w2e3r4 aaaaaaaa
""".split())


def password_problem(password: str, email: str = "", name: str = "") -> str | None:
    """Why a new password isn't good enough, or None. Length plus the
    commonest guesses; the password must not be the email or the name."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    lowered = password.lower()
    core = lowered.rstrip("0123456789!@#$%^&*._-")
    if lowered in _COMMON_PASSWORDS or (len(core) >= 4 and core in _COMMON_PASSWORDS) or len(set(lowered)) <= 2:
        return "That password is too easy to guess. Try a longer phrase, e.g. three unrelated words."
    local = (email or "").split("@")[0].lower()
    if (len(local) >= 4 and local in lowered) or (name and len(name) >= 4 and name.lower().replace(" ", "") in lowered):
        return "Don't use your name or email address in your password."
    return None


def _as_utc(dt):
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_user_for_reset_token(token: str):
    """Returns the user a password-reset token belongs to, or None if the
    token is unknown, already used, or older than PASSWORD_RESET_TTL. Shared
    by the reset page (app.py reset_password_page) and the reset API."""
    if not token or len(token) > 128:
        return None
    user = get_user_by_password_reset_token_hash(_hash_reset_token(token))
    if not user:
        return None
    sent_at = _as_utc(user.password_reset_sent_at)
    if not sent_at or datetime.now(timezone.utc) - sent_at > PASSWORD_RESET_TTL:
        return None
    return user


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
        logger.warning(f"Failed to send verification email to {mask_email(email)}: {e}")


def _user_payload(user) -> dict:
    initials = "".join(part[0] for part in user.name.split()[:2]).upper() or user.email[:2].upper()
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "initials": initials,
        "is_admin": bool(getattr(user, "is_admin", False)),
        "credit_limit": round(float(getattr(user, "credit_limit", 0.0) or 0.0), 2),
        "currency": user_currency(user.id),
        "account_type": getattr(user, "account_type", None),
    }


@auth_bp.route("/login", methods=["GET"])
def login_page():
    if get_current_user_id():
        return redirect(url_for("index"))
    return render_template("login.html")


def _claim_lead_preview(token, user_id: int, email: str) -> None:
    """Signed up from their preview page (Admin -> Invitations): their brand
    profile and draft posts move into the new account. Never blocks sign-up."""
    token = (token or "").strip() if isinstance(token, str) else ""
    if not token:
        return
    try:
        from services.lead_preview_service import claim_preview

        claimed = claim_preview(token, user_id, email)
        if claimed and session.get("user_id") == user_id:
            session["starter_conversation"] = claimed["conversation_id"]
    except Exception as err:  # noqa: BLE001
        logger.warning(f"Could not copy lead preview into user {user_id}: {err}")


@auth_bp.route("/register", methods=["POST"])
@limit_by_ip("register_ip")
def register():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not name or not email or not password:
        return jsonify({"success": False, "error": "Name, email, and password are required"}), 400
    if data.get("accept_terms") is not True:
        return jsonify({"success": False, "error": "Please agree to the Terms of Service and Privacy Policy."}), 400
    problem = password_problem(password, email, name)
    if problem:
        return jsonify({"success": False, "error": problem}), 400
    if get_user_by_email(email):
        return jsonify({"success": False, "error": "An account with this email already exists"}), 409

    # Signing up through an admin's invitation link (Admin -> Invitations):
    # the link was emailed to this address, which proves it's theirs - so no
    # captcha and no verification email; they're signed in straight away.
    invite_token = (data.get("invite_token") or "").strip()
    if invite_token:
        invitation = get_open_invitation_by_token(invite_token)
        if not invitation:
            return jsonify(
                {"success": False, "error": "This invitation link has expired or was already used. Ask for a new one."}
            ), 400
        if invitation["email"] != email:
            return jsonify({"success": False, "error": "Please sign up with the email address that was invited."}), 400
        user = create_user(name, email, generate_password_hash(password))
        _record_terms_accepted(user["id"])
        mark_user_email_verified(user["id"])
        mark_invitation_accepted(invitation["id"])
        session.clear()
        start_session(user["id"])
        _claim_lead_preview(data.get("preview_token"), user["id"], email)
        return jsonify({"success": True, "redirect": url_for("onboarding_page")})

    # Checked last so a typo'd form doesn't burn the one-use token.
    if Config.CAPTCHA_ENABLED and not consume_pass_token(data.get("captcha_token")):
        return jsonify({
            "success": False,
            "captcha_required": True,
            "error": "Please complete the security verification",
        }), 400

    user = create_user(name, email, generate_password_hash(password))
    _record_terms_accepted(user["id"])
    _claim_lead_preview(data.get("preview_token"), user["id"], email)
    # No session set here - registration no longer logs the user straight in.
    # They must verify their email first (see /api/auth/verify/<token>), which
    # is where the session actually gets created.
    _send_verification_email(user["id"], email, name)
    return jsonify({"success": True, "pending_verification": True, "email": email})


def _record_terms_accepted(user_id: int) -> None:
    """Which version of the Terms and Privacy Policy (legal/) this sign-up agreed to."""
    from db import log_audit

    log_audit("terms.accepted", actor_user_id=user_id, target_user_id=user_id,
              details={"version": Config.LEGAL_VERSION}, ip=client_ip())


def _check_password(data: dict):
    """(user, None) for a right email + password, else (None, error response).
    Shared by the browser sign-in and the API token sign-in, so both have the
    same brute-force limits."""
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return None, (jsonify({"success": False, "error": "Email and password are required"}), 400)
    # Brute-force protection: per visitor and per account being tried
    ip_blocked = hit("login_ip", client_ip())
    tries = count("login_email", email)
    if tries == RULES["login_email"][0] + 1:
        _notify_sign_in_lockout(email)  # the first attempt over the limit: tell the account's owner, once
    if ip_blocked or tries > RULES["login_email"][0]:
        return None, too_many("Too many sign-in attempts. Please wait 15 minutes, or reset your password.")

    user = get_user_by_email(email)
    if not user or not check_password_hash(user.password_hash, password):
        return None, (jsonify({"success": False, "error": "Invalid email or password"}), 401)

    reset("login_email", email)
    return user, None


@auth_bp.route("/login", methods=["POST"])
def login():
    user, error = _check_password(request.get_json() or {})
    if error:
        return error
    session.clear()
    if user.totp_enabled_at is not None:
        # Password was right; the authenticator code comes next (/api/auth/2fa/verify)
        session["pending_2fa"] = {"user_id": user.id, "at": int(time.time())}
        return jsonify({"success": True, "two_factor_required": True})
    start_session(user.id)
    return jsonify({"success": True, "user": _user_payload(user)})


def _notify_sign_in_lockout(email: str) -> None:
    """Someone keeps getting an account's password wrong: email its owner
    (once per lock window) and record it. Silent for unknown emails, so this
    can't be used to find out which addresses have accounts."""
    from db import log_audit

    user = get_user_by_email(email)
    if not user:
        return
    log_audit("login.locked", target_user_id=user.id, details={"attempts": RULES["login_email"][0] + 1}, ip=client_ip())
    try:
        from services.email_service import EmailService

        base = (Config.APP_BASE_URL or request.host_url).rstrip("/")
        EmailService().send_billing_email(
            to_email=user.email, name=user.name or "there", subject="Sign-in attempts on your account",
            headline="Someone tried to sign in to your account",
            lines=[f"There were {RULES['login_email'][0] + 1} sign-in attempts with a wrong password on your AVIR AI "
                   "account, so signing in is paused for 15 minutes.",
                   "If this was you, wait a few minutes or reset your password. If it wasn't, your account is still safe; "
                   "consider a new password and turning on two-step sign-in in Settings -> Security."],
            button="Reset my password", url=f"{base}/forgot-password",
            footer="You get this email to keep your AVIR AI account secure.")
    except Exception as err:  # noqa: BLE001 - the lock works without the email
        logger.warning(f"Lockout email to user {user.id} failed: {err}")


# ── Two-step sign-in (auth/two_factor.py) ─────────────────────────────────
PENDING_2FA_SECONDS = 5 * 60


def _check_two_factor_code(user_id: int, code):
    """None when the authenticator code is right (and marks it used), else an
    error response. Shared by the browser and API token sign-ins."""
    from auth import two_factor
    from db import get_totp_state, log_audit, mark_totp_used

    if hit("totp_user", str(user_id)):
        return too_many("Too many wrong codes. Wait a few minutes and sign in again.")
    state = get_totp_state(user_id)
    counter = two_factor.check_code(two_factor.decrypt_secret(state["secret"]), code, state["last_counter"])
    if counter is None:
        log_audit("login.2fa_failed", target_user_id=user_id, ip=client_ip())
        return jsonify({"success": False, "error": "That code isn't right. Check the app and try again."}), 401
    mark_totp_used(user_id, counter)
    reset("totp_user", str(user_id))
    return None


@auth_bp.route("/2fa/verify", methods=["POST"])
def verify_two_factor():
    """Second step of signing in: the six-digit code from the authenticator app."""
    pending = session.get("pending_2fa") or {}
    user_id = pending.get("user_id")
    if not user_id or time.time() - int(pending.get("at") or 0) > PENDING_2FA_SECONDS:
        session.pop("pending_2fa", None)
        return jsonify({"success": False, "error": "Your sign-in expired. Enter your email and password again."}), 401
    error = _check_two_factor_code(user_id, (request.get_json() or {}).get("code"))
    if error:
        return error
    session.clear()
    start_session(user_id)
    user = get_user_by_id(user_id)
    return jsonify({"success": True, "user": _user_payload(user)})


@auth_bp.route("/2fa", methods=["GET"])
def two_factor_status():
    from db import get_totp_state

    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    user = get_user_by_id(user_id)
    return jsonify({"success": True, "enabled": get_totp_state(user_id)["enabled"],
                    "required": bool(Config.ADMIN_2FA_REQUIRED and user and user.is_admin)})


@auth_bp.route("/2fa/setup", methods=["POST"])
def two_factor_setup():
    """Start setting up: a new secret, shown once as a QR code / key. Not on
    until a code from the app is confirmed (/2fa/enable)."""
    from auth import two_factor
    from db import get_totp_state, set_totp

    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    if get_totp_state(user_id)["enabled"]:
        return jsonify({"success": False, "error": "Two-step sign-in is already on."}), 409
    secret = two_factor.new_secret()
    set_totp(user_id, two_factor.encrypt_secret(secret), enabled=False)
    user = get_user_by_id(user_id)
    return jsonify({"success": True, "secret": secret, "uri": two_factor.provisioning_uri(secret, user.email)})


def _confirm_code(user_id):
    """The time step of a valid code in the request body, else None."""
    from auth import two_factor
    from db import get_totp_state

    if hit("totp_user", str(user_id)):
        return None
    state = get_totp_state(user_id)
    return two_factor.check_code(two_factor.decrypt_secret(state["secret"]), (request.get_json() or {}).get("code"),
                                 state["last_counter"])


@auth_bp.route("/2fa/enable", methods=["POST"])
def two_factor_enable():
    from db import get_totp_state, log_audit, set_totp

    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    state = get_totp_state(user_id)
    if not state["secret"]:
        return jsonify({"success": False, "error": "Start the set-up first."}), 400
    counter = _confirm_code(user_id)
    if counter is None:
        return jsonify({"success": False, "error": "That code isn't right. Check the app and try again."}), 400
    set_totp(user_id, state["secret"], enabled=True, last_counter=counter)
    log_audit("2fa.enabled", actor_user_id=user_id, target_user_id=user_id, ip=client_ip())
    _end_other_sessions(user_id)  # sessions from before two-step sign-in are ended
    return jsonify({"success": True, "enabled": True})


@auth_bp.route("/2fa/disable", methods=["POST"])
def two_factor_disable():
    """Turning it off needs a current code, so a stolen session can't remove it."""
    from db import get_totp_state, log_audit, set_totp

    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    if not get_totp_state(user_id)["enabled"]:
        return jsonify({"success": True, "enabled": False})
    if _confirm_code(user_id) is None:
        return jsonify({"success": False, "error": "That code isn't right. Check the app and try again."}), 400
    set_totp(user_id, None, enabled=False)
    log_audit("2fa.disabled", actor_user_id=user_id, target_user_id=user_id, ip=client_ip())
    _end_other_sessions(user_id)
    return jsonify({"success": True, "enabled": False})


@auth_bp.route("/verify/<token>", methods=["GET"])
def verify_email(token):
    """Opened from the verification-email link. Logs the user in on success -
    this is intentionally the first point a new account gets a session, since
    registration no longer does."""
    user = get_user_by_verification_token(token)
    if not user:
        return render_template(
            "verify_pending.html", error="This verification link is invalid or has already been used."
        )

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
    start_session(user.id)
    return redirect(url_for("onboarding_page"))


@auth_bp.route("/resend-verification", methods=["POST"])
@limit_by_ip("reset_ip")
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


@auth_bp.route("/forgot-password", methods=["POST"])
@limit_by_ip("reset_ip")
def forgot_password():
    """Emails a single-use reset link. Always answers the same way whether or
    not the account exists, so this can't be used to discover which emails
    are registered."""
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"success": False, "error": "Enter a valid email address"}), 400

    user = get_user_by_email(email)
    if user:
        last_sent = _as_utc(user.password_reset_sent_at)
        # Throttle per account; silently, so the response doesn't reveal
        # that the account exists.
        if not last_sent or datetime.now(timezone.utc) - last_sent >= RESEND_COOLDOWN:
            token = secrets.token_urlsafe(32)
            set_user_password_reset_token(user.id, _hash_reset_token(token))
            reset_url = f"{(Config.APP_BASE_URL or request.host_url).rstrip('/')}/reset-password/{token}"
            try:
                from services.email_service import EmailService

                EmailService().send_password_reset_email(
                    user.email, user.name, reset_url, int(PASSWORD_RESET_TTL.total_seconds() // 60)
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to send password reset email to {mask_email(user.email)}: {e}")
                # Local development without SMTP: surface the link in the
                # server console so the flow can still be tested. Never in
                # production, where the link must only go to the inbox.
                if current_app.debug:
                    logger.info(f"DEV ONLY - password reset link for {mask_email(user.email)}: {reset_url}")

    return jsonify(
        {"success": True, "message": "If an account exists for that email, a reset link is on its way."}
    )


@auth_bp.route("/reset-password", methods=["POST"])
@limit_by_ip("reset_ip")
def reset_password():
    data = request.get_json() or {}
    token = data.get("token") or ""
    password = data.get("password") or ""

    if len(password) < MIN_PASSWORD_LENGTH:
        return jsonify(
            {"success": False, "error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters"}
        ), 400

    user = get_user_for_reset_token(token)
    if not user:
        return jsonify(
            {"success": False, "error": "This reset link is invalid or has expired. Request a new one."}
        ), 400
    problem = password_problem(password, user.email, user.name or "")
    if problem:
        return jsonify({"success": False, "error": problem}), 400

    reset_user_password(user.id, generate_password_hash(password))
    # A new password ends every signed-in session (someone who knew the old
    # one is signed out too); this browser signs in fresh.
    from db import bump_session_version

    bump_session_version(user.id)
    session.clear()
    return jsonify({"success": True})


def _end_other_sessions(user_id: int) -> None:
    """Every other signed-in browser of the account is signed out; this one stays."""
    from db import bump_session_version

    session["sv"] = bump_session_version(user_id)


@auth_bp.route("/sessions/end-others", methods=["POST"])
def end_other_sessions():
    """Settings -> Security -> "Sign out everywhere else" (a lost phone, a shared computer)."""
    from db import log_audit

    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    _end_other_sessions(user_id)
    log_audit("sessions.ended", actor_user_id=user_id, target_user_id=user_id, ip=client_ip())
    return jsonify({"success": True})


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    session.modified = True
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.method == "POST":
        return jsonify({"success": True})
    return redirect(url_for("auth.login_page"))


# ── API bearer tokens (auth/jwt_tokens.py) ────────────────────────────────
@auth_bp.route("/token", methods=["POST"])
def issue_token():
    """Sign-in for API clients: email + password (+ `code` from the
    authenticator app when two-step sign-in is on) -> access + refresh token."""
    from auth.jwt_tokens import issue_tokens

    data = request.get_json() or {}
    user, error = _check_password(data)
    if error:
        return error
    if user.totp_enabled_at is not None:
        if not data.get("code"):
            return jsonify({"success": False, "two_factor_required": True,
                            "error": "Enter the code from your authenticator app as \"code\"."}), 401
        error = _check_two_factor_code(user.id, data.get("code"))
        if error:
            return error
    return jsonify({"success": True, "user": _user_payload(user), **issue_tokens(user.id)})


@auth_bp.route("/token/refresh", methods=["POST"])
def refresh_token():
    """A refresh token -> a new access + refresh token pair."""
    from auth.jwt_tokens import issue_tokens, user_id_from_token

    token = (request.get_json(silent=True) or {}).get("refresh_token") or ""
    user_id = user_id_from_token(token, kind="refresh") if isinstance(token, str) else None
    if user_id is None or not get_user_by_id(user_id):
        return jsonify({"success": False, "error": "Your refresh token is invalid or expired. Sign in again.",
                        "code": "invalid_token"}), 401
    return jsonify({"success": True, **issue_tokens(user_id)})


@auth_bp.route("/me", methods=["GET"])
def me():
    # No fallback account: a visitor without a session is not signed in. (This
    # used to sign them in as user #1 - the first admin - from any browser.)
    user_id = get_current_user_id()
    user = get_user_by_id(user_id) if user_id else None
    if not user:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    return jsonify({"success": True, "user": _user_payload(user)})
