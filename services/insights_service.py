"""What works for this brand: which kinds of post do better than its others,
learned from its own published posts, each scored at 48 hours old
(db.scored_post_items, so a new post isn't compared with a month-old one).

A post's traits are read from what was posted (the scheduled post's or the
approval request's item): its format, how it opens, its length and how many
hashtags it has. A trait is only reported when MIN_EACH posts have it and
MIN_EACH don't, and when it does clearly better or worse (LIFT_UP /
LIFT_DOWN, medians so one viral post doesn't decide it). Patterns from
the brand's own posts, not promises - the pages say so.

Used by the Performance page (/api/insights) and when writing new posts
(prompt_notes -> the caption agent), so the brand's next posts lean on what
has worked for it before.
"""

import re

MIN_EACH = 3
LIFT_UP = 1.3
LIFT_DOWN = 0.75
TOP = 6
NAMES = {"instagram": "Instagram", "facebook": "Facebook", "linkedin": "LinkedIn", "youtube": "YouTube"}

# (trait, value) -> how it reads on the page and in the prompt
LABELS = {
    ("format", "carousel"): "Carousels",
    ("format", "image"): "Posts with one image",
    ("format", "text"): "Text-only posts",
    ("format", "video"): "Videos",
    ("opening", "question"): "Posts that open with a question",
    ("opening", "number"): "Posts with a number in the first line",
    ("opening", "statement"): "Posts that open with a plain statement",
    ("length", "short"): "Short posts (under 400 characters)",
    ("length", "medium"): "Posts of 400 to 1,000 characters",
    ("length", "long"): "Long posts (over 1,000 characters)",
    ("hashtags", "few"): "Posts with 0 to 2 hashtags",
    ("hashtags", "some"): "Posts with 3 to 5 hashtags",
    ("hashtags", "many"): "Posts with 6 or more hashtags",
}


def _first_line(caption: str) -> str:
    for line in (caption or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def traits(item: dict) -> dict[str, str]:
    """{trait: value} for one posted item."""
    caption = (item.get("caption") or "").strip()
    images = item.get("images") or []
    media = item.get("media")
    if media == "video" or item.get("video"):
        fmt = "video"
    elif media == "carousel" or (media != "none" and media != "single" and len(images) > 1):
        fmt = "carousel"
    elif media != "none" and images:
        fmt = "image"
    else:
        fmt = "text"
    first = _first_line(caption)
    if first.rstrip().endswith("?") or (first.find("?") != -1 and first.find("?") < 160):
        opening = "question"
    elif re.search(r"\d", first):
        opening = "number"
    else:
        opening = "statement"
    tags = len(item.get("hashtags") or []) or len(re.findall(r"(?<!\w)#\w+", caption))
    return {
        "format": fmt,
        "opening": opening,
        "length": "short" if len(caption) < 400 else "medium" if len(caption) <= 1000 else "long",
        "hashtags": "few" if tags <= 2 else "some" if tags <= 5 else "many",
    }


def _median(values: list[int]) -> float:
    values = sorted(values)
    mid = len(values) // 2
    return float(values[mid]) if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def compare(posts: list[dict], both_sides: bool = False) -> list[dict]:
    """posts: [{platform, score, item}] -> the clear findings, strongest first:
    [{platform, trait, value, lift, posts, others, text}]. both_sides keeps
    every value's finding (explain needs the one for a post's own value)."""
    found = []
    by_platform: dict[str, list[tuple[dict, int]]] = {}
    for post in posts:
        by_platform.setdefault(post["platform"], []).append((traits(post["item"]), int(post["score"] or 0)))
    for platform, rows in by_platform.items():
        for trait in ("format", "opening", "length", "hashtags"):
            for value in sorted({t[trait] for t, _ in rows}):
                with_it = [s for t, s in rows if t[trait] == value]
                without = [s for t, s in rows if t[trait] != value]
                if len(with_it) < MIN_EACH or len(without) < MIN_EACH:
                    continue
                base = _median(without)
                if base <= 0:
                    continue
                lift = round(_median(with_it) / base, 1)
                if LIFT_DOWN < lift < LIFT_UP:
                    continue
                label = LABELS.get((trait, value), f"{trait}: {value}")
                found.append({
                    "platform": platform, "trait": trait, "value": value, "lift": lift,
                    "posts": len(with_it), "others": len(without),
                    "text": f"{label} get {lift}× the engagement of your other {NAMES.get(platform, platform)} posts",
                })
    # strongest either way: 2x and 0.5x are as telling as each other
    found.sort(key=lambda f: -(f["lift"] if f["lift"] >= 1 else 1 / max(f["lift"], 0.01)))
    if both_sides:
        return found
    # One finding per trait and platform: "carousels 2.4x" and "single images
    # 0.4x" are the same comparison seen from both sides. What does better is
    # kept; a "does worse" only when nothing in that trait does better.
    kept, seen = [], set()
    for f in sorted(found, key=lambda f: f["lift"] < 1):  # stable: does-better first, strength order kept
        key = (f["platform"], f["trait"])
        if key not in seen:
            seen.add(key)
            kept.append(f)
    kept.sort(key=lambda f: -(f["lift"] if f["lift"] >= 1 else 1 / max(f["lift"], 0.01)))
    return kept


def what_works(user_id: int) -> dict:
    """{insights: [...], posts: how many scored posts they come from}."""
    from db import scored_post_items

    posts = scored_post_items(user_id)
    return {"insights": compare(posts)[:TOP], "posts": len(posts)}


def explain(user_id: int, platform: str, item: dict) -> list[dict]:
    """One post's traits, each with what the brand's own posts say about it:
    [{trait, value, label, lift (None: nothing clear)}], clear ones first -
    for "Why it likely did well" on the post's page."""
    from db import scored_post_items

    found = {(f["trait"], f["value"]): f for f in compare(scored_post_items(user_id), both_sides=True)
             if f["platform"] == platform}
    out = []
    for trait, value in traits(item).items():
        f = found.get((trait, value))
        out.append({"trait": trait, "value": value, "label": LABELS.get((trait, value), value),
                    "lift": f["lift"] if f else None, "posts": f["posts"] if f else None})
    out.sort(key=lambda t: t["lift"] is None)
    return out


CAPTION_TRAITS = ("opening", "length")  # what the caption writer decides; format and hashtags are chosen elsewhere


def prompt_notes(user_id: int, platforms: list[str]) -> dict[str, str]:
    """{platform: a short block for the caption prompt} for the platforms with findings."""
    from db import scored_post_items

    found = compare(scored_post_items(user_id))
    notes = {}
    for platform in platforms:
        findings = [f for f in found if f["platform"] == platform and f["trait"] in CAPTION_TRAITS][:TOP]
        if not findings:
            continue
        lines = "\n".join(
            f"- {f['text']} ({f['posts']} posts vs {f['others']}): "
            f"{'lean towards this' if f['lift'] >= 1 else 'avoid this'} where it fits the brief"
            for f in findings
        )
        notes[platform] = ("WHAT HAS WORKED FOR THIS BRAND (its own published posts, compared at 48 hours old; "
                           f"follow where it fits, never at the cost of the brief or the facts):\n{lines}")
    return notes
