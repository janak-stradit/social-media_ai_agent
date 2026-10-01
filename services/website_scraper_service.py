"""Fetches a user-supplied website (homepage plus up to 6 same-origin pages -
About/Services/Blog/Pricing-style pages preferred, other internal links as a
fallback - fetched concurrently) for the onboarding/brand-analysis step (see
agents/website_analysis_agent.py). HTML is parsed with the stdlib
html.parser - no bs4 dependency.

Tiered fetching: a plain HTTP request first; if the site blocks it (bot
protection - 401/403/429, Cloudflare 503) or the homepage has almost no
readable text (content rendered by JavaScript), the same pages are fetched
again with headless Chromium (Playwright). Playwright is optional - when it
isn't installed (or its browser binary is missing) that tier is skipped and
the plain-HTTP outcome stands.

Failures are reported, not swallowed: scrape_website() returns
(result, None) or (None, reason) where reason is one of FAILURE_REASONS, so
callers can tell the user *why* (e.g. "your site blocked automated access")
instead of silently saving nothing.

SECURITY: this fetches URLs a user typed into a form (or links found on that
page), server-side - classic SSRF surface (a malicious "website" could point
at localhost, a cloud metadata endpoint, or an internal service).
_check_url resolves the hostname and rejects anything that isn't a public
IP before ever making a request; every additional URL this module fetches
(redirect targets, linked stylesheets, crawled same-origin pages) is
re-validated through the same check before being requested. Redirects are
followed manually (not requests' allow_redirects=True) so each hop is
checked individually. In the headless-browser tier, EVERY request the page
makes (redirects, scripts, XHR, stylesheets) goes through the same check via
request interception and is aborted if it targets a non-public address. A
timeout and a hard response-size cap are enforced on every request, and the
same-origin crawl is capped to a small, fixed number of extra pages.
"""

import concurrent.futures
import functools
import ipaddress
import json
import logging
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 8
_BROWSER_TIMEOUT_MS = 20000
_MAX_BYTES = 3 * 1024 * 1024  # 3MB per page/asset
_MAX_CSS_BYTES = 512 * 1024
_HOMEPAGE_TEXT_CHARS = 3000
_TEXT_EXCERPT_CHARS = 2000  # per crawled page - kept modest since up to 7 pages get concatenated into one prompt
_MIN_USEFUL_TEXT_CHARS = 400  # homepage text below this -> likely JS-rendered, retry in the browser tier
_MIN_ANALYZABLE_CHARS = 200  # total text below this (and no meta description) -> nothing worth analyzing
_MAX_COLORS = 10
_MAX_FONTS = 4
_MAX_CRAWL_PAGES = 6  # same-origin pages fetched in addition to the homepage - fetched concurrently, so this stays fast
_MAX_BROWSER_CRAWL_PAGES = 3  # browser fetches are sequential and slower, so crawl fewer pages in that tier
_MAX_STYLESHEETS = 2  # linked CSS files fetched for color/font extraction
_MAX_REDIRECTS = 5

# Why a scrape failed - stable codes callers map to user-facing messages
# (see services/brand_profile_service.py::SCRAPE_FAILURE_MESSAGES).
FAILURE_REASONS = (
    "invalid_url",  # not a parseable http(s) URL
    "unsafe_url",  # resolves to a private/loopback/reserved address
    "unreachable",  # DNS failure, connection refused, too many redirects
    "timeout",
    "blocked",  # bot protection (401/403/429, Cloudflare challenge)
    "http_error",  # any other non-200 status
    "not_html",  # responded, but not with a web page
    "thin_content",  # a page came back but had almost no readable text
)
# Plain-HTTP outcomes worth retrying with a real browser
_BROWSER_RETRY_REASONS = {"blocked", "http_error", "timeout"}

_HEX_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_CSS_VAR_COLOR_RE = re.compile(
    r"--(?!bs-)[a-zA-Z0-9_-]*(?:color|brand|primary|accent|theme)[a-zA-Z0-9_-]*\s*:\s*(#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3}))",
    re.IGNORECASE,
)
_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;\"'}]+)", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_CONTENT_TYPE_CHARSET_RE = re.compile(r"charset=([\w-]+)", re.IGNORECASE)
_META_CHARSET_RE = re.compile(rb"<meta[^>]+charset=[\"']?([a-zA-Z0-9_-]+)", re.IGNORECASE)

# Colors so generic they're almost never a real "brand" color - filtered out
# before handing candidates to the LLM.
_GENERIC_COLORS = {"#fff", "#ffffff", "#000", "#000000", "#fafafa", "#f5f5f5"}

# Generic/system font families - not worth reporting as "the brand's font".
_GENERIC_FONTS = {
    "sans-serif", "serif", "monospace", "cursive", "fantasy", "system-ui",
    "-apple-system", "blinkmacsystemfont", "helvetica", "arial", "inherit",
}

# Third-party library stylesheets - their colors/fonts are the library's
# defaults, not the brand's, and they'd otherwise use up the stylesheet quota.
_VENDOR_CSS_HINTS = (
    "bootstrap", "font-awesome", "fontawesome", "jquery", "slick", "swiper",
    "animate", "aos.", "owl.carousel", "fancybox", "select2", "fonts.googleapis",
)

# Anchor text/href hints used to find a couple of extra same-origin pages
# worth reading, beyond the homepage - keeps the crawl small and targeted
# instead of trying to discover a sitemap.
_CRAWL_HINTS = (
    "about", "services", "products", "solutions", "who-we-are", "company",
    "blog", "pricing", "features", "team", "contact", "faq", "help", "resources", "portfolio",
)

