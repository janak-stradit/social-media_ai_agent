"""Autopilot, Review first (services/autopilot_service.py, AUTOPILOT_PLAN.md):
off by default; posts are prepared for open calendar days and sent for
approval; only approved posts are scheduled. The AI, the calendar and the
social accounts are faked."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

import db
from services import autopilot_service as ap
from services import content_pipeline

NOW = datetime(2026, 10, 27, 6, 0, tzinfo=timezone.utc)  # Tue 27 Oct, 11:30 in India
IST = 330


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
    """A brand profile, LinkedIn connected, two open days with ideas, and a fake writer."""
    db.save_user_brand_profile(user["id"], "https://acme.in", "Acme", "Logistics", None, None, [], [], [], [])
    from services import best_time_service, publish_service

    monkeypatch.setattr(publish_service, "connections", lambda uid: {"linkedin": "Acme Ltd", "facebook": None, "instagram": None})
    monkeypatch.setattr(best_time_service, "best_times", lambda uid, tz=0, now=None: {"linkedin": {"hours": [9]}})
    days = [{"date": date(2026, 10, 29), "idea": {"title": "Same-day delivery", "prompt": "Explain same-day delivery."}},
            {"date": date(2026, 10, 30), "idea": None},
            {"date": date(2026, 10, 31), "idea": {"title": "Weekend tips", "prompt": "Weekend shipping tips."}}]
    ideas = [{"title": "Meet the team", "prompt": "Introduce the warehouse team."}]
    monkeypatch.setattr(ap, "open_days", lambda uid, settings, now: (list(days), list(ideas)))
    written = []

    def fake_generate(uid, data):
        written.append(data)
        return {"success": True, "run_id": None, "content": {p: {
            "caption": {"primary_caption": f"{data['story']} Faster deliveries for every customer in Pune, every day."},
            "hashtags": {"hashtags": ["#Logistics"]}} for p in data["platforms"]}}

    monkeypatch.setattr(content_pipeline, "generate_post", fake_generate)
    from api import routes

    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [])
    return written


def _client(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def _turn_on(user):
    return ap.save(user["id"], {"enabled": True, "platforms": ["linkedin"], "days_ahead": 5, "tz_minutes": IST})


# ── The switch ───────────────────────────────────────────────────────────


def test_it_is_off_by_default(app, user):
    state = _client(app, user).get("/api/autopilot").get_json()["autopilot"]
    assert state["enabled"] is False and state["posts"] == []
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


def test_turning_it_on_is_audited_and_instagram_is_ignored(app, user, ready):
    state = _client(app, user).put("/api/autopilot", json={"enabled": True, "platforms": ["linkedin", "instagram"],
                                                           "days_ahead": 30, "tz_minutes": IST}).get_json()["autopilot"]
    assert state["enabled"] and state["platforms"] == ["linkedin"] and state["days_ahead"] == ap.MAX_DAYS_AHEAD
    assert any(e["action"] == "autopilot.turned_on" for e in db.list_audit_log(10, user["id"]))


# ── Preparing posts ──────────────────────────────────────────────────────


def test_open_days_get_posts_sent_for_approval_never_published(user, ready):
    _turn_on(user)
    assert ap.run_for_user(user["id"], NOW) == ap.MAX_POSTS_PER_RUN  # two per hour, not the whole week at once
    posts = sorted(db.list_autopilot_posts(user["id"]), key=lambda p: p["slot_date"])
    assert [p["slot_date"] for p in posts] == ["2026-10-29", "2026-10-30"]
    assert [p["idea_title"] for p in posts] == ["Same-day delivery", "Meet the team"]  # a day without an idea gets one
    assert all(p["status"] == "awaiting_approval" for p in posts)
    assert posts[0]["slot_at"] == "2026-10-29T03:30:00+00:00"  # 09:00 in India, the best hour
    assert ready[0]["selected_outputs"] == ["text"] and ready[0]["platforms"] == ["linkedin"]

    req = db.get_approval_request(posts[0]["approval_request_id"])
    assert req["status"] == "pending" and req["pipeline_client_id"] == f"autopilot-{posts[0]['id']}"
    assert req["items"][0]["platform"] == "linkedin" and req["items"][0]["media"] == "none"
    from db import ScheduledPost

    with Session(db.engine) as session:  # nothing is scheduled before it is approved
        assert session.query(ScheduledPost).filter(ScheduledPost.user_id == user["id"]).count() == 0

    # the next run fills the next day only; the same days are never prepared twice
    assert ap.run_for_user(user["id"], NOW) == 1
    assert len(db.list_autopilot_posts(user["id"])) == 3
    assert ap.run_for_user(user["id"], NOW) == 0


def test_a_used_up_plan_pauses_autopilot(user, ready, monkeypatch):
    _turn_on(user)

    def blocked(uid, data):
        raise content_pipeline.PostBlocked({"error": "No posts left this month."}, 403)

    monkeypatch.setattr(content_pipeline, "generate_post", blocked)
    assert ap.run_for_user(user["id"], NOW) == 0
    assert db.get_autopilot_settings(user["id"])["paused_reason"] == "No posts left this month."
    assert db.list_autopilot_posts(user["id"]) == []  # the day is free again
    assert user["id"] not in db.autopilot_user_ids()


# ── Approval decides what is scheduled ───────────────────────────────────


def test_approving_schedules_the_post_at_its_slot(app, user, ready):
    _turn_on(user)
    ap.run_for_user(user["id"], NOW)
    post = sorted(db.list_autopilot_posts(user["id"]), key=lambda p: p["slot_date"])[0]
    res = _client(app, user).post(f"/api/approval-requests/{post['approval_request_id']}/decide", json={"decision": "approved"})
    body = res.get_json()
    assert body["success"] and body["autopilot"]["status"] == "scheduled"
    scheduled = db.get_scheduled_post(user["id"], body["autopilot"]["scheduled_post_id"])
    assert scheduled["platforms"] == ["linkedin"] and scheduled["status"] == "pending"
    when = datetime.fromisoformat(scheduled["scheduled_at"])
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)  # stored in UTC
    assert when == datetime(2026, 10, 29, 3, 30, tzinfo=timezone.utc)  # 09:00 in India


def test_rejected_and_late_posts_are_not_scheduled(app, user, ready):
    _turn_on(user)
    ap.run_for_user(user["id"], NOW)
    first, second = sorted(db.list_autopilot_posts(user["id"]), key=lambda p: p["slot_date"])
    client = _client(app, user)
    assert client.post(f"/api/approval-requests/{first['approval_request_id']}/decide",
                       json={"decision": "rejected"}).get_json()["autopilot"]["status"] == "rejected"
    # its slot passes before anyone approves it
    assert ap.expire_overdue(datetime.fromisoformat(second["slot_at"]) + timedelta(minutes=1)) >= 1
    assert next(p for p in db.list_autopilot_posts(user["id"]) if p["id"] == second["id"])["status"] == "expired"


def test_turning_it_off_withdraws_what_waits_for_approval(user, ready):
    _turn_on(user)
    ap.run_for_user(user["id"], NOW)
    ap.save(user["id"], {"enabled": False})
    posts = db.list_autopilot_posts(user["id"])
    assert posts and all(p["status"] == "cancelled" for p in posts)
    assert all(db.get_approval_request(p["approval_request_id"])["status"] == "rejected" for p in posts)
    assert ap.run_for_user(user["id"], NOW) == 0


# ── The pages ────────────────────────────────────────────────────────────


def test_settings_has_the_switch_and_the_calendar_shows_it(app, user):
    client = _client(app, user)
    html = client.get("/settings").get_data(as_text=True)
    for marker in ('id="autopilot-tab"', 'id="autopilot-pane"', 'id="apEnabled"', 'role="switch"', "js/autopilot.js"):
        assert marker in html, marker
    assert 'id="apEnabled" aria-describedby="apStatus">' in html  # not checked: off by default
    assert 'id="calAutopilot"' in client.get("/calendar").get_data(as_text=True)


def test_settings_has_a_grouped_sidebar_with_badges_and_search(app, user):
    html = _client(app, user).get("/settings").get_data(as_text=True)
    assert 'id="settingsSearch"' in html and html.count('class="st-group"') == 3
    publishing, account = html.index(">Publishing<"), html.index(">Account<")
    assert publishing < html.index('id="connected-accounts-tab"') < html.index('id="autopilot-tab"') < account
    assert account < html.index('id="plan-tab"') < html.index('id="security-tab"') < html.index('id="notifications-tab"')
    for tab in ("connected-accounts-tab", "scheduled-posts-tab", "autopilot-tab", "plan-tab", "security-tab"):
        assert f'data-badge-for="{tab}"' in html, tab
    assert html.count('role="tab"') == 7  # every tab kept, so /settings#... links still work


# ── Phase 3: the calendar shows Autopilot's posts; repeated failures pause it ──


def test_the_calendar_marks_autopilot_posts(app, user, ready):
    from services import calendar_service

    _turn_on(user)
    ap.run_for_user(user["id"], NOW)
    first, second = sorted(db.list_autopilot_posts(user["id"]), key=lambda p: p["slot_date"])
    _client(app, user).post(f"/api/approval-requests/{first['approval_request_id']}/decide", json={"decision": "approved"})

    days = {d["date"]: d for d in calendar_service.calendar_month(user["id"], 2026, 10, IST, NOW)["days"]}
    waiting = days[second["slot_date"]]
    assert waiting["autopilot"]["approval_request_id"] == second["approval_request_id"] and waiting["suggestion"] is None
    scheduled = days[first["slot_date"]]["scheduled"]
    assert len(scheduled) == 1 and scheduled[0]["autopilot"] is True
    assert days[first["slot_date"]]["autopilot"] is None  # approved: now an ordinary scheduled post


def test_two_failed_preparations_in_a_row_pause_it(user, ready, monkeypatch):
    _turn_on(user)

    def broken(uid, data):
        raise RuntimeError("the AI service didn't answer")

    monkeypatch.setattr(content_pipeline, "generate_post", broken)
    ap.run_for_user(user["id"], NOW)
    settings = db.get_autopilot_settings(user["id"])
    assert settings["paused_reason"] and "couldn't be prepared" in settings["paused_reason"]
    assert all(p["status"] == "failed" for p in db.list_autopilot_posts(user["id"]))
    assert any(e["action"] == "autopilot.paused" for e in db.list_audit_log(10, user["id"]))
    assert ap.run_for_user(user["id"], NOW) == 0  # paused: nothing more is tried


def test_the_help_center_explains_autopilot(app, user):
    page = _client(app, user).get("/help/autopilot")
    assert page.status_code == 200 and b"Nothing is published until you approve it" in page.data
