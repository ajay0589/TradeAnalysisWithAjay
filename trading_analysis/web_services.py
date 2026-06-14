from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from trading_analysis.analysis.market_structure import analyze_market_structure
from trading_analysis.analysis.options import (
    analyze_option_chain,
    load_option_chain_snapshot,
    nearest_expiry,
    option_contracts_for_symbol,
    select_strikes_around_spot,
    write_option_chain_snapshot,
)
from trading_analysis.analysis.relative_strength import (
    analyze_relative_strength,
    load_sector_map,
    sector_config_for_symbol,
)
from trading_analysis.analysis.technical import analyze_technical
from trading_analysis.analysis.trade_decision import build_trade_decision
from trading_analysis.brokers.zerodha import (
    ZerodhaKiteClient,
    build_login_url,
    generate_session,
    load_instruments_csv,
    resolve_instrument_token,
    write_candles_csv,
)
from trading_analysis.candles import (
    apply_window,
    candle_path,
    candle_window,
    fetch_interval,
    normalize_timeframe,
    prepare_candles,
    safe_symbol_filename,
    source_timeframe,
    timeframe_label,
)
from trading_analysis.config import load_settings, load_watchlist, upsert_env_value
from trading_analysis.data_sources.csv_loader import load_candles


DEFAULT_REFRESH_DAYS = {
    "month": 1460,
    "week": 730,
    "day": 365,
    "60minute": 90,
    "15minute": 45,
}


