"""Versioned swing rules. Continuous futures are never used for trade prices."""
from datetime import date, datetime, time, timedelta
from math import isfinite, ceil, floor

from trading_analysis.analysis.technical import atr, ema, rsi
from trading_analysis.live_timing import IST, as_ist

ROOTS = ("SILVERM", "GOLDM", "NATURALGAS", "CRUDEOIL")
VERSION = "mcx-swing-v1"
RISK = {"stop_daily_atr": 1.5, "target_r": 2.0, "max_holding_days": 10,
        "minimum_expiry_days": 21, "exit_before_expiry_days": 10,
        "max_signal_age_seconds": 300, "max_quote_age_seconds": 45,
        "max_spread_percent": 0.15, "cost_bps_per_side": 5.0}
# MCX's published 2026 calendar, checked 2026-10-14. Special Sunday Muhurat
# sessions are deliberately not entry sessions. New years fail closed for entry.
CALENDAR_URL = "https://www.mcxindia.com/market-operations/trading-surveillance/trading-holidays"
CLOSED_2026 = {"2026-01-26", "2026-04-03", "2026-10-02", "2026-12-25"}
EVENING_2026 = {"2026-03-03", "2026-03-26", "2026-03-31", "2026-04-14", "2026-05-01",
                "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-20", "2026-11-10", "2026-11-24"}


def session(day):
    if day.weekday() >= 5 or day.isoformat() in CLOSED_2026:
        return None
    start = time(17) if day.isoformat() in EVENING_2026 else time(9)
    march_first, november_first = date(day.year, 3, 1), date(day.year, 11, 1)
    spring = march_first + timedelta(days=(6-march_first.weekday()) % 7 + 7)
    autumn = november_first + timedelta(days=(6-november_first.weekday()) % 7)
    end = time(23, 30) if spring <= day < autumn else time(23, 55)
    if day.isoformat() == "2026-01-01":
        end = time(17)
    return datetime.combine(day, start, IST), datetime.combine(day, end, IST)


def market_window(now):
    bounds = session(as_ist(now).date())
    return bool(bounds and bounds[0] <= now < bounds[1])


def market_seconds_between(start, end):
    total = 0
    day = start.date()
    while day <= end.date():
        bounds = session(day)
        if bounds:
            left, right = max(start, bounds[0]), min(end, bounds[1])
            total += max(0, (right-left).total_seconds())
        day += timedelta(days=1)
    return total


def expected_bar(frame, now):
    now = as_ist(now)
    for offset in range(15):
        day = now.date() - timedelta(days=offset)
        bounds = session(day)
        if not bounds:
            continue
        start, end = bounds
        if frame == "day":
            if now >= end + timedelta(minutes=5):
                return datetime.combine(day, time(), IST)
        else:
            stamp = start
            latest = None
            while stamp < end:
                if min(stamp + timedelta(hours=1), end) <= now:
                    latest = stamp
                stamp += timedelta(hours=1)
            if latest:
                return latest
    raise ValueError("No recent MCX session")


def closed_rows(rows, frame, now):
    expected = expected_bar(frame, now)
    return [c for c in rows if as_ist(c.timestamp) <= expected]


def select_contracts(master, now):
    selected = {}
    for root in ROOTS:
        rows = [r for r in master if r.get("exchange") == "MCX" and r.get("segment") == "MCX-FUT"
                and r.get("instrument_type") == "FUT" and r.get("name") == root
                and date.fromisoformat(r["expiry"]) >= now.date() + timedelta(days=RISK["minimum_expiry_days"])]
        if rows:
            r = min(rows, key=lambda c: c["expiry"])
            selected[root] = {"root": root, "symbol": r["tradingsymbol"], "expiry": r["expiry"],
                              "token": str(r["instrument_token"]), "tick_size": float(r["tick_size"])}
    return selected


