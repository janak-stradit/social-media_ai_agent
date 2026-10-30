"""One-click "Connect with Facebook / LinkedIn": the sign-in round trip, the
checks that make it safe, choosing between several accounts, and connections
that run out. Facebook and LinkedIn are simulated - nothing leaves the machine."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy.orm import Session

import db
from config import Config
from services import publish_service
from services import social_oauth_service as oauth


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


PAGE = {"id": "PAGE1", "name": "Acme Logistics", "access_token": "PAGE-TOKEN-1",
        "instagram_business_account": {"id": "IG1", "username": "acme.logistics"}}
PAGE2 = {"id": "PAGE2", "name": "Acme Careers", "access_token": "PAGE-TOKEN-2"}


class FakePlatforms:
    """Answers what social_oauth_service asks Facebook and LinkedIn, and records it."""

    def __init__(self):
        self.calls = []
        self.pages = [PAGE]
        self.linkedin_scope = "openid,profile,w_member_social"
        self.organizations = []
        self.long_lived = True

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        params, data = kw.get("params") or {}, kw.get("data") or {}
        if "graph.facebook.com" in url and url.endswith("/oauth/access_token"):
            if params.get("grant_type") == "fb_exchange_token":
                return Resp(200, {"access_token": "LONG-USER-TOKEN"}) if self.long_lived else Resp(400, {"error": {"message": "nope"}})
            if params.get("code") != "good-code":
                return Resp(400, {"error": {"message": "This authorization code has been used"}})
            return Resp(200, {"access_token": "SHORT-USER-TOKEN", "token_type": "bearer", "expires_in": 5000})
        if url.endswith("/me/accounts"):
            return Resp(200, {"data": self.pages})
        if url.endswith("/oauth/v2/accessToken"):
            if data.get("code") != "good-code":
                return Resp(400, {"error": "invalid_request", "error_description": "authorization code not found"})
            return Resp(200, {"access_token": "LI-TOKEN", "expires_in": 5184000, "scope": self.linkedin_scope})
        if url.endswith("/v2/userinfo"):
            return Resp(200, {"sub": "abc123", "name": "Priya Shah"})
        if url.endswith("/rest/organizationAcls"):
            return Resp(200, {"elements": [{"organization": urn, "role": "ADMINISTRATOR", "state": "APPROVED"} for urn in self.organizations]})
        if "/rest/organizations/" in url:
            return Resp(200, {"localizedName": "Acme Logistics Ltd"})
        return Resp(404, {"message": f"unexpected {method} {url}"})


@pytest.fixture
def platforms(monkeypatch):
    fake = FakePlatforms()
    monkeypatch.setattr(oauth, "_http", fake)
    for name, value in (("META_APP_ID", "app-123"), ("META_APP_SECRET", "meta-secret"), ("META_LOGIN_CONFIG_ID", ""),
                        ("META_GRAPH_VERSION", "v23.0"), ("LINKEDIN_CLIENT_ID", "li-123"), ("LINKEDIN_CLIENT_SECRET", "li-secret"),
                        ("LINKEDIN_OAUTH_SCOPES", "openid profile w_member_social"), ("APP_BASE_URL", "https://app.test")):
        monkeypatch.setattr(Config, name, value)
    return fake


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _user(is_admin=False):
    return db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "h", is_admin=is_admin)


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def _start(client, provider):
    """Clicks Connect: returns (the platform's sign-in address, the state sent with it)."""
    r = client.get(f"/api/social/oauth/{provider}/start")
    assert r.status_code == 302
    target = urlparse(r.headers["Location"])
    return target, parse_qs(target.query)


def _result(client):
    return client.get("/api/social/oauth/result").get_json()["result"]


def _account(user, platform):
    with Session(db.engine) as session:
        return session.query(db.SocialAccount).filter(db.SocialAccount.user_id == user["id"], db.SocialAccount.platform == platform).first()


# ── Set up or not ───────────────────────────────────────────────────────────


def test_connect_buttons_only_exist_once_the_apps_are_set_up(app, monkeypatch):
    for name in ("META_APP_ID", "META_APP_SECRET", "LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET", "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"):
        monkeypatch.setattr(Config, name, "")
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    client = _login(app, _user())
    assert client.get("/api/social/oauth/providers").get_json()["providers"] == {"facebook": False, "linkedin": False, "instagram": False, "youtube": False}
    r = client.get("/api/social/oauth/facebook/start")
    assert r.status_code == 302 and r.headers["Location"].endswith("/settings")  # not to Facebook
    assert "isn't set up" in _result(client)["message"]
    monkeypatch.setattr(Config, "META_APP_ID", "app-123")  # an ID without its secret is not enough
    assert not oauth.enabled("facebook")
    assert client.get("/api/social/oauth/tiktok/start").headers["Location"].endswith("/settings")


def test_admins_see_the_return_addresses_to_register(app, platforms, monkeypatch):
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.setattr(Config, "INSTAGRAM_APP_ID", "")
    body = _login(app, _user(is_admin=True)).get("/api/social/oauth/providers").get_json()
    assert body["providers"] == {"facebook": True, "linkedin": True, "instagram": False, "youtube": False}
    assert body["redirect_uris"] == {"facebook": "https://app.test/api/social/oauth/facebook/callback",
                                     "linkedin": "https://app.test/api/social/oauth/linkedin/callback",
                                     "instagram": "https://app.test/api/social/oauth/instagram/callback"}
    assert "redirect_uris" not in _login(app, _user()).get("/api/social/oauth/providers").get_json()
    assert "meta-secret" not in json.dumps(body) and "li-secret" not in json.dumps(body)


def test_the_sign_in_addresses(app, platforms, monkeypatch):
    client = _login(app, _user())
    target, query = _start(client, "facebook")
    assert target.netloc == "www.facebook.com" and target.path == "/v23.0/dialog/oauth"
    assert query["client_id"] == ["app-123"] and query["response_type"] == ["code"] and len(query["state"][0]) >= 32
    assert query["redirect_uri"] == ["https://app.test/api/social/oauth/facebook/callback"]
    assert "pages_manage_posts" in query["scope"][0] and "instagram_content_publish" in query["scope"][0]
    assert "secret" not in target.query

    monkeypatch.setattr(Config, "META_LOGIN_CONFIG_ID", "cfg-9")  # Facebook Login for Business
    _, query = _start(client, "facebook")
    assert query["config_id"] == ["cfg-9"] and "scope" not in query

    target, query = _start(client, "linkedin")
    assert target.netloc == "www.linkedin.com" and target.path == "/oauth/v2/authorization"
    assert query["scope"] == ["openid profile w_member_social"] and query["client_id"] == ["li-123"]


# ── Facebook ────────────────────────────────────────────────────────────────


def test_one_page_is_connected_straight_away_with_its_instagram(app, platforms):
    user = _user()
    client = _login(app, user)
    _, query = _start(client, "facebook")
    r = client.get(f"/api/social/oauth/facebook/callback?code=good-code&state={query['state'][0]}")
    assert r.status_code == 302 and r.headers["Location"].endswith("/settings")

    result = _result(client)
    assert result == {"ok": True, "provider": "facebook",
                      "message": "Connected Facebook (Acme Logistics) and Instagram (@acme.logistics)."}
    assert _result(client) is None  # told once

    facebook, instagram = _account(user, "facebook"), _account(user, "instagram")
    assert (facebook.account_id, facebook.account_name, facebook.access_token) == ("PAGE1", "Acme Logistics", "PAGE-TOKEN-1")
    assert (instagram.account_id, instagram.account_name, instagram.access_token) == ("IG1", "@acme.logistics", "PAGE-TOKEN-1")
    assert facebook.connected_via == "oauth" and facebook.token_expires_at is None and facebook.status == "connected"
    assert publish_service.connections(user["id"]) == {"linkedin": None, "instagram": "@acme.logistics", "facebook": "Acme Logistics",
                                                       "youtube": None}

    # the code was exchanged with the app's secret, then for a long-lived sign-in, and the Pages asked with that
    exchange, long_lived, pages = (c[2]["params"] for c in platforms.calls)
    assert exchange["client_secret"] == "meta-secret" and exchange["redirect_uri"] == "https://app.test/api/social/oauth/facebook/callback"
    assert long_lived["fb_exchange_token"] == "SHORT-USER-TOKEN" and pages["access_token"] == "LONG-USER-TOKEN"
    # nothing secret reaches the page
    listed = json.dumps(client.get("/api/social/accounts").get_json())
    assert "PAGE-TOKEN-1" not in listed and '"connected_via": "oauth"' in listed


def test_a_page_without_instagram_and_a_failed_long_lived_exchange(app, platforms):
    platforms.pages, platforms.long_lived = [PAGE2], False
    user = _user()
    client = _login(app, user)
    _, query = _start(client, "facebook")
    client.get(f"/api/social/oauth/facebook/callback?code=good-code&state={query['state'][0]}")
    assert _result(client)["message"] == "Connected Facebook (Acme Careers)."
    assert _account(user, "facebook").access_token == "PAGE-TOKEN-2" and _account(user, "instagram") is None
    assert platforms.calls[-1][2]["params"]["access_token"] == "SHORT-USER-TOKEN"  # carried on with the first sign-in


def test_several_pages_the_user_chooses_and_tokens_stay_on_the_server(app, platforms):
    platforms.pages = [PAGE, PAGE2]
    user, other = _user(), _user()
    client = _login(app, user)
    _, query = _start(client, "facebook")
    client.get(f"/api/social/oauth/facebook/callback?code=good-code&state={query['state'][0]}")
    assert _account(user, "facebook") is None  # nothing yet

    result = _result(client)
    assert result["ok"] and result["provider"] == "facebook" and len(result["choose"]) >= 32
    assert result["options"] == [{"id": "PAGE1", "name": "Acme Logistics", "detail": "with Instagram @acme.logistics"},
                                 {"id": "PAGE2", "name": "Acme Careers", "detail": "no Instagram account linked"}]
    assert "TOKEN" not in json.dumps(result)

    pending = result["choose"]
    assert _login(app, other).post(f"/api/social/oauth/pending/{pending}", json={"choice": "PAGE2"}).status_code == 400  # not theirs
    assert client.post(f"/api/social/oauth/pending/{pending}", json={"choice": "PAGE9"}).status_code == 400  # not offered
    r = client.post(f"/api/social/oauth/pending/{pending}", json={"choice": "PAGE2"})
    assert r.status_code == 200 and r.get_json()["message"] == "Connected Facebook (Acme Careers)."
    assert _account(user, "facebook").access_token == "PAGE-TOKEN-2" and _account(other, "facebook") is None
    again = client.post(f"/api/social/oauth/pending/{pending}", json={"choice": "PAGE1"})
    assert again.status_code == 400 and "expired" in again.get_json()["error"]  # used up


def test_the_choice_does_not_wait_forever(app, platforms):
    platforms.pages = [PAGE, PAGE2]
    user = _user()
    client = _login(app, user)
    _, query = _start(client, "facebook")
    client.get(f"/api/social/oauth/facebook/callback?code=good-code&state={query['state'][0]}")
    with Session(db.engine) as session:
        row = session.query(db.OAuthPending).filter(db.OAuthPending.user_id == user["id"]).one()
        pending = row.id
        row.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=16)
        session.commit()
    assert "expired" in _result(client)["message"]
    assert client.post(f"/api/social/oauth/pending/{pending}", json={"choice": "PAGE1"}).status_code == 400
    assert _account(user, "facebook") is None


