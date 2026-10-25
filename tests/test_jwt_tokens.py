"""API bearer tokens (auth/jwt_tokens.py): signing in for a token, using it on
/api/* routes, refreshing it, and it ending with the account's sessions."""

import time
import uuid

import jwt
import pytest
from werkzeug.security import generate_password_hash

import db
from auth import jwt_tokens
from config import Config

PASSWORD = "correct horse battery staple"


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def user():
    return db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", generate_password_hash(PASSWORD))


def _sign_in(client, user, **extra):
    return client.post("/api/auth/token", json={"email": user["email"], "password": PASSWORD, **extra})


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_password_gets_a_token_pair(app, user):
    reply = _sign_in(app.test_client(), user)
    body = reply.get_json()
    assert reply.status_code == 200
    assert body["token_type"] == "Bearer" and body["expires_in"] == Config.JWT_ACCESS_TTL_SECONDS
    assert body["access_token"] and body["refresh_token"] and body["user"]["id"] == user["id"]


def test_wrong_password_gets_no_token(app, user):
    reply = app.test_client().post("/api/auth/token", json={"email": user["email"], "password": "nope-nope"})
    assert reply.status_code == 401 and "access_token" not in reply.get_json()


def test_token_opens_protected_routes_without_a_cookie(app, user):
    token = _sign_in(app.test_client(), user).get_json()["access_token"]
    client = app.test_client()  # a fresh client: no session cookie
    assert client.get("/api/auth/me").status_code == 401
    reply = client.get("/api/auth/me", headers=_auth(token))
    assert reply.status_code == 200 and reply.get_json()["user"]["id"] == user["id"]


@pytest.mark.parametrize("token", [
    "not-a-jwt",
    jwt.encode({"sub": "1", "sv": 0, "typ": "access", "iss": "avir-ai", "iat": 0, "exp": 9999999999},
               "someone-elses-key", algorithm="HS256"),
])
def test_bad_tokens_are_refused(app, token):
    reply = app.test_client().get("/api/auth/me", headers=_auth(token))
    assert reply.status_code == 401 and reply.get_json()["code"] == "invalid_token"


def test_expired_token_is_refused(app, user, monkeypatch):
    monkeypatch.setattr(Config, "JWT_ACCESS_TTL_SECONDS", -10)
    token = jwt_tokens.issue_tokens(user["id"])["access_token"]
    assert app.test_client().get("/api/auth/me", headers=_auth(token)).status_code == 401


def test_refresh_token_is_not_an_access_token(app, user):
    tokens = _sign_in(app.test_client(), user).get_json()
    assert app.test_client().get("/api/auth/me", headers=_auth(tokens["refresh_token"])).status_code == 401


def test_refresh_gives_a_new_pair(app, user):
    tokens = _sign_in(app.test_client(), user).get_json()
    reply = app.test_client().post("/api/auth/token/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reply.status_code == 200
    assert app.test_client().get("/api/auth/me", headers=_auth(reply.get_json()["access_token"])).status_code == 200
    # an access token can't be used to refresh
    reply = app.test_client().post("/api/auth/token/refresh", json={"refresh_token": tokens["access_token"]})
    assert reply.status_code == 401


def test_ending_sessions_ends_tokens(app, user):
    tokens = _sign_in(app.test_client(), user).get_json()
    db.bump_session_version(user["id"])  # password reset, "Sign out everywhere", ...
    client = app.test_client()
    assert client.get("/api/auth/me", headers=_auth(tokens["access_token"])).status_code == 401
    assert client.post("/api/auth/token/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_two_step_accounts_need_the_code(app, user):
    from auth import two_factor

    secret = two_factor.new_secret()
    db.set_totp(user["id"], two_factor.encrypt_secret(secret), enabled=True)
    reply = _sign_in(app.test_client(), user)
    assert reply.status_code == 401 and reply.get_json()["two_factor_required"]
    assert "access_token" not in reply.get_json()
    assert _sign_in(app.test_client(), user, code="000000").status_code == 401

    code = two_factor._code_at(secret, int(time.time() // two_factor.STEP))
    reply = _sign_in(app.test_client(), user, code=code)
    assert reply.status_code == 200 and reply.get_json()["access_token"]


def test_tokens_only_work_on_the_api(app, user):
    token = jwt_tokens.issue_tokens(user["id"])["access_token"]
    reply = app.test_client().get("/dashboard", headers=_auth(token))
    assert reply.status_code == 302  # pages still need the browser sign-in
