"""Independent paper ledgers; paired decisions and notification writes are atomic."""
from collections import Counter
from contextlib import closing
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

from trading_analysis.live_timing import as_ist
from trading_analysis.notifications.outbox import AlertOutbox
from trading_analysis.nifty_lab.setups import SETUPS, VARIANTS, VERSION


def day_value(value):
    return date.fromisoformat(value).isoformat()


def message(trade, kind):
    return (f"NIFTY SETUP LAB - PAPER {kind.upper()}\n{trade['setup']} | {trade['variant']} | {trade['direction']}\n"
            f"Trade: {trade['id']}\nEntry spot: {trade['entry_price']:.2f} at {trade['entry_time']} IST\n"
            f"Stop / Target: {trade['stop_level']:.2f} / {trade['target_level']:.2f}\n" +
            (f"Exit spot/reference: {trade['exit_price']:.2f} at {trade['exit_time']} IST\nReason: {trade['exit_reason']}\n"
             f"Open duration: {trade['open_seconds'] / 60:.1f} minutes\n" if kind == "exit" else "") +
            "Observed NIFTY spot, not option premium, strike, or broker fill. No order placed.")


class LabRepository:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.outbox = AlertOutbox(self.path)
        with closing(self.connect()) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS lab_decisions (
                    id TEXT PRIMARY KEY, setup TEXT NOT NULL, session TEXT NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS lab_decision_day ON lab_decisions(session, setup);
                CREATE TABLE IF NOT EXISTS lab_trades (
                    id TEXT PRIMARY KEY, setup TEXT NOT NULL, variant TEXT NOT NULL,
                    status TEXT NOT NULL, entry_day TEXT NOT NULL, exit_day TEXT, body TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS lab_one_open ON lab_trades(setup, variant) WHERE status='open';
                CREATE TABLE IF NOT EXISTS lab_events (
                    id INTEGER PRIMARY KEY, session TEXT NOT NULL, body TEXT NOT NULL);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    @staticmethod
    def decision_id(setup, bar):
        return f"{VERSION}:{setup}:{bar}"

    def seen(self, identifier):
        with closing(self.connect()) as conn:
            return bool(conn.execute("SELECT 1 FROM lab_decisions WHERE id=?", (identifier,)).fetchone())

    def event(self, event, now, **details):
        body = {"event": event, "time": now.isoformat(), **details}
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT INTO lab_events(session,body) VALUES(?,?)", (now.date().isoformat(), json.dumps(body)))

    def record(self, decision, entries):
        decision = json.loads(json.dumps(decision))
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM lab_decisions WHERE id=?", (decision["id"],)).fetchone():
                return []
            created = []
            for variant, trade in entries.items():
                if conn.execute("SELECT 1 FROM lab_trades WHERE setup=? AND variant=? AND status='open'",
                                (decision["setup"], variant)).fetchone():
                    decision["variants"][variant] = "already_open"
                    continue
                digest = hashlib.sha256((decision["id"] + variant).encode()).hexdigest()[:12].upper()
                trade = {**trade, "id": "NL-" + digest, "decision_id": decision["id"], "variant": variant,
                         "setup": decision["setup"], "version": VERSION, "status": "open"}
                conn.execute("INSERT INTO lab_trades VALUES(?,?,?,'open',?,NULL,?)",
                             (trade["id"], trade["setup"], variant, trade["entry_time"][:10], json.dumps(trade)))
                if trade["telegram_enabled"]:
                    self._enqueue(conn, trade, "entry", trade["entry_time"])
                decision["variants"][variant] = "entry_created"
                created.append(trade)
            conn.execute("INSERT INTO lab_decisions VALUES(?,?,?,?)",
                         (decision["id"], decision["setup"], decision["checked_at"][:10], json.dumps(decision)))
            return created

    def _enqueue(self, conn, trade, kind, now):
        conn.execute("INSERT OR IGNORE INTO alert_delivery_outbox(event_key,symbol,trade_id,event_kind,prefix,message,status,created_at,updated_at,next_attempt_at) VALUES(?,?,?,?,?,?,'queued',?,?,?)",
                     (f"NIFTY:{trade['id']}:{kind}", "NIFTY", trade["id"], kind, "NIFTY_LAB_", message(trade, kind), now, now, now))

    def close(self, trade, price, now, reason, basis="observed_spot_not_fill", detected_at=None):
        trade = dict(trade)
        sign = 1 if trade["direction"] == "bullish" else -1
        points = (price - trade["entry_price"]) * sign
        cost = (price + trade["entry_price"]) * trade["cost_bps_per_side"] / 10000
        trade.update(status="closed", exit_price=price, exit_time=now.isoformat(), exit_reason=reason,
                     exit_price_basis=basis, exit_detected_at=(detected_at or now).isoformat(),
                     open_seconds=max(0, (now - as_ist(trade["entry_time"])).total_seconds()),
                     gross_points=points, cost_points=cost, net_points=points - cost,
                     net_r=(points - cost) / trade["risk_points"],
                     performance_eligible=basis == "observed_spot_not_fill" and not trade.get("monitoring_interrupted"))
        with closing(self.connect()) as conn, conn:
            updated = conn.execute("UPDATE lab_trades SET status='closed',exit_day=?,body=? WHERE id=? AND status='open'",
                                   (now.date().isoformat(), json.dumps(trade), trade["id"])).rowcount
            if updated and trade["telegram_enabled"] and basis == "observed_spot_not_fill":
                self._enqueue(conn, trade, "exit", (detected_at or now).isoformat())
        return trade if updated else None

    def interrupt_open(self, reason, setup=None):
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT id,body FROM lab_trades WHERE status='open' AND (? IS NULL OR setup=?)", (setup, setup)).fetchall()
            for identifier, body in rows:
                trade = json.loads(body)
                trade.setdefault("monitoring_interrupted", reason)
                conn.execute("UPDATE lab_trades SET body=? WHERE id=?", (json.dumps(trade), identifier))

    def trades(self, day=None, setup=None, open_only=False):
        with closing(self.connect()) as conn:
            rows = conn.execute("""SELECT body FROM lab_trades WHERE
                (? IS NULL OR entry_day=? OR exit_day=? OR (entry_day<=? AND (exit_day IS NULL OR exit_day>=?)))
                AND (? IS NULL OR setup=?) AND (?=0 OR status='open') ORDER BY rowid DESC""",
                (day, day, day, day, day, setup, setup, int(open_only))).fetchall()
        return [json.loads(r[0]) for r in rows]

    def decisions(self, day):
        with closing(self.connect()) as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT body FROM lab_decisions WHERE session=? ORDER BY rowid DESC", (day,))]

    def events(self, day):
        with closing(self.connect()) as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT body FROM lab_events WHERE session=? ORDER BY id", (day,))]


def performance(trades, day):
    closed = sorted((t for t in trades if t["status"] == "closed" and t.get("exit_time", "")[:10] == day and t.get("performance_eligible")),
                    key=lambda t: (t["exit_time"], t["id"]))
    net = [t["net_r"] for t in closed]
    wins = sum(r > 0 for r in net)
    gain, loss = sum(max(0, r) for r in net), -sum(min(0, r) for r in net)
    equity = peak = dd = 0.0
    curve = [{"time": None, "net_r": 0}]
    for t in closed:
        equity += t["net_r"]
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
        curve.append({"time": t["exit_time"], "net_r": equity})
    return {"closed": len(closed), "open": sum(t["status"] == "open" for t in trades), "wins": wins,
            "win_rate": wins / len(net) * 100 if net else None, "net_r": equity,
            "expectancy_r": equity / len(net) if net else None, "profit_factor": gain / loss if loss else None,
            "no_losses": bool(net) and loss == 0, "max_drawdown_r": dd,
            "reference_exits": sum(t["status"] == "closed" and t.get("exit_time", "")[:10] == day and not t.get("performance_eligible") for t in trades),
            "average_open_minutes": sum(t["open_seconds"] for t in closed) / len(closed) / 60 if closed else None,
            "curve": curve}


def comparisons(trades, decisions, day):
    result = []
    for setup in SETUPS:
        selected = [d for d in decisions if d["setup"] == setup]
        candidates = [d for d in selected if d.get("direction")]
        metrics = {v: performance([t for t in trades if t["setup"] == setup and t["variant"] == v], day) for v in VARIANTS}
        paired = Counter(t["decision_id"] for t in trades if t["setup"] == setup)
        result.append({"setup": setup, "checked_candles": len(selected), "signals": len(candidates),
                       "paired_signals": sum(n == 2 for n in paired.values()), "metrics": metrics,
                       "combined_gates": dict(Counter(d.get("variants", {}).get("combined", "unknown") for d in candidates))})
    return result
