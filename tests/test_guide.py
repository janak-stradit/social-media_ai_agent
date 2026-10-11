"""In-app guide: the Help Center (Markdown articles in help/), the "Get
started" checklist that ticks itself off, and remembered tours."""

import json
import re
import uuid

import pytest
from sqlalchemy.orm import Session

import db
from services import guide_service, help_center

# ── Article rendering ───────────────────────────────────────────────────────


def test_markdown_subset_renders():
    html, toc = help_center.render(
        "Intro with **bold**, *italic*, `code` and a [link](/dashboard).\n\n"
        "## First steps\n\n1. One\n2. Two\n\n- a bullet\n- another\n\n### Details\n\n"
        "> Tip: keep it short.\n\n> Note: read this.\n\n| Name | Size |\n|---|---|\n| `/metaad` | 1:1 |\n"
    )
    assert "<strong>bold</strong>" in html and "<em>italic</em>" in html and "<code>code</code>" in html
    assert '<a href="/dashboard">link</a>' in html
    assert '<h3 id="first-steps">First steps</h3>' in html and '<h4 id="details">Details</h4>' in html
    assert "<ol><li>One</li><li>Two</li></ol>" in html and "<ul><li>a bullet</li><li>another</li></ul>" in html
    assert 'class="help-callout help-tip"' in html and 'class="help-callout help-note"' in html
    assert "<th>Name</th>" in html and "<td><code>/metaad</code></td>" in html
    assert toc == [{"id": "first-steps", "title": "First steps", "level": 2}, {"id": "details", "title": "Details", "level": 3}]


def test_articles_cannot_inject_html_or_script_links():
    html, _ = help_center.render('<script>alert(1)</script> and <img src=x onerror=1>\n\n[bad](javascript:alert(1)) [ok](https://stradit.com/)')
    assert "<script" not in html and "<img" not in html and "&lt;script&gt;" in html
    assert "javascript:" not in html and "bad" in html
    assert '<a href="https://stradit.com/" target="_blank" rel="noopener">ok</a>' in html


def test_every_guide_loads_and_links_resolve():
    articles = help_center.articles()
    slugs = [a["slug"] for a in articles]
    assert slugs[0] == "getting-started" and {"studio-chat", "image-commands", "approvals", "social-accounts"} <= set(slugs)
    for article in articles:
        assert article["title"] and article["summary"] and article["html"] and article["icon"].startswith("fa-")
        for target in re.findall(r'href="/help/([^"#]*)', article["html"]):
            assert target in slugs, f"{article['slug']} links to missing guide {target}"
    # every checklist step's "Learn more" points at a real guide
    assert {step[5] for step in guide_service.STEPS} <= set(slugs)


@pytest.mark.parametrize("slug", ["../config", "getting-started/../../app", "Getting-Started", "", "nope"])
def test_only_real_slugs_are_served(slug):
    assert help_center.article(slug) is None


def test_search_index_covers_headings_and_text():
    index = {a["slug"]: a for a in help_center.search_index()}
    assert "Carousels" in index["image-commands"]["headings"]
    assert "/carousel" in index["image-commands"]["text"] and "html" not in index["image-commands"]


# ── Pages ───────────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _user(is_admin=False):
    return db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "h", is_admin=is_admin)


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def test_help_pages(app):
    client = _login(app, _user(is_admin=True))
    home = client.get("/help").get_data(as_text=True)
    assert "How can we help?" in home and "/help/approvals" in home and "Take the Studio Chat tour" in home
    page = client.get("/help/image-commands")
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and "<code>/carousel</code>" in text and "On this page" in text
    assert client.get("/help/does-not-exist").status_code == 404
    assert app.test_client().get("/help").status_code in (301, 302)  # signed out: to the landing page


def test_header_has_the_help_button_and_tour_link(app):
    html = _login(app, _user(is_admin=True)).get("/dashboard").get_data(as_text=True)
    assert 'id="helpCenterBtn"' in html and "/dashboard?tour=studio" in html
    assert "js/guide.js" in html and "driver.js" in html and 'id="tourPlatforms"' in html


# ── Checklist ───────────────────────────────────────────────────────────────


def _done(user_id):
    return {s["key"]: s["done"] for s in guide_service.checklist(user_id)["steps"]}


def test_a_new_user_has_everything_still_to_do():
    user = _user()
    status = guide_service.checklist(user["id"])
    assert status["done"] == 0 and status["total"] == 7 and not status["complete"] and not status["dismissed"]
    assert [s["key"] for s in status["steps"]][:2] == ["brand_profile", "post"]
    assert status["steps"][1]["action"] == "action:composer" and status["steps"][0]["help"] == "/help/brand-profile"


