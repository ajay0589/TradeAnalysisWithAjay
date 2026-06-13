from __future__ import annotations

import csv
import json
from pathlib import Path
from urllib.request import Request, build_opener


NSE_FII_DII_PAGE = "https://www.nseindia.com/reports/fii-dii"
NSE_FII_DII_API = "https://www.nseindia.com/api/fiidiiTradeReact"


def fetch_fii_dii_activity(timeout_seconds: int = 20) -> list[dict[str, str]]:
    opener = build_opener()
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": NSE_FII_DII_PAGE,
    }
    opener.open(Request(NSE_FII_DII_PAGE, headers=headers), timeout=timeout_seconds).read()
    response = opener.open(Request(NSE_FII_DII_API, headers=headers), timeout=timeout_seconds)
    payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("data", [])
    return [dict(row) for row in payload]


def write_fii_dii_csv(path: str | Path, rows: list[dict[str, str]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        output_path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

