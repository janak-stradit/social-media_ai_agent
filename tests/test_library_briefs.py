"""The Image Library (every image a user has made) and saved briefs (the star
under a sent message in Studio Chat)."""

import uuid

import pytest

import db


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


def _image(user, prompt, kind="image", **extra):
    name = f"lib_{uuid.uuid4().hex[:8]}.jpg"
    asset = db.log_image_generation(user["id"], 0.04, kind=kind, media_url=f"/static/uploads/{name}", prompt=prompt,
                                    width=1080, height=1080, **extra)
    return asset, f"/static/uploads/{name}"


# ── Image library ───────────────────────────────────────────────────────────


def test_the_library_lists_the_users_own_images_newest_first(app):
    user, other = _user(), _user()
    conv = db.ensure_conversation(user["id"], None, "Sneakers")
    _, first = _image(user, "Orange running sneakers on a track", conversation_id=conv)
    parent, second = _image(user, "A Diwali greeting with lamps")
    _, edit = _image(user, "A Diwali greeting with lamps", kind="edit", parent_id=parent, edit_instruction="make the background darker")
    _image(other, "Someone else's picture")
    db.log_image_generation(user["id"], 0.5, kind="video", media_url="/static/uploads/clip.mp4")  # not an image
    db.log_image_generation(user["id"], 0.04, kind="image")  # failed: nothing to show

    body = _login(app, user).get("/api/library/images").get_json()
    # an image and its edit are one entry: the newest version, with how many there are
    assert body["total"] == 2 and not body["has_more"]
    assert [i["url"] for i in body["images"]] == [edit, first]
    newest = body["images"][0]
    assert newest["kind"] == "edit" and newest["description"] == "make the background darker" and newest["version"] == 2
    assert newest["versions"] == 2 and body["images"][1]["versions"] == 1
    assert body["images"][1]["conversation_id"] == conv and body["images"][1]["width"] == 1080
    assert second not in [i["url"] for i in body["images"]]


def test_search_and_paging(app):
    user = _user()
    for i in range(5):
        _image(user, f"Blue water bottle number {i}")
    _image(user, "Red festive banner for Eid")
    client = _login(app, user)

    assert client.get("/api/library/images?q=BOTTLE").get_json()["total"] == 5
    assert client.get("/api/library/images?q=festive eid").get_json()["total"] == 1  # every word must match
    assert client.get("/api/library/images?q=bottle eid").get_json()["total"] == 0
    assert client.get("/api/library/images?q=%25").get_json()["total"] == 0  # no wildcards slip through as "match all"

    first = client.get("/api/library/images?limit=4").get_json()
    assert len(first["images"]) == 4 and first["has_more"] and first["total"] == 6
    rest = client.get("/api/library/images?limit=4&offset=4").get_json()
    assert len(rest["images"]) == 2 and not rest["has_more"]
    assert not {i["id"] for i in first["images"]} & {i["id"] for i in rest["images"]}
    assert client.get("/api/library/images?limit=abc").status_code == 400


def test_library_page_and_login(app):
    client = _login(app, db.create_user("Admin", f"{uuid.uuid4().hex[:10]}@example.com", "h", is_admin=True))
    html = client.get("/library").get_data(as_text=True)
    assert "js/library.js" in html and 'id="libGrid"' in html and 'href="/library"' in html
    assert html.index("jquery") < html.index("/static/js/approval_send.js") < html.index("/static/js/library.js")
    assert app.test_client().get("/api/library/images").status_code == 401
    assert app.test_client().get("/library").status_code in (301, 302)


def test_folders_group_images_by_campaign(app):
    user = _user()
    diwali = db.ensure_conversation(user["id"], None, "Diwali sale")
    hiring = db.ensure_conversation(user["id"], None, "Q4 hiring")
    for i in range(5):
        _image(user, f"Diwali lamp banner {i}", conversation_id=diwali)
    _image(user, "Hiring poster", conversation_id=hiring)
    _image(user, "A loose image")
    client = _login(app, user)

    folders = client.get("/api/library/folders").get_json()["folders"]
    assert [(f["title"], f["count"]) for f in folders] == [("Other images", 1), ("Q4 hiring", 1), ("Diwali sale", 5)]
    assert len(folders[2]["covers"]) == 4  # a 2x2 cover
    assert [f["title"] for f in client.get("/api/library/folders?q=lamp").get_json()["folders"]] == ["Diwali sale"]
    assert [f["title"] for f in client.get("/api/library/folders?q=hiring").get_json()["folders"]] == ["Q4 hiring"]

    assert client.get(f"/api/library/images?conversation={diwali}").get_json()["total"] == 5
    assert client.get("/api/library/images?conversation=none").get_json()["total"] == 1
    assert client.get("/api/library/images?conversation=abc").status_code == 400


