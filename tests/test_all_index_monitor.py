from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from trading_analysis.all_index_monitor import AllIndexMonitor
from trading_analysis.brokers.zerodha import ZerodhaKiteClient, write_instruments_csv
from trading_analysis.instrument_master_service import InstrumentMasterService
from trading_analysis.web_services import AnalysisService


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
