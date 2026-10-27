"""Content Calendar: the weekly posting goal and what goes on each day.

A day on the calendar can hold
  posts      - what the user created that day (db.posts_created_between)
  scheduled  - posts queued to publish (db.ScheduledPost)
  occasions  - festivals / holidays in the user's markets
  suggestion - a suggested slot: a day to post on to reach the weekly goal,
               with an idea from the user's saved feed when there is one

"A post" means a brief the user generated: refining or regenerating it is the
same post. Days and weeks follow the user's own clock (tz = minutes ahead of
UTC, sent by the browser); weeks run Monday to Sunday. Loading the calendar
never calls the LLM - suggestions only use a feed that is already saved.
"""

from datetime import date, datetime, timedelta, timezone

# Which weekdays (0 = Monday) to suggest for a goal of n posts a week
PREFERRED_DAYS = {
    1: [2], 2: [1, 3], 3: [0, 2, 4], 4: [0, 1, 3, 4], 5: [0, 1, 2, 3, 4], 6: [0, 1, 2, 3, 4, 5], 7: [0, 1, 2, 3, 4, 5, 6],
}
_FESTIVAL_REGIONS = {"US": "USA", "India": "India"}
_STREAK_WEEKS = 26


def _tz(tz_minutes: int) -> timezone:
    return timezone(timedelta(minutes=max(-840, min(840, int(tz_minutes or 0)))))


def _day_start(day: date, tz: timezone) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_progress(user_id: int, tz_minutes: int = 0, now: datetime | None = None) -> dict:
    """This week's goal, how many posts are done, per-day counts and the
    streak of weeks (ending with this one, or the last finished one) the goal was met."""
    from db import get_weekly_post_goal, posts_created_between

    tz = _tz(tz_minutes)
    today = (now or datetime.now(timezone.utc)).astimezone(tz).date()
    monday = _monday(today)
    goal = get_weekly_post_goal(user_id)

    first = monday - timedelta(weeks=_STREAK_WEEKS)
    posts = posts_created_between(user_id, _day_start(first, tz), _day_start(monday + timedelta(days=7), tz))
    per_day: dict[date, int] = {}
    for post in posts:
        day = post["created_at"].astimezone(tz).date()
        per_day[day] = per_day.get(day, 0) + 1

    def week_total(week_monday: date) -> int:
        return sum(per_day.get(week_monday + timedelta(days=i), 0) for i in range(7))

    done = week_total(monday)
    streak = 1 if done >= goal else 0
    week = monday - timedelta(weeks=1)
    while week >= first and week_total(week) >= goal:  # a week still in progress doesn't break the streak
        streak += 1
        week -= timedelta(weeks=1)
    return {
        "goal": goal,
        "done": done,
        "remaining": max(0, goal - done),
        "goal_met": done >= goal,
        "streak_weeks": streak,
        "week_start": monday.isoformat(),
        "today": today.isoformat(),
        "days": [{"date": (monday + timedelta(days=i)).isoformat(), "count": per_day.get(monday + timedelta(days=i), 0)}
                 for i in range(7)],
    }


def _slot_days(goal: int, monday: date, today: date, posted: set[date], remaining: int) -> list[date]:
    """The days to suggest posting on: what is still needed this week (today
    onward, days without a post, the usual days for this goal first), then next week's usual days."""
    preferred = PREFERRED_DAYS.get(goal, PREFERRED_DAYS[3])
    open_days = [monday + timedelta(days=i) for i in range(7)
                 if monday + timedelta(days=i) >= today and monday + timedelta(days=i) not in posted]
    open_days.sort(key=lambda d: (d.weekday() not in preferred, d))
    this_week = sorted(open_days[:remaining])
    next_monday = monday + timedelta(days=7)
    return this_week + [next_monday + timedelta(days=i) for i in preferred]


def _ideas_for_slots(slots: list[date], feed: dict | None, occasions: list[dict]) -> dict[date, dict]:
    """An idea per slot from the saved feed: an occasion's idea on the last
    slot before it (within a week), then trending ideas, then post types."""
    groups = (feed or {}).get("groups") or {}
    assigned: dict[date, dict] = {}
    for idea in groups.get("dates") or []:
        occasion = next((o for o in occasions if o["name"].lower() in (idea.get("occasion") or "").lower()), None)
        if not occasion:
            continue
        when = date.fromisoformat(occasion["date"])
        before = [s for s in slots if s not in assigned and timedelta(0) <= when - s <= timedelta(days=7)]
        if before:
            assigned[max(before)] = {**idea, "group": "dates"}
    rest = [{**i, "group": "trending"} for i in groups.get("trending") or []]
    rest += [{**i, "group": "playbook"} for i in groups.get("playbook") or []]
    for slot in slots:
        if slot not in assigned and rest:
            assigned[slot] = rest.pop(0)
    return assigned


