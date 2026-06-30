from __future__ import annotations

from typing import Any

from trading_analysis.analysis.market_structure import analyze_market_structure
from trading_analysis.analysis.scanners import ScannerConfig, ScanMatch, scan_symbol_for_setups
from trading_analysis.models import Candle
from trading_analysis.strategies.base import StrategyDefinition, StrategyParameter, StrategySignal


PARAMETERS = [
    StrategyParameter("pullback_near_atr", "Pullback near ATR", "float", 0.85, "Max ATR distance from EMA/SMA/resistance.", 0.1, 5),
    StrategyParameter("rsi_min", "RSI minimum", "float", 45, "Minimum RSI for bearish pullback candidate.", 0, 100),
    StrategyParameter("rsi_max", "RSI maximum", "float", 60, "Maximum RSI for bearish pullback candidate.", 0, 100),
    StrategyParameter("entry_buffer_percent", "Entry buffer percent", "float", 0.10, "Breakout-stop buffer below signal candle low.", 0, 5),
    StrategyParameter("entry_buffer_atr", "Entry buffer ATR", "float", None, "Optional ATR-based breakout-stop buffer.", 0, 2),
    StrategyParameter("min_score", "Minimum score", "int", 0, "Minimum scanner score required.", 0, 100),
    StrategyParameter("require_structure_trend", "Require downtrend structure", "bool", False, "Require market structure to be downtrend."),
    StrategyParameter("stop_mode", "Stop mode", "str", "resistance_or_candle", "resistance_or_candle, candle, or resistance."),
]


def strategy() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id="bearish_pullback",
        label="Bearish Pullback",
        description="Bearish pullback candidate with breakout-stop confirmation below the signal candle low.",
        direction="bearish",
        default_timeframe="day",
        min_candles=50,
        default_params={
            "pullback_near_atr": 0.85,
            "rsi_min": 45,
            "rsi_max": 60,
            "entry_buffer_percent": 0.10,
            "entry_buffer_atr": None,
            "min_score": 0,
            "require_structure_trend": False,
            "stop_mode": "resistance_or_candle",
        },
        parameter_schema=PARAMETERS,
        generate_signal=generate_signal,
    )


def generate_signal(symbol: str, candles: list[Candle], params: dict[str, Any]) -> StrategySignal | None:
    if not candles:
        return None
    candles = sorted(candles, key=lambda candle: candle.timestamp)
    structure = analyze_market_structure(candles) if len(candles) >= 10 else None
    config = ScannerConfig(pullback_near_level_atr=float(params.get("pullback_near_atr") or 0.85))
    matches = scan_symbol_for_setups(symbol, candles, structure=structure, config=config)
    match = next((candidate for candidate in matches if candidate.setup_type == "bearish_pullback"), None)
    if match is None or not _passes_filters(match, params, expected_structure="downtrend"):
        return None

    latest = candles[-1]
    indicators = dict(match.indicators or {})
    buffer = _entry_buffer(latest, indicators, params)
    entry_price = latest.low - buffer
    stop_loss = _bearish_stop(latest, match, params)
    return StrategySignal(
        symbol=symbol.upper(),
        strategy_id="bearish_pullback",
        signal_date=latest.timestamp.date(),
        side="short",
        score=match.score,
        confidence=match.confidence,
        entry_type="breakout_stop",
        entry_price=entry_price,
        stop_loss=stop_loss,
        target=None,
        invalidation=stop_loss,
        reasons=_reasons(match, indicators, entry_price, stop_loss),
        warnings=list(match.warnings or []),
        indicators=indicators,
    )


def _passes_filters(match: ScanMatch, params: dict[str, Any], expected_structure: str) -> bool:
    if match.score < int(params.get("min_score") or 0):
        return False
    rsi_value = (match.indicators or {}).get("rsi14")
    if rsi_value is not None:
        if rsi_value < float(params.get("rsi_min") if params.get("rsi_min") is not None else 45):
            return False
        if rsi_value > float(params.get("rsi_max") if params.get("rsi_max") is not None else 60):
            return False
    if params.get("require_structure_trend") and (match.indicators or {}).get("structure_trend") != expected_structure:
        return False
    return True


def _entry_buffer(latest: Candle, indicators: dict[str, Any], params: dict[str, Any]) -> float:
    atr_value = indicators.get("atr14")
    atr_buffer = params.get("entry_buffer_atr")
    if atr_buffer is not None and atr_value is not None:
        return max(0.0, float(atr_value) * float(atr_buffer))
    return max(0.0, latest.low * (float(params.get("entry_buffer_percent") or 0) / 100))


def _bearish_stop(latest: Candle, match: ScanMatch, params: dict[str, Any]) -> float:
    resistance = match.resistance
    stop_mode = str(params.get("stop_mode") or "resistance_or_candle")
    if stop_mode == "candle":
        return latest.high
    if stop_mode == "resistance" and resistance is not None:
        return resistance
    return max(value for value in [latest.high, resistance] if value is not None)


def _reasons(match: ScanMatch, indicators: dict[str, Any], entry_price: float, stop_loss: float) -> list[str]:
    rsi = indicators.get("rsi14")
    resistance = match.resistance
    return [
        "Trend context is bearish for a pullback setup.",
        "Price is near the configured EMA/SMA/resistance pullback zone.",
        f"RSI {rsi:.1f} is inside the bearish pullback range." if isinstance(rsi, (int, float)) else "RSI pullback context is available.",
        f"Resistance hold/invalidation context is near {resistance:.2f}." if isinstance(resistance, (int, float)) else "Resistance hold context is based on candle structure.",
        f"Confirmation trigger is breakout-stop below signal candle at {entry_price:.2f}.",
        f"Risk context uses invalidation near {stop_loss:.2f}.",
        *list(match.reasons or [])[:3],
    ]
