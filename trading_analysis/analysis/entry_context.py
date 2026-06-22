from __future__ import annotations

from datetime import datetime
from typing import Any

from trading_analysis.analysis.market_structure import analyze_market_structure
from trading_analysis.analysis.technical import atr, ema, rsi
from trading_analysis.models import Candle


FIB_RATIOS = (0.382, 0.5, 0.618)


def build_entry_context(
    chart_candles: list[Candle],
    daily_candles: list[Candle] | None = None,
    hourly_candles: list[Candle] | None = None,
    intraday_candles: list[Candle] | None = None,
    bucket: str = "watch",
) -> dict[str, Any]:
    daily = daily_candles or chart_candles
    hourly = hourly_candles or chart_candles
    intraday = intraday_candles or []
    rows = [
        _volatility_normalized_pullback_row(daily, hourly, intraday, bucket),
        _fibonacci_row(chart_candles, bucket),
        _ema_pullback_row(chart_candles, bucket),
        _vwap_row(intraday or chart_candles, bucket),
        _previous_day_row(daily, bucket),
        _opening_range_row(intraday, bucket),
        _volume_confirmation_row(intraday or chart_candles, bucket),
    ]
    available = [row for row in rows if row["status"] != "missing"]
    supportive = sum(1 for row in available if row["signal"] == "supportive")
    caution = sum(1 for row in available if row["signal"] == "caution")
    adverse = sum(1 for row in available if row["signal"] == "adverse")
    if not available:
        status = "missing"
        summary = "Entry context is not available because candle data is missing."
    elif adverse:
        status = "caution"
        summary = f"{supportive} supportive, {caution} caution, {adverse} adverse entry-context check(s)."
    elif supportive >= 3:
        status = "supportive"
        summary = f"{supportive} supportive entry-context check(s); pullback/trigger context is constructive."
    else:
        status = "watch"
        summary = f"{supportive} supportive and {caution} caution entry-context check(s); wait for cleaner confirmation."
    return {
        "status": status,
        "summary": summary,
        "rows": rows,
    }


def _volatility_normalized_pullback_row(
    daily_candles: list[Candle],
    hourly_candles: list[Candle],
    intraday_candles: list[Candle],
    bucket: str,
) -> dict[str, Any]:
    if bucket not in {"bullish", "bearish"}:
        return _row(
            "Volatility-normalized pullback score",
            "not_applicable",
            "watch",
            "-",
            "Directional pullback scoring is used for bullish put-sell and bearish call-sell setups.",
        )
    if len(daily_candles) < 50:
        return _row(
            "Volatility-normalized pullback score",
            "missing",
            "missing",
            "-",
            "At least 50 daily candles are needed for regime, EMA20, ATR14, and RSI14 context.",
        )

    closes = [candle.close for candle in daily_candles]
    close = closes[-1]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200) if len(closes) >= 200 else None
    atr14 = atr(daily_candles, 14)
    rsi14 = rsi(closes, 14)
    previous_rsi14 = rsi(closes[:-1], 14) if len(closes) > 15 else None
    if ema20 is None or ema50 is None or atr14 is None or atr14 == 0 or rsi14 is None:
        return _row(
            "Volatility-normalized pullback score",
            "missing",
            "missing",
            "-",
            "Could not calculate EMA20, EMA50, ATR14, or RSI14.",
        )

    atr_z = (close - ema20) / atr14
    volume_ratio = _volume_ratio(hourly_candles)
    trigger = _intraday_trigger(intraday_candles, bucket)

    if bucket == "bullish":
        regime_ok = close > ema50 and (ema200 is None or ema50 > ema200)
        depth_ok = -1.8 <= atr_z <= -0.8
        rsi_ok = 38 <= rsi14 <= 52 and (previous_rsi14 is None or rsi14 >= previous_rsi14)
        trigger_ok = trigger == "bullish reclaim"
    else:
        regime_ok = close < ema50 and (ema200 is None or ema50 < ema200)
        depth_ok = 0.8 <= atr_z <= 1.8
        rsi_ok = 48 <= rsi14 <= 62 and (previous_rsi14 is None or rsi14 <= previous_rsi14)
        trigger_ok = trigger == "bearish rejection"

    volume_ok = volume_ratio is not None and volume_ratio <= 0.9
    checks = {
        "regime": regime_ok,
        "ATR pullback": depth_ok,
        "RSI reset": rsi_ok,
        "60m volume contraction": volume_ok,
        "15m trigger": trigger_ok,
    }
    score = sum(1 for passed in checks.values() if passed)
    signal = "supportive" if score >= 4 else "caution" if score >= 2 else "adverse"
    level = f"{score}/5"
    ema200_text = f", EMA200 {ema200:.2f}" if ema200 is not None else ""
    volume_text = f"{volume_ratio:.2f}x" if volume_ratio is not None else "missing"
    detail = (
        f"Regime {'ok' if regime_ok else 'not ok'}; ATR-z {atr_z:.2f}; "
        f"RSI14 {rsi14:.2f}; 60m volume {volume_text}; 15m trigger {trigger}. "
        f"EMA20 {ema20:.2f}, EMA50 {ema50:.2f}{ema200_text}. "
        f"Passed: {', '.join(name for name, passed in checks.items() if passed) or 'none'}."
    )
    return _row("Volatility-normalized pullback score", "analyzed", signal, level, detail)


