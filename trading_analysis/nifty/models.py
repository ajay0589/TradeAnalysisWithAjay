from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class NiftyTechnicalContext:
    symbol: str
    as_of: datetime | None
    spot: float | None
    timeframe: str
    trend_intraday: str
    trend_swing: str
    trend_positional: str
    bias_intraday: str
    bias_swing: str
    bias_positional: str
    support_levels: list[float]
    resistance_levels: list[float]
    vwap: float | None
    ema20: float | None
    ema50: float | None
    sma200: float | None
    rsi14: float | None
    atr14: float | None
    previous_day_high: float | None
    previous_day_low: float | None
    previous_day_close: float | None
    day_open: float | None
    candle_signal: str
    market_structure: str
    factors: list[dict[str, Any]]
    notes: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class NiftyOptionContext:
    symbol: str
    as_of: datetime | None
    spot: float | None
    expiries: list[str]
    selected_weekly_expiry: str | None
    selected_monthly_expiry: str | None
    weekly_chain_summary: dict[str, Any] | None
    monthly_chain_summary: dict[str, Any] | None
    pcr_oi: float | None
    pcr_volume: float | None
    max_pain: float | None
    atm_strike: float | None
    atm_iv: float | None
    atm_iv_change: float | None
    total_ce_oi: int
    total_pe_oi: int
    total_ce_oi_change: int | None
    total_pe_oi_change: int | None
    highest_ce_oi_strikes: list[float]
    highest_pe_oi_strikes: list[float]
    ce_writing_strikes: list[float]
    pe_writing_strikes: list[float]
    ce_unwinding_strikes: list[float]
    pe_unwinding_strikes: list[float]
    support_by_oi: float | None
    resistance_by_oi: float | None
    option_bias: str
    notes: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class NiftyIVContext:
    symbol: str
    as_of: datetime | None
    atm_iv: float | None
    iv_change: float | None
    iv_rank_lookback_days: int
    iv_rank: float | None
    iv_percentile: float | None
    iv_min: float | None
    iv_max: float | None
    iv_mean: float | None
    iv_regime: str
    enough_history: bool
    notes: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class NiftyStrategyCandidate:
    strategy_id: str
    label: str
    horizon: str
    structure: str
    suitability_score: int
    confidence: str
    required_view: str
    expiry_plan: str
    legs: list[dict[str, Any]]
    max_profit_note: str
    max_loss_note: str
    breakeven_note: str
    margin_note: str
    best_when: str
    avoid_when: str
    adjustment_notes: str
    reasons: list[str]
    risks: list[str]
    required_confirmations: list[str]


@dataclass(frozen=True)
class NiftyDeskResult:
    symbol: str
    as_of: datetime | None
    mode: str
    technical: NiftyTechnicalContext | None
    options: NiftyOptionContext | None
    iv: NiftyIVContext | None
    candidates: list[NiftyStrategyCandidate]
    summary: dict[str, Any]
    warnings: list[str]
    errors: list[str]


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return to_jsonable(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
