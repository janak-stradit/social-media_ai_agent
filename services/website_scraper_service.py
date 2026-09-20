"""Fetches a single page (the homepage, no crawling) from a user-supplied
website URL for the onboarding brand-analysis step (see
agents/website_analysis_agent.py). Stdlib-only HTML handling - no bs4
dependency.

SECURITY: this fetches a URL a user typed into a form, server-side - classic
SSRF surface (a malicious "website" could point at localhost, a cloud
metadata endpoint, or an internal service). _is_safe_url resolves the
hostname and rejects anything that isn't a public IP before ever making a
request, redirects are disabled entirely (a public URL redirecting to an
internal one is not handled in this pass - it just fails closed), and both a
timeout and a hard response-size cap are enforced.
"""

import ipaddress
import re
import socket
from urllib.parse import urlparse

import requests

_TIMEOUT_SECONDS = 8
_MAX_BYTES = 3 * 1024 * 1024  # 3MB
_TEXT_EXCERPT_CHARS = 4000
_MAX_COLORS = 8

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_STYLE_ATTR_RE = re.compile(r'style\s*=\s*"([^"]*)"', re.IGNORECASE)
_HEX_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_DESC_RE = re.compile(
    r'<meta\s+[^>]*name=["\']description["\'][^>]*content=["\']([^"\']*)["\']', re.IGNORECASE
)

# Colors so generic they're almost never a real "brand" color - filtered out
# before handing candidates to the LLM.
_GENERIC_COLORS = {"#fff", "#ffffff", "#000", "#000000", "#fafafa", "#f5f5f5"}


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


def _extract_colors(html: str) -> list[str]:
    candidates: list[str] = []
    for block in _STYLE_BLOCK_RE.findall(html):
        candidates.extend(_HEX_COLOR_RE.findall(block))
    for attr in _STYLE_ATTR_RE.findall(html):
        candidates.extend(_HEX_COLOR_RE.findall(attr))

    seen = set()
    ordered_unique = []
    for c in candidates:
        normalized = c.lower()
        if normalized in _GENERIC_COLORS or normalized in seen:
            continue
        seen.add(normalized)
        ordered_unique.append(normalized)

    return ordered_unique[:_MAX_COLORS]


def _extract_text(html: str) -> str:
    without_scripts = _SCRIPT_STYLE_RE.sub(" ", html)
    without_tags = _TAG_RE.sub(" ", without_scripts)
    collapsed = _WHITESPACE_RE.sub(" ", without_tags).strip()
    return collapsed[:_TEXT_EXCERPT_CHARS]


def fetch_website(url: str) -> dict | None:
    """Returns {title, meta_description, text_excerpt, colors} on success,
    None on any failure (unsafe URL, unreachable, non-HTML, too large,
    etc) - callers treat this as best-effort and must not block on it."""
    url = (url or "").strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    # Redirects are followed manually (not via requests' allow_redirects=True)
    # so each hop's target is re-validated through _is_safe_url before it's
    # ever fetched - a public URL that redirects to an internal one fails
    # closed instead of being silently followed. http->https and bare->www
    # redirects are extremely common on real homepages, so refusing all
    # redirects outright (the simpler alternative) would make this fail for
    # a large fraction of real sites.
    html = None
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
                url = requests.compat.urljoin(url, location)
                continue

            if response.status_code != 200:
                return None

            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and content_type:
                return None

            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > _MAX_BYTES:
                    break
                chunks.append(chunk)
            html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
            break
        except Exception as e:
            print(f"[website_scraper_service] Failed to fetch {url}: {e}")
            return None

    if html is None:
        return None

    title_match = _TITLE_RE.search(html)
    meta_match = _META_DESC_RE.search(html)

    return {
        "title": _WHITESPACE_RE.sub(" ", title_match.group(1)).strip() if title_match else "",
        "meta_description": meta_match.group(1).strip() if meta_match else "",
        "text_excerpt": _extract_text(html),
        "colors": _extract_colors(html),
    }
