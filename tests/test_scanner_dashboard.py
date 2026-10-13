from contextlib import closing
from datetime import datetime, timedelta
import json
import sqlite3
from threading import RLock, Thread
from types import SimpleNamespace as NS
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from trading_analysis.index_scanner import IndexScanRepository
from trading_analysis.live_quotes import LIVE_QUOTES
from trading_analysis.live_timing import IST
from trading_analysis.nifty_lab.repository import LabRepository
from trading_analysis.pullback_scanner import PullbackScanner
from trading_analysis.scanner_dashboard import ScannerDashboard, normalize
from trading_analysis.storage.sqlite import NiftyTradeRepository, KrishnaPurpleAlertRepository
from trading_analysis.trade_context import capture_vix, result_mark

NOW = datetime(2026, 10, 13, 11, tzinfo=IST)


def quote(value=15, at=NOW):
    return dict(price=value, quote_time=at.isoformat(), received_at=at.isoformat(), source="fixture")


@pytest.mark.parametrize("side,price,expected", [("bullish", 110, 10), ("bearish", 110, -10), ("long", 90, -10), ("short", 90, 10)])
def test_result_signs_and_quote_freshness(side, price, expected):
    live = result_mark(100, side, "open", quote=quote(price), now=NOW)
    assert live["points"] == live["percent"] == expected
    assert live["basis"] == "live"
    assert result_mark(100, side, "open", quote=quote(price), now=NOW+timedelta(seconds=31))["percent"] is None
    assert result_mark(100, side, "closed", price, now=NOW)["percent"] == expected


@pytest.mark.parametrize("q", [{}, quote(at=NOW-timedelta(seconds=61)), quote(at=NOW+timedelta(seconds=1)), quote(float("nan")), quote(0)])
def test_vix_does_not_backfill_stale_future_or_invalid_prices(q):
    assert capture_vix(NOW, q)["value"] is None


def test_vix_is_point_in_time_and_marks_are_read_only(monkeypatch):
    monkeypatch.setattr(LIVE_QUOTES, "get", lambda symbol: quote(15))
    saved = capture_vix(NOW)
    monkeypatch.setattr(LIVE_QUOTES, "get", lambda symbol: quote(19, NOW+timedelta(minutes=5)))
    assert saved["value"] == 15
    legacy = normalize(dict(id="old", status="closed", entry_time=NOW.isoformat(), entry_price=100, exit_price=101), "NIFTY", NOW)
    assert legacy["india_vix_entry"] is None
    assert legacy["result"]["percent"] == 1


def test_stock_result_uses_exchange_quote_time_not_checker_time(monkeypatch):
    monkeypatch.setattr(LIVE_QUOTES, "get", lambda s: {})
    row = dict(id="stock", symbol="TEST", status="open", side="short", entry_time=NOW.isoformat(),
               entry_price=100, last_price=90, last_checked=NOW.isoformat())
    assert normalize(row, "pullbacks", NOW)["result"]["percent"] is None
    row["last_quote"] = quote(90, NOW-timedelta(seconds=31))
    assert normalize(row, "pullbacks", NOW)["result"]["percent"] is None
    row["last_quote"] = quote(90)
    assert normalize(row, "pullbacks", NOW)["result"]["percent"] == 10


def test_optional_vix_master_does_not_break_existing_index_stream(monkeypatch):
    import kiteconnect
    from pathlib import Path
    from trading_analysis.live_quotes import LiveQuoteService
    from unittest.mock import Mock
    ticker = Mock()
    monkeypatch.setattr(kiteconnect, "KiteTicker", lambda *a, **kw: ticker)
    monkeypatch.setattr("trading_analysis.web_services._zerodha_client", lambda: NS(api_key="test", access_token="test"))
    monkeypatch.setattr(Path, "exists", lambda p: True)
    monkeypatch.setattr("trading_analysis.brokers.zerodha.load_instruments_csv", lambda p: [])
    def resolve(rows, exchange, name):
        if name == "INDIA VIX": raise ValueError("Missing optional VIX")
        return {"NIFTY 50": 1, "NIFTY BANK": 2, "SENSEX": 3}[name]
    monkeypatch.setattr("trading_analysis.brokers.zerodha.resolve_instrument_token", resolve)
    stream = LiveQuoteService()
    stream._start_stream()
    assert stream._tokens == {1:"NIFTY", 2:"BANKNIFTY", 3:"SENSEX"}
    ticker.connect.assert_called_once_with(threaded=True)


def signal():
    return dict(horizon="intraday", direction="bullish", entry_price=100, entry_time=NOW.isoformat(),
                entry_candle_timestamp=NOW.isoformat(), entry_timeframe="15minute", risk_points=1,
                stop_level=99, target_level=102, score=80)


