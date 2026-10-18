from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from threading import Thread
from urllib.request import urlopen, Request
from urllib.error import HTTPError
import json

import pytest

from trading_analysis.live_timing import IST
from trading_analysis.models import Candle
from trading_analysis.expiry import rules
from trading_analysis.expiry.service import ExpiryLab, summary
from trading_analysis.expiry.replay import replay_frames

NOW=datetime(2026,10,20,10,0,1,tzinfo=IST)


def quote(token=1,kind="CE",strike=23000,bid=100,ask=101,now=NOW):
    return dict(token=token,symbol="NIFTY",kind=kind,strike=strike,expiry=now.date().isoformat(),
        tradingsymbol=f"TEST{strike}{kind}",lot_size=50,price=(bid+ask)/2,bid=bid,ask=ask,bid_qty=100,ask_qty=100,
        quote_time=now.isoformat(),received_at=now.isoformat())


def market(now=NOW):
    spot={"price":23000,"quote_time":now.isoformat(),"received_at":now.isoformat()}
    quotes={1:quote(now=now),2:quote(2,strike=23100,bid=60,ask=60.5,now=now),3:quote(3,kind="PE",now=now)}
    snapshot=dict(identity="NIFTY:23000",expiry=now.date().isoformat(),strike=23000,CE=1,PE=3,spot=spot,time=now.isoformat())
    return spot,quotes,snapshot


SIGNAL=dict(direction="bullish",reference=23000,stop=22900,risk=100,atr=100)


@pytest.fixture
def lab(tmp_path,monkeypatch):
    runner=ExpiryLab(SimpleNamespace(),tmp_path/"expiry.db")
    for state in runner.states.values():state["enabled"]=True
    monkeypatch.setattr("trading_analysis.expiry.service.capture_vix",lambda *a:{"value":15})
    monkeypatch.setattr("trading_analysis.expiry.service.technical",lambda rows,now:dict(signals={k:dict(SIGNAL) for k in rules.SETUPS},reason="evaluated",indicators={},closed_at=now.replace(second=0,microsecond=0).isoformat()))
    monkeypatch.setattr("trading_analysis.expiry.service.premium_evidence",lambda *a:dict(premium_pass=True,oi_pass=False,reason="oi_unconfirmed"))
    return runner


def process(lab,now=NOW,spot=None,quotes=None,snapshot=None):
    s,q,p=market(now)
    lab.process("NIFTY",now,[],spot or s,[],snapshot or p,quotes or q)


def test_four_independent_entries_one_portfolio_and_duplicate_suppression(lab):
    process(lab);process(lab)
    trades=lab.repo.trades()
    assert len(trades)==4
    assert sum(t["portfolio_accepted"] for t in trades)==1
    assert next(t for t in trades if t["portfolio_accepted"])["setup"]=="NIFTY:Expiry_1"
    assert all(t["quantity"]==50 and t["debit"]==pytest.approx(41.2) for t in trades)
    assert all(t["risk_budget"]<=12500 for t in trades)
    assert len(lab.repo.decisions(NOW.date().isoformat()))==4
    assert not lab.repo.outbox.history(NOW.date().isoformat())


@pytest.mark.parametrize("failure",["stale","depth","wide","sync","budget","lot","expiry"])
def test_bad_spread_inputs_do_not_fill(failure):
    spot,quotes,snapshot=market()
    if failure=="stale":quotes[1]["received_at"]=(NOW-timedelta(seconds=10)).isoformat()
    if failure=="depth":quotes[2]["bid_qty"]=1
    if failure=="wide":quotes[2]["ask"]=100
    if failure=="sync":quotes[2]["quote_time"]=(NOW-timedelta(seconds=3)).isoformat()
    if failure=="budget":
        for q in quotes.values():q.update(lot_size=500,bid_qty=500,ask_qty=500)
    if failure=="lot":quotes[2]["lot_size"]=0
    if failure=="expiry":snapshot["expiry"]="2026-10-21"
    spread,reason=rules.select_spread(SIGNAL,snapshot,quotes,NOW)
    assert spread is None and reason!="qualified"


def test_bearish_spread_uses_put_lower_short_strike():
    spot,quotes,snapshot=market()
    quotes[4]=quote(4,kind="PE",strike=22900,bid=60,ask=60.5)
    spread,reason=rules.select_spread({**SIGNAL,"direction":"bearish"},snapshot,quotes,NOW)
    assert reason=="qualified" and spread["long"]["kind"]=="PE" and spread["short"]["strike"]==22900


def test_actual_expiry_date_not_weekday_gate(lab):
    s,q,p=market();p["expiry"]="2026-10-21"
    process(lab,snapshot=p)
    assert not lab.repo.trades()
    assert lab.states["NIFTY:Expiry_1"]["phase"]=="waiting_expiry"


