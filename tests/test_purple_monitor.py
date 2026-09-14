from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from trading_analysis.analysis.krishna_setup import krishna_purple_profile
from trading_analysis.models import Candle
from trading_analysis.storage.sqlite import KrishnaPurpleAlertRepository
from trading_analysis.web_services import AnalysisService, _aggregate_refresh_freshness, _expected_closed_candle


IST = ZoneInfo("Asia/Kolkata")


def setup_match(close: float = 101.0) -> dict:
    profile = krishna_purple_profile("week")
    return {
        "symbol": "ABC",
        "purple_timeframe": "week",
        "touch_timestamp": "2026-09-14T09:15:00+05:30",
        "purple_ema9": 100.0,
        "touch_price": 100.0,
        "purple_touch_distance_percent": close - 100.0,
        "score": 80,
        "confidence": "high",
        "profile": profile.to_dict(),
        "reasons": [],
        "warnings": [],
    }


def candles(close: float) -> list[Candle]:
    end = datetime.now(IST) - timedelta(hours=4)
    return [
        Candle(
            timestamp=end - timedelta(minutes=30 * (39 - index)),
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=1000,
        )
        for index in range(40)
    ]


def entry_snapshot(kind: str, status: str = "wait", discarded: bool = False) -> dict:
    return {
        "symbol": "ABC",
        "timeframe": "30minute" if kind == "early" else "120minute",
        "entry_kind": kind,
        "status": "discarded" if discarded else status,
        "trigger_date": (datetime.now(IST) - timedelta(hours=3)).isoformat(timespec="seconds"),
        "touch_timestamp": "2026-09-14T09:15:00+05:30",
        "close": 101.0,
        "yellow_line": 100.0,
        "candle1_low": 98.0,
        "candle2_low": 97.0,
        "invalidation_level": 97.0,
        "setup_discarded": discarded,
        "reasons": [],
        "warnings": [],
    }


class PurpleMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = KrishnaPurpleAlertRepository(Path(self.temp_dir.name) / "purple.db")
        self.service = AnalysisService()
        self.repo.upsert_setup(setup_match())
        now = datetime.now(IST).isoformat(timespec="seconds")
        for timeframe in ("30minute", "60minute"):
            self.repo.record_refresh_success("ABC", timeframe, now)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_checker(self, close: float, snapshot_side_effect=None):
        source_candles = candles(close)
        snapshot_side_effect = snapshot_side_effect or (
            lambda _symbol, _candles, _timeframe, kind, **_kwargs: entry_snapshot(kind)
        )
        with (
            patch("trading_analysis.web_services.KrishnaPurpleAlertRepository", return_value=self.repo),
            patch("trading_analysis.web_services.is_market_hours", return_value=True),
            patch.object(
                self.service,
                "_load_optional_timeframe_with_summary",
                return_value=(source_candles, {}),
            ),
            patch("trading_analysis.web_services._purple_entry_snapshot", side_effect=snapshot_side_effect),
        ):
            return self.service.check_krishna_purple_entries(["week"], send_telegram=False)

    def test_setup_upsert_preserves_captured_ema9_and_fixed_limit(self) -> None:
        changed = setup_match()
        changed["purple_ema9"] = 110.0
        result = self.repo.upsert_setup(changed)

        self.assertFalse(result["created"])
        self.assertEqual(result["setup"]["captured_purple_ema9"], 100.0)
        self.assertEqual(result["setup"]["upper_discard_level"], 103.0)
        self.assertEqual(self.repo.count_setups(), 1)

    def test_exactly_three_percent_remains_active(self) -> None:
        result = self.run_checker(103.0)
        setup = self.repo.active_setups(["week"])[0]

        self.assertEqual(result["setups_discarded"], 0)
        self.assertEqual(setup["lifecycle_status"], "active_waiting")
        self.assertAlmostEqual(setup["current_distance_percent"], 3.0)

    def test_above_three_percent_discards_against_captured_ema9(self) -> None:
        result = self.run_checker(103.01)
        setup = self.repo.list_setups(status="discarded_above_3_percent")[0]

        self.assertEqual(result["setups_discarded"], 1)
        self.assertEqual(setup["upper_discard_level"], 103.0)
        self.assertGreater(setup["current_distance_percent"], 3.0)

    def test_c3_low_break_discards_setup(self) -> None:
        result = self.run_checker(
            101.0,
            lambda _symbol, _candles, _timeframe, kind, **_kwargs: entry_snapshot(kind, discarded=True),
        )

        self.assertEqual(result["setups_discarded"], 1)
        self.assertEqual(
            self.repo.list_setups(status="discarded_c1_c2_low_break")[0]["invalidation_level"],
            97.0,
        )

    def test_same_closed_candle_does_not_create_duplicate_entry(self) -> None:
        def snapshots(_symbol, _candles, _timeframe, kind, **_kwargs):
            return entry_snapshot(kind, status="entry_candidate" if kind == "early" else "wait")

        first = self.run_checker(101.0, snapshots)
        second = self.run_checker(101.0, snapshots)

        self.assertEqual(first["entry_alerts_created"], 1)
        self.assertEqual(second["entry_alerts_created"], 0)
        self.assertEqual(self.repo.counts()["entry_alerts"], 1)

    def test_discard_after_early_keeps_early_trade_open(self) -> None:
        def snapshots(_symbol, _candles, _timeframe, kind, **_kwargs):
            return entry_snapshot(kind, status="entry_candidate" if kind == "early" else "wait")

        self.run_checker(101.0, snapshots)
        result = self.run_checker(104.0)

        self.assertEqual(result["setups_discarded"], 1)
        trades = self.repo.list_open_trades(limit=10)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["entry_kind"], "early")

    def test_final_entry_suppresses_early_for_same_setup(self) -> None:
        result = self.run_checker(
            101.0,
            lambda _symbol, _candles, _timeframe, kind, **_kwargs: entry_snapshot(
                kind, status="entry_candidate"
            ),
        )

        self.assertEqual(result["entry_alerts_created"], 1)
        trades = self.repo.list_open_trades(limit=10)
        self.assertEqual([trade["entry_kind"] for trade in trades], ["final"])
        setup = self.repo.list_setups(status="final_entry_triggered")[0]
        self.assertEqual(setup["final_status"], "triggered")

    def test_stale_entry_data_cannot_create_alert(self) -> None:
        stale_repo = KrishnaPurpleAlertRepository(Path(self.temp_dir.name) / "stale.db")
        stale_repo.upsert_setup(setup_match())
        with (
            patch("trading_analysis.web_services.KrishnaPurpleAlertRepository", return_value=stale_repo),
            patch("trading_analysis.web_services.is_market_hours", return_value=True),
        ):
            result = self.service.check_krishna_purple_entries(["week"], send_telegram=False)

        self.assertEqual(result["entry_alerts_created"], 0)
        self.assertGreaterEqual(len(result["stale"]), 1)
        self.assertEqual(stale_repo.counts()["entry_alerts"], 0)

    def test_expected_intraday_candle_is_capped_at_market_close(self) -> None:
        after_close = datetime(2026, 9, 14, 18, 45, tzinfo=IST)

        self.assertEqual(
            _expected_closed_candle("30minute", after_close),
            "2026-09-14T15:15:00+05:30",
        )
        self.assertEqual(
            _expected_closed_candle("60minute", after_close),
            "2026-09-14T15:15:00+05:30",
        )

    def test_untracked_required_timeframe_is_reported_missing(self) -> None:
        freshness = _aggregate_refresh_freshness([])

        self.assertTrue(all(row["missing"] == 1 for row in freshness))

    def test_entry_and_exit_workers_can_analyze_cache_concurrently(self) -> None:
        barrier = threading.Barrier(2)

        def entry_check():
            barrier.wait(timeout=2)
            return {"checked": 1}

        def exit_check():
            barrier.wait(timeout=2)
            return {"checked": 1}

        with (
            patch.object(self.service, "check_krishna_purple_entries", side_effect=entry_check),
            patch.object(self.service, "check_krishna_purple_exits", side_effect=exit_check),
        ):
            entry_thread = threading.Thread(target=self.service._run_purple_entry_checker)
            exit_thread = threading.Thread(target=self.service._run_purple_exit_checker)
            entry_thread.start()
            exit_thread.start()
            entry_thread.join(timeout=3)
            exit_thread.join(timeout=3)

        self.assertFalse(entry_thread.is_alive())
        self.assertFalse(exit_thread.is_alive())
        self.assertEqual(self.service._purple_monitor_state["entry_checks"], 1)
        self.assertEqual(self.service._purple_monitor_state["exit_checks"], 1)


if __name__ == "__main__":
    unittest.main()
