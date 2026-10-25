"""Publishing to LinkedIn and Facebook (text, one image, several images, a
LinkedIn document carousel), from an approved request and from the schedule.
Both APIs are simulated - nothing leaves the machine."""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

import db
from config import Config
from services import facebook_service as fb
from services import linkedin_service as li
from services import publish_service, schedule_service
from services.publish_service import PublishError


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body if body is not None else {}, headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeLinkedIn:
    def __init__(self):
        self.calls, self.n, self.processing, self.post_error = [], 0, 0, None

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if url.endswith("/v2/userinfo"):
            return Resp(200, {"sub": "abc123"})
        if "action=initializeUpload" in url:
            self.n += 1
            kind = "image" if "/rest/images" in url else "document"
            return Resp(200, {"value": {"uploadUrl": f"https://up.test/{kind}{self.n}", kind: f"urn:li:{kind}:A{self.n}"}})
        if url.startswith("https://up.test/"):
            return Resp(201)
        if url.endswith("/rest/posts"):
            if self.post_error:
                return Resp(*self.post_error)
            if self.processing:
                self.processing -= 1
                return Resp(400, {"message": "Media asset is still processing"})
            return Resp(201, {}, {"x-restli-id": "urn:li:share:777"})
        return Resp(404, {"message": f"unexpected {method} {url}"})

    def posts(self):
        return [kw["json"] for m, url, kw in self.calls if url.endswith("/rest/posts")]


class FakeFacebook:
    def __init__(self):
        self.calls, self.n, self.error = [], 0, None

    def __call__(self, method, url, **kw):
        path = url.split("/v23.0/")[1]
        self.calls.append((method, path, kw.get("data") or {}, bool(kw.get("files"))))
        if self.error:
            return Resp(400, {"error": self.error})
        if path.endswith("/photos"):
            self.n += 1
            published = kw["data"]["published"] == "true"
            return Resp(200, {"id": f"PH{self.n}", **({"post_id": f"PAGE1_{self.n}"} if published else {})})
        if path.endswith("/feed"):
            return Resp(200, {"id": "PAGE1_900"})
        return Resp(404, {"error": {"message": "unexpected"}})


@pytest.fixture
def linkedin(monkeypatch):
    fake = FakeLinkedIn()
    monkeypatch.setattr(li, "_http", fake)
    monkeypatch.setattr(li, "_sleep", lambda s: None)
    monkeypatch.setattr(Config, "LINKEDIN_API_VERSION", "202609")
    return fake


@pytest.fixture
def facebook(monkeypatch):
    fake = FakeFacebook()
    monkeypatch.setattr(fb, "_http", fake)
    monkeypatch.setattr(Config, "META_GRAPH_VERSION", "v23.0")
    return fake


@pytest.fixture
def images():
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    before = set(os.listdir(Config.UPLOAD_FOLDER))

    def make(count=1):
        urls = []
        for _ in range(count):
            name = f"test_pub_{uuid.uuid4().hex[:8]}.jpg"
            Image.new("RGB", (1080, 1080), "orange").save(os.path.join(Config.UPLOAD_FOLDER, name), "JPEG")
            urls.append(f"/static/uploads/{name}")
        return urls

    yield make
    for name in set(os.listdir(Config.UPLOAD_FOLDER)) - before:
        if name.startswith(("test_pub_", "carousel_")):
            os.remove(os.path.join(Config.UPLOAD_FOLDER, name))


def _paths(urls):
    return [os.path.join(Config.UPLOAD_FOLDER, os.path.basename(u)) for u in urls]


def _user(name="Priya"):
    return db.create_user(name, f"{uuid.uuid4().hex[:10]}@example.com", "h")


def _connect(user, *platforms):
    for platform in platforms:
        db.save_social_account(
            user_id=user["id"], platform=platform, access_token="tok",
            account_name={"linkedin": "Acme Logistics", "facebook": "Acme Page", "instagram": "@acme"}[platform],
            account_id={"linkedin": "urn:li:organization:42", "facebook": "PAGE1", "instagram": "IG1"}[platform])


# ── LinkedIn ────────────────────────────────────────────────────────────────


