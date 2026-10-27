"""Turn a link or a PDF into posts: "repurpose" existing content (a blog
post, product page, brochure, report) instead of starting from a blank brief.

    read_url(url)   -> {title, text, source}  one web page, fetched with the
                       website reader's SSRF checks (website_scraper_service)
    read_pdf(file)  -> {title, text, source}  the PDF's text (pypdf); scanned
                       PDFs without a text layer can't be read
    read_text(text) -> {title, text, source}  text the user pasted (for pages
                       that block reading, or a scanned PDF)
    to_angles(doc)  -> {summary, angles: [{title, brief, format}], usage}
                       one AI call: 3-5 distinct post ideas, each a complete
                       brief the composer can generate from

The composer's "From a link or PDF" window (static/js/app_v2.js) calls
POST /api/repurpose and loads the chosen angle into the brief.
"""

import io
import logging

from services.llm_service import LLMService

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 12000  # what the AI reads: the start of a long document
MIN_TEXT_CHARS = 200
MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 40
FORMATS = ("text", "image", "carousel")

URL_ERRORS = {
    "invalid_url": "That doesn't look like a web address. Paste the full link, starting with https://",
    "unsafe_url": "That address can't be read from here.",
    "unreachable": "Couldn't reach that page. Check the link and try again.",
    "timeout": "That page took too long to answer. Try again in a moment.",
    "blocked": "That website blocks automated reading. Copy the text into the brief instead.",
    "http_error": "That page didn't open (it may be private or removed).",
    "not_html": "That link isn't a web page. For a PDF, upload the file instead.",
    "thin_content": "That page has almost no readable text (it may need JavaScript). Copy the text into the brief instead.",
}


# What the window offers when a source can't be read: paste its text, or
# upload it as a PDF (static/js/app_v2.js shows the matching button)
FALLBACKS = {"blocked": "text", "thin_content": "text", "not_html": "pdf", "scanned_pdf": "text"}


class RepurposeError(ValueError):
    """Why the content couldn't be used, in words for the user. `code` names
    the reason; `fallback` ("text" | "pdf" | None) is the other way to try."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code
        self.fallback = FALLBACKS.get(code or "")


def read_url(url: str) -> dict:
    from services import website_scraper_service as web

    url = (url or "").strip()
    if not url:
        raise RepurposeError(URL_ERRORS["invalid_url"])
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    fetched, reason = web._fetch_raw(url, require_html=True)  # pylint: disable=protected-access
    if not fetched:
        code = reason if reason in URL_ERRORS else "unreachable"
        raise RepurposeError(URL_ERRORS[code], code)
    final_url, html = fetched
    page = web._parse(html)  # pylint: disable=protected-access
    text = page.text(MAX_TEXT_CHARS * 2)
    if len(text) < MIN_TEXT_CHARS:
        raise RepurposeError(URL_ERRORS["thin_content"], "thin_content")
    title = page.meta("og:title") or page.title() or final_url
    return {"title": title.strip()[:200], "text": text[:MAX_TEXT_CHARS], "source": final_url}


def read_pdf(data: bytes, filename: str = "document.pdf") -> dict:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if not data or not data.startswith(b"%PDF"):
        raise RepurposeError("That file isn't a PDF.")
    if len(data) > MAX_PDF_BYTES:
        raise RepurposeError("That PDF is larger than 15 MB. Upload a shorter version.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise RepurposeError("That PDF is password-protected. Upload a copy without a password.")
        parts, size = [], 0
        for page in reader.pages[:MAX_PDF_PAGES]:
            chunk = (page.extract_text() or "").strip()
            parts.append(chunk)
            size += len(chunk)
            if size >= MAX_TEXT_CHARS:
                break
        title = ((reader.metadata or {}).get("/Title") or "").strip() if reader.metadata else ""
    except RepurposeError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as err:
        logger.info(f"Unreadable PDF {filename}: {err}")
        raise RepurposeError("That PDF couldn't be read. It may be damaged.") from err
    text = " ".join(" ".join(parts).split())
    if len(text) < MIN_TEXT_CHARS:
        raise RepurposeError(
            "That PDF has no readable text (it may be scanned images). Paste its text instead.", "scanned_pdf"
        )
    name = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
    return {"title": (title or name or "Your PDF")[:200], "text": text[:MAX_TEXT_CHARS], "source": filename}


def read_text(text: str) -> dict:
    """Text the user pasted. The title is its first line, shortened."""
    text = (text or "").replace("\r\n", "\n").strip()
    if len(text) < MIN_TEXT_CHARS:
        raise RepurposeError(
            f"Paste a bit more text - at least {MIN_TEXT_CHARS} characters, so there is something to work from.",
            "too_short",
        )
    first = " ".join(text.split("\n", 1)[0].split())
    title = first if len(first) <= 80 else first[:77].rsplit(" ", 1)[0] + "..."
    return {"title": title or "Your text", "text": " ".join(text.split())[:MAX_TEXT_CHARS], "source": "Pasted text"}


SYSTEM_PROMPT = """You turn existing content (a web page or a document) into social media post ideas for the business
that owns it.

Read the content and return JSON:
{
  "summary": "two sentences: what the content is about and who it is for",
  "angles": [
    {"title": "a short name for the post idea (max 8 words)",
     "brief": "a complete brief for a social media writer: the angle, the key facts, numbers and names to use FROM THE
               CONTENT, the audience, and what the reader should take away. 2-4 sentences.",
     "format": "text | image | carousel"}
  ]
}

Rules:
- 3 to 5 angles, each clearly different (e.g. a key insight, a how-to, a surprising fact, a customer story, a
  checklist). Use "carousel" for step-by-step or list content, "image" when one strong visual or number carries it.
- Use only facts that are in the content. Never invent numbers, prices, quotes or claims.
- Keep product, brand and people's names exactly as written.
- Write the summary and briefs in the same language as the content.
- Ignore navigation, cookie banners, footers and ads."""


def to_angles(doc: dict, llm: LLMService | None = None) -> dict:
    llm = llm or LLMService()
    user = f"Title: {doc.get('title') or ''}\nSource: {doc.get('source') or ''}\n\nCONTENT:\n{doc['text'][:MAX_TEXT_CHARS]}"
    result, usage = llm.generate_json(SYSTEM_PROMPT, user, return_usage=True, reasoning_effort="low")
    result = result if isinstance(result, dict) else {}
    angles = []
    for angle in result.get("angles") or []:
        if not isinstance(angle, dict) or not str(angle.get("brief") or "").strip():
            continue
        fmt = str(angle.get("format") or "text").lower()
        angles.append(
            {
                "title": str(angle.get("title") or "Post idea").strip()[:120],
                "brief": str(angle["brief"]).strip()[:1500],
                "format": fmt if fmt in FORMATS else "text",
            }
        )
    if not angles:
        raise RepurposeError("Couldn't find post ideas in that content. Try another link or file.")
    return {"summary": str(result.get("summary") or "").strip()[:600], "angles": angles[:5], "usage": usage or {}}
