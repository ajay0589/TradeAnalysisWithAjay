from __future__ import annotations

from trading_analysis.strategies._scanner_adapter import SCANNER_PARAMETERS, scanner_signal
from trading_analysis.strategies.base import StrategyDefinition


def strategy() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id="bearish_pullback",
        label="Bearish Pullback",
        description="Candidate where a bearish trend pulls back near EMA/SMA/resistance and rejects.",
        direction="bearish",
        default_timeframe="day",
        min_candles=50,
        default_params={"pullback_near_atr": 0.85, "rsi_min": 45, "rsi_max": 60},
        parameter_schema=SCANNER_PARAMETERS,
        generate_signal=lambda symbol, candles, params: scanner_signal(symbol, candles, params, "bearish_pullback", "short"),
    )
