"""Studio Chat "Ideas for you" (services/trend_service.py): trends are looked
up once per industry, turned into briefs per user, and cached. The news
aggregator, Google and the LLM are faked - nothing is called or billed."""

import json
import uuid

import pytest
from sqlalchemy.orm import Session

import db
from services import trend_service

TOPICS = [
    {"topic": "SEBI tightens F&O rules", "summary": "New lot sizes from November.", "why_now": "Takes effect soon.",
     "source_title": "Mint", "source_url": "https://example.com/sebi"},
    {"topic": "Record SIP inflows", "summary": "Monthly SIPs crossed a new high.", "why_now": "Data out this week.",
     "source_title": "ET", "source_url": "https://example.com/sip"},
    {"topic": "Gold ETFs see inflows", "summary": "Investors added to gold funds.", "why_now": "Festive buying.",
     "source_title": "Mint", "source_url": "https://example.com/gold"},
]


def _idea(**extra):
    return {"title": "Explain the new F&O rules", "why_now": "Rules change soon.", "summary": "A plain explainer.",
            "format": "image", "platform": "instagram", "prompt": "Create a post about the new F&O rules.", **extra}


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def user():
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    with Session(db.engine) as session:
        session.add(db.UserBrandProfile(
            user_id=user["id"], website="https://example.com", company_name="Acme Invest",
            industry="Investment education", industry_category="investment_wealth",
            compliance_regions=json.dumps(["India"]),
        ))
        session.commit()
    return user


@pytest.fixture
def fakes(monkeypatch):
    """Counts the lookups and LLM calls; the market key is unique per test so caches don't leak."""
    calls = {"aggregator": 0, "search": 0, "news": 0, "llm": 0, "polish": [], "use_llm": []}
    region = f"India-{uuid.uuid4().hex[:6]}"
    monkeypatch.setitem(trend_service.REGIONS, region, ("India", "hl=en-IN"))
    monkeypatch.setattr(trend_service, "profile_market", lambda profile: ("investment_wealth", "Investment education", region))

    def search(label, region):
        calls["search"] += 1
        return TOPICS

    def llm(self, system_prompt, user_prompt, **kw):
        calls["llm"] += 1
        calls["prompt"] = user_prompt
        return ({"trending": [_idea(trend_index=1), _idea(trend_index=1, title="Same topic again"), _idea(trend_index=9)],
                 "playbook": [_idea(post_type="Explainer", format="hologram", platform="tiktok")],
                 "dates": []}, {"total_tokens": 10})

    def aggregator(category, label, region, use_llm=True):
        calls["aggregator"] += 1
        calls["use_llm"].append(use_llm)
        return TOPICS

    monkeypatch.setattr(trend_service, "_trends_from_news_aggregator", aggregator)
    # the background summaries would save new trends mid-test: just record the request
    monkeypatch.setattr(trend_service, "_polish_in_background", lambda key, *a: calls["polish"].append(key))
    monkeypatch.setattr(trend_service, "_gemini_paused_until", None)
    monkeypatch.setattr(trend_service, "_trends_from_google_search", search)
    monkeypatch.setattr(trend_service, "_trends_from_google_news",
                        lambda c, l, r: calls.__setitem__("news", calls["news"] + 1) or TOPICS[:1])
    from services.llm_service import LLMService

    monkeypatch.setattr(LLMService, "generate_json", llm)
    return calls


def test_feed_links_each_trending_idea_to_its_source(user, fakes):
    feed, _ = trend_service.generate_idea_feed(user["id"])

    trending = feed["groups"]["trending"]
    assert len(trending) == 1  # one idea per topic; an unknown topic number is dropped
    assert trending[0]["topic"] == "Record SIP inflows" and trending[0]["source_url"] == "https://example.com/sip"
    assert feed["trend_source"] == "news" and "Record SIP inflows" in fakes["prompt"]
    # values the page can't show fall back to safe ones
    assert feed["groups"]["playbook"][0]["format"] == "text" and feed["groups"]["playbook"][0]["platform"] == "linkedin"


def test_feed_and_trends_are_cached(user, fakes):
    first, _ = trend_service.generate_idea_feed(user["id"])
    second, _ = trend_service.generate_idea_feed(user["id"])
    assert first == second and fakes["llm"] == 1 and fakes["aggregator"] == 1 and fakes["search"] == 0

    trend_service.generate_idea_feed(user["id"], force=True)  # refresh: new ideas, same day's trends
    assert fakes["llm"] == 2 and fakes["aggregator"] == 1


def test_news_headlines_are_used_when_every_other_source_fails(user, fakes, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(trend_service, "_trends_from_news_aggregator", broken)
    monkeypatch.setattr(trend_service, "_trends_from_google_search", broken)

    feed, _ = trend_service.generate_idea_feed(user["id"])

    assert feed["trend_source"] == "google_news" and fakes["news"] == 1


def _key():
    return f"retail_ecommerce|test-{uuid.uuid4().hex[:6]}"


def test_a_visitor_gets_headlines_now_and_summaries_in_the_background(fakes):
    key = _key()
    category, _, region = key.partition("|")
    trends = trend_service.get_trends(category, "Retail", region)
    assert trends["source"] == "news" and fakes["use_llm"] == [False] and fakes["polish"] == [key]

    trend_service.get_trends(category, "Retail", region, force=True)  # the scheduler writes summaries itself
    assert fakes["use_llm"] == [False, True] and fakes["polish"] == [key]


def test_thin_aggregator_results_try_gemini_then_keep_the_best(fakes, monkeypatch):
    monkeypatch.setattr(trend_service, "_trends_from_news_aggregator", lambda *a, **kw: TOPICS[:2])
    category, _, region = _key().partition("|")
    assert trend_service.get_trends(category, "Retail", region)["source"] == "google_search"

    def no_search(label, region):
        raise RuntimeError("no grounding on this plan")

    monkeypatch.setattr(trend_service, "_trends_from_google_search", no_search)
    category, _, region = _key().partition("|")
    trends = trend_service.get_trends(category, "Retail", region)
    # 2 ranked topics beat Google News' single headline
    assert trends["source"] == "news" and len(trends["topics"]) == 2


def test_gemini_is_paused_after_a_quota_error(fakes, monkeypatch):
    monkeypatch.setattr(trend_service, "_trends_from_news_aggregator", lambda *a, **kw: [])

    def quota(label, region):
        fakes["search"] += 1
        raise RuntimeError("429 RESOURCE_EXHAUSTED. You exceeded your current quota")

    monkeypatch.setattr(trend_service, "_trends_from_google_search", quota)
    for _ in range(3):
        category, _, region = _key().partition("|")
        assert trend_service.get_trends(category, "Retail", region)["source"] == "google_news"
    assert fakes["search"] == 1  # not called again until the quota resets
    assert trend_service._gemini_paused_until.hour == 8


def test_endpoint_returns_the_feed_and_nothing_without_a_profile(app, user, fakes):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    assert client.get("/api/ideas/feed").get_json()["feed"]["groups"]["trending"]

    stranger = db.create_user("Sam", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    with client.session_transaction() as s:
        s["user_id"] = stranger["id"]
    assert client.get("/api/ideas/feed").get_json()["feed"] is None
