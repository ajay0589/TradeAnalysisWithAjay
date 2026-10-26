from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import os
from threading import Thread
from types import SimpleNamespace as NS
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from trading_analysis.brokers.zerodha import merge_candles_csv
from trading_analysis.candles import candle_path
from trading_analysis.live_timing import IST, expected_closed_bar
from trading_analysis.models import Candle
from trading_analysis.stock_research import entry_plan, session_on_or_before
from trading_analysis.stock_swing import DEFAULTS, StockSwingScanner

NOW = datetime(2026, 10, 26, 9, 15, 3, tzinfo=IST)
SIGNAL = {"side": "long", "reference": 100, "stop": 90, "risk": 10, "atr": 5}


def quote(price=100, at=NOW):
    return {"price": price, "quote_time": at.isoformat(), "received_at": at.isoformat()}


def bars(frame, now=NOW, count=90):
    stamp = expected_closed_bar(frame, now)
    rows = []
    for _ in range(count):
        rows.append(Candle(stamp, 100, 101, 99, 100, 1000))
        boundary = stamp.replace(hour=9, minute=15) if frame == "day" else stamp
        stamp = expected_closed_bar(frame, boundary)
    return list(reversed(rows))


@pytest.fixture
def scanner(tmp_path, monkeypatch):
    master = tmp_path / "instruments_NFO.csv"
    for name in ("NFO", "NSE"):
        p = master.with_name(f"instruments_{name}.csv")
        p.write_text("test")
        os.utime(p, (NOW.timestamp(), NOW.timestamp()))
    mapping = tmp_path / "sector.json"
    mapping.write_text(json.dumps({"symbols": {"TEST": {"index_symbol": "NIFTY BANK", "data_file": "NIFTY_BANK.csv"}}}))
    analysis = NS(nfo_instruments_path=master, daily_data_dir=tmp_path/"candles", sector_map_path=mapping,
                  _instruments_for_exchange=lambda _: [{"name": "TEST", "segment": "NFO-FUT", "expiry": "2026-12-31"}],
                  _watchlist_items_by_symbol=lambda: {"TEST": NS(symbol="TEST", exchange="NSE", instrument_type="EQ")})
    for stem in ("TEST", "NIFTY_50", "NIFTY_BANK"):
        merge_candles_csv(candle_path(analysis.daily_data_dir, "day", stem), bars("day"))
    merge_candles_csv(candle_path(analysis.daily_data_dir, "60minute", "TEST"), bars("60minute"))
    client = Mock()
    client.quotes.return_value = {"NSE:TEST": {"last_price": 100, "timestamp": NOW}}
    runner = StockSwingScanner(analysis, tmp_path/"swing.db", lambda: client)
    clock = NS(at=NOW)
    monkeypatch.setattr("trading_analysis.stock_swing.datetime", NS(now=lambda *a: clock.at, fromtimestamp=datetime.fromtimestamp))
    monkeypatch.setattr("trading_analysis.stock_swing.technical", lambda rows: ({"trend_pullback": dict(SIGNAL)}, {"reason": "candidate", "close": 100}))
    monkeypatch.setattr("trading_analysis.stock_swing.context", lambda *a: {"reason": "available", "aligned": "long", "relative": "long", "market": "long", "sector": "long"})
    monkeypatch.setattr(runner.repo.outbox, "start", Mock())
    runner._universe(NOW)
    for stem, frame in (("TEST", "day"), ("TEST", "60minute"), ("NIFTY_50", "day"), ("NIFTY_BANK", "day")):
        runner._verified[(stem, frame)] = expected_closed_bar(frame, NOW).isoformat()
    runner.state["running"] = True
    runner.clock, runner.client = clock, client
    return runner


def test_validation_end_rolls_back_over_weekend_and_holiday():
    assert str(session_on_or_before(NOW.date()-timedelta(days=1))) == "2026-10-23"
    assert str(session_on_or_before(NOW.replace(day=20).date())) == "2026-10-19"


def test_shared_risk_plan_and_live_paper_entry_match(scanner):
    result = scanner.analyze("TEST", "day", quote(), NOW)
    t = scanner.repo.trades()[0]
    assert result[0]["reason"] == "paper_entry"
    assert {k: t[k] for k in entry_plan(SIGNAL, 100, 2)} == entry_plan(SIGNAL, 100, 2)
    assert t["signal_time"] == "2026-10-23T15:30:00+05:30"
    assert t["signal_entry_due"] == "2026-10-26T09:15:00+05:30"
    assert not t["telegram_enabled"]
    assert next(r for r in scanner.repo.decisions(NOW.date().isoformat()) if r["family"] == "trend_pullback")["reason"] == "paper_entry"
    assert not scanner.repo.outbox.history()


