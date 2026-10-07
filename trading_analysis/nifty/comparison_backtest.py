"""Controlled spot-signal ablations using only observations received by decision time."""
from bisect import bisect_right
from datetime import datetime, timedelta
from statistics import mean

from trading_analysis.live_timing import IST, as_ist
from trading_analysis.nifty.live_scanner import _ema_series, _rsi_series, _historical_direction, candle_close_time, closed_candles
from trading_analysis.nifty.technical_backtest import Settings, simulate
from trading_analysis.option_history import rolling_evidence

VARIANTS = {"technical": "Technical only", "flow": "Technical + option price/volume",
            "oi": "Technical + rolling OI", "combined": "Technical + flow + rolling OI"}


def compare_setups(candles, horizon, snapshots, *, trigger="standard", trend_candles=None,
                   from_date=None, to_date=None, direction="both", target_r=2, max_bars=8,
                   cost_bps=2, now=None):
    if horizon not in {"intraday", "swing", "positional"} or direction not in {"both", "bullish", "bearish"}:
        raise ValueError("Invalid horizon or direction")
    if trigger not in {"standard", "15m_5m"} or trigger == "15m_5m" and horizon != "intraday":
        raise ValueError("15m trend / 5m trigger requires Intraday")
    if not 0 < target_r <= 10 or not 1 <= max_bars <= 100 or not 0 <= cost_bps <= 100:
        raise ValueError("Invalid target R, holding bars or cost")
    current = as_ist(now or datetime.now(IST))
    first = as_ist(from_date) if from_date else current - timedelta(days=365)
    end = min(current, as_ist(to_date) + timedelta(days=1) - timedelta(microseconds=1)) if to_date else current
    if first > end:
        raise ValueError("From date must not be after the available end date")
    frame = "5minute" if trigger == "15m_5m" else {"intraday": "15minute", "swing": "60minute", "positional": "day"}[horizon]
    bars = closed_candles(candles, frame, end)
    if len(bars) < 61:
        raise ValueError(f"At least 61 completed {frame} candles are needed. Download the selected timeframe in Data Ops.")
    trend = closed_candles(trend_candles or [], "15minute", end)
    if trigger == "15m_5m" and len(trend) < 50:
        raise ValueError("15m/5m comparison also requires 50 completed 15minute trend candles")
    trend_times = [candle_close_time(row.timestamp, "15minute") for row in trend]
    closes = [row.close for row in trend]
    fast, slow, momentum = _ema_series(closes, 20), _ema_series(closes, 50), _rsi_series(closes, 14)
    observations = sorted([row for row in snapshots if as_ist(row["received_at"]) <= end], key=lambda row: row["received_at"])
    observation_times = [as_ist(row["received_at"]) for row in observations]
    evidence_cache = {}

    def evidence_at(decision):
        position = bisect_right(observation_times, decision) - 1
        if position < 0 or (decision - observation_times[position]).total_seconds() > 600:
            return {"bias": "unavailable", "flow_bias": "unavailable"}
        if position not in evidence_cache:
            left = bisect_right(observation_times, observation_times[position] - timedelta(minutes=45))
            evidence_cache[position] = rolling_evidence(observations[left:position + 1], observation_times[position])
        return evidence_cache[position]

    results = []
    for variant, label in VARIANTS.items():
        counts = {"technical_checks": 0, "missing_option_evidence": 0, "filter_rejections": 0}

        def allowed(index, signal, decision):
            decision = as_ist(decision)
            if not first <= decision <= end:
                return False
            if trigger == "15m_5m":
                at = bisect_right(trend_times, decision) - 1
                if at < 0 or (decision - trend_times[at]).total_seconds() > 900:
                    return False
                if _historical_direction(trend[at], fast[at], slow[at], momentum[at], "both") != signal:
                    return False
            counts["technical_checks"] += 1
            if variant == "technical":
                return True
            evidence = evidence_at(decision)
            fields = ("flow_bias",) if variant == "flow" else ("bias",) if variant == "oi" else ("flow_bias", "bias")
            if any(evidence.get(key) == "unavailable" for key in fields):
                counts["missing_option_evidence"] += 1
                return False
            passed = all(evidence.get(key) == signal for key in fields)
            counts["filter_rejections"] += int(not passed)
            return passed

        settings = Settings(horizon, "scanner_rules", direction, target_r, max_bars,
                            cost_bps_per_side=cost_bps, trigger_minutes=5 if trigger == "15m_5m" else 15, ema_exit=True)
        trades = simulate(bars, settings, entry_filter=allowed)
        trades = [row for row in trades if first <= as_ist(row["entry_time"]) <= end]
        results.append({"variant": variant, "label": label, "trade_count": len(trades),
                        "metrics": metrics(trades), "coverage": counts, "trades": trades,
                        "periods": _periods(trades, first, end)})
    return {"horizon": horizon, "trigger": trigger, "timeframe": frame, "variants": results,
            "observation_count": len(observations), "from": first.isoformat(), "to": end.isoformat(),
            "observation_from": observations[0]["received_at"] if observations else None,
            "observation_to": observations[-1]["received_at"] if observations else None,
            "assumptions": ["Research comparison; does not change live scanner rules.",
                            "Technical baseline: EMA20/EMA50, RSI14 52/48 and candle direction. Intraday signal cutoff 14:45; session exit from 15:00.",
                            "Next-candle open; 1 ATR initial stop, fixed target R, EMA20 reversal and holding/session exits.",
                            "Live constituent breadth, candidate scores, quote-age and price-drift gates are not replayed; this is a controlled filter comparison, not a full live-scanner replica.",
                            "Same-bar stop/target ambiguity is resolved stop-first; exit time is the bar-close bound, not an exact touch time.",
                            "Option filters use receipt-time history, matching expiry/contracts and timestamp coverage. Missing history is skipped, never synthesized.",
                            f"{cost_bps:g} bps per side is a spot-equivalent cost stress, not option execution cost.",
                            "Results are spot-signal research, not option P&L or a guaranteed winning strategy."]}


def metrics(trades):
    values = [row["net_r"] for row in trades]
    gains, losses = sum(max(0, row) for row in values), -sum(min(0, row) for row in values)
    equity = peak = drawdown = 0
    for row in values:
        equity += row
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"win_rate": sum(row > 0 for row in values) / len(values) * 100 if values else None,
            "average_r": mean(values) if values else None, "net_r": sum(values),
            "profit_factor": gains / losses if losses else None, "max_drawdown_r": drawdown,
            "average_open_seconds": mean((as_ist(row["exit_time"]) - as_ist(row["entry_time"])).total_seconds() for row in trades) if trades else None}


def _periods(trades, first, end):
    split = first + (end - first) * 0.7
    return [{"name": name, "trade_count": len(rows), "metrics": metrics(rows)} for name, rows in (
        ("Earlier 70%", [row for row in trades if as_ist(row["entry_time"]) < split]),
        ("Later 30% holdout", [row for row in trades if as_ist(row["entry_time"]) >= split]))]
