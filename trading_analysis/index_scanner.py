from __future__ import annotations

import csv
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading_analysis import diagnostics
from trading_analysis.live_quotes import LIVE_QUOTES
from trading_analysis.live_timing import expected_closed_bar, prepare_live_entry, live_quote_exit, price_closed_exit
from trading_analysis.option_history import OptionHistory
from trading_analysis.notifications.outbox import AlertOutbox

from trading_analysis.brokers.zerodha import load_instruments_csv, merge_candles_csv, resolve_instrument_token
from trading_analysis.candles import candle_path, candle_window, fetch_interval
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.index_signal import leader_confirmation, option_footprint, technical_read
from trading_analysis.instrument_master_service import InstrumentMasterService
from trading_analysis.nifty.live_scanner import HORIZON_CONFIG, build_live_entry_signal, evaluate_live_exit, closed_candles
from trading_analysis.nifty.technical_context import build_nifty_technical_context
from trading_analysis.notifications.telegram import TelegramNotifier, nifty_trade_message
from trading_analysis.scheduler.market_hours import is_market_day, is_market_hours
from trading_analysis.storage import DEFAULT_DB_PATH
from trading_analysis.web_services import AnalysisService, _zerodha_client


IST = ZoneInfo("Asia/Kolkata")
PROFILE_PATH = Path("config/index_scanner_leaders.json")
CANDLE_DAYS = {"day": 365, "60minute": 90, "15minute": 45}


