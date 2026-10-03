"""Compare NIFTY spot-signal backtest settings without changing live rules."""

from __future__ import annotations

from collections import Counter
from datetime import date
from itertools import product
from statistics import mean

from trading_analysis.candles import candle_path
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.nifty.live_scanner import HORIZON_CONFIG, backtest_nifty_live_rules


TARGETS = (0.75, 1.0, 1.5, 2.0)
HOLDING_BARS = {
    "intraday": (4, 8, 12),
    "swing": (6, 12, 18),
    "positional": (5, 10, 15),
}
PERIODS = {
    "earlier": (date(2025, 1, 1), date(2025, 12, 31)),
    "development": (date(2026, 4, 1), date(2026, 7, 31)),
    "holdout": (date(2026, 8, 1), date(2026, 9, 23)),
}


def metrics(trades: list[dict]) -> dict:
    values = [float(trade["r_multiple"]) for trade in trades]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    gross_loss = abs(sum(losses))
    return {
        "n": len(values),
        "win": round(100 * len(wins) / len(values), 1) if values else None,
        "avg_r": round(mean(values), 3) if values else None,
        "net_r": round(sum(values), 2),
        "pf": round(sum(wins) / gross_loss, 2) if gross_loss else None,
    }


def print_result(row: dict) -> None:
    print(
        f"  {row['direction']:7} target={row['target']:4} bars={row['bars']:2} "
        f"dev={row['development']} holdout={row['holdout']} earlier={row['earlier']}"
    )


def main() -> None:
    for horizon, config in HORIZON_CONFIG.items():
        path = candle_path("data/raw/candles", config["timeframe"], "NIFTY_50")
        candles = load_candles(path)
        print(f"\n{horizon.upper()} source={path} rows={len(candles)} latest={candles[-1].timestamp}", flush=True)
        rows = []
        baseline = None
        for direction, target, bars in product(("both", "bullish", "bearish"), TARGETS, HOLDING_BARS[horizon]):
            result = backtest_nifty_live_rules(
                candles,
                horizon,
                from_date="2024-11-01",
                to_date="2026-09-23",
                direction=direction,
                target_r_multiple=target,
                max_holding_bars=bars,
            )
            trades = result["trades"]
            row = {"direction": direction, "target": target, "bars": bars}
            for name, (start, end) in PERIODS.items():
                period_trades = [trade for trade in trades if start <= trade["entry_time"].date() <= end]
                row[name] = metrics(period_trades)
                if name == "holdout":
                    row["holdout_reasons"] = dict(Counter(trade["exit_reason"] for trade in period_trades))
            rows.append(row)
            if direction == "both" and target == 2.0 and bars == config["max_bars"]:
                baseline = row
        if baseline:
            print("BASELINE", flush=True)
            print_result(baseline)
            print(f"  holdout exit reasons: {baseline['holdout_reasons']}", flush=True)
        minimum = {"intraday": 30, "swing": 8, "positional": 4}[horizon]
        eligible = [row for row in rows if row["development"]["n"] >= minimum]
        ranked = sorted(eligible, key=lambda row: (row["development"]["avg_r"], row["development"]["n"]), reverse=True)
        print(f"TOP DEVELOPMENT (min {minimum} trades)", flush=True)
        for row in ranked[:8]:
            print_result(row)
        valid = [row for row in ranked if row["holdout"]["n"] >= {"intraday": 15, "swing": 5, "positional": 3}[horizon]]
        print("BEST HOLDOUT AMONG ELIGIBLE (diagnostic only)", flush=True)
        for row in sorted(valid, key=lambda row: (row["holdout"]["avg_r"], row["holdout"]["n"]), reverse=True)[:5]:
            print_result(row)


if __name__ == "__main__":
    main()
