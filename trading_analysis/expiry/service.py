"""Independent paper ledgers with a shared, capital-limited portfolio overlay."""
from collections import Counter
from datetime import datetime, timedelta, time
import json
from math import isfinite
from pathlib import Path
import threading

from trading_analysis import diagnostics
from trading_analysis.audit_exports import date_window, days, trade_overlaps
from trading_analysis.live_timing import IST, as_ist
from trading_analysis.notifications.telegram import TelegramNotifier
from trading_analysis.scheduler.market_hours import is_market_hours
from trading_analysis.storage import DEFAULT_DB_PATH
from trading_analysis.trade_context import capture_vix
from trading_analysis.scalping.rules import aggregate, INDICES
from trading_analysis.expiry.feed import ExpiryFeed
from trading_analysis.expiry.repository import ExpiryRepository
from trading_analysis.expiry.rules import VERSION, SYMBOLS, SETUPS, POLICY, RULES, technical, fresh, premium_evidence, select_spread, spread_quote


def summary(trades):
    closed=sorted([t for t in trades if t["status"]=="closed" and t.get("performance_eligible")],key=lambda t:t["exit_time"])
    values=[t["net_pnl"] for t in closed]
    gain,loss=sum(max(0,x) for x in values),-sum(min(0,x) for x in values)
    equity=peak=dd=0
    for x in values:
        equity+=x;peak=max(peak,equity);dd=max(dd,peak-equity)
    return dict(open=sum(t["status"]=="open" for t in trades),closed=len(closed),net_pnl=sum(values),
        win_rate=100*sum(x>0 for x in values)/len(values) if values else None,
        expectancy=sum(values)/len(values) if values else None,profit_factor=gain/loss if loss else None,
        drawdown=dd,excluded=sum(t["status"]=="closed" and not t.get("performance_eligible") for t in trades))


