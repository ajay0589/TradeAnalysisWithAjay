from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BacktestConfig:
    strategy_id: str
    timeframe: str = "day"
    from_date: str | None = None
    to_date: str | None = None
    days: int | None = None
    symbols: list[str] | None = None
    strategy_params: dict[str, Any] = field(default_factory=dict)
    entry: str = "next_open"
    entry_valid_bars: int = 3
    holding_bars: int = 10
    stop_type: str = "none"
    stop_percent: float | None = None
    stop_atr: float | None = None
    target_type: str = "none"
    target_percent: float | None = None
    target_atr: float | None = None
    target_r_multiple: float | None = None
    allow_overlap: bool = False
    slippage_bps: float = 0.0
    brokerage_bps: float = 0.0
    capital: float = 100000.0
    risk_per_trade_percent: float = 1.0
    position_sizing: str = "fixed_capital"
    fixed_quantity: int = 1
    fixed_capital: float | None = None

    @classmethod
    def from_mapping(
        cls,
        strategy_id: str,
        timeframe: str = "day",
        from_date: str | None = None,
        to_date: str | None = None,
        days: int | None = None,
        symbols: list[str] | None = None,
        strategy_params: dict[str, Any] | None = None,
        backtest_params: dict[str, Any] | None = None,
    ) -> "BacktestConfig":
        values = dict(backtest_params or {})
        allowed = set(cls.__dataclass_fields__) - {"strategy_id", "timeframe", "from_date", "to_date", "days", "symbols", "strategy_params"}
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"Unknown backtest parameter(s): {', '.join(unknown)}. Allowed parameters: {', '.join(sorted(allowed))}.")
        filtered = {key: _coerce_backtest_value(key, values[key]) for key in values if key in allowed}
        config = cls(
            strategy_id=strategy_id,
            timeframe=timeframe,
            from_date=from_date,
            to_date=to_date,
            days=days,
            symbols=symbols,
            strategy_params=strategy_params or {},
            **filtered,
        )
        config.validate()
        return config

    def validate(self) -> None:
        _require_choice(self.entry, "entry", {"next_open", "signal_close", "breakout_stop", "limit_retest"})
        _require_choice(self.stop_type, "stop_type", {"none", "percent", "atr", "signal"})
        _require_choice(self.target_type, "target_type", {"none", "percent", "atr", "risk_multiple", "signal"})
        _require_choice(self.position_sizing, "position_sizing", {"fixed_quantity", "fixed_capital", "fixed_risk"})
        if self.entry_valid_bars < 1:
            raise ValueError("backtest parameter 'entry_valid_bars' must be >= 1.")
        if self.holding_bars < 1:
            raise ValueError("backtest parameter 'holding_bars' must be >= 1.")
        if self.capital <= 0:
            raise ValueError("backtest parameter 'capital' must be > 0.")
        if self.risk_per_trade_percent <= 0:
            raise ValueError("backtest parameter 'risk_per_trade_percent' must be > 0.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BacktestTrade:
    symbol: str
    strategy_id: str
    side: str
    signal_date: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: float
    score: int
    confidence: str
    exit_reason: str
    bars_held: int
    return_percent: float
    pnl: float
    r_multiple: float | None
    max_favorable_excursion_percent: float
    max_adverse_excursion_percent: float
    intrabar_ambiguous: bool
    stop_loss: float | None
    target: float | None
    reasons: list[str]
    warnings: list[str]
    indicators: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BacktestResult:
    strategy_id: str
    timeframe: str
    analyzed_symbols: int
    signal_count: int
    trade_count: int
    metrics: dict[str, Any]
    forward_accuracy: list[dict[str, Any]]
    score_buckets: list[dict[str, Any]]
    monthly_performance: list[dict[str, Any]]
    symbol_performance: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    signals: list[dict[str, Any]]
    errors: list[dict[str, str]]
    config: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _require_choice(value: str, name: str, allowed: set[str]) -> None:
    if value not in allowed:
        raise ValueError(f"backtest parameter '{name}' must be one of: {', '.join(sorted(allowed))}.")


def _coerce_backtest_value(key: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if key in {"entry_valid_bars", "holding_bars", "fixed_quantity"}:
        return int(value)
    if key in {
        "stop_percent",
        "stop_atr",
        "target_percent",
        "target_atr",
        "target_r_multiple",
        "slippage_bps",
        "brokerage_bps",
        "capital",
        "risk_per_trade_percent",
        "fixed_capital",
    }:
        return float(value)
    if key == "allow_overlap":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return value
