from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
SCAN_CLOSE = time(15, 45)

# Regular NSE/BSE equity and F&O sessions, verified 2026-10-24. Muhurat
# sessions are not ordinary 09:15-15:30 sessions and are deliberately excluded.
# https://nsearchives.nseindia.com/content/circulars/FAOP65588.pdf
# https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf
# https://www.nseindia.com/resources/exchange-communication-holidays
# https://zerodha.com/marketintel/holiday-calendar/
HOLIDAYS = {
    2025: frozenset({
        "2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10",
        "2025-04-14", "2025-04-18", "2025-05-01", "2025-08-15",
        "2025-08-27", "2025-10-02", "2025-10-21", "2025-10-22",
        "2025-11-05", "2025-12-25",
    }),
    2026: frozenset({
        "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26",
        "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01",
        "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02",
        "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25",
    }),
}
# Union Budget sessions used normal trading hours, including on weekends.
# https://nsearchives.nseindia.com/content/circulars/FAOP65730.pdf
# https://nsearchives.nseindia.com/content/circulars/FAOP72352.pdf
EXTRA_SESSIONS = frozenset({"2025-02-01", "2026-02-01"})


def is_market_day(value: date | None = None) -> bool:
    current = value or datetime.now(IST).date()
    if current.year not in HOLIDAYS:
        raise ValueError(f"NSE/BSE session calendar unavailable for {current.year}; update the verified calendar")
    return (current.weekday() < 5 or current.isoformat() in EXTRA_SESSIONS) and current.isoformat() not in HOLIDAYS[current.year]


def is_market_hours(now: datetime | None = None) -> bool:
    current = _as_ist(now or datetime.now(IST))
    return is_market_day(current.date()) and MARKET_OPEN <= current.time() <= MARKET_CLOSE


def is_scan_window(now: datetime | None = None) -> bool:
    current = _as_ist(now or datetime.now(IST))
    return is_market_day(current.date()) and MARKET_OPEN <= current.time() <= SCAN_CLOSE


def next_market_open(now: datetime | None = None) -> datetime | None:
    current = _as_ist(now or datetime.now(IST))
    candidate = datetime.combine(current.date(), MARKET_OPEN, tzinfo=IST)
    if is_market_day(current.date()) and current < candidate:
        return candidate
    next_day = current.date() + timedelta(days=1)
    for _ in range(10):
        if is_market_day(next_day):
            return datetime.combine(next_day, MARKET_OPEN, tzinfo=IST)
        next_day += timedelta(days=1)
    return None


def _as_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)
