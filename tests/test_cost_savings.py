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


# ── Live research: readable while it's being written ───────────────────────

RESEARCH = {
    "themes": ["AI becomes a collaborator", "Trust—governance—matters"],
    "research_notes": ['Gartner says "agentic AI" is rising', "On-device models cut latency"],
    "hooks": [{"day": 1, "topic": "One model or many?", "post": "A whole long post..."},
              {"day": 2, "topic": "Agents finish work", "post": "Another long post..."}, "Plain idea"],
}


@pytest.mark.parametrize("double_encoded", [False, True])
def test_partial_research_grows_at_every_cut(double_encoded):
    import json

    from api.routes import _partial_research

    text = json.dumps(RESEARCH)
    if double_encoded:
        text = json.dumps(text)  # the model wrapped its answer in a JSON string
    counts = []
    for cut in range(1, len(text) + 1):
        p = _partial_research(text[:cut])
        counts.append(sum(len(p.get(k, [])) for k in ("themes", "research_notes", "hooks")))
    from itertools import pairwise

    assert all(b >= a for a, b in pairwise(counts))  # items never disappear mid-stream
    final = _partial_research(text)
    assert final["hooks"] == ["Day 1: One model or many?", "Day 2: Agents finish work", "Plain idea"]
    assert final["themes"][1] == "Trust—governance—matters"


def test_research_items_become_plain_text():
    from agents.story_agent import normalize_research

    out = normalize_research({"hooks": RESEARCH["hooks"], "themes": "single theme", "audience": [{"segment": "CFOs"}]})
    assert out["hooks"] == ["Day 1: One model or many?", "Day 2: Agents finish work", "Plain idea"]
    assert out["themes"] == ["single theme"] and out["audience"] == ["CFOs"]


def test_double_encoded_reply_with_a_broken_inner_part_is_repaired():
    from services.llm_service import LLMService

    raw = '"{\\"themes\\": [\\"AI\\", \\"Trust\u0014governance\\"], \\"hooks\\": [\\"a\\"\n ]}"'
    out = LLMService.__new__(LLMService)._robust_parse_json(raw)
    assert isinstance(out, dict) and out["hooks"] == ["a"]


def test_em_dash_written_as_control_character_is_restored():
    from services.llm_service import LLMService

    assert LLMService.__new__(LLMService)._robust_parse_json('{"a": "clear\u0014and practical"}') == {"a": "clear—and practical"}


def test_streamed_answer_reports_progress_and_falls_back(monkeypatch):
    from services.llm_service import LLMService

    class Delta:
        def __init__(self, t):
            self.content = t

    class Chunk:
        def __init__(self, t=None, usage=None):
            self.choices = [type("C", (), {"delta": Delta(t)})()] if t else []
            self.usage = usage

    calls = {"stream": 0, "plain": 0}

    class Completions:
        def create(self, **kw):
            if kw.get("stream"):
                calls["stream"] += 1
                return iter([Chunk('{"themes": '), Chunk('["AI"]}'), Chunk(usage=type("U", (), {"prompt_tokens": 5, "completion_tokens": 3})())])
            calls["plain"] += 1
            return type("R", (), {"choices": [type("Ch", (), {"message": type("M", (), {"content": '{"themes": ["plain"]}'})()})()],
                                  "usage": None})()

    client = type("Cl", (), {"chat": type("Ch", (), {"completions": Completions()})()})()
    llm = LLMService.__new__(LLMService)
    llm.providers = [{"name": "heyroute", "client": client, "model": "m", "reasoning": True}]
    seen = []
    assert llm.generate_json("s", "u", on_partial=seen.append) == {"themes": ["AI"]}
    assert seen[-1] == '{"themes": ["AI"]}' and calls == {"stream": 1, "plain": 0}

    # a provider that can't stream: the same request without streaming
    def broken(**kw):
        if kw.get("stream"):
            raise TypeError("stream not supported")
        return Completions().create(**kw)

    monkeypatch.setattr(client.chat.completions, "create", broken)
    assert llm.generate_json("s", "u", on_partial=seen.append) == {"themes": ["plain"]}


# ── Upload: done at once, AI analysis in the background ────────────────────


def test_concurrent_analyses_of_one_image_make_one_call(vision, monkeypatch):
    import threading
    import time

    from agents.vision_agent import VisionAgent

    agent, calls, tmp = vision

    def slow(self, image_path):
        calls.append(image_path)
        time.sleep(1.0)
        return {"rich_description": "orange bottle"}

    monkeypatch.setattr(VisionAgent, "_analyze_uncached", slow)
    path = _img(tmp / "d.png", "orange")
    results = []
    threads = [threading.Thread(target=lambda: results.append(agent.analyze_image(path))) for _ in range(3)]
    threads[0].start()
    time.sleep(0.2)  # the first one is running (its marker is written)
    for t in threads[1:]:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and results == [{"rich_description": "orange bottle"}] * 3


def test_stale_marker_from_a_crashed_worker_is_ignored(vision):
    import os
    import time

    agent, calls, tmp = vision
    path = _img(tmp / "e.png", "purple")
    pending = agent._cache_path(path) + ".pending"
    os.makedirs(os.path.dirname(pending), exist_ok=True)
    open(pending, "w").close()
    old = time.time() - 600
    os.utime(pending, (old, old))
    agent.analyze_image(path)
    assert len(calls) == 1  # analysed right away, not stuck waiting


def test_upload_returns_before_the_analysis_and_reports_it_later(monkeypatch, tmp_path):
    import os
    import time
    import uuid

    import db
    from agents.vision_agent import VisionAgent
    from api import routes
    from app import create_app

    monkeypatch.setattr(VisionAgent, "_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(VisionAgent, "_analyze_uncached", lambda self, p: time.sleep(1.5) or {"rich_description": "a bottle"})
    app = create_app("development")
    app.config.update(TESTING=True)
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:8]}@example.com", "h")
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    from io import BytesIO

    buf = BytesIO()
    Image.new("RGB", (32, 32), "orange").save(buf, "PNG")
    buf.seek(0)
    t = time.time()
    r = client.post("/api/upload", data={"image": (buf, "bottle.png")}, content_type="multipart/form-data").get_json()
    assert time.time() - t < 1.0, "the upload waited for the AI analysis"
    assert r["success"] and r["analysis_pending"] and r["analysis"] is None
    assert client.get("/api/upload/analysis", query_string={"image_id": r["image_id"]}).get_json()["ready"] is False
    for _ in range(40):
        a = client.get("/api/upload/analysis", query_string={"image_id": r["image_id"]}).get_json()
        if a["ready"]:
            break
        time.sleep(0.1)
    assert a["ready"] and a["analysis"] == {"rich_description": "a bottle"}
    assert client.get("/api/upload/analysis", query_string={"image_id": "../app.py"}).status_code == 404
    os.remove(r["filepath"])
    assert routes.vision_agent is not None