def quote_read(raw, now):
    try:
        stamp, traded = as_ist(raw["timestamp"]), as_ist(raw["last_trade_time"])
        price = float(raw["last_price"])
        bid = float(raw["depth"]["buy"][0]["price"])
        ask = float(raw["depth"]["sell"][0]["price"])
        valid = all(isfinite(p) and p > 0 for p in (price, bid, ask)) and bid <= ask
        fresh = valid and 0 <= (now - stamp).total_seconds() <= RISK["max_quote_age_seconds"]
        fresh = fresh and 0 <= (now - traded).total_seconds() <= 120
        return {"fresh": fresh, "price": price, "bid": bid, "ask": ask,
                "quote_time": stamp.isoformat(), "last_trade_time": traded.isoformat(),
                "received_at": now.isoformat(), "source": "zerodha_mcx_quote",
                "spread_percent": (ask - bid) / price * 100 if valid else None,
                "volume": raw.get("volume"), "oi": raw.get("oi")}
    except (KeyError, IndexError, TypeError, ValueError):
        return {"fresh": False, "reason": "missing_quote_or_depth"}


def technical_read(sources, now):
    result = {"ready": False, "direction": None, "version": VERSION}
    data = {}
    for frame in ("day", "60minute"):
        rows = closed_rows(sources.get(frame, []), frame, now)
        if not rows or as_ist(rows[-1].timestamp) != expected_bar(frame, now):
            return {**result, "reason": frame + "_stale_or_missing"}
        if len(rows) < 60:
            return {**result, "reason": frame + "_warmup_under_60_bars"}
        if any(not all(isfinite(v) and v > 0 for v in (c.open, c.high, c.low, c.close))
               or not c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high for c in rows):
            return {**result, "reason": frame + "_invalid_ohlc"}
        if any(as_ist(a.timestamp) >= as_ist(b.timestamp) for a, b in zip(rows, rows[1:])):
            return {**result, "reason": frame + "_unordered_or_duplicate"}
        # Recent hourly holes invalidate a breakout. Older missing illiquid bars
        # remain visible in the downloaded source rather than being fabricated.
        if frame == "60minute" and any(as_ist(a.timestamp) != expected_bar(frame, as_ist(b.timestamp))
                                        for a, b in zip(rows[-21:], rows[-20:])):
            return {**result, "reason": "60minute_gap_in_breakout_window"}
        data[frame] = rows
    daily, hourly = data["day"], data["60minute"]
    dc, hc = [c.close for c in daily], [c.close for c in hourly]
    d20, d50, prior20 = ema(dc, 20), ema(dc, 50), ema(dc[:-3], 20)
    h20, h50, strength = ema(hc, 20), ema(hc, 50), rsi(hc)
    volatility = atr(daily)
    upper, lower = max(c.high for c in hourly[-21:-1]), min(c.low for c in hourly[-21:-1])
    long = dc[-1] > d20 > d50 and d20 > prior20 and hc[-1] > h20 > h50 and hc[-1] > upper and 52 <= strength <= 75
    short = dc[-1] < d20 < d50 and d20 < prior20 and hc[-1] < h20 < h50 and hc[-1] < lower and 25 <= strength <= 48
    direction = "bullish" if long else "bearish" if short else None
    bar = as_ist(hourly[-1].timestamp)
    indicators = {"daily_close": dc[-1], "daily_ema20": d20, "daily_ema50": d50, "daily_atr14": volatility,
                  "atr_percent": volatility / dc[-1] * 100, "hour_close": hc[-1], "hour_ema20": h20,
                  "hour_ema50": h50, "hour_rsi14": strength, "breakout_high": upper, "breakout_low": lower,
                  "daily_oi": daily[-1].open_interest, "daily_oi_change":
                  daily[-1].open_interest - daily[-2].open_interest
                  if daily[-1].open_interest is not None and daily[-2].open_interest is not None else None}
    return {**result, "ready": volatility > 0, "direction": direction,
            "reason": "technical_signal" if direction else "trend_or_breakout_not_confirmed",
            "bar_time": bar.isoformat(), "closed_at": (bar + timedelta(hours=1)).isoformat(),
            "daily_bar": as_ist(daily[-1].timestamp).isoformat(), "indicators": indicators}


