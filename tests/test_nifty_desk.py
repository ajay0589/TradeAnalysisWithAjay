from __future__ import annotations

import csv
import json
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from trading_analysis.analysis.options import OptionChainAnalysis, OptionChainRow
from trading_analysis.models import Candle
from trading_analysis.nifty.iv_context import build_nifty_iv_context
from trading_analysis.nifty.models import NiftyIVContext, NiftyOptionContext, NiftyTechnicalContext, to_jsonable
from trading_analysis.nifty.option_context import build_nifty_option_context
from trading_analysis.nifty.strategy_payoff import calculate_strategy_payoff
from trading_analysis.nifty.strategy_suggester import suggest_nifty_strategies
from trading_analysis.nifty.technical_context import build_nifty_technical_context
from trading_analysis.web_app import ReusableThreadingHTTPServer, TradingRequestHandler


class NiftyDeskTests(unittest.TestCase):
    def test_iv_rank_with_sufficient_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "iv.csv"
            _write_iv_history(path, [10 + (index % 20) for index in range(40)])

            context = build_nifty_iv_context(current_atm_iv=25, history_path=path)

            self.assertTrue(context.enough_history)
            self.assertIsNotNone(context.iv_rank)
            self.assertIsNotNone(context.iv_percentile)
            self.assertIn(context.iv_regime, {"low", "normal", "high", "extreme"})

    def test_iv_rank_warns_with_insufficient_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "iv.csv"
            _write_iv_history(path, [12, 13, 14])

            context = build_nifty_iv_context(current_atm_iv=14, history_path=path)

            self.assertFalse(context.enough_history)
            self.assertIsNone(context.iv_rank)
            self.assertTrue(context.warnings)

    def test_technical_context_detects_bullish_trend(self) -> None:
        context = build_nifty_technical_context(_candles_from_closes([100 + index for index in range(240)]))

        self.assertEqual(context.bias_swing, "bullish")
        self.assertEqual(context.bias_positional, "bullish")

    def test_technical_context_detects_bearish_trend(self) -> None:
        context = build_nifty_technical_context(_candles_from_closes([300 - index for index in range(240)]))

        self.assertEqual(context.bias_swing, "bearish")
        self.assertEqual(context.bias_positional, "bearish")

    def test_option_context_detects_oi_support_resistance(self) -> None:
        context = build_nifty_option_context(_option_chain(spot=24500))

        self.assertEqual(context.support_by_oi, 24400)
        self.assertEqual(context.resistance_by_oi, 24600)
        self.assertIsNotNone(context.pcr_oi)

    def test_strategy_suggester_returns_short_strangle_for_neutral_high_iv_range(self) -> None:
        candidates = suggest_nifty_strategies(
            _technical("neutral"),
            _option_context("neutral"),
            _iv("high"),
            mode="swing",
            risk_profile="undefined",
        )

        ids = {candidate.strategy_id for candidate in candidates}
        self.assertIn("nifty_short_strangle", ids)

    def test_strategy_suggester_avoids_short_strangle_for_volatile_condition(self) -> None:
        candidates = suggest_nifty_strategies(
            _technical("bullish"),
            _option_context("volatile"),
            _iv("low"),
            mode="swing",
            risk_profile="undefined",
        )

        self.assertNotIn("nifty_short_strangle", {candidate.strategy_id for candidate in candidates})

    def test_strategy_suggester_returns_directional_spread_for_bullish_alignment(self) -> None:
        candidates = suggest_nifty_strategies(
            _technical("bullish"),
            _option_context("bullish"),
            _iv("normal"),
            mode="swing",
            risk_profile="defined",
        )

        self.assertIn("nifty_bull_call_spread", {candidate.strategy_id for candidate in candidates})

    def test_payoff_calculator_handles_bull_call_spread(self) -> None:
        payoff = calculate_strategy_payoff(
            spot=24500,
            lot_size=75,
            legs=[
                {"side": "buy", "option_type": "CE", "strike": 24500, "premium": 120},
                {"side": "sell", "option_type": "CE", "strike": 24700, "premium": 50},
            ],
        )

        self.assertLess(payoff["net_premium"], 0)
        self.assertTrue(payoff["payoff_table"])
        json.dumps(payoff)

    def test_payoff_calculator_handles_short_strangle(self) -> None:
        payoff = calculate_strategy_payoff(
            spot=24500,
            legs=[
                {"side": "sell", "option_type": "PE", "strike": 24200, "premium": 80},
                {"side": "sell", "option_type": "CE", "strike": 24800, "premium": 75},
            ],
        )

        self.assertGreater(payoff["net_premium"], 0)
        self.assertIn("max loss", payoff["max_loss_note"].lower())

    def test_nifty_api_endpoints_work_with_mocked_service(self) -> None:
        class FakeNiftyService:
            def nifty_context(self, **kwargs):
                return {"symbol": "NIFTY", "mode": kwargs["mode"], "summary": {"points": []}, "warnings": [], "errors": []}

            def nifty_strategy_suggestions(self, **kwargs):
                return {"symbol": "NIFTY", "candidates": [{"strategy_id": "nifty_iron_condor"}], "warnings": [], "errors": []}

            def nifty_payoff(self, payload):
                return {"spot": payload["spot"], "payoff_table": [], "net_premium": 0}

            def nifty_backtest(self, payload):
                return {"strategy_id": payload["strategy_id"], "metrics": {"signals": 0}, "warnings": []}

        original = TradingRequestHandler.nifty_service
        TradingRequestHandler.nifty_service = FakeNiftyService()
        server = ReusableThreadingHTTPServer(("127.0.0.1", 0), TradingRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            self.assertEqual(_http_json(f"{base}/api/nifty/context?mode=intraday")["mode"], "intraday")
            self.assertEqual(
                _http_json(f"{base}/api/nifty/strategy-suggestions", {"mode": "swing"})["candidates"][0]["strategy_id"],
                "nifty_iron_condor",
            )
            self.assertEqual(_http_json(f"{base}/api/nifty/payoff", {"spot": 24500, "legs": []})["spot"], 24500)
            self.assertEqual(_http_json(f"{base}/api/nifty/backtest", {"strategy_id": "nifty_short_strangle"})["strategy_id"], "nifty_short_strangle")
        finally:
            server.shutdown()
            server.server_close()
            TradingRequestHandler.nifty_service = original

    def test_json_serialization(self) -> None:
        payload = to_jsonable(
            {
                "technical": _technical("bullish"),
                "options": _option_context("bullish"),
                "iv": _iv("normal"),
            }
        )

        json.dumps(payload)


def _candles_from_closes(closes: list[float]) -> list[Candle]:
    rows = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index else close
        rows.append(
            Candle(
                timestamp=datetime(2025, 1, 1) + timedelta(days=index),
                open=previous,
                high=max(previous, close) + 2,
                low=min(previous, close) - 2,
                close=close,
                volume=1000,
            )
        )
    return rows


def _option_chain(spot: float) -> OptionChainAnalysis:
    rows = (
        OptionChainRow("NIFTY24400PE", 24400, "PE", 80, 70, 10, 5000, 4000, 1000, 25, 18, 1, 1000, 79, 81, "Short build-up"),
        OptionChainRow("NIFTY24600CE", 24600, "CE", 75, 70, 5, 4500, 3800, 700, 18, 17, 1, 900, 74, 76, "Short build-up"),
        OptionChainRow("NIFTY24500PE", 24500, "PE", 120, 115, 5, 2500, 2400, 100, 4, 19, 1, 600, 119, 121, "Long build-up"),
        OptionChainRow("NIFTY24500CE", 24500, "CE", 115, 120, -5, 2300, 2200, 100, 5, 19, 1, 650, 114, 116, "Short build-up"),
    )
    return OptionChainAnalysis(
        symbol="NIFTY",
        expiry=date(2026, 7, 2),
        spot_price=spot,
        contract_count=len(rows),
        pcr_oi=1.1,
        max_pain=24500,
        atm_iv=19,
        atm_iv_change=1,
        iv_percentile=None,
        total_volume=sum(row.volume for row in rows),
        total_oi_change=sum(row.oi_change or 0 for row in rows),
        total_oi_change_percent=10,
        highest_call_oi_strike=24600,
        highest_put_oi_strike=24400,
        rows=rows,
    )


def _technical(bias: str) -> NiftyTechnicalContext:
    return NiftyTechnicalContext(
        symbol="NIFTY",
        as_of=datetime.now(),
        spot=24500,
        timeframe="day",
        trend_intraday=bias,
        trend_swing=bias,
        trend_positional=bias,
        bias_intraday=bias,
        bias_swing=bias,
        bias_positional=bias,
        support_levels=[24400],
        resistance_levels=[24600],
        vwap=24500,
        ema20=24450,
        ema50=24400,
        sma200=24000,
        rsi14=55 if bias != "bearish" else 42,
        atr14=120,
        previous_day_high=24600,
        previous_day_low=24400,
        previous_day_close=24500,
        day_open=24520,
        candle_signal="balanced",
        market_structure="range" if bias == "neutral" else "uptrend" if bias == "bullish" else "downtrend",
        factors=[],
        notes=[],
        warnings=[],
    )


def _option_context(bias: str) -> NiftyOptionContext:
    return NiftyOptionContext(
        symbol="NIFTY",
        as_of=datetime.now(),
        spot=24500,
        expiries=["2026-07-02"],
        selected_weekly_expiry="2026-07-02",
        selected_monthly_expiry=None,
        weekly_chain_summary={"atm_iv": 20},
        monthly_chain_summary=None,
        pcr_oi=1.0,
        pcr_volume=1.0,
        max_pain=24500,
        atm_strike=24500,
        atm_iv=20,
        atm_iv_change=1,
        total_ce_oi=5000,
        total_pe_oi=5500,
        total_ce_oi_change=500,
        total_pe_oi_change=700,
        highest_ce_oi_strikes=[24600],
        highest_pe_oi_strikes=[24400],
        ce_writing_strikes=[24600],
        pe_writing_strikes=[24400],
        ce_unwinding_strikes=[],
        pe_unwinding_strikes=[],
        support_by_oi=24400,
        resistance_by_oi=24600,
        option_bias=bias,
        notes=[],
        warnings=[],
    )


def _iv(regime: str) -> NiftyIVContext:
    ranks = {"low": 10, "normal": 40, "high": 75, "extreme": 90}
    return NiftyIVContext(
        symbol="NIFTY",
        as_of=datetime.now(),
        atm_iv=20,
        iv_change=1,
        iv_rank_lookback_days=252,
        iv_rank=ranks.get(regime),
        iv_percentile=ranks.get(regime),
        iv_min=10,
        iv_max=30,
        iv_mean=18,
        iv_regime=regime,
        enough_history=True,
        notes=[],
        warnings=[],
    )


def _write_iv_history(path: Path, values: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date_time", "symbol", "expiry", "days_to_expiry", "atm_strike", "atm_iv", "weekly_atm_iv", "monthly_atm_iv", "source_snapshot"])
        writer.writeheader()
        for index, value in enumerate(values):
            writer.writerow(
                {
                    "date_time": (datetime.now() - timedelta(days=len(values) - index)).isoformat(timespec="seconds"),
                    "symbol": "NIFTY",
                    "expiry": "2026-07-02",
                    "days_to_expiry": 5,
                    "atm_strike": 24500,
                    "atm_iv": value,
                    "weekly_atm_iv": value,
                    "monthly_atm_iv": "",
                    "source_snapshot": "",
                }
            )


def _http_json(url: str, payload: dict | None = None) -> dict:
    if payload is None:
        with urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
