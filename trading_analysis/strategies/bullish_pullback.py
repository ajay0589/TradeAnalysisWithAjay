from __future__ import annotations

from typing import Any

from trading_analysis.analysis.market_structure import analyze_market_structure
from trading_analysis.analysis.scanners import ScannerConfig, ScanMatch, scan_symbol_for_setups
from trading_analysis.models import Candle
from trading_analysis.strategies.base import StrategyDefinition, StrategyParameter, StrategySignal


PARAMETERS = [
    StrategyParameter("pullback_near_atr", "Pullback near ATR", "float", 0.85, "Max ATR distance from EMA/SMA/support.", 0.1, 5),
    StrategyParameter("rsi_min", "RSI minimum", "float", 40, "Minimum RSI for pullback candidate.", 0, 100),
    StrategyParameter("rsi_max", "RSI maximum", "float", 55, "Maximum RSI for pullback candidate.", 0, 100),
    StrategyParameter("entry_buffer_percent", "Entry buffer percent", "float", 0.10, "Breakout-stop buffer above signal candle high.", 0, 5),
    StrategyParameter("entry_buffer_atr", "Entry buffer ATR", "float", None, "Optional ATR-based breakout-stop buffer.", 0, 2),
    StrategyParameter("min_score", "Minimum score", "int", 0, "Minimum scanner score required.", 0, 100),
    StrategyParameter("require_structure_trend", "Require uptrend structure", "bool", False, "Require market structure to be uptrend."),
    StrategyParameter("stop_mode", "Stop mode", "str", "support_or_candle", "support_or_candle, candle, or support."),
]


def strategy() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id="bullish_pullback",
        label="Bullish Pullback",
        description="Bullish pullback candidate with breakout-stop confirmation above the signal candle high.",
        direction="bullish",
        default_timeframe="day",
        min_candles=50,
        default_params={
            "pullback_near_atr": 0.85,
            "rsi_min": 40,
            "rsi_max": 55,
            "entry_buffer_percent": 0.10,
            "entry_buffer_atr": None,
            "min_score": 0,
            "require_structure_trend": False,
            "stop_mode": "support_or_candle",
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
    match = next((candidate for candidate in matches if candidate.setup_type == "bullish_pullback"), None)
    if match is None or not _passes_filters(match, params, expected_structure="uptrend"):
        return None

    latest = candles[-1]
    indicators = dict(match.indicators or {})
    buffer = _entry_buffer(latest, indicators, params)
    entry_price = latest.high + buffer
    stop_loss = _bullish_stop(latest, match, params)
    return StrategySignal(
        symbol=symbol.upper(),
        strategy_id="bullish_pullback",
        signal_date=latest.timestamp.date(),
        side="long",
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
        if rsi_value < float(params.get("rsi_min") if params.get("rsi_min") is not None else 40):
            return False
        if rsi_value > float(params.get("rsi_max") if params.get("rsi_max") is not None else 55):
            return False
    if params.get("require_structure_trend") and (match.indicators or {}).get("structure_trend") != expected_structure:
        return False
    return True


def _entry_buffer(latest: Candle, indicators: dict[str, Any], params: dict[str, Any]) -> float:
    atr_value = indicators.get("atr14")
    atr_buffer = params.get("entry_buffer_atr")
    if atr_buffer is not None and atr_value is not None:
        return max(0.0, float(atr_value) * float(atr_buffer))
    return max(0.0, latest.high * (float(params.get("entry_buffer_percent") or 0) / 100))


def _bullish_stop(latest: Candle, match: ScanMatch, params: dict[str, Any]) -> float:
    support = match.support
    stop_mode = str(params.get("stop_mode") or "support_or_candle")
    if stop_mode == "candle":
        return latest.low
    if stop_mode == "support" and support is not None:
        return support
    return min(value for value in [latest.low, support] if value is not None)


def _reasons(match: ScanMatch, indicators: dict[str, Any], entry_price: float, stop_loss: float) -> list[str]:
    rsi = indicators.get("rsi14")
    support = match.support
    return [
        "Trend context is bullish for a pullback setup.",
        "Price is near the configured EMA/SMA/support pullback zone.",
        f"RSI {rsi:.1f} is inside the bullish pullback range." if isinstance(rsi, (int, float)) else "RSI pullback context is available.",
        f"Support hold/invalidation context is near {support:.2f}." if isinstance(support, (int, float)) else "Support hold context is based on candle structure.",
        f"Confirmation trigger is breakout-stop above signal candle at {entry_price:.2f}.",
        f"Risk context uses invalidation near {stop_loss:.2f}.",
        *list(match.reasons or [])[:3],
    ]
