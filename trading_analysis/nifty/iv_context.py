from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from trading_analysis.nifty.models import NiftyIVContext


DEFAULT_IV_HISTORY_PATH = Path("data/raw/iv_history/NIFTY_iv_history.csv")
FIELDNAMES = [
    "date_time",
    "symbol",
    "expiry",
    "days_to_expiry",
    "atm_strike",
    "atm_iv",
    "weekly_atm_iv",
    "monthly_atm_iv",
    "source_snapshot",
]


def record_nifty_iv_snapshot(
    symbol: str = "NIFTY",
    expiry: str | None = None,
    atm_strike: float | None = None,
    atm_iv: float | None = None,
    weekly_atm_iv: float | None = None,
    monthly_atm_iv: float | None = None,
    source_snapshot: str | None = None,
    path: str | Path = DEFAULT_IV_HISTORY_PATH,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    exists = output.exists()
    as_of = as_of or datetime.now()
    days_to_expiry = _days_to_expiry(expiry, as_of.date())
    row = {
        "date_time": as_of.isoformat(timespec="seconds"),
        "symbol": symbol.upper(),
        "expiry": expiry or "",
        "days_to_expiry": "" if days_to_expiry is None else days_to_expiry,
        "atm_strike": "" if atm_strike is None else atm_strike,
        "atm_iv": "" if atm_iv is None else atm_iv,
        "weekly_atm_iv": "" if weekly_atm_iv is None else weekly_atm_iv,
        "monthly_atm_iv": "" if monthly_atm_iv is None else monthly_atm_iv,
        "source_snapshot": source_snapshot or "",
    }
    with output.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        if not exists:
            writer.writeheader()
        writer.writerow(row)
    return row


def load_nifty_iv_history(
    path: str | Path = DEFAULT_IV_HISTORY_PATH,
    symbol: str = "NIFTY",
    lookback_days: int = 252,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    csv_path = Path(path)
    if not csv_path.exists():
        return []
    now = now or datetime.now()
    cutoff = now - timedelta(days=lookback_days)
    rows: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("symbol", "").upper() != symbol.upper():
                continue
            timestamp = _parse_datetime(row.get("date_time"))
            if timestamp and timestamp < cutoff:
                continue
            atm_iv = _float(row.get("atm_iv") or row.get("weekly_atm_iv"))
            if atm_iv is None:
                continue
            rows.append({**row, "date_time": timestamp, "atm_iv": atm_iv})
    return rows


def build_nifty_iv_context(
    symbol: str = "NIFTY",
    lookback_days: int = 252,
    current_atm_iv: float | None = None,
    current_iv_change: float | None = None,
    history_path: str | Path = DEFAULT_IV_HISTORY_PATH,
) -> NiftyIVContext:
    warnings: list[str] = []
    notes: list[str] = []
    history = load_nifty_iv_history(history_path, symbol=symbol, lookback_days=lookback_days)
    values = [float(row["atm_iv"]) for row in history]
    current = current_atm_iv if current_atm_iv is not None else (values[-1] if values else None)
    if current is None:
        warnings.append("Current ATM IV is unavailable.")
    if len(values) < 30:
        warnings.append(f"Only {len(values)} IV observation(s) available; IV rank needs at least 30.")
        return NiftyIVContext(
            symbol=symbol.upper(),
            as_of=datetime.now(),
            atm_iv=current,
            iv_change=current_iv_change,
            iv_rank_lookback_days=lookback_days,
            iv_rank=None,
            iv_percentile=None,
            iv_min=min(values) if values else None,
            iv_max=max(values) if values else None,
            iv_mean=(sum(values) / len(values)) if values else None,
            iv_regime="unknown",
            enough_history=False,
            notes=notes,
            warnings=warnings,
        )
    iv_min = min(values)
    iv_max = max(values)
    iv_rank = None if current is None or iv_max == iv_min else ((current - iv_min) / (iv_max - iv_min)) * 100
    iv_percentile = None if current is None else (len([value for value in values if value < current]) / len(values)) * 100
    regime = _regime(iv_rank)
    notes.append(f"IV rank calculated from {len(values)} observation(s).")
    return NiftyIVContext(
        symbol=symbol.upper(),
        as_of=datetime.now(),
        atm_iv=current,
        iv_change=current_iv_change,
        iv_rank_lookback_days=lookback_days,
        iv_rank=iv_rank,
        iv_percentile=iv_percentile,
        iv_min=iv_min,
        iv_max=iv_max,
        iv_mean=sum(values) / len(values),
        iv_regime=regime,
        enough_history=True,
        notes=notes,
        warnings=warnings,
    )


def _regime(iv_rank: float | None) -> str:
    if iv_rank is None:
        return "unknown"
    if iv_rank < 20:
        return "low"
    if iv_rank < 60:
        return "normal"
    if iv_rank < 85:
        return "high"
    return "extreme"


def _days_to_expiry(expiry: str | None, today: date) -> int | None:
    if not expiry:
        return None
    try:
        return (date.fromisoformat(expiry) - today).days
    except ValueError:
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
