from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from dataclasses import replace
import io
import json
import math
from threading import Event, Thread
from types import SimpleNamespace as NS
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

import pytest

from trading_analysis.live_timing import IST
from trading_analysis.mcx.rules import (ROOTS, VERSION, RISK, session, market_window, market_seconds_between, expected_bar,
    closed_rows, select_contracts, quote_read, technical_read, entry_check, trade_from_signal, exit_reason)
from trading_analysis.mcx.repository import McxRepository, performance
from trading_analysis.mcx.service import McxSwingScanner
from trading_analysis.models import Candle

NOW = datetime(2026, 10, 14, 11, 0, 20, tzinfo=IST)


def master():
    return [dict(exchange="MCX", segment="MCX-FUT", instrument_type="FUT", name=root,
                 tradingsymbol=f"{root}26{month}FUT", instrument_token=str(i*10+j), expiry=expiry, tick_size="0.1")
            for i, root in enumerate(ROOTS) for j, (month, expiry) in enumerate((("OCT", "2026-10-19"), ("NOV", "2026-11-30")))]


def contract():
    return select_contracts(master(), NOW)["GOLDM"]


def raw_quote(price=101, now=NOW):
    return dict(last_price=price, timestamp=now.isoformat(), last_trade_time=now.isoformat(), volume=500, oi=500,
                depth={"buy": [{"price": price-.05}], "sell": [{"price": price+.05}]})


def signal():
    return dict(ready=True, direction="bullish", bar_time=NOW.replace(hour=10, second=0).isoformat(),
                closed_at=NOW.replace(second=0).isoformat(), indicators=dict(hour_close=101, daily_atr14=4,
                atr_percent=4, breakout_high=100, breakout_low=90))


def trade():
    return trade_from_signal(contract(), signal(), quote_read(raw_quote(), NOW), NOW, True)


def decision(key="first"):
    return dict(id=key, checked_at=NOW.isoformat(), root="GOLDM", reason="eligible")


def history():
    days = [NOW.date()-timedelta(days=i) for i in range(180, 0, -1)]
    days = [d for d in days if session(d)]
    daily = [Candle(datetime(d.year,d.month,d.day,tzinfo=IST), 95+n*.1, 97+n*.1, 93+n*.1, 96+n*.1, 1000, 200)
             for n,d in enumerate(days)]
    hours = []
    for d in [*days[-8:], NOW.date()]:
        bounds = session(d)
        t = bounds[0]
        while t < bounds[1] and t <= expected_bar("60minute", NOW):
            n = len(hours)
            price = 100+n*.03+math.sin(n*1.5)
            hours.append(Candle(t, price-.1, price+.4, price-.4, price, 1000, 200))
            t += timedelta(hours=1)
    return {"day": daily, "60minute": hours}


@pytest.mark.parametrize("root", ROOTS)
def test_exact_futures_contract_selection_and_expiry_buffer(root):
    rows = master()+[dict(master()[0], name="SILVERMIC", expiry="2026-12-31")]
    chosen = select_contracts(rows, NOW)[root]
    assert chosen["root"] == root and "NOV" in chosen["symbol"]
    assert chosen["expiry"] == "2026-11-30"
    assert root not in select_contracts([r for r in rows if r["expiry"] == "2026-10-19"], NOW)


@pytest.mark.parametrize("stamp,active", [("2026-10-14T20:00:00",True), ("2026-10-14T08:59:00",False),
    ("2026-10-17T12:00:00",False), ("2026-10-02T20:00:00",False),
    ("2026-10-20T11:00:00",False), ("2026-10-20T18:00:00",True), ("2026-01-01T18:00:00",False)])
def test_mcx_session_not_nse_session(stamp, active):
    assert market_window(datetime.fromisoformat(stamp).replace(tzinfo=IST)) is active


def test_daily_and_hourly_close_boundaries_and_partial_last_hour():
    assert expected_bar("day", NOW).day == 13
    assert expected_bar("60minute", NOW).hour == 10
    end = NOW.replace(hour=23, minute=29)
    assert expected_bar("day", end).day == 13
    assert expected_bar("60minute", end).hour == 22
    assert expected_bar("60minute", end.replace(minute=31)).hour == 23
    assert expected_bar("day", NOW.replace(day=15, hour=0, minute=0)).day == 14
    rows = history()["60minute"]
    assert rows[-1].timestamp.hour == 10
    forming = Candle(NOW.replace(minute=0, second=0), 1, 2, 1, 2, 10)
    assert closed_rows(rows+[forming], "60minute", NOW) == rows


