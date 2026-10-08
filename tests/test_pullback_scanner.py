from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from trading_analysis.brokers.zerodha import merge_candles_csv
from trading_analysis.backtesting.engine import backtest_strategy_for_symbol
from trading_analysis.backtesting.exits import entry_price_for
from trading_analysis.backtesting.metrics import calculate_metrics
from trading_analysis.backtesting.models import BacktestConfig
from trading_analysis.live_timing import IST
from trading_analysis.models import Candle, WatchlistItem
from trading_analysis.pullback_context import PullbackContext, daily_regime, filter_settings
from trading_analysis.pullback_scanner import PullbackScanner
from trading_analysis.strategies.base import StrategyDefinition, StrategySignal


NOW = datetime(2026, 10, 8, 11, tzinfo=IST)


def history(end=datetime(2026, 10, 7), down=False):
    dates = []
    while len(dates) < 80:
        if end.weekday() < 5:
            dates.append(end)
        end -= timedelta(days=1)
    result = []
    for i, stamp in enumerate(reversed(dates)):
        value = 200 + (-1 if down else 1) * (i * .6 + (i % 6) * .6)
        result.append(Candle(stamp, value - .2, value + 1, value - 1, value, 1000))
    return result


@pytest.fixture
def service(tmp_path):
    sector = tmp_path / "sectors.json"
    sector.write_text(json.dumps({"symbols": {"TEST": {"sector": "Bank", "index_symbol": "NIFTY BANK", "data_file": "NIFTY_BANK.csv"}}}))
    return SimpleNamespace(daily_data_dir=tmp_path, sector_map_path=sector)


def test_regime_symmetric_and_structure_is_optional():
    assert daily_regime(history(), False)["direction"] == "bullish"
    assert daily_regime(history(down=True), False)["direction"] == "bearish"
    assert daily_regime(history()[:54])["direction"] == "missing"
    assert daily_regime(history(), True)["structure"] == "uptrend"


def test_context_blocks_wrong_sector_missing_and_future_candles(service):
    merge_candles_csv(service.daily_data_dir / "NIFTY_50.csv", history())
    merge_candles_csv(service.daily_data_dir / "NIFTY_BANK.csv", history(down=True))
    ctx = PullbackContext(service, {"market": True, "sector": True, "structure": False})
    assert ctx.check("TEST", "long", NOW)["reason"] == "sector_bearish"
    assert "market_bullish" in ctx.check("TEST", "short", NOW)["reason"]
    assert "sector_missing" in ctx.check("UNKNOWN", "long", NOW)["reason"]
    future = Candle(NOW.replace(hour=0, tzinfo=None), 1, 1, 1, 1, 1000)
    merge_candles_csv(service.daily_data_dir / "NIFTY_50.csv", [future])
    before = PullbackContext(service, {"market": True, "structure": False}).check("TEST", "long", NOW)
    assert before["passed"]
    assert before["evidence"]["market"]["candle_time"].startswith("2026-10-07")


def test_context_stale_and_no_lookahead_at_daily_entry(service):
    merge_candles_csv(service.daily_data_dir / "NIFTY_50.csv", history())
    ctx = PullbackContext(service, {"market": True, "structure": False})
    assert ctx.check("TEST", "long", NOW + timedelta(days=1))["reason"] == "market_stale"
    # Before the Oct 7 close, Oct 6 is the only eligible market candle.
    reading = ctx.check("TEST", "long", NOW - timedelta(days=1))["evidence"]["market"]
    assert reading["candle_time"].startswith("2026-10-06")


@pytest.mark.parametrize("benchmark", ["NIFTY 50", "NIFTY 100", "NIFTY 200", "NIFTY 500"])
def test_benchmark_options(benchmark):
    assert filter_settings({"benchmark": benchmark})["benchmark"] == benchmark


def test_invalid_context_settings():
    with pytest.raises(ValueError): filter_settings({"benchmark": "BAD"})
    with pytest.raises(ValueError): filter_settings({"market": "false"})


