"""The company's REAL logo on generated images and videos.

Image models can't reproduce a logo faithfully (misspelled wordmarks, wrong
colors, invented marks), so generation prompts say not to draw any logo
(NO_AI_LOGO_RULE) and the actual logo file is stamped on afterwards
(overlay_logo) - exactly the file, nothing redrawn.

Which logo (resolve_logo_path):
  - a self-serve user's brand profile logo (scraped at onboarding) - fetched
    once through the same SSRF checks as the website scraper, SVGs rendered
    to PNG with headless Chromium, cached under static/uploads/brand_logos/;
  - otherwise the dashboard's "logo" brand asset, then the root Logo.png -
    the StradIT branding those accounts have always used.
"""

import hashlib
import io
import logging
import os

import requests

from config import Config

logger = logging.getLogger(__name__)

NO_AI_LOGO_RULE = (
    "Do not draw, write or imitate any logo, wordmark, watermark or company name anywhere in the image "
    "(including on products, vehicles, screens, uniforms or signage) - the company's real logo is added "
    "afterwards. Keep the bottom-right corner free of important content."
)

_LOGO_WIDTH_RATIO = 0.16  # of the image width
_LOGO_MAX_HEIGHT_RATIO = 0.12  # of the image height
_PADDING_RATIO = 0.035  # of the shorter image side
_MIN_CONTRAST = 60  # luminance difference below which the logo gets a soft backing plate
_MAX_LOGO_BYTES = 5 * 1024 * 1024
_CACHE_DIR = os.path.join(Config.UPLOAD_FOLDER, "brand_logos")
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_logo_path(user_id: int | None) -> str | None:
    """Local PNG path of the logo to stamp for this user, or None (no logo)."""
    try:
        from db import get_user_brand_profile

        profile = get_user_brand_profile(user_id) if user_id else None
    except Exception:
        profile = None

    if profile is not None:
        # Self-serve brand: only their own logo - never fall back to StradIT's
        return _cached_logo(profile.get("logo_url")) if profile.get("logo_url") else None
    return _default_logo_path()


def _default_logo_path() -> str | None:
    try:
        from db import get_brand_asset

        asset = get_brand_asset("logo")
        if asset and asset.get("filename"):
            path = os.path.join(_PROJECT_ROOT, "static", "img", "brand", asset["filename"])
            if os.path.exists(path):
                return path
    except Exception:
        pass
    path = os.path.join(_PROJECT_ROOT, "Logo.png")
    return path if os.path.exists(path) else None


