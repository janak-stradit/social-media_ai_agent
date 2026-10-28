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


def _snapshot(post_id, age_hours, engagement):
    with Session(db.engine) as session:
        row = session.query(db.PublishedPost).filter(db.PublishedPost.platform_post_id == post_id).one()
        session.add(db.PostMetricSnapshot(post_id=row.id, age_hours=age_hours, metrics="{}", engagement=engagement))
        session.commit()


def test_each_fetch_keeps_a_snapshot_at_the_posts_age(platforms):
    user = _user()
    post_id = f"fb_{uuid.uuid4().hex[:6]}"
    _publish(user, "facebook", post_id, days_ago=1)
    metrics_service.refresh_due()
    with Session(db.engine) as session:
        snaps = (session.query(db.PostMetricSnapshot).join(db.PublishedPost, db.PublishedPost.id == db.PostMetricSnapshot.post_id)
                 .filter(db.PublishedPost.platform_post_id == post_id).all())
    assert len(snaps) == 1 and snaps[0].age_hours == 24 and snaps[0].engagement == 50 + 7 + 3


def test_a_post_is_fetched_again_as_it_turns_48_hours_old(platforms):
    user = _user()
    post_id = f"fb_{uuid.uuid4().hex[:6]}"
    _publish(user, "facebook", post_id)
    now = datetime.now(timezone.utc)
    metrics_service.refresh_due(now)
    with Session(db.engine) as session:  # last fetched at 45 hours old
        session.query(db.PublishedPost).filter(db.PublishedPost.platform_post_id == post_id).update(
            {"published_at": (now - timedelta(hours=45)).replace(tzinfo=None), "metrics_at": now.replace(tzinfo=None)})
        session.commit()
    due = lambda when: post_id in {p["platform_post_id"] for p in db.published_posts_due_for_metrics(when)}  # noqa: E731
    assert not due(now + timedelta(hours=2))  # 47 hours old
    assert due(now + timedelta(hours=3, minutes=1))  # just past 48: not 24 hours after the last fetch


def test_posts_are_compared_at_48_hours_against_the_usual(platforms):
    user = _user()
    ids = [f"ig_{uuid.uuid4().hex[:6]}" for _ in range(7)]
    for n, post_id in enumerate(ids[:6]):
        _publish(user, "instagram", post_id, days_ago=10 + n)
        _snapshot(post_id, 6, 10)
        _snapshot(post_id, 49, 100)  # the score: first snapshot at 48 hours or later
        _snapshot(post_id, 200, 900)  # later growth doesn't count
    _snapshot(ids[0], 52, 1)  # a second one past 48 hours is ignored too
    with Session(db.engine) as session:  # the best post: 250 at 48 hours
        row = session.query(db.PublishedPost).filter(db.PublishedPost.platform_post_id == ids[5]).one()
        session.query(db.PostMetricSnapshot).filter(db.PostMetricSnapshot.post_id == row.id,
                                                    db.PostMetricSnapshot.age_hours == 49).update({"engagement": 250})
        session.commit()
    _publish(user, "instagram", ids[6])  # still young: a score so far, no comparison yet
    _snapshot(ids[6], 3, 40)

    posts = {p["permalink"].rsplit("/", 1)[1]: p for p in db.post_analytics(user["id"], _since(60))["all_posts"]}
    assert posts[ids[5]]["score"] == 250 and posts[ids[5]]["score_final"]
    assert posts[ids[5]]["vs_usual"] == 2.5  # median of the other five is 100
    assert posts[ids[0]]["vs_usual"] == round(100 / 100, 1)
    assert posts[ids[6]]["score"] == 40 and not posts[ids[6]]["score_final"] and posts[ids[6]]["vs_usual"] is None


def test_no_comparison_until_enough_posts_are_scored(platforms):
    user = _user()
    ids = [f"ig_{uuid.uuid4().hex[:6]}" for _ in range(3)]
    for n, post_id in enumerate(ids):
        _publish(user, "instagram", post_id, days_ago=5 + n)
        _snapshot(post_id, 48, 100)
    posts = db.post_analytics(user["id"], _since())["all_posts"]
    assert all(p["score"] == 100 and p["vs_usual"] is None for p in posts)


def test_followers_are_counted_once_a_day(monkeypatch):
    user = _user()
    db.save_social_account(user["id"], "instagram", "@clinic", account_id="ig_17841", access_token="tok")
    db.save_social_account(user["id"], "linkedin", "Priya", account_id="urn:li:person:abc", access_token="tok")
    counts = iter([1200, 1260])
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return FakeResponse({"followers_count": next(counts)})

    monkeypatch.setattr(metrics_service.requests, "get", get)
    day1 = datetime.now(timezone.utc) - timedelta(days=3)
    mine = lambda now: [p for u, p in db.accounts_due_for_follower_count(now) if u == user["id"]]  # noqa: E731

    assert sorted(mine(day1)) == ["instagram", "linkedin"]
    metrics_service.refresh_followers(day1)
    assert mine(day1) == []  # a person's LinkedIn profile has no count, but isn't asked again today
    metrics_service.refresh_followers(day1)
    assert len(calls) == 1  # LinkedIn profile: no call at all

    metrics_service.refresh_followers(day1 + timedelta(days=2))
    growth = db.follower_growth(user["id"], day1 - timedelta(hours=1))
    assert growth == {"instagram": {"followers": 1260, "change": 60}}
    assert db.post_analytics(user["id"], _since())["followers"] == growth


def test_one_day_of_followers_has_no_change_yet(monkeypatch):
    user = _user()
    db.save_social_account(user["id"], "facebook", "Clinic", account_id="1234", access_token="tok")
    monkeypatch.setattr(metrics_service.requests, "get",
                        lambda url, params=None, headers=None, timeout=None: FakeResponse({"followers_count": 540}))
    metrics_service.refresh_followers()
    assert db.follower_growth(user["id"], _since(1)) == {"facebook": {"followers": 540, "change": None}}
