"""/hiring: a recruitment post - images with the role on them, plus the job
post caption - designed differently every time.

  plan_job       one text call turns the brief ("Delivery drivers in Pune,
                 paid training, apply by 30 Nov") into the job's details,
                 the short words printed on the image, and the caption
  pick_design    a layout, a scene and a colour treatment, never repeating a
                 layout from the user's last few /hiring posts
  image_prompt   the image prompt for one size: the design, the exact words to
                 print, the brand's colours

The image model draws the words itself (chosen over overlaying them, for
freer designs), so the on-image text is kept to a few short lines and given
word for word. Both sizes of one run share the design, so they read as a set.
"""

import random
import re

# Where the words sit and how the image is composed. Each keeps the
# bottom-right corner free: the real logo is stamped there afterwards.
LAYOUTS = {
    "bold_headline": ("Bold headline", ("A huge, bold headline filling the top half, the role underneath in a smaller weight, "
                                       "the picture below; strong, poster-like typography.")),
    "split": ("Split screen", ("The canvas split into two blocks: the picture on one side, a solid colour panel on the other "
                              "carrying all the text, left-aligned.")),
    "badge": ("Hiring badge", ("A round or ribbon badge reading the headline, the role in large type beside it, the picture "
                              "behind softly blurred.")),
    "minimal_card": ("Minimal card", ("A clean card or panel floating in the middle of the picture, holding the text with "
                                     "generous white space; calm and premium.")),
    "illustrated": ("Illustrated", "A flat vector illustration of the work, the text set in a clear band across the top."),
    "perks": ("Perks list", ("The role at the top, the perks as two or three short ticked lines with simple icons, the call to "
                            "action as a button-like pill.")),
    "photo_overlay": ("Photo with overlay", ("A full-bleed photo with a dark gradient over the lower half, the text in white "
                                            "on the gradient.")),
    "editorial": ("Editorial", ("Magazine-cover style: oversized serif headline, the role as a cover line, a refined, "
                               "asymmetric layout.")),
}

# What the picture shows ({workplace} is the setting the job planner described)
SCENES = {
    "workplace": ("Workplace", ("A realistic photo of the place the work happens: {workplace}. Natural light, no people's "
                               "faces in focus.")),
    "people": ("People at work", ("People doing the job in {workplace}, seen from behind or at a distance so no face is "
                                 "identifiable; a diverse, welcoming team.")),
    "abstract": ("Abstract shapes", ("No photo: bold abstract geometric shapes and soft gradients in the brand's colours, "
                                    "loosely suggesting {workplace}.")),
    "city": ("City", "The skyline or a recognisable street scene of {location}, softly out of focus, so the place is clear."),
    "illustration": ("Flat illustration", ("A friendly flat illustration of {workplace} and the tools of the job, simple "
                                          "shapes, no faces.")),
}

# How the brand's colours are used
TREATMENTS = {
    "brand": ("Brand colours", "The brand's main colour dominates, with its second colour as the accent."),
    "dark": ("Dark", "A deep, dark background with the brand's colour as a bright accent on the key words."),
    "light": ("Light", "A light, airy, mostly white background with the brand's colour on the headline only."),
    "duotone": ("Duotone", "The picture in a two-colour duotone made from the brand's colours."),
    "accent": ("Accent pop", "Neutral greys and black, with a single vivid pop of the brand's colour on the call to action."),
}

# A layout is not used again within this many /hiring posts of the same user
NO_REPEAT_LAYOUTS = 3
LIMITS = {"headline": 22, "role": 38, "location": 30, "cta": 18}
DEFAULT_HEADLINE, DEFAULT_CTA = "We're hiring", "Apply now"

PLAN_SYSTEM_PROMPT = """You write a recruitment social media post for a company that is hiring.
Return ONLY this JSON:
{"role": "", "location": "", "employment_type": "", "perks": [], "deadline": "", "apply": "", "headline": "",
 "cta": "", "workplace": "", "caption": "", "hashtags": []}

- role: the job title, as short as possible (at most 5 words), e.g. "Delivery Drivers".
- location: the city or area if the brief gives one, else "". At most 3 words.
- employment_type: e.g. "Full-time", "Part-time", "Contract", "Internship", or "" if not given.
- perks: up to 3 benefits the brief actually mentions, each at most 4 words. Never invent salary, benefits or perks.
- deadline: the closing date exactly as the brief gives it, or "".
- apply: how to apply if the brief says (an email, a link, "DM us"), else "".
- headline: 2-3 words for the top of the image, e.g. "We're hiring", "Join our team", "Now hiring".
- cta: 2-3 words for the call to action on the image, e.g. "Apply now", "Join us", "DM to apply".
- workplace: one short phrase describing where this job is done, for the picture, e.g. "a busy delivery depot
  with vans at dawn".
- caption: the post caption, 3-5 short sentences in the company's voice: the role, where, what makes it good,
  and how to apply. No hashtags in it.
- hashtags: 3-6 relevant hashtags, each starting with #, e.g. #Hiring and the role and city.
- Fair hiring: never state or imply a preference for age, sex, religion, nationality, marital status or
  disability (no "young", "male", "recent graduates only", "native speaker").
- Use only facts from the brief and the company profile."""


class HiringError(ValueError):
    """The brief can't be turned into a hiring post (shown to the user)."""


