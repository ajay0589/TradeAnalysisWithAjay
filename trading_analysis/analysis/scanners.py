from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import sqrt
from typing import Any

from trading_analysis.analysis.market_structure import MarketStructure, analyze_market_structure
from trading_analysis.analysis.technical import atr, ema, rsi, sma
from trading_analysis.models import Candle


SETUP_LABELS = {
    "bullish_trend": "Bullish trend continuation",
    "bullish_breakout": "Bullish breakout",
    "bullish_pullback": "Bullish pullback to support",
    "bearish_trend": "Bearish trend continuation",
    "bearish_breakdown": "Bearish breakdown",
    "bearish_pullback": "Bearish pullback to resistance",
    "neutral_range": "Neutral range-bound",
    "compression": "Volatility compression watch",
    "avoid": "Avoid / choppy",
}


@dataclass(frozen=True)
class ScannerConfig:
    min_candles: int = 20
    sma_fast_period: int = 20
    sma_mid_period: int = 50
    sma_slow_period: int = 200
    ema_period: int = 20
    rsi_period: int = 14
    atr_period: int = 14
    donchian_fast_period: int = 20
    donchian_slow_period: int = 55
    bollinger_period: int = 20
    bollinger_stddev: float = 2.0
    bollinger_width_lookback: int = 120
    breakout_volume_ratio: float = 1.3
    volume_confirmation_ratio: float = 1.0
    strong_volume_ratio: float = 1.2
    compression_percentile_threshold: float = 25.0
    neutral_range_atr_multiple: float = 4.0
    compression_range_atr_multiple: float = 3.0
    pullback_near_level_atr: float = 0.85
    very_low_volume_ratio: float = 0.35
    far_from_level_atr: float = 3.0
    large_move_atr: float = 2.0


@dataclass(frozen=True)
class IndicatorSnapshot:
    close: float
    sma20: float | None
    sma50: float | None
    sma200: float | None
    ema20: float | None
    rsi14: float | None
    atr14: float | None
    donchian_high20: float | None
    donchian_low20: float | None
    donchian_high55: float | None
    donchian_low55: float | None
    previous_high20: float | None
    previous_low20: float | None
    previous_high55: float | None
    previous_low55: float | None
    bollinger_upper20: float | None
    bollinger_mid20: float | None
    bollinger_lower20: float | None
    bollinger_width20: float | None
    bollinger_width_percentile120: float | None
    average_volume20: float | None
    volume_ratio20: float | None
    candle_body_percent: float
    close_location: float
    return5: float | None
    return10: float | None
    return20: float | None
    support: float | None
    resistance: float | None
    range_low: float | None
    range_high: float | None
    range_width_atr: float | None
    support_distance_atr: float | None
    resistance_distance_atr: float | None
    structure_trend: str | None
    previous_rsi14: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScanMatch:
    symbol: str
    setup_type: str
    direction: str
    score: int
    confidence: str
    close: float | None
    support: float | None
    resistance: float | None
    invalidation: float | None
    target_zone: str | None
    trigger_zone: str | None
    risk_level: str
    risk_reward_comment: str
    reasons: list[str]
    indicators: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScanResult:
    symbol: str
    matches: list[ScanMatch]
    errors: list[str] = field(default_factory=list)


def scan_symbol_for_setups(
    symbol: str,
    candles: list[Candle],
    structure: MarketStructure | None = None,
    relative_strength: dict[str, Any] | Any | None = None,
    config: ScannerConfig | None = None,
) -> list[ScanMatch]:
    """Classify cached candles into deterministic opportunity setups."""
    config = config or ScannerConfig()
    symbol = symbol.upper()
    candles = sorted(candles, key=lambda candle: candle.timestamp)

    if len(candles) < config.min_candles:
        return [
            _avoid_match(
                symbol=symbol,
                candles=candles,
                snapshot=None,
                reasons=[f"Insufficient candles: {len(candles)} available, {config.min_candles} required."],
                config=config,
            )
        ]

    if structure is None and len(candles) >= 10:
        structure = analyze_market_structure(candles)
    snapshot = _indicator_snapshot(candles, structure, config)

    matches = [
        _bullish_trend(symbol, candles, structure, snapshot, relative_strength, config),
        _bullish_breakout(symbol, candles, structure, snapshot, config),
        _bullish_pullback(symbol, candles, structure, snapshot, relative_strength, config),
        _bearish_trend(symbol, candles, structure, snapshot, relative_strength, config),
        _bearish_breakdown(symbol, candles, structure, snapshot, config),
        _bearish_pullback(symbol, candles, structure, snapshot, relative_strength, config),
        _neutral_range(symbol, structure, snapshot, config),
        _compression(symbol, snapshot, config),
    ]
    output = [match for match in matches if match is not None]
    if output:
        return sorted(output, key=lambda match: match.score, reverse=True)

    return [_avoid_match(symbol=symbol, candles=candles, snapshot=snapshot, reasons=[], config=config)]


