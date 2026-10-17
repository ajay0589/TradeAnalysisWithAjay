"""Cached-data, next-minute-open baseline research; never sends alerts or places orders."""
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, time
import hashlib

from trading_analysis.brokers.zerodha import merge_candles_csv, resolve_instrument_token
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.live_timing import IST, as_ist
from trading_analysis.scalping.rules import INDICES, SETUPS, POLICY, VERSION, technical, aggregate

WINDOWS = {"1 week": 7, "2 weeks": 14, "1 month": 30, "2 months": 60, "3 months": 90, "6 months": 180}


def replay(rows, signals, setup, target_r):
    trades, current, last_exit = [], None, None
    counts = {}
    for i, candle in enumerate(rows):
        stamp = as_ist(candle.timestamp)
        if current and (stamp.date() != current["entry"].date() or (i and stamp - rows[i-1].timestamp != timedelta(minutes=1))):
            trades.append({"entry_time":current["entry"].isoformat(), "exit_time":stamp.isoformat(), "net_r":None,
                           "excluded_reason":"candle_gap", "ambiguous":False})
            current = None
        if not current and i > 0 and (not last_exit or stamp - last_exit >= timedelta(minutes=POLICY["cooldown_minutes"])):
            reading = signals.get(i - 1, {})
            signal = reading.get("signals", {}).get(setup)
            if signal and reading["closed_at"] == stamp.isoformat() and counts.get(stamp.date(), 0) < POLICY["max_entries_per_ledger_day"]:
                sign = 1 if signal["direction"] == "bullish" else -1
                target = signal["reference"] + sign * signal["risk"] * target_r
                risk = (candle.open - signal["stop"]) * sign
                reward = (target - candle.open) * sign
                if risk > 0 and reward > 0 and reward / risk >= min(1, target_r) and abs(candle.open - signal["reference"]) <= .25 * min(signal["risk"], signal["atr"]):
                    current = {"entry": stamp, "price": candle.open, "stop": signal["stop"], "target": target, "risk": risk, "sign": sign}
                    counts[stamp.date()] = counts.get(stamp.date(), 0) + 1
        if not current:
            continue
        sign = current["sign"]
        stopped = candle.low <= current["stop"] if sign == 1 else candle.high >= current["stop"]
        targeted = candle.high >= current["target"] if sign == 1 else candle.low <= current["target"]
        exit_price = None
        if stopped:
            exit_price = min(candle.open, current["stop"]) if sign == 1 else max(candle.open, current["stop"])
        elif targeted:
            exit_price = current["target"]
        elif stamp + timedelta(minutes=1) - current["entry"] >= timedelta(minutes=POLICY["hold_minutes"]) or stamp.time().replace(tzinfo=None) >= time(15, 19):
            exit_price = candle.close
        if exit_price is not None:
            cost = (exit_price + current["price"]) * POLICY["cost_bps_side"] / 10000
            trades.append({"entry_time": current["entry"].isoformat(), "exit_time": (stamp + timedelta(minutes=1)).isoformat(),
                           "net_r": ((exit_price - current["price"]) * sign - cost) / current["risk"],
                           "ambiguous": stopped and targeted})
            current, last_exit = None, stamp + timedelta(minutes=1)
    if current:
        trades.append({"entry_time":current["entry"].isoformat(), "exit_time":None, "net_r":None,
                       "excluded_reason":"missing_exit", "ambiguous":False})
    return trades


def summarize(trades):
    values = [t["net_r"] for t in trades if t["net_r"] is not None]
    gain, loss = sum(max(0, v) for v in values), -sum(min(0, v) for v in values)
    total = peak = dd = 0
    for value in values:
        total += value
        peak = max(peak, total)
        dd = max(dd, peak - total)
    return {"trades": len(values), "win_rate": 100 * sum(v > 0 for v in values) / len(values) if values else None,
            "expectancy_r": total / len(values) if values else None, "profit_factor": gain / loss if loss else None,
            "net_r": total, "max_drawdown_r": dd, "ambiguous": sum(t["ambiguous"] for t in trades),
            "excluded": sum(t["net_r"] is None for t in trades)}


