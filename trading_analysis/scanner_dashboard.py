"""Read-only scanner inventory and a filtered, ten-row cross-scanner trade ledger."""
from contextlib import closing
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3

from trading_analysis.live_quotes import LIVE_QUOTES
from trading_analysis.live_timing import IST, as_ist
from trading_analysis.storage import DEFAULT_DB_PATH
from trading_analysis.trade_context import capture_vix, result_mark


PAGE_SIZE = 10


def _table(path, name, status="all"):
    path = Path(path)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone():
            return []
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM {name} WHERE status IN ('open','closed') AND (?='all' OR status=?)", (status, status))]


def _json(value):
    return json.loads(value or "{}")


def normalize(row, source, now):
    meta = row.get("metadata") or row.get("context") or {}
    entered = row.get("entry_time") or row.get("opened_at")
    exited = row.get("exit_time") or row.get("closed_at")
    direction = row.get("direction") or row.get("side") or "bullish"
    symbol = row.get("symbol") or "NIFTY"
    quote = row.get("last_quote", {}) if source == "scalping" else LIVE_QUOTES.get(symbol)
    if not quote:
        quote = row.get("last_quote") or {}
    result = result_mark(row.get("entry_price"), direction, row["status"], row.get("exit_price"), quote, now, exited)
    end = as_ist(exited) if exited else now
    return {"id": row.get("trade_id") or row.get("id"), "scanner": source, "symbol": symbol,
            "status": row["status"], "direction": direction, "horizon": row.get("horizon") or row.get("purple_timeframe") or row.get("timeframe") or "intraday",
            "variant": row.get("variant") or row.get("entry_kind") or row.get("strategy_id") or row.get("strategy"),
            "entry_time": as_ist(entered).isoformat() if entered else None, "entry_price": row.get("entry_price"),
            "exit_time": as_ist(exited).isoformat() if exited else None, "exit_price": row.get("exit_price"),
            "open_seconds": max(0, (end - as_ist(entered)).total_seconds()) if entered else None,
            "result": result, "stop": row.get("stop_level", row.get("stop")), "target": row.get("target_level", row.get("target")),
            "exit_reason": row.get("exit_reason"), "net_r": row.get("net_r"),
            "india_vix_entry": row.get("india_vix_entry") or meta.get("india_vix_entry"),
            "india_vix_exit": row.get("india_vix_exit") or meta.get("india_vix_exit"),
            "price_basis": row.get("price_basis") or meta.get("price_basis") or "candle_reference",
            "exit_price_basis": row.get("exit_price_basis") or meta.get("exit_price_basis") or "candle_reference",
            **{key: meta.get(key) for key in ("reference_price", "exit_reference_price", "signal_delay_seconds", "exit_delay_seconds")}}


