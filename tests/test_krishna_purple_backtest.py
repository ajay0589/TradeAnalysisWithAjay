from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from trading_analysis.analysis.krishna_purple_backtest import (
    PurpleTouchBacktestConfig,
    _backtest_symbol_profile,
)
from trading_analysis.models import Candle
from trading_analysis.storage import KrishnaPurpleAlertRepository
from trading_analysis.web_app import ReusableThreadingHTTPServer, TradingRequestHandler


IST = ZoneInfo("Asia/Kolkata")


def _daily_history() -> list[Candle]:
    start = datetime(2026, 3, 20, tzinfo=IST)
    return [
        Candle(start + timedelta(days=index), 100, 101, 99, 100, 1000)
        for index in range(108)
    ]


def _intraday(start: datetime, minutes: int, count: int) -> list[Candle]:
    return [
        Candle(start + timedelta(minutes=minutes * index), 100, 101, 99, 100, 1000)
        for index in range(count)
    ]


def _match(early_status: str = "entry_candidate", final_status: str = "entry_candidate") -> SimpleNamespace:
    entry = {
        "status": "entry_candidate",
        "close": 100,
        "yellow_line": 99,
        "invalidation_level": 95,
        "reasons": ["Entry timeframe candle closed above yellow."],
        "warnings": [],
    }
    return SimpleNamespace(
        score=85,
        confidence="high",
        purple_range_distance_percent=0.0,
        reasons=["Purple EMA9 setup qualified."],
        warnings=[],
        early_entry={**entry, "timeframe": "10minute", "status": early_status},
        final_entry={**entry, "timeframe": "30minute", "status": final_status},
    )


def _config(**overrides) -> PurpleTouchBacktestConfig:
    values = {
        "from_date": "2026-07-06",
        "to_date": "2026-07-06",
        "profiles": ["day"],
        "entry_mode": "both",
        "stop_mode": "none",
        "target_r_multiple": 0,
        "max_holding_bars": 1,
        "slippage_bps": 0,
        "costs_bps": 0,
    }
    values.update(overrides)
    return PurpleTouchBacktestConfig.from_mapping(values)


def _run_profile(ten_start: datetime, ten_count: int = 8) -> dict:
    candles = {
        "day": _daily_history(),
        "10minute": _intraday(ten_start, 10, ten_count),
        "30minute": _intraday(datetime(2026, 7, 6, 9, 15, tzinfo=IST), 30, 5),
    }
    with (
        patch(
            "trading_analysis.analysis.krishna_purple_backtest.scan_krishna_purple_touch_setup",
            return_value=_match(),
        ),
        patch(
            "trading_analysis.analysis.krishna_purple_backtest.scan_krishna_purple_exit_status",
            return_value={"status": "open"},
        ),
    ):
        return _backtest_symbol_profile("ABC", candles, "day", _config())