def test_linkedin_text_is_escaped_but_hashtags_survive():
    assert li.escape_commentary("Save 20% (this week) on plan_a* #Sale #big_deal") == r"Save 20% \(this week\) on plan\_a\* #Sale #big_deal"
    assert li.escape_commentary("We're #1! Mail a@b.co [now] {x|y} <b> ~ \\") == "We're #1! Mail a\\@b.co \\[now\\] \\{x\\|y\\} \\<b\\> \\~ \\\\"
    assert li.escape_commentary("C# is # fun") == r"C\# is \# fun"
    assert li.escape_commentary("") == ""


def test_linkedin_text_post(linkedin):
    out = li.publish("urn:li:organization:42", "tok", "Hello (world) #News")
    assert out == {"post_id": "urn:li:share:777", "permalink": "https://www.linkedin.com/feed/update/urn:li:share:777/", "format": "text"}
    [body] = linkedin.posts()
    assert body["commentary"] == r"Hello \(world\) #News" and "content" not in body
    assert body["author"] == "urn:li:organization:42" and body["lifecycleState"] == "PUBLISHED" and body["visibility"] == "PUBLIC"
    headers = linkedin.calls[-1][2]["headers"]
    assert headers["LinkedIn-Version"] == "202609" and headers["X-Restli-Protocol-Version"] == "2.0.0" and headers["Authorization"] == "Bearer tok"


def test_linkedin_single_image(linkedin, images):
    out = li.publish("urn:li:person:abc", "tok", "One image", _paths(images(1)))
    assert out["format"] == "image" and linkedin.posts()[0]["content"] == {"media": {"id": "urn:li:image:A1"}}
    init, put = linkedin.calls[0], linkedin.calls[1]
    assert init[1].endswith("/rest/images?action=initializeUpload") and init[2]["json"] == {"initializeUploadRequest": {"owner": "urn:li:person:abc"}}
    assert put[0] == "PUT" and put[1] == "https://up.test/image1" and len(put[2]["data"]) > 100


def test_linkedin_multi_image_post(linkedin, images):
    out = li.publish("urn:li:organization:42", "tok", "Three slides", _paths(images(3)))
    assert out["format"] == "multi_image"
    content = linkedin.posts()[0]["content"]
    assert content == {"multiImage": {"images": [{"id": f"urn:li:image:A{i}", "altText": f"Slide {i}"} for i in (1, 2, 3)]}}
    assert [c[0] for c in linkedin.calls] == ["POST", "PUT"] * 3 + ["POST"]


def test_linkedin_document_carousel(linkedin, images):
    from services.carousel_service import build_pdf

    pdf = os.path.join(Config.UPLOAD_FOLDER, build_pdf(_paths(images(3)), Config.UPLOAD_FOLDER))
    out = li.publish("urn:li:organization:42", "tok", "Swipe through", document_path=pdf, document_title="Five tips")
    assert out["format"] == "document"
    assert linkedin.posts()[0]["content"] == {"media": {"title": "Five tips", "id": "urn:li:document:A1"}}
    assert linkedin.calls[0][1].endswith("/rest/documents?action=initializeUpload")
    assert linkedin.calls[1][2]["data"][:4] == b"%PDF"


def test_linkedin_waits_for_media_that_is_still_processing(linkedin, images):
    linkedin.processing = 2
    assert li.publish("urn:li:organization:42", "tok", "Soon", _paths(images(1)))["post_id"] == "urn:li:share:777"
    assert len(linkedin.posts()) == 3
    linkedin.processing = 99
    with pytest.raises(li.LinkedInError, match="still processing"):
        li.publish("urn:li:organization:42", "tok", "Never", _paths(images(1)))


@pytest.mark.parametrize("error, message", [
    ((401, {"message": "token expired"}), "connect the account again"),
    ((403, {"message": "no access"}), "needs permission to post"),
    ((426, {"message": "Requested version 202401 is not active"}), "LINKEDIN_API_VERSION"),
    ((429, {"message": "throttled"}), "limit"),
    ((400, {"message": "commentary is invalid"}), "commentary is invalid"),
])
def test_linkedin_errors_say_what_to_do(linkedin, error, message):
    linkedin.post_error = error
    with pytest.raises(li.LinkedInError, match=message):
        li.publish("urn:li:organization:42", "tok", "Hello")
    assert len(linkedin.posts()) == 1  # a refusal is not retried


def test_linkedin_limits(linkedin, images):
    with pytest.raises(li.LinkedInError, match="at most 3,000 characters"):
        li.publish("urn:li:organization:42", "tok", "x" * 3001)
    with pytest.raises(li.LinkedInError, match="nothing to post"):
        li.publish("urn:li:organization:42", "tok", "  ")
    with pytest.raises(li.LinkedInError, match="2 to 20 images"):
        li.publish("urn:li:organization:42", "tok", "Too many", _paths(images(1)) * 21)
    assert not linkedin.calls


