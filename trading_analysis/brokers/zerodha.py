from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from trading_analysis.models import Candle


QUOTE_LIMIT = 500


class ZerodhaKiteClient:
    """Small read-only Zerodha Kite Connect client.

    It expects an already generated access token. Login and token refresh are kept
    outside this class because they involve user authentication.
    """

    base_url = "https://api.kite.trade"

    def __init__(self, api_key: str, access_token: str, timeout_seconds: int = 20) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self.timeout_seconds = timeout_seconds

    def instruments(self, exchange: str | None = None) -> list[dict[str, str]]:
        path = "/instruments" if exchange is None else f"/instruments/{exchange.upper()}"
        body = self._get_text(path)
        return list(csv.DictReader(StringIO(body)))

    def quotes(self, instruments: list[str]) -> dict[str, dict]:
        output: dict[str, dict] = {}
        for chunk in chunked(instruments, QUOTE_LIMIT):
            params = [("i", instrument) for instrument in chunk]
            payload = self._get_json("/quote", params=params)
            output.update(payload.get("data", {}))
        return output

    def historical_candles(
        self,
        instrument_token: str,
        interval: str,
        from_time: datetime,
        to_time: datetime,
        include_oi: bool = False,
        continuous: bool = False,
    ) -> list[Candle]:
        params = {
            "from": from_time.strftime("%Y-%m-%d %H:%M:%S"),
            "to": to_time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if include_oi:
            params["oi"] = "1"
        if continuous:
            params["continuous"] = "1"
        payload = self._get_json(
            f"/instruments/historical/{instrument_token}/{interval}",
            params=params,
        )
        rows = payload.get("data", {}).get("candles", [])
        return [
            Candle(
                timestamp=parse_kite_timestamp(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=int(row[5]),
                open_interest=int(row[6]) if len(row) > 6 and row[6] is not None else None,
            )
            for row in rows
        ]

    def _get_json(self, path: str, params: dict[str, str] | list[tuple[str, str]] | None = None) -> dict:
        return json.loads(self._get_text(path, params=params))

    def _get_text(self, path: str, params: dict[str, str] | list[tuple[str, str]] | None = None) -> str:
        query = f"?{urlencode(params)}" if params else ""
        request = Request(
            f"{self.base_url}{path}{query}",
            headers={
                "Authorization": f"token {self.api_key}:{self.access_token}",
                "X-Kite-Version": "3",
            },
            method="GET",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return response.read().decode("utf-8")


def build_login_url(api_key: str, redirect_params: dict[str, str] | None = None) -> str:
    params = {"v": "3", "api_key": api_key}
    if redirect_params:
        params["redirect_params"] = urlencode(redirect_params)
    return f"https://kite.zerodha.com/connect/login?{urlencode(params)}"


def generate_session(
    api_key: str,
    api_secret: str,
    request_token: str,
    timeout_seconds: int = 20,
) -> dict:
    token = extract_request_token(request_token)
    checksum = kite_checksum(api_key, token, api_secret)
    body = urlencode(
        {
            "api_key": api_key,
            "request_token": token,
            "checksum": checksum,
        }
    ).encode("utf-8")
    request = Request(
        "https://api.kite.trade/session/token",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Kite-Version": "3",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") != "success":
        raise RuntimeError(f"Zerodha token exchange failed: {payload}")
    return payload.get("data", {})


def kite_checksum(api_key: str, request_token: str, api_secret: str) -> str:
    return hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode("utf-8")).hexdigest()


def extract_request_token(value: str) -> str:
    if "request_token=" not in value:
        return value.strip()
    parsed = urlparse(value)
    tokens = parse_qs(parsed.query).get("request_token", [])
    if not tokens:
        raise ValueError("Could not find request_token in URL.")
    return tokens[0]


def parse_kite_timestamp(value: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_instruments_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_instruments_csv(path: str | Path, instruments: list[dict[str, str]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not instruments:
        output_path.write_text("", encoding="utf-8")
        return

    fieldnames = list(instruments[0].keys())
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(instruments)


def resolve_instrument_token(
    instruments: list[dict[str, str]],
    exchange: str,
    tradingsymbol: str,
) -> str:
    exchange = exchange.upper()
    tradingsymbol = tradingsymbol.upper()
    matches = [
        row
        for row in instruments
        if row.get("exchange", "").upper() == exchange
        and row.get("tradingsymbol", "").upper() == tradingsymbol
    ]
    if not matches:
        raise ValueError(f"Instrument not found in cache: {exchange}:{tradingsymbol}")
    return matches[0]["instrument_token"]


def write_candles_csv(path: str | Path, candles: list[Candle]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "open", "high", "low", "close", "volume", "open_interest"]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for candle in candles:
            writer.writerow(
                {
                    "date": candle.timestamp.isoformat(),
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                    "open_interest": "" if candle.open_interest is None else candle.open_interest,
                }
            )


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
