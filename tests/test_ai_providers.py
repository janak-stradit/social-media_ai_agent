"""Admin -> AI providers, phase 1 (services/ai_router.py, services/ai_keys.py,
AI_PROVIDERS_PLAN.md): encrypted write-only keys, the catalog seeded from
ENV_FILE, connection tests, models, the router's fallbacks, and an honest AI
status. Providers are faked - no request leaves the machine."""

import json
import uuid

import pytest
from cryptography.fernet import Fernet

import db
from config import Config
from services import ai_keys, ai_router

FERNET_KEY = Fernet.generate_key().decode()


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(Config, "AI_KEYS_ENCRYPTION_KEY", FERNET_KEY)


@pytest.fixture
def public_dns(monkeypatch):
    """Made-up hostnames count as public; 127.x stays private (no real DNS in tests)."""
    from services import website_scraper_service as web

    monkeypatch.setattr(web, "_host_check", lambda host: "unsafe_url" if host.startswith("127.") else None)


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _login(app, admin=True):
    user = db.create_user("Asha", f"{uuid.uuid4().hex[:10]}@example.com", "h", is_admin=admin)
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


@pytest.fixture
def provider(monkeypatch):
    """A fake OpenAI-compatible /models endpoint; records the headers it got."""
    seen = {}

    class Response:
        def __init__(self, status, body):
            self.status_code, self._body, self.ok = status, body, status < 400

        def json(self):
            return self._body

    def get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers)
        if headers.get("Authorization") != "Bearer sk-good-1234":
            return Response(401, {"error": "bad key"})
        return Response(200, {"data": [{"id": "model-b"}, {"id": "model-a"}]})

    monkeypatch.setattr(ai_router.requests, "get", get)
    monkeypatch.setattr(ai_router, "check_base_url", lambda url: (url or "").rstrip("/"))  # no DNS in tests
    return seen


# ── Keys ─────────────────────────────────────────────────────────────────


def test_keys_are_encrypted_and_only_readable_with_their_key(keys, monkeypatch):
    token, last4 = ai_keys.encrypt("  sk-secret-abcd ")
    assert last4 == "abcd" and "sk-secret" not in token and ai_keys.decrypt(token) == "sk-secret-abcd"
    monkeypatch.setattr(Config, "AI_KEYS_ENCRYPTION_KEY", "a long passphrase, not a Fernet key")
    other, _ = ai_keys.encrypt("sk-other")
    assert ai_keys.decrypt(other) == "sk-other" and ai_keys.decrypt(token) is None  # a changed key reads nothing
    monkeypatch.setattr(Config, "AI_KEYS_ENCRYPTION_KEY", "")
    assert not ai_keys.ready() and ai_keys.decrypt(other) is None
    with pytest.raises(ai_keys.KeysUnavailable):
        ai_keys.encrypt("sk-x")


# ── Seeding and what runs today ──────────────────────────────────────────


