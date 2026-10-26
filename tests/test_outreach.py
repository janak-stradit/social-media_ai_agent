"""Invitation outreach: unsubscribes, click tracking, plain-note emails, the
funnel, and lead previews (made before sign-up, copied into the account at
sign-up). SMTP, the website scan, the AI and image models are all faked."""

import email as email_lib
import secrets
import threading
import uuid

import pytest

import db
from services import invite_outreach as outreach
from services import lead_preview_service as lps

BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
SCANNER = "Mozilla/5.0 (compatible; Microsoft Office SafeLinks scanner)"

PROFILE = {
    "company_name": "Synkrama Technologies", "industry": "IT services", "target_audience": "Startups",
    "brand_voice_summary": "Friendly and clear", "key_themes": ["apps"], "core_products": ["Mobile apps"],
    "primary_colors": ["#1A73E8", "bad;color"], "content_dos": [], "content_donts": [], "suggested_post_ideas": [],
    "logo_url": "https://synkrama.example/logo.png", "website_signals": {"regions_detected": ["India"]},
}
POSTS = {"first_line": "Saw that Synkrama builds custom mobile apps for startups across Pune.",
         "posts": [{"title": "Our apps", "caption": "We build apps.", "hashtags": ["apps", "#Pune"], "image_prompt": "A team"},
                   {"title": "Tips", "caption": "Three tips.", "hashtags": [], "image_prompt": "A laptop"},
                   {"title": "Diwali", "caption": "Happy Diwali.", "hashtags": ["#Diwali"], "image_prompt": "Lamps"}]}


def _email():
    return f"{uuid.uuid4().hex[:10]}@synkrama.example"


def _client(user=None, app=None):
    from app import create_app

    app = app or create_app("development")
    app.config.update(TESTING=True)
    client = app.test_client()
    if user:
        with client.session_transaction() as s:
            s["user_id"] = user["id"]
    return client


@pytest.fixture
def admin():
    return db.create_user("Admin", f"{uuid.uuid4().hex[:8]}@avir.test", "h", is_admin=True)


def _contact(admin, **fields):
    email = fields.pop("email", None) or _email()
    db.save_invite_contacts([{"email": email, "company": "Synkrama Technologies", "website": "www.synkrama.example", **fields}],
                            None, admin["id"], source="manual")
    return next(c for c in db.list_invite_contacts()["contacts"] if c["email"] == email)


@pytest.fixture
def fake_ai(monkeypatch):
    """The website scan + analysis + post writing, faked. scan["ok"] decides whether the site can be read."""
    scan = {"ok": True, "calls": 0}

    def scrape(url):
        scan["calls"] += 1
        return ({"title": "Synkrama", "text_excerpt": "We build apps"}, None) if scan["ok"] else (None, "blocked")

    class FakeLLM:
        def generate_json(self, *args, **kwargs):
            return POSTS, {"total_tokens": 900, "cost_usd": 0.002}

    monkeypatch.setattr("services.website_scraper_service.scrape_website", scrape)
    monkeypatch.setattr("agents.website_analysis_agent.WebsiteAnalysisAgent.analyze", lambda self, scraped: dict(PROFILE))
    monkeypatch.setattr("services.llm_service.LLMService", FakeLLM)
    return scan


@pytest.fixture
def fake_images(monkeypatch):
    """Image making runs at once (no thread) and returns a fake image."""
    made = []

    class FakeMedia:
        def create_preset_image(self, prompt, image_path, aspect, logo_path=None, model=None, require_reference=True):
            made.append({"prompt": prompt, "logo": logo_path})
            return {"success": True, "url": f"/static/uploads/lead_{len(made)}.png", "clean_url": None, "cost": 0.04,
                    "model": "fake", "prompt": prompt, "width": 1024, "height": 1024}

    class NowThread:
        def __init__(self, target, args=(), **_kwargs):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr("services.media_service.MediaGenerationService", FakeMedia)
    monkeypatch.setattr("services.brand_logo_service._cached_logo", lambda url: "/tmp/logo.png")
    monkeypatch.setattr(lps.threading, "Thread", NowThread)
    return made


