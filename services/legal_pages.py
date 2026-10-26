"""Legal pages (/legal, /terms, /privacy, ...): one Markdown file per page in
legal/, rendered with the Help Center's safe Markdown subset
(services/help_center.render), public - no login.

Each file starts with a header:

    ---
    title: Terms of Service
    path: /terms
    summary: One sentence shown on /legal.
    order: 1
    ---

Company details come from Config (LEGAL_*), filled into {placeholders} in
the text: {company}, {address}, {email}, {grievance_name}, {grievance_email},
{jurisdiction}, {site}, {product} and {updated} (LEGAL_UPDATED - change it,
and LEGAL_VERSION, whenever a policy changes; sign-ups record the version
they agreed to).

These texts are a starting point written for how this application works.
Have a lawyer review them for every market you sell in before relying on them.
"""

import os
import re
from functools import lru_cache

from config import Config
from services.help_center import render

LEGAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "legal")
_SLUG = re.compile(r"^[a-z0-9-]+$")
_FIELD = re.compile(r"\{([a-z_]+)\}")


def _fields() -> dict:
    return {
        "product": "AVIR AI",
        "company": Config.LEGAL_COMPANY_NAME,
        "address": Config.LEGAL_ADDRESS or f"{Config.INVOICE_SELLER_STATE}, India",
        "email": Config.LEGAL_EMAIL,
        "grievance_name": Config.LEGAL_GRIEVANCE_OFFICER,
        "grievance_email": Config.LEGAL_GRIEVANCE_EMAIL or Config.LEGAL_EMAIL,
        "jurisdiction": Config.LEGAL_JURISDICTION,
        "site": (Config.APP_BASE_URL or "").rstrip("/") or "this website",
        "updated": Config.LEGAL_UPDATED,
    }


def _parse(raw: str, slug: str, fields: dict) -> dict:
    meta, body = {}, raw
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw.replace("\r\n", "\n"), re.DOTALL)
    if match:
        for line in match.group(1).split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
        body = match.group(2)
    # unknown {names} are left as they are
    body = _FIELD.sub(lambda m: str(fields.get(m.group(1), m.group(0))), body)
    html_body, toc = render(body)
    return {
        "slug": slug,
        "title": meta.get("title") or slug.replace("-", " ").capitalize(),
        "path": meta.get("path") or f"/legal/{slug}",
        "summary": meta.get("summary", ""),
        "order": int(meta.get("order") or 99),
        "html": html_body,
        "toc": toc,
    }


@lru_cache(maxsize=1)
def _load(stamp: float, fields_key: tuple) -> tuple[dict, ...]:
    fields = dict(fields_key)
    pages = []
    for name in sorted(os.listdir(LEGAL_DIR)) if os.path.isdir(LEGAL_DIR) else []:
        slug = name[:-3]
        if name.endswith(".md") and _SLUG.match(slug):
            with open(os.path.join(LEGAL_DIR, name), encoding="utf-8") as f:
                pages.append(_parse(f.read(), slug, fields))
    return tuple(sorted(pages, key=lambda p: (p["order"], p["title"])))


def pages() -> list[dict]:
    """Every legal page, in order (re-read when a file in legal/ changes)."""
    try:
        stamp = max((os.path.getmtime(os.path.join(LEGAL_DIR, n)) for n in os.listdir(LEGAL_DIR)), default=0.0)
    except OSError:
        stamp = 0.0
    return list(_load(stamp, tuple(sorted(_fields().items()))))


def page(slug: str) -> dict | None:
    if not _SLUG.match(slug or ""):
        return None
    return next((p for p in pages() if p["slug"] == slug), None)
