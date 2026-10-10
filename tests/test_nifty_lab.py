from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
import io
import csv
import json
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import threading
import zipfile

import pytest

from trading_analysis.brokers.zerodha import merge_candles_csv
from trading_analysis.candles import candle_path
from trading_analysis.live_timing import IST, as_ist, expected_closed_bar
from trading_analysis.models import Candle
from trading_analysis.nifty_lab import setups
from trading_analysis.nifty_lab.options import compare, option_evidence, quadrant
from trading_analysis.nifty_lab.repository import LabRepository, comparisons
from trading_analysis.nifty_lab.service import NiftySetupLab

NOW = datetime(2026, 10, 8, 11, 0, 5, tzinfo=IST)


def bars(frame, count=90, now=NOW):
    stamp = expected_closed_bar(frame, now)
    result = []
    for _ in range(count):
        result.append(Candle(stamp, 100, 101, 99, 100, 1000))
        stamp = expected_closed_bar(frame, stamp)
    return list(reversed(result))


def replace_bar(row, open=100, high=105, low=99, close=104):
    return Candle(row.timestamp, open, high, low, close, 1000)


@pytest.mark.parametrize("setup", list(setups.SETUPS))
@pytest.mark.parametrize("bearish", [False, True])
def test_five_rules_are_symmetric_and_closed_candle_only(monkeypatch, setup, bearish):
    sources = {"5minute": bars("5minute"), "15minute": bars("15minute"), "day": bars("day", 2)}
    frame = setups.SETUPS[setup]["frame"]
    sources[frame][-1] = replace_bar(sources[frame][-1])
    # Isolate rule composition from the separately tested indicator library.
    def ema(values, period):
        result = (101 if len(values) == 89 else 103) if period == 20 else 102
        return 200 - result if bearish else result
    monkeypatch.setattr(setups, "ema", ema)
    monkeypatch.setattr(setups, "rsi", lambda values: 40 if bearish else 60)
    monkeypatch.setattr(setups, "atr", lambda _: 10)
    if setup in {"Nifty_Setup3", "Nifty_Setup5"}:
        sources["15minute"][-1] = replace_bar(sources["15minute"][-1])
    if setup == "Nifty_Setup4":
        sources[frame][-2] = replace_bar(sources[frame][-2], open=100, high=101, low=89, close=90)
        sources[frame][-1] = replace_bar(sources[frame][-1], open=98, high=101, low=97, close=100)
        monkeypatch.setattr(setups, "ema", lambda *_: 100)
        monkeypatch.setattr(setups, "rsi", lambda values: (80 if len(values) == 89 else 60) if bearish else (20 if len(values) == 89 else 40))
    if setup == "Nifty_Setup5":
        sources[frame][-2] = replace_bar(sources[frame][-2], high=106, close=105)
        sources[frame][-1] = replace_bar(sources[frame][-1], open=104, high=106, low=103.9, close=105)
        sources["day"][-1] = replace_bar(sources["day"][-1], high=104, low=90, close=100)
    if bearish:
        sources = {f: [Candle(c.timestamp, 200-c.open, 200-c.low, 200-c.high, 200-c.close, c.volume) for c in rows] for f, rows in sources.items()}
    result = setups.technical_read(setup, sources, NOW)
    assert result["direction"] == ("bearish" if bearish else "bullish"), result
    assert result["closed_at"] == NOW.replace(second=0).isoformat()
    # An in-progress candle cannot alter the decision.
    sources[frame].append(Candle(NOW.replace(second=0), 100, 1000, 1, 1, 1))
    assert setups.technical_read(setup, sources, NOW) == result


def test_missing_stale_invalid_gap_and_warmup():
    rows = bars("15minute")
    assert setups.technical_read("Nifty_Setup1", {"15minute": rows[:-1]}, NOW)["reason"] == "15minute_stale_or_missing"
    assert setups.technical_read("Nifty_Setup1", {"15minute": rows[-30:]}, NOW)["reason"] == "indicator_warmup"
    assert setups.technical_read("Nifty_Setup1", {"15minute": rows[:-3] + rows[-2:]}, NOW)["reason"] == "15minute_candle_gap"
    rows[-1] = replace_bar(rows[-1], close=float("nan"))
    assert setups.technical_read("Nifty_Setup1", {"15minute": rows}, NOW)["reason"] == "15minute_invalid_ohlc"