# Browser-like request headers. Many sites (Akamai/Cloudflare-fronted ones in
# particular, e.g. hungama.com) answer a self-identified bot User-Agent with a
# 403 while serving the same page normally to a browser - which made the
# onboarding analysis and the brand-profile re-scan fail for those sites.
_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Hrefs never worth following even as a same-origin fallback link - not a
# real content page (or not one that tells us anything about the brand).
_SKIP_HREF_PATTERNS = ("#", "mailto:", "tel:", "javascript:", "/login", "/signin", "/cart", "/checkout")

# Elements whose text is navigation/chrome rather than page content - their
# text is left out of the excerpt (their links are still used for crawling).
_NON_CONTENT_TAGS = {
    "script", "style", "noscript", "svg", "template", "nav", "footer", "aside",
    "form", "iframe", "select", "button",
}
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}
# Never visible at all - excluded even from all-visible-text (which, unlike
# the content excerpt, keeps nav/footer text: that's where disclaimers,
# license numbers and phone numbers usually live).
_INVISIBLE_TAGS = {"script", "style", "noscript", "svg", "template"}


# ── URL safety ──────────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=512)
def _host_check(hostname: str) -> str | None:
    """None if every address the hostname resolves to is public, else a
    failure reason. Cached - the browser tier checks every sub-request."""
    try:
        resolved_ips = {info[4][0] for info in socket.getaddrinfo(hostname, None)}
    except Exception:
        return "unreachable"

    for ip_str in resolved_ips:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return "unsafe_url"
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return "unsafe_url"
    return None


def _check_url(url: str) -> str | None:
    """None if url is a public http(s) URL that's safe to fetch, else a failure reason."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "invalid_url"
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "invalid_url"
    return _host_check(parsed.hostname.lower())


def _is_safe_url(url: str) -> bool:
    return _check_url(url) is None


# ── Plain HTTP tier ─────────────────────────────────────────────────────────


def _decode(raw: bytes, content_type: str) -> str:
    """Decode with the declared charset (header, then <meta charset>), else
    UTF-8. requests' own fallback for text/* without a charset is ISO-8859-1,
    which garbled every UTF-8 page that didn't declare one in the header."""
    encoding = None
    header_match = _CONTENT_TYPE_CHARSET_RE.search(content_type or "")
    if header_match:
        encoding = header_match.group(1)
    else:
        meta_match = _META_CHARSET_RE.search(raw[:4096])
        if meta_match:
            encoding = meta_match.group(1).decode("ascii", "ignore")
    encoding = encoding or "utf-8"
    try:
        return raw.decode(encoding)
    except LookupError:
        encoding = "utf-8"
    except UnicodeDecodeError:
        pass
    # Declared/assumed UTF-8 that isn't (a common mislabel: Windows-1252
    # curly quotes on a "utf-8" page came through as "UAE�s").
    if encoding.lower().replace("-", "") == "utf8":
        return raw.decode("cp1252", errors="replace")
    return raw.decode(encoding, errors="replace")


def _fetch_raw(
    url: str, max_bytes: int = _MAX_BYTES, require_html: bool = False, timeout: float = _TIMEOUT_SECONDS
) -> tuple[tuple[str, str] | None, str | None]:
    """SSRF-checked GET (each redirect hop re-checked). Returns
    ((final_url, decoded_body), None) on success or (None, reason).
    require_html=True rejects a response whose Content-Type explicitly isn't
    text/html (an empty/absent Content-Type is still allowed) - not applied to
    stylesheet fetches, which are expected to be text/css."""
    for _hop in range(_MAX_REDIRECTS):
        reason = _check_url(url)
        if reason:
            return None, reason
        try:
            response = requests.get(
                url,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
                headers=_REQUEST_HEADERS,
            )
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    return None, "http_error"
                url = urljoin(url, location)
                continue

            if response.status_code != 200:
                logger.warning(f"{url} returned HTTP {response.status_code}")
                server = (response.headers.get("Server") or "").lower()
                if response.status_code in (401, 403, 429) or (response.status_code == 503 and "cloudflare" in server):
                    return None, "blocked"
                return None, "http_error"

            content_type = response.headers.get("Content-Type", "")
            if require_html and content_type and "text/html" not in content_type:
                return None, "not_html"

            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > max_bytes:
                    break
                chunks.append(chunk)
            return (url, _decode(b"".join(chunks), content_type)), None
        except requests.Timeout:
            logger.warning(f"Timed out fetching {url}")
            return None, "timeout"
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            return None, "unreachable"
    return None, "unreachable"  # too many redirects


# ── Headless browser tier (optional) ────────────────────────────────────────


@functools.lru_cache(maxsize=1)
def _browser_installed() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


