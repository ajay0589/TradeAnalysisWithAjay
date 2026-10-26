from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS
from threading import Thread
from urllib.request import urlopen, Request
from urllib.error import HTTPError

import pytest

from trading_analysis.models import Candle
from trading_analysis.live_timing import IST
from trading_analysis.stock_research import StockResearch, technical, context, replay, summary, screen, window_start
from trading_analysis.backtesting.exits import exit_for


def bars(n=90):
    return [Candle(datetime(2026,1,1,tzinfo=IST)+timedelta(days=i), 100+i*.4-.1, 100+i*.4+2.5,
                   100+i*.4-2.5, 100+i*.4, 1000) for i in range(n)]


def test_pullback_is_symmetric_and_uses_closed_history_only():
    rows=bars()
    p=rows[-2]
    rows[-1]=Candle(rows[-1].timestamp,p.close+1,p.high+1,p.low,p.high+.5,1000)
    signals,info=technical(rows)
    assert signals["trend_pullback"]["side"]=="long"
    mirror=[Candle(r.timestamp,300-r.open,300-r.low,300-r.high,300-r.close,r.volume) for r in rows]
    assert technical(mirror)[0]["trend_pullback"]["side"]=="short"
    assert technical(rows[:-1])[0] != signals
    assert technical(rows[:20])[1]["reason"]=="insufficient_warmup"


def test_context_cannot_see_today_before_close_or_future_bars():
    rows=bars()
    at=rows[-1].timestamp.replace(hour=10)
    result=context(rows,rows,rows,at,rows[-2].timestamp.date())
    assert result["daily_bar"]==str(rows[-2].timestamp.date())
    assert result["aligned"]=="long"
    assert result["relative"] is None  # Equal returns are not relative outperformance.
    changed=[*rows[:-1],Candle(rows[-1].timestamp,1,2,1,1,1000)]
    assert context(changed,changed,changed,at,rows[-2].timestamp.date())==result
    assert context(rows,rows,[],at,rows[-2].timestamp.date())["reason"]=="context_missing_or_stale"


def test_compression_requires_volume_and_prior_range_break():
    rows=bars()
    p=rows[-2]
    rows[-1]=Candle(rows[-1].timestamp,p.close+1,p.high+1,p.low,p.high+.5,1500)
    assert "compression_breakout" in technical(rows)[0]
    from dataclasses import replace
    rows[-1]=replace(rows[-1],volume=1200)
    assert "compression_breakout" not in technical(rows)[0]
    rows[-1]=replace(rows[-1],volume=1500,close=p.high-.1)
    assert "compression_breakout" not in technical(rows)[0]


def test_calendar_month_windows_and_fixed_week_windows():
    assert window_start(date(2026,10,16),"1 week")==date(2026,10,10)
    assert window_start(date(2026,10,16),"6 months")==date(2026,4,17)
    assert window_start(date(2026,3,31),"1 month")==date(2026,3,1)


def test_gap_stops_are_conservative_without_changing_legacy_default():
    c=Candle(datetime(2026,1,1,tzinfo=IST),90,112,88,100,1000)
    assert exit_for([c],0,"long",95,110,1)["price"]==95
    result=exit_for([c],0,"long",95,110,1,adverse_gaps=True)
    assert result["price"]==90 and result["intrabar_ambiguous"]
    assert exit_for([c],0,"short",105,89,1,adverse_gaps=True)["price"]==105


def test_replay_next_open_costs_no_overlap_and_no_future_exit():
    rows=[Candle(datetime(2026,1,1,tzinfo=IST)+timedelta(days=i),100,102,98,100,1000) for i in range(20)]
    signal={"trend_pullback":{"side":"long","stop":90,"reference":100,"risk":10,"atr":5}}
    signals={i:signal for i in range(20)}
    out=replay("TEST",rows,signals,"trend_pullback","long","technical",1,date(2026,1,1),date(2026,1,20),"day",{})
    assert out[0]["entry_time"].startswith("2026-01-02T09:15")
    assert out[0]["exit_time"].startswith("2026-01-11T15:30")
    assert out[0]["net_r"]==-0.008
    assert out[-1]["status"]=="excluded"
    assert replay("TEST",rows,signals,"trend_pullback","long","aligned",1,date(2026,1,1),date(2026,1,20),"day",{})==[]
    assert all(t["signal_time"]<t["entry_time"]<=t["exit_time"] for t in out)


def test_drift_and_invalid_risk_block_entries():
    rows=bars(3)
    for reference,stop in ((50,40),(100,120)):
        sig={0:{"trend_pullback":{"side":"long","stop":stop,"reference":reference,"risk":10,"atr":5}}}
        assert not replay("T",rows,sig,"trend_pullback","long","technical",2,date(2026,1,1),date(2026,1,10),"day",{})


