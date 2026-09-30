"""Live progress of a Studio Chat generation (/api/generate), so the page's
"Multi-Agent Execution Pipeline" shows the step the server is really on
instead of a timer. Polled via GET /api/generate/progress/<progress_id>.

Stored in Redis when available (production: every gunicorn worker must see
the same progress, and a poll can land on any worker); otherwise in memory,
which is fine for the single-process dev server. Best-effort throughout -
progress must never break or slow down a generation.
"""

import re
import threading
import time

from config import Config

TTL_SECONDS = 3600
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

_redis = None
_redis_checked = False
_memory: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def valid_id(progress_id) -> bool:
    return isinstance(progress_id, str) and bool(_ID_RE.match(progress_id))


def _key(user_id, progress_id) -> str:
    # Scoped per user so nobody can read another user's generation progress
    return f"genprogress:{user_id}:{progress_id}"


def _client():
    global _redis, _redis_checked  # pylint: disable=global-statement
    if not _redis_checked:
        with _lock:
            if not _redis_checked:
                try:
                    import redis

                    client = redis.Redis.from_url(Config.REDIS_URL, decode_responses=True, socket_connect_timeout=0.5)
                    client.ping()
                    _redis = client
                except Exception:  # noqa: BLE001
                    _redis = None
                _redis_checked = True
    return _redis


def get(user_id, progress_id) -> dict:
    if not valid_id(progress_id):
        return {}
    key = _key(user_id, progress_id)
    try:
        client = _client()
        if client is not None:
            return client.hgetall(key) or {}
        with _lock:
            entry = _memory.get(key)
            if entry and entry[0] > time.time():
                return dict(entry[1])
    except Exception as e:  # noqa: BLE001
        print(f"[progress] read failed: {e}")
    return {}


def set_step(user_id, progress_id, step: str, state: str) -> None:
    """state: "active" | "done" | "skipped" | "failed"."""
    if not valid_id(progress_id):
        return
    key = _key(user_id, progress_id)
    try:
        client = _client()
        if client is not None:
            # One hash field per step: parallel steps (captions + hashtags run
            # concurrently) update independently without overwriting each other
            pipe = client.pipeline()
            pipe.hset(key, step, state)
            pipe.expire(key, TTL_SECONDS)
            pipe.execute()
            return
        with _lock:
            now = time.time()
            for k in [k for k, (exp, _) in _memory.items() if exp <= now]:
                del _memory[k]
            steps = dict(_memory.get(key, (0, {}))[1])
            steps[step] = state
            _memory[key] = (now + TTL_SECONDS, steps)
    except Exception as e:  # noqa: BLE001
        print(f"[progress] write failed: {e}")
