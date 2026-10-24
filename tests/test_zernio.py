"""X and Threads through the unified social API (services/zernio_service.py).
Zernio is simulated - nothing leaves the machine."""

import json
import uuid
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from sqlalchemy.orm import Session

import db
from config import Config
from services import publish_service, zernio_service


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body if body is not None else {}

    def json(self):
        return self._body


class FakeZernio:
    def __init__(self):
        self.calls, self.accounts, self.post_status = [], [], 201

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        assert kw["headers"]["Authorization"] == "Bearer zk-test"
        path = url.split("/api/v1/", 1)[1]
        if method == "POST" and path == "profiles":
            return Resp(201, {"profile": {"_id": f"prof-{kw['json']['externalId']}"}})
        if method == "GET" and path.startswith("connect/"):
            return Resp(
                200,
                {
                    "authUrl": "https://x.com/i/oauth2/authorize?"
                    + urlencode({"zernio": 1, "back": kw["params"]["redirect_url"]})
                },
            )
        if method == "GET" and path == "accounts":
            return Resp(200, {"accounts": [a for a in self.accounts if a["profile"] == kw["params"]["profileId"]]})
        if method == "POST" and path == "posts":
            platform = kw["json"]["platforms"][0]["platform"]
            if self.post_status == 207:
                return Resp(
                    207,
                    {
                        "post": {
                            "_id": "p1",
                            "platforms": [
                                {"platform": platform, "status": "failed", "errorMessage": "Duplicate content"}
                            ],
                        }
                    },
                )
            return Resp(
                201,
                {
                    "post": {
                        "_id": "p1",
                        "platforms": [
                            {
                                "platform": platform,
                                "status": "published",
                                "platformPostUrl": "https://x.com/acme/status/1",
                            }
                        ],
                    }
                },
            )
        if method == "DELETE" and path.startswith("accounts/"):
            return Resp(200, {"message": "Account disconnected successfully"})
        return Resp(404, {"error": f"unexpected {method} {path}"})


@pytest.fixture
def zernio(monkeypatch):
    fake = FakeZernio()
    monkeypatch.setattr(zernio_service.requests, "request", fake)
    for name, value in (
        ("ZERNIO_API_KEY", "zk-test"),
        ("ZERNIO_API_BASE", "https://zernio.com/api/v1"),
        ("ZERNIO_PLATFORMS", "twitter,threads"),
        ("APP_BASE_URL", "https://app.test"),
    ):
        monkeypatch.setattr(Config, name, value)
    return fake


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _user():
    return db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "h")


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def _account(user, platform):
    with Session(db.engine) as session:
        return (
            session.query(db.SocialAccount)
            .filter(db.SocialAccount.user_id == user["id"], db.SocialAccount.platform == platform)
            .first()
        )


def _connect(app, zernio, user, account_id="acc-1", platform="twitter", owner=None):
    client = _login(app, user)
    r = client.get(f"/api/social/unified/{platform}/start")
    assert r.status_code == 302 and r.headers["Location"].startswith("https://x.com/i/oauth2/authorize")
    back = urlparse(parse_qs(urlparse(r.headers["Location"]).query)["back"][0])
    zernio.accounts.append(
        {"_id": account_id, "platform": platform, "username": "acme", "profile": owner or f"prof-avir-{user['id']}"}
    )
    client.get(f"{back.path}?{back.query}&connected={platform}&accountId={account_id}&username=acme")
    return client


def test_off_until_the_key_is_set(app, monkeypatch):
    monkeypatch.setattr(Config, "ZERNIO_API_KEY", "")
    client = _login(app, _user())
    assert client.get("/api/social/oauth/providers").get_json()["unified"] == []
    assert client.get("/api/social/unified/twitter/start").headers["Location"].endswith("/settings")
    assert "twitter" not in publish_service.platforms()


def test_x_is_connected_through_the_users_own_profile(app, zernio):
    user = _user()
    client = _connect(app, zernio, user)
    assert client.get("/api/social/oauth/result").get_json()["result"]["message"] == "Connected X (@acme)."
    acc = _account(user, "twitter")
    assert (acc.account_id, acc.account_name, acc.connection_type, acc.access_token) == (
        "acc-1",
        "@acme",
        "zernio",
        None,
    )
    assert publish_service.connections(user["id"])["twitter"] == "@acme"
    providers = client.get("/api/social/oauth/providers").get_json()["unified"]
    assert providers == [{"platform": "twitter", "name": "X"}, {"platform": "threads", "name": "Threads"}]
    assert "zk-test" not in json.dumps(providers)


def test_someone_elses_account_id_connects_nothing(app, zernio):
    user = _user()
    client = _connect(app, zernio, user, account_id="theirs", owner="prof-avir-999999")
    result = client.get("/api/social/oauth/result").get_json()["result"]
    assert result["ok"] is False and "isn't connected to your workspace" in result["message"]
    assert _account(user, "twitter") is None


def test_a_callback_this_browser_did_not_start_is_refused(app, zernio):
    user = _user()
    client = _login(app, user)
    client.get("/api/social/unified/callback?network=twitter&state=forged&accountId=acc-1")
    assert "could not be verified" in client.get("/api/social/oauth/result").get_json()["result"]["message"]
    assert _account(user, "twitter") is None


def test_cancelling_connects_nothing(app, zernio):
    user = _user()
    client = _login(app, user)
    back = urlparse(
        parse_qs(urlparse(client.get("/api/social/unified/threads/start").headers["Location"]).query)["back"][0]
    )
    client.get(f"{back.path}?{back.query}&error=oauth_denied&platform=threads")
    assert "cancelled" in client.get("/api/social/oauth/result").get_json()["result"]["message"]


def test_publishing_and_a_refusal(app, zernio):
    user = _user()
    _connect(app, zernio, user)
    result = publish_service.publish_item(
        user["id"],
        {"platform": "twitter", "caption": "Live tracking is here", "hashtags": ["#Freight"], "media": "none"},
        Config.UPLOAD_FOLDER,
    )
    assert result["permalink"] == "https://x.com/acme/status/1" and result["account"] == "@acme"
    sent = [c for c in zernio.calls if c[0] == "POST" and c[1].endswith("/posts")][-1][2]["json"]
    assert sent["content"] == "Live tracking is here\n\n#Freight" and sent["publishNow"] is True
    assert sent["platforms"] == [{"platform": "twitter", "accountId": "acc-1"}] and "mediaItems" not in sent

    zernio.post_status = 207
    with pytest.raises(publish_service.PublishError, match="Duplicate content"):
        publish_service.publish_item(
            user["id"], {"platform": "twitter", "caption": "Again", "media": "none"}, Config.UPLOAD_FOLDER
        )


def test_disconnecting_removes_it_at_zernio_too(app, zernio):
    user = _user()
    client = _connect(app, zernio, user)
    assert client.delete("/api/social/accounts/twitter").status_code == 200
    assert any(c[0] == "DELETE" and c[1].endswith("/accounts/acc-1") for c in zernio.calls)
    assert publish_service.connections(user["id"])["twitter"] is None


def test_the_writer_knows_x_and_threads():
    from agents.caption_agent import CaptionAgent
    from agents.hashtag_agent import HashtagAgent

    assert CaptionAgent.PLATFORM_CONFIGS["twitter"]["max_length"] == 280
    assert CaptionAgent.PLATFORM_CONFIGS["threads"]["max_length"] == 500
    assert HashtagAgent.PLATFORM_HASHTAG_LIMITS["threads"] == 1
