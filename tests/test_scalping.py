from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

from trading_analysis.live_timing import IST
from trading_analysis.models import Candle
from trading_analysis.scalping import rules
from trading_analysis.scalping.feed import ScalpingFeed
from trading_analysis.scalping.service import ScalpingLab, ScalpRepository, metrics
from trading_analysis.scalping.research import replay, run_research

NOW = datetime(2026, 10, 16, 11, 0, 1, tzinfo=IST)


def quote(price=106, now=NOW, **extra):
    return {"price": price, "quote_time": now.isoformat(), "received_at": now.isoformat(), **extra}


def bars():
    rows = []
    for day, count in ((15, 375), (16, 105)):
        start = NOW.replace(day=day, hour=9, minute=15, second=0)
        rows.extend(Candle(start + timedelta(minutes=i), 100, 101, 99, 100, 0) for i in range(count))
    return rows


@pytest.mark.parametrize("setup", rules.SETUPS)
@pytest.mark.parametrize("bearish", [False, True])
def test_closed_rules_symmetric_no_future(monkeypatch, setup, bearish):
    rows = bars()
    rows[-2] = Candle(rows[-2].timestamp, 103, 105, 101, 104, 0)
    rows[-1] = Candle(rows[-1].timestamp, 102, 107, 101, 106, 0)
    def ema(values, period):
        value = 105 if period == 9 else 100 if period == 50 else 103 if len(values) == 95 else 104
        if setup == "Scalp3":
            value = 100
        return 200 - value if bearish else value
    monkeypatch.setattr(rules, "ema", ema)
    monkeypatch.setattr(rules, "atr", lambda _: 10)
    if setup == "Scalp3":
        rows[-2] = Candle(rows[-2].timestamp, 99, 101, 98, 100, 0)
        rows[-1] = Candle(rows[-1].timestamp, 100, 104, 99, 103, 0)
    if bearish:
        rows = [Candle(c.timestamp, 200-c.open, 200-c.low, 200-c.high, 200-c.close, 0) for c in rows]
    result = rules.technical(rows, NOW)
    assert setup in result["signals"], result
    assert result["signals"][setup]["direction"] == ("bearish" if bearish else "bullish")
    future = Candle(NOW.replace(second=0), 200, 300, 100, 250, 0)
    assert rules.technical(rows + [future], NOW) == result


def test_gaps_duplicates_and_forming_bars():
    rows = bars()
    assert rules.technical(rows[:-1], NOW)["reason"] == "stale_candles"
    assert rules.technical(rows[:-3] + rows[-2:], NOW)["reason"] == "recent_candle_gap"
    assert len(rules.aggregate(rows + [rows[-1]])) == len(rows)-1
    assert not rules.technical(rows[:100], NOW)["ready"]


def sample(now, ce=100, pe=100, oi=1000, volume=1000, spot=100, identity="same"):
    def option(price, token):
        return quote(price, now, bid=price-.1, ask=price+.1, bid_qty=10, ask_qty=10, token=token, oi=oi, volume=volume)
    return {"time": now.isoformat(), "identity": identity, "spot": quote(spot, now), "CE": option(ce, 1), "PE": option(pe, 2)}


def history():
    return [sample(NOW-timedelta(minutes=3)), sample(NOW-timedelta(minutes=1), ce=101, pe=99, oi=1010),
            sample(NOW, ce=103, pe=97, oi=1030, volume=1100, spot=102)]


def test_price_and_oi_layers_and_fixed_contracts():
    rows = history()
    evidence = rules.premium_evidence(rows, NOW, "bullish")
    assert evidence["premium_pass"] and evidence["oi_pass"]
    assert not rules.premium_evidence(rows, NOW, "bearish")["premium_pass"]
    for row in rows:
        row["CE"]["oi"] = row["PE"]["oi"] = 1000
    evidence = rules.premium_evidence(rows, NOW, "bullish")
    assert evidence["premium_pass"] and not evidence["oi_pass"]
    rows[1]["identity"] = "changed"
    assert rules.premium_evidence(rows, NOW, "bullish")["reason"] == "atm_changed_warming_up"


@pytest.mark.parametrize("mutation", ["wide", "stale", "token", "future", "depth", "unsync"])
def test_bad_premium_evidence_blocks(mutation):
    rows = history()
    last = rows[-1]["CE"]
    if mutation == "wide": last["ask"] += 10
    if mutation == "stale": last["quote_time"] = (NOW-timedelta(seconds=10)).isoformat()
    if mutation == "token": last["token"] = 9
    if mutation == "future": rows[-1]["time"] = (NOW+timedelta(seconds=1)).isoformat()
    if mutation == "depth": last["bid_qty"] = 0
    if mutation == "unsync": last["quote_time"] = (NOW-timedelta(seconds=2.5)).isoformat()
    assert not rules.premium_evidence(rows, NOW, "bullish")["premium_pass"]


