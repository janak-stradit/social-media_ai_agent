"""Facebook Page publishing (Graph API): a text post, one photo, or several
photos in one post.

  text        POST /{page}/feed   message
  one photo   POST /{page}/photos the file + caption
  several     each photo is uploaded unpublished (POST /{page}/photos,
              published=false), then one POST /{page}/feed attaches them all

Uses the Page ID and Page access token saved in Settings, and the Graph
version in Config.META_GRAPH_VERSION. Exercised against a simulated Graph API
(tests/test_publishing.py); not run against the live API from here.
"""

import json
import os

import requests

MAX_PHOTOS = 10


class FacebookError(RuntimeError):
    """A problem to show the user (what Meta said, or what to fix)."""


def _http(method: str, url: str, **kwargs):
    return requests.request(method, url, timeout=kwargs.pop("timeout", 30), **kwargs)


def graph_url(path: str) -> str:
    from config import Config

    return f"https://graph.facebook.com/{Config.META_GRAPH_VERSION}/{path.lstrip('/')}"


def _result(resp) -> dict:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code == 200 and isinstance(body, dict) and "error" not in body:
        return body
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    message = error.get("error_user_msg") or error.get("message") or (resp.text or "")[:300]
    code = error.get("code")
    if code == 190:
        raise FacebookError("Facebook no longer accepts the saved Page access token - connect the Page again in Settings.")
    if code in (10, 200) or error.get("type") == "OAuthException" and "permission" in message.lower():
        raise FacebookError(f"The connected token isn't allowed to post to this Page (it needs pages_manage_posts): {message}")
    if code in (4, 17, 32, 613):
        raise FacebookError("Facebook's posting limit has been reached - try again later.")
    raise FacebookError(f"Facebook: {message}" if message else f"Facebook returned an error ({resp.status_code}).")


def _upload_photo(page_id: str, token: str, path: str, caption: str | None, published: bool) -> dict:
    data = {"access_token": token, "published": "true" if published else "false"}
    if caption:
        data["caption"] = caption
    with open(path, "rb") as handle:
        resp = _http("POST", graph_url(f"{page_id}/photos"), data=data,
                     files={"source": (os.path.basename(path), handle)}, timeout=120)
    return _result(resp)


def publish(page_id: str, token: str, text: str, image_paths: list[str] | None = None) -> dict:
    """Publishes and returns {post_id, permalink, format}. format: text | image | multi_image."""
    text = (text or "").strip()
    images = list(image_paths or [])
    if not text and not images:
        raise FacebookError("There is nothing to post.")
    if len(images) > MAX_PHOTOS:
        raise FacebookError(f"A Facebook post can carry at most {MAX_PHOTOS} photos (this one has {len(images)}).")
    if len(images) > 1:
        photo_ids = [_upload_photo(page_id, token, path, None, published=False)["id"] for path in images]
        data = {"access_token": token, "message": text}
        for i, photo_id in enumerate(photo_ids):
            data[f"attached_media[{i}]"] = json.dumps({"media_fbid": photo_id})
        body, fmt = _result(_http("POST", graph_url(f"{page_id}/feed"), data=data)), "multi_image"
    elif images:
        body, fmt = _upload_photo(page_id, token, images[0], text, published=True), "image"
    else:
        body, fmt = _result(_http("POST", graph_url(f"{page_id}/feed"), data={"access_token": token, "message": text})), "text"
    post_id = body.get("post_id") or body.get("id")
    if not post_id:
        raise FacebookError("Facebook accepted the post but did not return its ID - check your Page before trying again.")
    return {"post_id": post_id, "permalink": f"https://www.facebook.com/{post_id}", "format": fmt}
