"""Legal pages (legal/*.md, services/legal_pages.py) and agreeing to them at sign-up."""

import uuid

import pytest

import db
from app import LEGAL_PATHS
from config import Config
from services import legal_pages


@pytest.fixture
def client():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app.test_client()


def test_every_policy_has_a_page_and_its_path():
    paths = {p["path"] for p in legal_pages.pages()}
    assert paths == set(LEGAL_PATHS)
    for page in legal_pages.pages():
        assert page["html"] and page["summary"], page["slug"]


def test_company_details_are_filled_in(monkeypatch):
    monkeypatch.setattr(Config, "LEGAL_GRIEVANCE_OFFICER", "Asha Patel")
    contact = legal_pages.page("contact")
    assert "Asha Patel" in contact["html"] and Config.LEGAL_COMPANY_NAME in contact["html"]
    assert not any("{" in p["html"] for p in legal_pages.pages())  # no placeholder left unfilled


@pytest.mark.parametrize("path", ["/legal", *LEGAL_PATHS])
def test_pages_are_public_and_indexable(client, path):
    res = client.get(path)
    assert res.status_code == 200 and b"AVIR AI" in res.data
    assert "noindex" not in res.headers.get("X-Robots-Tag", "")


def test_unknown_legal_page_is_404(client):
    assert client.get("/legal/nope").status_code == 404
    assert client.get("/legal/privacy").status_code == 200


def test_privacy_covers_google_limited_use_and_the_landing_links_it(client):
    assert "Limited Use" in legal_pages.page("privacy")["html"]
    landing = client.get("/").data
    assert b'href="/privacy"' in landing and b'href="/terms"' in landing
    assert b"/refund-policy" in client.get("/sitemap.xml").data


def test_sign_up_needs_the_terms_and_records_the_version(client, monkeypatch):
    monkeypatch.setattr(Config, "CAPTCHA_ENABLED", False)
    body = {"name": "Neha", "email": f"{uuid.uuid4().hex[:8]}@example.com", "password": "violet-harbour-42"}
    refused = client.post("/api/auth/register", json=body)
    assert refused.status_code == 400 and "Terms of Service" in refused.get_json()["error"]
    assert client.post("/api/auth/register", json={**body, "accept_terms": True}).get_json()["success"]
    user = db.get_user_by_email(body["email"])
    accepted = [e for e in db.list_audit_log(10, user.id) if e["action"] == "terms.accepted"]
    assert accepted and accepted[0]["details"]["version"] == Config.LEGAL_VERSION


def test_disconnecting_an_account_deletes_its_tokens():
    from sqlalchemy.orm import Session

    user = db.create_user("Ravi", f"{uuid.uuid4().hex[:8]}@example.com", "h")
    with Session(db.engine) as session:
        session.add(db.SocialAccount(user_id=user["id"], platform="linkedin", account_name="Ravi",
                                     access_token="secret-token", refresh_token="refresh", status="connected"))
        session.commit()
    assert db.disconnect_social_account(user["id"], "linkedin")
    with Session(db.engine) as session:
        acc = session.query(db.SocialAccount).filter_by(user_id=user["id"], platform="linkedin").one()
        assert acc.status == "disconnected" and acc.access_token is None and acc.refresh_token is None
