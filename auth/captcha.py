"""Slider-puzzle verification shown before account creation.

Flow:
  GET  /api/auth/captcha/challenge -> background with a hole + the loose piece
  POST /api/auth/captcha/verify    -> {challenge_id, x, track} -> captcha_token
  POST /api/auth/register          -> must include that captcha_token (one use)

The correct x only ever lives server-side (Flask's default session cookie is
signed, not encrypted, so it can't hold the answer). This stops casual
scripted signups; email verification remains the real gate.
"""

import base64
import io
import json
import random
import secrets
import statistics
import threading
import time

from flask import Blueprint, jsonify, request
from PIL import Image, ImageDraw, ImageFilter

from config import Config

captcha_bp = Blueprint("captcha", __name__)

CANVAS_W, CANVAS_H = 320, 160
PIECE = 56  # square bounding box of the jigsaw mask
CHALLENGE_TTL = 120  # seconds to solve a challenge
PASS_TTL = 120  # seconds to use the resulting captcha_token
MAX_ATTEMPTS = 3
CHALLENGE_RATE_LIMIT = 30  # challenges per IP per minute


# --- storage --------------------------------------------------------------

class _MemoryStore:
    """Single-process fallback for dev/tests when Redis isn't running."""

    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()

    def _live(self, key):
        item = self._data.get(key)
        if item and item[1] < time.time():
            self._data.pop(key, None)
            return None
        return item

    def set(self, key, value, ttl):
        with self._lock:
            self._data[key] = (value, time.time() + ttl)

    def get(self, key):
        with self._lock:
            item = self._live(key)
            return item[0] if item else None

    def pop(self, key):
        with self._lock:
            item = self._live(key)
            self._data.pop(key, None)
            return item[0] if item else None

    def incr(self, key, ttl):
        with self._lock:
            item = self._live(key)
            count = (int(item[0]) if item else 0) + 1
            expires = item[1] if item else time.time() + ttl
            self._data[key] = (str(count), expires)
            return count


class _RedisStore:
    def __init__(self, client):
        self._r = client

    def set(self, key, value, ttl):
        self._r.set(key, value, ex=ttl)

    def get(self, key):
        return self._r.get(key)

    def pop(self, key):
        pipe = self._r.pipeline()  # MULTI/EXEC, so a token can't be redeemed twice
        pipe.get(key)
        pipe.delete(key)
        value, _ = pipe.execute()
        return value

    def incr(self, key, ttl):
        count = int(self._r.incr(key))
        if count == 1:
            self._r.expire(key, ttl)
        return count


_store = None
_store_lock = threading.Lock()


def get_store():
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                try:
                    import redis

                    client = redis.Redis.from_url(
                        Config.REDIS_URL, decode_responses=True, socket_connect_timeout=0.5
                    )
                    client.ping()
                    _store = _RedisStore(client)
                except Exception:  # noqa: BLE001
                    print("[captcha] Redis unavailable - using in-memory store (single process only)")
                    _store = _MemoryStore()
    return _store


# --- image generation -----------------------------------------------------

def _background() -> Image.Image:
    """Soft pastel blobs, blurred - different every time, no asset files needed."""
    base = tuple(random.randint(225, 250) for _ in range(3))
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), base)
    draw = ImageDraw.Draw(img)
    for _ in range(random.randint(9, 14)):
        r = random.randint(35, 95)
        cx, cy = random.randint(-20, CANVAS_W + 20), random.randint(-20, CANVAS_H + 20)
        color = tuple(random.randint(140, 250) for _ in range(3))
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    img = img.filter(ImageFilter.GaussianBlur(26))
    # Light grain so the hole edge isn't trivially found by a flat-colour diff.
    noise = Image.effect_noise((CANVAS_W, CANVAS_H), 18).convert("RGB")
    return Image.blend(img, noise, 0.06)


def _piece_mask() -> Image.Image:
    """Jigsaw piece: 44x44 body with a knob on top and one on the right."""
    mask = Image.new("L", (PIECE, PIECE), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 12, 44, 55), radius=5, fill=255)
    d.ellipse((22 - 8, 12 - 8, 22 + 8, 12 + 8), fill=255)
    d.ellipse((44 - 8, 34 - 8, 44 + 8, 34 + 8), fill=255)
    return mask


def _outline(mask: Image.Image) -> Image.Image:
    edges = mask.filter(ImageFilter.FIND_EDGES)
    return edges.point(lambda v: 255 if v > 40 else 0)


