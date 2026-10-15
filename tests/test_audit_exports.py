import csv
from datetime import datetime, timedelta, date
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from trading_analysis.audit_exports import date_window, in_window, trade_csv, trade_overlaps
from trading_analysis.live_timing import IST, quote_is_fresh
from trading_analysis.nifty_lab.options import liquid
from trading_analysis.option_history import _fresh_contract


NOW = datetime(2026, 10, 15, 9, 30, 5, tzinfo=IST)


@pytest.mark.parametrize("seconds,expected", [(-4, False), (-3, True), (-1.64, True), (0, True), (20, True), (21, False)])
def test_quote_clock_tolerance_is_bounded(seconds, expected):
    quote = {"price": 23000, "quote_time": (NOW-timedelta(seconds=seconds)).isoformat(), "received_at": NOW.isoformat()}
    assert quote_is_fresh(quote, NOW) is expected
    quote["received_at"] = (NOW-timedelta(seconds=21)).isoformat()
    assert not quote_is_fresh(quote, NOW)


def test_option_clock_tolerance_preserves_stale_and_future_guards():
    row = {"last_price": 100, "bid_price": 99, "ask_price": 101, "oi": 1000, "volume": 10,
           "exchange_timestamp": (NOW+timedelta(seconds=1.64)).isoformat(), "last_trade_time": NOW.isoformat()}
    assert liquid(row, NOW) and _fresh_contract(row, NOW)
    row["exchange_timestamp"] = (NOW+timedelta(seconds=4)).isoformat()
    assert not liquid(row, NOW) and not _fresh_contract(row, NOW)
    row["exchange_timestamp"] = (NOW-timedelta(seconds=181)).isoformat()
    assert not liquid(row, NOW) and not _fresh_contract(row, NOW)


def test_date_windows_validate_and_include_ist_midnight():
    assert date_window("2026-10-15") == ("2026-10-15", "2026-10-15")
    for first, last in [("2026-10-16", "2026-10-15"), ("bad", "2026-10-15"), ("2024-01-01", "2026-10-15")]:
        with pytest.raises(ValueError): date_window(from_date=first, to_date=last)
    assert in_window("2026-10-14T18:30:00+00:00", "2026-10-15", "2026-10-15")
    assert not in_window("2026-10-15T18:30:00+00:00", "2026-10-15", "2026-10-15")
    trade = {"entry_time": "2026-10-01T12:00:00+05:30", "exit_time": "2026-10-16T10:00:00+05:30"}
    assert trade_overlaps(trade, "2026-10-14", "2026-10-15")
    assert not trade_overlaps(trade, "2026-10-17", "2026-10-18")


def test_trade_csv_has_directional_result_and_escapes_formulas():
    rows = list(csv.DictReader(io.StringIO(trade_csv([{"trade_id": "=HYPERLINK(bad)", "symbol": "NIFTY", "direction": "bearish",
                    "entry_price": 100, "exit_price": 110, "stop_level": 105}]))))
    assert rows[0]["trade_id"].startswith("'=")
    assert float(rows[0]["gross_points"]) == -10
    assert float(rows[0]["gross_r"]) == -2
    assert rows[0]["net_r"] == ""  # Do not invent costs or net performance.


def test_range_index_exports_deduplicate_overlapping_trades(tmp_path):
    from trading_analysis.all_index_monitor import AllIndexMonitor
    from trading_analysis.index_scanner import IndexScanRepository
    from trading_analysis.storage.sqlite import NiftyTradeRepository, NiftyAlertRepository, MarketJobRepository
    path = tmp_path / "audit.db"
    repo = NiftyTradeRepository(path)
    created = repo.open_trade({"horizon": "swing", "direction": "bullish", "entry_time": "2026-10-01T10:00:00+05:30",
                              "entry_price": 23000, "entry_timeframe": "60minute", "stop_level": 22900,
                              "target_level": 23200, "risk_points": 100})
    assert created["created"]
    monitor = AllIndexMonitor(SimpleNamespace(trade_repository=repo, alert_repository=NiftyAlertRepository(path),
                                             job_repository=MarketJobRepository(path)),
                              SimpleNamespace(repository=IndexScanRepository(path), _data_status={}), Mock())
    report = monitor.diagnostics(from_date="2026-10-14", to_date="2026-10-15")
    assert len(report["NIFTY"]["trades"]) == 1
    assert report["from_date"] == "2026-10-14" and report["to_date"] == "2026-10-15"


def test_validation_windows_and_no_automatic_promotion():
    from scripts.validate_nifty_setups import windows, run
    periods = windows(date(2026, 10, 15))
    assert len(periods) == 6 and periods[-1] == ("6_month", date(2026, 4, 16))
    # No source data must never produce a positive screen.
    result = run("does-not-exist", date(2026, 10, 15))
    assert not result["promoted"] and not any(r["screen_pass"] for r in result["results"])
