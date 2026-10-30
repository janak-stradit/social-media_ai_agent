"""Every AI (LLM) call, kept for admins to look into: what was sent (the
system and user prompts), what came back, which provider and model, tokens,
cost, how long it took, and which part of the app made it, for which user.
Like a LangSmith trace, but inside the app: the prompts carry customers' brand
profiles and posts, so they stay in this database, admins only
(Admin -> AI calls, /admin/ai-calls).

  record     called by services/llm_service.py for every attempt (a retry or a
             fallback to the next provider is its own row, so failures show too)
  who        the user and request from the Flask request when there is one;
             work on other threads or in the scheduler is tied to its user by
             bind() (services/content_pipeline.py binds a whole generation,
             and carry() takes that along into its worker threads)
  where      the first function outside llm_service that asked for the call,
             e.g. agents/caption_agent.py:generate_caption
  group_id   calls made for the same request or generation share it, so one
             post's calls (caption, hashtags, review...) can be seen together
  keep       LLM_TRACE_DAYS (30) days, then purge_old() deletes them.
             Off with LLM_TRACING=false.

Nothing here ever raises into the AI call.
"""

import contextvars
import logging
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

MAX_TEXT = 100_000  # characters kept per prompt / answer
_context: contextvars.ContextVar[dict] = contextvars.ContextVar("llm_trace", default={})
_HERE = os.path.normcase(os.path.abspath(__file__))
_SKIP = {_HERE, os.path.join(os.path.dirname(_HERE), "llm_service.py")}  # exact files: not tests/test_llm_trace.py


def enabled() -> bool:
    from config import Config

    return getattr(Config, "LLM_TRACING", True)


@contextmanager
def bind(**fields):
    """Tie the AI calls made inside to a user / run / group (fields: user_id,
    run_id, group_id, request_id). A new group_id is made when none is given."""
    current = dict(_context.get())
    current.update({k: v for k, v in fields.items() if v is not None})
    current.setdefault("group_id", uuid.uuid4().hex[:16])
    token = _context.set(current)
    try:
        yield current
    finally:
        _context.reset(token)


def carry(fn):
    """fn, run with this thread's trace context - for ThreadPoolExecutor.submit.
    Call it once per submit: each task gets its own copy."""
    ctx = contextvars.copy_context()
    return lambda *args, **kwargs: ctx.run(fn, *args, **kwargs)


def _who() -> dict:
    out = dict(_context.get())
    try:
        from flask import g, has_request_context, session

        if has_request_context():
            out.setdefault("user_id", session.get("user_id"))
            out.setdefault("request_id", getattr(g, "request_id", None))
    except Exception:  # noqa: BLE001, S110 - who made it is optional
        pass
    out.setdefault("group_id", out.get("request_id"))
    return out


def _caller() -> str:
    """The first function outside the LLM service on the stack: "agents/caption_agent.py:generate_caption"."""
    root = os.path.dirname(os.path.dirname(_HERE))
    frame = sys._getframe(2)  # noqa: SLF001 - cheap, unlike inspect.stack()
    while frame is not None:
        path = frame.f_code.co_filename
        if os.path.normcase(os.path.abspath(path)) not in _SKIP:
            rel = os.path.relpath(path, root) if path.startswith(root) else os.path.basename(path)
            return f"{rel.replace(os.sep, '/')}:{frame.f_code.co_name}"[:160]
        frame = frame.f_back
    return "unknown"


def record(*, kind: str, provider: str, model: str | None, system_prompt: str, user_prompt: str,
           output: str | None, status: str, error: str | None = None, usage: dict | None = None,
           started: float, ended: float, temperature=None, max_tokens=None, reasoning_effort=None,
           streamed: bool = False, attempt: int = 1) -> None:
    """One attempt at an AI call. status: ok / empty / error."""
    if not enabled():
        return
    try:
        from db import save_llm_call

        who = _who()
        usage = usage or {}
        save_llm_call({
            "user_id": who.get("user_id"), "request_id": (who.get("request_id") or None),
            "group_id": who.get("group_id"), "run_id": who.get("run_id"), "feature": _caller(),
            "kind": kind, "provider": provider, "model": (model or "")[:120] or None, "status": status,
            "error": (error or "")[:1000] or None, "system_prompt": (system_prompt or "")[:MAX_TEXT],
            "user_prompt": (user_prompt or "")[:MAX_TEXT], "output": (output or "")[:MAX_TEXT] if output is not None else None,
            "input_tokens": int(usage.get("input_tokens") or 0), "output_tokens": int(usage.get("output_tokens") or 0),
            "cost_usd": float(usage.get("cost_usd") or 0.0), "latency_ms": int(max(0.0, ended - started) * 1000),
            "temperature": temperature, "max_tokens": max_tokens, "reasoning_effort": reasoning_effort,
            "streamed": bool(streamed), "attempt": attempt,
        })
    except Exception as err:  # noqa: BLE001 - tracing must never break an AI call
        logger.debug(f"AI call not traced: {err}")


def purge_old(now: datetime | None = None) -> int:
    """Scheduler: delete calls older than LLM_TRACE_DAYS. How many."""
    from config import Config
    from db import purge_llm_calls

    days = int(getattr(Config, "LLM_TRACE_DAYS", 30) or 30)
    return purge_llm_calls((now or datetime.now(timezone.utc)) - timedelta(days=days))
