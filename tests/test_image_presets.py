"""Studio Chat image commands (/3dbillboard, /metaad, /premiumshowcase): the
image model and text model are faked - these tests check the app around them."""

import os
import uuid

import pytest
from PIL import Image

import db
from config import Config


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def product():
    """A product photo in the uploads folder, as /api/upload leaves it."""
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    name = f"test_product_{uuid.uuid4().hex[:8]}.png"
    path = os.path.join(Config.UPLOAD_FOLDER, name)
    Image.new("RGB", (64, 64), "orange").save(path)
    yield path
    os.remove(path)


def _client(app, limit=db.IMAGE_UNLIMITED):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "h")
    db.set_user_image_access(user["id"], limit, None)
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return user, client


@pytest.fixture
def fakes(monkeypatch):
    from api import routes

    calls = {"images": [], "ad_prompts": []}

    def create_preset_image(prompt, image_path, aspect, logo_path=None, model=None):
        calls["images"].append({"prompt": prompt, "image": image_path, "aspect": aspect, "logo": logo_path})
        n = len(calls["images"])
        return {"success": True, "type": "image", "url": f"/static/uploads/preset{n}.jpg", "prompt": prompt,
                "cost": 0.55, "model_id": "gemini-3.1-flash-image", "aspect": aspect, "width": 1080, "height": 1080}

    def generate_json(system_prompt, user_prompt, **kw):
        calls["ad_prompts"].append(user_prompt)
        return ({"headline": "Stay hydrated all day with the bottle that keeps water ice cold",
                 "primary_text": "x " * 100, "description": "Leak-proof steel bottle for work and gym",
                 "cta": "Buy It Now"}, {"total_tokens": 40, "cost_usd": 0.001})

    monkeypatch.setattr(routes.media_service, "create_preset_image", create_preset_image)
    monkeypatch.setattr(routes.refine_llm, "generate_json", generate_json)
    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [])
    return calls


def test_menu_lists_the_commands(app):
    _, client = _client(app)
    presets = client.get("/api/image-presets").get_json()["presets"]
    assert [p["command"] for p in presets] == ["/3dbillboard", "/metaad", "/premiumshowcase", "/lifestyle", "/catalog", "/festive"]
    meta = next(p for p in presets if p["id"] == "metaad")
    assert meta["images"] == 1 and meta["all_sizes_images"] == 3 and meta["ad_copy"]


@pytest.mark.parametrize("image_path", [None, "", "app.py", "../../app.py", "C:/Windows/win.ini", "/etc/passwd", "nope.png"])
def test_needs_an_uploaded_product_photo(app, fakes, image_path):
    _, client = _client(app)
    r = client.post("/api/presets/run", json={"preset": "3dbillboard", "image_path": image_path})
    assert r.status_code == 400 and r.get_json()["code"] == "image_required"
    assert fakes["images"] == []


def test_billboard_run_is_saved_into_the_conversation(app, fakes, product):
    user, client = _client(app)
    r = client.post("/api/presets/run", json={"preset": "/3dbillboard", "image_path": product,
                                              "text": "Times Square at night", "product_notes": "Orange steel bottle"})
    body = r.get_json()
    assert r.status_code == 200 and body["success"], body
    call = fakes["images"][0]
    assert call["aspect"] == "16:9" and call["image"] == product and call["logo"] is None
    assert "Do not redesign, recolour" in call["prompt"]
    assert "Times Square at night" in call["prompt"] and "Orange steel bottle" in call["prompt"]

    run = db.get_run_by_id(body["run_id"], user["id"])
    assert run["story"] == "/3dbillboard Times Square at night"
    assert run["conversation_id"] == body["conversation_id"]
    assert run["content"]["_preset"]["keys"] == ["billboard"]
    image = run["content"]["billboard"]["media"]["image"]
    asset = db.get_image_asset(image["asset_id"], user["id"])
    assert asset["run_id"] == body["run_id"] and asset["conversation_id"] == body["conversation_id"]
    assert db.get_active_image_id(body["conversation_id"], user["id"]) == asset["id"]
    assert run["cost_usd"] == pytest.approx(0.55)  # the image is charged once, as part of the run


