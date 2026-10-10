"""Instagram publishing through the Graph API: connecting the account behind a
Facebook Page, single-image and carousel posts, waiting for Meta to process
them, error messages, image preparation, and the Publish button's endpoint.
Meta's API is faked (FakeGraph) - nothing is sent anywhere."""

import json
import os
import uuid

import pytest
from PIL import Image

import db
from config import Config
from services import instagram_service as ig


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = status < 400

    def json(self):
        return self._body


class FakeGraph:
    """Answers the Graph API calls instagram_service makes, and records them."""

    def __init__(self):
        self.calls = []
        self.status_sequence = {}  # container id -> list of status codes to return in turn
        self.fail = {}  # (method, path-suffix) -> (status, error dict)
        self.next_id = 100

    def __call__(self, method, url, params=None, data=None, timeout=None):
        path = url.split("/v23.0/")[1] if "/v23.0/" in url else url
        body = params or data or {}
        self.calls.append((method, path, {k: v for k, v in body.items() if k != "access_token"}))
        for (m, suffix), (status, error) in self.fail.items():
            if m == method and path.endswith(suffix):
                return Resp(status, {"error": error})
        if method == "GET" and body.get("fields") == "instagram_business_account{id,username}":
            return Resp(200, {"instagram_business_account": {"id": "17841400000000001", "username": "acme.logistics"}, "id": path})
        if method == "GET" and body.get("fields") == "id,username":
            return Resp(400, {"error": {"message": "Unsupported get request", "code": 100}}) if path == "PAGE1" else \
                Resp(200, {"id": path, "username": "acme.logistics"})
        if method == "GET" and path.endswith("content_publishing_limit"):
            return Resp(200, {"data": [{"quota_usage": 3, "config": {"quota_total": 100}}]})
        if method == "GET" and body.get("fields") == "status_code,status":
            seq = self.status_sequence.get(path) or ["FINISHED"]
            code = seq.pop(0) if len(seq) > 1 else seq[0]
            return Resp(200, {"status_code": code, "status": f"{code}: details"})
        if method == "GET" and body.get("fields") == "permalink":
            return Resp(200, {"permalink": f"https://www.instagram.com/p/{path}/"})
        if method == "POST" and path.endswith("/media"):
            self.next_id += 1
            return Resp(200, {"id": f"C{self.next_id}"})
        if method == "POST" and path.endswith("/media_publish"):
            return Resp(200, {"id": "M900"})
        return Resp(404, {"error": {"message": f"unexpected {method} {path}", "code": 100}})


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(ig.requests, "request", fake)
    monkeypatch.setattr(ig, "_sleep", lambda s: None)
    monkeypatch.setattr(Config, "META_GRAPH_VERSION", "v23.0")
    monkeypatch.setattr(Config, "APP_BASE_URL", "https://avir.example.com")
    monkeypatch.setattr(Config, "PUBLIC_MEDIA_BASE_URL", "")
    return fake


@pytest.fixture
def uploads():
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    before = set(os.listdir(Config.UPLOAD_FOLDER))

    def make(size=(1080, 1080), fmt="PNG"):
        name = f"test_ig_{uuid.uuid4().hex[:8]}.{'jpg' if fmt == 'JPEG' else 'png'}"
        Image.new("RGB", size, "orange").save(os.path.join(Config.UPLOAD_FOLDER, name), fmt)
        return f"/static/uploads/{name}"

    yield make
    for name in set(os.listdir(Config.UPLOAD_FOLDER)) - before:
        if name.startswith(("test_ig_", "ig_", "test_slide_", "carousel_")):
            os.remove(os.path.join(Config.UPLOAD_FOLDER, name))


# ── Account ─────────────────────────────────────────────────────────────────


def test_finds_the_instagram_account_behind_a_page(graph):
    assert ig.account_from_page("PAGE1", "tok") == {"id": "17841400000000001", "username": "acme.logistics"}


