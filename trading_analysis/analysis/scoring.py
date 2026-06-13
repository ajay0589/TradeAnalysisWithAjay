from __future__ import annotations

from trading_analysis.models import CombinedSignal, FundamentalSignal, TechnicalSignal


def combine_signals(
    symbol: str,
    technical: TechnicalSignal,
    fundamental: FundamentalSignal,
    notes: tuple[str, ...] = (),
) -> CombinedSignal:
    score = round((technical.score * 0.65) + (fundamental.score * 0.35))
    return CombinedSignal(
        symbol=symbol,
        score=score,
        label=_label(score, technical.trend),
        technical=technical,
        fundamental=fundamental,
        notes=notes,
    )


def _label(score: int, trend: str) -> str:
    if score >= 75 and trend == "bullish":
        return "High-quality bullish watch"
    if score >= 65:
        return "Constructive watch"
    if score >= 50:
        return "Neutral / wait"
    return "Weak / avoid"

