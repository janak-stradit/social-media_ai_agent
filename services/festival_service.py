"""Upcoming US holidays and Indian festivals, surfaced as a distinct
"Festive" category of Suggested Storyline - seasonal/greeting content ideas,
independent of competitor posts. Computed live (deterministic, no LLM call
needed for the calendar itself), not persisted.
"""

import calendar
from datetime import date, timedelta

# Indian festivals follow a lunisolar calendar and shift every year - these
# are real, verified dates for 2026 and must be updated annually. US holidays
# below are either fixed-date or "Nth weekday of month" and are computed
# programmatically further down, so those stay correct for any year.
INDIAN_FESTIVALS_2026 = [
    ("Ganesh Chaturthi", date(2026, 9, 14)),
    ("Dussehra / Vijayadashami", date(2026, 10, 20)),
    ("Karwa Chauth", date(2026, 10, 29)),
    ("Diwali", date(2026, 11, 8)),
    ("Bhai Dooj", date(2026, 11, 11)),
    ("Chhath Puja", date(2026, 11, 15)),
    ("Guru Nanak Jayanti", date(2026, 11, 24)),
]

US_FIXED_HOLIDAYS = [
    # (name, month, day)
    ("Veterans Day", 11, 11),
    ("Christmas", 12, 25),
    ("New Year's Day", 1, 1),
    ("Independence Day", 7, 4),
]


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """weekday: 0=Monday ... 6=Sunday. n: 1st, 2nd, 3rd, 4th occurrence."""
    days = [d for d in calendar.Calendar().itermonthdates(year, month) if d.month == month and d.weekday() == weekday]
    return days[n - 1]


def _us_computed_holidays(year: int) -> list[tuple[str, date]]:
    return [
        ("Labor Day", _nth_weekday(year, 9, 0, 1)),
        ("Columbus Day", _nth_weekday(year, 10, 0, 2)),
        ("Thanksgiving", _nth_weekday(year, 11, 3, 4)),
    ]


def get_upcoming_festivals(days_ahead: int = 60, today: date | None = None) -> list[dict]:
    """Returns upcoming US holidays and Indian festivals within the next
    `days_ahead` days, sorted by date, each with days_until and a suggested
    content angle."""
    today = today or date.today()
    window_end = today + timedelta(days=days_ahead)

    candidates: list[tuple[str, date, str]] = []

    for name, month, day in US_FIXED_HOLIDAYS:
        for year in (today.year, today.year + 1):
            try:
                candidates.append((name, date(year, month, day), "USA"))
            except ValueError:
                continue

    for year in (today.year, today.year + 1):
        for name, d in _us_computed_holidays(year):
            candidates.append((name, d, "USA"))

    for name, d in INDIAN_FESTIVALS_2026:
        candidates.append((name, d, "India"))

    upcoming = [
        {
            "name": name,
            "date": d.isoformat(),
            "region": region,
            "days_until": (d - today).days,
        }
        for name, d, region in candidates
        if today <= d <= window_end
    ]
    upcoming.sort(key=lambda f: f["date"])
    return upcoming
