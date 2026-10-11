"""LinkedIn publishing through the versioned Posts API (/rest/posts): a text
post, one image, several images (a "multi-image" post) or a document (the PDF
carousel people swipe through).

  1. each image / the PDF is uploaded first: initializeUpload gives an upload
     address and the asset's URN, then the file is PUT there
  2. the post is created with the caption and those URNs

The caption is "little text": characters LinkedIn reserves ( ) [ ] { } < > @ |
* _ ~ \\ must be escaped or the post is cut short; #hashtags stay as they are.
Every call carries LinkedIn-Version (Config.LINKEDIN_API_VERSION, YYYYMM) -
LinkedIn retires a version about a year after its release, so it needs
bumping from time to time.

The request shapes follow LinkedIn's documentation and are exercised against
a simulated API (tests/test_publishing.py). They have not been run against
the live API from here, which needs an approved LinkedIn app.
"""

import os
import re
import time

import requests

API = "https://api.linkedin.com"
MAX_COMMENTARY = 3000
MULTI_IMAGE = (2, 20)        # images in a multi-image post
MAX_DOCUMENT_BYTES = 100 * 1024 * 1024
POST_ATTEMPTS = 4            # an asset just uploaded can still be processing
POST_RETRY_WAIT = 3          # seconds
_RESERVED = re.compile(r"([\\|{}@\[\]()<>*_~])")
_HASHTAG = re.compile(r"#\w+")


class LinkedInError(RuntimeError):
    """A problem to show the user (what LinkedIn said, or what to fix)."""


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _http(method: str, url: str, **kwargs):
    return requests.request(method, url, timeout=kwargs.pop("timeout", 30), **kwargs)


def escape_commentary(text: str) -> str:
    """Plain text -> little text: reserved characters escaped, #hashtags kept."""
    out, last = [], 0
    for match in _HASHTAG.finditer(text or ""):
        out.append(_RESERVED.sub(r"\\\1", text[last:match.start()]).replace("#", "\\#"))
        out.append(match.group(0))
        last = match.end()
    out.append(_RESERVED.sub(r"\\\1", (text or "")[last:]).replace("#", "\\#"))
    return "".join(out)