def _indicator_snapshot(
    candles: list[Candle],
    structure: MarketStructure | None,
    config: ScannerConfig,
) -> IndicatorSnapshot:
    latest = candles[-1]
    closes = [candle.close for candle in candles]
    highs = [candle.high for candle in candles]
    lows = [candle.low for candle in candles]
    volumes = [candle.volume for candle in candles]
    high_low_range = latest.high - latest.low

    sma20 = sma(closes, config.sma_fast_period)
    sma50 = sma(closes, config.sma_mid_period)
    sma200 = sma(closes, config.sma_slow_period)
    ema20 = ema(closes, config.ema_period)
    rsi14 = rsi(closes, config.rsi_period)
    previous_rsi14 = rsi(closes[:-1], config.rsi_period) if len(closes) > config.rsi_period + 1 else None
    atr14 = atr(candles, config.atr_period)
    bb_upper, bb_mid, bb_lower, bb_width = _bollinger(closes, config)
    avg_volume20 = _average(volumes[-config.sma_fast_period :]) if len(volumes) >= config.sma_fast_period else None
    volume_ratio20 = latest.volume / avg_volume20 if avg_volume20 else None

    support = structure.support if structure else None
    resistance = structure.resistance if structure else None
    range_low = structure.range_low if structure else _donchian_low(candles, config.donchian_fast_period, False)
    range_high = structure.range_high if structure else _donchian_high(candles, config.donchian_fast_period, False)
    range_width = (range_high - range_low) if range_low is not None and range_high is not None else None

    return IndicatorSnapshot(
        close=latest.close,
        sma20=sma20,
        sma50=sma50,
        sma200=sma200,
        ema20=ema20,
        rsi14=rsi14,
        atr14=atr14,
        donchian_high20=_donchian_high(candles, config.donchian_fast_period, False),
        donchian_low20=_donchian_low(candles, config.donchian_fast_period, False),
        donchian_high55=_donchian_high(candles, config.donchian_slow_period, False),
        donchian_low55=_donchian_low(candles, config.donchian_slow_period, False),
        previous_high20=_donchian_high(candles, config.donchian_fast_period, True),
        previous_low20=_donchian_low(candles, config.donchian_fast_period, True),
        previous_high55=_donchian_high(candles, config.donchian_slow_period, True),
        previous_low55=_donchian_low(candles, config.donchian_slow_period, True),
        bollinger_upper20=bb_upper,
        bollinger_mid20=bb_mid,
        bollinger_lower20=bb_lower,
        bollinger_width20=bb_width,
        bollinger_width_percentile120=_bollinger_width_percentile(closes, config),
        average_volume20=avg_volume20,
        volume_ratio20=volume_ratio20,
        candle_body_percent=(abs(latest.close - latest.open) / high_low_range) * 100 if high_low_range else 0.0,
        close_location=(latest.close - latest.low) / high_low_range if high_low_range else 0.5,
        return5=_period_return(closes, 5),
        return10=_period_return(closes, 10),
        return20=_period_return(closes, 20),
        support=support,
        resistance=resistance,
        range_low=range_low,
        range_high=range_high,
        range_width_atr=(range_width / atr14) if atr14 and range_width is not None else None,
        support_distance_atr=((latest.close - support) / atr14) if atr14 and support is not None else None,
        resistance_distance_atr=((resistance - latest.close) / atr14) if atr14 and resistance is not None else None,
        structure_trend=structure.trend if structure else None,
        previous_rsi14=previous_rsi14,
    )


