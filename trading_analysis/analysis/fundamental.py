from __future__ import annotations

from trading_analysis.models import FundamentalSignal, FundamentalSnapshot


def analyze_fundamentals(snapshot: FundamentalSnapshot) -> FundamentalSignal:
    score = 50
    reasons: list[str] = []

    if snapshot.roe_percent is not None:
        if snapshot.roe_percent >= 20:
            score += 15
            reasons.append("High ROE")
        elif snapshot.roe_percent >= 12:
            score += 8
            reasons.append("Healthy ROE")
        else:
            score -= 8
            reasons.append("Low ROE")

    if snapshot.debt_to_equity is not None:
        if snapshot.debt_to_equity <= 0.3:
            score += 10
            reasons.append("Low debt")
        elif snapshot.debt_to_equity > 1.0:
            score -= 12
            reasons.append("High debt")

    if snapshot.sales_growth_yoy_percent is not None:
        if snapshot.sales_growth_yoy_percent >= 10:
            score += 8
            reasons.append("Double-digit sales growth")
        elif snapshot.sales_growth_yoy_percent < 0:
            score -= 8
            reasons.append("Sales contraction")

    if snapshot.profit_growth_yoy_percent is not None:
        if snapshot.profit_growth_yoy_percent >= 10:
            score += 10
            reasons.append("Profit growth")
        elif snapshot.profit_growth_yoy_percent < 0:
            score -= 12
            reasons.append("Profit contraction")

    if snapshot.pledged_percent is not None:
        if snapshot.pledged_percent == 0:
            score += 5
            reasons.append("No pledged promoter holding")
        elif snapshot.pledged_percent > 5:
            score -= 15
            reasons.append("Promoter pledge risk")

    if not reasons:
        reasons.append("Fundamental data not supplied")

    return FundamentalSignal(score=max(0, min(100, score)), reasons=tuple(reasons))

