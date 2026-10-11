"""Help Center (/help): the user guide, one Markdown file per article in help/.

Each file starts with a small header:

    ---
    title: Getting started
    summary: One sentence shown in the article list and search results.
    order: 1
    icon: fa-rocket
    ---

The body is plain Markdown (the subset below), so anyone can edit an article
without touching code. Rendering escapes all HTML first - an article can't
inject scripts or break the page.

Supported: # ## ### headings, paragraphs, - and 1. lists, **bold**, *italic*,
`code`, [links](/path), > tips (a "Tip:" / "Note:" first word sets the style),
and | tables |.
"""

import html
import os
import re
from functools import lru_cache

HELP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "help")
_SLUG = re.compile(r"^[a-z0-9-]+$")


def _inline(text: str) -> str:
    """Escaped text with inline formatting."""
    out = html.escape(text, quote=True)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![*\w])\*([^*\s][^*]*)\*(?![*\w])", r"<em>\1</em>", out)

    def link(m):
        label, url = m.group(1), html.unescape(m.group(2))
        if not (url.startswith(("/", "#", "https://", "mailto:"))):  # no javascript: or other schemes
            return label
        external = url.startswith("https://")
        return (f'<a href="{html.escape(url, quote=True)}"'
                + (' target="_blank" rel="noopener"' if external else "") + f">{label}</a>")

    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, out)


def _heading_id(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def render(markdown: str) -> tuple[str, list[dict]]:
    """(html, [{"id", "title", "level"}]) for an article body."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    out, toc, para, i = [], [], [], 0

    def flush():
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped:
            flush()
            i += 1
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if heading:
            flush()
            level, title = len(heading.group(1)), heading.group(2).strip()
            hid = _heading_id(title)
            if level > 1:
                toc.append({"id": hid, "title": title, "level": level})
            out.append(f'<h{level + 1} id="{hid}">{_inline(title)}</h{level + 1}>')
            i += 1
            continue
        if re.match(r"^([-*]|\d+\.)\s+", stripped):
            flush()
            ordered = bool(re.match(r"^\d+\.", stripped))
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]).strip())
                i += 1
                while i < len(lines) and lines[i].startswith("   ") and lines[i].strip():  # wrapped item text
                    items[-1] += " " + lines[i].strip()
                    i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + f"</{tag}>")
            continue
        if stripped.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            text = " ".join(q for q in quote if q)
            kind = "note" if text.lower().startswith("note") else "tip"
            out.append(f'<div class="help-callout help-{kind}">{_inline(text)}</div>')
            continue
        if stripped.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                head, body = rows[0], rows[1:]
                out.append('<div class="help-table"><table><thead><tr>' + "".join(f"<th>{_inline(c)}</th>" for c in head)
                           + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
                                                             for r in body) + "</tbody></table></div>")
            continue
        para.append(stripped)
        i += 1
    flush()
    return "\n".join(out), toc


def _parse(raw: str, slug: str) -> dict:
    meta, body = {}, raw
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw.replace("\r\n", "\n"), re.DOTALL)
    if match:
        for line in match.group(1).split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
        body = match.group(2)
    html_body, toc = render(body)
    plain = re.sub(r"[#>*`|\[\]()-]", " ", body)
    return {
        "slug": slug, "title": meta.get("title") or slug.replace("-", " ").capitalize(),
        "summary": meta.get("summary", ""), "icon": meta.get("icon", "fa-circle-question"),
        "order": int(meta.get("order") or 99), "html": html_body, "toc": toc,
        "text": re.sub(r"\s+", " ", plain).strip()[:4000],
    }


@lru_cache(maxsize=1)
def _load(stamp: float) -> tuple[dict, ...]:
    articles = []
    for name in sorted(os.listdir(HELP_DIR)) if os.path.isdir(HELP_DIR) else []:
        slug = name[:-3]
        if name.endswith(".md") and _SLUG.match(slug):
            with open(os.path.join(HELP_DIR, name), encoding="utf-8") as f:
                articles.append(_parse(f.read(), slug))
    return tuple(sorted(articles, key=lambda a: (a["order"], a["title"])))


def articles() -> list[dict]:
    """Every article, in reading order (re-read when a file in help/ changes)."""
    try:
        stamp = max((os.path.getmtime(os.path.join(HELP_DIR, n)) for n in os.listdir(HELP_DIR)), default=0.0)
    except OSError:
        stamp = 0.0
    return list(_load(stamp))


def article(slug: str) -> dict | None:
    if not _SLUG.match(slug or ""):
        return None
    return next((a for a in articles() if a["slug"] == slug), None)


def search_index() -> list[dict]:
    """What the page searches in (title, summary, headings, text)."""
    return [{"slug": a["slug"], "title": a["title"], "summary": a["summary"], "icon": a["icon"],
             "headings": [t["title"] for t in a["toc"]], "text": a["text"]} for a in articles()]
