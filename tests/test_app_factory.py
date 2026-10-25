"""Smoke tests: the Flask app boots and the page routes enforce login."""

import pytest


@pytest.fixture
def client():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def test_health_check(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "healthy"


def test_landing_page_is_public(client):
    assert client.get("/").status_code == 200


@pytest.mark.parametrize("path", ["/dashboard", "/settings", "/competitor-dashboard", "/calendar", "/admin"])
def test_protected_pages_redirect_when_logged_out(client, path):
    # signed-out visitors are sent to the landing page (which links to log in)
    resp = client.get(path, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] in ("/", "http://localhost/")


def test_login_page_is_public(client):
    resp = client.get("/login")
    assert resp.status_code == 200


@pytest.mark.parametrize("path", ["/api/history", "/api/opportunity-suggestions"])
def test_protected_get_api_routes_require_auth(client, path):
    resp = client.get(path)
    assert resp.status_code == 401


@pytest.mark.parametrize("path", ["/api/approve-asset", "/api/publish-pipeline-asset"])
def test_protected_post_api_routes_require_auth(client, path):
    resp = client.post(path, json={})
    assert resp.status_code == 401