def test_sessional_gap_ignores_overnight_and_weekend_but_not_real_outages():
    before = datetime(2026,10,16,23,29,50,tzinfo=IST)
    after = datetime(2026,10,19,9,0,10,tzinfo=IST)
    assert market_seconds_between(before,after) == 20
    assert market_seconds_between(before,after+timedelta(minutes=3)) == 200
    assert session(datetime(2026,12,1).date())[1].strftime("%H:%M") == "23:55"
    assert not market_window(NOW.replace(hour=23,minute=31))


@pytest.mark.parametrize("mutate", [lambda q:q.update(timestamp=(NOW-timedelta(seconds=46)).isoformat()),
    lambda q:q.update(last_trade_time=(NOW-timedelta(seconds=121)).isoformat()),
    lambda q:q.update(timestamp=(NOW+timedelta(seconds=1)).isoformat()),
    lambda q:q.update(last_price=float("nan")), lambda q:q.update(depth={})])
def test_stale_future_missing_invalid_quotes_are_rejected(mutate):
    q = raw_quote(); mutate(q)
    assert not quote_read(q, NOW)["fresh"]


def test_daily_trend_hourly_indicators_and_oi_evidence():
    data = history()
    read = technical_read(data, NOW)
    assert read["ready"] and read["indicators"]["daily_ema20"] > read["indicators"]["daily_ema50"]
    assert read["daily_bar"].startswith("2026-10-13")
    assert read["indicators"]["daily_oi_change"] == 0
    data["day"].append(Candle(NOW.replace(hour=0,minute=0,second=0), 1, 999, 1, 999, 1))
    assert technical_read(data, NOW) == read
    data["60minute"].pop(-3)
    assert technical_read(data, NOW)["reason"] == "60minute_gap_in_breakout_window"


def test_real_rule_engine_bullish_bearish_and_opposite_daily_trend():
    data = history()
    top = max(c.high for c in data["60minute"][-21:-1])
    data["60minute"][-1] = replace(data["60minute"][-1], high=top+.3, close=top+.2)
    assert technical_read(data,NOW)["direction"] == "bullish"
    def mirror(rows):
        return [replace(c,open=250-c.open,high=250-c.low,low=250-c.high,close=250-c.close) for c in rows]
    data["day"] = mirror(data["day"])
    assert technical_read(data,NOW)["direction"] is None
    data["60minute"] = mirror(data["60minute"])
    assert technical_read(data,NOW)["direction"] == "bearish"


@pytest.mark.parametrize("frame", ["day", "60minute"])
def test_stale_warmup_and_malformed_candles(frame):
    data = history(); data[frame].pop()
    assert technical_read(data,NOW)["reason"] == frame+"_stale_or_missing"
    data = history(); data[frame] = data[frame][-10:]
    assert technical_read(data,NOW)["reason"] == frame+"_warmup_under_60_bars"
    data = history(); c = data[frame][-1]; data[frame][-1] = Candle(c.timestamp, 10, 5, 4, 11, 1)
    assert technical_read(data,NOW)["reason"] == frame+"_invalid_ohlc"


def test_entry_uses_observed_ask_not_signal_close_and_safe_stop():
    q = quote_read(raw_quote(), NOW)
    assert entry_check(contract(),signal(),q,NOW) == "eligible"
    t = trade()
    assert t["entry_price"] == q["ask"] != t["reference_price"]
    assert t["entry_time"] == NOW.isoformat() and t["signal_delay_seconds"] == 20
    assert t["stop_level"] < t["entry_price"] < t["target_level"]
    assert t["horizon"] == "swing" and t["risk_policy"]["max_holding_days"] == 10


def test_entry_blocks_late_spread_expiry_calendar_and_lost_breakout():
    s, q, c = signal(), quote_read(raw_quote(), NOW), contract()
    assert entry_check(c,s,q,NOW+timedelta(minutes=6)) == "signal_too_old"
    assert entry_check(c,s,{**q,"spread_percent":1},NOW) == "spread_too_wide"
    assert entry_check({**c,"expiry":"2026-10-19"},s,q,NOW) == "expiry_buffer"
    assert entry_check(c,s,q,NOW.replace(year=2027)) == "calendar_year_not_verified"
    assert entry_check(c,s,{**q,"price":99},NOW) == "breakout_no_longer_valid"
    assert entry_check(c,s,{**q,"fresh":False},NOW) == "quote_stale_or_missing"


