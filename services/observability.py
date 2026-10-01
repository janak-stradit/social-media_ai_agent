"""Structured events for generation flows (generate, image.generate,
image.edit, image.select, refine), written through the app's logging
(logging_setup.py) as the "events" source with key=value fields, so context
and image-lineage problems can be traced by conversation, run and image ids:

    ... INFO    events         image.edit  conversation_id=14 asset_id=88 parent_asset_id=87 status=completed  req=3f2a... user=7

    docker logs socialmedia-web-1 2>&1 | grep 'image.edit'
"""

import logging

logger = logging.getLogger("avir.events")


def log_event(event: str, **fields) -> None:
    """Fields with None values are dropped. Never raises."""
    try:
        logger.info(event, extra={"fields": {k: v for k, v in fields.items() if v is not None}})
    except Exception:  # noqa: BLE001, S110 - logging must never break a request
        pass


def estimate_tokens(*texts: str | None) -> int:
    """Rough token estimate (~4 characters per token) for logging context size."""
    return sum(len(t) for t in texts if t) // 4
