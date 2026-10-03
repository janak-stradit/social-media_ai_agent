"""HeyRoute image replies: streamed (SSE) in the formats different models use,
plain JSON, and readable errors instead of Cloudflare HTML pages."""

import json

import pytest

from services.media_service import MediaGenerationService


class Resp:
    def __init__(self, text, content_type="text/event-stream", status=200):
        self.text, self.status_code = text, status
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