def fake_lab(tmp_path, monkeypatch):
    feed = ScalpingFeed(SimpleNamespace(), tmp_path / "candles")
    feed.start()
    feed.connected = True
    feed.quotes["NIFTY"] = quote()
    monkeypatch.setattr(feed, "snapshot", lambda symbol, now: ([], feed.quotes.get(symbol, {}), history(), history()[-1]))
    lab = ScalpingLab(SimpleNamespace(), tmp_path / "lab.db", feed)
    key = "NIFTY:Scalp1"
    lab.states[key].update(enabled=True)
    reading = {"ready": True, "reason": "evaluated", "bar_time": NOW.replace(minute=59, hour=10, second=0).isoformat(),
               "closed_at": NOW.replace(second=0).isoformat(), "indicators": {},
               "signals": {"Scalp1": {"direction": "bullish", "reference": 106, "stop": 100, "risk": 6, "atr": 10}}}
    monkeypatch.setattr("trading_analysis.scalping.service.technical", lambda *args: reading)
    monkeypatch.setattr(lab.repo.outbox, "start", Mock())
    return lab, key


def test_abc_ledgers_duplicate_suppression_stop_drains(tmp_path, monkeypatch):
    lab, key = fake_lab(tmp_path, monkeypatch)
    lab.tick("NIFTY", NOW)
    lab.tick("NIFTY", NOW)
    assert len(lab.repo.trades()) == 3
    assert len(lab.repo.decisions(NOW.date().isoformat())) == 1
    lab.stop(key)
    assert lab.states[key]["phase"] == "draining"
    lab.feed.quotes["NIFTY"] = quote(116, NOW+timedelta(seconds=2))
    lab.tick("NIFTY", NOW+timedelta(seconds=2))
    assert all(t["status"] == "closed" for t in lab.repo.trades())
    assert lab.states[key]["phase"] == "stopped"
    assert not lab.repo.outbox.history()


def test_option_failure_does_not_block_technical(tmp_path, monkeypatch):
    lab, key = fake_lab(tmp_path, monkeypatch)
    monkeypatch.setattr(lab.feed, "snapshot", lambda *args: ([], quote(), [], {}))
    lab.tick("NIFTY", NOW)
    assert [t["variant"] for t in lab.repo.trades()] == ["A"]


def test_stale_spot_no_entries_and_expired_no_retry(tmp_path, monkeypatch):
    lab, key = fake_lab(tmp_path, monkeypatch)
    lab.feed.quotes["NIFTY"] = quote(now=NOW-timedelta(seconds=15))
    lab.tick("NIFTY", NOW)
    assert not lab.repo.trades()
    lab.tick("NIFTY", NOW+timedelta(seconds=11))
    assert not lab.repo.trades()
    assert lab.repo.decisions(NOW.date().isoformat())[0]["variants"]["A"] == "signal_expired"


def test_restart_and_gap_excluded_from_performance(tmp_path, monkeypatch):
    lab, key = fake_lab(tmp_path, monkeypatch)
    lab.tick("NIFTY", NOW)
    lab.tick("NIFTY", NOW+timedelta(seconds=10))
    assert all(t.get("monitoring_interrupted") for t in lab.repo.trades())
    lab.feed.quotes["NIFTY"] = quote(116, NOW+timedelta(seconds=12))
    lab.tick("NIFTY", NOW+timedelta(seconds=12))
    assert all(not t["performance_eligible"] for t in lab.repo.trades())


def test_repository_atomic_concurrent_duplicate_and_outbox(tmp_path):
    repo = ScalpRepository(tmp_path / "lab.db")
    decision = {"id": "test", "setup": "SENSEX:Scalp1", "checked_at": NOW.isoformat(), "variants": {"A": "passed"}}
    trade = {"symbol": "SENSEX", "entry_time": NOW.isoformat(), "entry_price": 100, "direction": "bullish",
             "signal_closed_at": NOW.isoformat(), "stop_level": 99, "target_level": 101.5, "telegram_enabled": True}
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: repo.record(dict(decision), {"A": trade}), range(12)))
    assert sum(len(r) for r in results) == 1
    assert len(repo.outbox.history()) == 1
    with repo.connect() as conn:
        assert conn.execute("SELECT prefix FROM alert_delivery_outbox").fetchone()[0] == "SCALP_"


