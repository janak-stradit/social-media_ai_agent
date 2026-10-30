"""Publishing one platform's version of a post to the account the user has
connected. The "Publish" button on an approved request and scheduled posts
both go through publish_item, so a post is checked and sent the same way
whichever road it took.

An item (the shape approval_bundle_service.normalize_items produces):
    {platform, caption, hashtags, media: none|single|carousel|video,
     images: ["/static/uploads/..."], video: "/static/uploads/....mp4",
     linkedin_format, pdf_url, slide_titles}

  instagram  one image or a carousel               services/instagram_service.py
  facebook   text, one photo or several photos     services/facebook_service.py
  linkedin   text, one image, a multi-image post
             or a document (PDF) carousel          services/linkedin_service.py
  youtube    the post's video                       services/youtube_service.py
"""

import os
import requests

PLATFORMS = ("linkedin", "instagram", "facebook", "youtube")
NAMES = {"linkedin": "LinkedIn", "instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}


class PublishError(RuntimeError):
    """Why a post could not be published, in words for the user.
    unreachable: the platform could not be contacted at all (worth trying again as is)."""

    def __init__(self, message: str, unreachable: bool = False):
        super().__init__(message)
        self.unreachable = unreachable


def accounts(user_id: int) -> dict[str, dict]:
    """The user's connected accounts by platform (with their tokens - never sent to a page)."""
    from sqlalchemy.orm import Session

    from db import SocialAccount, engine, social_account_expired

    with Session(engine) as session:
        rows = session.query(SocialAccount).filter(SocialAccount.user_id == user_id, SocialAccount.status == "connected").all()
        return {
            row.platform: {
                "name": row.account_name, "account_id": row.account_id, "token": row.access_token,
                "connection_type": getattr(row, "connection_type", None) or "direct",
                "mcp_endpoint": getattr(row, "mcp_endpoint", None), "mcp_token": getattr(row, "mcp_token", None),
                "mcp_tool_name": getattr(row, "mcp_tool_name", None) or "linkedin_publish_post",
                "expired": social_account_expired(row),
            }
            for row in rows
        }


def connections(user_id: int) -> dict[str, str | None]:
    """{platform: the connected account's name, or None} for the pages that offer publishing."""
    connected = accounts(user_id)

    def usable(platform: str) -> bool:
        acc = connected.get(platform)
        if not acc or acc["expired"]:
            return False
        if acc["connection_type"] == "mcp":
            return bool(acc["mcp_endpoint"])
        return bool(acc["account_id"] and acc["token"])

    return {p: (connected[p]["name"] if usable(p) else None) for p in PLATFORMS}


def post_text(item: dict) -> str:
    """The caption with its hashtags under it (those not already in the caption)."""
    caption = (item.get("caption") or "").strip()
    tags = [t for t in item.get("hashtags") or [] if t.lower() not in caption.lower()]
    return f"{caption}\n\n{' '.join(tags)}".strip() if tags else caption


def _local(url: str, upload_folder: str) -> str:
    """The file behind one of our /static/uploads/ addresses (fetched from storage if it isn't on this disk)."""
    from services import storage_service

    name = os.path.basename(url or "")
    if not name or not str(url).startswith("/static/uploads/"):
        raise PublishError("One of the post's images could not be found.")
    path = os.path.join(upload_folder, name)
    if os.path.isfile(path):
        return path
    fetched = storage_service.ensure_local(url)
    if not fetched or not os.path.isfile(fetched):
        raise PublishError("One of the post's images could not be found.")
    return fetched


def _linkedin_mcp(acc: dict, text: str) -> dict:
    """A LinkedIn account connected through the user's own MCP server: text only."""
    headers = {"Content-Type": "application/json"}
    if acc.get("mcp_token"):
        headers["Authorization"] = f"Bearer {acc['mcp_token']}"
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": acc["mcp_tool_name"], "arguments": {"author_urn": acc.get("account_id"), "commentary": text}}}
    resp = requests.post(acc["mcp_endpoint"], json=payload, headers=headers, timeout=20)
    if resp.status_code >= 400:
        raise PublishError(f"Your LinkedIn MCP server answered with an error ({resp.status_code}).")
    return {"post_id": None, "permalink": None, "format": "text"}


def publish_item(user_id: int, item: dict, upload_folder: str) -> dict:
    """Publishes the item and returns {platform, post_id, permalink, account, format}.
    PublishError says why it couldn't be."""
    from services.facebook_service import FacebookError
    from services.instagram_service import InstagramError
    from services.linkedin_service import LinkedInError

    platform = (item.get("platform") or "").lower()
    name = NAMES.get(platform, platform or "this platform")
    if platform not in PLATFORMS:
        raise PublishError(f"Publishing to {name} isn't available yet.")
    text = post_text(item)
    images = list(item.get("images") or []) if item.get("media") != "none" else []
    if item.get("media") == "single":
        images = images[:1]
    try:
        if platform == "youtube":
            from services import youtube_service

            if not item.get("video"):
                raise PublishError("YouTube posts need a video.")
            try:
                result = youtube_service.publish(user_id, item, _local(item["video"], upload_folder))
            except youtube_service.YouTubeError as err:
                raise PublishError(str(err)) from err
            return {"platform": platform, "account": (accounts(user_id).get("youtube") or {}).get("name"), **result}
        if platform == "instagram":
            from services.instagram_service import publish_for_user

            if not images:
                raise PublishError("Instagram posts need an image.")
            result = publish_for_user(user_id, text, images, upload_folder)
            return {"platform": platform, "post_id": result["media_id"], "media_id": result["media_id"], "permalink": result["permalink"],
                    "account": result["account"], "format": "carousel" if result.get("carousel") else "image"}

        acc = accounts(user_id).get(platform)
        if not acc:
            raise PublishError(f"Connect your {name} account first (Settings -> Social accounts).")
        if acc["expired"]:
            raise PublishError(f"Your {name} connection has expired - connect it again in Settings.")
        paths = [_local(url, upload_folder) for url in images]
        if platform == "facebook":
            from services import facebook_service

            if not acc["account_id"] or not acc["token"]:
                raise PublishError("The Facebook Page ID or access token is missing in Settings.")
            result = facebook_service.publish(str(acc["account_id"]), str(acc["token"]), text, paths)
        elif acc["connection_type"] == "mcp":
            if not acc["mcp_endpoint"]:
                raise PublishError("The LinkedIn MCP server address is missing in Settings.")
            if paths:
                raise PublishError("LinkedIn is connected through an MCP server, which can only publish text posts. "
                                   "Connect LinkedIn directly in Settings to publish images and carousels.")
            result = _linkedin_mcp(acc, text)
        else:
            from services import linkedin_service

            if not acc["account_id"] or not acc["token"]:
                raise PublishError("The LinkedIn Author URN or access token is missing in Settings.")
            author = linkedin_service.resolve_author(str(acc["account_id"]), str(acc["token"]))
            document = None
            if item.get("media") == "carousel" and item.get("linkedin_format") == "document":
                if item.get("pdf_url"):
                    document = _local(item["pdf_url"], upload_folder)
                else:
                    from services.carousel_service import build_pdf

                    document = os.path.join(upload_folder, build_pdf(paths, upload_folder))
            title = next((t for t in item.get("slide_titles") or [] if t and not t.lower().startswith("slide ")), None)
            result = linkedin_service.publish(author, str(acc["token"]), text, [] if document else paths,
                                              document_path=document, document_title=title or text.split("\n")[0][:80] or "Carousel")
        return {"platform": platform, "account": acc["name"], **result}
    except (InstagramError, FacebookError, LinkedInError) as err:
        raise PublishError(str(err)) from err
    except requests.exceptions.RequestException as err:
        raise PublishError(f"Could not reach {name}: {err}", unreachable=True) from err