def test_exits_use_both_legs_costs_and_continue_after_stop(lab):
    process(lab)
    lab.stop()
    now=NOW+timedelta(seconds=1);spot,quotes,snapshot=market(now)
    spot["price"]=22899
    process(lab,now,spot,quotes,snapshot)
    trades=lab.repo.trades()
    assert all(t["status"]=="closed" and t["exit_reason"]=="structural_stop" for t in trades)
    expected=(39.3-41.2)*50-(161*50*.0025+50)-(160.5*50*.0025+50)
    assert trades[0]["net_pnl"]==pytest.approx(expected)
    assert trades[0]["performance_eligible"]
    assert trades[0]["open_seconds"]==1


def test_stale_exit_not_filled_and_next_day_excluded(lab):
    process(lab)
    now=NOW.replace(hour=15,minute=10);s,q,p=market(now)
    q[2]["received_at"]=NOW.isoformat()
    process(lab,now,s,q,p)
    assert all(t["status"]=="open" and t["pending_exit"]=="session_exit" for t in lab.repo.trades())
    process(lab,now+timedelta(days=1))
    assert all(t["status"]=="closed" and t["net_pnl"] is None and not t["performance_eligible"] for t in lab.repo.trades())
    assert summary(lab.repo.trades())["closed"]==0


def test_restart_marks_open_trades_interrupted(lab):
    process(lab)
    other=ExpiryLab(SimpleNamespace(),lab.path)
    assert all(t["monitoring_interrupted"]=="server_restart" for t in other.repo.trades())
    assert not any(s["enabled"] for s in other.states.values())


def test_start_requires_acknowledgement_and_no_orders(lab,monkeypatch):
    with pytest.raises(ValueError,match="Acknowledge"):lab.start()
    with pytest.raises(ValueError,match="Unknown"):lab.start("bad",acknowledge=True)
    with pytest.raises(ValueError,match="boolean"):lab.start(telegram_enabled="yes",acknowledge=True)
    monkeypatch.setattr("trading_analysis.expiry.service.threading.Thread",Mock())
    lab.start(acknowledge=True)
    lab.start(acknowledge=True)
    assert len(lab._threads)==3


def test_daily_entry_limit_and_persistent_counts(lab):
    process(lab)
    for i in (1,2):
        for t in lab.repo.trades(open_only=True):
            t.update(status="closed",exit_time=NOW.isoformat(),net_pnl=-100,net_r=-.05)
            lab.repo.save_trade(t)
        process(lab,NOW+timedelta(minutes=5*i))
    assert len(lab.repo.trades())==8
    assert all(s["reason"]=="daily_entry_limit" for k,s in lab.states.items() if k.startswith("NIFTY"))


def test_portfolio_unknown_marks_block_new_acceptance(lab):
    process(lab)
    t=lab.repo.portfolio_trades()[0]
    t.update(mark_pnl=None);lab.repo.save_trade(t)
    assert lab._portfolio_state(NOW)["unknown"]


def test_export_and_replay_missing_history(lab):
    process(lab)
    report=lab.export("2026-10-20","2026-10-20",True)
    assert len(report["trades"])==4 and "frames" in report and "long" in report["csv_columns"]
    assert not lab.export("2026-10-21","2026-10-21")["decisions"]
    with pytest.raises(ValueError):lab.export("2026-10-21","2026-10-20")
    assert lab.replay("2026-10-20","2026-10-20")["phase"]=="unavailable"
    with pytest.raises(ValueError,match="version"):replay_frames([{"version":"bad"}])


def test_replay_uses_same_entry_exit_engine_without_alerts(lab):
    frames=[]
    for now in (NOW,NOW+timedelta(seconds=1)):
        s,q,p=market(now)
        if now!=NOW:s["price"]=22899
        frames.append(dict(version=rules.VERSION,symbol="NIFTY",time=now.isoformat(),rows=[],spot=s,quotes=q,snapshot=p,enabled=list(lab.states)))
        process(lab,now,s,q,p)
    replay=replay_frames(frames)
    assert len(replay["trades"])==len(lab.repo.trades())==4
    assert [t["net_pnl"] for t in replay["trades"]]==[t["net_pnl"] for t in lab.repo.trades()]
    assert not any(t["telegram_enabled"] for t in replay["trades"])


def test_technical_no_future_and_missing_minutes():
    start=NOW.replace(hour=9,minute=15,second=0)-timedelta(days=1)
    rows=[]
    for day in range(4):
        for i in range(375):
            price=23000+i*.1
            rows.append(Candle(start+timedelta(days=day,minutes=i),price,price+2,price-2,price+1,0))
    result=rules.technical(rows,NOW)
    past=[c for c in rows if c.timestamp+timedelta(minutes=1)<=NOW]
    assert result==rules.technical(past,NOW)
    assert rules.technical([],NOW)["reason"]=="warmup"