def _bullish_trend(
    symbol: str,
    candles: list[Candle],
    structure: MarketStructure | None,
    snapshot: IndicatorSnapshot,
    relative_strength: dict[str, Any] | Any | None,
    config: ScannerConfig,
) -> ScanMatch | None:
    if not all([snapshot.sma50, snapshot.ema20, snapshot.rsi14]):
        return None
    if snapshot.close <= snapshot.sma50 or snapshot.close <= snapshot.ema20:
        return None
    if snapshot.sma20 is not None and snapshot.sma20 <= snapshot.sma50:
        return None
    if not 52 <= snapshot.rsi14 <= 70:
        return None
    above_swing_high = _above_previous_swing_high(structure, snapshot.close)
    if snapshot.structure_trend != "uptrend" and not above_swing_high:
        return None

    score = 62
    reasons = [
        "Close is above SMA50 and EMA20.",
        "SMA20 is above SMA50." if snapshot.sma20 and snapshot.sma20 > snapshot.sma50 else "Price trend is above the mid-term average.",
        f"RSI {snapshot.rsi14:.1f} is in a constructive bullish zone.",
        "Market structure is uptrend." if snapshot.structure_trend == "uptrend" else "Close is above the previous swing high.",
    ]
    if snapshot.volume_ratio20 is not None and snapshot.volume_ratio20 >= config.volume_confirmation_ratio:
        score += 6
        reasons.append(f"Volume is confirming at {snapshot.volume_ratio20:.2f}x the 20-period average.")
    if snapshot.volume_ratio20 is not None and snapshot.volume_ratio20 >= config.strong_volume_ratio:
        score += 5
        reasons.append("Volume expansion is stronger than normal.")
    score += _relative_strength_bonus(relative_strength, bullish=True, reasons=reasons)
    invalidation = _bullish_invalidation(structure, snapshot)
    return _make_match(
        symbol,
        "bullish_trend",
        "bullish",
        score,
        snapshot,
        invalidation,
        target_zone=_zone_above(snapshot.resistance),
        trigger_zone="Trend continuation while price holds above EMA20/SMA50.",
        reasons=reasons,
    )


def _bullish_breakout(
    symbol: str,
    candles: list[Candle],
    structure: MarketStructure | None,
    snapshot: IndicatorSnapshot,
    config: ScannerConfig,
) -> ScanMatch | None:
    if not all([snapshot.previous_high20, snapshot.volume_ratio20, snapshot.rsi14, snapshot.atr14]):
        return None
    if snapshot.close <= snapshot.previous_high20:
        return None
    if snapshot.volume_ratio20 < config.breakout_volume_ratio:
        return None
    if not 55 <= snapshot.rsi14 <= 75:
        return None
    if snapshot.close_location < 0.70:
        return None

    score = 64
    reasons = [
        f"Close broke the previous 20-period high at {snapshot.previous_high20:.2f}.",
        f"Breakout volume is {snapshot.volume_ratio20:.2f}x the 20-period average.",
        f"RSI {snapshot.rsi14:.1f} supports breakout momentum.",
        "Candle closed in the top 30% of its range.",
    ]
    if snapshot.previous_high55 is not None and snapshot.close > snapshot.previous_high55:
        score += 8
        reasons.append("Close also cleared the previous 55-period high.")
    if _prior_range_width_atr(candles, config) is not None and _prior_range_width_atr(candles, config) <= config.compression_range_atr_multiple:
        score += 8
        reasons.append("Prior candles were compressed before the breakout.")

    breakout_level = snapshot.previous_high20
    swing_low = _last_swing_low(structure)
    invalidation = swing_low if swing_low is not None and swing_low >= breakout_level * 0.98 else breakout_level
    return _make_match(
        symbol,
        "bullish_breakout",
        "bullish",
        score,
        snapshot,
        invalidation,
        target_zone=_zone_above(snapshot.resistance),
        trigger_zone=f"Breakout above {breakout_level:.2f}; retest hold improves quality.",
        reasons=reasons,
    )


def _bullish_pullback(
    symbol: str,
    candles: list[Candle],
    structure: MarketStructure | None,
    snapshot: IndicatorSnapshot,
    relative_strength: dict[str, Any] | Any | None,
    config: ScannerConfig,
) -> ScanMatch | None:
    if not all([snapshot.rsi14, snapshot.atr14]):
        return None
    bullish_trend = (
        snapshot.sma50 is not None
        and snapshot.close > snapshot.sma50
        or snapshot.sma20 is not None
        and snapshot.sma50 is not None
        and snapshot.sma20 > snapshot.sma50
    )
    if not bullish_trend:
        return None
    if snapshot.structure_trend == "downtrend" and snapshot.sma50 and snapshot.close < snapshot.sma50:
        return None
    if not 40 <= snapshot.rsi14 <= 55:
        return None
    if snapshot.support is not None and snapshot.close <= snapshot.support:
        return None

    nearest = _nearest_level_distance(snapshot, [snapshot.ema20, snapshot.sma20, snapshot.support])
    if nearest is None or nearest > config.pullback_near_level_atr:
        return None

    score = 58
    reasons = [
        "Higher/same timeframe trend remains bullish.",
        f"Price is {nearest:.2f} ATR from EMA20/SMA20/support.",
        f"RSI {snapshot.rsi14:.1f} is in a pullback zone.",
        "Latest close remains above support." if snapshot.support is not None else "Support level was not available.",
    ]
    if _turning_up(candles, snapshot):
        score += 8
        reasons.append("Latest candle/RSI is turning up from the pullback.")
    if snapshot.structure_trend == "uptrend":
        score += 7
        reasons.append("Swing structure still shows an uptrend.")
    score += _relative_strength_bonus(relative_strength, bullish=True, reasons=reasons)
    return _make_match(
        symbol,
        "bullish_pullback",
        "bullish",
        score,
        snapshot,
        _bullish_invalidation(structure, snapshot),
        target_zone=_zone_above(snapshot.resistance),
        trigger_zone="Reclaim/hold of EMA20, SMA20, or support with volume confirmation.",
        reasons=reasons,
    )


