"""Video generation: the prompt sent to the model, what a video costs, the
daily video limit, and mock mode. HeyRoute itself is faked - nothing is billed.
"""

import os
import uuid

import pytest

import db
from config import Config


@pytest.fixture
def app():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def routes():
    from api import routes

    assert routes.DB_AVAILABLE and routes.MEDIA_AVAILABLE
    return routes


@pytest.fixture
def service(routes, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.media_service, "upload_folder", str(tmp_path))
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    return routes.media_service


def _user(app, is_admin=False, video=True):
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:10]}@example.com", "hash", is_admin=is_admin)
    if video:  # an admin switched video on for this user
        db.set_user_video_access(user["id"], True)
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user["id"]
    return user, client


# ── Prompt ──────────────────────────────────────────────────────────────────

SCENE = "A freight truck drives along a desert highway at sunset, slow tracking shot"


def test_prompt_keeps_the_brief_scene(service):
    prompt = service._build_video_prompt(SCENE, "linkedin")
    assert SCENE in prompt and "executive" not in prompt.lower()


def test_prompt_with_reference_image_still_uses_the_scene(service):
    prompt = service._build_video_prompt(SCENE, "instagram", has_reference_image=True)
    assert prompt.startswith("Start from the provided image") and SCENE in prompt


def test_long_prompt_is_cut_at_a_sentence(service):
    prompt = service._build_video_prompt("The truck drives on. " * 200, "facebook")
    scene = prompt.split(" Style:")[0]
    assert len(scene) <= service._VIDEO_PROMPT_LIMIT + 1 and scene.endswith("drives on.")


# ── HeyRoute request + cost ─────────────────────────────────────────────────


def test_grok_video_cost_is_seconds_times_rate(service, monkeypatch):
    sent = []
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_API_KEY", "key")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_MODEL", "grok-video")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_FALLBACK_MODEL", "")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_SECONDS", 8)
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_COST_PER_SECOND_USD", 0.05)
    monkeypatch.setattr(service, "_heyroute_video_task", lambda key, body: sent.append(body) or b"mp4")

    result = service._generate_video_heyroute(SCENE, "instagram")

    # grok-video: 6 / 10 / 15 s only, and no ratio / resolution / reference image
    assert sent == [{"model": "grok-video", "prompt": SCENE, "seconds": "6"}]
    assert result["duration"] == 6 and result["cost"] == pytest.approx(0.30)
    assert os.path.exists(os.path.join(service.upload_folder, os.path.basename(result["url"])))


def test_grok_imagine_gets_the_platform_ratio(service, monkeypatch):
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_SECONDS", 8)
    body = service._heyroute_video_body("grok-imagine-video-1.5", SCENE, "instagram", None)
    assert body["ratio"] == "9:16" and body["seconds"] == "8"


def test_minimax_body_has_one_reference_and_no_ratio(service, monkeypatch, tmp_path):
    from PIL import Image

    ref = tmp_path / "ref.jpg"
    Image.new("RGB", (8, 8)).save(ref)
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_SECONDS", 2)

    body = service._heyroute_video_body("minimax-h3-original-768p", SCENE, "instagram", str(ref))

    assert set(body) == {"model", "prompt", "seconds", "input_reference"}  # ratio / resolution do nothing here
    assert body["seconds"] == "4" and body["input_reference"].startswith("data:image/")  # 4 s minimum; not an array


def test_minimax_is_given_thirty_minutes(service, monkeypatch):
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_TIMEOUT", 900)
    assert service._heyroute_video_timeout("minimax-h3-original-768p") == 1800
    assert service._heyroute_video_timeout("grok-video") == 900


def test_minimax_uses_the_post_image_but_never_buys_a_keyframe(service, monkeypatch):
    monkeypatch.setattr(Config, "USE_MOCK_LLM", False)
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_API_KEY", "key")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_MODEL", "minimax-h3-original-768p")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_FALLBACK_MODEL", "")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_SECONDS", 4)
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_COST_PER_SECOND_USD", 0.70)
    sent = []
    monkeypatch.setattr(service, "_heyroute_video_task", lambda key, body: sent.append(body) or b"not a real mp4")
    monkeypatch.setattr(service, "generate_image", lambda *a, **k: pytest.fail("no keyframe for minimax"))
    monkeypatch.setattr(service, "_extract_speech_dialogue", lambda caption, max_seconds=None: "")

    result = service.generate_video(SCENE, "linkedin")

    assert result["success"] and result["cost"] == pytest.approx(2.80) and result["model"] == "minimax-h3-original-768p"
    assert "input_reference" not in sent[0]


