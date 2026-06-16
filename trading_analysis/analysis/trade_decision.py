from __future__ import annotations

from dataclasses import dataclass

from trading_analysis.analysis.market_structure import MarketStructure
from trading_analysis.analysis.options import OptionChainAnalysis
from trading_analysis.analysis.relative_strength import RelativeStrengthReport
from trading_analysis.models import TechnicalSignal


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    points: int
    detail: str


@dataclass(frozen=True)
class ScoreBreakdown:
    base_score: int
    raw_score: int
    final_score: int
    components: tuple[ScoreComponent, ...]


@dataclass(frozen=True)
class TradeDecision:
    symbol: str
    bias: str
    decision: str
    preferred_strategy: str
    score: int
    score_breakdown: ScoreBreakdown
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
    components: list[ScoreComponent] = []

    daily_points, daily_details = _bias_component(daily_technical.trend, daily_structure.trend)
    score += daily_points
    reasons.extend(daily_details)
    components.append(
        ScoreComponent(
            "Daily direction",
            daily_points,
            "; ".join(daily_details) or "Daily trend/structure neutral",
        )
    )

    if hourly_technical and hourly_structure:
        hourly_points, hourly_details = _bias_component(hourly_technical.trend, hourly_structure.trend, prefix="60m ")
        score += hourly_points
        reasons.extend(hourly_details)
        components.append(
            ScoreComponent(
                "60-minute confirmation",
                hourly_points,
                "; ".join(hourly_details) or "60-minute trend/structure neutral",
            )
        )
    else:
        warnings.append("60-minute data not available")
        components.append(ScoreComponent("60-minute confirmation", 0, "60-minute data not available"))

    rs_signal = relative_strength.stock_vs_nifty if relative_strength else None
    if rs_signal:
        points = 0
        if rs_signal.label == "outperforming":
            points = 10
            reasons.append("Stock outperforming Nifty")
        elif rs_signal.label == "underperforming":
            points = -10
            reasons.append("Stock underperforming Nifty")
        score += points
        components.append(
            ScoreComponent(
                "Stock vs Nifty RS",
                points,
                f"{rs_signal.label}; relative return {_fmt_percent(rs_signal.relative_return_percent)}",
            )
        )
    else:
        warnings.append("Nifty relative strength not available")
        components.append(ScoreComponent("Stock vs Nifty RS", 0, "Nifty relative strength not available"))

    sector_signal = relative_strength.stock_vs_sector if relative_strength else None
    if sector_signal:
        points = 0
        if sector_signal.label == "outperforming":
            points = 5
            reasons.append("Stock outperforming sector")
        elif sector_signal.label == "underperforming":
            points = -5
            reasons.append("Stock underperforming sector")
        score += points
        components.append(
            ScoreComponent(
                "Stock vs Sector RS",
                points,
                f"{sector_signal.label}; relative return {_fmt_percent(sector_signal.relative_return_percent)}",
            )
        )
    else:
        warnings.append("Sector relative strength not available")
        components.append(ScoreComponent("Stock vs Sector RS", 0, "Sector relative strength not available"))

    if option_chain:
        option_points, option_details = _option_chain_component(option_chain, warnings)
        score += option_points
        reasons.extend(option_details)
        components.append(
            ScoreComponent(
                "Option-chain context",
                option_points,
                "; ".join(option_details) or "Option chain neutral",
            )
        )
    else:
        warnings.append("Option-chain context not available")
        components.append(ScoreComponent("Option-chain context", 0, "Option-chain context not available"))

    raw_score = score
    final_score = max(0, min(100, raw_score))
    bias = _bias_from_score(final_score)
    decision = _decision_from_score(final_score, has_hourly=bool(hourly_technical and hourly_structure), has_option_chain=bool(option_chain))
    strategy = _strategy_from_context(bias, daily_structure.trend, option_chain)

    return TradeDecision(
        symbol=symbol.upper(),
        bias=bias,
        decision=decision,
        preferred_strategy=strategy,
        score=final_score,
        score_breakdown=ScoreBreakdown(
            base_score=50,
            raw_score=raw_score,
            final_score=final_score,
            components=tuple(components),
        ),
        reasons=tuple(reasons[:8]),
        warnings=tuple(warnings),
    )


def _bias_component(technical_trend: str, structure_trend: str, prefix: str = "") -> tuple[int, list[str]]:
    points = 0
    details: list[str] = []
    if technical_trend == "bullish":
        points += 10
        details.append(f"{prefix}technical trend bullish")
    elif technical_trend == "bearish":
        points -= 10
        details.append(f"{prefix}technical trend bearish")

    if structure_trend == "uptrend":
        points += 15
        details.append(f"{prefix}market structure uptrend")
    elif structure_trend == "downtrend":
        points -= 15
        details.append(f"{prefix}market structure downtrend")
    else:
        details.append(f"{prefix}market structure range")
    return points, details


def _option_chain_component(
    option_chain: OptionChainAnalysis,
    warnings: list[str],
) -> tuple[int, list[str]]:
    points = 0
    details: list[str] = []
    spot = option_chain.spot_price
    pcr = option_chain.pcr_oi
    if pcr is not None:
        if pcr >= 1.1:
            points += 5
            details.append("Option chain PCR supportive")
        elif pcr <= 0.8:
            points -= 5
            details.append("Option chain PCR weak")

    if spot and option_chain.highest_call_oi_strike and option_chain.highest_call_oi_strike <= spot * 1.01:
        points -= 5
        details.append("High call OI near spot")
    if spot and option_chain.highest_put_oi_strike and option_chain.highest_put_oi_strike >= spot * 0.99:
        points += 5
        details.append("High put OI near spot")

    buildup_labels = {row.buildup for row in option_chain.rows}
    if "Needs previous OI snapshot" in buildup_labels:
        warnings.append("Build-up classification needs a previous option snapshot")
    if "Short build-up" in buildup_labels:
        points -= 5
        details.append("Short build-up present")
    if "Long build-up" in buildup_labels:
        points += 5
        details.append("Long build-up present")
    return points, details


def _bias_from_score(score: int) -> str:
    if score >= 65:
        return "bullish"
    if score <= 40:
        return "bearish"
    return "neutral"


def _fmt_percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}%"


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
