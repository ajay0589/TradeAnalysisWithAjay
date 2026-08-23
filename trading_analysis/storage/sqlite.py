from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading_analysis.models import Candle


DEFAULT_DB_PATH = Path("data/db/trading_analysis.db")

_SEVERITY_RANK = {"info": 1, "watch": 2, "important": 3, "risk": 4}
IST = ZoneInfo("Asia/Kolkata")


def initialize_database(path: str | Path = DEFAULT_DB_PATH) -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms INTEGER,
                error TEXT,
                result_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_market_jobs_recent
            ON market_jobs(job_name, started_at DESC)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nifty_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                mode TEXT,
                horizon TEXT,
                severity TEXT NOT NULL,
                symbol TEXT NOT NULL DEFAULT 'NIFTY',
                spot REAL,
                strategy_id TEXT,
                direction TEXT,
                score REAL,
                confidence TEXT,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                trigger_level REAL,
                invalidation_level REAL,
                expiry TEXT,
                reasons_json TEXT,
                risks_json TEXT,
                metadata_json TEXT,
                context_snapshot_id INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1,
                acknowledged_at TEXT
            )
            """
        )
        _ensure_column(conn, "nifty_alerts", "horizon", "TEXT")
        _ensure_column(conn, "nifty_alerts", "metadata_json", "TEXT")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_nifty_alerts_recent
            ON nifty_alerts(created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_nifty_alerts_duplicate
            ON nifty_alerts(alert_type, strategy_id, direction, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nifty_context_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                mode TEXT,
                spot REAL,
                intraday_bias TEXT,
                swing_bias TEXT,
                positional_bias TEXT,
                option_bias TEXT,
                iv_rank REAL,
                iv_percentile REAL,
                iv_regime TEXT,
                technical_json TEXT,
                options_json TEXT,
                iv_json TEXT,
                summary_json TEXT,
                warnings_json TEXT,
                errors_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_nifty_context_snapshots_captured
            ON nifty_context_snapshots(captured_at DESC)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nifty_strategy_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                context_snapshot_id INTEGER NOT NULL,
                strategy_id TEXT,
                label TEXT,
                horizon TEXT,
                structure TEXT,
                suitability_score INTEGER,
                confidence TEXT,
                direction TEXT,
                expiry_plan TEXT,
                legs_json TEXT,
                reasons_json TEXT,
                risks_json TEXT,
                confirmations_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_nifty_strategy_candidates_context
            ON nifty_strategy_candidates(context_snapshot_id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nifty_alert_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id INTEGER NOT NULL,
                evaluated_at TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                horizon_bars INTEGER,
                entry_time TEXT,
                entry_price REAL,
                exit_time TEXT,
                exit_price REAL,
                forward_return_percent REAL,
                directional_return_percent REAL,
                max_favorable_percent REAL,
                max_adverse_percent REAL,
                success INTEGER,
                status TEXT,
                notes TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_nifty_alert_outcomes_lookup
            ON nifty_alert_outcomes(alert_id, timeframe, horizon_bars)
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_nifty_alert_outcomes_unique
            ON nifty_alert_outcomes(alert_id, timeframe, horizon_bars, entry_time)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nifty_candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL DEFAULT 'NIFTY',
                timeframe TEXT NOT NULL,
                ts TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                oi INTEGER,
                source TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(symbol, timeframe, ts)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_nifty_candles_lookup
            ON nifty_candles(symbol, timeframe, ts)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nifty_option_chain_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL DEFAULT 'NIFTY',
                expiry TEXT,
                spot REAL,
                captured_at TEXT NOT NULL,
                source TEXT,
                pcr_oi REAL,
                pcr_volume REAL,
                max_pain REAL,
                atm_strike REAL,
                atm_iv REAL,
                atm_iv_change REAL,
                total_ce_oi INTEGER,
                total_pe_oi INTEGER,
                total_ce_oi_change INTEGER,
                total_pe_oi_change INTEGER,
                raw_file TEXT,
                UNIQUE(symbol, expiry, captured_at)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_nifty_option_snapshots_lookup
            ON nifty_option_chain_snapshots(symbol, expiry, captured_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nifty_option_chain_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                strike REAL,
                option_type TEXT,
                tradingsymbol TEXT,
                last_price REAL,
                previous_close REAL,
                price_change REAL,
                oi INTEGER,
                previous_oi INTEGER,
                oi_change INTEGER,
                oi_change_percent REAL,
                implied_volatility REAL,
                iv_change REAL,
                volume INTEGER,
                bid_price REAL,
                ask_price REAL,
                buildup TEXT,
                UNIQUE(snapshot_id, strike, option_type)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_nifty_option_rows_lookup
            ON nifty_option_chain_rows(snapshot_id, strike, option_type)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nifty_iv_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL DEFAULT 'NIFTY',
                captured_at TEXT NOT NULL,
                expiry TEXT,
                days_to_expiry INTEGER,
                atm_strike REAL,
                atm_iv REAL,
                weekly_atm_iv REAL,
                monthly_atm_iv REAL,
                source_snapshot_id INTEGER,
                source_file TEXT,
                UNIQUE(symbol, expiry, captured_at)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_nifty_iv_observations_lookup
            ON nifty_iv_observations(symbol, captured_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS krishna_purple_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                purple_timeframe TEXT,
                entry_kind TEXT,
                status TEXT,
                price REAL,
                yellow_line REAL,
                score REAL,
                confidence TEXT,
                title TEXT,
                message TEXT,
                reasons_json TEXT,
                warnings_json TEXT,
                metadata_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_krishna_purple_alerts_recent
            ON krishna_purple_alerts(created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS krishna_purple_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL UNIQUE,
                symbol TEXT NOT NULL,
                purple_timeframe TEXT,
                entry_kind TEXT,
                status TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                entry_timeframe TEXT,
                exit_timeframe TEXT,
                entry_price REAL,
                exit_price REAL,
                entry_yellow_line REAL,
                exit_yellow_line REAL,
                score REAL,
                confidence TEXT,
                last_alert_id INTEGER,
                last_checked_at TEXT,
                reasons_json TEXT,
                warnings_json TEXT,
                metadata_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_krishna_purple_trades_status
            ON krishna_purple_trades(status, symbol, purple_timeframe, entry_kind)
            """
        )


