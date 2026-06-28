from __future__ import annotations

from typing import Any

from trading_analysis.analysis.krishna_setup import scan_krishna_bullish_setup
from trading_analysis.analysis.market_structure import analyze_market_structure
from trading_analysis.analysis.scanners import ScannerConfig, ScanMatch, scan_symbol_for_setups
from trading_analysis.models import Candle
from trading_analysis.strategies.base import StrategyParameter, StrategySignal


SCANNER_PARAMETERS = [
    StrategyParameter("breakout_period", "Breakout period", "int", 20, "Donchian breakout lookback.", 5, 100),
    StrategyParameter("slow_breakout_period", "Slow breakout period", "int", 55, "Longer breakout confirmation lookback.", 20, 200),
    StrategyParameter("min_volume_ratio", "Minimum volume ratio", "float", 1.3, "Volume versus 20-candle average.", 0.1, 10),
    StrategyParameter("pullback_near_atr", "Pullback near ATR", "float", 0.85, "Max ATR distance from pullback level.", 0.1, 5),
    StrategyParameter("rsi_min", "RSI minimum", "float", None, "Optional post-filter RSI floor.", 0, 100),
    StrategyParameter("rsi_max", "RSI maximum", "float", None, "Optional post-filter RSI ceiling.", 0, 100),
]


def scanner_signal(
    symbol: str,
    candles: list[Candle],
    params: dict[str, Any],
    setup_type: str,
    side: str,
    entry_type: str = "next_open",
) -> StrategySignal | None:
    if not candles:
        return None
    config = _scanner_config(params)
    structure = analyze_market_structure(candles) if len(candles) >= 10 else None
    matches = scan_symbol_for_setups(symbol, candles, structure=structure, config=config)
    match = next((candidate for candidate in matches if candidate.setup_type == setup_type), None)
    if match is None or not _passes_rsi_filter(match, params):
        return None
    return signal_from_match(symbol, candles, match, side=side, entry_type=entry_type)


def signal_from_match(
    symbol: str,
    candles: list[Candle],
    match: ScanMatch,
    side: str,
    entry_type: str = "next_open",
) -> StrategySignal:
    indicators = dict(match.indicators or {})
    entry_price = _entry_price(match, indicators)
    return StrategySignal(
        symbol=symbol.upper(),
        strategy_id=match.setup_type,
        signal_date=candles[-1].timestamp.date(),
        side=side,
        score=match.score,
        confidence=match.confidence,
        entry_type=entry_type,
        entry_price=entry_price,
        stop_loss=match.invalidation,
        target=None,
        invalidation=match.invalidation,
        reasons=list(match.reasons or []),
        warnings=list(match.warnings or []),
        indicators=indicators,
    )


def krishna_signal(symbol: str, candles: list[Candle], params: dict[str, Any]) -> StrategySignal | None:
    if not candles:
        return None
    structure = analyze_market_structure(candles) if len(candles) >= 10 else None
    match = scan_krishna_bullish_setup(symbol, candles, structure)
    if match is None:
        return None
    indicators = match.to_dict()
    invalidation = structure.support if structure else None
    return StrategySignal(
        symbol=symbol.upper(),
        strategy_id="krishna_bullish_pullback",
        signal_date=candles[-1].timestamp.date(),
        side="long",
        score=match.score,
        confidence=match.confidence,
        entry_type=str(params.get("entry_type") or "next_open"),
        entry_price=match.close,
        stop_loss=invalidation,
        target=None,
        invalidation=invalidation,
        reasons=list(match.reasons),
        warnings=list(match.warnings),
        indicators=indicators,
    )


def _scanner_config(params: dict[str, Any]) -> ScannerConfig:
    return ScannerConfig(
        donchian_fast_period=int(params.get("breakout_period") or 20),
        donchian_slow_period=int(params.get("slow_breakout_period") or 55),
        breakout_volume_ratio=float(params.get("min_volume_ratio") or 1.3),
        pullback_near_level_atr=float(params.get("pullback_near_atr") or 0.85),
    )


def _passes_rsi_filter(match: ScanMatch, params: dict[str, Any]) -> bool:
    rsi_value = (match.indicators or {}).get("rsi14")
    if rsi_value is None:
        return True
    rsi_min = params.get("rsi_min")
    rsi_max = params.get("rsi_max")
    if rsi_min is not None and rsi_value < float(rsi_min):
        return False
    if rsi_max is not None and rsi_value > float(rsi_max):
        return False
    return True


def _entry_price(match: ScanMatch, indicators: dict[str, Any]) -> float | None:
    if match.trigger is not None:
        return match.trigger
    return indicators.get("close") or match.close
