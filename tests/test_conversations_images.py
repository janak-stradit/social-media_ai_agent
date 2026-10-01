"""Conversation threads and image versions (lineage) in Studio Chat.

The AI planner and the image model are faked - these tests check what the app
does around them: which conversation a message lands in, which image an edit
starts from, how versions are linked, and that nothing leaks across users.
"""

import uuid

import pytest

import db


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def routes():
    from api import routes

    assert routes.DB_AVAILABLE and routes.MEDIA_AVAILABLE
    return routes


def _user(app, name="Priya"):
    user = db.create_user(name, f"{uuid.uuid4().hex[:10]}@example.com", "hash")
    db.set_user_image_access(user["id"], db.IMAGE_UNLIMITED, None)  # many edits per test
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return user, client


@pytest.fixture
def fakes(monkeypatch, routes):
    """Fake planner + image model. plans: queue of planner replies;
    prompts: what the planner was sent; edits: what the image model got."""
    state = {"plans": [], "prompts": [], "edits": [], "events": [], "fail_next_edit": False}

    def plan(system_prompt, user_prompt, **kw):
        state["prompts"].append(user_prompt)
        return state["plans"].pop(0), {"total_tokens": 10, "cost_usd": 0.001}

    def edit_image(prompt, platform, image_path=None, logo_path=None, model=None):
        if state["fail_next_edit"]:
            state["fail_next_edit"] = False
            return {"success": False, "error": "provider down"}
        state["edits"].append({"prompt": prompt, "references": image_path})
        n = len(state["edits"])
        return {"success": True, "url": f"/static/uploads/edit{n}.jpg", "clean_url": f"/static/uploads/edit{n}_clean.jpg",
                "cost": 0.55, "model_id": "gemini-3.1-flash-image", "size": "1024x1024"}

    def generate_image(prompt, platform, tone, image_path=None, ai_model="kie", logo_path=None, square=False, model=None):
        return {"success": True, "type": "image", "url": "/static/uploads/a.jpg", "clean_url": "/static/uploads/a_clean.jpg",
                "prompt": prompt, "cost": 0.55, "model_id": "gemini-3.1-flash-image", "size": "1024x1024"}

    monkeypatch.setattr(routes.refine_llm, "generate_json", plan)
    monkeypatch.setattr(routes.media_service, "edit_image", edit_image)
    monkeypatch.setattr(routes.media_service, "generate_image", generate_image)
    monkeypatch.setattr(routes, "log_event", lambda event, **f: state["events"].append({"event": event, **f}))
    return state


def _image_plan(instruction, image_ref=None):
    return {"intent": "refine", "targets": ["image"], "image_instruction": instruction,
            "change_summary": instruction, "image_ref": image_ref}


def _start_post(client, user):
    """A conversation with one post (run) and its original image A."""
    conv_id = db.ensure_conversation(user["id"], None, "Launch our trucking app")
    run_id = db.save_run(story="Launch our trucking app", tone="Auto", platforms=["linkedin"],
                         content={"linkedin": {"caption": {"primary_caption": "Ship faster."}, "media_prompt": "white truck at sunrise"}},
                         user_id=user["id"], conversation_id=conv_id)
    res = client.post("/api/generate-media", json={
        "platform": "linkedin", "caption": "Ship faster.", "media_type": "image", "run_id": run_id,
        "image_prompt": "A white delivery truck on a highway at sunrise",
    }).get_json()
    assert res["success"] and res["asset_id"]
    return conv_id, run_id, res["asset_id"]


def _base(asset):
    return {"caption": "Ship faster.", "hashtags": [], "media_prompt": "white truck at sunrise",
            "image_url": asset["url"], "image_clean_url": asset["clean_url"], "image_asset_id": asset["id"]}


