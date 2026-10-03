"""Studio Chat image commands: a product photo + "/3dbillboard", "/metaad",
"/premiumshowcase" ... -> a specific kind of image of that product.

One entry per command. To add a command, add an entry here (prompt in
services/prompt_builder.build_preset_prompt reads "scene" and "outputs").
Every image counts toward the user's daily image limit.
"""

# Exact pixel size per aspect ratio (Meta's recommended ad sizes; 16:9 for
# landscape). Images are cropped to these after generation - the image model
# treats the requested shape only as a hint.
ASPECT_SIZES = {
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "9:16": (1080, 1920),
    "16:9": (1600, 900),
}

ASPECT_HINTS = {
    "1:1": "Square 1:1 composition.",
    "4:5": "Vertical 4:5 portrait composition.",
    "9:16": "Tall vertical 9:16 full-screen composition; keep the product in the middle third, with empty space at the top and bottom for on-screen overlays.",
    "16:9": "Wide 16:9 landscape composition.",
}

META_CTAS = ("Shop Now", "Learn More", "Order Now", "Get Offer", "Sign Up", "Book Now", "Contact Us", "Download")

PRESETS = {
    "3dbillboard": {
        "label": "3D Billboard",
        "icon": "fa-city",
        "description": "Your product popping out of a giant 3D billboard in a city",
        "placeholder": "Add details (optional), e.g. Times Square at night",
        "requires_image": True,
        "stamp_logo": False,  # the product carries its own branding
        "platforms": [],
        "outputs": [{"key": "billboard", "aspect": "16:9", "label": "Billboard 16:9"}],
        # The pop-out illusion of the famous Shinjuku / Times Square corner screens
        # needs: an L-shaped corner screen seen diagonally, the product breaking
        # past the frame, a deep box inside the screen, and real shadows on the
        # building. Without these the model draws a product inside a flat screen.
        "scene": (
            "Create a photorealistic photo of a 3D anamorphic billboard advertisement (forced-perspective digital "
            "out-of-home ad, like the famous corner screens in Shinjuku and Times Square). A huge L-shaped LED screen "
            "wraps the corner of a modern building, seen from street level at a diagonal so both faces of the screen "
            "are visible and the illusion works. "
            "THE PRODUCT BREAKS OUT OF THE SCREEN: it bursts forward past the screen's edge and frame into the open air "
            "above the street, with about a third of it outside the display, overlapping the building's corner. "
            "Inside the screen, a deep box-like space with lit inner walls gives strong perspective depth behind it. "
            "The product is a real 3D object, not a flat picture on the screen: it casts a shadow onto the building "
            "facade and the screen frame, and the street lights reflect on it. Light streaks and small particles flow "
            "out of the screen with it. "
            "Dusk, people on the street looking up at it, traffic for scale, reflections on wet pavement. "
            "The product is the hero - very large, sharp and instantly recognisable."
        ),
    },
    "metaad": {
        "label": "Meta Ad",
        "icon": "fa-bullhorn",
        "description": "Facebook & Instagram ad image plus ad copy",
        "placeholder": "Add details (optional), e.g. summer sale, young professionals",
        "requires_image": True,
        "stamp_logo": True,
        "platforms": ["facebook", "instagram"],
        "ad_copy": True,
        "outputs": [{"key": "feed", "aspect": "1:1", "label": "Feed 1:1"}],
        # "All 3 sizes" (each one counts toward the daily image limit)
        "all_outputs": [
            {"key": "feed", "aspect": "1:1", "label": "Feed 1:1"},
            {"key": "portrait", "aspect": "4:5", "label": "Feed 4:5"},
            {"key": "story", "aspect": "9:16", "label": "Story 9:16"},
        ],
        "scene": (
            "Create a scroll-stopping advertising photo for Facebook and Instagram. The product is the clear hero, "
            "well lit and in sharp focus, on a clean, uncluttered background with tasteful accent colours. "
            "Leave clear empty space for the ad's text, but put no text, prices, buttons or logos in the image."
        ),
    },
    "premiumshowcase": {
        "label": "Premium Showcase",
        "icon": "fa-gem",
        "description": "Luxury studio photoshoot of your product",
        "placeholder": "Add details (optional), e.g. black marble, gold accents",
        "requires_image": True,
        "stamp_logo": False,
        "platforms": [],
        "outputs": [{"key": "showcase", "aspect": "1:1", "label": "Showcase 1:1"}],
        "scene": (
            "Create a premium studio product photograph: the product on an elegant pedestal or reflective surface, "
            "soft dramatic key light with a gentle rim light, a subtle reflection and soft shadow, and a minimal "
            "luxury backdrop in deep neutral tones. High-end commercial photography look, shallow depth of field."
        ),
    },
    "lifestyle": {
        "label": "Lifestyle",
        "icon": "fa-mug-hot",
        "description": "Your product in real everyday use",
        "placeholder": "Add details (optional), e.g. morning coffee at a home office",
        "requires_image": True,
        "stamp_logo": False,
        "platforms": [],
        "outputs": [{"key": "lifestyle", "aspect": "4:5", "label": "Lifestyle 4:5"}],
        "scene": (
            "Create an authentic lifestyle photograph of the product being used naturally in a real, relatable "
            "everyday setting that suits the people who would buy it. Natural light, candid documentary feel, a "
            "person's hands or partly visible figure interacting with the product, a believable environment with a "
            "few tasteful props, shallow depth of field. Not a staged studio shot; the product stays the clear, "
            "sharp focus."
        ),
    },
    "catalog": {
        "label": "Catalog",
        "icon": "fa-tag",
        "description": "Clean white-background e-commerce shot (Amazon / Shopify style)",
        "placeholder": "Add details (optional), e.g. front view, show the cap open",
        "requires_image": True,
        # Marketplaces (Amazon, Noon, Flipkart) reject listing photos with logos or watermarks
        "stamp_logo": False,
        "platforms": [],
        "outputs": [{"key": "catalog", "aspect": "1:1", "label": "Catalog 1:1"}],
        "scene": (
            "Create a clean e-commerce catalog photo: the product alone on a pure white (#FFFFFF) seamless background, "
            "evenly lit with soft, shadowless studio lighting and only a subtle natural contact shadow beneath it. "
            "Front three-quarter angle, centred, filling about 85% of the frame. No props, no text, no logos, no "
            "gradient or scenery in the background - ready for an Amazon or Shopify listing."
        ),
    },
    "festive": {
        "label": "Festive",
        "icon": "fa-gifts",
        "description": "Your product dressed up for the next festival - or name one: /festive Diwali",
        "placeholder": "Name the occasion (optional), e.g. Diwali, Eid, Christmas, National Day",
        "requires_image": True,
        "stamp_logo": True,
        "platforms": [],
        # The occasion is the one the user names, else the next upcoming
        # celebration in the brand's markets (services/festival_service.py)
        "occasion": True,
        "outputs": [{"key": "festive", "aspect": "1:1", "label": "Festive 1:1"}],
        "scene": (
            "Create a warm, celebratory {occasion} social media image with the product as the centrepiece, styled "
            "with authentic, tasteful {occasion} decorations, colours and lighting. Respect the occasion's culture "
            "and traditions: no religious figures or sacred text on or around the product. Leave clean space for a "
            "greeting, but add no text."
        ),
    },
}

