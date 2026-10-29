"""The Inbox: comments on posts the app published, with a suggested reply
in the brand's voice that the person approves before anything is posted.

  fetch     every 30 minutes in a post's first day, every 3 hours for a week
            (db.published_posts_due_for_comments), with the connected
            account's token. The account's own comments are left out.
              facebook   Graph API  /{post}/comments
              instagram  Graph API  /{media}/comments
              linkedin   socialActions/{post}/comments (names aren't shared:
                         "LinkedIn member")
  suggest   one small AI call, only when the person opens a comment without a
            draft: what kind of comment it is (question, lead, praise,
            complaint, other) and a short reply using only facts from the
            brand profile and the post; anything it doesn't know becomes a
            [PLACEHOLDER] to fill in. The brand's compliance rules are checked
            on it like on a caption. Its cost counts towards the user's credits.
  reply     only when the person approves it, with their edits: posted as a
            reply under the comment, or (Facebook and Instagram, within 7
            days of the comment) sent to the commenter as a private message.

Needs the platforms' comment permissions (Meta: pages_read_engagement,
pages_manage_engagement, instagram_manage_comments, and for private replies
pages_messaging / instagram_manage_messages; LinkedIn: Community Management
API). Without them fetching fails quietly and the Inbox stays empty.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

from config import Config

logger = logging.getLogger(__name__)
TIMEOUT = 20
KINDS = ("question", "lead", "praise", "complaint", "other")
PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9 _/-]{2,40}\]")
MAX_REPLY = 1200


class ReplyError(ValueError):
    """Shown to the person as is."""


def _graph_url(path: str, token: str) -> str:
    host = "graph.instagram.com" if (token or "").startswith("IG") else "graph.facebook.com"
    return f"https://{host}/{Config.META_GRAPH_VERSION}/{path.lstrip('/')}"


def _graph(method: str, path: str, token: str, **params) -> dict:
    resp = requests.request(method, _graph_url(path, token), params={**params, "access_token": token}, timeout=TIMEOUT)
    body = resp.json() if resp.content else {}
    if resp.status_code >= 400 or "error" in body:
        raise RuntimeError((body.get("error") or {}).get("message") or f"HTTP {resp.status_code}")
    return body


def _when(value) -> datetime:
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00").replace("+0000", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _linkedin_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "LinkedIn-Version": Config.LINKEDIN_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0"}


# ── Fetching ──────────────────────────────────────────────────────────


def fetch_facebook(post_id: str, account: dict) -> list[dict]:
    body = _graph("GET", f"{post_id}/comments", account["token"], fields="id,from{id,name},message,created_time",
                  filter="toplevel", order="reverse_chronological", limit=50)
    return [{"id": c["id"], "author": (c.get("from") or {}).get("name"), "text": c.get("message") or "",
             "at": _when(c.get("created_time"))}
            for c in body.get("data") or [] if (c.get("from") or {}).get("id") != account.get("account_id")]


def fetch_instagram(media_id: str, account: dict) -> list[dict]:
    body = _graph("GET", f"{media_id}/comments", account["token"], fields="id,username,text,timestamp", limit=50)
    own = (account.get("name") or "").lstrip("@").lower()
    return [{"id": c["id"], "author": c.get("username"), "text": c.get("text") or "", "at": _when(c.get("timestamp"))}
            for c in body.get("data") or [] if (c.get("username") or "").lower() != own]


def fetch_linkedin(post_urn: str, account: dict) -> list[dict]:
    resp = requests.get(f"https://api.linkedin.com/rest/socialActions/{quote(post_urn, safe='')}/comments",
                        headers=_linkedin_headers(account["token"]), timeout=TIMEOUT)
    if resp.status_code >= 400:
        raise RuntimeError(f"LinkedIn answered {resp.status_code}")
    out = []
    for c in resp.json().get("elements") or []:
        if c.get("actor") == account.get("account_id"):
            continue
        out.append({"id": c.get("$URN") or c.get("commentUrn") or c.get("id"), "author": "LinkedIn member",
                    "text": (c.get("message") or {}).get("text") or "", "at": _when((c.get("created") or {}).get("time"))})
    return [c for c in out if c["id"]]


FETCHERS = {"facebook": fetch_facebook, "instagram": fetch_instagram, "linkedin": fetch_linkedin}


def notify(post: dict, now: datetime) -> None:
    """A bell notification for new comments on a post: once per post per day
    (dedupe_key), counting what is waiting on it. In a post's first day it
    says so - early replies are when they help a post most."""
    from db import count_new_comments, create_notification

    waiting, title, young = count_new_comments(post["id"])
    if not waiting:
        return
    where = (post["platform"] or "").capitalize()
    create_notification(
        post["user_id"], "comments",
        f"{waiting} new comment{'s' if waiting != 1 else ''} on your {where} post",
        (f"“{title}”. " if title else "") + ("It went out today: replying now helps it keep going."
                                             if young else "Suggested replies are ready to review in your Inbox."),
        "/inbox", f"comments:{post['id']}:{now.astimezone(timezone.utc).date().isoformat()}", when=now,
    )


def refresh_due(now: datetime | None = None) -> dict:
    """Scheduler: fetch comments for every post that is due."""
    from db import published_posts_due_for_comments, save_post_comments
    from services.publish_service import accounts

    result = {"new": 0, "failed": 0}
    by_user: dict[int, dict] = {}
    for post in published_posts_due_for_comments(now or datetime.now(timezone.utc)):
        fetcher = FETCHERS.get(post["platform"])
        if post["user_id"] not in by_user:
            by_user[post["user_id"]] = accounts(post["user_id"])
        account = by_user[post["user_id"]].get(post["platform"])
        if not fetcher or not account or not account.get("token") or account.get("expired"):
            save_post_comments(post, None)
            continue
        try:
            added = save_post_comments(post, fetcher(post["platform_post_id"], account))
            result["new"] += added
            if added:
                notify(post, now or datetime.now(timezone.utc))
        except Exception as err:  # noqa: BLE001 - one platform's error must not stop the others
            save_post_comments(post, None)
            result["failed"] += 1
            logger.info(f"Comments for {post['platform']} post {post['platform_post_id']} failed: {err}")
    if result["new"] or result["failed"]:
        logger.info(f"Post comments: {result}")
    return result


# ── Pushed by Meta as they happen (webhooks) ──────────────────────────


def webhook_signature_ok(raw: bytes, signature: str) -> bool:
    """X-Hub-Signature-256 is "sha256=<HMAC of the body>" with the app's secret:
    the Facebook app's for Pages (and Instagram through a Page), the Instagram
    app's for accounts connected with Instagram Login."""
    import hashlib
    import hmac

    sent = (signature or "").removeprefix("sha256=")
    secrets = [s for s in (Config.META_APP_SECRET, Config.INSTAGRAM_APP_SECRET) if s]
    return bool(sent) and any(
        hmac.compare_digest(hmac.new(s.encode(), raw, hashlib.sha256).hexdigest(), sent) for s in secrets)


