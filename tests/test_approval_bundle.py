"""Carousels (/carousel and picked slides) and one approval request for a
whole post: each platform's rules, LinkedIn's two carousel kinds, the review
email, per-platform decisions and the email back to the requester.
The image model, text model and SMTP are faked - nothing is sent or billed."""

import json
import os
import uuid
from typing import ClassVar

import pytest
from PIL import Image
from sqlalchemy.orm import Session

import db
from config import Config
from services import approval_bundle_service as bundle
from services import carousel_service as carousel

# ── helpers ─────────────────────────────────────────────────────────────────


@pytest.fixture
def uploads():
    """Makes images in the uploads folder; removes them (and any PDFs made) afterwards."""
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    made = []

    def make(size=(108, 108), color="orange"):
        name = f"test_slide_{uuid.uuid4().hex[:8]}.png"
        Image.new("RGB", size, color).save(os.path.join(Config.UPLOAD_FOLDER, name))
        made.append(name)
        return f"/static/uploads/{name}"

    before = set(os.listdir(Config.UPLOAD_FOLDER))
    yield make
    for name in set(os.listdir(Config.UPLOAD_FOLDER)) - before:
        if name.startswith(("test_slide_", "carousel_")):
            os.remove(os.path.join(Config.UPLOAD_FOLDER, name))


class FakeSMTP:
    sent: ClassVar[list] = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def sendmail(self, sender, recipients, body):
        FakeSMTP.sent.append({"to": recipients, "body": body})


@pytest.fixture
def smtp(monkeypatch):
    import services.email_service as es

    FakeSMTP.sent = []
    monkeypatch.setattr(es.smtplib, "SMTP", FakeSMTP)
    for name, value in (("SMTP_HOST", "smtp.test"), ("SMTP_USERNAME", "u"), ("SMTP_PASSWORD", "p"),
                        ("SMTP_FROM_EMAIL", "noreply@test")):
        monkeypatch.setattr(Config, name, value)
    return FakeSMTP


# ── Carousel planning ───────────────────────────────────────────────────────


@pytest.mark.parametrize("text, count", [("7 slides on hiring", 7), ("a carousel", 5), ("20 slides", 10), ("2 cards", 3)])
def test_slide_count(text, count):
    assert carousel.slide_count(text) == count


def test_portrait_when_asked():
    assert carousel.carousel_aspect("5 slides, 4:5 please") == "4:5" and carousel.carousel_aspect("5 slides") == "1:1"


def test_plan_is_padded_or_trimmed_to_the_count_and_cleaned():
    plan = carousel.normalize_plan({"slides": [{"title": "Hook", "headline": "one two three four five six seven eight nine ten eleven"}],
                                    "hashtags": ["growth", "#Growth", "#B2B marketing"], "caption": "Swipe"}, 4, "brief")
    assert len(plan["slides"]) == 4 and plan["slides"][-1]["title"] == "Call to action"
    assert len(plan["slides"][0]["headline"].split()) == 10
    assert plan["hashtags"] == ["#growth", "#B2Bmarketing"]


def test_slide_prompt_prints_the_headline_and_keeps_the_set_consistent():
    plan = {"style": "Navy and orange, bold sans-serif", "slides": [{"title": "Hook", "headline": "Stop guessing", "visual": "a dashboard"},
                                                                    {"title": "CTA", "headline": "Try it", "visual": ""}]}
    prompt = carousel.slide_prompt("SCENE", plan, 1, {"primary_colors": ["#e85a1c"]}, aspect="4:5")
    assert '"Stop guessing"' in prompt and "slide 1 of 2" in prompt and "Navy and orange" in prompt
    assert "#e85a1c" in prompt and "4:5" in prompt


def test_document_carousel_pdf_has_one_page_per_slide(uploads):
    urls = [uploads(), uploads(color="navy"), uploads(size=(80, 100))]
    paths = [os.path.join(Config.UPLOAD_FOLDER, os.path.basename(u)) for u in urls]
    name = carousel.build_pdf(paths, Config.UPLOAD_FOLDER)
    with open(os.path.join(Config.UPLOAD_FOLDER, name), "rb") as f:
        data = f.read()
    assert data.startswith(b"%PDF") and data.count(b"/Type /Page\n") + data.count(b"/Type /Page\r") + data.count(b"/Type /Page ") >= 3


