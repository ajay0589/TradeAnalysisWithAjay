"""As-received option observations and conservative multi-window evidence."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from math import isfinite

from trading_analysis.live_timing import IST, as_ist

WINDOWS = (3, 6, 15, 30)
FIELDS = ("tradingsymbol", "strike", "option_type", "oi", "volume", "last_price",
          "bid_price", "ask_price", "exchange_timestamp", "last_trade_time", "received_at")


class OptionHistory:
    def __init__(self, db_path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS option_evidence (
                id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, expiry TEXT NOT NULL,
                received_at TEXT NOT NULL, rows_json TEXT NOT NULL,
                UNIQUE(symbol, expiry, received_at))""")
            conn.execute("CREATE INDEX IF NOT EXISTS option_evidence_time ON option_evidence(symbol, received_at)")

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, symbol, rows):
        if not rows or not rows[0].get("expiry"):
            return None
        # Old CSVs have no reliable availability time. Never backdate their receipt.
        received = max((as_ist(row["received_at"]) for row in rows if row.get("received_at")), default=datetime.now(IST))
        expiry = rows[0]["expiry"]
        if any(row.get("expiry") != expiry for row in rows):
            raise ValueError("Option observation mixes expiries")
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO option_evidence(symbol, expiry, received_at, rows_json) VALUES(?,?,?,?)",
                         (symbol, expiry, received.isoformat(), json.dumps([{key: row.get(key) for key in FIELDS} for row in rows])))
        return received.isoformat()

    def load(self, symbol, start, end):
        with closing(self.connect()) as conn:
            rows = conn.execute("SELECT * FROM option_evidence WHERE symbol=? AND received_at>=? AND received_at<=? ORDER BY received_at",
                                (symbol, as_ist(start).isoformat(), as_ist(end).isoformat())).fetchall()
        return [{key: row[key] for key in ("id", "symbol", "expiry", "received_at")} | {"rows": json.loads(row["rows_json"])} for row in rows]

    def current(self, symbol, now=None):
        now = as_ist(now or datetime.now(IST))
        return rolling_evidence(self.load(symbol, now - timedelta(minutes=45), now), now)


def _number(row, key):
    try:
        value = float(row.get(key) or 0)
        return value if isfinite(value) else 0.0
    except (ValueError, TypeError):
        return 0.0


def _contracts(snapshot):
    return {(_number(row, "strike"), str(row["option_type"]).upper()): row for row in snapshot["rows"]
            if _number(row, "strike") > 0 and str(row.get("option_type")).upper() in {"CE", "PE"}}


def _fresh_contract(row, received):
    try:
        age = (received - as_ist(row["exchange_timestamp"])).total_seconds()
        return 0 <= age <= 180
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def compare_snapshots(current, previous):
    from trading_analysis.index_signal import option_footprint

    unavailable = {"bias": "unavailable", "flow_bias": "unavailable", "matched_contracts": 0}
    end, begin = as_ist(current["received_at"]), as_ist(previous["received_at"])
    if current["expiry"] != previous["expiry"] or begin.date() != end.date() or begin >= end:
        return {**unavailable, "reason": "Same-session, same-expiry observations required"}
    new, old = _contracts(current), _contracts(previous)
    common = set(new) & set(old)
    valid = [key for key in common if _fresh_contract(new[key], end) and _fresh_contract(old[key], begin)]
    coverage = len(valid) / max(len(new), len(old), 1)
    if len(valid) < 8 or coverage < 0.7:
        return {**unavailable, "coverage": coverage, "reason": "Insufficient matched contracts with exchange timestamps"}
    fresh, before = [new[key] for key in valid], [old[key] for key in valid]
    evidence = option_footprint(fresh, before, None)
    flow = {"bullish": 0, "bearish": 0}
    liquid_contracts = 0
    increments = {"CE": 0, "PE": 0}
    old_oi = {side: sum(_number(old[key], "oi") for key in valid if key[1] == side) for side in increments}
    for key in valid:
        row, prior = new[key], old[key]
        volume = _number(row, "volume") - _number(prior, "volume")
        if volume < 0:
            return {**unavailable, "reason": "Cumulative volume reset; a new baseline is required"}
        increments[key[1]] += volume
        price, bid, ask = (_number(row, field) for field in ("last_price", "bid_price", "ask_price"))
        change = price - _number(prior, "last_price")
        if row.get("volume") is None or prior.get("volume") is None or price <= 0 or not 0 < bid <= ask or (ask - bid) / price > 0.05:
            continue
        liquid_contracts += 1
        if volume <= 0 or change == 0:
            continue
        direction = "bullish" if (key[1] == "CE") == (change > 0) else "bearish"
        flow[direction] += 1
    flow_bias = "unclear"
    for direction, opposite in (("bullish", "bearish"), ("bearish", "bullish")):
        if flow[direction] >= 4 and flow[direction] >= 2 * max(1, flow[opposite]):
            flow_bias = direction
    if liquid_contracts < 8 or liquid_contracts / max(len(new), len(old), 1) < 0.7:
        flow_bias = "unavailable"
    return {**evidence, "coverage": round(coverage, 3), "flow_bias": flow_bias,
            "flow_votes": flow, "liquid_contracts": liquid_contracts, "call_volume_increment": increments["CE"], "put_volume_increment": increments["PE"],
            "call_oi_change_percent": evidence["call_oi_change"] / old_oi["CE"] * 100 if old_oi["CE"] else None,
            "put_oi_change_percent": evidence["put_oi_change"] / old_oi["PE"] * 100 if old_oi["PE"] else None,
            "from_received_at": begin.isoformat(), "to_received_at": end.isoformat(),
            "elapsed_seconds": (end - begin).total_seconds()}


