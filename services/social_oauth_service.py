"""One-click "Connect with Facebook / LinkedIn" (OAuth 2.0, authorization code).

Instead of copying IDs and access tokens into Settings, the user clicks
Connect, signs in on the platform, approves, and comes back connected:

    /api/social/oauth/<provider>/start      -> the platform's sign-in page
    /api/social/oauth/<provider>/callback   <- back with a one-time code
        the code is exchanged (with this app's secret) for an access token,
        the accounts it can post to are looked up, and
          one account  -> connected at once
          several      -> the user picks one (the choices wait in
                          oauth_pending for 15 minutes; their tokens stay
                          on the server)

  facebook  a Facebook Page, and the Instagram account linked to it
  linkedin  the member's own profile, plus the company pages they
            administer when the app has the organisation permissions

A provider is offered only when its app credentials are set
(META_APP_ID + META_APP_SECRET, LINKEDIN_CLIENT_ID + LINKEDIN_CLIENT_SECRET);
entering details by hand in Settings keeps working either way. Setup:
docs/social-connect-setup.md.

The requests follow Meta's and LinkedIn's documentation and are exercised
against simulated services (tests/test_social_oauth.py). They have not been
run against the live services from here, which need the apps to be created
and approved first.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

from config import Config

logger = logging.getLogger(__name__)

PROVIDERS = ("facebook", "linkedin")
NAMES = {"facebook": "Facebook", "linkedin": "LinkedIn", "instagram": "Instagram"}
STATE_TTL_SECONDS = 600
PENDING_TTL_MINUTES = 15
EXPIRY_WARNING_DAYS = 7
LINKEDIN_AUTH = "https://www.linkedin.com/oauth/v2"
LINKEDIN_API = "https://api.linkedin.com"
_ORG_ADMIN_SCOPES = ("r_organization_admin", "rw_organization_admin")


class OAuthError(RuntimeError):
    """Why connecting did not work, in words for the user."""


def _http(method: str, url: str, **kwargs):
    return requests.request(method, url, timeout=kwargs.pop("timeout", 20), **kwargs)


# ── Configuration ───────────────────────────────────────────────────────────


def credentials(provider: str) -> tuple[str, str] | None:
    pair = {"facebook": (Config.META_APP_ID, Config.META_APP_SECRET),
            "linkedin": (Config.LINKEDIN_CLIENT_ID, Config.LINKEDIN_CLIENT_SECRET)}.get(provider)
    return pair if pair and pair[0] and pair[1] else None


def enabled(provider: str) -> bool:
    return credentials(provider) is not None


def providers() -> dict[str, bool]:
    return {p: enabled(p) for p in PROVIDERS}


def redirect_uri(provider: str, base_url: str) -> str:
    """Where the platform sends the user back. Must be registered, exactly, in the platform's app."""
    return f"{base_url.rstrip('/')}/api/social/oauth/{provider}/callback"


def new_state() -> str:
    return secrets.token_urlsafe(32)


def authorize_url(provider: str, state: str, base_url: str) -> str:
    client_id = (credentials(provider) or ("", ""))[0]
    params = {"client_id": client_id, "redirect_uri": redirect_uri(provider, base_url), "state": state, "response_type": "code"}
    if provider == "facebook":
        if Config.META_LOGIN_CONFIG_ID:  # Facebook Login for Business: the permissions live in the configuration
            params["config_id"] = Config.META_LOGIN_CONFIG_ID
        else:
            params["scope"] = Config.META_OAUTH_SCOPES
        return f"https://www.facebook.com/{Config.META_GRAPH_VERSION}/dialog/oauth?{urlencode(params)}"
    params["scope"] = Config.LINKEDIN_OAUTH_SCOPES
    return f"{LINKEDIN_AUTH}/authorization?{urlencode(params)}"


def cancelled(provider: str, error: str | None) -> bool:
    """The user closed the platform's sign-in or said no (not a fault)."""
    return (error or "") in ("access_denied", "user_cancelled_login", "user_cancelled_authorize")


# ── Facebook: the Pages the user manages, each with its own token ────────────


def _graph(path: str, **params) -> dict:
    resp = _http("GET", f"https://graph.facebook.com/{Config.META_GRAPH_VERSION}/{path.lstrip('/')}", params=params)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200 or not isinstance(body, dict) or "error" in body:
        error = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else {}
        raise OAuthError(f"Facebook did not complete the sign-in: {error.get('message') or 'unexpected answer'}.")
    return body