def _fibonacci_row(candles: list[Candle], bucket: str) -> dict[str, Any]:
    if len(candles) < 10:
        return _row("Fibonacci retracement", "missing", "missing", "-", "At least 10 candles are needed.")
    structure = analyze_market_structure(candles)
    swing_high = structure.last_swing_high
    swing_low = structure.last_swing_low
    if not swing_high or not swing_low or swing_high.price == swing_low.price:
        return _row("Fibonacci retracement", "missing", "missing", "-", "Latest swing high/low was not found.")

    close = candles[-1].close
    low = min(swing_low.price, swing_high.price)
    high = max(swing_low.price, swing_high.price)
    price_range = high - low
    trend = "up" if swing_low.index < swing_high.index else "down"
    if trend == "up":
        levels = {ratio: high - (price_range * ratio) for ratio in FIB_RATIOS}
    else:
        levels = {ratio: low + (price_range * ratio) for ratio in FIB_RATIOS}
    nearest_ratio, nearest_level = min(levels.items(), key=lambda item: abs(close - item[1]))
    distance = _distance_percent(close, nearest_level)
    signal = _zone_signal(bucket, close, nearest_level, distance, support_like=(trend == "up"))
    detail = (
        f"Swing low {low:.2f}, swing high {high:.2f}; "
        f"38.2 {levels[0.382]:.2f}, 50 {levels[0.5]:.2f}, 61.8 {levels[0.618]:.2f}. "
        f"Close is {distance:.2f}% from {nearest_ratio * 100:.1f}% retracement."
    )
    return _row("Fibonacci retracement", "analyzed", signal, f"{nearest_level:.2f}", detail)


def _ema_pullback_row(candles: list[Candle], bucket: str) -> dict[str, Any]:
    if len(candles) < 50:
        return _row("20 EMA / 50 EMA pullback", "missing", "missing", "-", "At least 50 candles are needed.")
    closes = [candle.close for candle in candles]
    close = closes[-1]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    if ema20 is None or ema50 is None:
        return _row("20 EMA / 50 EMA pullback", "missing", "missing", "-", "EMA values could not be calculated.")
    zone_low = min(ema20, ema50)
    zone_high = max(ema20, ema50)
    in_zone = zone_low <= close <= zone_high
    distance = min(abs(_distance_percent(close, ema20)), abs(_distance_percent(close, ema50)))
    if bucket == "bullish":
        signal = "supportive" if close >= zone_low and distance <= 1.5 else "caution"
    elif bucket == "bearish":
        signal = "supportive" if close <= zone_high and distance <= 1.5 else "caution"
    else:
        signal = "supportive" if in_zone else "caution"
    detail = f"EMA20 {ema20:.2f}, EMA50 {ema50:.2f}; close {close:.2f} is {'inside' if in_zone else 'outside'} the zone."
    return _row("20 EMA / 50 EMA pullback", "analyzed", signal, f"{zone_low:.2f}-{zone_high:.2f}", detail)


def _vwap_row(candles: list[Candle], bucket: str) -> dict[str, Any]:
    session = _latest_session(candles)
    if len(session) < 2:
        return _row("VWAP", "missing", "missing", "-", "Intraday candles are needed for useful VWAP.")
    total_volume = sum(candle.volume for candle in session)
    if total_volume <= 0:
        return _row("VWAP", "missing", "missing", "-", "VWAP needs non-zero volume.")
    vwap = sum(_typical_price(candle) * candle.volume for candle in session) / total_volume
    close = session[-1].close
    distance = _distance_percent(close, vwap)
    if bucket == "bullish":
        signal = "supportive" if close >= vwap else "caution"
    elif bucket == "bearish":
        signal = "supportive" if close <= vwap else "caution"
    else:
        signal = "supportive" if abs(distance) <= 0.5 else "caution"
    detail = f"Latest session VWAP {vwap:.2f}; close {close:.2f} is {distance:.2f}% from VWAP."
    return _row("VWAP", "analyzed", signal, f"{vwap:.2f}", detail)


def _previous_day_row(candles: list[Candle], bucket: str) -> dict[str, Any]:
    daily = _daily_sessions(candles)
    if len(daily) < 2:
        return _row("Previous day high/low", "missing", "missing", "-", "At least two daily sessions are needed.")
    previous = daily[-2]
    current = daily[-1]
    close = current.close
    if bucket == "bullish":
        signal = "supportive" if close >= previous.high else "caution"
    elif bucket == "bearish":
        signal = "supportive" if close <= previous.low else "caution"
    else:
        signal = "supportive" if previous.low <= close <= previous.high else "caution"
    detail = f"PDH {previous.high:.2f}, PDL {previous.low:.2f}; latest close {close:.2f}."
    return _row("Previous day high/low", "analyzed", signal, f"{previous.low:.2f}-{previous.high:.2f}", detail)