class MarketJobRepository:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        initialize_database(self.db_path)

    def start_job(self, job_name: str, params: dict[str, Any] | None = None) -> int:
        payload = {"params": params or {}} if params is not None else None
        now = _now()
        with _connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO market_jobs(job_name, status, started_at, result_json)
                VALUES (?, 'running', ?, ?)
                """,
                (job_name, now, _json(payload) if payload is not None else None),
            )
            return int(cursor.lastrowid)

    def finish_job(self, job_id: int, result: dict[str, Any] | None = None) -> dict[str, Any]:
        finished = _now()
        with _connection(self.db_path) as conn:
            row = conn.execute("SELECT started_at FROM market_jobs WHERE id = ?", (job_id,)).fetchone()
            duration = _duration_ms(row["started_at"], finished) if row else None
            conn.execute(
                """
                UPDATE market_jobs
                SET status = 'completed', finished_at = ?, duration_ms = ?, error = NULL, result_json = ?
                WHERE id = ?
                """,
                (finished, duration, _json(result or {}), job_id),
            )
            updated = conn.execute("SELECT * FROM market_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job_row(updated) if updated else {}

    def fail_job(self, job_id: int, error: str) -> dict[str, Any]:
        finished = _now()
        with _connection(self.db_path) as conn:
            row = conn.execute("SELECT started_at FROM market_jobs WHERE id = ?", (job_id,)).fetchone()
            duration = _duration_ms(row["started_at"], finished) if row else None
            conn.execute(
                """
                UPDATE market_jobs
                SET status = 'failed', finished_at = ?, duration_ms = ?, error = ?
                WHERE id = ?
                """,
                (finished, duration, error, job_id),
            )
            updated = conn.execute("SELECT * FROM market_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job_row(updated) if updated else {}

    def latest_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with _connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM market_jobs ORDER BY started_at DESC, id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [_job_row(row) for row in rows]

    def cleanup(self, days: int = 7) -> dict[str, Any]:
        cutoff = (datetime.now() - timedelta(days=max(1, days))).isoformat(timespec="seconds")
        with _connection(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM market_jobs WHERE started_at < ?", (cutoff,))
            deleted = int(cursor.rowcount or 0)
        return {"deleted": deleted, "cutoff": cutoff}


class NiftyAlertRepository:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        initialize_database(self.db_path)

    def create_alert(
        self,
        *,
        alert_type: str,
        mode: str | None,
        severity: str,
        horizon: str | None = None,
        title: str,
        message: str,
        symbol: str = "NIFTY",
        spot: float | None = None,
        strategy_id: str | None = None,
        direction: str | None = None,
        score: float | None = None,
        confidence: str | None = None,
        trigger_level: float | None = None,
        invalidation_level: float | None = None,
        expiry: str | None = None,
        reasons: list[str] | None = None,
        risks: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        context_snapshot_id: int | None = None,
    ) -> dict[str, Any]:
        with _connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO nifty_alerts(
                    created_at, alert_type, mode, horizon, severity, symbol, spot, strategy_id, direction,
                    score, confidence, title, message, trigger_level, invalidation_level, expiry,
                    reasons_json, risks_json, metadata_json, context_snapshot_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now(),
                    alert_type,
                    mode,
                    horizon,
                    severity,
                    symbol,
                    spot,
                    strategy_id,
                    direction,
                    score,
                    confidence,
                    title,
                    message,
                    trigger_level,
                    invalidation_level,
                    expiry,
                    _json(reasons or []),
                    _json(risks or []),
                    _json(metadata or {}),
                    context_snapshot_id,
                ),
            )
            row = conn.execute("SELECT * FROM nifty_alerts WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _alert_row(row) if row else {}

    def list_recent_alerts(self, limit: int = 50, active_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE is_active = 1" if active_only else ""
        with _connection(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM nifty_alerts {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [_alert_row(row) for row in rows]

    def acknowledge_alert(self, alert_id: int) -> dict[str, Any]:
        now = _now()
        with _connection(self.db_path) as conn:
            conn.execute(
                "UPDATE nifty_alerts SET is_active = 0, acknowledged_at = ? WHERE id = ?",
                (now, alert_id),
            )
            row = conn.execute("SELECT * FROM nifty_alerts WHERE id = ?", (alert_id,)).fetchone()
        return _alert_row(row) if row else {"id": alert_id, "updated": False}

    def suppress_duplicate(
        self,
        alert_type: str,
        strategy_id: str | None,
        direction: str | None,
        min_minutes: int = 15,
        score: float | None = None,
        severity: str | None = None,
    ) -> bool:
        cutoff = (datetime.now() - timedelta(minutes=max(1, min_minutes))).isoformat(timespec="seconds")
        with _connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT score, severity
                FROM nifty_alerts
                WHERE alert_type = ?
                  AND COALESCE(strategy_id, '') = COALESCE(?, '')
                  AND COALESCE(direction, '') = COALESCE(?, '')
                  AND created_at >= ?
                ORDER BY created_at DESC
                """,
                (alert_type, strategy_id, direction, cutoff),
            ).fetchall()
        if not rows:
            return False
        new_rank = _SEVERITY_RANK.get((severity or "").lower(), 0)
        for row in rows:
            old_score = float(row["score"] or 0)
            old_rank = _SEVERITY_RANK.get(str(row["severity"] or "").lower(), 0)
            materially_better = score is not None and score >= old_score + 10
            more_severe = new_rank > old_rank
            if not materially_better and not more_severe:
                return True
        return False


