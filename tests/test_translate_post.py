"""The same post in other languages (Studio Chat post card -> "Translate",
services/translate_service.py): one AI call per language, the compliance
check again on every caption, and each language saved as another version of
the post - so it can be edited, scheduled and approved on its own, and it
doesn't count as a new post on the plan. The AI is faked."""

import datetime
import json
import uuid

import pytest

import db
from api import routes
from services import translate_service as ts


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _user():
    return db.create_user("Ravi", f"{uuid.uuid4().hex[:10]}@example.com", "h")


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


class FakeLLM:
    """Answers every language with "[code] caption"; a code in fail_for raises."""

    def __init__(self, fail_for=()):
        self.fail_for = set(fail_for)
        self.prompts = []

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        self.prompts.append((system_prompt, user_prompt))
        code = next(
            c
            for c in ("hi", "ar", "mr", "ta", "ur")
            if f"LANGUAGE: write the caption in {ts.LANGUAGES[c][1]}" in system_prompt
        )
        if code in self.fail_for:
            raise RuntimeError("model down")
        posts = json.loads(user_prompt.split("POSTS:\n", 1)[1])
        return {
            "posts": {
                p.upper(): {"caption": f"[{code}] {v['caption']}", "hashtags": ["#Tag " + code, "Second"]}
                for p, v in posts.items()
            }
        }, {"total_tokens": 100, "cost_usd": 0.001}


def _post(user, **extra):
    content = {
        "linkedin": {
            "caption": {"primary_caption": "Wealth firms need AI rules."},
            "hashtags": {"hashtags": ["#AI"]},
            "media": {"image": {"url": "/img/a.png", "asset_id": 7}},
            "compliance": {"flags": []},
        },
        "facebook": {
            "caption": {"primary_caption": "AI rules for wealth firms."},
            "hashtags": {"hashtags": ["#Wealth"]},
        },
        "_quality": {"language": "en", "checks_passed": 4, "checks_total": 4},
        **extra,
    }
    conversation = db.ensure_conversation(user["id"], None, "AI rules")
    return db.save_run("AI rules", "Auto", ["linkedin", "facebook"], content, user["id"], conversation_id=conversation)


# ── the service ─────────────────────────────────────────────────────────────


def test_languages_are_checked_deduplicated_and_capped():
    assert ts.pick_languages(["hi", "HI", "xx", "en", "ar"], "en") == ["hi", "ar"]
    with pytest.raises(ts.TranslateError):
        ts.pick_languages(["en"], "en")  # only the post's own language
    with pytest.raises(ts.TranslateError):
        ts.pick_languages(["hi", "ar", "mr", "ta", "ur", "gu"], "en")
    assert ts.language_meta("ar")["rtl"] and not ts.language_meta("hi")["rtl"]


def test_each_language_is_one_call_for_every_platform_and_one_failing_keeps_the_others():
    texts = {"linkedin": {"caption": "Hello", "hashtags": ["#AI"]}, "facebook": {"caption": "Hi", "hashtags": []}}
    llm = FakeLLM(fail_for={"ar"})
    results = ts.translate_all(texts, ["hi", "ar"], llm)
    assert len(llm.prompts) == 2
    assert results["hi"]["posts"]["linkedin"] == {"caption": "[hi] Hello", "hashtags": ["#Taghi", "#Second"]}
    assert results["hi"]["posts"]["facebook"]["caption"] == "[hi] Hi"
    assert "Arabic" in results["ar"]["error"]
    system_prompt = llm.prompts[0][0]
    assert "Never add or drop claims" in system_prompt and "half of the hashtags" in system_prompt


# ── the endpoint ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_ai(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(routes, "refine_llm", llm)
    checked = []

    def check(caption, platform, rules, llm_):
        checked.append(platform)
        return {"caption": caption + " (checked)", "flags": [], "fixed": True}, {"total_tokens": 5, "cost_usd": 0.0001}

    monkeypatch.setattr(routes, "check_caption", check)
    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [{"id": "r1"}])
    return checked


def test_translating_saves_a_checked_version_per_language(app, fake_ai):
    user = _user()
    run_id = _post(user)
    client = _login(app, user)
    before = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)

    r = client.post(f"/api/history/{run_id}/translate", json={"languages": ["hi", "ar"]})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert [v["language"]["code"] for v in body["versions"]] == ["hi", "ar"] and body["source"]["code"] == "en"
    assert sorted(fake_ai) == ["facebook", "facebook", "linkedin", "linkedin"]  # every caption checked again

    hindi = db.get_run_by_id(body["versions"][0]["run_id"], user_id=user["id"])
    content = hindi["content"]
    assert content["linkedin"]["caption"]["primary_caption"] == "[hi] Wealth firms need AI rules. (checked)"
    assert content["linkedin"]["media"]["image"]["asset_id"] == 7  # the image is reused
    assert content["_meta"] == {"version_of": run_id, "language": "hi", "translated_from_run_id": run_id}
    assert content["_quality"]["language"] == "hi"
    assert hindi["conversation_id"] == db.get_run_by_id(run_id, user_id=user["id"])["conversation_id"]

    # a translation is a version of the same post, not a new one on the plan
    made = [p["id"] for p in db.posts_created_between(user["id"], before, before + datetime.timedelta(hours=1))]
    assert run_id in made and body["versions"][0]["run_id"] not in made


def test_translating_a_translation_hangs_off_the_first_version(app, fake_ai):
    user = _user()
    run_id = _post(user)
    client = _login(app, user)
    hindi = client.post(f"/api/history/{run_id}/translate", json={"languages": ["hi"]}).get_json()["versions"][0]
    r = client.post(
        f"/api/history/{hindi['run_id']}/translate", json={"languages": ["mr"], "version_of_run_id": run_id}
    )
    marathi = r.get_json()["versions"][0]
    assert marathi["content"]["_meta"]["version_of"] == run_id
    assert marathi["content"]["_meta"]["translated_from_run_id"] == hindi["run_id"]


def test_bad_requests_are_refused(app, fake_ai):
    user = _user()
    run_id = _post(user)
    client = _login(app, user)
    assert client.post(f"/api/history/{run_id}/translate", json={"languages": []}).status_code == 400
    assert client.post(f"/api/history/{run_id}/translate", json={"languages": ["en"]}).status_code == 400
    other = _login(app, _user())  # someone else's post
    assert other.post(f"/api/history/{run_id}/translate", json={"languages": ["hi"]}).status_code == 404
    image_post = _post(user, _preset={"id": "carousel"})
    assert client.post(f"/api/history/{image_post}/translate", json={"languages": ["hi"]}).status_code == 400


def test_a_language_that_fails_is_reported_and_the_rest_are_kept(app, fake_ai, monkeypatch):
    monkeypatch.setattr(routes, "refine_llm", FakeLLM(fail_for={"ar"}))
    user = _user()
    client = _login(app, user)
    body = client.post(f"/api/history/{_post(user)}/translate", json={"languages": ["hi", "ar"]}).get_json()
    assert [v["language"]["code"] for v in body["versions"]] == ["hi"]
    assert body["errors"][0]["language"]["code"] == "ar"


def test_the_credit_limit_stops_translating(app, fake_ai, monkeypatch):
    monkeypatch.setattr(
        routes,
        "get_user_usage_stats",
        lambda uid: {"credit_capped": True, "remaining_credits": 0.0, "credit_limit": 2.0, "currency": "USD"},
    )
    user = _user()
    r = _login(app, user).post(f"/api/history/{_post(user)}/translate", json={"languages": ["hi"]})
    assert r.status_code == 402