def _bearish_trend(
    symbol: str,
    candles: list[Candle],
    structure: MarketStructure | None,
    snapshot: IndicatorSnapshot,
    relative_strength: dict[str, Any] | Any | None,
    config: ScannerConfig,
) -> ScanMatch | None:
    if not all([snapshot.sma50, snapshot.ema20, snapshot.rsi14]):
        return None
    if snapshot.close >= snapshot.sma50 or snapshot.close >= snapshot.ema20:
        return None
    if snapshot.sma20 is not None and snapshot.sma20 >= snapshot.sma50:
        return None
    if not 30 <= snapshot.rsi14 <= 48:
        return None
    below_swing_low = _below_previous_swing_low(structure, snapshot.close)
    if snapshot.structure_trend != "downtrend" and not below_swing_low:
        return None

    score = 62
    reasons = [
        "Close is below SMA50 and EMA20.",
        "SMA20 is below SMA50." if snapshot.sma20 and snapshot.sma20 < snapshot.sma50 else "Price trend is below the mid-term average.",
        f"RSI {snapshot.rsi14:.1f} is in a bearish continuation zone.",
        "Market structure is downtrend." if snapshot.structure_trend == "downtrend" else "Close is below the previous swing low.",
    ]
    if snapshot.volume_ratio20 is not None and snapshot.volume_ratio20 >= config.volume_confirmation_ratio:
        score += 6
        reasons.append(f"Volume is confirming at {snapshot.volume_ratio20:.2f}x the 20-period average.")
    if snapshot.volume_ratio20 is not None and snapshot.volume_ratio20 >= config.strong_volume_ratio:
        score += 5
        reasons.append("Volume expansion is stronger than normal.")
    score += _relative_strength_bonus(relative_strength, bullish=False, reasons=reasons)
    return _make_match(
        symbol,
        "bearish_trend",
        "bearish",
        score,
        snapshot,
        _bearish_invalidation(structure, snapshot),
        target_zone=_zone_below(snapshot.support),
        trigger_zone="Trend continuation while price stays below EMA20/SMA50.",
        reasons=reasons,
    )


def _bearish_breakdown(
    symbol: str,
    candles: list[Candle],
    structure: MarketStructure | None,
    snapshot: IndicatorSnapshot,
    config: ScannerConfig,
) -> ScanMatch | None:
    if not all([snapshot.previous_low20, snapshot.volume_ratio20, snapshot.rsi14, snapshot.atr14]):
        return None
    if snapshot.close >= snapshot.previous_low20:
        return None
    if snapshot.volume_ratio20 < config.breakout_volume_ratio:
        return None
    if not 25 <= snapshot.rsi14 <= 45:
        return None
    if snapshot.close_location > 0.30:
        return None

    score = 64
    reasons = [
        f"Close broke the previous 20-period low at {snapshot.previous_low20:.2f}.",
        f"Breakdown volume is {snapshot.volume_ratio20:.2f}x the 20-period average.",
        f"RSI {snapshot.rsi14:.1f} supports downside momentum.",
        "Candle closed in the bottom 30% of its range.",
    ]
    if snapshot.previous_low55 is not None and snapshot.close < snapshot.previous_low55:
        score += 8
        reasons.append("Close also broke the previous 55-period low.")
    if _prior_range_width_atr(candles, config) is not None and _prior_range_width_atr(candles, config) <= config.compression_range_atr_multiple:
        score += 8
        reasons.append("Prior candles were compressed before the breakdown.")

    breakdown_level = snapshot.previous_low20
    swing_high = _last_swing_high(structure)
    invalidation = swing_high if swing_high is not None and swing_high <= breakdown_level * 1.02 else breakdown_level
    return _make_match(
        symbol,
        "bearish_breakdown",
        "bearish",
        score,
        snapshot,
        invalidation,
        target_zone=_zone_below(snapshot.support),
        trigger_zone=f"Breakdown below {breakdown_level:.2f}; failed retest improves quality.",
        reasons=reasons,
    )


