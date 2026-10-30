"""Post score: a quick checklist for a post before it goes out (the Studio
Chat post editor; POST /api/post-score). No AI call - it runs on every edit.

Each check is a platform's common practice, made personal where the brand's
own results say something clearer (services/insights_service.py): if its
LinkedIn posts that open with a question do 1.7x, the opening check says so.
Guidance, not a prediction - the editor says that too.
"""

import re

from services.hook_service import TARGET_LENGTH, opening_of

# Common practice per platform: (shortest, longest) caption and hashtag count
LENGTH = {"linkedin": (300, 1300), "instagram": (80, 1000), "facebook": (40, 500)}
HASHTAGS = {"linkedin": (3, 5), "instagram": (3, 10), "facebook": (0, 3)}
NAMES = {"instagram": "Instagram", "facebook": "Facebook", "linkedin": "LinkedIn"}
# What a reader is asked to do. English only: other languages skip this check.
CTA = re.compile(r"\b(comment|share|save|book|visit|learn more|read more|link in bio|dm|message us|call|sign up|"
                 r"register|download|try|reply|tell us|join|subscribe|follow|contact|get in touch|apply|order|shop|"
                 r"speak to|talk to|let us know|click|tap|swipe|check out|see how|find out)\b", re.I)
# insights_service buckets, in the words the editor uses
BUCKET_WORDS = {
    ("length", "short"): "under 400 characters", ("length", "medium"): "400 to 1,000 characters",
    ("length", "long"): "over 1,000 characters", ("hashtags", "few"): "0 to 2 hashtags",
    ("hashtags", "some"): "3 to 5 hashtags", ("hashtags", "many"): "6 or more hashtags",
    ("opening", "question"): "a question", ("opening", "number"): "a number", ("opening", "statement"): "a plain statement",
    ("format", "carousel"): "carousels", ("format", "image"): "posts with one image", ("format", "text"): "text-only posts",
}


def _mostly_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) > 0.6


def _check(ok: bool, title: str, note: str) -> dict:
    return {"ok": ok, "title": title, "note": note}


def score(platform: str, caption: str, hashtags: list[str], media: str | None = None,
          images: list | None = None, findings: list[dict] | None = None) -> dict:
    """{checks: [{ok, title, note}], passed, total, label} for one post.
    findings: the brand's own (insights_service.what_works()["insights"])."""
    from services.insights_service import traits

    platform = (platform or "").lower()
    caption = (caption or "").strip()
    tags = [t for t in hashtags or [] if str(t).strip()]
    item = {"caption": caption, "hashtags": tags, "media": media, "images": images or []}
    mine = traits(item)
    # the brand's clearest finding per trait on this platform, what does better first
    best = {}
    for f in findings or []:
        if f["platform"] == platform and f["trait"] not in best:
            best[f["trait"]] = f
    where = NAMES.get(platform, "this platform")
    checks = []

    first = opening_of(caption)
    checks.append(_check(len(first) <= TARGET_LENGTH, "Opening fits before “…more”" if len(first) <= TARGET_LENGTH
                         else "Opening runs past “…more”",
                         f"{where} shows about {TARGET_LENGTH} characters first. Put the point up front."
                         if len(first) > TARGET_LENGTH else f"{len(first)} characters: readers see all of it."))
    f = best.get("opening")
    if f:
        good = (mine["opening"] == f["value"]) == (f["lift"] >= 1)
        words = BUCKET_WORDS[("opening", f["value"])]
        checks.append(_check(good, "Opening style works for you" if good else "Try a different opening",
                             f"Your {where} posts that open with {words} get {f['lift']}× your others."))

    if _mostly_latin(caption):
        has_cta = bool(CTA.search(caption)) or caption.rstrip().endswith("?")
        checks.append(_check(has_cta, "Clear next step" if has_cta else "No call to action",
                             "Readers know what to do next." if has_cta
                             else "Ask a question or say what to do next: comment, book, visit, save."))

    f = best.get("length")
    if f:
        good = (mine["length"] == f["value"]) == (f["lift"] >= 1)
        checks.append(_check(good, "Length works for you" if good else "Length is off for you",
                             f"Your {where} posts of {BUCKET_WORDS[('length', f['value'])]} get {f['lift']}× your others. "
                             f"This one is {len(caption):,} characters."))
    elif platform in LENGTH:
        lo, hi = LENGTH[platform]
        good = lo <= len(caption) <= hi
        checks.append(_check(good, "Good length" if good else ("Quite short" if len(caption) < lo else "Quite long"),
                             f"{len(caption):,} characters. {lo:,} to {hi:,} usually works on {where}."))

    f = best.get("hashtags")
    if f:
        good = (mine["hashtags"] == f["value"]) == (f["lift"] >= 1)
        checks.append(_check(good, "Hashtags work for you" if good else "Hashtag count is off for you",
                             f"Your {where} posts with {BUCKET_WORDS[('hashtags', f['value'])]} get {f['lift']}× your others. "
                             f"This one has {len(tags)}."))
    elif platform in HASHTAGS:
        lo, hi = HASHTAGS[platform]
        good = lo <= len(tags) <= hi
        checks.append(_check(good, "Good number of hashtags" if good else f"{len(tags)} hashtags is {'a lot' if len(tags) > hi else 'few'} for {where}",
                             f"{lo} to {hi} usually works on {where}."))

    f = best.get("format")
    if f and media is not None:
        good = (mine["format"] == f["value"]) == (f["lift"] >= 1)
        checks.append(_check(good, "Format works for you" if good else "Format does less well for you",
                             f"On {where}, {BUCKET_WORDS[('format', f['value'])]} get {f['lift']}× your others."))

    passed = sum(c["ok"] for c in checks)
    total = len(checks)
    label = "Strong" if passed == total else "Good" if passed >= total - 1 else "Needs work"
    return {"checks": checks, "passed": passed, "total": total, "label": label, "personal": bool(best)}
