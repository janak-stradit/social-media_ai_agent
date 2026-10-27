"""The post pipeline as a function (services/content_pipeline.py): the same
steps Studio Chat's Generate runs, callable without a web request - which
Autopilot needs. The AI is faked."""

import uuid

import pytest

import db
from api import routes
from services import content_pipeline


@pytest.fixture
def fakes(monkeypatch):
    seen = {}
    monkeypatch.setattr(routes.memory_service, "retrieve_context", lambda *a, **k: [])
    monkeypatch.setattr(routes.memory_service, "store_campaign_run", lambda *a, **k: None)
    monkeypatch.setattr(routes.story_agent, "analyze", lambda *a, **k: ({"themes": ["Delivery"], "emotions": []}, {}))

    def caption(platform, *a, **k):
        seen.setdefault("platforms", []).append(platform)
        seen["language"] = k.get("language")
        return {"primary_caption": f"Faster deliveries on {platform}.", "usage": {"total_tokens": 10, "cost_usd": 0.001}}

    monkeypatch.setattr(routes.caption_agent, "generate_caption", caption)

    def rewrite(platform, original_caption, reviewer_feedback, **k):  # the quality check's one rewrite: no real AI
        seen.setdefault("rewrites", []).append(platform)
        return original_caption, {}

    monkeypatch.setattr(routes.caption_agent, "refine_caption", rewrite)
    monkeypatch.setattr(routes.hashtag_agent, "generate_hashtags_batch",
                        lambda platforms, *a, **k: {p: {"hashtags": ["#Logistics"], "_usage": {}} for p in platforms})
    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [])
    return seen


def _user():
    return db.create_user("Asha", f"{uuid.uuid4().hex[:8]}@example.com", "h")


def test_a_post_is_made_without_a_web_request(fakes):
    user = _user()
    result = content_pipeline.generate_post(user["id"], {"story": "Same-day delivery in Pune",
                                                         "platforms": ["linkedin", "instagram"], "language": "hi"})
    assert result["success"] and set(result["content"]) == {"linkedin", "instagram"}
    assert result["content"]["linkedin"]["caption"]["primary_caption"] == "Faster deliveries on linkedin."
    assert result["content"]["instagram"]["hashtags"]["hashtags"] == ["#Logistics"]
    assert sorted(fakes["platforms"]) == ["instagram", "linkedin"] and fakes["language"] == "hi"
    run = db.get_run_by_id(result["run_id"], user_id=user["id"])
    assert run and run["story"] == "Same-day delivery in Pune" and result["conversation_id"]


def test_a_reached_credit_limit_always_stops_the_post(fakes, monkeypatch):
    monkeypatch.setattr(routes, "get_user_usage_stats",
                        lambda uid: {"credit_capped": True, "remaining_credits": 0.0, "credit_limit": 2.0, "currency": "USD"})
    with pytest.raises(content_pipeline.PostBlocked) as blocked:
        content_pipeline.generate_post(_user()["id"], {"story": "x", "platforms": ["linkedin"]})
    assert blocked.value.status == 402 and blocked.value.payload["credit_limit_exceeded"] is True
    assert "platforms" not in fakes  # nothing was written


def test_a_failing_credit_check_doesnt_stop_the_post(fakes, monkeypatch):
    def broken(uid):
        raise RuntimeError("stats unavailable")

    monkeypatch.setattr(routes, "get_user_usage_stats", broken)
    assert content_pipeline.generate_post(_user()["id"], {"story": "x", "platforms": ["linkedin"]})["success"]


def test_no_posts_left_in_the_plan_stops_the_post(fakes, monkeypatch):
    monkeypatch.setattr(routes, "_posts_blocked_payload",
                        lambda uid: {"success": False, "code": "plan_posts_exhausted", "error": "No posts left."})
    with pytest.raises(content_pipeline.PostBlocked) as blocked:
        content_pipeline.generate_post(_user()["id"], {"story": "x", "platforms": ["linkedin"]})
    assert blocked.value.status == 403 and blocked.value.payload["code"] == "plan_posts_exhausted"


def test_the_endpoint_returns_what_the_pipeline_returns(fakes, monkeypatch):
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    user = _user()
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    body = client.post("/api/generate", json={"story": "Same-day delivery", "platforms": ["linkedin"]}).get_json()
    assert body["success"] and body["content"]["linkedin"]["caption"]["primary_caption"] == "Faster deliveries on linkedin."
    assert client.post("/api/generate", json={}).status_code == 400

    monkeypatch.setattr(routes, "get_user_usage_stats",
                        lambda uid: {"credit_capped": True, "remaining_credits": 0.0, "credit_limit": 2.0, "currency": "USD"})
    blocked = client.post("/api/generate", json={"story": "x", "platforms": ["linkedin"]})
    assert blocked.status_code == 402 and blocked.get_json()["credit_limit_exceeded"] is True
