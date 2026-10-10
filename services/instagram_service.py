"""Publishing to Instagram through Meta's Graph API (Instagram API with
Facebook Login). Needs an Instagram Business or Creator account linked to a
Facebook Page, and a Page access token from a Meta app with the
instagram_basic, instagram_content_publish, pages_show_list and
pages_read_engagement permissions (docs/instagram-publishing.md).

How a post goes out:
  single image  POST /{ig-user}/media {image_url, caption} -> container,
                wait until it is FINISHED, POST /{ig-user}/media_publish
  carousel      one container per slide (is_carousel_item=true), then a
                CAROUSEL container with the children and the caption,
                wait, publish (2-10 slides)

Meta's servers download the images themselves, so each one must be a JPEG at
a public HTTPS address: prepare_images makes a JPEG copy in the uploads folder
(cropped into Instagram's 4:5 - 1.91:1 range when needed) and returns its URL
under PUBLIC_MEDIA_BASE_URL / APP_BASE_URL.
"""

import logging
import os
import re
import time
import urllib.parse
import uuid

import requests

from config import Config

logger = logging.getLogger(__name__)

MAX_CAPTION = 2200
MAX_HASHTAGS = 30
MAX_SLIDES = 10
MIN_RATIO, MAX_RATIO = 4 / 5, 1.91  # width / height Instagram accepts for feed images
READY_TIMEOUT = 90  # seconds to wait for Meta to process a container
POLL_EVERY = 2
_sleep = time.sleep  # tests replace it


class InstagramError(RuntimeError):
    """A failure the user can act on (message shown as is)."""


def graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{Config.META_GRAPH_VERSION}/{path.lstrip('/')}"


def _error_text(resp) -> str:
    try:
        err = resp.json().get("error") or {}
    except ValueError:
        return f"Instagram returned HTTP {resp.status_code}."
    code, sub = err.get("code"), err.get("error_subcode")
    message = err.get("error_user_msg") or err.get("message") or f"HTTP {resp.status_code}"
    if code == 190:
        return "The Instagram connection has expired - reconnect Instagram in Settings -> Social accounts."
    if code in (4, 17, 32, 613) or sub == 2207042:
        return "Instagram's publishing limit for this account has been reached - try again later."
    if code == 10 or code == 200:
        return ("The Meta app is missing a permission (instagram_content_publish). "
                "See docs/instagram-publishing.md - " + message)
    return f"Instagram: {message}"


def _call(method: str, path: str, token: str, **params) -> dict:
    resp = requests.request(method, graph_url(path), params={**params, "access_token": token} if method == "GET" else None,
                            data={**params, "access_token": token} if method != "GET" else None, timeout=30)
    if not resp.ok:
        raise InstagramError(_error_text(resp))
    return resp.json()


# ── Account ─────────────────────────────────────────────────────────────────


def account_from_page(page_id: str, page_token: str) -> dict:
    """The Instagram professional account linked to a Facebook Page: {id, username}."""
    data = _call("GET", page_id, page_token, fields="instagram_business_account{id,username}")
    ig = data.get("instagram_business_account") or {}
    if not ig.get("id"):
        raise InstagramError("This Facebook Page has no Instagram Business or Creator account linked. "
                             "Link it in Meta Business Suite -> Settings -> Instagram accounts, then try again.")
    return {"id": str(ig["id"]), "username": ig.get("username") or ""}


def account_info(ig_user_id: str, token: str) -> dict:
    data = _call("GET", ig_user_id, token, fields="id,username")
    return {"id": str(data.get("id") or ig_user_id), "username": data.get("username") or ""}


def resolve_account(account_id: str, token: str) -> dict:
    """account_id may be the Instagram account id or the Facebook Page id."""
    try:
        info = account_info(account_id, token)
        if info["username"]:
            return info
    except InstagramError:
        pass
    return account_from_page(account_id, token)


def publishing_quota(ig_user_id: str, token: str) -> dict | None:
    """{used, total} of API-published posts in the last 24 hours, or None if Meta doesn't say."""
    try:
        data = (_call("GET", f"{ig_user_id}/content_publishing_limit", token, fields="quota_usage,config").get("data") or [{}])[0]
        return {"used": int(data.get("quota_usage") or 0), "total": int((data.get("config") or {}).get("quota_total") or 0)}
    except (InstagramError, ValueError, TypeError, IndexError):
        return None


# ── Images ──────────────────────────────────────────────────────────────────


def public_base_url() -> str:
    base = (Config.PUBLIC_MEDIA_BASE_URL or Config.APP_BASE_URL or "").rstrip("/")
    host = urllib.parse.urlparse(base).hostname or ""
    if not base.startswith("https://") or host in ("localhost", "127.0.0.1", "0.0.0.0") or host.endswith(".local"):
        raise InstagramError("Instagram downloads the images from your server, so they need a public HTTPS address. "
                             "Set APP_BASE_URL (or PUBLIC_MEDIA_BASE_URL) to e.g. https://avir.stradit.com.")
    return base