class _BrowserFetcher:
    """Headless Chromium (Playwright) for sites that block plain HTTP
    clients or render their content with JavaScript. Use as a context
    manager; fetch() mirrors _fetch_raw()'s return shape. Stylesheets the
    page loads are captured as it renders (last_css), since fetching them
    separately over plain HTTP would hit the same bot protection."""

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        try:
            # Without this flag (and the webdriver override below) Chromium
            # announces itself as automation-controlled, which is exactly
            # what bot-protection services check first.
            self._browser = self._pw.chromium.launch(
                headless=True, args=["--disable-blink-features=AutomationControlled"]
            )
        except Exception:
            self._pw.stop()
            raise
        self._context = self._browser.new_context(
            user_agent=_REQUEST_HEADERS["User-Agent"],
            locale="en-US",
            viewport={"width": 1366, "height": 850},
            extra_http_headers={"Accept-Language": _REQUEST_HEADERS["Accept-Language"]},
        )
        self._context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        self._context.route("**/*", self._guard)
        self.last_css: list[str] = []
        return self

    def __exit__(self, *exc):
        for closer in (self._context.close, self._browser.close, self._pw.stop):
            try:
                closer()
            except Exception:
                pass

    @staticmethod
    def _guard(route):
        """SSRF guard for every request the page makes, plus skip heavy
        assets we never read."""
        request = route.request
        if request.resource_type in ("image", "media", "font"):
            return route.abort()
        if urlparse(request.url).scheme in ("data", "blob"):
            return route.continue_()
        if not _is_safe_url(request.url):
            return route.abort()
        return route.continue_()

    def fetch(self, url: str) -> tuple[tuple[str, str] | None, str | None]:
        reason = _check_url(url)
        if reason:
            return None, reason

        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        css_responses = []
        page = self._context.new_page()
        page.on(
            "response",
            lambda r: css_responses.append(r) if r.request.resource_type == "stylesheet" and r.ok else None,
        )
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=_BROWSER_TIMEOUT_MS)
            if response is None:
                return None, "unreachable"
            if response.status in (401, 403, 429):
                return None, "blocked"
            if response.status >= 400:
                return None, "http_error"
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightTimeout:
                pass  # long-polling/analytics keep some sites from ever going idle - the DOM is ready

            self.last_css = []
            for css_response in css_responses:
                if len(self.last_css) >= _MAX_STYLESHEETS:
                    break
                if any(hint in css_response.url.lower() for hint in _VENDOR_CSS_HINTS):
                    continue
                try:
                    self.last_css.append(css_response.text()[:_MAX_CSS_BYTES])
                except Exception:
                    pass
            return (page.url, page.content()), None
        except PlaywrightTimeout:
            return None, "timeout"
        except Exception as e:
            logger.warning(f"Browser failed to fetch {url}: {e}")
            return None, "unreachable"
        finally:
            page.close()


# ── Parsing ─────────────────────────────────────────────────────────────────


