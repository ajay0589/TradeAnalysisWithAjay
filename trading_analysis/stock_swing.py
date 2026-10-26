"""Live-data F&O swing paper observation using the historical research rules."""
from contextlib import closing
from datetime import datetime, timedelta, time
import hashlib
import json
import math
from pathlib import Path
import threading

from trading_analysis import diagnostics
from trading_analysis.analysis.relative_strength import load_sector_map, sector_config_for_symbol
from trading_analysis.audit_exports import date_window, days, in_window, trade_overlaps
from trading_analysis.brokers.zerodha import merge_candles_csv, resolve_instrument_token
from trading_analysis.candles import candle_path
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.data_sources.fno_universe import current_watchlist_symbols
from trading_analysis.live_timing import IST, as_ist, expected_closed_bar, quote_is_fresh
from trading_analysis.nifty.live_scanner import candle_close_time, closed_candles
from trading_analysis.nifty_lab.repository import LabRepository
from trading_analysis.notifications.telegram import TelegramNotifier
from trading_analysis.scheduler.market_hours import is_market_hours, next_market_open
from trading_analysis.stock_research import SETUPS, context, entry_plan, technical
from trading_analysis.trade_context import capture_vix

VERSION = "fno-swing-paper-v1"
DEFAULTS = {"frame": "both", "benchmark": "NIFTY 50", "variant": "aligned", "target_r": 2,
            "setup": "all", "telegram_enabled": False}


class SwingRepository(LabRepository):
    def record_signal(self, decision, trade=None):
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM lab_decisions WHERE id=?", (decision["id"],)).fetchone():
                return None
            if trade:
                decision["reason"] = "paper_entry"
                active = [json.loads(r[0]) for r in conn.execute("SELECT body FROM lab_trades WHERE status='open'")]
                if any(t["symbol"] == trade["symbol"] for t in active):
                    decision["reason"], trade = "position_already_open", None
            if trade:
                trade = {**trade, "id": "SW-"+hashlib.sha256(decision["id"].encode()).hexdigest()[:16].upper(),
                         "setup": decision["setup"], "decision_id": decision["id"], "variant": "paper",
                         "version": VERSION, "status": "open"}
                conn.execute("INSERT INTO lab_trades VALUES(?,?,?,'open',?,NULL,?)",
                             (trade["id"], trade["setup"], "paper", trade["entry_time"][:10], json.dumps(trade)))
                if trade["telegram_enabled"]:
                    self._enqueue(conn, trade, "entry", trade["entry_time"])
            conn.execute("INSERT INTO lab_decisions VALUES(?,?,?,?)",
                         (decision["id"], decision["setup"], decision["checked_at"][:10], json.dumps(decision)))
        return trade

    def _enqueue(self, conn, trade, kind, now):
        message = (f"F&O SWING - UNVALIDATED PAPER {kind.upper()}\n{trade['symbol']} | {trade['family']} | {trade['direction']}\n"
                   f"Trade: {trade['id']} | Frame: {trade['timeframe']}\n"
                   f"Observed stock entry: {trade['entry_price']:.2f} at {trade['entry_time']} IST\n"
                   f"Stop / target: {trade['stop_level']:.2f} / {trade['target_level']:.2f}\n"
                   f"Signal closed: {trade['signal_time']} | Target R: {trade['target_r']}\n")
        if kind == "exit":
            message += f"Observed exit: {trade['exit_price']:.2f} at {now} IST | {trade['exit_reason']} | net model {trade['net_r']:.2f}R\n"
        message += "Stock spot observation, not an option/futures fill. No order placed. Review events, F&O ban and contract liquidity manually."
        conn.execute("INSERT OR IGNORE INTO alert_delivery_outbox(event_key,symbol,trade_id,event_kind,prefix,message,status,created_at,updated_at,next_attempt_at) VALUES(?,?,?,?,?,?,'queued',?,?,?)",
                     (f"SWING:{trade['id']}:{kind}", trade["symbol"], trade["id"], kind, "PULLBACK_", message, now, now, now))

    def mark(self, trade):
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE lab_trades SET body=? WHERE id=? AND status='open'", (json.dumps(trade), trade["id"]))