class KrishnaPurpleAlertRepository:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        initialize_database(self.db_path)

    def open_entry_alert(self, match: dict[str, Any], entry: dict[str, Any], entry_kind: str) -> dict[str, Any]:
        symbol = str(match.get("symbol") or "").upper()
        purple_timeframe = str(match.get("purple_timeframe") or "")
        existing = self.open_trade(symbol, purple_timeframe, entry_kind)
        if existing:
            return {"created": False, "trade": existing, "alert": None}

        trade_id = _purple_trade_id(symbol, purple_timeframe, entry_kind)
        now = _now()
        reasons = list(match.get("reasons") or []) + list(entry.get("reasons") or [])
        warnings = list(match.get("warnings") or []) + list(entry.get("warnings") or [])
        metadata = {
            "entry": entry,
            "profile": match.get("profile") or {},
            "exit_rule": match.get("exit_rule"),
        }
        with _connection(self.db_path) as conn:
            trade_cursor = conn.execute(
                """
                INSERT INTO krishna_purple_trades(
                    trade_id, symbol, purple_timeframe, entry_kind, status, opened_at,
                    entry_timeframe, exit_timeframe, entry_price, entry_yellow_line,
                    score, confidence, last_checked_at, reasons_json, warnings_json, metadata_json
                )
                VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    symbol,
                    purple_timeframe,
                    entry_kind,
                    now,
                    entry.get("timeframe"),
                    (match.get("profile") or {}).get("exit_timeframe"),
                    _optional_float(entry.get("close")),
                    _optional_float(entry.get("yellow_line")),
                    _optional_float(match.get("score")),
                    match.get("confidence"),
                    now,
                    _json(reasons),
                    _json(warnings),
                    _json(metadata),
                ),
            )
            alert = self._insert_alert(
                conn,
                trade_id=trade_id,
                alert_type="entry",
                symbol=symbol,
                purple_timeframe=purple_timeframe,
                entry_kind=entry_kind,
                status="open",
                price=entry.get("close"),
                yellow_line=entry.get("yellow_line"),
                score=match.get("score"),
                confidence=match.get("confidence"),
                title=f"{symbol} {entry_kind} purple-touch entry candidate",
                message=f"{symbol} {entry_kind} entry candidate on {entry.get('timeframe')} close above yellow.",
                reasons=reasons,
                warnings=warnings,
                metadata=metadata,
            )
            conn.execute(
                "UPDATE krishna_purple_trades SET last_alert_id = ? WHERE id = ?",
                (alert["id"], trade_cursor.lastrowid),
            )
            trade = conn.execute("SELECT * FROM krishna_purple_trades WHERE trade_id = ?", (trade_id,)).fetchone()
        return {"created": True, "trade": _purple_trade_row(trade), "alert": alert}

    def close_trade_alert(self, trade: dict[str, Any], exit_snapshot: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        reasons = list(exit_snapshot.get("reasons") or [])
        warnings = list(exit_snapshot.get("warnings") or [])
        with _connection(self.db_path) as conn:
            alert = self._insert_alert(
                conn,
                trade_id=trade["trade_id"],
                alert_type="exit",
                symbol=trade["symbol"],
                purple_timeframe=trade.get("purple_timeframe"),
                entry_kind=trade.get("entry_kind"),
                status="closed",
                price=exit_snapshot.get("close"),
                yellow_line=exit_snapshot.get("yellow_line"),
                score=trade.get("score"),
                confidence=trade.get("confidence"),
                title=f"{trade['symbol']} purple-touch exit triggered",
                message=f"{trade['symbol']} {trade.get('exit_timeframe')} candle closed below yellow; review/exit condition triggered.",
                reasons=reasons or ["Exit timeframe candle closed below yellow Chande Kroll line."],
                warnings=warnings,
                metadata={"exit": exit_snapshot},
            )
            conn.execute(
                """
                UPDATE krishna_purple_trades
                SET status = 'closed', closed_at = ?, exit_price = ?, exit_yellow_line = ?,
                    last_alert_id = ?, last_checked_at = ?
                WHERE trade_id = ?
                """,
                (
                    now,
                    _optional_float(exit_snapshot.get("close")),
                    _optional_float(exit_snapshot.get("yellow_line")),
                    alert["id"],
                    now,
                    trade["trade_id"],
                ),
            )
            updated = conn.execute("SELECT * FROM krishna_purple_trades WHERE trade_id = ?", (trade["trade_id"],)).fetchone()
        return {"created": True, "trade": _purple_trade_row(updated), "alert": alert}

    def update_checked(self, trade_id: str) -> None:
        with _connection(self.db_path) as conn:
            conn.execute("UPDATE krishna_purple_trades SET last_checked_at = ? WHERE trade_id = ?", (_now(), trade_id))

    def open_trade(self, symbol: str, purple_timeframe: str, entry_kind: str) -> dict[str, Any] | None:
        with _connection(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM krishna_purple_trades
                WHERE symbol = ? AND purple_timeframe = ? AND entry_kind = ? AND status = 'open'
                ORDER BY opened_at DESC LIMIT 1
                """,
                (symbol.upper(), purple_timeframe, entry_kind),
            ).fetchone()
        return _purple_trade_row(row) if row else None

    def list_open_trades(self, limit: int = 200) -> list[dict[str, Any]]:
        with _connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM krishna_purple_trades WHERE status = 'open' ORDER BY opened_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [_purple_trade_row(row) for row in rows]

    def list_recent_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        with _connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM krishna_purple_alerts ORDER BY created_at DESC, id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [_purple_alert_row(row) for row in rows]

    def _insert_alert(
        self,
        conn: sqlite3.Connection,
        *,
        trade_id: str,
        alert_type: str,
        symbol: str,
        purple_timeframe: str | None,
        entry_kind: str | None,
        status: str,
        price: Any,
        yellow_line: Any,
        score: Any,
        confidence: str | None,
        title: str,
        message: str,
        reasons: list[str],
        warnings: list[str],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        cursor = conn.execute(
            """
            INSERT INTO krishna_purple_alerts(
                trade_id, created_at, alert_type, symbol, purple_timeframe, entry_kind,
                status, price, yellow_line, score, confidence, title, message,
                reasons_json, warnings_json, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                _now(),
                alert_type,
                symbol.upper(),
                purple_timeframe,
                entry_kind,
                status,
                _optional_float(price),
                _optional_float(yellow_line),
                _optional_float(score),
                confidence,
                title,
                message,
                _json(reasons),
                _json(warnings),
                _json(metadata),
            ),
        )
        row = conn.execute("SELECT * FROM krishna_purple_alerts WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _purple_alert_row(row)


class NiftyCandleRepository:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        initialize_database(self.db_path)

    def upsert_candles(self, symbol: str, timeframe: str, candles: list[Any], source: str = "zerodha") -> int:
        now = _now()
        rows = [
            (
                symbol.upper(),
                timeframe,
                _string_or_none(candle.timestamp),
                _optional_float(candle.open),
                _optional_float(candle.high),
                _optional_float(candle.low),
                _optional_float(candle.close),
                _optional_int(candle.volume),
                _optional_int(getattr(candle, "open_interest", None)),
                source,
                now,
            )
            for candle in candles
        ]
        with _connection(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO nifty_candles(symbol, timeframe, ts, open, high, low, close, volume, oi, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, ts) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    oi = excluded.oi,
                    source = excluded.source,
                    created_at = excluded.created_at
                """,
                rows,
            )
        return len(rows)

    def load_candles(
        self,
        symbol: str = "NIFTY",
        timeframe: str = "15minute",
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int | None = None,
    ) -> list[Candle]:
        clauses = ["symbol = ?", "timeframe = ?"]
        params: list[Any] = [symbol.upper(), timeframe]
        if from_date:
            clauses.append("ts >= ?")
            params.append(from_date)
        if to_date:
            clauses.append("ts <= ?")
            params.append(to_date)
        limit_clause = f" LIMIT {int(limit)}" if limit else ""
        with _connection(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM nifty_candles
                WHERE {' AND '.join(clauses)}
                ORDER BY ts ASC
                {limit_clause}
                """,
                params,
            ).fetchall()
        return [_candle_from_row(row) for row in rows]

    def latest_timestamp(self, symbol: str, timeframe: str) -> str | None:
        with _connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT ts FROM nifty_candles WHERE symbol = ? AND timeframe = ? ORDER BY ts DESC LIMIT 1",
                (symbol.upper(), timeframe),
            ).fetchone()
        return row["ts"] if row else None

    def counts(self, symbol: str = "NIFTY") -> dict[str, int]:
        with _connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT timeframe, COUNT(*) AS count FROM nifty_candles WHERE symbol = ? GROUP BY timeframe",
                (symbol.upper(),),
            ).fetchall()
        return {row["timeframe"]: row["count"] for row in rows}


class NiftyOptionChainRepository:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        initialize_database(self.db_path)

    def save_snapshot(self, analysis_result: Any, raw_file: str | None = None, captured_at: str | None = None) -> int:
        rows = _analysis_rows(analysis_result)
        ce_rows = [row for row in rows if str(_get(row, "option_type") or "").upper() == "CE"]
        pe_rows = [row for row in rows if str(_get(row, "option_type") or "").upper() == "PE"]
        captured = captured_at or _snapshot_time_from_rows(rows) or _now()
        with _connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO nifty_option_chain_snapshots(
                    symbol, expiry, spot, captured_at, source, pcr_oi, pcr_volume, max_pain,
                    atm_strike, atm_iv, atm_iv_change, total_ce_oi, total_pe_oi,
                    total_ce_oi_change, total_pe_oi_change, raw_file
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(_get(analysis_result, "symbol") or "NIFTY").upper(),
                    _string_or_none(_get(analysis_result, "expiry")),
                    _optional_float(_get(analysis_result, "spot_price") or _get(analysis_result, "spot")),
                    captured,
                    "zerodha",
                    _optional_float(_get(analysis_result, "pcr_oi")),
                    _optional_float(_get(analysis_result, "pcr_volume")),
                    _optional_float(_get(analysis_result, "max_pain")),
                    _optional_float(_get(analysis_result, "atm_strike") or _atm_strike(rows, _get(analysis_result, "spot_price") or _get(analysis_result, "spot"))),
                    _optional_float(_get(analysis_result, "atm_iv")),
                    _optional_float(_get(analysis_result, "atm_iv_change")),
                    sum(_optional_int(_get(row, "oi")) or 0 for row in ce_rows),
                    sum(_optional_int(_get(row, "oi")) or 0 for row in pe_rows),
                    _sum_optional(_get(row, "oi_change") for row in ce_rows),
                    _sum_optional(_get(row, "oi_change") for row in pe_rows),
                    raw_file or _get(analysis_result, "latest_snapshot") or _get(analysis_result, "raw_file"),
                ),
            )
            if cursor.lastrowid:
                return int(cursor.lastrowid)
            existing = conn.execute(
                """
                SELECT id FROM nifty_option_chain_snapshots
                WHERE symbol = ? AND COALESCE(expiry, '') = COALESCE(?, '') AND captured_at = ?
                """,
                (
                    str(_get(analysis_result, "symbol") or "NIFTY").upper(),
                    _string_or_none(_get(analysis_result, "expiry")),
                    captured,
                ),
            ).fetchone()
        return int(existing["id"]) if existing else 0

    def save_rows(self, snapshot_id: int, rows: list[Any]) -> int:
        payload = [
            (
                snapshot_id,
                _optional_float(_get(row, "strike")),
                str(_get(row, "option_type") or "").upper(),
                _get(row, "tradingsymbol"),
                _optional_float(_get(row, "last_price")),
                _optional_float(_get(row, "previous_close")),
                _optional_float(_get(row, "price_change")),
                _optional_int(_get(row, "oi")),
                _optional_int(_get(row, "previous_oi")),
                _optional_int(_get(row, "oi_change")),
                _optional_float(_get(row, "oi_change_percent")),
                _optional_float(_get(row, "implied_volatility")),
                _optional_float(_get(row, "iv_change")),
                _optional_int(_get(row, "volume")),
                _optional_float(_get(row, "bid_price")),
                _optional_float(_get(row, "ask_price")),
                _get(row, "buildup"),
            )
            for row in rows
        ]
        with _connection(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO nifty_option_chain_rows(
                    snapshot_id, strike, option_type, tradingsymbol, last_price, previous_close,
                    price_change, oi, previous_oi, oi_change, oi_change_percent,
                    implied_volatility, iv_change, volume, bid_price, ask_price, buildup
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id, strike, option_type) DO UPDATE SET
                    tradingsymbol = excluded.tradingsymbol,
                    last_price = excluded.last_price,
                    previous_close = excluded.previous_close,
                    price_change = excluded.price_change,
                    oi = excluded.oi,
                    previous_oi = excluded.previous_oi,
                    oi_change = excluded.oi_change,
                    oi_change_percent = excluded.oi_change_percent,
                    implied_volatility = excluded.implied_volatility,
                    iv_change = excluded.iv_change,
                    volume = excluded.volume,
                    bid_price = excluded.bid_price,
                    ask_price = excluded.ask_price,
                    buildup = excluded.buildup
                """,
                payload,
            )
        return len(payload)

    def load_latest_snapshot(self, symbol: str = "NIFTY", expiry: str | None = None) -> dict[str, Any] | None:
        where = "symbol = ?"
        params: list[Any] = [symbol.upper()]
        if expiry:
            where += " AND expiry = ?"
            params.append(expiry)
        with _connection(self.db_path) as conn:
            row = conn.execute(
                f"SELECT * FROM nifty_option_chain_snapshots WHERE {where} ORDER BY captured_at DESC, id DESC LIMIT 1",
                params,
            ).fetchone()
        return _option_snapshot_row(row) if row else None

    def load_snapshot_rows(self, snapshot_id: int) -> list[dict[str, Any]]:
        with _connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM nifty_option_chain_rows WHERE snapshot_id = ? ORDER BY strike ASC, option_type ASC",
                (snapshot_id,),
            ).fetchall()
        return [_option_row(row) for row in rows]

    def list_snapshots(self, symbol: str = "NIFTY", expiry: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        where = "symbol = ?"
        params: list[Any] = [symbol.upper()]
        if expiry:
            where += " AND expiry = ?"
            params.append(expiry)
        params.append(max(1, int(limit)))
        with _connection(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM nifty_option_chain_snapshots WHERE {where} ORDER BY captured_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_option_snapshot_row(row) for row in rows]


class NiftyIVObservationRepository:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        initialize_database(self.db_path)

    def record_observation(
        self,
        symbol: str = "NIFTY",
        expiry: str | None = None,
        atm_strike: float | None = None,
        atm_iv: float | None = None,
        weekly_atm_iv: float | None = None,
        monthly_atm_iv: float | None = None,
        source_snapshot_id: int | None = None,
        source_file: str | None = None,
        captured_at: str | None = None,
    ) -> int:
        captured = captured_at or _now()
        with _connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO nifty_iv_observations(
                    symbol, captured_at, expiry, days_to_expiry, atm_strike, atm_iv,
                    weekly_atm_iv, monthly_atm_iv, source_snapshot_id, source_file
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol.upper(),
                    captured,
                    expiry,
                    _days_to_expiry(expiry, captured),
                    _optional_float(atm_strike),
                    _optional_float(atm_iv),
                    _optional_float(weekly_atm_iv),
                    _optional_float(monthly_atm_iv),
                    source_snapshot_id,
                    source_file,
                ),
            )
            if cursor.lastrowid:
                return int(cursor.lastrowid)
            existing = conn.execute(
                """
                SELECT id FROM nifty_iv_observations
                WHERE symbol = ? AND COALESCE(expiry, '') = COALESCE(?, '') AND captured_at = ?
                """,
                (symbol.upper(), expiry, captured),
            ).fetchone()
        return int(existing["id"]) if existing else 0

    def load_history(self, symbol: str = "NIFTY", lookback_days: int = 252) -> list[dict[str, Any]]:
        cutoff = (datetime.now(IST) - timedelta(days=lookback_days)).isoformat(timespec="seconds")
        with _connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM nifty_iv_observations
                WHERE symbol = ? AND captured_at >= ? AND atm_iv IS NOT NULL
                ORDER BY captured_at ASC
                """,
                (symbol.upper(), cutoff),
            ).fetchall()
        return [_iv_observation_row(row) for row in rows]

    def latest(self, symbol: str = "NIFTY") -> dict[str, Any] | None:
        with _connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM nifty_iv_observations WHERE symbol = ? ORDER BY captured_at DESC, id DESC LIMIT 1",
                (symbol.upper(),),
            ).fetchone()
        return _iv_observation_row(row) if row else None


class NiftyContextRepository:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        initialize_database(self.db_path)

    def save_context_result(self, context_result: dict[str, Any]) -> int:
        technical = context_result.get("technical") or {}
        options = context_result.get("options") or {}
        iv = context_result.get("iv") or {}
        with _connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO nifty_context_snapshots(
                    captured_at, mode, spot, intraday_bias, swing_bias, positional_bias,
                    option_bias, iv_rank, iv_percentile, iv_regime, technical_json,
                    options_json, iv_json, summary_json, warnings_json, errors_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now(),
                    context_result.get("mode"),
                    _optional_float(technical.get("spot") or options.get("spot")),
                    technical.get("bias_intraday"),
                    technical.get("bias_swing"),
                    technical.get("bias_positional"),
                    options.get("option_bias"),
                    _optional_float(iv.get("iv_rank")),
                    _optional_float(iv.get("iv_percentile")),
                    iv.get("iv_regime"),
                    _json(technical),
                    _json(options),
                    _json(iv),
                    _json(context_result.get("summary") or {}),
                    _json(context_result.get("warnings") or []),
                    _json(context_result.get("errors") or []),
                ),
            )
            return int(cursor.lastrowid)

    def save_strategy_candidates(self, context_snapshot_id: int, candidates: list[dict[str, Any]]) -> list[int]:
        ids: list[int] = []
        with _connection(self.db_path) as conn:
            for candidate in candidates:
                cursor = conn.execute(
                    """
                    INSERT INTO nifty_strategy_candidates(
                        context_snapshot_id, strategy_id, label, horizon, structure,
                        suitability_score, confidence, direction, expiry_plan, legs_json,
                        reasons_json, risks_json, confirmations_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        context_snapshot_id,
                        candidate.get("strategy_id"),
                        candidate.get("label"),
                        candidate.get("horizon"),
                        candidate.get("structure"),
                        _optional_int(candidate.get("suitability_score")),
                        candidate.get("confidence"),
                        _candidate_direction(candidate),
                        candidate.get("expiry_plan"),
                        _json(candidate.get("legs") or []),
                        _json(candidate.get("reasons") or []),
                        _json(candidate.get("risks") or []),
                        _json(candidate.get("required_confirmations") or []),
                    ),
                )
                ids.append(int(cursor.lastrowid))
        return ids

    def load_context_snapshot(self, context_snapshot_id: int) -> dict[str, Any]:
        with _connection(self.db_path) as conn:
            snapshot = conn.execute(
                "SELECT * FROM nifty_context_snapshots WHERE id = ?",
                (context_snapshot_id,),
            ).fetchone()
            candidates = conn.execute(
                "SELECT * FROM nifty_strategy_candidates WHERE context_snapshot_id = ? ORDER BY suitability_score DESC",
                (context_snapshot_id,),
            ).fetchall()
        if snapshot is None:
            return {}
        return {**_context_row(snapshot), "candidates": [_candidate_row(row) for row in candidates]}

    def list_context_snapshots(self, limit: int = 50) -> list[dict[str, Any]]:
        with _connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM nifty_context_snapshots ORDER BY captured_at DESC, id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [_context_row(row) for row in rows]