def snapshot(now=NOW, step=0, direction="bullish", expiry="2026-10-13"):
    rows = []
    for kind in ("CE", "PE"):
        for strike in range(4):
            sign = 1 if (kind == "CE") == (direction == "bullish") else -1
            price = 100 + sign * step
            rows.append({"tradingsymbol": f"NIFTY{strike}{kind}", "strike": str(23000 + 50 * strike), "option_type": kind,
                         "last_price": price, "oi": 10000 + step * 100, "bid_price": price-.1, "ask_price": price+.1,
                         "volume": 1000+step*20, "exchange_timestamp": now.isoformat(), "last_trade_time": now.isoformat(),
                         "received_at": now.isoformat(), "expiry": expiry})
    return {"received_at": now.isoformat(), "expiry": expiry, "rows": rows, "id": step}


@pytest.mark.parametrize("price,oi,label", [(1,1,"long_buildup"), (-1,1,"short_buildup"), (1,-1,"short_covering"), (-1,-1,"long_unwinding"), (0,1,"unchanged")])
def test_quadrants(price, oi, label):
    assert quadrant(price, oi) == label


@pytest.mark.parametrize("direction", ["bullish", "bearish"])
def test_price_oi_both_contract_sides_and_multiple_windows(direction):
    history = [snapshot(NOW-timedelta(minutes=15-i), i, direction) for i in range(16)]
    evidence = option_evidence(history, NOW)
    assert evidence["bias"] == direction
    assert evidence["state"] == "ready"
    assert all(w["matched"] == 8 for w in evidence["windows"].values())
    assert evidence["windows"]["3"]["previous_snapshot_id"] == 12
    assert len(evidence["windows"]["6"]["contracts"]) == 8
    future = snapshot(NOW+timedelta(minutes=1), 99, "bearish")
    assert option_evidence(history + [future], NOW) == evidence


@pytest.mark.parametrize("problem", ["expiry", "stale", "wide_spread", "no_volume", "no_oi", "reset", "one_side", "few_contracts", "timestamp"])
def test_option_gates(problem):
    old, new = snapshot(NOW-timedelta(minutes=3)), snapshot(NOW, 5)
    if problem == "expiry": new["expiry"] = "2026-10-20"
    if problem == "stale":
        for r in new["rows"]: r["exchange_timestamp"] = (NOW-timedelta(minutes=4)).isoformat()
    if problem == "wide_spread":
        for r in new["rows"]: r["ask_price"] = 200
    if problem == "no_volume":
        for r in new["rows"]: r["volume"] = 1000
    if problem == "no_oi":
        for r in new["rows"]: r["oi"] = 10000
    if problem == "reset": new["rows"][0]["volume"] = 1
    if problem == "one_side":
        for r in new["rows"]:
            if r["option_type"] == "PE": r.update(last_price=100, bid_price=99.9, ask_price=100.1)
    if problem == "few_contracts": new["rows"] = new["rows"][:5]
    if problem == "timestamp": new["rows"][0]["last_trade_time"] = None
    assert compare(new, old)["bias"] in {"unavailable", "unclear"}


def test_option_warmup_age_session_expiry_and_15m_conflict():
    history = [snapshot(NOW-timedelta(minutes=15-i), i) for i in range(16)]
    assert option_evidence(history[-3:], NOW)["state"] == "warming_up"
    assert option_evidence(history, NOW+timedelta(minutes=3))["state"] == "stale"
    assert option_evidence([snapshot(NOW, 5, expiry="2026-10-07")], NOW)["reason"] == "expired_contracts"
    assert compare(snapshot(NOW, 5), snapshot(NOW-timedelta(days=1)))["reason"] == "session_or_expiry_mismatch"
    # Same recent trend, opposing 15m premium direction.
    history[0] = snapshot(NOW-timedelta(minutes=15), 30)
    for r in history[0]["rows"]: r["volume"] = 1000
    assert option_evidence(history, NOW)["bias"] == "unclear"


def quote(price=100, now=NOW):
    return {"price": price, "quote_time": now.isoformat(), "received_at": now.isoformat()}


