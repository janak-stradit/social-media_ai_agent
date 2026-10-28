"""Autopilot (services/autopilot_service.py, AUTOPILOT_PLAN.md): off by
default; the user's set-up plans the next two weeks; days coming up are
written with the Studio pipeline and sent for approval; only approved posts
are scheduled, and nothing is published on its own. The AI, the image model
and the social accounts are faked."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

import db
from services import autopilot_service as ap
from services import content_pipeline

def _a_tuesday_ahead() -> datetime:
    """06:00 UTC (11:30 in India) on a Tuesday at least a week away: approving goes
    through the real endpoint, which uses the real clock, so slots must be ahead."""
    day = datetime.now(timezone.utc).date() + timedelta(days=7)
    day += timedelta(days=(1 - day.weekday()) % 7)
    while (day + timedelta(days=3)).month != day.month:  # the posts written in tests stay in one month
        day += timedelta(days=7)
    return datetime(day.year, day.month, day.day, 6, 0, tzinfo=timezone.utc)


NOW = _a_tuesday_ahead()
TODAY = NOW.date()
IST = 330


def _day(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


def _nine_ist(n: int) -> datetime:
    """09:00 in India (the best hour in these tests) n days after NOW's day, in UTC."""
    day = TODAY + timedelta(days=n)
    return datetime(day.year, day.month, day.day, 3, 30, tzinfo=timezone.utc)
SETUP = {"enabled": True, "platforms": ["linkedin"], "posts_per_week": 7, "days_off": [], "days_ahead": 3,
         "festivals": False, "tz_minutes": IST}


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def user():
    user = db.create_user("Asha", f"{uuid.uuid4().hex[:8]}@example.com", "h")
    with Session(db.engine) as session:
        session.query(db.User).filter(db.User.id == user["id"]).update({"email_verified": True, "onboarding_completed": True})
        session.commit()
    return user


@pytest.fixture
def ready(user, monkeypatch):
    """A brand profile, LinkedIn connected, ideas to write about, and a fake writer."""
    db.save_user_brand_profile(user["id"], "https://acme.in", "Acme", "Logistics", None, None, [], [], [], [])
    from services import best_time_service, publish_service

    monkeypatch.setattr(publish_service, "connections", lambda uid: {"linkedin": "Acme Ltd", "facebook": None, "instagram": None})
    monkeypatch.setattr(best_time_service, "best_times", lambda uid, tz=0, now=None: {"linkedin": {"hours": [9]}})
    ideas = [{"title": "Same-day delivery", "prompt": "Explain same-day delivery."},
             {"title": "Meet the team", "prompt": "Introduce the warehouse team."}]
    monkeypatch.setattr(ap, "_plan_ideas", lambda uid, settings, now: list(ideas))
    written = []

    def fake_generate(uid, data):
        written.append(data)
        return {"success": True, "run_id": None, "content": {p: {
            "caption": {"primary_caption": f"{data['story']} Faster deliveries for every customer in Pune, every day."},
            "hashtags": {"hashtags": ["#Logistics"]}, "media_prompt": "a delivery van at dawn"} for p in data["platforms"]}}

    monkeypatch.setattr(content_pipeline, "generate_post", fake_generate)
    from api import routes

    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [])
    return written