def _opening_range_row(candles: list[Candle], bucket: str, candles_count: int = 2) -> dict[str, Any]:
    session = _latest_session(candles)
    if len(session) < candles_count + 1:
        return _row("Opening range", "missing", "missing", "-", "15-minute candles are needed for opening range.")
    opening = session[:candles_count]
    high = max(candle.high for candle in opening)
    low = min(candle.low for candle in opening)
    close = session[-1].close
    if bucket == "bullish":
        signal = "supportive" if close > high else "caution"
    elif bucket == "bearish":
        signal = "supportive" if close < low else "caution"
    else:
        signal = "supportive" if low <= close <= high else "caution"
    detail = f"Opening range {low:.2f}-{high:.2f}; latest close {close:.2f}."
    return _row("Opening range", "analyzed", signal, f"{low:.2f}-{high:.2f}", detail)


def _volume_confirmation_row(candles: list[Candle], bucket: str) -> dict[str, Any]:
    if len(candles) < 21:
        return _row("Volume confirmation", "missing", "missing", "-", "At least 21 candles are needed.")
    latest = candles[-1]
    previous = candles[-2]
    average = sum(candle.volume for candle in candles[-21:-1]) / 20
    ratio = latest.volume / average if average else None
    if ratio is None:
        return _row("Volume confirmation", "missing", "missing", "-", "Average volume could not be calculated.")
    bullish_reclaim = latest.close > previous.high and latest.close > latest.open
    bearish_rejection = latest.close < previous.low and latest.close < latest.open
    if bucket == "bullish":
        signal = "supportive" if bullish_reclaim and ratio >= 1.2 else "caution"
        pattern = "bullish reclaim" if bullish_reclaim else "no bullish reclaim"
    elif bucket == "bearish":
        signal = "supportive" if bearish_rejection and ratio >= 1.2 else "caution"
        pattern = "bearish rejection" if bearish_rejection else "no bearish rejection"
    else:
        signal = "supportive" if ratio <= 0.8 else "caution"
        pattern = "volume dry-up" if ratio <= 0.8 else "active volume"
    detail = f"{pattern}; latest volume {latest.volume}, Vol x20 {ratio:.2f}."
    return _row("Volume confirmation", "analyzed", signal, f"{ratio:.2f}x", detail)


def _row(zone: str, status: str, signal: str, level: str, detail: str) -> dict[str, Any]:
    return {
        "zone": zone,
        "status": status,
        "signal": signal,
        "level": level,
        "detail": detail,
    }


def _zone_signal(bucket: str, close: float, level: float, distance: float, support_like: bool) -> str:
    if abs(distance) <= 1.0:
        return "supportive"
    if bucket == "bullish" and support_like and close >= level:
        return "supportive" if abs(distance) <= 2.5 else "caution"
    if bucket == "bearish" and not support_like and close <= level:
        return "supportive" if abs(distance) <= 2.5 else "caution"
    return "caution"


def _latest_session(candles: list[Candle]) -> list[Candle]:
    if not candles:
        return []
    latest_date = candles[-1].timestamp.date()
    return [candle for candle in candles if candle.timestamp.date() == latest_date]


def _daily_sessions(candles: list[Candle]) -> list[Candle]:
    sessions: dict[datetime.date, list[Candle]] = {}
    for candle in sorted(candles, key=lambda item: item.timestamp):
        sessions.setdefault(candle.timestamp.date(), []).append(candle)
    output = []
    for group in sessions.values():
        first = group[0]
        last = group[-1]
        output.append(
            Candle(
                timestamp=last.timestamp,
                open=first.open,
                high=max(candle.high for candle in group),
                low=min(candle.low for candle in group),
                close=last.close,
                volume=sum(candle.volume for candle in group),
                open_interest=last.open_interest,
            )
        )
    return output


def _typical_price(candle: Candle) -> float:
    return (candle.high + candle.low + candle.close) / 3


def _distance_percent(close: float, level: float) -> float:
    if close == 0:
        return 0.0
    return ((close - level) / close) * 100


def _volume_ratio(candles: list[Candle]) -> float | None:
    if len(candles) < 21:
        return None
    baseline = _median([candle.volume for candle in candles[-21:-1]])
    return candles[-1].volume / baseline if baseline else None


def _intraday_trigger(candles: list[Candle], bucket: str) -> str:
    if len(candles) < 2:
        return "missing"
    latest = candles[-1]
    previous = candles[-2]
    if bucket == "bullish" and latest.close > previous.high and latest.close > latest.open:
        return "bullish reclaim"
    if bucket == "bearish" and latest.close < previous.low and latest.close < latest.open:
        return "bearish rejection"
    return "not confirmed"


def _median(values: list[int]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2
