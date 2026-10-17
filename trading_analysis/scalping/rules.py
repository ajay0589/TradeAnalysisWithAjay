"""Shared live/replay rules. All inputs must have been available at decision time."""
from datetime import timedelta, time
from math import isfinite

from trading_analysis.analysis.technical import ema, atr
from trading_analysis.live_timing import as_ist, CLOCK_SKEW_SECONDS
from trading_analysis.models import Candle

VERSION = "scalping-v1-experimental"
INDICES = {"NIFTY": ("NSE", "NIFTY 50", "NFO", "NIFTY"),
           "BANKNIFTY": ("NSE", "NIFTY BANK", "NFO", "BANKNIFTY"),
           "SENSEX": ("BSE", "SENSEX", "BFO", "SENSEX")}
SETUPS = {"Scalp1": "Trend pullback", "Scalp2": "Opening-range retest", "Scalp3": "Failed-breakout reversal"}
VARIANTS = {"A": "Technical", "B": "Technical + premiums", "C": "Technical + premiums + OI"}
POLICY = {"frame_minutes": 1, "target_r": 1.5, "hold_minutes": 20, "cost_bps_side": 2,
          "max_signal_age_seconds": 10, "max_quote_age_seconds": 3, "max_spread_pct": 1.5,
          "premium_change_pct": .2, "oi_change_pct": .5, "cooldown_minutes": 3,
          "last_entry": "14:45", "session_exit": "15:20", "max_entries_per_ledger_day": 6}