def _webhook_comments(payload: dict) -> list[tuple[str, str, dict]]:
    """[(platform, the post's platform id, {id, author, text, at})] for new
    top-level comments by other people in a Meta webhook payload."""
    out = []
    obj = payload.get("object")
    for entry in payload.get("entry") or []:
        owner = str(entry.get("id") or "")  # the Page or Instagram account the webhook is for
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            author = value.get("from") or {}
            if str(author.get("id") or "") == owner:
                continue  # the account's own comment
            if obj == "instagram" and change.get("field") == "comments" and not value.get("parent_id"):
                media = (value.get("media") or {}).get("id")
                if media and value.get("id"):
                    out.append(("instagram", str(media), {"id": value["id"], "author": author.get("username"),
                                                          "text": value.get("text") or "", "at": datetime.now(timezone.utc)}))
            elif obj == "page" and change.get("field") == "feed" and value.get("item") == "comment" \
                    and value.get("verb") == "add" and value.get("parent_id") in (None, value.get("post_id")):
                if value.get("post_id") and value.get("comment_id"):
                    at = value.get("created_time")
                    out.append(("facebook", str(value["post_id"]), {
                        "id": value["comment_id"], "author": author.get("name"), "text": value.get("message") or "",
                        "at": datetime.fromtimestamp(at, tz=timezone.utc) if isinstance(at, (int, float)) else datetime.now(timezone.utc)}))
    return out


def handle_webhook(payload: dict, now: datetime | None = None) -> int:
    """Store the new comments in a (signature-checked) Meta webhook and ring
    the bell for them. Comments on posts the app didn't publish are ignored. New ones."""
    from db import find_published_post, save_post_comments

    now = now or datetime.now(timezone.utc)
    added = 0
    for platform, post_id, comment in _webhook_comments(payload):
        post = find_published_post(platform, post_id)
        if not post:
            continue
        new = save_post_comments(post, [comment])
        if new:
            notify(post, now)
        added += new
    return added


# ── Suggesting a reply ────────────────────────────────────────────────


