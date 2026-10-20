from contextlib import closing
from types import SimpleNamespace
import json

import pytest

from trading_analysis.nifty_lab.service import NiftySetupLab
from trading_analysis.scalping.service import ScalpingLab
from trading_analysis.expiry.service import ExpiryLab


@pytest.mark.parametrize("kind", ["nifty", "scalping", "expiry"])
def test_history_independent_of_review_and_export_dates(tmp_path, monkeypatch, kind):
    runner = {"nifty":NiftySetupLab,"scalping":ScalpingLab,"expiry":ExpiryLab}[kind](SimpleNamespace(),tmp_path/"lab.db")
    setup = {"nifty":"Nifty_Setup1","scalping":"NIFTY:Scalp1","expiry":"NIFTY:Expiry_1"}[kind]
    variant = {"nifty":"technical","scalping":"A","expiry":"paper"}[kind]
    if kind == "nifty":monkeypatch.setattr(runner,"candles",lambda frame:[])
    with closing(runner.repo.connect()) as conn, conn:
        for i,day in enumerate(("2026-10-12","2026-10-19")):
            trade=dict(id=f"T{i}",decision_id=f"D{i}",setup=setup,variant=variant,symbol="NIFTY",status="closed",entry_time=day+"T10:00:00+05:30",exit_time=day+"T10:10:00+05:30",net_r=1,net_pnl=100,performance_eligible=True,open_seconds=600)
            conn.execute("INSERT INTO lab_trades VALUES(?,?,?,'closed',?,?,?)",(trade["id"],setup,variant,day,day,json.dumps(trade)))
    def status(day):return runner.status(day) if kind=="nifty" else runner.status(day,day)
    def export(day):return runner.export(day) if kind=="nifty" else runner.export(day,day)
    before = runner.repo.trades()
    for day in ("2026-10-19","2026-10-20"):
        view=status(day)
        assert len(view["trade_history"]) == 2
        assert len(view["trades"]) == (1 if day=="2026-10-19" else 0)
        packet=export(day)
        assert "trade_history" not in packet
        assert len(packet["trades"]) == (1 if day=="2026-10-19" else 0)
    assert runner.repo.trades()==before


def test_first_monitoring_gap_survives_repeated_interrupts(tmp_path):
    runner=ScalpingLab(SimpleNamespace(),tmp_path/"lab.db")
    trade=dict(id="T",setup="NIFTY:Scalp1",variant="A",status="open",entry_time="2026-10-19T10:00:00+05:30")
    with closing(runner.repo.connect()) as conn,conn:
        conn.execute("INSERT INTO lab_trades VALUES('T','NIFTY:Scalp1','A','open','2026-10-19',NULL,?)",(json.dumps(trade),))
    runner.repo.interrupt_open("quote_gap",trade["setup"],{"detected_at":"first"})
    runner.repo.interrupt_open("worker_gap",trade["setup"],{"detected_at":"later"})
    saved=runner.repo.trades()[0]
    assert saved["monitoring_interrupted"]=="quote_gap"
    assert saved["first_monitoring_gap"]["detected_at"]=="first"


def test_mcx_history_keeps_results_outside_export_range(tmp_path, monkeypatch):
    from trading_analysis.mcx.service import McxSwingScanner
    runner=McxSwingScanner(path=tmp_path/"mcx.db",data_dir=tmp_path/"data")
    trades=[dict(id=day, root="GOLDM",symbol="GOLDMFUT",status="closed",direction="bullish",entry_price=100,
                 exit_price=95,entry_time=day+"T10:00:00+05:30",exit_time=day+"T11:00:00+05:30",
                 net_r=-1,performance_eligible=True,open_seconds=3600) for day in ["2026-10-12","2026-10-19"]]
    monkeypatch.setattr(runner.repository,"trades",lambda **kwargs: [] if kwargs.get("open_only") else [dict(t) for t in trades])
    report=runner.status("2026-10-19","2026-10-19")
    assert len(report["trade_history"])==2 and len(report["trades"])==1
    assert all(t["result"]["points"]==-5 for t in report["trade_history"])
    exported=runner.export("2026-10-19","2026-10-19")
    assert "trade_history" not in exported and len(exported["trades"])==1
