from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any

from trading_analysis.analysis.krishna_setup import KrishnaSetupMatch, scan_krishna_bullish_setup
from trading_analysis.analysis.market_structure import MarketStructure, analyze_market_structure
from trading_analysis.analysis.technical import ema
from trading_analysis.models import Candle


DEFAULT_FORWARD_HORIZONS = (5, 10, 15)


@dataclass(frozen=True)
class BacktestConfig:
    holding_days: int = 10
    forward_horizons: tuple[int, ...] = DEFAULT_FORWARD_HORIZONS
    min_candles: int = 60


def backtest_krishna_bullish_setup(
    symbol: str,
    candles: list[Candle],
    config: BacktestConfig | None = None,
) -> dict[str, Any]:
    config = config or BacktestConfig()
    candles = sorted(candles, key=lambda candle: candle.timestamp)
    max_horizon = max([config.holding_days, *config.forward_horizons])
    signals: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []

    if config.holding_days <= 0:
        raise ValueError("holding_days must be greater than zero.")
    if len(candles) < config.min_candles + max_horizon + 1:
        return _symbol_result(symbol, candles, signals, trades, [], "insufficient_candles")

    next_available_entry_index = 0
    for signal_index in range(config.min_candles - 1, len(candles) - max_horizon):
        history = candles[: signal_index + 1]
        structure = analyze_market_structure(history) if len(history) >= 10 else None
        match = scan_krishna_bullish_setup(symbol, history, structure)
        if match is None:
            continue

        forward = _forward_returns(candles, signal_index, config.forward_horizons)
        signal = _signal_row(symbol, candles[signal_index], match, structure, forward)
        signals.append(signal)

        entry_index = signal_index + 1
        exit_index = signal_index + config.holding_days
        if entry_index < next_available_entry_index:
            signal["trade_status"] = "skipped_overlap"
            continue
        if exit_index >= len(candles):
            signal["trade_status"] = "skipped_no_exit"
            continue

        entry_price = candles[entry_index].open
        exit_price = candles[exit_index].close
        if entry_price <= 0:
            signal["trade_status"] = "skipped_bad_entry"
            continue

        return_percent = ((exit_price - entry_price) / entry_price) * 100
        trade = {
            **{key: signal[key] for key in signal if key not in {"forward_returns", "forward_success"}},
            "entry_date": candles[entry_index].timestamp.date().isoformat(),
            "exit_date": candles[exit_index].timestamp.date().isoformat(),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "holding_days": config.holding_days,
            "return_percent": return_percent,
            "win": return_percent > 0,
            "forward_returns": forward,
            "forward_success": {str(days): row["success"] for days, row in forward.items()},
        }
        trades.append(trade)
        signal["trade_status"] = "taken"
        next_available_entry_index = exit_index + 1

    baseline_trades = _ema_baseline_trades(symbol, candles, config.holding_days)
    return _symbol_result(symbol, candles, signals, trades, baseline_trades, "ok")


def aggregate_krishna_backtests(
    results: list[dict[str, Any]],
    errors: list[dict[str, str]] | None = None,
    limit_symbols: int | None = None,
) -> dict[str, Any]:
    errors = errors or []
    all_trades = [trade for result in results for trade in result.get("trades", [])]
    all_signals = [signal for result in results for signal in result.get("signals", [])]
    baseline_trades = [trade for result in results for trade in result.get("baseline_trades", [])]
    buy_hold_returns = [
        result["baselines"]["buy_and_hold"]["return_percent"]
        for result in results
        if result.get("baselines", {}).get("buy_and_hold", {}).get("return_percent") is not None
    ]
    symbol_rows = [
        {
            "symbol": result["symbol"],
            "status": result["status"],
            "signals": result["signal_count"],
            "trades": result["trade_count"],
            "win_rate": result["metrics"]["win_rate"],
            "avg_return": result["metrics"]["avg_return"],
            "profit_factor": result["metrics"]["profit_factor"],
            "max_drawdown": result["metrics"]["max_drawdown"],
            "buy_hold_return": result["baselines"]["buy_and_hold"]["return_percent"],
        }
        for result in results
    ]
    symbol_rows.sort(key=lambda row: (row["trades"], row["win_rate"] or 0, row["avg_return"] or 0), reverse=True)

    return {
        "setup": "krishna_bullish_pullback_watch",
        "setup_label": "Krishna bullish pullback watch",
        "timeframe": "day",
        "timeframe_label": "Daily",
        "analyzed_symbols": len(results),
        "symbols_with_signals": sum(1 for result in results if result.get("signal_count", 0) > 0),
        "symbols_with_trades": sum(1 for result in results if result.get("trade_count", 0) > 0),
        "signal_count": len(all_signals),
        "trade_count": len(all_trades),
        "limit_symbols": limit_symbols,
        "metrics": metrics_from_trades(all_trades),
        "forward_accuracy": forward_accuracy(all_signals, DEFAULT_FORWARD_HORIZONS),
        "confidence_buckets": confidence_buckets(all_trades),
        "monthly_performance": monthly_performance(all_trades),
        "baselines": {
            "buy_and_hold": _baseline_return_summary(buy_hold_returns),
            "ema20_gt_ema50": metrics_from_trades(baseline_trades),
        },
        "symbol_results": symbol_rows,
        "trades": sorted(all_trades, key=lambda row: (row["exit_date"], row["symbol"]), reverse=True),
        "signals": sorted(all_signals, key=lambda row: (row["signal_date"], row["symbol"]), reverse=True),
        "errors": errors[:30],
        "summary": {
            "points": [
                "This validates directional edge with daily cached candles; it does not simulate option premium, IV, theta, margin, slippage, or expiry risk.",
                "Forward accuracy counts every historical signal. Trade P&L uses non-overlapping futures-style long trades entered at the next candle open.",
                "Signals and feature values are generated only from candles available on the signal date to avoid look-ahead bias.",
                "Use higher score/confidence buckets to check whether the scanner score is actually meaningful over history.",
            ],
        },
    }