def signal(setup, sources, now):
    frame = setups.SETUPS[setup]["frame"]
    return {"ready": True, "direction": "bullish", "reason": "technical_signal",
            "bar_time": expected_closed_bar(frame, now).isoformat(), "closed_at": now.replace(second=0).isoformat(),
            "indicators": {"close": 100, "atr14": 10, "ema20": 98, "ema50": 95, "rsi14": 60}}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    analysis = SimpleNamespace(daily_data_dir=tmp_path / "candles", nfo_instruments_path=tmp_path / "instruments_NFO.csv")
    quotes = SimpleNamespace(get=Mock(return_value=quote()), acquire=Mock(), release=Mock())
    worker = NiftySetupLab(analysis, tmp_path / "lab.db", quotes)
    monkeypatch.setattr(worker, "_master", Mock())
    monkeypatch.setattr(worker, "_ensure_workers", Mock())
    monkeypatch.setattr(worker.repo.outbox, "start", Mock())
    monkeypatch.setattr(worker, "evidence", Mock(return_value={"state": "ready", "bias": "bullish"}))
    monkeypatch.setattr("trading_analysis.nifty_lab.service.technical_read", signal)
    worker.start()
    return worker


def test_all_ten_ledgers_paired_once_concurrent_and_persistent(lab):
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda k: lab.tick(k, NOW), list(setups.SETUPS) * 3))
    trades = lab.repo.trades()
    assert len(trades) == 10
    assert len(lab.repo.decisions(NOW.date().isoformat())) == 5
    assert all(t["entry_price"] == 100 and t["risk_points"] == 10 and t["stop_level"] == 90 for t in trades)
    assert LabRepository(lab.path).seen(trades[0]["decision_id"])
    assert all(r["paired_signals"] == 1 for r in comparisons(trades, lab.repo.decisions("2026-10-08"), "2026-10-08"))
    assert lab.repo.outbox.history("2026-10-08") == []


def test_no_options_does_not_block_technical_and_never_backfills_combined(lab):
    lab.evidence.return_value = {"state": "warming_up", "reason": "need_history", "bias": "unavailable"}
    lab.tick("Nifty_Setup1", NOW)
    assert [t["variant"] for t in lab.repo.trades()] == ["technical"]
    assert lab.repo.decisions("2026-10-08")[0]["variants"]["combined"] == "options_need_history"
    lab.evidence.return_value = {"state": "ready", "bias": "bullish"}
    lab.tick("Nifty_Setup1", NOW)
    assert len(lab.repo.trades()) == 1


@pytest.mark.parametrize("problem", ["quote_stale", "quote_preclose", "drift", "stale_candles", "late_signal", "outside_hours"])
def test_no_invalid_entries(lab, monkeypatch, problem):
    when = NOW
    if problem == "quote_stale": lab.quotes.get.return_value = quote(now=NOW-timedelta(minutes=1))
    if problem == "quote_preclose": lab.quotes.get.return_value = quote(now=NOW-timedelta(seconds=10))
    if problem == "drift": lab.quotes.get.return_value = quote(110)
    if problem == "stale_candles": monkeypatch.setattr("trading_analysis.nifty_lab.service.technical_read", lambda *args: {"ready": False, "reason": "stale"})
    if problem == "late_signal":
        reading = signal("Nifty_Setup1", {}, NOW)
        reading["closed_at"] = (NOW-timedelta(minutes=3)).isoformat()
        monkeypatch.setattr("trading_analysis.nifty_lab.service.technical_read", lambda *args: reading)
    if problem == "outside_hours": when = NOW.replace(hour=16)
    lab.tick("Nifty_Setup1", when)
    assert not lab.repo.trades()


def test_waiting_quote_can_retry_same_bar_then_exit_independently(lab):
    lab.quotes.get.return_value = {}
    lab.tick("Nifty_Setup1", NOW)
    assert not lab.repo.decisions("2026-10-08")
    lab.quotes.get.return_value = quote()
    lab.tick("Nifty_Setup1", NOW)
    later = NOW+timedelta(seconds=20)
    lab.quotes.get.return_value = quote(121, later)
    lab.evidence.side_effect = RuntimeError("options offline")
    lab.tick("Nifty_Setup1", later)
    rows = lab.repo.trades()
    assert all(t["status"] == "closed" and t["exit_price"] == 121 and t["performance_eligible"] for t in rows)
    assert all(t["open_seconds"] == 20 and t["net_r"] < 2.1 for t in rows)


