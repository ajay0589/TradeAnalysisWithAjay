from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from trading_analysis.analysis.technical import atr, ema
from trading_analysis.models import Candle


@dataclass(frozen=True)
class IndicatorRow:
    name: str
    signal: str
    value: str
    reference: str
    detail: str


@dataclass(frozen=True)
class IndicatorSuite:
    score: int
    bias: str
    summary: str
    rows: list[IndicatorRow]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_indicator_suite(candles: list[Candle]) -> IndicatorSuite:
    if len(candles) < 20:
        return IndicatorSuite(
            score=50,
            bias="insufficient",
            summary=f"Only {len(candles)} candles available; at least 20 are needed for the indicator suite.",
            rows=[],
            warnings=["Insufficient candles for indicator confluence."],
        )

    candles = sorted(candles, key=lambda candle: candle.timestamp)
    rows = [
        _volume_row(candles),
        _ema_cross_row(candles, 9, 26, "EMA Cross 9 / 26"),
        _vwap_row(candles),
        _chande_kroll_row(candles),
        _donchian_row(candles),
        _ichimoku_row(candles),
        _vwma_row(candles),
        _ema_cross_row(candles, 26, 89, "EMA Cross 89 / 26"),
    ]
    score = _suite_score(rows)
    bullish = sum(1 for row in rows if row.signal == "bullish")
    bearish = sum(1 for row in rows if row.signal == "bearish")
    neutral = sum(1 for row in rows if row.signal in {"neutral", "watch", "insufficient"})
    bias = _suite_bias(score, bullish, bearish)
    return IndicatorSuite(
        score=score,
        bias=bias,
        summary=f"{bias.title()} indicator confluence; {bullish} bullish, {bearish} bearish, {neutral} neutral/watch.",
        rows=rows,
        warnings=[],
    )


def _volume_row(candles: list[Candle]) -> IndicatorRow:
    latest = candles[-1]
    avg20 = _average([candle.volume for candle in candles[-20:]])
    if latest.volume <= 0 or avg20 <= 0:
        return IndicatorRow(
            name="Volume",
            signal="insufficient",
            value=f"{latest.volume:,}",
            reference="Volume unavailable",
            detail="Volume-based confirmation is unavailable for this instrument/timeframe.",
        )
    ratio = latest.volume / avg20 if avg20 else 0.0
    candle_bias = "bullish" if latest.close > latest.open else "bearish" if latest.close < latest.open else "neutral"
    if ratio >= 1.2 and candle_bias in {"bullish", "bearish"}:
        signal = candle_bias
        detail = f"Participation confirms the latest {candle_bias} candle."
    elif ratio <= 0.7:
        signal = "watch"
        detail = "Volume is below average; price move has weak participation."
    else:
        signal = "neutral"
        detail = "Volume is near average; no strong participation signal."
    return IndicatorRow(
        name="Volume",
        signal=signal,
        value=f"{latest.volume:,}",
        reference=f"{ratio:.2f}x 20-period average",
        detail=detail,
    )


def _ema_cross_row(candles: list[Candle], fast_period: int, slow_period: int, name: str) -> IndicatorRow:
    closes = [candle.close for candle in candles]
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)
    if fast is None or slow is None:
        return IndicatorRow(name, "insufficient", "-", f"Needs {slow_period} candles", "EMA cross is not available yet.")

    previous_fast = ema(closes[:-1], fast_period) if len(closes) > fast_period else None
    previous_slow = ema(closes[:-1], slow_period) if len(closes) > slow_period else None
    signal = "bullish" if fast > slow else "bearish" if fast < slow else "neutral"
    cross = ""
    if previous_fast is not None and previous_slow is not None:
        if previous_fast <= previous_slow and fast > slow:
            cross = " Fresh bullish crossover."
        elif previous_fast >= previous_slow and fast < slow:
            cross = " Fresh bearish crossover."
    fast_label = "9 EMA" if fast_period == 9 else f"{fast_period} EMA"
    slow_label = "89 EMA" if slow_period == 89 else f"{slow_period} EMA"
    return IndicatorRow(
        name=name,
        signal=signal,
        value=f"{fast:.2f} / {slow:.2f}",
        reference=f"{fast_label} vs {slow_label}",
        detail=f"{fast_label} is {'above' if fast > slow else 'below' if fast < slow else 'equal to'} {slow_label}.{cross}".strip(),
    )


