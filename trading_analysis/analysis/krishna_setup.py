from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from trading_analysis.analysis.market_structure import MarketStructure
from trading_analysis.analysis.technical import atr, ema
from trading_analysis.models import Candle


@dataclass(frozen=True)
class KrishnaSetupConfig:
    min_candles: int = 60
    candle_top_lookback: int = 3
    line_tolerance_percent: float = 0.25
    max_line_gap_atr: float = 2.5
    max_line_gap_percent: float = 8.0


@dataclass(frozen=True)
class KrishnaSetupMatch:
    symbol: str
    score: int
    confidence: str
    close: float
    candle_high: float
    yellow_line: float
    yellow_gap_percent: float
    yellow_gap_atr: float | None
    ema9: float | None
    ema26: float | None
    vwap: float | None
    vwma20: float | None
    donchian_upper20: float | None
    donchian_mid20: float | None
    donchian_lower20: float | None
    volume_ratio20: float | None
    structure_trend: str | None
    reasons: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_krishna_bullish_setup(
    symbol: str,
    candles: list[Candle],
    structure: MarketStructure | None = None,
    config: KrishnaSetupConfig | None = None,
) -> KrishnaSetupMatch | None:
    config = config or KrishnaSetupConfig()
    candles = sorted(candles, key=lambda candle: candle.timestamp)
    if len(candles) < config.min_candles:
        return None

    levels = _levels(candles)
    close = candles[-1].close
    latest_high = candles[-1].high
    yellow_line = levels["yellow_line"]
    atr14 = levels["atr14"]
    if yellow_line is None or levels["ema9"] is None or levels["ema26"] is None:
        return None

    reasons: list[str] = []
    warnings: list[str] = []
    score = 45

    if levels["ema9"] <= levels["ema26"]:
        return None
    score += 12
    reasons.append("EMA9 is above EMA26; short-term momentum is bullish.")

    if close < levels["ema26"]:
        return None
    score += 8
    reasons.append("Close is above EMA26; daily trend has not broken down.")

    if structure and structure.trend == "downtrend":
        return None
    if structure and structure.trend == "uptrend":
        score += 10
        reasons.append("Market structure is an uptrend.")
    elif structure:
        score += 4
        reasons.append("Market structure is not downtrend.")

    candle_top = max(candle.high for candle in candles[-config.candle_top_lookback :])
    tolerance = 1 - (config.line_tolerance_percent / 100)
    if yellow_line < candle_top * tolerance:
        return None
    score += 14
    reasons.append(f"Yellow Chande Kroll line is above the latest {config.candle_top_lookback} candle high(s).")

    indicator_levels = {
        "EMA9": levels["ema9"],
        "EMA26": levels["ema26"],
        "VWAP": levels["vwap"],
        "VWMA20": levels["vwma20"],
        "Donchian mid": levels["donchian_mid20"],
    }
    missing = [name for name, value in indicator_levels.items() if value is None]
    blocking = [name for name, value in indicator_levels.items() if value is not None and yellow_line < value * tolerance]
    if blocking:
        return None
    score += 12
    reasons.append("Yellow Chande Kroll line is above EMA9, EMA26, VWAP/VWMA where available, and Donchian mid.")
    if missing:
        warnings.append(f"Missing indicator level(s): {', '.join(missing)}.")

    gap = ((yellow_line - close) / close) * 100 if close else 0.0
    gap_atr = (yellow_line - close) / atr14 if atr14 else None
    if gap < -config.line_tolerance_percent:
        return None
    if gap_atr is not None and gap_atr <= config.max_line_gap_atr:
        score += 7
        reasons.append(f"Yellow line is within {gap_atr:.2f} ATR of close; pullback watch is still nearby.")
    elif gap <= config.max_line_gap_percent:
        score += 4
        reasons.append(f"Yellow line is {gap:.2f}% above close; still within manual review range.")
    else:
        warnings.append(f"Yellow line is far from close ({gap:.2f}%).")

    if levels["vwma20"] is not None and close >= levels["vwma20"]:
        score += 5
        reasons.append("Close is above VWMA20; participation-weighted trend remains supportive.")
    if levels["donchian_mid20"] is not None and close >= levels["donchian_mid20"]:
        score += 4
        reasons.append("Close is above Donchian 20 midpoint.")
    if levels["volume_ratio20"] is not None:
        if levels["volume_ratio20"] <= 1.2:
            score += 3
            reasons.append(f"Volume is not climactic at {levels['volume_ratio20']:.2f}x average.")
        else:
            warnings.append(f"Volume is elevated at {levels['volume_ratio20']:.2f}x average; check exhaustion manually.")

    score = max(0, min(100, score))
    return KrishnaSetupMatch(
        symbol=symbol.upper(),
        score=score,
        confidence=_confidence(score, warnings),
        close=close,
        candle_high=latest_high,
        yellow_line=yellow_line,
        yellow_gap_percent=gap,
        yellow_gap_atr=gap_atr,
        ema9=levels["ema9"],
        ema26=levels["ema26"],
        vwap=levels["vwap"],
        vwma20=levels["vwma20"],
        donchian_upper20=levels["donchian_upper20"],
        donchian_mid20=levels["donchian_mid20"],
        donchian_lower20=levels["donchian_lower20"],
        volume_ratio20=levels["volume_ratio20"],
        structure_trend=structure.trend if structure else None,
        reasons=reasons,
        warnings=warnings,
    )


