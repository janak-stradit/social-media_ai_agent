"""Hand edits to a generated post (Studio Chat "Edit"): the caption and
hashtags of one platform, or of a carousel, changed without a new generation.

The AI's text is kept the first time a post is edited, so "Restore original"
can bring it back. A caption's compliance notes describe the text that was
checked: after an edit they are marked stale (the caption is checked again
when the post is sent for approval).

The limits here are mirrored in static/js/post_preview.js (the live counter).
"""

from datetime import datetime, timezone

# What is posted is the caption, a blank line and the hashtags - counted together.
# Facebook itself allows far more; 3000 is what an approval request carries
# (approval_bundle_service.MAX_CAPTION), so nothing is cut off later.
POST_LIMITS = {"instagram": 2200, "linkedin": 3000, "facebook": 3000}
DEFAULT_LIMIT = 3000
MAX_HASHTAGS = 30
CAROUSEL = "carousel"  # the caption shared by a /carousel's slides (posted to LinkedIn and Instagram)


class EditError(ValueError):
    """The edit can't be saved; the message is shown to the user."""


def limit_for(target: str) -> int:
    if target == CAROUSEL:
        return min(POST_LIMITS["linkedin"], POST_LIMITS["instagram"])
    return POST_LIMITS.get(target, DEFAULT_LIMIT)


def post_text(caption: str, hashtags: list[str]) -> str:
    return "\n\n".join(part for part in (caption.strip(), " ".join(hashtags)) if part)


def clean_hashtags(raw) -> list[str]:
    """A list, or text separated by spaces, commas or new lines -> ["#Tag", ...]."""
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    tags: list[str] = []
    seen = set()
    for tag in raw or []:
        tag = "".join(str(tag or "").split()).lstrip("#")
        if not tag or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        tags.append("#" + tag[:59])
    return tags


def tag_list(raw) -> list[str]:
    """The hashtags of a saved post, whichever shape they were saved in."""
    if isinstance(raw, list):
        return [str(t) for t in raw]
    if isinstance(raw, dict):
        for key in ("hashtags", "reach_hashtags"):
            if isinstance(raw.get(key), list):
                return [str(t) for t in raw[key]]
    return []


def _node(content: dict, target: str) -> dict:
    if target == CAROUSEL:
        carousel = (content.get("_preset") or {}).get("carousel") if isinstance(content.get("_preset"), dict) else None
        if not isinstance(carousel, dict):
            raise EditError("This post has no carousel caption to edit.")
        return carousel
    node = content.get(target) if not str(target).startswith("_") else None
    if not isinstance(node, dict) or not isinstance(node.get("caption"), dict):
        raise EditError("This post has no caption for that platform.")
    return node


def _read(node: dict, target: str) -> tuple[str, list[str]]:
    if target == CAROUSEL:
        return str(node.get("caption") or ""), tag_list(node.get("hashtags"))
    return str(node["caption"].get("primary_caption") or ""), tag_list(node.get("hashtags"))


def _write(node: dict, target: str, caption: str, hashtags: list[str]) -> None:
    if target == CAROUSEL:
        node["caption"], node["hashtags"] = caption, hashtags
        return
    node["caption"]["primary_caption"] = caption
    if isinstance(node.get("hashtags"), dict):
        node["hashtags"]["hashtags"] = hashtags
    else:
        node["hashtags"] = {"hashtags": hashtags}


def _original(node: dict) -> dict | None:
    original = node.get("original")
    return original if isinstance(original, dict) else None


def apply_edit(content: dict, target: str, caption=None, hashtags=None, restore: bool = False) -> dict:
    """Changes content in place and returns the edited part (a platform's
    entry, or the carousel). caption / hashtags left as None stay as they are."""
    node = _node(content, target)
    old_caption, old_tags = _read(node, target)
    original = _original(node)

    if restore:
        if not original:
            raise EditError("This post has not been edited.")
        new_caption, new_tags = str(original.get("caption") or ""), tag_list(original.get("hashtags"))
    else:
        new_caption = old_caption if caption is None else str(caption).replace("\r\n", "\n").strip()
        new_tags = old_tags if hashtags is None else clean_hashtags(hashtags)
        if not new_caption:
            raise EditError("The caption can't be empty.")
        if len(new_tags) > MAX_HASHTAGS:
            raise EditError(f"Use at most {MAX_HASHTAGS} hashtags (this has {len(new_tags)}).")
        length, limit = len(post_text(new_caption, new_tags)), limit_for(target)
        if length > limit:
            raise EditError(f"That is {length - limit} characters over the limit of {limit:,} (caption and hashtags together).")

    back_to_original = bool(original) and new_caption == str(original.get("caption") or "") \
        and new_tags == tag_list(original.get("hashtags"))
    caption_changed = new_caption != old_caption
    if not caption_changed and new_tags == old_tags:
        return node
    _write(node, target, new_caption, new_tags)
    compliance = node.get("compliance") if isinstance(node.get("compliance"), dict) else None
    if restore or back_to_original:
        node.pop("original", None)
        node.pop("edited", None)
        if compliance:
            compliance.pop("stale", None)
    else:
        if not original:
            node["original"] = {"caption": old_caption, "hashtags": old_tags}
        node["edited"] = {"at": datetime.now(timezone.utc).isoformat()}
        if compliance and caption_changed:
            compliance["stale"] = True  # these notes are about the text before the edit
    return node
