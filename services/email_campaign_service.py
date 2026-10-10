"""Admin -> Emails -> "Send now": email a segment of users, picked by industry,
account type and market. Each email is still written for that user's brand:

  ideas     three ideas from the user's own "Ideas for you" feed (like the
            weekly email; it also counts as that week's ideas email)
  festival  an idea for one upcoming occasion, written for the user's brand
            (like a festival nudge; it also goes under their header bell)

Rules: users who switched that email off are skipped; nobody gets two idea
emails within RECENT_HOURS; nobody gets the same festival email twice. A send
runs in the background (each email may need its feed written first); its
progress is stored in app_settings so any server worker can report it.
"""

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from config import Config

logger = logging.getLogger(__name__)

KINDS = {"ideas": "Ideas for you", "festival": "Festival idea"}
IDEA_EMAIL_KINDS = ("weekly_ideas", "nudge", "festival_idea", "ideas_campaign")
RECENT_HOURS = 24
WORKERS = 3
SAMPLE_SIZE = 8


def occasion_key(festival: dict) -> str:
    return f"{festival['name']}|{festival['date']}|{festival['region']}"


def upcoming_occasions(days_ahead: int = 60) -> list[dict]:
    from services.festival_service import get_upcoming_festivals

    return [{**f, "key": occasion_key(f)} for f in get_upcoming_festivals(days_ahead=days_ahead)]


def find_occasion(key: str | None) -> dict | None:
    return next((f for f in upcoming_occasions() if f["key"] == key), None) if key else None


def _region_of(festival: dict) -> str:
    """festival_service region ("USA") -> brand-profile market ("US")."""
    from services.festival_service import PROFILE_REGION_MAP

    return next((market for market, region in PROFILE_REGION_MAP.items() if region == festival["region"]), "")


def plan(kind: str, industry: str | None, account_type: str | None, region: str | None,
         occasion: dict | None) -> dict:
    """Who would get the email: {"eligible": [...], "skipped": {reason: n}}.
    A festival email only goes to users in that occasion's market."""
    from db import emailed_recently, occasion_emailed, users_in_segment

    if kind == "festival" and occasion:
        region = region or _region_of(occasion)
        if region and region != _region_of(occasion):
            return {"eligible": [], "skipped": {"other_market": 0}, "region": region}
    users = users_in_segment(industry or None, account_type or None, region or None)
    eligible, skipped = [], {"opted_out": 0, "emailed_recently": 0, "already_had_this_occasion": 0}
    for user in users:
        if not (user["ideas_ok"] if kind == "ideas" else user["nudges_ok"]):
            skipped["opted_out"] += 1
        elif emailed_recently(user["id"], IDEA_EMAIL_KINDS, hours=RECENT_HOURS):
            skipped["emailed_recently"] += 1
        elif kind == "festival" and occasion and occasion_emailed(user["id"], occasion["name"]):
            skipped["already_had_this_occasion"] += 1
        else:
            eligible.append(user)
    return {"eligible": eligible, "skipped": skipped, "region": region}


def preview(kind: str, industry=None, account_type=None, region=None, occasion_key_=None) -> dict:
    occasion = find_occasion(occasion_key_) if kind == "festival" else None
    if kind == "festival" and not occasion:
        raise ValueError("Pick an upcoming occasion.")
    result = plan(kind, industry, account_type, region, occasion)
    return {
        "count": len(result["eligible"]), "skipped": result["skipped"],
        "sample": [{"name": u["name"], "email": u["email"], "company": u["company_name"],
                    "industry": u["industry_category"], "account_type": u["account_type"]}
                   for u in result["eligible"][:SAMPLE_SIZE]],
        "occasion": occasion,
    }


# ── Sending ─────────────────────────────────────────────────────────────────


def _send_ideas(user: dict, admin_id: int) -> str:
    from services.email_service import email_context
    from services.idea_digest_service import send_ideas_email

    with email_context(kind="ideas_campaign", triggered_by=f"admin:{admin_id}"):
        outcome = send_ideas_email(user["id"], Config.APP_BASE_URL)
    return "sent" if outcome.get("sent") else "skipped"


