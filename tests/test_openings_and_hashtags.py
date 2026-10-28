"""Two small additions to writing a post:

- "Other openings" (services/hook_service.py): different first lines for a
  caption, only when asked, saved only through the normal hand edit;
- hashtags (services/hashtag_service.py, db.proven_hashtags): spam tags and
  repeats removed, and tags that did well on the brand's own posts offered to
  the hashtag agent.

The AI is faked."""

import json
import uuid

import pytest
from sqlalchemy.orm import Session

import db
from agents.hashtag_agent import HashtagAgent
from api import routes
from services import content_pipeline, hashtag_service, hook_service


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _user():
    return db.create_user("Meera", f"{uuid.uuid4().hex[:10]}@example.com", "h")


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


CAPTION = "Menopause can change how you sleep.\n\nOur gynaecologist shares 3 simple habits that help.\nJoin the live session on Sunday."


class FakeLLM:
    def __init__(self, openings):
        self.openings = openings
        self.prompts = []

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        self.prompts.append((system_prompt, user_prompt, kwargs))
        return {"openings": self.openings}, {"total_tokens": 120, "cost_usd": 0.0004}


# ── Other openings ──────────────────────────────────────────────────────────


def test_openings_are_cleaned_and_never_repeat_the_current_one():
    llm = FakeLLM([
        "Menopause can change how you sleep.",  # the current one
        '"Can’t sleep since your 40s?"',  # quotes go
        "Can’t sleep since your 40s?",  # a repeat after cleaning
        "#Sleep #Menopause",  # hashtags aren't an opening
        "3 habits that help you   sleep through the night.",
        "x" * 300,  # too long to be a first line
        "Better sleep in midlife starts with small habits.",
        "One more that won't be needed.",
    ])
    options, usage = hook_service.other_openings(CAPTION, "instagram", llm=llm)
    assert options == ["Can’t sleep since your 40s?", "3 habits that help you sleep through the night.",
                       "Better sleep in midlife starts with small habits."]
    assert usage["cost_usd"] == 0.0004
    system_prompt, user_prompt, kwargs = llm.prompts[0]
    assert "Never invent" in system_prompt and "Instagram" in system_prompt and kwargs["reasoning_effort"] == "minimal"
    assert user_prompt.startswith("Current first line:\nMenopause can change how you sleep.")


def test_no_text_or_no_usable_answer_is_a_clear_error():
    with pytest.raises(hook_service.OpeningError, match="Write the post first"):
        hook_service.other_openings("  \n ", "linkedin", llm=FakeLLM(["x"]))
    with pytest.raises(hook_service.OpeningError, match="try again"):
        hook_service.other_openings(CAPTION, "linkedin", llm=FakeLLM(["Menopause can change how you sleep."]))


def test_the_opening_is_the_first_line_with_text():
    assert hook_service.opening_of("\n\n  Hello there.  \nRest") == "Hello there."
    assert hook_service.opening_of("") == ""


def _run(user):
    content = {"linkedin": {"caption": {"primary_caption": CAPTION}, "hashtags": {"hashtags": ["#Sleep"]}}}
    return db.save_run(story="Sleep post", tone="Auto", platforms=["linkedin"], content=content,
                       user_id=user["id"], tokens_used=900, cost_usd=0.01)


def test_the_endpoint_suggests_without_saving_and_charges_the_run(app, monkeypatch):
    user = _user()
    run_id = _run(user)
    calls = []

    def fake(caption, platform, llm=None):
        calls.append((caption, platform))
        return ["Can’t sleep since your 40s?", "3 habits for better sleep."], {"cost_usd": 0.0004}

    monkeypatch.setattr(hook_service, "other_openings", fake)
    client = _login(app, user)
    r = client.post(f"/api/history/{run_id}/openings", json={"caption": CAPTION, "platform": "LinkedIn"})
    body = r.get_json()
    assert r.status_code == 200 and body["options"] == ["Can’t sleep since your 40s?", "3 habits for better sleep."]
    assert body["opening"] == "Menopause can change how you sleep." and calls == [(CAPTION, "linkedin")]
    saved = db.get_run_by_id(run_id)
    assert saved["content"]["linkedin"]["caption"]["primary_caption"] == CAPTION  # nothing saved
    assert "edited" not in saved["content"]["linkedin"]
    assert saved["cost_usd"] == pytest.approx(0.0104)  # the call counts towards credit, on the post's run