@pytest.fixture
def smtp(monkeypatch):
    """Captures the emails EmailService would send."""
    from services import email_service
    from services.email_service import EmailService

    sent = []
    for key, value in (("SMTP_HOST", "smtp.test"), ("SMTP_USERNAME", "u"), ("SMTP_PASSWORD", "p"),
                       ("SMTP_FROM_EMAIL", "invites@avir.test")):
        monkeypatch.setattr(email_service.Config, key, value)
    monkeypatch.setattr(EmailService, "_deliver", lambda self, msg, to, kind: sent.append(email_lib.message_from_string(msg.as_string())))
    monkeypatch.setattr(outreach, "SCANNER_WINDOW_SECONDS", 0)
    return sent


def _parts(msg):
    return {part.get_content_type(): part.get_payload(decode=True).decode() for part in msg.walk() if not part.is_multipart()}


# ── Unsubscribe ─────────────────────────────────────────────────────────────


def test_unsubscribe_link_and_one_click():
    email = _email()
    token = outreach.unsubscribe_token(email)
    assert outreach.email_from_unsubscribe_token(token) == email
    assert outreach.email_from_unsubscribe_token(token + "x") is None
    client = _client()
    page = client.get(f"/u/{token}").get_data(as_text=True)
    assert "Unsubscribe from AVIR AI emails?" in page and email in page
    assert not db.suppressed_emails([email])  # looking is not unsubscribing
    assert "You're unsubscribed" in client.post(f"/u/{token}").get_data(as_text=True)
    assert email in db.suppressed_emails([email])
    # A mail app's one-click button (RFC 8058)
    other = _email()
    r = client.post(f"/u/{outreach.unsubscribe_token(other)}", data={"List-Unsubscribe": "One-Click"})
    assert r.status_code == 200 and other in db.suppressed_emails([other])
    assert "doesn't work" in client.get("/u/not-a-token").get_data(as_text=True)


def test_unsubscribed_people_are_never_emailed_again(admin, smtp):
    contact = _contact(admin)
    db.add_suppression(contact["email"], "unsubscribed", "link")
    client = _client(admin)
    r = client.post("/api/admin/invite-contacts/send", json={"ids": [contact["id"]], "style": "note"}).get_json()
    assert r["unsubscribed"] == 1 and r["invited"] == 0 and not smtp
    assert client.post("/api/admin/invitations", json={"email": contact["email"]}).status_code == 409
    listed = next(c for c in db.list_invite_contacts()["contacts"] if c["id"] == contact["id"])
    assert listed["status"] == "unsubscribed" and listed["unsubscribed_at"]


# ── Emails ──────────────────────────────────────────────────────────────────


def test_every_invitation_has_plain_text_unsubscribe_headers_and_address(admin, smtp):
    db.save_setting("company_postal_address", "Office 4, Baner Road, Pune 411045, India")
    client = _client(admin)
    email = _email()
    assert client.post("/api/admin/invitations", json={"email": email, "name": "Asha Rao", "inviter_name": "Janak"}).status_code == 200
    [msg] = smtp
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click" and "/u/" in msg["List-Unsubscribe"]
    parts = _parts(msg)
    assert set(parts) == {"text/plain", "text/html"}
    assert "Hi Asha," in parts["text/plain"] and "/i/" in parts["text/plain"] and "Unsubscribe: http" in parts["text/plain"]
    assert "Baner Road" in parts["text/plain"] and "Baner Road" in parts["text/html"] and "Unsubscribe</a>" in parts["text/html"]
    assert "works for 30 days" in parts["text/plain"]


def test_note_style_shows_their_posts_or_falls_back_to_the_short_note(admin, smtp, fake_ai):
    with_preview = _contact(admin, name="Rohit Tiwari")
    without = _contact(admin, company="Beta Works", website="")
    lps.build_preview(db.queue_lead_previews([with_preview["id"]], admin["id"])["queued"][0])
    client = _client(admin)
    r = client.post("/api/admin/invite-contacts/send", json={
        "ids": [with_preview["id"], without["id"]], "style": "preview", "inviter_name": "Janak",
        "subject": "posts for {company}", "body": "{greeting}\n\n{first_line}\n\n{preview_link}\n\n{sender_name}",
        "postal_address": "Pune, India"}).get_json()
    assert r["invited"] == 2
    by_to = {m["To"]: m for m in smtp}
    mine = by_to[with_preview["email"]]
    assert mine["Subject"] == "posts for Synkrama Technologies" and mine["From"].startswith("Janak <")
    text = _parts(mine)["text/plain"]
    token = db.get_invite_contacts([with_preview["id"]])[0]["preview"]["token"]
    assert text.startswith("Hi Rohit,\n\nSaw that Synkrama builds") and f"/p/{token}" in text and "Pune, India" in text
    other = by_to[without["email"]]  # no preview: the short note, with the sign-up link
    assert other["Subject"] == "Beta Works on LinkedIn"
    other_text = _parts(other)["text/plain"]
    assert other_text.startswith("Hi Beta Works team,") and "/i/" in other_text and "/p/" not in other_text
    assert db.get_setting("company_postal_address") == "Pune, India"