class AnalysisService:
    def __init__(
        self,
        watchlist_path: str | Path = "config/watchlist.fno.json",
        daily_data_dir: str | Path = "data/raw/candles",
        hourly_data_dir: str | Path = "data/raw/candles/60minute",
        sector_map_path: str | Path = "config/sector_map.generated.json",
        nse_instruments_path: str | Path = "data/raw/zerodha/instruments_NSE.csv",
        nfo_instruments_path: str | Path = "data/raw/zerodha/instruments_NFO.csv",
        benchmark_file: str = "NIFTY_50.csv",
    ) -> None:
        self.watchlist_path = Path(watchlist_path)
        self.daily_data_dir = Path(daily_data_dir)
        self.hourly_data_dir = Path(hourly_data_dir)
        self.sector_map_path = Path(sector_map_path)
        self.nse_instruments_path = Path(nse_instruments_path)
        self.nfo_instruments_path = Path(nfo_instruments_path)
        self.benchmark_file = benchmark_file

    def zerodha_status(self) -> dict[str, Any]:
        creds = load_settings().broker_credentials
        status = {
            "api_key_configured": bool(creds.zerodha_api_key),
            "api_secret_configured": bool(creds.zerodha_api_secret),
            "access_token_configured": bool(creds.zerodha_access_token),
            "token_status": "missing",
            "message": "Zerodha access token is not configured.",
        }
        if not creds.zerodha_api_key or not creds.zerodha_access_token:
            return status

        try:
            client = ZerodhaKiteClient(creds.zerodha_api_key, creds.zerodha_access_token, timeout_seconds=8)
            client.quotes(["NSE:NIFTY 50"])
            status["token_status"] = "valid"
            status["message"] = "Zerodha access token validated successfully."
            return status
        except HTTPError as exc:
            status["token_status"] = "expired_or_invalid" if exc.code in {401, 403} else "check_failed"
            status["message"] = f"Zerodha token check failed with HTTP {exc.code}."
            return status
        except URLError as exc:
            status["token_status"] = "check_failed"
            status["message"] = f"Could not reach Zerodha: {exc.reason}"
            return status
        except Exception as exc:
            status["token_status"] = "check_failed"
            status["message"] = f"Zerodha token check failed: {exc}"
            return status

    def zerodha_login_url(self) -> dict[str, str]:
        creds = load_settings().broker_credentials
        if not creds.zerodha_api_key:
            raise ValueError("Missing ZERODHA_API_KEY in .env")
        return {"login_url": build_login_url(creds.zerodha_api_key)}

    def update_zerodha_access_token(self, request_token_or_url: str, env_file: str | Path = ".env") -> dict[str, Any]:
        creds = load_settings().broker_credentials
        if not creds.zerodha_api_key or not creds.zerodha_api_secret:
            raise ValueError("Missing ZERODHA_API_KEY or ZERODHA_API_SECRET in .env")
        session = generate_session(
            api_key=creds.zerodha_api_key,
            api_secret=creds.zerodha_api_secret,
            request_token=request_token_or_url,
        )
        access_token = session.get("access_token")
        if not access_token:
            raise ValueError("Zerodha response did not contain access_token.")
        upsert_env_value(env_file, "ZERODHA_ACCESS_TOKEN", access_token)
        return {
            "updated": True,
            "token_status": "updated",
            "user_id": session.get("user_id"),
            "message": "Zerodha access token updated in .env.",
        }

    def symbols(self) -> dict[str, Any]:
        watchlist = self._watchlist_symbols()
        names = self._symbol_names()
        available = [symbol for symbol in watchlist if self._has_candles(symbol, "day")]
        return {
            "total": len(watchlist),
            "available": len(available),
            "missing": len(watchlist) - len(available),
            "symbols": [
                {
                    "symbol": symbol,
                    "name": names.get(symbol, ""),
                    "has_daily": self._has_candles(symbol, "day"),
                    "has_hourly": self._has_candles(symbol, "60minute"),
                    "has_15minute": self._has_candles(symbol, "15minute"),
                }
                for symbol in watchlist
            ],
        }

    def analyze_symbol(
        self,
        symbol: str,
        include_option_chain: bool = False,
        previous_snapshot: str | None = None,
        strikes_around: int = 10,
        timeframe: str = "day",
        from_date: str | None = None,
        to_date: str | None = None,
        days: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        symbol = self.resolve_symbol(symbol)
        normalized_timeframe = normalize_timeframe(timeframe)
        window = candle_window(from_date=from_date, to_date=to_date, days=days)
        if refresh:
            self.refresh_candles(symbol, normalized_timeframe, window)

        chart_candles = self._load_timeframe(symbol, normalized_timeframe, window)
        if len(chart_candles) < 20:
            raise ValueError(
                f"{timeframe_label(normalized_timeframe)} analysis needs at least 20 candles; "
                f"found {len(chart_candles)} for {symbol}."
            )
        chart_technical = analyze_technical(chart_candles)
        chart_structure = analyze_market_structure(chart_candles)

        hourly = chart_candles if normalized_timeframe == "60minute" else self._load_optional_timeframe(symbol, "60minute", window)
        hourly_technical = analyze_technical(hourly) if hourly and len(hourly) >= 20 else None
        hourly_structure = analyze_market_structure(hourly) if hourly and len(hourly) >= 10 else None

        relative_strength = self._relative_strength(symbol, chart_candles, normalized_timeframe, window)
        option_chain = None
        warnings: list[str] = []
        if include_option_chain:
            try:
                option_chain = self._option_chain(symbol, previous_snapshot, strikes_around)
            except Exception as exc:
                warnings.append(f"Option-chain fetch failed: {exc}")

        decision = build_trade_decision(
            symbol=symbol,
            daily_technical=chart_technical,
            daily_structure=chart_structure,
            hourly_technical=hourly_technical,
            hourly_structure=hourly_structure,
            relative_strength=relative_strength,
            option_chain=option_chain,
        )
        setup = classify_setup(decision.score, chart_technical.trend, chart_structure.trend)

        return {
            "symbol": symbol,
            "setup": setup,
            "decision": asdict(decision),
            "chart": {
                "timeframe": normalized_timeframe,
                "label": timeframe_label(normalized_timeframe),
                "candle_count": len(chart_candles),
                "from": chart_candles[0].timestamp if chart_candles else None,
                "to": chart_candles[-1].timestamp if chart_candles else None,
                "technical": asdict(chart_technical),
                "structure": asdict(chart_structure),
            },
            "daily": {
                "technical": asdict(chart_technical),
                "structure": asdict(chart_structure),
            },
            "hourly": {
                "technical": asdict(hourly_technical) if hourly_technical else None,
                "structure": asdict(hourly_structure) if hourly_structure else None,
            },
            "relative_strength": asdict(relative_strength),
            "option_chain": asdict(option_chain) if option_chain else None,
            "warnings": warnings,
        }

    def refresh_candles(self, symbol: str, timeframe: str, window) -> list[dict[str, Any]]:
        source = source_timeframe(timeframe)
        interval = fetch_interval(source)
        client = _zerodha_client()
        instruments = self._nse_instruments()
        fetch_window = _window_with_default_from(window, timeframe)
        targets = [(symbol.upper(), safe_symbol_filename(symbol))]

        benchmark_symbol = self.benchmark_file.replace("_", " ").removesuffix(".csv")
        targets.append((benchmark_symbol.upper(), Path(self.benchmark_file).stem))

        sector_config = sector_config_for_symbol(load_sector_map(self.sector_map_path), symbol)
        if sector_config:
            targets.append((sector_config["index_symbol"].upper(), Path(sector_config["data_file"]).stem))

        results = []
        seen: set[tuple[str, str]] = set()
        for tradingsymbol, file_stem in targets:
            key = (tradingsymbol, file_stem)
            if key in seen:
                continue
            seen.add(key)
            token = resolve_instrument_token(instruments, "NSE", tradingsymbol)
            candles = client.historical_candles(
                instrument_token=token,
                interval=interval,
                from_time=fetch_window.from_time,
                to_time=fetch_window.to_time,
            )
            output = candle_path(self.daily_data_dir, source, file_stem)
            write_candles_csv(output, candles)
            results.append(
                {
                    "symbol": tradingsymbol,
                    "timeframe": source,
                    "candles": len(candles),
                    "output": str(output),
                }
            )
        return results

    def resolve_symbol(self, value: str) -> str:
        query = value.upper().strip()
        watchlist = set(self._watchlist_symbols())
        if query in watchlist or self._has_candles(query, "day"):
            return query

        matches = []
        for row in self._nse_instruments():
            symbol = row.get("tradingsymbol", "").upper()
            name = row.get("name", "").upper()
            if symbol not in watchlist:
                continue
            if query == symbol or query == name or query in name:
                matches.append(symbol)

        unique = sorted(set(matches))
        if len(unique) == 1:
            return unique[0]
        if len(unique) > 1:
            raise ValueError(f"Multiple F&O matches for '{value}': {', '.join(unique[:8])}")
        return query

    def scan(
        self,
        scan_type: str,
        limit: int = 50,
        timeframe: str = "day",
        from_date: str | None = None,
        to_date: str | None = None,
        days: int | None = None,
    ) -> dict[str, Any]:
        scan_type = scan_type.lower()
        if scan_type not in {"bullish", "bearish", "neutral"}:
            raise ValueError("scan_type must be bullish, bearish, or neutral")

        normalized_timeframe = normalize_timeframe(timeframe)
        rows = []
        errors = []
        for symbol in self._watchlist_symbols():
            if not self._has_candles(symbol, normalized_timeframe):
                continue
            try:
                result = self.analyze_symbol(
                    symbol,
                    include_option_chain=False,
                    timeframe=normalized_timeframe,
                    from_date=from_date,
                    to_date=to_date,
                    days=days,
                )
                setup = result["setup"]
                if setup["bucket"] == scan_type:
                    rows.append(_scan_row(result))
            except Exception as exc:
                errors.append({"symbol": symbol, "error": str(exc)})

        rows = sorted(
            rows,
            key=lambda row: _scan_sort_key(row, scan_type),
            reverse=(scan_type in {"bullish", "neutral"}),
        )
        return {
            "type": scan_type,
            "strategy": _strategy_for_scan(scan_type),
            "timeframe": normalized_timeframe,
            "timeframe_label": timeframe_label(normalized_timeframe),
            "available_symbols": self._available_count(normalized_timeframe),
            "total_fno_symbols": self.symbols()["total"],
            "results": rows[:limit],
            "errors": errors[:20],
        }

    def _watchlist_symbols(self) -> list[str]:
        if not self.watchlist_path.exists():
            return []
        return [item.symbol for item in load_watchlist(self.watchlist_path)]

    def _symbol_names(self) -> dict[str, str]:
        return {
            row.get("tradingsymbol", "").upper(): row.get("name", "")
            for row in self._nse_instruments()
            if row.get("exchange", "").upper() == "NSE"
        }

    def _nse_instruments(self) -> list[dict[str, str]]:
        if not self.nse_instruments_path.exists():
            return []
        return load_instruments_csv(self.nse_instruments_path)

    def _relative_strength(self, symbol: str, chart_candles, timeframe: str, window) -> Any:
        benchmark = self._load_optional_timeframe(Path(self.benchmark_file).stem, timeframe, window)
        sector_map = load_sector_map(self.sector_map_path)
        sector_config = sector_config_for_symbol(sector_map, symbol)
        sector = None
        if sector_config:
            sector = self._load_optional_timeframe(Path(sector_config["data_file"]).stem, timeframe, window)
        return analyze_relative_strength(
            stock_candles=chart_candles,
            nifty_candles=benchmark,
            sector_candles=sector,
        )

    def _option_chain(self, symbol: str, previous_snapshot: str | None, strikes_around: int) -> Any:
        client = _zerodha_client()
        instruments = load_instruments_csv(self.nfo_instruments_path)
        contracts = option_contracts_for_symbol(instruments, symbol)
        if not contracts:
            raise ValueError(f"No NFO option contracts found for {symbol}")

        expiry = nearest_expiry(contracts)
        contracts = option_contracts_for_symbol(instruments, symbol, expiry=expiry)
        spot_key = f"NSE:{symbol}"
        spot_quote = client.quotes([spot_key]).get(spot_key, {})
        spot_price = _optional_float(spot_quote.get("last_price"))
        selected = select_strikes_around_spot(contracts, spot_price, strikes_around)
        quotes = client.quotes([contract.kite_key for contract in selected])
        default_snapshot = Path("data/raw/option_chain") / f"{symbol}_{expiry.isoformat()}.csv"
        previous_path = Path(previous_snapshot) if previous_snapshot else default_snapshot
        previous = load_option_chain_snapshot(previous_path) if previous_path.exists() else {}
        analysis = analyze_option_chain(symbol, expiry, selected, quotes, spot_price, previous)
        write_option_chain_snapshot(default_snapshot, analysis)
        return analysis

    def _has_candles(self, symbol: str, timeframe: str) -> bool:
        return candle_path(self.daily_data_dir, timeframe, symbol).exists()

    def _available_count(self, timeframe: str) -> int:
        return sum(1 for symbol in self._watchlist_symbols() if self._has_candles(symbol, timeframe))

    def _load_timeframe(self, symbol_or_stem: str, timeframe: str, window):
        raw = load_candles(candle_path(self.daily_data_dir, timeframe, symbol_or_stem))
        return prepare_candles(raw, timeframe, window)

    def _load_optional_timeframe(self, symbol_or_stem: str, timeframe: str, window):
        try:
            return self._load_timeframe(symbol_or_stem, timeframe, window)
        except FileNotFoundError:
            return None


def classify_setup(score: int, technical_trend: str, structure_trend: str) -> dict[str, str]:
    if score >= 60 and technical_trend != "bearish" and structure_trend in {"uptrend", "range"}:
        return {
            "bucket": "bullish",
            "strategy": "Sell put option",
            "stance": "Bullish put-sell watch",
        }
    if score <= 40 and technical_trend != "bullish":
        return {
            "bucket": "bearish",
            "strategy": "Sell call option",
            "stance": "Bearish call-sell watch",
        }
    if 42 <= score <= 58 and structure_trend == "range":
        return {
            "bucket": "neutral",
            "strategy": "Sell call and put as strangle",
            "stance": "Neutral strangle watch",
        }
    return {
        "bucket": "watch",
        "strategy": "No options selling setup",
        "stance": "Wait",
    }


def _scan_row(result: dict[str, Any]) -> dict[str, Any]:
    decision = result["decision"]
    chart = result["chart"]
    hourly = result["hourly"]
    rs = result["relative_strength"]
    return {
        "symbol": result["symbol"],
        "score": decision["score"],
        "bias": decision["bias"],
        "stance": result["setup"]["stance"],
        "strategy": result["setup"]["strategy"],
        "timeframe": chart["label"],
        "close": chart["technical"]["close"],
        "daily_trend": chart["technical"]["trend"],
        "daily_structure": chart["structure"]["trend"],
        "hourly_trend": (hourly["technical"] or {}).get("trend", "-"),
        "support": chart["structure"]["support"],
        "resistance": chart["structure"]["resistance"],
        "invalidation": chart["structure"]["invalidation"],
        "stock_vs_nifty": _rs_label(rs.get("stock_vs_nifty")),
        "reason": "; ".join(decision["reasons"][:3]),
    }


def _scan_sort_key(row: dict[str, Any], scan_type: str) -> float:
    score = float(row["score"])
    if scan_type == "bearish":
        return -score
    if scan_type == "neutral":
        return -abs(score - 50)
    return score


def _strategy_for_scan(scan_type: str) -> str:
    return {
        "bullish": "Sell put option",
        "bearish": "Sell call option",
        "neutral": "Sell call and put options as strangle",
    }[scan_type]


def _rs_label(signal: dict[str, Any] | None) -> str:
    if not signal:
        return "-"
    relative = signal.get("relative_return_percent")
    suffix = "" if relative is None else f" ({relative:.2f}%)"
    return f"{signal.get('label', '-')}{suffix}"


def _zerodha_client() -> ZerodhaKiteClient:
    creds = load_settings().broker_credentials
    if not creds.zerodha_api_key or not creds.zerodha_access_token:
        raise ValueError("Missing Zerodha API key/access token")
    return ZerodhaKiteClient(creds.zerodha_api_key, creds.zerodha_access_token)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _window_with_default_from(window, timeframe: str):
    if window.from_time:
        return window
    days = DEFAULT_REFRESH_DAYS[normalize_timeframe(timeframe)]
    return type(window)(
        from_time=window.to_time - timedelta(days=days),
        to_time=window.to_time,
        days=days,
    )