def test_the_endpoint_refuses_other_users_posts_and_reports_errors(app, monkeypatch):
    owner, other = _user(), _user()
    run_id = _run(owner)
    client = _login(app, other)
    assert client.post(f"/api/history/{run_id}/openings", json={"caption": CAPTION}).status_code == 404

    client = _login(app, owner)
    assert client.post(f"/api/history/{run_id}/openings", json={"caption": ""}).status_code == 400

    def broken(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(hook_service, "other_openings", broken)
    r = client.post(f"/api/history/{run_id}/openings", json={"caption": CAPTION})
    assert r.status_code == 502 and "try again" in r.get_json()["error"]
    assert db.get_run_by_id(run_id)["cost_usd"] == pytest.approx(0.01)  # a failed call costs nothing

    monkeypatch.setattr(routes, "get_user_usage_stats",
                        lambda uid: {"credit_capped": True, "remaining_credits": 0.0, "credit_limit": 2.0, "currency": "USD"})
    assert client.post(f"/api/history/{run_id}/openings", json={"caption": CAPTION}).status_code == 402


# ── Hashtags: tidy ──────────────────────────────────────────────────────────


def test_spam_repeats_and_broken_tags_are_removed_in_order():
    tags = ["#Menopause", "#follow4follow", "#WomensHealth", "#menopause", "#2026", "#fyp", "#Sleep", "#" + "a" * 70,
            "#नींद", "  ", "#LikeForLike"]
    assert hashtag_service.tidy(tags) == ["#Menopause", "#WomensHealth", "#Sleep", "#नींद"]


def test_tidy_never_empties_a_set():
    assert hashtag_service.tidy(["#follow", "#like4like"]) == ["#follow", "#like4like"]
    assert hashtag_service.tidy([]) == [] and hashtag_service.tidy(None) == []


# ── Hashtags: what did well on the brand's own posts ───────────────────────


def _published(user, platform, engagement, hashtags, source="scheduled"):
    item = {"platform": platform, "caption": "A post", "hashtags": hashtags}
    if source == "scheduled":
        from datetime import datetime, timedelta, timezone

        source_id = db.create_scheduled_post(user["id"], [platform], datetime.now(timezone.utc) - timedelta(days=1),
                                             {"v": 2, "items": [item]})["id"]
        with Session(db.engine) as session:  # it went out (so other tests' publishing runs leave it alone)
            session.query(db.ScheduledPost).filter(db.ScheduledPost.id == source_id).update({"status": "published"})
            session.commit()
    else:
        source_id = db.create_approval_request(user["id"], uuid.uuid4().hex, platform, "post", caption="A post",
                                               items=[item])["id"]
    post_id = f"p_{uuid.uuid4().hex[:8]}"
    db.register_published_post(user["id"], platform, post_id, None, "A post", source, source_id)
    with Session(db.engine) as session:
        session.query(db.PublishedPost).filter(db.PublishedPost.platform_post_id == post_id).update({"engagement": engagement})
        session.commit()


def test_proven_hashtags_rank_tags_by_the_engagement_of_their_posts():
    user, other = _user(), _user()
    _published(user, "instagram", 50, ["#Menopause", "#Sleep"])
    _published(user, "instagram", 120, ["#menopause", "#Perimenopause"], source="approval")
    _published(user, "instagram", 0, ["#NoOneSawThis"])  # no engagement: not proven
    _published(user, "linkedin", 30, ["#WomensHealth"])
    _published(other, "instagram", 900, ["#SomeoneElse"])  # another user's posts never count
    proven = db.proven_hashtags(user["id"])
    # 170, 120, 50 - a tag is spelled as on the best post that used it
    assert proven["instagram"] == ["#menopause", "#Perimenopause", "#Sleep"]
    assert proven["linkedin"] == ["#WomensHealth"]
    assert db.proven_hashtags(_user()["id"]) == {}


def test_the_hashtag_prompt_mentions_proven_tags_only_when_there_are_some():
    story = {"themes": ["Sleep"], "emotions": []}
    plain = HashtagAgent._user_prompt(story, None, [], None, None)
    assert HashtagAgent._user_prompt(story, None, [], None, None, {}) == plain
    assert HashtagAgent._user_prompt(story, None, [], None, None, {"instagram": []}) == plain
    with_tags = HashtagAgent._user_prompt(story, None, [], None, None, {"instagram": ["#Menopause", "#Sleep"]})
    assert with_tags.startswith(plain) and "instagram: #Menopause #Sleep" in with_tags and "never add one that doesn't" in with_tags


@pytest.fixture
def pipeline(monkeypatch):
    seen = {}
    monkeypatch.setattr(routes.memory_service, "retrieve_context", lambda *a, **k: [])
    monkeypatch.setattr(routes.memory_service, "store_campaign_run", lambda *a, **k: None)
    monkeypatch.setattr(routes.story_agent, "analyze", lambda *a, **k: ({"themes": ["Sleep"], "emotions": []}, {}))
    monkeypatch.setattr(routes.caption_agent, "generate_caption",
                        lambda platform, *a, **k: {"primary_caption": "Sleep better in midlife.", "usage": {}})
    monkeypatch.setattr(routes.caption_agent, "refine_caption", lambda platform, original_caption, *a, **k: (original_caption, {}))

    def hashtags(platforms, *a, **k):
        seen["proven"] = k.get("proven")
        return {p: {"hashtags": ["#Sleep", "#followforfollow", "#sleep", "#Menopause"], "_usage": {}} for p in platforms}

    monkeypatch.setattr(routes.hashtag_agent, "generate_hashtags_batch", hashtags)
    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [])
    return seen


def test_a_new_post_gets_tidy_hashtags_and_the_brands_proven_ones(pipeline):
    user = _user()
    _published(user, "instagram", 80, ["#Menopause", "#WomensHealth"])
    result = content_pipeline.generate_post(user["id"], {"story": "Sleep in menopause", "platforms": ["instagram", "linkedin"]})
    assert result["content"]["instagram"]["hashtags"]["hashtags"] == ["#Sleep", "#Menopause"]
    assert pipeline["proven"] == {"instagram": ["#Menopause", "#WomensHealth"]}  # only platforms of this post with results
    agents = {a["agent"]: a for a in result["agents_executed"]}
    assert "did well on your posts" in agents["HashtagAgent"]["role"]


def test_a_brand_without_results_writes_hashtags_as_before(pipeline):
    result = content_pipeline.generate_post(_user()["id"], {"story": "Sleep in menopause", "platforms": ["linkedin"]})
    assert pipeline["proven"] == {} and result["success"]


def test_a_failing_lookup_never_stops_the_post(pipeline, monkeypatch):
    def broken(user_id):
        raise RuntimeError("database busy")

    monkeypatch.setattr(db, "proven_hashtags", broken)
    result = content_pipeline.generate_post(_user()["id"], {"story": "Sleep", "platforms": ["linkedin"]})
    assert result["success"] and pipeline["proven"] == {}