def test_no_page_shared(app, platforms):
    platforms.pages = []
    user = _user()
    client = _login(app, user)
    _, query = _start(client, "facebook")
    client.get(f"/api/social/oauth/facebook/callback?code=good-code&state={query['state'][0]}")
    result = _result(client)
    assert not result["ok"] and "No Facebook Page was shared" in result["message"]


# ── What makes it safe ──────────────────────────────────────────────────────


def test_an_answer_this_browser_did_not_ask_for_is_refused(app, platforms):
    user = _user()
    client = _login(app, user)

    def refused(url):
        r = client.get(url)
        result = _result(client)
        assert r.status_code == 302 and not result["ok"] and "could not be verified" in result["message"], url

    refused("/api/social/oauth/facebook/callback?code=good-code&state=made-up")  # never started
    _, query = _start(client, "facebook")
    state = query["state"][0]
    refused(f"/api/social/oauth/facebook/callback?code=good-code&state={state}x")  # wrong state
    refused(f"/api/social/oauth/facebook/callback?code=good-code&state={state}")  # ...and the real one is spent
    _, query = _start(client, "facebook")
    refused(f"/api/social/oauth/linkedin/callback?code=good-code&state={query['state'][0]}")  # started for another platform
    _, query = _start(client, "facebook")
    refused("/api/social/oauth/facebook/callback?code=good-code")  # no state at all
    assert not platforms.calls and _account(user, "facebook") is None