def test_option_exception_and_one_setup_failure_isolated(lab):
    lab.evidence.side_effect = RuntimeError("offline")
    lab.tick("Nifty_Setup1", NOW)
    lab._error("Nifty_Setup2", RuntimeError("test"))
    lab.tick("Nifty_Setup3", NOW)
    assert {t["setup"] for t in lab.repo.trades()} == {"Nifty_Setup1", "Nifty_Setup3"}


def test_stop_generation_prevents_inflight_entry_and_marks_interruptions(lab):
    old = dict(lab.states["Nifty_Setup1"])
    lab.stop("Nifty_Setup1")
    lab.tick("Nifty_Setup1", NOW, old)
    assert not lab.repo.trades()
    lab.start("Nifty_Setup1")
    lab.tick("Nifty_Setup1", NOW)
    lab.stop("Nifty_Setup1")
    lab.start("Nifty_Setup1")
    later = NOW+timedelta(minutes=1)
    lab.quotes.get.return_value = quote(121, later)
    lab.tick("Nifty_Setup1", later)
    assert all(not t["performance_eligible"] for t in lab.repo.trades())


def test_exits_stale_quotes_session_and_holding(lab):
    lab.tick("Nifty_Setup1", NOW)
    lab.check_exits("Nifty_Setup1", NOW+timedelta(hours=2), quote(121), 1)
    assert all(t["status"] == "open" for t in lab.repo.trades())
    when = NOW+timedelta(minutes=90)
    lab.check_exits("Nifty_Setup1", when, quote(105, when), 1)
    assert all(t["exit_reason"] == "holding_limit" for t in lab.repo.trades())
    lab.tick("Nifty_Setup2", NOW)
    when = NOW.replace(hour=15, minute=20)
    lab.check_exits("Nifty_Setup2", when, quote(105, when), 1)
    assert all(t["exit_reason"] == "session_exit" for t in lab.repo.trades(setup="Nifty_Setup2"))


def test_missed_session_recovery_never_uses_todays_quote(lab):
    lab.tick("Nifty_Setup1", NOW)
    close = NOW.replace(hour=15, minute=25, second=0)
    merge_candles_csv(candle_path(lab.analysis.daily_data_dir, "5minute", "NIFTY_50"), [Candle(close, 100, 110, 95, 102, 100)])
    tomorrow = NOW+timedelta(days=1)
    lab.check_exits("Nifty_Setup1", tomorrow, quote(121, tomorrow), 1)
    rows = lab.repo.trades()
    assert all(t["exit_price"] == 102 and not t["performance_eligible"] and t["exit_time"].startswith("2026-10-08T15:30") for t in rows)


def test_transactional_outbox_only_lab_prefix_and_no_duplicates(lab):
    lab.states["Nifty_Setup1"]["telegram_enabled"] = True
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: lab.tick("Nifty_Setup1", NOW), range(8)))
    assert len(lab.repo.outbox.history("2026-10-08")) == 2
    with closing(lab.repo.connect()) as conn:
        rows = conn.execute("SELECT prefix,message FROM alert_delivery_outbox").fetchall()
    assert all(r[0] == "NIFTY_LAB_" and "PAPER ENTRY" in r[1] for r in rows)


def test_export_status_date_and_inputs(lab):
    lab.tick("Nifty_Setup1", NOW)
    report = lab.export("2026-10-08")
    assert len(report["trades"]) == 2 and len(report["trade_entry_decisions"]) == 1
    assert len(report["comparisons"]) == 5 and len(report["freshness"]) == 3
    assert report["timezone"] == "Asia/Kolkata" and report["mode"] == "paper_only"
    assert not lab.export("2026-10-07")["trades"]
    with pytest.raises(ValueError): lab.status("bad")
    with pytest.raises(ValueError): lab.start("bad")
    with pytest.raises(ValueError): lab.start(telegram_enabled="false")
    with pytest.raises(ValueError): lab.stop("bad")


