"""Transactional paper trades, decision audit, replay samples and fill journal."""
from contextlib import closing
import hashlib
import json

from trading_analysis.nifty_lab.repository import LabRepository
from trading_analysis.expiry.rules import VERSION


class ExpiryRepository(LabRepository):
    def __init__(self, path):
        super().__init__(path)
        with closing(self.connect()) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS expiry_frames(id INTEGER PRIMARY KEY, session TEXT, symbol TEXT, body TEXT);
                CREATE INDEX IF NOT EXISTS expiry_frame_session ON expiry_frames(session,id);
                CREATE TABLE IF NOT EXISTS expiry_manual(id INTEGER PRIMARY KEY, trade_id TEXT, body TEXT);
                CREATE TABLE IF NOT EXISTS expiry_replay(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT);
            """)

    def record_decision(self, decision, trade=None):
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM lab_decisions WHERE id=?", (decision["id"],)).fetchone():
                return None
            if trade and conn.execute("SELECT 1 FROM lab_trades WHERE setup=? AND variant='paper' AND status='open'", (decision["setup"],)).fetchone():
                trade = None
                decision["reason"] = "already_open"
            if trade:
                trade.update(id="EX-"+hashlib.sha256(decision["id"].encode()).hexdigest()[:12].upper(),
                    setup=decision["setup"], version=VERSION, variant="paper", status="open")
                conn.execute("INSERT INTO lab_trades VALUES(?,?,?,'open',?,NULL,?)", (trade["id"], trade["setup"], "paper", trade["entry_time"][:10], json.dumps(trade)))
                if trade["portfolio_accepted"]:
                    portfolio={**trade,"id":trade["id"]+"-P","parent_id":trade["id"],"variant":"portfolio"}
                    conn.execute("INSERT INTO lab_trades VALUES(?,?,?,'open',?,NULL,?)", (portfolio["id"],portfolio["setup"],"portfolio",trade["entry_time"][:10],json.dumps(portfolio)))
                if trade["telegram_enabled"]:
                    self._enqueue(conn, trade, "entry", trade["entry_time"])
            conn.execute("INSERT INTO lab_decisions VALUES(?,?,?,?)", (decision["id"],decision["setup"],decision["checked_at"][:10],json.dumps(decision)))
        return trade

    def save_trade(self, trade):
        with closing(self.connect()) as conn, conn:
            changed = conn.execute("UPDATE lab_trades SET status=?,exit_day=?,body=? WHERE id=? AND status='open'",
                (trade["status"], (trade.get("exit_time") or "")[:10] or None,json.dumps(trade),trade["id"])).rowcount
            if changed and trade["status"] == "closed" and trade["telegram_enabled"]:
                self._enqueue(conn,trade,"exit",trade["exit_time"])

    def _enqueue(self, conn, trade, kind, now):
        ledger="PORTFOLIO" if trade.get("variant")=="portfolio" else "RESEARCH"
        message = (f"EXPIRY LAB - UNVALIDATED PAPER {ledger} {kind.upper()}\n{trade['setup']} | {trade['direction']}\n"
            f"Trade: {trade['id']} | Expiry: {trade['long']['expiry']} | Qty: {trade['quantity']}\n"
            f"BUY {trade['long']['tradingsymbol']} / SELL {trade['short']['tradingsymbol']}\n"
            f"Signal: {trade['signal_closed_at']}\nPaper entry: {trade['entry_time']} IST | debit {trade['debit']:.2f}\n"
            f"Spot invalidation: {trade['stop_level']:.2f} | risk allowance INR {trade['risk_budget']:.2f}\n"
            f"Portfolio: {trade['portfolio_reason']}\n")
        if kind == "exit":
            message += f"Exit: {now} IST | {trade['exit_reason']} | net INR {trade.get('net_pnl')}\n"
        message += "Bid/ask simulation with assumed costs, NOT broker fills. No order placed."
        conn.execute("INSERT OR IGNORE INTO alert_delivery_outbox(event_key,symbol,trade_id,event_kind,prefix,message,status,created_at,updated_at,next_attempt_at) VALUES(?,?,?,?,?,?,'queued',?,?,?)",
            (f"EXPIRY:{trade['id']}:{kind}",trade["symbol"],trade["id"],kind,"EXPIRY_",message,now,now,now))

    def trades(self, day=None, setup=None, open_only=False, variant="paper"):
        return [t for t in super().trades(day,setup,open_only) if variant is None or t.get("variant")==variant]

    def portfolio_trades(self, open_only=False):
        return self.trades(open_only=open_only,variant="portfolio")

    def frame(self, symbol, now, body):
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT INTO expiry_frames(session,symbol,body) VALUES(?,?,?)",(now.date().isoformat(),symbol,json.dumps(body)))

    def frames(self, first, last):
        with closing(self.connect()) as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT body FROM expiry_frames WHERE session BETWEEN ? AND ? ORDER BY id",(first,last))]

    def manual(self, body):
        with closing(self.connect()) as conn, conn:
            if not conn.execute("SELECT 1 FROM lab_trades WHERE id=?", (body["trade_id"],)).fetchone():
                raise ValueError("Unknown trade ID")
            conn.execute("INSERT INTO expiry_manual(trade_id,body) VALUES(?,?)",(body["trade_id"],json.dumps(body)))

    def manual_rows(self):
        with closing(self.connect()) as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT body FROM expiry_manual ORDER BY id")]

    def save_replay(self, body):
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO expiry_replay VALUES(1,?)", (json.dumps(body),))

    def replay_report(self):
        with closing(self.connect()) as conn:
            row=conn.execute("SELECT body FROM expiry_replay WHERE id=1").fetchone()
            return json.loads(row[0]) if row else {"phase":"not_run", "reason":"No historical option-quote validation yet"}
