"""Limits on repeated attempts at sign-in, sign-up and password reset, so
passwords can't be guessed by brute force and the email endpoints can't be
used to flood inboxes.

Counts live in the same store as the sign-up captcha (Redis when it is
running, so every gunicorn worker shares them; memory otherwise). Each rule
is "at most N attempts per key in W seconds"; keys are the visitor's IP and,
where there is one, the email address being tried.
"""

import hashlib
from functools import wraps

from flask import jsonify, request

from auth.captcha import get_store

# name: (attempts, window in seconds)
RULES = {
    "login_ip": (20, 10 * 60),
    "login_email": (8, 15 * 60),
    "register_ip": (10, 60 * 60),
    "reset_ip": (10, 60 * 60),
    "reset_email": (5, 60 * 60),
    "totp_user": (6, 10 * 60),  # wrong authenticator codes per account
}


def client_ip() -> str:
    """The visitor's address as nginx passes it on (X-Real-IP), else the socket's."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (
        request.headers.get("X-Real-IP")
        or (forwarded.split(",")[0].strip() if forwarded else request.remote_addr)
        or "?"
    )


def _key(rule: str, value: str) -> str:
    # emails are hashed so they never sit in Redis in plain text
    return f"rl:{rule}:{hashlib.sha256(value.lower().encode()).hexdigest()[:32]}"


def count(rule: str, value: str | None) -> int:
    """Count one attempt and return how many there have been in the window."""
    if not value:
        return 0
    return get_store().incr(_key(rule, value), RULES[rule][1])


def hit(rule: str, value: str | None) -> bool:
    """Count one attempt; True when this one goes over the limit."""
    return count(rule, value) > RULES[rule][0]


def reset(rule: str, value: str | None) -> None:
    """Forget the attempts for a key (e.g. after a successful sign-in)."""
    if value:
        get_store().pop(_key(rule, value))


def too_many(message: str = "Too many attempts. Please wait a few minutes and try again."):
    return jsonify({"success": False, "error": message, "code": "rate_limited"}), 429


def limit_by_ip(rule: str):
    """Decorator: at most RULES[rule] requests per visitor IP."""

    def wrap(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if hit(rule, client_ip()):
                return too_many()
            return view(*args, **kwargs)

        return wrapped

    return wrap