# ── Length: the finished video is the generated clip, 15 s at most ──────────


def _ffmpeg(*args):
    import subprocess

    import imageio_ffmpeg

    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *args], check=True, timeout=60)


def test_long_voiceover_never_stretches_the_video(service, monkeypatch, tmp_path):
    from moviepy.video.io.VideoFileClip import VideoFileClip

    clip, speech = tmp_path / "clip.mp4", tmp_path / "speech.wav"
    _ffmpeg("-f", "lavfi", "-i", "color=c=0x1e293b:s=1376x768:d=4", "-pix_fmt", "yuv420p", str(clip))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=30", str(speech))  # far longer than the clip
    monkeypatch.setattr(Config, "USE_MOCK_LLM", False)
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_API_KEY", "key")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_MODEL", "minimax-h3-original-768p")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_FALLBACK_MODEL", "")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_SECONDS", 4)
    monkeypatch.setattr(Config, "VIDEO_TTS_FALLBACK", True)  # the clip below is silent
    monkeypatch.setattr(service, "_heyroute_video_task", lambda key, body: clip.read_bytes())
    asked = []
    monkeypatch.setattr(service, "_extract_speech_dialogue",
                        lambda caption, max_seconds=None: asked.append(max_seconds) or "Hello there.")
    monkeypatch.setattr(service, "_synthesize_voiceover", lambda text, tone: str(speech))

    result = service.generate_video(SCENE, "linkedin")

    assert result["success"] and asked[-1] == 4
    with VideoFileClip(service._resolve_image_path(result["url"])) as video:
        assert video.duration <= 4.2 and video.audio is not None


def _minimax(monkeypatch, service, clip_bytes, sent):
    monkeypatch.setattr(Config, "USE_MOCK_LLM", False)
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_API_KEY", "key")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_MODEL", "minimax-h3-original-768p")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_FALLBACK_MODEL", "")
    monkeypatch.setattr(Config, "HEYROUTE_VIDEO_SECONDS", 4)
    monkeypatch.setattr(service, "_heyroute_video_task", lambda key, body: sent.append(body) or clip_bytes)
    monkeypatch.setattr(service, "_extract_speech_dialogue", lambda caption, max_seconds=None: "Hello there.")
    monkeypatch.setattr(service, "_synthesize_voiceover", lambda text, tone: pytest.fail("no separate voiceover"))


def test_the_models_own_audio_is_kept_and_no_voiceover_is_made(service, monkeypatch, tmp_path):
    from moviepy.video.io.VideoFileClip import VideoFileClip

    clip, sent = tmp_path / "clip.mp4", []
    _ffmpeg("-f", "lavfi", "-i", "color=c=0x1e293b:s=1376x768:d=4", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=4", "-pix_fmt", "yuv420p", "-shortest", str(clip))
    _minimax(monkeypatch, service, clip.read_bytes(), sent)

    result = service.generate_video(SCENE, "linkedin")

    assert result["success"] and result["audio_mode"] == "single_pass_native"
    assert 'narrator says, in a relaxed conversational voice: "Hello there."' in sent[0]["prompt"]
    with VideoFileClip(service._resolve_image_path(result["url"])) as video:
        assert video.audio is not None and video.duration <= 4.2


def test_a_silent_clip_stays_silent_by_default(service, monkeypatch, tmp_path):
    from moviepy.video.io.VideoFileClip import VideoFileClip

    clip = tmp_path / "clip.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=0x1e293b:s=1376x768:d=4", "-pix_fmt", "yuv420p", str(clip))
    _minimax(monkeypatch, service, clip.read_bytes(), [])
    monkeypatch.setattr(Config, "VIDEO_TTS_FALLBACK", False)

    result = service.generate_video(SCENE, "linkedin")

    assert result["success"]
    with VideoFileClip(service._resolve_image_path(result["url"])) as video:
        assert video.audio is None


