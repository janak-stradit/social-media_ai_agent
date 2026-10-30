"""The same post in other languages (Studio Chat post card -> "Translate").

A finished post is rewritten in each language the person ticks - the way a
native speaker would post it, not word for word (language_service.instruction).
One AI call per language covers every platform of the post; the languages run
side by side. Brand, product and company names, numbers, prices and URLs stay
exactly as they are, and about half the hashtags stay in English.

Each language becomes another version of the same post (content._meta:
version_of + language), so Edit, Schedule, Send for approval and Preview work
on it as on any version, and it doesn't count as a new post on the plan. The
images and videos are reused as they are. The caller re-runs the compliance
check on every translated caption.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from services.language_service import LANGUAGES, normalize

logger = logging.getLogger(__name__)

MAX_LANGUAGES = 5  # per click: keeps the wait and the cost predictable
MAX_CAPTION = 3000
RTL = {"ar", "ur", "fa", "he", "ps", "sd"}  # written right to left


class TranslateError(ValueError):
    """Shown to the person as is."""


def language_meta(code: str) -> dict:
    code = normalize(code)
    return {"code": code, "name": LANGUAGES[code][0], "short": LANGUAGES[code][2], "rtl": code in RTL}


def pick_languages(requested, source: str) -> list[str]:
    """The languages to make: known ones, each once, not the post's own."""
    source = normalize(source)
    picked = []
    for code in requested if isinstance(requested, list) else []:
        code = str(code or "").strip().lower()
        if code in LANGUAGES and code != source and code not in picked:
            picked.append(code)
    if not picked:
        raise TranslateError("Pick at least one language other than the post's own.")
    if len(picked) > MAX_LANGUAGES:
        raise TranslateError(f"Pick up to {MAX_LANGUAGES} languages at a time.")
    return picked


def post_texts(content: dict) -> dict[str, dict]:
    """{platform: {caption, hashtags}} for the platforms of a run that have a caption."""
    texts = {}
    for key, node in (content or {}).items():
        if str(key).startswith("_") or not isinstance(node, dict):
            continue
        caption = ((node.get("caption") or {}).get("primary_caption") or "").strip()
        if caption:
            tags = (node.get("hashtags") or {}).get("hashtags") or []
            texts[key] = {"caption": caption[:MAX_CAPTION], "hashtags": [str(t) for t in tags if t]}
    return texts


def _clean_tags(tags) -> list[str]:
    out = []
    for tag in tags if isinstance(tags, list) else []:
        tag = "".join(str(tag or "").split())
        if not tag:
            continue
        tag = tag if tag.startswith("#") else "#" + tag
        if tag not in out:
            out.append(tag)
    return out[:30]


def translate(texts: dict[str, dict], code: str, llm=None) -> tuple[dict[str, dict], dict]:
    """({platform: {caption, hashtags}} in that language, the AI call's usage)."""
    from services.language_service import instruction

    code = normalize(code)
    system_prompt = f"""You adapt finished social media posts into another language for the same brand.
You get each platform's caption and hashtags. Rewrite every one for the same platform.
Rules:
- Keep the meaning, the structure (line breaks, bullet points, emojis) and the call to action of each post.
- Use only facts that are in the post. Never add or drop claims, numbers, offers or disclaimers.{instruction(code)}{instruction(code, "hashtags")}
- Hashtags: the same number as the original, no spaces inside a hashtag.
Return JSON: {{"posts": {{"<platform>": {{"caption": "...", "hashtags": ["#..."]}}}}}}"""
    user_prompt = "POSTS:\n" + json.dumps(texts, ensure_ascii=False, indent=1)
    if llm is None:
        from services.llm_service import LLMService

        llm = LLMService()
    result, usage = llm.generate_json(
        system_prompt, user_prompt, temperature=0.4, max_tokens=600 + 900 * len(texts), return_usage=True
    )
    posts = (result or {}).get("posts") if isinstance(result, dict) else None
    posts = (
        {str(k).lower(): v for k, v in (posts or {}).items() if isinstance(v, dict)} if isinstance(posts, dict) else {}
    )
    out = {}
    for platform, original in texts.items():
        got = posts.get(platform) or {}
        caption = str(got.get("caption") or "").strip()
        if not caption:
            raise TranslateError(f"The {LANGUAGES[code][2]} version came back empty. Please try again.")
        out[platform] = {"caption": caption, "hashtags": _clean_tags(got.get("hashtags")) or original["hashtags"]}
    return out, usage or {}


def translate_all(texts: dict[str, dict], codes: list[str], llm=None) -> dict[str, dict]:
    """{code: {"posts": ..., "usage": ...} or {"error": "..."}} - one language failing doesn't stop the others."""

    def one(code):
        try:
            posts, usage = translate(texts, code, llm)
            return code, {"posts": posts, "usage": usage}
        except TranslateError as err:
            return code, {"error": str(err)}
        except Exception as err:  # noqa: BLE001 - reported per language
            logger.warning("Translate to %s failed: %s", code, err)
            return code, {"error": f"Couldn't write the {LANGUAGES[code][2]} version. Please try again."}

    with ThreadPoolExecutor(max_workers=min(MAX_LANGUAGES, len(codes))) as pool:
        return dict(pool.map(one, codes))
