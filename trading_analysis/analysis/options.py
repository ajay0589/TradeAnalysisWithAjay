from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OptionContract:
    tradingsymbol: str
    underlying: str
    expiry: date
    strike: float
    option_type: str
    lot_size: int

    @property
    def kite_key(self) -> str:
        return f"NFO:{self.tradingsymbol}"


@dataclass(frozen=True)
class OptionChainRow:
    tradingsymbol: str
    strike: float
    option_type: str
    last_price: float
    previous_close: float | None
    price_change: float | None
    oi: int
    previous_oi: int | None
    oi_change: int | None
    volume: int
    bid_price: float | None
    ask_price: float | None
    buildup: str


@dataclass(frozen=True)
class OptionChainAnalysis:
    symbol: str
    expiry: date
    spot_price: float | None
    contract_count: int
    pcr_oi: float | None
    max_pain: float | None
    highest_call_oi_strike: float | None
    highest_put_oi_strike: float | None
    rows: tuple[OptionChainRow, ...]


def option_contracts_for_symbol(
    instruments: list[dict[str, str]],
    symbol: str,
    expiry: date | None = None,
) -> list[OptionContract]:
    symbol = symbol.upper()
    contracts = [
        OptionContract(
            tradingsymbol=row["tradingsymbol"],
            underlying=row["name"].upper(),
            expiry=date.fromisoformat(row["expiry"]),
            strike=float(row["strike"]),
            option_type=row["instrument_type"].upper(),
            lot_size=int(float(row["lot_size"])),
        )
        for row in instruments
        if row.get("exchange", "").upper() == "NFO"
        and row.get("segment", "").upper() == "NFO-OPT"
        and row.get("name", "").upper() == symbol
        and row.get("instrument_type", "").upper() in {"CE", "PE"}
        and row.get("expiry")
    ]
    if expiry:
        contracts = [contract for contract in contracts if contract.expiry == expiry]
    return sorted(contracts, key=lambda item: (item.expiry, item.strike, item.option_type))


def nearest_expiry(contracts: list[OptionContract], today: date | None = None) -> date:
    today = today or date.today()
    expiries = sorted({contract.expiry for contract in contracts if contract.expiry >= today})
    if not expiries:
        expiries = sorted({contract.expiry for contract in contracts})
    if not expiries:
        raise ValueError("No option expiries found.")
    return expiries[0]


def select_strikes_around_spot(
    contracts: list[OptionContract],
    spot_price: float | None,
    strikes_around: int,
) -> list[OptionContract]:
    if spot_price is None:
        return contracts
    strikes = sorted({contract.strike for contract in contracts})
    if not strikes:
        return contracts
    atm_index = min(range(len(strikes)), key=lambda index: abs(strikes[index] - spot_price))
    selected = set(strikes[max(0, atm_index - strikes_around) : atm_index + strikes_around + 1])
    return [contract for contract in contracts if contract.strike in selected]


def analyze_option_chain(
    symbol: str,
    expiry: date,
    contracts: list[OptionContract],
    quotes: dict[str, dict[str, Any]],
    spot_price: float | None,
    previous_rows: dict[str, dict[str, str]] | None = None,
) -> OptionChainAnalysis:
    previous_rows = previous_rows or {}
    rows = [
        _row_from_quote(contract, quotes.get(contract.kite_key, {}), previous_rows.get(contract.tradingsymbol))
        for contract in contracts
        if contract.kite_key in quotes
    ]
    call_rows = [row for row in rows if row.option_type == "CE"]
    put_rows = [row for row in rows if row.option_type == "PE"]
    call_oi = sum(row.oi for row in call_rows)
    put_oi = sum(row.oi for row in put_rows)

    return OptionChainAnalysis(
        symbol=symbol.upper(),
        expiry=expiry,
        spot_price=spot_price,
        contract_count=len(rows),
        pcr_oi=(put_oi / call_oi) if call_oi else None,
        max_pain=_max_pain(rows),
        highest_call_oi_strike=_highest_oi_strike(call_rows),
        highest_put_oi_strike=_highest_oi_strike(put_rows),
        rows=tuple(sorted(rows, key=lambda row: (row.strike, row.option_type))),
    )


