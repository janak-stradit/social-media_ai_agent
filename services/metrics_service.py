"""How published posts are doing: likes, comments, shares, saves, reach and
views, fetched from each platform for the posts the app published
(db.PublishedPost), with the same connected-account tokens used to publish.

  facebook   Graph API: the post's reactions / comments / shares, plus
             impressions and reach from post insights when the Page allows it
  instagram  Graph API: like and comment counts, plus reach, saves, shares
             and views from media insights
  linkedin   socialActions: likes and comments (reach needs LinkedIn's
             organisation analytics, which most apps don't have)

A platform that refuses one of the extra numbers (missing permission, post
type without insights) still gives what it can; nothing here ever raises.
The scheduler refreshes posts every 6 hours in their first two days (and once
right at 48 hours, the age every post is scored at), then daily, and stops
after 30 days. Each fetch is kept (db.PostMetricSnapshot). Follower counts
for each connected account are taken once a day (refresh_followers).
"""

import logging
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from config import Config

logger = logging.getLogger(__name__)
TIMEOUT = 20


def _graph(path: str, token: str, **params) -> dict:
    resp = requests.get(
        # Instagram Login tokens ("IG...") are answered by graph.instagram.com
        f"https://{'graph.instagram.com' if (token or '').startswith('IG') else 'graph.facebook.com'}"
        f"/{Config.META_GRAPH_VERSION}/{path.lstrip('/')}",
        params={**params, "access_token": token},
        timeout=TIMEOUT,
    )
    body = resp.json() if resp.content else {}
    if resp.status_code >= 400 or "error" in body:
        raise RuntimeError((body.get("error") or {}).get("message") or f"HTTP {resp.status_code}")
    return body


def _insights(body: dict) -> dict:
    """{metric: value} from a Graph insights response."""
    out = {}
    for item in body.get("data") or []:
        values = item.get("values") or [{}]
        value = values[0].get("value") if values else item.get("total_value", {}).get("value")
        if value is None and item.get("total_value"):
            value = item["total_value"].get("value")
        if isinstance(value, (int, float)):
            out[item.get("name")] = int(value)
    return out


def fetch_facebook(post_id: str, token: str) -> dict:
    body = _graph(
        post_id, token, fields="reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0),shares"
    )
    metrics = {
        "likes": (body.get("reactions") or {}).get("summary", {}).get("total_count", 0),
        "comments": (body.get("comments") or {}).get("summary", {}).get("total_count", 0),
        "shares": (body.get("shares") or {}).get("count", 0),
    }
    try:
        stats = _insights(_graph(f"{post_id}/insights", token, metric="post_impressions_unique,post_impressions"))
        metrics["reach"] = stats.get("post_impressions_unique", 0)
        metrics["views"] = stats.get("post_impressions", 0)
    except RuntimeError as err:  # insights need the read_insights permission
        logger.info(f"Facebook insights unavailable for {post_id}: {err}")
    return metrics


def fetch_instagram(media_id: str, token: str) -> dict:
    body = _graph(media_id, token, fields="like_count,comments_count")
    metrics = {"likes": body.get("like_count", 0), "comments": body.get("comments_count", 0)}
    try:
        stats = _insights(_graph(f"{media_id}/insights", token, metric="reach,saved,shares,views"))
        metrics.update(
            reach=stats.get("reach", 0),
            saves=stats.get("saved", 0),
            shares=stats.get("shares", 0),
            views=stats.get("views", 0),
        )
    except RuntimeError as err:
        logger.info(f"Instagram insights unavailable for {media_id}: {err}")
    return metrics