# ── Clicks and the funnel ───────────────────────────────────────────────────


def _invite(admin, contact):
    token = secrets.token_urlsafe(32)
    invitation = db.upsert_invitation(contact["email"], contact.get("name"), None, admin["id"], token, inviter_name="Janak")
    db.record_contact_invite(contact["id"], invitation["id"])
    return token


def test_clicks_count_people_not_link_scanners(admin, monkeypatch):
    monkeypatch.setattr(outreach, "SCANNER_WINDOW_SECONDS", 0)
    contact = _contact(admin)
    token = _invite(admin, contact)
    client = _client()
    r = client.get(f"/i/{token}", headers={"User-Agent": SCANNER})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/signup?invite={token}")
    assert db.list_invite_contacts()["funnel"]["clicked"] == 0
    client.get(f"/i/{token}", headers={"User-Agent": BROWSER})
    listed = next(c for c in db.list_invite_contacts()["contacts"] if c["id"] == contact["id"])
    assert listed["status"] == "clicked" and listed["clicks"] == 1 and listed["first_clicked_at"]
    funnel = db.list_invite_contacts()["funnel"]
    assert funnel["invited"] >= 1 and funnel["clicked"] >= 1


def test_clicks_right_after_sending_are_treated_as_scanners():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    assert outreach.looks_automated(BROWSER, (now - timedelta(seconds=5)).isoformat(), now)
    assert not outreach.looks_automated(BROWSER, (now - timedelta(minutes=5)).isoformat(), now)
    assert outreach.looks_automated(None) and outreach.looks_automated("python-requests/2.31")


# ── Lead previews ───────────────────────────────────────────────────────────


def test_preparing_a_preview(admin, fake_ai):
    contact = _contact(admin)
    client = _client(admin)
    r = client.post("/api/admin/invite-contacts/previews", json={"ids": [contact["id"]]}).get_json()
    assert r == {"success": True, "queued": 1, "already_ready": 0}
    lps.wait_until_idle()
    preview = db.get_invite_contacts([contact["id"]])[0]["preview"]
    assert preview["status"] == "ready" and preview["first_line"].startswith("Saw that Synkrama")
    full = db.get_lead_preview(token=preview["token"])
    assert [p["title"] for p in full["posts"]] == ["Our apps", "Tips", "Diwali"]
    assert full["posts"][0]["hashtags"] == ["#apps", "#Pune"] and full["website"] == "https://www.synkrama.example"
    # Asking again doesn't redo (or pay for) a ready one
    again = client.post("/api/admin/invite-contacts/previews", json={"ids": [contact["id"]]}).get_json()
    assert again["queued"] == 0 and again["already_ready"] == 1 and fake_ai["calls"] == 1


def test_a_blocked_website_falls_back_to_the_sheet_description(admin, fake_ai):
    fake_ai["ok"] = False
    described = _contact(admin, description="Custom software and mobile app development for startups in Pune and Mumbai.")
    bare = _contact(admin)
    for c in (described, bare):
        lps.build_preview(db.queue_lead_previews([c["id"]], admin["id"])["queued"][0])
    assert db.get_invite_contacts([described["id"]])[0]["preview"]["status"] == "ready"
    failed = db.get_invite_contacts([bare["id"]])[0]["preview"]
    assert failed["status"] == "failed" and "bot protection" in failed["error"]


