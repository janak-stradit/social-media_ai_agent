"""A whole post for several platforms, sent for approval in one request
(Studio Chat "Send for approval"): one item per platform, each with its own
caption, hashtags and media - one image, or a carousel of slides.

  normalize_items  cleans what the page sent and checks each platform's rules
                   (Instagram: 2-10 slides of one shape; LinkedIn: 2-20 slides)
  prepare_items    adds what the reviewer needs: the PDF of a LinkedIn
                   document carousel, and the compliance review per caption
  apply_decisions  the reviewer's per-platform decisions -> overall status

LinkedIn carousels come in two kinds: "multi_image" (a post with several
images) and "document" (a PDF people swipe through - built here from the
slides, so the reviewer sees exactly what will be posted).
"""

import os

PLATFORMS = ("linkedin", "instagram", "facebook", "youtube")
PLATFORM_NAMES = {"linkedin": "LinkedIn", "instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}
LINKEDIN_FORMATS = {"multi_image": "Multi-image post", "document": "Document carousel (PDF)"}
# (min, max) slides per carousel
CAROUSEL_LIMITS = {"instagram": (2, 10), "linkedin": (2, 20), "facebook": (2, 10)}
MAX_INSTAGRAM_HASHTAGS = 30
MAX_CAPTION = 3000
ASPECT_TOLERANCE = 0.02  # Instagram: every slide must have the same shape


class BundleError(ValueError):
    """A problem the person sending the post can fix (shown to them as is)."""


def _image_path(url: str, upload_folder: str) -> str | None:
    """Local file of one of our images, or None. Only files in the uploads
    folder - never a path or URL the page made up."""
    if not isinstance(url, str) or not url.startswith("/static/uploads/"):
        return None
    name = os.path.basename(url)
    path = os.path.join(upload_folder, name)
    return path if name and os.path.isfile(path) else None


def _ratio(path: str) -> float:
    from PIL import Image

    with Image.open(path) as img:
        return img.width / img.height


def normalize_items(raw_items, upload_folder: str) -> list[dict]:
    """The items to store, or BundleError with a reason the user can act on."""
    if not isinstance(raw_items, list) or not raw_items:
        raise BundleError("Choose at least one platform to send.")
    items, seen = [], set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        platform = str(raw.get("platform") or "").lower()
        name = PLATFORM_NAMES.get(platform, platform)
        if platform not in PLATFORMS or platform in seen:
            continue
        seen.add(platform)
        images = []
        for url in raw.get("images") or []:
            path = _image_path(url, upload_folder)
            if not path:
                raise BundleError(f"{name}: one of the images could not be found - pick it again.")
            if url not in images:
                images.append(url)
        media = raw.get("media") if raw.get("media") in ("none", "single", "carousel") else ("single" if images else "none")
        if media == "none":
            images = []
        elif media == "single":
            images = images[:1]
        if media != "none" and not images:
            raise BundleError(f"{name}: pick an image.")
        if media == "carousel":
            low, high = CAROUSEL_LIMITS.get(platform, (2, 10))
            if not low <= len(images) <= high:
                raise BundleError(f"{name} carousels need {low} to {high} slides - you picked {len(images)}.")
            if platform == "instagram":
                ratios = [_ratio(_image_path(u, upload_folder)) for u in images]
                if max(ratios) - min(ratios) > ASPECT_TOLERANCE * max(ratios):
                    raise BundleError("Instagram carousels need every slide in the same shape (all square or all 4:5).")
        if platform == "instagram" and not images:
            raise BundleError("Instagram posts need an image.")
        hashtags = []
        for tag in raw.get("hashtags") or []:
            tag = str(tag).strip()
            if tag and not tag.startswith("#"):
                tag = "#" + tag
            if tag and tag not in hashtags:
                hashtags.append(tag[:60])
        if platform == "instagram" and len(hashtags) > MAX_INSTAGRAM_HASHTAGS:
            raise BundleError(f"Instagram allows at most {MAX_INSTAGRAM_HASHTAGS} hashtags - this post has {len(hashtags)}.")
        caption = str(raw.get("caption") or "").strip()[:MAX_CAPTION]
        if not caption and not images:
            raise BundleError(f"{name}: there is nothing to review - add a caption or an image.")
        titles = [str(t).strip()[:80] for t in (raw.get("slide_titles") or [])][:len(images)]
        item = {
            "platform": platform, "caption": caption, "hashtags": hashtags, "media": media, "images": images,
            "slide_titles": titles + [f"Slide {i}" for i in range(len(titles) + 1, len(images) + 1)],
            "decision": "pending", "comment": None,
        }
        if platform == "linkedin" and media == "carousel":
            item["linkedin_format"] = raw.get("linkedin_format") if raw.get("linkedin_format") in LINKEDIN_FORMATS \
                else "multi_image"
        items.append(item)
    if not items:
        raise BundleError("Choose at least one platform to send.")
    return items


def prepare_items(items: list[dict], upload_folder: str, rules=None, check_caption=None) -> list[dict]:
    """The PDF for a LinkedIn document carousel, and a compliance review per
    caption (check_caption(caption, platform, rules) -> result or None)."""
    from services.carousel_service import build_pdf

    for item in items:
        if item.get("linkedin_format") == "document":
            paths = [_image_path(u, upload_folder) for u in item["images"]]
            item["pdf_url"] = "/static/uploads/" + build_pdf(paths, upload_folder)
        if rules and check_caption and item["caption"]:
            try:
                result = check_caption(item["caption"], item["platform"], rules)
                if result:
                    result["suggested_caption"] = result.pop("caption", None)
                    item["compliance"] = result
            except Exception:  # noqa: BLE001, S110 - the review is guidance; the request still goes out
                pass
    return items


def summary(items: list[dict]) -> dict:
    """What the request contains, for the old one-platform columns and lists."""
    images = []
    for item in items:
        images.extend(u for u in item["images"] if u not in images)
    first = items[0]
    return {
        "platform": first["platform"] if len(items) == 1 else "multi",
        "asset_type": "carousel" if any(i["media"] == "carousel" for i in items) else ("image" if images else "text"),
        "caption": first["caption"],
        "image_urls": images,
    }


def apply_decisions(items: list[dict], decision: str | None, item_decisions: dict) -> tuple[list[dict], str]:
    """(items, status). decision 'approved'/'rejected' applies to every platform;
    item_decisions {index or platform: {"decision": "approved"|"changes", "comment"}}
    per platform. Status: approved (all), rejected (changes on all), partial."""
    for i, item in enumerate(items):
        if decision:
            item["decision"] = "approved" if decision == "approved" else "changes"
        choice = item_decisions.get(str(i)) or item_decisions.get(i) or item_decisions.get(item["platform"])
        if isinstance(choice, dict) and choice.get("decision") in ("approved", "changes"):
            item["decision"] = choice["decision"]
            item["comment"] = (str(choice.get("comment") or "").strip()[:1000]) or None
    decided = [i["decision"] for i in items]
    if "pending" in decided:
        raise ValueError("Decide every platform before submitting.")
    if all(d == "approved" for d in decided):
        return items, "approved"
    if all(d == "changes" for d in decided):
        return items, "rejected"
    return items, "partial"