def test_worker_count_single_data_feeds_and_idempotence(lab, monkeypatch):
    lab._threads = []
    thread = Mock()
    monkeypatch.setattr("trading_analysis.nifty_lab.service.threading.Thread", thread)
    NiftySetupLab._ensure_workers(lab)
    NiftySetupLab._ensure_workers(lab)
    assert thread.call_count == 7
    assert {c.kwargs["name"] for c in thread.call_args_list} == set(setups.SETUPS) | {"lab-candles", "lab-options"}
    gen = lab.states["Nifty_Setup1"]["generation"]
    lab.start()
    assert lab.states["Nifty_Setup1"]["generation"] == gen
    lab.stop()
    lab.quotes.release.assert_called_with("NIFTY_SETUP_LAB")


def test_api_routes_download_and_safe_defaults(lab, monkeypatch):
    from trading_analysis.web_app import ReusableThreadingHTTPServer, TradingRequestHandler
    monkeypatch.setattr(TradingRequestHandler, "nifty_lab", lab)
    lab.tick("Nifty_Setup1", NOW)
    server = ReusableThreadingHTTPServer(("127.0.0.1", 0), TradingRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base+"/api/nifty-lab/status?date=2026-10-08") as response:
            assert len(json.load(response)["trades"]) == 2
        with urlopen(base+"/api/nifty-lab/export?date=2026-10-08&format=zip") as response:
            with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                assert len(json.loads(archive.read(archive.namelist()[0]))["trades"]) == 2
        with urlopen(Request(base+"/api/nifty-lab/stop", data=b'{"setup":"Nifty_Setup1"}', headers={"Content-Type":"application/json"})) as response:
            assert json.load(response)["running_count"] == 4
        with pytest.raises(HTTPError) as error:
            urlopen(base+"/api/nifty-lab/status?date=bad")
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_refresh_uses_one_source_download_and_throttles_failed_source(lab, monkeypatch):
    class Clock:
        @staticmethod
        def now(tz): return NOW
    monkeypatch.setattr("trading_analysis.nifty_lab.service.datetime", Clock)
    monkeypatch.setattr("trading_analysis.nifty_lab.service.is_scan_window", lambda: True)
    lab.analysis._instruments_for_exchange = Mock(return_value=[{"exchange":"NSE", "tradingsymbol":"NIFTY 50", "instrument_token":"256265"}])
    def historical(token, frame, start, end):
        assert token == "256265" and end == NOW
        if frame == "5minute": raise RuntimeError("5m temporarily offline")
        return bars(frame, 90 if frame != "day" else 2)
    client = SimpleNamespace(historical_candles=Mock(side_effect=historical))
    monkeypatch.setattr("trading_analysis.web_services._zerodha_client", lambda: client)
    lab.refresh_candles()
    assert client.historical_candles.call_count == 3
    assert len(lab.candles("15minute")) == 90 and len(lab.candles("day")) == 2
    lab.refresh_candles()
    assert client.historical_candles.call_count == 3


def test_one_option_refresh_persists_received_history_for_all_workers(lab, monkeypatch, tmp_path):
    monkeypatch.setattr("trading_analysis.nifty_lab.service.is_market_hours", lambda: True)
    path = tmp_path / "snapshot.csv"
    rows = snapshot()["rows"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lab.analysis.refresh_option_chain_snapshot = Mock(return_value={"history_snapshot":str(path)})
    lab.refresh_options()
    lab.analysis.refresh_option_chain_snapshot.assert_called_once_with(symbol="NIFTY", strikes_around=8, max_snapshots=8)
    assert len(lab.options.load("NIFTY", NOW-timedelta(minutes=1), NOW)) == 1
    assert lab.data_state["options"]["last_success"] == NOW.isoformat()


def test_atomic_trade_alert_failure_rolls_back_decision(lab, monkeypatch):
    lab.states["Nifty_Setup1"]["telegram_enabled"] = True
    monkeypatch.setattr(lab.repo, "_enqueue", Mock(side_effect=RuntimeError("disk error")))
    with pytest.raises(RuntimeError): lab.tick("Nifty_Setup1", NOW)
    assert not lab.repo.trades() and not lab.repo.decisions("2026-10-08")


def test_real_worker_failure_does_not_end_loop(lab):
    callback = Mock(side_effect=[ValueError("failure"), None])
    count = []
    def once():
        count.append(1)
        if len(count) == 2: lab._shutdown.set()
        callback()
    lab._loop(once, 0, "Nifty_Setup1")
    assert len(count) == 2
    assert lab.states["Nifty_Setup1"]["error"] == "failure"
