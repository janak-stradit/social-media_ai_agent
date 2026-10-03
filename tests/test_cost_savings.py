"""Fewer AI calls for the same output: hashtags and compliance checks are
batched per post, and an image is analysed once however often it's used.
(Each AI call carries ~4k tokens of fixed provider overhead.)"""

import pytest
from PIL import Image


class FakeLLM:
    """Records calls; answers from a queue."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def generate_json(self, system_prompt, user_prompt, **kw):
        self.calls.append({"system": system_prompt, "user": user_prompt, **kw})
        return self.answers.pop(0), {"total_tokens": 100, "cost_usd": 0.001, "input_tokens": 90, "output_tokens": 10}


# ── Hashtags: one call per post ─────────────────────────────────────────────


@pytest.fixture
def hashtag_agent(monkeypatch):
    from agents.hashtag_agent import HashtagAgent

    agent = HashtagAgent.__new__(HashtagAgent)  # no real LLM / memory clients
    agent.memory = type("M", (), {"get_trending_hashtags": lambda self, category=None: [], "store_content": lambda self, *a, **k: None})()
    return agent


STORY = {"themes": ["Logistics"], "emotions": ["Confident"]}


def test_hashtags_for_all_platforms_in_one_call(hashtag_agent):
    hashtag_agent.llm = FakeLLM({"LinkedIn": {"hashtags": ["#Freight", "#UAE"], "engagement_prediction": 7},
                                 "instagram": {"hashtags": ["#Trucks", "#Dubai", "#Logistics"]}})
    out = hashtag_agent.generate_hashtags_batch(["linkedin", "instagram"], STORY)
    assert len(hashtag_agent.llm.calls) == 1
    call = hashtag_agent.llm.calls[0]
    assert "linkedin: exactly 5 hashtags" in call["system"] and "instagram: exactly 30 hashtags" in call["system"]
    assert call["reasoning_effort"] == "minimal"
    assert out["linkedin"]["hashtags"] == ["#Freight", "#UAE"] and out["instagram"]["hashtags"][0] == "#Trucks"
    assert out["linkedin"]["_usage"]["total_tokens"] == 100 and out["instagram"]["_usage"] == {}  # counted once


def test_platform_missing_from_batch_gets_its_own_call(hashtag_agent):
    hashtag_agent.llm = FakeLLM({"linkedin": {"hashtags": ["#Freight"]}}, {"hashtags": ["#Own"]})
    out = hashtag_agent.generate_hashtags_batch(["linkedin", "facebook"], STORY)
    assert len(hashtag_agent.llm.calls) == 2  # batch + fallback for facebook
    assert out["facebook"]["hashtags"] == ["#Own"]
    assert out["linkedin"]["_usage"]["total_tokens"] == 100 and out["facebook"]["_usage"]["total_tokens"] == 100


def test_single_platform_uses_the_normal_call(hashtag_agent):
    hashtag_agent.llm = FakeLLM({"hashtags": ["#One"]})
    out = hashtag_agent.generate_hashtags_batch(["linkedin"], STORY)
    assert out["linkedin"]["hashtags"] == ["#One"] and len(hashtag_agent.llm.calls) == 1


# ── Compliance: one review per post ─────────────────────────────────────────

RULES = [
    {"id": "R1", "framework": "FTC", "regions": ["US"], "severity": "high", "content_rules": ["No guaranteed results."]},
    {"id": "R2", "framework": "SEBI", "regions": ["India"], "severity": "high", "content_rules": ["Investment disclaimer."],
     "required_disclaimer": "Investments are subject to market risks."},
]


def test_compliance_for_all_platforms_in_one_call():
    from services.compliance_service import check_captions

    llm = FakeLLM({
        "linkedin": {"flags": [{"rule_id": "R1", "issue": '"guaranteed"', "fix": "removed", "auto_fixed": True},
                               {"rule_id": "INVENTED", "issue": "x"}],
                     "revised_caption": "Faster deliveries.", "disclaimer_rule_ids": []},
        "Instagram": {"flags": [], "revised_caption": "Grow your savings.", "disclaimer_rule_ids": ["R2"]},
    })
    results, usage = check_captions({"linkedin": "Guaranteed faster deliveries.", "instagram": "Grow your savings."}, RULES, llm)
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["user"].startswith("RULES:") and "=== PLATFORM: instagram ===" in call["user"]
    assert "SEVERAL captions" in call["system"]
    assert results["linkedin"]["caption"] == "Faster deliveries."
    assert [f["rule_id"] for f in results["linkedin"]["flags"]] == ["R1"]  # invented rule id dropped
    assert results["instagram"]["caption"].endswith("Investments are subject to market risks.")
    assert usage["total_tokens"] == 100


def test_compliance_platform_missing_from_batch_gets_its_own_check():
    from services.compliance_service import check_captions

    llm = FakeLLM({"linkedin": {"flags": [], "revised_caption": "OK"}},
                  {"flags": [], "revised_caption": "Checked alone", "disclaimer_rule_ids": []})
    results, usage = check_captions({"linkedin": "OK", "facebook": "Something"}, RULES, llm)
    assert len(llm.calls) == 2 and results["facebook"]["caption"] == "Checked alone"
    assert usage["total_tokens"] == 200


def test_no_rules_means_no_call():
    from services.compliance_service import check_captions

    llm = FakeLLM()
    assert check_captions({"linkedin": "Hi"}, [], llm) == ({"linkedin": None}, {}) and llm.calls == []


# ── Image analysis: once per image ──────────────────────────────────────────


@pytest.fixture
def vision(monkeypatch, tmp_path):
    from agents.vision_agent import VisionAgent

    agent = VisionAgent.__new__(VisionAgent)
    agent.hf = type("HF", (), {"vision_provider": "heyroute"})()
    monkeypatch.setattr(VisionAgent, "_CACHE_DIR", str(tmp_path / "cache"))
    calls = []

    def fake_uncached(self, image_path):
        calls.append(image_path)
        return {"rich_description": f"photo {len(calls)}", "colors": ["orange"]}

    monkeypatch.setattr(VisionAgent, "_analyze_uncached", fake_uncached)
    return agent, calls, tmp_path


def _img(path, color):
    Image.new("RGB", (16, 16), color).save(path)
    return str(path)


def test_same_image_is_analysed_once(vision):
    agent, calls, tmp = vision
    a = _img(tmp / "a.png", "orange")
    copy = _img(tmp / "a-again.png", "orange")  # same content, another name (e.g. re-upload)
    first = agent.analyze_image(a)
    assert agent.analyze_image(a) == first and agent.analyze_image(copy) == first
    assert len(calls) == 1
    agent.analyze_image(_img(tmp / "b.png", "blue"))  # a different image is analysed
    assert len(calls) == 2


def test_failed_analysis_is_not_cached(vision, monkeypatch):
    from agents.vision_agent import VisionAgent

    agent, calls, tmp = vision
    monkeypatch.setattr(VisionAgent, "_analyze_uncached", lambda self, p: calls.append(p) or {"error": "unavailable"})
    path = _img(tmp / "c.png", "green")
    agent.analyze_image(path)
    agent.analyze_image(path)
    assert len(calls) == 2  # tried again, not stuck on the failure


# ── Regenerate keeps the image ─────────────────────────────────────────────


def test_regenerate_keeps_the_versions_image(monkeypatch):
    import uuid

    import db
    from api import routes
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:8]}@example.com", "h")
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]

    captions = iter(["First caption about live truck tracking.", "A rewritten caption about live truck tracking."])
    monkeypatch.setattr(routes.memory_service, "retrieve_context", lambda *a, **k: [])
    monkeypatch.setattr(routes.memory_service, "store_campaign_run", lambda *a, **k: None)
    monkeypatch.setattr(routes.story_agent, "analyze", lambda *a, **k: ({"themes": ["Logistics"], "emotions": []}, {}))
    monkeypatch.setattr(routes.caption_agent, "generate_caption",
                        lambda platform, *a, **k: {"primary_caption": next(captions), "usage": {}})
    monkeypatch.setattr(routes.hashtag_agent, "generate_hashtags_batch",
                        lambda platforms, *a, **k: {p: {"hashtags": ["#Freight"], "_usage": {}} for p in platforms})
    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [])
    monkeypatch.setattr(routes.media_service, "generate_image", lambda *a, **k: pytest.fail("no image should be bought"))

    body = {"story": "Announce live truck tracking", "platforms": ["linkedin"], "tone": "Professional", "selected_outputs": ["text", "image"]}
    first = client.post("/api/generate", json=body).get_json()
    image = {"url": "/static/uploads/truck.jpg", "clean_url": "/static/uploads/truck_clean.jpg", "asset_id": 7}
    db.append_run_media(first["run_id"], "linkedin", "image", image, user_id=user["id"])

    again = client.post("/api/generate", json={**body, "keep_media_from_run_id": first["run_id"],
                                               "conversation_id": first["conversation_id"]}).get_json()
    assert again["content"]["linkedin"]["caption"]["primary_caption"].startswith("A rewritten")
    assert again["content"]["linkedin"]["media"]["image"]["url"] == "/static/uploads/truck.jpg"
    saved = db.get_run_by_id(again["run_id"], user["id"])
    assert saved["content"]["linkedin"]["media"]["image"]["asset_id"] == 7  # the new version keeps it too

    # someone else's run is never a source of media
    other = db.create_user("Sam", f"{uuid.uuid4().hex[:8]}@example.com", "h")
    other_client = app.test_client()
    with other_client.session_transaction() as s:
        s["user_id"] = other["id"]
    captions = iter(["Sam's caption."])
    stolen = other_client.post("/api/generate", json={**body, "keep_media_from_run_id": first["run_id"]}).get_json()
    assert "media" not in stolen["content"]["linkedin"] or not stolen["content"]["linkedin"]["media"].get("image")


# ── Research step: one AI call, structured result ──────────────────────────


@pytest.mark.parametrize("wrap", [0, 1, 2])
def test_double_encoded_json_reply_becomes_an_object(wrap):
    import json

    from services.llm_service import LLMService

    obj = {"themes": ["AI"], "hooks": ['a "quoted" hook']}
    raw = json.dumps(obj)
    for _ in range(wrap):
        raw = json.dumps(raw)  # the model wrapped its answer in a JSON string
    assert LLMService.__new__(LLMService)._robust_parse_json(raw) == obj


def test_research_makes_a_single_ai_call(monkeypatch):
    import uuid

    import db
    from api import routes
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:8]}@example.com", "h")
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    monkeypatch.setattr(routes.memory_service, "retrieve_context", lambda *a, **k: [])
    monkeypatch.setattr(routes.story_agent, "analyze", lambda *a, **k: {"themes": ["AI"], "hooks": ["Day 1"]})
    monkeypatch.setattr(routes.story_agent, "extract_key_points", lambda *a, **k: pytest.fail("unused second call"))
    body = client.post("/api/analyze-story", json={"story": "10 days of LinkedIn posts about AI"}).get_json()
    assert body["success"] and body["analysis"]["hooks"] == ["Day 1"] and body["key_points"] == []
