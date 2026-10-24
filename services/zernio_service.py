"""Networks we don't have our own app for (X, Threads, ...) through a unified
social API: Zernio (formerly Late, https://docs.zernio.com).

Zernio owns approved apps for these networks. Each AVIR user gets one Zernio
profile (users.zernio_profile_id); their accounts are connected to it:

    /api/social/unified/<platform>/start  -> Zernio's connect link (authUrl)
                                             -> the network's own sign-in
    /api/social/unified/callback          <- back with accountId and username
        the account is checked against the user's own profile, then saved as
        a SocialAccount with connection_type "zernio" (no token kept here:
        Zernio holds it)

Publishing (publish_service.publish_item) sends POST /posts with
publishNow; disconnecting removes the account at Zernio too.

Off until ZERNIO_API_KEY is set. ZERNIO_PLATFORMS lists the networks offered
(default "twitter,threads"); Facebook, Instagram, LinkedIn and YouTube keep
using our own apps (services/social_oauth_service.py).

The requests follow Zernio's documentation and are tested against a
simulated service (tests/test_zernio.py), not the live one.
"""

import logging

import requests

from config import Config

logger = logging.getLogger(__name__)

NAMES = {
    "twitter": "X",
    "threads": "Threads",
    "tiktok": "TikTok",
    "pinterest": "Pinterest",
    "bluesky": "Bluesky",
    "googlebusiness": "Google Business Profile",
    "reddit": "Reddit",
}
TIMEOUT = 30
PUBLISH_TIMEOUT = 120  # publishNow waits for the network


class ZernioError(RuntimeError):
    """Why it didn't work, in words for the user."""


def enabled() -> bool:
    return bool(Config.ZERNIO_API_KEY)


def platforms() -> list[str]:
    """The networks offered through Zernio (none while it isn't set up)."""
    if not enabled():
        return []
    return [p.strip().lower() for p in (Config.ZERNIO_PLATFORMS or "").split(",") if p.strip().lower() in NAMES]


