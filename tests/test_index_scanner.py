from __future__ import annotations

import tempfile
import unittest
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_analysis.analysis.options import option_contracts_for_symbol
from trading_analysis.index_scanner import IndexScanRepository, IndexScannerService, _scan_window
from trading_analysis.index_signal import leader_confirmation, option_footprint, technical_read
from trading_analysis.models import Candle
from trading_analysis.web_services import AnalysisService


IST = ZoneInfo("Asia/Kolkata")


def _chain(side: str = "PE", rising_premium: bool = False):
    current = []
    previous = []
    for strike in (100, 110, 120, 130):
        for option_type in ("CE", "PE"):
            before = {"strike": strike, "option_type": option_type, "oi": 1000, "last_price": 20}
            after = dict(before)
            if option_type == side:
                after["oi"] = 1200
                after["last_price"] = 21 if rising_premium else 19
            previous.append(before)
            current.append(after)
    return current, previous


class IndexScannerTests(unittest.TestCase):
    def test_daily_finalization_window(self):
        self.assertTrue(_scan_window(datetime(2026, 10, 5, 15, 40, tzinfo=IST)))
        self.assertFalse(_scan_window(datetime(2026, 10, 5, 15, 46, tzinfo=IST)))

    def test_bfo_contracts_use_bfo_quote_key(self):
        rows = [{"exchange": "BFO", "segment": "BFO-OPT", "name": "SENSEX",
                 "tradingsymbol": "SENSEX26OCT80000CE", "expiry": "2026-10-29",
                 "strike": "80000", "instrument_type": "CE", "lot_size": "20"}]
        contracts = option_contracts_for_symbol(rows, "SENSEX", exchange="BFO")
        self.assertEqual(contracts[0].kite_key, "BFO:SENSEX26OCT80000CE")
        self.assertEqual(option_contracts_for_symbol(rows, "SENSEX"), [])

    def test_option_footprint_needs_oi_and_falling_premium(self):
        current, previous = _chain("PE")
        self.assertEqual(option_footprint(current, previous, 100)["bias"], "bullish")
        current, previous = _chain("CE")
        self.assertEqual(option_footprint(current, previous, 100)["bias"], "bearish")
        current, previous = _chain("PE", rising_premium=True)
        self.assertNotEqual(option_footprint(current, previous, 100)["bias"], "bullish")

    def test_leaders_require_technical_and_option_breadth(self):
        rows = [{"symbol": str(i), "technical_bias": "bullish", "option_bias": "bullish",
                 "weight_pct": None, "change_pct": 1} for i in range(5)]
        self.assertTrue(leader_confirmation(rows, "bullish")["passed"])
        rows[1]["option_bias"] = "unclear"
        rows[2]["option_bias"] = "unclear"
        rows[3]["option_bias"] = "unclear"
        rows[4]["option_bias"] = "unclear"
        self.assertFalse(leader_confirmation(rows, "bullish")["passed"])
        rows = [{"symbol": str(i), "technical_bias": "bullish" if i < 3 else "bearish",
                 "option_bias": "bullish" if i >= 3 else "unclear", "weight_pct": None, "change_pct": 1}
                for i in range(5)]
        self.assertFalse(leader_confirmation(rows, "bullish")["passed"])

    def test_technical_freshness_handles_utc_timestamps(self):
        now = datetime(2026, 10, 5, 12, 35, tzinfo=IST)
        end = datetime(2026, 10, 5, 12, 0, tzinfo=IST)
        candles = []
        for index in range(60):
            time = (end - timedelta(minutes=15 * (59 - index))).astimezone(timezone.utc)
            candles.append(Candle(time, 100 + index, 101 + index, 99 + index, 100.5 + index, 100))
        self.assertEqual(technical_read(candles, "15minute", now)["bias"], "bullish")

    def test_repository_deduplicates_trade_and_records_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = IndexScanRepository(Path(tmp) / "index.db")
            signal = {"entry_candle_timestamp": datetime(2026, 10, 5, 9, 15, tzinfo=IST),
                      "horizon": "intraday", "direction": "bullish", "entry_price": 100,
                      "stop_level": 99, "target_level": 102, "score": 80}
            trade = repo.open_trade("BANKNIFTY", signal)
            self.assertIsNotNone(trade)
            self.assertIsNone(repo.open_trade("BANKNIFTY", signal))
            repo.alert(trade, "entry", "test", "not_configured")
            repo.alert(trade, "entry", "test", "not_configured")
            closed = repo.close_trade(trade["trade_id"], {"candle_timestamp": signal["entry_candle_timestamp"],
                                                            "price": 102, "reason": "target_reached"})
            self.assertEqual(closed["status"], "closed")
            self.assertIsNotNone(closed["open_seconds"])
            self.assertIsNone(repo.open_trade("BANKNIFTY", signal))
            history = repo.history("BANKNIFTY")
            self.assertEqual(len(history["trades"]), 1)
            self.assertEqual(len(history["alerts"]), 1)

    def test_service_profiles_and_no_cached_data_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis = AnalysisService(daily_data_dir=Path(tmp) / "candles")
            scanner = IndexScannerService(analysis, db_path=Path(tmp) / "index.db")
            self.assertEqual(scanner.profiles["BANKNIFTY"]["option_exchange"], "NFO")
            self.assertEqual(scanner.profiles["SENSEX"]["option_exchange"], "BFO")
            result = scanner.run_once("SENSEX", refresh=False)
            self.assertEqual(result["entries_created"], 0)
            self.assertEqual(result["horizons"]["intraday"]["technical"]["bias"], "unavailable")
            self.assertEqual(scanner.status("SENSEX")["history"]["alerts"], [])

    def test_missing_and_stale_option_master_block_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            master = Path(tmp) / "instruments_NFO.csv"
            analysis = AnalysisService(nfo_instruments_path=master)
            scanner = IndexScannerService(analysis, db_path=Path(tmp) / "index.db")
            with self.assertRaises(FileNotFoundError):
                scanner._refresh_options("BANKNIFTY")
            master.write_text("exchange,segment,name\n", encoding="utf-8")
            old = time.time() - 8 * 86400
            os.utime(master, (old, old))
            with self.assertRaisesRegex(ValueError, "older than 7 days"):
                scanner._refresh_options("BANKNIFTY")

    def test_individual_monitor_refuses_missing_instrument_masters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis = AnalysisService(nse_instruments_path=root / "instruments_NSE.csv",
                                       nfo_instruments_path=root / "instruments_NFO.csv",
                                       bse_instruments_path=root / "instruments_BSE.csv")
            scanner = IndexScannerService(analysis, db_path=root / "index.db")
            with self.assertRaisesRegex(ValueError, "NSE \\(missing\\), NFO \\(missing\\)"):
                scanner.start("BANKNIFTY")
            self.assertFalse(scanner.status("BANKNIFTY")["running"])


if __name__ == "__main__":
    unittest.main()
