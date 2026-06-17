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
    "NIFTY CAPITAL GOODS": "NIFTY INFRA",
    "NIFTY CEMENT": "NIFTY INFRA",
    "NIFTY COMMERCIAL & TRANSPORT SERVICES": "NIFTY TRANS LOGIS",
    "NIFTY CONSTRUCTION": "NIFTY INFRA",
    "NIFTY CONSUMER DURABLES": "NIFTY CONSR DURBL",
    "NIFTY CONSUMER DURABLES INDEX": "NIFTY CONSR DURBL",
    "NIFTY CONSUMER SERVICES": "NIFTY CONSUMPTION",
    "NIFTY OIL & GAS": "NIFTY OIL AND GAS",
    "NIFTY FINANCIAL SERVICES": "NIFTY FIN SERVICE",
    "NIFTY FINANCIAL SERVICES 25/50": "NIFTY FINSRV25 50",
    "NIFTY FINANCIAL SERVICES 25/50 INDEX": "NIFTY FINSRV25 50",
    "NIFTY FINANCIAL SERVICES EX-BANK": "NIFTY FINSEREXBNK",
    "NIFTY HOSPITALS": "NIFTY HEALTHCARE",
    "NIFTY HOUSING FINANCE": "NIFTY HOUSING",
    "NIFTY INSURANCE": "NIFTY FIN SERVICE",
    "NIFTY NBFC": "NIFTY FIN SERVICE",
    "NIFTY PRIVATE BANK": "NIFTY PVT BANK",
    "NIFTY POWER": "NIFTY ENERGY",
    "NIFTY REITS & REALTY": "NIFTY REALTY",
    "NIFTY RETAIL": "NIFTY CONSUMPTION",
    "NIFTY SERVICES SECTOR": "NIFTY SERV SECTOR",
    "NIFTY TELECOMMUNICATIONS": "NIFTY SERV SECTOR",
    "NIFTY INFRASTRUCTURE": "NIFTY INFRA",
    "NIFTY500 HEALTHCARE": "NIFTY500 HEALTH",
    "NIFTY MIDSMALL FINANCIAL SERVICES": "NIFTY MS FIN SERV",
    "NIFTY MIDSMALL HEALTHCARE": "NIFTY MIDSML HLTH",
    "NIFTY MIDSMALL IT & TELECOM": "NIFTY MS IT TELCM",
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
    "NIFTY500 HEALTH",
    "NIFTY AUTO",
    "NIFTY FMCG",
    "NIFTY METAL",
    "NIFTY REALTY",
    "NIFTY MEDIA",
    "NIFTY CHEMICALS",
    "NIFTY CONSR DURBL",
    "NIFTY CONSUMPTION",
    "NIFTY TRANS LOGIS",
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