def test_a_sign_in_started_by_someone_else_or_too_long_ago_is_refused(app, platforms, monkeypatch):
    from api import routes

    user, other = _user(), _user()
    client = _login(app, user)
    _, query = _start(client, "facebook")
    with client.session_transaction() as s:  # the same browser, now signed in as someone else
        s["user_id"] = other["id"]
    client.get(f"/api/social/oauth/facebook/callback?code=good-code&state={query['state'][0]}")
    assert "could not be verified" in _result(client)["message"]

    _, query = _start(client, "facebook")
    real_time = routes.time.time
    monkeypatch.setattr(routes.time, "time", lambda: real_time() + oauth.STATE_TTL_SECONDS + 5)
    client.get(f"/api/social/oauth/facebook/callback?code=good-code&state={query['state'][0]}")
    assert "could not be verified" in _result(client)["message"]
    assert not platforms.calls and _account(other, "facebook") is None and _account(user, "facebook") is None


def test_signed_out_visitors_are_sent_home(app, platforms):
    visitor = app.test_client()
    for url in ("/api/social/oauth/facebook/start", "/api/social/oauth/facebook/callback?code=good-code&state=x"):
        r = visitor.get(url)
        assert r.status_code == 302 and urlparse(r.headers["Location"]).path == "/"
    assert visitor.get("/api/social/oauth/result").status_code == 401
    assert visitor.post("/api/social/oauth/pending/abc", json={"choice": "x"}).status_code == 401
    assert not platforms.calls


