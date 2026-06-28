from __future__ import annotations

from statistics import mean, median
from typing import Any


FORWARD_HORIZONS = (1, 3, 5, 10, 15)


def calculate_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [float(trade["return_percent"]) for trade in trades]
    if not returns:
        return {
            "trades": 0,
            "winners": 0,
            "losers": 0,
            "win_rate": None,
            "avg_return": None,
            "median_return": None,
            "expectancy": None,
            "profit_factor": None,
            "max_drawdown": None,
            "ending_return": None,
            "avg_r_multiple": None,
        }
    winners = [value for value in returns if value > 0]
    losers = [value for value in returns if value <= 0]
    win_rate = (len(winners) / len(returns)) * 100
    avg_winner = mean(winners) if winners else 0.0
    avg_loser = mean(losers) if losers else 0.0
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    r_values = [float(trade["r_multiple"]) for trade in trades if trade.get("r_multiple") is not None]
    return {
        "trades": len(returns),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": win_rate,
        "avg_return": mean(returns),
        "median_return": median(returns),
        "expectancy": ((win_rate / 100) * avg_winner) + (((100 - win_rate) / 100) * avg_loser),
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "max_drawdown": max_drawdown(returns),
        "ending_return": compounded_return(returns),
        "avg_r_multiple": mean(r_values) if r_values else None,
    }


def max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in returns:
        equity *= 1 + (value / 100)
        peak = max(peak, equity)
        if peak:
            drawdown = max(drawdown, ((peak - equity) / peak) * 100)
    return drawdown


def compounded_return(returns: list[float]) -> float:
    equity = 1.0
    for value in returns:
        equity *= 1 + (value / 100)
    return (equity - 1) * 100


def score_bucket_performance(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = [(0, 60, "<60"), (60, 70, "60-69"), (70, 80, "70-79"), (80, 90, "80-89"), (90, 101, "90+")]
    rows = []
    for low, high, label in buckets:
        bucket = [trade for trade in trades if low <= int(trade.get("score") or 0) < high]
        metrics = calculate_metrics(bucket)
        rows.append(
            {
                "score_bucket": label,
                "trades": metrics["trades"],
                "win_rate": metrics["win_rate"],
                "avg_return": metrics["avg_return"],
                "expectancy": metrics["expectancy"],
                "avg_r_multiple": metrics["avg_r_multiple"],
            }
        )
    return rows


def monthly_performance(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        groups.setdefault(str(trade["exit_date"])[:7], []).append(trade)
    return [
        {
            "month": month,
            "trades": calculate_metrics(rows)["trades"],
            "win_rate": calculate_metrics(rows)["win_rate"],
            "avg_return": calculate_metrics(rows)["avg_return"],
            "total_return": sum(float(row["return_percent"]) for row in rows),
            "return_sum": sum(float(row["return_percent"]) for row in rows),
        }
        for month, rows in sorted(groups.items())
    ]


def symbol_performance(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        groups.setdefault(str(trade["symbol"]), []).append(trade)
    rows = []
    for symbol, symbol_trades in groups.items():
        metrics = calculate_metrics(symbol_trades)
        rows.append(
            {
                "symbol": symbol,
                "trades": metrics["trades"],
                "win_rate": metrics["win_rate"],
                "avg_return": metrics["avg_return"],
                "ending_return": metrics["ending_return"],
                "profit_factor": metrics["profit_factor"],
            }
        )
    return sorted(rows, key=lambda row: (row["trades"], row["avg_return"] or 0), reverse=True)


def forward_accuracy(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for horizon in FORWARD_HORIZONS:
        evaluated = [
            signal["forward_returns"][str(horizon)]
            for signal in signals
            if str(horizon) in signal.get("forward_returns", {}) and signal["forward_returns"][str(horizon)]["success"] is not None
        ]
        successes = sum(1 for item in evaluated if item["success"])
        rows.append(
            {
                "horizon_bars": horizon,
                "signals": len(evaluated),
                "successes": successes,
                "accuracy": (successes / len(evaluated)) * 100 if evaluated else None,
                "avg_forward_return": mean([item["return_percent"] for item in evaluated]) if evaluated else None,
            }
        )
    return rows
