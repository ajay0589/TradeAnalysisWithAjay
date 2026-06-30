from __future__ import annotations

from trading_analysis.analysis.market_structure import analyze_market_structure
from trading_analysis.analysis.technical import atr, ema, rsi, sma
from trading_analysis.models import Candle
from trading_analysis.nifty.models import NiftyTechnicalContext


def build_nifty_technical_context(
    daily_candles: list[Candle],
    hourly_candles: list[Candle] | None = None,
    minute15_candles: list[Candle] | None = None,
    minute5_candles: list[Candle] | None = None,
    mode: str = "auto",
) -> NiftyTechnicalContext:
    warnings: list[str] = []
    notes: list[str] = []
    if not daily_candles:
        raise ValueError("NIFTY daily candles are required for technical context.")

    normalized_mode = (mode or "auto").lower()
    primary, timeframe = _primary_candles(normalized_mode, daily_candles, hourly_candles, minute15_candles, minute5_candles)
    if len(primary) < 20:
        warnings.append(f"{timeframe} context has only {len(primary)} candle(s); at least 20 is preferred.")

    closes = [candle.close for candle in primary]
    latest = primary[-1]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    sma200 = sma(closes, 200)
    rsi14 = rsi(closes, 14)
    atr14 = atr(primary, 14)
    vwap = _vwap(minute5_candles or minute15_candles or hourly_candles or [])
    structure = _safe_structure(primary, warnings)
    daily_structure = _safe_structure(daily_candles, warnings)

    previous_day_high, previous_day_low, previous_day_close, day_open = _previous_day_levels(daily_candles)
    support_levels = _dedupe_levels(
        [
            structure.support if structure else None,
            structure.range_low if structure else None,
            previous_day_low,
            daily_structure.support if daily_structure else None,
        ]
    )
    resistance_levels = _dedupe_levels(
        [
            structure.resistance if structure else None,
            structure.range_high if structure else None,
            previous_day_high,
            daily_structure.resistance if daily_structure else None,
        ]
    )
    trend = _trend(latest.close, ema20, ema50, rsi14, structure.trend if structure else "range")
    intraday_bias = _bias(latest.close, ema20, ema50, rsi14, vwap, structure.trend if structure else "range")
    swing_bias = _bias(daily_candles[-1].close, ema([c.close for c in daily_candles], 20), ema([c.close for c in daily_candles], 50), rsi([c.close for c in daily_candles], 14), None, daily_structure.trend if daily_structure else "range")
    positional_bias = _positional_bias(daily_candles, daily_structure.trend if daily_structure else "range")
    candle_signal = _candle_signal(latest, atr14)
    factors = _technical_factors(
        latest=latest,
        ema20=ema20,
        ema50=ema50,
        sma200=sma200,
        rsi14=rsi14,
        atr14=atr14,
        vwap=vwap,
        structure=structure.trend if structure else "unknown",
        previous_day_high=previous_day_high,
        previous_day_low=previous_day_low,
        candle_signal=candle_signal,
    )

    if previous_day_high and previous_day_low and previous_day_close:
        pivot = (previous_day_high + previous_day_low + previous_day_close) / 3
        bc = (previous_day_high + previous_day_low) / 2
        tc = (2 * pivot) - bc
        notes.append(f"CPR context: pivot {pivot:.2f}, BC {bc:.2f}, TC {tc:.2f}.")
    if atr14 and len(primary) >= 2:
        last_range = primary[-1].high - primary[-1].low
        if last_range > atr14 * 1.5:
            notes.append("Latest candle range is expanded versus ATR.")

    return NiftyTechnicalContext(
        symbol="NIFTY",
        as_of=latest.timestamp,
        spot=latest.close,
        timeframe=timeframe,
        trend_intraday=trend if timeframe in {"5minute", "15minute", "60minute"} else intraday_bias,
        trend_swing=swing_bias,
        trend_positional=positional_bias,
        bias_intraday=intraday_bias,
        bias_swing=swing_bias,
        bias_positional=positional_bias,
        support_levels=support_levels,
        resistance_levels=resistance_levels,
        vwap=vwap,
        ema20=ema20,
        ema50=ema50,
        sma200=sma200,
        rsi14=rsi14,
        atr14=atr14,
        previous_day_high=previous_day_high,
        previous_day_low=previous_day_low,
        previous_day_close=previous_day_close,
        day_open=day_open,
        candle_signal=candle_signal,
        market_structure=structure.trend if structure else "unknown",
        factors=factors,
        notes=notes,
        warnings=warnings,
    )


def _primary_candles(
    mode: str,
    daily: list[Candle],
    hourly: list[Candle] | None,
    minute15: list[Candle] | None,
    minute5: list[Candle] | None,
) -> tuple[list[Candle], str]:
    if mode == "intraday":
        if minute5:
            return minute5, "5minute"
        if minute15:
            return minute15, "15minute"
        if hourly:
            return hourly, "60minute"
    if mode == "swing" and hourly:
        return hourly, "60minute"
    return daily, "day"


def _safe_structure(candles: list[Candle], warnings: list[str]):
    try:
        return analyze_market_structure(candles)
    except ValueError as exc:
        warnings.append(str(exc))
        return None


def _previous_day_levels(candles: list[Candle]) -> tuple[float | None, float | None, float | None, float | None]:
    if len(candles) < 2:
        return None, None, None, candles[-1].open if candles else None
    previous = candles[-2]
    latest = candles[-1]
    return previous.high, previous.low, previous.close, latest.open


