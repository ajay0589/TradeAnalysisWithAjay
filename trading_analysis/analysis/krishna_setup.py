from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from trading_analysis.analysis.market_structure import MarketStructure
from trading_analysis.analysis.technical import atr, ema, rsi
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


@dataclass(frozen=True)
class KrishnaEntryTrigger:
    symbol: str
    timeframe: str
    status: str
    trigger_date: str | None
    close: float | None
    yellow_line: float | None
    vwma20: float | None
    score: int
    confidence: str
    entry_price: float | None
    stop_loss: float | None
    reasons: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KrishnaPurpleTouchConfig:
    min_candles: int = 52
    step1_min_candles: int = 9
    step1_touch_tolerance_percent: float = 1.0
    purple_touch_tolerance_percent: float = 1.0
    approach_lookback_candles: int = 3
    approach_tolerance_percent: float = 3.0


@dataclass(frozen=True)
class KrishnaPurpleProfile:
    purple_timeframe: str
    label: str
    early_timeframe: str
    final_timeframe: str
    exit_timeframe: str
    minimum_days: int
    confirmation_timeframe: str
    confirmation_label: str
    confirmation_open_or_close: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KrishnaPurpleTouchMatch:
    symbol: str
    purple_timeframe: str
    purple_timeframe_label: str
    score: int
    confidence: str
    close: float
    purple_ema9: float | None
    light_green_level: float | None
    brown_vwma20: float | None
    yellow_line: float | None
    blue_line: float | None
    ema26: float | None
    ema89: float | None
    rsi14: float | None
    purple_touch: bool
    purple_touch_distance_percent: float | None
    purple_range_distance_percent: float | None
    yellow_below_ema26: bool | None
    brown_vs_light_green: str
    above_black_line: bool | None
    blue_above_purple: bool | None
    approach_from_blue: bool | None
    higher_confirmation: dict[str, Any]
    early_entry: dict[str, Any]
    final_entry: dict[str, Any]
    exit_rule: str
    understood_rules: list[str]
    open_questions: list[str]
    reasons: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KrishnaPurpleStep1Candidate:
    symbol: str
    purple_timeframe: str
    purple_timeframe_label: str
    scan_stage: str
    score: int
    confidence: str
    close: float
    candle_high: float
    candle_low: float
    purple_ema9: float | None
    purple_touch: bool
    purple_touch_distance_percent: float | None
    purple_range_distance_percent: float | None
    light_green_level: float | None
    brown_vwma20: float | None
    yellow_line: float | None
    blue_line: float | None
    ema26: float | None
    ema89: float | None
    rsi14: float | None
    volume_ratio20: float | None
    structure_trend: str | None
    above_black_line: bool | None
    blue_above_purple: bool | None
    approach_from_blue: bool | None
    higher_confirmation: dict[str, Any]
    above_ema26: bool | None
    ema9_above_ema26: bool | None
    yellow_below_ema26: bool | None
    brown_vs_light_green: str
    early_entry: dict[str, Any]
    final_entry: dict[str, Any]
    exit_rule: str
    full_setup_status: str
    blockers: list[str]
    reasons: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PURPLE_TOUCH_PROFILES: dict[str, KrishnaPurpleProfile] = {
    "month": KrishnaPurpleProfile(
        "month", "Monthly purple touch", "120minute", "day", "120minute", 3000,
        "5month", "Derived 5-month", True,
    ),
    "week": KrishnaPurpleProfile(
        "week", "Weekly purple touch", "30minute", "120minute", "30minute", 730,
        "month", "Monthly", True,
    ),
    "day": KrishnaPurpleProfile(
        "day", "Daily purple touch", "10minute", "10minute", "10minute", 365,
        "week", "Weekly", True,
    ),
}


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


