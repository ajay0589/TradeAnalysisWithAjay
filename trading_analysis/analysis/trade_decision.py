from __future__ import annotations

from dataclasses import dataclass

from trading_analysis.analysis.market_structure import MarketStructure
from trading_analysis.analysis.options import OptionChainAnalysis
from trading_analysis.analysis.relative_strength import RelativeStrengthReport
from trading_analysis.models import TechnicalSignal


@dataclass(frozen=True)
class TradeDecision:
    symbol: str
    bias: str
    decision: str
    preferred_strategy: str
    score: int
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


def build_trade_decision(
    symbol: str,
    daily_technical: TechnicalSignal,
    daily_structure: MarketStructure,
    hourly_technical: TechnicalSignal | None = None,
    hourly_structure: MarketStructure | None = None,
    relative_strength: RelativeStrengthReport | None = None,
    option_chain: OptionChainAnalysis | None = None,
) -> TradeDecision:
    score = 50
    reasons: list[str] = []
    warnings: list[str] = []

    score += _bias_points(daily_technical.trend, daily_structure.trend, reasons)

    if hourly_technical and hourly_structure:
        score += _bias_points(hourly_technical.trend, hourly_structure.trend, reasons, prefix="60m ")
    else:
        warnings.append("60-minute data not available")

    rs_signal = relative_strength.stock_vs_nifty if relative_strength else None
    if rs_signal:
        if rs_signal.label == "outperforming":
            score += 10
            reasons.append("Stock outperforming Nifty")
        elif rs_signal.label == "underperforming":
            score -= 10
            reasons.append("Stock underperforming Nifty")
    else:
        warnings.append("Nifty relative strength not available")

    sector_signal = relative_strength.stock_vs_sector if relative_strength else None
    if sector_signal:
        if sector_signal.label == "outperforming":
            score += 5
            reasons.append("Stock outperforming sector")
        elif sector_signal.label == "underperforming":
            score -= 5
            reasons.append("Stock underperforming sector")
    else:
        warnings.append("Sector relative strength not available")

    if option_chain:
        score += _option_chain_points(option_chain, reasons, warnings)
    else:
        warnings.append("Option-chain context not available")

    score = max(0, min(100, score))
    bias = _bias_from_score(score)
    decision = _decision_from_score(score, has_hourly=bool(hourly_technical and hourly_structure), has_option_chain=bool(option_chain))
    strategy = _strategy_from_context(bias, daily_structure.trend, option_chain)

    return TradeDecision(
        symbol=symbol.upper(),
        bias=bias,
        decision=decision,
        preferred_strategy=strategy,
        score=score,
        reasons=tuple(reasons[:8]),
        warnings=tuple(warnings),
    )


def _bias_points(technical_trend: str, structure_trend: str, reasons: list[str], prefix: str = "") -> int:
    points = 0
    if technical_trend == "bullish":
        points += 10
        reasons.append(f"{prefix}technical trend bullish")
    elif technical_trend == "bearish":
        points -= 10
        reasons.append(f"{prefix}technical trend bearish")

    if structure_trend == "uptrend":
        points += 15
        reasons.append(f"{prefix}market structure uptrend")
    elif structure_trend == "downtrend":
        points -= 15
        reasons.append(f"{prefix}market structure downtrend")
    else:
        reasons.append(f"{prefix}market structure range")
    return points


def _option_chain_points(
    option_chain: OptionChainAnalysis,
    reasons: list[str],
    warnings: list[str],
) -> int:
    points = 0
    spot = option_chain.spot_price
    pcr = option_chain.pcr_oi
    if pcr is not None:
        if pcr >= 1.1:
            points += 5
            reasons.append("Option chain PCR supportive")
        elif pcr <= 0.8:
            points -= 5
            reasons.append("Option chain PCR weak")

    if spot and option_chain.highest_call_oi_strike and option_chain.highest_call_oi_strike <= spot * 1.01:
        points -= 5
        reasons.append("High call OI near spot")
    if spot and option_chain.highest_put_oi_strike and option_chain.highest_put_oi_strike >= spot * 0.99:
        points += 5
        reasons.append("High put OI near spot")

    buildup_labels = {row.buildup for row in option_chain.rows}
    if "Needs previous OI snapshot" in buildup_labels:
        warnings.append("Build-up classification needs a previous option snapshot")
    if "Short build-up" in buildup_labels:
        points -= 5
        reasons.append("Short build-up present")
    if "Long build-up" in buildup_labels:
        points += 5
        reasons.append("Long build-up present")
    return points


def _bias_from_score(score: int) -> str:
    if score >= 65:
        return "bullish"
    if score <= 40:
        return "bearish"
    return "neutral"


def _decision_from_score(score: int, has_hourly: bool, has_option_chain: bool) -> str:
    if score >= 70:
        if not has_option_chain:
            return "watch / needs option-chain confirmation"
        if not has_hourly:
            return "watch / needs 60m confirmation"
        return "trade candidate"
    if score <= 35:
        if not has_option_chain:
            return "watch / needs option-chain confirmation"
        if not has_hourly:
            return "watch / needs 60m confirmation"
        return "bearish trade candidate"
    return "watch / no trade"


def _strategy_from_context(
    bias: str,
    structure_trend: str,
    option_chain: OptionChainAnalysis | None,
) -> str:
    if option_chain is None:
        return "Directional bias only; run option-chain before trade"
    if bias == "bullish":
        if structure_trend == "range":
            return "Bull call spread near support; avoid chasing breakout"
        return "Bull call spread or call diagonal after entry trigger"
    if bias == "bearish":
        if structure_trend == "range":
            return "Bear put spread near resistance; avoid selling breakdown late"
        return "Bear put spread or put diagonal after entry trigger"
    if option_chain and option_chain.pcr_oi is not None and 0.8 < option_chain.pcr_oi < 1.2:
        return "Range strategy candidate only if IV/risk supports it"
    return "No directional options trade"