@pytest.mark.parametrize("provider, query, message", [
    ("facebook", "error=access_denied&error_reason=user_denied&error_description=Permissions+error.", "You cancelled the Facebook sign-in"),
    ("linkedin", "error=user_cancelled_authorize&error_description=The+user+cancelled", "You cancelled the LinkedIn sign-in"),
    ("linkedin", "error=unauthorized_scope_error&error_description=Scope+not+authorized", "LinkedIn did not complete the sign-in: Scope not authorized"),
])
def test_saying_no_on_the_platform_connects_nothing(app, platforms, provider, query, message):
    user = _user()
    client = _login(app, user)
    _, started = _start(client, provider)
    client.get(f"/api/social/oauth/{provider}/callback?{query}&state={started['state'][0]}")
    result = _result(client)
    assert not result["ok"] and message in result["message"]
    assert not platforms.calls and _account(user, provider) is None


@pytest.mark.parametrize("provider, message", [("facebook", "This authorization code has been used"), ("linkedin", "authorization code not found")])
def test_a_code_the_platform_rejects(app, platforms, provider, message):
    user = _user()
    client = _login(app, user)
    _, query = _start(client, provider)
    client.get(f"/api/social/oauth/{provider}/callback?code=stale-code&state={query['state'][0]}")
    result = _result(client)
    assert not result["ok"] and message in result["message"] and _account(user, provider) is None


def test_the_platform_being_unreachable_is_said_plainly(app, platforms, monkeypatch):
    import requests

    def down(*a, **kw):
        raise requests.exceptions.ConnectionError("no route")

    monkeypatch.setattr(oauth, "_http", down)
    client = _login(app, _user())
    _, query = _start(client, "linkedin")
    client.get(f"/api/social/oauth/linkedin/callback?code=good-code&state={query['state'][0]}")
    assert _result(client) == {"ok": False, "provider": "linkedin", "message": "Could not reach LinkedIn. Please try again."}


# ── LinkedIn ────────────────────────────────────────────────────────────────


