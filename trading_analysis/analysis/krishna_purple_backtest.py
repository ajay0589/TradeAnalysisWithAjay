from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any

from trading_analysis.analysis.krishna_setup import (
    KrishnaPurpleTouchConfig,
    krishna_purple_profile,
    scan_krishna_purple_exit_status,
    scan_krishna_purple_touch_setup,
)
from trading_analysis.analysis.market_structure import analyze_market_structure
from trading_analysis.analysis.technical import ema
from trading_analysis.candles import aggregate_month_span, convert_timeframe
from trading_analysis.models import Candle


PROFILE_ORDER = ("month", "week", "day")
_TIMEFRAME_MINUTES = {
    "10minute": 10,
    "15minute": 15,
    "30minute": 30,
    "60minute": 60,
    "120minute": 120,
}


@dataclass(frozen=True)
class PurpleTouchBacktestConfig:
    from_date: str
    to_date: str
    profiles: tuple[str, ...] = PROFILE_ORDER
    entry_mode: str = "both"
    touch_tolerance_percent: float = 3.0
    min_score: int = 60
    stop_mode: str = "setup_invalidation"
    stop_percent: float = 3.0
    target_r_multiple: float = 2.0
    max_holding_bars: int = 0
    slippage_bps: float = 5.0
    costs_bps: float = 10.0
    capital: float = 1_000_000.0
    risk_per_trade_percent: float = 1.0

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> PurpleTouchBacktestConfig:
        raw = dict(values or {})
        profiles = raw.get("profiles") or PROFILE_ORDER
        if isinstance(profiles, str):
            profiles = [part.strip() for part in profiles.split(",") if part.strip()]
        normalized_profiles = tuple(_profile_key(value) for value in profiles)
        if not normalized_profiles:
            raise ValueError("Select at least one Purple Touch profile.")
        unknown = set(raw) - {
            "from_date", "to_date", "profiles", "entry_mode", "touch_tolerance_percent",
            "min_score", "stop_mode", "stop_percent", "target_r_multiple",
            "max_holding_bars", "slippage_bps", "costs_bps", "capital",
            "risk_per_trade_percent",
        }
        if unknown:
            raise ValueError(f"Unknown Purple Touch backtest parameter(s): {', '.join(sorted(unknown))}.")
        from_date = _required_date(raw.get("from_date"), "from_date")
        to_date = _required_date(raw.get("to_date"), "to_date")
        if from_date > to_date:
            raise ValueError("from_date must be on or before to_date.")
        entry_mode = str(raw.get("entry_mode") or "both").lower()
        if entry_mode not in {"both", "early", "final"}:
            raise ValueError("entry_mode must be one of: both, early, final.")
        stop_mode = str(raw.get("stop_mode") or "setup_invalidation").lower()
        if stop_mode not in {"setup_invalidation", "percent", "none"}:
            raise ValueError("stop_mode must be one of: setup_invalidation, percent, none.")
        tolerance = float(raw.get("touch_tolerance_percent", 3.0))
        if tolerance < 0 or tolerance > 3:
            raise ValueError("touch_tolerance_percent must be between 0 and 3.")
        score = int(raw.get("min_score", 60))
        if score < 0 or score > 100:
            raise ValueError("min_score must be between 0 and 100.")
        stop_percent = float(raw.get("stop_percent", 3.0))
        target_r = float(raw.get("target_r_multiple", 2.0))
        holding = int(raw.get("max_holding_bars", 0))
        capital = float(raw.get("capital", 1_000_000.0))
        risk_percent = float(raw.get("risk_per_trade_percent", 1.0))
        if stop_percent <= 0:
            raise ValueError("stop_percent must be greater than zero.")
        if target_r < 0:
            raise ValueError("target_r_multiple cannot be negative.")
        if holding < 0:
            raise ValueError("max_holding_bars cannot be negative.")
        if capital <= 0 or risk_percent <= 0:
            raise ValueError("capital and risk_per_trade_percent must be greater than zero.")
        return cls(
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
            profiles=normalized_profiles,
            entry_mode=entry_mode,
            touch_tolerance_percent=tolerance,
            min_score=score,
            stop_mode=stop_mode,
            stop_percent=stop_percent,
            target_r_multiple=target_r,
            max_holding_bars=holding,
            slippage_bps=max(0.0, float(raw.get("slippage_bps", 5.0))),
            costs_bps=max(0.0, float(raw.get("costs_bps", 10.0))),
            capital=capital,
            risk_per_trade_percent=risk_percent,
        )