# ── Each platform's rules ───────────────────────────────────────────────────


def test_a_whole_post_is_normalized(uploads):
    a, b, c = uploads(), uploads(), uploads()
    items = bundle.normalize_items([
        {"platform": "linkedin", "caption": "Big news", "hashtags": ["launch"], "media": "carousel", "images": [a, b, c],
         "slide_titles": ["Hook"], "linkedin_format": "document"},
        {"platform": "instagram", "caption": "New!", "media": "carousel", "images": [a, b]},
        {"platform": "facebook", "caption": "Hi all", "media": "single", "images": [c, a]},
        {"platform": "linkedin", "caption": "duplicate platform is ignored"},
    ], Config.UPLOAD_FOLDER)
    assert [i["platform"] for i in items] == ["linkedin", "instagram", "facebook"]
    assert items[0]["hashtags"] == ["#launch"] and items[0]["linkedin_format"] == "document"
    assert items[0]["slide_titles"] == ["Hook", "Slide 2", "Slide 3"]
    assert items[2]["images"] == [c] and all(i["decision"] == "pending" for i in items)


@pytest.mark.parametrize("raw, message", [
    ({"platform": "instagram", "caption": "x"}, "Instagram posts need an image"),
    ({"platform": "linkedin", "caption": "x", "media": "carousel", "images": ["ONE"]}, "2 to 20 slides"),
    ({"platform": "facebook", "caption": "x", "media": "single", "images": ["/etc/passwd"]}, "could not be found"),
    ({"platform": "facebook", "caption": "", "media": "none"}, "nothing to review"),
    ({"platform": "instagram", "caption": "x", "media": "single", "images": ["ONE"], "hashtags": [f"#t{i}" for i in range(31)]},
     "at most 30 hashtags"),
])
def test_platform_rules(uploads, raw, message):
    one = uploads()
    raw = {**raw, "images": [one if u == "ONE" else u for u in raw.get("images", [])]}
    with pytest.raises(bundle.BundleError, match=message):
        bundle.normalize_items([raw], Config.UPLOAD_FOLDER)


def test_instagram_carousel_slides_must_share_a_shape(uploads):
    square, portrait = uploads((108, 108)), uploads((108, 135))
    with pytest.raises(bundle.BundleError, match="same shape"):
        bundle.normalize_items([{"platform": "instagram", "caption": "x", "media": "carousel", "images": [square, portrait]}],
                               Config.UPLOAD_FOLDER)
    # LinkedIn doesn't mind mixed shapes
    assert bundle.normalize_items([{"platform": "linkedin", "caption": "x", "media": "carousel", "images": [square, portrait]}],
                                  Config.UPLOAD_FOLDER)[0]["linkedin_format"] == "multi_image"


def test_prepare_builds_the_pdf_and_reviews_each_caption(uploads):
    a, b = uploads(), uploads()
    items = bundle.normalize_items([
        {"platform": "linkedin", "caption": "Guaranteed returns!", "media": "carousel", "images": [a, b], "linkedin_format": "document"},
        {"platform": "facebook", "caption": "Hello", "media": "single", "images": [a]},
    ], Config.UPLOAD_FOLDER)
    checked = []

    def check(caption, platform, rules):
        checked.append(platform)
        return {"flags": [{"severity": "high"}], "caption": "Fixed"} if "Guaranteed" in caption else None

    items = bundle.prepare_items(items, Config.UPLOAD_FOLDER, rules=["r"], check_caption=check)
    assert items[0]["pdf_url"].endswith(".pdf") and os.path.isfile(os.path.join(Config.UPLOAD_FOLDER, os.path.basename(items[0]["pdf_url"])))
    assert items[0]["compliance"]["suggested_caption"] == "Fixed" and "compliance" not in items[1]
    assert checked == ["linkedin", "facebook"]


@pytest.mark.parametrize("decision, per_item, status", [
    ("approved", {}, "approved"),
    ("rejected", {}, "rejected"),
    (None, {"0": {"decision": "approved"}, "1": {"decision": "changes", "comment": "New photo"}}, "partial"),
    (None, {"linkedin": {"decision": "changes"}, "facebook": {"decision": "changes"}}, "rejected"),
])
def test_decisions_set_the_overall_status(decision, per_item, status):
    items = [{"platform": "linkedin", "decision": "pending"}, {"platform": "facebook", "decision": "pending"}]
    items, result = bundle.apply_decisions(items, decision, per_item)
    assert result == status
    if status == "partial":
        assert items[1]["comment"] == "New photo"