def rolling_evidence(snapshots, as_of):
    as_of = as_ist(as_of)
    available = sorted([row for row in snapshots if as_ist(row["received_at"]) <= as_of], key=lambda row: row["received_at"])
    result = {"bias": "unavailable", "flow_bias": "unavailable", "windows": {}, "state": "warming_up",
              "oi_event_time": None, "note": "Positioning heuristics, not identified buyers/sellers. OI publication time is unknown."}
    if not available:
        return {**result, "reason": "No as-received option history"}
    latest = available[-1]
    end = as_ist(latest["received_at"])
    result.update(received_at=end.isoformat(), age_seconds=(as_of - end).total_seconds(), expiry=latest["expiry"])
    if end.date() != as_of.date() or (as_of - end).total_seconds() > 600:
        return {**result, "state": "stale", "reason": "Latest observation is stale"}
    same = [row for row in available if row["expiry"] == latest["expiry"] and as_ist(row["received_at"]).date() == end.date()]
    for minutes in WINDOWS:
        cutoff = end - timedelta(minutes=minutes)
        candidates = [row for row in same[:-1] if as_ist(row["received_at"]) <= cutoff]
        if not candidates:
            result["windows"][str(minutes)] = {"bias": "unavailable", "reason": "Window history is not yet available"}
            continue
        before = candidates[-1]
        if (cutoff - as_ist(before["received_at"])).total_seconds() > 180:
            result["windows"][str(minutes)] = {"bias": "unavailable", "reason": "Gap in observation history"}
            continue
        result["windows"][str(minutes)] = compare_snapshots(latest, before)
    valid = [row for row in result["windows"].values() if row["bias"] != "unavailable"]
    votes = Counter(row["bias"] for row in valid)
    bias = "unclear"
    for direction, opposite in (("bullish", "bearish"), ("bearish", "bullish")):
        if votes[direction] >= 2 and not votes[opposite]:
            bias = direction
    short = result["windows"].get("3", {})
    result.update(bias=bias if len(valid) >= 2 else "unavailable", flow_bias=short.get("flow_bias", "unavailable"),
                  state="ready" if len(valid) >= 2 else "warming_up", agreeing_windows=votes.get(bias, 0),
                  valid_windows=len(valid), pcr_oi=short.get("pcr_oi"))
    # An observed value change is not an exchange OI publication timestamp.
    result["last_observed_oi_change_at"] = None
    for previous, current in reversed(list(zip(same, same[1:]))):
        old, new = _contracts(previous), _contracts(current)
        if any(_number(new[key], "oi") != _number(old[key], "oi") for key in set(new) & set(old)):
            result["last_observed_oi_change_at"] = current["received_at"]
            break
    return result
