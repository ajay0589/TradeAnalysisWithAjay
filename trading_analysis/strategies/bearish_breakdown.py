from __future__ import annotations

from trading_analysis.strategies._scanner_adapter import SCANNER_PARAMETERS, scanner_signal
from trading_analysis.strategies.base import StrategyDefinition


def strategy() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id="bearish_breakdown",
        label="Bearish Breakdown",
        description="Candidate where price breaks the previous range low with volume and downside momentum.",
        direction="bearish",
        default_timeframe="day",
        min_candles=55,
        default_params={"breakout_period": 20, "slow_breakout_period": 55, "min_volume_ratio": 1.3, "rsi_min": 25, "rsi_max": 45},
        parameter_schema=SCANNER_PARAMETERS,
        generate_signal=lambda symbol, candles, params: scanner_signal(symbol, candles, params, "bearish_breakdown", "short"),
    )
