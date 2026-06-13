from __future__ import annotations

from trading_analysis.models import Candle, TechnicalSignal


def analyze_technical(candles: list[Candle]) -> TechnicalSignal:
    if len(candles) < 20:
        raise ValueError("At least 20 candles are required for the default technical analysis.")

    closes = [candle.close for candle in candles]
    highs = [candle.high for candle in candles]
    lows = [candle.low for candle in candles]
    volumes = [candle.volume for candle in candles]

    close = closes[-1]
    sma20_value = sma(closes, 20)
    ema20_value = ema(closes, 20)
    rsi14_value = rsi(closes, 14)
    atr14_value = atr(candles, 14)
    support20 = min(lows[-20:])
    resistance20 = max(highs[-20:])
    avg_volume20 = sum(volumes[-20:]) / 20
    volume_ratio20 = volumes[-1] / avg_volume20 if avg_volume20 else None

    trend = _trend(close, ema20_value, rsi14_value)
    score, reasons = _score(
        close=close,
        sma20_value=sma20_value,
        ema20_value=ema20_value,
        rsi14_value=rsi14_value,
        volume_ratio20=volume_ratio20,
        resistance20=resistance20,
        trend=trend,
    )

    return TechnicalSignal(
        close=close,
        sma20=sma20_value,
        ema20=ema20_value,
        rsi14=rsi14_value,
        atr14=atr14_value,
        volume_ratio20=volume_ratio20,
        support20=support20,
        resistance20=resistance20,
        trend=trend,
        score=score,
        reasons=tuple(reasons),
    )


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    current = sum(values[:period]) / period
    for value in values[period:]:
        current = (value - current) * multiplier + current
    return current


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None

    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    seed = deltas[:period]
    avg_gain = sum(max(delta, 0) for delta in seed) / period
    avg_loss = sum(abs(min(delta, 0)) for delta in seed) / period

    for delta in deltas[period:]:
        gain = max(delta, 0)
        loss = abs(min(delta, 0))
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

    if avg_loss == 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100 - (100 / (1 + relative_strength))


def atr(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) <= period:
        return None

    true_ranges: list[float] = []
    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )

    if len(true_ranges) < period:
        return None
    return sum(true_ranges[-period:]) / period


def _trend(close: float, ema20_value: float | None, rsi14_value: float | None) -> str:
    if ema20_value is None or rsi14_value is None:
        return "unknown"
    if close > ema20_value and rsi14_value >= 55:
        return "bullish"
    if close < ema20_value and rsi14_value <= 45:
        return "bearish"
    return "neutral"


def _score(
    close: float,
    sma20_value: float | None,
    ema20_value: float | None,
    rsi14_value: float | None,
    volume_ratio20: float | None,
    resistance20: float | None,
    trend: str,
) -> tuple[int, list[str]]:
    score = 50
    reasons: list[str] = []

    if trend == "bullish":
        score += 15
        reasons.append("Bullish price/RSI trend")
    elif trend == "bearish":
        score -= 15
        reasons.append("Bearish price/RSI trend")

    if sma20_value is not None:
        if close > sma20_value:
            score += 10
            reasons.append("Close above SMA20")
        else:
            score -= 10
            reasons.append("Close below SMA20")

    if ema20_value is not None and close > ema20_value:
        score += 5
        reasons.append("Close above EMA20")

    if rsi14_value is not None:
        if 50 <= rsi14_value <= 68:
            score += 10
            reasons.append("RSI in constructive zone")
        elif rsi14_value > 75:
            score -= 8
            reasons.append("RSI extended")
        elif rsi14_value < 35:
            score -= 10
            reasons.append("RSI weak")

    if volume_ratio20 is not None and volume_ratio20 >= 1.2:
        score += 8
        reasons.append("Volume expansion")

    if resistance20 is not None and close >= resistance20 * 0.995:
        score += 7
        reasons.append("Closing near 20-period resistance")

    return max(0, min(100, score)), reasons

