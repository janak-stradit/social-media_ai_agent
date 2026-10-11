"""Editing a generated post by hand (Studio Chat "Edit"): the caption and
hashtags are changed in place - no generation - and the AI's text can be
brought back. Also the shared preview cards the editor's limits live in."""

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

import db
from services import post_edit_service as edits
from services.post_edit_service import EditError, apply_edit

ROOT = Path(__file__).resolve().parent.parent


def _content():
    return {
        "linkedin": {"caption": {"primary_caption": "We launched live tracking."},
                     "hashtags": {"hashtags": ["#Logistics", "#Tracking"]},
                     "compliance": {"rules_checked": 4, "flags": [], "needs_attention": 0},
                     "media": {"image": {"url": "/static/uploads/a.jpg"}}},
        "instagram": {"caption": {"primary_caption": "Track every parcel."}, "hashtags": ["#Parcel"]},
        "_agents": [],
    }


# ── The edit itself ─────────────────────────────────────────────────────────


def test_an_edit_keeps_the_original_and_marks_the_post():
    content = _content()
    node = apply_edit(content, "linkedin", caption="  We launched live tracking today.\r\nTry it.  ", hashtags="logistics, #Tracking #NEW")
    assert node["caption"]["primary_caption"] == "We launched live tracking today.\nTry it."
    assert node["hashtags"]["hashtags"] == ["#logistics", "#Tracking", "#NEW"]
    assert node["original"] == {"caption": "We launched live tracking.", "hashtags": ["#Logistics", "#Tracking"]}
    assert node["edited"]["at"] and node["compliance"]["stale"] is True
    assert node["media"]["image"]["url"] == "/static/uploads/a.jpg"  # nothing else is touched
    assert content["linkedin"] is node

    apply_edit(content, "linkedin", caption="Second edit.")  # the original stays the AI's text
    assert node["original"]["caption"] == "We launched live tracking." and node["hashtags"]["hashtags"][2] == "#NEW"


def test_restore_brings_the_ai_text_back():
    content = _content()
    apply_edit(content, "linkedin", caption="Changed.", hashtags=[])
    node = apply_edit(content, "linkedin", restore=True)
    assert node["caption"]["primary_caption"] == "We launched live tracking."
    assert node["hashtags"]["hashtags"] == ["#Logistics", "#Tracking"]
    assert "original" not in node and "edited" not in node and "stale" not in node["compliance"]
    with pytest.raises(EditError, match="not been edited"):
        apply_edit(content, "linkedin", restore=True)


def test_typing_the_original_back_counts_as_restored():
    content = _content()
    apply_edit(content, "linkedin", caption="Changed.")
    node = apply_edit(content, "linkedin", caption="We launched live tracking.")
    assert "edited" not in node and "original" not in node and "stale" not in node["compliance"]


def test_changing_only_hashtags_leaves_the_compliance_notes_valid():
    content = _content()
    node = apply_edit(content, "linkedin", hashtags="#One")
    assert node["edited"] and "stale" not in node["compliance"] and node["caption"]["primary_caption"] == "We launched live tracking."
    same = apply_edit(_content(), "linkedin", caption="We launched live tracking.")  # no change: not an edit
    assert "edited" not in same


def test_hashtags_saved_as_a_plain_list_are_handled():
    content = _content()
    node = apply_edit(content, "instagram", hashtags="#Parcel #Fast")
    assert node["hashtags"] == {"hashtags": ["#Parcel", "#Fast"]} and node["original"]["hashtags"] == ["#Parcel"]
    assert apply_edit(content, "instagram", restore=True)["hashtags"] == {"hashtags": ["#Parcel"]}


def test_hashtags_are_cleaned():
    assert edits.clean_hashtags("  sale,#Sale  ##big\n#two words") == ["#sale", "#big", "#two", "#words"]
    assert edits.clean_hashtags(["a b", "#c", "", None, "#C"]) == ["#ab", "#c"]
    assert edits.clean_hashtags("") == [] and edits.clean_hashtags(None) == []


@pytest.mark.parametrize("platform, limit", [("instagram", 2200), ("linkedin", 3000), ("facebook", 3000), ("carousel", 2200)])
def test_each_platform_has_its_limit(platform, limit):
    assert edits.limit_for(platform) == limit


def test_limits_count_caption_and_hashtags_together():
    content = _content()
    fits = "x" * (2200 - len("\n\n#Parcel"))
    assert apply_edit(content, "instagram", caption=fits)["caption"]["primary_caption"] == fits
    with pytest.raises(EditError, match="1 characters over the limit of 2,200"):
        apply_edit(content, "instagram", caption=fits + "x")
    assert apply_edit(content, "linkedin", caption="x" * 2900)  # LinkedIn allows more
    with pytest.raises(EditError, match="at most 30 hashtags"):
        apply_edit(content, "linkedin", hashtags=" ".join(f"#t{i}" for i in range(31)))
    with pytest.raises(EditError, match="can't be empty"):
        apply_edit(content, "linkedin", caption="   ")


@pytest.mark.parametrize("target", ["youtube", "_agents", "_meta", "", "carousel"])
def test_only_a_real_caption_can_be_edited(target):
    with pytest.raises(EditError):
        apply_edit(_content(), target, caption="Hello")