NIFTY_SECTOR_INDEX_SOURCES = [
    ("NIFTY AUTO", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-auto"),
    ("NIFTY BANK", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-bank"),
    ("NIFTY INFRA", "https://niftyindices.com/indices/equity/sectoral-indices/nifty-cement"),
    ("NIFTY INFRA", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-capital-goods"),
    ("NIFTY CHEMICALS", "https://niftyindices.com/indices/equity/sectoral-indices/nifty-chemicals"),
    ("NIFTY TRANS LOGIS", "https://niftyindices.com/indices/equity/sectoral-indices/nifty-commercial-transport-services"),
    ("NIFTY INFRA", "https://niftyindices.com/indices/equity/sectoral-indices/nifty-construction"),
    ("NIFTY CONSUMPTION", "https://niftyindices.com/indices/equity/sectoral-indices/nifty-consumer-services"),
    ("NIFTY FIN SERVICE", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-financial-services"),
    ("NIFTY FINSRV25 50", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-financial-services-25-50-index"),
    ("NIFTY FINSEREXBNK", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-financial-services-ex-bank"),
    ("NIFTY FMCG", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-fmcg"),
    ("NIFTY HEALTHCARE", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-healthcare-index"),
    ("NIFTY HEALTHCARE", "https://niftyindices.com/indices/equity/sectoral-indices/nifty-hospitals"),
    ("NIFTY HOUSING", "https://niftyindices.com/indices/equity/sectoral-indices/nifty-housing-finance"),
    ("NIFTY FIN SERVICE", "https://niftyindices.com/indices/equity/sectoral-indices/nifty-insurance"),
    ("NIFTY IT", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-it"),
    ("NIFTY MEDIA", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-media"),
    ("NIFTY METAL", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-metal"),
    ("NIFTY FIN SERVICE", "https://niftyindices.com/indices/equity/sectoral-indices/nifty-nbfc"),
    ("NIFTY PHARMA", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-pharma"),
    ("NIFTY ENERGY", "https://niftyindices.com/indices/equity/sectoral-indices/nifty-power"),
    ("NIFTY PVT BANK", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-private-bank"),
    ("NIFTY PSU BANK", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-psu-bank"),
    ("NIFTY REALTY", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-realty"),
    ("NIFTY REALTY", "https://niftyindices.com/indices/equity/sectoral-indices/nifty-reits-realty"),
    ("NIFTY CONSUMPTION", "https://niftyindices.com/indices/equity/sectoral-indices/nifty-retail"),
    ("NIFTY SERV SECTOR", "https://niftyindices.com/indices/equity/sectoral-indices/nifty-telecommunications"),
    ("NIFTY CONSR DURBL", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-consumer-durables-index"),
    ("NIFTY OIL AND GAS", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-oil-and-gas-index"),
    ("NIFTY500 HEALTH", "https://niftyindices.com/indices/equity/sectoral-indices/nifty500-healthcare"),
    ("NIFTY MS FIN SERV", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-midsmall-financial-services"),
    ("NIFTY MIDSML HLTH", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-midsmall-healthcare"),
    ("NIFTY MS IT TELCM", "https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-midsmall-it-telecom"),
]

FNO_SYMBOL_SECTOR_GROUPS = {
    "NIFTY AUTO": (
        "ASHOKLEY", "BAJAJ-AUTO", "BHARATFORG", "BOSCHLTD", "EICHERMOT", "EXIDEIND",
        "FORCEMOT", "HEROMOTOCO", "HYUNDAI", "M&M", "MARUTI", "MOTHERSON", "SONACOMS",
        "TIINDIA", "TMPV", "TVSMOTOR", "UNOMINDA",
    ),
    "NIFTY PSU BANK": ("BANKBARODA", "BANKINDIA", "CANBK", "INDIANB", "PNB", "SBIN", "UNIONBANK"),
    "NIFTY PVT BANK": (
        "AUBANK", "AXISBANK", "BANDHANBNK", "FEDERALBNK", "HDFCBANK", "ICICIBANK",
        "IDFCFIRSTB", "INDUSINDBK", "KOTAKBANK", "RBLBANK", "YESBANK",
    ),
    "NIFTY FIN SERVICE": (
        "ABCAPITAL", "BAJAJFINSV", "BAJAJHLDNG", "BAJFINANCE", "CHOLAFIN", "HDFCAMC",
        "HDFCLIFE", "ICICIGI", "ICICIPRULI", "JIOFIN", "LICHSGFIN", "LICI", "LTF",
        "IRFC", "MANAPPURAM", "MFSL", "MUTHOOTFIN", "PFC", "PNBHOUSING", "POLICYBZR", "RECLTD",
        "SAMMAANCAP", "SBICARD", "SBILIFE", "SHRIRAMFIN",
    ),
    "NIFTY CAPITAL MKT": (
        "360ONE", "ANGELONE", "BSE", "CAMS", "CDSL", "IEX", "KFINTECH", "MCX",
        "MOTILALOFS", "NAM-INDIA", "NUVAMA",
    ),
    "NIFTY IT": (
        "COFORGE", "HCLTECH", "INFY", "KPITTECH", "LTM", "MPHASIS", "OFSS",
        "PERSISTENT", "TATAELXSI", "TCS", "TECHM", "WIPRO",
    ),
    "NIFTY PHARMA": (
        "ALKEM", "AUROPHARMA", "BIOCON", "CIPLA", "DIVISLAB", "DRREDDY", "GLENMARK",
        "LAURUSLABS", "LUPIN", "MANKIND", "SUNPHARMA", "TORNTPHARM", "ZYDUSLIFE",
    ),
    "NIFTY HEALTHCARE": ("APOLLOHOSP", "FORTIS", "MAXHEALTH"),
    "NIFTY FMCG": (
        "BRITANNIA", "COLPAL", "DABUR", "GODFRYPHLP", "GODREJCP", "HINDUNILVR",
        "ITC", "MARICO", "NESTLEIND", "PATANJALI", "RADICO", "TATACONSUM", "UNITDSPR", "VBL",
    ),
    "NIFTY OIL AND GAS": ("BPCL", "GAIL", "HINDPETRO", "IOC", "OIL", "ONGC", "PETRONET", "RELIANCE"),
    "NIFTY ENERGY": (
        "ADANIENSOL", "ADANIGREEN", "ADANIPOWER", "INOXWIND", "IREDA", "JSWENERGY",
        "NHPC", "NTPC", "POWERGRID", "PREMIERENE", "SUZLON", "TATAPOWER", "WAAREEENER",
    ),
    "NIFTY METAL": (
        "APLAPOLLO", "HINDALCO", "HINDZINC", "JINDALSTEL", "JSWSTEEL", "NATIONALUM",
        "NMDC", "SAIL", "TATASTEEL", "VEDL",
    ),
    "NIFTY REALTY": ("DLF", "GODREJPROP", "LODHA", "OBEROIRLTY", "PHOENIXLTD", "PRESTIGE"),
    "NIFTY CONSR DURBL": (
        "AMBER", "ASIANPAINT", "BLUESTARCO", "CROMPTON", "DIXON", "HAVELLS", "KALYANKJIL",
        "PAGEIND", "PGEL", "TITAN", "VOLTAS",
    ),
    "NIFTY CHEMICALS": ("ASTRAL", "PIDILITIND", "PIIND", "SOLARINDS", "SRF", "SUPREMEIND", "UPL"),
    "NIFTY INFRA": (
        "ABB", "AMBUJACEM", "BDL", "BEL", "BHEL", "CGPOWER", "COCHINSHIP", "CUMMINSIND",
        "DALBHARAT", "GRASIM", "GVT&D", "HAL", "KAYNES", "KEI", "LT", "MAZDOCK",
        "NBCC", "POLYCAB", "POWERINDIA", "RVNL", "SHREECEM", "SIEMENS", "ULTRACEMCO",
    ),
    "NIFTY TRANS LOGIS": ("ADANIPORTS", "CONCOR", "DELHIVERY", "GMRAIRPORT", "INDIGO"),
    "NIFTY CONSUMPTION": (
        "DMART", "ETERNAL", "INDHOTEL", "JUBLFOOD", "NAUKRI", "NYKAA", "PAYTM", "SWIGGY",
        "TRENT", "VMM",
    ),
    "NIFTY SERV SECTOR": ("BHARTIARTL", "IDEA", "INDUSTOWER"),
    "NIFTY COMMODITIES": ("ADANIENT", "COALINDIA"),
}

FNO_SYMBOL_SECTOR_OVERRIDES = {
    symbol: sector
    for sector, symbols in FNO_SYMBOL_SECTOR_GROUPS.items()
    for symbol in symbols
}


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


def build_sector_map_from_symbol_overrides(
    symbols: list[str],
    nse_instruments: list[dict[str, str]],
    source: str = "Nifty Indices sectoral catalog + built-in F&O symbol overrides",
) -> dict[str, Any]:
    index_symbols = zerodha_index_symbols(nse_instruments)
    sectors: dict[str, dict[str, str]] = {}
    mapped_symbols: dict[str, dict[str, str]] = {}
    unmapped: dict[str, dict[str, Any]] = {}

    for symbol in symbols:
        key = symbol.upper()
        preferred = FNO_SYMBOL_SECTOR_OVERRIDES.get(key)
        selected = _canonical_index_name(preferred) if preferred else None
        if selected and selected in index_symbols:
            sectors.setdefault(
                selected,
                {
                    "index_symbol": selected,
                    "data_file": f"{safe_symbol_filename(selected)}.csv",
                    "source_url": sector_source_url(selected),
                },
            )
            mapped_symbols[key] = {
                "sector": selected,
                "index_symbol": selected,
                "data_file": f"{safe_symbol_filename(selected)}.csv",
                "source": "built-in F&O sector override",
                "source_url": sector_source_url(selected),
            }
        else:
            unmapped[key] = {
                "sector": "NA",
                "preferred_index": preferred or "",
                "reason": (
                    "No confident stock-to-sector mapping configured."
                    if not preferred
                    else "Preferred sector index is not present in Zerodha NSE instruments."
                ),
            }

    return {
        "generated_on": date.today().isoformat(),
        "source": source,
        "sector_source_catalog": [
            {"index_symbol": index_symbol, "source_url": url}
            for index_symbol, url in NIFTY_SECTOR_INDEX_SOURCES
        ],
        "sectors": dict(sorted(sectors.items())),
        "symbols": dict(sorted(mapped_symbols.items())),
        "unmapped": dict(sorted(unmapped.items())),
    }


def sector_source_url(index_symbol: str) -> str:
    canonical = _canonical_index_name(index_symbol)
    for source_index, url in NIFTY_SECTOR_INDEX_SOURCES:
        if _canonical_index_name(source_index) == canonical:
            return url
    return ""


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