def _request(method: str, path: str, timeout: int = TIMEOUT, **kwargs) -> tuple[int, dict]:
    resp = requests.request(
        method,
        f"{Config.ZERNIO_API_BASE.rstrip('/')}/{path.lstrip('/')}",
        headers={"Authorization": f"Bearer {Config.ZERNIO_API_KEY}"},
        timeout=timeout,
        **kwargs,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body if isinstance(body, dict) else {}


def _message(body: dict, fallback: str) -> str:
    error = body.get("error")
    if isinstance(error, dict):
        error = error.get("message")
    return str(body.get("message") or error or fallback)[:300]


# ── Profiles: one per AVIR user ─────────────────────────────────────────────


def profile_id(user_id: int) -> str:
    """The user's Zernio profile, created the first time."""
    from sqlalchemy.orm import Session

    from db import User, engine

    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            raise ZernioError("Please sign in again.")
        if user.zernio_profile_id:
            return user.zernio_profile_id
        status, body = _request(
            "POST", "profiles", json={"name": f"AVIR user {user_id}", "externalId": f"avir-{user_id}"}
        )
        if status == 409:  # made before but not saved here (e.g. a crash): find it again
            found = ((body.get("details") or {}).get("existingProfileId")) or _find_profile(user_id)
            if not found:
                raise ZernioError("Could not set up the connection. Please try again.")
            user.zernio_profile_id = str(found)
        elif status in (200, 201) and (body.get("profile") or {}).get("_id"):
            user.zernio_profile_id = str(body["profile"]["_id"])
        else:
            raise ZernioError(f"Could not set up the connection: {_message(body, f'HTTP {status}')}")
        session.commit()
        return user.zernio_profile_id


def _find_profile(user_id: int) -> str | None:
    status, body = _request("GET", "profiles")
    for profile in (body.get("profiles") or []) if status == 200 else []:
        if profile.get("externalId") == f"avir-{user_id}":
            return profile.get("_id")
    return None


# ── Connecting ──────────────────────────────────────────────────────────────


def connect_url(user_id: int, platform: str, redirect_url: str) -> str:
    """Zernio's link that leads to the network's sign-in."""
    if platform not in platforms():
        raise ZernioError(f"Connecting {NAMES.get(platform, platform)} isn't available.")
    status, body = _request(
        "GET", f"connect/{platform}", params={"profileId": profile_id(user_id), "redirect_url": redirect_url}
    )
    if status != 200 or not body.get("authUrl"):
        raise ZernioError(f"{NAMES[platform]} sign-in could not be started: {_message(body, f'HTTP {status}')}")
    return body["authUrl"]


def confirm_account(user_id: int, platform: str, account_id: str) -> dict:
    """The account Zernio says was connected, checked against the user's own
    profile (so a link with someone else's accountId connects nothing).
    -> {id, platform, name}"""
    pid = profile_id(user_id)
    status, body = _request("GET", "accounts", params={"profileId": pid, "platform": platform})
    if status != 200:
        raise ZernioError(f"Could not confirm the {NAMES.get(platform, platform)} account. Please try again.")
    for acc in body.get("accounts") or []:
        if str(acc.get("_id")) == str(account_id) and acc.get("platform") == platform:
            name = acc.get("username") or acc.get("displayName") or NAMES.get(platform, platform)
            if platform in ("twitter", "threads") and not name.startswith("@"):
                name = f"@{name}"
            return {"id": str(acc["_id"]), "platform": platform, "name": name}
    raise ZernioError(
        f"That {NAMES.get(platform, platform)} account isn't connected to your workspace. Please click Connect again."
    )


def disconnect(account_id: str) -> None:
    """Removes the account at Zernio (already gone counts as done)."""
    status, body = _request("DELETE", f"accounts/{account_id}")
    if status not in (200, 204, 404):
        logger.warning(f"Zernio disconnect of {account_id} answered {status}: {_message(body, '')}")


# ── Publishing ──────────────────────────────────────────────────────────────


def _post_url(body: dict, platform: str) -> str | None:
    post = body.get("post") if isinstance(body.get("post"), dict) else body
    for entry in post.get("platforms") or []:
        if entry.get("platform") == platform and entry.get("platformPostUrl"):
            return entry["platformPostUrl"]
    return post.get("platformPostUrl") or body.get("platformPostUrl")


def _failed(body: dict, platform: str) -> str | None:
    post = body.get("post") if isinstance(body.get("post"), dict) else body
    for entry in post.get("platforms") or []:
        if entry.get("platform") == platform and entry.get("status") == "failed":
            return entry.get("errorMessage") or entry.get("error") or "the network refused the post"
    return None


def publish(account_id: str, platform: str, text: str, image_urls: list[str]) -> dict:
    """Publishes now. image_urls: public HTTPS addresses.
    -> {post_id, permalink, format}"""
    payload = {"content": text, "publishNow": True, "platforms": [{"platform": platform, "accountId": account_id}]}
    if image_urls:
        payload["mediaItems"] = [{"type": "image", "url": url} for url in image_urls]
    status, body = _request("POST", "posts", timeout=PUBLISH_TIMEOUT, json=payload)
    name = NAMES.get(platform, platform)
    if status == 409:
        raise ZernioError(f"{name} already has this exact post. Change the text a little to post it again.")
    if status == 429:
        raise ZernioError(f"{name}'s posting limit has been reached. Try again later.")
    if status not in (200, 201, 202, 207):
        raise ZernioError(f"{name}: {_message(body, f'HTTP {status}')}")
    reason = _failed(body, platform)
    if status == 207 or reason:
        raise ZernioError(f"{name} did not publish the post: {reason or _message(body, 'unknown reason')}")
    post = body.get("post") if isinstance(body.get("post"), dict) else body
    post_id = post.get("_id") or body.get("postId")
    return {
        "post_id": str(post_id) if post_id else None,
        "permalink": _post_url(body, platform),
        "format": "image" if image_urls else "text",
    }
