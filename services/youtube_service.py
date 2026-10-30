"""YouTube: publishing a post's video, and how it does.

  tokens     Google access tokens last an hour. Each connection keeps its
             refresh token (/api/auth/youtube asks for offline access), and
             the scheduler renews the access token a few minutes before it
             runs out (refresh_tokens); publishing renews it first too. A
             refresh token Google no longer accepts (revoked, or unused for
             months) sets the connection's status to "expired", so Settings
             asks to reconnect and it isn't tried again.
  publish    the item's video (an .mp4 AVIR made, in the uploads folder) as a
             public upload: title = the caption's first line, description =
             caption + hashtags. Short vertical videos show as Shorts.
  results    views, likes and comments (Data API videos.statistics), and the
             channel's subscribers, with the same token. Watch time would need
             the YouTube Analytics API and another permission: not used.

Needs YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET (the Google app that connects
channels) for renewing tokens.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)
TIMEOUT = 20
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
RENEW_WITHIN = timedelta(minutes=10)
MAX_TITLE = 100
MAX_DESCRIPTION = 5000


class YouTubeError(RuntimeError):
    """Shown to the person as is."""


def _renew(acc, now: datetime) -> bool:
    """Swap the connection's refresh token for a new access token (acc: a
    db.SocialAccount in an open session). False when Google says no."""
    client_id, secret = os.getenv("YOUTUBE_CLIENT_ID"), os.getenv("YOUTUBE_CLIENT_SECRET")
    if not (client_id and secret and acc.refresh_token):
        return False
    resp = requests.post(TOKEN_URL, data={"client_id": client_id, "client_secret": secret,
                                          "refresh_token": acc.refresh_token, "grant_type": "refresh_token"}, timeout=TIMEOUT)
    body = resp.json() if resp.content else {}
    if resp.status_code >= 400 or not body.get("access_token"):
        if body.get("error") == "invalid_grant":  # revoked or expired for good: Settings asks to connect again
            acc.status = "expired"
        logger.warning(f"YouTube token renewal for account {acc.id} failed: {body.get('error') or resp.status_code}")
        return False
    acc.access_token = body["access_token"]
    acc.token_expires_at = now + timedelta(seconds=int(body.get("expires_in") or 3600))
    return True


def refresh_tokens(now: datetime | None = None) -> int:
    """Scheduler: renew every connected channel's token that runs out within
    RENEW_WITHIN (or whose end isn't known). How many were renewed."""
    from sqlalchemy.orm import Session

    from db import SocialAccount, engine

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    renewed = 0
    with Session(engine) as session:
        rows = (session.query(SocialAccount)
                .filter(SocialAccount.platform == "youtube", SocialAccount.status == "connected",
                        SocialAccount.refresh_token.isnot(None),
                        (SocialAccount.token_expires_at.is_(None)) | (SocialAccount.token_expires_at <= now + RENEW_WITHIN))
                .all())
        for acc in rows:
            try:
                renewed += _renew(acc, now)
            except requests.exceptions.RequestException as err:
                logger.warning(f"YouTube token renewal for account {acc.id} failed: {err}")
        session.commit()
    return renewed


def fresh_token(user_id: int, now: datetime | None = None) -> str:
    """The user's YouTube access token, renewed first if it is about to run out."""
    from sqlalchemy.orm import Session

    from db import SocialAccount, engine

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    with Session(engine) as session:
        acc = (session.query(SocialAccount)
               .filter(SocialAccount.user_id == user_id, SocialAccount.platform == "youtube",
                       SocialAccount.status.in_(("connected", "expired"))).first())
        if not acc or not acc.access_token:
            raise YouTubeError("Connect your YouTube channel first (Settings -> Social accounts).")
        if acc.status == "expired":
            raise YouTubeError("Your YouTube connection has expired - connect it again in Settings.")
        if acc.token_expires_at is None or acc.token_expires_at <= now + RENEW_WITHIN:
            try:
                ok = _renew(acc, now)
            except requests.exceptions.RequestException:
                ok = False
            session.commit()
            if not ok and (acc.status != "connected" or (acc.token_expires_at is not None and acc.token_expires_at <= now)):
                raise YouTubeError("Your YouTube connection has expired - connect it again in Settings.")
        return acc.access_token


# ── Publishing ─────────────────────────────────────────────────────────


def title_and_description(item: dict) -> tuple[str, str]:
    from services.publish_service import post_text

    caption = (item.get("caption") or "").strip()
    first = next((line.strip() for line in caption.splitlines() if line.strip()), "") or "New video"
    title = first if len(first) <= MAX_TITLE else first[:MAX_TITLE - 1].rstrip() + "…"
    return title.replace("<", "").replace(">", ""), post_text(item)[:MAX_DESCRIPTION].replace("<", "").replace(">", "")


def publish(user_id: int, item: dict, video_path: str) -> dict:
    """Uploads the video publicly. {post_id, permalink, format: "video"}."""
    if not video_path or not os.path.isfile(video_path):
        raise YouTubeError("The video for this post could not be found - make it again in Studio Chat.")
    token = fresh_token(user_id)
    title, description = title_and_description(item)
    start = requests.post(UPLOAD_URL, timeout=TIMEOUT, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Length": str(os.path.getsize(video_path)), "X-Upload-Content-Type": "video/mp4",
    }, json={"snippet": {"title": title, "description": description, "categoryId": "22"},
             "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}})
    if start.status_code == 401:
        raise YouTubeError("Your YouTube connection has expired - connect it again in Settings.")
    if start.status_code >= 400 or not start.headers.get("Location"):
        raise YouTubeError(f"YouTube didn't accept the upload ({start.status_code}).")
    with open(video_path, "rb") as video:
        done = requests.put(start.headers["Location"], data=video, timeout=600,
                            headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"})
    if done.status_code >= 400:
        raise YouTubeError(f"YouTube didn't accept the upload ({done.status_code}).")
    video_id = (done.json() if done.content else {}).get("id")
    if not video_id:
        raise YouTubeError("YouTube didn't say where the video went. Check your channel before trying again.")
    return {"post_id": video_id, "permalink": f"https://www.youtube.com/watch?v={video_id}", "format": "video"}


# ── Results ────────────────────────────────────────────────────────────


def _get(path: str, token: str, **params) -> dict:
    resp = requests.get(f"{API}/{path}", params=params, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
    body = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        raise RuntimeError(((body.get("error") or {}).get("message")) or f"YouTube answered {resp.status_code}")
    return body


def fetch_metrics(video_id: str, token: str) -> dict:
    """{views, likes, comments} - metrics_service.FETCHERS["youtube"]."""
    items = _get("videos", token, part="statistics", id=video_id).get("items") or []
    if not items:
        raise RuntimeError("The video is gone or private.")
    stats = items[0].get("statistics") or {}
    return {"views": int(stats.get("viewCount") or 0), "likes": int(stats.get("likeCount") or 0),
            "comments": int(stats.get("commentCount") or 0)}


def subscribers(channel_id: str, token: str) -> int:
    """The channel's subscribers - metrics_service.FOLLOWER_FETCHERS["youtube"].
    A channel that hides its count gives none (RuntimeError, saved as unknown)."""
    items = _get("channels", token, part="statistics", id=channel_id).get("items") or []
    stats = (items[0].get("statistics") or {}) if items else {}
    if not items or stats.get("hiddenSubscriberCount"):
        raise RuntimeError("The channel doesn't show its subscriber count.")
    return int(stats.get("subscriberCount") or 0)
