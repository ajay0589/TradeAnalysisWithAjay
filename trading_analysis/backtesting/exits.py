from __future__ import annotations

from typing import Any

from trading_analysis.backtesting.models import BacktestConfig
from trading_analysis.models import Candle
from trading_analysis.strategies.base import StrategySignal


def resolve_stop(signal: StrategySignal, entry_price: float, config: BacktestConfig) -> float | None:
    if config.stop_type == "signal":
        return signal.stop_loss or signal.invalidation
    if config.stop_type == "percent" and config.stop_percent:
        distance = entry_price * (config.stop_percent / 100)
        return entry_price - distance if signal.side == "long" else entry_price + distance
    if config.stop_type == "atr" and config.stop_atr:
        atr_value = _atr_from_signal(signal)
        if atr_value is None:
            return signal.stop_loss or signal.invalidation
        distance = atr_value * config.stop_atr
        return entry_price - distance if signal.side == "long" else entry_price + distance
    return None


def resolve_target(signal: StrategySignal, entry_price: float, stop_loss: float | None, config: BacktestConfig) -> float | None:
    if config.target_type == "percent" and config.target_percent:
        distance = entry_price * (config.target_percent / 100)
        return entry_price + distance if signal.side == "long" else entry_price - distance
    if config.target_type == "atr" and config.target_atr:
        atr_value = _atr_from_signal(signal)
        if atr_value is None:
            return signal.target
        distance = atr_value * config.target_atr
        return entry_price + distance if signal.side == "long" else entry_price - distance
    if config.target_type == "risk_multiple" and config.target_r_multiple and stop_loss is not None:
        risk = abs(entry_price - stop_loss)
        distance = risk * config.target_r_multiple
        return entry_price + distance if signal.side == "long" else entry_price - distance
    return signal.target if config.target_type == "signal" else None


def entry_price_for(signal: StrategySignal, candles: list[Candle], signal_index: int, config: BacktestConfig) -> tuple[int | None, float | None]:
    if config.entry == "signal_close":
        return signal_index, candles[signal_index].close
    entry_index = signal_index + 1
    if entry_index >= len(candles):
        return None, None
    wanted = signal.entry_price or candles[signal_index].close
    if config.entry == "breakout_stop":
        for index in _entry_candidate_indexes(candles, entry_index, config.entry_valid_bars):
            candle = candles[index]
            if signal.side == "long" and candle.high >= wanted:
                return index, wanted
            if signal.side == "short" and candle.low <= wanted:
                return index, wanted
        return None, None
    if config.entry == "limit_retest":
        for index in _entry_candidate_indexes(candles, entry_index, config.entry_valid_bars):
            candle = candles[index]
            if candle.low <= wanted <= candle.high:
                return index, wanted
        return None, None
    next_candle = candles[entry_index]
    return entry_index, next_candle.open


def exit_for(
    candles: list[Candle],
    entry_index: int,
    side: str,
    stop_loss: float | None,
    target: float | None,
    holding_bars: int,
) -> dict[str, Any]:
    max_exit_index = min(len(candles) - 1, entry_index + max(1, holding_bars))
    ambiguous = False
    for index in range(entry_index, max_exit_index + 1):
        candle = candles[index]
        stop_hit = _stop_hit(side, candle, stop_loss)
        target_hit = _target_hit(side, candle, target)
        if stop_hit and target_hit:
            ambiguous = True
            return {"index": index, "price": stop_loss, "reason": "stop_loss", "intrabar_ambiguous": ambiguous}
        if stop_hit:
            return {"index": index, "price": stop_loss, "reason": "stop_loss", "intrabar_ambiguous": ambiguous}
        if target_hit:
            return {"index": index, "price": target, "reason": "target", "intrabar_ambiguous": ambiguous}
    return {
        "index": max_exit_index,
        "price": candles[max_exit_index].close,
        "reason": "holding_period",
        "intrabar_ambiguous": ambiguous,
    }


def _stop_hit(side: str, candle: Candle, stop_loss: float | None) -> bool:
    if stop_loss is None:
        return False
    return candle.low <= stop_loss if side == "long" else candle.high >= stop_loss


def _target_hit(side: str, candle: Candle, target: float | None) -> bool:
    if target is None:
        return False
    return candle.high >= target if side == "long" else candle.low <= target


def _atr_from_signal(signal: StrategySignal) -> float | None:
    value = signal.indicators.get("atr14")
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def _entry_candidate_indexes(candles: list[Candle], start_index: int, valid_bars: int):
    end_index = min(len(candles), start_index + max(1, valid_bars))
    return range(start_index, end_index)