def test_every_platform_must_be_decided():
    with pytest.raises(ValueError, match="every platform"):
        bundle.apply_decisions([{"platform": "linkedin", "decision": "pending"}, {"platform": "facebook", "decision": "pending"}],
                               None, {"0": {"decision": "approved"}})


# ── API: send, review, decide ───────────────────────────────────────────────


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


def _login(app, user):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return client


@pytest.fixture
def people(app, monkeypatch):
    from api import routes

    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [])
    requester = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "h")
    reviewer = db.create_user("Ravi", f"{uuid.uuid4().hex[:10]}@example.com", "h")
    db.set_approval_reviewer_email(requester["id"], reviewer["email"])
    with Session(db.engine) as session:
        session.add(db.UserBrandProfile(user_id=requester["id"], website="https://acme.test", company_name="Acme Logistics"))
        session.commit()
    return {"requester": requester, "reviewer": reviewer}


def test_send_a_whole_post_in_one_email(app, people, smtp, uploads):
    a, b, c = uploads(), uploads(), uploads()
    client = _login(app, people["requester"])
    r = client.post("/api/approval-requests", json={"pipeline_client_id": "studio-41", "story": "Announce live tracking", "items": [
        {"platform": "linkedin", "caption": "Live tracking is here", "hashtags": ["#Logistics"], "media": "carousel",
         "images": [a, b, c], "linkedin_format": "document"},
        {"platform": "instagram", "caption": "Track every truck", "media": "carousel", "images": [a, b]},
        {"platform": "facebook", "caption": "Big news", "media": "single", "images": [c]},
    ]})
    body = r.get_json()
    assert r.status_code == 200 and body["email"]["success"]
    req = body["request"]
    assert req["platform"] == "multi" and req["asset_type"] == "carousel" and len(req["items"]) == 3
    assert req["items"][0]["pdf_url"].endswith(".pdf")
    [mail] = smtp.sent
    assert mail["to"] == [people["reviewer"]["email"]]
    assert "Acme Logistics" in mail["body"] and "linkedin-carousel.pdf" in mail["body"]
    assert body["email"]["images"] == 3  # each image embedded once, even when platforms share it
    row = db.list_email_log(kind="approval_request")["emails"][0]
    assert "LinkedIn, Instagram, Facebook" in row["subject"]


def test_bad_items_are_explained(app, people, uploads):
    client = _login(app, people["requester"])
    r = client.post("/api/approval-requests", json={"pipeline_client_id": "studio-42",
                                                    "items": [{"platform": "instagram", "caption": "no image"}]})
    assert r.status_code == 400 and "need an image" in r.get_json()["error"]


def test_reviewer_decides_per_platform_and_the_requester_is_told(app, people, smtp, uploads):
    a, b = uploads(), uploads()
    owner = _login(app, people["requester"])
    req = owner.post("/api/approval-requests", json={"pipeline_client_id": "studio-43", "items": [
        {"platform": "linkedin", "caption": "One", "media": "carousel", "images": [a, b]},
        {"platform": "facebook", "caption": "Two", "media": "single", "images": [a]},
    ]}).get_json()["request"]
    reviewer = _login(app, people["reviewer"])

    view = reviewer.get(f"/api/approval-requests/{req['id']}").get_json()["request"]
    assert view["company_name"] == "Acme Logistics" and view["items"][0]["media"] == "carousel"

    assert reviewer.post(f"/api/approval-requests/{req['id']}/decide", json={"items": {"0": {"decision": "approved"}}}).status_code == 400
    r = reviewer.post(f"/api/approval-requests/{req['id']}/decide", json={
        "items": {"0": {"decision": "approved"}, "1": {"decision": "changes", "comment": "Use the truck photo"}},
        "comments": "Nearly there"})
    decided = r.get_json()["request"]
    assert decided["status"] == "partial" and decided["items"][1]["comment"] == "Use the truck photo"
    told = smtp.sent[-1]
    assert told["to"] == [people["requester"]["email"]] and "partly approved" in told["body"].lower()
    assert "Use the truck photo" in told["body"]
    # decided once only
    assert reviewer.post(f"/api/approval-requests/{req['id']}/decide", json={"decision": "approved"}).status_code == 409


