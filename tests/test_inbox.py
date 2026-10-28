"""The Inbox (services/comment_service.py): fetching comments per platform,
the schedule, suggested replies (a fake AI) and their cost, posting only
what the person approved, and the endpoints. The platforms are faked."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

import db
from services import comment_service


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self._body, self.status_code, self.content, self.headers = body, status, b"x", headers or {}

    def json(self):
        return self._body


@pytest.fixture
def platforms(monkeypatch):
    """Comments by platform; replies are recorded."""
    state = {"posted": []}

    def request(method, url, params=None, timeout=None):
        tag = url.rsplit("/", 2)[-2][-6:]  # the post: comment ids are unique per platform, as on the real ones
        if method == "POST":
            state["posted"].append((url, params.get("message")))
            return FakeResponse({"id": "reply_1"})
        if "fb_" in url:
            return FakeResponse({"data": [
                {"id": "fbc_1_" + tag, "from": {"id": "u9", "name": "Arjun Rao"}, "message": "Does this work with our software?",
                 "created_time": "2026-10-28T10:00:00+0000"},
                {"id": "fbc_2_" + tag, "from": {"id": "page_1", "name": "Clinic"}, "message": "Thanks all!",  # the page itself
                 "created_time": "2026-10-28T11:00:00+0000"},
            ]})
        return FakeResponse({"data": [
            {"id": "igc_1_" + tag, "username": "priya.s", "text": "SMS or WhatsApp?", "timestamp": "2026-10-28T10:05:00+0000"},
            {"id": "igc_2_" + tag, "username": "clinic", "text": "Both!", "timestamp": "2026-10-28T10:06:00+0000"},
        ]})

    def get(url, headers=None, params=None, timeout=None):
        tag = url.rsplit("/", 2)[-2][-6:]
        return FakeResponse({"elements": [
            {"$URN": f"urn:li:comment:(urn:li:activity:{tag},77)", "actor": "urn:li:person:x", "message": {"text": "Great post"},
             "created": {"time": 1793200000000}},
            {"$URN": f"urn:li:comment:(urn:li:activity:{tag},78)", "actor": "urn:li:organization:5", "message": {"text": "Thanks!"},
             "created": {"time": 1793200100000}},
        ]})

    monkeypatch.setattr(comment_service.requests, "request", request)
    monkeypatch.setattr(comment_service.requests, "get", get)
    from services import publish_service

    monkeypatch.setattr(publish_service, "accounts", lambda user_id: {
        "facebook": {"token": "tok", "expired": False, "account_id": "page_1", "name": "Clinic"},
        "instagram": {"token": "tok", "expired": False, "account_id": "ig_1", "name": "@clinic"},
        "linkedin": {"token": "tok", "expired": False, "account_id": "urn:li:organization:5", "name": "Clinic"},
    })
    return state


class FakeLLM:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        self.calls += 1
        return self.answer, {"cost_usd": 0.002}


def _user(**extra):
    return db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash", **extra)


def _published(user, platform, hours_ago=1):
    prefix = {"facebook": "fb_", "instagram": "ig_", "linkedin": "urn:li:share:"}[platform]
    post_id = f"{prefix}{uuid.uuid4().hex[:8]}"
    db.register_published_post(user["id"], platform, post_id, "https://example.com/p", "No-shows cut by a third", "scheduled", 1)
    with Session(db.engine) as session:
        row = session.query(db.PublishedPost).filter(db.PublishedPost.platform_post_id == post_id).one()
        row.published_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).replace(tzinfo=None)
        session.commit()
        return row.id


def _mine(user, status="new"):
    return db.list_post_comments(user["id"], status)


def test_comments_are_fetched_without_the_accounts_own(platforms):
    user = _user()
    for platform in ("facebook", "instagram", "linkedin"):
        _published(user, platform)
    comment_service.refresh_due()
    texts = {c["platform"]: c for c in _mine(user)["comments"]}
    assert set(texts) == {"facebook", "instagram", "linkedin"} and len(_mine(user)["comments"]) == 3
    assert texts["facebook"]["author"] == "Arjun Rao" and texts["instagram"]["author"] == "priya.s"
    assert texts["linkedin"]["author"] == "LinkedIn member" and texts["linkedin"]["comment_id"].endswith(",77)")
    assert _mine(user)["counts"]["new"] == 3


def test_comment_schedule_and_no_duplicates(platforms):
    user = _user()
    young, older, old = _published(user, "instagram", 2), _published(user, "facebook", 30), _published(user, "facebook", 24 * 9)
    now = datetime.now(timezone.utc)
    due = {p["id"] for p in db.published_posts_due_for_comments(now)}
    assert {young, older} <= due and old not in due  # a week, then no more
    comment_service.refresh_due(now)
    later = {p["id"] for p in db.published_posts_due_for_comments(now + timedelta(minutes=31))}
    assert young in later and older not in later  # every 30 minutes on day one, every 3 hours after
    with Session(db.engine) as session:  # fetched again: nothing new
        session.query(db.PublishedPost).filter(db.PublishedPost.id == young).update({"comments_at": None})
        session.commit()
    comment_service.refresh_due(now)
    assert len([c for c in _mine(user)["comments"] if c["post_id"] == young]) == 1


def test_a_suggested_reply_keeps_placeholders_and_its_cost_counts(platforms):
    user = _user()
    _published(user, "instagram")
    comment_service.refresh_due()
    comment = _mine(user)["comments"][0]
    llm = FakeLLM({"kind": "question", "reply": "Hi! Plans start at [YOUR PRICE] a month.", "note": "Fill in the price."})
    suggestion, cost = comment_service.suggest(user["id"], comment, llm=llm)
    assert suggestion == {"kind": "question", "draft": "Hi! Plans start at [YOUR PRICE] a month.", "note": "Fill in the price."}
    assert cost == 0.002 and llm.calls == 1  # no compliance rules for this brand: one call
    before = db.get_user_usage_stats(user["id"])["total_cost_usd"]
    db.update_post_comment(user["id"], comment["id"], add_cost=cost, **suggestion)
    assert round(db.get_user_usage_stats(user["id"])["total_cost_usd"] - before, 6) == 0.002
    assert comment_service.suggest(user["id"], comment, llm=FakeLLM({"kind": "spam", "reply": "Thanks!"}))[0]["kind"] == "other"
    with pytest.raises(comment_service.ReplyError):
        comment_service.suggest(user["id"], comment, llm=FakeLLM({"kind": "praise", "reply": ""}))


def test_only_an_approved_complete_reply_is_posted(platforms):
    user = _user()
    _published(user, "facebook")
    comment_service.refresh_due()
    comment = _mine(user)["comments"][0]
    account = {"token": "tok", "expired": False}
    with pytest.raises(comment_service.ReplyError, match=r"Fill in \[YOUR PRICE\]"):
        comment_service.post_reply(comment, "It costs [YOUR PRICE].", account)
    with pytest.raises(comment_service.ReplyError, match="Connect"):
        comment_service.post_reply(comment, "Yes it does!", None)
    assert platforms["posted"] == []
    comment_service.post_reply(comment, "Yes it does!", account)
    assert platforms["posted"] == [(platforms["posted"][0][0], "Yes it does!")] and "/comments" in platforms["posted"][0][0]


def test_inbox_endpoints(app, platforms, monkeypatch):
    user, other = _user(), _user()
    _published(user, "instagram")
    comment_service.refresh_due()
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    listed = client.get("/api/inbox").get_json()
    comment_id = listed["comments"][0]["id"]
    assert listed["counts"]["new"] == 1

    monkeypatch.setattr(comment_service, "suggest", lambda uid, c: ({"kind": "question", "draft": "Both!", "note": None}, 0.001))
    body = client.post(f"/api/inbox/{comment_id}/suggest").get_json()
    assert body["comment"]["draft"] == "Both!" and body["comment"]["kind"] == "question"
    assert client.get("/api/inbox").get_json()["counts"]["kinds"] == {"question": 1}

    bad = client.post(f"/api/inbox/{comment_id}/reply", json={"text": "From [YOUR PRICE]"})
    assert bad.status_code == 400 and "Fill in" in bad.get_json()["error"]
    ok = client.post(f"/api/inbox/{comment_id}/reply", json={"text": "Both, you choose!"}).get_json()
    assert ok["comment"]["status"] == "replied" and ok["comment"]["reply"] == "Both, you choose!"
    assert client.post(f"/api/inbox/{comment_id}/reply", json={"text": "Again"}).status_code == 409
    assert client.get("/api/inbox?status=replied").get_json()["comments"][0]["id"] == comment_id

    with client.session_transaction() as s:  # someone else's comment
        s["user_id"] = other["id"]
    assert client.post(f"/api/inbox/{comment_id}/status", json={"status": "done"}).status_code == 404
    assert client.post(f"/api/inbox/{comment_id}/suggest").status_code == 404


def test_inbox_page(app):
    admin = _user(is_admin=True)  # an admin skips the onboarding steps that page logins go through
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = admin["id"]
    page = client.get("/inbox")
    assert page.status_code == 200 and b"inboxList" in page.data and b"js/inbox.js" in page.data


def test_a_private_reply_goes_to_the_commenter_within_7_days(platforms):
    user = _user()
    _published(user, "instagram")
    _published(user, "linkedin")
    comment_service.refresh_due()
    by_platform = {c["platform"]: c for c in _mine(user)["comments"]}
    ig, li = by_platform["instagram"], by_platform["linkedin"]
    account = {"token": "tok", "expired": False}
    now = datetime.fromisoformat(ig["commented_at"])
    with pytest.raises(comment_service.ReplyError, match="within 7 days"):
        comment_service.post_private_reply(ig, "Sent you the details!", account, now=now + timedelta(days=8))
    with pytest.raises(comment_service.ReplyError, match="Facebook and Instagram"):
        comment_service.post_private_reply(li, "Hello!", account, now=now)
    assert platforms["posted"] == []
    comment_service.post_private_reply(ig, "Sent you the details!", account, now=now + timedelta(days=1))
    url, _ = platforms["posted"][0]
    assert url.endswith("/me/messages")


def test_the_private_reply_endpoint_marks_it(app, platforms):
    user = _user()
    _published(user, "facebook")
    comment_service.refresh_due()
    comment = _mine(user)["comments"][0]
    with Session(db.engine) as session:  # a comment from just now, so it's inside the 7 days
        session.query(db.PostComment).filter(db.PostComment.id == comment["id"]).update(
            {"commented_at": datetime.now(timezone.utc).replace(tzinfo=None)})
        session.commit()
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    body = client.post(f"/api/inbox/{comment['id']}/reply", json={"text": "Thanks, we'll message you.", "private": True}).get_json()
    assert body["comment"]["status"] == "replied" and body["comment"]["replied_privately"] is True
