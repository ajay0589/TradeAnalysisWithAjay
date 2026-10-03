"""Research-only NIFTY spot backtest with next-bar execution and fixed periods.

Run with: python -m scripts.research_nifty_technicals
These signals are not the live scanner's rules and do not model option premiums.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import product
from pathlib import Path
from statistics import mean

from trading_analysis.candles import candle_path
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.models import Candle
from trading_analysis.nifty.live_scanner import _atr_series, _ema_series, _rsi_series


PERIODS = {
    "development": (date(2023, 1, 1), date(2024, 12, 31)),
    "validation": (date(2025, 1, 1), date(2025, 12, 31)),
    "holdout": (date(2026, 1, 1), date(2026, 9, 23)),
}
COST_BPS_PER_SIDE = 2.0  # Spot-equivalent stress assumption, not an option fill model.


@dataclass(frozen=True)
class Settings:
    horizon: str
    strategy: str
    direction: str
    target_r: float
    max_bars: int
    atr_stop: float = 1.0
    cost_bps_per_side: float = COST_BPS_PER_SIDE


def _clock(candle: Candle) -> time:
    return candle.timestamp.timetz().replace(tzinfo=None)


def _trade_time(candle: Candle, horizon: str, *, at_close: bool = False) -> datetime:
    if horizon == "positional":
        return candle.timestamp.replace(hour=15 if at_close else 9, minute=30 if at_close else 15)
    minutes = 15 if horizon == "intraday" else 60
    return candle.timestamp + (timedelta(minutes=minutes) if at_close else timedelta())


def _opening_range(candles: list[Candle]) -> dict[date, tuple[float, float]]:
    output: dict[date, tuple[float, float]] = {}
    for candle in candles:
        if time(9, 15) <= _clock(candle) < time(10, 15):
            key = candle.timestamp.date()
            high, low = output.get(key, (float("-inf"), float("inf")))
            output[key] = max(high, candle.high), min(low, candle.low)
    return output


def _signal(
    candles: list[Candle], index: int, settings: Settings,
    fast: list[float | None], slow: list[float | None], rsi: list[float | None],
    opening_range: dict[date, tuple[float, float]],
) -> str | None:
    candle = candles[index]
    previous = candles[index - 1]
    ema20, ema50, momentum = fast[index], slow[index], rsi[index]
    if ema20 is None or ema50 is None or momentum is None:
        return None
    if settings.horizon == "intraday":
        # The 15:15 index print is often a single-price candle; leave time to exit.
        if not time(9, 30) <= _clock(candle) <= time(14, 45):
            return None
    bullish = bearish = False
    if settings.strategy == "trend_breakout":
        previous_high = max(row.high for row in candles[index - 10:index])
        previous_low = min(row.low for row in candles[index - 10:index])
        bullish = candle.close > previous_high and candle.close > ema20 > ema50 and 55 <= momentum <= 75
        bearish = candle.close < previous_low and candle.close < ema20 < ema50 and 25 <= momentum <= 45
    elif settings.strategy == "ema_pullback":
        previous_ema = fast[index - 1]
        if previous_ema is None:
            return None
        bullish = previous.close <= previous_ema and candle.close > ema20 > ema50 and candle.close > candle.open and 52 <= momentum <= 70
        bearish = previous.close >= previous_ema and candle.close < ema20 < ema50 and candle.close < candle.open and 30 <= momentum <= 48
    elif settings.strategy == "rsi_reversal":
        previous_rsi = rsi[index - 1]
        if previous_rsi is None:
            return None
        bullish = previous_rsi < 35 <= momentum and candle.close > candle.open and candle.close < ema20
        bearish = previous_rsi > 65 >= momentum and candle.close < candle.open and candle.close > ema20
    elif settings.strategy == "opening_range" and settings.horizon == "intraday":
        if not time(10, 15) <= _clock(candle) <= time(13, 45):
            return None
        bounds = opening_range.get(candle.timestamp.date())
        if bounds is None:
            return None
        upper, lower = bounds
        bullish = candle.close > upper and previous.close <= upper and candle.close > ema20 > ema50 and momentum >= 55
        bearish = candle.close < lower and previous.close >= lower and candle.close < ema20 < ema50 and momentum <= 45
    if bullish and settings.direction in {"both", "bullish"}:
        return "bullish"
    if bearish and settings.direction in {"both", "bearish"}:
        return "bearish"
    return None


def _exit(
    candles: list[Candle], start: int, direction: str, entry: float,
    stop: float, target: float, settings: Settings,
) -> tuple[int, float, str]:
    end = min(len(candles) - 1, start + settings.max_bars - 1)
    for index in range(start, end + 1):
        candle = candles[index]
        if settings.horizon == "intraday" and candle.timestamp.date() != candles[start].timestamp.date():
            previous = candles[index - 1]
            return index - 1, previous.close, "session_close"
        if direction == "bullish":
            if candle.open <= stop:
                return index, candle.open, "gap_stop"
            if candle.low <= stop:
                return index, stop, "stop"
            if candle.high >= target:
                return index, target, "target"
        else:
            if candle.open >= stop:
                return index, candle.open, "gap_stop"
            if candle.high >= stop:
                return index, stop, "stop"
            if candle.low <= target:
                return index, target, "target"
        if settings.horizon == "intraday" and _clock(candle) >= time(15, 0):
            return index, candle.close, "session_close"
    return end, candles[end].close, "max_bars"


def simulate(candles: list[Candle], settings: Settings) -> list[dict]:
    closes = [row.close for row in candles]
    fast, slow, momentum, volatility = (
        _ema_series(closes, 20), _ema_series(closes, 50),
        _rsi_series(closes, 14), _atr_series(candles, 14),
    )
    opening_range = _opening_range(candles) if settings.strategy == "opening_range" else {}
    trades: list[dict] = []
    index = 60
    while index < len(candles) - 1:
        signal = _signal(candles, index, settings, fast, slow, momentum, opening_range)
        if signal is None or volatility[index] is None:
            index += 1
            continue
        entry_index = index + 1
        entry_candle = candles[entry_index]
        if settings.horizon == "intraday" and entry_candle.timestamp.date() != candles[index].timestamp.date():
            index += 1
            continue
        if settings.horizon in {"intraday", "swing"} and _clock(entry_candle) >= time(15, 15):
            index += 1
            continue
        entry = entry_candle.open
        risk = settings.atr_stop * volatility[index]
        stop = entry - risk if signal == "bullish" else entry + risk
        target = entry + settings.target_r * risk if signal == "bullish" else entry - settings.target_r * risk
        exit_index, exit_price, exit_reason = _exit(candles, entry_index, signal, entry, stop, target, settings)
        gross_points = (exit_price - entry) * (1 if signal == "bullish" else -1)
        cost_points = (entry + exit_price) * settings.cost_bps_per_side / 10000
        entry_time = _trade_time(entry_candle, settings.horizon)
        exit_time = _trade_time(candles[exit_index], settings.horizon, at_close=True)
        trades.append({
            "entry_date": entry_time.date(), "entry_time": entry_time,
            "exit_time": exit_time, "direction": signal,
            "entry": entry, "exit": exit_price, "risk_points": risk,
            "stop": stop, "target": target,
            "exit_reason": exit_reason,
            "gross_r": gross_points / risk, "net_r": (gross_points - cost_points) / risk,
        })
        index = exit_index + 1
    return trades


def backtest_technical_setup(
    candles: list[Candle],
    horizon: str,
    *,
    strategy: str = "ema_pullback",
    direction: str = "both",
    from_date: str | None = None,
    to_date: str | None = None,
    target_r_multiple: float = 1.0,
    max_holding_bars: int = 10,
    cost_bps_per_side: float = COST_BPS_PER_SIDE,
) -> dict:
    if horizon not in {"intraday", "swing", "positional"}:
        raise ValueError("Unsupported NIFTY horizon.")
    if strategy not in {"ema_pullback", "trend_breakout", "rsi_reversal", "opening_range"}:
        raise ValueError("Unsupported technical setup.")
    if strategy == "opening_range" and horizon != "intraday":
        raise ValueError("Opening range requires the 15-minute intraday horizon.")
    if direction not in {"both", "bullish", "bearish"}:
        raise ValueError("Direction must be both, bullish, or bearish.")
    if not 0 < target_r_multiple <= 10 or not 1 <= max_holding_bars <= 100:
        raise ValueError("Target R or maximum bars are outside the supported range.")
    if not 0 <= cost_bps_per_side <= 100:
        raise ValueError("Cost per side must be between 0 and 100 basis points.")
    if from_date and to_date and from_date > to_date:
        raise ValueError("From date must be on or before To date.")
    candles = sorted(candles, key=lambda row: row.timestamp)
    if horizon != "positional":
        candles = [row for row in candles if _clock(row) < time(15, 15)]
    if not candles:
        raise ValueError("No NIFTY spot candles are available for this timeframe.")
    settings = Settings(horizon, strategy, direction, target_r_multiple, max_holding_bars, cost_bps_per_side=cost_bps_per_side)
    rows = simulate(candles, settings)
    first = date.fromisoformat(from_date) if from_date else date.min
    last = date.fromisoformat(to_date) if to_date else date.max
    rows = [row for row in rows if first <= row["entry_date"] <= last]
    values = [row["net_r"] for row in rows]
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value <= 0)
    equity = peak = max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    trades = []
    for row in rows:
        duration = max(0, int((row["exit_time"] - row["entry_time"]).total_seconds()))
        risk = row["risk_points"]
        direction_sign = 1 if row["direction"] == "bullish" else -1
        gross_points = (row["exit"] - row["entry"]) * direction_sign
        cost_points = gross_points - row["net_r"] * risk
        trades.append({
            "horizon": horizon,
            "strategy": strategy,
            "direction": row["direction"],
            "entry_time": row["entry_time"],
            "entry_price": row["entry"],
            "exit_time": row["exit_time"],
            "exit_price": row["exit"],
            "open_seconds": duration,
            "open_duration": _duration(duration),
            "stop_level": row["stop"],
            "target_level": row["target"],
            "risk_points": risk,
            "r_multiple": row["net_r"],
            "return_percent": (gross_points - cost_points) / row["entry"] * 100,
            "exit_reason": row["exit_reason"],
        })
    return {
        "method": "technical",
        "strategy": strategy,
        "horizon": horizon,
        "timeframe": {"intraday": "15minute", "swing": "60minute", "positional": "day"}[horizon],
        "candle_count": len(candles),
        "data_through": candles[-1].timestamp,
        "trade_count": len(trades),
        "trades": trades,
        "metrics": {
            "wins": sum(value > 0 for value in values),
            "losses": sum(value <= 0 for value in values),
            "win_rate": 100 * sum(value > 0 for value in values) / len(values) if values else None,
            "average_r": mean(values) if values else None,
            "expectancy_r": mean(values) if values else None,
            "profit_factor": gains / losses if losses else None,
            "net_r": sum(values),
            "max_drawdown_r": max_drawdown,
            "average_open_seconds": mean(row["open_seconds"] for row in trades) if trades else None,
        },
        "coverage": {
            "technicals": f"{strategy.replace('_', ' ').title()}; signal on completed candle, entry at next candle open, 1 ATR stop, {target_r_multiple:g}R target, at most {max_holding_bars} bars",
            "option_chain": "Not used; NIFTY 50 spot index candles only",
            "costs": f"{cost_bps_per_side:g} bps per side applied to spot-equivalent movement; option premiums and fill costs are not modeled",
        },
        "warnings": [
            "Intraday positions close by the end of the session; a candle hitting both stop and target counts as a stop.",
            "For daily stop/target exits, 15:30 IST is a candle-end timestamp; the exact intraday hit time is unavailable.",
            "This research setup does not change NIFTY live scanner alert rules.",
        ],
    }


def _duration(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def summarize(trades: list[dict], period: tuple[date, date]) -> dict:
    selected = [row for row in trades if period[0] <= row["entry_date"] <= period[1]]
    values = [row["net_r"] for row in selected]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    gross_loss = abs(sum(losses))
    return {
        "n": len(selected),
        "win_pct": round(100 * len(wins) / len(selected), 1) if selected else None,
        "avg_r": round(mean(values), 3) if values else None,
        "net_r": round(sum(values), 2),
        "pf": round(sum(wins) / gross_loss, 2) if gross_loss else None,
        "reasons": dict(Counter(row["exit_reason"] for row in selected)),
    }


def _compact(result: dict) -> str:
    return f"{result['n']} trades, {result['win_pct']}% wins, {result['avg_r']} R/trade, PF {result['pf']}"


def main() -> None:
    for horizon, timeframe, max_bars in (
        ("intraday", "15minute", (4, 8)),
        ("swing", "60minute", (6, 12)),
        ("positional", "day", (5, 10)),
    ):
        path: Path = candle_path("data/raw/candles", timeframe, "NIFTY_50")
        candles = [
            row for row in load_candles(path)
            if horizon == "positional" or _clock(row) < time(15, 15)
        ]
        strategies = ("trend_breakout", "ema_pullback", "rsi_reversal")
        if horizon == "intraday":
            strategies += ("opening_range",)
        rows = []
        for strategy, direction, target_r, bars in product(strategies, ("both", "bullish", "bearish"), (1.0, 1.5), max_bars):
            settings = Settings(horizon, strategy, direction, target_r, bars)
            trades = simulate(candles, settings)
            rows.append({"settings": settings, **{name: summarize(trades, period) for name, period in PERIODS.items()}})
        min_development = {"intraday": 50, "swing": 20, "positional": 10}[horizon]
        min_validation = {"intraday": 25, "swing": 10, "positional": 5}[horizon]
        eligible = [row for row in rows if row["development"]["n"] >= min_development and row["validation"]["n"] >= min_validation]
        ranked = sorted(eligible, key=lambda row: (min(row["development"]["avg_r"], row["validation"]["avg_r"]), row["validation"]["n"]), reverse=True)
        print(f"\n{horizon.upper()} {len(candles)} candles through {candles[-1].timestamp.date()}, {len(rows)} combinations, {len(eligible)} sample-qualified", flush=True)
        for row in ranked[:8]:
            config = row["settings"]
            print(f"{config.strategy:16} {config.direction:7} {config.target_r}R {config.max_bars} bars", flush=True)
            for period in PERIODS:
                print(f"  {period:11} {_compact(row[period])}", flush=True)
        stable = [row for row in ranked if row["development"]["avg_r"] > 0 and row["validation"]["avg_r"] > 0]
        print(f"Positive in development AND validation: {len(stable)}", flush=True)
        for row in stable[:5]:
            config = row["settings"]
            print(f"  {config.strategy} {config.direction} {config.target_r}R/{config.max_bars} bars: holdout {_compact(row['holdout'])}", flush=True)


if __name__ == "__main__":
    main()
