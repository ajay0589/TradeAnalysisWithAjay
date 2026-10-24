from datetime import date, datetime, timedelta

import pytest

from trading_analysis.live_timing import IST, expected_closed_bar
from trading_analysis.mcx.rules import market_window
from trading_analysis.models import Candle
from trading_analysis.nifty_lab.setups import SETUPS, technical_read
from trading_analysis.scalping.rules import aggregate
from trading_analysis.scheduler.market_hours import (
    is_market_day, is_market_hours, is_scan_window, next_market_open,
)


@pytest.mark.parametrize("day", [
    date(2026, 10, 20), date(2026, 1, 15), date(2026, 10, 2),
    date(2025, 10, 21), date(2025, 10, 22), date(2026, 10, 24),
])
def test_holidays_and_weekends_are_not_regular_sessions(day):
    at = datetime.combine(day, datetime.min.time(), IST).replace(hour=10)
    assert not is_market_day(day)
    assert not is_market_hours(at)
    assert not is_scan_window(at)


@pytest.mark.parametrize("year", [2025, 2026])
def test_declared_budget_weekend_is_a_full_session(year):
    at = datetime(year, 2, 1, 10, tzinfo=IST)
    assert is_market_day(at.date())
    assert is_market_hours(at)
    assert expected_closed_bar("15minute", at) == at.replace(minute=45, hour=9)
    stamp = at.replace(hour=9, minute=15)
    assert len(aggregate([Candle(stamp, 100, 101, 99, 100, 0)])) == 1


@pytest.mark.parametrize("frame,expected", [
    ("5minute", "2026-10-19T15:25:00+05:30"),
    ("15minute", "2026-10-19T15:15:00+05:30"),
    ("60minute", "2026-10-19T15:15:00+05:30"),
    ("day", "2026-10-19T00:00:00+05:30"),
])
def test_reopening_uses_last_actual_session(frame, expected):
    assert expected_closed_bar(frame, datetime(2026, 10, 21, 9, 15, tzinfo=IST)).isoformat() == expected


def test_holiday_next_open_and_consecutive_closures():
    assert next_market_open(datetime(2026, 10, 19, 16, tzinfo=IST)) == datetime(2026, 10, 21, 9, 15, tzinfo=IST)
    assert expected_closed_bar("day", datetime(2025, 10, 23, 9, 15, tzinfo=IST)) == datetime(2025, 10, 20, tzinfo=IST)
    assert not is_market_hours(datetime(2025, 10, 21, 18, tzinfo=IST))
    assert not is_market_hours(datetime(2026, 11, 8, 18, tzinfo=IST))


def test_calendar_never_guesses_an_uncovered_year():
    with pytest.raises(ValueError, match="calendar unavailable for 2027"):
        is_market_day(date(2027, 1, 4))


def test_minute_aggregation_excludes_holiday_bars():
    stamp = datetime(2026, 10, 20, 9, 15, tzinfo=IST)
    assert not aggregate([Candle(stamp, 100, 101, 99, 100, 0)])


def test_mcx_evening_is_independent_of_equity_holiday():
    at = datetime(2026, 10, 20, 18, tzinfo=IST)
    assert not is_market_hours(at)
    assert market_window(at)


def reopening_sources():
    rows = {}
    for frame, minutes in (("5minute", 5), ("15minute", 15)):
        rows[frame] = [
            Candle(datetime(2026, 10, day, 9, 15, tzinfo=IST) + timedelta(minutes=i), 100, 101, 99, 100, 0)
            for day in (15, 16, 19, 21) for i in range(0, 375, minutes)
        ]
    rows["day"] = [Candle(datetime(2026, 10, day, tzinfo=IST), 100, 101, 99, 100, 0) for day in (15, 16, 19)]
    return rows


@pytest.mark.parametrize("setup", SETUPS)
def test_lab_accepts_real_candles_across_holiday(setup):
    result = technical_read(setup, reopening_sources(), datetime(2026, 10, 21, 10, 0, 5, tzinfo=IST))
    assert result["ready"], result


def test_lab_still_rejects_a_real_missing_bar_after_holiday():
    sources = reopening_sources()
    sources["15minute"] = [c for c in sources["15minute"] if c.timestamp != datetime(2026, 10, 19, 15, 15, tzinfo=IST)]
    result = technical_read("Nifty_Setup1", sources, datetime(2026, 10, 21, 10, 0, 5, tzinfo=IST))
    assert result["reason"] == "15minute_candle_gap"