def _client(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def _turn_on(user, **extra):
    return ap.save(user["id"], {**SETUP, **extra}, now=NOW)


def _posts(user, *statuses):
    posts = sorted(db.list_autopilot_posts(user["id"], limit=100), key=lambda p: p["slot_date"])
    return [p for p in posts if not statuses or p["status"] in statuses]


# ── Planning ─────────────────────────────────────────────────────────────


def test_posting_days_spread_over_the_open_days():
    assert ap.posting_days(3, [6]) == [0, 2, 4]
    assert ap.posting_days(7, [5, 6]) == [0, 1, 2, 3, 4]  # never more than the open days
    assert ap.posting_days(1, []) == [0]


def test_kinds_follow_the_mix_interleaved():
    assert ap.kind_sequence({"tips": 2, "offers": 1, "news": 1}, 4) == ["tips", "offers", "news", "tips"]
    assert ap.kind_sequence({"tips": 1}, 3) == ["tips", "tips", "tips"]  # a short mix repeats


def test_the_plan_covers_two_weeks_at_the_best_hour():
    settings = {**db.AUTOPILOT_DEFAULTS, **ap.clean_settings({**SETUP, "posts_per_week": 3, "days_off": [6],
                                                               "mix": {"tips": 2, "offers": 1}})}
    days = ap.plan_days(settings, {"linkedin": {"hours": [9]}}, NOW)
    assert days and all(d["date"].weekday() in (0, 2, 4) for d in days)
    assert all(d["slot_at"].astimezone(timezone(timedelta(minutes=IST))).hour == 9 for d in days)
    assert len(days) == 6 and {d["kind"] for d in days} <= {"tips", "offers"}


def test_a_festival_takes_the_posting_day_before_it(monkeypatch):
    from services import festival_service

    monkeypatch.setattr(festival_service, "next_celebration",
                        lambda regions, today=None, days_ahead=120: {"name": "Diwali", "date": _day(12)})
    settings = {**db.AUTOPILOT_DEFAULTS, **ap.clean_settings({**SETUP, "festivals": True})}
    festival = [d for d in ap.plan_days(settings, {}, NOW) if d["kind"] == "festival"]
    assert len(festival) == 1 and festival[0]["topic"] == "Diwali"
    assert festival[0]["date"] == TODAY + timedelta(days=11) and "Diwali" in festival[0]["reason"]


@pytest.mark.parametrize("raw,error", [
    ({"posts_per_week": 9}, "between 1 and 7"),
    ({"days_off": [0, 1, 2, 3, 4, 5, 6]}, "at least one day"),
])
def test_settings_are_checked(raw, error):
    with pytest.raises(ap.AutopilotError, match=error):
        ap.clean_settings(raw)


def test_turning_it_on_plans_two_weeks_once(user, ready):
    _turn_on(user)
    planned = _posts(user, "planned")
    assert len(planned) == 14 and planned[0]["slot_date"] == _day(1)  # today's 09:00 has passed
    assert ap.plan_ahead(user["id"], NOW) == 0  # never twice
    ap.save(user["id"], {"posts_per_week": 2, "days_off": []}, now=NOW)  # a new plan
    assert len(_posts(user, "planned")) == 4


# ── The switch ───────────────────────────────────────────────────────────


def test_it_is_off_by_default(app, user):
    state = _client(app, user).get("/api/autopilot").get_json()["autopilot"]
    assert state["enabled"] is False and state["posts"] == [] and state["plan"] == []
    assert [p["id"] for p in state["platform_choices"] if not p["available"]] == ["instagram"]  # needs an image


def test_it_cant_be_turned_on_without_a_brand_or_an_account(app, user, monkeypatch):
    from services import publish_service

    monkeypatch.setattr(publish_service, "connections", lambda uid: {})
    res = _client(app, user).put("/api/autopilot", json={"enabled": True, "platforms": ["linkedin"]})
    assert res.status_code == 400 and "brand profile" in res.get_json()["error"]
    db.save_user_brand_profile(user["id"], "https://acme.in", "Acme", "Logistics", None, None, [], [], [], [])
    res = _client(app, user).put("/api/autopilot", json={"enabled": True, "platforms": ["linkedin"]})
    assert res.status_code == 400 and "Connect LinkedIn" in res.get_json()["error"]
    assert db.get_autopilot_settings(user["id"])["enabled"] is False


def test_turning_it_on_is_audited_and_instagram_needs_images(app, user, ready):
    state = _client(app, user).put("/api/autopilot", json={**SETUP, "platforms": ["linkedin", "instagram"],
                                                           "days_ahead": 30}).get_json()["autopilot"]
    assert state["enabled"] and state["platforms"] == ["linkedin"] and state["days_ahead"] == ap.MAX_DAYS_AHEAD
    assert any(e["action"] == "autopilot.turned_on" for e in db.list_audit_log(10, user["id"]))
    state = _client(app, user).put("/api/autopilot", json={"images": True, "platforms": ["linkedin", "instagram"]}).get_json()["autopilot"]
    assert state["platforms"] == ["linkedin", "instagram"]


def test_pausing_and_resuming(app, user, ready):
    _turn_on(user)
    client = _client(app, user)
    state = client.put("/api/autopilot", json={"paused": True}).get_json()["autopilot"]
    assert state["paused_reason"] == ap.PAUSED_BY_USER and user["id"] not in db.autopilot_user_ids()
    assert ap.run_for_user(user["id"], NOW) == 0
    state = client.put("/api/autopilot", json={"paused": False}).get_json()["autopilot"]
    assert state["paused_reason"] is None and user["id"] in db.autopilot_user_ids()


# ── Preparing posts ──────────────────────────────────────────────────────


def test_due_days_are_sent_for_approval_never_published(user, ready):
    _turn_on(user)
    assert ap.run_for_user(user["id"], NOW) == ap.MAX_POSTS_PER_RUN  # two per hour, not the whole week at once
    waiting = _posts(user, "awaiting_approval")
    assert [p["slot_date"] for p in waiting] == [_day(1), _day(2)]
    assert waiting[0]["slot_at"] == _nine_ist(1).isoformat()  # 09:00 in India, the best hour
    assert ready[0]["selected_outputs"] == ["text"] and ready[0]["platforms"] == ["linkedin"]
    assert {p["idea_title"] for p in waiting} <= {"Same-day delivery", "Meet the team", "Products & offers"}

    req = db.get_approval_request(waiting[0]["approval_request_id"])
    assert req["status"] == "pending" and req["pipeline_client_id"] == f"autopilot-{waiting[0]['id']}"
    assert req["items"][0]["platform"] == "linkedin" and req["items"][0]["media"] == "none"
    from db import ScheduledPost

    with Session(db.engine) as session:  # nothing is scheduled before it is approved
        assert session.query(ScheduledPost).filter(ScheduledPost.user_id == user["id"]).count() == 0

    # the next run writes the last due day (days_ahead=3: up to Friday), then nothing
    assert ap.run_for_user(user["id"], NOW) == 1
    assert ap.run_for_user(user["id"], NOW) == 0
    assert len(_posts(user, "awaiting_approval")) == 3


def test_topics_and_avoid_shape_the_brief(user, ready):
    _turn_on(user, topics="same-day delivery, cold chain", avoid="competitors")
    ap.run_for_user(user["id"], NOW)
    assert all("Do not mention: competitors." in w["story"] for w in ready)
    assert all("same-day delivery" in w["story"] or "cold chain" in w["story"] for w in ready)


def test_images_stop_at_the_monthly_cap(user, ready, monkeypatch):
    class FakeMedia:
        calls = 0

        def create_preset_image(self, prompt, image_path, aspect, logo_path=None, model=None, require_reference=True):
            FakeMedia.calls += 1
            return {"success": True, "url": "/static/img/landing/channel-square.webp", "cost": 0.55, "model": "fake"}

    from services import approval_bundle_service

    monkeypatch.setattr(db, "get_image_quota", lambda uid: {"unlimited": True, "remaining": 99, "model": None})
    monkeypatch.setattr(approval_bundle_service, "_image_path", lambda url, folder: "/tmp/x.webp")
    _turn_on(user, images=True, image_cap_month=1)
    ap.run_for_user(user["id"], NOW, media=FakeMedia())
    first, second = _posts(user, "awaiting_approval")
    assert first["image_url"] and first["cost_usd"] == 0.55 and second["image_url"] is None
    assert FakeMedia.calls == 1
    assert db.get_approval_request(first["approval_request_id"])["items"][0]["media"] == "single"


def test_a_used_up_plan_pauses_autopilot(user, ready, monkeypatch):
    _turn_on(user)

    def blocked(uid, data):
        raise content_pipeline.PostBlocked({"error": "No posts left this month."}, 403)

    monkeypatch.setattr(content_pipeline, "generate_post", blocked)
    assert ap.run_for_user(user["id"], NOW) == 0
    assert db.get_autopilot_settings(user["id"])["paused_reason"] == "No posts left this month."
    assert not _posts(user, "preparing", "failed")  # the day stays on the plan
    assert user["id"] not in db.autopilot_user_ids()


def test_two_failed_preparations_in_a_row_pause_it(user, ready, monkeypatch):
    _turn_on(user)

    def broken(uid, data):
        raise RuntimeError("the AI service didn't answer")

    monkeypatch.setattr(content_pipeline, "generate_post", broken)
    ap.run_for_user(user["id"], NOW)
    settings = db.get_autopilot_settings(user["id"])
    assert settings["paused_reason"] and "couldn't be prepared" in settings["paused_reason"]
    assert len(_posts(user, "failed")) == 2
    assert any(e["action"] == "autopilot.paused" for e in db.list_audit_log(10, user["id"]))
    assert ap.run_for_user(user["id"], NOW) == 0  # paused: nothing more is tried


# ── Approval decides what is scheduled ───────────────────────────────────


def test_approving_schedules_the_post_at_its_slot(app, user, ready):
    _turn_on(user)
    ap.run_for_user(user["id"], NOW)
    post = _posts(user, "awaiting_approval")[0]
    res = _client(app, user).post(f"/api/approval-requests/{post['approval_request_id']}/decide", json={"decision": "approved"})
    body = res.get_json()
    assert body["success"] and body["autopilot"]["status"] == "scheduled"
    scheduled = db.get_scheduled_post(user["id"], body["autopilot"]["scheduled_post_id"])
    assert scheduled["platforms"] == ["linkedin"] and scheduled["status"] == "pending"
    when = datetime.fromisoformat(scheduled["scheduled_at"])
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)  # stored in UTC
    assert when == _nine_ist(1)  # 09:00 in India
    assert db.cancel_scheduled_post(user["id"], scheduled["id"])  # leave nothing due for other tests