def test_index_and_nifty_persist_entry_and_exit_vix_without_duplicate_mutation(tmp_path, monkeypatch):
    later = NOW+timedelta(minutes=10)
    monkeypatch.setattr(LIVE_QUOTES, "get", lambda s: quote(15))
    monkeypatch.setattr("trading_analysis.index_scanner._iso", lambda: NOW.isoformat())
    repo = IndexScanRepository(tmp_path / "trades.db")
    entries = [repo.open_trade(s, signal()) for s in ("BANKNIFTY", "SENSEX")]
    nifty = NiftyTradeRepository(tmp_path / "trades.db")
    n = nifty.open_trade(signal())["trade"]
    monkeypatch.setattr(LIVE_QUOTES, "get", lambda s: quote(20, later))
    monkeypatch.setattr("trading_analysis.index_scanner._iso", lambda: later.isoformat())
    assert repo.open_trade("BANKNIFTY", signal()) is None
    assert nifty.open_trade(signal())["trade"]["metadata"]["india_vix_entry"]["value"] == 15
    for t in entries:
        closed = repo.close_trade(t["trade_id"], dict(price=102, reason="target"))
        meta = json.loads(closed["context_json"])
        assert meta["india_vix_entry"]["value"] == 15
        assert meta["india_vix_exit"]["value"] == 20
    closed = nifty.close_trade(n["trade_id"], exit_time=later.isoformat(), exit_price=102, exit_reason="target")
    assert closed["metadata"]["india_vix_entry"]["value"] == 15
    assert closed["metadata"]["india_vix_exit"]["value"] == 20


def test_lab_and_purple_capture_vix_separately(tmp_path, monkeypatch):
    monkeypatch.setattr(LIVE_QUOTES, "get", lambda s: quote(15))
    monkeypatch.setattr("trading_analysis.storage.sqlite._now", lambda: NOW.isoformat())
    lab = LabRepository(tmp_path / "lab.db")
    d = dict(id="d1", setup="Nifty_Setup1", checked_at=NOW.isoformat(), variants={})
    entry = {**signal(), "cost_bps_per_side": 2, "risk_points": 1, "telegram_enabled": False}
    trade = lab.record(d, {"technical": entry})[0]
    purple = KrishnaPurpleAlertRepository(tmp_path / "purple.db")
    p = purple.open_entry_alert(dict(symbol="TEST", purple_timeframe="week", score=80), dict(timeframe="30minute", close=100), "early")["trade"]
    later = NOW+timedelta(minutes=5)
    monkeypatch.setattr(LIVE_QUOTES, "get", lambda s: quote(19, later))
    monkeypatch.setattr("trading_analysis.storage.sqlite._now", lambda: later.isoformat())
    l = lab.close(trade, 102, later, "target")
    p = purple.close_trade_alert(p, dict(timeframe="30minute", close=102))["trade"]
    for row in (l, p["metadata"]):
        assert row["india_vix_entry"]["value"] == 15
        assert row["india_vix_exit"]["value"] == 19


def test_pullback_captures_vix_without_changing_rules(tmp_path, monkeypatch):
    from tests.test_pullback_scanner import waiting, aligned, history
    scanner = PullbackScanner(NS(), tmp_path / "pullback.db")
    monkeypatch.setattr(scanner, "_candles", lambda *args: history(NOW.replace(hour=0, tzinfo=None)-timedelta(days=1)))
    monkeypatch.setattr(scanner.outbox, "start", lambda: None)
    scanner._universe_date, scanner.symbols = NOW.date(), ["TEST"]
    monkeypatch.setattr(LIVE_QUOTES, "get", lambda s: quote(15))
    row = waiting()
    row["signal_time"] = (NOW-timedelta(days=1)).replace(hour=0).isoformat()
    scanner.repository.add(row)
    scanner.process_quote(row, quote(100.1), NOW, aligned())
    row = scanner.repository.rows()[0]
    assert row["status"] == "open"
    later = NOW+timedelta(minutes=1)
    monkeypatch.setattr(LIVE_QUOTES, "get", lambda s: quote(18, later))
    scanner.process_quote(row, quote(105, later), later, aligned())
    row = scanner.repository.rows()[0]
    assert row["india_vix_entry"]["value"] == 15
    assert row["india_vix_exit"]["value"] == 18


