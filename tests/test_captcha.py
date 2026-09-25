import json
import uuid

import pytest
from flask import Flask

from auth import captcha
from auth.captcha import captcha_bp
from auth.routes import auth_bp


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(captcha, "_store", captcha._MemoryStore())
    monkeypatch.setattr(captcha.Config, "CAPTCHA_ENABLED", True)
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test")
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(captcha_bp, url_prefix="/api/auth/captcha")
    return app.test_client()


def human_track(target_x, steps=25):
    """Ease-out drag with slight vertical wobble."""
    points = []
    for i in range(steps + 1):
        f = i / steps
        points.append([target_x * (1 - (1 - f) ** 2), (i % 3) - 1, 1000 + i * 30])
    return points


def new_challenge(client):
    res = client.get("/api/auth/captcha/challenge")
    assert res.status_code == 200
    body = res.get_json()
    target = json.loads(captcha.get_store().get(f"captcha:ch:{body['challenge_id']}"))
    return body, target["x"]


def solve(client):
    body, x = new_challenge(client)
    res = client.post("/api/auth/captcha/verify", json={
        "challenge_id": body["challenge_id"], "x": x, "track": human_track(x),
    })
    assert res.status_code == 200, res.get_json()
    return res.get_json()["captcha_token"]


def test_challenge_hides_answer(client):
    body, x = new_challenge(client)
    assert body["bg"].startswith("data:image/png;base64,")
    assert body["piece"].startswith("data:image/png;base64,")
    assert "x" not in body
    assert captcha.PIECE + 24 <= x <= captcha.CANVAS_W - captcha.PIECE - 8


def test_correct_answer_issues_token(client):
    assert solve(client)


def test_wrong_position_rejected(client):
    body, x = new_challenge(client)
    wrong = x + 30
    res = client.post("/api/auth/captcha/verify", json={
        "challenge_id": body["challenge_id"], "x": wrong, "track": human_track(wrong),
    })
    assert res.status_code == 400
    assert "captcha_token" not in res.get_json()


def test_linear_bot_track_rejected(client):
    body, x = new_challenge(client)
    track = [[x * i / 20, 0, 1000 + i * 20] for i in range(21)]
    res = client.post("/api/auth/captcha/verify", json={
        "challenge_id": body["challenge_id"], "x": x, "track": track,
    })
    assert res.status_code == 400


def test_challenge_dies_after_max_attempts(client):
    body, x = new_challenge(client)
    for _ in range(captcha.MAX_ATTEMPTS):
        res = client.post("/api/auth/captcha/verify", json={
            "challenge_id": body["challenge_id"], "x": x + 40, "track": human_track(x + 40),
        })
    assert res.get_json().get("expired") is True
    # Even the right answer no longer works.
    res = client.post("/api/auth/captcha/verify", json={
        "challenge_id": body["challenge_id"], "x": x, "track": human_track(x),
    })
    assert res.status_code == 400


def test_challenge_is_single_use(client):
    body, x = new_challenge(client)
    payload = {"challenge_id": body["challenge_id"], "x": x, "track": human_track(x)}
    assert client.post("/api/auth/captcha/verify", json=payload).status_code == 200
    assert client.post("/api/auth/captcha/verify", json=payload).status_code == 400


def _signup(client, token):
    return client.post("/api/auth/register", json={
        "name": "Test User",
        "email": f"captcha-{uuid.uuid4().hex[:8]}@example.com",
        "password": "secret123",
        "captcha_token": token,
    })


def test_register_requires_token(client):
    res = _signup(client, None)
    assert res.status_code == 400
    assert res.get_json()["captcha_required"] is True


def test_register_with_token_and_token_is_one_use(client, monkeypatch):
    monkeypatch.setattr("auth.routes._send_verification_email", lambda *a, **k: None)
    token = solve(client)
    assert _signup(client, token).status_code == 200
    assert _signup(client, token).status_code == 400
