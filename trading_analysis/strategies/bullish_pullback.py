from __future__ import annotations

from trading_analysis.strategies._scanner_adapter import SCANNER_PARAMETERS, scanner_signal
from trading_analysis.strategies.base import StrategyDefinition


def strategy() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id="bullish_pullback",
        label="Bullish Pullback",
        description="Candidate where a bullish trend pulls back near EMA/SMA/support and holds risk context.",
        direction="bullish",
        default_timeframe="day",
        min_candles=50,
        default_params={"pullback_near_atr": 0.85, "rsi_min": 40, "rsi_max": 55},
        parameter_schema=SCANNER_PARAMETERS,
        generate_signal=lambda symbol, candles, params: scanner_signal(symbol, candles, params, "bullish_pullback", "long"),
    )