class ScanCancelled(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(IST)


def _iso() -> str:
    return _now().isoformat(timespec="seconds")


def _as_ist(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed.astimezone(IST)


def _scan_window(now: datetime | None = None) -> bool:
    current = (now or _now()).astimezone(IST)
    return is_market_day(current.date()) and clock_time(9, 15) <= current.time() <= clock_time(15, 45)


class IndexScanRepository:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS index_scan_trades (
                    trade_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, horizon TEXT NOT NULL,
                    status TEXT NOT NULL, direction TEXT NOT NULL,
                    entry_candle_timestamp TEXT NOT NULL, entry_time TEXT NOT NULL,
                    entry_price REAL NOT NULL, stop_level REAL NOT NULL, target_level REAL NOT NULL,
                    score INTEGER, last_candle_timestamp TEXT, exit_time TEXT, exit_price REAL,
                    exit_reason TEXT, open_seconds INTEGER, context_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(symbol, horizon, entry_candle_timestamp)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS index_scan_one_open
                    ON index_scan_trades(symbol, horizon) WHERE status = 'open';
                CREATE TABLE IF NOT EXISTS index_scan_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, horizon TEXT NOT NULL, event_kind TEXT NOT NULL,
                    created_at TEXT NOT NULL, telegram_status TEXT NOT NULL,
                    message TEXT NOT NULL, telegram_error TEXT, UNIQUE(trade_id, event_kind)
                );
                CREATE INDEX IF NOT EXISTS index_scan_alerts_recent
                    ON index_scan_alerts(symbol, created_at DESC);
                CREATE TABLE IF NOT EXISTS index_scan_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
                    started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
                    total_steps INTEGER NOT NULL, completed_steps INTEGER NOT NULL DEFAULT 0,
                    errors_json TEXT NOT NULL DEFAULT '[]', result_json TEXT
                );
                CREATE INDEX IF NOT EXISTS index_scan_runs_date ON index_scan_runs(symbol, started_at);
                CREATE TABLE IF NOT EXISTS index_scan_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL, stage TEXT NOT NULL, item TEXT NOT NULL,
                    timeframe TEXT, status TEXT NOT NULL, duration_ms INTEGER NOT NULL,
                    detail TEXT, FOREIGN KEY(run_id) REFERENCES index_scan_runs(id)
                );
                CREATE INDEX IF NOT EXISTS index_scan_steps_run ON index_scan_steps(run_id, id);
            """)
            if "telegram_error" not in {row["name"] for row in conn.execute("PRAGMA table_info(index_scan_alerts)")}:
                conn.execute("ALTER TABLE index_scan_alerts ADD COLUMN telegram_error TEXT")
            if "owner_pid" not in {row["name"] for row in conn.execute("PRAGMA table_info(index_scan_runs)")}:
                conn.execute("ALTER TABLE index_scan_runs ADD COLUMN owner_pid INTEGER")
            for row in conn.execute("SELECT id, owner_pid FROM index_scan_runs WHERE status = 'running'").fetchall():
                if row["owner_pid"] and not _process_alive(row["owner_pid"]):
                    conn.execute("UPDATE index_scan_runs SET status = 'interrupted', finished_at = ?, errors_json = ? WHERE id = ?",
                                 (_iso(), json.dumps(["Scanner process ended before completion"]), row["id"]))

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def open_trade(self, symbol: str, signal: dict[str, Any]) -> dict[str, Any] | None:
        candle_time = _as_ist(signal["entry_candle_timestamp"]).isoformat(timespec="seconds")
        trade_id = f"IX-{symbol}-{signal['horizon'].upper()}-{_as_ist(candle_time).strftime('%Y%m%d%H%M')}"
        stamp = _iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute("SELECT 1 FROM index_scan_trades WHERE symbol = ? AND horizon = ? AND status = 'open'",
                                  (symbol, signal["horizon"])).fetchone()
            if exists:
                return None
            cursor = conn.execute("""
                INSERT OR IGNORE INTO index_scan_trades
                (trade_id, symbol, horizon, status, direction, entry_candle_timestamp, entry_time,
                 entry_price, stop_level, target_level, score, last_candle_timestamp,
                 context_json, created_at, updated_at)
                VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (trade_id, symbol, signal["horizon"], signal["direction"], candle_time, stamp,
                  signal["entry_price"], signal["stop_level"], signal["target_level"], signal["score"],
                  candle_time, json.dumps(signal.get("metadata") or {}, default=str), stamp, stamp))
            if not cursor.rowcount:
                return None
            return dict(conn.execute("SELECT * FROM index_scan_trades WHERE trade_id = ?", (trade_id,)).fetchone())

    def open_trades(self, symbol: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM index_scan_trades WHERE symbol = ? AND status = 'open'", (symbol,)).fetchall()]

    def mark_checked(self, trade_id: str, candle_time: datetime) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE index_scan_trades SET last_candle_timestamp = ?, updated_at = ? WHERE trade_id = ? AND status = 'open'",
                         (_as_ist(candle_time).isoformat(timespec="seconds"), _iso(), trade_id))

    def close_trade(self, trade_id: str, outcome: dict[str, Any]) -> dict[str, Any] | None:
        exit_time = _iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM index_scan_trades WHERE trade_id = ? AND status = 'open'", (trade_id,)).fetchone()
            if not row:
                return None
            duration = max(0, int((_as_ist(exit_time) - _as_ist(row["entry_time"])).total_seconds()))
            conn.execute("""UPDATE index_scan_trades SET status = 'closed', exit_time = ?, exit_price = ?,
                exit_reason = ?, open_seconds = ?, updated_at = ? WHERE trade_id = ?""",
                (exit_time, float(outcome["price"]), outcome["reason"], duration, _iso(), trade_id))
            context = {**json.loads(row["context_json"] or "{}"), **(outcome.get("metadata") or {})}
            conn.execute("UPDATE index_scan_trades SET context_json = ? WHERE trade_id = ?", (json.dumps(context, default=str), trade_id))
            return dict(conn.execute("SELECT * FROM index_scan_trades WHERE trade_id = ?", (trade_id,)).fetchone())

    def alert(self, trade: dict[str, Any], event: str, message: str, telegram_status: str,
              telegram_error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute("""INSERT OR IGNORE INTO index_scan_alerts
                (trade_id, symbol, horizon, event_kind, created_at, telegram_status, message, telegram_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (trade["trade_id"], trade["symbol"], trade["horizon"], event, _iso(), telegram_status, message, telegram_error))

    def history(self, symbol: str) -> dict[str, Any]:
        with self._connect() as conn:
            trades = [dict(row) for row in conn.execute(
                "SELECT * FROM index_scan_trades WHERE symbol = ? ORDER BY created_at DESC LIMIT 100", (symbol,)).fetchall()]
            alerts = [dict(row) for row in conn.execute(
                "SELECT * FROM index_scan_alerts WHERE symbol = ? ORDER BY id DESC LIMIT 100", (symbol,)).fetchall()]
        for row in trades:
            row["context"] = json.loads(row.pop("context_json") or "{}")
        return {"trades": trades, "alerts": alerts}

    def start_run(self, symbol: str, total_steps: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "INSERT INTO index_scan_runs(symbol, started_at, status, total_steps, owner_pid) VALUES (?, ?, 'running', ?, ?)",
                (symbol, _iso(), total_steps, os.getpid()),
            )
            return int(row.lastrowid)

    def record_step(self, run_id: int, stage: str, item: str, timeframe: str | None,
                    status: str, duration_ms: int, detail: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO index_scan_steps(run_id, recorded_at, stage, item, timeframe, status, duration_ms, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, _iso(), stage, item, timeframe, status, duration_ms, (detail or "")[:500]),
            )
            conn.execute("UPDATE index_scan_runs SET completed_steps = completed_steps + 1 WHERE id = ?", (run_id,))

    def finish_run(self, run_id: int, status: str, errors: list[str], result: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE index_scan_runs SET finished_at = ?, status = ?, errors_json = ?, result_json = ? WHERE id = ?",
                (_iso(), status, json.dumps(errors), json.dumps(result, default=str) if result else None, run_id),
            )

    def runs(self, symbol: str, day: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM index_scan_runs WHERE symbol = ? AND substr(started_at, 1, 10) = ? "
                "ORDER BY id DESC LIMIT ?", (symbol, day, limit),
            ).fetchall()
        return [{**dict(row), "status": "unknown_legacy_owner" if row["status"] == "running" and not row["owner_pid"] else row["status"],
                 "errors": json.loads(row["errors_json"]),
                 "result": json.loads(row["result_json"]) if row["result_json"] else None}
                for row in rows]

    def steps(self, symbol: str, day: str, limit: int = 20000) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT s.* FROM index_scan_steps s JOIN index_scan_runs r ON s.run_id = r.id "
                "WHERE r.symbol = ? AND substr(r.started_at, 1, 10) = ? ORDER BY s.id DESC LIMIT ?",
                (symbol, day, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def diagnostics(self, symbol: str, day: str) -> dict[str, Any]:
        with self._connect() as conn:
            trades = [dict(row) for row in conn.execute(
                "SELECT * FROM index_scan_trades WHERE symbol = ? AND "
                "(substr(created_at, 1, 10) = ? OR substr(updated_at, 1, 10) = ?) ORDER BY created_at",
                (symbol, day, day),
            )]
            alerts = [dict(row) for row in conn.execute(
                "SELECT * FROM index_scan_alerts WHERE symbol = ? AND substr(created_at, 1, 10) = ? ORDER BY id",
                (symbol, day),
            )]
        for row in trades:
            row["context"] = json.loads(row.pop("context_json") or "{}")
        return {"runs": self.runs(symbol, day, limit=-1), "steps": self.steps(symbol, day, limit=-1),
                "trades": trades, "alerts": alerts}


class IndexScannerService:
    def __init__(self, analysis_service: AnalysisService | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH, profile_path: str | Path = PROFILE_PATH) -> None:
        self.analysis = analysis_service or AnalysisService()
        self.profiles = json.loads(Path(profile_path).read_text(encoding="utf-8"))
        self.repository = IndexScanRepository(db_path)
        self.option_history = OptionHistory(db_path)
        self.outbox = AlertOutbox(db_path)
        self._states = {symbol: {"running": False, "phase": "stopped", "started_at": None,
                                "stopped_at": None, "last_cycle_at": None, "next_run": None,
                                "last_result": None, "progress": None, "errors": []} for symbol in self.profiles}
        self._events = {symbol: threading.Event() for symbol in self.profiles}
        self._threads: dict[str, threading.Thread] = {}
        self._scan_locks = {symbol: threading.Lock() for symbol in self.profiles}
        self._refresh_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._source_locks: dict[tuple[str, str], threading.Lock] = {}
        self._instrument_tokens: dict[tuple[str, str], tuple[int, str]] = {}
        self._candle_refresh: dict[tuple[str, str], datetime] = {}
        self._option_refresh: dict[str, dict[str, Any]] = {}
        self._data_lock = threading.Lock()
        self._data_thread: threading.Thread | None = None
        self._data_wake = threading.Event()
        self._analysis_wake = {symbol: threading.Event() for symbol in self.profiles}
        self._data_attempts: dict[tuple[str, str], datetime] = {}
        self._data_status = {"running": False, "current": None, "pending": 0,
                             "successes": 0, "failures": 0, "last_success": None,
                             "last_error": None, "sources": {}}
        self._data_status["waiting"] = 0
        self._pending_counts = {}
        self._source_detail = {}
        self._candle_cache = {}
        self._candle_cache_lock = threading.Lock()
        self._exit_locks = {symbol: threading.Lock() for symbol in self.profiles}
        self._exit_threads = {}

    def _symbol(self, value: str) -> str:
        symbol = value.upper()
        if symbol not in self.profiles:
            raise ValueError("Index must be BANKNIFTY or SENSEX.")
        return symbol

    def _broker_call(self, fn, owner: str | None = None):
        if owner and self._events[owner].is_set():
            raise ScanCancelled("Scanner stopped before the next broker request")
        with diagnostics.scope(owner.lower() if owner else "indexes"):
            return fn()

    def _source_lock(self, stage: str, symbol: str) -> threading.Lock:
        with self._refresh_lock:
            return self._source_locks.setdefault((stage, symbol), threading.Lock())

    def _acquire_source(self, lock: threading.Lock, owner: str | None) -> None:
        while not lock.acquire(timeout=0.25):
            if owner and self._events[owner].is_set():
                raise ScanCancelled("Scanner stopped while waiting for shared data refresh")
        if owner and self._events[owner].is_set():
            lock.release()
            raise ScanCancelled("Scanner stopped before shared data refresh")

    def start(self, symbol: str, interval_seconds: int = 180) -> dict[str, Any]:
        with self._start_lock:
            return self._start_locked(symbol, interval_seconds)

    def _start_locked(self, symbol: str, interval_seconds: int) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        if not 60 <= int(interval_seconds) <= 3600:
            raise ValueError("Check interval must be 60 to 3600 seconds.")
        if self._threads.get(symbol) and self._threads[symbol].is_alive():
            return self.status(symbol)
        if self._exit_threads.get(symbol) and self._exit_threads[symbol].is_alive():
            return self.status(symbol)
        masters = InstrumentMasterService(self.analysis).status()["masters"]
        required = ("NSE", "NFO") if symbol == "BANKNIFTY" else ("NSE", "NFO", "BSE", "BFO")
        unavailable = [f"{exchange} ({masters[exchange]['state']})" for exchange in required
                       if masters[exchange]["state"] != "fresh"]
        if unavailable:
            raise ValueError(f"Refresh instrument masters in Data Ops first: {', '.join(unavailable)}.")
        self._events[symbol].clear()
        state = self._states[symbol]
        state.update({"running": True, "phase": "starting", "started_at": _iso(), "stopped_at": None,
                      "interval_seconds": max(60, int(interval_seconds))})
        thread = threading.Thread(target=self._loop, args=(symbol,), name=f"index-{symbol}", daemon=True)
        self._threads[symbol] = thread
        thread.start()
        LIVE_QUOTES.acquire(symbol)
        self.outbox.start()
        self._exit_threads[symbol] = threading.Thread(target=self._exit_loop, args=(symbol,), name=f"exit-{symbol}", daemon=True)
        self._exit_threads[symbol].start()
        self._ensure_data_worker()
        return self.status(symbol)

    def _ensure_data_worker(self) -> None:
        with self._data_lock:
            if not self._data_thread or not self._data_thread.is_alive():
                self._data_thread = threading.Thread(target=self._data_loop, name="index-data", daemon=True)
                self._data_thread.start()
        self._data_wake.set()

    def _data_tasks(self, owners: list[str]) -> list[tuple[str, str, str]]:
        tasks = {}
        for owner in owners:
            for item in [owner, *[row["symbol"] for row in self.profiles[owner]["leaders"]]]:
                for frame in ("options", "15minute", "60minute", "day"):
                    tasks.setdefault((item, frame), (owner, item, frame))
        return list(tasks.values())

    def _data_due(self, item: str, frame: str, now: datetime) -> bool:
        attempted = self._data_attempts.get((item, frame))
        # Failed/empty sources retry with a bounded delay, not a tight loop.
        delay = min(120, 15 * 2 ** min(self._pending_counts.get((item, frame), 0), 3)) if self._pending_counts.get((item, frame)) else 30
        if attempted and (now - attempted).total_seconds() < delay:
            return False
        if frame == "options":
            refreshed = self._option_refresh.get(item, {}).get("refreshed_at")
            return not refreshed or (now - refreshed).total_seconds() >= 180
        last = self._candle_refresh.get((item, frame))
        return not last or self._closed_bucket(frame, last) != self._closed_bucket(frame, now)

    @staticmethod
    def _closed_bucket(frame: str, now: datetime) -> tuple:
        current = now.astimezone(IST)
        start = current.replace(hour=9, minute=15, second=0, microsecond=0)
        end = current.replace(hour=15, minute=30, second=0, microsecond=0)
        if current >= end:
            return current.date(), "closed"
        minutes = {"15minute": 15, "60minute": 60, "day": 375}[frame]
        return current.date(), max(0, int((current - start).total_seconds() // (minutes * 60)))

    def _data_loop(self) -> None:
        self._data_status["running"] = True
        try:
            while True:
                owners = [s for s in self.profiles if self._states[s]["running"] and not self._events[s].is_set()]
                if not owners:
                    with self._data_lock:
                        # A concurrent start either joins this worker or starts its replacement.
                        if not any(self._states[s]["running"] and not self._events[s].is_set() for s in self.profiles):
                            self._data_status.update({"running": False, "current": None, "pending": 0})
                            self._data_thread = None
                            return
                    continue
                if not _scan_window():
                    self._data_wake.wait(5)
                    self._data_wake.clear()
                    continue
                due = [task for task in self._data_tasks(owners) if self._data_due(task[1], task[2], _now())]
                self._data_status["pending"] = len(due)
                if not due:
                    self._data_wake.wait(1)
                    self._data_wake.clear()
                    continue
                open_frames = {(owner, HORIZON_CONFIG[t["horizon"]]["timeframe"])
                               for owner in owners for t in self.repository.open_trades(owner)}
                def priority(task):
                    _, item, frame = task
                    return (0 if (item, frame) in open_frames else 1 if frame == "options" else
                            2 if item in owners else 3,
                            self._data_attempts.get((item, frame), datetime.min.replace(tzinfo=IST)))
                due.sort(key=priority)
                owner, item, frame = due[0]
                # All option chains share spot/contract quote requests, including common constituents.
                batch = [task for task in due if task[2] == "options"] if frame == "options" else [due[0]]
                started = time.monotonic()
                self._data_status["current"] = ", ".join(f"{i} {f}" for _, i, f in batch)
                for _, i, f in batch:
                    self._data_attempts[(i, f)] = _now()
                changed = False
                try:
                    if frame == "options":
                        changed = self._refresh_option_batch(batch, started)
                    else:
                        result = self._refresh_candle(item, frame, owner)
                        changed = result == "updated"
                        self._record_data(item, frame, "Empty candle response" if result == "empty" else None,
                                          started, waiting=result == "stale", cached=result == "cached")
                except Exception as exc:
                    for _, i, f in batch:
                        self._record_data(i, f, str(exc), started)
                finally:
                    self._data_status["current"] = None
                    if changed:
                        for active_owner in owners:
                            self._analysis_wake[active_owner].set()
        finally:
            if self._data_thread is threading.current_thread():
                self._data_status.update({"running": False, "current": None, "pending": 0})

    def _refresh_option_batch(self, batch, started):
        acquired = []
        changed = False
        try:
            for owner, item, _ in batch:
                lock = self._source_lock("options", item)
                if not self._events[owner].is_set() and lock.acquire(blocking=False):
                    acquired.append((item, lock))
            if not acquired:
                return
            with diagnostics.scope("indexes"):
                results = self.analysis.refresh_option_chain_snapshots([item for item, _ in acquired])
            for item, _ in acquired:
                result = results.get(item, {"error": "No snapshot returned"})
                error = result.get("error")
                try:
                    if not error:
                        self._publish_options(item, result)
                        changed = True
                except Exception as exc:
                    error = str(exc)
                self._record_data(item, "options", error, started)
            return changed
        finally:
            for _, lock in acquired:
                lock.release()

    def _record_data(self, item: str, frame: str, error: str | None, started: float,
                     waiting: bool = False, cached: bool = False) -> None:
        key = (item, frame)
        self._pending_counts[key] = self._pending_counts.get(key, 0) + 1 if waiting else 0
        retry = min(120, 15 * 2 ** min(self._pending_counts[key], 3))
        row = {"symbol": item, "timeframe": frame, "status": "failed" if error else "waiting" if waiting else "cached" if cached else "updated",
               "finished_at": _iso(), "duration_ms": int((time.monotonic() - started) * 1000), "error": error,
               **self._source_detail.get(key, {}),
               "retry_at": (_now() + timedelta(seconds=retry)).isoformat() if waiting else None}
        # Replace the mapping so status readers never iterate a mutating dictionary.
        self._data_status["sources"] = {**self._data_status["sources"], f"{item}:{frame}": row}
        self._data_status["failures" if error else "waiting" if waiting else "successes"] += 1
        if error:
            self._data_status["last_error"] = f"{item} {frame}: {error}"
        elif not waiting and not cached:
            self._data_status["last_success"] = _iso()
        diagnostics.record("index_data_refresh", area="indexes", **row)

    def stop(self, symbol: str) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        self._events[symbol].set()
        self._analysis_wake[symbol].set()
        self._data_wake.set()
        LIVE_QUOTES.release(symbol)
        self._states[symbol]["phase"] = "stopping"
        thread = self._threads.get(symbol)
        if thread and thread.is_alive():
            thread.join(timeout=5)
        elif not self._scan_locks[symbol].locked():
            self._states[symbol].update({"running": False, "phase": "stopped", "stopped_at": _iso(), "next_run": None})
        exit_thread = self._exit_threads.get(symbol)
        if exit_thread and exit_thread.is_alive():
            exit_thread.join(timeout=5)
        return self.status(symbol)

    def _loop(self, symbol: str) -> None:
        state = self._states[symbol]
        try:
            while not self._events[symbol].is_set():
                if _scan_window():
                    self._ensure_data_worker()
                    self._analysis_wake[symbol].clear()
                    self.run_once(symbol, refresh=False)
                    state["next_run"] = (_now() + timedelta(seconds=state["interval_seconds"])).isoformat(timespec="seconds")
                    self._analysis_wake[symbol].wait(state["interval_seconds"])
                    self._events[symbol].wait(1)
                else:
                    state["phase"] = "waiting for market"
                    self._events[symbol].wait(20)
        finally:
            state.update({"running": False, "phase": "stopped", "stopped_at": _iso(), "next_run": None})

    def status(self, symbol: str) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        state = dict(self._states[symbol])
        state["running"] = bool(self._threads.get(symbol) and self._threads[symbol].is_alive())
        state["scan_active"] = self._scan_locks[symbol].locked()
        state["symbol"] = symbol
        state["profile"] = self.profiles[symbol]
        state["market_hours"] = is_market_hours()
        state["scan_window"] = _scan_window()
        state["telegram_destination"] = self._telegram_source(symbol)
        state["history"] = self.repository.history(symbol)
        state["data_service"] = dict(self._data_status)
        state["live_quotes"] = LIVE_QUOTES.status()
        items = {symbol, *[row["symbol"] for row in self.profiles[symbol]["leaders"]]}
        state["data_service"]["sources"] = {key: row for key, row in self._data_status["sources"].items() if row["symbol"] in items}
        return state

    def _telegram_source(self, symbol: str) -> str | None:
        if TelegramNotifier.from_env(f"{symbol}_").configured():
            return f"{symbol}_TELEGRAM_CHAT_ID"
        if TelegramNotifier.from_env("NIFTY_").configured():
            return "NIFTY_TELEGRAM_CHAT_ID"
        return None

    def _notify(self, trade: dict[str, Any], event: str) -> str | None:
        symbol = trade["symbol"]
        notifier = TelegramNotifier.from_env(f"{symbol}_")
        if not notifier.configured():
            notifier = TelegramNotifier.from_env("NIFTY_")
        context = json.loads(trade.get("context_json") or "{}")
        entry = float(trade["entry_price"])
        risk = abs(entry - float(trade["stop_level"]))
        signed_return = ((float(trade["exit_price"]) - entry) / entry * 100
                         * (1 if trade["direction"] == "bullish" else -1)) if trade.get("exit_price") is not None and entry else None
        message = nifty_trade_message(event, {**trade, "metadata": context,
                    "strategy_id": "index_confluence", "confidence": "medium",
                    "target_r_multiple": abs(float(trade["target_level"]) - entry) / risk if risk else None,
                    "directional_return_percent": signed_return,
                    "entry_timeframe": HORIZON_CONFIG[trade["horizon"]]["timeframe"],
                    "open_duration": f"{(trade.get('open_seconds') or 0) // 60}m"})
        status = "queued" if notifier.configured() else "not_configured"
        self.repository.alert(trade, event, message, status)
        if status == "queued":
            prefix = f"{symbol}_" if TelegramNotifier.from_env(f"{symbol}_").configured() else "NIFTY_"
            self.outbox.enqueue(symbol, trade["trade_id"], event, prefix, message)
        return None

    def _candle_path(self, symbol: str, timeframe: str) -> Path:
        stem = self.profiles[symbol]["candle_stem"] if symbol in self.profiles else symbol
        return candle_path(self.analysis.daily_data_dir, timeframe, stem)

    def _candles(self, symbol: str, timeframe: str) -> list[Any]:
        try:
            path = self._candle_path(symbol, timeframe)
            info = path.stat()
            version = (info.st_mtime_ns, info.st_size)
            with self._candle_cache_lock:
                cached = self._candle_cache.get((symbol, timeframe))
                if cached and cached[0] == version:
                    return cached[1]
            rows = load_candles(path)
            with self._candle_cache_lock:
                self._candle_cache[(symbol, timeframe)] = (version, rows)
            return rows
        except FileNotFoundError:
            return []

    def _refresh_candle(self, symbol: str, timeframe: str, owner: str | None = None) -> str:
        lock = self._source_lock(timeframe, symbol)
        self._acquire_source(lock, owner)
        try:
            return self._refresh_candle_locked(symbol, timeframe, owner)
        finally:
            lock.release()

    def _refresh_candle_locked(self, symbol: str, timeframe: str, owner: str | None = None) -> str:
        key = (symbol, timeframe)
        requested_at = _now()
        last = self._candle_refresh.get(key)
        if last and self._closed_bucket(timeframe, last) == self._closed_bucket(timeframe, requested_at):
            return "cached"
        profile = self.profiles.get(symbol)
        exchange = profile["exchange"] if profile else "NSE"
        tradingsymbol = profile["tradingsymbol"] if profile else symbol
        instrument_path = self.analysis.nse_instruments_path if exchange == "NSE" else self.analysis.bse_instruments_path
        if not instrument_path.exists():
            raise FileNotFoundError(f"Missing {exchange} instrument cache: {instrument_path}")
        cache_key = (exchange, tradingsymbol)
        modified = instrument_path.stat().st_mtime_ns
        cached = self._instrument_tokens.get(cache_key)
        if not cached or cached[0] != modified:
            instrument = resolve_instrument_token(load_instruments_csv(instrument_path), exchange, tradingsymbol)
            self._instrument_tokens[cache_key] = (modified, instrument)
        else:
            instrument = cached[1]
        path = self._candle_path(symbol, timeframe)
        days = CANDLE_DAYS[timeframe] if not path.exists() else {"day": 8, "60minute": 4, "15minute": 3}[timeframe]
        cached_candles = self._candles(symbol, timeframe)
        if cached_candles:
            days = max(days, (requested_at.date() - _as_ist(cached_candles[-1].timestamp).date()).days + 2)
        window = candle_window(days=days)
        candles = self._broker_call(lambda: _zerodha_client().historical_candles(
            instrument_token=instrument, interval=fetch_interval(timeframe),
            from_time=window.from_time, to_time=window.to_time), owner)
        if candles:
            merge_candles_csv(path, candles)
            closed = closed_candles(candles, timeframe, requested_at)
            self._source_detail[key] = {"expected_closed_candle": self._expected_closed_bar(timeframe, requested_at).isoformat(),
                                       "latest_closed_candle": _as_ist(closed[-1].timestamp).isoformat() if closed else None,
                                       "latest_received_candle": _as_ist(candles[-1].timestamp).isoformat()}
            if not closed or _as_ist(closed[-1].timestamp) < self._expected_closed_bar(timeframe, requested_at):
                return "stale"
            self._candle_refresh[key] = requested_at
            return "updated"
        return "empty"

    @staticmethod
    def _expected_closed_bar(frame: str, now: datetime) -> datetime:
        return expected_closed_bar(frame, now)

    def _refresh_options(self, symbol: str, owner: str | None = None) -> str:
        lock = self._source_lock("options", symbol)
        self._acquire_source(lock, owner)
        try:
            return self._refresh_options_locked(symbol, owner)
        finally:
            lock.release()

    def _refresh_options_locked(self, symbol: str, owner: str | None = None) -> str:
        last = self._option_refresh.get(symbol)
        if last and (_now() - last["refreshed_at"]).total_seconds() < 300:
            return "cached"
        exchange = self.profiles[symbol]["option_exchange"] if symbol in self.profiles else "NFO"
        master = self.analysis.nfo_instruments_path if exchange == "NFO" else self.analysis.nfo_instruments_path.with_name("instruments_BFO.csv")
        if not master.exists():
            self._option_refresh.pop(symbol, None)
            raise FileNotFoundError(f"Missing {exchange} instrument cache: {master}")
        if _now() - datetime.fromtimestamp(master.stat().st_mtime, tz=IST) > timedelta(days=7):
            self._option_refresh.pop(symbol, None)
            raise ValueError(f"{exchange} instrument cache is older than 7 days: {master}; refresh it before live scans")
        result = self._broker_call(lambda: self.analysis.refresh_option_chain_snapshot(
            symbol=symbol, strikes_around=20 if symbol in self.profiles else 8, max_snapshots=8), owner)
        if not result.get("expiry") or date.fromisoformat(result["expiry"]) < _now().date():
            raise ValueError(f"{symbol} option contracts are expired; refresh the instrument cache")
        self._publish_options(symbol, result)
        return "updated"

    def _publish_options(self, symbol: str, result: dict[str, Any]) -> None:
        def read(path):
            if not path:
                return []
            with Path(path).open("r", encoding="utf-8", newline="") as handle:
                return list(csv.DictReader(handle))
        current = read(result.get("history_snapshot") or result["latest_snapshot"])
        previous = read(result.get("archived_previous_latest"))
        self.option_history.save(symbol, current)
        rolling = self.option_history.current(symbol, _now())
        self._option_refresh[symbol] = {"current": result["latest_snapshot"],
                                        "previous": result.get("archived_previous_latest"),
                                        "rows": (current, previous),
                                        "refreshed_at": _now(), "expiry": result.get("expiry"), "rolling": rolling}

    def _footprint(self, symbol: str, spot: float | None) -> dict[str, Any]:
        files = self._option_refresh.get(symbol)
        if not files or not files["previous"]:
            return {"bias": "unavailable", "reason": "Two snapshots for the same expiry are required"}
        if (_now() - files["refreshed_at"]).total_seconds() > 600:
            return {"bias": "stale", "reason": "Option snapshot older than 10 minutes"}
        def read(path):
            with Path(path).open("r", encoding="utf-8", newline="") as handle:
                return list(csv.DictReader(handle))
        current, previous = files["rows"] if "rows" in files else (read(files["current"]), read(files["previous"]))
        if not current or not previous or current[0].get("expiry") != previous[0].get("expiry"):
            return {"bias": "unavailable", "reason": "Snapshot expiry mismatch"}
        previous_at = _as_ist(previous[0]["snapshot_time"])
        current_at = _as_ist(current[0]["snapshot_time"])
        if current_at <= previous_at or current_at - previous_at > timedelta(minutes=20) or _now() - current_at > timedelta(minutes=10):
            return {"bias": "stale", "reason": "Previous option snapshot is too old"}
        return {**option_footprint(current, previous, spot), "snapshot_time": current_at.isoformat(timespec="seconds"),
                "previous_time": previous_at.isoformat(timespec="seconds"), "expiry": files["expiry"],
                "rolling": files.get("rolling"), "live_filter": "existing_two_snapshot_confluence"}

    def run_once(self, symbol: str, refresh: bool = True) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        if not self._scan_locks[symbol].acquire(blocking=False):
            return {"symbol": symbol, "status": "already_running"}
        state = self._states[symbol]
        if not state["running"]:
            self._events[symbol].clear()
        profile = self.profiles[symbol]
        leaders = [row["symbol"] for row in profile["leaders"]]
        total_steps = (len(leaders) + 1) * 4 + 4 if refresh else 4
        try:
            run_id = self.repository.start_run(symbol, total_steps)
        except Exception:
            self._scan_locks[symbol].release()
            raise
        started = time.monotonic()
        progress = {"run_id": run_id, "total": total_steps, "completed": 0, "current": None,
                    "started_at": _iso(), "successes": 0, "cached": 0, "failures": 0, "skipped": 0}
        state["progress"] = progress
        errors: list[str] = []

        def record_step(stage: str, item: str, frame: str | None, status: str,
                        elapsed: float, detail: str | None = None) -> None:
            self.repository.record_step(run_id, stage, item, frame, status, int(elapsed * 1000), detail)
            diagnostics.record("scan_step", area=symbol.lower(), status=status, run_id=run_id, stage=stage,
                               symbol=item, timeframe=frame, duration_ms=int(elapsed * 1000), detail=detail)
            progress["completed"] += 1
            progress["current"] = None
            bucket = ("failures" if status == "failed" else "cached" if status == "cached"
                      else "skipped" if status in {"cancelled", "market_closed", "empty"} else "successes")
            progress[bucket] += 1

        def refresh_step(stage: str, item: str, frame: str | None, fn) -> None:
            progress["current"] = f"{stage}: {item}{' ' + frame if frame else ''}"
            state["phase"] = "refreshing " + progress["current"]
            tick = time.monotonic()
            try:
                outcome = fn()
                record_step(stage, item, frame, outcome, time.monotonic() - tick)
            except ScanCancelled:
                record_step(stage, item, frame, "cancelled", time.monotonic() - tick)
                raise
            except Exception as exc:
                detail = f"{item} {frame or stage}: {exc}"
                errors.append(detail)
                record_step(stage, item, frame, "failed", time.monotonic() - tick, str(exc))

        try:
            state["phase"] = "refreshing" if refresh else "analyzing cache"
            if refresh:
                for item in [symbol, *leaders]:
                    if self._events[symbol].is_set():
                        raise ScanCancelled("Scanner stopped during candle refresh")
                    for frame in CANDLE_DAYS:
                        if self._events[symbol].is_set():
                            raise ScanCancelled("Scanner stopped during candle refresh")
                        refresh_step("candle", item, frame,
                                     lambda item=item, frame=frame: self._refresh_candle(item, frame, symbol))
                for item in [symbol, *leaders]:
                    if self._events[symbol].is_set():
                        raise ScanCancelled("Scanner stopped during option refresh")
                    refresh_step("options", item, None, lambda item=item: self._refresh_options(item, symbol))
            if self._events[symbol].is_set():
                raise ScanCancelled("Scanner stopped before analysis")
            state["phase"] = "analyzing"
            index_spot = self._candles(symbol, "15minute")
            index_option = self._footprint(symbol, index_spot[-1].close if index_spot else None)
            horizons: dict[str, Any] = {}
            created = 0
            active = _scan_window() and not self._events[symbol].is_set()
            progress["current"] = "open-trade exits"
            tick = time.monotonic()
            exited = self._check_exits(symbol, errors) if active else 0
            record_step("exit", symbol, None, "completed" if active else "market_closed",
                        time.monotonic() - tick, f"{exited} exits")
            for horizon, config in HORIZON_CONFIG.items():
                progress["current"] = f"{horizon} technical and option gates"
                tick = time.monotonic()
                frame = config["timeframe"]
                candles = self._candles(symbol, frame)
                read = technical_read(candles, frame, _now())
                leader_rows = []
                for item in profile["leaders"]:
                    stock = item["symbol"]
                    stock_read = technical_read(self._candles(stock, frame), frame, _now())
                    stock_option = self._footprint(stock, stock_read.get("close"))
                    leader_rows.append({"symbol": stock, "weight_pct": item.get("weight_pct"),
                                        "technical_bias": stock_read["bias"], "option_bias": stock_option["bias"],
                                        "change_pct": stock_read.get("change_pct"),
                                        "candle_time": stock_read.get("candle_time"),
                                        "put_oi_change": stock_option.get("put_oi_change"),
                                        "call_oi_change": stock_option.get("call_oi_change")})
                direction = read["bias"]
                confirmation = leader_confirmation(leader_rows, direction) if direction in {"bullish", "bearish"} else {"passed": False}
                entry_checks: dict[str, Any] = {}
                reason = "Index technical setup unavailable or unconfirmed"
                if direction in {"bullish", "bearish"}:
                    reason = f"Index option footprint {index_option['bias']}: {index_option.get('reason') or 'not aligned'}"
                    if index_option["bias"] == direction:
                        reason = "Tracked constituent technical/option breadth did not confirm"
                        if confirmation["passed"]:
                            reason = "Outside scan window; no new entry checks"
                            if active:
                                signal = self._entry_signal(symbol, horizon, candles, read, index_option,
                                                            leader_rows, confirmation, entry_checks)
                                if signal and not self._events[symbol].is_set():
                                    trade = self.repository.open_trade(symbol, signal)
                                    if trade:
                                        delivery_error = self._notify(trade, "entry")
                                        if delivery_error:
                                            errors.append(delivery_error)
                                        created += 1
                                        reason = "Entry alert created"
                                    else:
                                        reason = "An open trade exists or this candle was already processed"
                                else:
                                    reason = f"Entry candle gate: {entry_checks.get('gate') or 'not confirmed'}"
                horizons[horizon] = {"technical": read, "option": index_option,
                                     "confirmation_state": "technical_only" if direction in {"bullish", "bearish"} and index_option["bias"] != direction else "options_aligned" if direction in {"bullish", "bearish"} else "no_setup",
                                     "constituents": leader_rows, "confirmation": confirmation,
                                     "entry_checks": entry_checks, "reason": reason}
                record_step("analysis", symbol, horizon, "completed", time.monotonic() - tick, reason)
            result = {"symbol": symbol, "status": "completed", "as_of": _iso(), "horizons": horizons,
                      "entries_created": created, "exits_created": exited, "errors": errors}
            state.update({"phase": "waiting" if state["running"] else "idle", "last_cycle_at": _iso(),
                          "last_result": result, "errors": errors[-15:]})
            self.repository.finish_run(run_id, "completed", errors, result)
            return result
        except ScanCancelled as exc:
            state.update({"phase": "stopping" if state["running"] else "stopped", "errors": errors[-15:]})
            if not state["running"]:
                state["stopped_at"] = _iso()
            self.repository.finish_run(run_id, "cancelled", errors + [str(exc)])
            return {"symbol": symbol, "status": "cancelled", "errors": errors + [str(exc)]}
        except Exception as exc:
            errors.append(str(exc))
            state.update({"phase": "error", "errors": errors[-15:], "last_cycle_at": _iso()})
            self.repository.finish_run(run_id, "failed", errors)
            return {"symbol": symbol, "status": "failed", "errors": errors}
        finally:
            progress["duration_ms"] = int((time.monotonic() - started) * 1000)
            progress["current"] = None
            self._scan_locks[symbol].release()

    def _entry_signal(self, symbol: str, horizon: str, candles: list[Any], read: dict[str, Any],
                      option: dict[str, Any], leaders: list[dict[str, Any]], confirmation: dict[str, Any],
                      diagnostics: dict[str, Any] | None = None) -> dict[str, Any] | None:
        try:
            technical = build_nifty_technical_context(
                self._candles(symbol, "day"), self._candles(symbol, "60minute"),
                self._candles(symbol, "15minute"), mode=horizon)
            support, resistance = technical.support_levels, technical.resistance_levels
        except ValueError:
            support, resistance = [], []
        score = min(90, 70 + 4 * confirmation["technical_aligned"])
        context = {"summary": {"data_links": {"latest_option_snapshot_at": option["snapshot_time"]}},
                   "technical": {"support_levels": support, "resistance_levels": resistance},
                   "options": {"option_bias": option["bias"], "pcr_oi": option.get("pcr_oi")}, "iv": {}}
        candidates = [{"required_view": read["bias"], "suitability_score": score,
                       "strategy_id": "index_confluence", "label": f"{symbol} {horizon} confluence",
                       "confidence": "medium", "reasons": ["Index trend, option OI changes and constituent breadth agree"]}]
        signal = build_live_entry_signal(context, candidates, horizon, candles, now=_now(), diagnostics=diagnostics)
        if signal:
            signal = prepare_live_entry(signal, LIVE_QUOTES.get(symbol), _now(), diagnostics)
        if signal:
            signal["metadata"] = {**signal["metadata"], "index_option_footprint": option,
                                   "leader_confirmation": confirmation, "leaders": leaders}
        return signal

    def _exit_loop(self, symbol):
        try:
            while not self._events[symbol].wait(1):
                if _scan_window():
                    try:
                        self._check_exits(symbol, quote_only=True)
                    except Exception as exc:
                        diagnostics.record("live_exit_failed", area=symbol.lower(), status="failed", error=str(exc))
        finally:
            LIVE_QUOTES.release(symbol)

    def _check_exits(self, symbol: str, errors: list[str] | None = None, quote_only=False) -> int:
        if not self._exit_locks[symbol].acquire(blocking=False):
            return 0
        try:
            return self._check_exits_locked(symbol, errors, quote_only)
        finally:
            self._exit_locks[symbol].release()

    def _check_exits_locked(self, symbol, errors, quote_only):
        count = 0
        for trade in self.repository.open_trades(symbol):
            if self._events[symbol].is_set():
                break
            frame = HORIZON_CONFIG[trade["horizon"]]["timeframe"]
            quote = LIVE_QUOTES.get(symbol)
            outcome = live_quote_exit(trade, quote, _now())
            if not outcome and not quote_only:
                candles = self._candles(symbol, frame)
                outcome = price_closed_exit(trade, evaluate_live_exit(trade, candles, now=_now()), quote, _now(), frame)
            if not outcome:
                continue
            if outcome.get("checked_only"):
                self.repository.mark_checked(trade["trade_id"], outcome["candle_timestamp"])
                continue
            closed = self.repository.close_trade(trade["trade_id"], outcome)
            if closed:
                delivery_error = self._notify(closed, "exit")
                if delivery_error and errors is not None:
                    errors.append(delivery_error)
                count += 1
        return count


def _process_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is not proof of termination.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