def _vwap_row(candles: list[Candle]) -> IndicatorRow:
    latest = candles[-1]
    session = [candle for candle in candles if candle.timestamp.date() == latest.timestamp.date()]
    total_volume = sum(candle.volume for candle in session)
    if total_volume <= 0:
        return IndicatorRow("VWAP hlc3 Session", "insufficient", "-", "Session volume unavailable", "VWAP cannot be calculated.")
    vwap = sum(_typical_price(candle) * candle.volume for candle in session) / total_volume
    signal = "bullish" if latest.close > vwap else "bearish" if latest.close < vwap else "neutral"
    detail = "Price is above session VWAP; buyers have intraday control." if signal == "bullish" else (
        "Price is below session VWAP; sellers have intraday control." if signal == "bearish" else "Price is at VWAP."
    )
    if len(session) == 1:
        detail += " On higher timeframes this is a single-candle approximation."
    return IndicatorRow(
        name="VWAP hlc3 Session",
        signal=signal,
        value=f"{vwap:.2f}",
        reference="hlc3 weighted by session volume",
        detail=detail,
    )


def _chande_kroll_row(candles: list[Candle], atr_period: int = 10, atr_multiplier: float = 1.0, stop_period: int = 9) -> IndicatorRow:
    if len(candles) < atr_period + stop_period:
        return IndicatorRow("Chande Kroll Stop 10 1 9", "insufficient", "-", "Needs 19 candles", "Chande Kroll stop is not available yet.")

    long_candidates: list[float] = []
    short_candidates: list[float] = []
    for end in range(len(candles) - stop_period + 1, len(candles) + 1):
        window = candles[:end]
        atr_value = atr(window, atr_period)
        if atr_value is None or len(window) < atr_period:
            continue
        period_slice = window[-atr_period:]
        long_candidates.append(max(candle.high for candle in period_slice) - (atr_multiplier * atr_value))
        short_candidates.append(min(candle.low for candle in period_slice) + (atr_multiplier * atr_value))
    if not long_candidates or not short_candidates:
        return IndicatorRow("Chande Kroll Stop 10 1 9", "insufficient", "-", "ATR unavailable", "Chande Kroll stop is not available yet.")

    long_stop = max(long_candidates)
    short_stop = min(short_candidates)
    close = candles[-1].close
    if close > long_stop and close > short_stop:
        signal = "bullish"
        detail = "Close is above the volatility stop zone; trailing bias is bullish."
    elif close < long_stop and close < short_stop:
        signal = "bearish"
        detail = "Close is below the volatility stop zone; trailing bias is bearish."
    else:
        signal = "neutral"
        detail = "Close is inside the stop zone; trend/invalidation is mixed."
    return IndicatorRow(
        name="Chande Kroll Stop 10 1 9",
        signal=signal,
        value=f"{long_stop:.2f} / {short_stop:.2f}",
        reference="ATR-based trailing stop zone",
        detail=detail,
    )


def _donchian_row(candles: list[Candle], period: int = 20) -> IndicatorRow:
    if len(candles) < period:
        return IndicatorRow("DC 20 0", "insufficient", "-", "Needs 20 candles", "Donchian Channel is not available yet.")
    current = candles[-period:]
    upper = max(candle.high for candle in current)
    lower = min(candle.low for candle in current)
    middle = (upper + lower) / 2
    previous_upper = max(candle.high for candle in candles[-period - 1 : -1]) if len(candles) > period else upper
    previous_lower = min(candle.low for candle in candles[-period - 1 : -1]) if len(candles) > period else lower
    close = candles[-1].close
    if close > previous_upper:
        signal = "bullish"
        detail = f"Close broke above the previous 20-period high {previous_upper:.2f}."
    elif close < previous_lower:
        signal = "bearish"
        detail = f"Close broke below the previous 20-period low {previous_lower:.2f}."
    else:
        signal = "neutral"
        detail = "Close remains inside the 20-period channel."
    return IndicatorRow(
        name="DC 20 0",
        signal=signal,
        value=f"{upper:.2f} / {middle:.2f} / {lower:.2f}",
        reference="Upper / middle / lower",
        detail=detail,
    )


