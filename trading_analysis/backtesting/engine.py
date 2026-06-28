from __future__ import annotations

from typing import Any

from trading_analysis.backtesting.exits import entry_price_for, exit_for, resolve_stop, resolve_target
from trading_analysis.backtesting.metrics import (
    FORWARD_HORIZONS,
    calculate_metrics,
    forward_accuracy,
    monthly_performance,
    score_bucket_performance,
    symbol_performance,
)
from trading_analysis.backtesting.models import BacktestConfig, BacktestResult, BacktestTrade
from trading_analysis.models import Candle
from trading_analysis.strategies.base import StrategyDefinition, StrategySignal


def backtest_strategy_for_symbol(
    symbol: str,
    candles: list[Candle],
    strategy: StrategyDefinition,
    config: BacktestConfig,
) -> dict[str, Any]:
    candles = sorted(candles, key=lambda candle: candle.timestamp)
    params = strategy.merged_params(config.strategy_params)
    signals: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    next_available_signal_index = 0
    min_index = max(strategy.min_candles, 2) - 1
    max_horizon = max([config.holding_bars, *FORWARD_HORIZONS])
    if len(candles) <= min_index + 1:
        return {"symbol": symbol.upper(), "signals": signals, "trades": trades, "status": "insufficient_candles"}

    last_signal_index = max(min_index, len(candles) - max_horizon - 1)
    for signal_index in range(min_index, last_signal_index + 1):
        history = candles[: signal_index + 1]
        signal = strategy.generate_signal(symbol, history, params)
        if signal is None:
            continue

        signal_row = _signal_row(signal, candles, signal_index)
        signals.append(signal_row)
        if signal.side not in {"long", "short"}:
            signal_row["trade_status"] = "signal_only"
            continue
        if not config.allow_overlap and signal_index < next_available_signal_index:
            signal_row["trade_status"] = "skipped_overlap"
            continue

        trade = _simulate_trade(symbol, candles, signal_index, signal, strategy, config)
        if trade is None:
            signal_row["trade_status"] = "skipped_no_entry"
            continue
        trade_row = trade.to_dict()
        trades.append(trade_row)
        signal_row["trade_status"] = "taken"
        if not config.allow_overlap:
            next_available_signal_index = _index_for_date(candles, trade.exit_date) + 1

    return {"symbol": symbol.upper(), "signals": signals, "trades": trades, "status": "ok"}


def backtest_strategy_for_symbols(
    symbol_candles_map: dict[str, list[Candle]],
    strategy: StrategyDefinition,
    config: BacktestConfig,
) -> dict[str, Any]:
    results = []
    errors: list[dict[str, str]] = []
    for symbol, candles in symbol_candles_map.items():
        try:
            results.append(backtest_strategy_for_symbol(symbol, candles, strategy, config))
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})

    trades = [trade for result in results for trade in result.get("trades", [])]
    signals = [signal for result in results for signal in result.get("signals", [])]
    payload = BacktestResult(
        strategy_id=strategy.strategy_id,
        timeframe=config.timeframe,
        analyzed_symbols=len(results),
        signal_count=len(signals),
        trade_count=len(trades),
        metrics=calculate_metrics(trades),
        forward_accuracy=forward_accuracy(signals),
        score_buckets=score_bucket_performance(trades),
        monthly_performance=monthly_performance(trades),
        symbol_performance=symbol_performance(trades),
        trades=sorted(trades, key=lambda row: (row["exit_date"], row["symbol"]), reverse=True),
        signals=sorted(signals, key=lambda row: (row["signal_date"], row["symbol"]), reverse=True),
        errors=errors,
        config=config.to_dict(),
    ).to_dict()
    payload["strategy"] = strategy.to_dict()
    payload["summary"] = {
        "points": [
            "Historical simulation uses cached candles only and does not place orders.",
            "Signals are generated using candles available up to the signal bar.",
            "Default entry is the next candle open; signal-close entries still evaluate exits after the signal bar.",
            "If stop and target are hit in the same candle, stop is assumed first and the trade is marked ambiguous.",
        ]
    }
    return payload