def test_disabled_sector_filter_does_not_read_sector_file(service):
    service.sector_map_path.write_text("not valid JSON")
    assert PullbackContext(service).check("TEST", "long", NOW)["passed"]


def fixed_signal(symbol, candles, params):
    return StrategySignal(symbol, "fixed", candles[-1].timestamp.date(), "long", 80, "high",
                          "breakout_stop", 101, 98, None, 98, [], [], {})


def test_backtest_gates_signal_and_entry_and_preserves_no_overlap():
    rows = [Candle(datetime(2026, 1, 5, 9, 15) + timedelta(minutes=15 * i), 101, 102, 100, 101, 1000) for i in range(50)]
    strategy = StrategyDefinition("fixed", "Fixed", "test", "bullish", "15minute", 3, {}, [], fixed_signal)
    config = BacktestConfig("fixed", timeframe="15minute", holding_bars=2)
    blocked = backtest_strategy_for_symbol("TEST", rows, strategy, config, lambda *args: {"passed": False})
    assert blocked["signals"] and not blocked["trades"]
    calls = []
    def gate(*args):
        calls.append(args)
        return {"passed": len(calls) % 2 == 1}
    assert not backtest_strategy_for_symbol("TEST", rows, strategy, config, gate)["trades"]
    result = backtest_strategy_for_symbol("TEST", rows, strategy, config)
    for previous, current in zip(result["trades"], result["trades"][1:]):
        assert current["entry_time"] > previous["exit_time"]


def test_breakout_entry_respects_gap_and_metrics_are_chronological():
    rows = [Candle(datetime(2026, 1, 5), 100, 101, 99, 100, 1000), Candle(datetime(2026, 1, 6), 105, 106, 104, 105, 1000)]
    signal = fixed_signal("TEST", rows[:1], {})
    assert entry_price_for(signal, rows, 0, BacktestConfig("fixed", entry="breakout_stop")) == (1, 105)
    trades = [{"exit_date": f"2026-01-0{i+1}", "return_percent": r} for i, r in enumerate((10, -20, 10))]
    assert calculate_metrics(trades) == calculate_metrics(list(reversed(trades)))


@pytest.fixture
def scanner(service, tmp_path, monkeypatch):
    worker = PullbackScanner(service, tmp_path / "test.db")
    worker._universe_date = NOW.date()
    worker.symbols = ["TEST"]
    monkeypatch.setattr(worker.outbox, "start", Mock())
    monkeypatch.setattr(worker, "_candles", lambda *args: history())
    return worker


def waiting(side="long"):
    return {"id": "PB-TEST" + side, "symbol": "TEST", "side": side, "status": "waiting", "timeframe": "day",
            "signal_time": "2026-10-07T00:00:00+05:30", "trigger": 100, "stop": 98 if side == "long" else 102,
            "strategy": "bullish_pullback" if side == "long" else "bearish_pullback", "score": 75,
            "context": {"passed": True, "reason": "aligned", "evidence": {}}}


def quote(price, now=NOW):
    return {"price": price, "quote_time": now.isoformat(), "received_at": now.isoformat()}


def aligned(passed=True):
    return SimpleNamespace(check=lambda *args: {"passed": passed, "reason": "aligned" if passed else "market_bearish", "evidence": {}})


@pytest.mark.parametrize("side,entry,exit_price", [("long", 100.1, 104.5), ("short", 99.9, 95.5)])
def test_persisted_lifecycle_atomic_dedup_separate_telegram(scanner, side, entry, exit_price):
    row = waiting(side)
    assert scanner.repository.add(row)
    assert not scanner.repository.add(row)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: scanner.process_quote(row, quote(entry), NOW, aligned()), range(3)))
    current = scanner.repository.rows()[0]
    assert current["status"] == "open"
    assert len(scanner.repository.events()) == 1
    assert len(scanner.outbox.history()) == 1
    with scanner.repository.connect() as conn:
        assert conn.execute("SELECT prefix FROM alert_delivery_outbox").fetchone()[0] == "PULLBACK_"
    later = NOW + timedelta(minutes=10)
    # Existing positions keep exits even if the market turns against new entries.
    scanner.process_quote(current, quote(exit_price, later), later, aligned(False))
    assert scanner.repository.rows()[0]["status"] == "closed"
    assert scanner.repository.rows()[0]["open_seconds"] == 600
    assert len(scanner.outbox.history()) == 2