def test_preview_page_views_and_images(admin, fake_ai, fake_images, monkeypatch):
    monkeypatch.setattr(outreach, "SCANNER_WINDOW_SECONDS", 0)
    contact = _contact(admin)
    lps.build_preview(db.queue_lead_previews([contact["id"]], admin["id"])["queued"][0])
    token = db.get_invite_contacts([contact["id"]])[0]["preview"]["token"]
    client = _client()
    page = client.get(f"/p/{token}")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "3 LinkedIn posts for Synkrama Technologies" in html
    assert page.headers["X-Robots-Tag"] == "noindex, nofollow" and "bad;color" not in html and "--accent: #1A73E8" in html
    assert not fake_images  # opening the page alone (e.g. a link scanner) makes nothing
    # A scanner calling the script endpoint is not a view and makes no images
    client.post(f"/p/{token}/seen", headers={"User-Agent": SCANNER})
    assert not fake_images and db.get_lead_preview(token=token)["view_count"] == 0
    data = client.post(f"/p/{token}/seen", headers={"User-Agent": BROWSER}).get_json()
    assert len(fake_images) == 3 and all(m["logo"] == "/tmp/logo.png" for m in fake_images)  # the real logo, stamped
    assert "No text, words, letters" in fake_images[0]["prompt"]
    assert [p["image_status"] for p in data["posts"]] == ["ready"] * 3 and data["posts"][0]["image_url"].endswith(".png")
    assert "email" not in str(data) and "cost" not in str(data)  # nothing private on the public page
    assert db.get_lead_preview(token=token)["view_count"] == 1
    client.post(f"/p/{token}/seen", headers={"User-Agent": BROWSER})
    assert len(fake_images) == 3  # images are made once
    assert db.lead_preview_images_today() >= 3  # charged to the admin who prepared it
    assert client.get("/p/unknown-token-123").status_code == 404


def test_an_admin_looking_at_a_preview_is_not_a_view(admin, fake_ai, fake_images):
    contact = _contact(admin)
    lps.build_preview(db.queue_lead_previews([contact["id"]], admin["id"])["queued"][0])
    token = db.get_invite_contacts([contact["id"]])[0]["preview"]["token"]
    _client(admin).post(f"/p/{token}/seen", headers={"User-Agent": BROWSER})
    assert db.get_lead_preview(token=token)["view_count"] == 0


def test_daily_image_cap(admin, fake_ai, fake_images, monkeypatch):
    monkeypatch.setattr(lps.Config, "LEAD_PREVIEW_IMAGES_PER_DAY", 0)
    contact = _contact(admin)
    lps.build_preview(db.queue_lead_previews([contact["id"]], admin["id"])["queued"][0])
    preview = db.get_lead_preview(token=db.get_invite_contacts([contact["id"]])[0]["preview"]["token"])
    assert lps.start_images(preview) is False and not fake_images


def test_signing_up_from_a_preview_brings_the_brand_and_posts(admin, fake_ai, monkeypatch):
    monkeypatch.setattr(outreach, "SCANNER_WINDOW_SECONDS", 0)
    contact = _contact(admin, name="Rohit Tiwari")
    lps.build_preview(db.queue_lead_previews([contact["id"]], admin["id"])["queued"][0])
    preview_token = db.get_invite_contacts([contact["id"]])[0]["preview"]["token"]
    invite_token = _invite(admin, contact)
    client = _client()
    start = client.get(f"/p/{preview_token}/start", headers={"User-Agent": BROWSER})
    assert start.headers["Location"].endswith(f"/i/{invite_token}?p={preview_token}")
    to_signup = client.get(f"/i/{invite_token}?p={preview_token}", headers={"User-Agent": BROWSER})
    assert to_signup.headers["Location"].endswith(f"/signup?invite={invite_token}&preview={preview_token}")
    signup = client.get(f"/signup?invite={invite_token}&preview={preview_token}").get_data(as_text=True)
    assert "Your 3 posts for <strong>Synkrama Technologies</strong> are saved" in signup
    r = client.post("/api/auth/register", json={"name": "Rohit Tiwari", "email": contact["email"], "password": "violet-harbour-42", "accept_terms": True,
                                                "invite_token": invite_token, "preview_token": preview_token}).get_json()
    assert r["success"]
    user = db.get_user_by_email(contact["email"])
    profile = db.get_user_brand_profile(user.id)
    assert profile["company_name"] == "Synkrama Technologies" and profile["website"] == "https://www.synkrama.example"
    # Onboarding with the same website: no second scan, straight to the posts
    onboard = client.post("/api/onboarding/account-type", json={"account_type": "small", "website": "synkrama.example"}).get_json()
    assert onboard["scan_started"] is False and onboard["redirect"].startswith("/dashboard?conversation=")
    conversation_id = int(onboard["redirect"].split("=")[1])
    runs = client.get(f"/api/conversations/{conversation_id}").get_json()["runs"]
    assert len(runs) == 3
    listed = next(c for c in db.list_invite_contacts()["contacts"] if c["id"] == contact["id"])
    assert listed["status"] == "joined" and listed["activated"] is False  # copied posts aren't their own activity
    assert listed["preview"]["claimed"] is True
    # A preview is copied once, and only for the email it was made for
    assert lps.claim_preview(preview_token, user.id, contact["email"]) is None


