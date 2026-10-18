"""Offline deterministic replay of recorded observations; never downloads or alerts."""
from collections import deque
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from trading_analysis.expiry.rules import VERSION, SYMBOLS
from trading_analysis.live_timing import as_ist
from trading_analysis.models import Candle


def replay_frames(frames):
    from trading_analysis.expiry.service import ExpiryLab, summary
    if not frames:
        return {"phase":"unavailable","samples":0,"reason":"No recorded expiry option quotes in this range. Spot-only P&L is not substituted.","metrics":[]}
    if any(f.get("version")!=VERSION for f in frames):
        raise ValueError("Recording version differs; replay with its original rule version")
    rows={s:{} for s in SYMBOLS};history={s:deque(maxlen=1200) for s in SYMBOLS}
    identities={};gaps=0;last={}
    with TemporaryDirectory(prefix="expiry-replay-") as root:
        lab=ExpiryLab(SimpleNamespace(),Path(root)/"paper.db")
        for frame in frames:
            symbol=frame["symbol"];now=as_ist(frame["time"])
            if symbol in last and now.date()==last[symbol].date() and (now-last[symbol]).total_seconds()>10:gaps+=1
            last[symbol]=now
            for raw in frame["rows"]:
                c=Candle(as_ist(raw[0]),*raw[1:]);rows[symbol][c.timestamp]=c
            identity=frame["snapshot"].get("identity")
            if identities.get(symbol)!=identity:history[symbol].clear()
            identities[symbol]=identity;history[symbol].append(frame["snapshot"])
            for key,state in lab.states.items():state["enabled"]=key in frame["enabled"]
            lab.process(symbol,now,[rows[symbol][k] for k in sorted(rows[symbol])][-5000:],frame["spot"],
                list(history[symbol]),frame["snapshot"],{int(k):v for k,v in frame["quotes"].items()})
        trades=lab.repo.trades()
        report={"phase":"completed","samples":len(frames),"observation_gaps":gaps,"version":VERSION,
            "reason":"Recorded-session reproducibility only; not held-out historical validation",
            "unresolved_open":sum(t["status"]=="open" for t in trades),
            "metrics":[dict(setup=k,**summary([t for t in trades if t["setup"]==k])) for k in lab.states],"trades":trades,
            "portfolio_trades":lab.repo.portfolio_trades(),"portfolio":summary(lab.repo.portfolio_trades())}
    return report