@pytest.mark.parametrize("case", ["stale_quote", "closed_market", "stale_candles", "context", "chase", "stop", "expired"])
def test_no_entry_on_unsafe_data(scanner, monkeypatch, case):
    row = waiting()
    scanner.repository.add(row)
    now, q = NOW, quote(100.1)
    if case == "stale_quote": q = quote(100.1, NOW - timedelta(minutes=1))
    if case == "closed_market": now = NOW.replace(hour=18); q = quote(100.1, now)
    if case == "stale_candles": monkeypatch.setattr(scanner, "_candles", lambda *args: [])
    if case == "chase": q = quote(101)
    if case == "stop": q = quote(97)
    if case == "expired":
        now = NOW + timedelta(days=5)
        scanner._universe_date = now.date()
        q = quote(100.1, now)
        monkeypatch.setattr(scanner, "_candles", lambda *args: history(end=now.replace(tzinfo=None) - timedelta(days=1)))
    scanner.process_quote(row, q, now, aligned(case != "context"))
    assert scanner.repository.rows()[0]["status"] != "open"
    assert not scanner.repository.events()
    assert not scanner.outbox.history()


def test_current_fno_universe_excludes_indexes_and_expired_contracts(scanner):
    scanner.service._instruments_for_exchange = lambda _: [
        {"name": "TEST", "segment": "NFO-FUT", "expiry": "2026-10-29"},
        {"name": "OLD", "segment": "NFO-FUT", "expiry": "2026-09-29"},
        {"name": "NIFTY", "segment": "NFO-FUT", "expiry": "2026-10-29"}]
    scanner.service._watchlist_items_by_symbol = lambda: {"TEST": WatchlistItem("TEST"), "OLD": WatchlistItem("OLD"), "NIFTY": WatchlistItem("NIFTY", instrument_type="INDEX")}
    assert scanner.universe(NOW) == ["TEST"]


def test_stop_prevents_pending_entry(scanner):
    row = waiting()
    scanner.repository.add(row)
    scanner.stop()
    scanner.process_quote(row, quote(100.1), NOW, aligned())
    assert scanner.repository.rows()[0]["status"] == "waiting"
    assert scanner.status()["phase"] == "stopped"


def test_day_rollover_blocks_entries_but_not_exits(scanner):
    row = waiting()
    scanner.repository.add(row)
    scanner._universe_date = NOW.date() - timedelta(days=1)
    scanner.process_quote(row, quote(100.1), NOW, aligned())
    assert scanner.repository.rows()[0]["status"] == "waiting"
    scanner._universe_date = NOW.date()
    scanner.process_quote(row, quote(100.1), NOW, aligned())
    current = scanner.repository.rows()[0]
    scanner._universe_date = None
    scanner.process_quote(current, quote(97), NOW, aligned())
    assert scanner.repository.rows()[0]["status"] == "closed"


def test_stock_removed_from_fno_cannot_create_entry(scanner):
    row = waiting()
    scanner.repository.add(row)
    scanner.symbols = []
    scanner.process_quote(row, quote(100.1), NOW, aligned())
    assert scanner.repository.rows()[0]["discard_reason"] == "no_longer_in_fno_universe"
    assert not scanner.outbox.history()


