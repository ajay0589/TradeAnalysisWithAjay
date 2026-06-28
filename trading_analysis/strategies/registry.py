from __future__ import annotations

from importlib import import_module

from trading_analysis.strategies.base import StrategyDefinition


_STRATEGIES: dict[str, StrategyDefinition] = {}
_BUILTIN_MODULES = (
    "trading_analysis.strategies.bullish_breakout",
    "trading_analysis.strategies.bullish_pullback",
    "trading_analysis.strategies.bearish_breakdown",
    "trading_analysis.strategies.bearish_pullback",
    "trading_analysis.strategies.neutral_range",
    "trading_analysis.strategies.krishna_bullish",
)


def _register(strategy: StrategyDefinition) -> None:
    _STRATEGIES[strategy.strategy_id] = strategy


def _load_builtin_strategies() -> None:
    if _STRATEGIES:
        return
    for module_name in _BUILTIN_MODULES:
        module = import_module(module_name)
        _register(module.strategy())


def list_strategies() -> list[dict]:
    _load_builtin_strategies()
    return [strategy.to_dict() for strategy in sorted(_STRATEGIES.values(), key=lambda item: item.strategy_id)]


def get_strategy(strategy_id: str) -> StrategyDefinition:
    _load_builtin_strategies()
    key = (strategy_id or "").strip().lower()
    if key not in _STRATEGIES:
        allowed = ", ".join(sorted(_STRATEGIES))
        raise ValueError(f"Unknown strategy '{strategy_id}'. Available strategies: {allowed}")
    return _STRATEGIES[key]


def strategy_info(strategy_id: str) -> dict:
    return get_strategy(strategy_id).to_dict()
