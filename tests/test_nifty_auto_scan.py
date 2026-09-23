from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from trading_analysis.models import Candle
from trading_analysis.nifty.alert_backtest import backtest_nifty_alert_signals
from trading_analysis.nifty.iv_context import build_nifty_iv_context
from trading_analysis.nifty.auto_scan_service import NiftyAutoScanService
from trading_analysis.nifty.live_scanner import backtest_nifty_live_rules, build_live_entry_signal, evaluate_live_exit
from trading_analysis.scheduler.alerts import generate_nifty_alerts
from trading_analysis.scheduler.jobs import NiftyMarketJobs
from trading_analysis.scheduler.market_hours import is_market_hours
from trading_analysis.scheduler.runner import MarketScanScheduler
from trading_analysis.storage import (
    KrishnaPurpleAlertRepository,
    MarketJobRepository,
    NiftyAlertOutcomeRepository,
    NiftyAlertRepository,
    NiftyCandleRepository,
    NiftyContextRepository,
    NiftyIVObservationRepository,
    NiftyOptionChainRepository,
    NiftyTradeRepository,
)
from trading_analysis.web_app import ReusableThreadingHTTPServer, TradingRequestHandler
from trading_analysis.web_services import AnalysisService


class NiftyAutoScanTests(unittest.TestCase):
    def test_market_hours_detection_for_weekday_open(self) -> None:
        now = datetime(2026, 7, 6, 9, 20, tzinfo=ZoneInfo("Asia/Kolkata"))

        self.assertTrue(is_market_hours(now))

    def test_market_hours_false_after_close(self) -> None:
        now = datetime(2026, 7, 6, 15, 31, tzinfo=ZoneInfo("Asia/Kolkata"))

        self.assertFalse(is_market_hours(now))

    def test_market_hours_false_on_weekend(self) -> None:
        now = datetime(2026, 7, 4, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

        self.assertFalse(is_market_hours(now))

    def test_alert_generated_for_high_score_bullish_candidate(self) -> None:
        alerts = generate_nifty_alerts(_context("bullish"), [_candidate("nifty_bull_call_spread", "bullish", 85)], min_score=70)

        self.assertTrue(alerts)
        self.assertEqual(alerts[0]["direction"], "bullish")
        self.assertEqual(alerts[0]["horizon"], "swing")
        self.assertEqual(alerts[0]["alert_type"], "strategy_candidate")

    def test_duplicate_alert_suppressed_within_15_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = NiftyAlertRepository(Path(tmp) / "auto.db")
            repo.create_alert(**_alert(score=80, severity="watch"))

            suppressed = repo.suppress_duplicate(
                "strategy_candidate",
                "nifty_bull_call_spread",
                "bullish",
                min_minutes=15,
                score=85,
                severity="watch",
            )

            self.assertTrue(suppressed)

    def test_duplicate_alert_allowed_when_score_improves_materially(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = NiftyAlertRepository(Path(tmp) / "auto.db")
            repo.create_alert(**_alert(score=70, severity="watch"))

            suppressed = repo.suppress_duplicate(
                "strategy_candidate",
                "nifty_bull_call_spread",
                "bullish",
                min_minutes=15,
                score=82,
                severity="watch",
            )

            self.assertFalse(suppressed)

    def test_duplicate_alert_suppressed_after_acknowledgement_within_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = NiftyAlertRepository(Path(tmp) / "auto.db")
            alert = repo.create_alert(**_alert(score=80, severity="watch"))
            repo.acknowledge_alert(alert["id"])

            suppressed = repo.suppress_duplicate(
                "strategy_candidate",
                "nifty_bull_call_spread",
                "bullish",
                min_minutes=15,
                score=85,
                severity="watch",
            )

            self.assertTrue(suppressed)

    def test_market_job_start_finish_fail_stored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = MarketJobRepository(Path(tmp) / "jobs.db")
            job_id = repo.start_job("context", {"mode": "auto"})
            finished = repo.finish_job(job_id, {"ok": True})
            failed_id = repo.start_job("scan")
            failed = repo.fail_job(failed_id, "boom")

            self.assertEqual(finished["status"], "completed")
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(len(repo.latest_jobs()), 2)
            self.assertIn("+05:30", finished["started_at"])

    def test_krishna_purple_alert_trade_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = KrishnaPurpleAlertRepository(Path(tmp) / "purple.db")
            match = {
                "symbol": "ABC",
                "purple_timeframe": "week",
                "score": 82,
                "confidence": "high",
                "profile": {"exit_timeframe": "30minute"},
                "exit_rule": "Exit if 30-minute candle closes below yellow.",
                "reasons": ["Close is above black EMA89."],
                "warnings": [],
            }
            entry = {
                "timeframe": "30minute",
                "status": "entry_candidate",
                "close": 110,
                "yellow_line": 105,
                "reasons": ["30-minute candle closed above yellow."],
                "warnings": [],
            }

            opened = repo.open_entry_alert(match, entry, "early")
            duplicate = repo.open_entry_alert(match, entry, "early")
            closed = repo.close_trade_alert(
                opened["trade"],
                {"timeframe": "30minute", "status": "exit_triggered", "close": 102, "yellow_line": 104},
            )

            self.assertTrue(opened["created"])
            self.assertFalse(duplicate["created"])
            self.assertEqual(opened["trade"]["trade_id"], duplicate["trade"]["trade_id"])
            self.assertEqual(closed["trade"]["status"], "closed")
            self.assertEqual(repo.list_open_trades(), [])
            self.assertEqual([alert["alert_type"] for alert in repo.list_recent_alerts(limit=5)], ["exit", "entry"])
            self.assertEqual(
                repo.counts(),
                {
                    "entry_alerts": 1,
                    "exit_alerts": 1,
                    "open_trades": 0,
                    "closed_trades": 1,
                    "entry_tally_matches": True,
                    "exit_tally_matches": True,
                },
            )

    def test_krishna_purple_alert_history_supports_filtered_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = KrishnaPurpleAlertRepository(Path(tmp) / "purple-pages.db")
            for index in range(30):
                profile = "week" if index % 2 == 0 else "month"
                entry_kind = "early" if index % 3 else "final"
                repo.open_entry_alert(
                    {
                        "symbol": f"SYM{index:02d}",
                        "purple_timeframe": profile,
                        "score": 75 + (index % 10),
                        "confidence": "high",
                        "profile": {"exit_timeframe": "30minute"},
                        "reasons": ["Synthetic pagination setup."],
                        "warnings": [],
                    },
                    {
                        "timeframe": "30minute",
                        "status": "entry_candidate",
                        "close": 100 + index,
                        "yellow_line": 99 + index,
                        "reasons": ["Synthetic confirmation."],
                        "warnings": [],
                    },
                    entry_kind,
                )

            weekly_total = repo.count_alerts("entry", "week", None)
            weekly_page_2 = repo.list_recent_alerts(
                limit=5,
                offset=5,
                alert_type="entry",
                purple_timeframe="week",
            )
            final_open_total = repo.count_open_trades(None, "final")
            final_open_page = repo.list_open_trades(limit=4, offset=4, entry_kind="final")

            self.assertEqual(weekly_total, 15)
            self.assertEqual(len(weekly_page_2), 5)
            self.assertTrue(all(row["purple_timeframe"] == "week" for row in weekly_page_2))
            self.assertEqual(final_open_total, 10)
            self.assertEqual(len(final_open_page), 4)
            self.assertTrue(all(row["entry_kind"] == "final" for row in final_open_page))

    def test_krishna_purple_trade_lifecycle_supports_status_date_symbol_and_sorting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = KrishnaPurpleAlertRepository(Path(tmp) / "purple-lifecycle.db")
            abc = repo.open_entry_alert(
                {
                    "symbol": "ABC",
                    "purple_timeframe": "week",
                    "score": 85,
                    "confidence": "high",
                    "profile": {"exit_timeframe": "30minute"},
                    "reasons": ["Synthetic lifecycle setup."],
                    "warnings": [],
                },
                {
                    "timeframe": "30minute",
                    "status": "entry_candidate",
                    "close": 110,
                    "yellow_line": 105,
                    "reasons": ["Synthetic entry confirmation."],
                    "warnings": [],
                },
                "early",
            )
            repo.open_entry_alert(
                {
                    "symbol": "XYZ",
                    "purple_timeframe": "month",
                    "score": 75,
                    "confidence": "medium",
                    "profile": {"exit_timeframe": "120minute"},
                    "reasons": ["Synthetic lifecycle setup."],
                    "warnings": [],
                },
                {
                    "timeframe": "day",
                    "status": "entry_candidate",
                    "close": 210,
                    "yellow_line": 205,
                    "reasons": ["Synthetic entry confirmation."],
                    "warnings": [],
                },
                "final",
            )
            closed = repo.close_trade_alert(
                abc["trade"],
                {"timeframe": "30minute", "status": "exit_triggered", "close": 102, "yellow_line": 104},
            )
            lifecycle_date = abc["trade"]["opened_at"][:10]

            trades = repo.list_trades(sort_by="symbol", sort_direction="asc")
            closed_trades = repo.list_trades(status="closed", symbol="ABC")
            closed_entries = repo.list_recent_alerts(alert_type="entry", trade_status="closed", symbol="ABC")

            self.assertEqual([row["symbol"] for row in trades], ["ABC", "XYZ"])
            self.assertEqual(repo.count_trades(status="open"), 1)
            self.assertEqual(repo.count_trades(status="closed"), 1)
            self.assertEqual(repo.count_trades(from_date=lifecycle_date, to_date=lifecycle_date), 2)
            self.assertEqual(closed_trades[0]["closed_at"], closed["trade"]["closed_at"])
            self.assertEqual(closed_entries[0]["trade_status"], "closed")
            self.assertEqual(closed_entries[0]["trade_opened_at"], abc["trade"]["opened_at"])
            self.assertEqual(closed_entries[0]["trade_closed_at"], closed["trade"]["closed_at"])
            json.dumps({"trades": trades, "entry_alerts": closed_entries})

    def test_context_snapshot_and_candidates_save_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = NiftyContextRepository(Path(tmp) / "context.db")
            context_id = repo.save_context_result(_context("bullish"))
            candidate_ids = repo.save_strategy_candidates(context_id, [_candidate("nifty_bull_call_spread", "bullish", 85)])
            snapshot = repo.load_context_snapshot(context_id)

            self.assertEqual(snapshot["id"], context_id)
            self.assertEqual(len(candidate_ids), 1)
            self.assertEqual(snapshot["candidates"][0]["strategy_id"], "nifty_bull_call_spread")
            self.assertIn("+05:30", snapshot["captured_at"])

    def test_scheduler_run_once_executes_jobs_in_order(self) -> None:
        fake = FakeJobs()
        scheduler = MarketScanScheduler(
            job_runner=fake,
            clock=lambda: datetime(2026, 7, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        )

        result = scheduler.run_once()

        self.assertTrue(result["ran"])
        self.assertEqual(fake.calls, ["candles", "option_chain", "iv", "exit", "scan", "cleanup"])

    def test_api_status_alerts_and_acknowledge_return_json(self) -> None:
        class FakeAutoService:
            def status(self):
                return {"running": False, "market_hours": True, "recent_jobs": [], "recent_alerts": [], "active_alerts_count": 1}

            def recent_alerts(self, limit=50, active_only=False):
                return {"alerts": [{"id": 7, "title": "Test", "is_active": True}], "count": 1, "active_count": 1}

            def acknowledge_alert(self, alert_id):
                return {"alert": {"id": alert_id, "is_active": False}}

            def alert_backtest(self, **kwargs):
                return {"timeframe": kwargs["timeframe"], "saved_outcomes": 0, "metrics": {"overall": {"signals": 0}}, "rows": []}

            def context_snapshots(self, limit=50):
                return {"snapshots": [{"id": 9, "mode": "auto"}], "count": 1}

            def context_snapshot(self, context_snapshot_id):
                return {"snapshot": {"id": context_snapshot_id, "summary": {"points": []}}}

            def alert_outcomes(self, alert_id):
                return {"alert_id": alert_id, "outcomes": [{"alert_id": alert_id}], "count": 1}

            def latest_data(self):
                return {
                    "latest_candles": {"15minute": "2026-07-06T09:30:00"},
                    "latest_option_snapshot": {"id": 3},
                    "latest_iv_observation": {"id": 4},
                    "counts": {"candles": {"15minute": 2}, "context_snapshots": 1, "alerts": 1},
                }

            def option_snapshots(self, limit=20, expiry=None):
                return {"snapshots": [{"id": 3, "expiry": expiry}], "count": 1}

            def option_snapshot(self, snapshot_id):
                return {"snapshot": {"id": snapshot_id}, "rows": [{"strike": 24500}], "row_count": 1}

            def iv_history(self, lookback_days=252):
                return {"observations": [{"atm_iv": 15}], "count": 1, "lookback_days": lookback_days}

            def trades(self, status=None, limit=200):
                return {"trades": [], "count": 0, "counts": {"open": 0, "closed": 0, "total": 0}}

            def scanner_backtest(self, **kwargs):
                return {"horizon": kwargs["horizon"], "trade_count": 0, "trades": [], "metrics": {}}

            def start(self, scan_interval_seconds=None):
                return self.status()

            def stop(self):
                return self.status()

            def run_once(self, force=False):
                return {"ran": True, "force": force, **self.status()}

        original = TradingRequestHandler.nifty_auto_service
        TradingRequestHandler.nifty_auto_service = FakeAutoService()
        server = ReusableThreadingHTTPServer(("127.0.0.1", 0), TradingRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            self.assertFalse(_http_json(f"{base}/api/nifty/auto/status")["running"])
            self.assertEqual(_http_json(f"{base}/api/nifty/alerts")["count"], 1)
            self.assertEqual(_http_json(f"{base}/api/nifty/alerts/backtest?timeframe=15minute")["timeframe"], "15minute")
            self.assertEqual(_http_json(f"{base}/api/nifty/context-snapshots")["count"], 1)
            self.assertEqual(_http_json(f"{base}/api/nifty/context-snapshots/9")["snapshot"]["id"], 9)
            self.assertEqual(_http_json(f"{base}/api/nifty/alerts/7/outcomes")["count"], 1)
            self.assertEqual(_http_json(f"{base}/api/nifty/data/latest")["latest_option_snapshot"]["id"], 3)
            self.assertEqual(_http_json(f"{base}/api/nifty/option-snapshots")["count"], 1)
            self.assertEqual(_http_json(f"{base}/api/nifty/option-snapshots/3")["row_count"], 1)
            self.assertEqual(_http_json(f"{base}/api/nifty/iv-history?lookback_days=30")["lookback_days"], 30)
            self.assertEqual(_http_json(f"{base}/api/nifty/trades")["count"], 0)
            self.assertFalse(_http_json(f"{base}/api/nifty/alerts/7/ack", {})["alert"]["is_active"])
            self.assertTrue(_http_json(f"{base}/api/nifty/auto/run-once", {"force": True})["ran"])
            self.assertEqual(_http_json(f"{base}/api/nifty/scanner-backtest", {"horizon": "intraday"})["horizon"], "intraday")
        finally:
            server.shutdown()
            server.server_close()
            TradingRequestHandler.nifty_auto_service = original

    def test_auto_scan_service_recent_alerts_json_serializable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = NiftyAutoScanService(db_path=Path(tmp) / "service.db", scheduler=FakeScheduler())
            service.alert_repository.create_alert(**_alert(score=80, severity="watch"))

            payload = service.recent_alerts()

            json.dumps(payload)
            self.assertEqual(payload["count"], 1)

    def test_alert_signal_backtest_detects_favorable_bullish_move(self) -> None:
        alert = {"id": 1, "created_at": "2026-07-06T09:16:00", "direction": "bullish", "horizon": "intraday", "score": 80}
        candles = _candles([100, 101, 102, 103, 104, 105])

        payload = backtest_nifty_alert_signals([alert], candles, horizons=[3])

        self.assertEqual(payload["metrics"]["overall"]["signals"], 1)
        self.assertEqual(payload["metrics"]["overall"]["successes"], 1)
        self.assertGreater(payload["rows"][0]["directional_return_percent"], 0)

    def test_auto_scan_service_alert_backtest_uses_cached_candles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "candles"
            service = NiftyAutoScanService(
                nifty_service=FakeNiftyDeskService(root),
                db_path=Path(tmp) / "service.db",
                scheduler=FakeScheduler(),
            )
            alert = service.alert_repository.create_alert(**_alert(score=80, severity="watch"))
            start = datetime.fromisoformat(alert["created_at"]).replace(tzinfo=None) + timedelta(minutes=15)
            _write_candle_csv(root / "15minute" / "NIFTY_50.csv", _candles([100, 101, 102, 103, 104, 105], start=start))

            payload = service.alert_backtest(timeframe="15minute", horizons=[3])

            json.dumps(payload)
            self.assertGreaterEqual(payload["metrics"]["overall"]["signals"], 1)
            self.assertEqual(payload["saved_outcomes"], 1)
            self.assertEqual(len(service.alert_outcomes(1)["outcomes"]), 1)

    def test_alert_backtest_outcome_repository_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = NiftyAlertOutcomeRepository(Path(tmp) / "outcomes.db")
            row = {
                "holding_bars": 3,
                "entry_time": "2026-07-06T09:30:00",
                "entry_price": 100,
                "exit_time": "2026-07-06T10:15:00",
                "exit_price": 103,
                "forward_return_percent": 3,
                "directional_return_percent": 3,
                "max_favorable_percent": 3,
                "max_adverse_percent": 0,
                "success": True,
                "status": "evaluated",
            }

            self.assertTrue(repo.save_alert_backtest_result(1, row, timeframe="15minute"))
            self.assertFalse(repo.save_alert_backtest_result(1, row, timeframe="15minute"))
            self.assertEqual(len(repo.load_alert_outcomes(1)), 1)

    def test_public_option_chain_refresh_method_exists(self) -> None:
        self.assertTrue(hasattr(AnalysisService, "refresh_option_chain_snapshot"))

    def test_nifty_candle_repository_upsert_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = NiftyCandleRepository(Path(tmp) / "data.db")
            candles = _candles([100, 101, 102])

            self.assertEqual(repo.upsert_candles("NIFTY", "15minute", candles), 3)
            loaded = repo.load_candles("NIFTY", "15minute")

            self.assertEqual(len(loaded), 3)
            self.assertEqual(loaded[-1].close, 102)
            self.assertEqual(repo.latest_timestamp("NIFTY", "15minute"), candles[-1].timestamp.isoformat(timespec="seconds"))
            self.assertEqual(repo.counts("NIFTY")["15minute"], 3)

    def test_option_chain_snapshot_and_rows_save_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = NiftyOptionChainRepository(Path(tmp) / "options.db")
            analysis = {
                "symbol": "NIFTY",
                "expiry": "2026-07-09",
                "spot": 24510,
                "pcr_oi": 1.15,
                "max_pain": 24500,
                "atm_iv": 14.5,
                "rows": _option_rows(),
            }

            snapshot_id = repo.save_snapshot(analysis, raw_file="data/raw/option_chain/NIFTY_2026-07-09.csv")
            saved_rows = repo.save_rows(snapshot_id, analysis["rows"])
            latest = repo.load_latest_snapshot("NIFTY", "2026-07-09")
            rows = repo.load_snapshot_rows(snapshot_id)

            self.assertGreater(snapshot_id, 0)
            self.assertEqual(saved_rows, 2)
            self.assertEqual(latest["id"], snapshot_id)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["option_type"], "CE")

    def test_nifty_trade_repository_records_entry_exit_and_open_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = NiftyTradeRepository(Path(tmp) / "trades.db")
            signal = {
                "horizon": "intraday",
                "direction": "bullish",
                "strategy_id": "nifty_bull_call_spread",
                "title": "Bull Call Spread",
                "entry_time": "2026-07-06T09:30:00+05:30",
                "entry_price": 100,
                "entry_timeframe": "15minute",
                "entry_candle_timestamp": "2026-07-06T09:15:00+05:30",
                "stop_level": 98,
                "target_level": 104,
                "risk_points": 2,
                "target_r_multiple": 2,
                "score": 82,
                "confidence": "high",
            }

            opened = repo.open_trade(signal)
            duplicate = repo.open_trade(signal)
            closed = repo.close_trade(
                opened["trade"]["trade_id"],
                exit_time="2026-07-06T10:30:00+05:30",
                exit_price=104,
                exit_reason="target_reached",
            )

            self.assertTrue(opened["created"])
            self.assertFalse(duplicate["created"])
            self.assertEqual(closed["open_seconds"], 3600)
            self.assertEqual(closed["open_duration"], "1h 0m")
            self.assertEqual(repo.counts(), {"open": 0, "closed": 1, "total": 1})

    def test_live_entry_requires_closed_candle_and_option_alignment(self) -> None:
        candles = _candles([100 + index for index in range(60)])
        context = _context("bullish")
        context["summary"] = {"data_links": {"latest_option_snapshot_at": "2026-07-07T00:10:00+05:30"}}
        signal = build_live_entry_signal(
            context,
            [_candidate("nifty_bull_call_spread", "bullish", 85)],
            "intraday",
            candles,
            now=datetime(2026, 7, 7, 0, 15),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["horizon"], "intraday")
        self.assertGreater(signal["target_level"], signal["entry_price"])
        context["options"]["option_bias"] = "bearish"
        self.assertIsNone(build_live_entry_signal(context, [_candidate("nifty_bull_call_spread", "bullish", 85)], "intraday", candles, now=datetime(2026, 7, 7, 0, 15)))

    def test_nifty_scanner_backtest_returns_entry_exit_and_risk_metrics(self) -> None:
        candles = _candles([100 + index * 0.5 for index in range(140)])

        payload = backtest_nifty_live_rules(candles, "intraday", target_r_multiple=1.5, max_holding_bars=6)

        self.assertGreater(payload["trade_count"], 0)
        self.assertIn("entry_time", payload["trades"][0])
        self.assertIn("exit_time", payload["trades"][0])
        self.assertIn("open_duration", payload["trades"][0])
        self.assertIn("average_r", payload["metrics"])

    def test_iv_observation_repository_save_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = NiftyIVObservationRepository(Path(tmp) / "iv.db")
            observation_id = repo.record_observation(
                expiry="2026-07-09",
                atm_strike=24500,
                atm_iv=15.2,
                captured_at=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds"),
            )

            latest = repo.latest("NIFTY")
            history = repo.load_history("NIFTY", lookback_days=30)

            self.assertGreater(observation_id, 0)
            self.assertEqual(latest["atm_iv"], 15.2)
            self.assertEqual(len(history), 1)

    def test_iv_context_prefers_database_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = NiftyIVObservationRepository(Path(tmp) / "iv_context.db")
            now = datetime.now(ZoneInfo("Asia/Kolkata"))
            for index in range(35):
                repo.record_observation(
                    expiry="2026-07-09",
                    atm_iv=10 + index * 0.2,
                    captured_at=(now - timedelta(days=34 - index)).isoformat(timespec="seconds"),
                )

            context = build_nifty_iv_context(current_atm_iv=17, iv_repository=repo)

            self.assertTrue(context.enough_history)
            self.assertIsNotNone(context.iv_rank)
            self.assertIn("35 observation", context.notes[0])

    def test_auto_scan_job_persists_option_snapshot_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            option_file = root / "NIFTY_2026-07-09.csv"
            _write_option_csv(option_file)
            db_path = root / "jobs.db"
            service = FakeNiftyDeskForJobs(root, option_file)
            jobs = NiftyMarketJobs(
                nifty_service=service,
                job_repository=MarketJobRepository(db_path),
                alert_repository=NiftyAlertRepository(db_path),
                context_repository=NiftyContextRepository(db_path),
                candle_repository=NiftyCandleRepository(db_path),
                option_repository=NiftyOptionChainRepository(db_path),
                iv_repository=NiftyIVObservationRepository(db_path),
            )

            payload = jobs.update_nifty_option_chain_job(refresh=True)

            self.assertGreater(payload["result"]["option_snapshot_id"], 0)
            self.assertEqual(payload["result"]["saved_rows"], 2)

    def test_alert_metadata_links_latest_market_data_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "links.db"
            candle_repo = NiftyCandleRepository(db_path)
            option_repo = NiftyOptionChainRepository(db_path)
            iv_repo = NiftyIVObservationRepository(db_path)
            start = datetime.now() - timedelta(minutes=15 * 60)
            candle_repo.upsert_candles("NIFTY", "15minute", _candles([100 + index for index in range(60)], start=start))
            snapshot_id = option_repo.save_snapshot(
                {"symbol": "NIFTY", "expiry": "2026-07-09", "rows": _option_rows()},
                captured_at=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds"),
            )
            iv_id = iv_repo.record_observation(expiry="2026-07-09", atm_iv=15.5, source_snapshot_id=snapshot_id)
            jobs = NiftyMarketJobs(
                nifty_service=FakeNiftyDeskForJobs(Path(tmp), None),
                job_repository=MarketJobRepository(db_path),
                alert_repository=NiftyAlertRepository(db_path),
                context_repository=NiftyContextRepository(db_path),
                candle_repository=candle_repo,
                option_repository=option_repo,
                iv_repository=iv_repo,
            )

            payload = jobs.run_nifty_opportunity_scan_job(mode="auto", min_score=70)
            alert = payload["result"]["alerts"][0]

            self.assertEqual(alert["metadata"]["latest_option_snapshot_id"], snapshot_id)
            self.assertEqual(alert["metadata"]["latest_iv_observation_id"], iv_id)
            self.assertIsNotNone(alert["metadata"]["latest_candle_timestamp"])


class FakeJobs:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def update_nifty_candles_job(self, refresh=False):
        self.calls.append("candles")
        return {"job": {"status": "completed"}, "result": {"refresh": refresh}}

    def update_nifty_option_chain_job(self, refresh=False):
        self.calls.append("option_chain")
        return {"job": {"status": "completed"}, "result": {"refresh": refresh}}

    def record_nifty_iv_job(self):
        self.calls.append("iv")
        return {"job": {"status": "completed"}, "result": {}}

    def run_nifty_context_job(self, mode="auto"):
        self.calls.append("context")
        return {"job": {"status": "completed"}, "result": {"mode": mode}}

    def run_nifty_opportunity_scan_job(self, mode="auto"):
        self.calls.append("scan")
        return {"job": {"status": "completed"}, "result": {"mode": mode}}

    def run_nifty_exit_scan_job(self):
        self.calls.append("exit")
        return {"job": {"status": "completed"}, "result": {}}

    def cleanup_market_jobs_job(self, days=7):
        self.calls.append("cleanup")
        return {"job": {"status": "completed"}, "result": {"days": days}}


class FakeScheduler:
    def configure(self, intervals):
        return None

    def start(self):
        return {"running": True, "market_hours": True}

    def stop(self):
        return {"running": False, "market_hours": True}

    def status(self):
        return {"running": False, "market_hours": True, "last_results": {}}

    def run_once(self, force=False):
        return {"ran": True, "running": False, "market_hours": True, "force": force}


class FakeNiftyDeskService:
    def __init__(self, candle_root: Path) -> None:
        self.candle_root = candle_root


class FakeAnalysisServiceForJobs:
    def __init__(self, option_file: Path | None) -> None:
        self.option_file = option_file

    def refresh_option_chain_snapshot(self, **kwargs):
        if self.option_file is None:
            return {}
        return {
            "symbol": "NIFTY",
            "expiry": "2026-07-09",
            "latest_snapshot": str(self.option_file),
            "spot": 24510,
            "pcr_oi": 1.1,
            "max_pain": 24500,
            "atm_iv": 15.0,
        }


class FakeNiftyDeskForJobs:
    def __init__(self, root: Path, option_file: Path | None) -> None:
        self.candle_root = root / "candles"
        self.option_chain_dir = root
        self.analysis_service = FakeAnalysisServiceForJobs(option_file)

    def nifty_context(self, **kwargs):
        return _context("bullish")

    def nifty_strategy_suggestions(self, **kwargs):
        context = _context("bullish")
        context["candidates"] = [_candidate("nifty_bull_call_spread", "bullish", 85)]
        return context


def _context(bias: str) -> dict:
    return {
        "mode": "auto",
        "technical": {
            "spot": 24500,
            "bias_intraday": bias,
            "bias_swing": bias,
            "bias_positional": bias,
            "support_levels": [24350],
            "resistance_levels": [24600],
            "warnings": [],
        },
        "options": {
            "spot": 24500,
            "option_bias": bias,
            "selected_weekly_expiry": "2026-07-09",
            "support_by_oi": 24300,
            "resistance_by_oi": 24700,
            "warnings": [],
        },
        "iv": {"iv_regime": "normal", "atm_iv": 15, "iv_rank": 45, "iv_percentile": 50, "enough_history": True},
    }


def _candidate(strategy_id: str, direction: str, score: int) -> dict:
    return {
        "strategy_id": strategy_id,
        "label": strategy_id.replace("_", " ").title(),
        "required_view": direction,
        "horizon": "swing",
        "structure": "directional",
        "suitability_score": score,
        "confidence": "high",
        "expiry_plan": "weekly",
        "reasons": ["Technical and option context align."],
        "risks": ["Requires confirmation."],
    }


def _alert(score: int, severity: str) -> dict:
    return {
        "alert_type": "strategy_candidate",
        "mode": "auto",
        "horizon": "swing",
        "severity": severity,
        "title": "Bullish spread candidate",
        "message": "NIFTY bullish setup candidate; wait for confirmation.",
        "strategy_id": "nifty_bull_call_spread",
        "direction": "bullish",
        "score": score,
        "confidence": "high",
        "reasons": ["Context alignment"],
        "risks": ["Invalidation can change"],
        "event_kind": "entry",
    }


def _http_json(url: str, payload: dict | None = None) -> dict:
    if payload is None:
        with urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _candles(closes: list[float], start: datetime | None = None) -> list[Candle]:
    rows: list[Candle] = []
    start = start or datetime(2026, 7, 6, 9, 15)
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index else close
        rows.append(
            Candle(
                timestamp=start + timedelta(minutes=15 * index),
                open=previous,
                high=max(previous, close) + 1,
                low=min(previous, close) - 1,
                close=close,
                volume=1000,
            )
        )
    return rows


def _write_candle_csv(path: Path, candles: list[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ["date,open,high,low,close,volume,open_interest"]
    for candle in candles:
        rows.append(
            f"{candle.timestamp.isoformat()},{candle.open},{candle.high},{candle.low},{candle.close},{candle.volume},"
        )
    path.write_text("\n".join(rows), encoding="utf-8")


def _option_rows() -> list[dict]:
    return [
        {
            "snapshot_time": "2026-07-06T10:00:00+05:30",
            "strike": "24500",
            "option_type": "CE",
            "tradingsymbol": "NIFTY2670924500CE",
            "last_price": "80",
            "previous_close": "75",
            "price_change": "5",
            "oi": "1000",
            "previous_oi": "900",
            "oi_change": "100",
            "oi_change_percent": "11.11",
            "implied_volatility": "15",
            "iv_change": "0.5",
            "volume": "500",
            "bid_price": "79",
            "ask_price": "81",
            "buildup": "short_build_up",
        },
        {
            "snapshot_time": "2026-07-06T10:00:00+05:30",
            "strike": "24500",
            "option_type": "PE",
            "tradingsymbol": "NIFTY2670924500PE",
            "last_price": "70",
            "previous_close": "72",
            "price_change": "-2",
            "oi": "1200",
            "previous_oi": "1000",
            "oi_change": "200",
            "oi_change_percent": "20",
            "implied_volatility": "15.5",
            "iv_change": "0.3",
            "volume": "650",
            "bid_price": "69",
            "ask_price": "71",
            "buildup": "long_build_up",
        },
    ]


def _write_option_csv(path: Path) -> None:
    rows = _option_rows()
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = list(rows[0].keys())
    lines = [",".join(headers)]
    for row in rows:
        lines.append(",".join(str(row.get(header, "")) for header in headers))
    path.write_text("\n".join(lines), encoding="utf-8")