class _PageParser(HTMLParser):
    """Collects everything the analysis needs in one pass: <title>, <meta>
    and <link> attributes (order-independent, entities decoded - the old
    regexes only matched name= before content= and left "&amp;" in text),
    anchors for crawling, CSS for color/font extraction, and the page's
    content text with navigation/footer/script chrome left out."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.metas: list[dict] = []
        self.links: list[dict] = []
        self.anchors: list[tuple[str, str]] = []
        self.text_parts: list[str] = []
        self.all_text_parts: list[str] = []
        self.css_parts: list[str] = []
        self.jsonld_blocks: list[str] = []
        self.logo_images: list[str] = []
        self.lang = ""
        self._stack: list[tuple[str, bool, bool]] = []  # (tag, skipped from content, invisible)
        self._skip_depth = 0
        self._invisible_depth = 0
        self._in_title = False
        self._in_style = False
        self._in_jsonld = False
        self._anchor: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag, attrs):
        attributes = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            self.metas.append(attributes)
            return
        if tag == "link":
            self.links.append(attributes)
            return
        if tag == "html":
            self.lang = attributes.get("lang", "")
        if tag == "img":
            hint = " ".join(attributes.get(k, "") for k in ("src", "alt", "class", "id")).lower()
            if "logo" in hint and attributes.get("src") and len(self.logo_images) < 3:
                self.logo_images.append(attributes["src"])
        if attributes.get("style"):
            self.css_parts.append(attributes["style"])
        if tag in _VOID_TAGS:
            return

        if tag == "title":
            self._in_title = True
        elif tag == "style":
            self._in_style = True
        elif tag == "script" and "ld+json" in attributes.get("type", "").lower():
            self._in_jsonld = True
            self.jsonld_blocks.append("")
        elif tag == "a":
            self._anchor = (attributes.get("href", ""), [])

        skip = (
            tag in _NON_CONTENT_TAGS
            or attributes.get("role") == "navigation"
            or attributes.get("aria-hidden") == "true"
        )
        invisible = tag in _INVISIBLE_TAGS
        self._stack.append((tag, skip, invisible))
        self._skip_depth += skip
        self._invisible_depth += invisible

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "style":
            self._in_style = False
        elif tag == "script":
            self._in_jsonld = False
        elif tag == "a" and self._anchor is not None:
            self.anchors.append((self._anchor[0], " ".join(self._anchor[1])))
            self._anchor = None

        # Pop back to the matching open tag - tolerates unclosed <p>/<li> etc.
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                self._skip_depth -= sum(1 for _, skipped, _ in self._stack[i:] if skipped)
                self._invisible_depth -= sum(1 for _, _, invisible in self._stack[i:] if invisible)
                del self._stack[i:]
                break

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._in_style:
            self.css_parts.append(data)
            return
        if self._in_jsonld:
            self.jsonld_blocks[-1] += data
            return
        if self._anchor is not None:
            self._anchor[1].append(data)
        if not data.strip():
            return
        if self._skip_depth == 0:
            self.text_parts.append(data)
        if self._invisible_depth == 0:
            self.all_text_parts.append(data)

    # ── accessors ──
    def title(self) -> str:
        return _collapse(" ".join(self.title_parts))

    def meta(self, key: str) -> str:
        """content of <meta name=key> or <meta property=key>"""
        for m in self.metas:
            if (m.get("name") or m.get("property") or "").lower() == key:
                return _collapse(m.get("content", ""))
        return ""

    def link_href(self, *rels: str) -> str:
        for rel in rels:
            for link in self.links:
                if rel in (link.get("rel") or "").lower().split() and link.get("href"):
                    return link["href"]
        return ""

    def stylesheet_hrefs(self) -> list[str]:
        hrefs = [
            link["href"]
            for link in self.links
            if "stylesheet" in (link.get("rel") or "").lower() and link.get("href")
        ]
        return [h for h in hrefs if not any(hint in h.lower() for hint in _VENDOR_CSS_HINTS)]

    def text(self, limit: int) -> str:
        return _collapse(" ".join(self.text_parts))[:limit]

    def all_text(self) -> str:
        return _collapse(" ".join(self.all_text_parts))

    def hreflangs(self) -> list[str]:
        return [
            (link.get("hreflang") or "").lower()
            for link in self.links
            if link.get("hreflang") and "alternate" in (link.get("rel") or "").lower()
        ]


def _collapse(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value or "").strip()


def _parse(html: str) -> _PageParser:
    parser = _PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as e:  # malformed markup - keep whatever was collected
        logger.warning(f"HTML parse warning: {e}")
    return parser


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


# ── Brand & compliance signals ──────────────────────────────────────────────
# Facts read straight off the site (never inferred): what the company says it
# is (schema.org JSON-LD), where it operates, the disclaimers and credentials
# it already publishes, and where its legal pages are. Feeds the brand
# profile now, and the industry/region compliance layer next.

_MAX_SIGNAL_ITEMS = 8

_SOCIAL_HOSTS = {
    "linkedin.com": "linkedin",
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "x.com": "x",
    "twitter.com": "x",
    "youtube.com": "youtube",
    "tiktok.com": "tiktok",
    "pinterest.com": "pinterest",
}
_SOCIAL_SHARE_HINTS = ("sharer", "share?", "/share", "intent/", "shareArticle", "dialog/")
# First path segment of a link to a post/video/playlist rather than a profile
_SOCIAL_NON_PROFILE_PATHS = {
    "watch", "playlist", "embed", "shorts", "share", "sharer", "intent", "hashtag", "p", "reel", "reels",
    "posts", "feed", "search", "explore",
}

# Types checked in order (first match wins); within a type, earlier hints are
# more specific - "terms" beats a generic "legal" (which picked Stripe's
# /legal/restricted-businesses as its terms page).
_LEGAL_PAGE_TYPES = (
    ("disclaimer", ("disclaimer", "disclosure", "regulatory", "investor-charter", "risk-factor", "grievance")),
    ("privacy", ("privacy", "data-protection")),
    ("terms", ("terms", "conditions", "tos", "legal")),
    ("cookies", ("cookie",)),
    ("accessibility", ("accessibility",)),
)

_REGIONS = ("US", "UAE/GCC", "India")
_TLD_REGIONS = {"us": "US", "in": "India", "ae": "UAE/GCC", "sa": "UAE/GCC", "qa": "UAE/GCC", "kw": "UAE/GCC", "om": "UAE/GCC", "bh": "UAE/GCC"}
_COUNTRY_REGIONS = {
    "us": "US", "usa": "US", "united states": "US", "united states of america": "US",
    "in": "India", "ind": "India", "india": "India",
    "ae": "UAE/GCC", "are": "UAE/GCC", "uae": "UAE/GCC", "united arab emirates": "UAE/GCC",
    "sa": "UAE/GCC", "saudi arabia": "UAE/GCC", "qa": "UAE/GCC", "qatar": "UAE/GCC",
    "kw": "UAE/GCC", "kuwait": "UAE/GCC", "om": "UAE/GCC", "oman": "UAE/GCC", "bh": "UAE/GCC", "bahrain": "UAE/GCC",
}
_PHONE_RE = re.compile(r"\+\s?(971|966|974|965|968|973|91|1)[\s\-.()]*\d[\d\s\-.()]{6,}")
_PHONE_REGIONS = {"1": "US", "91": "India", "971": "UAE/GCC", "966": "UAE/GCC", "974": "UAE/GCC", "965": "UAE/GCC", "968": "UAE/GCC", "973": "UAE/GCC"}
_US_PHONE_RE = re.compile(r"(?<![\d+])\(?[2-9]\d{2}\)?[\s.\-][2-9]\d{2}[\s.\-]\d{4}(?!\d)")
_US_STATE_ZIP_RE = re.compile(
    r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|"
    r"NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY),?\s+\d{5}(?:-\d{4})?\b"
)
_CURRENCY_RES = {
    "India": re.compile(r"₹|\bINR\b|\bRs\.?\s?\d"),
    "UAE/GCC": re.compile(r"\b(?:AED|SAR|QAR|KWD|OMR|BHD)\b|\bDhs?\.?\s?\d|د\.إ"),
    "US": re.compile(r"\bUSD\b|US\$"),
}

# Sentences that are the company's own disclaimers / regulatory statements -
# reused verbatim later, since the company already stands behind them.
_DISCLAIMER_RES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"subject to market risks?",
        r"past performance",
        r"not (?:fdic|ncua) insured",
        r"member (?:fdic|finra|sipc)",
        r"equal housing (?:lender|opportunity)",
        r"\bnmls\b",
        r"\b(?:sebi|irdai?|rbi|amfi|pfrda)\b.{0,60}(?:regist|licen)",
        r"registration (?:no|number)",
        r"licen[cs]e (?:no|number)",
        r"trade licen[cs]e",
        r"(?:authori[sz]ed and )?regulated by",
        r"not (?:intended|meant) to (?:diagnose|treat|cure|prevent)",
        r"not (?:been )?evaluated by the (?:food and drug|fda)",
        r"(?:does not|doesn't|is not|not) (?:constitute|provide|intended as|a substitute for) (?:professional )?(?:medical|legal|financial|investment|tax) advice",
        r"consult (?:your|a) (?:doctor|physician|healthcare|financial|legal|tax)",
        r"attorney advertising",
        r"(?:prior|past) results do not guarantee",
        r"t&cs? apply|terms (?:and|&) conditions apply",
        r"\bcin\b[:\s]+[lu]\d{5}",
        r"\b(?:dha|mohap|doh|dhcc)\b.{0,60}(?:licen|approv|permit|regist)",
        r"read (?:all|the) (?:scheme|offer) (?:related )?documents",
    )
]
_CREDENTIAL_RES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\biso\s?(?:/iec\s?)?\d{4,5}(?::\d{4})?",
        r"\bsoc\s?[12](?: type (?:ii|i|1|2))?",
        r"\bhipaa[- ]compliant",
        r"\bgdpr[- ]compliant",
        r"\bpci[- ]dss(?: level \d)?",
        r"\bhitrust(?: certified)?",
        r"\bcmmi(?:[- ]level \d)?",
        r"\bnabh[- ]accredited|\bnabh\b",
        r"\bnabl[- ]accredited",
        r"\bjci[- ]accredited|\bjoint commission accredited",
        r"\bfssai\b",
        r"\bcertified b corp(?:oration)?",
        r"\bgreat place to work(?: certified)?",
        r"\bfda[- ](?:approved|cleared|registered)",
        r"\bce[- ]marked",
    )
]
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _parse_structured_data(blocks: list[str]) -> dict:
    """schema.org JSON-LD: every @type the site declares about itself (a
    direct industry signal - MedicalClinic, FinancialService, LegalService,
    ...) plus the organization node's name/logo/sameAs/country/phone."""
    nodes: list[dict] = []

    def _collect(value):
        if isinstance(value, list):
            for item in value:
                _collect(item)
        elif isinstance(value, dict):
            nodes.append(value)
            _collect(value.get("@graph"))

    for block in blocks:
        try:
            _collect(json.loads(block.strip()))
        except (ValueError, TypeError):
            continue

    types: list[str] = []
    org: dict = {}
    for node in nodes:
        node_types = node.get("@type")
        node_types = node_types if isinstance(node_types, list) else [node_types] if node_types else []
        for t in node_types:
            if isinstance(t, str) and t not in types:
                types.append(t)
        is_org = any(
            isinstance(t, str) and (t.endswith("Organization") or t in _ORG_LIKE_TYPES) for t in node_types
        )
        if is_org and not org:
            org = node

    def _text(value) -> str:
        if isinstance(value, dict):
            value = value.get("url") or value.get("name") or value.get("@id")
        if isinstance(value, list):
            value = value[0] if value else ""
        return value.strip() if isinstance(value, str) else ""

    address = org.get("address")
    address = address[0] if isinstance(address, list) and address else address
    country = _text(address.get("addressCountry")) if isinstance(address, dict) else ""
    same_as = org.get("sameAs")
    same_as = [s for s in (same_as if isinstance(same_as, list) else [same_as]) if isinstance(s, str)]

    return {
        "types": types[:12],
        "name": _text(org.get("name")),
        "legal_name": _text(org.get("legalName")),
        "logo": _text(org.get("logo")) or _text(org.get("image")),
        "same_as": same_as[:12],
        "country": country,
        "telephone": _text(org.get("telephone")),
    }