def scan_krishna_entry_trigger(
    symbol: str,
    candles: list[Candle],
    timeframe: str = "120minute",
) -> KrishnaEntryTrigger:
    candles = sorted(candles, key=lambda candle: candle.timestamp)
    if len(candles) < 30:
        return KrishnaEntryTrigger(
            symbol=symbol.upper(),
            timeframe=timeframe,
            status="insufficient",
            trigger_date=None,
            close=None,
            yellow_line=None,
            vwma20=None,
            score=0,
            confidence="low",
            entry_price=None,
            stop_loss=None,
            reasons=[],
            warnings=[f"Needs at least 30 {timeframe} candles for Krishna entry trigger."],
        )

    levels = _levels(candles)
    ck_long, ck_short = _chande_kroll_stops(candles)
    latest = candles[-1]
    close = latest.close
    yellow_candidates = [value for value in (ck_long, ck_short) if value is not None]
    yellow_line = min(yellow_candidates) if yellow_candidates else None
    vwma20 = levels["vwma20"]
    atr14 = levels["atr14"]
    reasons: list[str] = []
    warnings: list[str] = []
    score = 35

    if yellow_line is None:
        warnings.append("Yellow Chande Kroll line is not available.")
    if vwma20 is None:
        warnings.append("VWMA20 is not available.")
    if warnings:
        return KrishnaEntryTrigger(
            symbol=symbol.upper(),
            timeframe=timeframe,
            status="wait",
            trigger_date=latest.timestamp.isoformat(),
            close=close,
            yellow_line=yellow_line,
            vwma20=vwma20,
            score=score,
            confidence="low",
            entry_price=None,
            stop_loss=None,
            reasons=reasons,
            warnings=warnings,
        )

    if close > yellow_line:
        score += 30
        reasons.append("2-hour candle closed above the yellow Chande Kroll line.")
    else:
        warnings.append("2-hour candle has not closed above the yellow Chande Kroll line.")

    if yellow_line < vwma20:
        score += 25
        reasons.append("Yellow Chande Kroll line is below VWMA20.")
    else:
        warnings.append("Yellow Chande Kroll line is not below VWMA20 yet.")

    if levels["volume_ratio20"] is not None and levels["volume_ratio20"] >= 1.0:
        score += 5
        reasons.append(f"2-hour volume is {levels['volume_ratio20']:.2f}x its 20-candle average.")

    status = "entry_allowed" if close > yellow_line and yellow_line < vwma20 else "wait"
    score = max(0, min(100, score))
    stop_loss = yellow_line - (0.5 * atr14) if atr14 and yellow_line else yellow_line
    return KrishnaEntryTrigger(
        symbol=symbol.upper(),
        timeframe=timeframe,
        status=status,
        trigger_date=latest.timestamp.isoformat(),
        close=close,
        yellow_line=yellow_line,
        vwma20=vwma20,
        score=score,
        confidence=_confidence(score, warnings),
        entry_price=close if status == "entry_allowed" else None,
        stop_loss=stop_loss if status == "entry_allowed" else None,
        reasons=reasons,
        warnings=warnings,
    )


def krishna_purple_profile(purple_timeframe: str) -> KrishnaPurpleProfile:
    key = _normalize_purple_timeframe(purple_timeframe)
    return PURPLE_TOUCH_PROFILES[key]