def test_status_export_date_range(tmp_path, monkeypatch):
    lab, key = fake_lab(tmp_path, monkeypatch)
    lab.tick("NIFTY", NOW)
    report = lab.status("2026-10-15", "2026-10-17")
    assert len(report["metrics"]) == 27
    assert len(report["states"]) == 9
    assert len(report["trades"]) == 3
    assert report["version"] == rules.VERSION
    with pytest.raises(ValueError): lab.export("2026-10-17", "2026-10-15")


def test_research_empty_cache_honest_coverage(tmp_path):
    feed = ScalpingFeed(SimpleNamespace(), tmp_path)
    result = run_research(feed)
    assert len(result["rows"]) == 324
    assert all(r["coverage"] == "missing" and r["win_rate"] is None for r in result["rows"])
    assert result["validation"] == "not_promoted"


def test_replay_stop_first_gap_price_and_costs():
    start = NOW.replace(second=0)
    rows = [Candle(start-timedelta(minutes=1),100,101,99,100,0), Candle(start,100,103,98,102,0)]
    signals = {0: {"closed_at":start.isoformat(),"signals":{"Scalp1":{"direction":"bullish","reference":100,"stop":99,"risk":1,"atr":2}}}}
    result = replay(rows, signals, "Scalp1", 2)
    assert len(result) == 1 and result[0]["ambiguous"] and result[0]["net_r"] < -1


def test_stream_only_finalizes_complete_minutes(tmp_path):
    feed = ScalpingFeed(SimpleNamespace(), tmp_path)
    start = NOW.replace(minute=59,hour=10,second=0)
    for i in range(61):
        at = start + timedelta(seconds=i)
        feed._bar("NIFTY", quote(100+i/100, at), at)
    assert len(feed.rows["NIFTY"]) == 1
    assert feed.rows["NIFTY"][0].close == 100.59
    feed.forming.clear()
    feed.rows["NIFTY"].clear()
    for seconds in (10, 59, 60):
        at=start+timedelta(seconds=seconds)
        feed._bar("NIFTY", quote(100,at),at)
    assert not feed.rows["NIFTY"]


def test_preaggregated_research_matches_live_reading():
    rows = bars()
    for frame in (1,3):
        prepared = (rows, rules.aggregate(rows,frame)[-300:], rules.aggregate(rows,5)[-300:])
        assert rules.technical(rows, NOW, frame) == rules.technical([],NOW,frame,prepared)


def test_clock_skew_is_bounded_without_rewriting_timestamps():
    q = quote()
    q["quote_time"] = (NOW+timedelta(seconds=1.64)).isoformat()
    assert rules.fresh(q,NOW)
    q["quote_time"] = (NOW+timedelta(seconds=4)).isoformat()
    assert not rules.fresh(q,NOW)


def test_unknown_exit_is_not_invented_profit(tmp_path,monkeypatch):
    lab,key=fake_lab(tmp_path,monkeypatch)
    lab.tick("NIFTY",NOW)
    lab._exits("NIFTY",{},NOW+timedelta(days=1))
    assert all(t["status"]=="closed" and t["exit_price"] is None and t["net_r"] is None for t in lab.repo.trades())
    assert not lab.repo.outbox.history()


def test_disconnected_stream_cannot_enter(tmp_path,monkeypatch):
    lab,key=fake_lab(tmp_path,monkeypatch)
    lab.feed.connected=False
    lab.tick("NIFTY",NOW)
    assert not lab.repo.trades()
    assert lab.states[key]["reason"]=="stream_disconnected"


def test_delayed_scalp_telegram_expires_without_sending(tmp_path):
    repo=ScalpRepository(tmp_path/"lab.db")
    decision={"id":"test","setup":"NIFTY:Scalp1","checked_at":NOW.isoformat(),"variants":{"A":"passed"}}
    trade={"symbol":"NIFTY","entry_time":NOW.isoformat(),"entry_price":100,"direction":"bullish",
           "signal_closed_at":NOW.isoformat(),"stop_level":99,"target_level":102,"telegram_enabled":True}
    repo.record(decision,{"A":trade})
    sender=Mock()
    repo.outbox.drain(sender=sender,now=NOW+timedelta(seconds=16))
    sender.send_message.assert_not_called()
    assert repo.outbox.history()[0]["status"]=="expired"


def test_one_minute_historical_download_is_chunked():
    from trading_analysis.brokers.zerodha import historical_windows
    windows=historical_windows(NOW-timedelta(days=190),NOW,"minute")
    assert len(windows)==7
    assert all(end-start<=timedelta(days=30) for start,end in windows)
    assert windows[0][0]==NOW-timedelta(days=190) and windows[-1][1]==NOW


