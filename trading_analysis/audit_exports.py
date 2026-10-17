"""Validated IST export windows and portable trade ledgers."""
import csv
from datetime import date, datetime, timedelta
import io
import json

from trading_analysis.live_timing import IST, as_ist


def date_window(day=None, from_date=None, to_date=None):
    first = date.fromisoformat(from_date or day or to_date or datetime.now(IST).date().isoformat())
    last = date.fromisoformat(to_date or day or from_date or first.isoformat())
    if first > last:
        raise ValueError("From date must not be after To date")
    if (last - first).days > 365:
        raise ValueError("Download at most 366 days per export")
    return first.isoformat(), last.isoformat()


def days(first, last):
    current = date.fromisoformat(first)
    while current.isoformat() <= last:
        yield current.isoformat()
        current += timedelta(days=1)


def in_window(value, first, last):
    return bool(value) and first <= as_ist(value).date().isoformat() <= last


def trade_overlaps(row, first, last):
    entry, exit_at = row.get("entry_time"), row.get("exit_time")
    return bool(entry) and as_ist(entry).date().isoformat() <= last and (
        not exit_at or as_ist(exit_at).date().isoformat() >= first)


def trade_csv(rows, extra_columns=()):
    columns = ["symbol", "trade_id", "setup", "variant", "horizon", "direction", "status",
               "entry_time", "entry_price", "exit_time", "exit_price", "stop_level", "target_level",
               "gross_points", "gross_r", "net_r", "exit_reason", "price_basis", "india_vix_entry", "india_vix_exit"]
    columns += [name for name in extra_columns if name not in columns]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for original in rows:
        row = dict(original)
        context = row.get("metadata") or row.get("context") or {}
        row["trade_id"] = row.get("trade_id") or row.get("id")
        if row.get("exit_price") is not None and row.get("entry_price") is not None:
            row["gross_points"] = (float(row["exit_price"]) - float(row["entry_price"])) * (1 if row.get("direction") == "bullish" else -1)
            risk = abs(float(row["entry_price"]) - float(row.get("stop_level") or row["entry_price"]))
            row["gross_r"] = row["gross_points"] / risk if risk else None
        for key in ("price_basis", "india_vix_entry", "india_vix_exit"):
            row.setdefault(key, context.get(key))
        cells = {}
        for key in columns:
            value = row.get(key)
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                value = "'" + value
            cells[key] = value
        writer.writerow(cells)
    return buffer.getvalue()