class KrishnaPurpleBacktestTests(unittest.TestCase):
    def test_live_repository_allows_early_then_final_per_profile(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = KrishnaPurpleAlertRepository(Path(tmp) / "purple.db")
            match = {
                "symbol": "ABC",
                "purple_timeframe": "week",
                "score": 85,
                "confidence": "high",
                "profile": {"exit_timeframe": "30minute"},
                "reasons": [],
                "warnings": [],
            }
            early = repo.open_entry_alert(match, _entry_snapshot("30minute"), "early")
            final = repo.open_entry_alert(match, _entry_snapshot("120minute"), "final")

            self.assertTrue(early["created"])
            self.assertTrue(final["created"])
            self.assertEqual(repo.count_trades(status="open", symbol="ABC"), 2)

    def test_live_repository_final_suppresses_later_early_same_profile(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = KrishnaPurpleAlertRepository(Path(tmp) / "purple.db")
            match = {
                "symbol": "ABC",
                "purple_timeframe": "week",
                "score": 85,
                "confidence": "high",
                "profile": {"exit_timeframe": "30minute"},
                "reasons": [],
                "warnings": [],
            }
            final = repo.open_entry_alert(match, _entry_snapshot("120minute"), "final")
            repo.close_trade_alert(
                final["trade"],
                {"status": "exit_triggered", "close": 102, "yellow_line": 104, "reasons": [], "warnings": []},
            )
            early = repo.open_entry_alert(match, _entry_snapshot("30minute"), "early")

            self.assertTrue(final["created"])
            self.assertFalse(early["created"])
            self.assertTrue(early["suppressed"])
            self.assertEqual(early["reason"], "final_entry_already_recorded_for_setup")
            self.assertEqual(repo.count_trades(status="open", symbol="ABC"), 0)

    def test_backtest_can_progress_from_early_to_final(self) -> None:
        result = _run_profile(datetime(2026, 7, 6, 9, 15, tzinfo=IST))

        self.assertEqual([row["entry_kind"] for row in result["trades"]], ["early", "final"])
        self.assertEqual(result["trades"][0]["signal_time"], "2026-07-06T09:25:00+05:30")
        self.assertEqual(result["trades"][0]["entry_time"], "2026-07-06T09:25:00+05:30")

    def test_backtest_final_first_suppresses_later_early(self) -> None:
        candles = {
            "day": _daily_history(),
            "10minute": _intraday(datetime(2026, 7, 6, 9, 15, tzinfo=IST), 10, 10),
            "30minute": _intraday(datetime(2026, 7, 6, 9, 15, tzinfo=IST), 30, 5),
        }

        def delayed_early(*_args, **kwargs):
            early_rows = kwargs["early_candles"]
            final_rows = kwargs["final_candles"]
            early_status = "entry_candidate" if len(early_rows) >= 6 else "wait"
            final_status = "entry_candidate" if final_rows else "wait"
            return _match(early_status, final_status)

        with (
            patch(
                "trading_analysis.analysis.krishna_purple_backtest.scan_krishna_purple_touch_setup",
                side_effect=delayed_early,
            ),
            patch(
                "trading_analysis.analysis.krishna_purple_backtest.scan_krishna_purple_exit_status",
                return_value={"status": "open"},
            ),
        ):
            result = _backtest_symbol_profile("ABC", candles, "day", _config())

        self.assertEqual([row["entry_kind"] for row in result["trades"]], ["final"])
        self.assertGreater(result["suppressed_entries"], 0)

    def test_backtest_scanner_receives_only_closed_candles(self) -> None:
        seen: list[datetime] = []
        candles = {
            "day": _daily_history(),
            "10minute": _intraday(datetime(2026, 7, 6, 9, 15, tzinfo=IST), 10, 4),
            "30minute": _intraday(datetime(2026, 7, 6, 9, 15, tzinfo=IST), 30, 3),
        }

        def capture(*_args, **kwargs):
            early = kwargs["early_candles"]
            seen.append(early[-1].timestamp if early else datetime.min.replace(tzinfo=IST))
            return _match()

        with (
            patch(
                "trading_analysis.analysis.krishna_purple_backtest.scan_krishna_purple_touch_setup",
                side_effect=capture,
            ),
            patch(
                "trading_analysis.analysis.krishna_purple_backtest.scan_krishna_purple_exit_status",
                return_value={"status": "open"},
            ),
        ):
            result = _backtest_symbol_profile("ABC", candles, "day", _config(entry_mode="early"))

        self.assertTrue(result["signals"])
        self.assertEqual(seen[0], datetime(2026, 7, 6, 9, 15, tzinfo=IST))
        self.assertLess(seen[0], datetime.fromisoformat(result["signals"][0]["signal_time"]))

    def test_backtest_config_and_result_are_json_serializable(self) -> None:
        result = _run_profile(datetime(2026, 7, 6, 9, 15, tzinfo=IST))

        json.dumps(result)
        with self.assertRaisesRegex(ValueError, "between 0 and 3"):
            _config(touch_tolerance_percent=4)

    def test_purple_backtest_api_returns_service_payload(self) -> None:
        class FakeService:
            def backtest_krishna_purple_touch(self, **kwargs):
                return {
                    "type": "krishna_purple_touch_backtest",
                    "symbols": kwargs["symbols"],
                    "config": kwargs["params"],
                    "trade_count": 0,
                    "trades": [],
                }

        original = TradingRequestHandler.service
        TradingRequestHandler.service = FakeService()
        server = ReusableThreadingHTTPServer(("127.0.0.1", 0), TradingRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/krishna-purple-touch-backtest",
                data=json.dumps({
                    "symbols": ["ABC"],
                    "params": {"from_date": "2026-07-01", "to_date": "2026-07-31"},
                    "limit_symbols": 1,
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                payload = json.loads(response.read().decode("utf-8"))

            self.assertEqual(payload["type"], "krishna_purple_touch_backtest")
            self.assertEqual(payload["symbols"], ["ABC"])
        finally:
            server.shutdown()
            server.server_close()
            TradingRequestHandler.service = original


def _entry_snapshot(timeframe: str) -> dict:
    return {
        "timeframe": timeframe,
        "status": "entry_candidate",
        "close": 110,
        "yellow_line": 105,
        "touch_timestamp": "2026-07-01T00:00:00+05:30",
        "reasons": [],
        "warnings": [],
    }


if __name__ == "__main__":
    unittest.main()