def _levels(candles: list[Candle]) -> dict[str, float | None]:
    closes = [candle.close for candle in candles]
    volumes = [candle.volume for candle in candles]
    ck_long, ck_short = _chande_kroll_stops(candles)
    upper, mid, lower = _donchian(candles, 20)
    avg_volume20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
    return {
        "ema9": ema(closes, 9),
        "ema26": ema(closes, 26),
        "vwap": _session_vwap(candles),
        "vwma20": _vwma(candles, 20),
        "donchian_upper20": upper,
        "donchian_mid20": mid,
        "donchian_lower20": lower,
        "volume_ratio20": candles[-1].volume / avg_volume20 if avg_volume20 else None,
        "atr14": atr(candles, 14),
        "yellow_line": max(value for value in (ck_long, ck_short) if value is not None) if ck_long is not None or ck_short is not None else None,
    }


def _chande_kroll_stops(
    candles: list[Candle],
    atr_period: int = 10,
    atr_multiplier: float = 1.0,
    stop_period: int = 9,
) -> tuple[float | None, float | None]:
    if len(candles) < atr_period + stop_period:
        return None, None
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
        return None, None
    return max(long_candidates), min(short_candidates)


def _donchian(candles: list[Candle], period: int) -> tuple[float | None, float | None, float | None]:
    if len(candles) < period:
        return None, None, None
    window = candles[-period:]
    upper = max(candle.high for candle in window)
    lower = min(candle.low for candle in window)
    return upper, (upper + lower) / 2, lower


def _session_vwap(candles: list[Candle]) -> float | None:
    latest = candles[-1]
    session = [candle for candle in candles if candle.timestamp.date() == latest.timestamp.date()]
    total_volume = sum(candle.volume for candle in session)
    if total_volume <= 0:
        return None
    return sum(((candle.high + candle.low + candle.close) / 3) * candle.volume for candle in session) / total_volume


def _vwma(candles: list[Candle], period: int) -> float | None:
    if len(candles) < period:
        return None
    window = candles[-period:]
    total_volume = sum(candle.volume for candle in window)
    if total_volume <= 0:
        return None
    return sum(candle.close * candle.volume for candle in window) / total_volume


def _confidence(score: int, warnings: list[str]) -> str:
    if warnings:
        return "medium" if score >= 75 else "low"
    if score >= 75:
        return "high"
    if score >= 60:
        return "medium"
    return "low"
