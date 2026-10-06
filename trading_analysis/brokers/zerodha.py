from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from trading_analysis.models import Candle
from trading_analysis import diagnostics
from trading_analysis.network import https_context


QUOTE_LIMIT = 500
_CANDLE_MERGE_LOCK = threading.RLock()
_REQUEST_CONDITION = threading.Condition()
_REQUEST_WAITERS: deque = deque()
_LAST_REQUEST_AT = 0.0
_MIN_REQUEST_GAP_SECONDS = 0.45
_MAX_QUEUE_WAIT_SECONDS = 60
HISTORICAL_MAX_DAYS = {
    "day": 1900,
    "60minute": 390,
    "30minute": 190,
    "15minute": 190,
    "10minute": 90,
}


@contextmanager
def request_slot():
    ticket = object()
    started = time.monotonic()
    with _REQUEST_CONDITION:
        _REQUEST_WAITERS.append(ticket)
        while _REQUEST_WAITERS[0] is not ticket:
            remaining = _MAX_QUEUE_WAIT_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                _REQUEST_WAITERS.remove(ticket)
                _REQUEST_CONDITION.notify_all()
                raise TimeoutError("Broker refresh queue wait limit reached; retry on the next cycle")
            _REQUEST_CONDITION.wait(min(remaining, 0.25))
    try:
        yield int((time.monotonic() - started) * 1000)
    finally:
        with _REQUEST_CONDITION:
            _REQUEST_WAITERS.popleft()
            _REQUEST_CONDITION.notify_all()


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
        candles_by_timestamp: dict[datetime, Candle] = {}
        for chunk_from, chunk_to in historical_windows(from_time, to_time, interval):
            params = {
                "from": chunk_from.strftime("%Y-%m-%d %H:%M:%S"),
                "to": chunk_to.strftime("%Y-%m-%d %H:%M:%S"),
            }
            if include_oi:
                params["oi"] = "1"
            if continuous:
                params["continuous"] = "1"
            payload = self._get_json(
                f"/instruments/historical/{instrument_token}/{interval}",
                params=params,
            )
            for row in payload.get("data", {}).get("candles", []):
                candle = Candle(
                    timestamp=parse_kite_timestamp(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=int(row[5]),
                    open_interest=int(row[6]) if len(row) > 6 and row[6] is not None else None,
                )
                candles_by_timestamp[candle.timestamp] = candle
        return [candles_by_timestamp[timestamp] for timestamp in sorted(candles_by_timestamp)]

    def _get_json(self, path: str, params: dict[str, str] | list[tuple[str, str]] | None = None) -> dict:
        return json.loads(self._get_text(path, params=params))

    def _get_text(self, path: str, params: dict[str, str] | list[tuple[str, str]] | None = None) -> str:
        global _LAST_REQUEST_AT
        query = f"?{urlencode(params)}" if params else ""
        request = Request(
            f"{self.base_url}{path}{query}",
            headers={
                "Authorization": f"token {self.api_key}:{self.access_token}",
                "X-Kite-Version": "3",
            },
            method="GET",
        )
        started = time.monotonic()
        queue_ms = network_ms = 0
        network_started = None
        error = None
        try:
            with request_slot() as queue_ms:
                wait = _MIN_REQUEST_GAP_SECONDS - (time.monotonic() - _LAST_REQUEST_AT)
                if wait > 0:
                    time.sleep(wait)
                network_started = time.monotonic()
                try:
                    with urlopen(request, timeout=self.timeout_seconds, context=https_context()) as response:
                        return response.read().decode("utf-8")
                finally:
                    network_ms = int((time.monotonic() - network_started) * 1000)
                    _LAST_REQUEST_AT = time.monotonic()
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            if network_started is None:
                queue_ms = int((time.monotonic() - started) * 1000)
            diagnostics.record("broker_request", status="failed" if error else "ok", endpoint=path,
                               queue_ms=queue_ms, network_ms=network_ms, error=error,
                               duration_ms=int((time.monotonic() - started) * 1000),
                               worker=threading.current_thread().name)


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
    with urlopen(request, timeout=timeout_seconds, context=https_context()) as response:
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
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", delete=False,
                                         dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp") as handle:
            temporary_path = Path(handle.name)
            if instruments:
                writer = csv.DictWriter(handle, fieldnames=list(instruments[0].keys()))
                writer.writeheader()
                writer.writerows(instruments)
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_replace(temporary_path, output_path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


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
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            delete=False,
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
        ) as handle:
            temporary_path = Path(handle.name)
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
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_replace(temporary_path, output_path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def _atomic_replace(source: Path, target: Path) -> None:
    for attempt in range(6):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 5:
                raise
            diagnostics.record("cache_replace_retry", area="data", status="retry", file=target.name, attempt=attempt + 1)
            time.sleep(0.05 * 2 ** attempt)


def merge_candles_csv(path: str | Path, candles: list[Candle]) -> None:
    with _CANDLE_MERGE_LOCK:
        output_path = Path(path)
        candles_by_timestamp: dict[datetime, Candle] = {}
        if output_path.exists():
            with output_path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    timestamp = parse_kite_timestamp(row["date"])
                    candles_by_timestamp[timestamp] = Candle(
                        timestamp=timestamp,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=int(float(row.get("volume") or 0)),
                        open_interest=int(float(row["open_interest"])) if row.get("open_interest") else None,
                    )
        for candle in candles:
            candles_by_timestamp[candle.timestamp] = candle
        write_candles_csv(output_path, [candles_by_timestamp[timestamp] for timestamp in sorted(candles_by_timestamp)])


def historical_windows(from_time: datetime, to_time: datetime, interval: str) -> list[tuple[datetime, datetime]]:
    if from_time > to_time:
        return []
    max_days = HISTORICAL_MAX_DAYS.get(interval)
    if not max_days or (to_time - from_time) <= timedelta(days=max_days):
        return [(from_time, to_time)]

    windows: list[tuple[datetime, datetime]] = []
    cursor = from_time
    while cursor <= to_time:
        chunk_to = min(cursor + timedelta(days=max_days), to_time)
        windows.append((cursor, chunk_to))
        cursor = chunk_to + timedelta(seconds=1)
    return windows


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
