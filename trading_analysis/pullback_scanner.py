"""Read-only F&O pullback watchlists, spot-triggered alerts and spot exit tracking."""
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

from trading_analysis import diagnostics
from trading_analysis.brokers.zerodha import merge_candles_csv, resolve_instrument_token
from trading_analysis.candles import candle_path
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.live_timing import IST, as_ist, expected_closed_bar, quote_is_fresh
from trading_analysis.nifty.live_scanner import closed_candles
from trading_analysis.notifications.outbox import AlertOutbox
from trading_analysis.notifications.telegram import TelegramNotifier
from trading_analysis.pullback_context import PullbackContext, filter_settings
from trading_analysis.scheduler.market_hours import is_market_hours
from trading_analysis.storage import DEFAULT_DB_PATH
from trading_analysis.strategies.registry import get_strategy


class PullbackRepository:
    def __init__(self, path=DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS pullback_setups (
                id TEXT PRIMARY KEY, symbol TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS pullback_events (
                event_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, body TEXT NOT NULL)""")

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def rows(self, active=False):
        with closing(self.connect()) as conn:
            rows = conn.execute("SELECT body FROM pullback_setups" + (" WHERE status IN ('waiting','open')" if active else "") + " ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def add(self, row):
        with closing(self.connect()) as conn, conn:
            # A symbol has at most one waiting/open setup per direction and timeframe.
            conn.execute("BEGIN IMMEDIATE")
            active = [json.loads(r[0]) for r in conn.execute("SELECT body FROM pullback_setups WHERE symbol=? AND status IN ('waiting','open')", (row["symbol"],))]
            if any(r["side"] == row["side"] and r["timeframe"] == row["timeframe"] for r in active):
                return False
            return bool(conn.execute("INSERT OR IGNORE INTO pullback_setups VALUES(?,?,?,?)", (row["id"], row["symbol"], row["status"], json.dumps(row))).rowcount)

    def transition(self, row, previous, event=None, message=None, now=None):
        with closing(self.connect()) as conn, conn:
            updated = conn.execute("UPDATE pullback_setups SET status=?,body=? WHERE id=? AND status=?",
                                   (row["status"], json.dumps(row), row["id"], previous)).rowcount
            if updated and event:
                stamp = now.isoformat()
                body = {**row, "event": event, "event_time": stamp}
                conn.execute("INSERT OR IGNORE INTO pullback_events VALUES(?,?,?)", (f"{row['id']}:{event}", stamp, json.dumps(body)))
                conn.execute("INSERT OR IGNORE INTO alert_delivery_outbox(event_key,symbol,trade_id,event_kind,prefix,message,status,created_at,updated_at,next_attempt_at) VALUES(?,?,?,?,?,?,'queued',?,?,?)",
                             (f"{row['symbol']}:{row['id']}:{event}", row["symbol"], row["id"], event, "PULLBACK_", message, stamp, stamp, stamp))
            return bool(updated)

    def events(self):
        with closing(self.connect()) as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT body FROM pullback_events ORDER BY created_at DESC")]


class PullbackScanner:
    def __init__(self, service, db_path=DEFAULT_DB_PATH, client_factory=None):
        self.service = service
        self.repository = PullbackRepository(db_path)
        self.outbox = AlertOutbox(db_path)
        self.client_factory = client_factory
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads = []
        self._seen = set()
        self._refresh_times = {}
        self._universe_date = None
        self.settings = {"timeframe": "day", "benchmark": "NIFTY 50", "structure": True}
        self.state = {"running": False, "phase": "stopped", "started_at": None, "stopped_at": None,
                      "last_completed": None, "current": "", "completed": 0, "total": 0,
                      "refresh": {}, "decisions": [], "errors": []}

    def _client(self):
        if self.client_factory:
            return self.client_factory()
        from trading_analysis.web_services import _zerodha_client
        return _zerodha_client()

    def universe(self, now):
        nfo = self.service._instruments_for_exchange("NFO")
        names = {row.get("name", "").upper() for row in nfo if row.get("segment") == "NFO-FUT"
                 and str(row.get("expiry", ""))[:10] >= now.date().isoformat()}
        return [item.symbol for item in self.service._watchlist_items_by_symbol().values()
                if item.exchange == "NSE" and item.instrument_type == "EQ" and item.symbol.upper() in names]

    def _prepare_universe(self, now):
        for exchange in ("NSE", "NFO"):
            path = self.service.nfo_instruments_path.with_name(f"instruments_{exchange}.csv")
            if not path.exists() or datetime.fromtimestamp(path.stat().st_mtime, IST).date() != now.date():
                raise ValueError(f"Refresh {exchange} instrument master in Data Ops before starting pullbacks")
        symbols = self.universe(now)
        if not symbols:
            raise ValueError("No current F&O stocks matched the watchlist and NFO futures master")
        self.symbols = symbols
        self._universe_date = now.date()

    def start(self, settings=None):
        settings = dict(settings or {})
        if set(settings) - {"timeframe", "benchmark", "structure"}:
            raise ValueError("Unknown pullback scanner setting")
        config = {"timeframe": "day", "benchmark": "NIFTY 50", "structure": True, **settings}
        if config["timeframe"] not in {"day", "60minute", "15minute"}:
            raise ValueError("Choose Daily, 1h or 15m pullback setups")
        filter_settings({"benchmark": config["benchmark"], "structure": config["structure"]})
        with self._lock:
            if any(thread.is_alive() for thread in self._threads):
                if self._stop.is_set():
                    raise ValueError("Pullback workers are stopping; wait for the current request to finish")
                return self.status()
            now = datetime.now(IST)
            self._prepare_universe(now)
            self.settings = config
            self._seen.clear()
            self._stop.clear()
            self.state.update(running=True, phase="starting", started_at=now.isoformat(), stopped_at=None, errors=[])
            self._threads = [threading.Thread(target=self._loop, args=(self.refresh, 20), daemon=True, name="pullback-candles"),
                             threading.Thread(target=self._loop, args=(self.cycle, 15), daemon=True, name="pullback-checker")]
            for thread in self._threads:
                thread.start()
            self.outbox.start()
        return self.status()

    def stop(self):
        with self._lock:
            self._stop.set()
            self.state.update(running=False, phase="stopping" if any(t.is_alive() for t in self._threads) else "stopped",
                              stopped_at=datetime.now(IST).isoformat())
        return self.status()

    def _loop(self, fn, seconds):
        while not self._stop.is_set():
            try:
                if is_market_hours():
                    fn()
                else:
                    with self._lock:
                        self.state["phase"] = "waiting_market_hours"
            except Exception as exc:
                self._error(str(exc))
            self._stop.wait(seconds)

    def _error(self, message):
        with self._lock:
            self.state["errors"] = [*self.state["errors"][-19:], message]
        diagnostics.record("pullback_error", area="pullback", status="failed", error=message)

    def _context(self):
        return PullbackContext(self.service, {"market": True, "sector": True,
                                             "benchmark": self.settings["benchmark"], "structure": self.settings["structure"]})

    def _candles(self, symbol, frame):
        path = candle_path(self.service.daily_data_dir, frame, symbol)
        return load_candles(path) if path.exists() else []

    def refresh(self):
        now = datetime.now(IST)
        context = self._context()
        targets = {}
        for symbol in self.symbols:
            for _, name, stem in context.sources(symbol):
                if stem:
                    targets[(stem, "day")] = name
        for symbol in self.symbols:
            targets[(symbol, self.settings["timeframe"])] = symbol
        for row in self.repository.rows(active=True):
            targets[(row["symbol"], row["timeframe"])] = row["symbol"]
        instruments = self.service._instruments_for_exchange("NSE")
        client = self._client()
        for (stem, frame), name in targets.items():
            if self._stop.is_set() or not is_market_hours():
                return
            now = datetime.now(IST)
            key = (stem, frame)
            if (now - self._refresh_times.get(key, now - timedelta(days=1))).total_seconds() < 60:
                continue
            try:
                rows = closed_candles(self._candles(stem, frame), frame, now)
                expected = expected_closed_bar(frame, now)
                if rows and as_ist(rows[-1].timestamp) >= expected and len(rows) >= 55:
                    continue
                with self._lock:
                    self.state["refresh"].update(current=f"{name} {frame}", started_at=now.isoformat())
                token = resolve_instrument_token(instruments, "NSE", name)
                days = 400 if frame == "day" else 45
                start = now - timedelta(days=days) if len(rows) < 55 else as_ist(rows[-1].timestamp) - timedelta(days=3)
                candles = client.historical_candles(token, frame, start, now)
                merge_candles_csv(candle_path(self.service.daily_data_dir, frame, stem), candles)
                with self._lock:
                    self.state["refresh"].update(last_success=datetime.now(IST).isoformat(), candles=len(candles))
            except Exception as exc:
                self._error(f"{name} {frame}: {exc}")
            finally:
                self._refresh_times[key] = now
                with self._lock:
                    self.state["refresh"]["current"] = ""

    def cycle(self):
        now = datetime.now(IST)
        context = self._context()
        self.check_active(context)
        if self._universe_date != now.date():
            # Exit checks above remain available when new-session admission is blocked.
            self._prepare_universe(now)
        decisions = []
        with self._lock:
            self.state.update(phase="scanning", completed=0, total=len(self.symbols))
        for i, symbol in enumerate(self.symbols):
            if self._stop.is_set() or not is_market_hours():
                return
            frame = self.settings["timeframe"]
            try:
                now = datetime.now(IST)
                rows = closed_candles(self._candles(symbol, frame), frame, now)
                fresh = rows and as_ist(rows[-1].timestamp) == expected_closed_bar(frame, now)
                reason = "no_pullback"
                if not fresh or len(rows) < 55:
                    reason = "stock_data_stale_or_missing"
                else:
                    evaluations = {}
                    for strategy_id in ("bullish_pullback", "bearish_pullback"):
                        strategy = get_strategy(strategy_id)
                        evidence = context.check(symbol, "long" if strategy_id == "bullish_pullback" else "short", now)
                        if not evidence["passed"]:
                            evaluations[strategy_id] = evidence["reason"]
                            continue
                        key = (symbol, frame, rows[-1].timestamp.isoformat(), strategy_id)
                        if key in self._seen:
                            evaluations[strategy_id] = "closed_candle_already_checked"
                            continue
                        signal = strategy.generate_signal(symbol, rows, strategy.default_params)
                        if signal:
                            identifier = "PB-" + hashlib.sha256("|".join(key).encode()).hexdigest()[:12].upper()
                            row = {"id": identifier, "symbol": symbol, "timeframe": frame, "strategy": strategy_id,
                                   "signal_time": as_ist(rows[-1].timestamp).isoformat(), "detected_at": now.isoformat(),
                                   "side": signal.side, "score": signal.score, "trigger": signal.entry_price,
                                   "stop": signal.stop_loss, "status": "waiting", "context": evidence,
                                   "entry_time": None, "exit_time": None, "price_basis": "spot_not_fill"}
                            added = self.repository.add(row)
                            reason = "setup_waiting_for_trigger" if added else "existing_setup"
                        else:
                            reason = "stock_pullback_rules_not_met"
                        self._seen.add(key)
                        evaluations[strategy_id] = reason
                    reason = " | ".join(f"{key}: {value}" for key, value in evaluations.items())
                decisions.append({"symbol": symbol, "reason": reason, "checked_at": now.isoformat()})
                diagnostics.record("pullback_decision", area="pullback", symbol=symbol, status=reason)
            except Exception as exc:
                decisions.append({"symbol": symbol, "reason": str(exc)})
                self._error(f"{symbol}: {exc}")
            with self._lock:
                self.state.update(current=symbol, completed=i + 1)
            if i % 20 == 0:
                self.check_active(self._context())
        self.check_active(self._context())
        with self._lock:
            self.state.update(phase="waiting", decisions=decisions, last_completed=datetime.now(IST).isoformat())

    def check_active(self, context):
        active = self.repository.rows(active=True)
        if not active or self._stop.is_set() or not is_market_hours():
            return
        try:
            quotes = self._client().quotes(list({f"NSE:{row['symbol']}" for row in active}))
        except Exception as exc:
            self._error(f"Active setup quotes: {exc}")
            return
        received = datetime.now(IST)
        for row in active:
            if self._stop.is_set() or not is_market_hours():
                return
            q = quotes.get(f"NSE:{row['symbol']}", {})
            quote = {"price": q.get("last_price"), "quote_time": q.get("timestamp") or q.get("last_trade_time"),
                     "received_at": received.isoformat()}
            now = datetime.now(IST)
            if not quote_is_fresh(quote, now, max_age=30):
                diagnostics.record("pullback_quote_skipped", area="pullback", symbol=row["symbol"],
                                   status="missing_or_stale", quote_time=quote["quote_time"])
                continue
            try:
                self.process_quote(row, quote, now, context)
            except Exception as exc:
                self._error(f"{row['symbol']}: {exc}")

    def process_quote(self, row, quote, now, context):
        if not is_market_hours(now) or not quote_is_fresh(quote, now, max_age=30):
            return
        row = dict(row)
        previous = row["status"]
        price = float(quote["price"])
        sign = 1 if row["side"] == "long" else -1
        row.update(last_price=price, last_checked=now.isoformat())
        event = None
        if previous == "open":
            reason = "stop" if (price - row["stop"]) * sign <= 0 else "target" if (price - row["target"]) * sign >= 0 else None
            if reason is None:
                candles = closed_candles(self._candles(row["symbol"], row["timeframe"]), row["timeframe"], now)
                held = sum(as_ist(c.timestamp) > as_ist(row["entry_time"]) for c in candles)
                if held >= 5:
                    reason = "holding_limit"
            if reason:
                row.update(status="closed", exit_price=price, exit_time=now.isoformat(), exit_reason=reason,
                           open_seconds=(now - as_ist(row["entry_time"])).total_seconds(),
                           return_percent=(price - row["entry_price"]) / row["entry_price"] * sign * 100)
                event = "exit"
        elif previous == "waiting":
            if self._universe_date != now.date():
                return
            if row["symbol"] not in self.symbols:
                row.update(status="discarded", discard_reason="no_longer_in_fno_universe", discarded_at=now.isoformat())
                self.repository.transition(row, previous)
                return
            candles = closed_candles(self._candles(row["symbol"], row["timeframe"]), row["timeframe"], now)
            fresh = candles and as_ist(candles[-1].timestamp) == expected_closed_bar(row["timeframe"], now)
            if not fresh:
                return
            age = sum(as_ist(c.timestamp) > as_ist(row["signal_time"]) for c in candles)
            evidence = context.check(row["symbol"], row["side"], now)
            risk = (row["trigger"] - row["stop"]) * sign
            if age >= 3 or risk <= 0 or (price - row["stop"]) * sign <= 0 or not evidence["passed"]:
                row.update(status="discarded", discard_reason="expired" if age >= 3 else "context_or_stop_invalidated",
                           discarded_at=now.isoformat(), context=evidence)
            elif (price - row["trigger"]) * sign >= 0:
                target = row["trigger"] + sign * risk * 2
                reward, current_risk = (target - price) * sign, (price - row["stop"]) * sign
                if abs(price - row["trigger"]) > risk * 0.25 or current_risk <= 0 or reward / current_risk < 1.5:
                    row.update(status="discarded", discard_reason="trigger_moved_too_far", discarded_at=now.isoformat())
                else:
                    row.update(status="open", entry_price=price, entry_time=now.isoformat(), target=target,
                               risk_reward=reward / current_risk, quote=quote, context=evidence)
                    event = "entry"
        else:
            return
        if self._stop.is_set():
            return
        message = None
        if event:
            message = (f"F&O PULLBACK PAPER {event.upper()} ALERT\n{row['id']} | {row['symbol']} | {row['strategy']}\n"
                       f"Setup frame: {row['timeframe']} | Score: {row['score']}\n"
                       f"Observed stock spot: {price:.2f} at {now.isoformat()} IST\n"
                       f"Trigger: {row['trigger']:.2f} | Stop: {row['stop']:.2f} | Target: {row.get('target', 0):.2f}\n"
                       f"Market/sector: {row['context']['reason']}\n"
                       "Underlying spot signal, not an option strike/premium or broker fill. No order placed.")
        if self.repository.transition(row, previous, event, message, now) and event:
            self.outbox.start()
            diagnostics.record("pullback_alert", area="pullback", symbol=row["symbol"], event_kind=event, setup_id=row["id"])

    def status(self):
        with self._lock:
            result = json.loads(json.dumps(self.state))
            if self._stop.is_set() and not any(t.is_alive() for t in self._threads):
                result["phase"] = "stopped"
        rows = self.repository.rows()
        return {**result, "settings": self.settings, "mode": "paper_only_unvalidated", "telegram_configured": TelegramNotifier.from_env("PULLBACK_").configured(),
                "setups": rows[:500], "setup_total": len(rows), "events": self.repository.events()[:200],
                "delivery": [r for r in self.outbox.history() if r["trade_id"].startswith("PB-")][:200]}

    def export(self):
        return {"status": self.status(), "setups": self.repository.rows(), "events": self.repository.events(),
                "timezone": "Asia/Kolkata", "note": "Underlying spot signals; no orders. Review before sharing."}
