"""/brand-profile: one Save all bar for every section (static/js/brand_profile.js),
and every field it sends is really stored."""

import uuid

import pytest

import db


@pytest.fixture
def client():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    user = db.create_user("Ravi", f"{uuid.uuid4().hex[:8]}@example.com", "h")
    db.update_user_profile(user["id"], account_type="small")
    db.mark_user_email_verified(user["id"])
    db.save_user_brand_profile(user["id"], "https://acme.in", "Acme", "Retail", None, None, [], [], [], [])
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = user["id"]
    c.user_id = user["id"]
    return c


def test_every_field_the_page_sends_is_saved(client):
    body = {"company_name": "Acme Logistics", "tagline": "On time", "key_themes": ["Speed"],
            "core_products": ["Live tracking", "  ", "Same-day delivery"], "content_donts": ["No prices"]}
    assert client.put("/api/brand-profile", json=body).get_json()["success"]
    profile = db.get_user_brand_profile(client.user_id)
    assert profile["company_name"] == "Acme Logistics" and profile["key_themes"] == ["Speed"]
    assert profile["core_products"] == ["Live tracking", "Same-day delivery"]  # used to be silently dropped


def test_the_page_has_one_save_all_bar(client):
    from sqlalchemy.orm import Session

    with Session(db.engine) as session:  # a fully set-up account, so the page opens
        session.query(db.User).filter(db.User.id == client.user_id).update({"onboarding_completed": True})
        session.commit()
    html = client.get("/brand-profile").get_data(as_text=True)
    for marker in ('id="bpSaveBar"', 'id="bpSaveAll"', 'id="bpDiscard"', 'id="bpSaveBarText"'):
        assert marker in html, marker
    for old in ('id="bpSaveBtn"', 'id="bpComplianceSaveBtn"', "saveComplianceProfile()"):
        assert old not in html, old
    assert html.index("js/brand_profile.js") < html.index("js/approval_settings.js")
