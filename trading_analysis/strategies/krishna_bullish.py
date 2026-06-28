from __future__ import annotations

from trading_analysis.strategies._scanner_adapter import krishna_signal
from trading_analysis.strategies.base import StrategyDefinition, StrategyParameter


def strategy() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id="krishna_bullish_pullback",
        label="Krishna Bullish Pullback",
        description="Daily bullish pullback watch where the yellow Chande Kroll line sits above recent candles and selected indicators.",
        direction="bullish",
        default_timeframe="day",
        min_candles=60,
        default_params={"entry_type": "next_open"},
        parameter_schema=[
            StrategyParameter("entry_type", "Entry type", "str", "next_open", "Signal entry style used by the historical simulation."),
        ],
        generate_signal=krishna_signal,
    )
