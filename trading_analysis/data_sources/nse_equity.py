from __future__ import annotations

import csv
import json
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, build_opener

try:
    import requests
except ImportError:  # pragma: no cover - exercised only in minimal Python environments.
    requests = None


NSE_EQUITY_QUOTE_PAGE = "https://www.nseindia.com/get-quotes/equity"
NSE_EQUITY_QUOTE_API = "https://www.nseindia.com/api/quote-equity"

INDEX_ALIASES = {
    "NIFTY OIL & GAS": "NIFTY OIL AND GAS",
    "NIFTY FINANCIAL SERVICES": "NIFTY FIN SERVICE",
    "NIFTY FINANCIAL SERVICES EX-BANK": "NIFTY FINSEREXBNK",
    "NIFTY PRIVATE BANK": "NIFTY PVT BANK",
    "NIFTY SERVICES SECTOR": "NIFTY SERV SECTOR",
    "NIFTY INFRASTRUCTURE": "NIFTY INFRA",
}

PREFERRED_SECTOR_INDICES = [
    "NIFTY PSU BANK",
    "NIFTY PRIVATE BANK",
    "NIFTY PVT BANK",
    "NIFTY BANK",
    "NIFTY FINANCIAL SERVICES EX-BANK",
    "NIFTY FINSEREXBNK",
    "NIFTY FINANCIAL SERVICES",
    "NIFTY FIN SERVICE",
    "NIFTY OIL & GAS",
    "NIFTY OIL AND GAS",
    "NIFTY ENERGY",
    "NIFTY IT",
    "NIFTY PHARMA",
    "NIFTY HEALTHCARE",
    "NIFTY AUTO",
    "NIFTY FMCG",
    "NIFTY METAL",
    "NIFTY REALTY",
    "NIFTY MEDIA",
    "NIFTY COMMODITIES",
    "NIFTY INFRASTRUCTURE",
    "NIFTY INFRA",
    "NIFTY SERVICES SECTOR",
    "NIFTY SERV SECTOR",
    "NIFTY MNC",
    "NIFTY CPSE",
    "NIFTY PSE",
]

INDUSTRY_FALLBACKS = [
    ("bank", "NIFTY BANK"),
    ("financial", "NIFTY FIN SERVICE"),
    ("finance", "NIFTY FIN SERVICE"),
    ("insurance", "NIFTY FIN SERVICE"),
    ("nbfc", "NIFTY FIN SERVICE"),
    ("software", "NIFTY IT"),
    ("information technology", "NIFTY IT"),
    ("technology", "NIFTY IT"),
    ("pharma", "NIFTY PHARMA"),
    ("healthcare", "NIFTY HEALTHCARE"),
    ("hospital", "NIFTY HEALTHCARE"),
    ("automobile", "NIFTY AUTO"),
    ("auto", "NIFTY AUTO"),
    ("oil", "NIFTY OIL AND GAS"),
    ("gas", "NIFTY OIL AND GAS"),
    ("energy", "NIFTY ENERGY"),
    ("power", "NIFTY ENERGY"),
    ("utilities", "NIFTY ENERGY"),
    ("metal", "NIFTY METAL"),
    ("mining", "NIFTY METAL"),
    ("steel", "NIFTY METAL"),
    ("realty", "NIFTY REALTY"),
    ("real estate", "NIFTY REALTY"),
    ("media", "NIFTY MEDIA"),
    ("entertainment", "NIFTY MEDIA"),
    ("fmcg", "NIFTY FMCG"),
    ("fast moving consumer goods", "NIFTY FMCG"),
    ("consumer", "NIFTY FMCG"),
    ("infrastructure", "NIFTY INFRA"),
    ("construction", "NIFTY INFRA"),
    ("cement", "NIFTY INFRA"),
    ("telecom", "NIFTY SERV SECTOR"),
    ("services", "NIFTY SERV SECTOR"),
    ("commodity", "NIFTY COMMODITIES"),
    ("chemicals", "NIFTY COMMODITIES"),
]


def fetch_equity_quote_metadata(
    symbol: str,
    timeout_seconds: int = 20,
    opener: Any | None = None,
    warm_session: bool = True,
) -> dict[str, Any]:
    if opener is None and requests is not None:
        opener = requests.Session()
    if requests is not None and isinstance(opener, requests.Session):
        return _fetch_equity_quote_metadata_requests(symbol, opener, timeout_seconds, warm_session)

    opener = opener or build_opener()
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": f"{NSE_EQUITY_QUOTE_PAGE}?symbol={symbol.upper()}",
    }
    if warm_session:
        opener.open(Request(NSE_EQUITY_QUOTE_PAGE, headers=headers), timeout=timeout_seconds).read()
    url = f"{NSE_EQUITY_QUOTE_API}?{urlencode({'symbol': symbol.upper()})}"
    response = opener.open(Request(url, headers=headers), timeout=timeout_seconds)
    return json.loads(response.read().decode("utf-8"))