def test_old_single_platform_requests_still_work(app, people, smtp):
    owner = _login(app, people["requester"])
    req = owner.post("/api/approval-requests", json={"pipeline_client_id": "pipe-1", "platform": "linkedin",
                                                     "asset_type": "Text (Caption)", "caption": "Hello"}).get_json()["request"]
    assert req["items"] is None
    r = _login(app, people["reviewer"]).post(f"/api/approval-requests/{req['id']}/decide", json={"decision": "approved"})
    assert r.get_json()["request"]["status"] == "approved"


# ── Conversation images (the slide picker) ──────────────────────────────────


def test_conversation_images_are_only_for_their_owner(app, people):
    owner = people["requester"]
    conversation_id = db.ensure_conversation(owner["id"], None, "Launch")
    db.log_image_generation(owner["id"], 0.55, kind="image", media_url="/static/uploads/a.jpg", conversation_id=conversation_id,
                            description="Slide 1")
    images = _login(app, owner).get(f"/api/conversations/{conversation_id}/images").get_json()["images"]
    assert [i["url"] for i in images] == ["/static/uploads/a.jpg"]
    assert _login(app, people["reviewer"]).get(f"/api/conversations/{conversation_id}/images").status_code == 404


# ── /carousel ───────────────────────────────────────────────────────────────


@pytest.fixture
def carousel_fakes(monkeypatch):
    from api import routes
    from services.llm_service import LLMService

    made = []

    def create_preset_image(prompt, image_path, aspect, logo_path=None, model=None, require_reference=True):
        made.append({"prompt": prompt, "image": image_path, "aspect": aspect, "require_reference": require_reference})
        return {"success": True, "type": "image", "url": f"/static/uploads/slide{len(made)}.jpg", "prompt": prompt,
                "cost": 0.55, "model_id": "m", "aspect": aspect, "width": 1080, "height": 1350 if aspect == "4:5" else 1080}

    def plan(self, system_prompt, user_prompt, **kw):
        count = int(system_prompt.split(":")[1].split()[0]) if "carousel:" in system_prompt else 5
        return ({"style": "Clean navy", "caption": "Swipe to see how", "hashtags": ["#logistics"],
                 "slides": [{"title": f"S{i}", "headline": f"Headline {i}", "visual": "truck"} for i in range(1, count + 1)]},
                {"total_tokens": 300, "cost_usd": 0.002})

    monkeypatch.setattr(routes.media_service, "create_preset_image", create_preset_image)
    monkeypatch.setattr(LLMService, "generate_json", plan)
    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [])
    return made


def test_carousel_command_plans_and_draws_every_slide(app, carousel_fakes):
    user = db.create_user("Sam", f"{uuid.uuid4().hex[:10]}@example.com", "h")
    db.set_user_image_access(user["id"], db.IMAGE_UNLIMITED, None)
    r = _login(app, user).post("/api/presets/run", json={"preset": "carousel", "text": "7 slides on live tracking, 4:5"})
    body = r.get_json()
    assert r.status_code == 200, body
    assert len(carousel_fakes) == 7 and all(m["aspect"] == "4:5" and m["require_reference"] is False for m in carousel_fakes)
    assert '"Headline 3"' in carousel_fakes[2]["prompt"]
    meta = body["preset"]["carousel"]
    assert meta["caption"] == "Swipe to see how" and [s["key"] for s in meta["slides"]] == [f"slide{i}" for i in range(1, 8)]
    assert body["usage"]["media_count"] == 7 and json.dumps(body["content"]["slide1"]["media"])


def test_carousel_checks_the_daily_limit_for_every_slide_first(app, carousel_fakes):
    user = db.create_user("Sam", f"{uuid.uuid4().hex[:10]}@example.com", "h")
    db.set_user_image_access(user["id"], 3, None)
    r = _login(app, user).post("/api/presets/run", json={"preset": "carousel", "text": "5 slides on hiring"})
    assert r.status_code == 403 and "needs 5 images" in r.get_json()["error"] and carousel_fakes == []