def facebook_options(code: str, base_url: str) -> list[dict]:
    app_id, app_secret = credentials("facebook")
    token = _graph("oauth/access_token", client_id=app_id, client_secret=app_secret, code=code,
                   redirect_uri=redirect_uri("facebook", base_url)).get("access_token")
    if not token:
        raise OAuthError("Facebook did not complete the sign-in. Please try again.")
    try:  # a long-lived sign-in gives Page tokens that keep working
        token = _graph("oauth/access_token", grant_type="fb_exchange_token", client_id=app_id, client_secret=app_secret,
                       fb_exchange_token=token).get("access_token") or token
    except OAuthError as err:
        logger.warning(f"Facebook long-lived token exchange failed, continuing with the short one: {err}")
    pages, after = [], None
    for _ in range(5):  # up to 500 Pages
        params = {"fields": "id,name,access_token,instagram_business_account{id,username}", "limit": 100, "access_token": token}
        if after:
            params["after"] = after
        body = _graph("me/accounts", **params)
        pages.extend(p for p in body.get("data") or [] if p.get("id") and p.get("access_token"))
        after = ((body.get("paging") or {}).get("cursors") or {}).get("after") if (body.get("paging") or {}).get("next") else None
        if not after:
            break
    if not pages:
        raise OAuthError("No Facebook Page was shared. You need to be an admin of a Page, and to tick it when Facebook asks "
                         "which Pages this app may use.")
    options = []
    for page in pages:
        ig = page.get("instagram_business_account") or {}
        instagram = {"id": str(ig["id"]), "username": ig.get("username") or ""} if ig.get("id") else None
        options.append({
            "id": str(page["id"]), "name": page.get("name") or "Facebook Page", "token": page["access_token"], "instagram": instagram,
            "detail": (f"with Instagram @{instagram['username']}" if instagram and instagram["username"] else "with its Instagram account")
            if instagram else "no Instagram account linked",
        })
    return options


# ── LinkedIn: the member's profile, and the company pages they administer ────


def _linkedin_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "LinkedIn-Version": Config.LINKEDIN_API_VERSION, "X-Restli-Protocol-Version": "2.0.0"}


def _linkedin_pages(token: str) -> list[dict]:
    """Company pages the member administers: [{id: urn, name}]. Needs an organisation-admin permission."""
    resp = _http("GET", f"{LINKEDIN_API}/rest/organizationAcls", headers=_linkedin_headers(token),
                 params={"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED", "count": 50})
    if resp.status_code != 200:
        logger.warning(f"LinkedIn company pages could not be listed ({resp.status_code})")
        return []
    pages = []
    for element in (resp.json().get("elements") or []):
        urn = element.get("organization") or element.get("organizationTarget")
        if not urn or any(p["id"] == urn for p in pages):
            continue
        org_id = str(urn).rsplit(":", 1)[-1]
        name = f"Company page {org_id}"
        try:
            detail = _http("GET", f"{LINKEDIN_API}/rest/organizations/{org_id}", headers=_linkedin_headers(token))
            if detail.status_code == 200:
                name = detail.json().get("localizedName") or name
        except requests.exceptions.RequestException:
            pass  # the page can still be connected under its number
        pages.append({"id": urn, "name": name})
    return pages


def linkedin_options(code: str, base_url: str, now: datetime | None = None) -> list[dict]:
    client_id, client_secret = credentials("linkedin")
    resp = _http("POST", f"{LINKEDIN_AUTH}/accessToken", headers={"Content-Type": "application/x-www-form-urlencoded"},
                 data={"grant_type": "authorization_code", "code": code, "client_id": client_id, "client_secret": client_secret,
                       "redirect_uri": redirect_uri("linkedin", base_url)})
    try:
        body = resp.json()
    except ValueError:
        body = {}
    token = body.get("access_token") if resp.status_code == 200 else None
    if not token:
        raise OAuthError(f"LinkedIn did not complete the sign-in: {body.get('error_description') or body.get('error') or 'unexpected answer'}.")
    expires_at = None
    if body.get("expires_in"):
        expires_at = ((now or datetime.now(timezone.utc)) + timedelta(seconds=int(body["expires_in"]))).isoformat()
    shared = {"token": token, "refresh_token": body.get("refresh_token"), "expires_at": expires_at}

    me = _http("GET", f"{LINKEDIN_API}/v2/userinfo", headers={"Authorization": f"Bearer {token}"})
    info = me.json() if me.status_code == 200 else {}
    if not info.get("sub"):
        raise OAuthError("LinkedIn did not share your profile. The LinkedIn app needs the 'Sign In with LinkedIn using OpenID Connect' product.")
    options = [{"id": f"urn:li:person:{info['sub']}", "name": info.get("name") or "Your LinkedIn profile", "detail": "Your profile", **shared}]
    granted = str(body.get("scope") or "").replace(",", " ").split()
    if any(scope in granted for scope in _ORG_ADMIN_SCOPES):
        options += [{**page, "detail": "Company page", **shared} for page in _linkedin_pages(token)]
    return options


