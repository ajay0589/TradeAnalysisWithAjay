"""Durable delivery queue. Ambiguous POST failures are not blindly resent."""
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import threading

from trading_analysis import diagnostics
from trading_analysis.live_timing import IST
from trading_analysis.notifications.telegram import TelegramNotifier


class AlertOutbox:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._thread = None
        with closing(self.connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS alert_delivery_outbox(
                event_key TEXT PRIMARY KEY, symbol TEXT NOT NULL, trade_id TEXT NOT NULL,
                event_kind TEXT NOT NULL, prefix TEXT NOT NULL, message TEXT NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, next_attempt_at TEXT NOT NULL,
                delivered_at TEXT, error TEXT, telegram_message_id INTEGER)""")

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def enqueue(self, symbol, trade_id, event, prefix, message, *, start=True):
        now = datetime.now(IST).isoformat()
        key = f"{symbol}:{trade_id}:{event}"
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO alert_delivery_outbox(event_key,symbol,trade_id,event_kind,prefix,message,status,created_at,updated_at,next_attempt_at) VALUES(?,?,?,?,?,?,'queued',?,?,?)",
                         (key, symbol, trade_id, event, prefix, message, now, now, now))
        if start:
            self.start()
        return key

    def start(self):
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="alert-delivery", daemon=True)
                self._thread.start()

    def _loop(self):
        idle = 0
        while True:
            try:
                count = self.drain()
                idle = 0 if count else idle + 1
            except Exception as exc:
                diagnostics.record("alert_outbox_error", area="notifications", status="failed", error=str(exc))
                idle += 1
            threading.Event().wait(1)
            if idle >= 30:
                with self._lock, closing(self.connect()) as conn:
                    if not conn.execute("SELECT 1 FROM alert_delivery_outbox WHERE status IN ('queued','retry','sending') LIMIT 1").fetchone():
                        self._thread = None
                        return
                idle = 0

    def drain(self, sender=None, now=None):
        now = now or datetime.now(IST)
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            interrupted = conn.execute("SELECT * FROM alert_delivery_outbox WHERE status='sending' AND updated_at<?",
                                       ((now - timedelta(seconds=60)).isoformat(),)).fetchall()
            for row in interrupted:
                self._finish(conn, row, "delivery_unknown", "Delivery interrupted; check Telegram before retrying", now)
            row = conn.execute("SELECT * FROM alert_delivery_outbox WHERE status IN ('queued','retry') AND next_attempt_at<=? ORDER BY created_at LIMIT 1", (now.isoformat(),)).fetchone()
            if row is None:
                return 0
            conn.execute("UPDATE alert_delivery_outbox SET status='sending',attempts=attempts+1,updated_at=? WHERE event_key=?", (now.isoformat(), row["event_key"]))
        age = (now - datetime.fromisoformat(row["created_at"])).total_seconds()
        if age > 900:
            result = {"sent": False, "delivery_status": "expired", "error": "Delivery exceeded 15 minutes; retained for audit"}
        else:
            notifier = sender or TelegramNotifier.from_env(row["prefix"])
            message = row["message"]
            if age > 30:
                message = "DELAYED DELIVERY - original signal below\n" + message
            result = notifier.send_message(message)
        status = "sent" if result.get("sent") else result.get("delivery_status", "failed")
        if status == "retry" and row["attempts"] >= 2:
            status = "failed"
        finished = datetime.now(IST) if sender is None else now
        with closing(self.connect()) as conn, conn:
            self._finish(conn, row, status, result.get("error"), finished,
                         result.get("retry_after", 30), ((result.get("response") or {}).get("result") or {}).get("message_id"))
        diagnostics.record("alert_delivery_result", area="notifications", status=status, event_key=row["event_key"],
                           delivered_at=finished.isoformat() if status == "sent" else None, error=result.get("error"))
        return 1

    def _finish(self, conn, row, status, error, now, retry_after=30, message_id=None):
        conn.execute("UPDATE alert_delivery_outbox SET status=?,error=?,updated_at=?,next_attempt_at=?,delivered_at=?,telegram_message_id=? WHERE event_key=?",
                     (status, error, now.isoformat(), (now + timedelta(seconds=max(5, retry_after))).isoformat(),
                      now.isoformat() if status == "sent" else None, message_id, row["event_key"]))
        table = "nifty_alerts" if row["symbol"] == "NIFTY" else "index_scan_alerts"
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            conn.execute(f"UPDATE {table} SET telegram_status=?,telegram_error=? WHERE trade_id=? AND event_kind=?",
                         (status, error, row["trade_id"], row["event_kind"]))

    def history(self, day=None):
        with closing(self.connect()) as conn:
            rows = conn.execute("SELECT event_key,symbol,trade_id,event_kind,status,attempts,created_at,updated_at,delivered_at,error FROM alert_delivery_outbox WHERE (? IS NULL OR substr(created_at,1,10)=?) ORDER BY created_at DESC", (day, day)).fetchall()
        return [dict(row) for row in rows]