def aggregate(rows, minutes=1):
    """Session-aligned, complete buckets only; never bridge missing source minutes."""
    groups = {}
    for c in rows:
        stamp = as_ist(c.timestamp)
        start = stamp.replace(hour=9, minute=15, second=0, microsecond=0)
        offset = int((stamp - start).total_seconds() // 60)
        if stamp.weekday() >= 5 or not 0 <= offset < 375:
            continue
        key = start + timedelta(minutes=offset // minutes * minutes)
        groups.setdefault(key, []).append(c)
    result = []
    for key, bucket in sorted(groups.items()):
        bucket.sort(key=lambda c: c.timestamp)
        if len(bucket) != minutes or any(as_ist(c.timestamp) != key + timedelta(minutes=i) for i, c in enumerate(bucket)):
            continue
        if any(not all(isfinite(v) and v > 0 for v in (c.open, c.high, c.low, c.close))
               or not c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high for c in bucket):
            continue
        result.append(Candle(key, bucket[0].open, max(c.high for c in bucket), min(c.low for c in bucket),
                             bucket[-1].close, sum(c.volume for c in bucket)))
    return result


def technical(rows, now, minutes=1, prepared=None):
    now = as_ist(now)
    # Stable lookback shared with replay; exclude every forming minute first.
    if prepared is None:
        source = [c for c in rows if as_ist(c.timestamp) + timedelta(minutes=1) <= now][-1800:]
        base, higher = aggregate(source, minutes), aggregate(source, 5)
    else:
        source, base, higher = prepared
    blocked = lambda reason: {"ready": False, "reason": reason, "signals": {}}
    if len(base) < 60 or len(higher) < 60:
        return blocked("indicator_warmup")
    current, previous = base[-1], base[-2]
    close_at = as_ist(current.timestamp) + timedelta(minutes=minutes)
    if now.date() != close_at.date() or not 0 <= (now - close_at).total_seconds() < minutes * 60:
        return blocked("stale_candles")
    recent = base[-5:]
    if any(b.timestamp - a.timestamp != timedelta(minutes=minutes) for a, b in zip(recent, recent[1:])):
        return blocked("recent_candle_gap")
    if (now - (higher[-1].timestamp + timedelta(minutes=5))).total_seconds() >= 300:
        return blocked("higher_candle_gap")
    hc, bc = [c.close for c in higher[-300:]], [c.close for c in base[-300:]]
    fast, slow, prior_fast = ema(hc, 20), ema(hc, 50), ema(hc[:-1], 20)
    volatility = atr(base[-300:])
    if not volatility or volatility <= 0:
        return blocked("invalid_atr")
    up = hc[-1] > fast > slow and fast > prior_fast
    down = hc[-1] < fast < slow and fast < prior_fast
    flat = abs(fast - slow) <= .5 * (atr(higher[-300:]) or 0)
    values = {"close": current.close, "atr": volatility, "ema9": ema(bc, 9), "ema20": ema(bc, 20),
              "higher_ema20": fast, "higher_ema50": slow, "higher_ema20_previous": prior_fast,
              "higher_close_at": (higher[-1].timestamp + timedelta(minutes=5)).isoformat(), "flat": flat}
    result = {"ready": True, "reason": "evaluated", "bar_time": current.timestamp.isoformat(),
              "closed_at": close_at.isoformat(), "indicators": values, "signals": {}}
    if not time(9, 30) <= close_at.time().replace(tzinfo=None) < time(14, 45):
        return {**result, "reason": "outside_entry_window"}
    if current.high - current.low > 2.5 * volatility:
        return {**result, "reason": "oversized_signal_candle"}
    green, red = current.close > current.open, current.close < current.open
    recent_pullback = base[-4:-1]
    prior_ema = ema(bc[:-1], 20)
    long1 = up and min(c.low for c in recent_pullback) <= prior_ema and current.close > values["ema9"] and green and current.close > previous.high
    short1 = down and max(c.high for c in recent_pullback) >= prior_ema and current.close < values["ema9"] and red and current.close < previous.low
    candidates = {"Scalp1": (long1, short1, min(c.low for c in base[-4:]), max(c.high for c in base[-4:]))}
    opening = [c for c in source if c.timestamp.date() == now.date() and time(9, 15) <= c.timestamp.time().replace(tzinfo=None) < time(9, 30)]
    if len(opening) == 15 and len({c.timestamp for c in opening}) == 15 and previous.timestamp.time().replace(tzinfo=None) >= time(9, 30):
        upper, lower = max(c.high for c in opening), min(c.low for c in opening)
        values.update(opening_high=upper, opening_low=lower)
        candidates["Scalp2"] = (up and previous.close > upper and upper - .25 * volatility <= current.low <= upper + .1 * volatility and current.close > upper and green,
                                down and previous.close < lower and lower - .1 * volatility <= current.high <= lower + .25 * volatility and current.close < lower and red,
                                current.low, current.high)
        candidates["Scalp3"] = (flat and previous.low < lower < previous.close and current.close > previous.high and green,
                                flat and previous.high > upper > previous.close and current.close < previous.low and red,
                                min(previous.low, current.low), max(previous.high, current.high))
    for setup, (long, short, low, high) in candidates.items():
        if not (long or short):
            continue
        sign = 1 if long else -1
        stop = low - .1 * volatility if long else high + .1 * volatility
        risk = (current.close - stop) * sign
        if .3 * volatility <= risk <= 2.5 * volatility:
            result["signals"][setup] = {"direction": "bullish" if long else "bearish", "reference": current.close,
                                        "stop": stop, "risk": risk, "atr": volatility}
    return result


def fresh(row, now, max_age=3):
    try:
        return (row and isfinite(row["price"]) and row["price"] > 0
                and -CLOCK_SKEW_SECONDS <= (now - as_ist(row["quote_time"])).total_seconds() <= max_age
                and 0 <= (now - as_ist(row["received_at"])).total_seconds() <= max_age)
    except (KeyError, TypeError, ValueError):
        return False


def usable_option(row, now):
    if not fresh(row, now):
        return False
    try:
        bid, ask = row["bid"], row["ask"]
        return (isfinite(bid) and isfinite(ask) and 0 < bid <= ask and row["bid_qty"] > 0 and row["ask_qty"] > 0
                and (ask - bid) / ((ask + bid) / 2) * 100 <= POLICY["max_spread_pct"])
    except (KeyError, TypeError):
        return False


def premium_evidence(history, now, direction):
    """Fixed-contract paired CE/PE evidence; only previously received snapshots."""
    samples = [s for s in history if as_ist(s["time"]) <= now]
    result = {"premium_pass": False, "oi_pass": False, "reason": "option_warmup", "windows": {}}
    if not samples:
        return result
    latest = samples[-1]
    result["latest"] = latest
    if (now - as_ist(latest["time"])).total_seconds() > 3:
        return {**result, "reason": "option_snapshot_stale"}
    sign = 1 if direction == "bullish" else -1
    for minutes in (1, 3):
        boundary = now - timedelta(minutes=minutes)
        baselines = [s for s in samples if 0 <= (boundary - as_ist(s["time"])).total_seconds() <= 5]
        if not baselines:
            return result
        old = baselines[-1]
        # ATM identity must remain stable throughout a window, not just at endpoints.
        identity = latest.get("identity")
        if not identity or any(s.get("identity") != identity for s in samples if old["time"] <= s["time"] <= latest["time"]):
            return {**result, "reason": "atm_changed_warming_up"}
        changes, oi_votes = {}, []
        for side in ("CE", "PE"):
            a, b = old.get(side, {}), latest.get(side, {})
            if not usable_option(a, as_ist(old["time"])) or not usable_option(b, now) or a.get("token") != b.get("token"):
                return {**result, "reason": "option_quote_invalid_stale_or_wide"}
            if any(abs((as_ist(option["quote_time"]) - as_ist(snapshot.get("spot", {}).get("quote_time", snapshot["time"]))).total_seconds()) > 2
                   for option, snapshot in ((a, old), (b, latest))):
                return {**result, "reason": "unsynchronized_quotes"}
            change = ((b["bid"] + b["ask"]) / (a["bid"] + a["ask"]) - 1) * 100
            oi = ((b.get("oi", 0) / a["oi"] - 1) * 100) if a.get("oi", 0) > 0 else None
            volume = b.get("volume", 0) - a.get("volume", 0)
            changes[side] = {"premium_pct": change, "oi_pct": oi, "volume_change": volume}
            # Positioning hypothesis, never identification of buyers/sellers.
            changes[side]["classification"] = ("long_buildup" if change > 0 and oi > 0 else "short_covering" if change > 0 else "short_buildup" if oi > 0 else "long_unwinding") if oi else "unchanged_or_missing"
            oi_votes.append(oi is not None and abs(oi) >= POLICY["oi_change_pct"] and volume > 0)
        if not fresh(latest.get("spot"), now) or not fresh(old.get("spot"), as_ist(old["time"])):
            return {**result, "reason": "spot_snapshot_stale"}
        spot_change = latest["spot"]["price"] - old["spot"]["price"]
        passed = (spot_change * sign > 0 and changes["CE"]["premium_pct"] * sign >= POLICY["premium_change_pct"]
                  and changes["PE"]["premium_pct"] * sign <= -POLICY["premium_change_pct"])
        result["windows"][str(minutes)] = {"from": old, "changes": changes, "spot_points": spot_change,
                                           "premium_pass": passed, "oi_pass": passed and all(oi_votes)}
    result["premium_pass"] = all(w["premium_pass"] for w in result["windows"].values())
    result["oi_pass"] = all(w["oi_pass"] for w in result["windows"].values())
    result["reason"] = "confirmed" if result["oi_pass"] else "oi_unconfirmed" if result["premium_pass"] else "premium_conflict"
    return result
