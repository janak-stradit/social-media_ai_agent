"""Caching: versioned static links + browser cache headers, weekly logo
refresh, and clean-up of the photo-analysis cache."""

import os
import re
import time

import pytest


@pytest.fixture
def client():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    return app, app.test_client()


# ── 1. Static files ─────────────────────────────────────────────────────────


def test_static_links_carry_a_content_fingerprint(client, tmp_path):
    import app as app_module

    app, _ = client
    with app.test_request_context():
        from flask import url_for

        url = url_for("static", filename="css/style.css")
    assert re.search(r"/static/css/style\.css\?v=[0-9a-f]{10}$", url)

    # the fingerprint follows the file's content
    f = tmp_path / "x.css"
    f.write_text("a{}")
    first = app_module._static_fingerprint(str(tmp_path), "x.css")
    f.write_text("a{color:red}")
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert app_module._static_fingerprint(str(tmp_path), "x.css") != first
    assert app_module._static_fingerprint(str(tmp_path), "missing.css") is None


def test_cache_headers_by_kind_of_file(client):
    from config import Config

    _, c = client
    versioned = c.get("/static/css/style.css?v=abc1234567")
    assert versioned.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    plain = c.get("/static/css/style.css")
    assert "31536000" not in plain.headers.get("Cache-Control", "")  # unversioned: always revalidated

    os.makedirs(os.path.join(Config.UPLOAD_FOLDER, "brand_logos"), exist_ok=True)
    gen = os.path.join(Config.UPLOAD_FOLDER, "gen_test_cache.jpg")
    logo = os.path.join(Config.UPLOAD_FOLDER, "brand_logos", "test_cache_logo.png")
    for path in (gen, logo):
        with open(path, "wb") as f:
            f.write(b"x")
    try:
        assert c.get("/static/uploads/gen_test_cache.jpg").headers["Cache-Control"] == "public, max-age=2592000"
        assert "2592000" not in c.get("/static/uploads/brand_logos/test_cache_logo.png").headers.get("Cache-Control", "")
    finally:
        os.remove(gen)
        os.remove(logo)


def test_pages_link_versioned_assets(client):
    import db

    app, c = client
    user = db.create_user("Cache", f"cache-{time.time_ns()}@example.com", "h", is_admin=True)
    with c.session_transaction() as s:
        s["user_id"] = user["id"]
    html = c.get("/dashboard").get_data(as_text=True)
    assert re.search(r'/static/css/style\.css\?v=[0-9a-f]{10}"', html)
    assert re.search(r'/static/js/app_v2\.js\?v=[0-9a-f]{10}"', html)


# ── 2. Brand logos ─────────────────────────────────────────────────────────


@pytest.fixture
def logos(monkeypatch, tmp_path):
    import services.brand_logo_service as bls

    monkeypatch.setattr(bls, "_CACHE_DIR", str(tmp_path))
    fetches = []
    state = {"data": b"PNG-v1"}
    monkeypatch.setattr(bls, "_fetch_logo_bytes", lambda url: fetches.append(url) or state["data"])
    monkeypatch.setattr(bls, "_looks_like_svg", lambda url, data: False)
    monkeypatch.setattr(bls, "_normalize_raster", lambda data: data)
    return bls, fetches, state


def test_logo_is_fetched_once_while_fresh(logos):
    bls, fetches, _ = logos
    path = bls._cached_logo("https://acme.test/logo.png")
    assert bls._cached_logo("https://acme.test/logo.png") == path and len(fetches) == 1


def test_old_logo_is_refreshed(logos):
    bls, fetches, state = logos
    path = bls._cached_logo("https://acme.test/logo.png")
    old = time.time() - bls.LOGO_REFRESH_SECONDS - 60
    os.utime(path, (old, old))
    state["data"] = b"PNG-v2"  # the company changed its logo at the same address
    bls._cached_logo("https://acme.test/logo.png")
    assert len(fetches) == 2 and open(path, "rb").read() == b"PNG-v2"


def test_failed_refresh_keeps_the_last_good_logo(logos):
    bls, fetches, state = logos
    path = bls._cached_logo("https://acme.test/logo.png")
    old = time.time() - bls.LOGO_REFRESH_SECONDS - 60
    os.utime(path, (old, old))
    state["data"] = None  # site down
    assert bls._cached_logo("https://acme.test/logo.png") == path and open(path, "rb").read() == b"PNG-v1"
    bls._cached_logo("https://acme.test/logo.png")
    assert len(fetches) == 2  # not retried on every image - waits another period


def test_rescan_forgets_the_cached_logo(logos):
    bls, fetches, _ = logos
    path = bls._cached_logo("https://acme.test/logo.png")
    bls.forget_logo("https://acme.test/logo.png")
    assert not os.path.exists(path)
    bls._cached_logo("https://acme.test/logo.png")
    assert len(fetches) == 2


# ── 3. Photo-analysis cache clean-up ────────────────────────────────────────


def test_old_analyses_and_leftovers_are_removed(monkeypatch, tmp_path):
    from agents.vision_agent import VisionAgent

    monkeypatch.setattr(VisionAgent, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(VisionAgent, "_last_prune", 0.0)
    now = time.time()
    files = {"old.json": now - 8 * 86400, "recent.json": now - 86400, "x.json.pending": now - 7200, "y.json.1.tmp": now - 60}
    for name, mtime in files.items():
        (tmp_path / name).write_text("{}")
        os.utime(tmp_path / name, (mtime, mtime))
    VisionAgent._prune_cache()
    assert sorted(os.listdir(tmp_path)) == ["recent.json", "y.json.1.tmp"]
    (tmp_path / "old2.json").write_text("{}")
    os.utime(tmp_path / "old2.json", (now - 9 * 86400,) * 2)
    VisionAgent._prune_cache()  # within the hour: skipped
    assert "old2.json" in os.listdir(tmp_path)