def test_report_persists_across_restart(tmp_path,monkeypatch):
    lab,key=fake_lab(tmp_path,monkeypatch)
    lab.research={"phase":"completed","rows":[{"coverage":"missing"}]}
    lab._save_research()
    again=ScalpingLab(SimpleNamespace(),tmp_path/"lab.db",lab.feed)
    assert again.research==lab.research


def test_cancelled_research_stops_between_symbols(tmp_path):
    with pytest.raises(InterruptedError):
        run_research(ScalpingFeed(SimpleNamespace(),tmp_path),cancelled=lambda:True)


def test_invalid_controls_and_duplicate_start(tmp_path,monkeypatch):
    lab,key=fake_lab(tmp_path,monkeypatch)
    with pytest.raises(ValueError): lab.start([])
    with pytest.raises(ValueError): lab.stop({})
    with pytest.raises(ValueError): lab.start(key, telegram_enabled="yes")
    lab.research["phase"]="running"
    with pytest.raises(ValueError): lab.start(key)


def test_contract_selection_keeps_expiry_and_retains_open_contract(tmp_path):
    feed=ScalpingFeed(SimpleNamespace(),tmp_path)
    catalog=[]
    for expiry in ("2026-10-20","2026-10-27"):
        for strike in (90,100,110,120):
            for side in ("CE","PE"):
                catalog.append({"name":"NIFTY","instrument_type":side,"expiry":expiry,"strike":str(strike),
                                "instrument_token":str(len(catalog)+1),"tradingsymbol":f"N{expiry}{strike}{side}"})
    feed.catalog["NIFTY"]=(NOW.date(),catalog)
    feed.select_pair("NIFTY",100,NOW)
    old=feed.pairs["NIFTY"]["CE"]
    assert feed.pairs["NIFTY"]["expiry"]=="2026-10-20"
    feed.retained={old}
    feed.history["NIFTY"].append(sample(NOW))
    feed.select_pair("NIFTY",120,NOW)
    assert not feed.history["NIFTY"]
    assert old in feed.tokens
    assert feed.pairs["NIFTY"]["strike"]==120


def test_live_tick_timestamps_after_quote_copy_without_false_gap(tmp_path, monkeypatch):
    lab, key = fake_lab(tmp_path, monkeypatch)
    lab.tick("NIFTY", NOW)
    clock = Mock()
    clock.now.return_value = NOW + timedelta(seconds=1)
    monkeypatch.setattr("trading_analysis.scalping.service.datetime", clock)
    monkeypatch.setattr("trading_analysis.scalping.feed.datetime", clock)
    received = NOW + timedelta(seconds=1, microseconds=699)

    def updated_snapshot(symbol, now=None):
        lab.feed.quotes[symbol] = quote(116, received)
        clock.now.return_value = received
        return [], dict(lab.feed.quotes[symbol]), [], {"time": received.isoformat()}

    monkeypatch.setattr(lab.feed, "snapshot", updated_snapshot)
    lab.tick("NIFTY")
    trades = lab.repo.trades()
    assert len(trades) == 3
    assert all(t["status"] == "closed" and t["performance_eligible"] for t in trades)
    assert all(not t.get("monitoring_interrupted") for t in trades)
    assert all(t["exit_time"] == received.isoformat() for t in trades)


def test_observation_copies_option_quotes_and_keeps_explicit_replay_time(tmp_path):
    feed = ScalpingFeed(SimpleNamespace(), tmp_path)
    future = NOW + timedelta(microseconds=699)
    feed.quotes["NIFTY"] = quote(106, future)
    feed.quotes[1] = quote(100, future, symbol="NIFTY", token=1)
    at, _, spot, _, _, options = feed.observe("NIFTY", NOW)
    assert at == NOW
    assert not rules.fresh(spot, at)
    assert not rules.fresh(options[1], at)
    feed.quotes[1]["price"] = 500
    assert options[1]["price"] == 100


def test_exit_uses_option_quote_from_same_observation(tmp_path, monkeypatch):
    lab, key = fake_lab(tmp_path, monkeypatch)
    lab.tick("NIFTY", NOW)
    at = NOW + timedelta(seconds=1)
    option = quote(110, at, token=1, bid=109.9, ask=110.1, bid_qty=10, ask_qty=10)
    lab.feed.quotes[1] = {**option, "received_at": (at + timedelta(seconds=1)).isoformat()}
    lab._exits("NIFTY", quote(116, at), at, {1: option})
    assert all(t["option_exit"]["price"] == 110 for t in lab.repo.trades())