def test_swing_exit_no_nse_squareoff_and_uses_exact_contract_quote():
    t = trade(); q = quote_read(raw_quote(),NOW)
    assert exit_reason(t,q,{},NOW.replace(hour=15,minute=30)) is None
    assert exit_reason(t,{**q,"bid":t["stop_level"]-2},{},NOW) == "stop_loss"
    assert exit_reason(t,{**q,"bid":t["target_level"]+2},{},NOW) == "target"
    assert exit_reason(t,{**q,"fresh":False,"bid":1},{},NOW) is None
    assert exit_reason({**t,"expiry":"2026-10-20"},q,{},NOW) == "expiry_risk_exit"
    assert exit_reason({**t,"entry_time":(NOW-timedelta(days=11)).isoformat()},q,{},NOW) == "maximum_holding_days"


def test_bearish_entries_and_exits():
    q = quote_read(raw_quote(), NOW); s = {**signal(),"direction":"bearish"}
    t = trade_from_signal(contract(),s,q,NOW)
    assert t["entry_price"] == q["bid"] and t["stop_level"] > t["entry_price"] > t["target_level"]
    assert exit_reason(t,{**q,"ask":t["target_level"]-1},{},NOW) == "target"


def test_transactional_dedupe_one_open_and_outbox(tmp_path):
    repo = McxRepository(tmp_path/"mcx.db")
    with ThreadPoolExecutor(max_workers=5) as pool:
        created = list(pool.map(lambda _:repo.record(decision(),trade()),range(10)))
    assert sum(t is not None for t in created) == 1
    assert len(repo.trades()) == len(repo.outbox.history()) == 1
    assert repo.record(decision("second"),trade()) is None
    assert len(repo.trades()) == 1
    assert repo.seen("second")
    with closing(repo.connect()) as conn:
        assert conn.execute("SELECT prefix FROM alert_delivery_outbox").fetchone()[0] == "MCX_"


def test_persistence_restart_interruption_close_and_costs(tmp_path):
    repo = McxRepository(tmp_path/"mcx.db"); row = repo.record(decision(),trade())
    restarted = McxSwingScanner(repo.path,tmp_path/"data")
    assert restarted.repository.trades()[0]["monitoring_interrupted"]
    q = quote_read(raw_quote(110),NOW)
    closed = restarted.repository.close(row,q,NOW+timedelta(days=1),"target")
    assert closed["exit_price"] == q["bid"] and closed["open_seconds"] == 86400
    assert closed["net_points"] < closed["gross_points"]
    assert not closed["performance_eligible"]
    assert performance([closed])["excluded"] == 1
    assert restarted.repository.close(row,q,NOW,"target") is None
    assert len(repo.outbox.history()) == 2


def test_refresh_atomic_cache_archive_separation_and_incremental(tmp_path):
    calls = []
    def candles(token, frame, start, now, **kw):
        calls.append((token,frame,kw)); return history()[frame]
    scanner = McxSwingScanner(tmp_path/"mcx.db",tmp_path/"data",lambda:NS(instruments=lambda ex:master(),historical_candles=candles))
    scanner.refresh(NOW,archive=True)
    assert len(calls) == 12
    assert sum(c[2].get("continuous",False) for c in calls) == 4
    assert (tmp_path/"data/continuous_context_only/GOLDM.csv").exists()
    assert len(scanner._sources) == 4
    scanner.refresh(NOW+timedelta(minutes=2))
    assert len(calls) == 12
    scanner.refresh(NOW+timedelta(hours=1))
    assert len(calls) == 16


def test_forming_cache_never_promoted_after_clock_advances(tmp_path):
    calls = []
    data = history()
    forming_time = NOW.replace(minute=0,second=0)
    forming = Candle(forming_time, 100, 110, 90, 105, 500)
    def candles(token,frame,*a,**kw):
        calls.append(frame)
        return data[frame]+([forming] if frame == "60minute" else [])
    scanner = McxSwingScanner(tmp_path/"db",tmp_path/"data",lambda:NS(instruments=lambda ex:master(),historical_candles=candles))
    scanner.refresh(NOW)
    assert scanner._sources[contract()["symbol"]]["60minute"][-1].timestamp.hour == 10
    assert len(calls) == 8
    later = NOW+timedelta(hours=1)
    assert technical_read(scanner._sources[contract()["symbol"]], later)["reason"] == "60minute_stale_or_missing"
    scanner.refresh(later)
    assert len(calls) == 12
    assert scanner._sources[contract()["symbol"]]["60minute"][-1].timestamp.hour == 11


