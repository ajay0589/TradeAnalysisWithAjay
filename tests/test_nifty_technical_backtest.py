from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from trading_analysis.models import Candle
from trading_analysis.nifty.technical_backtest import Settings, _exit, _signal, backtest_technical_setup, simulate


IST = ZoneInfo("Asia/Kolkata")


class NiftyTechnicalBacktestTests(unittest.TestCase):
    def test_daily_ema_pullback_requires_reclaim_in_trend(self) -> None:
        candles = [
            Candle(datetime(2026, 1, 5, tzinfo=IST), 100, 101, 96, 98, 0),
            Candle(datetime(2026, 1, 6, tzinfo=IST), 98, 103, 97, 102, 0),
        ]
        settings = Settings("positional", "ema_pullback", "both", 1.0, 10)
        self.assertEqual(
            _signal(candles, 1, settings, [100, 100], [95, 95], [50, 56], {}),
            "bullish",
        )
        self.assertIsNone(
            _signal(candles, 1, settings, [100, 100], [101, 101], [50, 56], {}),
        )

    def test_daily_entry_uses_next_open_and_stop_wins_same_candle(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=IST)
        candles = [
            Candle(start + timedelta(days=index), 100, 102, 98, 100, 0)
            for index in range(63)
        ]
        candles[61] = Candle(start + timedelta(days=61), 100, 105, 95, 100, 0)
        with patch("trading_analysis.nifty.technical_backtest._signal", side_effect=lambda _rows, index, *_args: "bullish" if index == 60 else None):
            trades = simulate(candles, Settings("positional", "ema_pullback", "both", 1.0, 10))
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["entry_time"], candles[61].timestamp.replace(hour=9, minute=15))
        self.assertEqual(trades[0]["exit_reason"], "stop")
        self.assertLess(trades[0]["net_r"], -1.0)

    def test_intraday_exit_closes_at_session_end(self) -> None:
        candle = Candle(datetime(2026, 9, 22, 15, 0, tzinfo=IST), 100, 101, 99, 100.5, 0)
        index, price, reason = _exit([candle], 0, "bullish", 100, 95, 105, Settings("intraday", "ema_pullback", "both", 1.0, 8))
        self.assertEqual((index, price, reason), (0, 100.5, "session_close"))

    def test_research_payload_identifies_spot_and_costs(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=IST)
        candles = [Candle(start + timedelta(days=index), 100, 102, 98, 100, 0) for index in range(62)]
        payload = backtest_technical_setup(candles, "positional", cost_bps_per_side=5)
        self.assertEqual(payload["method"], "technical")
        self.assertIn("spot index", payload["coverage"]["option_chain"])
        self.assertIn("5 bps per side", payload["coverage"]["costs"])


if __name__ == "__main__":
    unittest.main()