def scan_krishna_purple_touch_setup(
    symbol: str,
    touch_candles: list[Candle],
    early_candles: list[Candle] | None = None,
    final_candles: list[Candle] | None = None,
    confirmation_candles: list[Candle] | None = None,
    purple_timeframe: str = "week",
    structure: MarketStructure | None = None,
    config: KrishnaPurpleTouchConfig | None = None,
) -> KrishnaPurpleTouchMatch | None:
    config = config or KrishnaPurpleTouchConfig()
    profile = krishna_purple_profile(purple_timeframe)
    touch_candles = sorted(touch_candles, key=lambda candle: candle.timestamp)
    if len(touch_candles) < config.min_candles:
        return None

    levels = _levels(touch_candles)
    latest = touch_candles[-1]
    ema9 = levels["ema9"]
    ema26 = levels["ema26"]
    if ema9 is None or ema26 is None:
        return None

    reasons: list[str] = []
    warnings: list[str] = []
    open_questions: list[str] = []
    score = 35

    tolerance = config.purple_touch_tolerance_percent / 100
    purple_touch = latest.low <= ema9 * (1 + tolerance) and latest.high >= ema9 * (1 - tolerance)
    if not purple_touch:
        return None
    close_distance = _purple_close_distance_percent(latest, ema9)
    range_distance = _purple_touch_distance_percent(latest, ema9)
    score += 20
    reasons.append(_purple_touch_reason(range_distance, config.purple_touch_tolerance_percent))

    blue = levels["ck_blue_line"]
    blue_above_purple = blue > ema9 if blue is not None else None
    if blue_above_purple is not True:
        return None
    score += 8
    reasons.append("Blue Chande Kroll is above purple EMA9 on the touch timeframe.")

    approach_from_blue = _approaches_purple_from_blue(touch_candles, ema9, blue, config)
    if approach_from_blue is not True:
        return None
    score += 8
    reasons.append(
        f"Price approached purple EMA9 from the blue-line area across the latest {config.approach_lookback_candles} candles."
    )

    higher_confirmation = _purple_higher_confirmation(profile, confirmation_candles)
    if higher_confirmation["status"] != "pass":
        return None
    score += 8
    reasons.extend(higher_confirmation["reasons"])

    if levels["ema9"] > levels["ema26"]:
        score += 8
        reasons.append("EMA9 is above EMA26; short-term higher-timeframe trend remains constructive.")
    else:
        warnings.append("EMA9 is not above EMA26 on the purple-touch timeframe.")

    if latest.close >= ema26:
        score += 8
        reasons.append("Close is above EMA26/light-green moving-average area.")
    else:
        return None

    if structure and structure.trend == "downtrend":
        return None
    if structure and structure.trend == "uptrend":
        score += 8
        reasons.append("Market structure is an uptrend.")
    elif structure:
        score += 3
        reasons.append("Market structure is not downtrend.")

    above_black = latest.close > levels["ema89"] if levels["ema89"] is not None else None
    if above_black is True:
        score += 7
        reasons.append("Close is above the black EMA89 trend line; Krishna marked this mandatory.")
    elif above_black is False:
        return None
    else:
        return None

    yellow = levels["ck_yellow_line"]
    light_green = ema26
    brown = levels["vwma20"]
    yellow_below_ema26 = yellow < light_green if yellow is not None and light_green is not None else None
    if yellow_below_ema26 is True:
        score += 6
        reasons.append("Yellow Chande Kroll line is below light-green EMA26.")
    elif yellow_below_ema26 is False:
        warnings.append("Yellow Chande Kroll line is not below light-green EMA26.")
    else:
        warnings.append("Yellow/EMA26 relationship is unavailable.")

    brown_relation = _relation(brown, light_green)
    if brown_relation != "unknown":
        reasons.append(f"Brown VWMA20 is {brown_relation} light-green EMA26.")

    entry_context = {
        "purple_timeframe": profile.purple_timeframe,
        "purple_ema9": ema9,
        "touch_timestamp": latest.timestamp,
        "touch_tolerance_percent": config.purple_touch_tolerance_percent,
    }
    early_entry = _purple_entry_snapshot(
        symbol, early_candles, profile.early_timeframe, "early", **entry_context
    )
    final_entry = _purple_entry_snapshot(
        symbol, final_candles, profile.final_timeframe, "final", **entry_context
    )
    if early_entry["status"] == "entry_candidate":
        score += 7
    if final_entry["status"] == "entry_candidate":
        score += 9
    if final_entry.get("yellow_above_brown"):
        score += 5

    understood_rules = [
        f"{profile.label}: first shortlist stocks where the higher timeframe candle touches the purple EMA9 zone.",
        f"Early entry timeframe: {profile.early_timeframe}; candle close above yellow Chande Kroll line.",
        f"Final entry timeframe: {profile.final_timeframe}; candle close above yellow Chande Kroll line.",
        f"Exit for both entry styles: {profile.exit_timeframe} candle close below yellow Chande Kroll line.",
        "Black EMA89 filter is mandatory.",
        "Blue Chande Kroll must be above purple EMA9 on both the touch and entry timeframes.",
        f"Price must approach purple from the blue-line area within the latest {config.approach_lookback_candles} touch-timeframe candles.",
        higher_confirmation["rule"],
        "The mapped touch bar is Candle 1; Candle 2 must close before entry confirmation becomes active.",
        "From Candle 3 onward, the setup is discarded only if a candle breaks the lower of Candle 1 and Candle 2 lows; there is no fixed candle-count expiry.",
        "Light green is EMA26; Ichimoku, VWAP, and Donchian Channel 20 are ignored for this setup.",
        "Final entry requires yellow Chande Kroll above brown VWMA20.",
        "RSI bullish divergence is optional and only improves quality when present.",
        "Close above blue Chande Kroll is a rare stronger confirmation.",
    ]
    score = max(0, min(100, score))
    return KrishnaPurpleTouchMatch(
        symbol=symbol.upper(),
        purple_timeframe=profile.purple_timeframe,
        purple_timeframe_label=profile.label,
        score=score,
        confidence=_confidence(score, warnings),
        close=latest.close,
        purple_ema9=ema9,
        light_green_level=light_green,
        brown_vwma20=brown,
        yellow_line=yellow,
        blue_line=levels["ck_blue_line"],
        ema26=ema26,
        ema89=levels["ema89"],
        rsi14=levels["rsi14"],
        purple_touch=purple_touch,
        purple_touch_distance_percent=close_distance,
        purple_range_distance_percent=range_distance,
        yellow_below_ema26=yellow_below_ema26,
        brown_vs_light_green=brown_relation,
        above_black_line=above_black,
        blue_above_purple=blue_above_purple,
        approach_from_blue=approach_from_blue,
        higher_confirmation=higher_confirmation,
        early_entry=early_entry,
        final_entry=final_entry,
        exit_rule=f"Exit/review if {profile.exit_timeframe} candle closes below yellow Chande Kroll line.",
        understood_rules=understood_rules,
        open_questions=open_questions,
        reasons=reasons,
        warnings=warnings + list(early_entry.get("warnings") or []) + list(final_entry.get("warnings") or []),
    )