def test_linkedin_author(linkedin):
    assert li.resolve_author("me", "tok") == "urn:li:person:abc123"
    assert li.resolve_author(" urn:li:organization:42 ", "tok") == "urn:li:organization:42"
    with pytest.raises(li.LinkedInError, match="urn:li:person"):
        li.resolve_author("12345", "tok")


# ── Facebook ────────────────────────────────────────────────────────────────


def test_facebook_text_post(facebook):
    out = fb.publish("PAGE1", "tok", "Big news #Launch")
    assert out == {"post_id": "PAGE1_900", "permalink": "https://www.facebook.com/PAGE1_900", "format": "text"}
    assert facebook.calls == [("POST", "PAGE1/feed", {"access_token": "tok", "message": "Big news #Launch"}, False)]


def test_facebook_single_photo(facebook, images):
    out = fb.publish("PAGE1", "tok", "With a photo", _paths(images(1)))
    assert out["format"] == "image" and out["post_id"] == "PAGE1_1"
    [(_, path, data, has_file)] = facebook.calls
    assert path == "PAGE1/photos" and has_file and data == {"access_token": "tok", "published": "true", "caption": "With a photo"}


def test_facebook_several_photos_in_one_post(facebook, images):
    out = fb.publish("PAGE1", "tok", "A carousel", _paths(images(3)))
    assert out["format"] == "multi_image" and out["post_id"] == "PAGE1_900"
    uploads, feed = facebook.calls[:3], facebook.calls[3]
    assert all(c[1] == "PAGE1/photos" and c[2]["published"] == "false" and "caption" not in c[2] for c in uploads)
    assert feed[1] == "PAGE1/feed" and feed[2]["message"] == "A carousel"
    assert [json.loads(feed[2][f"attached_media[{i}]"]) for i in range(3)] == [{"media_fbid": f"PH{i}"} for i in (1, 2, 3)]


@pytest.mark.parametrize("error, message", [
    ({"code": 190, "message": "expired"}, "connect the Page again"),
    ({"code": 200, "message": "requires pages_manage_posts permission"}, "pages_manage_posts"),
    ({"code": 32, "message": "too many calls"}, "limit"),
    ({"code": 100, "message": "Invalid parameter"}, "Invalid parameter"),
])
def test_facebook_errors_say_what_to_do(facebook, error, message):
    facebook.error = error
    with pytest.raises(fb.FacebookError, match=message):
        fb.publish("PAGE1", "tok", "Hello")


def test_facebook_limits(facebook, images):
    with pytest.raises(fb.FacebookError, match="at most 10 photos"):
        fb.publish("PAGE1", "tok", "Too many", _paths(images(1)) * 11)
    with pytest.raises(fb.FacebookError, match="nothing to post"):
        fb.publish("PAGE1", "tok", "")
    assert not facebook.calls


# ── One item, whichever platform ────────────────────────────────────────────


def test_hashtags_go_under_the_caption_once():
    assert publish_service.post_text({"caption": "News #Launch", "hashtags": ["#launch", "#Logistics"]}) == "News #Launch\n\n#Logistics"
    assert publish_service.post_text({"caption": " Plain ", "hashtags": []}) == "Plain"


def test_publish_item_routes_each_platform(linkedin, facebook, images):
    user = _user()
    _connect(user, "linkedin", "facebook")
    slides = images(3)
    out = publish_service.publish_item(user["id"], {"platform": "linkedin", "caption": "Tips", "hashtags": ["#Tips"], "media": "carousel",
                                                    "images": slides, "linkedin_format": "document", "slide_titles": ["Plan well", "Pack", "Track"]},
                                       Config.UPLOAD_FOLDER)
    assert out["platform"] == "linkedin" and out["format"] == "document" and out["account"] == "Acme Logistics"
    post = linkedin.posts()[0]
    assert post["commentary"] == "Tips\n\n#Tips" and post["content"]["media"]["title"] == "Plan well"  # the PDF was built on the way

    out = publish_service.publish_item(user["id"], {"platform": "facebook", "caption": "Tips", "media": "single", "images": slides},
                                       Config.UPLOAD_FOLDER)
    assert out["format"] == "image" and out["account"] == "Acme Page" and len(facebook.calls) == 1  # "single" sends one image

    out = publish_service.publish_item(user["id"], {"platform": "linkedin", "caption": "Just text", "media": "none", "images": slides},
                                       Config.UPLOAD_FOLDER)
    assert out["format"] == "text"