def fetch_linkedin(post_urn: str, token: str) -> dict:
    resp = requests.get(
        f"https://api.linkedin.com/rest/socialActions/{quote(post_urn, safe='')}",
        headers={
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": Config.LINKEDIN_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"LinkedIn answered {resp.status_code}")
    body = resp.json()
    return {
        "likes": (body.get("likesSummary") or {}).get("totalLikes", 0),
        "comments": (body.get("commentsSummary") or {}).get("aggregatedTotalComments", 0),
    }


FETCHERS = {"facebook": fetch_facebook, "instagram": fetch_instagram, "linkedin": fetch_linkedin}


# ── Followers, once a day per connected account ───────────────────────


def followers_facebook(page_id: str, token: str) -> int:
    body = _graph(page_id, token, fields="followers_count,fan_count")
    return int(body.get("followers_count") or body.get("fan_count") or 0)


def followers_instagram(ig_user_id: str, token: str) -> int:
    return int(_graph(ig_user_id, token, fields="followers_count").get("followers_count") or 0)


def followers_linkedin(author_urn: str, token: str) -> int:
    """Company pages only: LinkedIn doesn't give apps a person's follower count."""
    if not author_urn.startswith("urn:li:organization:"):
        raise RuntimeError("LinkedIn only shares follower counts for company pages.")
    resp = requests.get(
        f"https://api.linkedin.com/rest/networkSizes/{quote(author_urn, safe='')}",
        params={"edgeType": "COMPANY_FOLLOWED_BY_COMPANY"},
        headers={
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": Config.LINKEDIN_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"LinkedIn answered {resp.status_code}")
    return int(resp.json().get("firstDegreeSize") or 0)


FOLLOWER_FETCHERS = {"facebook": followers_facebook, "instagram": followers_instagram, "linkedin": followers_linkedin}


def refresh_followers(now: datetime | None = None) -> dict:
    """Scheduler: count followers for every connected account not counted today.
    A refusal is saved too (as no count), so each account is asked once a day."""
    from db import accounts_due_for_follower_count, save_follower_count
    from services.publish_service import accounts

    now = now or datetime.now(timezone.utc)
    result = {"counted": 0, "failed": 0}
    by_user: dict[int, dict] = {}
    for user_id, platform in accounts_due_for_follower_count(now):
        fetcher = FOLLOWER_FETCHERS.get(platform)
        if user_id not in by_user:
            by_user[user_id] = accounts(user_id)
        account = by_user[user_id].get(platform)
        if not fetcher or not account or not account.get("token") or account.get("expired") or not account.get("account_id"):
            save_follower_count(user_id, platform, now, None, "Not available for this account.")
            continue
        try:
            save_follower_count(user_id, platform, now, fetcher(account["account_id"], account["token"]))
            result["counted"] += 1
        except Exception as err:  # noqa: BLE001 - one account's error must not stop the others
            save_follower_count(user_id, platform, now, None, str(err))
            result["failed"] += 1
    if result["counted"] or result["failed"]:
        logger.info(f"Follower counts: {result}")
    return result


def refresh_post(post: dict, account: dict | None) -> bool:
    """Fetch and store one post's numbers. True when it worked."""
    from db import save_post_metrics

    fetcher = FETCHERS.get(post["platform"])
    if not fetcher:
        return False
    if not account or not account.get("token") or account.get("expired"):
        save_post_metrics(post["id"], None, "The account is no longer connected.")
        return False
    try:
        save_post_metrics(post["id"], fetcher(post["platform_post_id"], account["token"]))
        return True
    except Exception as err:  # noqa: BLE001 - one platform's error must not stop the others
        save_post_metrics(post["id"], None, str(err))
        logger.info(f"Metrics for {post['platform']} post {post['platform_post_id']} failed: {err}")
        return False


def refresh_due(now: datetime | None = None) -> dict:
    """Scheduler (hourly): refresh every post that is due."""
    from db import published_posts_due_for_metrics
    from services.publish_service import accounts

    result = {"refreshed": 0, "failed": 0}
    by_user: dict[int, dict] = {}
    for post in published_posts_due_for_metrics(now or datetime.now(timezone.utc)):
        if post["user_id"] not in by_user:
            by_user[post["user_id"]] = accounts(post["user_id"])
        ok = refresh_post(post, by_user[post["user_id"]].get(post["platform"]))
        result["refreshed" if ok else "failed"] += 1
    if result["refreshed"] or result["failed"]:
        logger.info(f"Post metrics: {result}")
    return result
