"""How published posts did (services/metrics_service.py): recording published
posts, fetching their numbers per platform, the refresh schedule and the
analytics endpoint. The platforms are faked."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

import db
from services import metrics_service


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


class FakeResponse:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.content = body, status, b"x"

    def json(self):
        return self._body


@pytest.fixture
def platforms(monkeypatch):
    """Facebook, Instagram and LinkedIn answers by URL; insights can be refused."""
    state = {"refuse_insights": False, "calls": []}

    def get(url, params=None, headers=None, timeout=None):
        state["calls"].append(url)
        if "/insights" in url:
            if state["refuse_insights"]:
                return FakeResponse({"error": {"message": "(#10) requires read_insights"}}, 400)
            if "ig_" in url:
                return FakeResponse(
                    {
                        "data": [
                            {"name": "reach", "values": [{"value": 900}]},
                            {"name": "saved", "values": [{"value": 12}]},
                            {"name": "shares", "values": [{"value": 4}]},
                            {"name": "views", "values": [{"value": 1500}]},
                        ]
                    }
                )
            return FakeResponse(
                {
                    "data": [
                        {"name": "post_impressions_unique", "values": [{"value": 700}]},
                        {"name": "post_impressions", "values": [{"value": 1100}]},
                    ]
                }
            )
        if "socialActions" in url:
            return FakeResponse({"likesSummary": {"totalLikes": 30}, "commentsSummary": {"aggregatedTotalComments": 6}})
        if "ig_" in url:
            return FakeResponse({"like_count": 80, "comments_count": 9})
        return FakeResponse(
            {
                "reactions": {"summary": {"total_count": 50}},
                "comments": {"summary": {"total_count": 7}},
                "shares": {"count": 3},
            }
        )

    monkeypatch.setattr(metrics_service.requests, "get", get)
    from services import publish_service

    monkeypatch.setattr(
        publish_service,
        "accounts",
        lambda user_id: {p: {"token": "tok", "expired": False} for p in ("facebook", "instagram", "linkedin")},
    )
    return state


def _user():
    return db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")


def _publish(user, platform, post_id, days_ago=0, title="A post about SIPs"):
    db.register_published_post(user["id"], platform, post_id, f"https://example.com/{post_id}", title, "scheduled", 1)
    if days_ago:
        with Session(db.engine) as session:
            session.query(db.PublishedPost).filter(db.PublishedPost.platform_post_id == post_id).update(
                {"published_at": (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(tzinfo=None)}
            )
            session.commit()


def _since(days=30):
    return datetime.now(timezone.utc) - timedelta(days=days)


def test_each_platform_gives_its_numbers(platforms):
    user = _user()
    _publish(user, "facebook", f"fb_{uuid.uuid4().hex[:6]}")
    _publish(user, "instagram", f"ig_{uuid.uuid4().hex[:6]}")
    _publish(user, "linkedin", f"urn:li:share:{uuid.uuid4().int % 10**12}")

    metrics_service.refresh_due()
    data = db.post_analytics(user["id"], _since())

    assert data["posts"] == 3
    assert data["platforms"]["facebook"]["likes"] == 50 and data["platforms"]["facebook"]["reach"] == 700
    assert data["platforms"]["instagram"]["saves"] == 12 and data["platforms"]["instagram"]["views"] == 1500
    assert data["platforms"]["linkedin"]["comments"] == 6
    assert data["totals"]["likes"] == 160 and data["engagement"] == 50 + 7 + 3 + 80 + 9 + 12 + 4 + 30 + 6
    assert data["top_posts"][0]["platform"] == "instagram"  # the most engagement
    assert data["engagement_rate"] == round(100 * data["engagement"] / 1600, 1)


def test_refused_insights_still_give_likes_and_comments(platforms):
    platforms["refuse_insights"] = True
    user = _user()
    _publish(user, "facebook", f"fb_{uuid.uuid4().hex[:6]}")
    metrics_service.refresh_due()
    data = db.post_analytics(user["id"], _since())
    assert data["totals"]["likes"] == 50 and data["totals"]["reach"] == 0 and data["engagement_rate"] is None


def test_refresh_schedule(platforms):
    user = _user()
    new_id, old_id, gone_id = (f"fb_{uuid.uuid4().hex[:6]}" for _ in range(3))
    _publish(user, "facebook", new_id)
    _publish(user, "facebook", old_id, days_ago=10)
    _publish(user, "facebook", gone_id, days_ago=45)  # past 30 days: not followed any more
    now = datetime.now(timezone.utc)

    due = {p["platform_post_id"] for p in db.published_posts_due_for_metrics(now)}
    assert {new_id, old_id} <= due and gone_id not in due
    metrics_service.refresh_due(now)
    later = {p["platform_post_id"] for p in db.published_posts_due_for_metrics(now + timedelta(hours=7))}
    assert new_id in later and old_id not in later  # young posts every 6 hours, older ones daily
    assert old_id in {p["platform_post_id"] for p in db.published_posts_due_for_metrics(now + timedelta(hours=25))}


def test_a_disconnected_account_is_noted_not_fatal(platforms, monkeypatch):
    from services import publish_service

    monkeypatch.setattr(publish_service, "accounts", lambda user_id: {})
    user = _user()
    _publish(user, "facebook", f"fb_{uuid.uuid4().hex[:6]}")
    assert metrics_service.refresh_due()["failed"] >= 1
    assert db.post_analytics(user["id"], _since())["top_posts"][0]["error"] == "The account is no longer connected."


def test_the_same_post_is_recorded_once():
    user = _user()
    post_id = f"fb_{uuid.uuid4().hex[:6]}"
    _publish(user, "facebook", post_id)
    _publish(user, "facebook", post_id)
    db.register_published_post(user["id"], "linkedin", None, None, "MCP post", "scheduled")  # no id: nothing to follow
    assert db.post_analytics(user["id"], _since())["posts"] == 1


def test_scheduled_publishing_records_the_post(monkeypatch, tmp_path):
    from services import publish_service, schedule_service

    user = _user()
    monkeypatch.setattr(
        publish_service,
        "publish_item",
        lambda uid, item, folder: {"post_id": f"fb_scheduled_{uuid.uuid4().hex[:8]}", "permalink": "https://fb.com/1", "account": "Page"},
    )
    monkeypatch.setattr(schedule_service, "_notify", lambda post, status, results: None)
    monkeypatch.setattr(db, "finish_scheduled_post", lambda *a, **k: None)
    schedule_service.publish(
        {"id": 99, "user_id": user["id"], "content": {"items": [{"platform": "facebook", "caption": "Diwali offer"}]}},
        str(tmp_path),
    )
    data = db.post_analytics(user["id"], _since())
    assert data["posts"] == 1 and data["top_posts"][0]["title"] == "Diwali offer"


def test_analytics_endpoint(app, platforms):
    user = _user()
    _publish(user, "instagram", f"ig_{uuid.uuid4().hex[:6]}")
    metrics_service.refresh_due()
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    body = client.get("/api/analytics?days=30").get_json()
    assert (
        body["analytics"]["posts"] == 1
        and body["analytics"]["totals"]["likes"] == 80
        and "all_posts" not in body["analytics"]
    )
