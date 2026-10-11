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
    assert body["total"] == 3 and not body["has_more"]
    assert [i["url"] for i in body["images"]] == [edit, second, first]
    newest = body["images"][0]
    assert newest["kind"] == "edit" and newest["description"] == "make the background darker" and newest["version"] == 2
    assert body["images"][2]["conversation_id"] == conv and body["images"][2]["width"] == 1080


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
