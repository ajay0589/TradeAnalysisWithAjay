"""Read-only, sampled futures confirmation. Never gates an existing strategy."""
from collections import deque
from contextlib import closing
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import sqlite3
import threading

from trading_analysis.live_timing import IST, as_ist
from trading_analysis.storage import DEFAULT_DB_PATH

VERSION = "futures-shadow-v1"
CONTRACTS = {"NIFTY": ("NFO", "NIFTY", "NSE:NIFTY 50"),
             "BANKNIFTY": ("NFO", "BANKNIFTY", "NSE:NIFTY BANK"),
             "SENSEX": ("BFO", "SENSEX", "BSE:SENSEX")}
POLICY = {"sample_seconds": 15, "max_age_seconds": 30, "max_sync_seconds": 5,
          "max_gap_seconds": 45, "price_noise_pct": .03, "oi_noise_pct": .5,
          "max_spread_pct": .1, "windows_minutes": [3, 6, 15], "mode": "shadow_only"}


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def select_contract(rows, symbol, now):
    exchange, name, _ = CONTRACTS[symbol]
    valid = [r for r in rows if r.get("name") == name and r.get("instrument_type") == "FUT"
             and r.get("exchange") == exchange and str(r.get("expiry", ""))[:10] >= now.date().isoformat()]
    if not valid:
        return None
    row = min(valid, key=lambda r: (str(r["expiry"]), r["tradingsymbol"]))
    return {"exchange": exchange, "tradingsymbol": row["tradingsymbol"], "expiry": str(row["expiry"])[:10],
            "lot_size": int(row["lot_size"]), "instrument_token": int(row["instrument_token"])}


def evaluate(samples, now, direction=None):
    now = as_ist(now)
    result = {"version": VERSION, "mode": "shadow_only", "state": "unavailable", "bias": "unavailable",
              "alignment": "unavailable", "reason": "no_samples", "windows": {}, "policy": POLICY}
    if not samples:
        return result
    latest = samples[-1]
    result["latest"] = latest
    if any(not 0 <= (now-as_ist(latest[k])).total_seconds() <= POLICY["max_age_seconds"]
           for k in ("time", "quote_time", "last_trade_time", "spot_quote_time")):
        return {**result, "state": "stale", "reason": "futures_sample_stale"}
    result.update(state="warming_up", reason="need_15_minutes_same_contract")
    samples = [s for s in samples if s["contract"] == latest["contract"] and s["time"][:10] == latest["time"][:10]
               and s["time"] <= latest["time"]]
    for minutes in POLICY["windows_minutes"]:
        cutoff = as_ist(latest["time"]) - timedelta(minutes=minutes)
        prior = [s for s in samples if as_ist(s["time"]) <= cutoff]
        if not prior or (cutoff-as_ist(prior[-1]["time"])).total_seconds() > 30:
            result["windows"][str(minutes)] = {"state": "warming_up"}
            continue
        first = prior[-1]
        window = [s for s in samples if s["time"] >= first["time"]]
        if any((as_ist(b["time"])-as_ist(a["time"])).total_seconds() > POLICY["max_gap_seconds"]
               or b["volume"] < a["volume"] for a, b in zip(window, window[1:])):
            result["windows"][str(minutes)] = {"state": "gap_or_volume_reset"}
            continue
        price = 100*(latest["price"]/first["price"]-1)
        spot = 100*(latest["spot"]/first["spot"]-1)
        oi = 100*(latest["oi"]/first["oi"]-1)
        volume = latest["volume"]-first["volume"]
        bias = "neutral"
        if volume > 0 and price > POLICY["price_noise_pct"] and spot > POLICY["price_noise_pct"] and latest["price"] > latest["vwap"]:
            bias = "bullish"
        elif volume > 0 and price < -POLICY["price_noise_pct"] and spot < -POLICY["price_noise_pct"] and latest["price"] < latest["vwap"]:
            bias = "bearish"
        classification = "unchanged_or_small"
        if abs(price) > POLICY["price_noise_pct"] and abs(oi) > POLICY["oi_noise_pct"]:
            classification = ("long_buildup" if price > 0 else "short_buildup") if oi > 0 else ("short_covering" if price > 0 else "long_unwinding")
        result["windows"][str(minutes)] = {"state": "ready", "from_time": first["time"], "to_time": latest["time"],
            "price_change_pct": price, "spot_change_pct": spot, "oi_change_pct": oi, "volume_delta": volume,
            "basis_change": latest["basis"]-first["basis"], "bias": bias, "interpretation": classification}
    windows = list(result["windows"].values())
    if all(w["state"] == "ready" for w in windows):
        biases = {w["bias"] for w in windows}
        bias = biases.pop() if len(biases) == 1 else "mixed"
        result.update(state="ready", bias=bias, reason="multi_window_confirmation" if bias in {"bullish", "bearish"} else "windows_neutral_or_disagree")
        if direction:
            result["alignment"] = "aligned" if bias == direction else "conflict" if bias in {"bullish", "bearish"} else "unconfirmed"
        else:
            result["alignment"] = "not_evaluated"
    return result