def test_linkedin_connects_the_members_profile(app, platforms):
    user = _user()
    client = _login(app, user)
    _, query = _start(client, "linkedin")
    client.get(f"/api/social/oauth/linkedin/callback?code=good-code&state={query['state'][0]}")
    assert _result(client)["message"] == "Connected LinkedIn (Priya Shah)."

    account = _account(user, "linkedin")
    assert (account.account_id, account.account_name, account.access_token) == ("urn:li:person:abc123", "Priya Shah", "LI-TOKEN")
    assert account.connected_via == "oauth" and account.connection_type == "direct"
    lasts = account.token_expires_at - datetime.now(timezone.utc).replace(tzinfo=None)
    assert timedelta(days=59, hours=23) < lasts <= timedelta(days=60)

    exchange = platforms.calls[0]
    assert exchange[0] == "POST" and exchange[2]["data"] == {
        "grant_type": "authorization_code", "code": "good-code", "client_id": "li-123", "client_secret": "li-secret",
        "redirect_uri": "https://app.test/api/social/oauth/linkedin/callback"}
    assert not any("organizationAcls" in c[1] for c in platforms.calls)  # not asked without the permission

    shown = next(a for a in client.get("/api/social/accounts").get_json()["accounts"] if a["platform"] == "linkedin")
    assert shown["token_expires_at"] and shown["expired"] is False and "LI-TOKEN" not in json.dumps(shown)


def test_linkedin_offers_company_pages_when_the_app_may_manage_them(app, platforms):
    platforms.linkedin_scope = "openid profile w_member_social w_organization_social r_organization_admin"
    platforms.organizations = ["urn:li:organization:42"]
    user = _user()
    client = _login(app, user)
    _, query = _start(client, "linkedin")
    client.get(f"/api/social/oauth/linkedin/callback?code=good-code&state={query['state'][0]}")
    result = _result(client)
    assert result["options"] == [{"id": "urn:li:person:abc123", "name": "Priya Shah", "detail": "Your profile"},
                                 {"id": "urn:li:organization:42", "name": "Acme Logistics Ltd", "detail": "Company page"}]
    acls = next(c for c in platforms.calls if c[1].endswith("/rest/organizationAcls"))
    assert acls[2]["params"]["q"] == "roleAssignee" and acls[2]["headers"]["LinkedIn-Version"] == Config.LINKEDIN_API_VERSION

    client.post(f"/api/social/oauth/pending/{result['choose']}", json={"choice": "urn:li:organization:42"})
    account = _account(user, "linkedin")
    assert account.account_id == "urn:li:organization:42" and account.account_name == "Acme Logistics Ltd" and account.token_expires_at


# ── Connections that run out ────────────────────────────────────────────────


def _linkedin_expiring(user, days):
    when = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)
    db.save_social_account(user_id=user["id"], platform="linkedin", account_name="Priya Shah", account_id="urn:li:person:abc123",
                           access_token="LI-TOKEN", connected_via="oauth", token_expires_at=when)
    return when


def test_an_expired_connection_is_not_published_to():
    user = _user()
    _linkedin_expiring(user, days=-1)
    assert publish_service.connections(user["id"])["linkedin"] is None
    with pytest.raises(publish_service.PublishError, match="connection has expired"):
        publish_service.publish_item(user["id"], {"platform": "linkedin", "caption": "Hello", "media": "none"}, Config.UPLOAD_FOLDER)
    _linkedin_expiring(user, days=20)  # connected again
    assert publish_service.connections(user["id"])["linkedin"] == "Priya Shah"


def test_a_heads_up_a_week_before_and_once_it_has_run_out():
    soon, later, gone = _user(), _user(), _user()
    when = _linkedin_expiring(soon, days=5)
    _linkedin_expiring(later, days=30)
    _linkedin_expiring(gone, days=-2)

    oauth.notify_expiring()
    oauth.notify_expiring()  # the hourly check again: no repeats
    [note] = db.list_notifications(soon["id"])["items"]
    assert note["title"] == f"Your LinkedIn connection expires on {when.strftime('%d %b')}" and note["url"] == "/settings"
    assert db.list_notifications(later["id"])["items"] == []
    [expired] = db.list_notifications(gone["id"])["items"]
    assert expired["title"] == "Your LinkedIn connection has expired"


def test_typing_new_details_by_hand_replaces_a_one_click_connection():
    user = _user()
    _linkedin_expiring(user, days=3)
    db.save_social_account(user_id=user["id"], platform="linkedin", account_name="Acme", account_id="urn:li:organization:42", access_token="HAND-TOKEN")
    account = _account(user, "linkedin")
    assert account.connected_via == "manual" and account.token_expires_at is None and account.access_token == "HAND-TOKEN"
    db.save_social_account(user_id=user["id"], platform="linkedin", account_name="Acme Ltd")  # a rename keeps the token's facts
    assert _account(user, "linkedin").access_token == "HAND-TOKEN"


