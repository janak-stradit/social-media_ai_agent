"""Fetches a user-supplied website (homepage plus a couple of same-origin
"About"/"Services"-style pages) for the onboarding/brand-analysis step (see
agents/website_analysis_agent.py). Stdlib-only HTML handling - no bs4
dependency.

SECURITY: this fetches URLs a user typed into a form (or links found on that
page), server-side - classic SSRF surface (a malicious "website" could point
at localhost, a cloud metadata endpoint, or an internal service).
_is_safe_url resolves the hostname and rejects anything that isn't a public
IP before ever making a request; every additional URL this module fetches
(redirect targets, linked stylesheets, crawled same-origin pages) is
re-validated through the same check before being requested. Redirects are
followed manually (not requests' allow_redirects=True) so each hop is
checked individually. A timeout and a hard response-size cap are enforced on
every request, and the same-origin crawl is capped to a small, fixed number
of extra pages.
"""

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import requests

_TIMEOUT_SECONDS = 8
_MAX_BYTES = 3 * 1024 * 1024  # 3MB per page/asset
_TEXT_EXCERPT_CHARS = 3000  # per page
_MAX_COLORS = 10
_MAX_FONTS = 4
_MAX_CRAWL_PAGES = 2  # same-origin pages fetched in addition to the homepage
_MAX_STYLESHEETS = 2  # linked CSS files fetched for color/font extraction

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_STYLE_ATTR_RE = re.compile(r'style\s*=\s*"([^"]*)"', re.IGNORECASE)
_HEX_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_CSS_VAR_COLOR_RE = re.compile(
    r"--(?!bs-)[a-zA-Z0-9_-]*(?:color|brand|primary|accent|theme)[a-zA-Z0-9_-]*\s*:\s*(#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3}))",
    re.IGNORECASE,
)
_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;\"'}]+)", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_DESC_RE = re.compile(
    r'<meta\s+[^>]*name=["\']description["\'][^>]*content=["\']([^"\']*)["\']', re.IGNORECASE
)
_META_OG_RE = {
    "og_title": re.compile(r'<meta\s+[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']*)["\']', re.IGNORECASE),
    "og_description": re.compile(
        r'<meta\s+[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']*)["\']', re.IGNORECASE
    ),
    "og_site_name": re.compile(
        r'<meta\s+[^>]*property=["\']og:site_name["\'][^>]*content=["\']([^"\']*)["\']', re.IGNORECASE
    ),
    "og_image": re.compile(r'<meta\s+[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']*)["\']', re.IGNORECASE),
}
_THEME_COLOR_RE = re.compile(
    r'<meta\s+[^>]*name=["\']theme-color["\'][^>]*content=["\']([^"\']*)["\']', re.IGNORECASE
)
_LINK_STYLESHEET_RE = re.compile(
    r'<link\s+[^>]*rel=["\']stylesheet["\'][^>]*href=["\']([^"\']+)["\']', re.IGNORECASE
)
_FAVICON_RE = re.compile(
    r'<link\s+[^>]*rel=["\'](?:shortcut icon|icon)["\'][^>]*href=["\']([^"\']+)["\']', re.IGNORECASE
)
_ANCHOR_RE = re.compile(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)

# Colors so generic they're almost never a real "brand" color - filtered out
# before handing candidates to the LLM.
_GENERIC_COLORS = {"#fff", "#ffffff", "#000", "#000000", "#fafafa", "#f5f5f5"}

# Generic/system font families - not worth reporting as "the brand's font".
_GENERIC_FONTS = {
    "sans-serif", "serif", "monospace", "cursive", "fantasy", "system-ui",
    "-apple-system", "blinkmacsystemfont", "helvetica", "arial", "inherit",
}