def _bearish_pullback(
    symbol: str,
    candles: list[Candle],
    structure: MarketStructure | None,
    snapshot: IndicatorSnapshot,
    relative_strength: dict[str, Any] | Any | None,
    config: ScannerConfig,
) -> ScanMatch | None:
    if not all([snapshot.rsi14, snapshot.atr14]):
        return None
    bearish_trend = (
        snapshot.sma50 is not None
        and snapshot.close < snapshot.sma50
        or snapshot.sma20 is not None
        and snapshot.sma50 is not None
        and snapshot.sma20 < snapshot.sma50
    )
    if not bearish_trend:
        return None
    if not 45 <= snapshot.rsi14 <= 60:
        return None
    if snapshot.resistance is not None and snapshot.close >= snapshot.resistance:
        return None

    nearest = _nearest_level_distance(snapshot, [snapshot.ema20, snapshot.sma20, snapshot.resistance])
    if nearest is None or nearest > config.pullback_near_level_atr:
        return None

    score = 58
    reasons = [
        "Higher/same timeframe trend remains bearish.",
        f"Price is {nearest:.2f} ATR from EMA20/SMA20/resistance.",
        f"RSI {snapshot.rsi14:.1f} is in a bearish pullback zone.",
        "Latest close remains below resistance." if snapshot.resistance is not None else "Resistance level was not available.",
    ]
    if _turning_down(candles, snapshot):
        score += 8
        reasons.append("Latest candle/RSI is turning down from the pullback.")
    if snapshot.structure_trend == "downtrend":
        score += 7
        reasons.append("Swing structure still shows a downtrend.")
    score += _relative_strength_bonus(relative_strength, bullish=False, reasons=reasons)
    return _make_match(
        symbol,
        "bearish_pullback",
        "bearish",
        score,
        snapshot,
        _bearish_invalidation(structure, snapshot),
        target_zone=_zone_below(snapshot.support),
        trigger_zone="Rejection from EMA20, SMA20, or resistance with volume confirmation.",
        reasons=reasons,
    )


def _neutral_range(
    symbol: str,
    structure: MarketStructure | None,
    snapshot: IndicatorSnapshot,
    config: ScannerConfig,
) -> ScanMatch | None:
    if structure is None or structure.trend != "range":
        return None
    if snapshot.atr14 is None or snapshot.rsi14 is None or snapshot.range_width_atr is None:
        return None
    if snapshot.range_width_atr > config.neutral_range_atr_multiple:
        return None
    if snapshot.donchian_low20 is not None and snapshot.close < snapshot.donchian_low20:
        return None
    if snapshot.donchian_high20 is not None and snapshot.close > snapshot.donchian_high20:
        return None
    if not 40 <= snapshot.rsi14 <= 60:
        return None
    if (
        snapshot.bollinger_width_percentile120 is not None
        and snapshot.bollinger_width_percentile120 > 80
        and snapshot.volume_ratio20 is not None
        and snapshot.volume_ratio20 > config.strong_volume_ratio
    ):
        return None

    score = 60
    reasons = [
        "Market structure is range-bound.",
        f"Range width is {snapshot.range_width_atr:.2f} ATR.",
        f"RSI {snapshot.rsi14:.1f} is neutral.",
        "Bollinger width is not expanding aggressively.",
    ]
    if snapshot.support_distance_atr is not None and snapshot.resistance_distance_atr is not None:
        centered = min(snapshot.support_distance_atr, snapshot.resistance_distance_atr)
        if centered > 0.8:
            score += 6
            reasons.append("Price is away from immediate range edges.")

    target_zone = _range_zone(snapshot.support, snapshot.resistance)
    return _make_match(
        symbol,
        "neutral_range",
        "neutral",
        score,
        snapshot,
        None,
        target_zone=target_zone,
        trigger_zone="Range behavior remains valid while price stays between support and resistance.",
        reasons=reasons,
    )