def backtest_krishna_purple_touch(
    symbol_candles: dict[str, dict[str, list[Candle]]],
    config: PurpleTouchBacktestConfig,
) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    suppressed = 0
    for symbol, candle_map in symbol_candles.items():
        for profile_key in config.profiles:
            profile = krishna_purple_profile(profile_key)
            required = {"day", profile.early_timeframe, profile.final_timeframe, profile.exit_timeframe}
            missing = sorted(timeframe for timeframe in required if not candle_map.get(timeframe))
            coverage.append(_coverage_row(symbol, profile_key, candle_map, required, missing))
            if missing:
                errors.append({
                    "symbol": symbol,
                    "profile": profile_key,
                    "error": f"Missing cached candle timeframe(s): {', '.join(missing)}.",
                })
                continue
            try:
                result = _backtest_symbol_profile(symbol, candle_map, profile_key, config)
                trades.extend(result["trades"])
                signals.extend(result["signals"])
                suppressed += result["suppressed_entries"]
            except Exception as exc:
                errors.append({"symbol": symbol, "profile": profile_key, "error": str(exc)})

    trades.sort(key=lambda row: (row["entry_time"], row["symbol"], row["profile"], row["entry_kind"]))
    signals.sort(key=lambda row: (row["signal_time"], row["symbol"], row["profile"], row["entry_kind"]))
    metrics = _metrics(trades, config)
    return {
        "type": "krishna_purple_touch_backtest",
        "config": asdict(config),
        "analyzed_symbols": len(symbol_candles),
        "signal_count": len(signals),
        "trade_count": len(trades),
        "suppressed_entries": suppressed,
        "metrics": metrics,
        "profile_performance": _group_performance(trades, "profile"),
        "entry_performance": _group_performance(trades, "entry_kind"),
        "symbol_performance": _group_performance(trades, "symbol"),
        "trades": trades,
        "signals": signals,
        "coverage": coverage,
        "errors": errors,
        "summary": {
            "points": [
                "Historical simulation uses cached underlying-stock candles; it does not simulate option premiums, IV, margin, or expiry effects.",
                "Each event uses only candles available at that event. Entry is the next entry-timeframe candle open.",
                "Within one stock/profile setup, early may occur before final; final suppresses any later early entry.",
                "One symbol can have at most two trades per profile and six across Monthly, Weekly, and Daily; each profile remains independent.",
                "Monthly, Weekly, and Daily profiles are independent, so the same symbol can have separate profile trades.",
                "When stop and target are both touched in one candle, the stop is assumed first.",
                "R is the initial per-share risk from entry to stop. Positive average R and profit factor above 1 indicate historical edge before judging drawdown and sample size.",
            ],
            "limitations": [
                "Intraday history is usually shorter than daily history; coverage rows show the actual available overlap.",
                "Historical high-timeframe candles are rebuilt as-of each intraday event where source data permits, but broker timestamp conventions can still differ from charting platforms.",
                "Results are historical setup evidence for manual review, not an execution instruction.",
                "Position sizing uses the configured starting capital for each trade and does not reserve capital across overlapping symbols; ending return is therefore an aggregate simulation, not a portfolio-capacity result.",
            ],
        },
    }