def _cached_logo(logo_url: str) -> str | None:
    """Downloads (SSRF-checked) and normalizes a logo URL to a transparent
    PNG, cached by URL so each logo is fetched once."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(_CACHE_DIR, hashlib.sha256(logo_url.encode()).hexdigest()[:24] + ".png")
    if os.path.exists(cache_path):
        return cache_path

    data = _fetch_logo_bytes(logo_url)
    if not data:
        return None
    try:
        png = _render_svg(data) if _looks_like_svg(logo_url, data) else _normalize_raster(data)
    except Exception as e:
        logger.warning(f"Could not process logo {logo_url}: {e}")
        return None
    if not png:
        return None
    with open(cache_path, "wb") as f:
        f.write(png)
    return cache_path


def _fetch_logo_bytes(url: str) -> bytes | None:
    if url.startswith("/static/"):  # already a local file (e.g. an uploaded logo)
        path = os.path.join(_PROJECT_ROOT, url.lstrip("/"))
        return open(path, "rb").read() if os.path.exists(path) else None

    from services.website_scraper_service import _REQUEST_HEADERS, _check_url

    for _hop in range(5):
        if _check_url(url):
            return None
        try:
            resp = requests.get(url, timeout=10, allow_redirects=False, stream=True, headers=_REQUEST_HEADERS)
        except Exception as e:
            logger.warning(f"Failed to fetch logo {url}: {e}")
            return None
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
            url = requests.compat.urljoin(url, resp.headers["Location"])
            continue
        if resp.status_code != 200:
            return None
        data = resp.raw.read(_MAX_LOGO_BYTES + 1, decode_content=True)
        return data if len(data) <= _MAX_LOGO_BYTES else None
    return None


def _looks_like_svg(url: str, data: bytes) -> bool:
    return ".svg" in url.lower().split("?")[0] or b"<svg" in data[:2048].lower()


def _render_svg(svg: bytes) -> bytes | None:
    """Rasterizes an SVG logo to a transparent PNG with headless Chromium
    (Pillow can't read SVG). The SVG is loaded as an <img> data URI, so any
    scripts in it never run."""
    import base64

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    data_uri = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()
    html = (
        "<html><body style='margin:0;background:transparent'>"
        f"<img id='logo' src='{data_uri}' style='display:block;height:400px;width:auto'>"
        "</body></html>"
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 2400, "height": 400})
            page.route("**/*", lambda route: route.abort())  # no network from the logo page
            page.set_content(html)
            return page.locator("#logo").screenshot(omit_background=True)
        finally:
            browser.close()


def _normalize_raster(data: bytes) -> bytes:
    """RGBA PNG, cropped to the logo; a flat near-white background (a logo
    saved as JPEG / without transparency) is made transparent, so it isn't
    stamped as a white box."""
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    img.load()
    img = img.convert("RGBA")

    corners = [img.getpixel(xy) for xy in ((0, 0), (img.width - 1, 0), (0, img.height - 1), (img.width - 1, img.height - 1))]
    if all(c[3] > 250 and min(c[:3]) > 235 for c in corners):
        pixels = [(r, g, b, 0) if min(r, g, b) > 235 else (r, g, b, a) for r, g, b, a in img.getdata()]
        img.putdata(pixels)

    bbox = img.getchannel("A").getbbox()
    if bbox:
        img = img.crop(bbox)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _luminance(pixel) -> float:
    r, g, b = pixel[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def overlay_logo(image_path: str, logo_path: str) -> bool:
    """Stamps the logo onto the image file in place. Picks the corner where
    it reads best (contrast against the background, least visual clutter),
    preferring bottom-right; adds a soft translucent plate only when no
    corner gives enough contrast. Returns False (image untouched) on error."""
    from PIL import Image, ImageDraw, ImageFilter, ImageStat

    try:
        base = Image.open(image_path).convert("RGBA")
        logo = Image.open(logo_path).convert("RGBA")
    except Exception as e:
        logger.warning(f"Could not open image/logo: {e}")
        return False

    bbox = logo.getchannel("A").getbbox()
    if bbox:
        logo = logo.crop(bbox)
    width = int(base.width * _LOGO_WIDTH_RATIO)
    height = int(width * logo.height / logo.width)
    max_height = int(base.height * _LOGO_MAX_HEIGHT_RATIO)
    if height > max_height:
        height = max_height
        width = int(height * logo.width / logo.height)
    if width < 8 or height < 8:
        return False
    logo = logo.resize((width, height), Image.LANCZOS)

    alpha = logo.getchannel("A")
    opaque = [p for p, a in zip(logo.getdata(), alpha.getdata()) if a > 128]
    logo_lum = sum(_luminance(p) for p in opaque) / len(opaque) if opaque else 128

    pad = int(min(base.width, base.height) * _PADDING_RATIO)
    corners = {
        "bottom-right": (base.width - width - pad, base.height - height - pad),
        "bottom-left": (pad, base.height - height - pad),
        "top-right": (base.width - width - pad, pad),
        "top-left": (pad, pad),
    }
    # Clutter = edge density around the spot (padding included, so a
    # headline right next to it counts) - text and detail are edge-heavy.
    # Bottom-right is strongly preferred (prompts ask the model to keep it
    # clear); low contrast there is solved with a plate, not by moving the
    # logo onto a headline (seen: logo landed on top of the title text when
    # scoring used colour variation, which is low for thin text on sky).
    gray = base.convert("L")
    best = None
    for name, (x, y) in corners.items():
        area = (max(0, x - pad), max(0, y - pad), min(base.width, x + width + pad), min(base.height, y + height + pad))
        clutter = ImageStat.Stat(gray.crop(area).filter(ImageFilter.FIND_EDGES)).mean[0]
        contrast = abs(ImageStat.Stat(gray.crop((x, y, x + width, y + height))).mean[0] - logo_lum)
        score = 0.5 * contrast - 3 * clutter + (25 if name == "bottom-right" else 0)
        if best is None or score > best[0]:
            best = (score, name, x, y, contrast)
    _, _, x, y, contrast = best

    if contrast < _MIN_CONTRAST:
        plate_pad = max(4, int(height * 0.18))
        plate = Image.new("RGBA", base.size, (0, 0, 0, 0))
        fill = (0, 0, 0, 140) if logo_lum > 128 else (255, 255, 255, 170)
        ImageDraw.Draw(plate).rounded_rectangle(
            (x - plate_pad, y - plate_pad, x + width + plate_pad, y + height + plate_pad),
            radius=plate_pad,
            fill=fill,
        )
        base = Image.alpha_composite(base, plate)

    base.alpha_composite(logo, (x, y))
    try:
        fmt = "PNG" if image_path.lower().endswith(".png") else "JPEG"
        (base if fmt == "PNG" else base.convert("RGB")).save(image_path, format=fmt)
    except Exception as e:
        logger.warning(f"Could not save branded image: {e}")
        return False
    return True
