"""Structured event logs for generation flows - one JSON line per event, so
context and image-lineage problems can be traced by conversation, run and
image ids (e.g. `docker logs web | grep '"event": "image.edit"'`)."""

import json
import logging
import sys
import time

logger = logging.getLogger("avir.events")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def log_event(event: str, **fields) -> None:
    """event: e.g. "generate", "image.generate", "image.edit", "refine".
    Fields with None values are dropped. Never raises."""
    try:
        payload = {"event": event, "ts": round(time.time(), 3)}
        payload.update({k: v for k, v in fields.items() if v is not None})
        logger.info(json.dumps(payload, default=str))
    except Exception:  # noqa: BLE001, S110 - logging must never break a request
        pass


def estimate_tokens(*texts: str | None) -> int:
    """Rough token estimate (~4 characters per token) for logging context size."""
    return sum(len(t) for t in texts if t) // 4
