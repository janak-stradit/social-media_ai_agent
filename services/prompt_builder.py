"""Prompt construction for image generation and multi-turn image editing.

Kept out of the API handlers so every image prompt is built in one place.
An edit is built from the image's lineage (see db.image_lineage), not just the
latest message: the original visual intent and the edits already applied are
restated, so a later edit doesn't quietly undo an earlier one.
"""

IMAGE_TEXT_RULE = (
    "On-image text: at most one short headline (max 8 words) and one short subline, spelled exactly; "
    "no statistics, percentages, citations, source names or small print."
)

# How much of the lineage goes into an edit prompt - enough to keep earlier
# changes, short enough not to over-constrain the image model.
MAX_INTENT_CHARS = 400
MAX_PREVIOUS_EDITS = 3


def _on_image_text_block(plan: dict) -> str:
    text = plan.get("on_image_text") or {}
    headline = (text.get("headline") or "").strip()
    subline = (text.get("subline") or "").strip()
    # Backstop for the planner rule: a stat line is exactly the dense, easily
    # garbled (and unsourced-claim) text this path exists to remove.
    if "%" in subline:
        subline = ""
    if not headline:
        return IMAGE_TEXT_RULE
    lines = [f'Headline: "{headline}"'] + ([f'Subline: "{subline}"'] if subline else [])
    return (
        "The ONLY text allowed on the image is the following, spelled exactly letter for letter:\n"
        + "\n".join(lines)
        + "\nRemove every other word, number, statistic, label and small print. "
        "Use large, clean, high-contrast sans-serif type."
    )


def build_image_edit_prompt(
    plan: dict, platform: str, base_media_prompt: str, has_reference: bool, lineage: list[dict] | None = None
) -> str:
    """The image model's prompt for a refinement turn.

    lineage: the edited image's versions, oldest (original) first - each
    {"prompt", "edit_instruction"}. With a reference image the model gets:
    what to keep, the original intent, the edits already applied, and the new
    change. Without one (first image for a post) it gets a creation prompt.
    """
    instruction = (plan.get("image_instruction") or "").strip()
    lineage = lineage or []

    if not has_reference:
        parts = [
            f"Create a professional {platform.capitalize()} social media image. {base_media_prompt[:800]}",
            instruction,
        ]
    else:
        parts = [
            (
                "Edit the provided image. Keep the subject, people, vehicles, layout, composition, color palette, "
                "lighting, brand elements and aspect ratio exactly as they are, except for the new change below."
            )
        ]
        intent = ((lineage[0].get("prompt") if lineage else "") or base_media_prompt or "").strip()
        if intent:
            parts.append(f"Original intent of this image: {intent[:MAX_INTENT_CHARS]}")
        previous = [v.get("edit_instruction") for v in lineage[1:] if v.get("edit_instruction")]
        if previous:
            recent = previous[-MAX_PREVIOUS_EDITS:]
            parts.append(
                "Edits already applied (keep them): " + "; ".join(e.strip().rstrip(".") for e in recent) + "."
            )
        if instruction:
            parts.append(f"New change: {instruction}")
    parts.append(_on_image_text_block(plan))
    return "\n\n".join(p for p in parts if p)


def build_image_versions_block(images: list[dict], active_id: int | None) -> str:
    """The conversation's image versions for the refine planner, so it can
    resolve "the first image", "the previous version" or "the one before the
    last edit" to a version number (image_ref)."""
    if not images:
        return ""
    number = {img["id"]: n for n, img in enumerate(images, 1)}
    lines = []
    for n, img in enumerate(images, 1):
        if img.get("parent_id") in number:
            what = f'edit of #{number[img["parent_id"]]}: "{(img.get("edit_instruction") or "").strip()[:120]}"'
        else:
            what = f'original: "{(img.get("prompt") or "").strip()[:120]}"'
        active = "  <- ACTIVE (the image the user is looking at)" if img["id"] == active_id else ""
        lines.append(f"#{n} [{img.get('platform') or 'post'}] {what}{active}")
    return "IMAGE VERSIONS IN THIS CONVERSATION (oldest first):\n" + "\n".join(lines)


# ── Image commands (services/image_presets.py) ─────────────────────────────
PRODUCT_LOCK = (
    "Use the product from the reference photo exactly as it is: same shape, proportions, colours, materials, "
    "label and printed text. Do not redesign, recolour or add text to the product, and keep the whole product visible. "
    "Show the product exactly once - no second copy, smaller duplicate, reflection-copy or version of it anywhere else "
    "in the image."
)


def build_preset_prompt(preset: dict, output: dict, product_notes: str = "", brand: dict | None = None,
                        user_text: str = "", occasion: str | None = None) -> str:
    """The image prompt for one output of an image command: the command's
    scene, the product lock, the exact shape, the brand's look and the user's
    extra direction (which wins over the scene's defaults). occasion fills
    the /festive scene ("Diwali", "UAE National Day", ...)."""
    from services.image_presets import ASPECT_HINTS

    parts = [preset["scene"].replace("{occasion}", occasion or "seasonal holiday"), PRODUCT_LOCK]
    if product_notes:
        parts.append(f"The product (from the photo): {product_notes.strip()[:500]}")
    brand = brand or {}
    colors = [c for c in (brand.get("primary_colors") or []) if isinstance(c, str)][:4]
    look = []
    if colors:
        look.append(f"accent colours {', '.join(colors)}")
    if brand.get("visual_style"):
        look.append(str(brand["visual_style"]).strip()[:200])
    if look:
        parts.append("Match the brand's look: " + "; ".join(look) + ".")
    if user_text:
        parts.append(f"Extra direction from the user (follow it where it differs from the above): {user_text.strip()[:400]}")
    parts.append(ASPECT_HINTS.get(output["aspect"], ""))
    return "\n\n".join(p for p in parts if p)


AD_COPY_SYSTEM_PROMPT = """You write Meta (Facebook and Instagram) ad copy for one product.
Return ONLY this JSON:
{"headline": "<max 40 characters>", "primary_text": "<max 125 characters>",
 "description": "<max 30 characters>", "cta": "<one of: Shop Now, Learn More, Order Now, Get Offer, Sign Up, Book Now, Contact Us, Download>"}
Rules: plain, specific and benefit-led; match the brand voice if given. Never invent prices, discounts,
statistics, awards, guarantees or health claims - only say what the inputs support. No hashtags. At most one emoji."""


def build_ad_copy_prompt(product_notes: str, brand_block: str, user_text: str) -> str:
    return (
        f"PRODUCT (from the photo): {product_notes.strip()[:600] or 'see the user notes'}\n\n"
        f"{brand_block.strip()[:1200]}\n\n"
        f"USER NOTES: {user_text.strip()[:400] or '(none)'}"
    )