def test_a_preview_is_not_given_to_someone_else(admin, fake_ai):
    contact = _contact(admin)
    lps.build_preview(db.queue_lead_previews([contact["id"]], admin["id"])["queued"][0])
    token = db.get_invite_contacts([contact["id"]])[0]["preview"]["token"]
    stranger = db.create_user("Someone", _email(), "h")
    assert lps.claim_preview(token, stranger["id"], stranger["email"]) is None
    assert db.get_user_brand_profile(stranger["id"]) is None


def test_export_has_merge_fields_for_an_email_tool(admin, fake_ai):
    contact = _contact(admin)
    lps.build_preview(db.queue_lead_previews([contact["id"]], admin["id"])["queued"][0])
    export = _client(admin).get("/api/admin/invite-contacts/export.csv").get_data(as_text=True)
    header = export.splitlines()[0].split(",")
    assert {"first_line", "preview_url", "clicks", "unsubscribed_at", "activated"} <= set(header)
    assert "/p/" in export and "Saw that Synkrama" in export


def test_preview_endpoints_are_admin_only(admin):
    user = db.create_user("User", _email(), "h")
    assert _client(user).post("/api/admin/invite-contacts/previews", json={"ids": [1]}).status_code == 403
    assert _client(admin).post("/api/admin/invite-contacts/previews", json={"ids": []}).status_code == 400


def test_workers_do_not_leak_between_tests():
    assert all(t.daemon for t in threading.enumerate() if t.name.startswith("lead-preview"))


# ── What was sent, and each person's stage ──────────────────────────────────


def test_every_email_is_kept_as_sent_with_the_persons_stage(admin, smtp, fake_ai, monkeypatch):
    contact = _contact(admin, name="Rohit Tiwari")
    lps.build_preview(db.queue_lead_previews([contact["id"]], admin["id"])["queued"][0])
    client = _client(admin)
    assert client.post("/api/admin/invite-contacts/send", json={"ids": [contact["id"]], "style": "preview",
                                                                "inviter_name": "Janak"}).get_json()["invited"] == 1
    journey = client.get(f"/api/admin/invite-contacts/{contact['id']}/journey").get_json()
    [message] = journey["messages"]
    assert message["style"] == "preview" and message["subject"] == "social posts for Synkrama Technologies"
    assert message["body"].startswith("Hi Rohit,") and "Unsubscribe:" in message["body"] and message["status"] == "sent"
    assert message["sender_name"] == "Janak" and journey["stage"] == "invited"
    assert [s["key"] for s in journey["stages"] if s["at"]] == ["added", "preview_ready", "invited"]
    assert client.get(f"/api/admin/invite-messages/{message['id']}").get_json()["message"]["body"] == message["body"]
    listed = next(c for c in client.get("/api/admin/invite-contacts").get_json()["contacts"] if c["id"] == contact["id"])
    assert listed["stage"] == "invited" and listed["emails_sent"] == 1 and listed["last_email"]["subject"] == message["subject"]
    assert "body" not in listed["last_email"]  # the list stays light; the text loads on demand
    # They open it: the stage moves on, here and in Sent invitations
    token = db.get_invite_contacts([contact["id"]])[0]["preview"]["token"]
    _client().post(f"/p/{token}/seen", headers={"User-Agent": BROWSER})
    assert client.get(f"/api/admin/invite-contacts/{contact['id']}/journey").get_json()["stage"] == "opened"
    invitation = next(i for i in client.get("/api/admin/invitations").get_json()["invitations"] if i["email"] == contact["email"])
    assert invitation["last_email"]["subject"] == message["subject"] and invitation["emails_sent"] == 1


