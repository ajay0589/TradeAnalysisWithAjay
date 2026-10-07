"""Shared closed-candle boundaries and indicative (never executed) live prices."""
from datetime import datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

from trading_analysis.scheduler.market_hours import is_market_day

IST = ZoneInfo("Asia/Kolkata")
MAX_ENTRY_DELAY = {"intraday": 120, "swing": 300, "positional": 900}


def as_ist(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    return result.replace(tzinfo=IST) if result.tzinfo is None else result.astimezone(IST)


def expected_closed_bar(frame, now):
    now = as_ist(now)
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    minutes = 375 if frame == "day" else int(frame.replace("minute", ""))
    final_offset = ((375 - 1) // minutes) * minutes
    if is_market_day(now.date()) and now >= end:
        return now.replace(hour=0, minute=0, second=0, microsecond=0) if frame == "day" else start + timedelta(minutes=final_offset)
    count = int((now - start).total_seconds() // (minutes * 60))
    if is_market_day(now.date()) and count > 0:
        return start + timedelta(minutes=(count - 1) * minutes)
    previous = now - timedelta(days=1)
    while not is_market_day(previous.date()):
        previous -= timedelta(days=1)
    return previous.replace(hour=0, minute=0, second=0, microsecond=0) if frame == "day" else previous.replace(hour=9, minute=15, second=0, microsecond=0) + timedelta(minutes=final_offset)


def quote_is_fresh(quote, now, max_age=20):
    if not quote:
        return False
    try:
        price = float(quote["price"])
        ages = [(as_ist(now) - as_ist(quote[key])).total_seconds() for key in ("quote_time", "received_at")]
        return isfinite(price) and price > 0 and all(0 <= age <= max_age for age in ages)
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def prepare_live_entry(signal, quote, now, checks=None):
    """Revalidate an existing technical signal against a fresh spot observation."""
    checks = checks if checks is not None else {}
    metadata = dict(signal.get("metadata") or {})
    now = as_ist(now)
    closed_at = as_ist(metadata["signal_candle_closed_at"])
    delay = (now - closed_at).total_seconds()
    checks.update(signal_delay_seconds=round(delay, 3), gate="signal_expired")
    if not 0 <= delay <= MAX_ENTRY_DELAY[signal["horizon"]]:
        return None
    checks["gate"] = "spot_quote_unavailable_or_stale"
    fresh = quote_is_fresh(quote, now)
    session_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    closing_reference = (signal["horizon"] != "intraday" and closed_at == session_close
                         and session_close <= now <= session_close + timedelta(minutes=15)
                         and quote_is_fresh(quote, now, max_age=900)
                         and as_ist(quote["quote_time"]) >= session_close - timedelta(seconds=30))
    if not fresh and not closing_reference:
        return None
    reference, spot = float(signal["entry_price"]), float(quote["price"])
    sign = 1 if signal["direction"] == "bullish" else -1
    stop, target = float(signal["stop_level"]), float(signal["target_level"])
    original_risk = abs(reference - stop)
    risk, reward = (spot - stop) * sign, (target - spot) * sign
    checks.update(gate="price_moved_too_far", reference_price=reference, current_spot=spot)
    # Bound drift by both initial risk and ATR. Targets never move to justify chasing.
    allowance = min(original_risk * 0.25, float(metadata.get("atr14") or original_risk) * 0.25)
    if abs(spot - reference) > allowance or risk <= 0 or reward <= 0:
        return None
    checks.update(gate="current_risk_reward", current_risk_reward=reward / risk)
    if reward / risk < 1.5:
        return None
    checks["gate"] = "passed"
    metadata.update(reference_price=reference, signal_detected_at=now.isoformat(),
                    signal_delay_seconds=round(delay, 3), spot_quote=dict(quote),
                    price_basis="observed_spot_not_fill" if fresh else "session_close_reference_not_fill", timing_version=2)
    return {**signal, "entry_time": now, "entry_price": spot, "risk_points": risk,
            "target_r_multiple": reward / risk, "metadata": metadata}


def live_quote_exit(trade, quote, now):
    if trade.get("status") != "open" or not quote_is_fresh(quote, now):
        return None
    if trade.get("horizon") == "intraday" and as_ist(trade["entry_time"]).date() < as_ist(now).date():
        return None
    stamp = as_ist(quote["quote_time"])
    if stamp < as_ist(trade["entry_time"]):
        return None
    spot = float(quote["price"])
    sign = 1 if trade["direction"] == "bullish" else -1
    reason = None
    if (spot - float(trade["stop_level"])) * sign <= 0:
        reason = "stop_loss"
    elif (spot - float(trade["target_level"])) * sign >= 0:
        reason = "target_reached"
    if reason is None:
        return None
    return {"price": spot, "reason": reason, "exit_time": as_ist(now),
            "candle_timestamp": stamp, "metadata": {"exit_detected_at": as_ist(now).isoformat(),
            "exit_spot_quote": dict(quote), "exit_price_basis": "observed_spot_not_fill",
            "exit_trigger_level": trade["stop_level" if reason == "stop_loss" else "target_level"]}}


def price_closed_exit(trade, outcome, quote, now, timeframe):
    from trading_analysis.nifty.live_scanner import candle_close_time

    if not outcome or outcome.get("checked_only"):
        return outcome
    closed_at = as_ist(candle_close_time(outcome["candle_timestamp"], timeframe))
    detected = as_ist(now)
    live = quote_is_fresh(quote, now)
    recovered = closed_at.date() < detected.date()
    # Historical recovery is explicitly an audit outcome, never a present-day fill.
    basis = "recovered_candle_reference" if recovered else "observed_spot_not_fill" if live else "candle_reference_not_fill"
    return {**outcome, "price": float(quote["price"]) if live and not recovered else outcome["price"],
            "exit_time": detected, "metadata": {"exit_candle_timestamp": as_ist(outcome["candle_timestamp"]).isoformat(),
            "exit_candle_closed_at": closed_at.isoformat(), "exit_detected_at": detected.isoformat(),
            "exit_delay_seconds": max(0, (detected - closed_at).total_seconds()),
            "exit_reference_price": outcome["price"], "exit_spot_quote": dict(quote or {}),
            "exit_price_basis": basis, "recovered_event": recovered}}