def test_settings_page_carries_the_connect_buttons(app):
    """The cards (and their Connect buttons) are drawn by settings.js from
    /api/social/oauth/providers; the page carries the containers and the
    script's start addresses."""
    html = _login(app, _user(is_admin=True)).get("/settings").get_data(as_text=True)
    assert 'id="socialCards"' in html and 'id="oauthChooseModal"' in html and 'id="oauthBanner"' in html
    script = open("static/js/settings.js", encoding="utf-8").read()
    for start in ("/connect/facebook", "/connect/linkedin", "/connect/youtube", "/connect/instagram"):
        assert start in script


# ── YouTube: the same check on Google's answer ──────────────────────────────


class GoogleResp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


@pytest.fixture
def google(monkeypatch):
    from api import routes

    calls = []
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "yt-123")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "yt-secret")
    monkeypatch.setattr(routes.requests, "post", lambda url, **kw: calls.append(("POST", url, kw)) or GoogleResp(
        {"access_token": "YT-TOKEN", "refresh_token": "YT-REFRESH"}))
    monkeypatch.setattr(routes.requests, "get", lambda url, **kw: calls.append(("GET", url, kw)) or GoogleResp(
        {"items": [{"id": "CHANNEL1", "snippet": {"title": "Acme TV"}}]}))
    return calls


def _youtube_state(client):
    target = urlparse(client.get("/api/auth/youtube").get_json()["auth_url"])
    return parse_qs(target.query)["state"][0]


def test_youtube_connects_the_signed_in_user(app, google):
    user = _user()
    client = _login(app, user)
    state = _youtube_state(client)
    assert state != str(user["id"]) and len(state) >= 32  # not the user's id any more

    r = client.get(f"/api/auth/youtube/callback?code=good-code&state={state}")
    assert r.status_code == 302 and r.headers["Location"].endswith("/settings")
    assert _result(client) == {"ok": True, "provider": "youtube", "message": "Connected YouTube (Acme TV)."}
    account = _account(user, "youtube")
    assert (account.account_id, account.account_name, account.access_token, account.refresh_token) == ("CHANNEL1", "Acme TV", "YT-TOKEN", "YT-REFRESH")
    assert google[0][2]["data"]["client_secret"] == "yt-secret"


def test_a_youtube_answer_naming_another_user_is_refused(app, google):
    victim, attacker = _user(), _user()
    # The old hole: state was simply the user's id, with no sign-in needed
    visitor = app.test_client()
    r = visitor.get(f"/api/auth/youtube/callback?code=good-code&state={victim['id']}")
    assert r.status_code == 302 and urlparse(r.headers["Location"]).path == "/"

    client = _login(app, attacker)
    client.get(f"/api/auth/youtube/callback?code=good-code&state={victim['id']}")  # signed in, but never started for the victim
    assert "could not be verified" in _result(client)["message"]
    state = _youtube_state(client)
    client.get(f"/api/auth/youtube/callback?code=good-code&state={state}")
    assert _account(attacker, "youtube").account_id == "CHANNEL1"  # their own account, as it should be
    client.get(f"/api/auth/youtube/callback?code=good-code&state={state}")  # the state is spent
    assert "could not be verified" in _result(client)["message"]
    assert _account(victim, "youtube") is None


def test_nothing_from_the_address_is_shown_as_a_page(app, google):
    client = _login(app, _user())
    state = _youtube_state(client)
    r = client.get(f"/api/auth/youtube/callback?error=%3Cscript%3Ealert(1)%3C/script%3E&state={state}")
    assert r.status_code == 302 and "<script>alert" not in r.get_data(as_text=True)
    assert _result(client)["message"] == "The YouTube sign-in was cancelled, so nothing was connected."
    r = app.test_client().get("/api/auth/youtube/callback?error=%3Cscript%3Ealert(1)%3C/script%3E")
    assert "<script>alert" not in r.get_data(as_text=True)
    assert not google


# ── Simple connect page (Settings: one card per network) ────────────────────