def test_symbol_refresh_failure_isolated_and_retryable(tmp_path):
    def candles(token, frame, *a, **kw):
        if token == "1": raise ValueError("fixture failure")
        return history()[frame]
    scanner = McxSwingScanner(tmp_path/"db",tmp_path/"data",lambda:NS(instruments=lambda ex:master(),historical_candles=candles))
    scanner.refresh(NOW)
    assert len(scanner._sources["GOLDM26NOVFUT"]["day"]) > 60
    assert scanner.health["SILVERM26NOVFUT:day"]["failures"] == 1
    assert scanner.state["completed"] == scanner.state["total"] == 8


def test_cycle_dedupes_restart_and_does_not_use_stale_quotes(tmp_path,monkeypatch):
    scanner = McxSwingScanner(tmp_path/"db",tmp_path/"data",lambda:NS(quotes=lambda symbols:{s:raw_quote() for s in symbols}))
    scanner.contracts = {"GOLDM":contract()}; scanner._master_day = NOW.date()
    monkeypatch.setattr("trading_analysis.mcx.service.technical_read",lambda *a:signal())
    monkeypatch.setattr(scanner.repository.outbox,"start",lambda:None)
    scanner.cycle(NOW)
    assert len(scanner.repository.trades()) == 1
    scanner.cycle(NOW)
    assert len(scanner.repository.trades()) == 1
    assert scanner.decisions["GOLDM"]["reason"] == "closed_candle_already_processed"
    assert not scanner.repository.outbox.history()  # Telegram defaults off.


def test_stale_signals_cannot_create_alerts(tmp_path,monkeypatch):
    scanner = McxSwingScanner(tmp_path/"db",tmp_path/"data",lambda:NS(quotes=lambda symbols:{s:raw_quote(now=NOW-timedelta(hours=1)) for s in symbols}))
    scanner.contracts = {"GOLDM":contract()}; scanner._master_day = NOW.date()
    monkeypatch.setattr("trading_analysis.mcx.service.technical_read",lambda *a:signal())
    monkeypatch.setattr(scanner.repository.outbox,"start",lambda:None)
    scanner.cycle(NOW)
    assert not scanner.repository.trades() and not scanner.repository.outbox.history()
    assert scanner.decisions["GOLDM"]["reason"] == "quote_stale_or_missing"


def test_stop_cancels_inflight_quote_before_entry(tmp_path,monkeypatch):
    started, release = Event(), Event()
    def quotes(symbols):
        started.set(); release.wait(3); return {s:raw_quote() for s in symbols}
    scanner = McxSwingScanner(tmp_path/"db",tmp_path/"data",lambda:NS(quotes=quotes))
    scanner.contracts = {"GOLDM":contract()}; scanner._master_day=NOW.date()
    monkeypatch.setattr("trading_analysis.mcx.service.technical_read",lambda *a:signal())
    worker = Thread(target=lambda:scanner.cycle(NOW)); worker.start()
    assert started.wait(3)
    scanner.stop(); release.set(); worker.join(3)
    assert not worker.is_alive() and not scanner.repository.trades()


def test_manual_download_has_no_checker_and_duplicate_start_blocked(tmp_path,monkeypatch):
    scanner = McxSwingScanner(tmp_path/"db",tmp_path/"data")
    started, release = Event(), Event()
    def refresh(**kw): started.set(); release.wait(3)
    monkeypatch.setattr(scanner,"refresh",refresh)
    scanner.start(download_only=True)
    assert started.wait(3)
    with pytest.raises(ValueError,match="already running"):scanner.start()
    scanner.stop(); release.set()
    for t in scanner._threads:t.join(3)
    assert not scanner.status()["busy"] and not scanner.repository.trades()


def test_exports_history_overlap_dates_inputs_and_no_secrets(tmp_path):
    scanner = McxSwingScanner(tmp_path/"db",tmp_path/"data")
    scanner.repository.record(decision(),trade())
    scanner.repository.event("fixture",NOW)
    report = scanner.export("2026-10-14","2026-10-15")
    assert len(report["trades"]) == len(report["decisions"]) == len(report["events"]) == 1
    assert report["version"] == VERSION and report["timezone"] == "Asia/Kolkata"
    assert "api_key" not in json.dumps(report) and "bot_token" not in json.dumps(report)
    with zipfile.ZipFile(io.BytesIO(scanner.export_zip("2026-10-14","2026-10-15"))) as z:
        assert {"mcx-review.json","trades.csv","README.txt"} <= set(z.namelist())
        assert "GOLDM26NOVFUT" in z.read("trades.csv").decode()
    assert scanner.export("2026-10-15","2026-10-15")["trades"]  # Still open.
    with pytest.raises(ValueError):scanner.status("2026-10-15","2026-10-14")