class NiftyAlertOutcomeRepository:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        initialize_database(self.db_path)

    def save_alert_backtest_result(self, alert_id: int, row: dict[str, Any], timeframe: str | None = None) -> bool:
        with _connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO nifty_alert_outcomes(
                    alert_id, evaluated_at, timeframe, horizon_bars, entry_time, entry_price,
                    exit_time, exit_price, forward_return_percent, directional_return_percent,
                    max_favorable_percent, max_adverse_percent, success, status, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert_id,
                    _now(),
                    timeframe or row.get("timeframe") or "",
                    _optional_int(row.get("holding_bars")),
                    _string_or_none(row.get("entry_time")),
                    _optional_float(row.get("entry_price")),
                    _string_or_none(row.get("exit_time")),
                    _optional_float(row.get("exit_price")),
                    _optional_float(row.get("forward_return_percent")),
                    _optional_float(row.get("directional_return_percent")),
                    _optional_float(row.get("max_favorable_percent")),
                    _optional_float(row.get("max_adverse_percent")),
                    1 if row.get("success") else 0 if row.get("success") is not None else None,
                    row.get("status"),
                    row.get("reason") or row.get("notes"),
                ),
            )
            return bool(cursor.rowcount)

    def load_alert_outcomes(self, alert_id: int) -> list[dict[str, Any]]:
        with _connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM nifty_alert_outcomes WHERE alert_id = ? ORDER BY evaluated_at DESC",
                (alert_id,),
            ).fetchall()
        return [_outcome_row(row) for row in rows]

    def list_recent_outcomes(self, limit: int = 100) -> list[dict[str, Any]]:
        with _connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM nifty_alert_outcomes ORDER BY evaluated_at DESC, id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [_outcome_row(row) for row in rows]


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