# Anchor text/href hints used to find a couple of extra same-origin pages
# worth reading, beyond the homepage - keeps the crawl small and targeted
# instead of trying to discover a sitemap.
_CRAWL_HINTS = ("about", "services", "products", "solutions", "who-we-are", "company")


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False

    try:
        resolved_ips = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, None)}
    except Exception:
        return False

    for ip_str in resolved_ips:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False

    return True


def _fetch_raw(url: str, max_bytes: int = _MAX_BYTES, require_html: bool = False) -> tuple[str, str] | None:
    """SSRF-checked GET (following up to 3 same-origin-safety-checked
    redirect hops), returns (final_url, decoded_body) or None on any
    failure. Shared by page/stylesheet fetches. require_html=True rejects a
    response whose Content-Type explicitly isn't text/html (an empty/absent
    Content-Type is still allowed, matching the original page-fetch
    behavior) - not applied to stylesheet fetches, which are expected to be
    text/css."""
    for _hop in range(3):
        if not _is_safe_url(url):
            return None
        try:
            response = requests.get(
                url,
                timeout=_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
                headers={"User-Agent": "VortexSocialAI-OnboardingBot/1.0"},
            )
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    return None
                url = urljoin(url, location)
                continue

            if response.status_code != 200:
                return None

            if require_html:
                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type and content_type:
                    return None

            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > max_bytes:
                    break
                chunks.append(chunk)
            body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
            return url, body
        except Exception as e:
            print(f"[website_scraper_service] Failed to fetch {url}: {e}")
            return None
    return None


def _extract_colors(css_text: str) -> tuple[list[str], list[str]]:
    """Returns (declared_colors, generic_candidates) - declared_colors are
    hex values assigned to a CSS custom property whose name suggests it IS
    the brand color (--primary/--brand/--accent/--theme-color/...), a much
    higher-confidence signal than any hex value found anywhere on the page."""
    declared = [m.lower() for m in _CSS_VAR_COLOR_RE.findall(css_text)]
    generic = _HEX_COLOR_RE.findall(css_text)

    def _dedupe(colors):
        seen = set()
        out = []
        for c in colors:
            normalized = c.lower()
            if normalized in _GENERIC_COLORS or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out

    return _dedupe(declared)[:_MAX_COLORS], _dedupe(generic)[:_MAX_COLORS]


def _extract_fonts(css_text: str) -> list[str]:
    fonts = []
    seen = set()
    for raw in _FONT_FAMILY_RE.findall(css_text):
        # font-family can list several comma-separated fallbacks - take the
        # first (most specific/intentional) one.
        first = raw.split(",")[0].strip().strip("'\"")
        key = first.lower()
        # "var(--bs-font-sans-serif)" etc. is an unresolved CSS variable
        # reference, not an actual font name - skip rather than report
        # something unreadable.
        if not first or "var(" in key or "!important" in key or key in _GENERIC_FONTS or key in seen:
            continue
        seen.add(key)
        fonts.append(first)
        if len(fonts) >= _MAX_FONTS:
            break
    return fonts


def _extract_text(html: str) -> str:
    without_scripts = _SCRIPT_STYLE_RE.sub(" ", html)
    without_tags = _TAG_RE.sub(" ", without_scripts)
    collapsed = _WHITESPACE_RE.sub(" ", without_tags).strip()
    return collapsed[:_TEXT_EXCERPT_CHARS]


def _fetch_linked_stylesheets(base_url: str, html: str) -> str:
    """Fetches up to _MAX_STYLESHEETS linked CSS files (same SSRF checks as
    everything else) and returns their concatenated text, so color/font
    extraction can see design-system variables that live in an external
    stylesheet rather than inline <style> blocks - most real sites keep
    their actual theme colors there, not inline."""
    hrefs = _LINK_STYLESHEET_RE.findall(html)[:_MAX_STYLESHEETS]
    combined = []
    for href in hrefs:
        css_url = urljoin(base_url, href)
        result = _fetch_raw(css_url, max_bytes=512 * 1024)
        if result:
            combined.append(result[1])
    return "\n".join(combined)