def scan_krishna_purple_step1_candidate(
    symbol: str,
    touch_candles: list[Candle],
    early_candles: list[Candle] | None = None,
    final_candles: list[Candle] | None = None,
    confirmation_candles: list[Candle] | None = None,
    purple_timeframe: str = "week",
    structure: MarketStructure | None = None,
    config: KrishnaPurpleTouchConfig | None = None,
) -> KrishnaPurpleStep1Candidate | None:
    """Return the first-stage purple EMA9 touch shortlist before strict entry filters."""
    config = config or KrishnaPurpleTouchConfig()
    profile = krishna_purple_profile(purple_timeframe)
    touch_candles = sorted(touch_candles, key=lambda candle: candle.timestamp)
    if len(touch_candles) < config.step1_min_candles:
        return None

    levels = _levels(touch_candles)
    latest = touch_candles[-1]
    ema9 = levels["ema9"]
    if ema9 is None:
        return None

    tolerance = config.step1_touch_tolerance_percent / 100
    purple_touch = latest.low <= ema9 * (1 + tolerance) and latest.high >= ema9 * (1 - tolerance)
    if not purple_touch:
        return None

    close = latest.close
    ema26 = levels["ema26"]
    ema89 = levels["ema89"]
    yellow = levels["ck_yellow_line"]
    brown = levels["vwma20"]
    light_green = ema26
    above_black = close > ema89 if ema89 is not None else None
    above_ema26 = close >= ema26 if ema26 is not None else None
    ema9_above_ema26 = ema9 > ema26 if ema26 is not None else None
    yellow_below_ema26 = yellow < ema26 if yellow is not None and ema26 is not None else None
    brown_relation = _relation(brown, light_green)
    distance = _purple_close_distance_percent(latest, ema9)
    range_distance = _purple_touch_distance_percent(latest, ema9)
    blue = levels["ck_blue_line"]
    blue_above_purple = blue > ema9 if blue is not None else None
    approach_from_blue = _approaches_purple_from_blue(touch_candles, ema9, blue, config)
    higher_confirmation = _purple_higher_confirmation(profile, confirmation_candles)

    entry_context = {
        "purple_timeframe": profile.purple_timeframe,
        "purple_ema9": ema9,
        "touch_timestamp": latest.timestamp,
        "touch_tolerance_percent": config.step1_touch_tolerance_percent,
    }
    early_entry = _purple_entry_snapshot(
        symbol, early_candles, profile.early_timeframe, "early", **entry_context
    )
    final_entry = _purple_entry_snapshot(
        symbol, final_candles, profile.final_timeframe, "final", **entry_context
    )

    reasons = [_purple_touch_reason(range_distance, config.step1_touch_tolerance_percent, prefix="Step 1 passed: ")]
    warnings: list[str] = []
    blockers: list[str] = []
    score = 35

    if ema9_above_ema26 is True:
        score += 8
        reasons.append("Purple EMA9 is above light-green EMA26.")
    elif ema9_above_ema26 is False:
        warnings.append("Purple EMA9 is not above light-green EMA26 yet.")
    else:
        warnings.append("EMA26/light-green level is unavailable.")

    if above_ema26 is True:
        score += 8
        reasons.append("Close is above light-green EMA26.")
    elif above_ema26 is False:
        blockers.append("Close is below light-green EMA26.")

    if above_black is True:
        score += 14
        reasons.append("Close is above black EMA89; mandatory trend filter passes.")
    elif above_black is False:
        blockers.append("Close is not above black EMA89 mandatory filter.")
    else:
        blockers.append("Black EMA89 is unavailable; strict setup cannot qualify yet.")

    if blue_above_purple is True:
        score += 8
        reasons.append("Blue Chande Kroll is above purple EMA9 on the touch timeframe.")
    elif blue_above_purple is False:
        blockers.append("Blue Chande Kroll is not above purple EMA9 on the touch timeframe.")
    else:
        blockers.append("Blue Chande Kroll is unavailable on the touch timeframe.")

    if approach_from_blue is True:
        score += 7
        reasons.append(
            f"Price approached purple from the blue-line area across the latest {config.approach_lookback_candles} candles."
        )
    elif approach_from_blue is False:
        blockers.append(
            f"Price did not approach purple from the blue-line area across the latest {config.approach_lookback_candles} candles."
        )
    else:
        blockers.append("Blue-to-purple approach could not be evaluated.")

    if higher_confirmation["status"] == "pass":
        score += 8
        reasons.extend(higher_confirmation["reasons"])
    else:
        blockers.extend(higher_confirmation["warnings"])

    if structure and structure.trend == "downtrend":
        blockers.append("Market structure is downtrend.")
    elif structure and structure.trend == "uptrend":
        score += 7
        reasons.append("Market structure is uptrend.")
    elif structure:
        score += 3
        reasons.append("Market structure is not downtrend.")

    if yellow_below_ema26 is True:
        score += 5
        reasons.append("Yellow Chande Kroll line is below light-green EMA26.")
    elif yellow_below_ema26 is False:
        warnings.append("Yellow Chande Kroll line is not below light-green EMA26.")
    else:
        warnings.append("Yellow/EMA26 relationship is unavailable.")

    if early_entry.get("status") == "entry_candidate":
        score += 8
        reasons.append("Early entry timeframe is already above yellow.")
    if final_entry.get("status") == "entry_candidate":
        score += 10
        reasons.append("Final entry timeframe is already above yellow and final rules pass.")
    elif final_entry.get("status") == "wait":
        warnings.append("Final entry condition is still waiting.")

    full_setup_status = "qualified" if not blockers else "step1_only"
    score = max(0, min(100, score))
    return KrishnaPurpleStep1Candidate(
        symbol=symbol.upper(),
        purple_timeframe=profile.purple_timeframe,
        purple_timeframe_label=profile.label,
        scan_stage="step1_purple_ema9_touch",
        score=score,
        confidence=_confidence(score, warnings + blockers),
        close=close,
        candle_high=latest.high,
        candle_low=latest.low,
        purple_ema9=ema9,
        purple_touch=purple_touch,
        purple_touch_distance_percent=distance,
        purple_range_distance_percent=range_distance,
        light_green_level=light_green,
        brown_vwma20=brown,
        yellow_line=yellow,
        blue_line=blue,
        ema26=ema26,
        ema89=ema89,
        rsi14=levels["rsi14"],
        volume_ratio20=levels["volume_ratio20"],
        structure_trend=structure.trend if structure else None,
        above_black_line=above_black,
        blue_above_purple=blue_above_purple,
        approach_from_blue=approach_from_blue,
        higher_confirmation=higher_confirmation,
        above_ema26=above_ema26,
        ema9_above_ema26=ema9_above_ema26,
        yellow_below_ema26=yellow_below_ema26,
        brown_vs_light_green=brown_relation,
        early_entry=early_entry,
        final_entry=final_entry,
        exit_rule=f"Exit/review if {profile.exit_timeframe} candle closes below yellow Chande Kroll line.",
        full_setup_status=full_setup_status,
        blockers=blockers,
        reasons=reasons,
        warnings=warnings + list(early_entry.get("warnings") or []) + list(final_entry.get("warnings") or []),
    )


