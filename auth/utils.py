from functools import wraps
from flask import jsonify, redirect, session, url_for
from db import get_user_by_id


def get_current_user_id() -> int | None:
    user_id = session.get("user_id")
    return int(user_id) if user_id is not None else None


def login_required_api(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not get_current_user_id():
            return jsonify({"error": "Authentication required", "success": False}), 401
        return view(*args, **kwargs)

    return wrapped


def admin_required_api(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = get_current_user_id()
        if not user_id:
            return jsonify({"error": "Authentication required", "success": False}), 401
        user = get_user_by_id(user_id)
        if not user or not getattr(user, "is_admin", False):
            return jsonify({"error": "Admin access required", "success": False}), 403
        return view(*args, **kwargs)

    return wrapped


def login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not get_current_user_id():
            return redirect(url_for("auth.login_page"))
        return view(*args, **kwargs)

    return wrapped