def calendar_month(user_id: int, year: int, month: int, tz_minutes: int = 0, now: datetime | None = None) -> dict:
    """Everything the calendar page shows for one month (whole weeks, Monday first)."""
    from db import get_user_brand_profile, posts_created_between, scheduled_posts_between
    from services.festival_service import get_upcoming_festivals
    from services.trend_service import cached_idea_feed

    tz = _tz(tz_minutes)
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(tz).date()
    first = date(year, month, 1)
    grid_start = _monday(first)
    last = (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
    grid_end = _monday(last) + timedelta(days=7)  # exclusive

    days: dict[str, dict] = {}
    day = grid_start
    while day < grid_end:
        days[day.isoformat()] = {"date": day.isoformat(), "in_month": day.month == month, "posts": [], "scheduled": [],
                                 "occasions": [], "suggestion": None, "autopilot": None}
        day += timedelta(days=1)

    for post in posts_created_between(user_id, _day_start(grid_start, tz), _day_start(grid_end, tz)):
        key = post["created_at"].astimezone(tz).date().isoformat()
        days[key]["posts"].append({**post, "created_at": post["created_at"].isoformat()})
    for item in scheduled_posts_between(user_id, _day_start(grid_start, tz), _day_start(grid_end, tz)):
        local = item["scheduled_at"].astimezone(tz)
        days[local.date().isoformat()]["scheduled"].append(
            {**item, "scheduled_at": item["scheduled_at"].isoformat(), "time": local.strftime("%H:%M")})

    # Autopilot posts still waiting for approval (services/autopilot_service.py)
    try:
        from db import list_autopilot_posts

        for post in list_autopilot_posts(user_id, limit=60, statuses=("awaiting_approval",)):
            if post["slot_date"] in days:
                days[post["slot_date"]]["autopilot"] = {k: post[k] for k in ("id", "status", "idea_title", "approval_request_id", "slot_at")}
    except Exception:  # noqa: BLE001 - the calendar works without Autopilot
        pass

    profile = get_user_brand_profile(user_id) or {}
    markets = {_FESTIVAL_REGIONS[r] for r in profile.get("compliance_regions") or profile.get("regions_detected") or []
               if r in _FESTIVAL_REGIONS}
    occasions = [f for f in get_upcoming_festivals(days_ahead=max(0, (grid_end - today).days), today=today)
                 if f["region"] in markets]
    for occasion in occasions:
        if occasion["date"] in days:
            days[occasion["date"]]["occasions"].append({"name": occasion["name"], "region": occasion["region"]})

    progress = week_progress(user_id, tz_minutes, now)
    monday = _monday(today)
    posted = {date.fromisoformat(d["date"]) for d in progress["days"] if d["count"]}
    slots = _slot_days(progress["goal"], monday, today, posted, progress["remaining"])
    feed = cached_idea_feed(user_id) if profile else None
    ideas = _ideas_for_slots(slots, feed, occasions)
    for slot in slots:
        if slot.isoformat() in days and not days[slot.isoformat()]["autopilot"]:
            idea = ideas.get(slot)
            days[slot.isoformat()]["suggestion"] = {
                "idea": {k: idea.get(k) for k in ("title", "summary", "prompt", "platform", "format", "group")} if idea else None,
            }

    return {
        "month": f"{year:04d}-{month:02d}",
        "today": today.isoformat(),
        "days": list(days.values()),
        "progress": progress,
        "has_brand_profile": bool(profile),
        "plan": _plan(feed, markets, today),
    }


PLAN_IDEAS = 8
PLAN_OCCASION_DAYS = 45


def _plan(feed: dict | None, markets: set, today: date) -> dict:
    """The planner's "To plan" panel: ideas from the saved "Ideas for you"
    feed (upcoming dates first, then trending, then post types that work) and
    the occasions in the user's markets over the next weeks. Whatever month is
    shown, this is about what's ahead from today."""
    from services.festival_service import get_upcoming_festivals

    groups = (feed or {}).get("groups") or {}
    ideas = []
    for group in ("dates", "trending", "playbook"):
        for idea in groups.get(group) or []:
            if len(ideas) < PLAN_IDEAS and idea.get("title"):
                ideas.append({**{k: idea.get(k) for k in ("title", "summary", "prompt", "platform", "format", "occasion")},
                              "group": group})
    occasions = [{"name": f["name"], "date": f["date"], "region": f["region"]}
                 for f in get_upcoming_festivals(days_ahead=PLAN_OCCASION_DAYS, today=today) if f["region"] in markets]
    return {"ideas": ideas, "occasions": occasions[:8]}
