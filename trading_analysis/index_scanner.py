from __future__ import annotations

import csv
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading_analysis.brokers.zerodha import load_instruments_csv, merge_candles_csv, resolve_instrument_token
from trading_analysis.candles import candle_path, candle_window, fetch_interval
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.index_signal import leader_confirmation, option_footprint, technical_read
from trading_analysis.instrument_master_service import InstrumentMasterService
from trading_analysis.nifty.live_scanner import HORIZON_CONFIG, build_live_entry_signal, evaluate_live_exit
from trading_analysis.nifty.technical_context import build_nifty_technical_context
from trading_analysis.notifications.telegram import TelegramNotifier
from trading_analysis.scheduler.market_hours import is_market_day, is_market_hours
from trading_analysis.storage import DEFAULT_DB_PATH
from trading_analysis.web_services import AnalysisService, _zerodha_client


IST = ZoneInfo("Asia/Kolkata")
PROFILE_PATH = Path("config/index_scanner_leaders.json")
CANDLE_DAYS = {"day": 365, "60minute": 90, "15minute": 45}
REFRESH_SECONDS = {"day": 60 * 60, "60minute": 30 * 60, "15minute": 5 * 60}


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
                    message TEXT NOT NULL, UNIQUE(trade_id, event_kind)
                );
                CREATE INDEX IF NOT EXISTS index_scan_alerts_recent
                    ON index_scan_alerts(symbol, created_at DESC);
            """)

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
            return dict(conn.execute("SELECT * FROM index_scan_trades WHERE trade_id = ?", (trade_id,)).fetchone())

    def alert(self, trade: dict[str, Any], event: str, message: str, telegram_status: str) -> None:
        with self._connect() as conn:
            conn.execute("""INSERT OR IGNORE INTO index_scan_alerts
                (trade_id, symbol, horizon, event_kind, created_at, telegram_status, message)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (trade["trade_id"], trade["symbol"], trade["horizon"], event, _iso(), telegram_status, message))

    def history(self, symbol: str) -> dict[str, Any]:
        with self._connect() as conn:
            trades = [dict(row) for row in conn.execute(
                "SELECT * FROM index_scan_trades WHERE symbol = ? ORDER BY created_at DESC LIMIT 100", (symbol,)).fetchall()]
            alerts = [dict(row) for row in conn.execute(
                "SELECT * FROM index_scan_alerts WHERE symbol = ? ORDER BY id DESC LIMIT 100", (symbol,)).fetchall()]
        for row in trades:
            row["context"] = json.loads(row.pop("context_json") or "{}")
        return {"trades": trades, "alerts": alerts}