def _short(value, limit: int) -> str:
    text = " ".join(str(value or "").replace('"', "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut or text[:limit]


def normalize_plan(raw: dict, brief: str = "") -> dict:
    raw = raw if isinstance(raw, dict) else {}
    role = _short(raw.get("role"), LIMITS["role"])
    if not role:
        raise HiringError("Say which role you are hiring for, e.g. /hiring Delivery drivers in Pune.")
    hashtags = []
    for tag in raw.get("hashtags") or []:
        tag = "#" + re.sub(r"[^\w]", "", str(tag))
        if len(tag) > 1 and tag.lower() not in {t.lower() for t in hashtags}:
            hashtags.append(tag[:40])
    if not any(t.lower() == "#hiring" for t in hashtags):
        hashtags.insert(0, "#Hiring")
    return {
        "role": role,
        "location": _short(raw.get("location"), LIMITS["location"]),
        "employment_type": _short(raw.get("employment_type"), 20),
        "perks": [p for p in (_short(p, 32) for p in (raw.get("perks") or [])[:3]) if p],
        "deadline": _short(raw.get("deadline"), 30),
        "apply": _short(raw.get("apply"), 60),
        "headline": _short(raw.get("headline"), LIMITS["headline"]) or DEFAULT_HEADLINE,
        "cta": _short(raw.get("cta"), LIMITS["cta"]) or DEFAULT_CTA,
        "workplace": _short(raw.get("workplace"), 120) or f"the workplace of a {role.lower()}",
        "caption": str(raw.get("caption") or "").strip()[:1500] or f"We're hiring {role}. {brief.strip()}".strip(),
        "hashtags": hashtags[:6],
    }


def plan_job(brief: str, brand_block: str = "", llm=None) -> tuple[dict, dict]:
    """(job, usage) for the brief. HiringError when no role can be found."""
    if not (brief or "").strip():
        raise HiringError("Describe the job after /hiring, e.g. /hiring Delivery drivers in Pune, apply by 30 Nov.")
    from services.llm_service import LLMService

    user_prompt = f"BRIEF: {brief.strip()[:600]}\n\n" + (brand_block.strip()[:1500] or "")
    result, usage = (llm or LLMService()).generate_json(PLAN_SYSTEM_PROMPT, user_prompt, temperature=0.6, max_tokens=900,
                                                        return_usage=True, reasoning_effort="low", on_partial=lambda _text: None)
    return normalize_plan(result, brief), usage or {}


def pick_design(recent: list[dict] | None = None, rng: random.Random | None = None, has_location: bool = True) -> dict:
    """A layout, scene and treatment for the next post. recent: the designs of
    the user's last /hiring posts, newest first. No layout from the last
    NO_REPEAT_LAYOUTS posts, and neither the scene nor the treatment of the last one."""
    rng = rng or random.Random()
    recent = [d for d in (recent or []) if isinstance(d, dict)]
    used = {d.get("layout") for d in recent[:NO_REPEAT_LAYOUTS]}
    last = recent[0] if recent else {}
    layouts = [k for k in LAYOUTS if k not in used] or list(LAYOUTS)
    scenes = [k for k in SCENES if k != last.get("scene") and (has_location or k != "city")] or ["workplace"]
    treatments = [k for k in TREATMENTS if k != last.get("treatment")]
    return {"layout": rng.choice(layouts), "scene": rng.choice(scenes), "treatment": rng.choice(treatments)}


def design_names(design: dict) -> dict:
    return {"layout": LAYOUTS[design["layout"]][0], "scene": SCENES[design["scene"]][0],
            "treatment": TREATMENTS[design["treatment"]][0]}


def image_text(job: dict, aspect: str) -> list[str]:
    """The words printed on the image, line by line (fewer on a square)."""
    lines = [job["headline"], job["role"]]
    if job["location"]:
        lines.append(job["location"])
    if job["perks"] and aspect != "1:1":
        lines.extend(job["perks"][:2])
    if job["deadline"] and aspect != "1:1":
        lines.append(f"Apply by {job['deadline']}")
    lines.append(job["cta"])
    return lines


def image_prompt(scene: str, job: dict, design: dict, aspect: str, brand: dict | None = None, has_photo: bool = False) -> str:
    """The image prompt for one size of a hiring post."""
    from services.image_presets import ASPECT_HINTS

    brand = brand or {}
    lines = image_text(job, aspect)
    parts = [scene]
    parts.append("Layout: " + LAYOUTS[design["layout"]][1])
    if has_photo:
        parts.append("Picture: use the attached photo (the company's team or workplace) as the main picture; keep it "
                     "recognisable and do not change the people in it.")
    else:
        parts.append("Picture: " + SCENES[design["scene"]][1].format(workplace=job["workplace"], location=job["location"] or "the city"))
    colors = [c for c in (brand.get("primary_colors") or []) if isinstance(c, str)][:3]
    parts.append("Colours: " + TREATMENTS[design["treatment"]][1]
                 + (f" The brand's colours are {', '.join(colors)}." if colors else " Use a confident, modern palette."))
    if brand.get("visual_style"):
        parts.append(f"Brand's visual style: {str(brand['visual_style']).strip()[:160]}.")
    quoted = "\n".join(f'  "{line}"' for line in lines)
    parts.append(
        "Print exactly these words on the image, each on its own line or block, spelled exactly as written, "
        "in a clean, highly legible sans-serif or the layout's typeface, large enough to read on a phone:\n"
        f"{quoted}\n"
        f"The first line is the headline and \"{job['role']}\" is the most prominent words after it. "
        "Add no other words, letters, numbers, prices, watermarks or placeholder text anywhere.")
    parts.append("Keep the bottom-right corner clear of text and important detail.")
    parts.append(ASPECT_HINTS.get(aspect, ""))
    return "\n\n".join(p for p in parts if p)