def test_rejected_skipped_and_late_posts_are_not_scheduled(app, user, ready):
    _turn_on(user)
    ap.run_for_user(user["id"], NOW)
    first, second = _posts(user, "awaiting_approval")
    client = _client(app, user)
    assert client.post(f"/api/approval-requests/{first['approval_request_id']}/decide",
                       json={"decision": "rejected"}).get_json()["autopilot"]["status"] == "rejected"
    # its slot passes before anyone approves it
    assert ap.expire_overdue(datetime.fromisoformat(second["slot_at"]) + timedelta(minutes=1)) >= 1
    assert db.get_autopilot_post(user["id"], second["id"])["status"] == "expired"
    # a planned day skipped on the page
    planned = _posts(user, "planned")[0]
    res = client.post(f"/api/autopilot/posts/{planned['id']}/skip").get_json()
    assert res["post"]["status"] == "rejected"


def test_turning_it_off_withdraws_what_waits_for_approval(user, ready):
    _turn_on(user)
    ap.run_for_user(user["id"], NOW)
    ap.save(user["id"], {"enabled": False}, now=NOW)
    posts = _posts(user)
    assert posts and all(p["status"] == "cancelled" for p in posts)  # the planned days are gone
    assert all(db.get_approval_request(p["approval_request_id"])["status"] == "rejected" for p in posts)
    assert ap.run_for_user(user["id"], NOW) == 0