@contextmanager
def _connection(path: Path):
    conn = _connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _duration_ms(started_at: str, finished_at: str) -> int:
    start = datetime.fromisoformat(started_at)
    finish = datetime.fromisoformat(finished_at)
    return int((finish - start).total_seconds() * 1000)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def _string_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _candidate_direction(candidate: dict[str, Any]) -> str | None:
    text = " ".join(str(candidate.get(key) or "") for key in ("strategy_id", "label", "required_view", "structure")).lower()
    if "bull" in text:
        return "bullish"
    if "bear" in text:
        return "bearish"
    if "neutral" in text or "condor" in text or "strangle" in text or "straddle" in text:
        return "neutral"
    if "volatility" in text:
        return "volatile"
    return None


def _purple_trade_id(symbol: str, purple_timeframe: str, entry_kind: str) -> str:
    stamp = datetime.now(IST).strftime("%Y%m%d%H%M%S")
    return f"KPT-{stamp}-{symbol.upper()}-{purple_timeframe}-{entry_kind}".replace(" ", "_")


def _get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _analysis_rows(analysis_result: Any) -> list[Any]:
    rows = _get(analysis_result, "rows")
    return list(rows or [])


def _snapshot_time_from_rows(rows: list[Any]) -> str | None:
    for row in rows:
        value = _get(row, "snapshot_time")
        if value:
            return str(value)
    return None


