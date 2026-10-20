"""Paper-only scalp workers with isolated ledgers and durable audit/alert delivery."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, time
import hashlib
import json
from pathlib import Path
import threading

from trading_analysis import diagnostics
from trading_analysis.audit_exports import date_window, days, trade_overlaps
from trading_analysis.live_timing import IST, as_ist
from trading_analysis.nifty_lab.repository import LabRepository
from trading_analysis.notifications.telegram import TelegramNotifier
from trading_analysis.scalping.feed import ScalpingFeed
from trading_analysis.scalping.rules import INDICES, SETUPS, VARIANTS, VERSION, POLICY, technical, fresh, usable_option, premium_evidence
from trading_analysis.scheduler.market_hours import is_market_hours
from trading_analysis.storage import DEFAULT_DB_PATH
from trading_analysis.trade_context import capture_vix
from trading_analysis.futures_context import FUTURES


class ScalpRepository(LabRepository):
    def close_unknown(self, trade, now):
        trade = {**trade, "status": "closed", "exit_time": now.isoformat(), "exit_price": None, "net_r": None,
                 "exit_reason": "missed_session_exit_unknown", "exit_price_basis": "unknown_exit_excluded",
                 "performance_eligible": False, "monitoring_interrupted": "missed_session_exit",
                 "open_seconds": None}
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE lab_trades SET status='closed',exit_day=?,body=? WHERE id=? AND status='open'",
                         (now.date().isoformat(), json.dumps(trade), trade["id"]))

    def record(self, decision, entries):
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM lab_decisions WHERE id=?", (decision["id"],)).fetchone():
                return []
            created = []
            for variant, trade in entries.items():
                if conn.execute("SELECT 1 FROM lab_trades WHERE setup=? AND variant=? AND status='open'", (decision["setup"], variant)).fetchone():
                    decision["variants"][variant] = "already_open"
                    continue
                digest = hashlib.sha256((decision["id"] + variant).encode()).hexdigest()[:12].upper()
                trade = {**trade, "id": "SC-" + digest, "decision_id": decision["id"], "variant": variant,
                         "setup": decision["setup"], "version": VERSION, "status": "open",
                         "india_vix_entry": capture_vix(trade["entry_time"])}
                conn.execute("INSERT INTO lab_trades VALUES(?,?,?,'open',?,NULL,?)", (trade["id"], trade["setup"], variant, trade["entry_time"][:10], json.dumps(trade)))
                if trade["telegram_enabled"]:
                    self._enqueue(conn, trade, "entry", trade["entry_time"])
                decision["variants"][variant] = "entry_created"
                created.append(trade)
            conn.execute("INSERT INTO lab_decisions VALUES(?,?,?,?)", (decision["id"], decision["setup"], decision["checked_at"][:10], json.dumps(decision)))
            return created

    def _enqueue(self, conn, trade, kind, now):
        option = trade.get("option_entry") or {}
        message = (f"SCALPING LAB - EXPERIMENTAL PAPER {kind.upper()}\n{trade['setup']} | {VARIANTS[trade['variant']]} | {trade['direction']}\n"
                   f"Trade: {trade['id']}\nObserved spot entry: {trade['entry_price']:.2f}\n"
                   f"Signal close: {trade['signal_closed_at']}\nDetected: {trade['entry_time']} IST\n"
                   f"Spot stop / target: {trade['stop_level']:.2f} / {trade['target_level']:.2f}\n")
        if option:
            message += f"Option reference: {option['tradingsymbol']} | expiry {option['expiry']} | strike {option['strike']} | ask {option['ask']:.2f}\n"
        if kind == "exit":
            message += f"Observed spot exit: {trade['exit_price']:.2f} at {trade['exit_time']} IST\nReason: {trade['exit_reason']}\nDuration: {trade['open_seconds']/60:.1f} min\n"
        message += "Unvalidated experiment. Observations, not fills. No order placed."
        conn.execute("INSERT OR IGNORE INTO alert_delivery_outbox(event_key,symbol,trade_id,event_kind,prefix,message,status,created_at,updated_at,next_attempt_at) VALUES(?,?,?,?,?,?,'queued',?,?,?)",
                     (f"SCALP:{trade['id']}:{kind}", trade["symbol"], trade["id"], kind, "SCALP_", message, now, now, now))


def metrics(trades, first, last):
    closed = sorted((t for t in trades if t.get("exit_time") and first <= t["exit_time"][:10] <= last and t.get("performance_eligible")),
                    key=lambda t: (t["exit_time"], t["id"]))
    values = [t["net_r"] for t in closed]
    gain, loss = sum(max(0, v) for v in values), -sum(min(0, v) for v in values)
    equity = peak = dd = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return {"open": sum(t["status"] == "open" for t in trades), "closed": len(closed),
            "win_rate": 100 * sum(v > 0 for v in values) / len(values) if values else None,
            "net_r": equity, "expectancy_r": equity / len(values) if values else None,
            "profit_factor": gain / loss if loss else None, "max_drawdown_r": dd,
            "excluded": sum(t["status"] == "closed" and not t.get("performance_eligible") for t in trades)}


class ScalpingLab:
    def __init__(self, analysis, path=None, feed=None):
        self.path = Path(path or DEFAULT_DB_PATH.with_name("scalping_lab.db"))
        self.repo = ScalpRepository(self.path)
        self.feed = feed or ScalpingFeed(analysis, self.path.parent.parent / "scalping")
        self._lock = threading.RLock()
        self._threads = []
        self._shutdown = threading.Event()
        self._research_cancel = threading.Event()
        self._last_exit_check = {}
        self._feed_errors = {}
        self.research = {"phase": "idle", "rows": [], "error": None}
        with closing(self.repo.connect()) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS scalping_research(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL)")
            saved = conn.execute("SELECT body FROM scalping_research WHERE id=1").fetchone()
            if saved:
                self.research = json.loads(saved[0])
                if self.research["phase"] == "running":
                    self.research.update(phase="interrupted", error="Research interrupted by server restart; rerun explicitly")
        self.states = {f"{symbol}:{setup}": {"enabled": False, "phase": "stopped", "reason": "not_started",
                       "started_at": None, "stopped_at": None, "last_checked": None, "last_bar": None,
                       "telegram_enabled": False} for symbol in INDICES for setup in SETUPS}
        self.repo.interrupt_open("server_restart")

    def start(self, key=None, telegram_enabled=False):
        if key is not None and (not isinstance(key, str) or key not in self.states):
            raise ValueError("Unknown scalping index/setup")
        if not isinstance(telegram_enabled, bool):
            raise ValueError("Telegram setting must be boolean")
        if telegram_enabled and not TelegramNotifier.from_env("SCALP_").configured():
            raise ValueError("Configure SCALP_TELEGRAM_BOT_TOKEN and SCALP_TELEGRAM_CHAT_ID first")
        now = datetime.now(IST)
        with self._lock:
            if self.research["phase"] == "running":
                raise ValueError("Wait for historical research to finish before starting scalping")
            for name in ([key] if key else self.states):
                if self.states[name]["enabled"]:
                    continue
                self.states[name].update(enabled=True, phase="starting", started_at=now.isoformat(), stopped_at=None,
                                         telegram_enabled=telegram_enabled)
                self.repo.event("started", now, setup=name, version=VERSION, policy=POLICY)
            self.feed.start()
            if not self._threads:
                jobs = [(lambda s=s: self.tick(s), 1, s) for s in INDICES]
                jobs.append((self._refresh, 5, "feed"))
                self._threads = [threading.Thread(target=self._loop, args=(fn, seconds, name), daemon=True, name="scalp-" + name) for fn, seconds, name in jobs]
                for thread in self._threads:
                    thread.start()
            if telegram_enabled:
                self.repo.outbox.start()
        return {"ok": True}

    def stop(self, key=None):
        if key is not None and (not isinstance(key, str) or key not in self.states):
            raise ValueError("Unknown scalping index/setup")
        now = datetime.now(IST)
        with self._lock:
            for name in ([key] if key else self.states):
                self.states[name].update(enabled=False, stopped_at=now.isoformat(), phase="draining" if self.repo.trades(setup=name, open_only=True) else "stopped")
                self.repo.event("entries_stopped_exits_continue", now, setup=name)
            if not self._symbols():
                self.feed.stop()
        return {"ok": True}

    def _symbols(self):
        with self._lock:
            return sorted({key.split(":")[0] for key, s in self.states.items() if s["enabled"]}
                          | {t["symbol"] for t in self.repo.trades(open_only=True)})

    def _loop(self, fn, seconds, name):
        while not self._shutdown.is_set():
            try:
                fn()
            except Exception as exc:
                self.repo.event("worker_error", datetime.now(IST), worker=name, error=diagnostics.clean(str(exc)))
                diagnostics.record("scalping_error", area="scalping", worker=name, error=str(exc), status="failed")
            self._shutdown.wait(seconds)

    def _refresh(self):
        symbols = self._symbols()
        if symbols and is_market_hours():
            with self.feed.lock:
                self.feed.retained = {t["option_entry"]["token"] for t in self.repo.trades(open_only=True) if t.get("option_entry")}
            self.feed.refresh(symbols)
            for symbol in symbols:
                error = self.feed.state[symbol].get("error")
                if error != self._feed_errors.get(symbol):
                    self.repo.event("feed_failed" if error else "feed_recovered", datetime.now(IST), symbol=symbol, error=error)
                    self._feed_errors[symbol] = error

    def tick(self, symbol, now=None):
        now = as_ist(now or datetime.now(IST))
        keys = [f"{symbol}:{setup}" for setup in SETUPS]
        with self._lock:
            if not any(self.states[k]["enabled"] for k in keys) and not any(t["symbol"] == symbol for t in self.repo.trades(open_only=True)):
                return
        rows, spot, history, snapshot = self.feed.snapshot(symbol, now)
        self._exits(symbol, spot, now)
        if not is_market_hours(now):
            with self._lock:
                for key in keys:
                    if self.states[key]["enabled"]:
                        self.states[key].update(phase="waiting_market", last_checked=now.isoformat(), reason="market_closed")
            return
        reading = technical(rows, now)
        for setup in SETUPS:
            key = f"{symbol}:{setup}"
            with self._lock:
                state = self.states[key]
                if not state["enabled"]:
                    continue
                state.update(last_checked=now.isoformat(), phase="watching", reason=reading["reason"])
                if not self.feed.connected:
                    state.update(phase="waiting_data", reason="stream_disconnected")
                    continue
                if not reading["ready"]:
                    continue
                identifier = f"{VERSION}:{key}:{reading['bar_time']}"
                state["last_bar"] = reading["bar_time"]
                if self.repo.seen(identifier):
                    state["reason"] = "waiting_next_candle"
                    continue
                signal = reading["signals"].get(setup)
                delay = (now - as_ist(reading["closed_at"])).total_seconds()
                # Wait briefly for a post-close spot quote, not for option confirmation.
                if signal and delay <= POLICY["max_signal_age_seconds"] and (not fresh(spot, now) or as_ist(spot["quote_time"]) < as_ist(reading["closed_at"])):
                    state["reason"] = "waiting_fresh_spot"
                    continue
                evidence = premium_evidence(history, now, signal["direction"]) if signal else {}
                futures = FUTURES.evidence(symbol, now, signal["direction"] if signal else None)
                decision = {"id": identifier, "setup": key, "symbol": symbol, "checked_at": now.isoformat(),
                            **reading, "direction": signal["direction"] if signal else None, "spot": spot,
                            "signal_delay_seconds": delay, "option_evidence": evidence, "futures_evidence": futures, "variants": {}, "version": VERSION}
                entries = {}
                if signal:
                    sign = 1 if signal["direction"] == "bullish" else -1
                    target = signal["reference"] + sign * signal["risk"] * POLICY["target_r"]
                    price = spot.get("price", 0)
                    risk, reward = (price - signal["stop"]) * sign, (target - price) * sign
                    gate = "passed"
                    if delay > POLICY["max_signal_age_seconds"]:
                        gate = "signal_expired"
                    elif not fresh(spot, now):
                        gate = "spot_stale"
                    elif abs(price - signal["reference"]) > min(signal["atr"], signal["risk"]) * .25 or risk <= 0 or reward <= 0 or reward / risk < 1:
                        gate = "drift_or_reward_risk"
                    elif now.time().replace(tzinfo=None) >= time(14, 45):
                        gate = "entry_cutoff"
                    trades = self.repo.trades(day=now.date().isoformat(), setup=key)
                    for variant in VARIANTS:
                        reason = gate
                        own = [t for t in trades if t["variant"] == variant]
                        if any(t["status"] == "open" for t in own):
                            reason = "already_open"
                        elif sum(t["entry_time"][:10] == now.date().isoformat() for t in own) >= POLICY["max_entries_per_ledger_day"]:
                            reason = "daily_entry_limit"
                        elif any(t.get("exit_time") and (now - as_ist(t["exit_time"])).total_seconds() < 60 * POLICY["cooldown_minutes"] for t in own):
                            reason = "cooldown"
                        elif reason == "passed" and variant != "A" and not evidence.get("premium_pass"):
                            reason = evidence.get("reason", "premium_unavailable")
                        elif reason == "passed" and variant == "C" and not evidence.get("oi_pass"):
                            reason = "oi_unconfirmed"
                        decision["variants"][variant] = reason
                        if reason != "passed":
                            continue
                        option = snapshot.get("CE" if sign == 1 else "PE", {})
                        option = dict(option) if usable_option(option, now) and snapshot.get("identity") else None
                        entries[variant] = {"symbol": symbol, "direction": signal["direction"], "entry_price": price,
                            "entry_time": now.isoformat(), "signal_closed_at": reading["closed_at"], "signal_delay_seconds": delay,
                            "reference_price": signal["reference"], "risk_points": risk, "stop_level": signal["stop"], "target_level": target,
                            "cost_bps_per_side": POLICY["cost_bps_side"], "telegram_enabled": state["telegram_enabled"],
                            "price_basis": "observed_spot_not_fill", "option_entry": option, "horizon": "scalp",
                            "option_costs_modeled": False, "policy": POLICY, "last_quote": spot, "futures_evidence": futures}
                else:
                    decision["variants"] = {v: reading["reason"] if reading["reason"] != "evaluated" else "technical_rules_not_met" for v in VARIANTS}
                created = self.repo.record(decision, entries)
                if created and state["telegram_enabled"]:
                    self.repo.outbox.start()
                state["reason"] = "; ".join(f"{v}: {r}" for v, r in decision["variants"].items())

    def _exits(self, symbol, spot, now):
        with self._lock:
            last_check = self._last_exit_check.get(symbol)
            self._last_exit_check[symbol] = now
            for trade in self.repo.trades(open_only=True):
                if trade["symbol"] != symbol:
                    continue
                if as_ist(trade["entry_time"]).date() < now.date():
                    self.repo.close_unknown(trade, now)
                    continue
                if last_check and (now - last_check).total_seconds() > 5:
                    self.repo.interrupt_open("worker_gap", trade["setup"])
                    trade.setdefault("monitoring_interrupted", "worker_gap")
                if not fresh(spot, now):
                    if not trade.get("monitoring_interrupted"):
                        detail = {"detected_at": now.isoformat(), "quote_time": spot.get("quote_time"),
                                  "received_at": spot.get("received_at"), "stream_connected": self.feed.connected,
                                  "max_quote_age_seconds": POLICY["max_quote_age_seconds"]}
                        self.repo.interrupt_open("quote_gap", trade["setup"], detail)
                        self.repo.event("trade_quote_gap", now, trade_id=trade["id"], setup=trade["setup"], **detail)
                    continue
                if as_ist(spot["quote_time"]) < as_ist(trade["entry_time"]):
                    continue
                if not is_market_hours(now):
                    continue
                price, reason = spot["price"], None
                sign = 1 if trade["direction"] == "bullish" else -1
                if (price - trade["stop_level"]) * sign <= 0:
                    reason = "spot_stop"
                elif (price - trade["target_level"]) * sign >= 0:
                    reason = "spot_target"
                elif now.time().replace(tzinfo=None) >= time(15, 20):
                    reason = "session_exit"
                elif (now - as_ist(trade["entry_time"])).total_seconds() >= POLICY["hold_minutes"] * 60:
                    reason = "holding_limit"
                if reason:
                    entry = trade.get("option_entry") or {}
                    with self.feed.lock:
                        option = dict(self.feed.quotes.get(entry.get("token"), {}))
                    trade["option_exit"] = option if usable_option(option, now) else None
                    trade["option_gross_points"] = option["bid"] - entry["ask"] if entry and usable_option(option, now) else None
                    trade["last_quote"] = spot
                    self.repo.close(trade, price, now, reason)
                    if trade["telegram_enabled"]:
                        self.repo.outbox.start()
            for key, state in self.states.items():
                if key.startswith(symbol + ":") and not state["enabled"] and not self.repo.trades(setup=key, open_only=True):
                    state["phase"] = "stopped"
            if self.feed.active and not self._symbols():
                self.feed.stop()

    def export(self, from_date=None, to_date=None, include_futures=True):
        first, last = date_window(from_date=from_date, to_date=to_date)
        decisions, events = [], []
        for day in days(first, last):
            decisions.extend(self.repo.decisions(day))
            events.extend(self.repo.events(day))
        trades = [t for t in self.repo.trades() if trade_overlaps(t, first, last)]
        decisions.sort(key=lambda d: (d["checked_at"], d["id"]), reverse=True)
        with self.feed.lock:
            feed_state = {"connected": self.feed.connected, "indices": json.loads(json.dumps(self.feed.state)),
                          "pairs": json.loads(json.dumps(self.feed.pairs))}
        with self._lock:
            states = json.loads(json.dumps(self.states))
            research = json.loads(json.dumps(self.research))
        return diagnostics.clean({"from_date": first, "to_date": last, "version": VERSION, "policy": POLICY,
                "validation": "unvalidated_paper_only", "trades": trades, "decisions": decisions, "events": events,
                "delivery": [r for day in days(first, last) for r in self.repo.outbox.history(day)],
                "research": research, "runtime": diagnostics.runtime(), "feed_state": feed_state, "states": states,
                **({"futures_samples": FUTURES.export(first, last)} if include_futures else {}),
                "price_basis": "observed spot; indicative option ask-to-bid excluding costs"})

    def status(self, from_date=None, to_date=None):
        report = self.export(from_date, to_date, include_futures=False)
        now = datetime.now(IST)
        with self._lock:
            states = json.loads(json.dumps(self.states))
        with self.feed.lock:
            feeds = {s: {**self.feed.state[s], "spot": self.feed.quotes.get(s), "pair": self.feed.pairs.get(s),
                        "latest_candle": self.feed.rows[s][-1].timestamp.isoformat() if self.feed.rows[s] else None,
                        "option_samples": len(self.feed.history[s])} for s in INDICES}
        summaries = []
        for key in states:
            for variant in VARIANTS:
                selected = [t for t in report["trades"] if t["setup"] == key and t["variant"] == variant]
                summaries.append({"setup": key, "variant": variant, **metrics(selected, report["from_date"], report["to_date"])})
        return {**report, "futures": {s: FUTURES.evidence(s, now) for s in INDICES},
                "trade_history": self.repo.trades(), "trade_delivery": self.repo.outbox.history(),
                "now": now.isoformat(), "states": states, "setups": SETUPS, "variants": VARIANTS,
                "feeds": feeds, "stream_connected": self.feed.connected, "metrics": summaries,
                "telegram_configured": TelegramNotifier.from_env("SCALP_").configured(),
                "running_count": sum(s["enabled"] for s in states.values()),
                "decision_count": len(report["decisions"]), "decisions": report["decisions"][:300],
                "decision_counts": dict(Counter(d["setup"] for d in report["decisions"])),
                "rejections": dict(Counter(v for d in report["decisions"] for v in d["variants"].values()))}

    def start_research(self, download=False):
        if not isinstance(download, bool):
            raise ValueError("download must be boolean")
        with self._lock:
            if self.research["phase"] == "running":
                return {"ok": True, "phase": "already_running"}
            if self._symbols():
                raise ValueError("Stop new scalp entries and wait for exits before running research")
            self.research = {"phase": "running", "rows": [], "error": None, "started_at": datetime.now(IST).isoformat()}
            self._research_cancel.clear()
            self._save_research()
            threading.Thread(target=self._research, args=(download,), name="scalp-research", daemon=True).start()
        return {"ok": True}

    def _research(self, download):
        from trading_analysis.scalping.research import run_research
        try:
            report = run_research(self.feed, download, lambda message: self.research.update(progress=message), self._research_cancel.is_set)
            with self._lock:
                self.research = {**report, "phase": "completed", "completed_at": datetime.now(IST).isoformat()}
                self._save_research()
            self.repo.event("research_completed", datetime.now(IST), report=self.research)
        except InterruptedError as exc:
            with self._lock:
                self.research.update(phase="cancelled", error=str(exc))
                self._save_research()
        except Exception as exc:
            with self._lock:
                self.research.update(phase="failed", error=diagnostics.clean(str(exc)))
                self._save_research()

    def _save_research(self):
        with closing(self.repo.connect()) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO scalping_research VALUES(1,?)", (json.dumps(self.research),))

    def cancel_research(self):
        self._research_cancel.set()
        return {"ok": True}
