from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


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
                context_snapshot_id INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1,
                acknowledged_at TEXT
            )
            """
        )
        _ensure_column(conn, "nifty_alerts", "horizon", "TEXT")
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
        context_snapshot_id: int | None = None,
    ) -> dict[str, Any]:
        with _connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO nifty_alerts(
                    created_at, alert_type, mode, horizon, severity, symbol, spot, strategy_id, direction,
                    score, confidence, title, message, trigger_level, invalidation_level, expiry,
                    reasons_json, risks_json, context_snapshot_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        "context_snapshot_id": row["context_snapshot_id"],
        "is_active": bool(row["is_active"]),
        "acknowledged_at": row["acknowledged_at"],
    }


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
