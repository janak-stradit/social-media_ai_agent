"""Content Calendar (services/calendar_service.py): the weekly goal, the
streak, and what lands on each day. Nothing external is called."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

import db
from services import calendar_service

IST = 330
WED = datetime(2026, 10, 14, 6, 30, tzinfo=timezone.utc)  # Wednesday noon in India; the week starts Mon 12 Oct


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def user():
    return db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")


def _post(user, when, content=None, story="A post about SIPs"):
    run_id = db.save_run(story=story, tone="", platforms=["linkedin"], content=content or {"linkedin": {"caption": "Hi"}},
                         user_id=user["id"])
    with Session(db.engine) as session:
        session.query(db.RunHistory).filter(db.RunHistory.id == run_id).update({"created_at": when.replace(tzinfo=None)})
        session.commit()
    return run_id


def _day(data, iso):
    return next(d for d in data["days"] if d["date"] == iso)


def test_progress_counts_posts_not_refinements(user):
    first = _post(user, WED - timedelta(days=1))
    _post(user, WED - timedelta(hours=2), {"linkedin": {}, "_meta": {"refined_from_run_id": first}})  # an edit of it
    _post(user, WED - timedelta(hours=1), {"linkedin": {}, "_meta": {"version_of": first}})  # a regenerate

    progress = calendar_service.week_progress(user["id"], IST, WED)

    assert (progress["goal"], progress["done"], progress["remaining"], progress["goal_met"]) == (3, 1, 2, False)
    assert progress["week_start"] == "2026-10-12" and progress["today"] == "2026-10-14"
    assert [d["count"] for d in progress["days"]] == [0, 1, 0, 0, 0, 0, 0]


def test_days_follow_the_users_own_clock(user):
    _post(user, datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc))  # Sunday evening UTC = Monday 1:30 am in India
    assert calendar_service.week_progress(user["id"], IST, WED)["done"] == 1
    assert calendar_service.week_progress(user["id"], 0, WED)["done"] == 0


def test_streak_counts_weeks_the_goal_was_met(user):
    db.set_weekly_post_goal(user["id"], 1)
    _post(user, WED - timedelta(days=7))   # last week
    _post(user, WED - timedelta(days=14))  # the week before
    # three weeks ago: nothing
    _post(user, WED - timedelta(days=28))

    assert calendar_service.week_progress(user["id"], IST, WED)["streak_weeks"] == 2  # this week isn't over yet
    _post(user, WED - timedelta(hours=1))
    assert calendar_service.week_progress(user["id"], IST, WED)["streak_weeks"] == 3


def test_calendar_places_posts_and_suggests_the_slots_still_needed(user, monkeypatch):
    monkeypatch.setattr(calendar_service, "PREFERRED_DAYS", {**calendar_service.PREFERRED_DAYS})
    _post(user, WED - timedelta(days=2), story="Monday's   post\nabout charts")

    data = calendar_service.calendar_month(user["id"], 2026, 10, IST, WED)

    assert data["days"][0]["date"] == "2026-09-28" and data["days"][-1]["date"] == "2026-11-01"  # whole weeks
    assert _day(data, "2026-10-12")["posts"][0]["title"] == "Monday's post about charts"
    this_week = [d["date"] for d in data["days"] if d["suggestion"] and d["date"] < "2026-10-19"]
    assert this_week == ["2026-10-14", "2026-10-16"]  # 2 still needed: today (Wed) and Fri, never a past day
    next_week = [d["date"] for d in data["days"] if d["suggestion"] and "2026-10-19" <= d["date"] < "2026-10-26"]
    assert next_week == ["2026-10-19", "2026-10-21", "2026-10-23"]  # Mon / Wed / Fri for a goal of 3
    assert _day(data, "2026-10-14")["suggestion"]["idea"] is None  # no saved feed: a slot without an idea


def test_no_more_slots_this_week_once_the_goal_is_met(user):
    db.set_weekly_post_goal(user["id"], 1)
    _post(user, WED - timedelta(days=1))
    data = calendar_service.calendar_month(user["id"], 2026, 10, IST, WED)
    assert not [d for d in data["days"] if d["suggestion"] and d["date"] < "2026-10-19"]
    assert data["progress"]["goal_met"] is True


def test_slots_get_ideas_from_the_saved_feed_with_occasions_before_their_date(user, monkeypatch):
    with Session(db.engine) as session:
        session.add(db.UserBrandProfile(user_id=user["id"], website="https://example.com",
                                        industry_category="investment_wealth", compliance_regions=json.dumps(["India"])))
        session.commit()
    feed = {"groups": {
        "trending": [{"title": "F&O rules", "prompt": "Trend brief.", "platform": "instagram", "format": "image"}],
        "playbook": [{"title": "Read a chart", "prompt": "Chart brief.", "platform": "linkedin", "format": "text"}],
        "dates": [{"title": "Dussehra checklist", "prompt": "Dussehra brief.", "platform": "facebook", "format": "image",
                   "occasion": "Dussehra / Vijayadashami (2026-10-20, India)"}],
    }}
    from services import festival_service, trend_service

    monkeypatch.setattr(trend_service, "cached_idea_feed", lambda user_id: feed)
    monkeypatch.setattr(festival_service, "get_upcoming_festivals", lambda days_ahead=60, today=None: [
        {"name": "Dussehra / Vijayadashami", "date": "2026-10-20", "region": "India", "days_until": 6}])

    data = calendar_service.calendar_month(user["id"], 2026, 10, IST, WED)

    assert _day(data, "2026-10-20")["occasions"] == [{"name": "Dussehra / Vijayadashami", "region": "India"}]
    assert _day(data, "2026-10-19")["suggestion"]["idea"]["title"] == "Dussehra checklist"  # the last slot before it
    assert _day(data, "2026-10-14")["suggestion"]["idea"]["title"] == "F&O rules"
    assert _day(data, "2026-10-15")["suggestion"]["idea"]["prompt"] == "Chart brief."
    assert _day(data, "2026-10-16")["suggestion"]["idea"] is None  # more slots than ideas: an open slot


def test_endpoints(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]

    assert client.get("/api/me/goal?tz=330").get_json()["progress"]["goal"] == 3
    assert client.put("/api/me/goal?tz=330", json={"weekly_post_goal": 5}).get_json()["progress"]["goal"] == 5
    assert client.put("/api/me/goal", json={"weekly_post_goal": 9}).status_code == 400
    month = client.get("/api/calendar?month=2026-10&tz=330").get_json()
    assert month["success"] and month["month"] == "2026-10" and month["progress"]["goal"] == 5
    assert client.get("/api/calendar?month=nonsense").status_code == 400