# Occasions recognised in the user's /festive text (longest names first, so
# "Eid al-Adha" wins over "Eid")
KNOWN_OCCASIONS = sorted([
    "Diwali", "Holi", "Navratri", "Durga Puja", "Dussehra", "Ganesh Chaturthi", "Onam", "Pongal", "Raksha Bandhan",
    "Karwa Chauth", "Bhai Dooj", "Chhath Puja", "Guru Nanak Jayanti", "Makar Sankranti", "Baisakhi",
    "Eid al-Fitr", "Eid al-Adha", "Eid", "Ramadan", "UAE National Day", "National Day", "Saudi National Day",
    "Christmas", "New Year", "Thanksgiving", "Halloween", "Black Friday", "Cyber Monday", "Hanukkah", "Easter",
    "Valentine's Day", "Mother's Day", "Father's Day", "Independence Day", "Labor Day", "Lunar New Year",
], key=len, reverse=True)


def find_occasion(text: str | None) -> str | None:
    """The occasion the user named in their /festive text, if any."""
    low = (text or "").lower().replace("’", "'")
    for name in KNOWN_OCCASIONS:
        if name.lower() in low:
            return name
    return None


# ── Admin -> Image Settings -> Image commands ─────────────────────────────
# An admin can turn a command off or change its name, description, box hint,
# prompt and logo stamping. Only the changes are stored (app setting
# "image_presets", JSON {slug: {field: value}}), so "Reset" removes them and an
# update to a built-in prompt still reaches every command nobody customised.
OVERRIDES_KEY = "image_presets"
EDITABLE_FIELDS = ("enabled", "label", "description", "placeholder", "scene", "stamp_logo")
FIELD_LIMITS = {"label": (2, 40), "description": (5, 160), "placeholder": (0, 120), "scene": (80, 3000)}