def entry_check(contract, signal, quote, now):
    if now.year != 2026:
        return "calendar_year_not_verified"
    if not market_window(now) or not 10 <= now.hour <= 22:
        return "outside_entry_window"
    if not signal.get("ready") or not signal.get("direction"):
        return signal.get("reason", "no_signal")
    if not 0 <= (now - as_ist(signal["closed_at"])).total_seconds() <= RISK["max_signal_age_seconds"]:
        return "signal_too_old"
    if not quote.get("fresh"):
        return "quote_stale_or_missing"
    if quote["spread_percent"] > RISK["max_spread_percent"]:
        return "spread_too_wide"
    if date.fromisoformat(contract["expiry"]) < now.date() + timedelta(days=RISK["minimum_expiry_days"]):
        return "expiry_buffer"
    if not quote.get("volume") or not quote.get("oi"):
        return "no_volume_or_oi"
    reference = signal["indicators"]["hour_close"]
    price = quote["ask"] if signal["direction"] == "bullish" else quote["bid"]
    if abs(price - reference) > 0.25 * signal["indicators"]["daily_atr14"]:
        return "price_moved_from_signal"
    level = signal["indicators"]["breakout_high" if signal["direction"] == "bullish" else "breakout_low"]
    if (quote["price"] <= level if signal["direction"] == "bullish" else quote["price"] >= level):
        return "breakout_no_longer_valid"
    return "eligible"


def trade_from_signal(contract, signal, quote, now, telegram=False):
    sign = 1 if signal["direction"] == "bullish" else -1
    price = quote["ask"] if sign == 1 else quote["bid"]
    tick = contract["tick_size"]
    risk = ceil(signal["indicators"]["daily_atr14"] * RISK["stop_daily_atr"] / tick) * tick
    stop = (floor((price - risk) / tick) if sign == 1 else ceil((price + risk) / tick)) * tick
    risk = abs(price - stop)
    return {**contract, "direction": signal["direction"], "horizon": "swing", "version": VERSION,
            "entry_price": price, "entry_time": now.isoformat(), "reference_price": signal["indicators"]["hour_close"],
            "signal_time": signal["closed_at"], "signal_delay_seconds": (now - as_ist(signal["closed_at"])).total_seconds(),
            "entry_quote": quote, "stop_level": stop, "target_level": price + sign * risk * RISK["target_r"],
            "risk_points": risk, "price_basis": "observed_futures_bid_ask_not_fill",
            "telegram_enabled": telegram, "indicators": signal["indicators"], "risk_policy": dict(RISK)}


def exit_reason(trade, quote, sources, now):
    if not quote.get("fresh") or not market_window(now):
        return None
    sign = 1 if trade["direction"] == "bullish" else -1
    price = quote["bid"] if sign == 1 else quote["ask"]
    if sign * (price - trade["stop_level"]) <= 0:
        return "stop_loss"
    if sign * (price - trade["target_level"]) >= 0:
        return "target"
    if date.fromisoformat(trade["expiry"]) <= now.date() + timedelta(days=RISK["exit_before_expiry_days"]):
        return "expiry_risk_exit"
    if now - as_ist(trade["entry_time"]) >= timedelta(days=RISK["max_holding_days"]):
        return "maximum_holding_days"
    daily = closed_rows(sources.get("day", []), "day", now)
    if len(daily) >= 20 and as_ist(daily[-1].timestamp) == expected_bar("day", now):
        if sign * (daily[-1].close - ema([c.close for c in daily], 20)) < 0:
            return "closed_daily_ema20_reversal"
    return None