def _atm_strike(rows: list[Any], spot: Any) -> float | None:
    strikes = sorted({_optional_float(_get(row, "strike")) for row in rows if _optional_float(_get(row, "strike")) is not None})
    spot_value = _optional_float(spot)
    if not strikes or spot_value is None:
        return None
    return min(strikes, key=lambda strike: abs(strike - spot_value))


def _sum_optional(values) -> int | None:
    cleaned = [_optional_int(value) for value in values]
    present = [value for value in cleaned if value is not None]
    return sum(present) if present else None


def _days_to_expiry(expiry: str | None, captured_at: str) -> int | None:
    if not expiry:
        return None
    try:
        captured_date = datetime.fromisoformat(captured_at).date()
        return (date.fromisoformat(expiry) - captured_date).days
    except ValueError:
        return None


def _job_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "job_name": row["job_name"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "duration_ms": row["duration_ms"],
        "error": row["error"],
        "result": _loads(row["result_json"], {}),
    }


def _alert_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "alert_type": row["alert_type"],
        "mode": row["mode"],
        "horizon": (row["horizon"] if "horizon" in row.keys() else None) or row["mode"],
        "severity": row["severity"],
        "symbol": row["symbol"],
        "spot": row["spot"],
        "strategy_id": row["strategy_id"],
        "direction": row["direction"],
        "score": row["score"],
        "confidence": row["confidence"],
        "title": row["title"],
        "message": row["message"],
        "trigger_level": row["trigger_level"],
        "invalidation_level": row["invalidation_level"],
        "expiry": row["expiry"],
        "reasons": _loads(row["reasons_json"], []),
        "risks": _loads(row["risks_json"], []),
        "metadata": _loads(row["metadata_json"] if "metadata_json" in row.keys() else None, {}),
        "context_snapshot_id": row["context_snapshot_id"],
        "is_active": bool(row["is_active"]),
        "acknowledged_at": row["acknowledged_at"],
    }