class ExpiryLab:
    def __init__(self, analysis, path=None, feed=None):
        self.path=Path(path or DEFAULT_DB_PATH.with_name("expiry_lab.db"))
        self.repo=ExpiryRepository(self.path)
        self.feed=feed or ExpiryFeed(analysis,self.path.parent.parent/"expiry")
        self._lock=threading.RLock()
        self._shutdown=threading.Event()
        self._threads=[]
        self._recorded={}
        self._last_check={}
        self._pending={}
        self._replay_lock=threading.Lock()
        self.states={f"{s}:{k}":dict(enabled=False,phase="stopped",reason="not_started",started_at=None,
            stopped_at=None,last_checked=None,last_bar=None,telegram_enabled=False) for s in SYMBOLS for k in SETUPS}
        self.repo.interrupt_open("server_restart")

    def start(self, setup=None, telegram_enabled=False, acknowledge=False):
        if acknowledge is not True:
            raise ValueError("Acknowledge unvalidated paper experiments before starting")
        if setup is not None and setup not in self.states:
            raise ValueError("Unknown expiry setup")
        if not isinstance(telegram_enabled,bool):
            raise ValueError("Telegram setting must be boolean")
        if telegram_enabled and not TelegramNotifier.from_env("EXPIRY_").configured():
            raise ValueError("Configure EXPIRY_TELEGRAM_BOT_TOKEN and EXPIRY_TELEGRAM_CHAT_ID")
        now=datetime.now(IST)
        with self._lock:
            for key in ([setup] if setup else self.states):
                if not self.states[key]["enabled"]:
                    self.states[key].update(enabled=True,phase="starting",reason="waiting_for_data",started_at=now.isoformat(),stopped_at=None,telegram_enabled=telegram_enabled)
                    self.repo.event("started",now,setup=key,version=VERSION,policy=POLICY)
            self.feed.start()
            if not self._threads:
                jobs=[(self._refresh,5,"feed")]+[(lambda s=s:self.tick(s),1,s) for s in SYMBOLS]
                self._threads=[threading.Thread(target=self._loop,args=(fn,period),daemon=True,name="expiry-"+name) for fn,period,name in jobs]
                for thread in self._threads:thread.start()
            if telegram_enabled:self.repo.outbox.start()
        return {"ok":True,"mode":"unvalidated_paper"}

    def stop(self, setup=None):
        if setup is not None and setup not in self.states:raise ValueError("Unknown expiry setup")
        now=datetime.now(IST)
        with self._lock:
            for key in ([setup] if setup else self.states):
                self.states[key].update(enabled=False,stopped_at=now.isoformat(),phase="draining" if self.repo.trades(setup=key,open_only=True,variant=None) else "stopped")
                self.repo.event("entries_stopped_exits_continue",now,setup=key)
            if not self._symbols():self.feed.stop()
        return {"ok":True}

    def _symbols(self):
        return {k.split(":")[0] for k,s in self.states.items() if s["enabled"]}|{t["symbol"] for t in self.repo.trades(open_only=True,variant=None)}

    def _loop(self, fn, period):
        while not self._shutdown.is_set():
            try:fn()
            except Exception as exc:
                self.repo.event("worker_error",datetime.now(IST),error=diagnostics.clean(str(exc)))
                diagnostics.record("expiry_worker_error",area="expiry",error=str(exc))
            self._shutdown.wait(period)

    def _refresh(self):
        now=datetime.now(IST)
        with self._lock:symbols=sorted(self._symbols())
        if not symbols or not is_market_hours(now):return
        selected=[]
        for symbol in symbols:
            try:
                rows=self.feed.analysis._instruments_for_exchange(INDICES[symbol][2])
                expires=any(r.get("name")==INDICES[symbol][3] and r.get("instrument_type") in {"CE","PE"} and str(r.get("expiry"))[:10]==now.date().isoformat() for r in rows)
                if expires:selected.append(symbol)
                else:
                    with self._lock:
                        for k,s in self.states.items():
                            if k.startswith(symbol+":") and s["enabled"]:s.update(phase="waiting_expiry",reason="no_contract_expiring_today")
            except Exception as exc:
                with self.feed.lock:self.feed.state[symbol].update(phase="error",error=diagnostics.clean(str(exc)))
        with self.feed.lock:
            self.feed.retained={t[leg]["token"] for t in self.repo.trades(open_only=True,variant=None) for leg in ("long","short")}
        if selected:self.feed.refresh(selected,now)

    def tick(self,symbol,now=None):
        now=as_ist(now or datetime.now(IST))
        with self._lock:
            if symbol not in self._symbols():return
        rows,spot,history,snapshot=self.feed.snapshot(symbol,now)
        with self.feed.lock:quotes={token:dict(q) for token,q in self.feed.quotes.items() if isinstance(token,int) and q.get("symbol")==symbol}
        with self._lock:
            if is_market_hours(now) and snapshot.get("expiry")==now.date().isoformat():
                first=self._recorded.get(symbol,{}).get("day")!=now.date()
                previous=self._recorded.get(symbol,{})
                repair=first or (now-previous.get("repair",now)).total_seconds()>=60
                selected=rows if first else [c for c in rows if as_ist(c.timestamp)>previous.get("last_bar",now) or (repair and as_ist(c.timestamp)>=now-timedelta(minutes=10))]
                self.repo.frame(symbol,now,dict(version=VERSION,symbol=symbol,time=now.isoformat(),
                    rows=[[c.timestamp.isoformat(),c.open,c.high,c.low,c.close,c.volume] for c in selected],
                    spot=spot,snapshot=snapshot,quotes=quotes,enabled=[k for k,s in self.states.items() if s["enabled"]]))
                self._recorded[symbol]={"day":now.date(),"last_bar":max((as_ist(c.timestamp) for c in rows),default=now),"repair":now if repair else previous["repair"]}
            self.process(symbol,now,rows,spot,history,snapshot,quotes)

    def _portfolio_state(self, now):
        trades=self.repo.portfolio_trades()
        today=now.date().isoformat()
        daily=[t for t in trades if t["entry_time"][:10]==today]
        pnl=sum((t.get("net_pnl") or 0) if t["status"]=="closed" else (t.get("mark_pnl") or 0) for t in daily)
        unknown=any(t["status"]=="open" and (t.get("mark_pnl") is None or not t.get("mark_time") or (now-as_ist(t["mark_time"])).total_seconds()>3) for t in trades)
        unknown |= any(t["status"]=="closed" and not t.get("performance_eligible") for t in daily)
        halted=any(t.get("portfolio_halt_day")==today for t in daily)
        return dict(trades=trades,open=[t for t in trades if t["status"]=="open"],pnl=pnl,unknown=unknown,halted=halted)

    def process(self,symbol,now,rows,spot,history,snapshot,quotes):
        """Serialized cached-data analysis; replay uses this exact entry/exit path."""
        self._exits(symbol,now,rows,spot,quotes)
        keys=[f"{symbol}:{k}" for k in SETUPS]
        if not is_market_hours(now):
            for key in keys:
                if self.states[key]["enabled"]:self.states[key].update(phase="waiting_market",reason="market_closed",last_checked=now.isoformat())
            return
        if snapshot.get("expiry")!=now.date().isoformat():
            for key in keys:
                if self.states[key]["enabled"]:self.states[key].update(phase="waiting_expiry",reason="no_fresh_expiry_pair",last_checked=now.isoformat())
            return
        reading=technical(rows,now)
        if not time(9,35)<=now.time().replace(tzinfo=None)<time(13,30):
            reading={**reading,"signals":{},"reason":"outside_entry_window"}
        for setup in SETUPS:
            key=f"{symbol}:{setup}";state=self.states[key]
            if not state["enabled"]:continue
            state.update(phase="watching",last_checked=now.isoformat(),last_bar=reading.get("closed_at"))
            bar=reading.get("closed_at") or now.replace(second=0,microsecond=0).isoformat()
            identifier=f"{VERSION}:{key}:{bar}"
            if self.repo.seen(identifier):continue
            signal=reading["signals"].get(setup)
            decision=dict(id=identifier,setup=key,checked_at=now.isoformat(),signal_closed_at=reading.get("closed_at"),
                indicators=reading["indicators"],signal=signal,reason=reading["reason"] if reading["reason"]!="evaluated" else "no_signal")
            trade=None
            if signal:
                age=(now-as_ist(reading["closed_at"])).total_seconds()
                decision["signal_delay_seconds"]=age
                evidence=premium_evidence(history,now,signal["direction"])
                decision["option_evidence"]=evidence
                prior=self.repo.trades(day=now.date().isoformat(),setup=key)
                if any(t["status"]=="open" for t in prior):decision["reason"]="already_open"
                elif len(prior)>=POLICY["entries_per_setup"]:decision["reason"]="daily_entry_limit"
                elif not 0<=age<=POLICY["signal_age_seconds"]:decision["reason"]="signal_too_old"
                elif not fresh(spot,now):decision["reason"]="spot_stale"
                elif abs(spot["price"]-signal["reference"])>.25*signal["risk"]:decision["reason"]="entry_drift"
                elif not evidence["premium_pass"]:decision["reason"]=evidence["reason"]
                else:
                    spread,reason=select_spread(signal,snapshot,quotes,now)
                    decision["reason"]=reason
                    if spread:
                        portfolio=self._portfolio_state(now)
                        risk=sum(t["risk_budget"] for t in portfolio["open"])+spread["risk_budget"]
                        accepted=not portfolio["unknown"] and not portfolio["halted"] and portfolio["pnl"]>-POLICY["daily_loss"] and risk<=POLICY["portfolio_budget"] and not any(t["symbol"]==symbol for t in portfolio["open"])
                        liquidation=spread_quote(spread["long"],spread["short"],now,spread["quantity"],entry=False)
                        initial_mark=((liquidation["value"]-spread["debit"])*spread["quantity"]-spread["entry_fill"]["fees"]-liquidation["fees"]) if liquidation else None
                        trade={**spread,"symbol":symbol,"direction":signal["direction"],"entry_time":now.isoformat(),
                            "signal_closed_at":reading["closed_at"],"signal_delay_seconds":age,"entry_spot":spot["price"],
                            "stop_level":signal["stop"],"initial_risk_points":signal["risk"],"trailing_active":False,
                            "portfolio_accepted":accepted,"portfolio_reason":"accepted" if accepted else "overlap_risk_unknown_or_daily_limit",
                            "telegram_enabled":state["telegram_enabled"],"india_vix_entry":capture_vix(now),
                            "performance_eligible":True,"mark_pnl":initial_mark,"mark_time":now.isoformat(),
                            "last_observed":now.isoformat(),"price_basis":"paper_option_spread_bid_ask_with_cost_allowance"}
                        decision["reason"]="paper_entry"
            transient={"spot_stale","option_snapshot_stale","option_warmup","option_quote_invalid_stale_or_wide",
                       "unsynchronized_quotes","spot_snapshot_stale","stale_wide_unsynchronized_or_insufficient_depth",
                       "missing_long_quote","missing_short_quote"}
            if signal and decision["reason"] in transient and decision.get("signal_delay_seconds",999)<POLICY["signal_age_seconds"]:
                self._pending.setdefault(identifier,[]).append({"time":now.isoformat(),"reason":decision["reason"]})
                state["reason"]="waiting_for_quotes: "+decision["reason"]
                continue
            decision["quote_wait_attempts"]=self._pending.pop(identifier,[])
            # Abandoned observations never become later entries; retain their reason for audit.
            for old in [k for k in self._pending if k.startswith(f"{VERSION}:{key}:") and k!=identifier]:
                self.repo.event("quote_window_abandoned",now,setup=key,decision_id=old,attempts=self._pending.pop(old))
            created=self.repo.record_decision(decision,trade)
            if created and created["telegram_enabled"]:self.repo.outbox.start()
            state["reason"]=decision["reason"]

    def _exits(self,symbol,now,rows,spot,quotes):
        for trade in [t for t in self.repo.trades(open_only=True,variant=None) if t["symbol"]==symbol]:
            if trade.get("monitoring_interrupted"):trade["performance_eligible"]=False
            if trade["entry_time"][:10]<now.date().isoformat():
                trade.update(status="closed",exit_time=now.isoformat(),exit_reason="missed_session_exit_unknown",performance_eligible=False,net_pnl=None,net_r=None)
                self.repo.save_trade(trade)
                if trade["telegram_enabled"]:self.repo.outbox.start()
                continue
            if not is_market_hours(now):continue
            if (now-as_ist(trade["last_observed"])).total_seconds()>10:
                trade["monitoring_interrupted"]="observation_gap"
                trade["performance_eligible"]=False
            trade["last_observed"]=now.isoformat()
            reason=trade.get("pending_exit")
            if now.time().replace(tzinfo=None)>=time(15,10):reason=reason or "session_exit"
            if (now-as_ist(trade["entry_time"])).total_seconds()>=POLICY["hold_minutes"]*60:reason=reason or "holding_limit"
            if fresh(spot,now):
                sign=1 if trade["direction"]=="bullish" else -1
                if (spot["price"]-trade["stop_level"])*sign<=0:reason=reason or "structural_stop"
                if (spot["price"]-trade["entry_spot"])*sign>=trade["initial_risk_points"]:trade["trailing_active"]=True
                if trade["trailing_active"]:
                    closed=aggregate([c for c in rows if as_ist(c.timestamp)+timedelta(minutes=1)<=now],5)
                    recent=[c for c in closed if c.timestamp.date()==now.date()][-3:]
                    if len(recent)==3:
                        trail=min(c.low for c in recent) if sign==1 else max(c.high for c in recent)
                        trade["stop_level"]=max(trade["stop_level"],trail) if sign==1 else min(trade["stop_level"],trail)
            else:
                trade["monitoring_interrupted"]="stale_spot_stop_monitoring"
                trade["performance_eligible"]=False
            fill=spread_quote(quotes.get(trade["long"]["token"],{}),quotes.get(trade["short"]["token"],{}),now,trade["quantity"],entry=False)
            if fill:
                trade["mark_pnl"]=(fill["value"]-trade["debit"])*trade["quantity"]-trade["entry_fill"]["fees"]-fill["fees"]
                trade["mark_time"]=now.isoformat()
                if trade["mark_pnl"]<=-trade["risk_budget"]:reason=reason or "risk_budget"
            else:
                trade["mark_pnl"]=None
                trade["monitoring_interrupted"]="unexecutable_option_quote"
                trade["performance_eligible"]=False
            self.repo.save_trade(trade)
            portfolio=self._portfolio_state(now)
            if trade["variant"]=="portfolio" and (portfolio["pnl"]<=-POLICY["daily_loss"] or portfolio["halted"]):
                reason="portfolio_daily_loss"
                trade["portfolio_halt_day"]=now.date().isoformat()
            if reason:
                trade["pending_exit"]=reason
                if fill:
                    trade.update(status="closed",exit_time=now.isoformat(),exit_reason=reason,exit_fill=fill,
                        net_pnl=trade["mark_pnl"],net_r=trade["mark_pnl"]/trade["risk_budget"],
                        open_seconds=(now-as_ist(trade["entry_time"])).total_seconds(),india_vix_exit=capture_vix(now))
                self.repo.save_trade(trade)
                if trade["status"]=="closed" and trade["telegram_enabled"]:self.repo.outbox.start()
        self._last_check[symbol]=now
        for key,state in self.states.items():
            if key.startswith(symbol+":") and not state["enabled"] and not self.repo.trades(setup=key,open_only=True,variant=None):state["phase"]="stopped"
        if self.feed.active and not self._symbols():self.feed.stop()

    def export(self,from_date=None,to_date=None,include_frames=False):
        first,last=date_window(from_date=from_date,to_date=to_date)
        trades=[t for t in self.repo.trades() if trade_overlaps(t,first,last)]
        portfolio_trades=[t for t in self.repo.portfolio_trades() if trade_overlaps(t,first,last)]
        identifiers={t["id"] for t in trades}
        report=dict(version=VERSION,policy=POLICY,rules=RULES,from_date=first,to_date=last,trades=trades,portfolio_trades=portfolio_trades,
            decisions=[r for day in days(first,last) for r in self.repo.decisions(day)],
            events=[r for day in days(first,last) for r in self.repo.events(day)],
            delivery=[r for day in days(first,last) for r in self.repo.outbox.history(day)],
            manual_fills=[r for r in self.repo.manual_rows() if r["trade_id"] in identifiers],
            validation=self.repo.replay_report(),price_basis="paper_spread_not_spot_or_broker_fill",
            limitations=["No historical option validation until recorded quotes are available; replay is not independent validation.",
                "Costs are scenario allowances; actual fees and fills can differ. Partial executions are not assumed filled.",
                "No breadth, futures-volume or VWAP gate. OI is context only. Research P&Ls must not be summed as a portfolio."],
            csv_columns=["debit","quantity","long","short","entry_fill","exit_fill","net_pnl","risk_budget","portfolio_accepted","portfolio_reason","signal_closed_at","signal_delay_seconds","performance_eligible","monitoring_interrupted"])
        if include_frames:report["frames"]=self.repo.frames(first,last)
        return report

    def status(self,from_date=None,to_date=None):
        report=self.export(from_date,to_date)
        now=datetime.now(IST)
        with self._lock:
            states=json.loads(json.dumps(self.states))
            portfolio=self._portfolio_state(now)
        with self.feed.lock:feeds={s:{**self.feed.state[s],"pair":self.feed.pairs.get(s),"samples":len(self.feed.history[s])} for s in SYMBOLS}
        report.update(states=states,feeds=feeds,now=now.isoformat(),setups=SETUPS,
            running_count=sum(s["enabled"] for s in states.values()),telegram_configured=TelegramNotifier.from_env("EXPIRY_").configured(),
            metrics=[dict(setup=k,**summary([t for t in report["trades"] if t["setup"]==k])) for k in states],
            portfolio={**summary(report["portfolio_trades"]),"current_daily_pnl":portfolio["pnl"],"valuation_incomplete":portfolio["unknown"],"halted":portfolio["halted"]},
            rejection_counts=dict(Counter(d["reason"] for d in report["decisions"])))
        report["decisions"]=sorted(report["decisions"],key=lambda d:d["checked_at"],reverse=True)[:500]
        return report

    def record_manual(self, body):
        allowed={"trade_id","kind","time","quantity","long_price","short_price","charges"}
        if set(body)!=allowed or body["kind"] not in {"entry","exit"}:raise ValueError("Supply exact manual-fill journal fields")
        now=datetime.now(IST);stamp=as_ist(body["time"])
        if stamp>now:raise ValueError("Fill time cannot be in the future")
        trades=[t for t in self.repo.trades() if t["id"]==body["trade_id"]]
        if not trades:raise ValueError("Unknown trade ID")
        trade=trades[0]
        if stamp.date().isoformat()!=trade["long"]["expiry"] or stamp<as_ist(trade["entry_time"]):
            raise ValueError("Fill must follow the paper signal on its expiry session")
        for key in ("quantity","long_price","short_price","charges"):
            if isinstance(body[key],bool) or not isinstance(body[key],(int,float)) or not isfinite(body[key]) or body[key]<0:raise ValueError("Invalid fill value")
        if body["quantity"]<=0 or int(body["quantity"])!=body["quantity"] or min(body["long_price"],body["short_price"])<=0:raise ValueError("Positive quantity and prices required")
        if body["quantity"] % trade["long"]["lot_size"]:raise ValueError("Quantity must be a multiple of the contract lot size")
        self.repo.manual({**body,"time":stamp.isoformat(),"recorded_at":now.isoformat(),"basis":"user_reported_not_broker_verified"})
        return {"ok":True}

    def replay(self,from_date=None,to_date=None):
        from trading_analysis.expiry.replay import replay_frames
        first,last=date_window(from_date=from_date,to_date=to_date)
        if not self._replay_lock.acquire(blocking=False):raise ValueError("Replay already running")
        try:
            report=replay_frames(self.repo.frames(first,last))
            report.update(from_date=first,to_date=last)
            self.repo.save_replay(report)
            return report
        finally:self._replay_lock.release()
