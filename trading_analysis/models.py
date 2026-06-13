from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    open_interest: int | None = None


@dataclass(frozen=True)
class FundamentalSnapshot:
    roe_percent: float | None = None
    debt_to_equity: float | None = None
    sales_growth_yoy_percent: float | None = None
    profit_growth_yoy_percent: float | None = None
    pledged_percent: float | None = None


@dataclass(frozen=True)
class WatchlistItem:
    symbol: str
    exchange: str = "NSE"
    instrument_type: str = "EQ"
    data_file: str | None = None
    notes: str = ""
    fundamentals: FundamentalSnapshot = field(default_factory=FundamentalSnapshot)


@dataclass(frozen=True)
class TechnicalSignal:
    close: float
    sma20: float | None
    ema20: float | None
    rsi14: float | None
    atr14: float | None
    volume_ratio20: float | None
    support20: float | None
    resistance20: float | None
    trend: str
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class FundamentalSignal:
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CombinedSignal:
    symbol: str
    score: int
    label: str
    technical: TechnicalSignal
    fundamental: FundamentalSignal
    notes: tuple[str, ...]


def fundamentals_from_mapping(values: dict[str, Any] | None) -> FundamentalSnapshot:
    values = values or {}
    return FundamentalSnapshot(
        roe_percent=_optional_float(values.get("roe_percent")),
        debt_to_equity=_optional_float(values.get("debt_to_equity")),
        sales_growth_yoy_percent=_optional_float(values.get("sales_growth_yoy_percent")),
        profit_growth_yoy_percent=_optional_float(values.get("profit_growth_yoy_percent")),
        pledged_percent=_optional_float(values.get("pledged_percent")),
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)