def _purple_alert_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "trade_id": row["trade_id"],
        "created_at": row["created_at"],
        "alert_type": row["alert_type"],
        "symbol": row["symbol"],
        "purple_timeframe": row["purple_timeframe"],
        "entry_kind": row["entry_kind"],
        "status": row["status"],
        "price": row["price"],
        "yellow_line": row["yellow_line"],
        "score": row["score"],
        "confidence": row["confidence"],
        "title": row["title"],
        "message": row["message"],
        "reasons": _loads(row["reasons_json"], []),
        "warnings": _loads(row["warnings_json"], []),
        "metadata": _loads(row["metadata_json"], {}),
    }


def _purple_trade_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "trade_id": row["trade_id"],
        "symbol": row["symbol"],
        "purple_timeframe": row["purple_timeframe"],
        "entry_kind": row["entry_kind"],
        "status": row["status"],
        "opened_at": row["opened_at"],
        "closed_at": row["closed_at"],
        "entry_timeframe": row["entry_timeframe"],
        "exit_timeframe": row["exit_timeframe"],
        "entry_price": row["entry_price"],
        "exit_price": row["exit_price"],
        "entry_yellow_line": row["entry_yellow_line"],
        "exit_yellow_line": row["exit_yellow_line"],
        "score": row["score"],
        "confidence": row["confidence"],
        "last_alert_id": row["last_alert_id"],
        "last_checked_at": row["last_checked_at"],
        "reasons": _loads(row["reasons_json"], []),
        "warnings": _loads(row["warnings_json"], []),
        "metadata": _loads(row["metadata_json"], {}),
    }