def test_versions_favourites_and_delete(app):
    user, other = _user(), _user()
    original, _ = _image(user, "A sneaker on a track")
    edit, _ = _image(user, "A sneaker on a track", kind="edit", parent_id=original, edit_instruction="make it blue")
    keep, _ = _image(user, "A water bottle")
    client = _login(app, user)

    versions = client.get(f"/api/library/images/{edit}/versions").get_json()["versions"]
    assert [v["id"] for v in versions] == [original, edit]
    assert _login(app, other).get(f"/api/library/images/{edit}/versions").status_code == 404

    # starring any version stars the image, and survives a new edit
    assert client.post(f"/api/library/images/{edit}/favorite", json={"on": True}).get_json()["favorite"] is True
    _image(user, "A sneaker on a track", kind="edit", parent_id=edit, edit_instruction="add a logo")
    starred = client.get("/api/library/images?favorites=1").get_json()
    assert starred["total"] == 1 and starred["images"][0]["favorite"] and starred["images"][0]["versions"] == 3
    client.post(f"/api/library/images/{original}/favorite", json={"on": False})
    assert client.get("/api/library/images?favorites=1").get_json()["total"] == 0

    # deleting removes every version from the library, only for its owner
    assert _login(app, other).delete(f"/api/library/images/{edit}").status_code == 404
    assert client.delete(f"/api/library/images/{edit}").get_json()["removed"] == 3
    left = client.get("/api/library/images").get_json()
    assert [i["id"] for i in left["images"]] == [keep]
    assert client.get(f"/api/library/images/{original}/versions").status_code == 404

    more = [_image(user, f"Bulk {i}")[0] for i in range(2)]
    foreign, _ = _image(other, "Not yours")
    assert client.delete(f"/api/library/images?ids={more[0]},{more[1]},{foreign}").get_json()["removed"] == 2
    assert _login(app, other).get("/api/library/images").get_json()["total"] == 1
    assert client.delete("/api/library/images?ids=x").status_code == 400


def test_library_page_has_folders_and_the_tray(app):
    client = _login(app, db.create_user("Admin", f"{uuid.uuid4().hex[:10]}@example.com", "h", is_admin=True))
    html = client.get("/library").get_data(as_text=True)
    for marker in ('data-lib-view="folders"', 'data-lib-view="all"', 'id="libFavorites"', 'id="libTray"',
                   'id="libTrayItems"', 'id="libVersions"', "js/undo.js"):
        assert marker in html, marker


# ── Saved briefs ────────────────────────────────────────────────────────────


def test_a_brief_is_saved_once_and_listed_by_last_use(app):
    user = _user()
    client = _login(app, user)
    assert client.get("/api/briefs").get_json()["briefs"] == []

    r = client.post("/api/briefs", json={"text": "  Announce our Diwali offer: 20% off  ", "platforms": ["instagram", "facebook"]}).get_json()
    assert r["created"] and r["brief"]["text"] == "Announce our Diwali offer: 20% off" and r["brief"]["platforms"] == ["instagram", "facebook"]
    again = client.post("/api/briefs", json={"text": "announce our diwali offer: 20% off"}).get_json()
    assert not again["created"] and again["brief"]["id"] == r["brief"]["id"] and len(again["briefs"]) == 1

    second = client.post("/api/briefs", json={"text": "We are hiring a designer in Dubai"}).get_json()["brief"]
    assert [b["id"] for b in client.get("/api/briefs").get_json()["briefs"]] == [second["id"], r["brief"]["id"]]
    assert client.post(f"/api/briefs/{r['brief']['id']}/use").status_code == 200  # used just now: first again
    listed = client.get("/api/briefs").get_json()["briefs"]
    assert [b["id"] for b in listed] == [r["brief"]["id"], second["id"]] and listed[0]["use_count"] == 1

    left = client.delete(f"/api/briefs/{second['id']}").get_json()["briefs"]
    assert [b["id"] for b in left] == [r["brief"]["id"]]


def test_saved_briefs_are_private_and_checked(app):
    owner, other = _user(), _user()
    brief = _login(app, owner).post("/api/briefs", json={"text": "Our new warehouse opens Monday"}).get_json()["brief"]
    stranger = _login(app, other)
    assert stranger.get("/api/briefs").get_json()["briefs"] == []
    assert stranger.delete(f"/api/briefs/{brief['id']}").status_code == 404
    assert stranger.post(f"/api/briefs/{brief['id']}/use").status_code == 404
    assert _login(app, owner).post("/api/briefs", json={"text": " a "}).status_code == 400
    assert _login(app, owner).post("/api/briefs", json={}).status_code == 400
    assert app.test_client().get("/api/briefs").status_code == 401
    assert len(_login(app, owner).post("/api/briefs", json={"text": "x" * 5000}).get_json()["brief"]["text"]) == 4000


def test_there_is_a_limit_to_saved_briefs(app):
    user = _user()
    for i in range(db.MAX_SAVED_BRIEFS):
        assert db.save_brief(user["id"], f"Brief number {i}")[1]
    r = _login(app, user).post("/api/briefs", json={"text": "One too many"})
    assert r.status_code == 400 and "remove one first" in r.get_json()["error"]
    assert db.save_brief(user["id"], "brief number 3") == (db.save_brief(user["id"], "Brief number 3")[0], False)  # an existing one still answers


def test_studio_has_the_saved_briefs_button(app):
    admin = db.create_user("Admin", f"{uuid.uuid4().hex[:10]}@example.com", "h", is_admin=True)
    html = _login(app, admin).get("/dashboard").get_data(as_text=True)
    assert 'id="savedBriefsBtn"' in html and 'id="savedBriefsPanel"' in html