def test_someone_elses_post_cannot_be_skipped(app, user, ready):
    _turn_on(user)
    post = _posts(user, "planned")[0]
    stranger = db.create_user("Ravi", f"{uuid.uuid4().hex[:8]}@example.com", "h")
    assert _client(app, stranger).post(f"/api/autopilot/posts/{post['id']}/skip").status_code == 400
    assert app.test_client().get("/api/autopilot").status_code == 401
    assert db.get_autopilot_post(user["id"], post["id"])["status"] == "planned"


# ── The pages ────────────────────────────────────────────────────────────


def test_the_page_shows_the_plan_and_what_waits(app, user, ready):
    _turn_on(user)
    ap.run_for_user(user["id"], NOW)
    state = ap.state(user["id"], NOW)
    assert len(state["needs_ok"]) == 2 and state["needs_ok"][0]["caption"]
    assert state["next"]["slot_date"] == _day(1) and state["week"]["goal"] == 7
    assert state["image_use"] == {"used": 0, "cap": 12, "price": state["image_use"]["price"]}
    page = _client(app, user).get("/autopilot")
    assert page.status_code == 200 and b"js/autopilot.js" in page.data


def test_settings_links_to_autopilot_and_the_calendar_shows_it(app, user):
    client = _client(app, user)
    html = client.get("/settings").get_data(as_text=True)
    for marker in ('id="autopilot-tab"', 'id="autopilot-pane"', 'id="aptCard"', 'href="/autopilot"', "js/autopilot_settings.js"):
        assert marker in html, marker
    assert 'id="calAutopilot" href="/autopilot"' in client.get("/calendar").get_data(as_text=True)