def _send_festival(user: dict, occasion: dict, admin_id: int) -> str:
    from db import create_idea_link, create_notification, get_user_brand_profile
    from services.email_service import EmailService, email_context
    from services.idea_digest_service import _FORMATS, _PLATFORMS, unsubscribe_token
    from services.nudge_service import _idea_for

    profile = get_user_brand_profile(user["id"])
    if not profile:
        return "skipped"
    days = (date.fromisoformat(occasion["date"]) - datetime.now(timezone.utc).date()).days
    festival = {**occasion, "days_until": days}
    idea = _idea_for({"kind": "occasion", "festival": festival}, user["id"], profile)
    if not idea:
        return "skipped"
    when = "today" if days <= 0 else "tomorrow" if days == 1 else f"in {days} days"
    headline = f"{occasion['name']} is {when}"
    path = f"/dashboard?idea={create_idea_link(user['id'], idea)}"
    # Also under the bell, with the nudge's key - so the scheduler won't nudge for it again
    create_notification(user["id"], "occasion", headline, idea["title"], path,
                        f"occasion:{occasion['name']}:{occasion['date']}")
    base = Config.APP_BASE_URL.rstrip("/")
    details = {"occasion": occasion["name"], "occasion_date": occasion["date"], "idea": idea["title"],
               "industry": user["industry_category"], "account_type": user["account_type"]}
    with email_context(kind="festival_idea", triggered_by=f"admin:{admin_id}", user_id=user["id"], details=details):
        EmailService().send_nudge_email(
            to_email=user["email"], name=user.get("name") or "there", headline=headline,
            intro="Here's a post you could have ready in time.",
            idea={
                "title": idea["title"],
                "summary": idea.get("summary") or idea.get("why_now") or "",
                "makes": f"{_FORMATS.get(idea.get('format'), 'Text post')} for {_PLATFORMS.get(idea.get('platform'), 'LinkedIn')}",
                "url": base + path,
            },
            settings_url=f"{base}/settings#notifications",
            unsubscribe_url=f"{base}/api/notifications/nudges-email/unsubscribe/{unsubscribe_token(user['id'], 'nudges')}",
        )
    return "sent"


def _save_job(job: dict) -> None:
    from db import save_setting

    save_setting(f"email_job:{job['id']}", json.dumps(job))


def get_job(job_id: str) -> dict | None:
    from db import get_setting

    raw = get_setting(f"email_job:{job_id}", "")
    return json.loads(raw) if raw else None


def start(kind: str, admin_id: int, industry=None, account_type=None, region=None, occasion_key_=None,
          background: bool = True) -> dict:
    """Plans the send, then emails everyone in the background. Returns the job."""
    from services.email_service import EmailService

    if kind not in KINDS:
        raise ValueError("Unknown email type.")
    if not EmailService().enabled:
        raise ValueError("SMTP is not configured - set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD.")
    if not Config.APP_BASE_URL:
        raise ValueError("APP_BASE_URL is not set - the links in the email need it.")
    occasion = find_occasion(occasion_key_) if kind == "festival" else None
    if kind == "festival" and not occasion:
        raise ValueError("Pick an upcoming occasion.")
    planned = plan(kind, industry, account_type, region, occasion)
    job = {
        "id": uuid.uuid4().hex[:12], "kind": kind, "status": "running", "total": len(planned["eligible"]),
        "sent": 0, "skipped": 0, "failed": 0, "skipped_before": planned["skipped"],
        "filters": {"industry": industry, "account_type": account_type, "region": planned["region"],
                    "occasion": occasion and occasion["name"]},
        "admin_id": admin_id, "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
    }
    _save_job(job)
    lock = threading.Lock()

    def one(user):
        try:
            outcome = _send_ideas(user, admin_id) if kind == "ideas" else _send_festival(user, occasion, admin_id)
        except Exception as err:  # noqa: BLE001 - one user's failure never stops the rest
            logger.warning(f"Campaign {job['id']}: email to user {user['id']} failed: {str(err)[:200]}")
            outcome = "failed"
        with lock:
            job[outcome] += 1
            _save_job(job)

    def run():
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(one, planned["eligible"]))
        job["status"] = "done"
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        _save_job(job)
        logger.info(f"Email campaign {job['id']} ({kind}): {job['sent']} sent, {job['skipped']} skipped, "
                    f"{job['failed']} failed")

    if background:
        threading.Thread(target=run, daemon=True, name=f"email-campaign-{job['id']}").start()
    else:
        run()
    return job
