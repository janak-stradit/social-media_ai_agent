"""Other ways to open a post (Studio Chat "Edit" -> "Other openings").

The first line decides whether people stop and read, and platforms cut a
caption off after it ("...more"). This writes a few different first lines for
a caption that already exists: one small AI call, only when the person asks.

Nothing is saved here. Picking an opening only changes the text in the
editor; the person saves it like any hand edit (services/post_edit_service.py),
so the compliance check is marked stale and runs again when the post is sent
for approval. The rest of the post is never touched.
"""

import logging

logger = logging.getLogger(__name__)

COUNT = 3
MAX_CAPTION = 3000  # what an approval request carries; longer is cut for the prompt only
TARGET_LENGTH = 150  # about what Instagram and LinkedIn show before "...more"
MAX_OPENING = 220  # a longer answer isn't a first line
NAMES = {"instagram": "Instagram", "facebook": "Facebook", "linkedin": "LinkedIn", "youtube": "YouTube"}


class OpeningError(ValueError):
    """Shown to the person as is."""


def opening_of(caption: str) -> str:
    """The caption's first line with text in it."""
    for line in (caption or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _clean(options, current: str) -> list[str]:
    kept, seen = [], {current.strip().lower()}
    for option in options if isinstance(options, list) else []:
        text = " ".join(str(option or "").split()).strip().strip('"“”').strip()
        key = text.lower()
        if not text or key in seen or text.startswith("#") or len(text) > MAX_OPENING:
            continue
        seen.add(key)
        kept.append(text)
    return kept[:COUNT]


def other_openings(caption: str, platform: str = "", llm=None) -> tuple[list[str], dict]:
    """(up to COUNT new first lines, the AI call's usage)."""
    caption = (caption or "").strip()
    current = opening_of(caption)
    if not current:
        raise OpeningError("Write the post first, then try other openings.")
    where = NAMES.get((platform or "").lower(), "social media")
    system_prompt = f"""You write the first line of {where} posts.
You get a finished post. Write {COUNT} different first lines that could replace its current first line.
Rules:
- Each one takes a different angle: a question, a bold statement, a concrete benefit or number that is already in the post.
- Use only facts that are in the post. Never invent numbers, claims, results, quotes, prices or offers.
- Write in the same language, voice and spelling of names as the post. Use emojis only if the post already does.
- One line each, at most {TARGET_LENGTH} characters. No hashtags. No quotation marks around it.
- It must lead naturally into the rest of the post, which stays as it is.
Return JSON: {{"openings": ["...", "...", "..."]}}"""
    user_prompt = f"Current first line:\n{current}\n\nThe whole post:\n{caption[:MAX_CAPTION]}"
    if llm is None:
        from services.llm_service import LLMService

        llm = LLMService()
    result, usage = llm.generate_json(system_prompt, user_prompt, temperature=0.8, max_tokens=600,
                                      return_usage=True, reasoning_effort="minimal")
    options = _clean((result or {}).get("openings") if isinstance(result, dict) else None, current)
    if not options:
        raise OpeningError("No new openings this time. Please try again.")
    return options, usage or {}