class ScannerDashboard:
    def __init__(self, services):
        self.services = services

    def all_trades(self, source="all", now=None, status="all"):
        now = now or datetime.now(IST)
        h, rows = self.services, []
        if source in {"all", "NIFTY"}:
            for row in _table(h.nifty_auto_service.trade_repository.db_path, "nifty_trades", status):
                row["metadata"] = _json(row.get("metadata_json"))
                rows.append(normalize(row, "NIFTY", now))
        if source in {"all", "BANKNIFTY", "SENSEX"}:
            for row in _table(h.index_scanner_service.repository.db_path, "index_scan_trades", status):
                if source != "all" and row["symbol"] != source:
                    continue
                row["context"] = _json(row.get("context_json"))
                rows.append(normalize(row, row["symbol"], now))
        if source in {"all", "pullbacks"}:
            rows.extend(normalize(_json(r["body"]), "pullbacks", now) for r in _table(h.pullback_scanner.repository.path, "pullback_setups", status))
        if source in {"all", "purple"}:
            for row in _table(getattr(h, "purple_db_path", DEFAULT_DB_PATH), "krishna_purple_trades", status):
                row["metadata"] = _json(row.get("metadata_json"))
                rows.append(normalize(row, "purple", now))
        if source == "all" or source.startswith("Nifty_Setup"):
            for raw in _table(h.nifty_lab.path, "lab_trades", status):
                row = _json(raw["body"])
                if source == "all" or row["setup"] == source:
                    rows.append(normalize(row, row["setup"], now))
        if source in {"all", "MCX"} and hasattr(h, "mcx_scanner"):
            with h.mcx_scanner._lock:
                quotes = dict(h.mcx_scanner._quotes)
            for row in h.mcx_scanner.repository.trades(open_only=status == "open"):
                if status != "all" and row["status"] != status:
                    continue
                quote = quotes.get(row["symbol"], {})
                row["last_quote"] = {**quote, "price": quote.get("bid" if row["direction"] == "bullish" else "ask")} if quote.get("fresh") else {}
                rows.append(normalize(row, "MCX", now))
        if source in {"all", "scalping"} and hasattr(h, "scalping_lab"):
            for row in h.scalping_lab.repo.trades(open_only=status == "open"):
                if status != "all" and row["status"] != status:
                    continue
                with h.scalping_lab.feed.lock:
                    row["last_quote"] = dict(h.scalping_lab.feed.quotes.get(row["symbol"], {}))
                rows.append(normalize(row, "scalping", now))
        return sorted(rows, key=lambda r: (r["entry_time"] or "", str(r["id"])), reverse=True)

    def ledger(self, source="all", status="all", from_date=None, to_date=None, date_basis="entry", page=1):
        allowed = {"all", "NIFTY", "BANKNIFTY", "SENSEX", "MCX", "scalping", "pullbacks", "purple", *[f"Nifty_Setup{i}" for i in range(1, 6)]}
        if source not in allowed or status not in {"all", "open", "closed"} or date_basis not in {"entry", "exit"}:
            raise ValueError("Invalid scanner, trade status, or date basis")
        start, end = (date.fromisoformat(v) if v else None for v in (from_date, to_date))
        if start and end and start > end:
            raise ValueError("From date must not be after To date")
        page = int(page)
        if page < 1:
            raise ValueError("Page must be positive")
        rows = self.all_trades(source, status=status)
        selected = []
        for row in rows:
            if status != "all" and row["status"] != status:
                continue
            stamp = row[f"{date_basis}_time"]
            if start or end:
                if not stamp:
                    continue
                day = as_ist(stamp).date()
                if (start and day < start) or (end and day > end):
                    continue
            selected.append(row)
        total = len(selected)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        return {"trades": selected[(page - 1) * PAGE_SIZE:page * PAGE_SIZE], "page": page, "pages": pages,
                "total": total, "page_size": PAGE_SIZE, "timezone": "Asia/Kolkata",
                "counts": {s: sum(r["status"] == s for r in selected) for s in ("open", "closed")},
                "filters": {"source": source, "status": status, "from_date": from_date, "to_date": to_date, "date_basis": date_basis}}

    def status(self):
        h = self.services
        rows, issues = [], []
        def add(key, label, state, tab, detail, subtab=None):
            rows.append({"id": key, "name": label, "running": bool(state.get("running", state.get("enabled", False))),
                         "phase": state.get("phase") or ("scheduled" if state.get("running") else "stopped"),
                         "started_at": state.get("started_at"), "stopped_at": state.get("stopped_at"),
                         "last_checked": state.get("last_checked") or state.get("last_cycle_at") or state.get("last_completed"),
                         "detail": detail, "tab": tab, "subtab": subtab})
        try:
            index = h.all_index_monitor.status()["scanners"]
            for key, label in (("NIFTY", "NIFTY"), ("BANKNIFTY", "Bank Nifty"), ("SENSEX", "Sensex")):
                add(key, label, index[key], "indices", "Intraday / swing / positional", key.lower())
        except Exception as exc:
            issues.append(f"Index status: {exc}")
            for key in ("NIFTY", "BANKNIFTY", "SENSEX"):
                add(key, key, {"phase": "unavailable"}, "indices", "Status unavailable", key.lower())
        with h.nifty_lab._lock:
            for key, state in h.nifty_lab.states.items():
                add(key, key, dict(state), "nifty-lab", "Intraday / technical + combined ledgers", key)
        if hasattr(h, "scalping_lab"):
            with h.scalping_lab._lock:
                for key, state in h.scalping_lab.states.items():
                    add("scalp_" + key, key, dict(state), "scalping", "Unvalidated paper scalping / A, B, C", key)
        if hasattr(h, "mcx_scanner"):
            with h.mcx_scanner._lock:
                for root in ("SILVERM", "GOLDM", "NATURALGAS", "CRUDEOIL"):
                    add(f"mcx_{root}", f"MCX {root}", dict(h.mcx_scanner.state), "mcx", "Swing futures / Daily trend + hourly breakout")
        with h.pullback_scanner._lock:
            for side in ("bullish", "bearish"):
                add(f"pullback_{side}", f"F&O {side} pullback", h.pullback_scanner.state, "pullbacks", "Shared pullback monitor")
        with h.service._purple_monitor_lock:
            state = dict(h.service._purple_monitor_state)
            for profile, label in (("month", "Monthly"), ("week", "Weekly"), ("day", "Daily")):
                add(f"purple_{profile}", f"Purple Touch {label}", state, "purple", "Setup discovery + independent entry/exit checks")
        for key, label in (("scans", "Stock strategy scans"), ("krishna", "Krishna Setup scan"), ("analyze", "Stock / index research")):
            add(key, label, {"phase": "on_demand"}, key, "Manual analysis; no recurring monitor")
        open_rows = self.all_trades(status="open")
        return {"scanners": rows, "running_count": sum(r["running"] for r in rows), "open_trade_count": len(open_rows),
                "india_vix": capture_vix(), "issues": issues, "as_of": datetime.now(IST).isoformat()}