def test_meta_ad_all_sizes_and_copy_within_meta_limits(app, fakes, product):
    _, client = _client(app)
    body = client.post("/api/presets/run", json={"preset": "metaad", "image_path": product, "all_sizes": True}).get_json()
    assert [i["aspect"] for i in body["images"]] == ["1:1", "4:5", "9:16"]
    assert [c["aspect"] for c in fakes["images"]] == ["1:1", "4:5", "9:16"]
    ad = body["ad_copy"]
    assert len(ad["headline"]) <= 40 and len(ad["primary_text"]) <= 125 and len(ad["description"]) <= 30
    assert ad["cta"] == "Learn More"  # "Buy It Now" isn't a Meta button
    assert body["usage"]["media_count"] == 3


def test_meta_ad_default_is_one_feed_image(app, fakes, product):
    _, client = _client(app)
    body = client.post("/api/presets/run", json={"preset": "metaad", "image_path": product}).get_json()
    assert [i["key"] for i in body["images"]] == ["feed"]


def test_daily_limit_checked_for_all_images_up_front(app, fakes, product):
    user, client = _client(app, limit=2)
    r = client.post("/api/presets/run", json={"preset": "metaad", "image_path": product, "all_sizes": True})
    assert r.status_code == 403 and r.get_json()["code"] == "image_limit_reached"
    assert "needs 3 images and you have 2 left" in r.get_json()["error"]
    assert fakes["images"] == []  # nothing made, nothing charged
    assert client.post("/api/presets/run", json={"preset": "metaad", "image_path": product}).status_code == 200
    assert db.get_image_quota(user["id"])["used"] == 1


def test_fit_to_aspect_makes_exact_meta_sizes(tmp_path):
    from services.media_service import MediaGenerationService

    svc = MediaGenerationService()
    for aspect, size in (("9:16", (1080, 1920)), ("4:5", (1080, 1350)), ("16:9", (1600, 900)), ("1:1", (1080, 1080))):
        path = tmp_path / f"{aspect.replace(':', 'x')}.jpg"
        Image.new("RGB", (2048, 2048), "white").save(path)
        assert svc._fit_to_aspect(str(path), aspect) == size
        assert Image.open(path).size == size


def test_chat_edit_of_a_command_image_keeps_its_size(app, fakes, product, monkeypatch):
    from api import routes

    _, client = _client(app)
    run = client.post("/api/presets/run", json={"preset": "metaad", "image_path": product, "all_sizes": True}).get_json()
    story = next(i for i in run["images"] if i["key"] == "story")
    edits = []
    monkeypatch.setattr(routes.media_service, "edit_image", lambda prompt, p, refs=None, logo_path=None, model=None, aspect=None:
                        edits.append(aspect) or {"success": True, "url": "/static/uploads/e.jpg", "cost": 0.55, "size": "1080x1920"})
    monkeypatch.setattr(routes.refine_llm, "generate_json", lambda *a, **k: (
        {"intent": "refine", "targets": ["image"], "image_instruction": "make it night", "change_summary": "Night"}, {}))
    reply = client.post("/api/refine", json={
        "instruction": "make it night", "platforms": ["story"], "conversation_id": run["conversation_id"],
        "preset": run["preset"],
        "base": {"story": {"caption": "", "image_url": story["url"], "image_asset_id": story["asset_id"], "image_aspect": "9:16"}},
    }).get_json()
    assert edits == ["9:16"]
    assert reply["content"]["story"]["media"]["image"]["aspect"] == "9:16"
    saved = db.get_run_by_id(reply["run_id"])
    assert saved["content"]["_preset"]["id"] == "metaad"


def _scene(size=(1600, 900), bg=(40, 40, 70)):
    img = Image.new("RGB", size, bg)
    from PIL import ImageDraw

    ImageDraw.Draw(img).rectangle([600, 250, 1000, 650], fill=(255, 140, 40))  # the "product"
    return img


