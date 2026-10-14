"""Durable paper trades and decisions; notifications commit with the trade."""
from contextlib import closing
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

from trading_analysis.live_timing import as_ist
from trading_analysis.notifications.outbox import AlertOutbox
from .rules import VERSION


def alert_message(trade, kind):
    return (f"MCX SWING - PAPER {kind.upper()}\n{trade['root']} | {trade['symbol']} | Expiry {trade['expiry']}\n"
            f"{trade['direction']} | Trade {trade['id']}\n"
            f"Entry reference: {trade['entry_price']:.2f} at {trade['entry_time']}\n"
            f"Stop / Target: {trade['stop_level']:.2f} / {trade['target_level']:.2f}\n" +
            (f"Exit reference: {trade['exit_price']:.2f} at {trade['exit_time']}\nReason: {trade['exit_reason']}\n"
             f"Net model R: {trade['net_r']:.2f} | Held {trade['open_seconds']/3600:.1f} hours\n" if kind == "exit" else "") +
            f"Daily ATR%: {trade['indicators']['atr_percent']:.2f}\n"
            "IST. Futures bid/ask observation, not spot, option premium or a broker fill. No order placed.")


class McxRepository:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.outbox = AlertOutbox(self.path)
        with closing(self.connect()) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS mcx_trades (
                    id TEXT PRIMARY KEY, root TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS mcx_one_open ON mcx_trades(root) WHERE status='open';
                CREATE TABLE IF NOT EXISTS mcx_decisions (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS mcx_events (
                    id INTEGER PRIMARY KEY, session TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS mcx_state (key TEXT PRIMARY KEY, body TEXT NOT NULL);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def save_state(self, key, value):
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO mcx_state VALUES(?,?)", (key, json.dumps(value)))

    def state(self, key, default=None):
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT body FROM mcx_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def event(self, event, now, **details):
        body = {"event": event, "time": now.isoformat(), **details}
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT INTO mcx_events(session,body) VALUES(?,?)", (now.date().isoformat(), json.dumps(body)))

    def seen(self, key):
        with closing(self.connect()) as conn:
            return bool(conn.execute("SELECT 1 FROM mcx_decisions WHERE id=?", (key,)).fetchone())

    def record(self, decision, trade=None):
        decision = dict(decision)
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM mcx_decisions WHERE id=?", (decision["id"],)).fetchone():
                return None
            created = None
            if trade and not conn.execute("SELECT 1 FROM mcx_trades WHERE root=? AND status='open'", (trade["root"],)).fetchone():
                trade = {**trade, "id": "MCX-" + hashlib.sha256(decision["id"].encode()).hexdigest()[:12].upper(), "status": "open"}
                conn.execute("INSERT INTO mcx_trades VALUES(?,?,'open',?)", (trade["id"], trade["root"], json.dumps(trade)))
                if trade["telegram_enabled"]:
                    self._enqueue(conn, trade, "entry", trade["entry_time"])
                created = trade
                decision["reason"] = "entry_created"
            elif trade:
                decision["reason"] = "already_open"
            conn.execute("INSERT INTO mcx_decisions VALUES(?,?,?)", (decision["id"], decision["checked_at"][:10], json.dumps(decision)))
        return created

    def _enqueue(self, conn, trade, kind, stamp):
        conn.execute("INSERT OR IGNORE INTO alert_delivery_outbox(event_key,symbol,trade_id,event_kind,prefix,message,status,created_at,updated_at,next_attempt_at) VALUES(?,?,?,?,?,?,'queued',?,?,?)",
                     (f"MCX:{trade['id']}:{kind}", trade["symbol"], trade["id"], kind, "MCX_", alert_message(trade, kind), stamp, stamp, stamp))

    def close(self, trade, quote, now, reason):
        trade = dict(trade)
        sign = 1 if trade["direction"] == "bullish" else -1
        price = quote["bid"] if sign == 1 else quote["ask"]
        points = sign * (price - trade["entry_price"])
        costs = (price + trade["entry_price"]) * trade["risk_policy"]["cost_bps_per_side"] / 10000
        trade.update(status="closed", exit_price=price, exit_time=now.isoformat(), exit_reason=reason,
                     exit_quote=quote, exit_price_basis="observed_futures_bid_ask_not_fill",
                     open_seconds=(now - as_ist(trade["entry_time"])).total_seconds(),
                     gross_points=points, net_points=points-costs, cost_points=costs,
                     net_r=(points-costs)/trade["risk_points"],
                     performance_eligible=not bool(trade.get("monitoring_interrupted")))
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            # Reload interruptions written by a concurrent stop before closing.
            stored = conn.execute("SELECT body FROM mcx_trades WHERE id=? AND status='open'", (trade["id"],)).fetchone()
            if not stored:
                return None
            interruption = json.loads(stored[0]).get("monitoring_interrupted")
            if interruption:
                trade.update(monitoring_interrupted=interruption, performance_eligible=False)
            conn.execute("UPDATE mcx_trades SET status='closed',body=? WHERE id=?", (json.dumps(trade), trade["id"]))
            if trade["telegram_enabled"]:
                self._enqueue(conn, trade, "exit", now.isoformat())
        return trade

    def interrupt_open(self, reason):
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            for identifier, body in conn.execute("SELECT id,body FROM mcx_trades WHERE status='open'").fetchall():
                row = json.loads(body)
                row.setdefault("monitoring_interrupted", reason)
                conn.execute("UPDATE mcx_trades SET body=? WHERE id=?", (json.dumps(row), identifier))

    def trades(self, open_only=False):
        with closing(self.connect()) as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT body FROM mcx_trades WHERE (?=0 OR status='open') ORDER BY rowid DESC", (int(open_only),))]

    def records(self, kind, start, end):
        if kind not in {"decisions", "events"}:
            raise ValueError("Unknown record kind")
        with closing(self.connect()) as conn:
            return [json.loads(r[0]) for r in conn.execute(f"SELECT body FROM mcx_{kind} WHERE session>=? AND session<=? ORDER BY rowid DESC", (start, end))]


def date_range(start, end, now):
    start = date.fromisoformat(start) if start else now.date()
    end = date.fromisoformat(end) if end else now.date()
    if start > end:
        raise ValueError("From date must not be after To date")
    return start.isoformat(), end.isoformat()


def performance(trades):
    closed = [t for t in trades if t["status"] == "closed" and t.get("performance_eligible")]
    values = [t["net_r"] for t in closed]
    gain, loss = sum(max(r, 0) for r in values), -sum(min(r, 0) for r in values)
    return {"open": sum(t["status"] == "open" for t in trades), "closed": len(closed),
            "excluded": sum(t["status"] == "closed" and not t.get("performance_eligible") for t in trades),
            "win_rate": sum(r > 0 for r in values) / len(values) * 100 if values else None,
            "net_r": sum(values), "expectancy_r": sum(values)/len(values) if values else None,
            "profit_factor": gain/loss if loss else None}