def _backtest_symbol_profile(
    symbol: str,
    candle_map: dict[str, list[Candle]],
    profile_key: str,
    config: PurpleTouchBacktestConfig,
) -> dict[str, Any]:
    profile = krishna_purple_profile(profile_key)
    daily = sorted(candle_map["day"], key=lambda row: row.timestamp)
    early = sorted(candle_map[profile.early_timeframe], key=lambda row: row.timestamp)
    final = sorted(candle_map[profile.final_timeframe], key=lambda row: row.timestamp)
    exit_candles = sorted(candle_map[profile.exit_timeframe], key=lambda row: row.timestamp)
    base_intraday = _finest_intraday(candle_map, profile)
    from_day = date.fromisoformat(config.from_date)
    to_day = date.fromisoformat(config.to_date)
    events = _entry_events(early, profile.early_timeframe, final, profile.final_timeframe, from_day, to_day)
    scanner_config = KrishnaPurpleTouchConfig(
        step1_touch_tolerance_percent=config.touch_tolerance_percent,
        purple_touch_tolerance_percent=config.touch_tolerance_percent,
    )
    setup_states: dict[tuple[Any, ...], dict[str, bool]] = {}
    open_until: dict[str, datetime | None] = {"early": None, "final": None}
    trades: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    suppressed = 0

    for event_time, _priority, entry_kind, event_index in events:
        if config.entry_mode != "both" and config.entry_mode != entry_kind:
            continue
        daily_as_of = _daily_as_of(daily, base_intraday, event_time)
        touch_candles = convert_timeframe(daily_as_of, profile.purple_timeframe)[-160:]
        if len(touch_candles) < 52:
            continue
        purple_ema9 = ema([candle.close for candle in touch_candles], 9)
        latest_touch = touch_candles[-1]
        tolerance = config.touch_tolerance_percent / 100
        if purple_ema9 is None or not (
            latest_touch.low <= purple_ema9 * (1 + tolerance)
            and latest_touch.high >= purple_ema9 * (1 - tolerance)
        ):
            continue
        confirmation = _confirmation_as_of(daily_as_of, profile.confirmation_timeframe)[-160:]
        early_as_of = _available_prefix(early, profile.early_timeframe, event_time)[-200:]
        final_as_of = _available_prefix(final, profile.final_timeframe, event_time)[-200:]
        structure = analyze_market_structure(touch_candles) if len(touch_candles) >= 10 else None
        match = scan_krishna_purple_touch_setup(
            symbol,
            touch_candles,
            early_candles=early_as_of,
            final_candles=final_as_of,
            confirmation_candles=confirmation,
            purple_timeframe=profile.purple_timeframe,
            structure=structure,
            config=scanner_config,
        )
        if not match or match.score < config.min_score:
            continue
        entry = match.final_entry if entry_kind == "final" else match.early_entry
        if entry.get("status") != "entry_candidate":
            continue
        setup_key = _period_key(touch_candles[-1].timestamp, profile.purple_timeframe)
        state = setup_states.setdefault(setup_key, {"early": False, "final": False})
        if state[entry_kind]:
            continue
        if entry_kind == "early" and state["final"]:
            suppressed += 1
            continue
        if entry_kind == "early" and _still_open(open_until["final"], event_time):
            suppressed += 1
            continue
        if _still_open(open_until[entry_kind], event_time):
            suppressed += 1
            continue

        source = final if entry_kind == "final" else early
        next_index = event_index + 1
        signal = _signal_row(symbol, profile_key, entry_kind, event_time, match, entry)
        state[entry_kind] = True
        if entry_kind == "final":
            state["final"] = True
        if next_index >= len(source) or source[next_index].timestamp.date() > to_day:
            signal["status"] = "expired_no_next_bar"
            signals.append(signal)
            continue

        entry_candle = source[next_index]
        trade = _simulate_trade(
            symbol=symbol,
            profile_key=profile_key,
            entry_kind=entry_kind,
            signal=signal,
            entry_snapshot=entry,
            entry_candle=entry_candle,
            exit_candles=exit_candles,
            exit_timeframe=profile.exit_timeframe,
            config=config,
            to_day=to_day,
        )
        signal["status"] = "entered"
        signal["entry_time"] = trade["entry_time"]
        signal["entry_price"] = trade["entry_price"]
        signals.append(signal)
        trades.append(trade)
        open_until[entry_kind] = datetime.fromisoformat(trade["exit_time"])

    return {"trades": trades, "signals": signals, "suppressed_entries": suppressed}


