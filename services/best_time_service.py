"""When to post: the best days and hours for each platform, in the user's own
clock (tz = minutes ahead of UTC, from the browser).

From the user's own published posts (db.PublishedPost with fetched results)
once a platform has MIN_POSTS of them: the hours and weekdays whose posts got
the most engagement on average. Until then, common starting points for each
platform - general guidance, labelled as such, not a promise.
"""

from datetime import datetime, timedelta, timezone

MIN_POSTS = 8
MIN_PER_SLOT = 2
LOOKBACK_DAYS = 180
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Common starting points (weekday 0 = Monday, hour in local time)
TYPICAL = {
    "linkedin": {"days": [1, 2, 3], "hours": [9, 10, 12]},
    "facebook": {"days": [0, 1, 2, 3, 4], "hours": [9, 12, 15]},
    "instagram": {"days": [0, 2, 4], "hours": [11, 13, 19]},
    "youtube": {"days": [4, 5, 6], "hours": [15, 17, 19]},
}


def _tz(minutes: int) -> timezone:
    return timezone(timedelta(minutes=max(-840, min(840, int(minutes or 0)))))


def _ranked(posts: list[tuple[int, int]], top: int) -> list[int]:
    """Keys (hour or weekday) by average engagement, best first, each with enough posts."""
    totals: dict[int, list[int]] = {}
    for key, engagement in posts:
        totals.setdefault(key, []).append(engagement)
    good = [(sum(v) / len(v), k) for k, v in totals.items() if len(v) >= MIN_PER_SLOT]
    return [k for _, k in sorted(good, reverse=True)[:top]]


def _label(days: list[int], hours: list[int]) -> str:
    def hour_text(h: int) -> str:
        return f"{h % 12 or 12} {'am' if h < 12 else 'pm'}"

    return f"{', '.join(DAY_NAMES[d] for d in sorted(days))} around {', '.join(hour_text(h) for h in sorted(hours))}"


def next_slot(days: list[int], hours: list[int], now_local: datetime) -> datetime:
    """The next of these days at least an hour from now (local), at the
    best-ranked hour still ahead that day (hours are in rank order)."""
    earliest = now_local + timedelta(hours=1)
    for offset in range(0, 15):
        day = (now_local + timedelta(days=offset)).date()
        if day.weekday() not in days:
            continue
        for hour in hours:
            slot = datetime(day.year, day.month, day.day, hour, tzinfo=now_local.tzinfo)
            if slot >= earliest:
                return slot
    return earliest.replace(minute=0, second=0, microsecond=0)


def best_times(user_id: int, tz_minutes: int = 0, now: datetime | None = None) -> dict:
    """{platform: {source, days, hours, label, next_slot, posts}} for every platform."""
    from db import published_posts_for_timing

    tz = _tz(tz_minutes)
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz)
    rows = published_posts_for_timing(user_id, now_local - timedelta(days=LOOKBACK_DAYS))
    out = {}
    for platform, typical in TYPICAL.items():
        mine = [(r["published_at"].astimezone(tz), r["engagement"]) for r in rows if r["platform"] == platform]
        days = hours = None
        if len(mine) >= MIN_POSTS:
            hours = _ranked([(when.hour, e) for when, e in mine], 2)
            days = _ranked([(when.weekday(), e) for when, e in mine], 3)
        source = "your_posts" if hours and days else "typical"
        if source == "typical":
            days, hours = typical["days"], typical["hours"]
        out[platform] = {
            "source": source,
            "days": days,
            "hours": hours,
            "label": _label(days, hours),
            "next_slot": next_slot(days, hours, now_local).isoformat(),
            "posts": len(mine),
        }
    return out