def _ichimoku_row(candles: list[Candle]) -> IndicatorRow:
    if len(candles) < 52:
        return IndicatorRow("Ichimoku 9 26 52 26 26", "insufficient", "-", "Needs 52 candles", "Ichimoku cloud is not available yet.")
    tenkan = _midpoint(candles, 9)
    kijun = _midpoint(candles, 26)
    span_a = (tenkan + kijun) / 2
    span_b = _midpoint(candles, 52)
    cloud_top = max(span_a, span_b)
    cloud_bottom = min(span_a, span_b)
    close = candles[-1].close
    if close > cloud_top:
        signal = "bullish"
        detail = "Price is above the cloud; bullish regime."
    elif close < cloud_bottom:
        signal = "bearish"
        detail = "Price is below the cloud; bearish regime."
    else:
        signal = "neutral"
        detail = "Price is inside the cloud; regime is sideways/uncertain."
    future_bias = "bullish" if span_a > span_b else "bearish" if span_a < span_b else "flat"
    chikou_detail = ""
    if len(candles) > 26:
        chikou_detail = " Chikou confirms." if close > candles[-27].close and signal == "bullish" else (
            " Chikou is weak." if close < candles[-27].close and signal == "bearish" else ""
        )
    return IndicatorRow(
        name="Ichimoku 9 26 52 26 26",
        signal=signal,
        value=f"{tenkan:.2f} / {kijun:.2f} / {span_a:.2f} / {span_b:.2f}",
        reference=f"Tenkan / Kijun / Span A / Span B; future cloud {future_bias}",
        detail=f"{detail}{chikou_detail}",
    )


def _vwma_row(candles: list[Candle], period: int = 20) -> IndicatorRow:
    if len(candles) < period:
        return IndicatorRow("VWMA 20", "insufficient", "-", "Needs 20 candles", "VWMA is not available yet.")
    window = candles[-period:]
    total_volume = sum(candle.volume for candle in window)
    if total_volume <= 0:
        return IndicatorRow("VWMA 20", "insufficient", "-", "Volume unavailable", "VWMA cannot be calculated.")
    vwma = sum(candle.close * candle.volume for candle in window) / total_volume
    close = candles[-1].close
    signal = "bullish" if close > vwma else "bearish" if close < vwma else "neutral"
    return IndicatorRow(
        name="VWMA 20",
        signal=signal,
        value=f"{vwma:.2f}",
        reference="20-period volume weighted moving average",
        detail=f"Close is {'above' if close > vwma else 'below' if close < vwma else 'at'} VWMA20.",
    )


def _suite_score(rows: list[IndicatorRow]) -> int:
    score = 50
    for row in rows:
        if row.signal == "bullish":
            score += 7
        elif row.signal == "bearish":
            score -= 7
        elif row.signal == "watch":
            score -= 2
    return max(0, min(100, score))


def _suite_bias(score: int, bullish: int, bearish: int) -> str:
    if score >= 62 and bullish > bearish:
        return "bullish"
    if score <= 38 and bearish > bullish:
        return "bearish"
    return "neutral"


def _midpoint(candles: list[Candle], period: int) -> float:
    window = candles[-period:]
    return (max(candle.high for candle in window) + min(candle.low for candle in window)) / 2


def _typical_price(candle: Candle) -> float:
    return (candle.high + candle.low + candle.close) / 3


def _average(values: list[int] | list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