def _simulate_trade(
    *,
    symbol: str,
    profile_key: str,
    entry_kind: str,
    signal: dict[str, Any],
    entry_snapshot: dict[str, Any],
    entry_candle: Candle,
    exit_candles: list[Candle],
    exit_timeframe: str,
    config: PurpleTouchBacktestConfig,
    to_day: date,
) -> dict[str, Any]:
    raw_entry = entry_candle.open
    entry_price = raw_entry * (1 + config.slippage_bps / 10_000)
    stop, stop_note = _initial_stop(entry_price, entry_snapshot, config)
    risk = entry_price - stop if stop is not None else None
    target = entry_price + (risk * config.target_r_multiple) if risk and config.target_r_multiple > 0 else None
    entry_time = entry_candle.timestamp
    candidates = [
        (index, candle)
        for index, candle in enumerate(exit_candles)
        if candle.timestamp >= entry_time and candle.timestamp.date() <= to_day
    ]
    exit_price = None
    exit_time = None
    exit_reason = "end_of_data"
    ambiguous = False
    bars_held = 0
    highest = entry_price
    lowest = entry_price
    for index, candle in candidates:
        bars_held += 1
        highest = max(highest, candle.high)
        lowest = min(lowest, candle.low)
        stop_hit = stop is not None and candle.low <= stop
        target_hit = target is not None and candle.high >= target
        if stop_hit:
            exit_price = stop
            exit_time = candle.timestamp
            exit_reason = "stop_loss"
            ambiguous = bool(target_hit)
            break
        if target_hit:
            exit_price = target
            exit_time = candle.timestamp
            exit_reason = "target"
            break
        snapshot = scan_krishna_purple_exit_status(symbol, exit_candles[max(0, index - 199) : index + 1], exit_timeframe)
        if snapshot.get("status") == "exit_triggered":
            exit_price = candle.close
            exit_time = candle.timestamp
            exit_reason = "yellow_close_exit"
            break
        if config.max_holding_bars and bars_held >= config.max_holding_bars:
            exit_price = candle.close
            exit_time = candle.timestamp
            exit_reason = "max_holding_bars"
            break
    if exit_price is None:
        fallback = candidates[-1][1] if candidates else entry_candle
        exit_price = fallback.close
        exit_time = fallback.timestamp
    adjusted_exit = exit_price * (1 - config.slippage_bps / 10_000)
    gross_return = ((adjusted_exit - entry_price) / entry_price) * 100
    net_return = gross_return - (config.costs_bps / 100)
    risk_percent = ((entry_price - stop) / entry_price) * 100 if stop is not None else None
    r_multiple = net_return / risk_percent if risk_percent and risk_percent > 0 else None
    risk_capital = config.capital * (config.risk_per_trade_percent / 100)
    quantity = risk_capital / risk if risk and risk > 0 else config.capital / entry_price
    pnl = quantity * (adjusted_exit - entry_price) - (quantity * entry_price * config.costs_bps / 10_000)
    return {
        "trade_id": f"BT-{symbol}-{profile_key}-{entry_kind}-{entry_time.isoformat()}",
        "symbol": symbol,
        "profile": profile_key,
        "entry_kind": entry_kind,
        "signal_time": signal["signal_time"],
        "entry_time": entry_time.isoformat(),
        "exit_time": exit_time.isoformat(),
        "entry_timeframe": signal["entry_timeframe"],
        "exit_timeframe": exit_timeframe,
        "entry_price": round(entry_price, 4),
        "stop_loss": round(stop, 4) if stop is not None else None,
        "target": round(target, 4) if target is not None else None,
        "exit_price": round(adjusted_exit, 4),
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "holding_minutes": max(0, int((exit_time - entry_time).total_seconds() // 60)),
        "gross_return_percent": round(gross_return, 4),
        "net_return_percent": round(net_return, 4),
        "risk_percent": round(risk_percent, 4) if risk_percent is not None else None,
        "r_multiple": round(r_multiple, 4) if r_multiple is not None else None,
        "max_favorable_percent": round(((highest - entry_price) / entry_price) * 100, 4),
        "max_adverse_percent": round(((lowest - entry_price) / entry_price) * 100, 4),
        "quantity": round(quantity, 4),
        "pnl": round(pnl, 2),
        "score": signal["score"],
        "confidence": signal["confidence"],
        "touch_distance_percent": signal["touch_distance_percent"],
        "stop_note": stop_note,
        "intrabar_ambiguous": ambiguous,
        "success": net_return > 0,
    }


def _signal_row(symbol: str, profile_key: str, entry_kind: str, event_time: datetime, match, entry) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "profile": profile_key,
        "entry_kind": entry_kind,
        "signal_time": event_time.isoformat(),
        "entry_timeframe": entry.get("timeframe"),
        "score": match.score,
        "confidence": match.confidence,
        "touch_distance_percent": match.purple_range_distance_percent,
        "invalidation_level": entry.get("invalidation_level"),
        "reasons": list(match.reasons) + list(entry.get("reasons") or []),
        "warnings": list(match.warnings) + list(entry.get("warnings") or []),
        "status": "signal",
    }


def _entry_events(
    early: list[Candle], early_timeframe: str,
    final: list[Candle], final_timeframe: str,
    from_day: date, to_day: date,
) -> list[tuple[datetime, int, str, int]]:
    rows: list[tuple[datetime, int, str, int]] = []
    for kind, candles, timeframe, priority in (
        ("final", final, final_timeframe, 0),
        ("early", early, early_timeframe, 1),
    ):
        for index, candle in enumerate(candles):
            available = _available_time(candle, timeframe)
            if from_day <= available.date() <= to_day:
                rows.append((available, priority, kind, index))
    return sorted(rows, key=lambda row: (row[0], row[1]))


def _daily_as_of(daily: list[Candle], intraday: list[Candle], at: datetime) -> list[Candle]:
    completed = [candle for candle in daily if _available_time(candle, "day") <= at]
    if completed and completed[-1].timestamp.date() == at.date():
        return completed
    partial_rows = [
        candle for candle in intraday
        if candle.timestamp.date() == at.date() and _available_time(candle, _infer_intraday_timeframe(intraday)) <= at
    ]
    if not partial_rows:
        return completed
    first = partial_rows[0]
    last = partial_rows[-1]
    partial = Candle(
        timestamp=last.timestamp,
        open=first.open,
        high=max(candle.high for candle in partial_rows),
        low=min(candle.low for candle in partial_rows),
        close=last.close,
        volume=sum(candle.volume for candle in partial_rows),
        open_interest=last.open_interest,
    )
    return completed + [partial]


def _available_prefix(candles: list[Candle], timeframe: str, at: datetime) -> list[Candle]:
    return [candle for candle in candles if _available_time(candle, timeframe) <= at]


def _available_time(candle: Candle, timeframe: str) -> datetime:
    minutes = _TIMEFRAME_MINUTES.get(timeframe)
    if minutes:
        return candle.timestamp + timedelta(minutes=minutes)
    if timeframe == "day":
        return datetime.combine(candle.timestamp.date(), time(15, 30), tzinfo=candle.timestamp.tzinfo)
    return candle.timestamp


def _finest_intraday(candle_map: dict[str, list[Candle]], profile) -> list[Candle]:
    candidates = []
    for timeframe in {profile.early_timeframe, profile.final_timeframe, profile.exit_timeframe}:
        minutes = _TIMEFRAME_MINUTES.get(timeframe)
        if minutes and candle_map.get(timeframe):
            candidates.append((minutes, candle_map[timeframe]))
    return sorted(candidates, key=lambda row: row[0])[0][1]


def _infer_intraday_timeframe(candles: list[Candle]) -> str:
    if len(candles) >= 2:
        delta = int((candles[1].timestamp - candles[0].timestamp).total_seconds() // 60)
        if delta in {10, 15, 30, 60, 120}:
            return f"{delta}minute"
    return "10minute"


def _confirmation_as_of(daily: list[Candle], timeframe: str) -> list[Candle]:
    if timeframe == "5month":
        return aggregate_month_span(daily, 5)
    return convert_timeframe(daily, timeframe)


def _initial_stop(entry_price: float, entry_snapshot: dict[str, Any], config: PurpleTouchBacktestConfig) -> tuple[float | None, str]:
    if config.stop_mode == "none":
        return None, "No fixed stop; yellow close exit only."
    if config.stop_mode == "setup_invalidation":
        level = entry_snapshot.get("invalidation_level")
        if level is not None and 0 < float(level) < entry_price:
            return float(level), "Lower of Candle 1 and Candle 2 lows."
        fallback = entry_price * (1 - config.stop_percent / 100)
        return fallback, "Setup invalidation was unavailable; percentage stop fallback used."
    return entry_price * (1 - config.stop_percent / 100), f"{config.stop_percent:.2f}% below entry."


def _metrics(trades: list[dict[str, Any]], config: PurpleTouchBacktestConfig) -> dict[str, Any]:
    returns = [float(row["net_return_percent"]) for row in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    r_values = [float(row["r_multiple"]) for row in trades if row.get("r_multiple") is not None]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = config.capital
    peak = equity
    max_drawdown = 0.0
    for row in sorted(trades, key=lambda trade: trade["exit_time"]):
        equity += float(row.get("pnl") or 0)
        peak = max(peak, equity)
        if peak:
            max_drawdown = max(max_drawdown, ((peak - equity) / peak) * 100)
    target_r = config.target_r_multiple
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_percent": round((len(wins) / len(trades)) * 100, 2) if trades else None,
        "average_return_percent": round(sum(returns) / len(returns), 4) if returns else None,
        "median_return_percent": round(median(returns), 4) if returns else None,
        "average_winner_percent": round(sum(wins) / len(wins), 4) if wins else None,
        "average_loser_percent": round(sum(losses) / len(losses), 4) if losses else None,
        "expectancy_percent": round(sum(returns) / len(returns), 4) if returns else None,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else (None if not gross_profit else 999.0),
        "average_r_multiple": round(sum(r_values) / len(r_values), 4) if r_values else None,
        "target_hit_rate_percent": round(sum(row["exit_reason"] == "target" for row in trades) / len(trades) * 100, 2) if trades else None,
        "stop_hit_rate_percent": round(sum(row["exit_reason"] == "stop_loss" for row in trades) / len(trades) * 100, 2) if trades else None,
        "max_drawdown_percent": round(max_drawdown, 2),
        "starting_capital": config.capital,
        "ending_capital": round(equity, 2),
        "ending_return_percent": round(((equity / config.capital) - 1) * 100, 2),
        "target_r_multiple": target_r,
        "break_even_win_rate_percent": round((1 / (1 + target_r)) * 100, 2) if target_r > 0 else None,
        "sample_quality": "useful" if len(trades) >= 30 else "small_sample",
    }


def _group_performance(trades: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        groups.setdefault(str(trade.get(key) or "unknown"), []).append(trade)
    rows = []
    for label, group in sorted(groups.items()):
        returns = [float(row["net_return_percent"]) for row in group]
        wins = [value for value in returns if value > 0]
        losses = [value for value in returns if value <= 0]
        r_values = [float(row["r_multiple"]) for row in group if row.get("r_multiple") is not None]
        rows.append({
            key: label,
            "trades": len(group),
            "win_rate_percent": round(len(wins) / len(group) * 100, 2),
            "average_return_percent": round(sum(returns) / len(returns), 4),
            "average_r_multiple": round(sum(r_values) / len(r_values), 4) if r_values else None,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses else (999.0 if wins else None),
        })
    return rows


def _coverage_row(
    symbol: str,
    profile_key: str,
    candle_map: dict[str, list[Candle]],
    required: set[str],
    missing: list[str],
) -> dict[str, Any]:
    starts = [candles[0].timestamp for timeframe in required if (candles := candle_map.get(timeframe))]
    ends = [candles[-1].timestamp for timeframe in required if (candles := candle_map.get(timeframe))]
    return {
        "symbol": symbol,
        "profile": profile_key,
        "status": "missing" if missing else "available",
        "from": max(starts).isoformat() if starts and not missing else None,
        "to": min(ends).isoformat() if ends and not missing else None,
        "missing_timeframes": missing,
        "counts": {timeframe: len(candle_map.get(timeframe) or []) for timeframe in sorted(required)},
    }


def _period_key(timestamp: datetime, timeframe: str) -> tuple[Any, ...]:
    if timeframe == "month":
        return (timestamp.year, timestamp.month)
    if timeframe == "week":
        return tuple(timestamp.isocalendar()[:2])
    return (timestamp.year, timestamp.month, timestamp.day)


def _still_open(exit_time: datetime | None, at: datetime) -> bool:
    return exit_time is not None and exit_time >= at


def _profile_key(value: Any) -> str:
    key = str(value or "").strip().lower()
    aliases = {"monthly": "month", "month": "month", "weekly": "week", "week": "week", "daily": "day", "day": "day"}
    if key not in aliases:
        raise ValueError(f"Unsupported Purple Touch profile '{value}'.")
    return aliases[key]


def _required_date(value: Any, name: str) -> date:
    if not value:
        raise ValueError(f"{name} is required.")
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD format.") from exc
