"""Start from a link or PDF (services/repurpose_service.py): the page or
document is read and the AI suggests post angles. The web and the AI are faked."""

import io
import uuid

import pytest

from services import repurpose_service as repurpose

ARTICLE = "Live truck tracking cut late deliveries by 32 percent for Acme Logistics customers in Pune. " * 6


def _pdf(text: str, title: str = "") -> bytes:
    """A one-page PDF with real text (enough for pypdf to extract)."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    info = f"<< /Title ({title}) >>".encode() if title else b"<< >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        info,
    ]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


class FakeLLM:
    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer or {
            "summary": "How live tracking reduced late deliveries.",
            "angles": [
                {
                    "title": "The 32% result",
                    "brief": "Share that late deliveries fell 32 percent in Pune.",
                    "format": "image",
                },
                {"title": "How it works", "brief": "Explain live tracking in three steps.", "format": "carousel"},
                {"title": "Bad", "brief": "", "format": "text"},
                {"title": "Odd format", "brief": "A customer story.", "format": "reel"},
            ],
        }

    def generate_json(self, system, user, **kw):
        self.calls.append((system, user, kw))
        return self.answer, {"total_tokens": 900}


def test_a_web_page_is_read_with_its_title(monkeypatch):
    from services import website_scraper_service as web

    html = f"<html><head><title>Blog</title><meta property='og:title' content='Live tracking results'></head><body><nav>Menu</nav><article><p>{ARTICLE}</p></article></body></html>"
    seen = {}
    monkeypatch.setattr(
        web, "_fetch_raw", lambda url, **kw: seen.setdefault("url", url) and (("https://acme.com/blog/x", html), None)
    )
    doc = repurpose.read_url("acme.com/blog/x")
    assert seen["url"] == "https://acme.com/blog/x" and doc["title"] == "Live tracking results"
    assert "32 percent" in doc["text"] and doc["source"] == "https://acme.com/blog/x"


@pytest.mark.parametrize(
    "reason,words", [("unsafe_url", "can't be read"), ("blocked", "blocks automated"), ("not_html", "upload the file")]
)
def test_unreadable_pages_say_why(monkeypatch, reason, words):
    from services import website_scraper_service as web

    monkeypatch.setattr(web, "_fetch_raw", lambda url, **kw: (None, reason))
    with pytest.raises(repurpose.RepurposeError, match=words):
        repurpose.read_url("https://example.com")


def test_a_pdf_is_read():
    doc = repurpose.read_pdf(_pdf(ARTICLE[:400], title="Tracking report"), "tracking_report.pdf")
    assert doc["title"] == "Tracking report" and "late deliveries" in doc["text"]
    named = repurpose.read_pdf(_pdf(ARTICLE[:400]), "q3-results.pdf")
    assert named["title"] == "q3 results"  # no title inside: the file name


def test_bad_pdfs_say_why():
    with pytest.raises(repurpose.RepurposeError, match="isn't a PDF"):
        repurpose.read_pdf(b"hello", "x.pdf")
    with pytest.raises(repurpose.RepurposeError, match="no readable text"):
        repurpose.read_pdf(_pdf("Hi"), "scan.pdf")


def test_angles_keep_only_usable_ideas():
    llm = FakeLLM()
    result = repurpose.to_angles({"title": "T", "source": "s", "text": ARTICLE}, llm=llm)
    assert [a["title"] for a in result["angles"]] == ["The 32% result", "How it works", "Odd format"]
    assert result["angles"][2]["format"] == "text"  # unknown formats become text
    assert "Never invent numbers" in llm.calls[0][0] and ARTICLE[:50] in llm.calls[0][1]
    with pytest.raises(repurpose.RepurposeError, match="Couldn't find post ideas"):
        repurpose.to_angles({"title": "T", "source": "s", "text": ARTICLE}, llm=FakeLLM({"angles": []}))


@pytest.fixture
def client(monkeypatch):
    import db
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    user = db.create_user("Priya", f"{uuid.uuid4().hex[:8]}@example.com", "h")
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = user["id"]
    monkeypatch.setattr(repurpose, "LLMService", FakeLLM)
    return c


def test_the_route_reads_a_link_or_a_pdf(client, monkeypatch):
    monkeypatch.setattr(repurpose, "read_url", lambda url: {"title": "Blog", "text": ARTICLE, "source": url})
    body = client.post("/api/repurpose", json={"url": "https://acme.com/a"}).get_json()
    assert body["success"] and len(body["angles"]) == 3 and "usage" not in body

    upload = {"file": (io.BytesIO(_pdf(ARTICLE[:400])), "report.pdf")}
    body = client.post("/api/repurpose", data=upload, content_type="multipart/form-data").get_json()
    assert body["success"] and body["source"] == "report.pdf"

    bad = client.post(
        "/api/repurpose", data={"file": (io.BytesIO(b"nope"), "x.pdf")}, content_type="multipart/form-data"
    )
    assert bad.status_code == 400 and "isn't a PDF" in bad.get_json()["error"]


def test_signed_out_visitors_cannot_use_it():
    from app import create_app

    app = create_app("development")
    app.config.update(TESTING=True)
    assert app.test_client().post("/api/repurpose", json={"url": "https://acme.com"}).status_code == 401


def test_pasted_text_is_read_with_its_first_line_as_title():
    doc = repurpose.read_text("Acme cuts late deliveries\n" + ARTICLE)
    assert doc["title"] == "Acme cuts late deliveries" and doc["source"] == "Pasted text"
    assert "32 percent" in doc["text"] and "\n" not in doc["text"]
    long_first = repurpose.read_text("word " * 60 + "\n" + ARTICLE)["title"]
    assert long_first.endswith("...") and len(long_first) <= 80
    with pytest.raises(repurpose.RepurposeError, match="at least 200"):
        repurpose.read_text("Too short")


@pytest.mark.parametrize("reason,fallback", [("blocked", "text"), ("thin_content", "text"), ("not_html", "pdf"),
                                              ("unsafe_url", None)])
def test_failures_name_another_way_to_try(monkeypatch, reason, fallback):
    from services import website_scraper_service as web

    monkeypatch.setattr(web, "_fetch_raw", lambda url, **kw: (None, reason))
    with pytest.raises(repurpose.RepurposeError) as err:
        repurpose.read_url("https://example.com")
    assert err.value.code == reason and err.value.fallback == fallback
    with pytest.raises(repurpose.RepurposeError) as scanned:
        repurpose.read_pdf(_pdf("Hi"), "scan.pdf")
    assert scanned.value.fallback == "text"


def test_the_route_reads_pasted_text_and_reports_fallbacks(client, monkeypatch):
    body = client.post("/api/repurpose", json={"text": "Tracking results\n" + ARTICLE}).get_json()
    assert body["success"] and body["kind"] == "text" and body["title"] == "Tracking results"

    def blocked(url):
        raise repurpose.RepurposeError(repurpose.URL_ERRORS["blocked"], "blocked")

    monkeypatch.setattr(repurpose, "read_url", blocked)
    res = client.post("/api/repurpose", json={"url": "https://acme.com/a"})
    assert res.status_code == 400 and res.get_json()["fallback"] == "text" and res.get_json()["code"] == "blocked"
    assert client.post("/api/repurpose", json={"text": "short"}).get_json()["code"] == "too_short"


def test_studio_chat_has_the_new_window_and_starting_card(client):
    import db
    from sqlalchemy.orm import Session

    with client.session_transaction() as s:
        user_id = s["user_id"]
    with Session(db.engine) as session:  # a fully set-up account, so Studio Chat opens
        session.query(db.User).filter(db.User.id == user_id).update({"email_verified": True, "onboarding_completed": True})
        session.commit()
    html = client.get("/dashboard").data
    for marker in (b'id="rpStartCard"', b'data-rp-tab="link"', b'data-rp-tab="pdf"', b'data-rp-tab="text"',
                   b'id="rpDrop"', b'id="rpCancel"', b'id="rpFallback"', b'id="rpIdeaList"'):
        assert marker in html, marker
