"""Autopilot (services/autopilot_service.py): planning the next two weeks,
writing posts 2 days ahead, the user's OK before anything goes out, and
holding posts that weren't approved in time. The text and image models are faked."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

import db
from services import autopilot_service as ap

NOW = datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc)  # a Monday


class FakeLLM:
    def __init__(self, caption="Three ways to plan your posts so they go out on time. Save this for later."):
        self.caption, self.calls = caption, []

    def generate_json(self, system, user, **kw):
        self.calls.append((system, user))
        return {"title": "Planning tips", "caption": self.caption, "hashtags": ["planning", "#SmallBusiness"],
                "image_prompt": "a desk with a planner"}, {"total_tokens": 100}


class FakeMedia:
    def __init__(self):
        self.calls = 0

    def create_preset_image(self, prompt, image_path, aspect, logo_path=None, model=None, require_reference=True):
        self.calls += 1
        return {"success": True, "url": f"/static/uploads/ap{self.calls}.jpg", "cost": 0.55, "model": "fake"}


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _user():
    return db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "h")


def _client(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
        s["sv"] = db.get_session_version(user["id"]) or 0
    return client


SETTINGS = {"posts_per_week": 3, "platforms": ["linkedin"], "days_off": [6], "mix": {"tips": 2, "offers": 1, "news": 0},
            "festivals": False, "images": False, "image_cap_month": 4, "tz_minutes": 0}


def _turn_on(user_id, **extra):
    fields = ap.clean_settings({**SETTINGS, **extra})
    db.save_autopilot_settings(user_id, enabled=True, **fields)


# ── Planning ────────────────────────────────────────────────────────────
def test_posting_days_spread_over_the_open_days():
    assert ap.posting_days(3, [6]) == [0, 2, 4]
    assert ap.posting_days(7, [5, 6]) == [0, 1, 2, 3, 4]  # never more than the open days
    assert ap.posting_days(1, []) == [0]


def test_kinds_follow_the_mix_interleaved():
    assert ap.kind_sequence({"tips": 2, "offers": 1, "news": 1}, 4) == ["tips", "offers", "news", "tips"]
    assert ap.kind_sequence({}, 3).count("tips") >= 1  # no mix: an even split


def test_plan_covers_two_weeks_on_posting_days_at_the_best_hour():
    settings = {**ap.clean_settings(SETTINGS), "enabled": True, "paused": False}
    best = {"linkedin": {"hours": [9], "label": "Tue around 9 am"}}
    slots = ap.plan_slots(settings, best, NOW)
    assert slots and all(s["slot_at"].weekday() in (0, 2, 4) for s in slots)
    assert all(s["slot_at"].hour == 9 for s in slots)
    assert all(s["slot_at"] > NOW for s in slots)
    assert len(slots) in (6, 7)  # 3 a week for two weeks (+ today's if still ahead)
    assert {s["kind"] for s in slots} <= {"tips", "offers"}  # news is 0 in this mix


def test_a_festival_takes_a_slot_shortly_before_it(monkeypatch):
    from services import festival_service

    monkeypatch.setattr(festival_service, "next_celebration",
                        lambda regions, today=None, days_ahead=120: {"name": "Diwali", "date": "2026-10-30"})
    settings = {**ap.clean_settings({**SETTINGS, "festivals": True, "days_off": []}), "enabled": True, "paused": False}
    slots = ap.plan_slots(settings, {}, NOW)
    festival = [s for s in slots if s["kind"] == "festival"]
    assert len(festival) == 1 and festival[0]["topic"] == "Diwali"
    assert festival[0]["slot_at"].date().isoformat() == "2026-10-28" and "Diwali" in festival[0]["reason"]


@pytest.mark.parametrize("raw,error", [
    ({**SETTINGS, "platforms": []}, "at least one platform"),
    ({**SETTINGS, "posts_per_week": 9}, "between 1 and 7"),
    ({**SETTINGS, "days_off": [0, 1, 2, 3, 4, 5, 6]}, "at least one day"),
])
def test_settings_are_checked(raw, error):
    with pytest.raises(ap.AutopilotError, match=error):
        ap.clean_settings(raw)


def test_planning_twice_never_duplicates_slots():
    user = _user()
    _turn_on(user["id"])
    first = ap.plan_ahead(user["id"], NOW)
    assert first > 0 and ap.plan_ahead(user["id"], NOW) == 0


def test_paused_autopilot_plans_nothing():
    user = _user()
    _turn_on(user["id"])
    db.save_autopilot_settings(user["id"], paused=True)
    assert ap.plan_ahead(user["id"], NOW) == 0


# ── Writing and the user's OK ───────────────────────────────────────────
def _first_planned(user_id):
    return db.autopilot_posts_with_status(("planned",), user_id=user_id)[0]


def test_posts_are_written_two_days_ahead_and_wait_for_the_ok():
    user = _user()
    _turn_on(user["id"])
    llm = FakeLLM()
    result = ap.run_for_user(user["id"], NOW, llm=llm)
    assert result["planned"] > 0 and result["written"] >= 1
    waiting = db.autopilot_posts_with_status(("needs_ok",), user_id=user["id"])
    assert waiting and all(datetime.fromisoformat(p["slot_at"]) - NOW <= ap.WRITE_AHEAD for p in waiting)
    post = waiting[0]
    assert post["caption"].startswith("Three ways") and "#planning" in post["hashtags"]
    deadline = datetime.fromisoformat(post["approve_by"])
    assert NOW < deadline <= datetime.fromisoformat(post["slot_at"])
    # the brand profile leads the prompt, the post's brief comes last
    assert llm.calls[0][1].startswith("Brand profile:")
    # nothing is scheduled until the user approves
    assert db.scheduled_posts_between(user["id"], NOW - timedelta(days=1), NOW + timedelta(days=30)) == []


def test_a_price_or_offer_is_flagged_for_checking():
    user = _user()
    _turn_on(user["id"])
    ap.plan_ahead(user["id"], NOW)
    post = ap.draft(_first_planned(user["id"]), NOW, llm=FakeLLM("This week only: 20% off every plan."))
    assert any(c["label"] == "Check the facts" and not c["ok"] for c in post["checks"])


def test_images_stop_at_the_monthly_cap(monkeypatch):
    user = _user()
    _turn_on(user["id"], images=True, image_cap_month=1, days_off=[])
    monkeypatch.setattr(db, "get_image_quota", lambda uid: {"unlimited": True, "remaining": 99, "model": None})
    ap.plan_ahead(user["id"], NOW)
    media = FakeMedia()
    first = ap.draft(_first_planned(user["id"]), NOW, llm=FakeLLM(), media=media)
    second = ap.draft(_first_planned(user["id"]), NOW, llm=FakeLLM(), media=media)
    assert first["image_url"] and first["cost_usd"] == 0.55
    assert second["image_url"] is None and media.calls == 1
    assert any("used" in c["note"] for c in second["checks"])


def test_approving_puts_the_post_on_the_calendar(monkeypatch):
    from services import publish_service

    monkeypatch.setattr(publish_service, "connections", lambda uid: {"linkedin": "Your Brand"})
    user = _user()
    _turn_on(user["id"])
    ap.plan_ahead(user["id"], NOW)
    post = ap.draft(_first_planned(user["id"]), NOW, llm=FakeLLM())
    approved = ap.approve(user["id"], post["id"], caption="Edited caption.", now=NOW)
    assert approved["status"] == "approved" and approved["caption"] == "Edited caption."
    scheduled = db.get_scheduled_post(user["id"], approved["scheduled_post_id"])
    assert scheduled and scheduled["status"] == "pending"
    with pytest.raises(ap.AutopilotError):  # not twice
        ap.approve(user["id"], post["id"], now=NOW)
    # leave nothing due for other tests' publish_due runs
    assert db.cancel_scheduled_post(user["id"], approved["scheduled_post_id"])


def test_approving_without_a_connected_account_says_what_to_do(monkeypatch):
    from services import publish_service

    monkeypatch.setattr(publish_service, "connections", lambda uid: {})
    user = _user()
    _turn_on(user["id"])
    ap.plan_ahead(user["id"], NOW)
    post = ap.draft(_first_planned(user["id"]), NOW, llm=FakeLLM())
    with pytest.raises(ap.AutopilotError, match="Connect your LinkedIn"):
        ap.approve(user["id"], post["id"], now=NOW)
    assert db.get_autopilot_post(user["id"], post["id"])["status"] == "needs_ok"


def test_not_approved_in_time_is_held_never_posted():
    user = _user()
    _turn_on(user["id"])
    ap.plan_ahead(user["id"], NOW)
    post = ap.draft(_first_planned(user["id"]), NOW, llm=FakeLLM())
    later = datetime.fromisoformat(post["approve_by"]) + timedelta(minutes=1)
    assert ap.hold_missed(later) >= 1
    assert db.get_autopilot_post(user["id"], post["id"])["status"] == "held"


def test_skipped_posts_stay_skipped():
    user = _user()
    _turn_on(user["id"])
    ap.plan_ahead(user["id"], NOW)
    post = ap.draft(_first_planned(user["id"]), NOW, llm=FakeLLM())
    assert ap.skip(user["id"], post["id"])["status"] == "skipped"


# ── The API ─────────────────────────────────────────────────────────────
def test_turning_it_on_plans_the_next_two_weeks(app):
    user = _user()
    client = _client(app, user)
    off = client.get("/api/autopilot").get_json()
    assert off["settings"]["configured"] is False and off["posts"] == []
    on = client.put("/api/autopilot/settings", json={**SETTINGS, "enabled": True}).get_json()
    assert on["success"] and on["planned"] > 0 and on["settings"]["enabled"]
    assert all(p["status"] == "planned" for p in on["posts"])
    paused = client.post("/api/autopilot/pause").get_json()
    assert paused["settings"]["paused"] is True


def test_bad_settings_are_refused(app):
    client = _client(app, _user())
    reply = client.put("/api/autopilot/settings", json={**SETTINGS, "platforms": []})
    assert reply.status_code == 400 and "platform" in reply.get_json()["error"]


def test_someone_elses_post_cannot_be_approved_or_skipped(app):
    owner, stranger = _user(), _user()
    _turn_on(owner["id"])
    ap.plan_ahead(owner["id"], NOW)
    post = _first_planned(owner["id"])
    client = _client(app, stranger)
    for action in ("approve", "skip", "edit"):
        assert client.post(f"/api/autopilot/posts/{post['id']}/{action}").status_code == 400
    assert app.test_client().get("/api/autopilot").status_code == 401
    assert db.get_autopilot_post(owner["id"], post["id"])["status"] == "planned"


def test_the_page_loads(app):
    client = _client(app, _user())
    page = client.get("/autopilot")
    assert page.status_code in (200, 302)  # 302 = onboarding not finished for a bare test user


# ── Email when posts need the user's OK ─────────────────────────────────
class FakeEmail:
    sent = []

    def send_billing_email(self, **kw):
        FakeEmail.sent.append(kw)
        return {"success": True}


def test_one_email_lists_every_post_that_needs_the_ok(monkeypatch):
    from config import Config
    from services import email_service

    FakeEmail.sent = []
    monkeypatch.setattr(email_service, "EmailService", FakeEmail)
    monkeypatch.setattr(Config, "APP_BASE_URL", "https://app.example.com")
    user = _user()
    _turn_on(user["id"], posts_per_week=7, days_off=[])  # several posts inside the 48h window
    result = ap.run_for_user(user["id"], NOW, llm=FakeLLM())
    assert result["written"] >= 2 and result["emailed"] is True
    assert len(FakeEmail.sent) == 1
    mail = FakeEmail.sent[0]
    assert mail["to_email"] == user["email"] and mail["url"] == "https://app.example.com/autopilot"
    assert mail["subject"].startswith(f"{result['written']} posts need your OK")
    assert sum("approve by" in line for line in mail["lines"]) == result["written"]
    # the next hourly run writes nothing new, so no second email
    assert ap.run_for_user(user["id"], NOW, llm=FakeLLM())["emailed"] is False and len(FakeEmail.sent) == 1


def test_no_email_without_the_apps_address_or_on_failure(monkeypatch):
    from config import Config
    from services import email_service

    class Broken:
        def send_billing_email(self, **kw):
            raise RuntimeError("SMTP is not configured")

    user = _user()
    _turn_on(user["id"])
    ap.plan_ahead(user["id"], NOW)
    post = ap.draft(_first_planned(user["id"]), NOW, llm=FakeLLM())
    monkeypatch.setattr(Config, "APP_BASE_URL", "")
    assert ap.email_needs_ok(user["id"], [post]) is False
    monkeypatch.setattr(Config, "APP_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(email_service, "EmailService", Broken)
    assert ap.email_needs_ok(user["id"], [post]) is False  # logged; the bell notification still stands