def test_the_seed_names_env_variables_and_never_copies_keys(monkeypatch):
    monkeypatch.setattr(Config, "HEYROUTE_API_KEY", "hr-text-secret")
    monkeypatch.setattr(Config, "HEYROUTE_IMAGE_API_KEY", "hr-image-secret")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_API_KEY", "")
    monkeypatch.setattr(Config, "OPENAI_API_KEY", "sk-or-openrouter-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    plan = {c["name"]: c for c in ai_router._seed_plan()}
    assert plan["HeyRoute - text"]["key_env"] == "HEYROUTE_API_KEY"
    assert plan["HeyRoute - images"]["models"][0]["capabilities"] == ["image"]
    assert plan["OpenRouter"]["key_env"] == "OPENAI_API_KEY"  # an OpenRouter key kept in OPENAI_API_KEY
    assert "HeyRoute - video" not in plan and "OpenAI" not in plan
    assert "secret" not in json.dumps(list(plan.values()))


def test_seeding_happens_once(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_router, "_seed_plan", lambda: calls.append(1) or [])
    db.save_setting("ai_seeded", "1")
    assert ai_router.seed_from_env() is False and not calls


def test_current_reports_what_really_runs(monkeypatch):
    monkeypatch.setattr(Config, "USE_MOCK_LLM", False)
    monkeypatch.setattr(Config, "HEYROUTE_API_KEY", "hr")
    monkeypatch.setattr(Config, "HEYROUTE_LLM_MODEL", "gpt-5.6-terra")
    monkeypatch.setattr(Config, "VISION_PROVIDER", "heyroute")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_API_KEY", "")
    now = ai_router.current()
    assert (now["text"]["provider"], now["text"]["model"], now["text"]["source"]) == ("HeyRoute", "gpt-5.6-terra", "env")
    assert now["vision"]["provider"] == "HeyRoute" and now["video"]["provider"] == "Not configured"

    monkeypatch.setattr(Config, "HEYROUTE_API_KEY", "")
    monkeypatch.setattr(Config, "OPENAI_API_KEY", "sk-plain-openai")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    monkeypatch.setattr(Config, "AWS_PROFILE", None)
    monkeypatch.setattr(Config, "AWS_ACCESS_KEY_ID", None)
    text = ai_router.current()["text"]
    assert text["provider"] == "OpenAI" and text["fallbacks"] == ["Google Gemini: gemini-3.1-flash-lite"]


# ── Connections, tests and models through the Admin API ──────────────────


def test_only_admins_reach_it(app):
    assert _login(app, admin=False).get("/api/admin/ai").status_code == 403
    assert app.test_client().get("/api/admin/ai").status_code == 401


def test_a_connection_keeps_its_key_secret_and_can_be_tested(app, keys, provider):
    client = _login(app)
    name = f"Gateway {uuid.uuid4().hex[:6]}"
    res = client.post("/api/admin/ai/connections", json={"name": name, "kind": "openai_compatible",
                                                          "base_url": "https://gw.example.com/v1/", "api_key": "sk-bad-9999"})
    conn = res.get_json()["connection"]
    assert conn["key_last4"] == "9999" and conn["key_source"] == "admin" and conn["base_url"] == "https://gw.example.com/v1"
    overview = client.get("/api/admin/ai").get_data(as_text=True)
    assert "sk-bad" not in overview and name in overview

    failed = client.post(f"/api/admin/ai/connections/{conn['id']}/test").get_json()
    assert failed["ok"] is False and "refused the API key" in failed["message"]
    assert failed["connection"]["last_test_ok"] is False

    client.put(f"/api/admin/ai/connections/{conn['id']}", json={"api_key": "sk-good-1234"})
    passed = client.post(f"/api/admin/ai/connections/{conn['id']}/test").get_json()
    assert passed["ok"] and passed["models"] == ["model-a", "model-b"] and provider["url"].endswith("/v1/models")

    actions = [e["action"] for e in db.list_audit_log(20)]
    assert "ai.connection_added" in actions and "ai.connection_changed" in actions
    assert "sk-good" not in json.dumps(db.list_audit_log(20))


def test_keys_cant_be_saved_without_the_encryption_key(app, monkeypatch, public_dns):
    monkeypatch.setattr(Config, "AI_KEYS_ENCRYPTION_KEY", "")
    res = _login(app).post("/api/admin/ai/connections", json={"name": "X", "kind": "openai_compatible",
                                                              "base_url": "https://x.example.com", "api_key": "sk-1"})
    assert res.status_code == 400 and "AI_KEYS_ENCRYPTION_KEY" in res.get_json()["error"]


def test_bad_connections_are_refused(app, keys, public_dns):
    client = _login(app)
    for body, words in (({"name": "", "kind": "openai_compatible"}, "name"),
                        ({"name": "A", "kind": "nope"}, "kind"),
                        ({"name": "A", "kind": "bedrock"}, "AWS credentials"),
                        ({"name": "A", "kind": "openai_compatible", "base_url": "http://plain.example.com", "api_key": "k"}, "https"),
                        ({"name": "A", "kind": "openai_compatible", "base_url": "https://127.0.0.1", "api_key": "k"}, "private"),
                        ({"name": "A", "kind": "openai_compatible", "base_url": "https://ok.example.com"}, "API key")):
        res = client.post("/api/admin/ai/connections", json=body)
        assert res.status_code == 400 and words.lower() in res.get_json()["error"].lower(), body


def test_models_are_added_priced_and_checked(app, keys, provider):
    client = _login(app)
    conn = client.post("/api/admin/ai/connections", json={"name": f"G {uuid.uuid4().hex[:6]}", "kind": "openai_compatible",
                                                          "base_url": "https://gw.example.com/v1", "api_key": "sk-good-1234"}).get_json()["connection"]
    added = client.post("/api/admin/ai/models", json={"connection_id": conn["id"], "models": [
        {"model_id": "model-a", "capabilities": ["text", "vision"]}, {"model_id": "model-b", "capabilities": ["image"]}]}).get_json()
    assert [m["model_id"] for m in added["models"]] == ["model-a", "model-b"]
    again = client.post("/api/admin/ai/models", json={"connection_id": conn["id"], "model_id": "model-a", "capabilities": ["text"]})
    assert again.status_code == 400 and "already listed" in again.get_json()["error"]
    assert client.post("/api/admin/ai/models", json={"connection_id": conn["id"], "model_id": "m", "capabilities": []}).status_code == 400

    model = added["models"][0]
    priced = client.put(f"/api/admin/ai/models/{model['id']}", json={"price_in_per_1k": "0.002", "price_out_per_1k": 0.008}).get_json()
    assert priced["model"]["price_in_per_1k"] == 0.002
    assert client.put(f"/api/admin/ai/models/{model['id']}", json={"price_in_per_1k": -1}).status_code == 400
    assert client.put(f"/api/admin/ai/models/{model['id']}", json={"price_per_image": "abc"}).status_code == 400

    assert client.delete(f"/api/admin/ai/connections/{conn['id']}").get_json()["success"]
    assert not db.list_ai_models(conn["id"])


# ── The router ───────────────────────────────────────────────────────────


def test_resolve_follows_routes_with_fallbacks_and_env_otherwise(keys, monkeypatch):
    from db import AiRoute, Session, engine

    token, last4 = ai_keys.encrypt("sk-route-key")
    conn = db.save_ai_connection(None, name=f"R {uuid.uuid4().hex[:6]}", kind="openai_compatible",
                                 base_url="https://r.example.com/v1", api_key_encrypted=token, key_last4=last4)
    off = db.save_ai_connection(None, name=f"Off {uuid.uuid4().hex[:6]}", kind="openai_compatible",
                                base_url="https://o.example.com/v1", api_key_encrypted=token, key_last4=last4, enabled=False)
    primary = db.save_ai_model(None, connection_id=conn["id"], model_id="big", capabilities=["text"], price_in_per_1k=0.01)
    cheap = db.save_ai_model(None, connection_id=conn["id"], model_id="small", capabilities=["text"])
    unusable = db.save_ai_model(None, connection_id=off["id"], model_id="gone", capabilities=["text"])
    feature = f"text.test_{uuid.uuid4().hex[:6]}"
    capability = f"cap_{uuid.uuid4().hex[:6]}"
    with Session(engine) as session:
        session.add(AiRoute(key=capability, model_id=unusable["id"], fallback_model_ids=json.dumps([primary["id"]])))
        session.add(AiRoute(key=feature, model_id=cheap["id"], fallback_model_ids="[]"))
        session.commit()
    db.bump_ai_routes_version()

    chain = ai_router.resolve(capability)
    assert [c["model"] for c in chain] == ["big"]  # the disabled connection's model is skipped
    assert chain[0]["api_key"] == "sk-route-key" and chain[0]["prices"]["price_in_per_1k"] == 0.01
    assert [c["model"] for c in ai_router.resolve(capability, feature)] == ["small"]  # the feature's own route wins
    assert ai_router.resolve("video") is None or isinstance(ai_router.resolve("video"), list)
    assert ai_router.resolve(f"none_{uuid.uuid4().hex[:4]}") is None  # no route: ENV_FILE as before

    monkeypatch.setattr(Config, "AI_ROUTING_FROM_ENV", True)
    assert ai_router.resolve(capability) is None  # the emergency switch


# ── AI status page ───────────────────────────────────────────────────────


def test_the_status_page_reports_the_real_providers(app, monkeypatch):
    monkeypatch.setattr(Config, "HEYROUTE_API_KEY", "hr")
    monkeypatch.setattr(Config, "HEYROUTE_LLM_MODEL", "gpt-5.6-terra")
    monkeypatch.setattr(Config, "USE_MOCK_LLM", False)
    models = _login(app).get("/api/models/info").get_json()["models"]
    text = next(m for m in models if m["category"] == "llm")
    assert text["provider"] == "HeyRoute" and text["model_name"] == "gpt-5.6-terra"


def test_the_admin_page_has_the_ai_providers_tab(app):
    html = _login(app).get("/admin").get_data(as_text=True)
    for marker in ('data-admin-tab="ai"', 'id="adminPaneAi"', 'id="aiConnectionsTbody"', 'id="aiModelsTbody"',
                   'id="aiConnPanel"', 'id="aiFetchPanel"', "js/admin_ai.js", 'value="ai"'):
        assert marker in html, marker
    assert 'type="password" id="aiConnKey"' in html  # the key field never shows what's typed


# ── Images go to the provider that offers the model (e.g. Meta's Muse Image) ──


def test_an_image_model_is_sent_to_its_own_connection(keys, monkeypatch, tmp_path):
    import base64
    import io

    from PIL import Image

    import services.media_service as ms

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(buf, "PNG")
    png = base64.b64encode(buf.getvalue()).decode()
    sent = []

    class Resp:
        ok, status_code, headers = True, 200, {"Content-Type": "application/json"}
        text = ""

        def json(self):
            return {"data": [{"b64_json": png}]}

    def fake_post(url, headers=None, json=None, data=None, files=None, timeout=None):
        sent.append({"url": url, "auth": headers.get("Authorization"), "body": json or data})
        return Resp()

    monkeypatch.setattr(ms.requests, "post", fake_post)
    monkeypatch.setattr(ms.Config, "HEYROUTE_IMAGE_API_KEY", "heyroute-key")
    monkeypatch.setattr(ms.Config, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(ms, "_image_price", lambda model: 0.01)
    model_id = f"muse-test-{uuid.uuid4().hex[:6]}"
    token, last4 = ai_keys.encrypt("meta-key-123")
    conn = db.save_ai_connection(None, name="Meta", kind="openai_compatible", base_url="https://api.meta.ai/v1",
                                 api_key_encrypted=token, key_last4=last4)
    db.save_ai_model(None, connection_id=conn["id"], model_id=model_id, capabilities=["image"], price_per_image=0.01)
    db.bump_ai_routes_version()
    svc = ms.MediaGenerationService.__new__(ms.MediaGenerationService)
    svc.upload_folder = str(tmp_path)

    result = svc._generate_image_heyroute("a red fox", "instagram", "1024x1024", model=model_id)
    assert sent[-1]["url"] == "https://api.meta.ai/v1/images/generations"
    assert sent[-1]["auth"] == "Bearer meta-key-123" and "stream" not in sent[-1]["body"]  # Meta answers plainly
    assert result["model"] == f"meta/{model_id}" and result["cost"] == 0.01

    # a model no connection offers still goes to HeyRoute, streamed, with its key
    svc._generate_image_heyroute("a red fox", "instagram", "1024x1024", model=f"unlisted-{uuid.uuid4().hex[:6]}")
    assert sent[-1]["url"].startswith(Config.HEYROUTE_BASE_URL) and sent[-1]["auth"] == "Bearer heyroute-key"
    assert sent[-1]["body"]["stream"] is True

    # a turned-off connection is skipped
    db.save_ai_connection(conn["id"], enabled=False)
    db.bump_ai_routes_version()
    assert ai_router.connection_for_model(model_id, "image") is None
    assert ai_router.connection_for_model(model_id, "text") is None