def _edit(client, fakes, conv_id, base_asset_id, user, instruction, image_ref=None, **extra):
    fakes["plans"].append(_image_plan(instruction, image_ref))
    body = {"instruction": instruction, "platforms": ["linkedin"], "conversation_id": conv_id,
            "base": {"linkedin": _base(db.get_image_asset(base_asset_id, user["id"]))}}
    body.update(extra)
    r = client.post("/api/refine", json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _new_image_id(reply):
    return reply["content"]["linkedin"]["media"]["image"]["asset_id"]


# ── Conversation threads ────────────────────────────────────────────────


def test_messages_stay_in_one_conversation_until_new(app, fakes):
    user, client = _user(app)
    conv_id, _, a = _start_post(client, user)

    r1 = _edit(client, fakes, conv_id, a, user, "make the truck red")
    r2 = _edit(client, fakes, conv_id, _new_image_id(r1), user, "add a sunset")
    assert r1["conversation_id"] == r2["conversation_id"] == conv_id

    listed = client.get("/api/conversations").get_json()["conversations"]
    assert [c["id"] for c in listed] == [conv_id]
    assert listed[0]["message_count"] == 3

    thread = client.get(f"/api/conversations/{conv_id}").get_json()
    assert [r["story"] for r in thread["runs"]] == ["Launch our trucking app", "make the truck red", "add a sunset"]

    # "New Conversation": the client sends no conversation id -> a new one
    fakes["plans"].append({"intent": "refine", "targets": ["caption"], "captions": {"linkedin": "New post"}, "change_summary": "x"})
    fresh = client.post("/api/refine", json={"instruction": "shorter", "platforms": ["linkedin"],
                                             "base": {"linkedin": {"caption": "Old"}}}).get_json()
    assert fresh["conversation_id"] not in (None, conv_id)


def test_regenerate_is_saved_as_a_version_of_the_reply(app, fakes):
    user, client = _user(app)
    conv_id, _, a = _start_post(client, user)
    first = _edit(client, fakes, conv_id, a, user, "make the truck red")
    again = _edit(client, fakes, conv_id, a, user, "make the truck red", version_of_run_id=first["run_id"])
    run = db.get_run_by_id(again["run_id"], user["id"])
    assert run["content"]["_meta"]["version_of"] == first["run_id"]
    assert run["conversation_id"] == conv_id


def test_archive_and_restore_conversation(app, fakes):
    user, client = _user(app)
    conv_id, _, _ = _start_post(client, user)
    assert client.post(f"/api/conversations/{conv_id}/archive").status_code == 200
    assert client.get("/api/conversations").get_json()["conversations"] == []
    assert [c["id"] for c in client.get("/api/conversations?archived=true").get_json()["conversations"]] == [conv_id]
    assert client.post(f"/api/conversations/{conv_id}/unarchive").status_code == 200


def test_runs_saved_before_conversations_each_get_one(app):
    user, _ = _user(app)
    run_id = db.save_run(story="Old post", tone="Auto", platforms=["linkedin"], content={}, user_id=user["id"])
    assert db.get_run_by_id(run_id)["conversation_id"] is None
    db._backfill_conversations()
    conv_id = db.get_run_by_id(run_id)["conversation_id"]
    assert conv_id and db.get_conversation(conv_id, user["id"])["conversation"]["title"] == "Old post"


# ── Image versions ──────────────────────────────────────────────────────


def test_3_image_generation_creates_original(app, fakes):
    user, client = _user(app)
    conv_id, run_id, a = _start_post(client, user)
    asset = db.get_image_asset(a, user["id"])
    assert asset["parent_id"] is None and asset["root_id"] == a and asset["version"] == 1
    assert asset["conversation_id"] == conv_id and asset["run_id"] == run_id
    assert db.get_active_image_id(conv_id, user["id"]) == a
    # the post itself references the image, so a later edit knows where to start
    assert db.get_run_by_id(run_id)["content"]["linkedin"]["media"]["image"]["asset_id"] == a


def test_4_refinement_creates_child_of_the_image(app, fakes):
    user, client = _user(app)
    conv_id, _, a = _start_post(client, user)
    reply = _edit(client, fakes, conv_id, a, user, "make the background blue")
    b = db.get_image_asset(_new_image_id(reply), user["id"])
    assert b["parent_id"] == a and b["root_id"] == a and b["version"] == 2
    assert b["edit_instruction"] == "make the background blue"
    assert fakes["edits"][-1]["references"] == ["/static/uploads/a_clean.jpg"]  # the unbranded copy of A
    assert db.get_active_image_id(conv_id, user["id"]) == b["id"]
    assert b["run_id"] == reply["run_id"]  # linked to its run, so it isn't charged twice


def test_5_multiple_refinements_keep_lineage_and_history(app, fakes):
    user, client = _user(app)
    conv_id, _, a = _start_post(client, user)
    b = _new_image_id(_edit(client, fakes, conv_id, a, user, "make the background darker"))
    c = _new_image_id(_edit(client, fakes, conv_id, b, user, "change the jacket to blue"))
    d = _new_image_id(_edit(client, fakes, conv_id, c, user, "add a sunset"))

    chain = client.get(f"/api/images/{d}/lineage").get_json()["lineage"]
    assert [x["id"] for x in chain] == [a, b, c, d]
    assert [x["version"] for x in chain] == [1, 2, 3, 4]
    assert {x["root_id"] for x in chain} == {a}

    # the image model is told the original intent and the edits already made
    prompt = fakes["edits"][-1]["prompt"]
    assert "Original intent of this image: A white delivery truck" in prompt
    assert "make the background darker; change the jacket to blue" in prompt
    assert "New change: add a sunset" in prompt


def test_6_editing_an_older_version_branches(app, fakes):
    user, client = _user(app)
    conv_id, _, a = _start_post(client, user)
    b = _new_image_id(_edit(client, fakes, conv_id, a, user, "make it red"))
    c = _new_image_id(_edit(client, fakes, conv_id, a, user, "change the background to a forest"))
    assert db.get_image_asset(c, user["id"])["parent_id"] == a  # A -> C, not B -> C
    assert db.get_image_asset(b, user["id"])["parent_id"] == a  # B untouched
    assert [x["id"] for x in db.image_lineage(c, user["id"])] == [a, c]
    assert "make it red" not in fakes["edits"][-1]["prompt"]  # B's edit isn't carried into the branch


def test_7_it_means_the_active_image(app, fakes):
    user, client = _user(app)
    conv_id, _, a = _start_post(client, user)
    b = _new_image_id(_edit(client, fakes, conv_id, a, user, "make it red"))
    c = _new_image_id(_edit(client, fakes, conv_id, b, user, "add a logo strip"))
    d = _new_image_id(_edit(client, fakes, conv_id, c, user, "make it brighter"))
    assert db.get_image_asset(d, user["id"])["parent_id"] == c
    assert "#3 [linkedin] edit of #2" in fakes["prompts"][-1] and "<- ACTIVE" in fakes["prompts"][-1]


def test_8_explicitly_selected_or_named_image_is_used(app, fakes):
    user, client = _user(app)
    conv_id, _, a = _start_post(client, user)
    b = _new_image_id(_edit(client, fakes, conv_id, a, user, "make it red"))
    c = _new_image_id(_edit(client, fakes, conv_id, b, user, "add a sunset"))

    # the user picks A ("Refine this version"), then "make this image darker"
    assert client.post(f"/api/conversations/{conv_id}/active-image", json={"image_id": a}).status_code == 200
    assert db.get_active_image_id(conv_id, user["id"]) == a
    picked = _new_image_id(_edit(client, fakes, conv_id, a, user, "make this image darker"))
    assert db.get_image_asset(picked, user["id"])["parent_id"] == a

    # while refining C, the user says "go back to the first image" -> the planner names #1
    named = _new_image_id(_edit(client, fakes, conv_id, c, user, "use the first image, but brighter", image_ref=1))
    assert db.get_image_asset(named, user["id"])["parent_id"] == a


def test_9_caption_request_sees_the_active_image(app, fakes):
    user, client = _user(app)
    conv_id, _, a = _start_post(client, user)
    b = _new_image_id(_edit(client, fakes, conv_id, a, user, "make it more corporate"))
    fakes["plans"].append({"intent": "refine", "targets": ["caption"], "captions": {"linkedin": "Corporate caption"},
                           "change_summary": "caption"})
    client.post("/api/refine", json={"instruction": "write the LinkedIn caption for this image", "platforms": ["linkedin"],
                                     "conversation_id": conv_id, "base": {"linkedin": _base(db.get_image_asset(b, user["id"]))}})
    sent = fakes["prompts"][-1]
    assert "Image: exists" in sent
    assert 'edit of #1: "make it more corporate"  <- ACTIVE' in sent


def test_failed_edit_creates_no_version(app, fakes):
    user, client = _user(app)
    conv_id, _, a = _start_post(client, user)
    fakes["fail_next_edit"] = True
    reply = _edit(client, fakes, conv_id, a, user, "make it red")
    assert reply["media_errors"]
    assert [x["id"] for x in db.conversation_images(conv_id, user["id"])] == [a]
    assert db.get_active_image_id(conv_id, user["id"]) == a
    assert any(e["event"] == "image.edit" and e.get("status") == "failed" for e in fakes["events"])


# ── Privacy ─────────────────────────────────────────────────────────────


def test_no_leaks_between_users(app, fakes):
    owner, owner_client = _user(app, "Owner")
    conv_id, _, a = _start_post(owner_client, owner)
    other, other_client = _user(app, "Other")

    assert other_client.get(f"/api/conversations/{conv_id}").status_code == 404
    assert other_client.post(f"/api/conversations/{conv_id}/active-image", json={"image_id": a}).status_code == 404
    assert other_client.get(f"/api/images/{a}/lineage").status_code == 404
    assert db.get_image_asset(a, other["id"]) is None

    # posting into someone else's conversation id starts the sender's own conversation,
    # and their versions list doesn't include the owner's images
    fakes["plans"].append(_image_plan("make it red"))
    reply = other_client.post("/api/refine", json={
        "instruction": "make it red", "platforms": ["linkedin"], "conversation_id": conv_id,
        "base": {"linkedin": {"caption": "x", "image_url": "/static/uploads/mine.jpg", "image_asset_id": a}},
    }).get_json()
    assert reply["conversation_id"] != conv_id
    assert "IMAGE VERSIONS" not in fakes["prompts"][-1]
    new = db.get_image_asset(_new_image_id(reply), other["id"])
    assert new["parent_id"] is None  # the owner's image A was not used as the parent
    assert fakes["edits"][-1]["references"] == ["/static/uploads/mine.jpg"]


# ── Structured logs ─────────────────────────────────────────────────────


def test_edit_and_refine_are_logged_with_ids(app, fakes):
    user, client = _user(app)
    conv_id, _, a = _start_post(client, user)
    reply = _edit(client, fakes, conv_id, a, user, "make it red")
    edit = next(e for e in fakes["events"] if e["event"] == "image.edit")
    assert edit["conversation_id"] == conv_id and edit["parent_asset_id"] == a
    assert edit["asset_id"] == _new_image_id(reply) and edit["status"] == "completed"
    assert edit["context_token_estimate"] > 0
    refine = next(e for e in fakes["events"] if e["event"] == "refine")
    assert refine["run_id"] == reply["run_id"] and refine["latency_ms"] >= 0