def test_publish_item_explains_what_is_missing(linkedin, facebook, images):
    user = _user()
    item = {"platform": "facebook", "caption": "Hi", "media": "none", "images": []}
    with pytest.raises(PublishError, match="Connect your Facebook account"):
        publish_service.publish_item(user["id"], item, Config.UPLOAD_FOLDER)
    with pytest.raises(PublishError, match="YouTube isn't available"):
        publish_service.publish_item(user["id"], {**item, "platform": "youtube"}, Config.UPLOAD_FOLDER)
    _connect(user, "facebook")
    with pytest.raises(PublishError, match="could not be found"):
        publish_service.publish_item(user["id"], {**item, "media": "single", "images": ["/static/uploads/nope.jpg"]}, Config.UPLOAD_FOLDER)
    with pytest.raises(PublishError, match="could not be found"):
        publish_service.publish_item(user["id"], {**item, "media": "single", "images": ["/etc/passwd"]}, Config.UPLOAD_FOLDER)
    facebook.error = {"code": 190, "message": "expired"}
    with pytest.raises(PublishError, match="connect the Page again") as info:
        publish_service.publish_item(user["id"], item, Config.UPLOAD_FOLDER)
    assert not info.value.unreachable


def test_a_network_failure_is_marked_unreachable(monkeypatch):
    import requests

    user = _user()
    _connect(user, "facebook")

    def down(*a, **kw):
        raise requests.exceptions.ConnectionError("no route")

    monkeypatch.setattr(fb, "_http", down)
    with pytest.raises(PublishError, match="Could not reach Facebook") as info:
        publish_service.publish_item(user["id"], {"platform": "facebook", "caption": "Hi", "media": "none"}, Config.UPLOAD_FOLDER)
    assert info.value.unreachable


def test_linkedin_through_an_mcp_server_is_text_only(monkeypatch, images):
    user = _user()
    db.save_social_account(user_id=user["id"], platform="linkedin", account_name="Acme", account_id="urn:li:person:x",
                           connection_type="mcp", mcp_endpoint="https://mcp.test/rpc", mcp_token="secret")
    sent = []
    monkeypatch.setattr(publish_service.requests, "post", lambda url, **kw: sent.append((url, kw)) or Resp(200))
    out = publish_service.publish_item(user["id"], {"platform": "linkedin", "caption": "Text via MCP", "media": "none"}, Config.UPLOAD_FOLDER)
    assert out["format"] == "text" and sent[0][0] == "https://mcp.test/rpc"
    assert sent[0][1]["json"]["params"]["arguments"]["commentary"] == "Text via MCP"
    with pytest.raises(PublishError, match="only publish text posts"):
        publish_service.publish_item(user["id"], {"platform": "linkedin", "caption": "x", "media": "single", "images": images(1)}, Config.UPLOAD_FOLDER)
    monkeypatch.setattr(publish_service.requests, "post", lambda url, **kw: Resp(500))
    with pytest.raises(PublishError, match="answered with an error"):
        publish_service.publish_item(user["id"], {"platform": "linkedin", "caption": "x", "media": "none"}, Config.UPLOAD_FOLDER)


def test_connections_name_accounts_without_their_tokens():
    user = _user()
    assert publish_service.connections(user["id"]) == {"linkedin": None, "instagram": None, "facebook": None}
    _connect(user, "linkedin", "instagram")
    found = publish_service.connections(user["id"])
    assert found == {"linkedin": "Acme Logistics", "instagram": "@acme", "facebook": None} and "tok" not in json.dumps(found)


# ── From an approved request ────────────────────────────────────────────────


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