def test_failed_alert_persistence_rolls_back_trade_transition(scanner):
    import sqlite3
    row = waiting()
    scanner.repository.add(row)
    with scanner.repository.connect() as conn:
        conn.execute("DROP TABLE alert_delivery_outbox")
    with pytest.raises(sqlite3.OperationalError):
        scanner.process_quote(row, quote(100.1), NOW, aligned())
    assert scanner.repository.rows()[0]["status"] == "waiting"
    assert not scanner.repository.events()


def test_restart_preserves_waiting_and_open_setups(scanner):
    row = waiting()
    scanner.repository.add(row)
    scanner.process_quote(row, quote(100.1), NOW, aligned())
    other = PullbackScanner(scanner.service, scanner.repository.path)
    assert other.repository.rows()[0]["status"] == "open"
    assert len(other.repository.events()) == 1
    assert not other.status()["running"]


def test_failed_quotes_do_not_interrupt_cached_discovery(scanner, monkeypatch):
    scanner.repository.add(waiting())
    monkeypatch.setattr("trading_analysis.pullback_scanner.is_market_hours", lambda *args: True)
    monkeypatch.setattr(scanner, "_client", lambda: SimpleNamespace(quotes=Mock(side_effect=RuntimeError("offline"))))
    scanner.check_active(aligned())
    assert "offline" in scanner.status()["errors"][-1]
    assert scanner.repository.rows()[0]["status"] == "waiting"


def test_one_bad_stock_does_not_stop_other_cached_analysis(scanner, monkeypatch):
    monkeypatch.setattr("trading_analysis.pullback_scanner.datetime", SimpleNamespace(now=lambda _: NOW))
    monkeypatch.setattr("trading_analysis.pullback_scanner.is_market_hours", lambda *args: True)
    monkeypatch.setattr(scanner, "_context", lambda: aligned())
    def candles(symbol, frame):
        if symbol == "BAD":
            raise ValueError("bad candle data")
        return history()
    monkeypatch.setattr(scanner, "_candles", candles)
    scanner.symbols = ["BAD", "TEST"]
    scanner.cycle()
    result = scanner.status()
    assert result["completed"] == 2
    assert result["decisions"][0]["reason"] == "bad candle data"
    assert result["decisions"][1]["symbol"] == "TEST"


def test_start_has_independent_workers_and_is_idempotent(scanner, monkeypatch):
    class FakeThread:
        def __init__(self, **kwargs):
            self.alive = False
            self.name = kwargs["name"]
        def start(self): self.alive = True
        def is_alive(self): return self.alive
    monkeypatch.setattr("trading_analysis.pullback_scanner.threading.Thread", FakeThread)
    monkeypatch.setattr(scanner, "_prepare_universe", lambda now: None)
    scanner.start()
    threads = scanner._threads
    scanner.start()
    assert scanner._threads == threads
    assert {t.name for t in threads} == {"pullback-candles", "pullback-checker"}
    scanner.stop()
    with pytest.raises(ValueError, match="stopping"):
        scanner.start()


def test_pullback_api_status_export_and_controls(scanner, monkeypatch):
    import threading
    from urllib.request import Request, urlopen
    from trading_analysis.web_app import ReusableThreadingHTTPServer, TradingRequestHandler
    monkeypatch.setattr(TradingRequestHandler, "pullback_scanner", scanner)
    monkeypatch.setattr(scanner, "start", Mock(side_effect=lambda options: scanner.status()))
    monitors = Mock()
    monkeypatch.setattr(TradingRequestHandler, "all_index_monitor", monitors)
    server = ReusableThreadingHTTPServer(("127.0.0.1", 0), TradingRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for route in ("status", "export"):
            with urlopen(f"{base}/api/pullbacks/{route}") as response:
                assert "setups" in json.load(response)
        for route in ("start", "start-all", "stop"):
            request = Request(f"{base}/api/pullbacks/{route}", data=b'{}', headers={"Content-Type": "application/json"})
            with urlopen(request) as response:
                assert "running" in json.load(response)
        assert scanner.start.call_count == 2
        monitors.start.assert_called_once()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
