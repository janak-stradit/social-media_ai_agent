"""The extra protection layers (SECURITY.md): ending sessions everywhere,
sign-in on data routes, per-account limits, hidden server errors, stronger
passwords, image uploads checked by content, and extra browser headers."""

import io
import uuid

import pytest
from flask import jsonify
from werkzeug.datastructures import FileStorage

import db


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)

    @app.route("/api/_test_boom")
    def _boom():  # a route that leaks an internal error, as some do
        return jsonify({"error": 'psycopg2.OperationalError: password authentication failed for user "avir"'}), 500

    return app


def _user(**kw):
    return db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "h", **kw)


def _browser(app, user):
    """A browser signed in the way the sign-in routes do it."""
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
        s["sv"] = db.get_session_version(user["id"]) or 0
    return client


# ── Sessions can be ended everywhere ────────────────────────────────────────


def test_ending_sessions_signs_out_every_browser(app):
    user = _user()
    phone, laptop = _browser(app, user), _browser(app, user)
    assert phone.get("/api/me/notifications").status_code == 200
    db.bump_session_version(user["id"])  # e.g. the password was reset
    r = phone.get("/api/me/notifications")
    assert r.status_code == 401 and r.get_json()["code"] == "session_ended"
    assert laptop.get("/settings").headers["Location"].endswith("/login")
    assert phone.get("/api/me/notifications").status_code == 401  # the cookie was cleared too


def test_sign_out_everywhere_else_keeps_this_browser(app):
    user = _user()
    here, lost_phone = _browser(app, user), _browser(app, user)
    assert here.post("/api/auth/sessions/end-others").get_json()["success"]
    assert here.get("/api/me/notifications").status_code == 200
    assert lost_phone.get("/api/me/notifications").status_code == 401


def test_a_deactivated_account_is_signed_out(app):
    admin, user = _user(is_admin=True), _user()
    browser = _browser(app, user)
    _browser(app, admin).post(f"/api/admin/users/{user['id']}/active", json={"is_active": False})
    assert browser.get("/api/me/notifications").status_code == 401


def test_a_deleted_account_is_signed_out(app):
    browser = app.test_client()
    with browser.session_transaction() as s:
        s["user_id"] = 987654321
    assert browser.get("/api/me/notifications").status_code == 401


def test_a_new_password_ends_old_sessions(app, monkeypatch):
    from auth import routes as auth_routes

    user = _user()
    old_browser = _browser(app, user)
    found = db.get_user_by_id(user["id"])
    monkeypatch.setattr(auth_routes, "get_user_for_reset_token", lambda token: found)
    r = app.test_client().post("/api/auth/reset-password", json={"token": "t", "password": "violet-harbour-lantern"})
    assert r.get_json()["success"]
    assert old_browser.get("/api/me/notifications").status_code == 401


# ── Sign-in on data routes, per-account limits ──────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/competitor-posts?target=x",
        "/api/platform-posts?platform=linkedin",
        "/api/competitor-posts-db?platform=linkedin",
        "/api/stradit-projects",
    ],
)
def test_data_routes_need_sign_in(app, path):
    assert app.test_client().get(path).status_code == 401


def test_ai_routes_are_limited_per_account(app, monkeypatch):
    from auth import rate_limit
    from services import repurpose_service

    monkeypatch.setitem(rate_limit.RULES, "ai_user", (2, 60))
    monkeypatch.setattr(repurpose_service, "read_url", lambda url: {"title": "T", "text": "x" * 300, "source": url})
    monkeypatch.setattr(
        repurpose_service,
        "to_angles",
        lambda doc: {"summary": "", "angles": [{"title": "a", "brief": "b", "format": "text"}], "usage": {}},
    )
    browser, other = _browser(app, _user()), _browser(app, _user())
    codes = [browser.post("/api/repurpose", json={"url": "https://a.com"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert other.post("/api/repurpose", json={"url": "https://a.com"}).status_code == 200  # counted per account


# ── Server errors don't leak ────────────────────────────────────────────────


def test_server_errors_hide_their_details(app):
    body = app.test_client().get("/api/_test_boom").get_json()
    assert "psycopg2" not in body["error"] and "Reference:" in body["error"]


# ── Passwords ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "password,ok",
    [
        ("Password123!", False),
        ("qwerty12", False),
        ("aaaaaaaa", False),
        ("short", False),
        ("priya.shah2026", False),  # contains the email's name part
        ("violet-harbour-lantern", True),
        ("Tr4ding-with-c0ffee", True),
    ],
)
def test_password_rules(password, ok):
    from auth.routes import password_problem

    assert (password_problem(password, "priya.shah@acme.com", "Priya Shah") is None) is ok


def test_sign_up_refuses_a_common_password(app):
    r = app.test_client().post(
        "/api/auth/register", json={"name": "Sam", "email": f"{uuid.uuid4().hex[:8]}@x.com", "password": "password1", "accept_terms": True}
    )
    assert r.status_code == 400 and "too easy" in r.get_json()["error"]


# ── Uploads, headers ────────────────────────────────────────────────────────


def test_only_real_images_pass(app):
    from PIL import Image

    from api.routes import is_real_image

    png = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(png, format="PNG")
    with app.test_request_context():
        assert is_real_image(FileStorage(io.BytesIO(png.getvalue()), "a.png"))
        assert not is_real_image(FileStorage(io.BytesIO(b"<script>alert(1)</script>"), "evil.png"))


def test_extra_headers(app):
    r = app.test_client().get("/api/health")
    assert r.headers["Cross-Origin-Opener-Policy"] == "same-origin-allow-popups"
    assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Permitted-Cross-Domain-Policies"] == "none"