@pytest.mark.parametrize("setup",list(rules.SETUPS))
@pytest.mark.parametrize("bearish",[False,True])
def test_setup_signals_and_mirrored_direction(setup,bearish,monkeypatch):
    now=NOW.replace(hour=11,minute=0,second=1)
    start=now.replace(hour=4,minute=45,second=0)
    base=[Candle(start+timedelta(minutes=5*i),102,104,96,103,0) for i in range(75)]
    # Tail ends at 10:55; timestamps remain contiguous for the live checks.
    base=[Candle(now.replace(hour=10,minute=55,second=0)-timedelta(minutes=5*(74-i)),c.open,c.high,c.low,c.close,0) for i,c in enumerate(base)]
    if setup=="Expiry_1":
        base[-2]=Candle(base[-2].timestamp,104,107,103,105,0)
        base[-1]=Candle(base[-1].timestamp,104,108,103,107,0)
    elif setup=="Expiry_2":
        base[-1]=Candle(base[-1].timestamp,103,108,101,107,0)
    elif setup=="Expiry_3":
        for i in range(-7,-1):base[i]=Candle(base[i].timestamp,102,104,101,103,0)
        base[-1]=Candle(base[-1].timestamp,102,115,101,114,0)
    else:
        base[-2]=Candle(base[-2].timestamp,97,100,94,98,0)
        base[-1]=Candle(base[-1].timestamp,98,105,97,104,0)
    higher=[Candle(now.replace(hour=10,minute=45,second=0)-timedelta(minutes=15*(54-i)),109,111,108,110,0) for i in range(55)]
    if bearish:
        base=[Candle(c.timestamp,220-c.open,220-c.low,220-c.high,220-c.close,0) for c in base]
        higher=[Candle(c.timestamp,220-c.open,220-c.low,220-c.high,220-c.close,0) for c in higher]
    def ema(values,period):
        value=(105 if period==20 else (104 if setup=="Expiry_4" else 100)) if len(values)==55 else 104 if len(values)==54 else 103 if period==20 else 95
        return 220-value if bearish else value
    monkeypatch.setattr(rules,"aggregate",lambda rows,minutes:base if minutes==5 else higher)
    monkeypatch.setattr(rules,"ema",ema);monkeypatch.setattr(rules,"atr",lambda rows:10)
    result=rules.technical([],now)
    assert setup in result["signals"],result
    assert result["signals"][setup]["direction"]==("bearish" if bearish else "bullish")


def test_api_navigation_export_and_start_guard(lab,monkeypatch):
    from trading_analysis.web_app import TradingRequestHandler,ReusableThreadingHTTPServer
    monkeypatch.setattr(TradingRequestHandler,"expiry_lab",lab)
    with ReusableThreadingHTTPServer(("127.0.0.1",0),TradingRequestHandler) as server:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base+"/api/expiry/status?from_date=2026-10-20&to_date=2026-10-20") as r:
                data=json.load(r);assert len(data["states"])==8
            with urlopen(base+"/api/expiry/export?format=zip&from_date=2026-10-20") as r:assert r.read().startswith(b"PK")
            with pytest.raises(HTTPError) as exc:urlopen(Request(base+"/api/expiry/start",data=b"{}",headers={"Content-Type":"application/json"}))
            assert exc.value.code==400
        finally:server.shutdown();thread.join()


def test_bfo_master_available_to_real_feed(tmp_path):
    from trading_analysis.web_services import AnalysisService
    (tmp_path/"instruments_BFO.csv").write_text("exchange,name,instrument_type,expiry\nBFO,SENSEX,CE,2026-10-22\n")
    service=AnalysisService(nfo_instruments_path=tmp_path/"instruments_NFO.csv")
    assert service._instruments_for_exchange("BFO")[0]["name"]=="SENSEX"


def test_expiry_alerts_are_deduplicated_and_expire(lab,monkeypatch):
    monkeypatch.setattr(lab.repo.outbox,"start",Mock())
    lab.states["NIFTY:Expiry_1"]["telegram_enabled"]=True
    process(lab);process(lab)
    assert len(lab.repo.outbox.history(NOW.date().isoformat()))==1
    sender=Mock()
    lab.repo.outbox.drain(sender=sender,now=NOW+timedelta(seconds=16))
    sender.send_message.assert_not_called()
    assert lab.repo.outbox.history(NOW.date().isoformat())[0]["status"]=="expired"