def test_a_failed_email_is_kept_too(admin, smtp, monkeypatch):
    from services.email_service import EmailService

    def bounce(self, msg, to, kind):
        raise RuntimeError("Mailbox unavailable")

    monkeypatch.setattr(EmailService, "_deliver", bounce)
    contact = _contact(admin)
    r = _client(admin).post("/api/admin/invite-contacts/send", json={"ids": [contact["id"]], "style": "note"}).get_json()
    assert r["failed"] == 1
    [message] = db.invite_contact_journey(contact["id"])["messages"]
    assert message["status"] == "failed" and "Mailbox unavailable" in message["error"] and message["style"] == "note"


def test_email_and_journey_endpoints_are_admin_only(admin):
    user = db.create_user("User", _email(), "h")
    assert _client(user).get("/api/admin/invite-contacts/1/journey").status_code == 403
    assert _client(user).get("/api/admin/invite-messages/1").status_code == 403
    assert _client(admin).get("/api/admin/invite-messages/999999").status_code == 404


def test_the_designed_email_is_kept_as_html_too(admin, smtp):
    contact = _contact(admin, name="Asha Rao")
    client = _client(admin)
    assert client.post("/api/admin/invite-contacts/send", json={"ids": [contact["id"]], "style": "designed",
                                                                "inviter_name": "Janak", "message": "Welcome!"}).get_json()["invited"] == 1
    [message] = client.get(f"/api/admin/invite-contacts/{contact['id']}/journey").get_json()["messages"]
    assert message["style"] == "designed" and message["subject"] == "Janak invited you to AVIR AI"
    assert "Accept invitation" in message["html"] and "Welcome!" in message["html"] and "Unsubscribe</a>" in message["html"]
    assert message["body"].startswith("Hi Asha,")


def test_every_style_is_a_designed_html_email(admin, smtp, fake_ai):
    with_preview = _contact(admin, name="Rohit Tiwari")
    without = _contact(admin, company="Beta Works Pvt. Ltd.", website="")
    lps.build_preview(db.queue_lead_previews([with_preview["id"]], admin["id"])["queued"][0])
    client = _client(admin)
    client.post("/api/admin/invite-contacts/send", json={"ids": [with_preview["id"], without["id"]], "style": "preview",
                                                         "inviter_name": "Janak"})
    by_to = {m["To"]: _parts(m) for m in smtp}
    token = db.get_invite_contacts([with_preview["id"]])[0]["preview"]["token"]
    posts_html = by_to[with_preview["email"]]["text/html"]
    assert "3 LinkedIn posts for Synkrama Technologies" in posts_html and "linear-gradient" in posts_html  # the AVIR AI design
    assert f'/p/{token}" style="display: inline-block' in posts_html and "See your posts" in posts_html  # the link is a button
    assert "We build apps." in posts_html and "Our apps" in posts_html and "#1A73E8" in posts_html  # a peek at the posts
    assert "Unsubscribe</a>" in posts_html and "Hi Rohit," in posts_html
    note_html = by_to[without["email"]]["text/html"]
    assert "An invitation for Beta Works" in note_html and "Set up my account" in note_html and "/i/" in note_html
    assert "Hi Beta Works team," in note_html and "Draft post" not in note_html
    # what was kept is the same design
    [kept] = client.get(f"/api/admin/invite-contacts/{without['id']}/journey").get_json()["messages"]
    assert kept["html"] == note_html


def test_the_send_panel_shows_the_real_email_without_sending_it(admin, smtp, fake_ai):
    contact = _contact(admin, company="Sahyadri Motors")
    r = _client(admin).post("/api/admin/invite-contacts/sample", json={
        "contact_id": contact["id"], "style": "designed", "inviter_name": "Janak", "message": "Hello from Pune",
        "postal_address": "Baner Road, Pune"}).get_json()
    assert r["subject"] == "Janak invited you to AVIR AI" and "Hi Sahyadri Motors team," in r["html"]
    assert "Hello from Pune" in r["html"] and "Baner Road, Pune" in r["html"]
    assert not smtp and not db.invite_contact_journey(contact["id"])["messages"]  # nothing sent or kept
