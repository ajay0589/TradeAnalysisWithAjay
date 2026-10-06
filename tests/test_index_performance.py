from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo
import os
import threading
import time

from trading_analysis import network
from trading_analysis.index_scanner import IndexScannerService, IndexScanRepository
from trading_analysis.models import Candle
from trading_analysis.nifty.live_scanner import build_live_entry_signal, closed_candles, evaluate_live_exit
from trading_analysis.nifty.iv_context import build_nifty_iv_context
from trading_analysis.web_services import AnalysisService

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=IST)


def scanner_at(tmp_path):
    return IndexScannerService(AnalysisService(daily_data_dir=tmp_path / "candles"), db_path=tmp_path / "test.db")


def test_analysis_and_exits_do_not_wait_for_download(tmp_path):
    scanner = scanner_at(tmp_path)
    scanner._states["BANKNIFTY"]["running"] = True
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        entered.set()
        release.wait(5)
        return "updated"
    with patch("trading_analysis.index_scanner._scan_window", return_value=True), \
         patch.object(scanner, "_data_tasks", return_value=[("BANKNIFTY", "BANKNIFTY", "15minute")]), \
         patch.object(scanner, "_refresh_candle", side_effect=slow), \
         patch.object(scanner, "_check_exits", return_value=0) as exits:
        scanner._ensure_data_worker()
        worker = scanner._data_thread
        try:
            assert entered.wait(2)
            started = time.monotonic()
            result = scanner.run_once("BANKNIFTY", refresh=False)
            assert result["status"] == "completed"
            assert time.monotonic() - started < 2
            exits.assert_called_once()
            assert scanner.status("BANKNIFTY")["data_service"]["current"]
        finally:
            scanner._states["BANKNIFTY"]["running"] = False
            scanner._events["BANKNIFTY"].set()
            release.set()
            worker.join(3)
        assert not worker.is_alive()


def test_monitor_runs_cache_analysis_not_refresh_batch(tmp_path):
    scanner = scanner_at(tmp_path)
    scanner._states["BANKNIFTY"].update(running=True, interval_seconds=180)
    def analyzed(*args, **kwargs):
        scanner._events["BANKNIFTY"].set()
        scanner._analysis_wake["BANKNIFTY"].set()
    with patch("trading_analysis.index_scanner._scan_window", return_value=True), \
         patch.object(scanner, "_ensure_data_worker"), patch.object(scanner, "run_once", side_effect=analyzed) as run:
        scanner._loop("BANKNIFTY")
    run.assert_called_once_with("BANKNIFTY", refresh=False)


def test_shared_constituents_have_one_refresh_task(tmp_path):
    scanner = scanner_at(tmp_path)
    tasks = scanner._data_tasks(["BANKNIFTY", "SENSEX"])
    keys = [(item, frame) for _, item, frame in tasks]
    assert len(keys) == len(set(keys))
    assert keys.count(("HDFCBANK", "options")) == 1


def test_closed_boundary_scheduling_and_option_cadence(tmp_path):
    scanner = scanner_at(tmp_path)
    scanner._candle_refresh[("BANKNIFTY", "15minute")] = NOW
    scanner._candle_refresh[("BANKNIFTY", "60minute")] = NOW
    scanner._candle_refresh[("BANKNIFTY", "day")] = NOW
    assert not scanner._data_due("BANKNIFTY", "15minute", NOW + timedelta(minutes=2))
    assert scanner._data_due("BANKNIFTY", "15minute", NOW + timedelta(minutes=15))
    assert not scanner._data_due("BANKNIFTY", "60minute", NOW + timedelta(minutes=2))
    assert scanner._data_due("BANKNIFTY", "60minute", NOW.replace(minute=15))
    assert not scanner._data_due("BANKNIFTY", "day", NOW.replace(hour=15, minute=29))
    assert scanner._data_due("BANKNIFTY", "day", NOW.replace(hour=15, minute=30))
    scanner._option_refresh["BANKNIFTY"] = {"refreshed_at": NOW}
    assert not scanner._data_due("BANKNIFTY", "options", NOW + timedelta(seconds=179))
    assert scanner._data_due("BANKNIFTY", "options", NOW + timedelta(seconds=180))


def test_failed_data_source_retries_without_spinning(tmp_path):
    scanner = scanner_at(tmp_path)
    scanner._data_attempts[("BANKNIFTY", "options")] = NOW
    assert not scanner._data_due("BANKNIFTY", "options", NOW + timedelta(seconds=10))
    assert scanner._data_due("BANKNIFTY", "options", NOW + timedelta(seconds=31))