def _candle_from_row(row: sqlite3.Row) -> Candle:
    return Candle(
        timestamp=datetime.fromisoformat(row["ts"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row["volume"] or 0),
        open_interest=row["oi"],
    )


def _context_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "captured_at": row["captured_at"],
        "mode": row["mode"],
        "spot": row["spot"],
        "intraday_bias": row["intraday_bias"],
        "swing_bias": row["swing_bias"],
        "positional_bias": row["positional_bias"],
        "option_bias": row["option_bias"],
        "iv_rank": row["iv_rank"],
        "iv_percentile": row["iv_percentile"],
        "iv_regime": row["iv_regime"],
        "technical": _loads(row["technical_json"], {}),
        "options": _loads(row["options_json"], {}),
        "iv": _loads(row["iv_json"], {}),
        "summary": _loads(row["summary_json"], {}),
        "warnings": _loads(row["warnings_json"], []),
        "errors": _loads(row["errors_json"], []),
    }


def _candidate_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "context_snapshot_id": row["context_snapshot_id"],
        "strategy_id": row["strategy_id"],
        "label": row["label"],
        "horizon": row["horizon"],
        "structure": row["structure"],
        "suitability_score": row["suitability_score"],
        "confidence": row["confidence"],
        "direction": row["direction"],
        "expiry_plan": row["expiry_plan"],
        "legs": _loads(row["legs_json"], []),
        "reasons": _loads(row["reasons_json"], []),
        "risks": _loads(row["risks_json"], []),
        "required_confirmations": _loads(row["confirmations_json"], []),
    }


def _outcome_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "alert_id": row["alert_id"],
        "evaluated_at": row["evaluated_at"],
        "timeframe": row["timeframe"],
        "horizon_bars": row["horizon_bars"],
        "entry_time": row["entry_time"],
        "entry_price": row["entry_price"],
        "exit_time": row["exit_time"],
        "exit_price": row["exit_price"],
        "forward_return_percent": row["forward_return_percent"],
        "directional_return_percent": row["directional_return_percent"],
        "max_favorable_percent": row["max_favorable_percent"],
        "max_adverse_percent": row["max_adverse_percent"],
        "success": bool(row["success"]) if row["success"] is not None else None,
        "status": row["status"],
        "notes": row["notes"],
    }


def _option_snapshot_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "expiry": row["expiry"],
        "spot": row["spot"],
        "captured_at": row["captured_at"],
        "source": row["source"],
        "pcr_oi": row["pcr_oi"],
        "pcr_volume": row["pcr_volume"],
        "max_pain": row["max_pain"],
        "atm_strike": row["atm_strike"],
        "atm_iv": row["atm_iv"],
        "atm_iv_change": row["atm_iv_change"],
        "total_ce_oi": row["total_ce_oi"],
        "total_pe_oi": row["total_pe_oi"],
        "total_ce_oi_change": row["total_ce_oi_change"],
        "total_pe_oi_change": row["total_pe_oi_change"],
        "raw_file": row["raw_file"],
    }


def _option_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "snapshot_id": row["snapshot_id"],
        "strike": row["strike"],
        "option_type": row["option_type"],
        "tradingsymbol": row["tradingsymbol"],
        "last_price": row["last_price"],
        "previous_close": row["previous_close"],
        "price_change": row["price_change"],
        "oi": row["oi"],
        "previous_oi": row["previous_oi"],
        "oi_change": row["oi_change"],
        "oi_change_percent": row["oi_change_percent"],
        "implied_volatility": row["implied_volatility"],
        "iv_change": row["iv_change"],
        "volume": row["volume"],
        "bid_price": row["bid_price"],
        "ask_price": row["ask_price"],
        "buildup": row["buildup"],
    }


def _iv_observation_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "captured_at": row["captured_at"],
        "date_time": datetime.fromisoformat(row["captured_at"]),
        "expiry": row["expiry"],
        "days_to_expiry": row["days_to_expiry"],
        "atm_strike": row["atm_strike"],
        "atm_iv": row["atm_iv"],
        "weekly_atm_iv": row["weekly_atm_iv"],
        "monthly_atm_iv": row["monthly_atm_iv"],
        "source_snapshot_id": row["source_snapshot_id"],
        "source_file": row["source_file"],
    }