def test_steps_tick_themselves_off_from_real_activity():
    user = _user()
    uid = user["id"]
    with Session(db.engine) as session:
        session.add(db.UserBrandProfile(user_id=uid, website="https://acme.test", company_name="Acme"))
        session.add(db.RunHistory(user_id=uid, story="Announce tracking", platforms="[]", content="{}"))
        session.commit()
    assert _done(uid) == {"brand_profile": True, "post": True, "image": False, "idea": False, "approval": False,
                          "social": False, "published": False}

    db.log_image_generation(uid, 0.55, kind="image", media_url="/static/uploads/a.jpg")
    guide_service.update_state(uid, event="idea_used")
    guide_service.update_state(uid, event="not_a_real_event")
    db.create_approval_request(user_id=uid, pipeline_client_id="studio-1", platform="multi", asset_type="image", items=[
        {"platform": "instagram", "caption": "x", "images": [], "media": "none", "decision": "approved"}])
    db.save_social_account(user_id=uid, platform="instagram", account_name="@acme", account_id="IG1", access_token="tok")
    done = _done(uid)
    assert done["image"] and done["idea"] and done["approval"] and done["social"] and not done["published"]

    request_id = db.list_approval_requests(user_id=uid)[0]["id"]
    db.update_approval_item(request_id, 0, {"published": {"at": "2026-10-10T10:00:00+00:00", "permalink": "https://instagram.com/p/x"}})
    status = guide_service.checklist(uid)
    assert status["complete"] and status["done"] == 7


def test_an_idea_opened_from_an_email_counts_too():
    user = _user()
    token = db.create_idea_link(user["id"], {"title": "Diwali post", "prompt": "Create a Diwali post."})
    assert not _done(user["id"])["idea"]
    db.open_idea_link(token, user["id"])
    assert _done(user["id"])["idea"]


def test_tours_and_dismissal_are_remembered(app):
    user = _user()
    client = _login(app, user)
    assert client.get("/api/guide").get_json()["tours_seen"] == []

    body = client.post("/api/guide/state", json={"tour_seen": "studio"}).get_json()
    assert body["tours_seen"] == ["studio"]
    client.post("/api/guide/state", json={"tour_seen": "studio"})  # twice: still once
    client.post("/api/guide/state", json={"tour_seen": "made-up-tour"})
    assert client.get("/api/guide").get_json()["tours_seen"] == ["studio"]

    assert client.post("/api/guide/state", json={"dismiss_checklist": True}).get_json()["dismissed"] is True
    assert client.post("/api/guide/state", json={"reset_tours": True, "dismiss_checklist": False}).get_json()["tours_seen"] == []
    assert not client.get("/api/guide").get_json()["dismissed"]
    with Session(db.engine) as session:  # stored on the account, as JSON
        assert json.loads(session.get(db.User, user["id"]).guide_state)["tours"] == []


@pytest.mark.parametrize("tour", ["studio", "brand", "approvals", "review", "settings"])
def test_every_page_tour_can_be_remembered(tour):
    user = _user()
    assert guide_service.update_state(user["id"], tour_seen=tour)["tours"] == [tour]


def _self_serve_user():
    """A signed-up small-business user (admins are sent to Brand Configuration instead of /brand-profile)."""
    user = _user()
    with Session(db.engine) as session:
        row = session.get(db.User, user["id"])
        row.email_verified, row.onboarding_completed, row.account_type = True, True, "small"
        session.commit()
    return user


@pytest.mark.parametrize("url", ["/dashboard", "/brand-profile", "/settings", "/approve"])
def test_guided_pages_load_the_guide(app, url):
    html = _login(app, _self_serve_user()).get(url).get_data(as_text=True)
    assert "js/guide.js" in html and "driver.js" in html and 'id="helpCenterBtn"' in html
    assert html.index("jquery") < html.index("js/guide.js")  # guide.js needs jQuery


def test_the_review_page_loads_the_guide_too(app):
    admin = _user(is_admin=True)
    req = db.create_approval_request(user_id=admin["id"], pipeline_client_id="studio-9", platform="linkedin", asset_type="text")
    html = _login(app, admin).get(f"/approve/{req['id']}").get_data(as_text=True)
    assert "js/guide.js" in html


def test_guide_api_needs_a_login(app):
    assert app.test_client().get("/api/guide").status_code == 401
