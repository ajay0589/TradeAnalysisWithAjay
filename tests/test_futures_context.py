from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from trading_analysis.futures_context import FuturesContext, evaluate, select_contract
from trading_analysis.live_timing import IST

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=IST)
CONTRACT = dict(exchange="NFO", tradingsymbol="NIFTY26OCTFUT", expiry="2026-10-27", lot_size=50, instrument_token=123)


def quotes(at=NOW, price=23000, oi=100000, volume=10000):
    return dict(timestamp=at, last_trade_time=at, spot_timestamp=at, last_price=price, oi=oi,
                volume=volume, average_price=22950,
                depth={"buy": [dict(price=price-.1, quantity=50)], "sell": [dict(price=price+.1, quantity=50)]})


def history(sign=1, oi_sign=1):
    result = []
    for i in range(61):
        at = NOW-timedelta(seconds=(60-i)*15)
        price = 23000+sign*i
        result.append(dict(time=at.isoformat(), quote_time=at.isoformat(), last_trade_time=at.isoformat(),
                           spot_quote_time=at.isoformat(), contract=CONTRACT, price=price, spot=price-50,
                           vwap=23000-sign*10, oi=100000+oi_sign*i*100, volume=10000+i*500, basis=50))
    return result


@pytest.mark.parametrize("sign,oi_sign,interpretation", [(1,1,"long_buildup"),(-1,1,"short_buildup"),(1,-1,"short_covering"),(-1,-1,"long_unwinding")])
def test_multiple_windows_and_oi_are_separate_from_direction(sign, oi_sign, interpretation):
    direction = "bullish" if sign == 1 else "bearish"
    e = evaluate(history(sign,oi_sign), NOW, direction)
    assert e["state"] == "ready" and e["bias"] == direction and e["alignment"] == "aligned"
    assert all(w["interpretation"] == interpretation for w in e["windows"].values())
    assert evaluate(history(sign,oi_sign), NOW, "bearish" if sign==1 else "bullish")["alignment"] == "conflict"
    assert e["mode"] == "shadow_only"


@pytest.mark.parametrize("failure", ["stale","future","gap","rollover","session","volume_reset","spot_conflict","flat_oi"])
def test_no_false_confirmation(failure):
    rows = deepcopy(history())
    now = NOW
    if failure == "stale": now += timedelta(seconds=31)
    if failure == "future": now -= timedelta(seconds=1)
    if failure == "gap": del rows[45:50]
    if failure == "rollover": rows[-1]["contract"] = {**CONTRACT,"tradingsymbol":"NIFTY26NOVFUT"}
    if failure == "session": rows = rows[-1:]
    if failure == "volume_reset": rows[-1]["volume"] = 1
    if failure == "spot_conflict":
        for i,r in enumerate(rows): r["spot"] = 23000-i
    if failure == "flat_oi":
        for r in rows: r["oi"] = 100000
    e = evaluate(rows,now,"bullish")
    if failure == "flat_oi":
        assert e["alignment"] == "aligned"
        assert all(w["interpretation"] == "unchanged_or_small" for w in e["windows"].values())
    else:
        assert e["alignment"] != "aligned"


@pytest.mark.parametrize("failure", ["nan","zero_oi","missing_vwap","stale_trade","sync","wide","no_depth"])
def test_bad_quotes_rejected(tmp_path, failure):
    store = FuturesContext(tmp_path/"futures.db")
    q = quotes()
    if failure == "nan":q["last_price"] = float("nan")
    if failure == "zero_oi":q["oi"] = 0
    if failure == "missing_vwap":q.pop("average_price")
    if failure == "stale_trade":q["last_trade_time"] = NOW-timedelta(seconds=31)
    if failure == "sync":q["spot_timestamp"] = NOW-timedelta(seconds=6)
    if failure == "wide":q["depth"]["sell"][0]["price"] += 100
    if failure == "no_depth":q["depth"] = {}
    store.observe("NIFTY", CONTRACT, q, {"last_price":22950}, NOW)
    assert store.evidence("NIFTY",NOW)["state"] == "unavailable"
    assert not store.samples["NIFTY"]


def test_persisted_export_range_restart_and_contract_roll(tmp_path):
    path = tmp_path/"futures.db"
    store = FuturesContext(path)
    store.observe("NIFTY",CONTRACT,quotes(),{"last_price":22950},NOW)
    store.observe("NIFTY",CONTRACT,quotes(),{"last_price":22950},NOW+timedelta(seconds=15))
    assert len(store.samples["NIFTY"]) == 1  # Repeated exchange quote is not a new sample.
    restarted = FuturesContext(path)
    assert len(restarted.export("2026-10-20","2026-10-20")) == 1
    assert restarted.export("2026-10-19","2026-10-19") == []
    assert restarted.evidence("NIFTY",NOW)["state"] == "unavailable"  # Re-warm after downtime.
    new = {**CONTRACT,"tradingsymbol":"NIFTY26NOVFUT","expiry":"2026-11-24"}
    later = NOW+timedelta(seconds=30)
    store.observe("NIFTY",new,quotes(later),{"last_price":22950},later)
    assert len(store.samples["NIFTY"]) == 1
    assert store.evidence("NIFTY",later)["state"] == "warming_up"


def test_contract_uses_master_expiry_not_weekday():
    base = dict(CONTRACT, name="NIFTY", instrument_type="FUT")
    rows = [dict(base,expiry="2026-10-19"),dict(base,expiry="2026-10-22"),dict(base,expiry="2026-11-01"),dict(base,instrument_type="CE",expiry="2026-10-20")]
    assert select_contract(rows,"NIFTY",NOW)["expiry"] == "2026-10-22"
    assert select_contract(rows,"SENSEX",NOW) is None


def test_poll_batched_and_rate_limited(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path/"data/raw/zerodha"
    path.mkdir(parents=True)
    for exchange in ("NFO","BFO"): (path/f"instruments_{exchange}.csv").touch()
    rows = [dict(CONTRACT,name="NIFTY",instrument_type="FUT")]
    monkeypatch.setattr("trading_analysis.brokers.zerodha.load_instruments_csv", lambda p: rows)
    client = Mock()
    client.quotes.return_value = {}
    monkeypatch.setattr("trading_analysis.web_services._zerodha_client",lambda:client)
    store=FuturesContext(tmp_path/"f.db")
    store.poll(NOW);store.poll(NOW+timedelta(seconds=10))
    client.quotes.assert_called_once_with(["NFO:NIFTY26OCTFUT","NSE:NIFTY 50"])


def test_concurrent_samples_and_export_are_atomic(tmp_path):
    store = FuturesContext(tmp_path/"futures.db")
    def write():
        for i in range(20):
            at = NOW+timedelta(seconds=i*15)
            store.observe("NIFTY", CONTRACT, quotes(at), {"last_price":22950}, at)
    def read():
        for _ in range(20):
            rows = store.export("2026-10-20", "2026-10-20")
            assert all(r["contract"] == CONTRACT and r["oi"] > 0 for r in rows)
            assert len({r["time"] for r in rows}) == len(rows)
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(write), pool.submit(read), pool.submit(read)]
        for job in jobs: job.result()
    assert len(store.export("2026-10-20", "2026-10-20")) == 20