# ── Connecting ──────────────────────────────────────────────────────────────


def connect(user_id: int, provider: str, option: dict) -> list[dict]:
    """Saves the chosen account. -> what is now connected: [{platform, name}]"""
    from db import save_social_account

    if provider == "facebook":
        save_social_account(user_id=user_id, platform="facebook", account_name=option["name"], account_id=option["id"],
                            access_token=option["token"], connection_type="direct", connected_via="oauth", token_expires_at=None)
        connected = [{"platform": "facebook", "name": option["name"]}]
        instagram = option.get("instagram")
        if instagram:  # published through the Page's token
            name = f"@{instagram['username']}" if instagram.get("username") else "Instagram"
            save_social_account(user_id=user_id, platform="instagram", account_name=name, account_id=instagram["id"],
                                access_token=option["token"], connection_type="direct", connected_via="oauth", token_expires_at=None)
            connected.append({"platform": "instagram", "name": name})
        return connected
    expires = datetime.fromisoformat(option["expires_at"]).astimezone(timezone.utc).replace(tzinfo=None) if option.get("expires_at") else None
    save_social_account(user_id=user_id, platform="linkedin", account_name=option["name"], account_id=option["id"],
                        access_token=option["token"], refresh_token=option.get("refresh_token"), connection_type="direct",
                        connected_via="oauth", token_expires_at=expires)
    return [{"platform": "linkedin", "name": option["name"]}]


def connected_text(connected: list[dict]) -> str:
    return "Connected " + " and ".join(f"{NAMES.get(c['platform'], c['platform'])} ({c['name']})" for c in connected) + "."


def finish(user_id: int, provider: str, code: str, base_url: str) -> dict:
    """After the platform sent the user back: {"connected": [...]} or, with
    several accounts to choose from, {"choose": <pending id>}."""
    from db import create_oauth_pending

    options = facebook_options(code, base_url) if provider == "facebook" else linkedin_options(code, base_url)
    if len(options) == 1:
        return {"connected": connect(user_id, provider, options[0])}
    return {"choose": create_oauth_pending(user_id, provider, options, PENDING_TTL_MINUTES)}


def pending_options(user_id: int, pending_id: str) -> dict | None:
    """The accounts to choose from, without their tokens."""
    from db import get_oauth_pending

    pending = get_oauth_pending(user_id, pending_id, PENDING_TTL_MINUTES)
    if not pending:
        return None
    return {"provider": pending["provider"],
            "options": [{"id": o["id"], "name": o["name"], "detail": o.get("detail") or ""} for o in pending["options"]]}


def choose(user_id: int, pending_id: str, option_id: str) -> list[dict]:
    from db import delete_oauth_pending, get_oauth_pending

    pending = get_oauth_pending(user_id, pending_id, PENDING_TTL_MINUTES)
    if not pending:
        raise OAuthError("That sign-in has expired. Click Connect again.")
    option = next((o for o in pending["options"] if str(o["id"]) == str(option_id)), None)
    if not option:
        raise OAuthError("Choose one of the accounts listed.")
    connected = connect(user_id, pending["provider"], option)
    delete_oauth_pending(pending_id)
    return connected


# ── Sign-ins that run out (LinkedIn's last 60 days) ─────────────────────────


def notify_expiring(now: datetime | None = None) -> int:
    """Scheduler: a bell notification a week before a connection expires, and
    one when it has. Returns how many were added."""
    from db import create_notification, expiring_social_accounts

    now = now or datetime.now(timezone.utc)
    added = 0
    for acc in expiring_social_accounts(now.replace(tzinfo=None) + timedelta(days=EXPIRY_WARNING_DAYS)):
        name = NAMES.get(acc["platform"], acc["platform"])
        expires = acc["token_expires_at"]
        gone = expires <= now.replace(tzinfo=None)
        title = f"Your {name} connection has expired" if gone else f"Your {name} connection expires on {expires.strftime('%d %b')}"
        body = (f"Posts can't be published to {name} until you connect it again in Settings." if gone
                else f"Connect {name} again in Settings before then, so scheduled posts keep going out.")
        if create_notification(acc["user_id"], "social_connection", title, body, "/settings",
                               f"oauth-expiry:{acc['id']}:{expires.date().isoformat()}:{'gone' if gone else 'soon'}"):
            added += 1
    return added