def test_a_carousel_caption_is_edited_too():
    content = {"_preset": {"id": "carousel", "carousel": {"caption": "Five tips.", "hashtags": ["#Tips"], "slides": [{"key": "slide1"}]}},
               "slide1": {"caption": {"primary_caption": ""}, "media": {"image": {"url": "/static/uploads/s1.jpg"}}}}
    node = apply_edit(content, "carousel", caption="Five quick tips.", hashtags="#Tips #Carousel")
    assert node is content["_preset"]["carousel"]
    assert node["caption"] == "Five quick tips." and node["hashtags"] == ["#Tips", "#Carousel"] and node["slides"]
    assert node["original"] == {"caption": "Five tips.", "hashtags": ["#Tips"]}
    assert apply_edit(content, "carousel", restore=True)["caption"] == "Five tips."


# ── The endpoint ────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _user():
    return db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "h")


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def _run(user):
    return db.save_run(story="Announce tracking", tone="Auto", platforms=["linkedin", "instagram"], content=_content(),
                       user_id=user["id"], tokens_used=900, cost_usd=0.01)


def test_the_owner_edits_and_restores_a_post(app):
    user = _user()
    run_id = _run(user)
    client = _login(app, user)

    r = client.post(f"/api/history/{run_id}/edit", json={"target": "LinkedIn", "caption": "Edited by hand.", "hashtags": "#One #Two"})
    body = r.get_json()
    assert r.status_code == 200 and body["target"] == "linkedin" and body["node"]["edited"]
    saved = db.get_run_by_id(run_id)
    assert saved["content"]["linkedin"]["caption"]["primary_caption"] == "Edited by hand."
    assert saved["content"]["linkedin"]["hashtags"]["hashtags"] == ["#One", "#Two"]
    assert saved["content"]["instagram"]["caption"]["primary_caption"] == "Track every parcel."  # the other platform is untouched
    assert saved["tokens_used"] == 900 and saved["cost_usd"] == 0.01  # an edit costs nothing
    with Session(db.engine) as session:  # and makes no new run
        assert session.query(db.RunHistory).filter(db.RunHistory.user_id == user["id"]).count() == 1

    r = client.post(f"/api/history/{run_id}/edit", json={"target": "linkedin", "restore": True})
    assert r.get_json()["node"]["caption"]["primary_caption"] == "We launched live tracking."
    assert "edited" not in db.get_run_by_id(run_id)["content"]["linkedin"]


def test_bad_edits_are_refused_with_a_reason(app):
    user = _user()
    run_id = _run(user)
    client = _login(app, user)
    over = client.post(f"/api/history/{run_id}/edit", json={"target": "instagram", "caption": "x" * 2300})
    assert over.status_code == 400 and "over the limit" in over.get_json()["error"]
    assert client.post(f"/api/history/{run_id}/edit", json={"target": "tiktok", "caption": "Hi"}).status_code == 400
    assert client.post(f"/api/history/{run_id}/edit", json={}).status_code == 400
    assert db.get_run_by_id(run_id)["content"]["instagram"]["caption"]["primary_caption"] == "Track every parcel."


def test_nobody_else_can_edit_a_post(app):
    run_id = _run(_user())
    assert _login(app, _user()).post(f"/api/history/{run_id}/edit", json={"target": "linkedin", "caption": "Hacked"}).status_code == 404
    assert app.test_client().post(f"/api/history/{run_id}/edit", json={"target": "linkedin", "caption": "Hacked"}).status_code == 401
    assert _login(app, _user()).post("/api/history/99999999/edit", json={"target": "linkedin", "caption": "x"}).status_code == 404
    assert db.get_run_by_id(run_id)["content"]["linkedin"]["caption"]["primary_caption"] == "We launched live tracking."


# ── The browser side stays in step ──────────────────────────────────────────


def test_the_live_counter_uses_the_same_limits():
    js = (ROOT / "static/js/post_preview.js").read_text(encoding="utf-8")
    limits = dict(re.findall(r"(\w+): (\d+)", re.search(r"const LIMITS = \{([^}]*)\}", js).group(1)))
    assert {k: int(v) for k, v in limits.items()} == edits.POST_LIMITS
    assert int(re.search(r"const DEFAULT_LIMIT = (\d+)", js).group(1)) == edits.DEFAULT_LIMIT
    assert int(re.search(r"const MAX_HASHTAGS = (\d+)", js).group(1)) == edits.MAX_HASHTAGS


def test_preview_cards_are_loaded_before_the_pages_that_use_them(app):
    admin = db.create_user("Admin", f"{uuid.uuid4().hex[:10]}@example.com", "h", is_admin=True)
    client = _login(app, admin)
    studio = client.get("/dashboard").get_data(as_text=True)
    assert studio.index("js/post_preview.js") < studio.index("js/app_v2.js")
    req = db.create_approval_request(user_id=admin["id"], pipeline_client_id="studio-77", platform="linkedin", asset_type="text")
    review = client.get(f"/approve/{req['id']}").get_data(as_text=True)
    assert review.index("js/post_preview.js") < review.index("js/approval_review.js")
    # the review page no longer carries its own copy of the cards
    assert "PostPreview.cardHtml" in (ROOT / "static/js/approval_review.js").read_text(encoding="utf-8")
