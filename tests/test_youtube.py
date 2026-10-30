"""YouTube (services/youtube_service.py): token renewal, a video through
approvals and scheduling, publishing, and its results (views, likes,
comments, subscribers). Google is faked - nothing is sent."""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

import db
from config import Config
from services import metrics_service, youtube_service


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self._body, self.status_code, self.headers = body, status, headers or {}
        self.content = b"x"

    def json(self):
        return self._body


@pytest.fixture
def google(monkeypatch):
    """Token renewals, uploads and the Data API; state["refuse"] makes renewal fail for good."""
    state = {"refuse": False, "renewals": 0, "uploads": [], "token": "ya29.fresh"}

    def post(url, data=None, json=None, headers=None, timeout=None):
        if url == youtube_service.TOKEN_URL:
            if state["refuse"]:
                return FakeResponse({"error": "invalid_grant"}, 400)
            state["renewals"] += 1
            return FakeResponse({"access_token": state["token"], "expires_in": 3599})
        state["uploads"].append({"title": json["snippet"]["title"], "description": json["snippet"]["description"],
                                 "auth": headers["Authorization"]})
        return FakeResponse({}, headers={"Location": "https://upload.example/session"})

    def put(url, data=None, headers=None, timeout=None):
        return FakeResponse({"id": "vid123"})

    def get(url, params=None, headers=None, timeout=None):
        if url.endswith("/videos"):
            return FakeResponse({"items": [{"statistics": {"viewCount": "1540", "likeCount": "88", "commentCount": "7"}}]})
        return FakeResponse({"items": [{"statistics": {"subscriberCount": "2310", "hiddenSubscriberCount": False}}]})

    monkeypatch.setattr(youtube_service.requests, "post", post)
    monkeypatch.setattr(youtube_service.requests, "put", put)
    monkeypatch.setattr(youtube_service.requests, "get", get)
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "client")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "secret")
    return state


def _user():
    return db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash")


def _channel(user, expires_in_minutes=60, refresh_token="1//refresh"):
    db.save_social_account(user["id"], "youtube", "Clinic Channel", account_id=f"UC{uuid.uuid4().hex[:10]}",
                           access_token="ya29.old", refresh_token=refresh_token, connected_via="oauth",
                           token_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)).replace(tzinfo=None))
    with Session(db.engine) as session:
        return session.query(db.SocialAccount).filter_by(user_id=user["id"], platform="youtube").one()


def _video():
    name = f"gen_youtube_{uuid.uuid4().hex[:8]}.mp4"
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    with open(os.path.join(Config.UPLOAD_FOLDER, name), "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42")
    return f"/static/uploads/{name}"


def test_tokens_about_to_run_out_are_renewed(google):
    soon, later = _user(), _user()
    _channel(soon, expires_in_minutes=5)
    _channel(later, expires_in_minutes=50)
    youtube_service.refresh_tokens()
    with Session(db.engine) as session:
        renewed = session.query(db.SocialAccount).filter_by(user_id=soon["id"], platform="youtube").one()
        untouched = session.query(db.SocialAccount).filter_by(user_id=later["id"], platform="youtube").one()
        assert renewed.access_token == "ya29.fresh" and untouched.access_token == "ya29.old"
        assert renewed.token_expires_at > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=55)


def test_a_refused_refresh_token_asks_the_user_to_reconnect(google):
    user = _user()
    _channel(user, expires_in_minutes=-30)  # ran out, but renewable: still connected
    assert db.get_user_social_accounts(user["id"])[0]["expired"] is False
    from services.publish_service import connections

    assert connections(user["id"])["youtube"] == "Clinic Channel"
    google["refuse"] = True
    youtube_service.refresh_tokens()
    assert db.get_user_social_accounts(user["id"])[0]["expired"] is True
    with pytest.raises(youtube_service.YouTubeError, match="connect it again"):
        youtube_service.fresh_token(user["id"])
    renewals = google["renewals"]
    youtube_service.refresh_tokens()  # given up on: not asked again every loop
    assert google["renewals"] == renewals


def test_youtube_items_need_a_video_of_ours(google):
    from services.approval_bundle_service import BundleError, normalize_items

    video = _video()
    item = normalize_items([{"platform": "youtube", "caption": "How we cut no-shows", "hashtags": ["Shorts", "#Clinics"],
                             "video": video, "images": ["/static/uploads/whatever.png"]}], Config.UPLOAD_FOLDER)[0]
    assert item["media"] == "video" and item["video"] == video and item["images"] == []
    assert item["hashtags"] == ["#Shorts", "#Clinics"] and item["decision"] == "pending"
    for bad in ("/static/uploads/missing.mp4", "https://elsewhere.example/v.mp4", video.replace(".mp4", ".png")):
        with pytest.raises(BundleError, match="YouTube: the video could not be found"):
            normalize_items([{"platform": "youtube", "caption": "Hi", "video": bad}], Config.UPLOAD_FOLDER)
    # without a video it can still be sent for review, as before (publishing it then says it needs one)
    review = normalize_items([{"platform": "youtube", "caption": "Script for our next video"}], Config.UPLOAD_FOLDER)[0]
    assert review["media"] == "none" and "video" not in review


def test_publishing_uploads_the_video_with_a_fresh_token(google):
    from services.publish_service import publish_item

    user = _user()
    _channel(user, expires_in_minutes=2)  # renewed before the upload
    caption = "How Meera's clinic cut no-shows by a third\n\nReminders patients actually read."
    out = publish_item(user["id"], {"platform": "youtube", "caption": caption, "hashtags": ["#Shorts"], "media": "video",
                                    "video": _video()}, Config.UPLOAD_FOLDER)
    assert out == {"platform": "youtube", "account": "Clinic Channel", "post_id": "vid123",
                   "permalink": "https://www.youtube.com/watch?v=vid123", "format": "video"}
    upload = google["uploads"][0]
    assert upload["title"] == "How Meera's clinic cut no-shows by a third"
    assert upload["description"].endswith("#Shorts") and upload["auth"] == "Bearer ya29.fresh"


def test_a_long_first_line_is_cut_to_a_youtube_title():
    title, description = youtube_service.title_and_description({"caption": "x" * 140 + " <b>", "hashtags": []})
    assert len(title) == 100 and title.endswith("…") and "<" not in description


def test_scheduling_a_youtube_post(google):
    from services import schedule_service

    user = _user()
    _channel(user)
    post = schedule_service.create(user["id"], [{"platform": "youtube", "caption": "New video", "video": _video()}],
                                   (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), Config.UPLOAD_FOLDER)
    assert post["platforms"] == ["youtube"] and post["items"][0]["media"] == "video"
    assert db.cancel_scheduled_post(user["id"], post["id"])  # other tests' publish_due runs mustn't find it
    with pytest.raises(schedule_service.ScheduleError, match="YouTube posts need a video"):
        schedule_service.create(user["id"], [{"platform": "youtube", "caption": "No video"}],
                                (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), Config.UPLOAD_FOLDER)


def test_views_likes_comments_and_subscribers(google):
    assert metrics_service.FETCHERS["youtube"]("vid123", "tok") == {"views": 1540, "likes": 88, "comments": 7}
    assert metrics_service.FOLLOWER_FETCHERS["youtube"]("UC1", "tok") == 2310


def test_a_video_counts_as_its_own_format_in_what_works():
    from services.insights_service import traits

    assert traits({"caption": "Watch this", "media": "video", "video": "/static/uploads/v.mp4"})["format"] == "video"
