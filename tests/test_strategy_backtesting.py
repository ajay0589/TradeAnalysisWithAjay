from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta

from trading_analysis.backtesting.engine import backtest_strategy_for_symbol, backtest_strategy_for_symbols
from trading_analysis.backtesting.metrics import score_bucket_performance
from trading_analysis.backtesting.models import BacktestConfig
from trading_analysis.models import Candle
from trading_analysis.strategies.base import StrategyDefinition, StrategySignal
from trading_analysis.strategies.registry import get_strategy, list_strategies


def candle(day: int, open_: float, high: float, low: float, close: float, volume: int = 1000) -> Candle:
    return Candle(datetime(2025, 1, 1) + timedelta(days=day), open_, high, low, close, volume)


def breakout_candles() -> list[Candle]:
    candles: list[Candle] = []
    for index in range(60):
        if index < 55:
            close = 100 + (index % 10) * 0.10 + index * 0.02
            candles.append(candle(index, close - 0.1, close + 0.3, close - 0.6, close, 1000))
        elif index < 59:
            close = 101.8 + (index - 55) * 0.1
            candles.append(candle(index, close - 0.1, close + 0.3, close - 0.6, close, 1000))
        else:
            candles.append(candle(index, 101.8, 102.55, 101.45, 102.5, 3000))
    return candles


def breakdown_candles() -> list[Candle]:
    candles: list[Candle] = []
    for index in range(60):
        if index < 55:
            close = 100 - (index % 10) * 0.10 - index * 0.02
            candles.append(candle(index, close + 0.1, close + 0.6, close - 0.3, close, 1000))
        elif index < 59:
            close = 98.2 - (index - 55) * 0.1
            candles.append(candle(index, close + 0.1, close + 0.6, close - 0.3, close, 1000))
        else:
            candles.append(candle(index, 98.2, 98.55, 97.45, 97.5, 3000))
    return candles


def engine_candles() -> list[Candle]:
    rows = [candle(i, 100, 101, 99, 100, 1000) for i in range(3)]
    rows.extend(
        [
            candle(3, 100, 101, 99.5, 100.5, 1000),
            candle(4, 100.5, 102.5, 100.1, 102.0, 1000),
            candle(5, 102.0, 103.0, 101.0, 102.5, 1000),
        ]
    )
    rows.extend(candle(i, 102, 103, 101, 102, 1000) for i in range(6, 25))
    return rows


def fixed_strategy(side: str = "long", strategy_id: str = "fixed_test", min_candles: int = 3) -> StrategyDefinition:
    def generate(symbol: str, candles: list[Candle], params: dict) -> StrategySignal | None:
        if len(candles) < min_candles:
            return None
        latest = candles[-1]
        return StrategySignal(
            symbol=symbol,
            strategy_id=strategy_id,
            signal_date=latest.timestamp.date(),
            side=side,
            score=80,
            confidence="high",
            entry_type="next_open",
            entry_price=latest.close,
            stop_loss=None,
            target=None,
            invalidation=None,
            reasons=["Synthetic setup signal."],
            warnings=[],
            indicators={"atr14": 2.0},
        )

    return StrategyDefinition(
        strategy_id=strategy_id,
        label="Fixed Test",
        description="Synthetic test strategy",
        direction=side,
        default_timeframe="day",
        min_candles=min_candles,
        default_params={},
        parameter_schema=[],
        generate_signal=generate,
    )


class StrategyBacktestingTests(unittest.TestCase):
    def test_strategy_registry_lists_builtins(self) -> None:
        ids = {item["strategy_id"] for item in list_strategies()}
        self.assertIn("bullish_breakout", ids)
        self.assertIn("krishna_bullish_pullback", ids)

    def test_bullish_breakout_signal_generated(self) -> None:
        signal = get_strategy("bullish_breakout").generate_signal("TEST", breakout_candles(), {})
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "long")
        self.assertTrue(signal.reasons)

    def test_bearish_breakdown_signal_generated(self) -> None:
        signal = get_strategy("bearish_breakdown").generate_signal("TEST", breakdown_candles(), {})
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "short")
        self.assertTrue(signal.reasons)

    def test_backtest_enters_on_next_candle_open_without_lookahead(self) -> None:
        payload = backtest_strategy_for_symbol(
            "TEST",
            engine_candles(),
            fixed_strategy(),
            BacktestConfig(strategy_id="fixed_test", holding_bars=2, allow_overlap=False),
        )
        first_trade = payload["trades"][0]
        self.assertEqual(first_trade["entry_price"], 100)
        self.assertGreater(first_trade["entry_date"], first_trade["signal_date"])

    def test_stop_loss_exit(self) -> None:
        rows = engine_candles()
        rows[3] = candle(3, 100, 100.5, 98.5, 99.0)
        payload = backtest_strategy_for_symbol(
            "TEST",
            rows,
            fixed_strategy(),
            BacktestConfig(strategy_id="fixed_test", stop_type="percent", stop_percent=1, holding_bars=5),
        )
        self.assertEqual(payload["trades"][0]["exit_reason"], "stop_loss")

    def test_target_exit(self) -> None:
        payload = backtest_strategy_for_symbol(
            "TEST",
            engine_candles(),
            fixed_strategy(),
            BacktestConfig(strategy_id="fixed_test", target_type="percent", target_percent=2, holding_bars=5),
        )
        self.assertEqual(payload["trades"][0]["exit_reason"], "target")

    def test_holding_period_exit(self) -> None:
        payload = backtest_strategy_for_symbol(
            "TEST",
            engine_candles(),
            fixed_strategy(),
            BacktestConfig(strategy_id="fixed_test", holding_bars=1),
        )
        self.assertEqual(payload["trades"][0]["exit_reason"], "holding_period")

    def test_short_strategy_pnl_direction_is_correct(self) -> None:
        rows = engine_candles()
        rows[4] = candle(4, 99, 99.5, 94.0, 95.0)
        payload = backtest_strategy_for_symbol(
            "TEST",
            rows,
            fixed_strategy(side="short"),
            BacktestConfig(strategy_id="fixed_test", holding_bars=1),
        )
        self.assertGreater(payload["trades"][0]["return_percent"], 0)

    def test_overlapping_trades_are_skipped_by_default(self) -> None:
        payload = backtest_strategy_for_symbol(
            "TEST",
            engine_candles(),
            fixed_strategy(),
            BacktestConfig(strategy_id="fixed_test", holding_bars=5, allow_overlap=False),
        )
        signal_count = len(payload["signals"])
        trade_count = len(payload["trades"])
        self.assertGreater(signal_count, trade_count)

    def test_score_bucket_metrics_and_json_serialization(self) -> None:
        payload = backtest_strategy_for_symbols(
            {"TEST": engine_candles()},
            fixed_strategy(),
            BacktestConfig(strategy_id="fixed_test", holding_bars=2),
        )
        self.assertTrue(score_bucket_performance(payload["trades"]))
        json.dumps(payload, default=str)


if __name__ == "__main__":
    unittest.main()
