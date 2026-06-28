from __future__ import annotations

from trading_analysis.strategies._scanner_adapter import SCANNER_PARAMETERS, scanner_signal
from trading_analysis.strategies.base import StrategyDefinition


def strategy() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id="bullish_breakout",
        label="Bullish Breakout",
        description="Candidate where price breaks the previous range high with volume and momentum confirmation.",
        direction="bullish",
        default_timeframe="day",
        min_candles=55,
        default_params={"breakout_period": 20, "slow_breakout_period": 55, "min_volume_ratio": 1.3, "rsi_min": 55, "rsi_max": 75},
        parameter_schema=SCANNER_PARAMETERS,
        generate_signal=lambda symbol, candles, params: scanner_signal(symbol, candles, params, "bullish_breakout", "long"),
    )