class FuturesContext:
    def __init__(self, path=None):
        self.path = Path(path or DEFAULT_DB_PATH.with_name("futures_context.db"))
        self.lock = threading.RLock()
        self.samples = {s: deque(maxlen=100) for s in CONTRACTS}
        self.errors = {}
        self.last_poll = None
        self._catalog = {}

    def observe(self, symbol, contract, quote, spot, now):
        now = as_ist(now)
        try:
            stamp, traded, spot_stamp = (as_ist(quote[k]) for k in ("timestamp", "last_trade_time", "spot_timestamp"))
            if any(not 0 <= (now-t).total_seconds() <= 30 for t in (stamp, traded, spot_stamp)) or abs((stamp-spot_stamp).total_seconds()) > 5:
                raise ValueError("stale_or_unsynchronized_quotes")
            price, oi, volume, vwap, spot_price = [number(v) for v in (quote.get("last_price"), quote.get("oi"), quote.get("volume"), quote.get("average_price"), spot.get("last_price"))]
            if any(v is None or v <= 0 for v in (price, oi, volume, vwap, spot_price)):
                raise ValueError("missing_price_volume_oi_or_vwap")
            depth = quote.get("depth") or {}
            bid, ask = (depth.get("buy") or [{}])[0], (depth.get("sell") or [{}])[0]
            bp, ap = number(bid.get("price")), number(ask.get("price"))
            if not bp or not ap or ap < bp or (number(bid.get("quantity")) or 0) <= 0 or (number(ask.get("quantity")) or 0) <= 0 or 100*(ap-bp)/price > POLICY["max_spread_pct"]:
                raise ValueError("illiquid_futures_quote")
            row = {"version": VERSION, "time": now.isoformat(), "quote_time": stamp.isoformat(), "last_trade_time": traded.isoformat(),
                   "spot_quote_time": spot_stamp.isoformat(), "contract": contract, "price": price, "spot": spot_price,
                   "vwap": vwap, "oi": oi, "volume": volume, "basis": price-spot_price, "bid": bp, "ask": ap,
                   "expiry_day": contract["expiry"] == now.date().isoformat()}
            with self.lock:
                history = self.samples[symbol]
                if history and (history[-1]["contract"] != contract or history[-1]["time"][:10] != row["time"][:10]):
                    history.clear()
                if history and ((now-as_ist(history[-1]["time"])).total_seconds() < 15 or stamp <= as_ist(history[-1]["quote_time"])):
                    return
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                    conn.execute("CREATE TABLE IF NOT EXISTS futures_samples(symbol TEXT, time TEXT, body TEXT, PRIMARY KEY(symbol,time))")
                    conn.execute("INSERT OR IGNORE INTO futures_samples VALUES(?,?,?)", (symbol,row["time"],json.dumps(row)))
                history.append(row)
                self.errors.pop(symbol, None)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            with self.lock:
                self.errors[symbol] = str(exc)

    def poll(self, now=None):
        """One batched request every 15 seconds on the existing rate-limited worker."""
        from trading_analysis.brokers.zerodha import load_instruments_csv
        from trading_analysis.web_services import _zerodha_client
        now = as_ist(now or datetime.now(IST))
        with self.lock:
            if self.last_poll and (now-self.last_poll).total_seconds() < 15:
                return
            self.last_poll = now
        selected = {}
        for symbol, (exchange, _, _) in CONTRACTS.items():
            try:
                path = Path(f"data/raw/zerodha/instruments_{exchange}.csv")
                signature = (now.date(), path.stat().st_mtime_ns)
                if self._catalog.get(exchange, (None,))[0] != signature:
                    self._catalog[exchange] = (signature, load_instruments_csv(path))
                contract = select_contract(self._catalog[exchange][1], symbol, now)
                if not contract:
                    raise ValueError("Refresh instrument masters: no unexpired futures contract")
                selected[symbol] = contract
            except (OSError, ValueError) as exc:
                with self.lock:
                    self.errors[symbol] = str(exc)
        if not selected:
            return
        keys = [f"{c['exchange']}:{c['tradingsymbol']}" for c in selected.values()] + [CONTRACTS[s][2] for s in selected]
        try:
            rows = _zerodha_client().quotes(keys)
        except Exception:
            with self.lock:
                for symbol in selected:
                    self.errors[symbol] = "futures_refresh_failed; see app log"
            raise
        received = datetime.now(IST)
        for symbol, contract in selected.items():
            spot = rows.get(CONTRACTS[symbol][2], {})
            quote = rows.get(f"{contract['exchange']}:{contract['tradingsymbol']}", {})
            self.observe(symbol, contract, {**quote, "spot_timestamp": spot.get("timestamp")}, spot, received)

    def evidence(self, symbol, now=None, direction=None):
        with self.lock:
            result = evaluate(list(self.samples.get(symbol, [])), now or datetime.now(IST), direction)
            if symbol in self.errors:
                result.update(state="unavailable", bias="unavailable", alignment="unavailable", reason=self.errors[symbol])
            return result

    def export(self, first, last):
        with self.lock:
            if not self.path.exists():
                return []
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='futures_samples'").fetchone():
                    return []
                return [{"symbol": s, **json.loads(body)} for s, body in conn.execute(
                    "SELECT symbol,body FROM futures_samples WHERE substr(time,1,10) BETWEEN ? AND ? ORDER BY time", (first,last))]


FUTURES = FuturesContext()
