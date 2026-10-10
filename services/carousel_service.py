"""Carousels: several slides that tell one story, for LinkedIn and Instagram.

Studio Chat's /carousel command (services/image_presets.py, run by
api/routes.run_image_preset): the brief is turned into a slide plan (hook,
points, call to action + the post caption and hashtags) by one text call,
then every slide is drawn in the same style. A carousel can also be
assembled from images already made in the conversation (Send for approval).

LinkedIn has two kinds of carousel: a multi-image post, and a document post
(a PDF the viewer swipes through). build_pdf makes that PDF from the slides.
"""

import logging
import os
import re
import uuid

logger = logging.getLogger(__name__)

MIN_SLIDES, MAX_SLIDES, DEFAULT_SLIDES = 3, 10, 5

PLAN_SYSTEM_PROMPT = """You plan a social media carousel: {count} slides that tell one story, swiped in order.
Return ONLY this JSON:
{{"style": "", "slides": [{{"title": "", "headline": "", "visual": ""}}], "caption": "", "hashtags": []}}

- slides: exactly {count}. Slide 1 is the hook that makes people swipe; the middle slides each make one point;
  the last slide is a clear call to action.
- title: 1-3 words naming the slide's role (e.g. "Hook", "Problem", "Tip 2", "Call to action").
- headline: the text printed on the slide, at most 8 words, plain and readable at a glance.
- visual: what the slide shows behind or around the headline, one sentence. No people's faces unless asked.
- style: one sentence describing the shared look of every slide (layout, colours, typography, mood), using the
  brand's colours and visual style when given, so the slides look like one set.
- caption: the post caption that goes with the carousel, 2-4 short sentences, in the brand's voice, inviting
  people to swipe. No hashtags in it.
- hashtags: 3-6 relevant hashtags, each starting with #.
- Use only facts from the brief and the brand profile: never invent numbers, prices, awards or claims."""


def slide_count(text: str | None) -> int:
    """'5 slides on ...' -> 5 (kept between MIN_SLIDES and MAX_SLIDES)."""
    match = re.search(r"\b(\d{1,2})\s*(?:slides?|cards?|pages?)\b", text or "", re.IGNORECASE)
    if not match:
        return DEFAULT_SLIDES
    return max(MIN_SLIDES, min(MAX_SLIDES, int(match.group(1))))


def carousel_aspect(text: str | None) -> str:
    """Square unless the brief asks for portrait (4:5 shows larger in the Instagram and LinkedIn feeds)."""
    return "4:5" if re.search(r"\b(4\s*:\s*5|portrait|vertical|tall)\b", text or "", re.IGNORECASE) else "1:1"


def placeholder_outputs(count: int, aspect: str) -> list[dict]:
    return [{"key": f"slide{i}", "aspect": aspect, "label": f"Slide {i}"} for i in range(1, count + 1)]


def plan_carousel(brief: str, count: int, brand_block: str = "", product_notes: str = "", llm=None) -> tuple[dict, dict]:
    """(plan, usage). The plan always has exactly `count` slides."""
    from services.llm_service import LLMService

    user_prompt = (
        f"BRIEF: {brief.strip()[:600] or 'A carousel about what this company offers.'}\n\n"
        + (f"PRODUCT (from the attached photo): {product_notes.strip()[:500]}\n\n" if product_notes else "")
        + (brand_block.strip()[:1500] or "")
    )
    result, usage = (llm or LLMService()).generate_json(
        PLAN_SYSTEM_PROMPT.format(count=count), user_prompt, temperature=0.6, max_tokens=1600,
        return_usage=True, reasoning_effort="low", on_partial=lambda _text: None,
    )
    return normalize_plan(result, count, brief), usage or {}


def normalize_plan(result, count: int, brief: str = "") -> dict:
    """Exactly `count` slides with short, clean fields - padding or trimming what the model returned."""
    result = result if isinstance(result, dict) else {}
    slides = [s for s in (result.get("slides") or []) if isinstance(s, dict)][:count]
    roles = ["Hook"] + [f"Point {i}" for i in range(1, count - 1)] + ["Call to action"]
    while len(slides) < count:
        slides.append({"title": roles[len(slides)], "headline": "", "visual": brief[:200]})
    clean = []
    for i, s in enumerate(slides):
        words = str(s.get("headline") or "").split()
        clean.append({
            "title": str(s.get("title") or roles[i]).strip()[:30],
            "headline": " ".join(words[:10]),
            "visual": str(s.get("visual") or "").strip()[:300],
        })
    hashtags = []
    for tag in result.get("hashtags") or []:
        tag = "#" + re.sub(r"[^\w]", "", str(tag).lstrip("#"))
        if len(tag) > 1 and tag.lower() not in {h.lower() for h in hashtags}:
            hashtags.append(tag)
    return {
        "style": str(result.get("style") or "").strip()[:300],
        "slides": clean,
        "caption": str(result.get("caption") or "").strip()[:1500],
        "hashtags": hashtags[:8],
    }


def outputs_for(plan: dict, aspect: str) -> list[dict]:
    return [{"key": f"slide{i}", "aspect": aspect, "label": f"Slide {i} · {s['title']}"}
            for i, s in enumerate(plan["slides"], start=1)]


def slide_prompt(scene: str, plan: dict, index: int, brand: dict | None = None, product_notes: str = "",
                 has_product: bool = False, aspect: str = "1:1") -> str:
    """The image prompt for slide `index` (1-based)."""
    from services.image_presets import ASPECT_HINTS

    slide = plan["slides"][index - 1]
    count = len(plan["slides"])
    parts = [scene]
    if plan.get("style"):
        parts.append(f"Shared look of every slide in this carousel: {plan['style']}")
    brand = brand or {}
    colors = [c for c in (brand.get("primary_colors") or []) if isinstance(c, str)][:4]
    if colors:
        parts.append(f"Use the brand colours {', '.join(colors)}.")
    parts.append(f"This is slide {index} of {count} ({slide['title']}).")
    if slide["headline"]:
        parts.append(f'Print this headline on the slide, spelled exactly, large and easy to read: "{slide["headline"]}". '
                     "No other text except a small slide number.")
    if slide["visual"]:
        parts.append(f"Visual: {slide['visual']}")
    if has_product:
        parts.append("Show the product from the attached photo exactly as it is - same shape, colours and details.")
        if product_notes:
            parts.append(f"The product: {product_notes.strip()[:300]}")
    parts.append(ASPECT_HINTS.get(aspect, ""))
    return "\n\n".join(p for p in parts if p)


# ── LinkedIn document carousel ──────────────────────────────────────────────


def build_pdf(image_paths: list[str], out_dir: str) -> str:
    """One PDF page per slide, in order (all pages the size of the first slide).
    Returns the file name inside out_dir."""
    from PIL import Image

    pages = []
    size = None
    for path in image_paths:
        with Image.open(path) as img:
            page = img.convert("RGB")
            size = size or page.size
            if page.size != size:
                page = page.resize(size, Image.LANCZOS)
            pages.append(page)
    if not pages:
        raise ValueError("A document carousel needs at least one slide.")
    name = f"carousel_{uuid.uuid4().hex[:10]}.pdf"
    pages[0].save(os.path.join(out_dir, name), "PDF", save_all=True, append_images=pages[1:], resolution=150)
    return name