# schema.org organization subtypes that don't end in "Organization"
_ORG_LIKE_TYPES = {
    "Corporation", "LocalBusiness", "Hospital", "MedicalClinic", "Physician", "Dentist", "Pharmacy",
    "FinancialService", "BankOrCreditUnion", "InsuranceAgency", "AccountingService", "LegalService",
    "Attorney", "RealEstateAgent", "Store", "Restaurant", "AutomotiveBusiness", "ProfessionalService",
    "EducationalOrganization", "CollegeOrUniversity", "School", "NGO", "Brand",
}


def _social_links(anchors: list[tuple[str, str]], same_as: list[str]) -> dict:
    """{platform: profile_url} - first profile link per platform, from the
    site's own links and its JSON-LD sameAs. Share buttons are skipped."""
    found: dict[str, str] = {}
    for href in same_as + [href for href, _ in anchors]:
        href = (href or "").strip()
        if not href.startswith(("http://", "https://")) or any(h in href for h in _SOCIAL_SHARE_HINTS):
            continue
        host = (urlparse(href).hostname or "").lower().removeprefix("www.").removeprefix("m.")
        platform = _SOCIAL_HOSTS.get(host) or next(
            (p for domain, p in _SOCIAL_HOSTS.items() if host.endswith("." + domain)), None
        )
        path = urlparse(href).path.strip("/")
        if (
            platform
            and platform not in found
            and path
            and path.split("/")[0].lower() not in _SOCIAL_NON_PROFILE_PATHS
            and "/status/" not in f"/{path}/"
        ):
            found[platform] = href.split("?")[0]
    return found


def _legal_match(url_or_text: str) -> tuple[str, int] | None:
    """(page type, hint rank - lower is more specific) or None."""
    haystack = url_or_text.lower()
    for page_type, hints in _LEGAL_PAGE_TYPES:
        for rank, hint in enumerate(hints):
            if hint in haystack:
                return page_type, rank
    return None


def _legal_page_type(url_or_text: str) -> str | None:
    match = _legal_match(url_or_text)
    return match[0] if match else None


