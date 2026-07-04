from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path("data/db/trading_analysis.db")

_SEVERITY_RANK = {"info": 1, "watch": 2, "important": 3, "risk": 4}


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
                    created_at, alert_type, mode, severity, symbol, spot, strategy_id, direction,
                    score, confidence, title, message, trigger_level, invalidation_level, expiry,
                    reasons_json, risks_json, context_snapshot_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now(),
                    alert_type,
                    mode,
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
                  AND is_active = 1
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


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _connection(path: Path):
    conn = _connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
