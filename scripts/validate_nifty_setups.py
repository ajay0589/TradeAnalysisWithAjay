"""Offline, reproducible technical-rule screening. Never promotes a live strategy.

python -m scripts.validate_nifty_setups --as-of YYYY-MM-DD
"""
import argparse
from bisect import bisect_right
from calendar import monthrange
import csv
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path

from trading_analysis.candles import candle_path
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.live_timing import IST, as_ist
from trading_analysis.nifty.comparison_backtest import metrics
from trading_analysis.nifty.live_scanner import candle_close_time, closed_candles
from trading_analysis.nifty.technical_backtest import Settings, simulate
from trading_analysis.nifty_lab.setups import SETUPS, VERSION, technical_read


def windows(end):
    result = [("1_week", end - timedelta(days=6)), ("2_weeks", end - timedelta(days=13))]
    for count in (1, 2, 3, 6):
        month = end.year * 12 + end.month - 1 - count
        year, month = divmod(month, 12)
        first = date(year, month + 1, min(end.day, monthrange(year, month + 1)[1])) + timedelta(days=1)
        result.append((f"{count}_month", first))
    return result


def coverage(rows, first, end):
    warmup = sum(as_ist(c.timestamp).date() < first for c in rows)
    selected = [c for c in rows if first <= as_ist(c.timestamp).date() <= end]
    return {"first_available": as_ist(rows[0].timestamp).isoformat() if rows else None,
            "last_available": as_ist(rows[-1].timestamp).isoformat() if rows else None,
            "bars": len(selected), "warmup_bars": warmup,
            "covered": bool(selected) and warmup >= 60 and (end - as_ist(selected[-1].timestamp).date()).days <= 3}


def run(data_dir, end):
    end_at = datetime.combine(end, time(23, 59, 59), IST)
    sources = {}
    for frame in ("5minute", "15minute", "60minute", "day"):
        path = candle_path(Path(data_dir), frame, "NIFTY_50")
        sources[frame] = closed_candles(load_candles(path), frame, end_at) if path.exists() else []
    configurations = []
    for frame in ("5minute", "15minute", "60minute"):
        horizon = "swing" if frame == "60minute" else "intraday"
        for strategy in ("scanner_rules", "ema_pullback", "trend_breakout", "rsi_reversal", "opening_range"):
            if strategy == "opening_range" and horizon != "intraday":
                continue
            configurations.append((strategy, frame, horizon, None))
    # Reuse the live Lab technical evaluator with only candles known at each close.
    # Limit warm-up to 600 bars; no option snapshots or fabricated prices are used.
    for setup, definition in SETUPS.items():
        frame = definition["frame"]
        cached = {}
        close_times = {f: [as_ist(candle_close_time(c.timestamp, f)) for c in sources[f]] for f in definition["sources"]}
        earliest = windows(end)[-1][1]
        for i, bar in enumerate(sources[frame]):
            at = as_ist(candle_close_time(bar.timestamp, frame))
            if at.date() < earliest:
                continue
            known = {}
            for f in definition["sources"]:
                right = bisect_right(close_times[f], at)
                known[f] = sources[f][max(0, right - 600):right]
            result = technical_read(setup, known, at)
            cached[i] = result.get("direction") if result.get("ready") else None
        configurations.append((setup, frame, "intraday", cached))
    results = []
    for strategy, frame, horizon, signals in configurations:
        rows = sources[frame]
        for label, first in windows(end):
            split = first + (end - first) * .7
            available = coverage(rows, first, end)
            for rr in (1.0, 1.5, 2.0):
                settings = Settings(horizon, strategy, "both", rr,
                                    12 if horizon == "swing" else max(1, 90 // int(frame.replace("minute", ""))),
                                    trigger_minutes=int(frame.replace("minute", "")),
                                    session_exit=time(15, 15) if signals is not None else time(15, 0))
                def eligible(index, direction, at):
                    return first <= as_ist(at).date() <= end
                trades = simulate(rows, settings, entry_filter=eligible,
                                  signal_provider=signals.get if signals is not None else None)
                complete = [t for t in trades if as_ist(t["exit_time"]) < as_ist(candle_close_time(rows[-1].timestamp, frame)) or t["exit_reason"] != "max_bars"] if rows else []
                overall = metrics(complete)
                held_out = [t for t in complete if as_ist(t["entry_time"]).date() >= split]
                holdout = metrics(held_out)
                passed = (available["covered"] and len(complete) >= 30 and len(held_out) >= 10
                          and (overall["win_rate"] or 0) >= 50 and (holdout["win_rate"] or 0) >= 50
                          and (overall["average_r"] or 0) > 0 and (holdout["average_r"] or 0) > 0
                          and (overall["profit_factor"] or 0) > 1 and (holdout["profit_factor"] or 0) > 1)
                results.append({"strategy": strategy, "frame": frame, "window": label, "from_date": first,
                                "to_date": end, "target_r": rr, "trade_count": len(complete), **overall,
                                "holdout_from": split, "holdout_trades": len(held_out),
                                "holdout_win_rate": holdout["win_rate"], "holdout_average_r": holdout["average_r"],
                                "coverage": available, "screen_pass": bool(passed), "trades": complete})
    return {"as_of": end, "lab_version": VERSION, "promoted": False, "results": results,
            "assumptions": ["NIFTY spot, not futures or option P&L. No orders or alerts.",
                            "Windows overlap. Later 30% is a diagnostic holdout, not untouched after comparing many configurations.",
                            "Next-bar open, 1 ATR stop, stop-first ambiguity, 2 bps/side cost stress. No hypothetical fills at signal close.",
                            "Lab technical predicates use up to 600 prior bars. Historical execution does not replay live quote/price-drift gates or option confirmation.",
                            "Lab replay exits on the 15:15 bar close (15:20 for 5m, 15:30 for 15m); live exits at 15:20. Intrabar exit times are bounds.",
                            "Native Lab timeframes plus research EMA, RSI, breakout and opening-range combinations at 5m/15m/60m.",
                            "Coverage checks endpoints/warm-up, not exchange holidays or every missing session. Audit source data before promotion.",
                            "Screen requires >=30 closed trades, >=10 later-period trades, >=50% net win rate and positive expectancy/profit factor >1 in both samples.",
                            "No automatic approval. Require independent forward validation, drawdown limits and stability across windows before live use."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    parser.add_argument("--data-dir", default="data/raw/candles")
    parser.add_argument("--output", default="reports/nifty-validation")
    args = parser.parse_args()
    report = run(args.data_dir, args.as_of)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "validation.json").write_text(json.dumps(report, default=str, indent=2), encoding="utf-8")
    rows = [{**{k: v for k, v in row.items() if k not in {"trades", "coverage"}}, **row["coverage"]} for row in report["results"]]
    with (output / "validation.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"combinations": len(rows), "screen_pass": sum(r["screen_pass"] for r in rows), "output": str(output)}))


if __name__ == "__main__":
    main()