def scan_krishna_purple_exit_status(symbol: str, candles: list[Candle] | None, timeframe: str) -> dict[str, Any]:
    if not candles:
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "status": "missing",
            "reasons": [],
            "warnings": [f"Cached {timeframe} candles are needed to check the exit condition."],
        }
    candles = sorted(candles, key=lambda candle: candle.timestamp)
    if len(candles) < 30:
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "status": "insufficient",
            "trigger_date": candles[-1].timestamp.isoformat(),
            "reasons": [],
            "warnings": [f"Needs at least 30 {timeframe} candles to check the exit condition."],
        }
    levels = _levels(candles)
    latest = candles[-1]
    yellow = levels["ck_yellow_line"]
    if yellow is None:
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "status": "unknown",
            "trigger_date": latest.timestamp.isoformat(),
            "close": latest.close,
            "yellow_line": None,
            "reasons": [],
            "warnings": ["Yellow Chande Kroll line is unavailable for exit tracking."],
        }
    if latest.close < yellow:
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "status": "exit_triggered",
            "trigger_date": latest.timestamp.isoformat(),
            "close": latest.close,
            "yellow_line": yellow,
            "reasons": [f"{timeframe_label_text(timeframe)} candle closed below yellow Chande Kroll line."],
            "warnings": [],
        }
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "status": "open",
        "trigger_date": latest.timestamp.isoformat(),
        "close": latest.close,
        "yellow_line": yellow,
        "reasons": [f"{timeframe_label_text(timeframe)} candle remains above yellow Chande Kroll line."],
        "warnings": [],
    }