def metrics_from_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [float(trade["return_percent"]) for trade in trades]
    if not returns:
        return {
            "trades": 0,
            "winners": 0,
            "losers": 0,
            "win_rate": None,
            "avg_return": None,
            "avg_winner": None,
            "avg_loser": None,
            "expectancy": None,
            "profit_factor": None,
            "max_drawdown": None,
            "ending_return": None,
        }
    winners = [value for value in returns if value > 0]
    losers = [value for value in returns if value <= 0]
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    win_rate = (len(winners) / len(returns)) * 100
    avg_winner = mean(winners) if winners else 0.0
    avg_loser = mean(losers) if losers else 0.0
    expectancy = ((win_rate / 100) * avg_winner) + (((100 - win_rate) / 100) * avg_loser)
    return {
        "trades": len(returns),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": win_rate,
        "avg_return": mean(returns),
        "avg_winner": avg_winner,
        "avg_loser": avg_loser,
        "expectancy": expectancy,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "max_drawdown": _max_drawdown(returns),
        "ending_return": _compounded_return(returns),
    }


def forward_accuracy(signals: list[dict[str, Any]], horizons: tuple[int, ...]) -> list[dict[str, Any]]:
    rows = []
    for days in horizons:
        key = str(days)
        evaluated = [
            signal["forward_returns"][key]
            for signal in signals
            if key in signal.get("forward_returns", {}) and signal["forward_returns"][key]["success"] is not None
        ]
        winners = sum(1 for row in evaluated if row["success"])
        rows.append(
            {
                "horizon_days": days,
                "signals": len(evaluated),
                "successes": winners,
                "accuracy": (winners / len(evaluated)) * 100 if evaluated else None,
                "avg_forward_return": mean([row["return_percent"] for row in evaluated]) if evaluated else None,
            }
        )
    return rows