def test_batch_options_share_two_requests_and_keep_partial_failures_isolated(tmp_path):
    master = tmp_path / "instruments_NFO.csv"
    master.write_text("exchange\nNFO\n")
    service = AnalysisService(nfo_instruments_path=master)
    expiry = datetime.now().date() + timedelta(days=7)
    def contracts(_, symbol, **kwargs):
        return [SimpleNamespace(expiry=expiry, kite_key=f"NFO:{symbol}CE")]
    client = MagicMock()
    client.quotes.side_effect = [
        {"NSE:NIFTY BANK": {"last_price": 50000}, "NSE:HDFCBANK": {"last_price": 100}},
        {"NFO:BANKNIFTYCE": {"oi": 1000}, "NFO:HDFCBANKCE": {"oi": 1000}},
    ]
    with patch("trading_analysis.web_services._zerodha_client", return_value=client), \
         patch("trading_analysis.web_services.option_contracts_for_symbol", side_effect=contracts), \
         patch("trading_analysis.web_services.select_strikes_around_spot", side_effect=lambda rows, *args: rows), \
         patch.object(service, "_save_option_snapshot", return_value=(None, {"expiry": str(expiry)})) as save:
        result = service.refresh_option_chain_snapshots(["BANKNIFTY", "HDFCBANK", "BANKNIFTY", "SENSEX"])
    assert client.quotes.call_count == 2
    assert save.call_count == 2
    assert "error" in result["SENSEX"]
    assert result["BANKNIFTY"]["expiry"] == str(expiry)


def test_restart_recovers_dead_owner_but_preserves_live_process(tmp_path):
    path = tmp_path / "runs.db"
    repo = IndexScanRepository(path)
    dead = repo.start_run("SENSEX", 4)
    live = repo.start_run("BANKNIFTY", 4)
    with repo._connect() as conn:
        conn.execute("UPDATE index_scan_runs SET owner_pid = ? WHERE id = ?", (999999, dead))
    with patch("trading_analysis.index_scanner._process_alive", side_effect=lambda pid: pid == os.getpid()):
        restarted = IndexScanRepository(path)
    with restarted._connect() as conn:
        assert conn.execute("SELECT status FROM index_scan_runs WHERE id=?", (dead,)).fetchone()[0] == "interrupted"
        assert conn.execute("SELECT status FROM index_scan_runs WHERE id=?", (live,)).fetchone()[0] == "running"


def candle(hour, minute, low=99, high=102, close=101):
    return Candle(NOW.replace(hour=hour, minute=minute), 100, high, low, close, 1000)


def trade():
    return {"horizon": "intraday", "direction": "bullish", "entry_time": NOW.replace(hour=14, minute=0),
            "last_candle_timestamp": NOW.replace(hour=14, minute=0).isoformat(), "stop_level": 95, "target_level": 110}


def test_exit_replays_missed_stop_instead_of_only_latest_bar():
    rows = [candle(14, 0), candle(14, 15, low=94), candle(14, 30)]
    outcome = evaluate_live_exit(trade(), rows, now=NOW.replace(hour=15))
    assert outcome["reason"] == "stop_loss"
    assert outcome["candle_timestamp"] == rows[1].timestamp


def test_intraday_session_exit_needs_closed_final_bar_and_is_not_repeated():
    rows = [candle(14, 0), candle(15, 0), candle(15, 15)]
    assert evaluate_live_exit(trade(), rows, now=NOW.replace(hour=15, minute=29))["checked_only"]
    outcome = evaluate_live_exit(trade(), rows, now=NOW.replace(hour=15, minute=30))
    assert outcome["reason"] == "session_close"
    checked = {**trade(), "last_candle_timestamp": rows[-1].timestamp.isoformat()}
    assert evaluate_live_exit(checked, rows, now=NOW.replace(hour=15, minute=35))["reason"] == "session_close"
    assert evaluate_live_exit({**checked, "status": "closed"}, rows, now=NOW.replace(hour=15, minute=35)) is None


def test_swing_last_partial_hour_closes_at_session_end():
    rows = [candle(15, 15)]
    assert not closed_candles(rows, "60minute", NOW.replace(hour=15, minute=29))
    assert closed_candles(rows, "60minute", NOW.replace(hour=15, minute=30)) == rows


def test_entry_after_session_close_blocked():
    rows = [Candle(NOW - timedelta(minutes=15 * (59-i)), 100+i, 102+i, 99+i, 101+i, 1000) for i in range(60)]
    checks = {}
    assert build_live_entry_signal({}, [], "intraday", rows, now=NOW.replace(hour=15, minute=30), diagnostics=checks) is None
    assert checks["gate"] == "session_closed"