def test_an_approved_carousel_is_published_to_linkedin_and_facebook(app, linkedin, facebook, images, monkeypatch):
    from api import routes

    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [])
    owner, reviewer = _user("Priya"), _user("Ravi")
    db.set_approval_reviewer_email(owner["id"], reviewer["email"])
    _connect(owner, "linkedin", "facebook")
    slides = images(3)
    client = _login(app, owner)
    req = client.post("/api/approval-requests", json={"pipeline_client_id": "studio-li", "items": [
        {"platform": "linkedin", "caption": "Five tips (free)", "hashtags": ["#Tips"], "media": "carousel", "images": slides, "linkedin_format": "document"},
        {"platform": "facebook", "caption": "Five tips", "media": "carousel", "images": slides},
    ]}).get_json()["request"]
    rid = req["id"]
    assert client.post(f"/api/approval-requests/{rid}/publish", json={"platform": "linkedin"}).status_code == 409  # not approved yet
    _login(app, reviewer).post(f"/api/approval-requests/{rid}/decide", json={"decision": "approved"})

    view = client.get(f"/api/approval-requests/{rid}").get_json()["request"]
    assert view["publishing"]["accounts"] == {"linkedin": "Acme Logistics", "instagram": None, "facebook": "Acme Page"}

    r = client.post(f"/api/approval-requests/{rid}/publish", json={"platform": "linkedin"})
    body = r.get_json()
    assert r.status_code == 200 and body["result"]["format"] == "document"
    published = body["request"]["items"][0]["published"]
    assert published["permalink"].endswith("urn:li:share:777/") and published["account"] == "Acme Logistics" and published["format"] == "document"
    assert linkedin.posts()[0]["commentary"] == r"Five tips \(free\)" + "\n\n#Tips"
    assert linkedin.calls[1][2]["data"][:4] == b"%PDF"  # the PDF the reviewer saw is what was uploaded
    assert client.post(f"/api/approval-requests/{rid}/publish", json={"platform": "linkedin"}).status_code == 409  # once

    r = client.post(f"/api/approval-requests/{rid}/publish", json={"platform": "facebook"})
    assert r.status_code == 200 and r.get_json()["result"]["format"] == "multi_image"
    assert r.get_json()["request"]["items"][1]["published"]["permalink"] == "https://www.facebook.com/PAGE1_900"


def test_a_refused_publish_is_kept_on_the_item(app, linkedin, images, monkeypatch):
    from api import routes

    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [])
    owner, reviewer = _user(), _user("Ravi")
    db.set_approval_reviewer_email(owner["id"], reviewer["email"])
    _connect(owner, "linkedin")
    client = _login(app, owner)
    rid = client.post("/api/approval-requests", json={"pipeline_client_id": "studio-li2", "items": [
        {"platform": "linkedin", "caption": "Hello", "media": "none"}]}).get_json()["request"]["id"]
    _login(app, reviewer).post(f"/api/approval-requests/{rid}/decide", json={"decision": "approved"})
    linkedin.post_error = (401, {"message": "expired"})
    r = client.post(f"/api/approval-requests/{rid}/publish", json={"platform": "linkedin"})
    assert r.status_code == 400 and "connect the account again" in r.get_json()["error"]
    item = db.get_approval_request(rid)["items"][0]
    assert "connect the account again" in item["publish_error"] and not item.get("published")
    linkedin.post_error = None
    assert client.post(f"/api/approval-requests/{rid}/publish", json={"platform": "linkedin"}).status_code == 200
    assert db.get_approval_request(rid)["items"][0]["publish_error"] is None


# ── Scheduling ──────────────────────────────────────────────────────────────

NOW = datetime(2026, 10, 12, 6, 0, tzinfo=timezone.utc)


def _stored(*parts):
    """A time as the database holds it: UTC, without the zone."""
    return datetime(*parts, tzinfo=timezone.utc).replace(tzinfo=None)



def test_times_are_stored_in_utc():
    assert schedule_service.parse_when("2026-10-13T10:00:00+05:30", NOW) == _stored(2026, 10, 13, 4, 30)
    assert schedule_service.parse_when("2026-10-13T04:30:00.000Z", NOW) == _stored(2026, 10, 13, 4, 30)
    assert schedule_service.parse_when("2026-10-13T04:30", NOW) == _stored(2026, 10, 13, 4, 30)  # no offset: UTC
    assert schedule_service.parse_when((NOW - timedelta(minutes=1)).isoformat(), NOW)  # "now", picked a moment ago
    for bad, why in ((None, "Choose a date"), ("tomorrow", "Choose a date"), ("2026-10-12T05:00:00Z", "already passed"),
                     ("2028-01-01T00:00:00Z", "a year ahead")):
        with pytest.raises(schedule_service.ScheduleError, match=why):
            schedule_service.parse_when(bad, NOW)


def _items(slides):
    return [
        {"platform": "linkedin", "caption": "Five tips", "hashtags": ["#Tips"], "media": "carousel", "images": slides, "linkedin_format": "multi_image"},
        {"platform": "facebook", "caption": "Five tips on Facebook", "media": "single", "images": slides[:1]},
    ]