def test_manual_fill_journal_is_separate_and_validated(lab):
    past=NOW.replace(year=2026,month=10,day=13)
    process(lab,past)
    trade=lab.repo.trades()[0]
    body=dict(trade_id=trade["id"],kind="entry",time="2026-10-13T10:01:00+05:30",quantity=50,long_price=101,short_price=60,charges=50)
    lab.record_manual(body)
    assert lab.repo.manual_rows()[0]["basis"]=="user_reported_not_broker_verified"
    assert lab.repo.trades()[0]["entry_time"]==trade["entry_time"]
    with pytest.raises(ValueError):lab.record_manual({**body,"quantity":-1})
    with pytest.raises(ValueError):lab.record_manual({**body,"long_price":float("nan")})
    with pytest.raises(ValueError):lab.record_manual({**body,"trade_id":"missing"})
    with pytest.raises(ValueError):lab.record_manual({**body,"quantity":1})
    with pytest.raises(ValueError):lab.record_manual({**body,"time":"2026-10-12T10:00:00+05:30"})


def test_sensex_separate_ledgers_and_market_close_guard(lab):
    spot,quotes,snapshot=market()
    for q in quotes.values():q["symbol"]="SENSEX"
    snapshot["identity"]="SENSEX:23000"
    lab.process("SENSEX",NOW,[],spot,[],snapshot,quotes)
    assert len(lab.repo.trades())==4
    assert all(t["setup"].startswith("SENSEX:") for t in lab.repo.trades())
    process(lab,NOW.replace(hour=16))
    assert not any(t["symbol"]=="NIFTY" for t in lab.repo.trades())


def test_fixed_pair_resets_after_signal_boundary(tmp_path,monkeypatch):
    from trading_analysis.expiry.feed import ExpiryFeed
    from trading_analysis.scalping.feed import ScalpingFeed
    feed=ExpiryFeed(SimpleNamespace(),tmp_path)
    prices=[]
    monkeypatch.setattr(ScalpingFeed,"select_pair",lambda self,s,p,n:prices.append(p))
    feed.select_pair("NIFTY",23000,NOW.replace(hour=9,minute=56))
    feed.select_pair("NIFTY",23100,NOW.replace(hour=10,minute=0))
    feed.select_pair("NIFTY",23150,NOW.replace(hour=10,minute=1))
    assert prices==[23000,23000,23150]


def test_portfolio_daily_loss_circuit_persists(lab):
    process(lab)
    for t in lab.repo.trades(variant=None):
        t.update(status="closed",exit_time=NOW.isoformat(),net_pnl=-26000 if t["portfolio_accepted"] else -10,net_r=-1)
        lab.repo.save_trade(t)
    process(lab,NOW+timedelta(minutes=5))
    new=[t for t in lab.repo.trades() if t["status"]=="open"]
    assert len(new)==4 and not any(t["portfolio_accepted"] for t in new)


def test_transactional_decision_race_creates_once(lab):
    from concurrent.futures import ThreadPoolExecutor
    process(lab)
    original=lab.repo.trades()[0]
    decision=dict(id="race",setup="NIFTY:Expiry_4",checked_at=NOW.isoformat())
    for t in lab.repo.trades(open_only=True):
        t.update(status="closed",exit_time=NOW.isoformat());lab.repo.save_trade(t)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:lab.repo.record_decision(dict(decision),dict(original)),range(4)))
    assert sum(r is not None for r in results)==1


def test_quotes_can_recover_inside_short_entry_window_without_duplicates(lab):
    s,q,p=market();q[1]["received_at"]=(NOW-timedelta(seconds=10)).isoformat()
    process(lab,spot=s,quotes=q,snapshot=p)
    assert not lab.repo.trades()
    assert lab.states["NIFTY:Expiry_1"]["reason"].startswith("waiting_for_quotes")
    process(lab,NOW+timedelta(seconds=2))
    process(lab,NOW+timedelta(seconds=3))
    assert len(lab.repo.trades())==4
    decisions=lab.repo.decisions(NOW.date().isoformat())
    assert all(len(d["quote_wait_attempts"])==1 for d in decisions)
    assert lab.states["NIFTY:Expiry_1"]["reason"]=="paper_entry"


def test_portfolio_exit_does_not_change_independent_research(lab,monkeypatch):
    process(lab)
    monkeypatch.setitem(rules.POLICY,"daily_loss",1)
    process(lab,NOW+timedelta(seconds=1))
    assert all(t["status"]=="open" for t in lab.repo.trades())
    portfolio=lab.repo.portfolio_trades()
    assert len(portfolio)==1 and portfolio[0]["exit_reason"]=="portfolio_daily_loss"
    assert lab._portfolio_state(NOW)["halted"]