def _simulate_trade(
    symbol: str,
    candles: list[Candle],
    signal_index: int,
    signal: StrategySignal,
    strategy: StrategyDefinition,
    config: BacktestConfig,
) -> BacktestTrade | None:
    entry_index, entry_price = entry_price_for(signal, candles, signal_index, config)
    if entry_index is None or entry_price is None or entry_index >= len(candles):
        return None
    stop_loss = resolve_stop(signal, entry_price, config)
    target = resolve_target(signal, entry_price, stop_loss, config)
    exit_start_index = entry_index + 1 if config.entry == "signal_close" else entry_index
    if exit_start_index >= len(candles):
        return None
    exit_data = exit_for(candles, exit_start_index, signal.side, stop_loss, target, config.holding_bars)
    exit_index = int(exit_data["index"])
    exit_price = float(exit_data["price"])
    quantity = _quantity(entry_price, stop_loss, config)
    return_percent = _return_percent(signal.side, entry_price, exit_price, config)
    pnl = _pnl(signal.side, entry_price, exit_price, quantity, config)
    risk_per_unit = abs(entry_price - stop_loss) if stop_loss is not None else None
    r_multiple = _r_multiple(signal.side, entry_price, exit_price, risk_per_unit)
    mfe, mae = _excursions(candles[entry_index : exit_index + 1], signal.side, entry_price)
    return BacktestTrade(
        symbol=symbol.upper(),
        strategy_id=strategy.strategy_id,
        side=signal.side,
        signal_date=signal.signal_date.isoformat(),
        entry_date=candles[entry_index].timestamp.date().isoformat(),
        exit_date=candles[exit_index].timestamp.date().isoformat(),
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        score=signal.score,
        confidence=signal.confidence,
        exit_reason=str(exit_data["reason"]),
        bars_held=max(0, exit_index - entry_index),
        return_percent=return_percent,
        pnl=pnl,
        r_multiple=r_multiple,
        max_favorable_excursion_percent=mfe,
        max_adverse_excursion_percent=mae,
        intrabar_ambiguous=bool(exit_data["intrabar_ambiguous"]),
        stop_loss=stop_loss,
        target=target,
        reasons=list(signal.reasons),
        warnings=list(signal.warnings),
        indicators=dict(signal.indicators),
    )


def _signal_row(signal: StrategySignal, candles: list[Candle], signal_index: int) -> dict[str, Any]:
    row = signal.to_dict()
    row["signal_date"] = signal.signal_date.isoformat()
    row["signal_index"] = signal_index
    row["forward_returns"] = _forward_returns(signal.side, candles, signal_index)
    row["trade_status"] = "signal_only"
    return row


def _forward_returns(side: str, candles: list[Candle], signal_index: int) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    start = candles[signal_index].close
    for horizon in FORWARD_HORIZONS:
        target_index = signal_index + horizon
        if target_index >= len(candles) or start <= 0:
            output[str(horizon)] = {"return_percent": None, "success": None}
            continue
        end = candles[target_index].close
        if side == "short":
            return_percent = ((start - end) / start) * 100
            success = end < start
        else:
            return_percent = ((end - start) / start) * 100
            success = end > start
        output[str(horizon)] = {"return_percent": return_percent, "success": success}
    return output


def _quantity(entry_price: float, stop_loss: float | None, config: BacktestConfig) -> float:
    if config.position_sizing == "fixed_quantity":
        return float(config.fixed_quantity)
    if config.position_sizing == "fixed_risk" and stop_loss is not None and entry_price != stop_loss:
        risk_capital = config.capital * (config.risk_per_trade_percent / 100)
        return max(0.0, risk_capital / abs(entry_price - stop_loss))
    capital = config.fixed_capital if config.fixed_capital is not None else config.capital
    return max(0.0, capital / entry_price) if entry_price > 0 else 0.0


def _return_percent(side: str, entry_price: float, exit_price: float, config: BacktestConfig) -> float:
    gross = ((exit_price - entry_price) / entry_price) * 100
    if side == "short":
        gross = ((entry_price - exit_price) / entry_price) * 100
    costs = ((config.slippage_bps + config.brokerage_bps) * 2) / 100
    return gross - costs


def _pnl(side: str, entry_price: float, exit_price: float, quantity: float, config: BacktestConfig) -> float:
    gross = (exit_price - entry_price) * quantity
    if side == "short":
        gross = (entry_price - exit_price) * quantity
    costs = (entry_price + exit_price) * quantity * ((config.slippage_bps + config.brokerage_bps) / 10000)
    return gross - costs


def _r_multiple(side: str, entry_price: float, exit_price: float, risk_per_unit: float | None) -> float | None:
    if risk_per_unit is None or risk_per_unit <= 0:
        return None
    move = exit_price - entry_price if side == "long" else entry_price - exit_price
    return move / risk_per_unit


def _excursions(candles: list[Candle], side: str, entry_price: float) -> tuple[float, float]:
    if side == "short":
        favorable = max(((entry_price - candle.low) / entry_price) * 100 for candle in candles)
        adverse = min(((entry_price - candle.high) / entry_price) * 100 for candle in candles)
    else:
        favorable = max(((candle.high - entry_price) / entry_price) * 100 for candle in candles)
        adverse = min(((candle.low - entry_price) / entry_price) * 100 for candle in candles)
    return favorable, adverse


def _index_for_date(candles: list[Candle], value: str) -> int:
    for index, candle in enumerate(candles):
        if candle.timestamp.date().isoformat() == value:
            return index
    return 0
