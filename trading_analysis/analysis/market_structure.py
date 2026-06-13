from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_analysis.analysis.technical import atr
from trading_analysis.models import Candle


@dataclass(frozen=True)
class SwingPoint:
    index: int
    timestamp: datetime
    price: float
    kind: str


@dataclass(frozen=True)
class MarketStructure:
    trend: str
    support: float | None
    resistance: float | None
    invalidation: float | None
    range_low: float | None
    range_high: float | None
    last_swing_high: SwingPoint | None
    last_swing_low: SwingPoint | None
    score: int
    reasons: tuple[str, ...]


def analyze_market_structure(
    candles: list[Candle],
    swing_window: int = 2,
    range_lookback: int = 20,
) -> MarketStructure:
    if len(candles) < max(10, (swing_window * 2) + 1):
        raise ValueError("At least 10 candles are required for market structure analysis.")

    highs = find_swing_highs(candles, swing_window)
    lows = find_swing_lows(candles, swing_window)
    last_high = highs[-1] if highs else None
    last_low = lows[-1] if lows else None
    trend = classify_structure(highs, lows)
    range_slice = candles[-min(range_lookback, len(candles)) :]
    range_low = min(candle.low for candle in range_slice)
    range_high = max(candle.high for candle in range_slice)
    close = candles[-1].close
    support = nearest_support(lows, close) or range_low
    resistance = nearest_resistance(highs, close) or range_high
    invalidation = _invalidation(trend, support, resistance)
    score, reasons = _score_structure(candles, trend, support, resistance, range_low, range_high)

    return MarketStructure(
        trend=trend,
        support=support,
        resistance=resistance,
        invalidation=invalidation,
        range_low=range_low,
        range_high=range_high,
        last_swing_high=last_high,
        last_swing_low=last_low,
        score=score,
        reasons=tuple(reasons),
    )


def find_swing_highs(candles: list[Candle], window: int = 2) -> list[SwingPoint]:
    swings: list[SwingPoint] = []
    for index in range(window, len(candles) - window):
        current = candles[index]
        left = candles[index - window : index]
        right = candles[index + 1 : index + window + 1]
        if current.high > max(candle.high for candle in left) and current.high >= max(candle.high for candle in right):
            swings.append(SwingPoint(index, current.timestamp, current.high, "high"))
    return swings


def find_swing_lows(candles: list[Candle], window: int = 2) -> list[SwingPoint]:
    swings: list[SwingPoint] = []
    for index in range(window, len(candles) - window):
        current = candles[index]
        left = candles[index - window : index]
        right = candles[index + 1 : index + window + 1]
        if current.low < min(candle.low for candle in left) and current.low <= min(candle.low for candle in right):
            swings.append(SwingPoint(index, current.timestamp, current.low, "low"))
    return swings


def classify_structure(highs: list[SwingPoint], lows: list[SwingPoint]) -> str:
    if len(highs) >= 2 and len(lows) >= 2:
        higher_high = highs[-1].price > highs[-2].price
        higher_low = lows[-1].price > lows[-2].price
        lower_high = highs[-1].price < highs[-2].price
        lower_low = lows[-1].price < lows[-2].price
        if higher_high and higher_low:
            return "uptrend"
        if lower_high and lower_low:
            return "downtrend"
    return "range"


def nearest_support(lows: list[SwingPoint], close: float) -> float | None:
    below = [swing.price for swing in lows if swing.price <= close]
    return max(below) if below else None


def nearest_resistance(highs: list[SwingPoint], close: float) -> float | None:
    above = [swing.price for swing in highs if swing.price >= close]
    return min(above) if above else None


def _invalidation(trend: str, support: float | None, resistance: float | None) -> float | None:
    if trend == "uptrend":
        return support
    if trend == "downtrend":
        return resistance
    return None


def _score_structure(
    candles: list[Candle],
    trend: str,
    support: float | None,
    resistance: float | None,
    range_low: float,
    range_high: float,
) -> tuple[int, list[str]]:
    close = candles[-1].close
    atr14 = atr(candles, 14)
    score = 50
    reasons: list[str] = []

    if trend == "uptrend":
        score += 20
        reasons.append("Higher-high/higher-low structure")
    elif trend == "downtrend":
        score -= 20
        reasons.append("Lower-high/lower-low structure")
    else:
        reasons.append("Range structure")

    if resistance is not None and close > resistance:
        score += 15
        reasons.append("Close above swing resistance")
    if support is not None and close < support:
        score -= 15
        reasons.append("Close below swing support")

    range_width = range_high - range_low
    if atr14 and range_width <= atr14 * 4:
        reasons.append("Compressed recent range")
    if support is not None and close <= support * 1.01:
        reasons.append("Near support")
    if resistance is not None and close >= resistance * 0.99:
        reasons.append("Near resistance")

    return max(0, min(100, score)), reasons
