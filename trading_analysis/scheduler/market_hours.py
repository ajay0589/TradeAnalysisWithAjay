from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def is_market_day(value: date | None = None) -> bool:
    current = value or datetime.now(IST).date()
    return current.weekday() < 5


def is_market_hours(now: datetime | None = None) -> bool:
    current = _as_ist(now or datetime.now(IST))
    return is_market_day(current.date()) and MARKET_OPEN <= current.time() <= MARKET_CLOSE


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