def test_white_watermark_patch_is_trimmed_from_a_corner():
    from PIL import ImageDraw

    from services.media_service import _strip_corner_patch

    for corner in ((1410, 791, 1599, 899), (0, 0, 189, 108)):  # bottom-right, top-left
        img = _scene()
        ImageDraw.Draw(img).rectangle(corner, fill=(255, 255, 255))
        out = _strip_corner_patch(img)
        assert out.size[0] < 1600 and out.size[1] < 900
        gray = out.convert("L")
        assert max(gray.getpixel(p) for p in ((2, 2), (out.size[0] - 3, out.size[1] - 3))) < 200  # no white corner left


def test_real_white_backgrounds_are_left_alone():
    from services.media_service import _strip_corner_patch

    white_bg = _scene(bg=(255, 255, 255))  # catalog-style shot: white everywhere around the product
    assert _strip_corner_patch(white_bg).size == (1600, 900)
    plain = _scene()  # no patch at all
    assert _strip_corner_patch(plain).size == (1600, 900)



def test_lifestyle_and_catalog(app, fakes, product):
    _, client = _client(app)
    client.post("/api/presets/run", json={"preset": "lifestyle", "image_path": product})
    assert fakes["images"][-1]["aspect"] == "4:5"
    client.post("/api/presets/run", json={"preset": "catalog", "image_path": product})
    call = fakes["images"][-1]
    assert call["aspect"] == "1:1" and call["logo"] is None  # marketplaces reject logos
    assert "pure white (#FFFFFF)" in call["prompt"]


def test_festive_uses_the_occasion_the_user_names(app, fakes, product, monkeypatch):
    import services.festival_service as fs

    monkeypatch.setattr(fs, "next_celebration", lambda *a, **k: pytest.fail("should not auto-pick"))
    _, client = _client(app)
    body = client.post("/api/presets/run", json={"preset": "festive", "image_path": product,
                                                 "text": "Eid al-Adha offer, green and gold"}).get_json()
    assert body["preset"]["occasion"] == "Eid al-Adha" and not body["preset"]["occasion_auto"]
    assert "celebratory Eid al-Adha social media image" in fakes["images"][-1]["prompt"]


def test_festive_picks_the_next_celebration_in_the_brands_market(app, fakes, product, monkeypatch):
    import services.festival_service as fs

    seen = {}

    def next_celebration(regions, *a, **k):
        seen["regions"] = regions
        return {"name": "UAE National Day", "date": "2026-12-02", "region": "UAE"}

    monkeypatch.setattr(fs, "next_celebration", next_celebration)
    monkeypatch.setattr(db, "get_user_brand_profile", lambda uid: {"compliance_regions": ["UAE/GCC"]})
    _, client = _client(app)
    body = client.post("/api/presets/run", json={"preset": "festive", "image_path": product, "text": "warm colours"}).get_json()
    assert seen["regions"] == ["UAE/GCC"]
    assert body["preset"]["occasion"] == "UAE National Day" and body["preset"]["occasion_auto"]
    assert "UAE National Day decorations" in fakes["images"][-1]["prompt"]


@pytest.mark.parametrize("text, expected", [
    ("Diwali sale!", "Diwali"), ("eid al-fitr greetings", "Eid al-Fitr"), ("Happy Eid", "Eid"),
    ("mother’s day gift", "Mother's Day"), ("summer vibes", None), (None, None),
])
def test_find_occasion(text, expected):
    from services.image_presets import find_occasion

    assert find_occasion(text) == expected


def test_next_celebration_prefers_the_brands_market_and_skips_civic_days():
    from datetime import date

    from services.festival_service import next_celebration

    oct1 = date(2026, 10, 1)  # Columbus Day (US, civic) is on Oct 12
    assert next_celebration(None, today=oct1)["name"] == "Dussehra / Vijayadashami"
    assert next_celebration(["UAE/GCC"], today=oct1)["name"] == "UAE National Day"
    assert next_celebration(["US"], today=oct1)["name"] == "Thanksgiving"


# ── Admin -> Image Settings -> Image commands ──────────────────────────────


@pytest.fixture
def admin_client(app):
    admin = db.create_user("Admin", f"admin-{uuid.uuid4().hex[:8]}@example.com", "h", is_admin=True)
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = admin["id"]
    yield client
    db.save_setting("image_presets", "{}")  # other tests use the built-in commands


