from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from trading_analysis.analysis.technical import atr, ema, rsi
from trading_analysis.models import Candle


IST = ZoneInfo("Asia/Kolkata")
HORIZON_CONFIG = {
    "intraday": {"timeframe": "15minute", "minutes": 15, "max_bars": 8, "fallback_risk_percent": 0.35},
    "swing": {"timeframe": "60minute", "minutes": 60, "max_bars": 12, "fallback_risk_percent": 0.80},
    "positional": {"timeframe": "day", "minutes": 375, "max_bars": 10, "fallback_risk_percent": 2.00},
}


def build_live_entry_signal(
    context: dict[str, Any],
    candidates: list[dict[str, Any]],
    horizon: str,
    candles: list[Candle],
    *,
    min_score: int = 70,
    target_r_multiple: float = 2.0,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    config = HORIZON_CONFIG[horizon]
    closed = closed_candles(candles, config["timeframe"], now=now)
    if len(closed) < 50:
        return None
    latest = closed[-1]
    current = _as_naive_ist(now or datetime.now(IST))
    if not _candle_is_fresh(latest, horizon, current):
        return None
    links = ((context.get("summary") or {}).get("data_links") or {})
    if not _timestamp_is_fresh(links.get("latest_option_snapshot_at"), current, minutes=10):
        return None
    technical = context.get("technical") or {}
    options = context.get("options") or {}
    iv = context.get("iv") or {}
    closes = [candle.close for candle in closed]
    direction = _historical_direction(latest, ema(closes, 20), ema(closes, 50), rsi(closes, 14), "both") or ""
    if direction not in {"bullish", "bearish"}:
        return None
    option_bias = str(options.get("option_bias") or "").lower()
    if option_bias not in {direction, "neutral"}:
        return None
    matching = [
        candidate
        for candidate in candidates
        if _candidate_direction(candidate) == direction
        and int(candidate.get("suitability_score") or candidate.get("score") or 0) >= min_score
    ]
    if not matching:
        return None
    candidate = max(matching, key=lambda row: int(row.get("suitability_score") or row.get("score") or 0))
    ema20 = ema(closes, 20)
    if ema20 is None or not _candle_confirms(latest, direction, ema20):
        return None
    entry = float(latest.close)
    atr14 = atr(closed, 14)
    stop = _stop_level(direction, entry, technical, atr14, float(config["fallback_risk_percent"]))
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    target = entry + risk * target_r_multiple if direction == "bullish" else entry - risk * target_r_multiple
    score = int(candidate.get("suitability_score") or candidate.get("score") or 0)
    return {
        "horizon": horizon,
        "direction": direction,
        "strategy_id": candidate.get("strategy_id"),
        "title": candidate.get("label") or candidate.get("strategy_id") or "NIFTY setup",
        "entry_time": latest.timestamp,
        "entry_price": entry,
        "entry_timeframe": config["timeframe"],
        "entry_candle_timestamp": latest.timestamp,
        "stop_level": stop,
        "target_level": target,
        "risk_points": risk,
        "target_r_multiple": target_r_multiple,
        "score": score,
        "confidence": candidate.get("confidence"),
        "reasons": list(candidate.get("reasons") or []) + [
            f"Closed {config['timeframe']} candle confirmed {direction} above/below EMA20.",
            f"Option-chain bias is {option_bias}.",
            f"IV regime is {iv.get('iv_regime') or 'unknown'}.",
        ],
        "risks": list(candidate.get("risks") or []) + list(context.get("warnings") or [])[:2],
        "metadata": {
            "candle_open": latest.open,
            "candle_high": latest.high,
            "candle_low": latest.low,
            "candle_close": latest.close,
            "ema20": ema20,
            "rsi14": rsi(closes, 14),
            "atr14": atr14,
            "option_bias": option_bias,
            "pcr_oi": options.get("pcr_oi"),
            "max_pain": options.get("max_pain"),
            "atm_iv": iv.get("atm_iv"),
            "iv_rank": iv.get("iv_rank"),
            "iv_regime": iv.get("iv_regime"),
        },
    }


def evaluate_live_exit(
    trade: dict[str, Any],
    candles: list[Candle],
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    horizon = str(trade.get("horizon") or "")
    config = HORIZON_CONFIG[horizon]
    closed = closed_candles(candles, config["timeframe"], now=now)
    if not closed:
        return None
    latest = closed[-1]
    if str(trade.get("last_candle_timestamp") or "") == latest.timestamp.isoformat(timespec="seconds"):
        return None
    direction = str(trade.get("direction") or "")
    stop = float(trade["stop_level"])
    target = float(trade["target_level"])
    if direction == "bullish" and latest.low <= stop:
        return _exit_payload(latest, stop, "stop_loss")
    if direction == "bearish" and latest.high >= stop:
        return _exit_payload(latest, stop, "stop_loss")
    if direction == "bullish" and latest.high >= target:
        return _exit_payload(latest, target, "target_reached")
    if direction == "bearish" and latest.low <= target:
        return _exit_payload(latest, target, "target_reached")
    closes = [candle.close for candle in closed]
    ema20 = ema(closes, 20)
    if ema20 is not None and direction == "bullish" and latest.close < ema20:
        return _exit_payload(latest, latest.close, "closed_below_ema20")
    if ema20 is not None and direction == "bearish" and latest.close > ema20:
        return _exit_payload(latest, latest.close, "closed_above_ema20")
    entry_time = _as_naive_ist(trade.get("entry_time"))
    later_bars = [candle for candle in closed if _as_naive_ist(candle.timestamp) > entry_time]
    if len(later_bars) >= int(config["max_bars"]):
        return _exit_payload(latest, latest.close, "maximum_holding_period")
    return {
        "checked_only": True,
        "candle_timestamp": latest.timestamp,
        "price": latest.close,
    }


def backtest_nifty_live_rules(
    candles: list[Candle],
    horizon: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    direction: str = "both",
    target_r_multiple: float = 2.0,
    max_holding_bars: int | None = None,
) -> dict[str, Any]:
    config = HORIZON_CONFIG[horizon]
    filtered = _date_filter(sorted(candles, key=lambda row: row.timestamp), from_date, to_date)
    max_bars = max(1, int(max_holding_bars or config["max_bars"]))
    closes = [row.close for row in filtered]
    fast_series = _ema_series(closes, 20)
    slow_series = _ema_series(closes, 50)
    rsi_series = _rsi_series(closes, 14)
    atr_series = _atr_series(filtered, 14)
    trades: list[dict[str, Any]] = []
    index = 50
    while index < len(filtered) - 1:
        latest = filtered[index]
        fast = fast_series[index]
        slow = slow_series[index]
        rsi14 = rsi_series[index]
        signal_direction = _historical_direction(latest, fast, slow, rsi14, direction)
        if signal_direction is None:
            index += 1
            continue
        atr14 = atr_series[index]
        stop = _backtest_stop(signal_direction, latest.close, atr14, float(config["fallback_risk_percent"]))
        risk = abs(latest.close - stop)
        target = latest.close + risk * target_r_multiple if signal_direction == "bullish" else latest.close - risk * target_r_multiple
        exit_index, exit_price, exit_reason, favorable, adverse = _simulate_exit(
            filtered, fast_series, index, signal_direction, stop, target, max_bars
        )
        r_multiple = ((exit_price - latest.close) / risk) * (1 if signal_direction == "bullish" else -1)
        open_seconds = max(0, int((filtered[exit_index].timestamp - latest.timestamp).total_seconds()))
        trades.append(
            {
                "horizon": horizon,
                "direction": signal_direction,
                "entry_time": latest.timestamp,
                "entry_price": latest.close,
                "exit_time": filtered[exit_index].timestamp,
                "exit_price": exit_price,
                "open_seconds": open_seconds,
                "open_duration": _duration(open_seconds),
                "stop_level": stop,
                "target_level": target,
                "risk_points": risk,
                "r_multiple": r_multiple,
                "return_percent": ((exit_price - latest.close) / latest.close) * 100 * (1 if signal_direction == "bullish" else -1),
                "max_favorable_r": favorable / risk,
                "max_adverse_r": adverse / risk,
                "exit_reason": exit_reason,
                "rsi14": rsi14,
            }
        )
        index = exit_index + 1
    return _backtest_payload(trades, horizon, config["timeframe"], target_r_multiple, len(filtered))


def closed_candles(candles: list[Candle], timeframe: str, now: datetime | None = None) -> list[Candle]:
    current = now or datetime.now(IST)
    if current.tzinfo is not None:
        current = current.astimezone(IST).replace(tzinfo=None)
    output = []
    for candle in sorted(candles, key=lambda row: row.timestamp):
        timestamp = _as_naive_ist(candle.timestamp)
        if timeframe == "day":
            close_time = timestamp.replace(hour=15, minute=30, second=0, microsecond=0)
        else:
            close_time = timestamp + timedelta(minutes=int(HORIZON_CONFIG[_horizon_for_timeframe(timeframe)]["minutes"]))
        if close_time <= current:
            output.append(candle)
    return output


def _candidate_direction(candidate: dict[str, Any]) -> str:
    text = " ".join(str(candidate.get(key) or "") for key in ("required_view", "strategy_id", "label")).lower()
    if "bull" in text:
        return "bullish"
    if "bear" in text:
        return "bearish"
    return "other"


def _candle_is_fresh(candle: Candle, horizon: str, now: datetime) -> bool:
    timestamp = _as_naive_ist(candle.timestamp)
    if horizon == "positional":
        return timedelta(0) <= now - timestamp <= timedelta(days=4)
    config = HORIZON_CONFIG[horizon]
    return timestamp.date() == now.date() and timedelta(0) <= now - timestamp <= timedelta(minutes=int(config["minutes"]) * 3)


def _timestamp_is_fresh(value: Any, now: datetime, minutes: int) -> bool:
    if not value:
        return False
    timestamp = _as_naive_ist(value)
    return timedelta(0) <= now - timestamp <= timedelta(minutes=minutes)


def _candle_confirms(candle: Candle, direction: str, ema20: float) -> bool:
    if direction == "bullish":
        return candle.close > candle.open and candle.close > ema20
    return candle.close < candle.open and candle.close < ema20


def _stop_level(direction: str, entry: float, technical: dict[str, Any], atr14: float | None, fallback_percent: float) -> float:
    levels = technical.get("support_levels") if direction == "bullish" else technical.get("resistance_levels")
    valid = [float(level) for level in (levels or []) if (float(level) < entry if direction == "bullish" else float(level) > entry)]
    if valid:
        return max(valid) if direction == "bullish" else min(valid)
    risk = max(float(atr14 or 0), entry * fallback_percent / 100)
    return entry - risk if direction == "bullish" else entry + risk


def _backtest_stop(direction: str, entry: float, atr14: float | None, fallback_percent: float) -> float:
    risk = max(float(atr14 or 0), entry * fallback_percent / 100)
    return entry - risk if direction == "bullish" else entry + risk


def _historical_direction(candle: Candle, fast: float | None, slow: float | None, rsi14: float | None, selected: str) -> str | None:
    if fast is None or slow is None or rsi14 is None:
        return None
    if selected in {"both", "bullish"} and candle.close > candle.open and candle.close > fast > slow and rsi14 >= 52:
        return "bullish"
    if selected in {"both", "bearish"} and candle.close < candle.open and candle.close < fast < slow and rsi14 <= 48:
        return "bearish"
    return None


def _simulate_exit(candles: list[Candle], ema20_series: list[float | None], entry_index: int, direction: str, stop: float, target: float, max_bars: int) -> tuple[int, float, str, float, float]:
    entry = candles[entry_index].close
    favorable = adverse = 0.0
    last_index = min(len(candles) - 1, entry_index + max_bars)
    for index in range(entry_index + 1, last_index + 1):
        candle = candles[index]
        if direction == "bullish":
            favorable = max(favorable, candle.high - entry)
            adverse = min(adverse, candle.low - entry)
            if candle.low <= stop:
                return index, stop, "stop_loss", favorable, adverse
            if candle.high >= target:
                return index, target, "target_reached", favorable, adverse
        else:
            favorable = max(favorable, entry - candle.low)
            adverse = min(adverse, entry - candle.high)
            if candle.high >= stop:
                return index, stop, "stop_loss", favorable, adverse
            if candle.low <= target:
                return index, target, "target_reached", favorable, adverse
        ema20 = ema20_series[index]
        if ema20 is not None and ((direction == "bullish" and candle.close < ema20) or (direction == "bearish" and candle.close > ema20)):
            return index, candle.close, "ema20_reversal", favorable, adverse
    return last_index, candles[last_index].close, "maximum_holding_period", favorable, adverse


def _backtest_payload(trades: list[dict[str, Any]], horizon: str, timeframe: str, target_r: float, candle_count: int) -> dict[str, Any]:
    r_values = [float(row["r_multiple"]) for row in trades]
    wins = [value for value in r_values if value > 0]
    losses = [value for value in r_values if value <= 0]
    equity = peak = max_drawdown = 0.0
    for value in r_values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "horizon": horizon,
        "timeframe": timeframe,
        "target_r_multiple": target_r,
        "candle_count": candle_count,
        "trade_count": len(trades),
        "trades": trades,
        "metrics": {
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(trades) * 100) if trades else None,
            "average_r": mean(r_values) if r_values else None,
            "expectancy_r": mean(r_values) if r_values else None,
            "profit_factor": (gross_profit / gross_loss) if gross_loss else (None if not gross_profit else gross_profit),
            "net_r": sum(r_values),
            "max_drawdown_r": max_drawdown,
            "average_open_seconds": mean([row["open_seconds"] for row in trades]) if trades else None,
        },
        "coverage": {
            "technicals": "EMA20, EMA50, RSI14, ATR14, and candle direction replayed",
            "option_chain": "Not replayed unless timestamped historical option-chain snapshots are available",
            "costs": "Spot signal test; option premium, brokerage, slippage, and IV decay are not modeled",
        },
        "warnings": [
            "Live entries additionally require non-conflicting current option-chain context and use IV as risk context.",
            "Treat results as NIFTY spot signal validation, not option-strategy P&L.",
        ],
    }


