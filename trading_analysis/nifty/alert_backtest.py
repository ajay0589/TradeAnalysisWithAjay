from __future__ import annotations

from bisect import bisect_right
from datetime import datetime
from statistics import mean, median
from typing import Any


DEFAULT_ALERT_HORIZONS = (3, 5, 10, 15)


def backtest_nifty_alert_signals(
    alerts: list[dict[str, Any]],
    candles: list[Any],
    horizons: list[int] | tuple[int, ...] = DEFAULT_ALERT_HORIZONS,
    neutral_threshold_percent: float = 0.5,
    volatile_threshold_percent: float = 0.5,
) -> dict[str, Any]:
    warnings: list[str] = []
    sorted_candles = sorted(candles, key=lambda candle: _naive_datetime(candle.timestamp))
    if not sorted_candles:
        return {
            "alert_count": len(alerts),
            "evaluated_alerts": 0,
            "rows": [],
            "metrics": _empty_metrics(),
            "warnings": ["No NIFTY candles available for alert backtest."],
        }

    clean_horizons = sorted({max(1, int(value)) for value in horizons})
    timestamps = [_naive_datetime(candle.timestamp) for candle in sorted_candles]
    rows: list[dict[str, Any]] = []
    evaluated_alert_ids: set[int] = set()

    for alert in sorted(alerts, key=lambda row: str(row.get("created_at") or "")):
        created_at = _parse_datetime(alert.get("created_at"))
        if created_at is None:
            warnings.append(f"Alert {alert.get('id')} skipped because created_at is invalid.")
            continue
        entry_index = bisect_right(timestamps, created_at)
        if entry_index >= len(sorted_candles):
            rows.append(_expired_row(alert, "No later candle available after alert time."))
            continue
        entry_candle = sorted_candles[entry_index]
        entry_price = float(entry_candle.open or entry_candle.close)
        if entry_price <= 0:
            rows.append(_expired_row(alert, "Entry candle has invalid price."))
            continue
        evaluated_alert_ids.add(int(alert.get("id") or len(evaluated_alert_ids) + 1))
        for horizon in clean_horizons:
            exit_index = entry_index + horizon
            if exit_index >= len(sorted_candles):
                rows.append(_pending_row(alert, entry_candle, horizon, "Not enough future candles for this horizon."))
                continue
            window = sorted_candles[entry_index : exit_index + 1]
            exit_candle = sorted_candles[exit_index]
            forward_return = _return_percent(entry_price, float(exit_candle.close))
            direction = str(alert.get("direction") or "watch").lower()
            directional_return = _directional_return(
                direction,
                forward_return,
                neutral_threshold_percent=neutral_threshold_percent,
                volatile_threshold_percent=volatile_threshold_percent,
            )
            success = _success(
                direction,
                forward_return,
                neutral_threshold_percent=neutral_threshold_percent,
                volatile_threshold_percent=volatile_threshold_percent,
            )
            favorable, adverse = _favorable_adverse(direction, entry_price, window)
            rows.append(
                {
                    "alert_id": alert.get("id"),
                    "alert_time": alert.get("created_at"),
                    "alert_type": alert.get("alert_type"),
                    "mode": alert.get("mode"),
                    "horizon": alert.get("horizon") or alert.get("mode"),
                    "strategy_id": alert.get("strategy_id"),
                    "direction": direction,
                    "score": alert.get("score"),
                    "confidence": alert.get("confidence"),
                    "severity": alert.get("severity"),
                    "entry_time": entry_candle.timestamp,
                    "entry_price": entry_price,
                    "exit_time": exit_candle.timestamp,
                    "exit_price": exit_candle.close,
                    "holding_bars": horizon,
                    "forward_return_percent": forward_return,
                    "directional_return_percent": directional_return,
                    "max_favorable_percent": favorable,
                    "max_adverse_percent": adverse,
                    "success": success,
                    "status": "evaluated",
                }
            )

    evaluated_rows = [row for row in rows if row.get("status") == "evaluated"]
    return {
        "alert_count": len(alerts),
        "evaluated_alerts": len(evaluated_alert_ids),
        "row_count": len(rows),
        "horizons": clean_horizons,
        "rows": rows,
        "metrics": {
            "by_holding_bars": _group_metrics(evaluated_rows, "holding_bars"),
            "by_horizon": _group_metrics(evaluated_rows, "horizon"),
            "by_strategy": _group_metrics(evaluated_rows, "strategy_id"),
            "by_direction": _group_metrics(evaluated_rows, "direction"),
            "by_score_bucket": _group_metrics(evaluated_rows, "_score_bucket"),
            "overall": _metrics_for_rows(evaluated_rows),
        },
        "warnings": warnings,
        "notes": [
            "This is a signal-quality backtest using NIFTY spot candles after stored alerts.",
            "It does not simulate option premium, IV decay, margin, slippage, or exact strike P&L.",
        ],
    }


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _naive_datetime(value)
    if not value:
        return None
    try:
        return _naive_datetime(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def _naive_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _return_percent(entry: float, exit_price: float) -> float:
    return ((exit_price - entry) / entry) * 100


def _directional_return(
    direction: str,
    forward_return: float,
    neutral_threshold_percent: float,
    volatile_threshold_percent: float,
) -> float:
    if direction == "bullish":
        return forward_return
    if direction == "bearish":
        return -forward_return
    if direction == "neutral":
        return neutral_threshold_percent - abs(forward_return)
    if direction == "volatile":
        return abs(forward_return) - volatile_threshold_percent
    return forward_return


def _success(
    direction: str,
    forward_return: float,
    neutral_threshold_percent: float,
    volatile_threshold_percent: float,
) -> bool:
    if direction == "bullish":
        return forward_return > 0
    if direction == "bearish":
        return forward_return < 0
    if direction == "neutral":
        return abs(forward_return) <= neutral_threshold_percent
    if direction == "volatile":
        return abs(forward_return) >= volatile_threshold_percent
    return False


def _favorable_adverse(direction: str, entry_price: float, candles: list[Any]) -> tuple[float, float]:
    returns = [_return_percent(entry_price, float(candle.close)) for candle in candles]
    if direction == "bearish":
        directional = [-value for value in returns]
    elif direction == "neutral":
        directional = [-abs(value) for value in returns]
    elif direction == "volatile":
        directional = [abs(value) for value in returns]
    else:
        directional = returns
    return max(directional), min(directional)


def _expired_row(alert: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "alert_id": alert.get("id"),
        "alert_time": alert.get("created_at"),
        "horizon": alert.get("horizon") or alert.get("mode"),
        "strategy_id": alert.get("strategy_id"),
        "direction": alert.get("direction"),
        "score": alert.get("score"),
        "holding_bars": None,
        "status": "expired",
        "reason": reason,
    }


def _pending_row(alert: dict[str, Any], entry_candle: Any, horizon: int, reason: str) -> dict[str, Any]:
    return {
        "alert_id": alert.get("id"),
        "alert_time": alert.get("created_at"),
        "horizon": alert.get("horizon") or alert.get("mode"),
        "strategy_id": alert.get("strategy_id"),
        "direction": alert.get("direction"),
        "score": alert.get("score"),
        "entry_time": entry_candle.timestamp,
        "entry_price": entry_candle.open,
        "holding_bars": horizon,
        "status": "pending",
        "reason": reason,
    }


def _group_metrics(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        value = _score_bucket(row.get("score")) if key == "_score_bucket" else row.get(key)
        label = str(value if value is not None else "NA")
        groups.setdefault(label, []).append(row)
    return [
        {"group": group, **_metrics_for_rows(group_rows)}
        for group, group_rows in sorted(groups.items(), key=lambda item: item[0])
    ]


def _metrics_for_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "signals": 0,
            "successes": 0,
            "accuracy": None,
            "avg_forward_return": None,
            "median_forward_return": None,
            "avg_directional_return": None,
            "avg_max_favorable": None,
            "avg_max_adverse": None,
        }
    successes = sum(1 for row in rows if row.get("success"))
    forward = [float(row["forward_return_percent"]) for row in rows]
    directional = [float(row["directional_return_percent"]) for row in rows]
    favorable = [float(row["max_favorable_percent"]) for row in rows]
    adverse = [float(row["max_adverse_percent"]) for row in rows]
    return {
        "signals": len(rows),
        "successes": successes,
        "accuracy": successes / len(rows) * 100,
        "avg_forward_return": mean(forward),
        "median_forward_return": median(forward),
        "avg_directional_return": mean(directional),
        "avg_max_favorable": mean(favorable),
        "avg_max_adverse": mean(adverse),
    }


def _score_bucket(score: Any) -> str:
    if score is None:
        return "NA"
    value = int(float(score))
    if value < 60:
        return "<60"
    if value < 70:
        return "60-69"
    if value < 80:
        return "70-79"
    if value < 90:
        return "80-89"
    return "90+"


def _empty_metrics() -> dict[str, Any]:
    return {
        "overall": _metrics_for_rows([]),
        "by_holding_bars": [],
        "by_horizon": [],
        "by_strategy": [],
        "by_direction": [],
        "by_score_bucket": [],
    }