def test_a_page_without_instagram_is_explained(graph, monkeypatch):
    def page_without_instagram(method, url, params=None, data=None, timeout=None):
        if params and params.get("fields") == "instagram_business_account{id,username}":
            return Resp(200, {"id": "PAGE2"})
        return graph(method, url, params, data, timeout)

    monkeypatch.setattr(ig.requests, "request", page_without_instagram)
    with pytest.raises(ig.InstagramError, match="no Instagram Business or Creator account"):
        ig.account_from_page("PAGE2", "tok")


def test_resolve_accepts_the_instagram_id_or_the_page_id(graph):
    assert ig.resolve_account("17841400000000001", "tok")["username"] == "acme.logistics"
    assert ig.resolve_account("PAGE1", "tok")["id"] == "17841400000000001"  # falls back to the Page lookup


def test_quota(graph):
    assert ig.publishing_quota("17841400000000001", "tok") == {"used": 3, "total": 100}


# ── Publishing ──────────────────────────────────────────────────────────────


def test_single_image_is_created_waited_for_and_published(graph):
    graph.status_sequence["C101"] = ["IN_PROGRESS", "IN_PROGRESS", "FINISHED"]
    result = ig.publish("IG1", "tok", "Hello #logistics", ["https://avir.example.com/static/uploads/a.jpg"])
    assert result == {"media_id": "M900", "permalink": "https://www.instagram.com/p/M900/", "carousel": False}
    posts = [c for c in graph.calls if c[0] == "POST"]
    assert posts[0] == ("POST", "IG1/media", {"image_url": "https://avir.example.com/static/uploads/a.jpg", "caption": "Hello #logistics"})
    assert posts[1] == ("POST", "IG1/media_publish", {"creation_id": "C101"})
    assert sum(1 for c in graph.calls if c[2].get("fields") == "status_code,status") == 3


def test_carousel_makes_a_child_per_slide_then_one_post(graph):
    urls = [f"https://avir.example.com/static/uploads/s{i}.jpg" for i in range(3)]
    result = ig.publish("IG1", "tok", "Swipe", urls)
    posts = [c for c in graph.calls if c[0] == "POST"]
    assert [p[2].get("is_carousel_item") for p in posts[:3]] == ["true"] * 3 and "caption" not in posts[0][2]
    assert posts[3][2] == {"media_type": "CAROUSEL", "children": "C101,C102,C103", "caption": "Swipe"}
    assert posts[4] == ("POST", "IG1/media_publish", {"creation_id": "C104"}) and result["carousel"]


@pytest.mark.parametrize("status, message", [("ERROR", "could not process"), ("EXPIRED", "could not process")])
def test_processing_failures(graph, status, message):
    graph.status_sequence["C101"] = [status]
    with pytest.raises(ig.InstagramError, match=message):
        ig.publish("IG1", "tok", "x", ["https://avir.example.com/a.jpg"])


def test_processing_that_never_finishes_times_out(graph, monkeypatch):
    graph.status_sequence["C101"] = ["IN_PROGRESS"]
    monkeypatch.setattr(ig, "READY_TIMEOUT", 6)
    with pytest.raises(ig.InstagramError, match="taking too long"):
        ig.publish("IG1", "tok", "x", ["https://avir.example.com/a.jpg"])


@pytest.mark.parametrize("error, message", [
    ({"code": 190, "message": "Error validating access token"}, "reconnect Instagram"),
    ({"code": 10, "message": "Application does not have permission"}, "instagram_content_publish"),
    ({"code": 4, "message": "Application request limit reached"}, "publishing limit"),
    ({"code": 100, "message": "Invalid image", "error_user_msg": "The image's aspect ratio isn't supported."}, "aspect ratio"),
])
def test_meta_errors_become_actionable_messages(graph, error, message):
    graph.fail[("POST", "/media")] = (400, error)
    with pytest.raises(ig.InstagramError, match=message):
        ig.publish("IG1", "tok", "x", ["https://avir.example.com/a.jpg"])