class IndexScannerService:
    def __init__(self, analysis_service: AnalysisService | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH, profile_path: str | Path = PROFILE_PATH) -> None:
        self.analysis = analysis_service or AnalysisService()
        self.profiles = json.loads(Path(profile_path).read_text(encoding="utf-8"))
        self.repository = IndexScanRepository(db_path)
        self._states = {symbol: {"running": False, "phase": "stopped", "started_at": None,
                                "stopped_at": None, "last_cycle_at": None, "next_run": None,
                                "last_result": None, "errors": []} for symbol in self.profiles}
        self._events = {symbol: threading.Event() for symbol in self.profiles}
        self._threads: dict[str, threading.Thread] = {}
        self._scan_locks = {symbol: threading.Lock() for symbol in self.profiles}
        self._broker_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._candle_refresh: dict[tuple[str, str], datetime] = {}
        self._option_refresh: dict[str, dict[str, Any]] = {}

    def _symbol(self, value: str) -> str:
        symbol = value.upper()
        if symbol not in self.profiles:
            raise ValueError("Index must be BANKNIFTY or SENSEX.")
        return symbol

    def _broker_call(self, fn):
        with self._broker_lock:
            return fn()

    def start(self, symbol: str, interval_seconds: int = 180) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        if not 60 <= int(interval_seconds) <= 3600:
            raise ValueError("Check interval must be 60 to 3600 seconds.")
        if self._threads.get(symbol) and self._threads[symbol].is_alive():
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
        return self.status(symbol)

    def stop(self, symbol: str) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        self._events[symbol].set()
        self._states[symbol]["phase"] = "stopping"
        thread = self._threads.get(symbol)
        if thread:
            thread.join(timeout=5)
        return self.status(symbol)

    def _loop(self, symbol: str) -> None:
        state = self._states[symbol]
        try:
            while not self._events[symbol].is_set():
                if _scan_window():
                    self.run_once(symbol, refresh=True)
                    state["next_run"] = (_now() + timedelta(seconds=state["interval_seconds"])).isoformat(timespec="seconds")
                    self._events[symbol].wait(state["interval_seconds"])
                else:
                    state["phase"] = "waiting for market"
                    self._events[symbol].wait(20)
        finally:
            state.update({"running": False, "phase": "stopped", "stopped_at": _iso(), "next_run": None})

    def status(self, symbol: str) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        state = dict(self._states[symbol])
        state["running"] = bool(self._threads.get(symbol) and self._threads[symbol].is_alive())
        state["symbol"] = symbol
        state["profile"] = self.profiles[symbol]
        state["market_hours"] = is_market_hours()
        state["scan_window"] = _scan_window()
        state["telegram_destination"] = self._telegram_source(symbol)
        state["history"] = self.repository.history(symbol)
        return state

    def _telegram_source(self, symbol: str) -> str | None:
        if TelegramNotifier.from_env(f"{symbol}_").configured():
            return f"{symbol}_TELEGRAM_CHAT_ID"
        if TelegramNotifier.from_env("NIFTY_").configured():
            return "NIFTY_TELEGRAM_CHAT_ID"
        return None

    def _notify(self, trade: dict[str, Any], event: str) -> None:
        symbol = trade["symbol"]
        notifier = TelegramNotifier.from_env(f"{symbol}_")
        if not notifier.configured():
            notifier = TelegramNotifier.from_env("NIFTY_")
        message = (f"{symbol} {event.upper()} | {trade['horizon']} {trade['direction']}\n"
                   f"Trade {trade['trade_id']}\nEntry {trade['entry_price']:.2f} at {trade['entry_time']} IST\n"
                   f"Stop {trade['stop_level']:.2f} | Target {trade['target_level']:.2f}")
        if event == "exit":
            message += f"\nExit {trade['exit_price']:.2f} at {trade['exit_time']} IST | {trade['exit_reason']}"
        delivery = notifier.send_message(message) if notifier.configured() else {"sent": False, "error": "not configured"}
        status = "sent" if delivery.get("sent") else ("failed" if notifier.configured() else "not_configured")
        self.repository.alert(trade, event, message, status)
        if status == "failed":
            self._states[symbol]["errors"].append(f"Telegram {event}: {delivery.get('error')}")

    def _candle_path(self, symbol: str, timeframe: str) -> Path:
        stem = self.profiles[symbol]["candle_stem"] if symbol in self.profiles else symbol
        return candle_path(self.analysis.daily_data_dir, timeframe, stem)

    def _candles(self, symbol: str, timeframe: str) -> list[Any]:
        try:
            return load_candles(self._candle_path(symbol, timeframe))
        except FileNotFoundError:
            return []

    def _refresh_candle(self, symbol: str, timeframe: str) -> None:
        with self._refresh_lock:
            self._refresh_candle_locked(symbol, timeframe)

    def _refresh_candle_locked(self, symbol: str, timeframe: str) -> None:
        key = (symbol, timeframe)
        last = self._candle_refresh.get(key)
        if last and (_now() - last).total_seconds() < REFRESH_SECONDS[timeframe]:
            return
        profile = self.profiles.get(symbol)
        exchange = profile["exchange"] if profile else "NSE"
        tradingsymbol = profile["tradingsymbol"] if profile else symbol
        instrument_path = self.analysis.nse_instruments_path if exchange == "NSE" else self.analysis.bse_instruments_path
        if not instrument_path.exists():
            raise FileNotFoundError(f"Missing {exchange} instrument cache: {instrument_path}")
        instrument = resolve_instrument_token(load_instruments_csv(instrument_path), exchange, tradingsymbol)
        path = self._candle_path(symbol, timeframe)
        days = CANDLE_DAYS[timeframe] if not path.exists() else {"day": 8, "60minute": 4, "15minute": 3}[timeframe]
        window = candle_window(days=days)
        candles = self._broker_call(lambda: _zerodha_client().historical_candles(
            instrument_token=instrument, interval=fetch_interval(timeframe),
            from_time=window.from_time, to_time=window.to_time))
        if candles:
            merge_candles_csv(path, candles)
            self._candle_refresh[key] = _now()

    def _refresh_options(self, symbol: str) -> None:
        with self._refresh_lock:
            self._refresh_options_locked(symbol)

    def _refresh_options_locked(self, symbol: str) -> None:
        last = self._option_refresh.get(symbol)
        if last and (_now() - last["refreshed_at"]).total_seconds() < 300:
            return
        exchange = self.profiles[symbol]["option_exchange"] if symbol in self.profiles else "NFO"
        master = self.analysis.nfo_instruments_path if exchange == "NFO" else self.analysis.nfo_instruments_path.with_name("instruments_BFO.csv")
        if not master.exists():
            self._option_refresh.pop(symbol, None)
            raise FileNotFoundError(f"Missing {exchange} instrument cache: {master}")
        if _now() - datetime.fromtimestamp(master.stat().st_mtime, tz=IST) > timedelta(days=7):
            self._option_refresh.pop(symbol, None)
            raise ValueError(f"{exchange} instrument cache is older than 7 days: {master}; refresh it before live scans")
        result = self._broker_call(lambda: self.analysis.refresh_option_chain_snapshot(
            symbol=symbol, strikes_around=20 if symbol in self.profiles else 8, max_snapshots=8))
        if not result.get("expiry") or date.fromisoformat(result["expiry"]) < _now().date():
            raise ValueError(f"{symbol} option contracts are expired; refresh the instrument cache")
        self._option_refresh[symbol] = {"current": result["latest_snapshot"],
                                        "previous": result.get("archived_previous_latest"),
                                        "refreshed_at": _now(), "expiry": result.get("expiry")}

    def _footprint(self, symbol: str, spot: float | None) -> dict[str, Any]:
        files = self._option_refresh.get(symbol)
        if not files or not files["previous"]:
            return {"bias": "unavailable", "reason": "Two snapshots for the same expiry are required"}
        if (_now() - files["refreshed_at"]).total_seconds() > 600:
            return {"bias": "stale", "reason": "Option snapshot older than 10 minutes"}
        def read(path):
            with Path(path).open("r", encoding="utf-8", newline="") as handle:
                return list(csv.DictReader(handle))
        current, previous = read(files["current"]), read(files["previous"])
        if not current or not previous or current[0].get("expiry") != previous[0].get("expiry"):
            return {"bias": "unavailable", "reason": "Snapshot expiry mismatch"}
        previous_at = _as_ist(previous[0]["snapshot_time"])
        current_at = _as_ist(current[0]["snapshot_time"])
        if current_at <= previous_at or current_at - previous_at > timedelta(minutes=20) or _now() - current_at > timedelta(minutes=10):
            return {"bias": "stale", "reason": "Previous option snapshot is too old"}
        return {**option_footprint(current, previous, spot), "snapshot_time": current_at.isoformat(timespec="seconds"),
                "previous_time": previous_at.isoformat(timespec="seconds"), "expiry": files["expiry"]}

    def run_once(self, symbol: str, refresh: bool = True) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        if not self._scan_locks[symbol].acquire(blocking=False):
            return {"symbol": symbol, "status": "already_running"}
        state = self._states[symbol]
        errors: list[str] = []
        try:
            state["phase"] = "refreshing" if refresh else "analyzing cache"
            profile = self.profiles[symbol]
            leaders = [row["symbol"] for row in profile["leaders"]]
            if refresh:
                for item in [symbol, *leaders]:
                    if self._events[symbol].is_set() and state["running"]:
                        break
                    for frame in CANDLE_DAYS:
                        try:
                            self._refresh_candle(item, frame)
                        except Exception as exc:
                            errors.append(f"{item} {frame}: {exc}")
                for item in [symbol, *leaders]:
                    if self._events[symbol].is_set() and state["running"]:
                        break
                    try:
                        self._refresh_options(item)
                    except Exception as exc:
                        errors.append(f"{item} options: {exc}")
            state["phase"] = "analyzing"
            index_spot = self._candles(symbol, "15minute")
            index_option = self._footprint(symbol, index_spot[-1].close if index_spot else None)
            horizons: dict[str, Any] = {}
            created = 0
            active = _scan_window() and not self._events[symbol].is_set()
            exited = self._check_exits(symbol) if active else 0
            for horizon, config in HORIZON_CONFIG.items():
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
                reason = "Index technical setup unavailable or unconfirmed"
                if direction in {"bullish", "bearish"}:
                    reason = "Index option footprint disagrees or lacks two fresh snapshots"
                    if index_option["bias"] == direction:
                        reason = "Tracked constituent technical/option breadth did not confirm"
                        if confirmation["passed"]:
                            reason = "No new closed-candle entry or an open trade already exists"
                            if active:
                                signal = self._entry_signal(symbol, horizon, candles, read, index_option, leader_rows, confirmation)
                                if signal:
                                    trade = self.repository.open_trade(symbol, signal)
                                    if trade:
                                        self._notify(trade, "entry")
                                        created += 1
                                        reason = "Entry alert created"
                horizons[horizon] = {"technical": read, "option": index_option,
                                     "constituents": leader_rows, "confirmation": confirmation, "reason": reason}
            result = {"symbol": symbol, "status": "completed", "as_of": _iso(), "horizons": horizons,
                      "entries_created": created, "exits_created": exited, "errors": errors}
            state.update({"phase": "waiting" if state["running"] else "idle", "last_cycle_at": _iso(),
                          "last_result": result, "errors": errors[-15:]})
            return result
        except Exception as exc:
            errors.append(str(exc))
            state.update({"phase": "error", "errors": errors[-15:], "last_cycle_at": _iso()})
            return {"symbol": symbol, "status": "failed", "errors": errors}
        finally:
            self._scan_locks[symbol].release()

    def _entry_signal(self, symbol: str, horizon: str, candles: list[Any], read: dict[str, Any],
                      option: dict[str, Any], leaders: list[dict[str, Any]], confirmation: dict[str, Any]) -> dict[str, Any] | None:
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
        signal = build_live_entry_signal(context, candidates, horizon, candles, now=_now())
        if signal:
            signal["metadata"] = {**signal["metadata"], "index_option_footprint": option,
                                   "leader_confirmation": confirmation, "leaders": leaders}
        return signal

    def _check_exits(self, symbol: str) -> int:
        count = 0
        for trade in self.repository.open_trades(symbol):
            frame = HORIZON_CONFIG[trade["horizon"]]["timeframe"]
            candles = self._candles(symbol, frame)
            if technical_read(candles, frame, _now())["bias"] in {"unavailable", "stale"}:
                continue
            outcome = evaluate_live_exit(trade, candles, now=_now())
            if not outcome:
                continue
            if outcome.get("checked_only"):
                self.repository.mark_checked(trade["trade_id"], outcome["candle_timestamp"])
                continue
            closed = self.repository.close_trade(trade["trade_id"], outcome)
            if closed:
                self._notify(closed, "exit")
                count += 1
        return count
