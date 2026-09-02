from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from auth.utils import get_current_user_id, login_required_page
from db import create_user, get_user_by_email, get_user_by_id

auth_bp = Blueprint("auth", __name__)


def _user_payload(user) -> dict:
    initials = "".join(part[0] for part in user.name.split()[:2]).upper() or user.email[:2].upper()
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "initials": initials,
        "is_admin": bool(getattr(user, "is_admin", False)),
        "credit_limit": round(float(getattr(user, "credit_limit", 10.0) or 10.0), 2),
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
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    created = get_user_by_id(user["id"])
    return jsonify({"success": True, "user": _user_payload(created)})


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
