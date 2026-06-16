from __future__ import annotations

import unittest
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from trading_analysis.candles import normalize_timeframe, prepare_candles, candle_window
from trading_analysis.analysis.fundamental import analyze_fundamentals
from trading_analysis.analysis.market_structure import analyze_market_structure
from trading_analysis.analysis.options import (
    OptionContract,
    analyze_option_chain,
    classify_buildup,
    nearest_expiry,
    option_contracts_for_symbol,
    select_strikes_around_spot,
)
from trading_analysis.analysis.relative_strength import compare_relative_strength
from trading_analysis.analysis.scoring import combine_signals
from trading_analysis.analysis.technical import analyze_technical
from trading_analysis.analysis.trade_decision import build_trade_decision
from trading_analysis.brokers.zerodha import (
    build_login_url,
    chunked,
    extract_request_token,
    kite_checksum,
    parse_kite_timestamp,
    resolve_instrument_token,
)
from trading_analysis.data_sources.fno_universe import build_fno_watchlist, fno_stock_symbols
from trading_analysis.data_sources.nse_equity import (
    build_sector_map_from_csv_rows,
    build_sector_map_from_metadata,
    choose_sector_index,
)
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.config import upsert_env_value
from trading_analysis.models import Candle, FundamentalSnapshot
from trading_analysis.web_services import (
    AnalysisService,
    _bulk_window_days,
    _entry_trigger_panel,
    _normalize_bulk_requested_timeframes,
    _normalize_bulk_timeframes,
    classify_setup,
)


