from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from trading_analysis.analysis.technical import ema, rsi
from trading_analysis.nifty.live_scanner import closed_candles


def option_footprint(current: list[dict[str, Any]], previous: list[dict[str, Any]], spot: float | None) -> dict[str, Any]:
    old = {(float(row["strike"]), str(row["option_type"]).upper()): row for row in previous}
    paired = [(row, old[(float(row["strike"]), str(row["option_type"]).upper())]) for row in current
              if (float(row["strike"]), str(row["option_type"]).upper()) in old]
    totals = {"CE": 0, "PE": 0}
    deltas = {"CE": 0, "PE": 0}
    writing = {"CE": 0, "PE": 0}
    writing_contracts = {"CE": 0, "PE": 0}
    top: list[dict[str, Any]] = []
    for row, before in paired:
        side = str(row["option_type"]).upper()
        if side not in totals:
            continue
        change = int(float(row.get("oi") or 0)) - int(float(before.get("oi") or 0))
        premium_now = float(row.get("last_price") or 0)
        premium_before = float(before.get("last_price") or 0)
        premium_change = premium_now - premium_before
        totals[side] += int(float(row.get("oi") or 0))
        deltas[side] += change
        if change > 0 and premium_now > 0 and premium_before > 0 and premium_change < 0:
            writing[side] += change
            writing_contracts[side] += 1
        top.append({"strike": float(row["strike"]), "side": side, "oi_change": change,
                    "premium_change": round(premium_change, 2)})
    coverage = len(paired) / len(current) if current else 0
    pcr = totals["PE"] / totals["CE"] if totals["CE"] else None
    bias = "unavailable" if len(paired) < 8 or coverage < 0.7 or pcr is None else "unclear"
    if len(paired) >= 8 and coverage >= 0.7 and pcr is not None:
        if writing_contracts["PE"] >= 2 and writing["PE"] > max(writing["CE"], 0) * 1.25 and pcr >= 0.9:
            bias = "bullish"
        elif writing_contracts["CE"] >= 2 and writing["CE"] > max(writing["PE"], 0) * 1.25 and pcr <= 1.1:
            bias = "bearish"
        elif deltas["PE"] > 0 and deltas["CE"] > 0 and 0.8 <= pcr <= 1.25:
            bias = "neutral"
    return {"bias": bias, "reason": "Too few matching contracts across snapshots" if bias == "unavailable" else None,
            "pcr_oi": round(pcr, 3) if pcr is not None else None,
            "put_oi_change": deltas["PE"], "call_oi_change": deltas["CE"],
            "put_writing_oi": writing["PE"], "call_writing_oi": writing["CE"],
            "matched_contracts": len(paired), "coverage": round(coverage, 3), "spot": spot,
            "top_changes": sorted(top, key=lambda row: abs(row["oi_change"]), reverse=True)[:6]}


def technical_read(candles: list[Any], timeframe: str, now: datetime) -> dict[str, Any]:
    closed = closed_candles(candles, timeframe, now=now)
    if len(closed) < 50:
        return {"bias": "unavailable", "reason": "Fewer than 50 closed candles"}
    latest = closed[-1]
    zone = ZoneInfo("Asia/Kolkata")
    current = now.astimezone(zone).replace(tzinfo=None) if now.tzinfo else now
    candle_time = latest.timestamp.astimezone(zone).replace(tzinfo=None) if latest.timestamp.tzinfo else latest.timestamp
    age = current - candle_time
    maximum = timedelta(days=4) if timeframe == "day" else timedelta(minutes={"15minute": 45, "60minute": 180}[timeframe])
    if age < timedelta(0) or age > maximum:
        return {"bias": "stale", "reason": "Latest closed candle is stale", "candle_time": latest.timestamp.isoformat()}
    closes = [row.close for row in closed]
    fast, slow, momentum = ema(closes, 20), ema(closes, 50), rsi(closes, 14)
    bias = "unclear"
    if fast is not None and slow is not None and momentum is not None:
        if latest.close > latest.open and latest.close > fast > slow and momentum >= 52:
            bias = "bullish"
        elif latest.close < latest.open and latest.close < fast < slow and momentum <= 48:
            bias = "bearish"
    prior = closed[-2].close
    return {"bias": bias, "candle_time": latest.timestamp.isoformat(), "close": latest.close,
            "ema20": fast, "ema50": slow, "rsi14": momentum,
            "change_pct": round((latest.close / prior - 1) * 100, 3) if prior else None}


def leader_confirmation(leaders: list[dict[str, Any]], direction: str) -> dict[str, Any]:
    technical = [row for row in leaders if row.get("technical_bias") == direction]
    options = [row for row in leaders if row.get("option_bias") == direction]
    both = [row for row in leaders if row.get("technical_bias") == direction and row.get("option_bias") == direction]
    opposing = [row for row in leaders if row.get("technical_bias") not in {direction, "unclear", "unavailable", "stale"}]
    required_technical = max(3, (len(leaders) + 1) // 2)
    passed = len(technical) >= required_technical and len(options) >= 2 and len(both) >= 2 and len(technical) > len(opposing)
    pressure = sum(float(row["weight_pct"]) * float(row["change_pct"]) / 100
                   for row in leaders if row.get("weight_pct") is not None and row.get("change_pct") is not None)
    return {"passed": passed, "technical_aligned": len(technical), "option_aligned": len(options),
            "both_aligned": len(both),
            "opposing": len(opposing), "required_technical": required_technical,
            "sampled_weighted_move_pct": round(pressure, 3) if any(row.get("weight_pct") is not None for row in leaders) else None}
