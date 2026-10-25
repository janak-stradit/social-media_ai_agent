"""Images and videos made in the background (services/media_jobs.py): the
request returns a job id at once and the page checks /api/media-jobs/<id>.
The image model is faked."""

import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

import db
from services import media_jobs


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def fake_image(monkeypatch):
    from api import routes

    state = {"fail": False, "calls": 0}

    def generate_image(
        prompt, platform, tone, image_path=None, ai_model="kie", logo_path=None, square=False, model=None
    ):
        state["calls"] += 1
        if state["fail"]:
            return {"success": False, "error": "provider down"}
        return {
            "success": True,
            "type": "image",
            "url": "/static/uploads/bg.jpg",
            "prompt": prompt,
            "cost": 0.04,
            "size": "1024x1024",
        }

    monkeypatch.setattr(routes.media_service, "generate_image", generate_image)
    return state


def _client(app):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "h", credit_limit=10.0)
    db.set_user_image_access(user["id"], db.IMAGE_UNLIMITED, None)
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return user, client


def _wait(client, job_id, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        job = client.get(f"/api/media-jobs/{job_id}").get_json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


BODY = {"platform": "linkedin", "caption": "Live truck tracking is here", "media_type": "image", "background": True}


def test_a_background_image_returns_at_once_and_finishes(app, fake_image):
    _, client = _client(app)
    r = client.post("/api/generate-media", json=BODY)
    assert r.status_code == 202 and r.get_json()["status"] == "running"
    job = _wait(client, r.get_json()["job_id"])
    assert job["status"] == "done" and job["result"]["url"] == "/static/uploads/bg.jpg"


def test_a_failed_render_is_reported(app, fake_image):
    fake_image["fail"] = True
    _, client = _client(app)
    job = _wait(client, client.post("/api/generate-media", json=BODY).get_json()["job_id"])
    assert job["status"] == "failed" and job["error"] == "provider down"


def test_someone_elses_job_cannot_be_read(app, fake_image):
    _, mine = _client(app)
    _, theirs = _client(app)
    job_id = mine.post("/api/generate-media", json=BODY).get_json()["job_id"]
    assert theirs.get(f"/api/media-jobs/{job_id}").status_code == 404


def test_an_interrupted_job_is_reported_as_failed(app):
    user, client = _client(app)
    with Session(db.engine) as s:
        s.add(
            db.MediaJob(
                id="stale" + uuid.uuid4().hex[:20],
                user_id=user["id"],
                media_type="image",
                platform="linkedin",
                status="running",
                created_at=media_jobs._now() - timedelta(hours=1),
                updated_at=media_jobs._now(),
            )
        )
        s.commit()
        job_id = s.query(db.MediaJob.id).filter(db.MediaJob.user_id == user["id"]).first()[0]
    job = client.get(f"/api/media-jobs/{job_id}").get_json()
    assert job["status"] == "failed" and "interrupted" in job["error"]


def test_too_many_at_once_is_refused(app, fake_image, monkeypatch):
    monkeypatch.setattr(media_jobs, "MAX_RUNNING_PER_USER", 0)
    _, client = _client(app)
    r = client.post("/api/generate-media", json=BODY)
    assert r.status_code == 429 and r.get_json()["code"] == "too_many_jobs"


def test_without_background_it_still_answers_directly(app, fake_image):
    _, client = _client(app)
    body = client.post("/api/generate-media", json={**BODY, "background": False}).get_json()
    assert body["success"] and body["url"] == "/static/uploads/bg.jpg"
