"""HeyRoute image replies: streamed (SSE) in the formats different models use,
plain JSON, and readable errors instead of Cloudflare HTML pages."""

import json

import pytest

from services.media_service import MediaGenerationService


class Resp:
    def __init__(self, text, content_type="text/event-stream", status=200):
        self.text, self.status_code = text, status
        self.ok = status < 400
        self.headers = {"Content-Type": content_type}

    def json(self):
        return json.loads(self.text)


payload = MediaGenerationService._heyroute_image_payload


def test_heyroute_completed_event():
    body = 'event: completed\ndata: {"data": [{"b64_json": "AAA"}]}\n\n'
    assert payload(Resp(body))["data"][0]["b64_json"] == "AAA"


def test_openai_style_stream_skips_partial_images():
    body = (
        'event: image_generation.partial_image\ndata: {"type": "image_generation.partial_image", "b64_json": "PARTIAL"}\n\n'
        'event: image_generation.completed\ndata: {"type": "image_generation.completed", "b64_json": "FINAL"}\n\n'
        "data: [DONE]\n"
    )
    assert payload(Resp(body))["data"][0]["b64_json"] == "FINAL"


def test_stream_without_event_names_and_wrong_content_type():
    body = 'data: {"data": [{"url": "https://x/img.png"}]}\n\ndata: [DONE]\n'
    assert payload(Resp(body, content_type="text/plain"))["data"][0]["url"] == "https://x/img.png"


def test_plain_json_reply():
    assert payload(Resp('{"data": [{"b64_json": "J"}]}', content_type="application/json"))["data"][0]["b64_json"] == "J"


def test_error_event_raises():
    with pytest.raises(RuntimeError, match="HeyRoute image error"):
        payload(Resp('event: error\ndata: {"error": {"message": "content policy"}}\n\n'))


def test_stream_with_only_partials_is_an_error():
    body = 'event: image_generation.partial_image\ndata: {"type": "image_generation.partial_image", "b64_json": "P"}\n\n'
    with pytest.raises(RuntimeError, match="without an image"):
        payload(Resp(body))


@pytest.mark.parametrize("status, expected", [
    (524, "took too long"), (504, "took too long"), (503, "temporarily unavailable"), (502, "temporarily unavailable"),
])
def test_cloudflare_pages_become_readable(status, expected):
    html = "<!DOCTYPE html> <!--[if lt IE 7]> <html class=\"no-js ie6 oldie\" lang=\"en-US\"> ..."
    msg = MediaGenerationService._heyroute_error_text(Resp(html, "text/html", status))
    assert expected in msg and "<html" not in msg and str(status) in msg


def test_other_html_error_is_summarised():
    msg = MediaGenerationService._heyroute_error_text(Resp("<html><body>Bad gateway</body></html>", "text/html", 418))
    assert msg == "HeyRoute image request failed (418)."


# ── Reference images and rejected requests (400 invalid_request) ────────────

INVALID = ('{"error":{"message":"The request was rejected as invalid. Please check the request parameters. '
           '(code: invalid_request, request id: 2026100414512515752912626248298vtMqOVdb)",'
           '"type":"upstream_error","param":"","code":"invalid_request"}}')


def _image(path, fmt, size=(64, 48), mode="RGB"):
    from PIL import Image

    Image.new(mode, size, (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30)).save(path, format=fmt)
    return str(path)


def test_jpeg_named_png_is_sent_as_jpeg(tmp_path):
    from services.media_service import _reference_upload

    name, data, mime = _reference_upload(_image(tmp_path / "gen_linkedin_x_clean.png", "JPEG"))
    assert (name, mime) == ("gen_linkedin_x_clean.jpg", "image/jpeg") and data.startswith(b"\xff\xd8")


def test_good_png_is_sent_unchanged(tmp_path):
    from services.media_service import _reference_upload

    path = _image(tmp_path / "a.png", "PNG")
    assert _reference_upload(path) == ("a.png", open(path, "rb").read(), "image/png")


@pytest.mark.parametrize("fmt, mode, expected_mime", [
    ("WEBP", "RGB", "image/jpeg"), ("GIF", "RGB", "image/jpeg"), ("WEBP", "RGBA", "image/png"),
])
def test_other_formats_are_converted(tmp_path, fmt, mode, expected_mime):
    import io

    from PIL import Image

    from services.media_service import _reference_upload

    name, data, mime = _reference_upload(_image(tmp_path / f"up.{fmt.lower()}", fmt, mode=mode))
    assert mime == expected_mime and Image.open(io.BytesIO(data)).format == expected_mime.split("/")[1].upper()


def test_large_reference_is_downscaled(tmp_path):
    import io

    from PIL import Image

    from services.media_service import _REFERENCE_MAX_SIDE, _reference_upload

    _, data, _ = _reference_upload(_image(tmp_path / "big.jpg", "JPEG", size=(4000, 3000)))
    assert max(Image.open(io.BytesIO(data)).size) == _REFERENCE_MAX_SIDE


def test_saved_images_get_their_real_extension():
    from services.media_service import _image_extension

    assert _image_extension(b"\xff\xd8\xff\xe0rest") == ".jpg"
    assert _image_extension(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ".webp"
    assert _image_extension(b"\x89PNG\r\n\x1a\n....") == ".png"


def test_invalid_request_message_is_readable():
    msg = MediaGenerationService._heyroute_error_text(Resp(INVALID, "application/json", 400))
    assert "rejected this request" in msg and "2026100414512515752912626248298vtMqOVdb" in msg and "{" not in msg


def test_model_that_rejects_the_request_falls_back_to_default(monkeypatch, tmp_path):
    import base64

    import services.media_service as ms

    png = open(_image(tmp_path / "out.png", "PNG"), "rb").read()
    calls = []

    def fake_post(url, headers=None, json=None, data=None, files=None, timeout=None):
        body = json or data
        calls.append((body["model"], "stream" in body, [f[1][0] for f in files or []]))
        if body["model"] == "gpt-image-2":
            return Resp(INVALID, "application/json", 400)
        return Resp('{"data": [{"b64_json": "%s"}]}' % base64.b64encode(png).decode(), "application/json")

    monkeypatch.setattr(ms.requests, "post", fake_post)
    monkeypatch.setattr(ms.Config, "HEYROUTE_IMAGE_API_KEY", "k")
    monkeypatch.setattr(ms.Config, "HEYROUTE_IMAGE_MODEL", "gemini-3.1-flash-image")
    monkeypatch.setattr(ms.Config, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(ms, "_image_price", lambda model: 0.5)
    svc = MediaGenerationService.__new__(MediaGenerationService)
    svc.upload_folder = str(tmp_path)
    ref = _image(tmp_path / "ref_clean.png", "JPEG")

    result = svc._generate_image_heyroute("a red square", "instagram", "1024x1024", image_path=ref, model="gpt-image-2")
    assert result["model_id"] == "gemini-3.1-flash-image"
    # streamed try, plain retry, then the default model - each with the JPEG labelled as JPEG
    assert [c[:2] for c in calls] == [("gpt-image-2", True), ("gpt-image-2", False), ("gemini-3.1-flash-image", True)]
    assert all(c[2] == ["ref_clean.jpg"] for c in calls)