def _compression(symbol: str, snapshot: IndicatorSnapshot, config: ScannerConfig) -> ScanMatch | None:
    if snapshot.rsi14 is None:
        return None
    compressed = (
        snapshot.bollinger_width_percentile120 is not None
        and snapshot.bollinger_width_percentile120 <= config.compression_percentile_threshold
    ) or (
        snapshot.range_width_atr is not None
        and snapshot.range_width_atr <= config.compression_range_atr_multiple
    )
    if not compressed:
        return None
    if snapshot.volume_ratio20 is not None and snapshot.volume_ratio20 > 1.1:
        return None
    if not 40 <= snapshot.rsi14 <= 60:
        return None
    if snapshot.donchian_low20 is not None and snapshot.close < snapshot.donchian_low20:
        return None
    if snapshot.donchian_high20 is not None and snapshot.close > snapshot.donchian_high20:
        return None

    score = 58
    reasons = ["Volatility is compressed inside the 20-period range."]
    if snapshot.bollinger_width_percentile120 is not None:
        score += 8
        reasons.append(f"Bollinger Band width percentile is {snapshot.bollinger_width_percentile120:.1f}.")
    if snapshot.range_width_atr is not None:
        score += 5
        reasons.append(f"Recent range width is {snapshot.range_width_atr:.2f} ATR.")
    if snapshot.volume_ratio20 is not None:
        reasons.append(f"Volume is quiet at {snapshot.volume_ratio20:.2f}x the 20-period average.")

    upper = snapshot.donchian_high20 or snapshot.previous_high20
    lower = snapshot.donchian_low20 or snapshot.previous_low20
    return _make_match(
        symbol,
        "compression",
        "watch",
        score,
        snapshot,
        None,
        target_zone="Wait for breakout/breakdown confirmation.",
        trigger_zone=f"Upper trigger {upper:.2f}; lower trigger {lower:.2f}." if upper is not None and lower is not None else None,
        reasons=reasons,
    )


def _avoid_match(
    symbol: str,
    candles: list[Candle],
    snapshot: IndicatorSnapshot | None,
    reasons: list[str],
    config: ScannerConfig,
) -> ScanMatch:
    if snapshot is not None:
        reasons = [*reasons, *_avoid_reasons(candles, snapshot, config)]
    if not reasons:
        reasons = ["No clean rule-based setup matched current candles."]
    score = _clamp(52 + (len(reasons) * 8))
    close = snapshot.close if snapshot else candles[-1].close if candles else None
    return ScanMatch(
        symbol=symbol,
        setup_type="avoid",
        direction="avoid",
        score=score,
        confidence=_confidence(score),
        close=close,
        support=snapshot.support if snapshot else None,
        resistance=snapshot.resistance if snapshot else None,
        invalidation=None,
        target_zone=None,
        trigger_zone=None,
        risk_level="high",
        risk_reward_comment="Low-quality or unclear location; wait for a cleaner structure.",
        reasons=reasons,
        indicators=snapshot.to_dict() if snapshot else {},
    )


def _avoid_reasons(candles: list[Candle], snapshot: IndicatorSnapshot, config: ScannerConfig) -> list[str]:
    reasons: list[str] = []
    if snapshot.atr14 is None:
        reasons.append("ATR is unavailable.")
    if snapshot.volume_ratio20 is not None and snapshot.volume_ratio20 < config.very_low_volume_ratio:
        reasons.append(f"Very low participation: volume is {snapshot.volume_ratio20:.2f}x average.")
    if snapshot.rsi14 is not None and snapshot.rsi14 > 75 and snapshot.close <= (snapshot.previous_high20 or snapshot.close):
        reasons.append("RSI is extended without a confirmed breakout.")
    if snapshot.rsi14 is not None and snapshot.rsi14 < 25 and snapshot.close >= (snapshot.previous_low20 or snapshot.close):
        reasons.append("RSI is extremely weak without a confirmed breakdown.")
    if _contradictory_trend(snapshot):
        reasons.append("Trend inputs are contradictory across averages and structure.")
    nearest = _nearest_level_distance(snapshot, [snapshot.support, snapshot.resistance])
    if nearest is not None and nearest > config.far_from_level_atr:
        reasons.append(f"Price is {nearest:.2f} ATR away from the nearest support/resistance level.")
    if len(candles) >= 2 and snapshot.atr14:
        latest_move = abs(candles[-1].close - candles[-2].close) / snapshot.atr14
        if latest_move > config.large_move_atr:
            reasons.append(f"Latest candle already moved {latest_move:.2f} ATR from the prior close.")
    return reasons