def test_settings_shows_simple_cards_with_tokens_tucked_under_advanced(app):
    user = _user()
    with Session(db.engine) as session:  # a fully set-up account, so Settings opens
        session.query(db.User).filter(db.User.id == user["id"]).update({"email_verified": True, "onboarding_completed": True})
        session.commit()
    html = _login(app, user).get("/settings").get_data(as_text=True)
    assert 'id="socialCards"' in html and 'id="socialAdvanced"' in html
    assert 'id="fbStatusBadge"' not in html  # the old token-first cards are gone


def test_providers_say_when_youtube_one_click_is_set_up(app, monkeypatch):
    client = _login(app, _user())
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    assert client.get("/api/social/oauth/providers").get_json()["providers"]["youtube"] is False
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "yt-123")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "yt-secret")
    assert client.get("/api/social/oauth/providers").get_json()["providers"]["youtube"] is True


def test_the_expiry_heads_up_is_emailed_too(monkeypatch):
    from services import email_service

    sent = []
    monkeypatch.setattr(Config, "APP_BASE_URL", "https://app.test")
    monkeypatch.setattr(email_service.EmailService, "send_billing_email", lambda self, **kw: sent.append(kw))
    user = _user()
    _linkedin_expiring(user, days=3)
    oauth.notify_expiring()
    oauth.notify_expiring()  # once only
    [mail] = [m for m in sent if m["to_email"] == user["email"]]
    assert mail["button"] == "Reconnect LinkedIn" and mail["url"] == "https://app.test/settings"


def test_a_failed_reminder_email_still_leaves_the_bell_note(monkeypatch):
    from services import email_service

    def boom(self, **kw):
        raise RuntimeError("SMTP is not configured")

    monkeypatch.setattr(Config, "APP_BASE_URL", "https://app.test")
    monkeypatch.setattr(email_service.EmailService, "send_billing_email", boom)
    user = _user()
    _linkedin_expiring(user, days=-1)
    oauth.notify_expiring()
    assert db.list_notifications(user["id"])["items"][0]["title"] == "Your LinkedIn connection has expired"


# ── Instagram on its own (Instagram API with Instagram Login) ───────────────


class FakeInstagram:
    """Answers Instagram's sign-in, token and profile calls, and records them."""

    def __init__(self):
        self.calls = []
        self.long_lived = True

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        params, data = kw.get("params") or {}, kw.get("data") or {}
        if url == oauth.INSTAGRAM_TOKEN:
            if data.get("code") != "ig-code":
                return Resp(400, {"error_type": "OAuthException", "code": 400, "error_message": "Invalid authorization code"})
            return Resp(200, {"data": [{"access_token": "IGSHORT", "user_id": "1789", "permissions": "instagram_business_basic"}]})
        if url.endswith("graph.instagram.com/access_token"):
            if not self.long_lived:
                return Resp(400, {"error": {"message": "nope"}})
            return Resp(200, {"access_token": "IGLONG", "token_type": "bearer", "expires_in": 5184000})
        if url.endswith("/me"):
            return Resp(200, {"user_id": "17841400000000001", "username": "priya.makes", "account_type": "MEDIA_CREATOR"})
        if url.endswith("/refresh_access_token"):
            return Resp(200, {"access_token": params["access_token"] + "-RENEWED", "expires_in": 5184000})
        return Resp(404, {"message": f"unexpected {method} {url}"})


@pytest.fixture
def instagram(monkeypatch):
    fake = FakeInstagram()
    monkeypatch.setattr(oauth, "_http", fake)
    for name, value in (("INSTAGRAM_APP_ID", "ig-app-1"), ("INSTAGRAM_APP_SECRET", "ig-secret"), ("APP_BASE_URL", "https://app.test"),
                        ("INSTAGRAM_OAUTH_SCOPES", "instagram_business_basic,instagram_business_content_publish")):
        monkeypatch.setattr(Config, name, value)
    return fake


def test_instagram_sign_in_goes_to_instagram_itself(app, instagram):
    client = _login(app, _user())
    assert client.get("/api/social/oauth/providers").get_json()["providers"]["instagram"] is True
    target, query = _start(client, "instagram")
    assert target.netloc == "www.instagram.com" and target.path == "/oauth/authorize"
    assert query["client_id"] == ["ig-app-1"] and query["enable_fb_login"] == ["0"]
    assert query["scope"] == ["instagram_business_basic,instagram_business_content_publish"]
    assert query["redirect_uri"] == ["https://app.test/api/social/oauth/instagram/callback"] and "secret" not in target.query


