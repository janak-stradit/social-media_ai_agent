"""Invitation outreach (Admin -> Invitations): how the emails are written and
measured.

- Unsubscribe: every invitation carries a personal unsubscribe link
  (/u/<token>) and the List-Unsubscribe headers mail apps turn into a
  one-click button. Unsubscribed addresses are never emailed again
  (db.EmailSuppression).
- Clicks: links go through /i/<token>, which counts the click and goes on to
  sign-up. Company mail filters open links to check them, so clicks that look
  automated (known scanner user agents, or within a minute of sending) are
  not counted.
- Plain notes: a short personal email written from a template with merge
  fields ({greeting}, {company}, {first_line}, {preview_link}, ...) - the
  format that reaches the inbox and gets replies best for people who don't
  know us yet. The designed HTML invitation is still available.
"""

import re
from datetime import datetime, timezone

from config import Config

UNSUBSCRIBE_SALT = "avir-unsubscribe"

# Link checkers (Microsoft Safe Links, Proofpoint, Mimecast, Barracuda, ...)
# and tools - opening a link this way is not a person clicking.
_AUTOMATED_UA = re.compile(
    r"bot|crawl|spider|slurp|preview|scan|safelinks|proofpoint|mimecast|barracuda|forcepoint|symantec|"
    r"trend ?micro|sophos|fortinet|zscaler|checkpoint|headless|phantom|python|curl|wget|java/|go-http|okhttp|"
    r"libwww|httpclient|axios|node-fetch|facebookexternalhit|whatsapp|slackbot|linkedinbot|skypeuripreview",
    re.IGNORECASE,
)
SCANNER_WINDOW_SECONDS = 60  # scanners open links as the email arrives

TEMPLATES = {
    "preview": {
        "label": "Show them their posts (recommended)",
        "subject": "social posts for {company}",
        "body": (
            "{greeting}\n\n"
            "{first_line}\n\n"
            "I used AVIR AI, our AI content tool, to draft 3 LinkedIn posts for {company} from your website. "
            "You can see them here, no signup needed:\n"
            "{preview_link}\n\n"
            "Worth a look?\n\n"
            "{sender_name}\n"
            "AVIR AI, a StradIT product"
        ),
    },
    "note": {
        "label": "Short personal note",
        "subject": "{company} on LinkedIn",
        "body": (
            "{greeting}\n\n"
            "{first_line}\n\n"
            "I'd like to invite you to try AVIR AI. It turns one short brief into on-brand posts, captions and images "
            "for LinkedIn, Instagram and Facebook. Your account is ready to set up here:\n"
            "{invite_link}\n\n"
            "Would that be useful for {company}?\n\n"
            "{sender_name}\n"
            "AVIR AI, a StradIT product"
        ),
    },
}
MERGE_FIELDS = ("greeting", "first_name", "name", "company", "city", "industry", "first_line", "preview_link",
                "invite_link", "sender_name")


# ── Unsubscribe links ───────────────────────────────────────────────────────
def _serializer():
    from itsdangerous import URLSafeSerializer

    return URLSafeSerializer(Config.SECRET_KEY, salt=UNSUBSCRIBE_SALT)


def unsubscribe_token(email: str) -> str:
    return _serializer().dumps((email or "").strip().lower())


def email_from_unsubscribe_token(token: str) -> str | None:
    from itsdangerous import BadSignature

    try:
        email = _serializer().loads(token or "")
    except BadSignature:
        return None
    return email if isinstance(email, str) and "@" in email else None


def postal_address() -> str:
    """The sender's postal address for the email footer (US CAN-SPAM needs one
    in marketing email): Admin -> Invitations setting, else .env."""
    try:
        from db import get_setting

        saved = (get_setting("company_postal_address", "") or "").strip()
    except Exception:  # noqa: BLE001 - no database: the .env value
        saved = ""
    return saved or (getattr(Config, "COMPANY_POSTAL_ADDRESS", "") or "").strip()


# ── Clicks ──────────────────────────────────────────────────────────────────
def looks_automated(user_agent: str | None, sent_at_iso: str | None = None, now: datetime | None = None) -> bool:
    """True when a link open is probably a mail filter or tool, not a person."""
    if not user_agent or _AUTOMATED_UA.search(user_agent):
        return True
    if sent_at_iso:
        try:
            sent = datetime.fromisoformat(sent_at_iso)
            if sent.tzinfo is None:
                sent = sent.replace(tzinfo=timezone.utc)
            if ((now or datetime.now(timezone.utc)) - sent).total_seconds() < SCANNER_WINDOW_SECONDS:
                return True
        except ValueError:
            pass
    return False


# ── Writing the email ───────────────────────────────────────────────────────
_LEGAL_SUFFIX = re.compile(r"[\s,]+(pvt\.?|private|ltd\.?|limited|llp|llc|inc\.?|incorporated|corp\.?|corporation|co\.?|plc|gmbh|"
                           r"fzco|fze|fz-llc|l\.l\.c\.?)$", re.IGNORECASE)


def short_company(name: str | None) -> str:
    """How a person would write the company in an email: "Ontime Packers and
    Movers Pvt. Ltd." -> "Ontime Packers and Movers"."""
    text = (name or "").strip()
    previous = None
    while previous != text:
        previous = text
        text = _LEGAL_SUFFIX.sub("", text).strip()
    return text or (name or "").strip()


def merge_values(contact: dict, links: dict, sender_name: str) -> dict:
    name = (contact.get("name") or "").strip()
    first_name = name.split()[0] if name else ""
    company = short_company(contact.get("company"))
    greeting = f"Hi {first_name}," if first_name else (f"Hi {company} team," if company else "Hi there,")
    preview = contact.get("preview") or {}
    return {
        "greeting": greeting,
        "first_name": first_name or "there",
        "name": name,
        "company": company or "your company",
        "city": contact.get("city") or "",
        "industry": contact.get("industry") or "",
        "first_line": preview.get("first_line") or "",
        "preview_link": links.get("preview") or links.get("invite") or "",
        "invite_link": links.get("invite") or "",
        "sender_name": sender_name or "The AVIR AI team",
    }


def render(template: str, values: dict) -> str:
    """Fills {fields}; unknown fields stay as typed. Lines left empty by a
    blank field (e.g. no {first_line}) don't leave a gap."""
    def fill(match):
        key = match.group(1)
        return str(values.get(key, match.group(0))) if key in values else match.group(0)

    text = re.sub(r"\{([a-z_]+)\}", fill, template or "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def footer_text(unsubscribe_url: str, address: str) -> str:
    lines = ["--", ("You're getting this because we think AVIR AI could help your business. "
                    f"Don't want emails like this? Unsubscribe: {unsubscribe_url}"),
             "AVIR AI is a product of StradIT"]
    if address:
        lines.append(address)
    return "\n".join(lines)


def build_note(template_key: str, subject: str | None, body: str | None, contact: dict, links: dict,
               sender_name: str) -> tuple[str, str]:
    """(subject, body text without the footer) for one contact."""
    base = TEMPLATES.get(template_key) or TEMPLATES["note"]
    values = merge_values(contact, links, sender_name)
    return (render(subject or base["subject"], values)[:200] or "An invitation to AVIR AI",
            render(body or base["body"], values))
