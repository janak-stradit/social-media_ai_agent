"""Application-wide logging: one format for gunicorn, Flask, every module and
the structured events (services/observability.py).

Every line carries a UTC timestamp, level, source, and - inside a request -
the request id and user id, so one request can be followed across all lines.

LOG_FORMAT=text (default) - readable in `docker logs`, key=value fields:
    2026-10-01 10:57:44.123Z INFO    http       POST /api/auth/login 200 182ms  req=3f2a9c1d7b4e user=7 ip=203.0.113.4
LOG_FORMAT=json - one JSON object per line, for CloudWatch / Datadog / ELK.
LOG_LEVEL=INFO (default). DEBUG also shows static files, health checks and
progress polling, which are hidden at INFO.
"""

import json
import logging
import logging.config
import os
import re
import sys
from datetime import datetime, timezone

try:  # LOG_* settings live in .env, which config.py loads later than this runs
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:  # pragma: no cover
    pass

SERVICE = os.environ.get("SERVICE_NAME", "avir")
VERSION = os.environ.get("APP_VERSION", "dev")[:12]
ENVIRONMENT = os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or "production"

# Fields that belong to the log record itself, not extra context
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "fields"}
_CONTEXT = ("request_id", "user_id")
_SENSITIVE_KEYS = re.compile(r"token|invite|code|key|password|secret|sig|reset|verify|auth", re.IGNORECASE)
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")


def mask_email(text: str) -> str:
    """jane.doe@acme.com -> j***@acme.com (enough to recognise, not to harvest)."""
    return _EMAIL.sub(r"\1***@\2", str(text))


def redact_query(query: str) -> str:
    """Values of token-like query parameters (invite links, reset links,
    verification links, API keys) never reach the logs."""
    if not query:
        return ""
    parts = []
    for pair in query.split("&"):
        key, sep, _ = pair.partition("=")
        parts.append(f"{key}=***" if sep and _SENSITIVE_KEYS.search(key) else pair)
    return "&".join(parts)


class RequestContextFilter(logging.Filter):
    """Adds request_id and user_id to every record logged during a request."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from flask import g, has_request_context, session

            if has_request_context():
                if not hasattr(record, "request_id"):
                    record.request_id = getattr(g, "request_id", None)
                if not hasattr(record, "user_id"):
                    record.user_id = session.get("user_id")
        except Exception:  # noqa: BLE001, S110 - context is optional, logging must go on
            pass
        return True


def _fields(record: logging.LogRecord) -> dict:
    """Structured fields: `extra={"fields": {...}}` plus any extra= attributes."""
    out = dict(getattr(record, "fields", None) or {})
    for key, value in vars(record).items():
        if key not in _RESERVED and key not in _CONTEXT and not key.startswith("_"):
            out.setdefault(key, value)
    return {k: v for k, v in out.items() if v is not None}


def _short_logger(name: str) -> str:
    """services.media_service -> media_service; gunicorn.error -> gunicorn."""
    if name.startswith("gunicorn"):
        return "gunicorn"
    return name.rsplit(".", 1)[-1] if name.startswith(("services.", "agents.", "api.", "auth.")) else name.replace("avir.", "")


def _format_value(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, separators=(",", ":"))
    return f'"{text}"' if (" " in text or not text) and not text.startswith(("[", "{")) else text


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z"
        line = f"{ts} {record.levelname:<7} {_short_logger(record.name):<14} {record.getMessage()}"
        tail = " ".join(f"{k}={_format_value(v)}" for k, v in _fields(record).items())
        ctx = " ".join(
            f"{label}={getattr(record, key)}"
            for key, label in (("request_id", "req"), ("user_id", "user"))
            if getattr(record, key, None) is not None
        )
        extra = "  ".join(p for p in (tail, ctx) if p)
        if extra:
            line += "  " + extra
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "service": SERVICE,
            "env": ENVIRONMENT,
            "version": VERSION,
        }
        for key in _CONTEXT:
            if getattr(record, key, None) is not None:
                payload[key] = getattr(record, key)
        payload.update(_fields(record))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


# Chatty libraries: only their warnings and errors
_QUIET = ("urllib3", "botocore", "boto3", "s3transfer", "openai", "httpx", "httpcore", "chromadb",
          "PIL", "matplotlib", "filelock", "huggingface_hub", "sentence_transformers", "transformers", "asyncio")


def logging_config() -> dict:
    """dictConfig used by the app (configure_logging) and by gunicorn
    (gunicorn.conf.py logconfig_dict), so both write the same format."""
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    fmt = "json" if os.environ.get("LOG_FORMAT", "text").lower() == "json" else "text"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request": {"()": RequestContextFilter}},
        "formatters": {
            "text": {"()": TextFormatter},
            "json": {"()": JsonFormatter},
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": fmt,
                "filters": ["request"],
            }
        },
        "root": {"level": level, "handlers": ["stdout"]},
        "loggers": {
            # gunicorn's own messages (boot, worker timeouts) in the same format;
            # its access log is off - the app logs requests itself (with ids)
            "gunicorn.error": {"level": "INFO", "handlers": ["stdout"], "propagate": False},
            "gunicorn.access": {"level": "WARNING", "handlers": [], "propagate": False},
            # Flask's dev server prints its own access lines - ours replace them
            "werkzeug": {"level": "WARNING"},
            **{name: {"level": "WARNING"} for name in _QUIET},
        },
    }


def configure_logging() -> None:
    """Idempotent: safe to call from create_app, scripts and gunicorn."""
    root = logging.getLogger()
    if getattr(root, "_avir_configured", False):
        return
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    logging.config.dictConfig(logging_config())
    logging.captureWarnings(True)
    root._avir_configured = True  # type: ignore[attr-defined]
