from __future__ import annotations

from trading_analysis.strategies._scanner_adapter import SCANNER_PARAMETERS, scanner_signal
from trading_analysis.strategies.base import StrategyDefinition


def strategy() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id="neutral_range",
        label="Neutral Range",
        description="Range-bound candidate for historical neutral setup validation.",
        direction="neutral",
        default_timeframe="day",
        min_candles=50,
        default_params={"rsi_min": 40, "rsi_max": 60},
        parameter_schema=SCANNER_PARAMETERS,
        generate_signal=lambda symbol, candles, params: scanner_signal(symbol, candles, params, "neutral_range", "neutral"),
    )
