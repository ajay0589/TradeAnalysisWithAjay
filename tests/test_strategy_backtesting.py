from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from trading_analysis.backtesting.engine import backtest_strategy_for_symbol, backtest_strategy_for_symbols
from trading_analysis.backtesting.metrics import score_bucket_performance
from trading_analysis.backtesting.models import BacktestConfig
from trading_analysis.web_app import ReusableThreadingHTTPServer, TradingRequestHandler
from trading_analysis.web_services import AnalysisService
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


def extended_breakout_candles() -> list[Candle]:
    rows = breakout_candles()
    for offset in range(20):
        close = 102.8 + (offset * 0.2)
        rows.append(candle(60 + offset, close - 0.2, close + 0.6, close - 0.8, close, 1200))
    return rows


def fixed_strategy(
    side: str = "long",
    strategy_id: str = "fixed_test",
    min_candles: int = 3,
    entry_price: float | None = None,
) -> StrategyDefinition:
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
            entry_price=entry_price if entry_price is not None else latest.close,
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

    def test_breakout_stop_entry_waits_for_valid_bars(self) -> None:
        rows = engine_candles()
        rows[3] = candle(3, 100, 101, 99, 100)
        rows[4] = candle(4, 100, 102, 99, 101)
        rows[5] = candle(5, 101, 104, 100, 103)
        payload = backtest_strategy_for_symbol(
            "TEST",
            rows,
            fixed_strategy(entry_price=103),
            BacktestConfig(strategy_id="fixed_test", entry="breakout_stop", entry_valid_bars=3, holding_bars=2),
        )
        self.assertEqual(payload["trades"][0]["entry_date"], rows[5].timestamp.date().isoformat())

    def test_pending_entry_expires_when_not_triggered(self) -> None:
        rows = engine_candles()
        rows[3] = candle(3, 100, 100.5, 99, 100)
        rows[4] = candle(4, 100, 100.5, 99, 100)
        payload = backtest_strategy_for_symbol(
            "TEST",
            rows,
            fixed_strategy(entry_price=103),
            BacktestConfig(strategy_id="fixed_test", entry="breakout_stop", entry_valid_bars=2, holding_bars=2),
        )
        self.assertEqual(payload["signals"][0]["trade_status"], "expired_no_entry")

    def test_invalid_backtest_parameter_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BacktestConfig.from_mapping("fixed_test", backtest_params={"entry": "bad_entry"})

    def test_invalid_strategy_parameter_is_rejected(self) -> None:
        strategy = get_strategy("bullish_breakout")
        with self.assertRaises(ValueError):
            strategy.validate_params({"not_a_param": 1})

    def test_analysis_service_strategy_methods_and_backtest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_watchlist(root / "watchlist.json", ["TEST"])
            _write_candles(root / "candles" / "TEST.csv", extended_breakout_candles())
            service = AnalysisService(
                watchlist_path=root / "watchlist.json",
                daily_data_dir=root / "candles",
                reports_dir=root / "reports",
            )

            self.assertTrue(service.strategies()["strategies"])
            self.assertEqual(service.strategy_info("bullish_breakout")["strategy"]["strategy_id"], "bullish_breakout")
            payload = service.backtest_strategy(
                "bullish_breakout",
                symbols=["TEST"],
                days=5000,
                backtest_params={"holding_bars": 2},
            )

            self.assertEqual(payload["analyzed_symbols"], 1)
            self.assertIn("metrics", payload)
            json.dumps(payload, default=str)

    def test_web_strategy_endpoints(self) -> None:
        class FakeService:
            def strategies(self):
                return {"strategies": [{"strategy_id": "bullish_breakout"}]}

            def strategy_info(self, strategy_id):
                return {"strategy": {"strategy_id": strategy_id}}

            def backtest_strategy(self, **kwargs):
                return {"strategy_id": kwargs["strategy_id"], "analyzed_symbols": 0, "signals": [], "trades": [], "metrics": {}, "errors": []}

        original_service = TradingRequestHandler.service
        TradingRequestHandler.service = FakeService()
        server = ReusableThreadingHTTPServer(("127.0.0.1", 0), TradingRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            self.assertIn("bullish_breakout", _http_json(f"{base}/api/strategies")["strategies"][0]["strategy_id"])
            self.assertEqual(_http_json(f"{base}/api/strategy-info?strategy=bullish_breakout")["strategy"]["strategy_id"], "bullish_breakout")
            payload = _http_json(
                f"{base}/api/backtest-strategy",
                {"strategy_id": "bullish_breakout", "backtest_params": {"holding_bars": 2}},
            )
            self.assertEqual(payload["strategy_id"], "bullish_breakout")
        finally:
            server.shutdown()
            server.server_close()
            TradingRequestHandler.service = original_service

    def test_cli_backtest_strategy_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "backtest.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "trading_analysis.cli",
                    "backtest-strategy",
                    "--strategy",
                    "bullish_breakout",
                    "--timeframe",
                    "day",
                    "--days",
                    "730",
                    "--limit-symbols",
                    "1",
                    "--output-json",
                    str(output),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["strategy_id"], "bullish_breakout")


if __name__ == "__main__":
    unittest.main()


def _write_watchlist(path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"symbols": [{"symbol": symbol, "data_file": f"{symbol}.csv"} for symbol in symbols]}),
        encoding="utf-8",
    )


def _write_candles(path: Path, candles: list[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["date,open,high,low,close,volume"]
    for item in candles:
        lines.append(
            f"{item.timestamp.date().isoformat()},{item.open},{item.high},{item.low},{item.close},{item.volume}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _http_json(url: str, payload: dict | None = None) -> dict:
    if payload is None:
        with urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))