class AnalysisTests(unittest.TestCase):
    def test_sample_candles_produce_signal(self) -> None:
        candles = load_candles(Path("data/sample/RELIANCE.csv"))

        technical = analyze_technical(candles)

        self.assertGreaterEqual(technical.score, 0)
        self.assertLessEqual(technical.score, 100)
        self.assertIsNotNone(technical.rsi14)

    def test_combined_score_stays_bounded(self) -> None:
        candles = load_candles(Path("data/sample/TCS.csv"))
        technical = analyze_technical(candles)
        fundamental = analyze_fundamentals(
            FundamentalSnapshot(
                roe_percent=25,
                debt_to_equity=0.1,
                sales_growth_yoy_percent=12,
                profit_growth_yoy_percent=15,
                pledged_percent=0,
            )
        )

        signal = combine_signals("TCS", technical, fundamental)

        self.assertGreaterEqual(signal.score, 0)
        self.assertLessEqual(signal.score, 100)

    def test_kite_timestamp_parser_handles_india_offset(self) -> None:
        timestamp = parse_kite_timestamp("2017-12-15T09:15:00+0530")

        self.assertEqual(timestamp.hour, 9)
        self.assertEqual(timestamp.utcoffset().total_seconds(), 19800)

    def test_resolve_instrument_token_from_cached_master(self) -> None:
        instruments = [
            {"exchange": "NSE", "tradingsymbol": "INFY", "instrument_token": "408065"},
            {"exchange": "NFO", "tradingsymbol": "NIFTY26JUNFUT", "instrument_token": "12517890"},
        ]

        token = resolve_instrument_token(instruments, "nse", "infy")

        self.assertEqual(token, "408065")

    def test_kite_login_helpers(self) -> None:
        login_url = build_login_url("abc123")

        self.assertEqual(login_url, "https://kite.zerodha.com/connect/login?v=3&api_key=abc123")
        self.assertEqual(
            extract_request_token("https://example.com/?status=success&request_token=req123"),
            "req123",
        )
        self.assertEqual(
            kite_checksum("api", "request", "secret"),
            "257f5edc0415fc77bd14b16e08ca983df5e4d049db7c63e292f18f6d640402b5",
        )

    def test_fno_universe_uses_stock_futures_that_exist_on_nse(self) -> None:
        nfo = [
            {"exchange": "NFO", "segment": "NFO-FUT", "instrument_type": "FUT", "name": "NIFTY"},
            {"exchange": "NFO", "segment": "NFO-FUT", "instrument_type": "FUT", "name": "RELIANCE"},
            {"exchange": "NFO", "segment": "NFO-OPT", "instrument_type": "CE", "name": "TCS"},
        ]
        nse = [
            {"exchange": "NSE", "segment": "INDICES", "instrument_type": "EQ", "tradingsymbol": "NIFTY 50"},
            {"exchange": "NSE", "segment": "NSE", "instrument_type": "EQ", "tradingsymbol": "RELIANCE"},
            {"exchange": "NSE", "segment": "NSE", "instrument_type": "EQ", "tradingsymbol": "TCS"},
        ]

        symbols = fno_stock_symbols(nfo, nse)
        watchlist = build_fno_watchlist(symbols, source="test")

        self.assertEqual(symbols, ["RELIANCE"])
        self.assertEqual(watchlist["symbols"][0]["data_file"], "RELIANCE.csv")

    def test_option_chain_analysis_and_buildup(self) -> None:
        contracts = [
            OptionContract("ABC26JUN100CE", "ABC", __import__("datetime").date(2026, 6, 30), 100, "CE", 100),
            OptionContract("ABC26JUN100PE", "ABC", __import__("datetime").date(2026, 6, 30), 100, "PE", 100),
            OptionContract("ABC26JUN110CE", "ABC", __import__("datetime").date(2026, 6, 30), 110, "CE", 100),
            OptionContract("ABC26JUN110PE", "ABC", __import__("datetime").date(2026, 6, 30), 110, "PE", 100),
        ]
        quotes = {
            "NFO:ABC26JUN100CE": {"last_price": 8, "volume": 10, "oi": 130, "ohlc": {"close": 7}},
            "NFO:ABC26JUN100PE": {"last_price": 4, "volume": 20, "oi": 100, "ohlc": {"close": 5}},
            "NFO:ABC26JUN110CE": {"last_price": 3, "volume": 30, "oi": 70, "ohlc": {"close": 4}},
            "NFO:ABC26JUN110PE": {"last_price": 9, "volume": 40, "oi": 250, "ohlc": {"close": 8}},
        }
        previous = {
            "ABC26JUN100CE": {"oi": "100", "last_price": "6"},
            "ABC26JUN100PE": {"oi": "120", "last_price": "5"},
            "ABC26JUN110CE": {"oi": "60", "last_price": "4"},
            "ABC26JUN110PE": {"oi": "240", "last_price": "8"},
        }

        analysis = analyze_option_chain(
            "ABC",
            contracts[0].expiry,
            contracts,
            quotes,
            spot_price=105,
            previous_rows=previous,
        )

        self.assertEqual(round(analysis.pcr_oi or 0, 2), 1.75)
        self.assertEqual(analysis.highest_call_oi_strike, 100)
        self.assertEqual(analysis.highest_put_oi_strike, 110)
        self.assertIn("Long build-up", {row.buildup for row in analysis.rows})
        self.assertEqual(
            next(row for row in analysis.rows if row.tradingsymbol == "ABC26JUN100CE").previous_close,
            6,
        )
        self.assertEqual(classify_buildup(-1, 10), "Short build-up")

    def test_option_contract_selection_helpers(self) -> None:
        rows = [
            {
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_type": "CE",
                "name": "ABC",
                "tradingsymbol": "ABC26JUN100CE",
                "expiry": "2026-06-30",
                "strike": "100",
                "lot_size": "50",
            },
            {
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_type": "PE",
                "name": "ABC",
                "tradingsymbol": "ABC26JUN110PE",
                "expiry": "2026-06-30",
                "strike": "110",
                "lot_size": "50",
            },
        ]

        contracts = option_contracts_for_symbol(rows, "abc")

        self.assertEqual(nearest_expiry(contracts, today=__import__("datetime").date(2026, 6, 1)).isoformat(), "2026-06-30")
        self.assertEqual(len(select_strikes_around_spot(contracts, 105, 0)), 1)
        self.assertEqual(chunked(["a", "b", "c"], 2), [["a", "b"], ["c"]])

    def test_market_structure_support_resistance_are_price_relative(self) -> None:
        candles = load_candles(Path("data/sample/RELIANCE.csv"))

        structure = analyze_market_structure(candles)

        self.assertLessEqual(structure.support or 0, candles[-1].close)
        self.assertGreaterEqual(structure.resistance or candles[-1].close, candles[-1].close)

    def test_relative_strength_and_trade_decision(self) -> None:
        stock = load_candles(Path("data/sample/RELIANCE.csv"))
        benchmark = load_candles(Path("data/sample/TCS.csv"))
        technical = analyze_technical(stock)
        structure = analyze_market_structure(stock)

        rs = compare_relative_strength("Stock vs Benchmark", stock, benchmark, lookback=10)
        decision = build_trade_decision(
            "RELIANCE",
            daily_technical=technical,
            daily_structure=structure,
        )

        self.assertIn(rs.label, {"outperforming", "underperforming", "neutral", "insufficient data"})
        self.assertIn(decision.bias, {"bullish", "bearish", "neutral"})
        self.assertEqual(decision.score_breakdown.base_score, 50)
        self.assertEqual(decision.score_breakdown.final_score, decision.score)
        self.assertEqual(
            decision.score_breakdown.raw_score,
            decision.score_breakdown.base_score + sum(component.points for component in decision.score_breakdown.components),
        )
        self.assertIn("Daily direction", {component.name for component in decision.score_breakdown.components})

    def test_sector_map_prefers_zerodha_index_aliases(self) -> None:
        metadata = {
            "metadata": {"pdSectorIndAll": ["NIFTY 50", "NIFTY ENERGY", "NIFTY OIL & GAS"]},
            "industryInfo": {
                "macro": "Energy",
                "sector": "Oil Gas & Consumable Fuels",
                "industry": "Petroleum Products",
                "basicIndustry": "Refineries & Marketing",
            },
        }
        nse_instruments = [
            {"exchange": "NSE", "segment": "INDICES", "tradingsymbol": "NIFTY ENERGY"},
            {"exchange": "NSE", "segment": "INDICES", "tradingsymbol": "NIFTY OIL AND GAS"},
        ]

        selected = choose_sector_index(metadata, {"NIFTY ENERGY", "NIFTY OIL AND GAS"})
        sector_map = build_sector_map_from_metadata(["RELIANCE"], {"RELIANCE": metadata}, nse_instruments)

        self.assertEqual(selected, "NIFTY OIL AND GAS")
        self.assertEqual(sector_map["symbols"]["RELIANCE"]["index_symbol"], "NIFTY OIL AND GAS")
        self.assertEqual(sector_map["symbols"]["RELIANCE"]["data_file"], "NIFTY_OIL_AND_GAS.csv")

    def test_sector_map_from_csv_rows(self) -> None:
        rows = [
            {"Symbol": "RELIANCE", "Industry": "Oil Gas & Consumable Fuels", "Index Symbol": "NIFTY OIL & GAS"},
            {"Symbol": "TCS", "Industry": "Information Technology", "Index Symbol": ""},
            {"Symbol": "ABB", "Industry": "Capital Goods", "Index Symbol": ""},
        ]
        nse_instruments = [
            {"exchange": "NSE", "segment": "INDICES", "tradingsymbol": "NIFTY OIL AND GAS"},
            {"exchange": "NSE", "segment": "INDICES", "tradingsymbol": "NIFTY IT"},
        ]

        sector_map = build_sector_map_from_csv_rows(
            rows,
            nse_instruments,
            source="test.csv",
            symbols_filter=["RELIANCE", "TCS", "ABB"],
        )

        self.assertEqual(sector_map["symbols"]["RELIANCE"]["index_symbol"], "NIFTY OIL AND GAS")
        self.assertEqual(sector_map["symbols"]["TCS"]["index_symbol"], "NIFTY IT")
        self.assertIn("ABB", sector_map["unmapped"])

    def test_web_setup_classification_matches_options_action(self) -> None:
        self.assertEqual(classify_setup(70, "bullish", "uptrend")["strategy"], "Sell put option")
        self.assertEqual(classify_setup(30, "bearish", "downtrend")["strategy"], "Sell call option")
        self.assertEqual(
            classify_setup(50, "neutral", "range")["strategy"],
            "Sell call and put as strangle",
        )

    def test_web_symbols_include_index_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            watchlist = root / "watchlist.json"
            watchlist.write_text(
                '{"symbols":[{"symbol":"RELIANCE","instrument_type":"EQ"}]}',
                encoding="utf-8",
            )
            candles = root / "candles"
            candles.mkdir()
            (candles / "NIFTY_50.csv").write_text("", encoding="utf-8")
            nse_instruments = root / "instruments_NSE.csv"
            nse_instruments.write_text(
                "instrument_token,exchange,tradingsymbol,name,segment,instrument_type\n"
                "256265,NSE,NIFTY 50,NIFTY 50,INDICES,EQ\n"
                "260105,NSE,NIFTY BANK,NIFTY BANK,INDICES,EQ\n"
                "738561,NSE,RELIANCE,RELIANCE,NSE,EQ\n",
                encoding="utf-8",
            )

            service = AnalysisService(
                watchlist_path=watchlist,
                daily_data_dir=candles,
                nse_instruments_path=nse_instruments,
            )
            payload = service.symbols()
            symbols = {row["symbol"]: row for row in payload["symbols"]}

            self.assertEqual(service.resolve_symbol("NIFTY 50"), "NIFTY")
            self.assertEqual(service.resolve_symbol("banknifty"), "BANKNIFTY")
            self.assertEqual(service.resolve_symbol("SENSEX"), "SENSEX")
            self.assertTrue(symbols["NIFTY"]["has_daily"])
            self.assertIn("BANKNIFTY", symbols)
            self.assertIn("SENSEX", symbols)
            self.assertEqual(payload["total_indexes"], 3)

    def test_option_snapshot_history_is_listed_by_symbol_and_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            option_chain_dir = root / "option_chain"
            history = option_chain_dir / "history"
            history.mkdir(parents=True)
            latest = option_chain_dir / "NIFTY_2026-06-16.csv"
            old = history / "NIFTY_2026-06-16_20260615_091500.csv"
            csv_text = (
                "snapshot_time,symbol,expiry,tradingsymbol,strike,option_type,last_price,oi\n"
                "2026-06-15T09:15:00,NIFTY,2026-06-16,NIFTY2661625000CE,25000,CE,100,1000\n"
            )
            latest.write_text(csv_text.replace("09:15:00", "10:00:00"), encoding="utf-8")
            old.write_text(csv_text, encoding="utf-8")

            service = AnalysisService(option_chain_dir=option_chain_dir)
            payload = service.option_snapshots("NIFTY", "2026-06-16")
            paths = {row["path"] for row in payload["snapshots"]}

            self.assertEqual(payload["symbol"], "NIFTY")
            self.assertEqual(payload["expiry"], "2026-06-16")
            self.assertIn(str(latest), paths)
            self.assertIn(str(old), paths)

    def test_multi_timeframe_includes_monthly_and_weekly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            candles_dir = root / "candles"
            candles_dir.mkdir()
            rows = ["date,open,high,low,close,volume,open_interest"]
            start = datetime(2022, 1, 1)
            for index in range(1700):
                day = start + timedelta(days=index)
                close = 100 + index * 0.1
                rows.append(f"{day.isoformat()},{close - 1},{close + 1},{close - 2},{close},1000,")
            (candles_dir / "ABC.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

            service = AnalysisService(daily_data_dir=candles_dir)
            mtf = service._multi_timeframe_analysis(
                "ABC",
                candle_window(days=90, now=datetime(2026, 6, 15)),
            )
            by_frame = {row["timeframe"]: row for row in mtf["rows"]}

            self.assertEqual([row["timeframe"] for row in mtf["rows"]], ["month", "week", "day", "60minute", "15minute"])
            self.assertEqual(by_frame["month"]["lookback_days"], 1460)
            self.assertEqual(by_frame["week"]["lookback_days"], 730)
            self.assertEqual(by_frame["day"]["lookback_days"], 365)
            self.assertEqual(by_frame["60minute"]["lookback_days"], 90)
            self.assertEqual(by_frame["15minute"]["lookback_days"], 90)
            self.assertEqual(by_frame["month"]["status"], "analyzed")
            self.assertEqual(by_frame["week"]["status"], "analyzed")
            self.assertEqual(by_frame["day"]["status"], "analyzed")

    def test_bulk_download_timeframes_include_monthly_weekly_as_day_source(self) -> None:
        requested = _normalize_bulk_requested_timeframes(["month", "week", "15minute"])

        self.assertEqual(requested, ["month", "week", "15minute"])
        self.assertEqual(_normalize_bulk_timeframes(requested), ["day", "15minute"])
        self.assertEqual(_bulk_window_days(requested, 90), 1460)
        self.assertEqual(_bulk_window_days(["week"], 90), 730)
        self.assertEqual(_bulk_window_days(["day", "60minute"], 90), 90)

    def test_entry_trigger_allows_bullish_put_after_confirmations(self) -> None:
        option_chain = self._entry_option_chain(spot_price=105)
        panel = _entry_trigger_panel(
            setup={"bucket": "bullish"},
            chart_technical=SimpleNamespace(close=105),
            chart_structure=SimpleNamespace(support=102, resistance=110, invalidation=102),
            multi_timeframe=self._entry_mtf(close=105),
            option_chain=option_chain,
        )

        self.assertEqual(panel["status"], "Entry allowed")
        self.assertEqual(panel["candidates"][0]["strike"], 100)
        self.assertEqual(panel["candidates"][0]["option_type"], "PE")
        self.assertEqual(panel["candidates"][0]["status"], "Entry allowed")

    def test_entry_trigger_exits_when_price_breaches_invalidation(self) -> None:
        option_chain = self._entry_option_chain(spot_price=101)
        panel = _entry_trigger_panel(
            setup={"bucket": "bullish"},
            chart_technical=SimpleNamespace(close=101),
            chart_structure=SimpleNamespace(support=102, resistance=110, invalidation=102),
            multi_timeframe=self._entry_mtf(close=101),
            option_chain=option_chain,
        )

        self.assertEqual(panel["status"], "Exit/Adjust")
        self.assertTrue(any("below invalidation" in row["detail"] for row in panel["rows"]))

    def test_timeframe_aliases_and_weekly_resample(self) -> None:
        candles = [
            Candle(datetime(2026, 6, 1), 10, 12, 9, 11, 100),
            Candle(datetime(2026, 6, 2), 11, 13, 10, 12, 120),
            Candle(datetime(2026, 6, 8), 12, 15, 11, 14, 150),
        ]

        weekly = prepare_candles(candles, "weekly", candle_window())

        self.assertEqual(normalize_timeframe("1hour"), "60minute")
        self.assertEqual(len(weekly), 2)
        self.assertEqual(weekly[0].open, 10)
        self.assertEqual(weekly[0].high, 13)
        self.assertEqual(weekly[0].volume, 220)

    def test_env_upsert_updates_file_and_process_environment(self) -> None:
        original = os.environ.get("ZERODHA_ACCESS_TOKEN")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                env_path = Path(tmpdir) / ".env"
                env_path.write_text("TRADING_MODE=paper\nZERODHA_ACCESS_TOKEN=old\n", encoding="utf-8")

                upsert_env_value(env_path, "ZERODHA_ACCESS_TOKEN", "new")

                self.assertIn("ZERODHA_ACCESS_TOKEN=new", env_path.read_text(encoding="utf-8"))
                self.assertEqual(os.environ.get("ZERODHA_ACCESS_TOKEN"), "new")
        finally:
            if original is None:
                os.environ.pop("ZERODHA_ACCESS_TOKEN", None)
            else:
                os.environ["ZERODHA_ACCESS_TOKEN"] = original

    def _entry_option_chain(self, spot_price: float):
        contracts = [
            OptionContract("ABC26JUN100PE", "ABC", date(2026, 6, 30), 100, "PE", 100),
            OptionContract("ABC26JUN110CE", "ABC", date(2026, 6, 30), 110, "CE", 100),
        ]
        quotes = {
            "NFO:ABC26JUN100PE": {"last_price": 3, "volume": 1000, "oi": 1200, "ohlc": {"close": 4}},
            "NFO:ABC26JUN110CE": {"last_price": 2, "volume": 500, "oi": 500, "ohlc": {"close": 2}},
        }
        previous = {
            "ABC26JUN100PE": {"last_price": "4", "oi": "1000"},
            "ABC26JUN110CE": {"last_price": "2", "oi": "500"},
        }
        return analyze_option_chain("ABC", date(2026, 6, 30), contracts, quotes, spot_price, previous)

    def _entry_mtf(self, close: float):
        return {
            "bias": "bullish",
            "rows": [
                {
                    "timeframe": "15minute",
                    "label": "15 min",
                    "status": "analyzed",
                    "technical_trend": "bullish",
                    "structure_trend": "uptrend",
                    "close": close,
                    "support": 102,
                    "resistance": 108,
                    "invalidation": 102,
                    "volume_signal": "expansion",
                    "volume_ratio20": 1.35,
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