def test_scheduling_checks_the_post_before_taking_it(images):
    user = _user()
    slides = images(3)
    when = "2026-10-13T10:00:00+05:30"
    with pytest.raises(schedule_service.ScheduleError, match="Connect your LinkedIn account"):
        schedule_service.create(user["id"], _items(slides), when, Config.UPLOAD_FOLDER, now=NOW)
    _connect(user, "linkedin", "facebook")
    for items, why in (
        ([], "at least one platform to schedule"),
        ([{"platform": "instagram", "caption": "No picture", "media": "none"}], "Instagram posts need an image"),
        ([{"platform": "youtube", "caption": "A video", "media": "none"}], "YouTube posts can't be scheduled"),
        ([{"platform": "linkedin", "caption": "x" * 2990, "hashtags": ["#LongTagHere"], "media": "none"}], "over the limit of 3,000"),
        ([{"platform": "facebook", "caption": "Hi", "media": "single", "images": ["/static/uploads/gone.jpg"]}], "could not be found"),
    ):
        with pytest.raises(schedule_service.ScheduleError, match=why):
            schedule_service.create(user["id"], items, when, Config.UPLOAD_FOLDER, now=NOW)
    assert db.get_user_scheduled_posts(user["id"]) == []


def test_a_scheduled_post_keeps_each_platforms_version(images):
    user = _user()
    _connect(user, "linkedin", "facebook")
    slides = images(3)
    post = schedule_service.create(user["id"], _items(slides), "2026-10-13T10:00:00+05:30", Config.UPLOAD_FOLDER,
                                   story="Share five tips", run_id=7, conversation_id=3, now=NOW)
    assert post["status"] == "pending" and post["platforms"] == ["linkedin", "facebook"] and post["run_id"] == 7
    assert post["scheduled_at_iso"] == "2026-10-13T04:30:00+00:00"
    assert [i["caption"] for i in post["items"]] == ["Five tips", "Five tips on Facebook"]
    assert "decision" not in post["items"][0] and post["items"][0]["images"] == slides
    assert post["title"] == "Five tips" and post["thumb"] == slides[0] and post["media"] == "carousel"
    assert post["content"]["source"] == {"run_id": 7, "conversation_id": 3} and post["content"]["story"] == "Share five tips"


def test_due_posts_are_published_once_with_a_result_per_platform(linkedin, facebook, images):
    user = _user()
    _connect(user, "linkedin", "facebook")
    post = schedule_service.create(user["id"], _items(images(3)), "2026-10-13T04:30:00Z", Config.UPLOAD_FOLDER, now=NOW)
    early, due = datetime(2026, 10, 13, 4, 0, tzinfo=timezone.utc), datetime(2026, 10, 13, 4, 31, tzinfo=timezone.utc)

    schedule_service.publish_due(Config.UPLOAD_FOLDER, early)
    assert db.get_scheduled_post(user["id"], post["id"])["status"] == "pending" and not linkedin.calls

    schedule_service.publish_due(Config.UPLOAD_FOLDER, due)
    done = db.get_scheduled_post(user["id"], post["id"])
    assert done["status"] == "published"
    assert done["results"]["linkedin"]["ok"] and done["results"]["linkedin"]["format"] == "multi_image"
    assert done["results"]["facebook"]["permalink"] == "https://www.facebook.com/PAGE1_1" and done["results"]["facebook"]["account"] == "Acme Page"
    assert linkedin.posts()[0]["commentary"] == "Five tips\n\n#Tips" and facebook.calls[0][2]["caption"] == "Five tips on Facebook"

    schedule_service.publish_due(Config.UPLOAD_FOLDER, due + timedelta(minutes=1))  # the next pass
    assert len(linkedin.posts()) == 1 and len(facebook.calls) == 1
    bell = db.list_notifications(user["id"])["items"]
    assert bell[0]["title"] == "Your scheduled post is live" and "LinkedIn, Facebook" in bell[0]["body"]


