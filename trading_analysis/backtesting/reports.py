from __future__ import annotations

from typing import Any


def compact_backtest_summary(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("metrics") or {}
    return {
        "strategy_id": payload.get("strategy_id"),
        "timeframe": payload.get("timeframe"),
        "analyzed_symbols": payload.get("analyzed_symbols"),
        "signal_count": payload.get("signal_count"),
        "trade_count": payload.get("trade_count"),
        "win_rate": metrics.get("win_rate"),
        "avg_return": metrics.get("avg_return"),
        "expectancy": metrics.get("expectancy"),
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown": metrics.get("max_drawdown"),
        "ending_return": metrics.get("ending_return"),
    }