def load_option_chain_snapshot(path: str | Path) -> dict[str, dict[str, str]]:
    snapshot_path = Path(path)
    if not snapshot_path.exists():
        return {}
    with snapshot_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {row["tradingsymbol"]: row for row in rows}


def write_option_chain_snapshot(path: str | Path, analysis: OptionChainAnalysis) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "snapshot_time",
        "symbol",
        "expiry",
        "tradingsymbol",
        "strike",
        "option_type",
        "last_price",
        "previous_close",
        "price_change",
        "oi",
        "previous_oi",
        "oi_change",
        "volume",
        "bid_price",
        "ask_price",
        "buildup",
    ]
    snapshot_time = datetime.now().isoformat(timespec="seconds")
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in analysis.rows:
            writer.writerow(
                {
                    "snapshot_time": snapshot_time,
                    "symbol": analysis.symbol,
                    "expiry": analysis.expiry.isoformat(),
                    "tradingsymbol": row.tradingsymbol,
                    "strike": row.strike,
                    "option_type": row.option_type,
                    "last_price": row.last_price,
                    "previous_close": "" if row.previous_close is None else row.previous_close,
                    "price_change": "" if row.price_change is None else row.price_change,
                    "oi": row.oi,
                    "previous_oi": "" if row.previous_oi is None else row.previous_oi,
                    "oi_change": "" if row.oi_change is None else row.oi_change,
                    "volume": row.volume,
                    "bid_price": "" if row.bid_price is None else row.bid_price,
                    "ask_price": "" if row.ask_price is None else row.ask_price,
                    "buildup": row.buildup,
                }
            )


def _row_from_quote(
    contract: OptionContract,
    quote: dict[str, Any],
    previous_row: dict[str, str] | None,
) -> OptionChainRow:
    last_price = _float(quote.get("last_price"))
    previous_close = _float((quote.get("ohlc") or {}).get("close"))
    price_change = last_price - previous_close if previous_close is not None else None
    oi = int(_float(quote.get("oi")) or 0)
    previous_oi = _optional_int((previous_row or {}).get("oi"))
    oi_change = oi - previous_oi if previous_oi is not None else None
    bid_price = _depth_price(quote, "buy")
    ask_price = _depth_price(quote, "sell")
    return OptionChainRow(
        tradingsymbol=contract.tradingsymbol,
        strike=contract.strike,
        option_type=contract.option_type,
        last_price=last_price,
        previous_close=previous_close,
        price_change=price_change,
        oi=oi,
        previous_oi=previous_oi,
        oi_change=oi_change,
        volume=int(_float(quote.get("volume")) or 0),
        bid_price=bid_price,
        ask_price=ask_price,
        buildup=classify_buildup(price_change, oi_change),
    )


def classify_buildup(price_change: float | None, oi_change: int | None) -> str:
    if price_change is None or oi_change is None:
        return "Needs previous OI snapshot"
    if price_change > 0 and oi_change > 0:
        return "Long build-up"
    if price_change < 0 and oi_change > 0:
        return "Short build-up"
    if price_change < 0 and oi_change < 0:
        return "Long unwinding"
    if price_change > 0 and oi_change < 0:
        return "Short covering"
    return "Neutral"


def _max_pain(rows: list[OptionChainRow]) -> float | None:
    strikes = sorted({row.strike for row in rows})
    if not strikes:
        return None
    pain_by_strike = {}
    for settlement in strikes:
        pain = 0.0
        for row in rows:
            if row.option_type == "CE":
                pain += max(0.0, settlement - row.strike) * row.oi
            else:
                pain += max(0.0, row.strike - settlement) * row.oi
        pain_by_strike[settlement] = pain
    return min(pain_by_strike, key=pain_by_strike.get)


def _highest_oi_strike(rows: list[OptionChainRow]) -> float | None:
    if not rows:
        return None
    return max(rows, key=lambda row: row.oi).strike


def _depth_price(quote: dict[str, Any], side: str) -> float | None:
    depth = ((quote.get("depth") or {}).get(side) or [])
    for level in depth:
        price = _float(level.get("price"))
        if price:
            return price
    return None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))

