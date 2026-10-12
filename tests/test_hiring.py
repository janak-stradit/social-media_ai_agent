"""/hiring: a job post with a new design every time. The text model, the
image model and the compliance check are faked - nothing is generated or billed."""

import json
import random
import uuid

import pytest

import db
from services import hiring_service as hs
from services.image_presets import get_preset, list_presets


class FakeLLM:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def generate_json(self, system, user, **kw):
        self.calls.append((system, user, kw))
        return self.answer, {"total_tokens": 300, "cost_usd": 0.002}


JOB = {"role": "Delivery Drivers", "location": "Pune", "employment_type": "Full-time",
       "perks": ["Fixed routes", "Paid training", "Weekly pay", "Free lunch"], "deadline": "30 Nov", "apply": "",
       "headline": "We're hiring", "cta": "Apply now", "workplace": "a busy delivery depot with vans at dawn",
       "caption": "We're hiring delivery drivers in Pune. Fixed routes and paid training. Apply by 30 November.",
       "hashtags": ["Hiring", "#Jobs", "#PuneJobs", "#hiring"]}


# ── Reading the job ─────────────────────────────────────────────────────────


def test_the_job_is_read_and_kept_short():
    llm = FakeLLM({**JOB, "role": "Senior Experienced Delivery Van Drivers For Our New Western Depot"})
    job, usage = hs.plan_job("Delivery drivers in Pune, paid training, apply by 30 Nov", "BRAND: Acme", llm=llm)
    assert len(job["role"]) <= hs.LIMITS["role"] and job["role"].startswith("Senior")
    assert job["perks"] == ["Fixed routes", "Paid training", "Weekly pay"]  # at most three
    assert job["hashtags"] == ["#Hiring", "#Jobs", "#PuneJobs"]  # "#" added, the repeat dropped
    assert usage["total_tokens"] == 300
    system, user, kw = llm.calls[0]
    assert "Fair hiring" in system and "BRIEF: Delivery drivers in Pune" in user and "BRAND: Acme" in user
    assert kw["return_usage"] is True


def test_missing_words_get_sensible_defaults():
    job = hs.normalize_plan({"role": "Barista", "hashtags": ["#Coffee"]}, "Barista in Dubai")
    assert job["headline"] == hs.DEFAULT_HEADLINE and job["cta"] == hs.DEFAULT_CTA
    assert job["hashtags"][0] == "#Hiring" and job["caption"].startswith("We're hiring Barista")
    assert job["location"] == "" and "barista" in job["workplace"]


@pytest.mark.parametrize("answer", [{}, {"role": "  "}, None, "not json"])
def test_no_role_is_explained(answer):
    with pytest.raises(hs.HiringError, match="which role"):
        hs.normalize_plan(answer)
    with pytest.raises(hs.HiringError, match="Describe the job"):
        hs.plan_job("   ", llm=FakeLLM(JOB))


# ── A different design every time ───────────────────────────────────────────


def test_designs_change_and_no_layout_repeats_within_three_posts():
    rng = random.Random(7)
    history = []
    for _ in range(40):
        design = hs.pick_design(history, rng)
        recent_layouts = {d["layout"] for d in history[:hs.NO_REPEAT_LAYOUTS]}
        assert design["layout"] not in recent_layouts
        if history:
            assert design["scene"] != history[0]["scene"] and design["treatment"] != history[0]["treatment"]
        assert design["layout"] in hs.LAYOUTS and design["scene"] in hs.SCENES and design["treatment"] in hs.TREATMENTS
        history.insert(0, design)
    assert len({d["layout"] for d in history}) == len(hs.LAYOUTS)  # every layout gets used
    assert len({(d["layout"], d["scene"], d["treatment"]) for d in history}) > 30  # few identical combinations


def test_no_city_scene_without_a_place():
    rng = random.Random(1)
    assert all(hs.pick_design([], rng, has_location=False)["scene"] != "city" for _ in range(60))


def test_old_or_odd_history_is_ignored():
    design = hs.pick_design([None, "x", {"layout": "unknown"}], random.Random(3))
    assert design["layout"] in hs.LAYOUTS


# ── The image prompt ────────────────────────────────────────────────────────


def test_the_prompt_spells_out_the_exact_words_and_the_design():
    job = hs.normalize_plan(JOB)
    design = {"layout": "split", "scene": "workplace", "treatment": "dark"}
    square = hs.image_prompt("BASE SCENE", job, design, "1:1", {"primary_colors": ["#E85A1C", "#0F2244"], "visual_style": "bold"})
    for words in ('"We\'re hiring"', '"Delivery Drivers"', '"Pune"', '"Apply now"'):
        assert words in square
    assert '"Fixed routes"' not in square and "Apply by" not in square  # the square keeps to the essentials
    assert square.startswith("BASE SCENE") and hs.LAYOUTS["split"][1] in square and "a busy delivery depot" in square
    assert "#E85A1C, #0F2244" in square and "spelled exactly" in square and "bottom-right corner" in square
    assert "Square 1:1" in square

    tall = hs.image_prompt("BASE SCENE", job, design, "4:5")
    assert '"Fixed routes"' in tall and '"Apply by 30 Nov"' in tall and "Vertical 4:5" in tall


def test_an_attached_photo_is_used_as_the_picture():
    job = hs.normalize_plan(JOB)
    prompt = hs.image_prompt("S", job, {"layout": "badge", "scene": "abstract", "treatment": "light"}, "1:1", has_photo=True)
    assert "attached photo" in prompt and hs.SCENES["abstract"][1].split(":")[0] not in prompt