def confidence_buckets(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranges = [(0, 60, "<60"), (60, 70, "60-69"), (70, 80, "70-79"), (80, 90, "80-89"), (90, 101, "90+")]
    rows = []
    for low, high, label in ranges:
        bucket = [trade for trade in trades if low <= int(trade.get("score") or 0) < high]
        metrics = metrics_from_trades(bucket)
        rows.append(
            {
                "score_bucket": label,
                "trades": metrics["trades"],
                "win_rate": metrics["win_rate"],
                "avg_return": metrics["avg_return"],
                "expectancy": metrics["expectancy"],
            }
        )
    return rows


def monthly_performance(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        month = str(trade["exit_date"])[:7]
        groups.setdefault(month, []).append(trade)
    rows = []
    for month, month_trades in sorted(groups.items()):
        metrics = metrics_from_trades(month_trades)
        rows.append(
            {
                "month": month,
                "trades": metrics["trades"],
                "win_rate": metrics["win_rate"],
                "return_sum": sum(float(trade["return_percent"]) for trade in month_trades),
                "avg_return": metrics["avg_return"],
            }
        )
    return rows


def _symbol_result(
    symbol: str,
    candles: list[Candle],
    signals: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    baseline_trades: list[dict[str, Any]],
    status: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "status": status,
        "candle_count": len(candles),
        "from": candles[0].timestamp.date().isoformat() if candles else None,
        "to": candles[-1].timestamp.date().isoformat() if candles else None,
        "signal_count": len(signals),
        "trade_count": len(trades),
        "metrics": metrics_from_trades(trades),
        "forward_accuracy": forward_accuracy(signals, DEFAULT_FORWARD_HORIZONS),
        "confidence_buckets": confidence_buckets(trades),
        "monthly_performance": monthly_performance(trades),
        "baselines": {
            "buy_and_hold": _buy_hold(candles),
            "ema20_gt_ema50": metrics_from_trades(baseline_trades),
        },
        "baseline_trades": baseline_trades,
        "signals": signals,
        "trades": trades,
    }


def _signal_row(
    symbol: str,
    candle: Candle,
    match: KrishnaSetupMatch,
    structure: MarketStructure | None,
    forward: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row = {
        "symbol": symbol.upper(),
        "signal_date": candle.timestamp.date().isoformat(),
        "direction": "bullish",
        "signal_close": candle.close,
        "score": match.score,
        "confidence": match.confidence,
        "structure_trend": structure.trend if structure else None,
        "support": structure.support if structure else None,
        "resistance": structure.resistance if structure else None,
        "invalidation": structure.invalidation if structure else None,
        "reasons": match.reasons,
        "warnings": match.warnings,
        "reason_text": "; ".join(match.reasons),
        "forward_returns": forward,
        "forward_success": {str(days): row["success"] for days, row in forward.items()},
        "trade_status": "signal_only",
        "features": asdict(match),
    }
    row["features"].pop("reasons", None)
    row["features"].pop("warnings", None)
    return row


def _forward_returns(candles: list[Candle], signal_index: int, horizons: tuple[int, ...]) -> dict[str, dict[str, Any]]:
    output = {}
    signal_close = candles[signal_index].close
    for days in horizons:
        target_index = signal_index + days
        if target_index >= len(candles) or signal_close <= 0:
            output[str(days)] = {"return_percent": None, "success": None}
            continue
        forward_close = candles[target_index].close
        return_percent = ((forward_close - signal_close) / signal_close) * 100
        output[str(days)] = {"return_percent": return_percent, "success": forward_close > signal_close}
    return output


def _ema_baseline_trades(symbol: str, candles: list[Candle], holding_days: int) -> list[dict[str, Any]]:
    trades = []
    next_available_entry_index = 0
    for signal_index in range(49, len(candles) - holding_days):
        closes = [candle.close for candle in candles[: signal_index + 1]]
        ema20 = ema(closes, 20)
        ema50 = ema(closes, 50)
        if ema20 is None or ema50 is None or not (ema20 > ema50 and candles[signal_index].close > ema20):
            continue
        entry_index = signal_index + 1
        if entry_index < next_available_entry_index:
            continue
        exit_index = signal_index + holding_days
        entry_price = candles[entry_index].open
        exit_price = candles[exit_index].close
        if entry_price <= 0:
            continue
        trades.append(
            {
                "symbol": symbol.upper(),
                "signal_date": candles[signal_index].timestamp.date().isoformat(),
                "entry_date": candles[entry_index].timestamp.date().isoformat(),
                "exit_date": candles[exit_index].timestamp.date().isoformat(),
                "return_percent": ((exit_price - entry_price) / entry_price) * 100,
                "win": exit_price > entry_price,
            }
        )
        next_available_entry_index = exit_index + 1
    return trades


def _buy_hold(candles: list[Candle]) -> dict[str, Any]:
    if len(candles) < 2 or candles[0].close <= 0:
        return {"return_percent": None}
    return {
        "from": candles[0].timestamp.date().isoformat(),
        "to": candles[-1].timestamp.date().isoformat(),
        "return_percent": ((candles[-1].close - candles[0].close) / candles[0].close) * 100,
    }


def _baseline_return_summary(returns: list[float]) -> dict[str, Any]:
    if not returns:
        return {"symbols": 0, "avg_return": None, "positive_rate": None}
    positives = [value for value in returns if value > 0]
    return {
        "symbols": len(returns),
        "avg_return": mean(returns),
        "positive_rate": (len(positives) / len(returns)) * 100,
    }


def _max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for return_percent in returns:
        equity *= 1 + (return_percent / 100)
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, ((peak - equity) / peak) * 100)
    return max_drawdown


def _compounded_return(returns: list[float]) -> float:
    equity = 1.0
    for return_percent in returns:
        equity *= 1 + (return_percent / 100)
    return (equity - 1) * 100