def run_research(feed, download=False, progress=lambda message: None, cancelled=lambda: False):
    now = datetime.now(IST)
    # Latest completed session, not the final candle of a partial trading day.
    end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if end > now:
        end -= timedelta(days=1)
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    report = {"version": VERSION, "as_of": end.isoformat(), "rows": [], "coverage": {}, "errors": {},
              "validation": "not_promoted", "options_replay": "unavailable_without_historical_fixed_contract_quotes",
              "limitations": ["Baseline development research, not out-of-sample approval or option P&L.",
                  "Next-minute-open proxy; 5/15/30-second manual latency requires recorded tick/quote history.",
                  "Stop assumed first if both levels touch; two basis points per side assumed spot friction.",
                  "Overlapping windows are not independent evidence. No automatic strategy promotion."]}
    for symbol, (exchange, name, _, _) in INDICES.items():
        if cancelled():
            raise InterruptedError("Research cancelled")
        progress(f"{symbol}: loading minute candles")
        try:
            if download:
                from trading_analysis.web_services import _zerodha_client
                token = resolve_instrument_token(feed.analysis._instruments_for_exchange(exchange), exchange, name)
                candles = _zerodha_client().historical_candles(token, "minute", end - timedelta(days=190), end)
                merge_candles_csv(feed.path(symbol), [c for c in candles if as_ist(c.timestamp) + timedelta(minutes=1) <= end])
            rows = aggregate(load_candles(feed.path(symbol))) if feed.path(symbol).exists() else []
            rows = [c for c in rows if c.timestamp + timedelta(minutes=1) <= end]
            if not rows:
                report["coverage"][symbol] = {"status": "missing"}
                for frame in (1, 3):
                    for setup in SETUPS:
                        for rr in (1, 1.5, 2):
                            for label in WINDOWS:
                                report["rows"].append({"symbol": symbol, "setup": setup, "minutes": frame, "target_r": rr,
                                    "window": label, "coverage": "missing", **summarize([])})
                continue
            session_counts = {}
            for candle in rows:
                session_counts[candle.timestamp.date().isoformat()] = session_counts.get(candle.timestamp.date().isoformat(), 0) + 1
            report["coverage"][symbol] = {"first": rows[0].timestamp.isoformat(), "last": rows[-1].timestamp.isoformat(), "bars": len(rows),
                "sessions": len(session_counts), "incomplete_sessions": {d:n for d,n in session_counts.items() if n != 375},
                "sha256": hashlib.sha256(feed.path(symbol).read_bytes()).hexdigest()}
            stamps = [c.timestamp for c in rows]
            higher = aggregate(rows, 5)
            higher_closes = [c.timestamp + timedelta(minutes=5) for c in higher]
            opening = {}
            for c in rows:
                if time(9, 15) <= c.timestamp.time().replace(tzinfo=None) < time(9, 30):
                    opening.setdefault(c.timestamp.date(), []).append(c)
            for frame in (1, 3):
                signals = {}
                base = aggregate(rows, frame)
                base_closes = [c.timestamp + timedelta(minutes=frame) for c in base]
                for i in range(300, len(rows)):
                    if cancelled():
                        raise InterruptedError("Research cancelled")
                    current = rows[i].timestamp + timedelta(minutes=1)
                    if not time(9, 30) <= current.time().replace(tzinfo=None) < time(14, 45):
                        continue
                    if int((current - current.replace(hour=9, minute=15, second=0, microsecond=0)).total_seconds() / 60) % frame:
                        continue
                    if i % 1000 == 0:
                        progress(f"{symbol} {frame}m: {i}/{len(rows)} candles")
                    b, h = bisect_right(base_closes, current), bisect_right(higher_closes, current)
                    prepared = (opening.get(current.date(), []), base[max(0, b-300):b], higher[max(0, h-300):h])
                    reading = technical([], current, frame, prepared=prepared)
                    if reading.get("signals"):
                        signals[i] = reading
                for setup in SETUPS:
                    for rr in (1, 1.5, 2):
                        # Replay each window with an empty ledger; no occupancy leaks from outside it.
                        for label, count in WINDOWS.items():
                            begin = (end - timedelta(days=count-1)).replace(hour=0, minute=0)
                            offset = bisect_left(stamps, begin)
                            selected = replay(rows[offset:], {i - offset: r for i, r in signals.items() if i >= offset}, setup, rr)
                            report["rows"].append({"symbol": symbol, "setup": setup, "minutes": frame, "target_r": rr,
                                "window": label, "coverage": "depth_available" if rows[0].timestamp <= begin else "partial",
                                **summarize(selected)})
        except InterruptedError:
            raise
        except Exception as exc:
            from trading_analysis.diagnostics import clean
            report["errors"][symbol] = clean(str(exc))
    return report