def test_admin_lists_commands_and_only_admins_can_change_them(app, admin_client):
    presets = admin_client.get("/api/admin/image-presets").get_json()["presets"]
    assert [p["id"] for p in presets] == ["3dbillboard", "metaad", "premiumshowcase", "lifestyle", "catalog", "festive"]
    assert all(p["enabled"] and p["customized"] == [] and p["scene"] == p["defaults"]["scene"] for p in presets)
    _, user = _client(app)
    assert user.get("/api/admin/image-presets").status_code in (401, 403)
    assert user.put("/api/admin/image-presets/catalog", json={"enabled": False}).status_code in (401, 403)


def test_turning_a_command_off_hides_and_blocks_it(app, admin_client, fakes, product):
    r = admin_client.put("/api/admin/image-presets/catalog", json={"enabled": False}).get_json()
    assert r["preset"]["enabled"] is False and r["preset"]["customized"] == ["enabled"]
    _, user = _client(app)
    assert "catalog" not in [p["id"] for p in user.get("/api/image-presets").get_json()["presets"]]
    blocked = user.post("/api/presets/run", json={"preset": "catalog", "image_path": product})
    assert blocked.status_code == 400 and blocked.get_json()["code"] == "preset_disabled"
    assert fakes["images"] == []
    admin_client.put("/api/admin/image-presets/catalog", json={"enabled": True})
    assert "catalog" in [p["id"] for p in user.get("/api/image-presets").get_json()["presets"]]


def test_edited_prompt_is_used_and_reset_restores_it(app, admin_client, fakes, product, monkeypatch):
    import services.brand_logo_service as logos

    monkeypatch.setattr(logos, "resolve_logo_path", lambda uid: "LOGO.png")
    scene = "Create a moody premium product photo on dark slate with a single warm spotlight and soft haze, editorial style."
    r = admin_client.put("/api/admin/image-presets/premiumshowcase",
                         json={"scene": scene, "label": "Luxury Shot", "stamp_logo": True}).get_json()
    assert sorted(r["preset"]["customized"]) == ["label", "scene", "stamp_logo"]
    _, user = _client(app)
    menu = {p["id"]: p for p in user.get("/api/image-presets").get_json()["presets"]}
    assert menu["premiumshowcase"]["label"] == "Luxury Shot"
    user.post("/api/presets/run", json={"preset": "premiumshowcase", "image_path": product})
    assert fakes["images"][-1]["prompt"].startswith(scene)
    assert fakes["images"][-1]["logo"] == "LOGO.png"  # stamping turned on by the admin
    reset = admin_client.post("/api/admin/image-presets/premiumshowcase/reset").get_json()["preset"]
    assert reset["customized"] == [] and reset["scene"] == reset["defaults"]["scene"]
    user.post("/api/presets/run", json={"preset": "premiumshowcase", "image_path": product})
    assert not fakes["images"][-1]["prompt"].startswith(scene)
    assert fakes["images"][-1]["logo"] is None  # built-in: no logo on showcase shots


def test_saving_the_built_in_value_stores_nothing(admin_client):
    builtin = admin_client.get("/api/admin/image-presets").get_json()["presets"][0]
    r = admin_client.put(f"/api/admin/image-presets/{builtin['id']}",
                         json={"label": builtin["label"], "scene": builtin["scene"], "enabled": True}).get_json()
    assert r["preset"]["customized"] == []


@pytest.mark.parametrize("slug, body, message", [
    ("premiumshowcase", {"scene": "too short"}, "Prompt must be 80-3000"),
    ("festive", {"scene": "Create a warm celebratory social media image with the product as the centrepiece and tasteful decorations."}, "{occasion}"),
    ("metaad", {"label": "x" * 41}, "Name must be 2-40"),
    ("metaad", {"enabled": "yes"}, "enabled must be true or false"),
    ("nope", {"enabled": False}, "Unknown command"),
])
def test_invalid_changes_are_rejected(admin_client, slug, body, message):
    r = admin_client.put(f"/api/admin/image-presets/{slug}", json=body)
    assert r.status_code == 400 and message in r.get_json()["error"]