def test_caption_limits():
    with pytest.raises(ig.InstagramError, match="2200"):
        ig.check_caption("x" * 2201)
    with pytest.raises(ig.InstagramError, match="30 hashtags"):
        ig.check_caption(" ".join(f"#t{i}" for i in range(31)))
    with pytest.raises(ig.InstagramError, match="at most 10"):
        ig.publish("IG1", "tok", "x", ["u"] * 11)


# ── Images ──────────────────────────────────────────────────────────────────


def test_images_become_public_jpegs_in_instagrams_shapes(graph, uploads):
    jpeg_square = uploads((1080, 1080), "JPEG")
    png = uploads((1080, 1080), "PNG")
    too_tall = uploads((1080, 1920), "JPEG")
    out = ig.prepare_images([jpeg_square, png, too_tall], Config.UPLOAD_FOLDER)
    assert out[0] == f"https://avir.example.com{jpeg_square}"  # already fine: sent as is
    for url in out[1:]:
        assert url.startswith("https://avir.example.com/static/uploads/ig_") and url.endswith(".jpg")
    with Image.open(os.path.join(Config.UPLOAD_FOLDER, os.path.basename(out[2]))) as img:
        assert img.format == "JPEG" and img.size == (1080, 1350)  # cropped to 4:5


@pytest.mark.parametrize("base", ["http://avir.example.com", "https://localhost:5000", "", "https://127.0.0.1"])
def test_needs_a_public_https_address(monkeypatch, uploads, base):
    monkeypatch.setattr(Config, "APP_BASE_URL", base)
    monkeypatch.setattr(Config, "PUBLIC_MEDIA_BASE_URL", "")
    with pytest.raises(ig.InstagramError, match="public HTTPS address"):
        ig.prepare_images([uploads()], Config.UPLOAD_FOLDER)


# ── Connecting and publishing through the app ───────────────────────────────


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def _user(name="Priya"):
    return db.create_user(name, f"{uuid.uuid4().hex[:10]}@example.com", "h")


def _instagram(user_id):
    return next((a for a in db.get_user_social_accounts(user_id) if a["platform"] == "instagram"), None)


def test_connecting_stores_the_instagram_account_found_from_the_page(app, graph):
    user = _user()
    r = _login(app, user).post("/api/social/accounts", json={"platform": "instagram", "account_id": "PAGE1", "access_token": "tok"})
    assert r.status_code == 200
    acc = _instagram(user["id"])
    assert acc["account_id"] == "17841400000000001" and acc["account_name"] == "@acme.logistics"


def test_connect_from_the_facebook_page_in_one_click(app, graph):
    user = _user()
    client = _login(app, user)
    assert client.post("/api/social/instagram/from-facebook").status_code == 400  # no Facebook Page yet
    db.save_social_account(user_id=user["id"], platform="facebook", account_name="Acme", account_id="PAGE1", access_token="tok")
    r = client.post("/api/social/instagram/from-facebook")
    assert r.status_code == 200 and r.get_json()["username"] == "acme.logistics"
    assert _instagram(user["id"])["account_id"] == "17841400000000001"


def test_verify_reports_the_handle_and_quota(app, graph):
    r = _login(app, _user()).post("/api/social/verify/instagram", json={"account_id": "17841400000000001", "access_token": "tok"})
    body = r.get_json()
    assert body["verified"] and body["username"] == "acme.logistics" and "3 of 100" in body["message"]


