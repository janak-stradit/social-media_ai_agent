"""Post languages (services/language_service.py): captions and hashtags in
the language picked in Studio Chat. The LLM is faked."""

import uuid

import pytest

from services import language_service


def test_english_adds_nothing_and_unknown_codes_mean_english():
    assert language_service.instruction("en") == "" and language_service.instruction(None) == ""
    assert language_service.normalize("klingon") == "en"


def test_instructions_name_the_language_and_keep_names_as_they_are():
    text = language_service.instruction("hi", "caption")
    assert "Hindi, in Devanagari script" in text and "brand, product and company names" in text
    tags = language_service.instruction("hinglish", "hashtags")
    assert "Latin alphabet" in tags and "half of the hashtags" in tags


def test_menu_lists_english_first():
    names = language_service.options()
    assert names[0] == {"code": "en", "name": "English"} and any(o["code"] == "ta" for o in names)


def test_caption_and_hashtag_agents_pass_the_language_to_the_model(monkeypatch):
    from agents.caption_agent import CaptionAgent
    from agents.hashtag_agent import HashtagAgent

    prompts = []

    def fake_json(self, system_prompt, user_prompt, **kw):
        prompts.append(system_prompt)
        return (
            {"primary_caption": "नमस्ते", "linkedin": {"hashtags": ["#निवेश"]}, "facebook": {"hashtags": ["#SIP"]}},
            {},
        )

    from services.llm_service import LLMService

    monkeypatch.setattr(LLMService, "generate_json", fake_json)
    monkeypatch.setattr(LLMService, "generate", lambda self, s, u, **kw: (prompts.append(s) or "नमस्ते", {}))
    CaptionAgent().generate_caption("linkedin", {"themes": ["SIP"]}, language="hi")
    CaptionAgent().refine_caption("linkedin", "नमस्ते", "shorter", language="hi")
    agent = HashtagAgent()
    monkeypatch.setattr(agent.memory, "get_trending_hashtags", lambda **k: [])
    agent.generate_hashtags_batch(["linkedin", "facebook"], {"themes": ["SIP"]}, language="hi")
    assert sum("LANGUAGE:" in p and "Hindi" in p for p in prompts) == 3


def test_generate_route_writes_in_the_chosen_language(monkeypatch):
    import db
    from api import routes
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:8]}@example.com", "h")
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    seen = {}
    monkeypatch.setattr(routes.memory_service, "retrieve_context", lambda *a, **k: [])
    monkeypatch.setattr(routes.memory_service, "store_campaign_run", lambda *a, **k: None)
    monkeypatch.setattr(routes.story_agent, "analyze", lambda *a, **k: ({"themes": ["Investing"], "emotions": []}, {}))

    def caption(platform, *a, **k):
        seen["caption"] = k.get("language")
        return {"primary_caption": "SIP ki shuruaat aaj se.", "usage": {}}

    def hashtags(platforms, *a, **k):
        seen["tags"] = k.get("language")
        return {p: {"hashtags": ["#SIP"], "_usage": {}} for p in platforms}

    monkeypatch.setattr(routes.caption_agent, "generate_caption", caption)
    monkeypatch.setattr(routes.hashtag_agent, "generate_hashtags_batch", hashtags)
    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [])

    res = client.post(
        "/api/generate",
        json={
            "story": "SIP for beginners",
            "platforms": ["linkedin"],
            "selected_outputs": ["text"],
            "language": "hinglish",
        },
    ).get_json()
    assert seen == {"caption": "hinglish", "tags": "hinglish"}
    assert res["success"] is not False


@pytest.mark.parametrize("code,expected", [("ta", "Tamil"), ("en", "")])
def test_refine_prompt_keeps_the_language(code, expected):
    from api.routes import _refine_language_line

    line = _refine_language_line(code)
    assert (expected in line) if expected else line == ""