def _headers(token: str, json_body: bool = True) -> dict:
    from config import Config

    headers = {"Authorization": f"Bearer {token}", "LinkedIn-Version": Config.LINKEDIN_API_VERSION,
               "X-Restli-Protocol-Version": "2.0.0"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _error_text(resp) -> str:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    message = (body.get("message") if isinstance(body, dict) else None) or (resp.text or "")[:300]
    if resp.status_code == 401:
        return "LinkedIn no longer accepts the saved access token - connect the account again in Settings."
    if resp.status_code == 403:
        return f"LinkedIn refused this for the connected account (it needs permission to post as this author): {message}"
    if resp.status_code == 426 or "version" in message.lower() and "active" in message.lower():
        return f"LinkedIn has retired the API version this app uses - set LINKEDIN_API_VERSION to a current one. ({message})"
    if resp.status_code == 429:
        return "LinkedIn's posting limit for today has been reached - try again tomorrow."
    return f"LinkedIn: {message}" if message else f"LinkedIn returned an error ({resp.status_code})."


def resolve_author(account_id: str, token: str) -> str:
    """The saved author as a URN. "me" is looked up from the token."""
    author = (account_id or "").strip()
    if author.lower() in ("me", "urn:li:person:me", "urn:li:member:me"):
        resp = _http("GET", f"{API}/v2/userinfo", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        sub = resp.json().get("sub") if resp.status_code == 200 else None
        if not sub:
            raise LinkedInError("Could not find your LinkedIn member ID - enter your Author URN in Settings.")
        return f"urn:li:person:{sub}"
    if not author.startswith("urn:li:"):
        raise LinkedInError("The LinkedIn Author URN in Settings should look like urn:li:person:... or urn:li:organization:...")
    return author


def _upload(kind: str, owner: str, token: str, path: str) -> str:
    """Uploads one file ("images" or "documents") and returns its URN."""
    resp = _http("POST", f"{API}/rest/{kind}?action=initializeUpload", headers=_headers(token),
                 json={"initializeUploadRequest": {"owner": owner}})
    if resp.status_code not in (200, 201):
        raise LinkedInError(_error_text(resp))
    value = resp.json().get("value") or {}
    urn, upload_url = value.get("image" if kind == "images" else "document"), value.get("uploadUrl")
    if not urn or not upload_url:
        raise LinkedInError("LinkedIn did not give an upload address - please try again.")
    with open(path, "rb") as handle:
        put = _http("PUT", upload_url, headers={"Authorization": f"Bearer {token}"}, data=handle.read(), timeout=120)
    if put.status_code not in (200, 201):
        raise LinkedInError(f"LinkedIn did not accept {os.path.basename(path)} ({put.status_code}).")
    return urn


def _create_post(author: str, token: str, commentary: str, content: dict | None) -> str:
    body = {
        "author": author, "commentary": commentary, "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False,
    }
    if content:
        body["content"] = content
    for attempt in range(POST_ATTEMPTS):
        resp = _http("POST", f"{API}/rest/posts", headers=_headers(token), json=body)
        if resp.status_code == 201:
            post_id = resp.headers.get("x-restli-id") or resp.headers.get("X-RestLi-Id")
            if not post_id:
                raise LinkedInError("LinkedIn accepted the post but did not return its ID - check your LinkedIn page before trying again.")
            return post_id
        # Just-uploaded media can take a few seconds to become usable
        still_processing = content and resp.status_code in (400, 404, 422) and re.search(
            r"process|not (yet )?(ready|available)|WAITING_UPLOAD", resp.text or "", re.IGNORECASE)
        if not still_processing or attempt == POST_ATTEMPTS - 1:
            raise LinkedInError(_error_text(resp))
        _sleep(POST_RETRY_WAIT)
    raise LinkedInError("LinkedIn did not accept the post.")  # pragma: no cover - the loop always returns or raises


def publish(author: str, token: str, text: str, image_paths: list[str] | None = None,
            document_path: str | None = None, document_title: str | None = None) -> dict:
    """Publishes and returns {post_id, permalink, format}. format: text | image |
    multi_image | document."""
    text = (text or "").strip()
    images = list(image_paths or [])
    if len(text) > MAX_COMMENTARY:
        raise LinkedInError(f"LinkedIn posts can be at most {MAX_COMMENTARY:,} characters (this one has {len(text):,}).")
    if not text and not images and not document_path:
        raise LinkedInError("There is nothing to post.")
    commentary = escape_commentary(text)
    if document_path:
        if os.path.getsize(document_path) > MAX_DOCUMENT_BYTES:
            raise LinkedInError("The carousel PDF is larger than LinkedIn's 100 MB limit.")
        urn = _upload("documents", author, token, document_path)
        content, fmt = {"media": {"title": (document_title or "Carousel").strip()[:100], "id": urn}}, "document"
    elif len(images) > 1:
        low, high = MULTI_IMAGE
        if not low <= len(images) <= high:
            raise LinkedInError(f"A LinkedIn multi-image post needs {low} to {high} images (this one has {len(images)}).")
        urns = [_upload("images", author, token, path) for path in images]
        content, fmt = {"multiImage": {"images": [{"id": urn, "altText": f"Slide {i}"} for i, urn in enumerate(urns, start=1)]}}, "multi_image"
    elif images:
        content, fmt = {"media": {"id": _upload("images", author, token, images[0])}}, "image"
    else:
        content, fmt = None, "text"
    post_id = _create_post(author, token, commentary, content)
    return {"post_id": post_id, "permalink": f"https://www.linkedin.com/feed/update/{post_id}/", "format": fmt}