def _fit_ratio(img):
    ratio = img.width / img.height
    if ratio < MIN_RATIO:  # too tall: crop to 4:5
        height = round(img.width / MIN_RATIO)
        top = (img.height - height) // 2
        return img.crop((0, top, img.width, top + height))
    if ratio > MAX_RATIO:  # too wide: crop to 1.91:1
        width = round(img.height * MAX_RATIO)
        left = (img.width - width) // 2
        return img.crop((left, 0, left + width, img.height))
    return img


def prepare_images(urls: list[str], upload_folder: str) -> list[str]:
    """Public URLs of JPEG copies Instagram accepts, in order."""
    from PIL import Image

    base = public_base_url()
    out = []
    for url in urls:
        name = os.path.basename(url or "")
        path = os.path.join(upload_folder, name)
        if not name or not os.path.isfile(path):
            raise InstagramError("An image of this post could not be found on the server.")
        with Image.open(path) as img:
            fitted = _fit_ratio(img.convert("RGB"))
            if img.format == "JPEG" and fitted.size == img.size:
                out.append(f"{base}/static/uploads/{name}")
                continue
            copy = f"ig_{uuid.uuid4().hex[:12]}.jpg"
            fitted.save(os.path.join(upload_folder, copy), "JPEG", quality=92)
        try:
            from services import storage_service

            storage_service.upload(f"/static/uploads/{copy}")
        except Exception:  # noqa: BLE001, S110 - the local copy is what Meta downloads
            pass
        out.append(f"{base}/static/uploads/{copy}")
    return out


# ── Publishing ──────────────────────────────────────────────────────────────


def check_caption(caption: str) -> None:
    if len(caption) > MAX_CAPTION:
        raise InstagramError(f"Instagram captions can be at most {MAX_CAPTION} characters (this one has {len(caption)}).")
    if len(re.findall(r"(?<!\w)#\w+", caption)) > MAX_HASHTAGS:
        raise InstagramError(f"Instagram allows at most {MAX_HASHTAGS} hashtags in a caption.")


def _wait_until_ready(container_id: str, token: str) -> None:
    waited = 0
    while True:
        status = _call("GET", container_id, token, fields="status_code,status")
        code = status.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise InstagramError(f"Instagram could not process the image ({status.get('status') or code}).")
        if waited >= READY_TIMEOUT:
            raise InstagramError("Instagram is taking too long to process the post - try again in a few minutes.")
        _sleep(POLL_EVERY)
        waited += POLL_EVERY


def publish(ig_user_id: str, token: str, caption: str, image_urls: list[str]) -> dict:
    """Publishes one image or a carousel (image_urls: public JPEG URLs).
    Returns {"media_id", "permalink", "carousel"}."""
    if not image_urls:
        raise InstagramError("Instagram posts need an image.")
    if len(image_urls) > MAX_SLIDES:
        raise InstagramError(f"Instagram carousels can have at most {MAX_SLIDES} slides.")
    check_caption(caption)
    if len(image_urls) == 1:
        container = _call("POST", f"{ig_user_id}/media", token, image_url=image_urls[0], caption=caption)["id"]
    else:
        children = [_call("POST", f"{ig_user_id}/media", token, image_url=url, is_carousel_item="true")["id"]
                    for url in image_urls]
        for child in children:
            _wait_until_ready(child, token)
        container = _call("POST", f"{ig_user_id}/media", token, media_type="CAROUSEL", children=",".join(children),
                          caption=caption)["id"]
    _wait_until_ready(container, token)
    media_id = _call("POST", f"{ig_user_id}/media_publish", token, creation_id=container)["id"]
    permalink = None
    try:
        permalink = _call("GET", media_id, token, fields="permalink").get("permalink")
    except InstagramError:
        pass  # published; the link is only a convenience
    return {"media_id": str(media_id), "permalink": permalink, "carousel": len(image_urls) > 1}


def connected_account(user_id: int) -> dict | None:
    """The user's connected Instagram account: {id, username, token}, or None."""
    from sqlalchemy.orm import Session

    from db import SocialAccount, engine

    with Session(engine) as session:
        acc = (session.query(SocialAccount)
               .filter(SocialAccount.user_id == user_id, SocialAccount.platform == "instagram",
                       SocialAccount.status == "connected").first())
        if not acc or not acc.account_id or not acc.access_token:
            return None
        return {"id": acc.account_id, "username": acc.account_name, "token": acc.access_token}


def publish_for_user(user_id: int, caption: str, image_urls: list[str], upload_folder: str) -> dict:
    """Publishes local images (/static/uploads/...) to the user's connected Instagram account."""
    account = connected_account(user_id)
    if not account:
        raise InstagramError("Connect your Instagram account first (Settings -> Social accounts).")
    public = prepare_images(image_urls, upload_folder)
    result = publish(account["id"], account["token"], caption, public)
    return {**result, "account": account["username"]}