def test_an_instagram_account_without_a_page_is_connected_for_60_days(app, instagram):
    user = _user()
    client = _login(app, user)
    _, query = _start(client, "instagram")
    r = client.get(f"/api/social/oauth/instagram/callback?code=ig-code%23_&state={query['state'][0]}")
    assert r.status_code == 302
    assert _result(client) == {"ok": True, "provider": "instagram", "message": "Connected Instagram (@priya.makes)."}
    account = _account(user, "instagram")
    assert (account.account_id, account.account_name, account.access_token) == ("17841400000000001", "@priya.makes", "IGLONG")
    days = (account.token_expires_at - datetime.now(timezone.utc).replace(tzinfo=None)).days
    assert 58 <= days <= 60 and _account(user, "facebook") is None
    exchange = instagram.calls[0][2]["data"]
    assert exchange["code"] == "ig-code" and exchange["client_secret"] == "ig-secret"  # "#_" removed
    assert "IGLONG" not in json.dumps(client.get("/api/social/accounts").get_json())


def test_a_refused_instagram_code_is_explained(app, instagram):
    client = _login(app, _user())
    _, query = _start(client, "instagram")
    client.get(f"/api/social/oauth/instagram/callback?code=used&state={query['state'][0]}")
    result = _result(client)
    assert result["ok"] is False and "Invalid authorization code" in result["message"]


def test_cancelling_instagram_connects_nothing(app, instagram):
    user = _user()
    client = _login(app, user)
    _, query = _start(client, "instagram")
    client.get(f"/api/social/oauth/instagram/callback?error=access_denied&error_reason=user_denied&state={query['state'][0]}")
    assert "cancelled" in _result(client)["message"] and _account(user, "instagram") is None


def test_instagram_login_tokens_are_renewed_before_they_run_out(instagram):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    soon, later, page_linked = _user(), _user(), _user()
    for user, token, days in ((soon, "IGSOON", 10), (later, "IGLATER", 50), (page_linked, "EAAPAGE", 10)):
        db.save_social_account(user_id=user["id"], platform="instagram", account_name="@x", account_id="1", access_token=token,
                               connected_via="oauth", token_expires_at=now + timedelta(days=days))
    assert oauth.refresh_instagram_tokens() >= 1
    renewed = _account(soon, "instagram")
    assert renewed.access_token == "IGSOON-RENEWED" and (renewed.token_expires_at - now).days >= 59
    assert _account(later, "instagram").access_token == "IGLATER"  # plenty of time left
    assert _account(page_linked, "instagram").access_token == "EAAPAGE"  # a Facebook Page token: left alone


def test_instagram_login_tokens_publish_through_graph_instagram():
    from services import instagram_service

    assert instagram_service.graph_url("123/media", "IGAAtoken").startswith("https://graph.instagram.com/")
    assert instagram_service.graph_url("123/media", "EAAtoken").startswith("https://graph.facebook.com/")
    assert instagram_service.graph_url("123/media").startswith("https://graph.facebook.com/")


# ── Our own connect page ("AVIR AI wants to connect to your Facebook") ──────


def _ready_user():
    user = _user()
    with Session(db.engine) as session:  # a fully set-up account, so pages open
        session.query(db.User).filter(db.User.id == user["id"]).update({"email_verified": True, "onboarding_completed": True})
        session.commit()
    return user


def test_the_connect_page_leads_to_the_networks_own_sign_in(app, platforms):
    html = _login(app, _ready_user()).get("/connect/facebook").get_data(as_text=True)
    assert "AVIR AI wants to connect to your Facebook" in html
    assert 'href="/api/social/oauth/facebook/start"' in html and "meta-secret" not in html


def test_the_connect_page_only_exists_once_the_app_is_set_up(app, monkeypatch):
    monkeypatch.setattr(Config, "META_APP_ID", "")
    client = _login(app, _ready_user())
    assert client.get("/connect/facebook").headers["Location"].endswith("/settings")
    assert client.get("/connect/tiktok").headers["Location"].endswith("/settings")
    assert app.test_client().get("/connect/facebook").status_code in (302, 401)  # signed out

