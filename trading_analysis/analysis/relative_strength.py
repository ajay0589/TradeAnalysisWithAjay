from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trading_analysis.models import Candle


@dataclass(frozen=True)
class RelativeStrengthSignal:
    comparison: str
    subject_return_percent: float | None
    benchmark_return_percent: float | None
    relative_return_percent: float | None
    label: str
    score: int


@dataclass(frozen=True)
class RelativeStrengthReport:
    stock_vs_nifty: RelativeStrengthSignal | None
    stock_vs_sector: RelativeStrengthSignal | None
    sector_vs_nifty: RelativeStrengthSignal | None


def analyze_relative_strength(
    stock_candles: list[Candle],
    nifty_candles: list[Candle] | None = None,
    sector_candles: list[Candle] | None = None,
    lookback: int = 20,
) -> RelativeStrengthReport:
    stock_vs_nifty = (
        compare_relative_strength("Stock vs Nifty", stock_candles, nifty_candles, lookback)
        if nifty_candles
        else None
    )
    stock_vs_sector = (
        compare_relative_strength("Stock vs Sector", stock_candles, sector_candles, lookback)
        if sector_candles
        else None
    )
    sector_vs_nifty = (
        compare_relative_strength("Sector vs Nifty", sector_candles, nifty_candles, lookback)
        if sector_candles and nifty_candles
        else None
    )
    return RelativeStrengthReport(
        stock_vs_nifty=stock_vs_nifty,
        stock_vs_sector=stock_vs_sector,
        sector_vs_nifty=sector_vs_nifty,
    )


def compare_relative_strength(
    comparison: str,
    subject: list[Candle],
    benchmark: list[Candle],
    lookback: int = 20,
) -> RelativeStrengthSignal:
    subject_return = period_return_percent(subject, lookback)
    benchmark_return = period_return_percent(benchmark, lookback)
    if subject_return is None or benchmark_return is None:
        return RelativeStrengthSignal(comparison, subject_return, benchmark_return, None, "insufficient data", 50)

    relative = subject_return - benchmark_return
    if relative >= 2.0:
        label = "outperforming"
        score = 70
    elif relative <= -2.0:
        label = "underperforming"
        score = 30
    else:
        label = "neutral"
        score = 50
    return RelativeStrengthSignal(comparison, subject_return, benchmark_return, relative, label, score)


def period_return_percent(candles: list[Candle], lookback: int = 20) -> float | None:
    if len(candles) < lookback + 1:
        return None
    start = candles[-lookback - 1].close
    end = candles[-1].close
    if start == 0:
        return None
    return ((end - start) / start) * 100


def load_sector_map(path: str | Path) -> dict[str, Any]:
    map_path = Path(path)
    if not map_path.exists():
        return {}
    return json.loads(map_path.read_text(encoding="utf-8"))


def sector_config_for_symbol(sector_map: dict[str, Any], symbol: str) -> dict[str, str] | None:
    symbols = sector_map.get("symbols", {})
    value = symbols.get(symbol.upper())
    if isinstance(value, str):
        sectors = sector_map.get("sectors", {})
        sector = sectors.get(value, {})
        return {
            "sector": value,
            "index_symbol": sector.get("index_symbol", value),
            "data_file": sector.get("data_file", f"{value}.csv"),
        }
    if isinstance(value, dict):
        return {
            "sector": value.get("sector", ""),
            "index_symbol": value.get("index_symbol", value.get("sector", "")),
            "data_file": value.get("data_file", f"{value.get('index_symbol', value.get('sector', 'sector'))}.csv"),
        }
    return None