def test_the_on_image_words_stay_short():
    job = hs.normalize_plan({**JOB, "headline": "We are urgently hiring right now today", "cta": "Click here to apply today please"})
    assert len(job["headline"]) <= hs.LIMITS["headline"] and len(job["cta"]) <= hs.LIMITS["cta"]
    assert all('"' not in line for line in hs.image_text(job, "4:5"))


# ── The command ─────────────────────────────────────────────────────────────


def test_hiring_is_in_the_menu_without_needing_a_photo():
    entry = next(p for p in list_presets() if p["id"] == "hiring")
    assert entry["hiring"] and not entry["requires_image"] and entry["images"] == 2 and entry["all_sizes_images"] == 3
    assert [o["aspect"] for o in get_preset("hiring")["outputs"]] == ["1:1", "4:5"]


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def run(app, monkeypatch):
    """Runs /hiring with fakes. Returns (client, user, calls)."""
    from api import routes

    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "h")
    db.set_user_image_access(user["id"], db.IMAGE_UNLIMITED, None)
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    calls = {"images": [], "checked": []}

    def create_preset_image(prompt, image_path, aspect, logo_path=None, model=None, require_reference=True):
        calls["images"].append({"prompt": prompt, "aspect": aspect, "require_reference": require_reference})
        n = len(calls["images"])
        return {"success": True, "type": "image", "url": f"/static/uploads/hiring{n}.jpg", "prompt": prompt, "cost": 0.04,
                "aspect": aspect, "width": 1080, "height": 1350 if aspect == "4:5" else 1080}

    def check_caption(caption, platform, rules, llm):
        calls["checked"].append([r["id"] for r in rules])
        return ({"caption": caption + " We welcome applicants of every background.", "rules_checked": len(rules), "flags": [],
                 "disclaimers_added": [], "needs_attention": 0}, {"total_tokens": 50, "cost_usd": 0.001})

    real_plan = hs.plan_job
    monkeypatch.setattr(hs, "plan_job", lambda brief, brand_block="": real_plan(brief, brand_block, llm=FakeLLM(JOB)))
    monkeypatch.setattr(routes.media_service, "create_preset_image", create_preset_image)
    monkeypatch.setattr(routes, "check_caption", check_caption)
    monkeypatch.setattr(routes, "active_rules_for_user", lambda uid: [])
    monkeypatch.setattr(db, "get_user_brand_profile", lambda uid: {"compliance_regions": ["US"], "primary_colors": ["#E85A1C"]})
    return client, user, calls


def test_a_hiring_post_is_made_and_saved(run):
    client, _, calls = run
    r = client.post("/api/presets/run", json={"preset": "hiring", "text": "Delivery drivers in Pune, paid training, apply by 30 Nov"})
    body = r.get_json()
    assert r.status_code == 200, body
    assert [i["aspect"] for i in calls["images"]] == ["1:1", "4:5"]
    assert all(not i["require_reference"] for i in calls["images"])  # no photo needed
    hiring = body["preset"]["hiring"]
    assert hiring["caption"].endswith("We welcome applicants of every background.")  # with the compliance check's fix
    assert hiring["hashtags"][0] == "#Hiring" and hiring["job"]["role"] == "Delivery Drivers" and "caption" not in hiring["job"]
    assert set(hiring["design"]) == {"layout", "scene", "treatment"} and hiring["design_names"]["layout"]
    [checked] = calls["checked"]  # fair-hiring rules of the brand's market first, then its general advertising rules
    assert checked[0] == "us-eeo-job-ads" and "us-ftc-truthful-advertising" in checked
    both = {i["prompt"] for i in calls["images"]}
    assert all(hs.LAYOUTS[hiring["design"]["layout"]][1] in p for p in both)  # one design for both sizes
    saved = db.get_run_by_id(body["run_id"])
    assert saved["story"].startswith("/hiring") and saved["content"]["_preset"]["hiring"]["design"] == hiring["design"]
    assert body["usage"]["total_tokens"] == 350


def test_the_next_post_gets_a_different_layout(run):
    client, user, _ = run
    layouts = []
    for _ in range(4):
        body = client.post("/api/presets/run", json={"preset": "hiring", "text": "Baristas in Dubai"}).get_json()
        layouts.append(body["preset"]["hiring"]["design"]["layout"])
    for i in range(1, 4):
        assert layouts[i] not in layouts[max(0, i - 3):i]
    assert [d["layout"] for d in db.recent_hiring_designs(user["id"])][:4] == layouts[::-1]


def test_all_sizes_adds_a_story(run):
    client, _, calls = run
    client.post("/api/presets/run", json={"preset": "hiring", "text": "Nurses in Abu Dhabi", "all_sizes": True})
    assert [i["aspect"] for i in calls["images"]] == ["1:1", "4:5", "9:16"]


def test_an_empty_brief_is_refused_before_anything_is_made(run):
    client, _, calls = run
    r = client.post("/api/presets/run", json={"preset": "hiring", "text": "  "})
    assert r.status_code == 400 and r.get_json()["code"] == "text_required" and not calls["images"]


def test_the_job_caption_can_be_edited_by_hand(run):
    client, _, _ = run
    body = client.post("/api/presets/run", json={"preset": "hiring", "text": "Delivery drivers in Pune"}).get_json()
    r = client.post(f"/api/history/{body['run_id']}/edit", json={"target": "carousel", "caption": "Join our Pune team as a driver."})
    assert r.status_code == 200, r.get_json()
    hiring = db.get_run_by_id(body["run_id"])["content"]["_preset"]["hiring"]
    assert hiring["caption"] == "Join our Pune team as a driver." and hiring["original"]["caption"].startswith("We're hiring")
    assert json.dumps(hiring["design"])