def test_screen_is_not_approval_and_requires_later_sample_costs_coverage():
    m={"trades":50,"win_rate":60,"expectancy_r":.3,"profit_factor":1.5,"no_losses":False,"stress_expectancy_r":.2}
    assert screen(m,m,True)=="screen_pass_not_approved"
    assert screen(m,m,False)=="incomplete_data"
    assert screen(m,{**m,"trades":9},True)=="insufficient_sample"
    assert screen({**m,"stress_expectancy_r":-.1},m,True)=="screen_failed"
    assert summary([])["win_rate"] is None


@pytest.fixture
def runner(tmp_path):
    return StockResearch(NS(),tmp_path/"research.db")


def test_persistence_read_only_status_and_date_range_export(runner,monkeypatch):
    report={"id":"one","created_at":"2026-10-17T10:00:00+05:30","rows":[],"trades":[
        {"symbol":"TEST","entry_time":"2026-10-01T09:15:00+05:30","exit_time":"2026-10-03T15:30:00+05:30"}]}
    monkeypatch.setattr(runner,"run",lambda *a:report)
    runner._worker(date(2026,10,16),"NIFTY 50",False)
    assert runner.status()["phase"]=="completed"
    assert "trades" not in runner.status()["report"]
    assert not runner.status()["live_enabled"]
    assert len(runner.export("2026-10-02","2026-10-02")["trades"])==1
    assert not runner.export("2026-10-04","2026-10-04")["trades"]
    with pytest.raises(ValueError):runner.export("2026-10-05","2026-10-04")
    other=StockResearch(NS(),runner.path)
    assert other.status()["report"]["id"]=="one"


def test_duplicate_and_invalid_run_rejected_and_cancel_retains_previous(runner,monkeypatch):
    runner.state["phase"]="running"
    with pytest.raises(ValueError):runner.start("2026-01-01")
    with pytest.raises(ValueError):runner.start("2999-01-01")
    with pytest.raises(ValueError):runner.start("2026-01-01","bad")
    runner.cancel()
    with pytest.raises(InterruptedError):runner._progress("test")
    monkeypatch.setattr(runner,"run",lambda *a:runner._progress("cancelled"))
    runner._worker(date(2026,1,1),"NIFTY 50",False)
    assert runner.status()["phase"]=="cancelled"


def test_real_analysis_service_can_read_nfo_master(tmp_path):
    from trading_analysis.web_services import AnalysisService
    path=tmp_path/"instruments_NFO.csv"
    path.write_text("exchange,segment,name,expiry\nNFO,NFO-FUT,TEST,2026-10-29\n")
    service=AnalysisService(nfo_instruments_path=path)
    assert service._instruments_for_exchange("NFO")[0]["name"]=="TEST"


def test_stock_api_no_live_start_and_export(runner,monkeypatch):
    from trading_analysis.web_app import TradingRequestHandler,ReusableThreadingHTTPServer
    monkeypatch.setattr(TradingRequestHandler,"stock_research",runner)
    with ReusableThreadingHTTPServer(("127.0.0.1",0),TradingRequestHandler) as server:
        worker=Thread(target=server.serve_forever,daemon=True);worker.start()
        base=f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base+"/api/stocks/status") as r: assert json.load(r)["live_enabled"] is False
            with urlopen(base+"/api/stocks/export?format=zip") as r: assert r.read().startswith(b"PK")
            with pytest.raises(HTTPError) as e:urlopen(Request(base+"/api/stocks/start",data=b"{}",headers={"Content-Type":"application/json"}))
            assert e.value.code==400
        finally:server.shutdown();worker.join()


def test_validation_weekend_end_and_progress_completion_are_explicit(runner, monkeypatch):
    from trading_analysis.stock_research import session_on_or_before
    assert session_on_or_before(date(2026, 10, 25)) == date(2026, 10, 23)
    report = {"id": "session", "created_at": "2026-10-26T10:00:00+05:30", "as_of": "2026-10-23",
              "rows": [{"screen": "incomplete_data"}], "trades": []}
    runner.state.update(total=206, completed=205)
    monkeypatch.setattr(runner, "run", lambda *a: report)
    runner._worker(date(2026, 10, 23), "NIFTY 50", False)
    result = runner.status()
    assert result["completed"] == result["total"] == 206
    assert result["paper_available"] and not result["live_enabled"]
    assert any("coverage" in r for r in result["approval_reasons"])
    assert "live approval remains blocked" not in result["progress"]