def test_duplicate_signals_and_two_frames_do_not_double_positions(scanner):
    scanner.analyze("TEST", "day", quote(), NOW)
    scanner.analyze("TEST", "day", quote(), NOW)
    scanner.analyze("TEST", "60minute", quote(), NOW)
    assert len(scanner.repo.trades()) == 1


@pytest.mark.parametrize("price", [103, 90, float("nan")])
def test_drift_invalid_risk_or_invalid_quote_never_enters(scanner, price):
    scanner.analyze("TEST", "day", quote(price), NOW)
    assert not scanner.repo.trades()


def test_stale_quote_can_recover_but_expired_daily_signal_cannot_enter(scanner):
    scanner.analyze("TEST", "day", quote(at=NOW-timedelta(seconds=40)), NOW)
    assert not scanner.repo.trades()
    assert any(r.get("family") == "trend_pullback" and r["reason"] == "waiting_fresh_quote" for r in scanner.repo.decisions(NOW.date().isoformat()))
    scanner.analyze("TEST", "day", quote(), NOW)
    assert len(scanner.repo.trades()) == 1
    scanner.repo.close(scanner.repo.trades()[0], 100, NOW+timedelta(seconds=1), "test")
    later = NOW+timedelta(minutes=10)
    scanner.analyze("TEST", "day", quote(at=later), later)
    assert len(scanner.repo.trades()) == 1


@pytest.mark.parametrize("reason,aligned,relative", [("available", "short", "long"), ("context_missing_or_stale", None, None), ("available", "long", "short")])
def test_market_sector_and_relative_confirmation_are_required(scanner, monkeypatch, reason, aligned, relative):
    scanner.settings["variant"] = "relative"
    monkeypatch.setattr("trading_analysis.stock_swing.context", lambda *a: {"reason": reason, "aligned": aligned, "relative": relative})
    scanner.analyze("TEST", "day", quote(), NOW)
    assert not scanner.repo.trades()


def test_missing_candle_is_not_forward_filled(scanner, monkeypatch):
    source = scanner._rows("TEST", "day", NOW)
    monkeypatch.setattr(scanner, "_rows", lambda *a: source[:-3]+source[-2:])
    assert scanner.analyze("TEST", "day", quote(), NOW)[0]["reason"] == "candle_gap"
    assert not scanner.repo.trades()


def test_stop_blocks_entries_and_keeps_open_exit_monitoring(scanner):
    scanner.analyze("TEST", "day", quote(), NOW)
    result = scanner.stop()
    assert not result["running"] and result["phase"] == "monitoring_exits"
    later = NOW+timedelta(seconds=15)
    scanner._exits({"TEST": quote(121, later)}, later)
    t = scanner.repo.trades()[0]
    assert t["status"] == "closed" and t["net_r"] > 2 and t["performance_eligible"]
    scanner.analyze("TEST", "60minute", quote(), NOW)
    assert len(scanner.repo.trades()) == 1


def test_real_monitoring_gap_excludes_outcome_not_fake_profit(scanner):
    scanner.analyze("TEST", "day", quote(), NOW)
    later = NOW+timedelta(minutes=2)
    scanner._exits({}, later)
    assert scanner.repo.trades()[0]["monitoring_interrupted"] == "monitoring_gap"
    scanner._exits({"TEST": quote(121, later)}, later)
    assert scanner.repo.trades()[0]["performance_eligible"] is False


def test_persistence_and_export_dates_do_not_filter_history(scanner):
    scanner.analyze("TEST", "day", quote(), NOW)
    assert not scanner.export("2026-10-25", "2026-10-25")["trades"]
    assert len(scanner.status()["trades"]) == 1
    assert len(scanner.export("2026-10-26", "2026-10-26")["trades"]) == 1
    restarted = StockSwingScanner(scanner.analysis, scanner.path)
    assert len(restarted.repo.trades()) == 1
    assert restarted.repo.trades()[0]["monitoring_interrupted"] == "server_restart"
    assert not restarted.state["running"]


def test_refresh_reuses_fresh_caches_and_quote_response_is_serializable(scanner):
    scanner.refresh()
    scanner.client.historical_candles.assert_not_called()
    json.dumps(scanner._quotes({"TEST"}))