def _levels(candles: list[Candle]) -> dict[str, float | None]:
    closes = [candle.close for candle in candles]
    volumes = [candle.volume for candle in candles]
    ck_long, ck_short = _chande_kroll_stops(candles)
    yellow_candidates = [value for value in (ck_long, ck_short) if value is not None]
    upper, mid, lower = _donchian(candles, 20)
    avg_volume20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
    ichimoku = _ichimoku_levels(candles)
    return {
        "ema9": ema(closes, 9),
        "ema26": ema(closes, 26),
        "ema89": ema(closes, 89),
        "vwap": _session_vwap(candles),
        "vwma20": _vwma(candles, 20),
        "donchian_upper20": upper,
        "donchian_mid20": mid,
        "donchian_lower20": lower,
        "volume_ratio20": candles[-1].volume / avg_volume20 if avg_volume20 else None,
        "atr14": atr(candles, 14),
        "yellow_line": max(value for value in (ck_long, ck_short) if value is not None) if ck_long is not None or ck_short is not None else None,
        "ck_yellow_line": min(yellow_candidates) if yellow_candidates else None,
        "ck_blue_line": max(yellow_candidates) if yellow_candidates else None,
        "rsi14": rsi(closes, 14),
        **ichimoku,
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


def _ichimoku_levels(candles: list[Candle]) -> dict[str, float | None]:
    if len(candles) < 52:
        return {
            "ichimoku_green": None,
            "ichimoku_pink": None,
            "ichimoku_cloud_top": None,
            "ichimoku_cloud_bottom": None,
        }
    tenkan = _midpoint(candles, 9)
    kijun = _midpoint(candles, 26)
    span_a = (tenkan + kijun) / 2
    span_b = _midpoint(candles, 52)
    return {
        "ichimoku_green": span_a,
        "ichimoku_pink": span_b,
        "ichimoku_cloud_top": max(span_a, span_b),
        "ichimoku_cloud_bottom": min(span_a, span_b),
    }


def _midpoint(candles: list[Candle], period: int) -> float:
    window = candles[-period:]
    return (max(candle.high for candle in window) + min(candle.low for candle in window)) / 2


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


def _purple_entry_snapshot(
    symbol: str,
    candles: list[Candle] | None,
    timeframe: str,
    entry_kind: str,
    purple_timeframe: str,
    purple_ema9: float,
    touch_timestamp: datetime,
    touch_tolerance_percent: float,
) -> dict[str, Any]:
    if not candles:
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "entry_kind": entry_kind,
            "status": "missing",
            "warnings": [f"Cached {timeframe} candles are needed for {entry_kind} entry status."],
            "reasons": [],
        }
    candles = sorted(candles, key=lambda candle: candle.timestamp)
    if len(candles) < 30:
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "entry_kind": entry_kind,
            "status": "insufficient",
            "trigger_date": candles[-1].timestamp.isoformat(),
            "warnings": [f"Needs at least 30 {timeframe} candles for {entry_kind} entry status."],
            "reasons": [],
        }
    levels = _levels(candles)
    latest = candles[-1]
    yellow = levels["ck_yellow_line"]
    blue = levels["ck_blue_line"]
    entry_ema9 = levels["ema9"]
    brown = levels["vwma20"]
    rsi14 = levels["rsi14"]
    reasons: list[str] = []
    warnings: list[str] = []
    if yellow is None:
        warnings.append("Yellow Chande Kroll line is unavailable.")
    close_above_yellow = latest.close > yellow if yellow is not None else False
    if close_above_yellow:
        reasons.append(f"{entry_kind.title()} timeframe candle closed above yellow Chande Kroll line.")
    else:
        warnings.append(f"{entry_kind.title()} timeframe candle has not closed above yellow Chande Kroll line.")
    yellow_above_brown = yellow > brown if yellow is not None and brown is not None else None
    if entry_kind == "final":
        if yellow_above_brown is True:
            reasons.append("Yellow Chande Kroll line is above brown VWMA20; Krishna marked this mandatory.")
        elif yellow_above_brown is False:
            warnings.append("Yellow Chande Kroll line is not above brown VWMA20; final entry is not active.")
    divergence = _bullish_rsi_divergence(candles)
    if divergence:
        reasons.append("Possible bullish RSI divergence is present.")
    close_above_blue = latest.close > blue if blue is not None else None
    if close_above_blue:
        reasons.append("Close is above the blue Chande Kroll line; Krishna marked this as rare/stronger.")
    blue_above_purple = blue > entry_ema9 if blue is not None and entry_ema9 is not None else None
    if blue_above_purple is True:
        reasons.append("Blue Chande Kroll is above purple EMA9 on the entry timeframe.")
    elif blue_above_purple is False:
        warnings.append("Blue Chande Kroll is not above purple EMA9 on the entry timeframe.")
    else:
        warnings.append("Blue/purple relationship is unavailable on the entry timeframe.")

    touch_index = _mapped_touch_index(
        candles,
        purple_timeframe=purple_timeframe,
        touch_timestamp=touch_timestamp,
        purple_ema9=purple_ema9,
        tolerance_percent=touch_tolerance_percent,
    )
    bars_since_touch = len(candles) - 1 - touch_index if touch_index is not None else None
    candle1 = candles[touch_index] if touch_index is not None else None
    candle2_index = touch_index + 1 if touch_index is not None else None
    candle2 = candles[candle2_index] if candle2_index is not None and candle2_index < len(candles) else None
    reference_lows_ready = candle1 is not None and candle2 is not None
    invalidation_level = min(candle1.low, candle2.low) if reference_lows_ready else None
    invalidation_candle = None
    if reference_lows_ready:
        invalidation_candle = next(
            (
                candle
                for candle in candles[candle2_index + 1 :]
                if candle.low < invalidation_level
            ),
            None,
        )
    setup_discarded = invalidation_candle is not None
    if touch_index is None:
        warnings.append("The higher-timeframe purple touch could not be mapped to this entry timeframe.")
    elif candle2 is None:
        warnings.append("Candle 1 is the purple-touch candle; waiting for Candle 2 to close before entry confirmation.")
    elif setup_discarded:
        warnings.append(
            "Setup discarded from Candle 3 onward because price broke the lower of Candle 1 and Candle 2 lows."
        )
    else:
        reasons.append(
            "Candle 2 has closed and no later candle has broken the lower of Candle 1 and Candle 2 lows; setup remains active."
        )

    entry_rules_pass = (
        close_above_yellow
        and blue_above_purple is True
        and reference_lows_ready
        and not setup_discarded
        and (entry_kind != "final" or yellow_above_brown is True)
    )
    if entry_rules_pass:
        status = "entry_candidate"
    elif setup_discarded:
        status = "discarded"
    else:
        status = "wait"
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "entry_kind": entry_kind,
        "status": status,
        "trigger_date": latest.timestamp.isoformat(),
        "close": latest.close,
        "yellow_line": yellow,
        "brown_vwma20": brown,
        "blue_line": blue,
        "rsi14": rsi14,
        "close_above_yellow": close_above_yellow,
        "yellow_above_brown": yellow_above_brown,
        "close_above_blue": close_above_blue,
        "blue_above_purple": blue_above_purple,
        "rsi_divergence": divergence,
        "touch_timestamp": candle1.timestamp.isoformat() if candle1 is not None else None,
        "bars_since_touch": bars_since_touch,
        "candle1_low": candle1.low if candle1 is not None else None,
        "candle2_timestamp": candle2.timestamp.isoformat() if candle2 is not None else None,
        "candle2_low": candle2.low if candle2 is not None else None,
        "reference_lows_ready": reference_lows_ready,
        "invalidation_level": invalidation_level,
        "setup_discarded": setup_discarded,
        "invalidation_timestamp": invalidation_candle.timestamp.isoformat() if invalidation_candle else None,
        "entry_price_reference": latest.close if entry_rules_pass else None,
        "exit_rule": f"Exit/review if {timeframe} candle closes below yellow Chande Kroll line.",
        "reasons": reasons,
        "warnings": warnings,
    }