def _find_legal_pages(base_url: str, parser: _PageParser, extra_urls: list[str]) -> list[dict]:
    """[{type, url}] - the site's privacy/terms/disclaimer/... pages, the
    most specifically-named one per type."""
    base_host = urlparse(base_url).hostname or ""
    best: dict[str, tuple[int, str]] = {}
    for href, text in parser.anchors + [(u, "") for u in extra_urls]:
        absolute = urljoin(base_url, href.strip()).split("#")[0]
        host = urlparse(absolute).hostname or ""
        if urlparse(absolute).scheme not in ("http", "https") or not (
            host == base_host or host.endswith("." + base_host.removeprefix("www."))
        ):
            continue
        match = _legal_match(f"{urlparse(absolute).path} {_collapse(text)}")
        if match and (match[0] not in best or match[1] < best[match[0]][0]):
            best[match[0]] = (match[1], absolute)
    return [{"type": t, "url": url} for t, (_, url) in best.items()]


def _region_signals(final_url: str, parser: _PageParser, structured: dict, all_text: str) -> tuple[dict, list[str]]:
    """Evidence for where the business operates, and the regions it points
    to (US / UAE/GCC / India - the markets our compliance rules cover),
    strongest first. Weights: a declared country or country TLD is strong;
    phone codes and currencies are solid; page language/hreflang are weak
    (many sites default to en-US regardless of market)."""
    scores = dict.fromkeys(_REGIONS, 0)
    host = urlparse(final_url).hostname or ""
    tld = host.rsplit(".", 1)[-1] if "." in host else ""

    if tld in _TLD_REGIONS:
        scores[_TLD_REGIONS[tld]] += 3
    country_region = _COUNTRY_REGIONS.get(structured.get("country", "").strip().lower())
    if country_region:
        scores[country_region] += 3

    phone_counts: dict[str, int] = {}
    for code in _PHONE_RE.findall(all_text + " " + structured.get("telephone", "")):
        region = _PHONE_REGIONS[code]
        phone_counts[region] = phone_counts.get(region, 0) + 1
    # US sites rarely write +1: "216.444.2200" / "(800) 555-0199", and
    # "Cleveland, OH 44195" addresses
    us_local = len(_US_PHONE_RE.findall(all_text)) + len(_US_STATE_ZIP_RE.findall(all_text))
    if us_local >= 2:
        phone_counts["US"] = phone_counts.get("US", 0) + us_local
    for region in phone_counts:
        scores[region] += 2

    currency_counts = {region: len(pattern.findall(all_text)) for region, pattern in _CURRENCY_RES.items()}
    currency_counts = {r: c for r, c in currency_counts.items() if c}
    # Only the dominant currency counts - banks and remittance sites list USD
    # exchange rates without operating in the US.
    if currency_counts:
        scores[max(currency_counts, key=currency_counts.get)] += 2

    lang = parser.lang.lower()
    locale = parser.meta("og:locale").lower().replace("_", "-")
    hreflangs = parser.hreflangs()
    for tag in [lang, locale] + hreflangs:
        if tag.endswith("-in") or tag.startswith("hi"):
            scores["India"] += 1
        elif tag.startswith("ar") or tag.endswith(("-ae", "-sa", "-qa", "-kw", "-om", "-bh")):
            scores["UAE/GCC"] += 1
        elif tag.endswith("-us"):
            scores["US"] += 1

    signals = {
        "tld": tld,
        "lang": lang,
        "locale": locale,
        "country": structured.get("country", ""),
        "phone_codes": phone_counts,
        "currencies": currency_counts,
        "hreflang": hreflangs[:10],
        "scores": scores,
    }
    regions = [r for r, s in sorted(scores.items(), key=lambda kv: -kv[1]) if s >= 2]
    return signals, regions


def _find_disclaimers(texts: list[str]) -> list[str]:
    """Sentences matching known disclaimer/regulatory patterns, verbatim."""
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for sentence in _SENTENCE_SPLIT_RE.split(text):
            sentence = sentence.strip()
            if not 20 <= len(sentence) <= 400 or not any(p.search(sentence) for p in _DISCLAIMER_RES):
                continue
            key = sentence.lower()
            if key not in seen:
                seen.add(key)
                found.append(sentence)
                if len(found) >= _MAX_SIGNAL_ITEMS:
                    return found
    return found


def _find_credentials(text: str) -> list[str]:
    """Certifications/accreditations the site claims (ISO 27001, SOC 2,
    NABH, ...) - the only such claims generated content should repeat."""
    found: list[str] = []
    seen: set[str] = set()
    for pattern in _CREDENTIAL_RES:
        for match in pattern.finditer(text):
            value = _collapse(match.group(0))
            key = re.sub(r"[\s\-/]", "", value.lower())
            if key not in seen:
                seen.add(key)
                found.append(value)
                if len(found) >= _MAX_SIGNAL_ITEMS:
                    return found
    return found


def _fetch_sitemap_urls(final_url: str) -> list[str]:
    """Page URLs from /sitemap.xml (one level of sitemap index followed,
    preferring a "page" sitemap over post/product ones) - lets the crawl
    find About/legal pages that aren't linked from the homepage."""
    root = f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}"
    # Short timeout - the crawl waits on this, and a sitemap is a nice-to-have
    fetched, _ = _fetch_raw(f"{root}/sitemap.xml", max_bytes=1024 * 1024, timeout=_SITEMAP_TIMEOUT_SECONDS)
    if not fetched:
        return []
    locs = _SITEMAP_LOC_RE.findall(fetched[1])
    if "<sitemapindex" in fetched[1][:2000].lower() and locs:
        child = next((u for u in locs if "page" in u.lower()), locs[0])
        fetched, _ = _fetch_raw(child.strip(), max_bytes=1024 * 1024, timeout=_SITEMAP_TIMEOUT_SECONDS)
        locs = _SITEMAP_LOC_RE.findall(fetched[1]) if fetched else []
    return [u.strip() for u in locs[:2000]]