def _cut_hole(bg: Image.Image, mask: Image.Image, x: int, y: int, darkness: int):
    shade = Image.new("RGBA", (PIECE, PIECE), (40, 40, 40, darkness))
    bg.paste(shade, (x, y), mask.point(lambda v: v * darkness // 255))
    bg.paste((255, 255, 255), (x, y), _outline(mask).point(lambda v: v * 140 // 255))


def _png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def build_challenge():
    """Returns (target_x, target_y, background PNG, piece PNG)."""
    bg = _background()
    mask = _piece_mask()
    x = random.randint(PIECE + 24, CANVAS_W - PIECE - 8)
    y = random.randint(8, CANVAS_H - PIECE - 8)

    piece = Image.new("RGBA", (PIECE, PIECE), (0, 0, 0, 0))
    piece.paste(bg.crop((x, y, x + PIECE, y + PIECE)), (0, 0), mask)
    piece.paste((255, 255, 255, 230), (0, 0), _outline(mask))

    _cut_hole(bg, mask, x, y, darkness=150)

    # One decoy hole well away from the real one (like the reference design).
    decoy_candidates = [
        dx for dx in range(PIECE + 24, CANVAS_W - PIECE - 8) if abs(dx - x) > PIECE + 10
    ]
    if decoy_candidates:
        dx = random.choice(decoy_candidates)
        dy = random.randint(8, CANVAS_H - PIECE - 8)
        _cut_hole(bg, mask, dx, dy, darkness=random.randint(120, 170))

    return x, y, bg, piece


# --- verification ---------------------------------------------------------

def _track_looks_human(track, source: str) -> bool:
    """Loose sanity check on the drag path: [[x, y, t_ms], ...]."""
    if not isinstance(track, list) or len(track) > 2000:
        return False
    try:
        points = [(float(p[0]), float(p[1]), float(p[2])) for p in track]
    except (TypeError, ValueError, IndexError):
        return False

    if source == "keyboard":
        return len(points) >= 3 and points[-1][2] - points[0][2] >= 300

    if len(points) < 8:
        return False
    duration = points[-1][2] - points[0][2]
    if not 250 <= duration <= 20000:
        return False
    # Perfectly uniform steps = scripted movement.
    steps = [b[0] - a[0] for a, b in zip(points, points[1:]) if b[2] > a[2]]
    if len(steps) < 4 or statistics.pstdev(steps) < 0.3:
        return False
    return True


def issue_pass_token() -> str:
    token = secrets.token_urlsafe(32)
    get_store().set(f"captcha:pass:{token}", "1", PASS_TTL)
    return token


def consume_pass_token(token) -> bool:
    """Single use - called by register()."""
    if not token or not isinstance(token, str) or len(token) > 128:
        return False
    return get_store().pop(f"captcha:pass:{token}") is not None


@captcha_bp.route("/challenge", methods=["GET"])
def challenge():
    store = get_store()
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    if store.incr(f"captcha:rate:{ip}", 60) > CHALLENGE_RATE_LIMIT:
        return jsonify({"success": False, "error": "Too many attempts. Please wait a minute."}), 429

    x, y, bg, piece = build_challenge()
    challenge_id = secrets.token_urlsafe(16)
    store.set(f"captcha:ch:{challenge_id}", json.dumps({"x": x, "y": y}), CHALLENGE_TTL)
    return jsonify({
        "success": True,
        "challenge_id": challenge_id,
        "width": CANVAS_W,
        "height": CANVAS_H,
        "piece_size": PIECE,
        "piece_y": y,
        "bg": _png_b64(bg),
        "piece": _png_b64(piece),
    })


@captcha_bp.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True) or {}
    challenge_id = str(data.get("challenge_id") or "")[:64]
    store = get_store()

    raw = store.get(f"captcha:ch:{challenge_id}")
    if not raw:
        return jsonify({"success": False, "expired": True, "error": "Puzzle expired. Try a new one."}), 400
    target = json.loads(raw)

    try:
        x = float(data.get("x"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid answer"}), 400

    tolerance = Config.CAPTCHA_TOLERANCE_PX
    source = "keyboard" if data.get("source") == "keyboard" else "pointer"
    if abs(x - target["x"]) <= tolerance and _track_looks_human(data.get("track"), source):
        store.pop(f"captcha:ch:{challenge_id}")
        return jsonify({"success": True, "captcha_token": issue_pass_token()})

    attempts = store.incr(f"captcha:att:{challenge_id}", CHALLENGE_TTL)
    if attempts >= MAX_ATTEMPTS:
        store.pop(f"captcha:ch:{challenge_id}")
        return jsonify({"success": False, "expired": True, "error": "Too many tries. Here's a new puzzle."}), 400
    return jsonify({"success": False, "error": "Not quite - try again."}), 400