def test_voiceover_script_is_cut_to_what_fits_the_clip(service, monkeypatch):
    monkeypatch.setattr(service.llm_service, "generate", lambda **k: "One two three. " * 40)
    script = service._extract_speech_dialogue("Any caption", max_seconds=8)
    assert len(script.split()) <= 17 and script.endswith("three.")


# ── Mock mode ───────────────────────────────────────────────────────────────


def test_mock_video_is_a_real_mp4(service, monkeypatch):
    monkeypatch.setattr(Config, "USE_MOCK_LLM", True)
    result = service.generate_video(SCENE, "instagram")
    assert result["success"] and result["url"].endswith(".mp4") and result["resolution"] == "720x1280"
    assert service._video_resolution(result["url"]) == "720x1280"


# ── /api/generate-media: charge + daily limit ───────────────────────────────


@pytest.fixture
def fake_video(monkeypatch, routes):
    calls = []

    def generate_video(caption, platform, tone=None, image_path=None, logo_path=None):
        calls.append(caption)
        return {"success": True, "type": "video", "platform": platform, "url": f"/static/uploads/v{len(calls)}.mp4",
                "prompt": caption, "duration": 6, "resolution": "1280x720", "model": "grok-video", "cost": 0.6}

    monkeypatch.setattr(routes.media_service, "generate_video", generate_video)
    monkeypatch.setattr(routes, "log_event", lambda event, **f: None)
    return calls


def _make_video(client):
    return client.post("/api/generate-media", json={"platform": "linkedin", "caption": SCENE, "media_type": "video"})


def test_video_is_charged_to_the_user(app, fake_video):
    user, client = _user(app)
    res = _make_video(client).get_json()
    assert res["success"] and res["video_quota"]["used"] == 1
    assert db.get_user_usage_stats(user["id"])["used_credits"] == pytest.approx(0.6)


def test_daily_video_limit_blocks_the_next_video(app, fake_video):
    user, client = _user(app)
    for _ in range(db.VIDEO_LIMIT_DEFAULT):
        assert _make_video(client).get_json()["success"]

    blocked = _make_video(client)
    assert blocked.status_code == 403 and blocked.get_json()["code"] == "video_limit_reached"
    assert "video" in blocked.get_json()["error"] and len(fake_video) == db.VIDEO_LIMIT_DEFAULT
    assert db.get_image_quota(user["id"])["used"] == 0  # videos don't use up the image limit


def test_admin_has_no_video_limit(app, fake_video):
    _, client = _user(app, is_admin=True)
    for _ in range(db.VIDEO_LIMIT_DEFAULT + 1):
        assert _make_video(client).get_json()["success"]


# ── Video is "coming soon" until an admin switches it on for the user ───────


def test_video_is_off_for_a_new_user(app, fake_video):
    user, client = _user(app, video=False)

    blocked = _make_video(client)

    assert blocked.status_code == 403 and blocked.get_json()["code"] == "video_not_enabled"
    assert "coming soon" in blocked.get_json()["error"] and not fake_video
    assert client.get("/api/me/image-quota").get_json()["video_quota"]["enabled"] is False


def test_admin_switches_video_on_for_one_user(app, fake_video):
    user, client = _user(app, video=False)
    _, admin = _user(app, is_admin=True, video=False)

    saved = admin.put(f"/api/admin/users/{user['id']}/image-access", json={"limit": None, "model": None, "video_enabled": True})

    assert saved.get_json()["video_quota"]["enabled"] is True
    assert client.get("/api/me/image-quota").get_json()["video_quota"]["enabled"] is True
    assert _make_video(client).get_json()["success"] and len(fake_video) == 1
    summary = next(u for u in db.get_all_users_credit_summary() if u["id"] == user["id"])
    assert summary["images"]["video_enabled"] is True


def test_admin_always_has_video(app, fake_video):
    _, admin = _user(app, is_admin=True, video=False)
    assert _make_video(admin).get_json()["success"]


def test_a_user_cannot_switch_video_on_themselves(app):
    user, client = _user(app, video=False)
    res = client.put(f"/api/admin/users/{user['id']}/image-access", json={"limit": None, "model": None, "video_enabled": True})
    assert res.status_code in (401, 403) and db.get_video_quota(user["id"])["enabled"] is False
