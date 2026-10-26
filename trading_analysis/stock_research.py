"""Shared F&O swing rules and historical validation; never places broker orders."""
from bisect import bisect_right
from collections import Counter
from datetime import date, datetime, timedelta, time
from dataclasses import replace
import calendar
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
import uuid

from trading_analysis.analysis.technical import atr, ema, sma
from trading_analysis.analysis.relative_strength import load_sector_map, sector_config_for_symbol
from trading_analysis.backtesting.exits import exit_for
from trading_analysis.candles import candle_path
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.live_timing import IST, as_ist
from trading_analysis.nifty.live_scanner import candle_close_time
from trading_analysis.scheduler.market_hours import is_market_day

VERSION = "fno-swing-research-v1"
SETUPS = {"trend_pullback": "Trend Pullback", "compression_breakout": "Compression Breakout"}
VARIANTS = {"technical": "Technical control", "aligned": "Market + sector", "relative": "Market + sector + relative strength"}
WINDOWS = ("1 week", "2 weeks", "1 month", "2 months", "3 months", "6 months")
RULES = [
    "Historical validation is separate from live-data paper observation. No automatic approval or broker orders.",
    "Both directions; Daily and 1-hour completed candles. EMA20/50 trend and five-bar EMA50 slope.",
    "Pullback: prior three bars touch prior EMA20 within 0.25 ATR, hold prior EMA50, then close beyond prior high/low and EMA20.",
    "Compression: prior five-bar range <= 60% of prior twenty-bar range; close breaks prior twenty-bar high/low; volume >= 1.3x prior twenty-bar mean.",
    "Both: close within 1.5 ATR of EMA20; stop beyond prior five bars and signal bar by 0.1 ATR; risk 0.5-3 ATR.",
    "Aligned variants: completed Daily benchmark and sector close/EMA20 above rising SMA50 for longs; reverse for shorts. Neutral/missing blocks.",
    "Relative variant: stock twenty-session return exceeds both benchmark and sector for longs; reverse for shorts, with matched dates.",
    "Next-bar open, maximum 0.25R drift from signal close; fixed structural stop and 1/1.5/2R target from actual model entry. No chased entry.",
    "Hold up to ten Daily bars or thirty-five hourly bars including entry bar. Stop-first ambiguous bars; adverse stop gaps; target gaps filled at target.",
    "Four bps per side baseline friction; eight bps stress test. Spot research, not futures/option fills or taxes/fees specific to a contract.",
    "One position per symbol/configuration. Trade-series R drawdown is NOT portfolio drawdown; no capital/lot/margin allocation model.",
    "Historical F&O membership, corporate-action verification, event calendars, ban lists and contract liquidity are not reconstructed; approval is blocked.",
    "Screen: >=30 trades, >=50% net wins, positive expectancy, PF>1; later chronological 30% also needs >=10 trades and these conditions. No automatic promotion.",
]


