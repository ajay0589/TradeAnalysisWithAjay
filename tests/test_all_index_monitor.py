from __future__ import annotations

import tempfile
import threading
import time
import unittest
import json
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from trading_analysis.all_index_monitor import AllIndexMonitor
from trading_analysis.brokers.zerodha import ZerodhaKiteClient, write_instruments_csv
from trading_analysis.instrument_master_service import InstrumentMasterService
from trading_analysis.web_services import AnalysisService
from trading_analysis.web_app import ReusableThreadingHTTPServer, TradingRequestHandler


def _master_service(root: Path, fetcher=None) -> InstrumentMasterService:
    analysis = AnalysisService(nse_instruments_path=root / "instruments_NSE.csv",
                               nfo_instruments_path=root / "instruments_NFO.csv",
                               bse_instruments_path=root / "instruments_BSE.csv")
    return InstrumentMasterService(analysis, fetcher=fetcher)


class FakeNifty:
    def __init__(self) -> None:
        self.running = False

    def status(self):
        return {"running": self.running, "active_jobs": [], "started_at": None,
                "stopped_at": None, "last_cycle_completed_at": None, "errors": []}

    def start(self, scan_interval_seconds=None):
        self.running = True
        return self.status()

    def stop(self):
        self.running = False
        return self.status()


class FakeIndexes:
    def __init__(self) -> None:
        self.running = {"BANKNIFTY": False, "SENSEX": False}

    def status(self, symbol):
        return {"running": self.running[symbol], "phase": "waiting", "started_at": None,
                "stopped_at": None, "last_cycle_at": None, "telegram_destination": None, "errors": []}

    def start(self, symbol, interval_seconds):
        self.running[symbol] = True
        return self.status(symbol)

    def stop(self, symbol):
        self.running[symbol] = False
        return self.status(symbol)