def test_refresh_verifies_old_cache_and_never_saves_forming_bars(scanner, monkeypatch):
    from trading_analysis.data_sources.csv_loader import load_candles
    scanner._verified.clear()
    scanner.settings["frame"] = "day"
    scanner.sectors = {}
    monkeypatch.setattr("trading_analysis.stock_swing.resolve_instrument_token", lambda *a: "1")
    forming = Candle(NOW.replace(hour=0, minute=0, second=0), 100, 110, 90, 105, 1000)
    scanner.client.historical_candles.return_value = bars("day") + [forming]
    assert scanner.analyze("TEST", "day", quote(), NOW)[0]["reason"] == "closed_bar_verification_pending"
    scanner.refresh()
    assert scanner.client.historical_candles.call_count == 2
    rows = load_candles(candle_path(scanner.analysis.daily_data_dir, "day", "TEST"))
    assert rows[-1].timestamp.date() == NOW.replace(day=23).date()
    assert scanner._verified[("TEST", "day")] == expected_closed_bar("day", NOW).isoformat()


def test_close_signal_is_not_consumed_before_next_session(scanner):
    friday = NOW.replace(day=23, hour=15, minute=30, second=0)
    result = scanner.analyze("TEST", "day", quote(at=friday), friday)
    assert result[0]["reason"] == "waiting_next_session"
    assert not scanner.repo.trades()
    assert any(r.get("family") == "trend_pullback" and r["reason"] == "waiting_next_session" for r in scanner.repo.decisions("2026-10-23"))
    scanner.analyze("TEST", "day", quote(), NOW)
    assert len(scanner.repo.trades()) == 1


def test_start_requires_acknowledgement_and_is_idempotent(scanner, monkeypatch):
    scanner.state["running"] = False
    with pytest.raises(ValueError, match="Acknowledge"):
        scanner.start()
    with pytest.raises(ValueError):
        scanner.start(acknowledge=True, frame="15minute")
    with pytest.raises(ValueError):
        scanner.start(acknowledge=True, unknown=True)
    monkeypatch.setattr("trading_analysis.stock_swing.threading.Thread", Mock())
    first = scanner.start(acknowledge=True)
    scanner.start(acknowledge=True)
    assert first["running"] and len(scanner._threads) == 2
    assert first["orders_enabled"] is False


def test_missing_master_is_actionable(scanner):
    scanner.analysis.nfo_instruments_path.unlink()
    assert not scanner.readiness()["ready"]
    scanner.state["running"] = False
    with pytest.raises(ValueError, match="Data Ops"):
        scanner.start(acknowledge=True)


def test_waiting_data_reasons_are_persisted_without_polling_duplicates(scanner):
    scanner._verified.clear()
    scanner.analyze("TEST", "day", quote(), NOW)
    scanner.analyze("TEST", "day", quote(), NOW)
    report = scanner.export("2026-10-26", "2026-10-26")
    assert len(report["decisions"]) == 1
    assert report["decisions"][0]["reason"] == "closed_bar_verification_pending"


def test_swing_alert_channel_is_opt_in_and_separate_from_indices(scanner):
    scanner.settings["telegram_enabled"] = True
    scanner.analyze("TEST", "day", quote(), NOW)
    with scanner.repo.connect() as db:
        prefix, message = db.execute("SELECT prefix,message FROM alert_delivery_outbox").fetchone()
    assert prefix == "PULLBACK_" and "UNVALIDATED PAPER" in message and "No order placed" in message


def test_concurrent_duplicate_decision_is_atomic(scanner):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: scanner.analyze("TEST", "day", quote(), NOW), range(4)))
    assert len(scanner.repo.trades()) == 1


def test_swing_api_is_read_only_until_explicit_paper_start(scanner, monkeypatch):
    from trading_analysis.web_app import TradingRequestHandler, ReusableThreadingHTTPServer
    monkeypatch.setattr(TradingRequestHandler, "stock_swing", scanner)
    monkeypatch.setattr(TradingRequestHandler, "pullback_scanner", NS(status=lambda: {"running": False}))
    scanner._threads = [NS(is_alive=lambda: True)]
    scanner.state["running"] = False
    with ReusableThreadingHTTPServer(("127.0.0.1", 0), TradingRequestHandler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base+"/api/stocks/scanner/status") as r:
                assert not json.load(r)["running"]
            req = lambda body: Request(base+"/api/stocks/scanner/start", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with pytest.raises(HTTPError):
                urlopen(req({}))
            with urlopen(req({"acknowledge": True})) as r:
                assert json.load(r)["running"]
            with urlopen(base+"/api/stocks/scanner/export?format=zip&from_date=2026-10-26") as r:
                assert r.read().startswith(b"PK")
        finally:
            server.shutdown()
            worker.join()