def test_iv_rank_handles_new_low_and_high():
    history = [{"atm_iv": 10 + i / 10} for i in range(30)]
    with patch("trading_analysis.nifty.iv_context.load_nifty_iv_history", return_value=history):
        assert build_nifty_iv_context(current_atm_iv=5).iv_rank == 0
        assert build_nifty_iv_context(current_atm_iv=30).iv_rank == 100


def test_transport_reuses_verified_pool_and_never_retries_posts():
    from urllib.request import Request
    pool = MagicMock()
    response = pool.request.return_value
    response.status = 200
    response.read.return_value = b"ok"
    with patch.object(network, "_pool", return_value=pool), patch.object(network, "proxy_bypass", return_value=True):
        with network.pooled_urlopen(Request("https://example.com/test", data=b"test")) as result:
            assert result.read() == b"ok"
    assert pool.request.call_args.kwargs["retries"] is False
    assert pool.request.call_args.kwargs["redirect"] is False
    response.release_conn.assert_called()
    context = network.https_context()
    assert context.check_hostname


def test_failed_source_remains_visible_in_payload(tmp_path):
    scanner = scanner_at(tmp_path)
    scanner._record_data("BANKNIFTY", "options", "timeout", time.monotonic())
    state = scanner.status("BANKNIFTY")
    assert state["data_service"]["failures"] == 1
    assert state["data_service"]["sources"]["BANKNIFTY:options"]["error"] == "timeout"


def test_batch_skips_source_already_refreshing(tmp_path):
    scanner = scanner_at(tmp_path)
    lock = scanner._source_lock("options", "HDFCBANK")
    lock.acquire()
    try:
        with patch.object(scanner.analysis, "refresh_option_chain_snapshots", return_value={"BANKNIFTY": {"error": "unavailable"}}) as fetch:
            scanner._refresh_option_batch([("BANKNIFTY", "HDFCBANK", "options"), ("BANKNIFTY", "BANKNIFTY", "options")], time.monotonic())
        fetch.assert_called_once_with(["BANKNIFTY"])
        assert lock.locked()
    finally:
        lock.release()


def test_successful_download_without_required_close_retries(tmp_path):
    master = tmp_path / "instruments_NSE.csv"
    master.write_text("exchange,tradingsymbol,instrument_token\nNSE,NIFTY BANK,123\n")
    scanner = IndexScannerService(AnalysisService(daily_data_dir=tmp_path / "candles", nse_instruments_path=master), db_path=tmp_path / "test.db")
    client = MagicMock()
    client.historical_candles.return_value = [candle(11, 15)]
    with patch("trading_analysis.index_scanner._now", return_value=NOW), \
         patch("trading_analysis.index_scanner._zerodha_client", return_value=client):
        assert scanner._refresh_candle("BANKNIFTY", "15minute") == "stale"
        assert ("BANKNIFTY", "15minute") not in scanner._candle_refresh
        client.historical_candles.return_value = [candle(11, 45)]
        assert scanner._refresh_candle("BANKNIFTY", "15minute") == "updated"


def test_atomic_option_write_keeps_previous_file_on_failure(tmp_path):
    from trading_analysis.analysis import options
    path = tmp_path / "snapshot.csv"
    path.write_text("previous complete snapshot")
    with patch.object(options, "_write_option_chain_snapshot", side_effect=RuntimeError("interrupted")):
        try:
            options.write_option_chain_snapshot(path, None)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected write failure")
    assert path.read_text() == "previous complete snapshot"
    assert not list(tmp_path.glob("*.tmp"))


def test_http_error_body_available_without_retry():
    from urllib.request import Request
    from urllib.error import HTTPError
    pool = MagicMock()
    response = pool.request.return_value
    response.status = 401
    response.read.return_value = b'{"error":"invalid token"}'
    with patch.object(network, "_pool", return_value=pool), patch.object(network, "proxy_bypass", return_value=True):
        try:
            network.pooled_urlopen(Request("https://example.com/test"))
        except HTTPError as exc:
            assert exc.code == 401
            assert b"invalid token" in exc.read()
            exc.close()
        else:
            raise AssertionError("Expected HTTP error")
    assert pool.request.call_count == 1
    response.release_conn.assert_called_once()


def test_stale_snapshot_pair_still_blocks_after_performance_change(tmp_path):
    scanner = scanner_at(tmp_path)
    scanner._option_refresh["BANKNIFTY"] = {"previous": "present", "refreshed_at": NOW,
        "expiry": "2026-10-29", "rows": ([{"expiry": "2026-10-29", "snapshot_time": NOW.isoformat()}],
        [{"expiry": "2026-10-29", "snapshot_time": (NOW - timedelta(minutes=21)).isoformat()}])}
    with patch("trading_analysis.index_scanner._now", return_value=NOW):
        assert scanner._footprint("BANKNIFTY", 50000)["bias"] == "stale"
