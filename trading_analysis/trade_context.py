"""Read-only result marks and point-in-time India VIX references."""
from datetime import datetime
from math import isfinite

from trading_analysis.live_timing import IST, as_ist, quote_is_fresh


def capture_vix(at=None, quote=None):
    from trading_analysis.live_quotes import LIVE_QUOTES
    at = as_ist(at or datetime.now(IST))
    quote = LIVE_QUOTES.get("INDIAVIX") if quote is None else quote
    fresh = quote_is_fresh(quote, at, max_age=60)
    return {"value": float(quote["price"]) if fresh else None,
            "status": "recorded" if fresh else "stale_or_unavailable",
            "quote_time": quote.get("quote_time"), "captured_at": at.isoformat(),
            "source": quote.get("source"), "instrument": "NSE:INDIA VIX"}


def result_mark(entry, direction, status, exit_price=None, quote=None, now=None, exit_time=None):
    now = as_ist(now or datetime.now(IST))
    fresh = quote_is_fresh(quote, now, max_age=30)
    mark = exit_price if status == "closed" else (quote or {}).get("price") if fresh else None
    result = {"points": None, "percent": None, "price": mark,
              "basis": "closed" if status == "closed" else "live" if fresh else "unavailable",
              "time": exit_time if status == "closed" else (quote or {}).get("quote_time")}
    try:
        entry, mark = float(entry), float(mark)
        if not isfinite(entry) or not isfinite(mark) or entry <= 0 or mark <= 0:
            return result
        if direction not in {"bullish", "bearish", "long", "short"}:
            return result
        points = (mark - entry) * (1 if direction in {"bullish", "long"} else -1)
        return {**result, "points": points, "percent": points / entry * 100}
    except (ValueError, TypeError):
        return result