def test_settings_has_a_grouped_sidebar_with_badges_and_search(app, user):
    html = _client(app, user).get("/settings").get_data(as_text=True)
    assert 'id="settingsSearch"' in html and html.count('class="st-group"') == 3
    publishing, account = html.index(">Publishing<"), html.index(">Account<")
    assert publishing < html.index('id="connected-accounts-tab"') < html.index('id="autopilot-tab"') < account
    assert account < html.index('id="plan-tab"') < html.index('id="security-tab"') < html.index('id="notifications-tab"')
    for tab in ("connected-accounts-tab", "scheduled-posts-tab", "autopilot-tab", "plan-tab", "security-tab"):
        assert f'data-badge-for="{tab}"' in html, tab
    assert html.count('role="tab"') == 7  # every tab kept, so /settings#... links still work


def test_the_calendar_marks_autopilot_posts(app, user, ready):
    from services import calendar_service

    _turn_on(user)
    ap.run_for_user(user["id"], NOW)
    first, second = _posts(user, "awaiting_approval")
    _client(app, user).post(f"/api/approval-requests/{first['approval_request_id']}/decide", json={"decision": "approved"})

    days = {}
    for slot_date in (first["slot_date"], second["slot_date"]):  # the two days may be in different months
        month = datetime.fromisoformat(slot_date)
        days.update({d["date"]: d for d in calendar_service.calendar_month(user["id"], month.year, month.month, IST, NOW)["days"]})
    waiting = days[second["slot_date"]]
    assert waiting["autopilot"]["approval_request_id"] == second["approval_request_id"] and waiting["suggestion"] is None
    scheduled = days[first["slot_date"]]["scheduled"]
    assert len(scheduled) == 1 and scheduled[0]["autopilot"] is True
    assert days[first["slot_date"]]["autopilot"] is None  # approved: now an ordinary scheduled post
    db.cancel_scheduled_post(user["id"], db.get_autopilot_post(user["id"], first["id"])["scheduled_post_id"])


def test_the_help_center_explains_autopilot(app, user):
    page = _client(app, user).get("/help/autopilot")
    assert page.status_code == 200 and b"Nothing is published until you approve it" in page.data
