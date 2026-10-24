"""Best times to post (services/best_time_service.py) and ideas that learn
from the user's best-performing posts (services/trend_service.py)."""

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

import db
from services import best_time_service

IST = 330
WED_8AM_IST = datetime(2026, 10, 14, 2, 30, tzinfo=timezone.utc)


def _user():
    return db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")


def _published(user, platform, local_when, engagement, title="A post"):
    """A published post with results, at a local (IST) time."""
    post_id = f"{platform}_{uuid.uuid4().hex[:8]}"
    db.register_published_post(user["id"], platform, post_id, None, title, "scheduled")
    with Session(db.engine) as session:
        row = session.query(db.PublishedPost).filter(db.PublishedPost.platform_post_id == post_id).one()
        row.published_at = local_when.astimezone(timezone.utc).replace(tzinfo=None)
        row.metrics = json.dumps({"likes": engagement})
        row.engagement = engagement
        row.metrics_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()


def test_common_starting_points_until_there_are_enough_posts():
    best = best_time_service.best_times(_user()["id"], IST, WED_8AM_IST)
    assert (
        best["linkedin"]["source"] == "typical"
        and best["linkedin"]["label"] == "Tue, Wed, Thu around 9 am, 10 am, 12 pm"
    )
    slot = datetime.fromisoformat(best["linkedin"]["next_slot"])
    assert slot.isoformat() == "2026-10-14T09:00:00+05:30"  # Wednesday 9 am, more than an hour away


def test_learns_from_the_users_own_results():
    user = _user()
    tz = timezone(timedelta(minutes=IST))
    base = datetime(2026, 9, 1, tzinfo=tz)  # a Tuesday
    for week in range(4):  # Saturday 6 pm posts do best; Tuesday 9 am ones poorly
        _published(user, "instagram", base + timedelta(weeks=week, days=4, hours=18), 120)
        _published(user, "instagram", base + timedelta(weeks=week, hours=9), 10)
    best = best_time_service.best_times(user["id"], IST, WED_8AM_IST)
    insta = best["instagram"]
    assert insta["source"] == "your_posts" and insta["posts"] == 8
    assert insta["hours"][0] == 18 and insta["days"][0] == 5  # Saturday
    assert datetime.fromisoformat(insta["next_slot"]).isoformat() == "2026-10-17T18:00:00+05:30"
    assert best["linkedin"]["source"] == "typical"  # other platforms unaffected


def test_next_slot_skips_times_less_than_an_hour_away():
    tz = timezone(timedelta(minutes=IST))
    now = datetime(2026, 10, 13, 8, 30, tzinfo=tz)  # Tuesday 8:30
    assert best_time_service.next_slot([1], [9], now).isoformat() == "2026-10-20T09:00:00+05:30"


def test_endpoint(monkeypatch):
    from app import create_app

    app = create_app("development")
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = _user()["id"]
    body = client.get("/api/best-times?tz=330").get_json()
    assert set(body["platforms"]) == {"linkedin", "facebook", "instagram", "youtube"}


def test_ideas_prompt_names_what_worked_before(monkeypatch):
    from services import trend_service

    user = _user()
    _published(
        user, "linkedin", datetime.now(timezone.utc) - timedelta(days=3), 90, title="Why SIPs beat timing the market"
    )
    _published(user, "linkedin", datetime.now(timezone.utc) - timedelta(days=5), 0, title="A quiet post")
    lines = trend_service._past_winners(user["id"])
    assert '"Why SIPs beat timing the market" (90 likes, comments and shares)' in lines and "quiet" not in lines
    assert trend_service._past_winners(_user()["id"]) == "none"
