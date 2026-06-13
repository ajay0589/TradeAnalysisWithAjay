from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from trading_analysis.models import Candle, WatchlistItem


def load_candles_for_item(item: WatchlistItem, data_dir: str | Path) -> list[Candle]:
    data_path = Path(data_dir) / (item.data_file or f"{item.symbol}.csv")
    return load_candles(data_path)


def load_candles(path: str | Path) -> list[Candle]:
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Candle file not found: {csv_path}")

    candles: list[Candle] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            candles.append(
                Candle(
                    timestamp=_parse_timestamp(row["date"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(float(row["volume"])),
                    open_interest=_optional_int(row.get("open_interest")),
                )
            )
    return sorted(candles, key=lambda candle: candle.timestamp)


def _parse_timestamp(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(value)


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))

