from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from trading_analysis.models import Candle


def backtest_nifty_context(
    candles: list[Candle],
    strategy_id: str,
    mode: str = "swing",
    from_date: str | None = None,
    to_date: str | None = None,
    days: int | None = None,
    params: dict[str, Any] | None = None,
    exit_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = params or {}
    exit_rules = exit_rules or {}
    filtered = _filter_candles(candles, from_date, to_date, days)
    holding = int(exit_rules.get("holding_bars") or params.get("holding_bars") or 5)
    if len(filtered) <= holding + 60:
        return {
            "strategy_id": strategy_id,
            "mode": mode,
            "signals": [],
            "simulated_trades": [],
            "context_forward_returns": [],
            "metrics": {"signals": 0, "accuracy": None, "avg_forward_return": None},
            "warnings": ["Not enough NIFTY candles for context-only backtest."],
        }
    signals = []
    returns = []
    for index in range(50, len(filtered) - holding):
        window = filtered[: index + 1]
        signal = _simple_signal(window, strategy_id)
        if signal is None:
            continue
        future = filtered[index + holding]
        forward_return = ((future.close - filtered[index].close) / filtered[index].close) * 100
        if signal["side"] == "short":
            forward_return *= -1
        signals.append(signal)
        returns.append(
            {
                "signal_date": filtered[index].timestamp.isoformat(timespec="seconds"),
                "side": signal["side"],
                "entry_reference": filtered[index].close,
                "exit_reference": future.close,
                "holding_bars": holding,
                "forward_return": forward_return,
                "success": forward_return > 0,
            }
        )
    wins = len([row for row in returns if row["success"]])
    avg_return = (sum(row["forward_return"] for row in returns) / len(returns)) if returns else None
    return {
        "strategy_id": strategy_id,
        "mode": mode,
        "signals": signals,
        "simulated_trades": [],
        "context_forward_returns": returns,
        "metrics": {
            "signals": len(signals),
            "trade_count": 0,
            "accuracy": (wins / len(returns) * 100) if returns else None,
            "avg_forward_return": avg_return,
        },
        "warnings": [
            "Context-only backtest: historical option premiums were not used.",
            "Forward spot movement is shown instead of exact option strategy P&L.",
        ],
    }


def _filter_candles(candles: list[Candle], from_date: str | None, to_date: str | None, days: int | None) -> list[Candle]:
    output = sorted(candles, key=lambda candle: candle.timestamp)
    end = _parse_date(to_date) or (output[-1].timestamp if output else datetime.now())
    start = _parse_date(from_date)
    if days and not start:
        start = end - timedelta(days=days)
    return [candle for candle in output if (not start or candle.timestamp >= start) and candle.timestamp <= end]


def _simple_signal(candles: list[Candle], strategy_id: str) -> dict[str, Any] | None:
    closes = [candle.close for candle in candles]
    if len(closes) < 50:
        return None
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50
    latest = candles[-1]
    side = "long" if "bull" in strategy_id or "call" in strategy_id else "short" if "bear" in strategy_id or "put" in strategy_id else "neutral"
    if side == "long" and latest.close > sma20 > sma50:
        return {"signal_date": latest.timestamp.isoformat(timespec="seconds"), "side": "long", "score": 70}
    if side == "short" and latest.close < sma20 < sma50:
        return {"signal_date": latest.timestamp.isoformat(timespec="seconds"), "side": "short", "score": 70}
    if side == "neutral" and abs(sma20 - sma50) / latest.close < 0.01:
        return {"signal_date": latest.timestamp.isoformat(timespec="seconds"), "side": "neutral", "score": 60}
    return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt == "%Y-%m-%d":
                return parsed.replace(hour=23, minute=59, second=59)
            return parsed
        except ValueError:
            continue
    return datetime.fromisoformat(value)