_SITEMAP_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_SITEMAP_TIMEOUT_SECONDS = 3


# ── Crawl selection ─────────────────────────────────────────────────────────


def _same_origin_content_link(base_url: str, base_host: str, href: str) -> str | None:
    """Resolves href to an absolute URL and returns it only if it's a
    same-origin, http(s), plausible content page - None otherwise (external
    link, anchor, mailto:, login/cart page, etc.)."""
    href = href.strip()
    if not href or any(href.lower().startswith(p) or p in href.lower() for p in _SKIP_HREF_PATTERNS):
        return None
    absolute = urljoin(base_url, href).split("#")[0]
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https") or parsed.hostname != base_host:
        return None
    if absolute.rstrip("/") == base_url.rstrip("/"):
        return None
    return absolute


def _find_crawl_candidates(base_url: str, parser: _PageParser, limit: int, extra_urls: list[str] = ()) -> list[str]:
    """Up to `limit` same-origin pages worth reading besides the homepage.

    Hinted pages (About/Services/Pricing...) come first, shallowest path
    first - a top-level /about-us describes the brand, while a deep
    /savings-account/services/update-pan form merely contains "services" in
    its URL (seen on a real bank site, which used to crawl 6 such forms and
    no About page). Other internal links fill any remaining slots.
    extra_urls (from the sitemap) are considered after the page's own links.
    Legal pages are left out - they're boilerplate, not brand description
    (see _find_legal_pages)."""
    base_host = urlparse(base_url).hostname
    hinted: list[tuple[int, int, str]] = []
    other: list[str] = []
    seen_urls = set()

    for order, (href, text) in enumerate(parser.anchors + [(u, "") for u in extra_urls]):
        link = _same_origin_content_link(base_url, base_host, href)
        if not link or link in seen_urls or _legal_page_type(f"{urlparse(link).path} {text}"):
            continue
        seen_urls.add(link)

        path = urlparse(link).path.strip("/")
        depth = path.count("/") + 1 if path else 0
        haystack = f"{path} {_collapse(text)}".lower()
        if any(hint in haystack for hint in _CRAWL_HINTS):
            hinted.append((depth, order, link))
        else:
            other.append(link)

    candidates = [link for _, _, link in sorted(hinted)][:limit]
    if len(candidates) < limit:
        candidates += other[: limit - len(candidates)]
    return candidates


# ── Assembly ────────────────────────────────────────────────────────────────


def _assemble(
    final_url: str,
    parser: _PageParser,
    pages: list[tuple[str, str]],
    css_texts: list[str],
    method: str,
    legal_pages: list[dict],
    legal_htmls: list[str] = (),
) -> dict:
    """Builds the result dict from the parsed homepage plus already-fetched
    crawl pages [(url, html)], stylesheet texts, and legal pages (listed,
    and some fetched - legal_htmls - for their disclaimer text)."""
    og_image = parser.meta("og:image")
    favicon = parser.link_href("apple-touch-icon", "icon", "shortcut")

    text_parts = [parser.text(_HOMEPAGE_TEXT_CHARS)]
    all_texts = [parser.all_text()]
    jsonld_blocks = list(parser.jsonld_blocks)
    pages_scraped = [final_url]
    for page_url, page_html in pages:
        page_parser = _parse(page_html)
        page_text = page_parser.text(_TEXT_EXCERPT_CHARS)
        all_texts.append(page_parser.all_text())
        jsonld_blocks += page_parser.jsonld_blocks
        if page_text:
            text_parts.append(page_text)
            pages_scraped.append(page_url)
    legal_texts = [_parse(html).all_text() for html in legal_htmls]

    all_css = "\n".join(parser.css_parts + css_texts)
    declared_colors, generic_colors = _extract_colors(all_css)

    structured = _parse_structured_data(jsonld_blocks)
    combined_text = " ".join(all_texts)
    region_signals, regions = _region_signals(final_url, parser, structured, combined_text)
    # Most intentional logo first: the one the site declares in its
    # structured data, then an <img> marked as the logo, then icons; the
    # og:image social banner only as a last resort.
    logo_candidates = [structured["logo"], *parser.logo_images, favicon, og_image]
    logo = next((urljoin(final_url, c) for c in logo_candidates if c and not c.startswith("data:")), "")

    return {
        "title": parser.title(),
        "meta_description": parser.meta("description"),
        "og_title": parser.meta("og:title"),
        "og_description": parser.meta("og:description"),
        "og_site_name": parser.meta("og:site_name"),
        "og_image": urljoin(final_url, og_image) if og_image else "",
        "favicon": urljoin(final_url, favicon) if favicon else "",
        "theme_color": parser.meta("theme-color"),
        "text_excerpt": "\n\n".join(t for t in text_parts if t),
        "declared_colors": declared_colors,
        "colors": generic_colors,
        "fonts": _extract_fonts(all_css),
        "pages_scraped": pages_scraped,
        "fetch_method": method,
        "logo": logo,
        "structured_data": structured,
        "schema_types": structured["types"],
        "social_links": _social_links(parser.anchors, structured["same_as"]),
        "legal_pages": legal_pages,
        "region_signals": region_signals,
        "regions_detected": regions,
        "site_disclaimers": _find_disclaimers(all_texts + legal_texts),
        "certifications": _find_credentials(combined_text),
    }


