"""Admin -> Users: "Rescan" a user's website. Only admins can; the scan runs
in the background and the user is told (bell + email) once the brand
profile is ready. The scan and SMTP are faked."""

import threading
import uuid

import pytest

import db
from services import brand_profile_service as bps


def _email():
    return f"{uuid.uuid4().hex[:10]}@example.com"


def _user(account_type="small", website="www.sourcekode.in", is_admin=False):
    user = db.create_user("Asha Rao", _email(), "h", is_admin=is_admin)
    db.update_user_profile(user["id"], account_type=account_type, company_website=website)
    return user


def _client(user):
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


def _wait_for_scans():
    for thread in threading.enumerate():
        if thread.name.startswith("brand-scan-"):
            thread.join(timeout=10)


@pytest.fixture
def scan(monkeypatch):
    """outcome["ok"] decides whether the fake scan succeeds; emails are collected."""
    outcome, emails = {"ok": True}, []

    def fake_run(user_id, website, manual_text=None, _already_claimed=False):
        if outcome["ok"]:
            db.set_brand_scan_status(user_id, "ready")
            return True, None
        db.set_brand_scan_status(user_id, "failed", "analysis_failed")
        return False, "analysis_failed"

    class FakeMailer:
        enabled = True

        def send_brand_ready_email(self, to_email, name, company, brand_url):
            emails.append((to_email, brand_url))

    monkeypatch.setattr(bps, "run_brand_analysis", fake_run)
    monkeypatch.setattr("services.email_service.EmailService", FakeMailer)
    return outcome, emails


def test_admin_rescan_notifies_the_user_when_ready(scan):
    _, emails = scan
    user = _user()
    admin = _client(db.create_user("Admin", _email(), "h", is_admin=True))
    r = admin.post(f"/api/admin/users/{user['id']}/brand-rescan")
    assert r.status_code == 200 and r.get_json()["brand"] == {"status": "analyzing", "website": "www.sourcekode.in"}
    _wait_for_scans()
    assert db.get_brand_scan_status(user["id"])["status"] == "ready"
    [note] = db.list_notifications(user["id"])["items"]
    assert note["title"] == "Your brand profile is ready" and note["url"] == "/brand-profile"
    [(to, link)] = emails
    assert to == user["email"] and link.endswith("/brand-profile")


def test_a_failed_rescan_does_not_notify_the_user(scan):
    outcome, emails = scan
    outcome["ok"] = False
    user = _user()
    admin = _client(db.create_user("Admin", _email(), "h", is_admin=True))
    assert admin.post(f"/api/admin/users/{user['id']}/brand-rescan").status_code == 200
    _wait_for_scans()
    assert db.get_brand_scan_status(user["id"])["status"] == "failed"
    assert db.list_notifications(user["id"])["items"] == [] and emails == []


def test_only_admins_can_rescan(scan):
    user = _user()
    assert _client(user).post(f"/api/admin/users/{user['id']}/brand-rescan").status_code == 403
    assert _client(_user()).post(f"/api/admin/users/{user['id']}/brand-rescan").status_code == 403
    assert db.get_brand_scan_status(user["id"])["status"] is None


def test_rescan_needs_a_self_serve_account_with_a_website(scan):
    admin = _client(db.create_user("Admin", _email(), "h", is_admin=True))
    assert admin.post("/api/admin/users/999999/brand-rescan").status_code == 404
    enterprise = _user(account_type="enterprise")
    assert admin.post(f"/api/admin/users/{enterprise['id']}/brand-rescan").status_code == 400
    no_site = _user(website="")
    r = admin.post(f"/api/admin/users/{no_site['id']}/brand-rescan")
    assert r.status_code == 400 and "Edit profile" in r.get_json()["error"]


def test_a_second_rescan_while_one_runs_is_refused(scan):
    user = _user()
    admin = _client(db.create_user("Admin", _email(), "h", is_admin=True))
    with bps._running_scans_lock:
        bps._running_scans.add(user["id"])
    try:
        assert admin.post(f"/api/admin/users/{user['id']}/brand-rescan").status_code == 409
    finally:
        with bps._running_scans_lock:
            bps._running_scans.discard(user["id"])