class AllIndexMonitorTests(unittest.TestCase):
    def test_telegram_check_uses_configured_destination_without_creating_trade(self):
        class FakeNotifier:
            def configured(self):
                return True

            def send_message(self, message):
                self.message = message
                return {"sent": False, "error": "HTTP 400: chat not found"}

        notifier = FakeNotifier()
        with patch("trading_analysis.all_index_monitor.TelegramNotifier.from_env", return_value=notifier) as configured:
            result = AllIndexMonitor.test_telegram("NIFTY")
        configured.assert_called_once_with("NIFTY_")
        self.assertFalse(result["sent"])
        self.assertEqual(result["error"], "HTTP 400: chat not found")
        self.assertIn("TEST MESSAGE (not a trade alert)", notifier.message)

    def test_telegram_check_rejects_unknown_symbol(self):
        with self.assertRaisesRegex(ValueError, "Choose NIFTY"):
            AllIndexMonitor.test_telegram("OTHER")

    def test_telegram_check_uses_nifty_fallback_for_bank_nifty(self):
        class FakeNotifier:
            def __init__(self, configured):
                self.enabled = configured

            def configured(self):
                return self.enabled

            def send_message(self, message):
                self.message = message
                return {"sent": True}

        fallback = FakeNotifier(True)
        with patch("trading_analysis.all_index_monitor.TelegramNotifier.from_env",
                   side_effect=[FakeNotifier(False), fallback]) as configured:
            result = AllIndexMonitor.test_telegram("BANKNIFTY")
        self.assertEqual([call.args[0] for call in configured.call_args_list], ["BANKNIFTY_", "NIFTY_"])
        self.assertEqual(result["destination"], "NIFTY_TELEGRAM_CHAT_ID")
        self.assertIn("BANKNIFTY scanner TEST MESSAGE", fallback.message)

    def test_diagnostics_api_returns_selected_day(self):
        class FakeMonitor:
            def diagnostics(self, day):
                return {"date": day, "NIFTY": {"jobs": []}, "BANKNIFTY": {"runs": []}, "SENSEX": {"runs": []}}

        original = TradingRequestHandler.all_index_monitor
        TradingRequestHandler.all_index_monitor = FakeMonitor()
        server = ReusableThreadingHTTPServer(("127.0.0.1", 0), TradingRequestHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/api/index-scanners/diagnostics?date=2026-10-05") as response:
                data = json.load(response)
            self.assertEqual(data["date"], "2026-10-05")
            self.assertEqual(data["BANKNIFTY"]["runs"], [])
        finally:
            server.shutdown()
            server.server_close()
            TradingRequestHandler.all_index_monitor = original

    def test_telegram_check_api_returns_delivery_failure(self):
        class FakeMonitor:
            def test_telegram(self, symbol):
                return {"symbol": symbol, "sent": False, "error": "HTTP 400: chat not found"}

        original = TradingRequestHandler.all_index_monitor
        TradingRequestHandler.all_index_monitor = FakeMonitor()
        server = ReusableThreadingHTTPServer(("127.0.0.1", 0), TradingRequestHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/index-scanners/telegram-test",
                data=json.dumps({"symbol": "NIFTY"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request) as response:
                result = json.load(response)
            self.assertEqual(result["error"], "HTTP 400: chat not found")
        finally:
            server.shutdown()
            server.server_close()
            TradingRequestHandler.all_index_monitor = original

    def test_refresh_all_masters_then_start_and_stop_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            masters = _master_service(root, fetcher=lambda exchange: [{"exchange": exchange,
                "tradingsymbol": "TEST", "instrument_token": "1"}])
            nifty, indexes = FakeNifty(), FakeIndexes()
            monitor = AllIndexMonitor(nifty, indexes, masters)
            with self.assertRaisesRegex(ValueError, "Refresh Zerodha instrument masters"):
                monitor.start()
            masters.start_refresh()
            masters._thread.join(timeout=5)
            self.assertTrue(masters.status()["ready_for_all"])
            self.assertEqual(set(masters.status()["job"]["results"]), {"NSE", "NFO", "BSE", "BFO"})
            self.assertTrue(monitor.start()["all_running"])
            self.assertFalse(monitor.stop()["any_running"])

    def test_failed_refresh_preserves_existing_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "instruments_BFO.csv"
            write_instruments_csv(path, [{"exchange": "BFO", "tradingsymbol": "OLD", "instrument_token": "1"}])
            def fetch(exchange):
                if exchange == "BFO":
                    raise RuntimeError("permission denied")
                return [{"exchange": exchange, "tradingsymbol": "TEST", "instrument_token": "2"}]
            masters = _master_service(root, fetcher=fetch)
            masters.start_refresh()
            masters._thread.join(timeout=5)
            self.assertIn("BFO", masters.status()["job"]["errors"])
            self.assertIn("OLD", path.read_text(encoding="utf-8"))

    def test_all_start_rolls_back_newly_started_monitors_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            masters = _master_service(Path(tmp), fetcher=lambda exchange: [{"exchange": exchange,
                "tradingsymbol": "TEST", "instrument_token": "1"}])
            masters.start_refresh()
            masters._thread.join(timeout=5)
            nifty, indexes = FakeNifty(), FakeIndexes()
            original_start = indexes.start

            def fail_sensex(symbol, interval_seconds):
                if symbol == "SENSEX":
                    raise RuntimeError("Sensex unavailable")
                return original_start(symbol, interval_seconds)

            indexes.start = fail_sensex
            monitor = AllIndexMonitor(nifty, indexes, masters)
            with self.assertRaisesRegex(RuntimeError, "Sensex unavailable"):
                monitor.start()
            self.assertFalse(monitor.status()["any_running"])

    def test_zerodha_calls_are_serialized_across_clients(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        class Response:
            def __enter__(self):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                return self

            def __exit__(self, *_):
                nonlocal active
                with lock:
                    active -= 1

            def read(self):
                time.sleep(0.01)
                return b"ok"

        with patch("trading_analysis.brokers.zerodha.urlopen", return_value=Response()), \
             patch("trading_analysis.brokers.zerodha._MIN_REQUEST_GAP_SECONDS", 0):
            clients = [ZerodhaKiteClient("key", "token") for _ in range(4)]
            workers = [threading.Thread(target=client._get_text, args=("/quote",)) for client in clients]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=5)
        self.assertEqual(peak, 1)


if __name__ == "__main__":
    unittest.main()