class StockSwingScanner:
    def __init__(self, analysis, path="data/db/stock_swing.db", client_factory=None):
        self.analysis, self.path, self.client_factory = analysis, Path(path), client_factory
        self.repo = SwingRepository(self.path)
        self._lock, self._halt = threading.RLock(), threading.Event()
        self._threads, self._cache, self._retry, self._verified, self._last = [], {}, {}, {}, None
        self.settings, self.symbols, self.sectors = dict(DEFAULTS), [], {}
        self._session = None
        self.state = {"running": False, "phase": "stopped", "started_at": None, "stopped_at": None,
                      "last_completed": None, "current": "", "completed": 0, "total": 0, "errors": [], "refresh": {}}
        self.repo.interrupt_open("server_restart")

    def _client(self):
        if self.client_factory:
            return self.client_factory()
        from trading_analysis.web_services import _zerodha_client
        return _zerodha_client()

    def readiness(self, now=None):
        now = as_ist(now or datetime.now(IST))
        blockers = []
        for exchange in ("NSE", "NFO"):
            path = self.analysis.nfo_instruments_path.with_name(f"instruments_{exchange}.csv")
            if not path.exists() or datetime.fromtimestamp(path.stat().st_mtime, IST).date() != now.date():
                blockers.append(f"Refresh {exchange} instrument master in Data Ops")
        return {"ready": not blockers, "blockers": blockers,
                "telegram_configured": TelegramNotifier.from_env("PULLBACK_").configured(),
                "mode": "unvalidated_paper", "orders_enabled": False}

    def _universe(self, now):
        ready = self.readiness(now)
        if not ready["ready"]:
            raise ValueError("; ".join(ready["blockers"]))
        symbols = sorted(current_watchlist_symbols(self.analysis, now))
        if not symbols:
            raise ValueError("No current F&O equities in the watchlist and futures master")
        mapping = load_sector_map(self.analysis.sector_map_path)
        self.symbols = symbols
        self.sectors = {s: sector_config_for_symbol(mapping, s) for s in symbols}
        self._session = now.date()

    def start(self, acknowledge=False, **settings):
        if acknowledge is not True:
            raise ValueError("Acknowledge unvalidated paper observation before starting")
        if set(settings) - set(DEFAULTS):
            raise ValueError("Unknown swing scanner setting")
        config = {**DEFAULTS, **settings}
        if (config["frame"] not in {"day", "60minute", "both"} or config["benchmark"] not in {"NIFTY 50", "NIFTY 500"}
                or config["variant"] not in {"aligned", "relative"} or config["setup"] not in {"all", *SETUPS}
                or isinstance(config["target_r"], bool) or config["target_r"] not in {1, 1.5, 2}
                or not isinstance(config["telegram_enabled"], bool)):
            raise ValueError("Invalid swing scanner settings")
        if config["telegram_enabled"] and not TelegramNotifier.from_env("PULLBACK_").configured():
            raise ValueError("Configure PULLBACK_TELEGRAM_BOT_TOKEN and PULLBACK_TELEGRAM_CHAT_ID")
        with self._lock:
            if self.state["running"]:
                return self.status()
            now = datetime.now(IST)
            self._universe(now)
            self.settings = config
            self.state.update(running=True, phase="starting", started_at=now.isoformat(), stopped_at=None, errors=[])
            self.repo.event("started", now, version=VERSION, settings=config)
            self._halt.clear()
            if not any(t.is_alive() for t in self._threads):
                self._threads = [threading.Thread(target=self._loop, args=(fn, seconds), daemon=True, name=name)
                                 for fn, seconds, name in ((self.refresh, 30, "swing-candles"), (self.cycle, 15, "swing-checker"))]
                for thread in self._threads:
                    thread.start()
            if config["telegram_enabled"]:
                self.repo.outbox.start()
        return self.status()

    def stop(self):
        with self._lock:
            now = datetime.now(IST)
            self.state.update(running=False, stopped_at=now.isoformat(),
                              phase="monitoring_exits" if self.repo.trades(open_only=True) else "stopped")
            self.repo.event("entries_stopped_exits_continue", now)
        return self.status()

    def _loop(self, fn, seconds):
        while not self._halt.is_set():
            try:
                if self.state["running"] or self.repo.trades(open_only=True):
                    if is_market_hours():
                        fn()
                    elif self.state["running"]:
                        with self._lock:
                            self.state["phase"] = "waiting_market"
            except Exception as exc:
                self._error(str(exc))
            self._halt.wait(seconds)

    def _error(self, message):
        message = diagnostics.clean(message)
        with self._lock:
            self.state["errors"] = [*self.state["errors"][-19:], message]
            if self.state["running"]:
                self.state["phase"] = "waiting_data"
        self.repo.event("worker_error", datetime.now(IST), error=message)
        diagnostics.record("stock_swing_error", area="stock_swing", error=message)

    def _rows(self, stem, frame, now):
        path = candle_path(self.analysis.daily_data_dir, frame, stem)
        if not path.exists():
            return []
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        if self._cache.get((stem, frame), (None,))[0] != stamp:
            rows = load_candles(path)[-600:]
            if any(not all(math.isfinite(v) and v > 0 for v in (c.open, c.high, c.low, c.close))
                   or not c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high
                   or not math.isfinite(c.volume) or c.volume < 0 for c in rows):
                raise ValueError(f"Invalid candles: {stem} {frame}")
            self._cache[(stem, frame)] = (stamp, rows)
        return closed_candles(self._cache[(stem, frame)][1], frame, now)

    def refresh(self):
        now = datetime.now(IST)
        frames = ("day", "60minute") if self.settings["frame"] == "both" else (self.settings["frame"],)
        targets = {(s, f): s for s in self.symbols for f in {*frames, "day"}}
        targets[(self.settings["benchmark"].replace(" ", "_"), "day")] = self.settings["benchmark"]
        for sec in self.sectors.values():
            if sec:
                targets[(Path(sec["data_file"]).stem, "day")] = sec["index_symbol"]
        for trade in self.repo.trades(open_only=True):
            targets[(trade["symbol"], trade["timeframe"])] = trade["symbol"]
        instruments = self.analysis._instruments_for_exchange("NSE")
        client = self._client()
        with self._lock:
            self.state["refresh"].update(completed=0, total=len(targets))
        for n, ((stem, frame), name) in enumerate(targets.items()):
            now = datetime.now(IST)
            if self._halt.is_set() or not is_market_hours(now):
                return
            rows = self._rows(stem, frame, now)
            expected = expected_closed_bar(frame, now)
            with self._lock:
                self.state["refresh"].update(completed=n)
            if rows and len(rows) >= 60 and as_ist(rows[-1].timestamp) == expected and self._verified.get((stem, frame)) == expected.isoformat():
                continue
            if (now-self._retry.get((stem, frame), now-timedelta(days=1))).total_seconds() < 60:
                continue
            try:
                with self._lock:
                    self.state["refresh"].update(current=f"{name} {frame}")
                token = resolve_instrument_token(instruments, "NSE", name)
                start = as_ist(rows[-1].timestamp)-timedelta(days=3) if len(rows) >= 60 else now-timedelta(days=400 if frame == "day" else 90)
                candles = client.historical_candles(token, frame, start, now)
                # Never let a forming candle become "complete" just because time passed.
                complete = closed_candles(candles, frame, now)
                merge_candles_csv(candle_path(self.analysis.daily_data_dir, frame, stem), complete)
                if complete and as_ist(complete[-1].timestamp) == expected:
                    self._verified[(stem, frame)] = expected.isoformat()
                with self._lock:
                    self.state["refresh"].update(last_success=datetime.now(IST).isoformat())
            except Exception as exc:
                self._error(f"{name} {frame}: {exc}")
            finally:
                self._retry[(stem, frame)] = now
                with self._lock:
                    self.state["refresh"]["current"] = ""
        with self._lock:
            self.state["refresh"].update(completed=len(targets), current="")

    def _quotes(self, symbols):
        keys = [f"NSE:{s}" for s in sorted(symbols)]
        raw = self._client().quotes(keys) if keys else {}
        received = datetime.now(IST)
        quotes = {}
        for symbol in symbols:
            row = raw.get(f"NSE:{symbol}", {})
            stamp = row.get("timestamp") or row.get("last_trade_time")
            try:
                quotes[symbol] = {"price": row.get("last_price"), "quote_time": as_ist(stamp).isoformat(),
                                  "received_at": received.isoformat(), "source": "zerodha_quote"}
            except (TypeError, ValueError, AttributeError):
                quotes[symbol] = {}
        return quotes

    def _exits(self, quotes, now):
        for trade in self.repo.trades(open_only=True):
            q = quotes.get(trade["symbol"], {})
            checked = as_ist(trade.get("last_checked") or trade["entry_time"])
            expected = checked if checked.date() == now.date() else next_market_open(checked)
            if expected and (now-expected).total_seconds() > 90 and not trade.get("monitoring_interrupted"):
                self.repo.interrupt_open("monitoring_gap", trade["setup"])
                trade.setdefault("monitoring_interrupted", "monitoring_gap")
            if not quote_is_fresh(q, now, max_age=30) or as_ist(q["quote_time"]) < as_ist(trade["entry_time"]):
                continue
            price, reason = float(q["price"]), None
            sign = 1 if trade["direction"] == "bullish" else -1
            if (price-trade["stop_level"])*sign <= 0:
                reason = "stop_loss"
            elif (price-trade["target_level"])*sign >= 0:
                reason = "target_reached"
            rows = self._rows(trade["symbol"], trade["timeframe"], now)
            held = sum(candle_close_time(c.timestamp, trade["timeframe"]) > as_ist(trade["entry_time"]) for c in rows)
            if not reason and held >= trade["holding_bars"]:
                reason = "holding_limit"
            if reason:
                closed = self.repo.close(trade, price, now, reason)
                if closed:
                    self.repo.event("exit", now, trade_id=trade["id"], symbol=trade["symbol"], reason=reason)
                    if trade["telegram_enabled"]:
                        self.repo.outbox.start()
            else:
                self.repo.mark({**trade, "last_quote": q, "last_checked": now.isoformat()})

    def cycle(self):
        now = datetime.now(IST)
        if not is_market_hours(now):
            return
        active = self.repo.trades(open_only=True)
        symbols = set(self.symbols if self.state["running"] else []) | {t["symbol"] for t in active}
        quotes = self._quotes(symbols)
        now = datetime.now(IST)
        self._exits(quotes, now)
        self._last = now
        if not self.state["running"]:
            with self._lock:
                self.state["phase"] = "monitoring_exits" if self.repo.trades(open_only=True) else "stopped"
            return
        if self._session != now.date():
            self._universe(now)
            return
        frames = ("day", "60minute") if self.settings["frame"] == "both" else (self.settings["frame"],)
        decisions = []
        with self._lock:
            self.state.update(phase="scanning", completed=0, total=len(self.symbols), current="")
        for i, symbol in enumerate(self.symbols):
            if not self.state["running"] or not is_market_hours():
                break
            for frame in frames:
                try:
                    decisions.extend(self.analyze(symbol, frame, quotes.get(symbol, {}), datetime.now(IST)))
                except Exception as exc:
                    self._error(f"{symbol} {frame}: {exc}")
            with self._lock:
                self.state.update(current=symbol, completed=i+1)
        with self._lock:
            phase = "waiting" if self.state["running"] else "monitoring_exits" if self.repo.trades(open_only=True) else "stopped"
            self.state.update(phase=phase, current="", decisions=decisions, last_completed=datetime.now(IST).isoformat())

    def analyze(self, symbol, frame, quote, now):
        def blocked(reason):
            decision = {"id": f"{VERSION}:{symbol}:{frame}:data:{expected_closed_bar(frame, now).isoformat()}:{reason}",
                        "setup": f"{symbol}:{frame}:data", "symbol": symbol, "frame": frame,
                        "reason": reason, "checked_at": now.isoformat()}
            self.repo.record_signal(decision)
            return [decision]
        rows = self._rows(symbol, frame, now)
        if not rows or as_ist(rows[-1].timestamp) != expected_closed_bar(frame, now) or len(rows) < 60:
            return blocked("candles_stale_or_warming")
        if self._verified.get((symbol, frame)) != expected_closed_bar(frame, now).isoformat():
            return blocked("closed_bar_verification_pending")
        if any(as_ist(a.timestamp) >= as_ist(b.timestamp) for a, b in zip(rows, rows[1:])):
            return blocked("unordered_candles")
        checked = rows[-61:]
        for a, b in zip(checked, checked[1:]):
            boundary = as_ist(b.timestamp)
            if frame == "day":
                boundary = boundary.replace(hour=9, minute=15)
            if as_ist(a.timestamp) != expected_closed_bar(frame, boundary):
                return blocked("candle_gap")
        signals, indicators = technical(rows)
        signal_at = candle_close_time(rows[-1].timestamp, frame)
        due = next_market_open(signal_at) if signal_at.time().replace(tzinfo=None) >= time(15, 30) else signal_at
        age = (now-due).total_seconds()
        daily = self._rows(symbol, "day", now)
        benchmark = self._rows(self.settings["benchmark"].replace(" ", "_"), "day", now)
        sec = self.sectors.get(symbol)
        sector = self._rows(Path(sec["data_file"]).stem, "day", now) if sec else []
        expected = expected_closed_bar("day", now).date()
        evidence = context(daily, benchmark, sector, now, expected)
        sources = [symbol, self.settings["benchmark"].replace(" ", "_")]
        if sec:
            sources.append(Path(sec["data_file"]).stem)
        if any(self._verified.get((stem, "day")) != expected_closed_bar("day", now).isoformat() for stem in sources):
            evidence = {"reason": "context_missing_or_stale", "aligned": None, "relative": None}
        result = []
        for family in SETUPS:
            if self.settings["setup"] not in {"all", family}:
                continue
            identifier = f"{VERSION}:{symbol}:{frame}:{family}:{rows[-1].timestamp.isoformat()}"
            signal = signals.get(family)
            reason = ("setup_not_confirmed" if indicators["reason"] == "candidate" else indicators["reason"]) if not signal else "passed"
            trade = None
            if signal:
                if age < 0:
                    reason = "waiting_next_session"
                elif age > 300:
                    reason = "entry_window_expired"
                elif evidence.get("aligned") != signal["side"] or (self.settings["variant"] == "relative" and evidence.get("relative") != signal["side"]):
                    reason = "market_sector_not_aligned" if evidence["reason"] == "available" else evidence["reason"]
                elif not quote_is_fresh(quote, now, max_age=30) or as_ist(quote["quote_time"]) < due:
                    reason = "waiting_fresh_quote"
                else:
                    plan = entry_plan(signal, float(quote["price"]), self.settings["target_r"])
                    if not plan:
                        reason = "entry_drift_or_risk"
                    else:
                        trade = {**plan, "symbol": symbol, "family": family, "timeframe": frame, "horizon": "swing",
                                 "direction": "bullish" if signal["side"] == "long" else "bearish", "entry_time": now.isoformat(),
                                 "signal_time": signal_at.isoformat(), "signal_entry_due": due.isoformat(),
                                 "target_r": self.settings["target_r"], "context_variant": self.settings["variant"], "context": evidence,
                                 "holding_bars": 10 if frame == "day" else 35, "cost_bps_per_side": 4,
                                 "telegram_enabled": self.settings["telegram_enabled"], "india_vix_entry": capture_vix(now),
                                 "price_basis": "observed_stock_spot_not_fill", "last_quote": quote, "last_checked": now.isoformat(),
                                 "performance_eligible": True}
            decision = {"id": identifier, "setup": f"{symbol}:{frame}:{family}", "symbol": symbol, "frame": frame,
                        "family": family, "reason": reason, "direction": signal.get("side") if signal else None,
                        "checked_at": now.isoformat(), "signal_time": signal_at.isoformat(), "context": evidence, "indicators": indicators}
            if signal and (age < 0 or (0 <= age <= 300 and reason in {"waiting_fresh_quote", "context_missing_or_stale"})):
                waiting = {**decision, "id": identifier+":waiting:"+reason}
                self.repo.record_signal(waiting)
                result.append(decision)
                continue
            if self.repo.seen(identifier):
                decision["reason"] = "closed_candle_already_checked"
                result.append(decision)
                continue
            with self._lock:
                if trade and not self.state["running"]:
                    trade, decision["reason"] = None, "entries_stopped"
                created = self.repo.record_signal(decision, trade)
            if created:
                decision["reason"] = "paper_entry"
                self.repo.event("entry", now, trade_id=created["id"], symbol=symbol, family=family)
                if created["telegram_enabled"]:
                    self.repo.outbox.start()
            result.append(decision)
        return result

    def status(self):
        with self._lock:
            state = json.loads(json.dumps(self.state))
        trades = self.repo.trades()
        return {**state, "version": VERSION, "mode": "unvalidated_paper", "settings": self.settings,
                "readiness": self.readiness(), "trades": trades, "delivery": self.repo.outbox.history(),
                "universe_count": len(self.symbols), "open_count": sum(t["status"] == "open" for t in trades),
                "setups": SETUPS, "orders_enabled": False}

    def export(self, from_date=None, to_date=None):
        first, last = date_window(from_date=from_date, to_date=to_date)
        return {"version": VERSION, "from_date": first, "to_date": last, "settings": self.settings,
                "mode": "unvalidated_paper", "timezone": "Asia/Kolkata", "orders_enabled": False,
                "trades": [t for t in self.repo.trades() if trade_overlaps(t, first, last)],
                "decisions": [r for day in days(first, last) for r in self.repo.decisions(day)],
                "events": [r for day in days(first, last) for r in self.repo.events(day)],
                "delivery": [r for r in self.repo.outbox.history() if in_window(r["created_at"], first, last)],
                "csv_columns": ["family", "timeframe", "context_variant", "signal_time", "target_r", "monitoring_interrupted"],
                "limitations": ["Unvalidated live-data paper observation, not an approved strategy or broker fill.",
                                "Stock spot costs are modeled; option/futures execution, F&O bans and events require manual review."]}