def _make_match(
    symbol: str,
    setup_type: str,
    direction: str,
    score: int,
    snapshot: IndicatorSnapshot,
    invalidation: float | None,
    target_zone: str | None,
    trigger_zone: str | None,
    reasons: list[str],
) -> ScanMatch:
    score = _clamp(score)
    return ScanMatch(
        symbol=symbol,
        setup_type=setup_type,
        direction=direction,
        score=score,
        confidence=_confidence(score),
        close=snapshot.close,
        support=snapshot.support,
        resistance=snapshot.resistance,
        invalidation=invalidation,
        target_zone=target_zone,
        trigger_zone=trigger_zone,
        risk_level=_risk_level(snapshot, invalidation),
        risk_reward_comment=_risk_comment(snapshot, invalidation),
        reasons=reasons,
        indicators=snapshot.to_dict(),
    )


def _donchian_high(candles: list[Candle], period: int, exclude_current: bool) -> float | None:
    lookback = period + 1 if exclude_current else period
    if len(candles) < lookback:
        return None
    values = candles[-lookback:-1] if exclude_current else candles[-period:]
    return max(candle.high for candle in values)


def _donchian_low(candles: list[Candle], period: int, exclude_current: bool) -> float | None:
    lookback = period + 1 if exclude_current else period
    if len(candles) < lookback:
        return None
    values = candles[-lookback:-1] if exclude_current else candles[-period:]
    return min(candle.low for candle in values)


def _bollinger(closes: list[float], config: ScannerConfig) -> tuple[float | None, float | None, float | None, float | None]:
    if len(closes) < config.bollinger_period:
        return None, None, None, None
    values = closes[-config.bollinger_period :]
    mid = _average(values)
    variance = sum((value - mid) ** 2 for value in values) / len(values)
    stddev = sqrt(variance)
    upper = mid + (config.bollinger_stddev * stddev)
    lower = mid - (config.bollinger_stddev * stddev)
    width = ((upper - lower) / mid) * 100 if mid else None
    return upper, mid, lower, width


def _bollinger_width_percentile(closes: list[float], config: ScannerConfig) -> float | None:
    period = config.bollinger_period
    if len(closes) < period:
        return None
    start = max(period, len(closes) - config.bollinger_width_lookback + 1)
    widths: list[float] = []
    for end in range(start, len(closes) + 1):
        upper, mid, lower, width = _bollinger_for_window(closes[end - period : end], config)
        if upper is not None and lower is not None and mid is not None and width is not None:
            widths.append(width)
    if not widths:
        return None
    current = widths[-1]
    lower_or_equal = sum(1 for width in widths if width <= current)
    return (lower_or_equal / len(widths)) * 100


def _bollinger_for_window(values: list[float], config: ScannerConfig) -> tuple[float | None, float | None, float | None, float | None]:
    if len(values) < config.bollinger_period:
        return None, None, None, None
    mid = _average(values)
    variance = sum((value - mid) ** 2 for value in values) / len(values)
    stddev = sqrt(variance)
    upper = mid + (config.bollinger_stddev * stddev)
    lower = mid - (config.bollinger_stddev * stddev)
    width = ((upper - lower) / mid) * 100 if mid else None
    return upper, mid, lower, width


def _period_return(values: list[float], lookback: int) -> float | None:
    if len(values) < lookback + 1:
        return None
    start = values[-lookback - 1]
    if start == 0:
        return None
    return ((values[-1] - start) / start) * 100


def _average(values: list[float] | list[int]) -> float:
    return sum(values) / len(values)


def _last_swing_high(structure: MarketStructure | None) -> float | None:
    return structure.last_swing_high.price if structure and structure.last_swing_high else None


def _last_swing_low(structure: MarketStructure | None) -> float | None:
    return structure.last_swing_low.price if structure and structure.last_swing_low else None


def _above_previous_swing_high(structure: MarketStructure | None, close: float) -> bool:
    swing = _last_swing_high(structure)
    return swing is not None and close > swing


def _below_previous_swing_low(structure: MarketStructure | None, close: float) -> bool:
    swing = _last_swing_low(structure)
    return swing is not None and close < swing


def _bullish_invalidation(structure: MarketStructure | None, snapshot: IndicatorSnapshot) -> float | None:
    swing_low = _last_swing_low(structure)
    levels = [value for value in [snapshot.support, swing_low] if value is not None and value < snapshot.close]
    return max(levels) if levels else snapshot.support