def _fetch_equity_quote_metadata_requests(
    symbol: str,
    session: Any,
    timeout_seconds: int,
    warm_session: bool,
) -> dict[str, Any]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": f"{NSE_EQUITY_QUOTE_PAGE}?symbol={symbol.upper()}",
    }
    if warm_session:
        session.get(
            f"{NSE_EQUITY_QUOTE_PAGE}?{urlencode({'symbol': symbol.upper()})}",
            headers=headers,
            timeout=timeout_seconds,
        )
    response = session.get(
        NSE_EQUITY_QUOTE_API,
        params={"symbol": symbol.upper()},
        headers=headers,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def load_metadata_cache(path: str | Path) -> dict[str, Any]:
    cache_path = Path(path)
    if not cache_path.exists():
        return {}
    return json.loads(cache_path.read_text(encoding="utf-8"))


def write_metadata_cache(path: str | Path, cache: dict[str, Any]) -> None:
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def fetch_metadata_for_symbols(
    symbols: list[str],
    cache_path: str | Path,
    refresh: bool = False,
    sleep_seconds: float = 0.15,
    timeout_seconds: int = 20,
    retries: int = 1,
) -> dict[str, Any]:
    cache = load_metadata_cache(cache_path)
    opener: Any = requests.Session() if requests is not None else build_opener()
    warmed = False
    for symbol in symbols:
        key = symbol.upper()
        cached = cache.get(key)
        if cached and "_error" not in cached and not refresh:
            continue
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                cache[key] = fetch_equity_quote_metadata(
                    key,
                    timeout_seconds=timeout_seconds,
                    opener=opener,
                    warm_session=not warmed,
                )
                warmed = True
                break
            except Exception as exc:  # NSE public endpoints can intermittently time out.
                last_error = exc
                time.sleep(max(sleep_seconds, 0.5))
        else:
            cache[key] = {
                "_error": f"{type(last_error).__name__}: {last_error}",
            }
        write_metadata_cache(cache_path, cache)
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return cache


def build_sector_map_from_metadata(
    symbols: list[str],
    metadata_by_symbol: dict[str, Any],
    nse_instruments: list[dict[str, str]],
) -> dict[str, Any]:
    index_symbols = zerodha_index_symbols(nse_instruments)
    sectors: dict[str, dict[str, str]] = {}
    mapped_symbols: dict[str, dict[str, str]] = {}
    unmapped: dict[str, dict[str, Any]] = {}

    for symbol in symbols:
        key = symbol.upper()
        metadata = metadata_by_symbol.get(key, {})
        selected = choose_sector_index(metadata, index_symbols)
        industry_info = metadata.get("industryInfo", {}) if isinstance(metadata, dict) else {}
        info = metadata.get("info", {}) if isinstance(metadata, dict) else {}
        if selected:
            sectors.setdefault(
                selected,
                {
                    "index_symbol": selected,
                    "data_file": f"{safe_symbol_filename(selected)}.csv",
                },
            )
            mapped_symbols[key] = {
                "sector": selected,
                "index_symbol": selected,
                "data_file": f"{safe_symbol_filename(selected)}.csv",
                "macro": str(industry_info.get("macro", "")),
                "nse_sector": str(industry_info.get("sector", "")),
                "industry": str(industry_info.get("industry", info.get("industry", ""))),
                "basic_industry": str(industry_info.get("basicIndustry", "")),
            }
        else:
            unmapped[key] = {
                "industryInfo": industry_info,
                "indices": _metadata_indices(metadata),
            }

    return {
        "generated_on": date.today().isoformat(),
        "source": "NSE quote-equity metadata + Zerodha NSE index instruments",
        "sectors": dict(sorted(sectors.items())),
        "symbols": dict(sorted(mapped_symbols.items())),
        "unmapped": dict(sorted(unmapped.items())),
    }


def load_sector_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build_sector_map_from_csv(
    path: str | Path,
    nse_instruments: list[dict[str, str]],
    symbols_filter: list[str] | None = None,
) -> dict[str, Any]:
    rows = load_sector_csv_rows(path)
    return build_sector_map_from_csv_rows(
        rows=rows,
        nse_instruments=nse_instruments,
        source=str(path),
        symbols_filter=symbols_filter,
    )


def build_sector_map_from_csv_rows(
    rows: list[dict[str, str]],
    nse_instruments: list[dict[str, str]],
    source: str,
    symbols_filter: list[str] | None = None,
) -> dict[str, Any]:
    index_symbols = zerodha_index_symbols(nse_instruments)
    allowed_symbols = {symbol.upper() for symbol in symbols_filter} if symbols_filter else None
    sectors: dict[str, dict[str, str]] = {}
    mapped_symbols: dict[str, dict[str, str]] = {}
    unmapped: dict[str, dict[str, Any]] = {}

    for row in rows:
        symbol = _csv_value(row, ("symbol", "ticker", "nse symbol", "tradingsymbol", "security symbol")).upper()
        if not symbol:
            continue
        if allowed_symbols is not None and symbol not in allowed_symbols:
            continue

        explicit_index = _csv_value(
            row,
            ("index_symbol", "index symbol", "sector_index", "sector index", "nifty_index", "nifty index"),
        )
        industry_text = " ".join(
            value
            for value in (
                _csv_value(row, ("sector", "industry sector", "nse_sector", "nse sector")),
                _csv_value(row, ("industry", "basic industry", "basicindustry")),
                _csv_value(row, ("macro", "macro sector")),
            )
            if value
        )
        selected = _select_index_from_csv_row(explicit_index, industry_text, index_symbols)
        if selected:
            sectors.setdefault(
                selected,
                {
                    "index_symbol": selected,
                    "data_file": f"{safe_symbol_filename(selected)}.csv",
                },
            )
            mapped_symbols[symbol] = {
                "sector": selected,
                "index_symbol": selected,
                "data_file": f"{safe_symbol_filename(selected)}.csv",
                "csv_sector": _csv_value(row, ("sector", "industry sector", "nse_sector", "nse sector")),
                "industry": _csv_value(row, ("industry", "basic industry", "basicindustry")),
                "macro": _csv_value(row, ("macro", "macro sector")),
                "company": _csv_value(row, ("company name", "company", "name", "security name")),
            }
        else:
            unmapped[symbol] = {
                "explicit_index": explicit_index,
                "industry_text": industry_text,
                "row": row,
            }

    return {
        "generated_on": date.today().isoformat(),
        "source": f"CSV sector file: {source}",
        "sectors": dict(sorted(sectors.items())),
        "symbols": dict(sorted(mapped_symbols.items())),
        "unmapped": dict(sorted(unmapped.items())),
    }


def choose_sector_index(metadata: dict[str, Any], index_symbols: set[str]) -> str | None:
    indices = {_canonical_index_name(value) for value in _metadata_indices(metadata)}
    for preferred in PREFERRED_SECTOR_INDICES:
        canonical = _canonical_index_name(preferred)
        if canonical in indices and canonical in index_symbols:
            return canonical

    fallback = fallback_index_from_industry(metadata)
    if fallback and fallback in index_symbols:
        return fallback
    return None


def fallback_index_from_industry(metadata: dict[str, Any]) -> str | None:
    industry_info = metadata.get("industryInfo", {}) if isinstance(metadata, dict) else {}
    info = metadata.get("info", {}) if isinstance(metadata, dict) else {}
    text = " ".join(
        str(value)
        for value in (
            industry_info.get("macro", ""),
            industry_info.get("sector", ""),
            industry_info.get("industry", ""),
            industry_info.get("basicIndustry", ""),
            info.get("industry", ""),
        )
    ).lower()
    return fallback_index_from_text(text)


def fallback_index_from_text(text: str) -> str | None:
    lowered = text.lower()
    for needle, index_symbol in INDUSTRY_FALLBACKS:
        if needle in lowered:
            return index_symbol
    return None


def zerodha_index_symbols(nse_instruments: list[dict[str, str]]) -> set[str]:
    return {
        row.get("tradingsymbol", "").upper()
        for row in nse_instruments
        if row.get("exchange", "").upper() == "NSE" and row.get("segment", "").upper() == "INDICES"
    }


def safe_symbol_filename(symbol: str) -> str:
    return (
        symbol.upper()
        .replace("&", "AND")
        .replace("/", "_")
        .replace("-", "_")
        .replace(" ", "_")
    )


def _metadata_indices(metadata: dict[str, Any]) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    values = metadata.get("metadata", {}).get("pdSectorIndAll", [])
    return [str(value).upper() for value in values if value]


def _canonical_index_name(value: str) -> str:
    upper = value.upper().replace("&", "&").strip()
    return INDEX_ALIASES.get(upper, upper)


def _select_index_from_csv_row(explicit_index: str, industry_text: str, index_symbols: set[str]) -> str | None:
    if explicit_index:
        canonical = _canonical_index_name(explicit_index)
        if canonical in index_symbols:
            return canonical

    fallback = fallback_index_from_text(industry_text)
    if fallback and fallback in index_symbols:
        return fallback
    return None


def _csv_value(row: dict[str, str], candidates: tuple[str, ...]) -> str:
    normalized = {_normalize_header(key): value for key, value in row.items()}
    for candidate in candidates:
        value = normalized.get(_normalize_header(candidate), "")
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _normalize_header(value: str) -> str:
    return " ".join(value.replace("_", " ").replace("-", " ").strip().lower().split())