def test_period_metrics_do_not_include_future_exits(tmp_path):
    scanner = McxSwingScanner(tmp_path/"db",tmp_path/"data")
    row = scanner.repository.record(decision(),trade())
    scanner.repository.close(row,quote_read(raw_quote(110),NOW),NOW+timedelta(days=1),"target")
    earlier = scanner.status("2026-10-14","2026-10-14")
    assert len(earlier["trades"]) == 1 and earlier["metrics"]["closed"] == 0
    assert scanner.status("2026-10-15","2026-10-15")["metrics"]["closed"] == 1


def test_expired_open_contract_never_uses_reassigned_token(tmp_path,monkeypatch):
    calls = []
    def candles(token,frame,*a,**kw):
        calls.append(token); return history()[frame]
    scanner = McxSwingScanner(tmp_path/"db",tmp_path/"data",lambda:NS(instruments=lambda ex:master(),historical_candles=candles))
    scanner.repository.record(decision(),{**trade(),"symbol":"GOLDM26SEPFUT","expiry":"2026-09-30","token":"EXPIRED"})
    scanner.refresh(NOW)
    assert "EXPIRED" not in calls
    assert scanner.repository.trades(open_only=True)[0]["symbol"] == "GOLDM26SEPFUT"
    assert scanner.health["GOLDM26SEPFUT:day"]["status"] == "failed"


def test_dashboard_includes_mcx_without_changing_nse_sources(tmp_path):
    from trading_analysis.scanner_dashboard import ScannerDashboard
    scanner = McxSwingScanner(tmp_path/"db",tmp_path/"data")
    row = scanner.repository.record(decision(),trade())
    dashboard = ScannerDashboard(NS(mcx_scanner=scanner))
    ledger = dashboard.ledger(source="MCX",status="open")
    assert ledger["total"] == 1 and ledger["trades"][0]["scanner"] == "MCX"
    assert ledger["trades"][0]["horizon"] == "swing"
    assert ledger["trades"][0]["symbol"] == row["symbol"]
    assert ledger["trades"][0]["result"]["percent"] is None


def test_api_status_export_and_validation(tmp_path):
    from trading_analysis.web_app import TradingRequestHandler, ReusableThreadingHTTPServer
    scanner = McxSwingScanner(tmp_path/"db",tmp_path/"data")
    class Handler(TradingRequestHandler): mcx_scanner = scanner
    with ReusableThreadingHTTPServer(("127.0.0.1",0),Handler) as server:
        worker = Thread(target=server.serve_forever,daemon=True); worker.start()
        base=f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base+"/api/mcx/status") as r: assert json.load(r)["roots"] == list(ROOTS)
            with urlopen(base+"/api/mcx/export?format=zip") as r: assert zipfile.is_zipfile(io.BytesIO(r.read()))
            with pytest.raises(HTTPError):urlopen(base+"/api/mcx/status?unknown=x")
            with pytest.raises(HTTPError):urlopen(Request(base+"/api/mcx/start",data=b'{"bogus":true}',headers={"Content-Type":"application/json"}))
            with urlopen(Request(base+"/api/mcx/stop",data=b'{}',headers={"Content-Type":"application/json"})) as r:assert not json.load(r)["running"]
        finally:server.shutdown();worker.join(3)


def test_browser_asset_burst_has_connection_capacity():
    from http.server import BaseHTTPRequestHandler
    from trading_analysis.web_app import ReusableThreadingHTTPServer
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        def log_message(self,*args):pass
    with ReusableThreadingHTTPServer(("127.0.0.1",0),Handler) as server:
        assert server.request_queue_size >= 32
        worker=Thread(target=server.serve_forever,daemon=True); worker.start()
        try:
            def read(_):
                with urlopen(f"http://127.0.0.1:{server.server_port}",timeout=5) as r:return r.read()
            with ThreadPoolExecutor(max_workers=24) as pool:
                assert list(pool.map(read,range(48))) == [b"ok"]*48
        finally:server.shutdown();worker.join(3)