def _dedupe_levels(values: list[float | None]) -> list[float]:
    output: list[float] = []
    for value in values:
        if value is None:
            continue
        rounded = round(float(value), 2)
        if rounded not in output:
            output.append(rounded)
    return sorted(output)


def _vwap(candles: list[Candle]) -> float | None:
    if not candles:
        return None
    latest_date = candles[-1].timestamp.date()
    session = [candle for candle in candles if candle.timestamp.date() == latest_date]
    volume = sum(candle.volume for candle in session)
    if volume <= 0:
        return None
    return sum(((candle.high + candle.low + candle.close) / 3) * candle.volume for candle in session) / volume


def _trend(close: float, ema20: float | None, ema50: float | None, rsi14: float | None, structure: str) -> str:
    if ema20 is None or ema50 is None or rsi14 is None:
        return "unclear"
    if close > ema20 and ema20 >= ema50 and rsi14 >= 52 and structure != "downtrend":
        return "bullish"
    if close < ema20 and ema20 <= ema50 and rsi14 <= 48 and structure != "uptrend":
        return "bearish"
    if 40 <= rsi14 <= 60:
        return "neutral"
    return "unclear"


def _bias(close: float, ema20: float | None, ema50: float | None, rsi14: float | None, vwap: float | None, structure: str) -> str:
    trend = _trend(close, ema20, ema50, rsi14, structure)
    if vwap is not None and trend == "bullish" and close < vwap:
        return "unclear"
    if vwap is not None and trend == "bearish" and close > vwap:
        return "unclear"
    return trend


def _positional_bias(candles: list[Candle], structure: str) -> str:
    closes = [candle.close for candle in candles]
    ema20_value = ema(closes, 20)
    sma200_value = sma(closes, 200)
    rsi14_value = rsi(closes, 14)
    close = closes[-1]
    if sma200_value and close > sma200_value and ema20_value and close > ema20_value and structure != "downtrend":
        return "bullish"
    if sma200_value and close < sma200_value and ema20_value and close < ema20_value and structure != "uptrend":
        return "bearish"
    if rsi14_value and 40 <= rsi14_value <= 60:
        return "neutral"
    return "unclear"


def _candle_signal(candle: Candle, atr14: float | None) -> str:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return "flat"
    location = (candle.close - candle.low) / candle_range
    body = abs(candle.close - candle.open)
    if atr14 and body > atr14 * 1.2:
        return "momentum_up" if candle.close > candle.open else "momentum_down"
    if location >= 0.7:
        return "close_near_high"
    if location <= 0.3:
        return "close_near_low"
    return "balanced"


def _technical_factors(
    latest: Candle,
    ema20: float | None,
    ema50: float | None,
    sma200: float | None,
    rsi14: float | None,
    atr14: float | None,
    vwap: float | None,
    structure: str,
    previous_day_high: float | None,
    previous_day_low: float | None,
    candle_signal: str,
) -> list[dict[str, str]]:
    close = latest.close
    return [
        _factor("Price vs EMA20", _level_read(close, ema20), f"Close {close:.2f}, EMA20 {_fmt(ema20)}", "Short-term trend filter."),
        _factor("EMA20 vs EMA50", _cross_read(ema20, ema50), f"EMA20 {_fmt(ema20)}, EMA50 {_fmt(ema50)}", "Trend alignment."),
        _factor("Price vs SMA200", _level_read(close, sma200), f"Close {close:.2f}, SMA200 {_fmt(sma200)}", "Positional regime filter."),
        _factor("RSI14", _rsi_read(rsi14), _fmt(rsi14), "Momentum zone: 40-60 neutral, above 52 bullish, below 48 bearish."),
        _factor("VWAP", _level_read(close, vwap), f"Close {close:.2f}, VWAP {_fmt(vwap)}", "Intraday control when intraday candles are available."),
        _factor("Market Structure", structure, structure, "Swing high/low structure: uptrend, downtrend, or range."),
        _factor("Previous Day Range", _previous_day_read(close, previous_day_high, previous_day_low), f"PDH {_fmt(previous_day_high)}, PDL {_fmt(previous_day_low)}", "Shows whether price is inside or outside yesterday's range."),
        _factor("ATR14", "available" if atr14 else "missing", _fmt(atr14), "Volatility context and stop/zone sizing."),
        _factor("Latest Candle", candle_signal, candle_signal, "Close location and candle momentum context."),
    ]


def _factor(name: str, signal: str, value: str, purpose: str) -> dict[str, str]:
    return {"factor": name, "signal": signal, "value": value, "purpose": purpose}


def _level_read(close: float, level: float | None) -> str:
    if level is None:
        return "missing"
    if close > level:
        return "bullish"
    if close < level:
        return "bearish"
    return "neutral"


def _cross_read(fast: float | None, slow: float | None) -> str:
    if fast is None or slow is None:
        return "missing"
    if fast > slow:
        return "bullish"
    if fast < slow:
        return "bearish"
    return "neutral"


def _rsi_read(value: float | None) -> str:
    if value is None:
        return "missing"
    if value >= 65:
        return "bullish_extended"
    if value >= 52:
        return "bullish"
    if value <= 35:
        return "bearish_extended"
    if value <= 48:
        return "bearish"
    return "neutral"


def _previous_day_read(close: float, high: float | None, low: float | None) -> str:
    if high is None or low is None:
        return "missing"
    if close > high:
        return "bullish_breakout"
    if close < low:
        return "bearish_breakdown"
    return "inside_range"


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"
