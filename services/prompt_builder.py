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