def test_a_platform_that_fails_can_be_retried_without_reposting_the_others(linkedin, facebook, images):
    user = _user()
    _connect(user, "linkedin", "facebook")
    post = schedule_service.create(user["id"], _items(images(3)), "2026-10-13T04:30:00Z", Config.UPLOAD_FOLDER, now=NOW)
    due = datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)
    facebook.error = {"code": 190, "message": "expired"}
    assert schedule_service.publish_due(Config.UPLOAD_FOLDER, due) == {"published": 0, "partial": 1, "failed": 0}
    half = db.get_scheduled_post(user["id"], post["id"])
    assert half["status"] == "partial" and half["results"]["linkedin"]["ok"]
    assert not half["results"]["facebook"]["ok"] and "connect the Page again" in half["results"]["facebook"]["error"]
    assert "Not published on Facebook" in db.list_notifications(user["id"])["items"][0]["body"]

    facebook.error = None
    with pytest.raises(schedule_service.ScheduleError, match="still waiting"):
        schedule_service.reschedule(user["id"], post["id"], "2026-10-14T04:30:00Z", now=NOW)
    assert schedule_service.retry(user["id"], post["id"], now=due)["status"] == "pending"
    schedule_service.publish_due(Config.UPLOAD_FOLDER, due + timedelta(minutes=1))
    done = db.get_scheduled_post(user["id"], post["id"])
    assert done["status"] == "published" and done["results"]["facebook"]["ok"]
    assert len(linkedin.posts()) == 1  # LinkedIn was not posted again
    with pytest.raises(schedule_service.ScheduleError, match="nothing left to send"):
        schedule_service.retry(user["id"], post["id"])


def test_nothing_published_means_failed(linkedin, images):
    user = _user()
    _connect(user, "linkedin")
    post = schedule_service.create(user["id"], [{"platform": "linkedin", "caption": "Hello", "media": "none"}],
                                   "2026-10-13T04:30:00Z", Config.UPLOAD_FOLDER, now=NOW)
    linkedin.post_error = (401, {"message": "expired"})
    schedule_service.publish_due(Config.UPLOAD_FOLDER, datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc))
    failed = db.get_scheduled_post(user["id"], post["id"])
    assert failed["status"] == "failed" and "connect the account again" in failed["results"]["linkedin"]["error"]
    assert db.list_notifications(user["id"])["items"][0]["title"] == "Your scheduled post could not be published"


def test_a_post_is_claimed_by_one_publisher_only(images):
    user = _user()
    _connect(user, "facebook")
    post = schedule_service.create(user["id"], [{"platform": "facebook", "caption": "Hello", "media": "none"}],
                                   "2026-10-13T04:30:00Z", Config.UPLOAD_FOLDER, now=NOW)
    assert db.claim_scheduled_post(post["id"]) is True
    assert db.claim_scheduled_post(post["id"]) is False
    assert db.get_scheduled_post(user["id"], post["id"])["status"] == "publishing"
    assert db.due_scheduled_posts(_stored(2027, 1, 1)) == [] or all(p["id"] != post["id"] for p in db.due_scheduled_posts(_stored(2027, 1, 1)))


def test_moving_and_cancelling(images):
    user = _user()
    _connect(user, "facebook")
    post = schedule_service.create(user["id"], [{"platform": "facebook", "caption": "Hello", "media": "none"}],
                                   "2026-10-13T04:30:00Z", Config.UPLOAD_FOLDER, now=NOW)
    moved = schedule_service.reschedule(user["id"], post["id"], "2026-10-15T09:00:00+05:30", now=NOW)
    assert moved["scheduled_at_iso"] == "2026-10-15T03:30:00+00:00" and moved["status"] == "pending"
    with pytest.raises(schedule_service.ScheduleError, match="already passed"):
        schedule_service.reschedule(user["id"], post["id"], "2026-10-01T09:00:00Z", now=NOW)
    with pytest.raises(LookupError):
        schedule_service.reschedule(_user()["id"], post["id"], "2026-10-15T09:00:00Z", now=NOW)  # someone else's
    assert db.cancel_scheduled_post(user["id"], post["id"])
    with pytest.raises(schedule_service.ScheduleError, match="still waiting"):
        schedule_service.reschedule(user["id"], post["id"], "2026-10-16T09:00:00Z", now=NOW)
    assert schedule_service.publish_due(Config.UPLOAD_FOLDER, datetime(2026, 11, 1, tzinfo=timezone.utc)) == {"published": 0, "partial": 0, "failed": 0}