@pytest.fixture
def approved(app, graph, uploads, monkeypatch):
    """An approved two-platform request with an Instagram carousel, owner connected to Instagram."""
    from api import routes

    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [])
    owner, reviewer = _user("Priya"), _user("Ravi")
    db.set_approval_reviewer_email(owner["id"], reviewer["email"])
    db.save_social_account(user_id=owner["id"], platform="instagram", account_name="@acme.logistics",
                           account_id="17841400000000001", access_token="tok")
    slides = [uploads((1080, 1350), "JPEG") for _ in range(3)]
    req = _login(app, owner).post("/api/approval-requests", json={"pipeline_client_id": "studio-ig", "items": [
        {"platform": "instagram", "caption": "Track every truck", "hashtags": ["#logistics"], "media": "carousel", "images": slides},
        {"platform": "facebook", "caption": "Big news", "media": "single", "images": [slides[0]]},
    ]}).get_json()["request"]
    return {"owner": owner, "reviewer": reviewer, "req": req, "slides": slides}


def _decide(app, ctx, items):
    return _login(app, ctx["reviewer"]).post(f"/api/approval-requests/{ctx['req']['id']}/decide", json={"items": items})


def test_only_an_approved_item_can_be_published_and_only_once(app, graph, approved):
    rid = approved["req"]["id"]
    owner = _login(app, approved["owner"])
    assert owner.post(f"/api/approval-requests/{rid}/publish", json={"platform": "instagram"}).status_code == 409  # still pending
    _decide(app, approved, {"0": {"decision": "approved"}, "1": {"decision": "changes", "comment": "x"}})

    view = owner.get(f"/api/approval-requests/{rid}").get_json()["request"]
    assert view["publishing"] == {"can_publish": True, "instagram": "@acme.logistics"}
    reviewer_view = _login(app, approved["reviewer"]).get(f"/api/approval-requests/{rid}").get_json()["request"]
    assert reviewer_view["publishing"] == {"can_publish": False}
    assert _login(app, approved["reviewer"]).post(f"/api/approval-requests/{rid}/publish",
                                                  json={"platform": "instagram"}).status_code == 404

    r = owner.post(f"/api/approval-requests/{rid}/publish", json={"platform": "instagram"})
    body = r.get_json()
    assert r.status_code == 200 and body["result"]["carousel"]
    published = body["request"]["items"][0]["published"]
    assert published["permalink"] == "https://www.instagram.com/p/M900/" and published["account"] == "@acme.logistics"
    carousel = next(c for c in graph.calls if c[2].get("media_type") == "CAROUSEL")
    assert carousel[2]["caption"] == "Track every truck\n\n#logistics"
    assert owner.post(f"/api/approval-requests/{rid}/publish", json={"platform": "instagram"}).status_code == 409
    assert owner.post(f"/api/approval-requests/{rid}/publish", json={"platform": "facebook"}).status_code == 400


def test_a_failed_publish_is_shown_on_the_item(app, graph, approved):
    rid = approved["req"]["id"]
    _decide(app, approved, {"0": {"decision": "approved"}, "1": {"decision": "approved"}})
    graph.fail[("POST", "/media")] = (400, {"code": 190, "message": "expired"})
    r = _login(app, approved["owner"]).post(f"/api/approval-requests/{rid}/publish", json={"platform": "instagram"})
    assert r.status_code == 400 and "reconnect" in r.get_json()["error"]
    item = db.get_approval_request(rid)["items"][0]
    assert "reconnect" in item["publish_error"] and not item.get("published")


def test_scheduled_posts_can_go_to_instagram(app, graph, uploads):
    from services.social_publisher_service import SocialPublisherService

    user = _user()
    db.save_social_account(user_id=user["id"], platform="instagram", account_name="@acme", account_id="IG7", access_token="tok")
    image = uploads((1080, 1080), "JPEG")
    result = SocialPublisherService().publish_post_to_connected_accounts(
        user["id"], ["instagram"], "Scheduled caption", os.path.join(Config.UPLOAD_FOLDER, os.path.basename(image)))
    assert result["instagram"]["success"] and result["instagram"]["post_url"].endswith("/M900/")
    no_image = SocialPublisherService().publish_post_to_connected_accounts(user["id"], ["instagram"], "Text only")
    assert "need an image" in no_image["instagram"]["error"]
    assert json.dumps(result)
