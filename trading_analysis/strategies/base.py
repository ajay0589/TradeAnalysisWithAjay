from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Callable

from trading_analysis.models import Candle


@dataclass(frozen=True)
class StrategyParameter:
    name: str
    label: str
    type: str
    default: Any
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategySignal:
    symbol: str
    strategy_id: str
    signal_date: date
    side: str
    score: int
    confidence: str
    entry_type: str
    entry_price: float | None
    stop_loss: float | None
    target: float | None
    invalidation: float | None
    reasons: list[str]
    warnings: list[str] = field(default_factory=list)
    indicators: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyContext:
    symbol: str
    candles: list[Candle]
    params: dict[str, Any]


GenerateSignal = Callable[[str, list[Candle], dict[str, Any]], StrategySignal | None]


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    label: str
    description: str
    direction: str
    default_timeframe: str
    min_candles: int
    default_params: dict[str, Any]
    parameter_schema: list[StrategyParameter]
    generate_signal: GenerateSignal

    def merged_params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {**self.default_params, **(params or {})}

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "label": self.label,
            "description": self.description,
            "direction": self.direction,
            "default_timeframe": self.default_timeframe,
            "min_candles": self.min_candles,
            "default_params": dict(self.default_params),
            "parameter_schema": [parameter.to_dict() for parameter in self.parameter_schema],
        }