# Legal pages actually read for disclaimer text (the rest are only listed) -
# a disclaimer/regulatory page first, then terms.
_LEGAL_PAGES_TO_READ = ("disclaimer", "terms")
_MAX_LEGAL_FETCHES = 2


def _scrape_plain(final_url: str, html: str) -> dict:
    parser = _parse(html)
    sitemap_urls = _fetch_sitemap_urls(final_url)
    stylesheet_urls = [urljoin(final_url, h) for h in parser.stylesheet_hrefs()[:_MAX_STYLESHEETS]]
    crawl_urls = _find_crawl_candidates(final_url, parser, _MAX_CRAWL_PAGES, sitemap_urls)
    legal_pages = _find_legal_pages(final_url, parser, sitemap_urls)
    legal_urls = [p["url"] for t in _LEGAL_PAGES_TO_READ for p in legal_pages if p["type"] == t][:_MAX_LEGAL_FETCHES]

    # Independent requests (each stylesheet, crawled page, legal page) -
    # fetched concurrently, so the wait is roughly the slowest single request
    # rather than the sum of all of them.
    css_texts: list[str] = []
    pages: list[tuple[str, str]] = []
    legal_htmls: list[str] = []
    if stylesheet_urls or crawl_urls or legal_urls:
        workers = _MAX_STYLESHEETS + _MAX_CRAWL_PAGES + _MAX_LEGAL_FETCHES
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            css_futures = [executor.submit(_fetch_raw, u, max_bytes=_MAX_CSS_BYTES) for u in stylesheet_urls]
            page_futures = [executor.submit(_fetch_raw, u, require_html=True) for u in crawl_urls]
            legal_futures = [executor.submit(_fetch_raw, u, require_html=True) for u in legal_urls]
            css_texts = [f.result()[0][1] for f in css_futures if f.result()[0]]
            pages = [f.result()[0] for f in page_futures if f.result()[0]]
            legal_htmls = [f.result()[0][1] for f in legal_futures if f.result()[0]]

    return _assemble(final_url, parser, pages, css_texts, "http", legal_pages, legal_htmls)


def _scrape_with_browser(url: str) -> tuple[dict | None, str | None]:
    try:
        with _BrowserFetcher() as browser:
            fetched, reason = browser.fetch(url)
            if not fetched:
                return None, reason
            final_url, html = fetched
            homepage_css = browser.last_css
            parser = _parse(html)
            pages = []
            for crawl_url in _find_crawl_candidates(final_url, parser, _MAX_BROWSER_CRAWL_PAGES):
                page, _ = browser.fetch(crawl_url)
                if page:
                    pages.append(page)
            # Legal pages are only listed in this tier - each browser fetch is slow
            legal_pages = _find_legal_pages(final_url, parser, [])
            return _assemble(final_url, parser, pages, homepage_css, "browser", legal_pages), None
    except Exception as e:  # browser binary missing, launch failure, etc.
        logger.warning(f"Browser tier unavailable: {e}")
        return None, None


def _has_analyzable_content(result: dict) -> bool:
    return len(result.get("text_excerpt") or "") >= _MIN_ANALYZABLE_CHARS or bool(
        result.get("meta_description") or result.get("og_description")
    )


def scrape_website(url: str) -> tuple[dict | None, str | None]:
    """Returns (result, None) on success or (None, reason) - reason is one
    of FAILURE_REASONS.

    result: {
      title, meta_description, og_title, og_description, og_site_name,
      og_image, favicon, theme_color,
      text_excerpt (homepage + crawled pages' content text, concatenated),
      declared_colors (from CSS custom properties like --primary/--brand -
        high confidence these ARE the brand's colors),
      colors (any other hex literals found - lower confidence, for the LLM
        to use its judgment on),
      fonts (font-family values actually declared on the page),
      pages_scraped (URLs actually fetched, for transparency),
      fetch_method ("http" or "browser"),
      logo (best logo URL: JSON-LD logo > <img> marked logo > icon > og:image),
      structured_data ({types, name, legal_name, logo, same_as, country, telephone}
        from schema.org JSON-LD), schema_types (its @type list),
      social_links ({platform: url}), legal_pages ([{type, url}]),
      region_signals (the evidence) + regions_detected (["US", "UAE/GCC", "India"] subset, strongest first),
      site_disclaimers (the site's own disclaimer sentences, verbatim),
      certifications (credentials the site claims, e.g. "ISO 27001", "SOC 2"),
    }
    """
    url = (url or "").strip()
    if not url:
        return None, "invalid_url"
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    fetched, reason = _fetch_raw(url, require_html=True)
    plain_result = _scrape_plain(*fetched) if fetched else None

    needs_browser = (plain_result is None and reason in _BROWSER_RETRY_REASONS) or (
        plain_result is not None and len(_parse(fetched[1]).text(_MIN_USEFUL_TEXT_CHARS)) < _MIN_USEFUL_TEXT_CHARS
    )
    if needs_browser and _browser_installed():
        browser_result, browser_reason = _scrape_with_browser(url)
        if browser_result and _has_analyzable_content(browser_result):
            return browser_result, None
        if plain_result is None and browser_reason:
            reason = browser_reason

    if plain_result is None:
        return None, reason
    if not _has_analyzable_content(plain_result):
        return None, "thin_content"
    return plain_result, None


def fetch_website(url: str) -> dict | None:
    """Best-effort wrapper around scrape_website() for callers that don't
    need the failure reason."""
    result, _reason = scrape_website(url)
    return result
