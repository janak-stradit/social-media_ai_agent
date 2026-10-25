"""Two-step sign-in (auth/two_factor.py) and the audit log of admin changes."""

import time
import uuid

import pytest
from werkzeug.security import generate_password_hash

import db
from auth import two_factor


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _user(is_admin=False, password="correct-horse"):
    email = f"{uuid.uuid4().hex[:10]}@example.com"
    user = db.create_user("Priya", email, generate_password_hash(password), is_admin=is_admin)
    return user, email


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def _enable(client, now=None):
    secret = client.post("/api/auth/2fa/setup").get_json()["secret"]
    code = two_factor._code_at(secret, int((now or time.time()) // two_factor.STEP))
    assert client.post("/api/auth/2fa/enable", json={"code": code}).get_json()["enabled"] is True
    return secret


def test_codes_match_the_standard_test_vector():
    # RFC 6238 SHA-1 secret "12345678901234567890" at 59 s -> 94287082 (8 digits; last 6 = 287082)
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode()
    assert two_factor._code_at(secret, 59 // 30) == "287082"


def test_a_code_works_once_and_old_codes_fail():
    secret = two_factor.new_secret()
    now = 1_800_000_000
    code = two_factor._code_at(secret, now // 30)
    step = two_factor.check_code(secret, code, None, now)
    assert step == now // 30
    assert two_factor.check_code(secret, code, step, now) is None  # replay
    assert two_factor.check_code(secret, two_factor._code_at(secret, now // 30 - 5), None, now) is None
    assert two_factor.check_code(secret, "12345", None, now) is None


def test_secret_is_stored_encrypted(app):
    user, _ = _user()
    secret = _login(app, user).post("/api/auth/2fa/setup").get_json()["secret"]
    stored = db.get_totp_state(user["id"])["secret"]
    assert secret not in stored and two_factor.decrypt_secret(stored) == secret


def test_sign_in_asks_for_the_code_once_it_is_on(app):
    user, email = _user()
    secret = _enable(_login(app, user), now=time.time() - 30)  # set up a step ago, so the current code is unused
    client = app.test_client()

    first = client.post("/api/auth/login", json={"email": email, "password": "correct-horse"}).get_json()
    assert first["two_factor_required"] is True
    assert client.get("/api/auth/me").status_code == 401  # not signed in yet
    assert client.post("/api/auth/2fa/verify", json={"code": "000000"}).status_code == 401
    code = two_factor._code_at(secret, int(time.time() // 30))
    assert client.post("/api/auth/2fa/verify", json={"code": code}).get_json()["success"] is True
    assert client.get("/api/auth/me").status_code == 200


def test_turning_it_off_needs_a_code(app):
    user, _ = _user()
    client = _login(app, user)
    secret = _enable(client, now=time.time() - 30)
    assert client.post("/api/auth/2fa/disable", json={"code": "000000"}).status_code == 400
    code = two_factor._code_at(secret, int(time.time() // 30))
    assert client.post("/api/auth/2fa/disable", json={"code": code}).get_json()["enabled"] is False


def test_admin_tools_need_two_step_sign_in(app):
    admin, _ = _user(is_admin=True)
    client = _login(app, admin)
    blocked = client.get("/api/admin/plans")
    assert blocked.status_code == 403 and blocked.get_json()["code"] == "admin_2fa_required"
    assert client.get("/admin").headers["Location"].endswith("/settings#security")
    _enable(client)
    assert client.get("/api/admin/plans").status_code == 200


def test_admin_changes_are_in_the_audit_log(app, monkeypatch):
    from config import Config

    monkeypatch.setattr(Config, "ADMIN_2FA_REQUIRED", False)
    admin, admin_email = _user(is_admin=True)
    user, user_email = _user()
    client = _login(app, admin)
    client.put(f"/api/admin/users/{user['id']}/plan", json={"plan": "growth"})
    client.post(f"/api/admin/users/{user['id']}/packs", json={"kind": "image", "quantity": 10, "note": "UPI"})
    client.post(f"/api/admin/users/{user['id']}/credits", json={"add_amount": 5})

    entries = client.get(f"/api/admin/audit-log?user={user['id']}").get_json()["entries"]
    actions = [e["action"] for e in entries]
    assert actions == ["credits.add", "pack.granted", "plan.assigned"]  # newest first
    plan_change = entries[-1]
    assert plan_change["actor"] == admin_email and plan_change["target"] == user_email
    assert plan_change["details"]["to"] == "growth" and "from" in plan_change["details"]
    assert entries[1]["details"]["note"] == "UPI"
    assert _login(app, user).get("/api/admin/audit-log").status_code == 403