def _overrides() -> dict:
    try:
        import json

        from db import get_setting

        data = json.loads(get_setting(OVERRIDES_KEY, "") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - no database (tests, scripts): built-in commands
        return {}


def _merged(slug: str, overrides: dict | None = None) -> dict:
    base = {"enabled": True, **PRESETS[slug]}
    changes = (overrides if overrides is not None else _overrides()).get(slug) or {}
    return {"id": slug, **base, **{k: v for k, v in changes.items() if k in EDITABLE_FIELDS}}


def get_preset(slug: str | None, include_disabled: bool = False) -> dict | None:
    """The command with any admin changes; None when unknown or turned off."""
    slug = (slug or "").strip().lstrip("/").lower()
    if slug not in PRESETS:
        return None
    preset = _merged(slug)
    return preset if preset["enabled"] or include_disabled else None


def admin_presets() -> list[dict]:
    """Every command for Admin -> Image Settings: current values, built-in
    values (for "Reset") and which fields were changed."""
    from services.prompt_builder import PRODUCT_LOCK

    overrides = _overrides()
    out = []
    for slug, default in PRESETS.items():
        p = _merged(slug, overrides)
        changed = sorted(k for k in (overrides.get(slug) or {}) if k in EDITABLE_FIELDS)
        out.append({
            "id": slug, "command": f"/{slug}", "icon": p["icon"],
            **{k: p[k] for k in EDITABLE_FIELDS},
            "defaults": {"enabled": True, **{k: default[k] for k in EDITABLE_FIELDS if k != "enabled"}},
            "customized": changed,
            "sizes": [o["label"] for o in p["outputs"]],
            "all_sizes": [o["label"] for o in p.get("all_outputs") or []],
            "ad_copy": bool(p.get("ad_copy")),
            "occasion": bool(p.get("occasion")),
            "always_added": PRODUCT_LOCK,
        })
    return out


def save_preset_changes(slug: str, data: dict) -> dict:
    """Validates and stores an admin's changes to one command. Raises
    ValueError with a readable message. Fields equal to the built-in value are
    not stored."""
    import json

    from db import save_setting

    if slug not in PRESETS:
        raise ValueError("Unknown command.")
    default = {"enabled": True, **PRESETS[slug]}
    overrides = _overrides()
    current = dict(overrides.get(slug) or {})
    for field in EDITABLE_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if field in ("enabled", "stamp_logo"):
            if not isinstance(value, bool):
                raise ValueError(f"{field} must be true or false.")
        else:
            value = " ".join(str(value or "").split()) if field != "scene" else str(value or "").strip()
            low, high = FIELD_LIMITS[field]
            if not low <= len(value) <= high:
                names = {"label": "Name", "description": "Description", "placeholder": "Box hint", "scene": "Prompt"}
                raise ValueError(f"{names[field]} must be {low}-{high} characters (it is {len(value)}).")
            if field == "scene" and PRESETS[slug].get("occasion") and "{occasion}" not in value:
                raise ValueError("The /festive prompt must contain {occasion} - it is replaced by the festival's name.")
        if value == default[field]:
            current.pop(field, None)
        else:
            current[field] = value
    if current:
        overrides[slug] = current
    else:
        overrides.pop(slug, None)
    save_setting(OVERRIDES_KEY, json.dumps(overrides))
    return next(p for p in admin_presets() if p["id"] == slug)


def reset_preset(slug: str) -> dict:
    """Back to the built-in command (removes every admin change)."""
    import json

    from db import save_setting

    if slug not in PRESETS:
        raise ValueError("Unknown command.")
    overrides = _overrides()
    overrides.pop(slug, None)
    save_setting(OVERRIDES_KEY, json.dumps(overrides))
    return next(p for p in admin_presets() if p["id"] == slug)


def preset_outputs(preset: dict, all_sizes: bool = False) -> list[dict]:
    """The images a run makes (each counts toward the daily limit)."""
    if all_sizes and preset.get("all_outputs"):
        return [dict(o) for o in preset["all_outputs"]]
    return [dict(o) for o in preset["outputs"]]


def list_presets() -> list[dict]:
    """What the Studio Chat "/" menu shows: the commands that are turned on."""
    overrides = _overrides()
    presets = [_merged(slug, overrides) for slug in PRESETS]
    return [
        {
            "id": p["id"],
            "command": f"/{p['id']}",
            "label": p["label"],
            "icon": p["icon"],
            "description": p["description"],
            "placeholder": p["placeholder"],
            "requires_image": p["requires_image"],
            "images": len(p["outputs"]),
            "all_sizes_images": len(p["all_outputs"]) if p.get("all_outputs") else None,
            "sizes": [o["label"] for o in p["outputs"]],
            "all_sizes": [o["label"] for o in p.get("all_outputs") or []],
            "ad_copy": bool(p.get("ad_copy")),
        }
        for p in presets
        if p["enabled"]
    ]