def _find_crawl_candidates(base_url: str, html: str) -> list[str]:
    """Finds up to _MAX_CRAWL_PAGES same-origin links whose href/text hints
    at an About/Services/Products-style page - a deliberately small,
    targeted crawl rather than following a sitemap or every link."""
    base_host = urlparse(base_url).hostname
    candidates: list[str] = []
    seen_urls = set()

    for href, text in _ANCHOR_RE.findall(html):
        haystack = f"{href} {_WHITESPACE_RE.sub(' ', _TAG_RE.sub(' ', text))}".lower()
        if not any(hint in haystack for hint in _CRAWL_HINTS):
            continue

        absolute = urljoin(base_url, href.strip())
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or parsed.hostname != base_host:
            continue
        if absolute in seen_urls or absolute.rstrip("/") == base_url.rstrip("/"):
            continue

        seen_urls.add(absolute)
        candidates.append(absolute)
        if len(candidates) >= _MAX_CRAWL_PAGES:
            break

    return candidates


def fetch_website(url: str) -> dict | None:
    """Returns a dict on success, None on any failure (unsafe URL,
    unreachable, non-HTML, too large, etc) - callers treat this as
    best-effort and must not block on it.

    {
      title, meta_description, og_title, og_description, og_site_name,
      og_image, favicon, theme_color,
      text_excerpt (homepage + up to 2 same-origin About/Services-style
        pages, concatenated),
      declared_colors (from CSS custom properties like --primary/--brand -
        high confidence these ARE the brand's colors),
      colors (any other hex literals found - lower confidence, for the LLM
        to use its judgment on),
      fonts (font-family values actually declared on the page),
      pages_scraped (URLs actually fetched, for transparency),
    }
    """
    url = (url or "").strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    fetched = _fetch_raw(url, require_html=True)
    if not fetched:
        return None
    final_url, html = fetched

    title_match = _TITLE_RE.search(html)
    meta_match = _META_DESC_RE.search(html)
    theme_color_match = _THEME_COLOR_RE.search(html)
    favicon_match = _FAVICON_RE.search(html)

    og = {}
    for key, pattern in _META_OG_RE.items():
        m = pattern.search(html)
        og[key] = m.group(1).strip() if m else ""
    if og.get("og_image"):
        og["og_image"] = urljoin(final_url, og["og_image"])

    stylesheet_css = _fetch_linked_stylesheets(final_url, html)
    inline_css = "\n".join(_STYLE_BLOCK_RE.findall(html)) + "\n" + "\n".join(_STYLE_ATTR_RE.findall(html))
    all_css = f"{inline_css}\n{stylesheet_css}"

    declared_colors, generic_colors = _extract_colors(all_css)
    fonts = _extract_fonts(all_css)

    text_parts = [_extract_text(html)]
    pages_scraped = [final_url]

    for crawl_url in _find_crawl_candidates(final_url, html):
        crawled = _fetch_raw(crawl_url, require_html=True)
        if crawled:
            crawled_url, crawled_html = crawled
            text_parts.append(_extract_text(crawled_html))
            pages_scraped.append(crawled_url)

    return {
        "title": _WHITESPACE_RE.sub(" ", title_match.group(1)).strip() if title_match else "",
        "meta_description": meta_match.group(1).strip() if meta_match else "",
        "og_title": og.get("og_title", ""),
        "og_description": og.get("og_description", ""),
        "og_site_name": og.get("og_site_name", ""),
        "og_image": og.get("og_image", ""),
        "favicon": urljoin(final_url, favicon_match.group(1)) if favicon_match else "",
        "theme_color": theme_color_match.group(1).strip() if theme_color_match else "",
        "text_excerpt": "\n\n".join(text_parts),
        "declared_colors": declared_colors,
        "colors": generic_colors,
        "fonts": fonts,
        "pages_scraped": pages_scraped,
    }
