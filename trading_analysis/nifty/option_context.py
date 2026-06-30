from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from trading_analysis.analysis.options import OptionChainAnalysis, OptionChainRow
from trading_analysis.nifty.models import NiftyOptionContext


def build_nifty_option_context(
    weekly_chain: OptionChainAnalysis | None,
    monthly_chain: OptionChainAnalysis | None = None,
    previous_weekly_chain: OptionChainAnalysis | None = None,
    previous_monthly_chain: OptionChainAnalysis | None = None,
    spot: float | None = None,
) -> NiftyOptionContext:
    notes: list[str] = []
    warnings: list[str] = []
    active = weekly_chain or monthly_chain
    if active is None:
        warnings.append("NIFTY option-chain context is unavailable.")
        return NiftyOptionContext(
            symbol="NIFTY",
            as_of=None,
            spot=spot,
            expiries=[],
            selected_weekly_expiry=None,
            selected_monthly_expiry=None,
            weekly_chain_summary=None,
            monthly_chain_summary=None,
            pcr_oi=None,
            pcr_volume=None,
            max_pain=None,
            atm_strike=None,
            atm_iv=None,
            atm_iv_change=None,
            total_ce_oi=0,
            total_pe_oi=0,
            total_ce_oi_change=None,
            total_pe_oi_change=None,
            highest_ce_oi_strikes=[],
            highest_pe_oi_strikes=[],
            ce_writing_strikes=[],
            pe_writing_strikes=[],
            ce_unwinding_strikes=[],
            pe_unwinding_strikes=[],
            support_by_oi=None,
            resistance_by_oi=None,
            option_bias="unclear",
            notes=notes,
            warnings=warnings,
        )

    active_spot = spot if spot is not None else active.spot_price
    rows = list(active.rows)
    ce_rows = [row for row in rows if row.option_type == "CE"]
    pe_rows = [row for row in rows if row.option_type == "PE"]
    ce_oi = sum(row.oi for row in ce_rows)
    pe_oi = sum(row.oi for row in pe_rows)
    ce_volume = sum(row.volume for row in ce_rows)
    pe_volume = sum(row.volume for row in pe_rows)
    ce_change = _sum_optional(row.oi_change for row in ce_rows)
    pe_change = _sum_optional(row.oi_change for row in pe_rows)
    support = _support_by_oi(pe_rows, active_spot)
    resistance = _resistance_by_oi(ce_rows, active_spot)
    atm_strike = _atm_strike(rows, active_spot)
    option_bias = _option_bias(
        pcr_oi=(pe_oi / ce_oi) if ce_oi else None,
        ce_change=ce_change,
        pe_change=pe_change,
        support=support,
        resistance=resistance,
        spot=active_spot,
        rows=rows,
        notes=notes,
    )
    if weekly_chain and monthly_chain:
        notes.append("Weekly and monthly option contexts were both available for comparison.")

    return NiftyOptionContext(
        symbol=active.symbol,
        as_of=datetime.now(),
        spot=active_spot,
        expiries=_expiries(weekly_chain, monthly_chain),
        selected_weekly_expiry=weekly_chain.expiry.isoformat() if weekly_chain else None,
        selected_monthly_expiry=monthly_chain.expiry.isoformat() if monthly_chain else None,
        weekly_chain_summary=_summary(weekly_chain),
        monthly_chain_summary=_summary(monthly_chain),
        pcr_oi=(pe_oi / ce_oi) if ce_oi else None,
        pcr_volume=(pe_volume / ce_volume) if ce_volume else None,
        max_pain=active.max_pain,
        atm_strike=atm_strike,
        atm_iv=active.atm_iv,
        atm_iv_change=active.atm_iv_change,
        total_ce_oi=ce_oi,
        total_pe_oi=pe_oi,
        total_ce_oi_change=ce_change,
        total_pe_oi_change=pe_change,
        highest_ce_oi_strikes=_top_strikes(ce_rows, "oi"),
        highest_pe_oi_strikes=_top_strikes(pe_rows, "oi"),
        ce_writing_strikes=_top_change_strikes(ce_rows, positive=True),
        pe_writing_strikes=_top_change_strikes(pe_rows, positive=True),
        ce_unwinding_strikes=_top_change_strikes(ce_rows, positive=False),
        pe_unwinding_strikes=_top_change_strikes(pe_rows, positive=False),
        support_by_oi=support,
        resistance_by_oi=resistance,
        option_bias=option_bias,
        notes=notes,
        warnings=warnings,
    )


def _summary(chain: OptionChainAnalysis | None) -> dict[str, Any] | None:
    if chain is None:
        return None
    payload = asdict(chain)
    payload["expiry"] = chain.expiry.isoformat()
    payload["rows"] = []
    return payload


def _expiries(*chains: OptionChainAnalysis | None) -> list[str]:
    output: list[str] = []
    for chain in chains:
        if chain and chain.expiry.isoformat() not in output:
            output.append(chain.expiry.isoformat())
    return output


def _sum_optional(values) -> int | None:
    seen = [value for value in values if value is not None]
    return sum(seen) if seen else None


def _atm_strike(rows: list[OptionChainRow], spot: float | None) -> float | None:
    strikes = sorted({row.strike for row in rows})
    if not strikes:
        return None
    if spot is None:
        return strikes[len(strikes) // 2]
    return min(strikes, key=lambda strike: abs(strike - spot))


def _top_strikes(rows: list[OptionChainRow], attr: str, count: int = 3) -> list[float]:
    return [row.strike for row in sorted(rows, key=lambda row: getattr(row, attr) or 0, reverse=True)[:count]]


def _top_change_strikes(rows: list[OptionChainRow], positive: bool, count: int = 3) -> list[float]:
    filtered = [row for row in rows if row.oi_change is not None and (row.oi_change > 0 if positive else row.oi_change < 0)]
    return [row.strike for row in sorted(filtered, key=lambda row: abs(row.oi_change or 0), reverse=True)[:count]]


def _support_by_oi(rows: list[OptionChainRow], spot: float | None) -> float | None:
    eligible = [row for row in rows if spot is None or row.strike <= spot]
    if not eligible:
        eligible = rows
    return max(eligible, key=lambda row: row.oi).strike if eligible else None


def _resistance_by_oi(rows: list[OptionChainRow], spot: float | None) -> float | None:
    eligible = [row for row in rows if spot is None or row.strike >= spot]
    if not eligible:
        eligible = rows
    return max(eligible, key=lambda row: row.oi).strike if eligible else None


def _option_bias(
    pcr_oi: float | None,
    ce_change: int | None,
    pe_change: int | None,
    support: float | None,
    resistance: float | None,
    spot: float | None,
    rows: list[OptionChainRow],
    notes: list[str],
) -> str:
    if not rows or pcr_oi is None:
        return "unclear"
    ce_writing = ce_change is not None and ce_change > 0
    pe_writing = pe_change is not None and pe_change > 0
    if support and resistance and spot and support < spot < resistance and 0.8 <= pcr_oi <= 1.25:
        notes.append("OI support and resistance create a visible range around spot.")
        return "neutral"
    if pe_writing and not ce_writing and pcr_oi >= 1.0:
        notes.append("Put-side OI change dominates current chain snapshot.")
        return "bullish"
    if ce_writing and not pe_writing and pcr_oi <= 1.0:
        notes.append("Call-side OI change dominates current chain snapshot.")
        return "bearish"
    if abs((pe_change or 0) - (ce_change or 0)) > max(sum(row.oi for row in rows) * 0.05, 1_000_000):
        return "volatile"
    return "unclear"