@pytest.fixture
def dashboard(tmp_path):
    path = tmp_path / "data.db"
    # Minimal legacy schemas exercise read-only compatibility without migrations.
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("CREATE TABLE index_scan_trades(trade_id, symbol, status, entry_time, exit_time, entry_price, exit_price, direction, context_json)")
        for i in range(235):
            c.execute("INSERT INTO index_scan_trades VALUES(?,?,?,?,?,?,?,?,?)", (f"IX-{i:03}", "BANKNIFTY", "closed" if i % 2 else "open", "2026-10-12T18:30:00+00:00", "2026-10-14T10:00:00+05:30" if i % 2 else None, 100, 110 if i % 2 else None, "bullish", "{}"))
        c.execute("CREATE TABLE lab_trades(status, body)")
        c.execute("INSERT INTO lab_trades VALUES('open', ?)", (json.dumps(dict(id="lab1", setup="Nifty_Setup1", status="open", entry_time=NOW.isoformat(), entry_price=100)),))
        c.execute("CREATE TABLE pullback_setups(status, body)")
        c.execute("INSERT INTO pullback_setups VALUES('open', ?)", (json.dumps(dict(id="p1", symbol="TEST", side="short", status="open", entry_time=NOW.isoformat(), entry_price=100)),))
    h = NS(index_scanner_service=NS(repository=NS(db_path=path)), nifty_auto_service=NS(trade_repository=NS(db_path=path)),
           nifty_lab=NS(path=path, _lock=RLock(), states={f"Nifty_Setup{i}": dict(enabled=i == 1, phase="waiting") for i in range(1, 6)}),
           pullback_scanner=NS(repository=NS(path=path), _lock=RLock(), state=dict(running=False)),
           service=NS(_purple_monitor_lock=RLock(), _purple_monitor_state=dict(running=False)), purple_db_path=path,
           all_index_monitor=NS(status=lambda: dict(scanners={s: dict(running=s == "NIFTY") for s in ("NIFTY", "BANKNIFTY", "SENSEX")})))
    return ScannerDashboard(h)


def test_all_history_filtered_before_pagination_and_ist_inclusive_dates(dashboard):
    result = dashboard.ledger(source="BANKNIFTY", status="closed", from_date="2026-10-13", to_date="2026-10-13")
    assert result["total"] == 117 and len(result["trades"]) == 10
    assert result["pages"] == 12
    assert dashboard.ledger(source="BANKNIFTY", page=24)["trades"][-1]["id"] == "IX-000"
    assert dashboard.ledger(source="BANKNIFTY", status="closed", page=99)["page"] == 12
    assert dashboard.ledger(source="BANKNIFTY", date_basis="exit", from_date="2026-10-13", to_date="2026-10-13")["total"] == 0
    assert dashboard.ledger(source="BANKNIFTY", date_basis="exit", from_date="2026-10-14", to_date="2026-10-14")["total"] == 117
    assert dashboard.ledger(source="SENSEX")["total"] == 0
    assert dashboard.ledger(source="Nifty_Setup1")["total"] == 1
    assert dashboard.ledger(source="all", status="open")["total"] == 120


@pytest.mark.parametrize("params", [dict(status="bad"), dict(source="oops"), dict(date_basis="bad"), dict(page=0), dict(from_date="oops"), dict(from_date="2026-10-14", to_date="2026-10-13")])
def test_invalid_filters(dashboard, params):
    with pytest.raises(ValueError): dashboard.ledger(**params)


def test_catalog_is_read_only_and_has_all_scanners(dashboard, monkeypatch):
    monkeypatch.setattr(LIVE_QUOTES, "acquire", lambda *_: pytest.fail("Dashboard must not start quotes"))
    d = dashboard.status()
    assert d["running_count"] == 2 and d["open_trade_count"] == 120
    assert len(d["scanners"]) == 16
    assert all("Intraday" in s["detail"] for s in d["scanners"] if s["id"].startswith("Nifty_Setup"))


def test_dashboard_http_payloads_and_invalid_filter_response(dashboard, monkeypatch):
    from trading_analysis.web_app import TradingRequestHandler, ReusableThreadingHTTPServer
    monkeypatch.setattr("trading_analysis.web_app.ScannerDashboard", lambda _: dashboard)
    with ReusableThreadingHTTPServer(("127.0.0.1", 0), TradingRequestHandler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base+"/api/trade-ledger?source=BANKNIFTY&status=closed&page=2") as r:
                data = json.load(r)
                assert data["page"] == 2 and len(data["trades"]) == 10
                assert data["trades"][0]["result"]["percent"] == 10
            with urlopen(base+"/api/scanner-dashboard") as r:
                assert json.load(r)["running_count"] == 2
            with pytest.raises(HTTPError) as error: urlopen(base+"/api/trade-ledger?status=oops")
            assert error.value.code == 400
        finally:
            server.shutdown()
            worker.join(3)
