"""Versioned, closed-candle technical rules. No option inputs enter this module."""
from datetime import time
from math import isfinite
from statistics import pstdev

from trading_analysis.analysis.technical import atr, ema, rsi, sma
from trading_analysis.live_timing import as_ist, expected_closed_bar
from trading_analysis.nifty.live_scanner import candle_close_time, closed_candles

VERSION = "nifty-lab-v1"
VARIANTS = ("technical", "combined")
RISK = {"stop_atr": 1.0, "target_r": 2.0, "max_holding_minutes": 90,
        "last_entry": "14:30", "session_exit": "15:20", "cost_bps_per_side": 2.0,
        "max_signal_age_seconds": 120, "max_quote_age_seconds": 20}
SETUPS = {
    "Nifty_Setup1": {"name": "EMA pullback", "frame": "15minute", "sources": ["15minute"],
        "rules": "EMA20 reclaim in EMA20/50 trend; bullish RSI 52-70, bearish 30-48; directional candle."},
    "Nifty_Setup2": {"name": "10-bar range breakout", "frame": "15minute", "sources": ["15minute"],
        "rules": "Close breaks the preceding 10-bar high/low; EMA20/50 trend; bullish RSI 55-75, bearish 25-45."},
    "Nifty_Setup3": {"name": "Opening-range breakout", "frame": "5minute", "sources": ["5minute", "15minute"],
        "rules": "Cross of the completed 09:15-09:30 opening range, aligned with the latest closed 15m EMA20/50 trend."},
    "Nifty_Setup4": {"name": "Bollinger re-entry", "frame": "5minute", "sources": ["5minute"],
        "rules": "Re-entry inside 20-bar, 2-SD bands after an outside close; RSI crosses 35/65; EMA20/50 gap <= 0.5 ATR."},
    "Nifty_Setup5": {"name": "Previous-day level retest", "frame": "5minute", "sources": ["5minute", "15minute", "day"],
        "rules": "Retest after a close beyond previous-day high/low; rejection candle, 0.1 ATR touch tolerance, 0.25 ATR failure buffer; aligned 15m trend."},
}