def window_start(end, label):
    if "week" in label:
        return end - timedelta(days=7 * int(label.split()[0]) - 1)
    months = int(label.split()[0])
    n = end.year * 12 + end.month - 1 - months
    start = date(n // 12, n % 12 + 1, min(end.day, calendar.monthrange(n // 12, n % 12 + 1)[1]))
    return start + timedelta(days=1)


def session_on_or_before(day):
    while not is_market_day(day):
        day -= timedelta(days=1)
    return day


def entry_plan(signal, price, target_r):
    sign = 1 if signal["side"] == "long" else -1
    risk = (price-signal["stop"]) * sign
    if not math.isfinite(price) or risk <= 0 or abs(price-signal["reference"]) > .25*signal["risk"]:
        return None
    return {"entry_price": price, "stop_level": signal["stop"], "risk_points": risk,
            "target_level": price + sign*risk*target_r}


def technical(rows):
    if len(rows) < 60:
        return {}, {"reason": "insufficient_warmup"}
    rows = rows[-250:]
    closes = [r.close for r in rows]
    fast, slow, old = ema(closes, 20), ema(closes, 50), ema(closes[:-5], 50)
    a = atr(rows, 14)
    if not a or a <= 0:
        return {}, {"reason": "invalid_atr"}
    c, p = rows[-1], rows[-2]
    side = "long" if c.close > fast > slow > old else "short" if c.close < fast < slow < old else None
    info = {"ema20": fast, "ema50": slow, "atr14": a, "close": c.close, "side": side}
    if not side or abs(c.close - fast) > 1.5 * a:
        return {}, {**info, "reason": "trend_or_extension"}
    sign = 1 if side == "long" else -1
    stop = min(r.low for r in rows[-6:]) - .1*a if sign == 1 else max(r.high for r in rows[-6:]) + .1*a
    risk = (c.close - stop) * sign
    if not .5*a <= risk <= 3*a:
        return {}, {**info, "reason": "risk_distance"}
    prior_fast, prior_slow = ema(closes[:-1], 20), ema(closes[:-1], 50)
    prior3, prior5, prior20 = rows[-4:-1], rows[-6:-1], rows[-21:-1]
    pullback = (min(r.low for r in prior3) <= prior_fast + .25*a and min(r.close for r in prior3) > prior_slow
                and c.close > p.high and c.close > c.open) if sign == 1 else (
                max(r.high for r in prior3) >= prior_fast - .25*a and max(r.close for r in prior3) < prior_slow
                and c.close < p.low and c.close < c.open)
    high, low = max(r.high for r in prior20), min(r.low for r in prior20)
    width5 = max(r.high for r in prior5) - min(r.low for r in prior5)
    avg_volume = sum(r.volume for r in prior20) / 20
    compression = high > low and width5 <= .6 * (high-low) and avg_volume > 0 and c.volume >= 1.3 * avg_volume
    compression = compression and (c.close > high if sign == 1 else c.close < low)
    signals = {name: {"side": side, "reference": c.close, "stop": stop, "risk": risk, "atr": a}
               for name, passed in (("trend_pullback", pullback), ("compression_breakout", compression)) if passed}
    return signals, {**info, "reason": "candidate" if signals else "pattern_not_confirmed"}


def regime(rows):
    if len(rows) < 60:
        return None
    closes = [r.close for r in rows[-250:]]
    f, s, old = ema(closes, 20), sma(closes, 50), sma(closes[:-5], 50)
    return "long" if closes[-1] > s and f > s > old else "short" if closes[-1] < s and f < s < old else "neutral"


def context(stock, market, sector, decision, expected_day):
    """All inputs are complete daily bars as-of decision; no current-day lookahead."""
    series = []
    for rows in (stock, market, sector):
        end = bisect_right([candle_close_time(r.timestamp, "day") for r in rows], decision)
        selected = rows[max(0, end-250):end]
        if len(selected) < 60 or selected[-1].timestamp.date() != expected_day:
            return {"reason": "context_missing_or_stale", "aligned": None, "relative": None}
        series.append(selected)
    s, m, sec = series
    a, b = regime(m), regime(sec)
    aligned = a if a == b and a in {"long", "short"} else None
    dates = [r.timestamp.date() for r in m[-21:]]
    maps = [{r.timestamp.date(): r.close for r in rows} for rows in series]
    relative = None
    if all(all(d in mapping for d in dates) for mapping in maps):
        returns = [mapping[dates[-1]] / mapping[dates[0]] - 1 for mapping in maps]
        relative = "long" if returns[0] > max(returns[1:]) else "short" if returns[0] < min(returns[1:]) else None
    return {"reason": "available", "aligned": aligned, "relative": relative,
            "market": a, "sector": b, "daily_bar": str(expected_day)}


def summary(trades):
    valid = sorted((t for t in trades if t.get("net_r") is not None), key=lambda t: (t["exit_time"], t["symbol"]))
    vals = [t["net_r"] for t in valid]
    gains, losses = sum(max(v, 0) for v in vals), -sum(min(v, 0) for v in vals)
    total = peak = dd = 0
    for v in vals:
        total += v
        peak = max(peak, total)
        dd = max(dd, peak-total)
    return {"trades": len(vals), "win_rate": 100*sum(v > 0 for v in vals)/len(vals) if vals else None,
            "expectancy_r": total/len(vals) if vals else None, "profit_factor": gains/losses if losses else None,
            "no_losses": bool(vals) and losses == 0, "net_r": total, "drawdown_r": dd,
            "stress_expectancy_r": sum(t["stress_r"] for t in valid)/len(vals) if vals else None,
            "excluded": len(trades)-len(valid)}


def screen(metrics, later, coverage):
    if not coverage:
        return "incomplete_data"
    if metrics["trades"] < 30 or later["trades"] < 10:
        return "insufficient_sample"
    def good(m):
        return m["win_rate"] >= 50 and m["expectancy_r"] > 0 and (m["no_losses"] or (m["profit_factor"] or 0) > 1)
    return "screen_pass_not_approved" if good(metrics) and good(later) and metrics["stress_expectancy_r"] > 0 else "screen_failed"


def replay(symbol, rows, signals, setup, side, variant, rr, start, end, frame, contexts):
    trades, available = [], 0
    hold = 10 if frame == "day" else 35
    for i, signal_map in signals.items():
        sig = signal_map.get(setup)
        if i < available or not sig or sig["side"] != side or not start <= rows[i].timestamp.date() <= end or i+1 >= len(rows):
            continue
        c = rows[i+1]
        if c.timestamp.date() > end:
            continue
        if variant != "technical":
            checks = [contexts.get((i, "signal"), {}), contexts.get((i, "entry"), {})]
            if any(r.get("aligned") != side or (variant == "relative" and r.get("relative") != side) for r in checks):
                continue
        sign = 1 if side == "long" else -1
        plan = entry_plan(sig, c.open, rr)
        if not plan:
            continue
        risk, target = plan["risk_points"], plan["target_level"]
        # Reuse the application's stop/target engine; enable adverse gap handling only here.
        result = exit_for(rows, i+1, side, sig["stop"], target, hold-1, adverse_gaps=True)
        j, price = result["index"], result["price"]
        truncated = result["reason"] == "holding_period" and j < i+hold
        cost = (c.open+price)*4/10000
        gross = (price-c.open)*sign/risk
        entry_at = as_ist(c.timestamp)
        if frame == "day":
            entry_at = entry_at.replace(hour=9, minute=15)
        trade = {"symbol": symbol, "setup": setup, "side": side, "direction": "bullish" if sign == 1 else "bearish",
                 "variant": variant, "frame": frame, "target_r": rr, "signal_time": candle_close_time(rows[i].timestamp, frame).isoformat(),
                 "entry_time": entry_at.isoformat(), "entry_price": c.open, "stop_level": sig["stop"], "target_level": target,
                 "exit_time": candle_close_time(rows[j].timestamp, frame).isoformat(), "exit_price": price,
                 "status": "excluded" if truncated else "closed", "exit_reason": "incomplete_exit_window" if truncated else result["reason"],
                 "gross_r": gross, "net_r": None if truncated else gross-cost/risk, "stress_r": None if truncated else gross-2*cost/risk,
                 "ambiguous": result["intrabar_ambiguous"], "price_basis": "spot_OHLC_model_not_fill",
                 "time_basis": "entry_open; intrabar_exit_time_unknown_reported_at_bar_close"}
        trades.append(trade)
        available = j+1
    return trades


class StockResearch:
    def __init__(self, analysis, path="data/db/stock_research.db"):
        self.analysis, self.path = analysis, Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock, self.cancelled = threading.RLock(), threading.Event()
        self.state = {"phase": "idle", "progress": "", "completed": 0, "total": 0}
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS stock_research_runs(id TEXT PRIMARY KEY, created_at TEXT, body TEXT)")

    def status(self):
        with self.lock:
            state = dict(self.state)
        with sqlite3.connect(self.path) as db:
            record = db.execute("SELECT body FROM stock_research_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        report = json.loads(record[0]) if record else None
        if report:
            report = {k: v for k, v in report.items() if k != "trades"}
            if state["phase"] == "idle":
                state.update(phase="completed", progress="Last completed report loaded")
        rows = (report or {}).get("rows", [])
        reasons = []
        if not report:
            reasons.append("No historical validation report")
        elif report.get("as_of") and not is_market_day(date.fromisoformat(report["as_of"])):
            reasons.append("Previous report ends on a non-trading date; rerun validation")
        if rows and any(r.get("screen") == "incomplete_data" for r in rows):
            reasons.append("Incomplete historical candle or market/sector coverage")
        if report and not any(r.get("screen") == "screen_pass_not_approved" for r in rows):
            reasons.append("No comparison passes the historical sample, net-return and stress-cost screens")
        reasons.append("Historical membership, corporate actions and derivative execution still require review")
        return {**state, "version": VERSION, "setups": SETUPS, "variants": VARIANTS, "rules": RULES,
                "live_enabled": False, "paper_available": True, "approval": "blocked_pending_review",
                "approval_reasons": reasons, "report": report}

    def export(self, from_date=None, to_date=None):
        from trading_analysis.audit_exports import date_window, trade_overlaps
        with sqlite3.connect(self.path) as db:
            record = db.execute("SELECT body FROM stock_research_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        report = json.loads(record[0]) if record else {"trades": [], "rows": [], "rules": RULES}
        end = date.fromisoformat(report.get("as_of") or datetime.now(IST).date().isoformat())
        report["from_date"], report["to_date"] = str(window_start(end,"6 months")), str(end)
        report["csv_columns"] = ["frame","window","target_r","signal_time","stress_r","coverage_complete","time_basis"]
        if from_date or to_date:
            first, last = date_window(from_date=from_date, to_date=to_date)
            report["trades"] = [t for t in report["trades"] if trade_overlaps(t, first, last)]
            report["export_filter"] = {"from_date": first, "to_date": last, "basis": "trade overlaps; matrix metrics remain original windows"}
            report["from_date"], report["to_date"] = first, last
        return report

    def start(self, as_of=None, benchmark="NIFTY 50", download=False):
        if benchmark not in {"NIFTY 50", "NIFTY 500"} or not isinstance(download, bool):
            raise ValueError("Invalid benchmark or download flag")
        end = date.fromisoformat(as_of) if as_of else datetime.now(IST).date()-timedelta(days=1)
        if end >= datetime.now(IST).date():
            raise ValueError("Choose a completed historical session before today")
        end = session_on_or_before(end)
        with self.lock:
            if self.state["phase"] == "running":
                raise ValueError("Stock validation is already running")
            self.cancelled.clear()
            self.state = {"phase": "running", "progress": "Loading universe", "completed": 0, "total": 0,
                          "started_at": datetime.now(IST).isoformat(), "ended_at": None}
            threading.Thread(target=self._worker, args=(end, benchmark, download), daemon=True, name="stock-validation").start()
        return self.status()

    def cancel(self):
        self.cancelled.set()
        return {"status": "cancellation_requested"}

    def _progress(self, message, completed=0, total=0):
        if self.cancelled.is_set():
            raise InterruptedError("Cancelled; previous completed report retained")
        with self.lock:
            self.state.update(progress=message, completed=completed, total=total)

    def _worker(self, end, benchmark, download):
        try:
            report = self.run(end, benchmark, download)
            with self.lock:
                if self.cancelled.is_set():
                    raise InterruptedError("Cancelled; previous completed report retained")
                with sqlite3.connect(self.path) as db:
                    db.execute("INSERT INTO stock_research_runs VALUES(?,?,?)", (report["id"], report["created_at"], json.dumps(report, allow_nan=False)))
                self.state.update(phase="completed", completed=self.state["total"],
                                  progress="Historical backtest complete; paper scanner is separate from strategy approval")
        except Exception as exc:
            from trading_analysis.diagnostics import clean
            with self.lock:
                self.state.update(phase="cancelled" if isinstance(exc, InterruptedError) else "failed", progress=clean(str(exc)))
        finally:
            with self.lock:
                self.state["ended_at"] = datetime.now(IST).isoformat()

    def run(self, end, benchmark="NIFTY 50", download=False):
        from trading_analysis.data_sources.fno_universe import current_watchlist_symbols
        end = session_on_or_before(end)
        symbols = sorted(current_watchlist_symbols(self.analysis, datetime.now(IST)))
        if not symbols:
            raise ValueError("No current F&O stock universe. Refresh NSE/NFO instrument masters in Data Ops.")
        mapping = load_sector_map(self.analysis.sector_map_path)
        sectors = {s: sector_config_for_symbol(mapping, s) for s in symbols}
        start = window_start(end, "6 months")
        report = {"id": uuid.uuid4().hex, "version": VERSION, "created_at": datetime.now(IST).isoformat(), "as_of": str(end),
                  "benchmark": benchmark, "universe": symbols, "universe_basis": "current master/watchlist, NOT historical membership",
                  "approval": "blocked_pending_review", "rules": RULES, "rows": [], "trades": [], "coverage": [], "candidates": [], "errors": []}
        series = {}
        def load(stem, frame):
            key = (stem, frame)
            if key in series:
                return series[key]
            path = candle_path(self.analysis.daily_data_dir, frame, stem)
            rows = [replace(r, timestamp=as_ist(r.timestamp)) for r in load_candles(path)] if path.exists() else []
            rows = sorted((r for r in rows if candle_close_time(r.timestamp, frame) <= datetime.combine(end, time(15, 30), IST)), key=lambda r: r.timestamp)
            if any(not all(math.isfinite(v) and v > 0 for v in (r.open, r.high, r.low, r.close)) or not math.isfinite(r.volume) or r.volume < 0 or r.low > min(r.open,r.close) or r.high < max(r.open,r.close) or r.low > r.high for r in rows):
                raise ValueError(f"Invalid OHLC: {stem} {frame}")
            if len({r.timestamp for r in rows}) != len(rows):
                raise ValueError(f"Duplicate candles: {stem} {frame}")
            series[key] = rows
            report["coverage"].append({"symbol": stem, "frame": frame, "bars": len(rows), "first": rows[0].timestamp.isoformat() if rows else None,
                "last": rows[-1].timestamp.isoformat() if rows else None, "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None})
            return rows
        if download:
            from trading_analysis.web_services import _zerodha_client
            from trading_analysis.brokers.zerodha import resolve_instrument_token, merge_candles_csv
            client = _zerodha_client()
            instruments = self.analysis._instruments_for_exchange("NSE")
            sources = {(s, s, f) for s in symbols for f in ("day", "60minute")}
            sources.add((benchmark.replace(" ", "_"), benchmark, "day"))
            for sec in sectors.values():
                if sec:
                    sources.add((Path(sec["data_file"]).stem, sec["index_symbol"], "day"))
            for n, (stem, name, frame) in enumerate(sorted(sources)):
                self._progress(f"Downloading {name} {frame}", n, len(sources))
                try:
                    token = resolve_instrument_token(instruments, "NSE", name)
                    rows = client.historical_candles(token, frame, datetime.combine(start-timedelta(days=200), time(), IST), datetime.combine(end,time(15,30),IST))
                    merge_candles_csv(candle_path(self.analysis.daily_data_dir,frame,stem), rows)
                except Exception as exc:
                    from trading_analysis.diagnostics import clean
                    report["errors"].append({"symbol": name, "frame": frame, "error": clean(str(exc))})
        market = load(benchmark.replace(" ", "_"), "day")
        market_days = sorted({r.timestamp.date() for r in market})
        grouped, later_grouped = {}, {}
        coverage = Counter()
        for n, symbol in enumerate(symbols):
            self._progress(f"Validating {symbol}", n, len(symbols))
            try:
                daily = load(symbol, "day")
                sec = sectors[symbol]
                sector = load(Path(sec["data_file"]).stem, "day") if sec else []
                day_context = {}
                for frame in ("day", "60minute"):
                    rows = load(symbol, frame)
                    signals, contexts = {}, {}
                    for i in range(59, len(rows)):
                        if rows[i].timestamp.date() < start:
                            continue
                        if i % 100 == 0:
                            self._progress(f"Validating {symbol} {frame}", n, len(symbols))
                        signal, values = technical(rows[max(0,i-249):i+1])
                        if i == len(rows)-1:
                            report["candidates"].append({"symbol":symbol, "frame":frame, "setups":list(signal), "as_of":candle_close_time(rows[i].timestamp,frame).isoformat(), **values})
                        if not signal:
                            continue
                        signals[i] = signal
                        for kind, at in (("signal",candle_close_time(rows[i].timestamp,frame)), ("entry",as_ist(rows[i+1].timestamp) if i+1<len(rows) else candle_close_time(rows[i].timestamp,frame))):
                            if kind == "entry" and frame == "day":
                                at = at.replace(hour=9, minute=15)
                            eligible = [d for d in market_days if datetime.combine(d,time(15,30),IST) <= at]
                            expected = eligible[-1] if eligible else None
                            # Missing sessions cannot silently forward-fill a regime indefinitely.
                            if expected and (at.date()-expected).days <= 4:
                                key = expected
                                if key not in day_context:
                                    day_context[key] = context(daily, market, sector, datetime.combine(expected,time(15,30),IST), expected)
                                contexts[(i,kind)] = day_context[key]
                    for label in WINDOWS:
                        begin = window_start(end,label)
                        expected = {d for d in market_days if begin <= d <= end}
                        stock_counts = Counter(r.timestamp.date() for r in rows)
                        complete = bool(expected) and market_days[-1] == end and len([r for r in rows if r.timestamp.date() < begin]) >= 60
                        complete = complete and all(stock_counts[d] == (1 if frame=="day" else 7) for d in expected)
                        # Market/sector context needs warmup and coverage too, even for technical-control comparisons.
                        complete = complete and len([r for r in market if r.timestamp.date()<begin]) >= 60 and len([r for r in sector if r.timestamp.date()<begin]) >= 60
                        complete = complete and expected.issubset({r.timestamp.date() for r in sector})
                        if complete:
                            coverage[(frame,label)] += 1
                        end_rows = [r for r in rows if r.timestamp.date() <= end]
                        for setup in SETUPS:
                            for side in ("long","short"):
                                for variant in VARIANTS:
                                    for rr in (1,1.5,2):
                                        key=(setup,side,frame,variant,rr,label)
                                        trades = replay(symbol,end_rows,signals,setup,side,variant,rr,begin,end,frame,contexts)
                                        for t in trades:
                                            t.update(window=label, coverage_complete=bool(complete), sector=sec["index_symbol"] if sec else "unmapped")
                                            identity = "|".join(map(str,(VERSION,symbol,setup,side,frame,variant,rr,label,t["entry_time"])))
                                            t["trade_id"] = "SR-"+hashlib.sha256(identity.encode()).hexdigest()[:20]
                                        grouped.setdefault(key,[]).extend(trades)
                                        split = begin+timedelta(days=int(((end-begin).days+1)*.7))
                                        later_grouped.setdefault(key,[]).extend(replay(symbol,end_rows,signals,setup,side,variant,rr,split,end,frame,contexts))
            except Exception as exc:
                report["errors"].append({"symbol":symbol,"error":str(exc)})
        for key, trades in grouped.items():
            setup,side,frame,variant,rr,label = key
            begin = window_start(end,label)
            split = begin+timedelta(days=int(((end-begin).days+1)*.7))
            # Later-period test starts flat and excludes trades spanning its boundary.
            later = summary(later_grouped[key])
            metrics = summary(trades)
            count = coverage[(frame,label)]
            report["rows"].append({"setup":setup,"side":side,"frame":frame,"variant":variant,"target_r":rr,"window":label,
                "from_date":str(begin),"to_date":str(end),"covered_symbols":count,"total_symbols":len(symbols),
                **metrics,"later":later,"screen":screen(metrics,later,count==len(symbols))})
            report["trades"].extend(trades)
        report["limitations"] = ["Current membership/sector mappings introduce survivorship bias.",
            "Six windows overlap; later 30% is a development diagnostic, not an untouched holdout. No automatic approval.",
            "Coverage is benchmark-relative, not exchange-calendar certified. Source corporate actions and historical contract tradability unverified.",
            "Intrabar exit timing is unknown. R drawdown sums trades, not a capital-constrained portfolio.",
            "Each trade can appear in several comparison windows. Do not sum across configurations or windows."]
        return report

