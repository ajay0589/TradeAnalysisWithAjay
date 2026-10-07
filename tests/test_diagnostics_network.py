from __future__ import annotations

import json
import os
import ssl
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import URLError
from urllib.request import Request, urlopen

from trading_analysis import diagnostics
from trading_analysis.brokers import zerodha
from trading_analysis.brokers.zerodha import ZerodhaKiteClient, write_candles_csv
from trading_analysis.models import Candle
from trading_analysis.network import https_context
from trading_analysis.notifications.telegram import TelegramNotifier
from trading_analysis.nifty.service import NiftyDeskService
from trading_analysis.scheduler.jobs import NiftyMarketJobs
from trading_analysis.web_app import ReusableThreadingHTTPServer, TradingRequestHandler


class DiagnosticsNetworkTests(unittest.TestCase):
    def test_shared_queue_preserves_waiter_order_and_recovers_from_timeout(self):
        order = []
        def worker(number):
            with zerodha.request_slot():
                order.append(number)

        threads = []
        with zerodha.request_slot():
            for number in range(3):
                thread = threading.Thread(target=worker, args=(number,))
                threads.append(thread)
                thread.start()
                deadline = time.monotonic() + 2
                while len(zerodha._REQUEST_WAITERS) < number + 2 and time.monotonic() < deadline:
                    time.sleep(0.005)
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(order, [0, 1, 2])
        with zerodha.request_slot(), patch.object(zerodha, "_MAX_QUEUE_WAIT_SECONDS", 0):
            with self.assertRaises(TimeoutError):
                with zerodha.request_slot():
                    self.fail("Timed out waiter must not enter")
        self.assertEqual(len(zerodha._REQUEST_WAITERS), 0)

    def test_request_telemetry_separates_wait_and_network_without_credentials(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"ok"
        with patch.object(zerodha, "urlopen", return_value=response), \
             patch.object(zerodha, "_MIN_REQUEST_GAP_SECONDS", 0), \
             patch.object(diagnostics, "record") as logged:
            self.assertEqual(ZerodhaKiteClient("hidden-key", "hidden-access")._get_text("/quote"), "ok")
        detail = logged.call_args.kwargs
        self.assertIn("queue_ms", detail)
        self.assertIn("network_ms", detail)
        self.assertNotIn("hidden", json.dumps(detail))

    def test_tls_failure_is_actionable_and_never_disables_verification(self):
        context = https_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        error = URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED self-signed certificate in chain"))
        with patch("trading_analysis.notifications.telegram.urlopen", side_effect=error) as send:
            result = TelegramNotifier("dummy-token", "dummy-chat").send_message("test")
        self.assertFalse(result["sent"])
        self.assertIn("TRADING_CA_BUNDLE", result["hint"])
        self.assertIs(send.call_args.kwargs["context"], context)

    def test_cache_replace_retries_temporary_windows_lock_without_truncating_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SBIN.csv"
            candle = Candle(datetime(2026, 10, 6), 100, 110, 99, 108, 500)
            write_candles_csv(path, [candle])
            original = path.read_text()
            replacement = os.replace
            calls = []
            def locked_once(source, target):
                calls.append(1)
                if len(calls) == 1:
                    self.assertEqual(path.read_text(), original)
                    raise PermissionError("in use")
                return replacement(source, target)
            with patch.object(zerodha.os, "replace", side_effect=locked_once), patch.object(zerodha.time, "sleep"):
                write_candles_csv(path, [candle])
            self.assertEqual(len(calls), 2)
            self.assertEqual(path.read_text(), original)
            self.assertFalse(list(Path(tmp).glob("*.tmp")))

    def test_iv_job_uses_cached_context_and_still_records_observation(self):
        service = MagicMock()
        service.nifty_context.return_value = {"iv": {"atm_iv": 18}, "options": {"atm_iv": 18}}
        repositories = {name: MagicMock() for name in ("job_repository", "alert_repository", "context_repository",
            "candle_repository", "option_repository", "iv_repository", "trade_repository")}
        with tempfile.TemporaryDirectory() as tmp:
            repositories["alert_repository"].db_path = Path(tmp) / "iv-job.db"
            jobs = NiftyMarketJobs(nifty_service=service, notifier=TelegramNotifier(), **repositories)
            with patch("trading_analysis.scheduler.jobs.record_nifty_iv_snapshot") as saved:
                jobs._record_nifty_iv()
        self.assertFalse(service.nifty_context.call_args.kwargs["refresh"])
        saved.assert_called_once()
        repositories["iv_repository"].record_observation.assert_called_once()

    def test_nifty_refresh_uses_short_windows_when_cache_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis = MagicMock()
            analysis.refresh_candles.return_value = []
            service = NiftyDeskService(candle_root=tmp, analysis_service=analysis)
            for frame in ("day", "60minute", "15minute"):
                from trading_analysis.candles import candle_path
                file = candle_path(tmp, frame, "NIFTY_50")
                write_candles_csv(file, [Candle(datetime.now(), 100, 110, 99, 108, 500)])
            service._refresh_latest_candles("15minute", 45, None, [], due_only=True)
            windows = {call.args[1]: call.args[2].days for call in analysis.refresh_candles.call_args_list}
            self.assertEqual(windows, {"day": 8, "60minute": 4, "15minute": 3})
            analysis.refresh_candles.reset_mock()
            service._refresh_latest_candles("15minute", 45, None, [])
            windows = {call.args[1]: call.args[2].days for call in analysis.refresh_candles.call_args_list}
            self.assertEqual(windows, {"day": 365, "60minute": 90, "15minute": 45})

    def test_refresh_backfills_gap_after_extended_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis = MagicMock()
            analysis.refresh_candles.return_value = []
            service = NiftyDeskService(candle_root=tmp, analysis_service=analysis)
            from trading_analysis.candles import candle_path
            for frame in ("day", "60minute", "15minute"):
                write_candles_csv(candle_path(tmp, frame, "NIFTY_50"),
                                  [Candle(datetime(2026, 9, 1), 100, 110, 99, 108, 500)])
            with patch("trading_analysis.nifty.service.datetime") as clock:
                clock.now.return_value = datetime(2026, 10, 6, 10)
                service._refresh_latest_candles("15minute", 45, None, [], due_only=True)
            self.assertTrue(all(call.args[2].days == 37 for call in analysis.refresh_candles.call_args_list))

    def test_empty_candle_response_does_not_suppress_next_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis = MagicMock()
            analysis.refresh_candles.return_value = [{"candles": 0}]
            service = NiftyDeskService(candle_root=tmp, analysis_service=analysis)
            from trading_analysis.candles import candle_path
            for frame in ("day", "60minute", "15minute"):
                write_candles_csv(candle_path(tmp, frame, "NIFTY_50"),
                                  [Candle(datetime(2026, 10, 6), 100, 110, 99, 108, 500)])
            with patch("trading_analysis.nifty.service.datetime") as clock:
                clock.now.return_value = datetime(2026, 10, 6, 10)
                service._refresh_latest_candles("15minute", 45, None, [], due_only=True)
                service._refresh_latest_candles("15minute", 45, None, [], due_only=True)
            self.assertEqual(analysis.refresh_candles.call_count, 6)
            self.assertEqual(service._refresh_buckets, {})

    def test_export_redacts_secrets_and_preserves_selected_date(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(diagnostics, "ROOT", Path(tmp)), \
             patch.dict(os.environ, {"ZERODHA_ACCESS_TOKEN": "secret-value"}):
            diagnostics.record("test", area="analyze", error="token=secret-value", details={"api_key": "key123"})
            result = diagnostics.export()
            self.assertEqual(len(result["events"]), 1)
            serialized = json.dumps(result)
            self.assertNotIn("secret-value", serialized)
            self.assertNotIn("key123", serialized)
            self.assertEqual(result["events"][0]["area"], "analyze")

    def test_diagnostic_failure_does_not_interrupt_scanner(self):
        with patch.object(diagnostics, "clean", side_effect=ValueError("serialization failed")):
            diagnostics.record("test")
        self.assertEqual(diagnostics.runtime()["logging_error"], "serialization failed")
        diagnostics.record("recovered")
        self.assertIsNone(diagnostics.runtime()["logging_error"])

    def test_background_refresh_reuses_candles_and_pulls_at_close_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis = MagicMock()
            analysis.refresh_candles.return_value = [{"candles": 1}]
            service = NiftyDeskService(candle_root=tmp, analysis_service=analysis)
            from trading_analysis.candles import candle_path
            for frame in ("day", "60minute", "15minute"):
                file = candle_path(tmp, frame, "NIFTY_50")
                file.parent.mkdir(parents=True, exist_ok=True)
                file.touch()
            from trading_analysis.live_timing import expected_closed_bar
            with patch("trading_analysis.nifty.service.datetime") as clock, \
                 patch("trading_analysis.nifty.service.load_candles") as load:
                load.side_effect = lambda path: [Candle(expected_closed_bar(
                    path.parent.name if path.parent.name in {"15minute", "60minute"} else "day",
                    clock.now.return_value), 100, 102, 99, 101, 100)]
                for minute in range(60):
                    clock.now.return_value = datetime(2026, 10, 6, 9, 15) + timedelta(minutes=minute)
                    service._refresh_latest_candles("15minute", 45, None, [], due_only=True)
                self.assertEqual(analysis.refresh_candles.call_count, 6)
                clock.now.return_value = datetime(2026, 10, 6, 10, 15)
                service._refresh_latest_candles("15minute", 45, None, [], due_only=True)
                self.assertEqual(analysis.refresh_candles.call_count, 8)
                clock.now.return_value = datetime(2026, 10, 6, 15, 30)
                service._refresh_latest_candles("15minute", 45, None, [], due_only=True)
                self.assertEqual(analysis.refresh_candles.call_count, 11)
                service._refresh_latest_candles("15minute", 45, None, [], due_only=False)
                self.assertEqual(analysis.refresh_candles.call_count, 14)

    def test_api_audits_analyze_latest_symbol_and_other_sections(self):
        class FakeAnalysis:
            def analyze_symbol(self, symbol, **kwargs):
                return {"symbol": symbol, "decision": {"symbol": symbol, "score": 70}}
            def scan(self, *args, **kwargs):
                return {"status": "completed", "total": 2}
            def sector_map_status(self):
                return {"status": "ready"}
            def purple_monitor_status(self):
                return {"status": "stopped"}
            def export_report(self, payload):
                return {"status": "saved"}

        with tempfile.TemporaryDirectory() as tmp, patch.object(diagnostics, "ROOT", Path(tmp)), \
             patch.object(TradingRequestHandler, "service", FakeAnalysis()):
            server = ReusableThreadingHTTPServer(("127.0.0.1", 0), TradingRequestHandler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                for symbol in ("NIFTY", "SBIN", "SENSEX"):
                    with urlopen(f"{base}/api/analyze?symbol={symbol}") as response:
                        self.assertEqual(json.load(response)["symbol"], symbol)
                for route in ("/api/scan", "/api/sector-map/status", "/api/krishna-purple-monitor/status"):
                    with urlopen(base + route) as response:
                        self.assertEqual(response.status, 200)
                request = Request(base + "/api/export-report", data=b'{}', headers={"Content-Type": "application/json"})
                with urlopen(request) as response:
                    self.assertEqual(response.status, 200)
                # Request audit is written after the response is sent.
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    events = diagnostics.export()["events"]
                    if len([row for row in events if row["event"] == "request_finished"]) >= 7:
                        break
                    time.sleep(0.01)
                areas = {row["area"] for row in events}
                self.assertTrue({"analyze", "scans", "data", "purple", "reports"}.issubset(areas))
            finally:
                server.shutdown()
                server.server_close()
