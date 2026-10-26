"""Protections against outside attackers: cross-site requests, brute-force
sign-in, security headers, secret key and default admin password."""

import uuid

import pytest
from werkzeug.security import generate_password_hash

import db


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def test_other_websites_get_no_cross_site_access(app):
    res = app.test_client().get("/api/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in res.headers


def test_changes_sent_from_another_website_are_refused(app):
    client = app.test_client()
    blocked = client.post(
        "/api/auth/login", json={"email": "a@b.c", "password": "x"}, headers={"Origin": "https://evil.example"}
    )
    assert blocked.status_code == 403
    same_site = client.post(
        "/api/auth/login", json={"email": "a@b.c", "password": "x"}, headers={"Origin": "http://localhost"}
    )
    assert same_site.status_code == 401  # reaches the login check
    no_origin = client.post("/api/auth/login", json={"email": "a@b.c", "password": "x"})
    assert no_origin.status_code == 401  # scripts, mail apps, webhooks


def test_security_headers(app):
    headers = app.test_client().get("/api/health").headers
    assert headers["X-Frame-Options"] == "SAMEORIGIN" and headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]


def test_password_guessing_is_stopped(app):
    email = f"{uuid.uuid4().hex[:10]}@example.com"
    db.create_user("Priya", email, generate_password_hash("correct-horse"))
    client = app.test_client()
    codes = [
        client.post(
            "/api/auth/login", json={"email": email, "password": f"guess{i}"}, headers={"X-Real-IP": f"10.0.0.{i}"}
        ).status_code
        for i in range(9)
    ]
    assert codes[:8] == [401] * 8 and codes[8] == 429  # 8 tries per account in 15 minutes
    # even the right password waits until the window passes
    assert (
        client.post(
            "/api/auth/login", json={"email": email, "password": "correct-horse"}, headers={"X-Real-IP": "10.0.1.1"}
        ).status_code
        == 429
    )


def test_a_successful_sign_in_clears_the_count(app):
    email = f"{uuid.uuid4().hex[:10]}@example.com"
    db.create_user("Priya", email, generate_password_hash("correct-horse"))
    client = app.test_client()
    for i in range(5):
        client.post("/api/auth/login", json={"email": email, "password": "wrong"}, headers={"X-Real-IP": "10.1.0.1"})
    assert (
        client.post(
            "/api/auth/login", json={"email": email, "password": "correct-horse"}, headers={"X-Real-IP": "10.1.0.2"}
        ).status_code
        == 200
    )
    for i in range(7):
        assert (
            client.post(
                "/api/auth/login", json={"email": email, "password": "wrong"}, headers={"X-Real-IP": "10.1.0.3"}
            ).status_code
            == 401
        )


def test_new_passwords_need_eight_characters(app):
    res = app.test_client().post(
        "/api/auth/register", json={"name": "A", "email": "short@example.com", "password": "abc1234", "accept_terms": True}
    )
    assert res.status_code == 400 and "8 characters" in res.get_json()["error"]


def test_production_refuses_the_default_secret_key(monkeypatch):
    from app import create_app
    from config import ProductionConfig

    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", "dev-secret-key")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app("production")


def test_no_session_means_not_signed_in(app):
    # used to sign any visitor in as user #1, the first admin
    client = app.test_client()
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/admin/users").status_code == 401


def test_the_account_owner_is_told_once_when_sign_in_is_paused(app, monkeypatch):
    from services.email_service import EmailService

    mails = []
    monkeypatch.setattr(EmailService, "send_billing_email", lambda self, **kw: mails.append(kw) or {"success": True})
    email = f"{uuid.uuid4().hex[:10]}@example.com"
    db.create_user("Priya", email, generate_password_hash("correct-horse"))
    client = app.test_client()
    for i in range(12):
        client.post(
            "/api/auth/login", json={"email": email, "password": f"guess{i}"}, headers={"X-Real-IP": f"10.9.0.{i}"}
        )
    assert len(mails) == 1 and mails[0]["to_email"] == email and "paused for 15 minutes" in mails[0]["lines"][0]
    user = db.get_user_by_email(email)
    assert any(e["action"] == "login.locked" for e in db.list_audit_log(10, user.id))


def test_no_lockout_email_for_unknown_addresses(app, monkeypatch):
    from services.email_service import EmailService

    mails = []
    monkeypatch.setattr(EmailService, "send_billing_email", lambda self, **kw: mails.append(kw) or {"success": True})
    client = app.test_client()
    for i in range(10):
        client.post(
            "/api/auth/login",
            json={"email": "nobody-here@example.com", "password": "x"},
            headers={"X-Real-IP": f"10.8.0.{i}"},
        )
    assert not mails