def _bullish_rsi_divergence(candles: list[Candle], lookback: int = 24) -> bool:
    if len(candles) < max(lookback, 16):
        return False
    closes = [candle.close for candle in candles]
    first_half = candles[-lookback : -lookback // 2]
    second_half = candles[-lookback // 2 :]
    first_index = min(range(len(first_half)), key=lambda index: first_half[index].low)
    second_index = min(range(len(second_half)), key=lambda index: second_half[index].low)
    first_candle_index = len(candles) - lookback + first_index
    second_candle_index = len(candles) - (lookback // 2) + second_index
    first_rsi = rsi(closes[: first_candle_index + 1], 14)
    second_rsi = rsi(closes[: second_candle_index + 1], 14)
    return (
        first_rsi is not None
        and second_rsi is not None
        and second_half[second_index].low <= first_half[first_index].low
        and second_rsi > first_rsi
    )


def _purple_higher_confirmation(
    profile: KrishnaPurpleProfile,
    candles: list[Candle] | None,
) -> dict[str, Any]:
    rule = (
        f"{profile.label} requires the latest {profile.confirmation_label} candle "
        + ("open or close" if profile.confirmation_open_or_close else "close")
        + " above its blue Chande Kroll line."
    )
    if not candles:
        return {
            "timeframe": profile.confirmation_timeframe,
            "label": profile.confirmation_label,
            "status": "missing",
            "rule": rule,
            "open": None,
            "close": None,
            "blue_line": None,
            "reasons": [],
            "warnings": [f"{profile.confirmation_label} candles are unavailable for the mandatory confirmation."],
        }
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    latest = ordered[-1]
    blue = _levels(ordered)["ck_blue_line"]
    if blue is None:
        return {
            "timeframe": profile.confirmation_timeframe,
            "label": profile.confirmation_label,
            "status": "insufficient",
            "rule": rule,
            "open": latest.open,
            "close": latest.close,
            "blue_line": None,
            "reasons": [],
            "warnings": [f"{profile.confirmation_label} blue Chande Kroll needs more candle history."],
        }
    passed = latest.close > blue or (profile.confirmation_open_or_close and latest.open > blue)
    value_text = "open or close" if profile.confirmation_open_or_close else "close"
    return {
        "timeframe": profile.confirmation_timeframe,
        "label": profile.confirmation_label,
        "status": "pass" if passed else "block",
        "rule": rule,
        "timestamp": latest.timestamp.isoformat(),
        "open": latest.open,
        "close": latest.close,
        "blue_line": blue,
        "reasons": [f"{profile.confirmation_label} {value_text} is above blue Chande Kroll."] if passed else [],
        "warnings": [f"{profile.confirmation_label} {value_text} is not above blue Chande Kroll."] if not passed else [],
    }


def _approaches_purple_from_blue(
    candles: list[Candle],
    purple_ema9: float,
    blue_line: float | None,
    config: KrishnaPurpleTouchConfig,
) -> bool | None:
    if blue_line is None or len(candles) < 2:
        return None
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    window = ordered[-max(2, config.approach_lookback_candles) :]
    latest = window[-1]
    prior = window[:-1]
    tolerance = config.approach_tolerance_percent / 100
    came_from_blue = any(candle.close >= blue_line * (1 - tolerance) for candle in prior)
    moved_toward_purple = latest.close < max(candle.close for candle in prior) or latest.close <= purple_ema9 * (1 + tolerance)
    return came_from_blue and moved_toward_purple


def _mapped_touch_index(
    candles: list[Candle],
    purple_timeframe: str,
    touch_timestamp: datetime,
    purple_ema9: float,
    tolerance_percent: float,
) -> int | None:
    tolerance = tolerance_percent / 100
    matches = []
    for index, candle in enumerate(candles):
        if not _same_period(candle.timestamp, touch_timestamp, purple_timeframe):
            continue
        if candle.low <= purple_ema9 * (1 + tolerance) and candle.high >= purple_ema9 * (1 - tolerance):
            matches.append(index)
    return matches[-1] if matches else None


def _same_period(left: datetime, right: datetime, timeframe: str) -> bool:
    normalized = _normalize_purple_timeframe(timeframe)
    if normalized == "month":
        return (left.year, left.month) == (right.year, right.month)
    if normalized == "week":
        return left.isocalendar()[:2] == right.isocalendar()[:2]
    return left.date() == right.date()


def _relation(left: float | None, right: float | None) -> str:
    if left is None or right is None:
        return "unknown"
    if left > right:
        return "above"
    if left < right:
        return "below"
    return "at"


def _purple_touch_distance_percent(candle: Candle, ema9: float) -> float | None:
    if ema9 == 0:
        return None
    if candle.low <= ema9 <= candle.high:
        return 0.0
    nearest = candle.low if ema9 < candle.low else candle.high
    return abs((nearest - ema9) / ema9) * 100


def _purple_touch_reason(
    range_distance_percent: float | None,
    tolerance_percent: float,
    prefix: str = "",
) -> str:
    if range_distance_percent is not None and range_distance_percent <= 1e-9:
        detail = "purple EMA9 is inside the higher-timeframe candle high/low range (exact 0.00% touch)."
    else:
        detail = (
            f"higher-timeframe candle is within {range_distance_percent:.2f}% of purple EMA9 "
            f"(maximum {tolerance_percent:.2f}%)."
            if range_distance_percent is not None
            else f"higher-timeframe candle is within the {tolerance_percent:.2f}% purple EMA9 zone."
        )
    return prefix + detail[0].upper() + detail[1:]


def _purple_close_distance_percent(candle: Candle, ema9: float) -> float | None:
    if ema9 == 0:
        return None
    return abs((candle.close - ema9) / ema9) * 100


def _normalize_purple_timeframe(value: str) -> str:
    key = str(value or "week").strip().lower().replace("_", "").replace("-", "")
    aliases = {
        "monthly": "month",
        "month": "month",
        "1m": "month",
        "weekly": "week",
        "week": "week",
        "1w": "week",
        "daily": "day",
        "day": "day",
        "1d": "day",
    }
    if key not in aliases:
        raise ValueError("Use purple timeframe monthly, weekly, or daily.")
    return aliases[key]


def _confidence(score: int, warnings: list[str]) -> str:
    if warnings:
        return "medium" if score >= 75 else "low"
    if score >= 75:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def timeframe_label_text(timeframe: str) -> str:
    labels = {
        "month": "Monthly",
        "5month": "5-month",
        "week": "Weekly",
        "day": "Daily",
        "120minute": "2-hour",
        "60minute": "1-hour",
        "30minute": "30-minute",
        "15minute": "15-minute",
        "10minute": "10-minute",
    }
    return labels.get(timeframe, timeframe)