def test_older_one_caption_posts_are_left_to_the_older_path():
    user = _user()
    old = db.create_scheduled_post(user["id"], ["facebook"], _stored(2026, 10, 1, 9, 0), {"caption": "Old style", "image_url": None})
    assert all(p["id"] != old["id"] for p in db.due_scheduled_posts(_stored(2026, 10, 2)))
    listed = db.get_user_scheduled_posts(user["id"])[0]
    assert listed["items"] == [] and listed["title"] == "Old style" and listed["scheduled_at_iso"] == "2026-10-01T09:00:00+00:00"
    with pytest.raises(schedule_service.ScheduleError):
        schedule_service.retry(user["id"], old["id"])


def test_schedule_endpoints(app, images):
    user, other = _user(), _user("Ravi")
    _connect(user, "linkedin", "facebook")
    client = _login(app, user)
    assert client.get("/api/social/connections").get_json()["connections"] == {"linkedin": "Acme Logistics", "instagram": None, "facebook": "Acme Page"}

    when = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    r = client.post("/api/social/schedule", json={"scheduled_at": when.isoformat(), "items": _items(images(3)), "story": "Tips", "run_id": 5})
    post = r.get_json()["scheduled_post"]
    assert r.status_code == 200 and post["status"] == "pending" and post["scheduled_at_iso"] == when.isoformat()
    bad = client.post("/api/social/schedule", json={"scheduled_at": "2020-01-01T00:00:00Z", "items": _items(images(2))})
    assert bad.status_code == 400 and "already passed" in bad.get_json()["error"]

    later = when + timedelta(days=1)
    moved = client.patch(f"/api/social/scheduled/{post['id']}", json={"scheduled_at": later.isoformat()})
    assert moved.status_code == 200 and moved.get_json()["scheduled_post"]["scheduled_at_iso"] == later.isoformat()
    assert client.get(f"/api/social/scheduled/{post['id']}").get_json()["scheduled_post"]["title"] == "Five tips"
    assert client.post(f"/api/social/scheduled/{post['id']}/retry").status_code == 400  # nothing failed

    stranger = _login(app, other)
    assert stranger.get(f"/api/social/scheduled/{post['id']}").status_code == 404
    assert stranger.patch(f"/api/social/scheduled/{post['id']}", json={"scheduled_at": later.isoformat()}).status_code == 404
    assert stranger.post(f"/api/social/scheduled/{post['id']}/retry").status_code == 404
    assert app.test_client().post("/api/social/schedule", json={}).status_code == 401

    month = later.strftime("%Y-%m")
    days = client.get(f"/api/calendar?tz=0&month={month}").get_json()["days"]
    [chip] = [s for d in days for s in d["scheduled"] if s["id"] == post["id"]]
    assert chip["title"] == "Five tips" and chip["movable"] and chip["status"] == "pending" and chip["media"] == "carousel"
    assert chip["time"] == later.strftime("%H:%M") and chip["thumb"].startswith("/static/uploads/")

    assert client.post(f"/api/social/scheduled/{post['id']}/cancel").status_code == 200
    days = client.get(f"/api/calendar?tz=0&month={month}").get_json()["days"]
    assert not [s for d in days for s in d["scheduled"] if s["id"] == post["id"]]  # cancelled posts leave the calendar


def test_the_older_schedule_shape_still_works_and_stores_utc(app):
    user = _user()
    client = _login(app, user)
    when = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    local = when.astimezone(timezone(timedelta(hours=5, minutes=30)))
    r = client.post("/api/social/schedule", json={"platforms": ["facebook"], "scheduled_at": local.isoformat(), "content_json": {"story": "Hi"}})
    assert r.status_code == 200 and r.get_json()["scheduled_post"]["scheduled_at_iso"] == when.isoformat()
    assert client.post("/api/social/schedule", json={"platforms": [], "scheduled_at": local.isoformat()}).status_code == 400


def test_the_schedule_button_is_on_unless_switched_off(app, monkeypatch):
    from config import Config

    assert Config.STUDIO_SCHEDULING is True or "STUDIO_SCHEDULING" in __import__("os").environ  # on by default
    admin = db.create_user("Admin", f"{uuid.uuid4().hex[:10]}@example.com", "h", is_admin=True)
    client = _login(app, admin)
    app.config["STUDIO_SCHEDULING"] = True
    for page in ("/dashboard", "/library"):
        assert "window.SCHEDULING_ENABLED = true;" in client.get(page).get_data(as_text=True)
    app.config["STUDIO_SCHEDULING"] = False
    for page in ("/dashboard", "/library"):
        assert "window.SCHEDULING_ENABLED = false;" in client.get(page).get_data(as_text=True)