def _bearish_invalidation(structure: MarketStructure | None, snapshot: IndicatorSnapshot) -> float | None:
    swing_high = _last_swing_high(structure)
    levels = [value for value in [snapshot.resistance, swing_high] if value is not None and value > snapshot.close]
    return min(levels) if levels else snapshot.resistance


def _nearest_level_distance(snapshot: IndicatorSnapshot, levels: list[float | None]) -> float | None:
    if snapshot.atr14 is None:
        return None
    distances = [abs(snapshot.close - level) / snapshot.atr14 for level in levels if level is not None]
    return min(distances) if distances else None


def _turning_up(candles: list[Candle], snapshot: IndicatorSnapshot) -> bool:
    price_up = len(candles) >= 2 and candles[-1].close > candles[-2].close
    rsi_up = snapshot.previous_rsi14 is not None and snapshot.rsi14 is not None and snapshot.rsi14 >= snapshot.previous_rsi14
    return price_up or rsi_up


def _turning_down(candles: list[Candle], snapshot: IndicatorSnapshot) -> bool:
    price_down = len(candles) >= 2 and candles[-1].close < candles[-2].close
    rsi_down = snapshot.previous_rsi14 is not None and snapshot.rsi14 is not None and snapshot.rsi14 <= snapshot.previous_rsi14
    return price_down or rsi_down


def _relative_strength_bonus(
    relative_strength: dict[str, Any] | Any | None,
    bullish: bool,
    reasons: list[str],
) -> int:
    signal = _relative_strength_signal(relative_strength, "stock_vs_nifty")
    if not signal:
        return 0
    label = str(_read_attr(signal, "label", "")).lower()
    relative = _read_attr(signal, "relative_return_percent", None)
    if bullish and label == "outperforming":
        reasons.append(f"Stock is outperforming Nifty{_relative_suffix(relative)}.")
        return 5
    if not bullish and label == "underperforming":
        reasons.append(f"Stock is underperforming Nifty{_relative_suffix(relative)}.")
        return 5
    return 0


def _relative_strength_signal(relative_strength: dict[str, Any] | Any | None, name: str) -> Any | None:
    if relative_strength is None:
        return None
    if isinstance(relative_strength, dict):
        return relative_strength.get(name)
    return getattr(relative_strength, name, None)


def _read_attr(value: Any, name: str, default: Any) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _relative_suffix(relative: Any) -> str:
    return f" ({relative:.2f}%)" if isinstance(relative, (int, float)) else ""


def _prior_range_width_atr(candles: list[Candle], config: ScannerConfig) -> float | None:
    if len(candles) < config.donchian_fast_period + 1:
        return None
    prior = candles[-(config.donchian_fast_period + 1) : -1]
    prior_atr = atr(candles[:-1], config.atr_period)
    if not prior_atr:
        return None
    return (max(candle.high for candle in prior) - min(candle.low for candle in prior)) / prior_atr


def _contradictory_trend(snapshot: IndicatorSnapshot) -> bool:
    bullish_average = snapshot.sma50 is not None and snapshot.close > snapshot.sma50
    bearish_average = snapshot.ema20 is not None and snapshot.close < snapshot.ema20
    bullish_structure = snapshot.structure_trend == "uptrend"
    bearish_structure = snapshot.structure_trend == "downtrend"
    return (bullish_average and bearish_structure) or (bearish_average and bullish_structure)


def _zone_above(level: float | None) -> str | None:
    return f"Above resistance {level:.2f}" if level is not None else None


def _zone_below(level: float | None) -> str | None:
    return f"Below support {level:.2f}" if level is not None else None


def _range_zone(support: float | None, resistance: float | None) -> str | None:
    if support is None or resistance is None:
        return "Neutral option zone outside identified range."
    return f"Outside support/resistance: below {support:.2f} or above {resistance:.2f}."


def _risk_level(snapshot: IndicatorSnapshot, invalidation: float | None) -> str:
    if invalidation is None or snapshot.atr14 is None:
        return "medium"
    distance = abs(snapshot.close - invalidation) / snapshot.atr14
    if distance <= 1.2:
        return "low"
    if distance <= 2.5:
        return "medium"
    return "high"


def _risk_comment(snapshot: IndicatorSnapshot, invalidation: float | None) -> str:
    if invalidation is None or snapshot.atr14 is None:
        return "Invalidation is not clear; reduce confidence until a cleaner level forms."
    distance = abs(snapshot.close - invalidation) / snapshot.atr14
    return f"Invalidation {invalidation:.2f}; distance is {distance:.2f} ATR from close."


def _confidence(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def _clamp(value: float | int) -> int:
    return max(0, min(100, int(round(value))))
