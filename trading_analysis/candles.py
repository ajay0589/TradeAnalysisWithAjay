from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from trading_analysis.models import Candle


TIMEFRAME_ALIASES = {
    "monthly": "month",
    "month": "month",
    "m": "month",
    "weekly": "week",
    "week": "week",
    "w": "week",
    "daily": "day",
    "day": "day",
    "d": "day",
    "1day": "day",
    "1d": "day",
    "hour": "60minute",
    "hourly": "60minute",
    "1hour": "60minute",
    "1h": "60minute",
    "60min": "60minute",
    "60minute": "60minute",
    "2hour": "120minute",
    "2hours": "120minute",
    "2h": "120minute",
    "120min": "120minute",
    "120minute": "120minute",
    "15min": "15minute",
    "15m": "15minute",
    "15minute": "15minute",
}

TIMEFRAME_LABELS = {
    "month": "Monthly",
    "week": "Weekly",
    "day": "Daily",
    "60minute": "1 hour",
    "120minute": "2 hour",
    "15minute": "15 min",
}

FETCH_INTERVALS = {
    "day": "day",
    "60minute": "60minute",
    "15minute": "15minute",
}


@dataclass(frozen=True)
class CandleWindow:
    from_time: datetime | None
    to_time: datetime | None
    days: int | None = None


def normalize_timeframe(value: str | None) -> str:
    key = (value or "day").strip().lower().replace("_", "").replace("-", "")
    if key not in TIMEFRAME_ALIASES:
        allowed = ", ".join(TIMEFRAME_LABELS.values())
        raise ValueError(f"Unsupported timeframe '{value}'. Use one of: {allowed}.")
    return TIMEFRAME_ALIASES[key]


def timeframe_label(timeframe: str) -> str:
    return TIMEFRAME_LABELS[normalize_timeframe(timeframe)]


def source_timeframe(timeframe: str) -> str:
    normalized = normalize_timeframe(timeframe)
    if normalized in {"month", "week"}:
        return "day"
    if normalized == "120minute":
        return "60minute"
    return normalized


def fetch_interval(timeframe: str) -> str:
    source = source_timeframe(timeframe)
    return FETCH_INTERVALS[source]


def safe_symbol_filename(symbol: str) -> str:
    return symbol.upper().replace(" ", "_").replace("/", "_").replace("&", "AND")


def candle_path(root: str | Path, timeframe: str, symbol_or_stem: str) -> Path:
    root_path = Path(root)
    source = source_timeframe(timeframe)
    stem = safe_symbol_filename(symbol_or_stem)
    if source == "day":
        return root_path / f"{stem}.csv"
    return root_path / source / f"{stem}.csv"


def parse_datetime(value: str | None, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    stripped = value.strip()
    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
    for fmt in formats:
        try:
            parsed = datetime.strptime(stripped, fmt)
            if fmt == "%Y-%m-%d" and end_of_day:
                return parsed.replace(hour=23, minute=59, second=59)
            return parsed
        except ValueError:
            continue
    return datetime.fromisoformat(stripped)


def candle_window(
    from_date: str | None = None,
    to_date: str | None = None,
    days: int | None = None,
    now: datetime | None = None,
) -> CandleWindow:
    now = now or datetime.now()
    to_time = parse_datetime(to_date, end_of_day=True) or now
    from_time = parse_datetime(from_date)
    if days is not None and days > 0:
        from_time = to_time - timedelta(days=days)
    return CandleWindow(from_time=from_time, to_time=to_time, days=days)


def apply_window(candles: list[Candle], window: CandleWindow) -> list[Candle]:
    output = []
    for candle in candles:
        from_time = _comparable_time(window.from_time, candle.timestamp)
        to_time = _comparable_time(window.to_time, candle.timestamp)
        if from_time and candle.timestamp < from_time:
            continue
        if to_time and candle.timestamp > to_time:
            continue
        output.append(candle)
    return output


def convert_timeframe(candles: list[Candle], timeframe: str) -> list[Candle]:
    normalized = normalize_timeframe(timeframe)
    if normalized == "week":
        return _resample(candles, lambda candle: candle.timestamp.isocalendar()[:2])
    if normalized == "month":
        return _resample(candles, lambda candle: (candle.timestamp.year, candle.timestamp.month))
    if normalized == "120minute":
        return _resample_intraday(candles, 120)
    return candles


def prepare_candles(candles: list[Candle], timeframe: str, window: CandleWindow) -> list[Candle]:
    return convert_timeframe(apply_window(candles, window), timeframe)


def _resample(candles: list[Candle], key_fn) -> list[Candle]:
    groups: dict[tuple[int, int], list[Candle]] = {}
    for candle in sorted(candles, key=lambda item: item.timestamp):
        groups.setdefault(tuple(key_fn(candle)), []).append(candle)

    output = []
    for group in groups.values():
        first = group[0]
        last = group[-1]
        output.append(
            Candle(
                timestamp=last.timestamp,
                open=first.open,
                high=max(candle.high for candle in group),
                low=min(candle.low for candle in group),
                close=last.close,
                volume=sum(candle.volume for candle in group),
                open_interest=last.open_interest,
            )
        )
    return output


def _resample_intraday(candles: list[Candle], minutes: int) -> list[Candle]:
    groups: dict[tuple[int, int, int, int], list[Candle]] = {}
    for candle in sorted(candles, key=lambda item: item.timestamp):
        bucket_minute = ((candle.timestamp.hour * 60 + candle.timestamp.minute) // minutes) * minutes
        key = (
            candle.timestamp.year,
            candle.timestamp.timetuple().tm_yday,
            bucket_minute // 60,
            bucket_minute % 60,
        )
        groups.setdefault(key, []).append(candle)

    output = []
    for group in groups.values():
        first = group[0]
        last = group[-1]
        output.append(
            Candle(
                timestamp=last.timestamp,
                open=first.open,
                high=max(candle.high for candle in group),
                low=min(candle.low for candle in group),
                close=last.close,
                volume=sum(candle.volume for candle in group),
                open_interest=last.open_interest,
            )
        )
    return output


def _comparable_time(value: datetime | None, timestamp: datetime) -> datetime | None:
    if value is None:
        return None
    if timestamp.tzinfo is not None and value.tzinfo is None:
        return value.replace(tzinfo=timestamp.tzinfo)
    if timestamp.tzinfo is None and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value