def suggest(user_id: int, comment: dict, llm=None) -> tuple[dict, float]:
    """({kind, draft, note}, cost in USD) for one comment."""
    from services.brand_profile_service import build_brand_profile_block

    if llm is None:
        from services.llm_service import LLMService

        llm = LLMService()
    brand = build_brand_profile_block(user_id) or "(No brand profile saved: keep the reply general.)"
    system_prompt = f"""You reply to comments on a company's social media posts, as the company.
Return JSON: {{"kind": one of {list(KINDS)}, "reply": "...", "note": "..."}}
- kind: question (asks something), lead (sounds like a possible customer: asks about buying, pricing, a demo, working together), praise, complaint, other.
- reply: warm, plain and short (1 to 3 sentences, at most 400 characters), in the same language as the comment. Address the person by name only if a name is given.
- Use only facts from the brand context and the post below. Never invent prices, dates, features, offers or promises: write a placeholder in capitals in square brackets instead, like [YOUR PRICE] or [OPENING HOURS].
- complaint: thank them, apologise once, and move the details to a private message. Never argue.
- lead: answer briefly and invite them to a private message.
- No hashtags. Emojis only if the comment uses them.
- note: one short line for the person approving the reply when something needs their attention (a placeholder to fill in, a possible lead, a complaint to follow up), else "".

BRAND CONTEXT:
{brand}"""
    user_prompt = (f"PLATFORM: {comment['platform']}\nPOST: {comment.get('post_title') or '(not available)'}\n"
                   f"COMMENT by {comment.get('author') or 'someone'}:\n{comment['text'][:2000]}")
    result, usage = llm.generate_json(system_prompt, user_prompt, temperature=0.5, max_tokens=500,
                                      return_usage=True, reasoning_effort="minimal")
    result = result if isinstance(result, dict) else {}
    cost = float((usage or {}).get("cost_usd") or 0.0)
    draft = " ".join(str(result.get("reply") or "").split())[:MAX_REPLY]
    kind = result.get("kind") if result.get("kind") in KINDS else "other"
    note = str(result.get("note") or "").strip()[:300]
    if not draft:
        raise ReplyError("No reply suggested this time. Please try again or write your own.")

    # The brand's compliance rules, as on every caption
    from services.compliance_service import active_rules_for_user, check_caption

    rules = active_rules_for_user(user_id)
    if rules:
        checked, check_usage = check_caption(draft, comment["platform"], rules, llm)
        cost += float((check_usage or {}).get("cost_usd") or 0.0)
        if checked:
            draft = checked["caption"][:MAX_REPLY]
            if checked["needs_attention"]:
                note = (note + " " if note else "") + "Compliance: " + "; ".join(
                    f["issue"] for f in checked["flags"] if not f["auto_fixed"])[:200]
    return {"kind": kind, "draft": draft, "note": note[:300] or None}, cost


# ── Posting an approved reply ─────────────────────────────────────────


def _ready(text: str, account: dict | None) -> str:
    """The reply as it will be sent; ReplyError when it can't be yet."""
    text = (text or "").strip()
    if not text:
        raise ReplyError("Write a reply first.")
    if len(text) > MAX_REPLY:
        raise ReplyError(f"Keep the reply under {MAX_REPLY:,} characters.")
    missing = PLACEHOLDER.findall(text)
    if missing:
        raise ReplyError(f"Fill in {', '.join(dict.fromkeys(missing))} before posting.")
    if not account or not account.get("token") or account.get("expired"):
        raise ReplyError("Connect this account again in Settings to reply.")
    return text


PRIVATE_PLATFORMS = ("facebook", "instagram")
PRIVATE_DAYS = 7  # Meta takes one private reply per comment, within 7 days of it


def can_reply_privately(comment: dict, now: datetime | None = None) -> bool:
    if comment["platform"] not in PRIVATE_PLATFORMS:
        return False
    at = datetime.fromisoformat(comment["commented_at"])
    return (now or datetime.now(timezone.utc)) - at < timedelta(days=PRIVATE_DAYS)


def post_private_reply(comment: dict, text: str, account: dict | None, now: datetime | None = None) -> str | None:
    """Sends `text` to the commenter as a private message (Meta's private
    replies: needs pages_messaging / instagram_manage_messages)."""
    text = _ready(text, account)
    if not can_reply_privately(comment, now):
        raise ReplyError("A private reply is only possible on Facebook and Instagram, within 7 days of the comment.")
    try:
        body = _graph("POST", "me/messages", account["token"],
                      recipient=json.dumps({"comment_id": comment["comment_id"]}), message=json.dumps({"text": text}))
    except RuntimeError as err:
        raise ReplyError(f"{comment['platform'].capitalize()} didn't send the private reply: {err}") from err
    return body.get("message_id")


def post_reply(comment: dict, text: str, account: dict | None) -> str | None:
    """Posts `text` under the comment; the reply's id when the platform gives one."""
    text = _ready(text, account)
    platform = comment["platform"]
    try:
        if platform == "facebook":
            return _graph("POST", f"{comment['comment_id']}/comments", account["token"], message=text).get("id")
        if platform == "instagram":
            return _graph("POST", f"{comment['comment_id']}/replies", account["token"], message=text).get("id")
        if platform == "linkedin":
            post_urn = comment["platform_post_id"]
            resp = requests.post(
                f"https://api.linkedin.com/rest/socialActions/{quote(post_urn, safe='')}/comments",
                headers=_linkedin_headers(account["token"]),
                json={"actor": account.get("account_id"), "object": post_urn, "message": {"text": text},
                      "parentComment": comment["comment_id"]},
                timeout=TIMEOUT,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"LinkedIn answered {resp.status_code}")
            return resp.headers.get("x-restli-id")
    except RuntimeError as err:
        raise ReplyError(f"{platform.capitalize()} didn't accept the reply: {err}") from err
    raise ReplyError("Replies aren't available for this platform yet.")
