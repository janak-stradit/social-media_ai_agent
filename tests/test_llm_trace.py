"""Admin -> AI calls (services/llm_trace.py): every AI call attempt is kept with
its prompts, answer, tokens, cost and where it came from; who made it follows
into worker threads; admins only; old calls are deleted; and
scripts/make_admin.py. The AI provider is faked."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

import db
from config import Config
from services import llm_trace
from services.llm_service import LLMService


class FakeCompletions:
    def __init__(self, answers):
        self.answers = list(answers)

    def create(self, **kwargs):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer), finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30))


def _llm(*answers):
    llm = LLMService.__new__(LLMService)
    llm.providers = [{"name": "openrouter", "model": "gpt-4o-mini",
                      "client": SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(answers)))}]
    return llm


def _calls(group):
    return db.list_llm_calls(group_id=group, limit=50)["calls"]


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def test_each_attempt_is_kept_with_its_prompts_answer_and_cost():
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    llm = _llm(RuntimeError("rate limited"), "", '{"caption": "Hello Pune"}')
    with llm_trace.bind(user_id=user["id"]) as ctx:
        assert llm.generate_json("You write captions. Return JSON.", "Brief: Diwali offer", max_retries_per_provider=3) \
            == {"caption": "Hello Pune"}
    calls = _calls(ctx["group_id"])
    assert [c["status"] for c in reversed(calls)] == ["error", "empty", "ok"]  # newest first
    assert [c["attempt"] for c in reversed(calls)] == [1, 2, 3]
    ok = db.get_llm_call(calls[0]["id"])
    assert ok["system_prompt"] == "You write captions. Return JSON." and ok["user_prompt"] == "Brief: Diwali offer"
    assert ok["output"] == '{"caption": "Hello Pune"}' and ok["kind"] == "json" and ok["model"] == "gpt-4o-mini"
    assert ok["input_tokens"] == 120 and ok["output_tokens"] == 30 and ok["cost_usd"] > 0
    assert ok["user_id"] == user["id"] and ok["user_email"] == user["email"]
    assert ok["feature"] == "tests/test_llm_trace.py:test_each_attempt_is_kept_with_its_prompts_answer_and_cost"
    assert db.get_llm_call(calls[-1]["id"])["error"] == "rate limited"


def test_who_asked_follows_into_worker_threads():
    with llm_trace.bind(user_id=4242) as ctx:
        with ThreadPoolExecutor(max_workers=2) as pool:
            carried = pool.submit(llm_trace.carry(_llm("one").generate), "s", "u").result()
            lost = pool.submit(_llm("two").generate, "s", "u").result()  # not carried: no user
    assert carried == "one" and lost == "two"
    calls = _calls(ctx["group_id"])
    assert len(calls) == 1 and calls[0]["user_id"] == 4242


def test_nothing_is_kept_when_switched_off(monkeypatch):
    monkeypatch.setattr(Config, "LLM_TRACING", False)
    with llm_trace.bind() as ctx:
        _llm("hi").generate("s", "u")
    assert _calls(ctx["group_id"]) == []


def test_tracing_never_breaks_the_call(monkeypatch):
    monkeypatch.setattr(db, "save_llm_call", lambda fields: (_ for _ in ()).throw(RuntimeError("db down")))
    assert _llm("still works").generate("s", "u") == "still works"


def test_old_calls_are_deleted(monkeypatch):
    with llm_trace.bind() as ctx:
        _llm("old").generate("s", "u")
    with Session(db.engine) as session:
        session.query(db.LlmCall).filter(db.LlmCall.group_id == ctx["group_id"]).update(
            {"created_at": (datetime.now(timezone.utc) - timedelta(days=31)).replace(tzinfo=None)})
        session.commit()
    monkeypatch.setattr(Config, "LLM_TRACE_DAYS", 30)
    assert llm_trace.purge_old() >= 1 and _calls(ctx["group_id"]) == []


def test_admins_only_with_filters_and_one_call_in_full(app, monkeypatch):
    monkeypatch.setattr(Config, "ADMIN_2FA_REQUIRED", False)
    admin = db.create_user("Admin", f"{uuid.uuid4().hex[:10]}@example.com", "hash", is_admin=True)
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    marker = uuid.uuid4().hex[:10]
    with llm_trace.bind(user_id=user["id"]) as ctx:
        _llm("Answer", RuntimeError("boom")).generate("System", f"Brief {marker}", max_retries_per_provider=1)
        with pytest.raises(Exception):
            _llm(RuntimeError("boom")).generate("System", f"Brief {marker}", max_retries_per_provider=1)
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    assert client.get("/api/admin/ai-calls").status_code == 403
    assert client.get("/admin/ai-calls").status_code in (302, 403)
    with client.session_transaction() as s:
        s["user_id"] = admin["id"]
    body = client.get(f"/api/admin/ai-calls?q={marker}").get_json()
    assert body["total"] == 2 and body["totals"]["errors"] == 1 and body["calls"][0]["user_email"] == user["email"]
    assert client.get(f"/api/admin/ai-calls?q={marker}&status=error").get_json()["total"] == 1
    assert client.get(f"/api/admin/ai-calls?group={ctx['group_id']}").get_json()["total"] == 2
    assert client.get(f"/api/admin/ai-calls?user={user['email'][:8]}&q={marker}").get_json()["total"] == 2
    full = client.get(f"/api/admin/ai-calls/{body['calls'][1]['id']}").get_json()["call"]
    assert full["user_prompt"] == f"Brief {marker}" and full["output"] == "Answer"
    page = client.get("/admin/ai-calls")
    assert page.status_code == 200 and b"js/admin_ai_calls.js" in page.data


def test_make_admin_script():
    from scripts import make_admin

    email = f"{uuid.uuid4().hex[:10]}@example.com"
    user = db.create_user("Janak", email, "hash")
    assert make_admin.main(["make_admin.py", "nobody@example.com"]) == 1
    assert make_admin.main(["make_admin.py", email.upper()]) == 0
    assert db.get_user_by_email(email).is_admin
    assert make_admin.main(["make_admin.py", email, "--remove"]) == 0
    assert not db.get_user_by_email(email).is_admin and user["id"]


def _call(group, feature, ended, latency_ms, status="ok", cost=0.001, user_id=None):
    db.save_llm_call({"user_id": user_id, "group_id": group, "feature": feature, "kind": "json", "provider": "heyroute",
                      "model": "gpt-4o-mini", "status": status, "system_prompt": "s", "user_prompt": "u", "output": "o",
                      "input_tokens": 100, "output_tokens": 20, "cost_usd": cost, "latency_ms": latency_ms,
                      "created_at": ended.replace(tzinfo=None)})


def test_a_trace_is_a_timeline_of_one_generations_calls(app, monkeypatch):
    monkeypatch.setattr(Config, "ADMIN_2FA_REQUIRED", False)
    admin = db.create_user("Admin", f"{uuid.uuid4().hex[:10]}@example.com", "hash", is_admin=True)
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    group, t0 = uuid.uuid4().hex[:16], datetime.now(timezone.utc) - timedelta(minutes=5)
    # research 0-4 s; caption and hashtags in parallel 4-9 s and 4-6 s (hashtags failed once first); review 9-10 s
    _call(group, "agents/story_agent.py:analyze", t0 + timedelta(seconds=4), 4000, user_id=user["id"])
    _call(group, "agents/hashtag_agent.py:generate_hashtags_batch", t0 + timedelta(seconds=5), 1000, "error", 0.0, user["id"])
    _call(group, "agents/hashtag_agent.py:generate_hashtags_batch", t0 + timedelta(seconds=6), 1000, user_id=user["id"])
    _call(group, "agents/caption_agent.py:generate_caption", t0 + timedelta(seconds=9), 5000, user_id=user["id"])
    _call(group, "agents/reviewer_agent.py:review", t0 + timedelta(seconds=10), 1000, user_id=user["id"])

    trace = db.get_llm_trace(group)
    assert trace["duration_ms"] == 10000 and trace["user_email"] == user["email"]
    assert trace["totals"] == {"calls": 5, "errors": 1, "input_tokens": 500, "output_tokens": 100, "cost_usd": 0.004}
    starts = {(c["feature"].split(":")[1], c["status"]): c["start_ms"] for c in trace["calls"]}
    assert starts == {("analyze", "ok"): 0, ("generate_hashtags_batch", "error"): 4000, ("generate_hashtags_batch", "ok"): 5000,
                      ("generate_caption", "ok"): 4000, ("review", "ok"): 9000}
    assert [c["start_ms"] for c in trace["calls"]] == sorted(c["start_ms"] for c in trace["calls"])

    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = admin["id"]
    listed = client.get(f"/api/admin/ai-calls/traces?user={user['email']}").get_json()
    row = listed["traces"][0]
    assert listed["total"] == 1 and row["group_id"] == group and row["calls"] == 5 and row["errors"] == 1
    assert row["duration_ms"] == 10000 and row["features"][0] == "agents/story_agent.py:analyze"
    assert client.get(f"/api/admin/ai-calls/traces?user={user['email']}&status=error").get_json()["total"] == 1  # has a failed call
    assert client.get(f"/api/admin/ai-calls/traces/{group}").get_json()["trace"]["duration_ms"] == 10000
    assert client.get("/api/admin/ai-calls/traces/nope").status_code == 404
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    assert client.get(f"/api/admin/ai-calls/traces/{group}").status_code == 403


def test_overview_per_day_and_per_part_of_the_app(app, monkeypatch):
    monkeypatch.setattr(Config, "ADMIN_2FA_REQUIRED", False)
    admin = db.create_user("Admin", f"{uuid.uuid4().hex[:10]}@example.com", "hash", is_admin=True)
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    now = datetime.now(timezone.utc)
    for n, latency in enumerate((100, 200, 300, 400, 5000)):
        _call(uuid.uuid4().hex[:16], "agents/caption_agent.py:generate_caption", now - timedelta(days=n % 2), latency,
              "error" if n == 4 else "ok", 0.002, user["id"])
    _call(uuid.uuid4().hex[:16], "services/hook_service.py:other_openings", now, 50, cost=0.0005, user_id=user["id"])
    stats = db.llm_call_stats(user_email=user["email"])
    caption = stats["features"][0]
    assert caption["feature"] == "agents/caption_agent.py:generate_caption"  # costliest first
    assert caption["calls"] == 5 and caption["errors"] == 1 and caption["cost_usd"] == 0.01
    assert caption["avg_ms"] == 1200 and caption["p95_ms"] == 5000
    assert sum(d["calls"] for d in stats["days"]) == 6 and len(stats["days"]) == 2
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = admin["id"]
    body = client.get(f"/api/admin/ai-calls/stats?user={user['email']}&days=7").get_json()
    assert body["success"] and len(body["features"]) == 2
