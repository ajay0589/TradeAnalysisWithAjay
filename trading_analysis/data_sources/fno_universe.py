from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any


def fno_stock_symbols(
    nfo_instruments: list[dict[str, str]],
    nse_instruments: list[dict[str, str]],
) -> list[str]:
    equity_symbols = {
        row.get("tradingsymbol", "").upper()
        for row in nse_instruments
        if row.get("exchange", "").upper() == "NSE"
        and row.get("segment", "").upper() == "NSE"
        and row.get("instrument_type", "").upper() == "EQ"
    }
    futures_underlyings = {
        row.get("name", "").upper()
        for row in nfo_instruments
        if row.get("exchange", "").upper() == "NFO"
        and row.get("segment", "").upper() == "NFO-FUT"
        and row.get("instrument_type", "").upper() == "FUT"
        and row.get("name")
    }
    return sorted(symbol for symbol in futures_underlyings if symbol in equity_symbols)


def build_fno_watchlist(
    symbols: list[str],
    source: str,
    generated_on: date | None = None,
) -> dict[str, Any]:
    generated = generated_on or date.today()
    return {
        "timezone": "Asia/Kolkata",
        "generated_from": source,
        "generated_on": generated.isoformat(),
        "risk": {
            "max_risk_per_trade_percent": 1.0,
            "avoid_event_days": True,
        },
        "symbols": [
            {
                "symbol": symbol,
                "exchange": "NSE",
                "instrument_type": "EQ",
                "data_file": f"{symbol}.csv",
                "notes": "Generated F&O stock from Zerodha NFO futures universe.",
                "fundamentals": {},
            }
            for symbol in symbols
        ],
    }


def write_watchlist(path: str | Path, watchlist: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(watchlist, indent=2), encoding="utf-8")