def _date_filter(candles: list[Candle], from_date: str | None, to_date: str | None) -> list[Candle]:
    start = _as_naive_ist(from_date) if from_date else None
    end = _as_naive_ist(to_date) if to_date else None
    if end and len(to_date or "") == 10:
        end = end.replace(hour=23, minute=59, second=59)
    return [
        row
        for row in candles
        if (start is None or _as_naive_ist(row.timestamp) >= start)
        and (end is None or _as_naive_ist(row.timestamp) <= end)
    ]


def _ema_series(values: list[float], period: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    if len(values) < period:
        return output
    current = sum(values[:period]) / period
    output[period - 1] = current
    multiplier = 2 / (period + 1)
    for index in range(period, len(values)):
        current = (values[index] - current) * multiplier + current
        output[index] = current
    return output


def _rsi_series(values: list[float], period: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return output
    gains = [max(0.0, values[index] - values[index - 1]) for index in range(1, len(values))]
    losses = [max(0.0, values[index - 1] - values[index]) for index in range(1, len(values))]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    output[period] = _rsi_value(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        average_gain = ((average_gain * (period - 1)) + gains[index - 1]) / period
        average_loss = ((average_loss * (period - 1)) + losses[index - 1]) / period
        output[index] = _rsi_value(average_gain, average_loss)
    return output


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0
    return 100 - (100 / (1 + average_gain / average_loss))


def _atr_series(candles: list[Candle], period: int) -> list[float | None]:
    output: list[float | None] = [None] * len(candles)
    if not candles:
        return output
    true_ranges: list[float] = []
    for index, candle in enumerate(candles):
        if index == 0:
            true_ranges.append(candle.high - candle.low)
        else:
            previous_close = candles[index - 1].close
            true_ranges.append(max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close)))
    if len(true_ranges) < period:
        return output
    current = sum(true_ranges[:period]) / period
    output[period - 1] = current
    for index in range(period, len(true_ranges)):
        current = ((current * (period - 1)) + true_ranges[index]) / period
        output[index] = current
    return output


def _exit_payload(candle: Candle, price: float, reason: str) -> dict[str, Any]:
    return {"checked_only": False, "candle_timestamp": candle.timestamp, "price": float(price), "reason": reason}


def _as_naive_ist(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is not None:
        return parsed.astimezone(IST).replace(tzinfo=None)
    return parsed


def _horizon_for_timeframe(timeframe: str) -> str:
    for horizon, config in HORIZON_CONFIG.items():
        if config["timeframe"] == timeframe:
            return horizon
    raise ValueError(f"Unsupported NIFTY timeframe: {timeframe}")


def _duration(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m" if hours else f"{minutes}m"
