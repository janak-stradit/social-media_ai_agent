from functools import wraps

from flask import jsonify, redirect, session, url_for


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


def login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not get_current_user_id():
            return redirect(url_for("auth.login_page"))
        return view(*args, **kwargs)

    return wrapped
