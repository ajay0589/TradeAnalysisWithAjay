"""Five independent cached-data workers, sharing one candle and one option refresher."""
import csv
from contextlib import closing
from datetime import datetime, time, timedelta
import json
from pathlib import Path
import threading
import uuid

from trading_analysis import diagnostics
from trading_analysis.brokers.zerodha import merge_candles_csv, resolve_instrument_token
from trading_analysis.candles import candle_path
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.live_quotes import LIVE_QUOTES
from trading_analysis.live_timing import IST, as_ist, expected_closed_bar, prepare_live_entry, quote_is_fresh
from trading_analysis.nifty.live_scanner import candle_close_time, closed_candles
from trading_analysis.nifty_lab.options import option_evidence
from trading_analysis.nifty_lab.repository import LabRepository, comparisons, day_value
from trading_analysis.nifty_lab.setups import RISK, SETUPS, VARIANTS, VERSION, technical_read
from trading_analysis.notifications.telegram import TelegramNotifier
from trading_analysis.option_history import OptionHistory
from trading_analysis.scheduler.market_hours import is_market_hours, is_scan_window
from trading_analysis.storage import DEFAULT_DB_PATH


class NiftySetupLab:
    def __init__(self, analysis, path=None, quotes=None):
        self.analysis = analysis
        self.path = Path(path or DEFAULT_DB_PATH.with_name("nifty_setup_lab.db"))
        self.repo = LabRepository(self.path)
        self.options = OptionHistory(self.path)
        self.quotes = quotes or LIVE_QUOTES
        self._lock = threading.RLock()
        self._cache_lock = threading.Lock()
        self._cache = {}
        self._threads = []
        self._shutdown = threading.Event()
        self._refresh_attempts = {}
        self._owner = "NIFTY_SETUP_LAB"
        self.states = {key: {"enabled": False, "generation": 0, "phase": "stopped", "started_at": None,
                            "stopped_at": None, "last_checked": None, "last_bar": None, "reason": "not_started",
                            "telegram_enabled": False, "run_id": None, "error": None} for key in SETUPS}
        self.data_state = {"candles": {}, "options": {"status": "stopped"}}

    def _master(self, exchange, now):
        path = self.analysis.nfo_instruments_path.with_name(f"instruments_{exchange}.csv")
        if not path.exists() or (now - datetime.fromtimestamp(path.stat().st_mtime, IST)).total_seconds() > 7 * 86400:
            raise ValueError(f"Refresh {exchange} instrument master in Data Ops")

    def start(self, setup=None, telegram_enabled=False):
        if setup is not None and (not isinstance(setup, str) or setup not in SETUPS):
            raise ValueError("Unknown setup")
        selected = [setup] if setup else list(SETUPS)
        if any(key not in SETUPS for key in selected) or not isinstance(telegram_enabled, bool):
            raise ValueError("Choose Nifty_Setup1 through Nifty_Setup5 and a boolean Telegram setting")
        now = datetime.now(IST)
        if telegram_enabled and not TelegramNotifier.from_env("NIFTY_LAB_").configured():
            raise ValueError("Configure NIFTY_LAB_TELEGRAM_BOT_TOKEN and NIFTY_LAB_TELEGRAM_CHAT_ID, or leave Telegram paper alerts off")
        self._master("NSE", now)
        with self._lock:
            for key in selected:
                if self.states[key]["enabled"]:
                    continue
                if self.states[key]["run_id"] is None:
                    self.repo.interrupt_open("server_restart", key)
                self.states[key].update(enabled=True, generation=self.states[key]["generation"] + 1,
                                        phase="starting", started_at=now.isoformat(), stopped_at=None,
                                        run_id=uuid.uuid4().hex, telegram_enabled=telegram_enabled, error=None)
                self.repo.event("started", now, setup=key, run_id=self.states[key]["run_id"], version=VERSION,
                                telegram_enabled=telegram_enabled)
            self.quotes.acquire(self._owner)
            self._ensure_workers()
            if telegram_enabled:
                self.repo.outbox.start()
        return self.status()

    def stop(self, setup=None):
        if setup is not None and (not isinstance(setup, str) or setup not in SETUPS):
            raise ValueError("Unknown setup")
        now = datetime.now(IST)
        with self._lock:
            for key in [setup] if setup else SETUPS:
                if self.states[key]["enabled"]:
                    self.repo.interrupt_open("monitor_stopped", key)
                    self.states[key].update(enabled=False, generation=self.states[key]["generation"] + 1,
                                            phase="stopped", stopped_at=now.isoformat())
                    self.repo.event("stopped", now, setup=key, run_id=self.states[key]["run_id"])
            if not any(s["enabled"] for s in self.states.values()):
                self.quotes.release(self._owner)
                for feed in self.data_state.values():
                    feed["status"] = "stopped"
        return self.status()

    def _ensure_workers(self):
        if self._threads:
            return
        jobs = [(lambda key=key: self._setup_tick(key), 2, key) for key in SETUPS]
        jobs += [(self.refresh_candles, 5, "lab-candles"), (self.refresh_options, 60, "lab-options")]
        self._threads = [threading.Thread(target=self._loop, args=(fn, seconds, name), name=name, daemon=True)
                         for fn, seconds, name in jobs]
        for thread in self._threads:
            thread.start()

    def _loop(self, fn, seconds, name):
        while not self._shutdown.is_set():
            try:
                fn()
            except Exception as exc:
                self._error(name, exc)
            self._shutdown.wait(seconds)

    def _error(self, name, exc):
        error = diagnostics.clean(str(exc))
        now = datetime.now(IST)
        with self._lock:
            if name in self.states:
                self.states[name].update(error=error, phase="error")
            else:
                target = "options" if name == "lab-options" else "candles"
                self.data_state[target].update(error=error, status="failed")
        self.repo.event("error", now, worker=name, error=error)
        diagnostics.record("nifty_lab_error", area="nifty_lab", worker=name, error=error, status="failed")

    def _enabled(self):
        with self._lock:
            return any(s["enabled"] for s in self.states.values())

    def candles(self, frame):
        path = candle_path(self.analysis.daily_data_dir, frame, "NIFTY_50")
        with self._cache_lock:
            if not path.exists():
                return []
            stamp = (path.stat().st_mtime_ns, path.stat().st_size)
            if self._cache.get(frame, (None,))[0] != stamp:
                self._cache[frame] = (stamp, load_candles(path))
            return self._cache[frame][1]

    def refresh_candles(self):
        if not self._enabled() or not is_scan_window():
            return
        from trading_analysis.web_services import _zerodha_client
        now = datetime.now(IST)
        self._master("NSE", now)
        token = resolve_instrument_token(self.analysis._instruments_for_exchange("NSE"), "NSE", "NIFTY 50")
        for frame in ("5minute", "15minute", "day"):
            if not self._enabled():
                return
            now = datetime.now(IST)
            if (now - self._refresh_attempts.get(frame, now - timedelta(days=1))).total_seconds() < 20:
                continue
            try:
                rows = closed_candles(self.candles(frame), frame, now)
                expected = expected_closed_bar(frame, now)
                if rows and as_ist(rows[-1].timestamp) == expected and len(rows) >= (2 if frame == "day" else 60):
                    continue
                self._refresh_attempts[frame] = now
                with self._lock:
                    self.data_state["candles"].update(status="refreshing", current=frame, started_at=now.isoformat())
                start = as_ist(rows[-1].timestamp) - timedelta(days=2) if len(rows) >= 60 else now - timedelta(days=45)
                fresh = _zerodha_client().historical_candles(token, frame, start, now)
                merge_candles_csv(candle_path(self.analysis.daily_data_dir, frame, "NIFTY_50"), fresh)
                with self._lock:
                    self.data_state["candles"].update(status="waiting", last_success=datetime.now(IST).isoformat(), error=None)
            except Exception as exc:
                self._error("lab-candles", f"{frame}: {exc}")

    def refresh_options(self):
        if not self._enabled() or not is_market_hours():
            return
        now = datetime.now(IST)
        self._master("NFO", now)
        with self._lock:
            self.data_state["options"].update(status="refreshing", started_at=now.isoformat())
        result = self.analysis.refresh_option_chain_snapshot(symbol="NIFTY", strikes_around=8, max_snapshots=8)
        with Path(result["history_snapshot"]).open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        saved = self.options.save("NIFTY", rows)
        if not saved:
            raise ValueError("Empty option snapshot; combined variants remain blocked")
        with self._lock:
            self.data_state["options"].update(status="waiting", last_success=saved, error=None)

    def evidence(self, now):
        return option_evidence(self.options.load("NIFTY", now - timedelta(minutes=25), now), now)

    def _setup_tick(self, setup):
        with self._lock:
            state = dict(self.states[setup])
        if not state["enabled"]:
            return
        now = datetime.now(IST)
        try:
            self.tick(setup, now, state)
        except Exception as exc:
            self._error(setup, exc)

    def tick(self, setup, now, state=None):
        now = as_ist(now)
        with self._lock:
            state = state or dict(self.states[setup])
        if not state["enabled"]:
            return
        quote = self.quotes.get("NIFTY")
        self.check_exits(setup, now, quote, state["generation"])
        if not is_market_hours(now):
            self._progress(setup, state, now, "waiting_market", "market_closed")
            return
        sources = {frame: self.candles(frame) for frame in SETUPS[setup]["sources"]}
        base = closed_candles(sources[SETUPS[setup]["frame"]], SETUPS[setup]["frame"], now)
        if base and as_ist(base[-1].timestamp) == expected_closed_bar(SETUPS[setup]["frame"], now) and self.repo.seen(self.repo.decision_id(setup, as_ist(base[-1].timestamp).isoformat())):
            self._progress(setup, state, now, "waiting_candle", "closed_candle_already_checked")
            return
        reading = technical_read(setup, sources, now)
        if not reading["ready"]:
            self._progress(setup, state, now, "waiting_data", reading["reason"])
            return
        identifier = self.repo.decision_id(setup, reading["bar_time"])
        if self.repo.seen(identifier):
            self._progress(setup, state, now, "waiting_candle", "closed_candle_already_checked", reading)
            return
        checks, prepared = {}, None
        if reading["direction"]:
            sign = 1 if reading["direction"] == "bullish" else -1
            reference, risk = reading["indicators"]["close"], reading["indicators"]["atr14"]
            signal = {"horizon": "intraday", "direction": reading["direction"], "entry_price": reference,
                      "stop_level": reference - sign * risk, "target_level": reference + sign * risk * 2,
                      "metadata": {"signal_candle_closed_at": reading["closed_at"], "atr14": risk}}
            prepared = prepare_live_entry(signal, quote, now, checks)
            if prepared and as_ist(quote["quote_time"]) < as_ist(reading["closed_at"]):
                checks["gate"] = "spot_quote_before_signal_close"
            if checks.get("gate") in {"spot_quote_unavailable_or_stale", "spot_quote_before_signal_close"}:
                self._progress(setup, state, now, "waiting_quote", checks["gate"], reading)
                return
            if now.time().replace(tzinfo=None) >= time(14, 30):
                prepared = None
                checks["gate"] = "entry_cutoff"
        # Option availability never blocks or delays the technical variant.
        try:
            evidence = self.evidence(now) if reading["direction"] else {}
        except Exception as exc:
            evidence = {"bias": "unavailable", "reason": "option_history_error", "error": diagnostics.clean(str(exc))}
        reason = checks.get("gate") or reading["reason"]
        variants = {key: reason for key in VARIANTS}
        entries = {}
        if prepared:
            trade = {"direction": reading["direction"], "entry_price": prepared["entry_price"], "entry_time": now.isoformat(),
                     "stop_level": prepared["stop_level"], "target_level": prepared["target_level"],
                     "risk_points": prepared["risk_points"], "reference_price": reading["indicators"]["close"],
                     "quote": quote, "price_basis": "observed_spot_not_fill", "frame": SETUPS[setup]["frame"],
                     "signal_closed_at": reading["closed_at"], "signal_delay_seconds": checks.get("signal_delay_seconds"),
                     "cost_bps_per_side": RISK["cost_bps_per_side"], "max_holding_minutes": RISK["max_holding_minutes"],
                     "telegram_enabled": state["telegram_enabled"], "run_id": state["run_id"]}
            entries["technical"] = trade
            if evidence.get("state") == "ready" and evidence.get("bias") == reading["direction"]:
                entries["combined"] = trade
            else:
                variants["combined"] = "options_" + str(evidence.get("reason") or evidence.get("bias"))
        decision = {"id": identifier, "setup": setup, "checked_at": now.isoformat(), "run_id": state["run_id"],
                    "version": VERSION, **reading, "entry_checks": checks, "option_evidence": evidence, "variants": variants}
        with self._lock:
            if not self._valid(setup, state["generation"]):
                return
            created = self.repo.record(decision, entries)
        if created and state["telegram_enabled"]:
            self.repo.outbox.start()
        self._progress(setup, state, now, "waiting_candle", "entry_created" if created else reason, reading)

    def _valid(self, setup, generation):
        return self.states[setup]["enabled"] and self.states[setup]["generation"] == generation

    def _progress(self, setup, state, now, phase, reason, reading=None):
        with self._lock:
            if self._valid(setup, state["generation"]):
                previous = self.states[setup]
                if (previous["phase"], previous["reason"]) != (phase, reason):
                    self.repo.event("progress", now, setup=setup, phase=phase, reason=reason)
                self.states[setup].update(phase=phase, reason=reason, last_checked=now.isoformat(), error=None)
                if reading:
                    self.states[setup].update(last_bar=reading["bar_time"], indicators=reading.get("indicators", {}))

    def check_exits(self, setup, now, quote, generation):
        for trade in self.repo.trades(setup=setup, open_only=True):
            entry = as_ist(trade["entry_time"])
            reason, price, stamp, basis = None, None, now, "observed_spot_not_fill"
            if now.date() == entry.date() and is_market_hours(now) and quote_is_fresh(quote, now) and as_ist(quote["quote_time"]) >= entry:
                price = float(quote["price"])
                sign = 1 if trade["direction"] == "bullish" else -1
                if (price - trade["stop_level"]) * sign <= 0:
                    reason = "stop_loss"
                elif (price - trade["target_level"]) * sign >= 0:
                    reason = "target_reached"
                elif now.time().replace(tzinfo=None) >= time(15, 20):
                    reason = "session_exit"
                elif (now - entry).total_seconds() >= trade["max_holding_minutes"] * 60:
                    reason = "holding_limit"
            elif now > entry.replace(hour=15, minute=30, second=0, microsecond=0):
                final = [c for c in closed_candles(self.candles("5minute"), "5minute", now)
                         if as_ist(c.timestamp).date() == entry.date() and as_ist(c.timestamp).time().replace(tzinfo=None) == time(15, 25)]
                if final:
                    price, stamp = final[-1].close, candle_close_time(final[-1].timestamp, "5minute")
                    reason, basis = "missed_session_exit_reference", "recovered_close_not_fill"
            if reason:
                with self._lock:
                    if not self._valid(setup, generation):
                        return
                    closed = self.repo.close(trade, price, stamp, reason, basis, now)
                if closed and trade["telegram_enabled"] and basis == "observed_spot_not_fill":
                    self.repo.outbox.start()

    def status(self, day=None):
        now = datetime.now(IST)
        day = day_value(day or now.date().isoformat())
        trades, decisions = self.repo.trades(day), self.repo.decisions(day)
        with self._lock:
            states, sources = json.loads(json.dumps(self.states)), json.loads(json.dumps(self.data_state))
        freshness = []
        for frame in ("5minute", "15minute", "day"):
            try:
                rows = closed_candles(self.candles(frame), frame, now)
                latest = as_ist(rows[-1].timestamp) if rows else None
                expected = expected_closed_bar(frame, now)
                freshness.append({"frame": frame, "state": "fresh" if latest == expected else "stale" if latest else "missing",
                                  "latest": latest.isoformat() if latest else None, "expected": expected.isoformat()})
            except Exception as exc:
                freshness.append({"frame": frame, "state": "error", "error": diagnostics.clean(str(exc))})
        try:
            evidence = self.evidence(now)
        except Exception as exc:
            evidence = {"bias": "unavailable", "reason": diagnostics.clean(str(exc))}
        quote = self.quotes.get("NIFTY")
        quote_health = self.quotes.status() if hasattr(self.quotes, "status") else {}
        return {"date": day, "now": now.isoformat(), "version": VERSION, "mode": "paper_only", "setups": SETUPS, "risk": RISK,
                "states": states, "running_count": sum(s["enabled"] for s in states.values()), "data_service": sources,
                "freshness": freshness, "spot": quote, "spot_fresh": quote_is_fresh(quote, now),
                "quote_feed": {k: quote_health.get(k) for k in ("mode", "error", "stream_connected")}, "option_evidence": evidence,
                "comparisons": comparisons(trades, decisions, day), "trades": trades,
                "decisions": decisions[:250], "decision_count": len(decisions),
                "delivery": self.repo.outbox.history(day), "events": self.repo.events(day)[-100:],
                "telegram_configured": TelegramNotifier.from_env("NIFTY_LAB_").configured()}

    def export(self, day=None):
        report = self.status(day)
        day = report["date"]
        begin = as_ist(day + "T00:00:00")
        report.update(decisions=self.repo.decisions(day), events=self.repo.events(day),
                      option_snapshots=self.options.load("NIFTY", begin, begin + timedelta(days=1) - timedelta(microseconds=1)),
                      runtime=diagnostics.runtime(), timezone="Asia/Kolkata",
                      limitations=["Forward paper observations, not broker fills or option P&L.",
                                   "Interrupted monitoring and recovered session references are excluded from performance.",
                                   "Daily performance groups exits by IST date. No exchange holiday calendar.",
                                   "Polling may miss between-check stop/target touches. No performance guarantee."])
        identifiers = {t["decision_id"] for t in report["trades"]}
        with closing(self.repo.connect()) as conn:
            report["trade_entry_decisions"] = [json.loads(row[0]) for identifier in sorted(identifiers)
                                               for row in conn.execute("SELECT body FROM lab_decisions WHERE id=?", (identifier,))]
        return diagnostics.clean(report)