def technical_read(setup_id, sources, now):
    definition = SETUPS[setup_id]
    now = as_ist(now)
    data = {}
    for frame in definition["sources"]:
        rows = closed_candles(sources.get(frame, []), frame, now)
        if not rows or as_ist(rows[-1].timestamp) != expected_closed_bar(frame, now):
            return {"ready": False, "reason": f"{frame}_stale_or_missing"}
        checked = rows[-61:]
        if any(not all(isfinite(v) and v > 0 for v in (c.open, c.high, c.low, c.close))
               or not c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high for c in checked):
            return {"ready": False, "reason": f"{frame}_invalid_ohlc"}
        if any(as_ist(a.timestamp) >= as_ist(b.timestamp) for a, b in zip(checked, checked[1:])):
            return {"ready": False, "reason": f"{frame}_unordered_or_duplicate"}
        if frame != "day" and any(as_ist(a.timestamp) != expected_closed_bar(frame, as_ist(b.timestamp))
                                  for a, b in zip(checked, checked[1:])):
            return {"ready": False, "reason": f"{frame}_candle_gap"}
        data[frame] = rows
    rows = data[definition["frame"]]
    if len(rows) < 60:
        return {"ready": False, "reason": "indicator_warmup"}
    latest, previous = rows[-1], rows[-2]
    closed_at = candle_close_time(latest.timestamp, definition["frame"])
    result = {"ready": True, "direction": None, "reason": "rules_not_met",
              "bar_time": as_ist(latest.timestamp).isoformat(), "closed_at": closed_at.isoformat(),
              "input_candles": {frame: [{"time": as_ist(c.timestamp).isoformat(), "open": c.open,
                                        "high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
                                       for c in source[-2:]] for frame, source in data.items()}}
    closes = [c.close for c in rows]
    fast, slow, strength, volatility = ema(closes, 20), ema(closes, 50), rsi(closes), atr(rows)
    result["indicators"] = {"close": latest.close, "ema20": fast, "ema50": slow,
                            "rsi14": strength, "atr14": volatility}
    if volatility is None or volatility <= 0:
        return {**result, "reason": "invalid_atr"}
    if closed_at.date() != now.date() or not time(9, 30) <= closed_at.time().replace(tzinfo=None) < time(14, 30):
        return {**result, "reason": "outside_entry_window"}
    up, down = latest.close > fast > slow, latest.close < fast < slow
    green, red = latest.close > latest.open, latest.close < latest.open
    long = short = False
    if setup_id == "Nifty_Setup1":
        prior_fast = ema(closes[:-1], 20)
        long = previous.close <= prior_fast and up and green and 52 <= strength <= 70
        short = previous.close >= prior_fast and down and red and 30 <= strength <= 48
    elif setup_id == "Nifty_Setup2":
        upper, lower = max(c.high for c in rows[-11:-1]), min(c.low for c in rows[-11:-1])
        result["indicators"].update(range_high=upper, range_low=lower)
        long = latest.close > upper and up and 55 <= strength <= 75
        short = latest.close < lower and down and 25 <= strength <= 45
    elif setup_id == "Nifty_Setup4":
        center, prior_center = sma(closes, 20), sma(closes[:-1], 20)
        sd, prior_sd = pstdev(closes[-20:]), pstdev(closes[-21:-1])
        lower, upper = center - 2 * sd, center + 2 * sd
        previous_rsi = rsi(closes[:-1])
        result["indicators"].update(band_low=lower, band_high=upper, previous_rsi=previous_rsi)
        flat = abs(fast - slow) <= 0.5 * volatility
        long = flat and previous.close < prior_center - 2 * prior_sd and lower < latest.close < upper and previous_rsi < 35 <= strength and green
        short = flat and previous.close > prior_center + 2 * prior_sd and lower < latest.close < upper and previous_rsi > 65 >= strength and red
    else:
        higher = [c.close for c in data["15minute"]]
        if len(higher) < 60:
            return {"ready": False, "reason": "15minute_indicator_warmup"}
        hfast, hslow = ema(higher, 20), ema(higher, 50)
        up, down = higher[-1] > hfast > hslow, higher[-1] < hfast < hslow
        result["indicators"].update(higher_ema20=hfast, higher_ema50=hslow,
                                    higher_bar=as_ist(data["15minute"][-1].timestamp).isoformat())
        if setup_id == "Nifty_Setup3":
            opening = [c for c in rows if as_ist(c.timestamp).date() == now.date()
                       and time(9, 15) <= as_ist(c.timestamp).time().replace(tzinfo=None) < time(9, 30)]
            if {as_ist(c.timestamp).strftime("%H:%M") for c in opening} != {"09:15", "09:20", "09:25"}:
                return {"ready": False, "reason": "opening_range_incomplete"}
            upper, lower = max(c.high for c in opening), min(c.low for c in opening)
            result["indicators"].update(opening_high=upper, opening_low=lower)
            long = up and previous.close <= upper < latest.close and green
            short = down and previous.close >= lower > latest.close and red
        else:
            daily = data["day"][-1]
            upper, lower = daily.high, daily.low
            result["indicators"].update(previous_day_high=upper, previous_day_low=lower,
                                       previous_day=as_ist(daily.timestamp).isoformat())
            long = up and previous.close > upper and upper - .25 * volatility <= latest.low <= upper + .1 * volatility and latest.close > upper and green
            short = down and previous.close < lower and lower - .1 * volatility <= latest.high <= lower + .25 * volatility and latest.close < lower and red
    direction = "bullish" if long else "bearish" if short else None
    return {**result, "direction": direction, "reason": "technical_signal" if direction else result["reason"]}
