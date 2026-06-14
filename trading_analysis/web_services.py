from __future__ import annotations

import csv
import json
import threading
import time
import uuid
from dataclasses import asdict
from datetime import date, datetime
from datetime import timedelta
from io import StringIO
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
from trading_analysis.data_sources.nse_equity import build_sector_map_from_csv_rows
from trading_analysis.data_sources.nse_fii_dii import fetch_fii_dii_activity, write_fii_dii_csv


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
        fii_dii_path: str | Path = "data/raw/nse/fii_dii.csv",
        reports_dir: str | Path = "reports",
        benchmark_file: str = "NIFTY_50.csv",
    ) -> None:
        self.watchlist_path = Path(watchlist_path)
        self.daily_data_dir = Path(daily_data_dir)
        self.hourly_data_dir = Path(hourly_data_dir)
        self.sector_map_path = Path(sector_map_path)
        self.nse_instruments_path = Path(nse_instruments_path)
        self.nfo_instruments_path = Path(nfo_instruments_path)
        self.fii_dii_path = Path(fii_dii_path)
        self.reports_dir = Path(reports_dir)
        self.benchmark_file = benchmark_file
        self._jobs: dict[str, dict[str, Any]] = {}
        self._jobs_lock = threading.Lock()

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

    def start_bulk_candle_download(
        self,
        timeframes: list[str],
        days: int | None = 90,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int | None = None,
        sleep_seconds: float = 0.35,
    ) -> dict[str, Any]:
        normalized_timeframes = _normalize_bulk_timeframes(timeframes)
        window = candle_window(from_date=from_date, to_date=to_date, days=None if from_date else days)
        if window.from_time is None:
            window = type(window)(
                from_time=window.to_time - timedelta(days=days or 90),
                to_time=window.to_time,
                days=days or 90,
            )
        symbols = self._watchlist_symbols()
        if limit:
            symbols = symbols[:limit]
        targets = self._bulk_targets(symbols)
        total = len(targets) * len(normalized_timeframes)
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "type": "bulk_candles",
            "status": "queued",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "started_at": None,
            "finished_at": None,
            "total": total,
            "completed": 0,
            "successes": 0,
            "failures": 0,
            "current": "",
            "results": [],
            "errors": [],
            "timeframes": normalized_timeframes,
            "window": {
                "from": window.from_time,
                "to": window.to_time,
                "days": window.days,
            },
        }
        with self._jobs_lock:
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run_bulk_candle_download,
            args=(job_id, targets, normalized_timeframes, window, sleep_seconds),
            daemon=True,
        )
        thread.start()
        return self.job_status(job_id)

    def job_status(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job:
                raise ValueError(f"Unknown job id: {job_id}")
            return dict(job)

    def sector_map_status(self) -> dict[str, Any]:
        if not self.sector_map_path.exists():
            return {
                "exists": False,
                "path": str(self.sector_map_path),
                "mapped": 0,
                "unmapped": 0,
                "sectors": 0,
                "generated_on": None,
            }
        payload = json.loads(self.sector_map_path.read_text(encoding="utf-8"))
        return {
            "exists": True,
            "path": str(self.sector_map_path),
            "mapped": len(payload.get("symbols", {})),
            "unmapped": len(payload.get("unmapped", {})),
            "sectors": len(payload.get("sectors", {})),
            "generated_on": payload.get("generated_on"),
        }

    def generate_sector_map_from_csv_text(
        self,
        csv_text: str,
        include_all: bool = False,
    ) -> dict[str, Any]:
        rows = list(csv.DictReader(StringIO(csv_text)))
        if not rows:
            raise ValueError("CSV did not contain any rows.")
        symbols_filter = None if include_all else self._watchlist_symbols()
        sector_map = build_sector_map_from_csv_rows(
            rows=rows,
            nse_instruments=self._nse_instruments(),
            source="web upload",
            symbols_filter=symbols_filter,
        )
        self.sector_map_path.parent.mkdir(parents=True, exist_ok=True)
        self.sector_map_path.write_text(json.dumps(sector_map, indent=2), encoding="utf-8")
        return self.sector_map_status()

    def fii_dii_activity(self, refresh: bool = False) -> dict[str, Any]:
        rows = []
        error = None
        if refresh:
            try:
                rows = fetch_fii_dii_activity()
                write_fii_dii_csv(self.fii_dii_path, rows)
            except Exception as exc:
                error = str(exc)
        if not rows:
            rows = _read_csv_rows(self.fii_dii_path)
        return {
            "path": str(self.fii_dii_path),
            "exists": self.fii_dii_path.exists(),
            "rows": rows,
            "latest": rows[0] if rows else None,
            "count": len(rows),
            "error": error,
        }

    def option_expiries(self, symbol: str) -> dict[str, Any]:
        symbol = self.resolve_symbol(symbol)
        contracts = option_contracts_for_symbol(load_instruments_csv(self.nfo_instruments_path), symbol)
        expiries = sorted({contract.expiry.isoformat() for contract in contracts})
        return {
            "symbol": symbol,
            "expiries": expiries,
            "nearest": nearest_expiry(contracts).isoformat() if contracts else None,
        }

    def export_report(self, payload: dict[str, Any]) -> dict[str, str]:
        symbol = str(payload.get("symbol") or "REPORT").upper()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = self.reports_dir / f"{symbol}_trade_decision_{timestamp}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return {
            "saved": True,
            "path": str(output),
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
        expiry: str | None = None,
        all_strikes: bool = False,
        timeframe: str = "day",
        from_date: str | None = None,
        to_date: str | None = None,
        days: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        symbol = self.resolve_symbol(symbol)
        normalized_timeframe = normalize_timeframe(timeframe)
        window = candle_window(from_date=from_date, to_date=to_date, days=days)
        refresh_results: list[dict[str, Any]] = []
        refresh_errors: list[str] = []
        if refresh:
            for refresh_timeframe in _refresh_timeframes_for_analysis(normalized_timeframe):
                try:
                    refresh_results.extend(self.refresh_candles(symbol, refresh_timeframe, window))
                except Exception as exc:
                    refresh_errors.append(f"{timeframe_label(refresh_timeframe)}: {exc}")

        chart_candles, chart_source = self._load_timeframe_with_summary(symbol, normalized_timeframe, window)
        if len(chart_candles) < 20:
            raise ValueError(
                f"{timeframe_label(normalized_timeframe)} analysis needs at least 20 candles; "
                f"found {len(chart_candles)} for {symbol}."
            )
        chart_technical = analyze_technical(chart_candles)
        chart_structure = analyze_market_structure(chart_candles)

        if normalized_timeframe == "60minute":
            hourly = chart_candles
            hourly_source = chart_source
        else:
            hourly, hourly_source = self._load_optional_timeframe_with_summary(symbol, "60minute", window)
        hourly_technical = analyze_technical(hourly) if hourly and len(hourly) >= 20 else None
        hourly_structure = analyze_market_structure(hourly) if hourly and len(hourly) >= 10 else None

        relative_strength = self._relative_strength(symbol, chart_candles, normalized_timeframe, window)
        option_chain = None
        warnings: list[str] = []
        for refresh_error in refresh_errors:
            warnings.append(f"Zerodha candle refresh failed; using cached candles: {refresh_error}")
        if include_option_chain:
            try:
                option_chain = self._option_chain(symbol, previous_snapshot, strikes_around, expiry, all_strikes)
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
        multi_timeframe = self._multi_timeframe_analysis(symbol, window)
        analysis_summary = self._analysis_summary(
            symbol=symbol,
            timeframe=normalized_timeframe,
            chart_source=chart_source,
            hourly_source=hourly_source,
            multi_timeframe=multi_timeframe,
            relative_strength=relative_strength,
            include_option_chain=include_option_chain,
            option_chain=option_chain,
            refresh_requested=refresh,
            refresh_results=refresh_results,
            refresh_error="; ".join(refresh_errors) if refresh_errors else None,
            warnings=warnings + list(decision.warnings),
            window=window,
        )

        return {
            "symbol": symbol,
            "setup": setup,
            "decision": asdict(decision),
            "multi_timeframe": multi_timeframe,
            "option_trade_guide": _option_trade_guide(setup, chart_structure, option_chain),
            "analysis_summary": analysis_summary,
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

    def _run_bulk_candle_download(self, job_id: str, targets, timeframes, window, sleep_seconds: float) -> None:
        self._update_job(job_id, status="running", started_at=datetime.now().isoformat(timespec="seconds"))
        try:
            client = _zerodha_client()
            instruments = self._nse_instruments()
            for tradingsymbol, file_stem in targets:
                for timeframe in timeframes:
                    current = f"{tradingsymbol} {timeframe}"
                    self._update_job(job_id, current=current)
                    try:
                        token = resolve_instrument_token(instruments, "NSE", tradingsymbol)
                        candles = client.historical_candles(
                            instrument_token=token,
                            interval=fetch_interval(timeframe),
                            from_time=window.from_time,
                            to_time=window.to_time,
                        )
                        output = candle_path(self.daily_data_dir, timeframe, file_stem)
                        write_candles_csv(output, candles)
                        self._append_job_result(
                            job_id,
                            {
                                "symbol": tradingsymbol,
                                "timeframe": timeframe,
                                "candles": len(candles),
                                "output": str(output),
                            },
                        )
                    except Exception as exc:
                        self._append_job_error(job_id, f"{current}: {exc}")
                    finally:
                        self._increment_job(job_id)
                        if sleep_seconds:
                            time.sleep(sleep_seconds)
            self._update_job(job_id, status="completed", finished_at=datetime.now().isoformat(timespec="seconds"), current="")
        except Exception as exc:
            self._append_job_error(job_id, str(exc))
            self._update_job(job_id, status="failed", finished_at=datetime.now().isoformat(timespec="seconds"), current="")

    def _bulk_targets(self, symbols: list[str]) -> list[tuple[str, str]]:
        targets = [(symbol.upper(), safe_symbol_filename(symbol)) for symbol in symbols]
        targets.append(("NIFTY 50", "NIFTY_50"))
        sector_map = load_sector_map(self.sector_map_path)
        for symbol in symbols:
            sector_config = sector_config_for_symbol(sector_map, symbol)
            if sector_config:
                targets.append((sector_config["index_symbol"].upper(), Path(sector_config["data_file"]).stem))
        return _dedupe_targets(targets)

    def _update_job(self, job_id: str, **updates) -> None:
        with self._jobs_lock:
            self._jobs[job_id].update(updates)

    def _increment_job(self, job_id: str) -> None:
        with self._jobs_lock:
            job = self._jobs[job_id]
            job["completed"] += 1

    def _append_job_result(self, job_id: str, result: dict[str, Any]) -> None:
        with self._jobs_lock:
            job = self._jobs[job_id]
            job["successes"] += 1
            job["results"].append(result)
            job["results"] = job["results"][-50:]

    def _append_job_error(self, job_id: str, error: str) -> None:
        with self._jobs_lock:
            job = self._jobs[job_id]
            job["failures"] += 1
            job["errors"].append(error)
            job["errors"] = job["errors"][-100:]

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
        limit: int | None = None,
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
        limited_rows = rows if limit is None else rows[:limit]
        return {
            "type": scan_type,
            "strategy": _strategy_for_scan(scan_type),
            "timeframe": normalized_timeframe,
            "timeframe_label": timeframe_label(normalized_timeframe),
            "available_symbols": self._available_count(normalized_timeframe),
            "total_fno_symbols": self.symbols()["total"],
            "matched_symbols": len(rows),
            "limit": limit,
            "results": limited_rows,
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

    def _option_chain(
        self,
        symbol: str,
        previous_snapshot: str | None,
        strikes_around: int,
        expiry: str | None = None,
        all_strikes: bool = False,
    ) -> Any:
        client = _zerodha_client()
        instruments = load_instruments_csv(self.nfo_instruments_path)
        contracts = option_contracts_for_symbol(instruments, symbol)
        if not contracts:
            raise ValueError(f"No NFO option contracts found for {symbol}")

        selected_expiry = date.fromisoformat(expiry) if expiry else nearest_expiry(contracts)
        contracts = option_contracts_for_symbol(instruments, symbol, expiry=selected_expiry)
        spot_key = f"NSE:{symbol}"
        spot_quote = client.quotes([spot_key]).get(spot_key, {})
        spot_price = _optional_float(spot_quote.get("last_price"))
        selected = contracts if all_strikes else select_strikes_around_spot(contracts, spot_price, strikes_around)
        quotes = client.quotes([contract.kite_key for contract in selected])
        default_snapshot = Path("data/raw/option_chain") / f"{symbol}_{selected_expiry.isoformat()}.csv"
        previous_path = Path(previous_snapshot) if previous_snapshot else default_snapshot
        previous = load_option_chain_snapshot(previous_path) if previous_path.exists() else {}
        analysis = analyze_option_chain(symbol, selected_expiry, selected, quotes, spot_price, previous)
        write_option_chain_snapshot(default_snapshot, analysis)
        return analysis

    def _has_candles(self, symbol: str, timeframe: str) -> bool:
        return candle_path(self.daily_data_dir, timeframe, symbol).exists()

    def _available_count(self, timeframe: str) -> int:
        return sum(1 for symbol in self._watchlist_symbols() if self._has_candles(symbol, timeframe))

    def _load_timeframe(self, symbol_or_stem: str, timeframe: str, window):
        candles, _summary = self._load_timeframe_with_summary(symbol_or_stem, timeframe, window)
        return candles

    def _load_optional_timeframe(self, symbol_or_stem: str, timeframe: str, window):
        try:
            return self._load_timeframe(symbol_or_stem, timeframe, window)
        except FileNotFoundError:
            return None

    def _load_timeframe_with_summary(self, symbol_or_stem: str, timeframe: str, window):
        path = candle_path(self.daily_data_dir, timeframe, symbol_or_stem)
        raw = load_candles(path)
        prepared = prepare_candles(raw, timeframe, window)
        return prepared, _candle_source_summary(
            symbol=symbol_or_stem,
            timeframe=timeframe,
            path=path,
            raw_candles=raw,
            analyzed_candles=prepared,
        )

    def _load_optional_timeframe_with_summary(self, symbol_or_stem: str, timeframe: str, window):
        try:
            return self._load_timeframe_with_summary(symbol_or_stem, timeframe, window)
        except FileNotFoundError:
            return None, _missing_candle_source_summary(symbol_or_stem, timeframe, candle_path(self.daily_data_dir, timeframe, symbol_or_stem))

    def _analysis_summary(
        self,
        symbol: str,
        timeframe: str,
        chart_source: dict[str, Any],
        hourly_source: dict[str, Any],
        multi_timeframe: dict[str, Any],
        relative_strength,
        include_option_chain: bool,
        option_chain,
        refresh_requested: bool,
        refresh_results: list[dict[str, Any]],
        refresh_error: str | None,
        warnings: list[str],
        window,
    ) -> dict[str, Any]:
        rows = []
        if refresh_requested and refresh_results:
            rows.append(
                _coverage_row(
                    "Zerodha candle refresh",
                    "pulled",
                    f"Pulled {len(refresh_results)} candle file(s) before analysis.",
                    "; ".join(f"{item['symbol']} {item['timeframe']} {item['candles']} candles" for item in refresh_results),
                )
            )
        elif refresh_requested:
            rows.append(
                _coverage_row(
                    "Zerodha candle refresh",
                    "failed",
                    f"Fresh pull failed; cached local data was used where available. {refresh_error or ''}".strip(),
                    "Check Zerodha token status.",
                )
            )
        else:
            rows.append(
                _coverage_row(
                    "Zerodha candle refresh",
                    "not_requested",
                    "Pull latest candles was off; analysis used local cached candle CSV files.",
                    "",
                )
            )

        rows.extend(
            [
                _coverage_row(
                    f"{timeframe_label(timeframe)} chart candles",
                    "analyzed",
                    _source_detail(chart_source),
                    chart_source["path"],
                ),
                _coverage_row(
                    "Technical indicators",
                    "analyzed",
                    "Calculated SMA20, EMA20, RSI14, ATR14, volume ratio, trend, and score.",
                    "Selected chart candles",
                ),
                _coverage_row(
                    "Market structure",
                    "analyzed",
                    "Calculated trend/range, swing support, resistance, and invalidation.",
                    "Selected chart candles",
                ),
            ]
        )

        if hourly_source.get("exists") and hourly_source.get("analyzed_count", 0) >= 20:
            rows.append(
                _coverage_row(
                    "60-minute confirmation",
                    "analyzed",
                    _source_detail(hourly_source),
                    hourly_source["path"],
                )
            )
        else:
            rows.append(
                _coverage_row(
                    "60-minute confirmation",
                    "missing",
                    "Not enough 60-minute candles were available for confirmation.",
                    hourly_source["path"],
                )
            )

        rows.append(
            _coverage_row(
                "Multi-timeframe direction",
                "analyzed" if multi_timeframe["analyzed_count"] else "missing",
                multi_timeframe["summary"],
                "Day, 1 hour, and 15 min candle CSVs",
            )
        )

        rows.extend(self._relative_strength_rows(symbol, timeframe, window, relative_strength))

        if include_option_chain and option_chain:
            rows.append(
                _coverage_row(
                    "Option chain",
                    "analyzed",
                    f"Nearest expiry {option_chain.expiry}; {option_chain.contract_count} contracts; PCR {option_chain.pcr_oi}; max pain {option_chain.max_pain}.",
                    f"{self.nfo_instruments_path}",
                )
            )
        elif include_option_chain:
            message = next((warning for warning in warnings if warning.startswith("Option-chain fetch failed")), "Option-chain fetch failed.")
            rows.append(_coverage_row("Option chain", "failed", message, f"{self.nfo_instruments_path}"))
        else:
            rows.append(
                _coverage_row(
                    "Option chain",
                    "not_requested",
                    "Option chain checkbox was off; IV, OI, PCR, max pain, and build-up were not included.",
                    "",
                )
            )

        rows.extend(
            [
                _coverage_row(
                    "Fundamentals",
                    "not_analyzed",
                    "Fundamental scoring is not included in this Web UI trade-decision score yet.",
                    "Watchlist fundamentals / future source",
                ),
                _coverage_row(
                    "FII/DII flow",
                    "not_analyzed",
                    "Market-wide FII/DII data is not included in this stock-specific UI score yet.",
                    "data/raw/nse/fii_dii.csv",
                ),
            ]
        )

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "timeframe_label": timeframe_label(timeframe),
            "window": {
                "from": window.from_time,
                "to": window.to_time,
                "days": window.days,
            },
            "rows": rows,
        }

    def _relative_strength_rows(self, symbol: str, timeframe: str, window, relative_strength) -> list[dict[str, str]]:
        rows = []
        benchmark_source = self._optional_source_summary(Path(self.benchmark_file).stem, timeframe, window)
        stock_vs_nifty = relative_strength.stock_vs_nifty
        if stock_vs_nifty:
            rows.append(
                _coverage_row(
                    "Relative strength vs Nifty",
                    "analyzed",
                    (
                        f"{stock_vs_nifty.label}; stock {_fmt_percent(stock_vs_nifty.subject_return_percent)}, "
                        f"Nifty {_fmt_percent(stock_vs_nifty.benchmark_return_percent)}, "
                        f"relative {_fmt_percent(stock_vs_nifty.relative_return_percent)}."
                    ),
                    benchmark_source["path"],
                )
            )
        else:
            rows.append(
                _coverage_row(
                    "Relative strength vs Nifty",
                    "missing",
                    "Nifty benchmark candles were missing or insufficient for this timeframe/window.",
                    benchmark_source["path"],
                )
            )

        sector_map = load_sector_map(self.sector_map_path)
        sector_config = sector_config_for_symbol(sector_map, symbol)
        if not sector_config:
            return rows + [
                _coverage_row(
                    "Relative strength vs sector",
                    "missing",
                    "No sector mapping found for this stock.",
                    str(self.sector_map_path),
                ),
                _coverage_row(
                    "Sector vs Nifty",
                    "missing",
                    "No sector mapping found for this stock.",
                    str(self.sector_map_path),
                ),
            ]

        sector_source = self._optional_source_summary(Path(sector_config["data_file"]).stem, timeframe, window)
        stock_vs_sector = relative_strength.stock_vs_sector
        sector_vs_nifty = relative_strength.sector_vs_nifty
        rows.append(
            _coverage_row(
                "Relative strength vs sector",
                "analyzed" if stock_vs_sector else "missing",
                (
                    f"{stock_vs_sector.label}; stock {_fmt_percent(stock_vs_sector.subject_return_percent)}, "
                    f"sector {_fmt_percent(stock_vs_sector.benchmark_return_percent)}, "
                    f"relative {_fmt_percent(stock_vs_sector.relative_return_percent)}."
                    if stock_vs_sector
                    else "Sector-index candles were missing or insufficient for this timeframe/window."
                ),
                sector_source["path"],
            )
        )
        rows.append(
            _coverage_row(
                "Sector vs Nifty",
                "analyzed" if sector_vs_nifty else "missing",
                (
                    f"{sector_vs_nifty.label}; sector {_fmt_percent(sector_vs_nifty.subject_return_percent)}, "
                    f"Nifty {_fmt_percent(sector_vs_nifty.benchmark_return_percent)}, "
                    f"relative {_fmt_percent(sector_vs_nifty.relative_return_percent)}."
                    if sector_vs_nifty
                    else "Sector or Nifty candles were missing or insufficient for this timeframe/window."
                ),
                sector_source["path"],
            )
        )
        return rows

    def _optional_source_summary(self, symbol_or_stem: str, timeframe: str, window) -> dict[str, Any]:
        _candles, summary = self._load_optional_timeframe_with_summary(symbol_or_stem, timeframe, window)
        return summary

    def _multi_timeframe_analysis(self, symbol: str, window) -> dict[str, Any]:
        rows = []
        for timeframe in ("day", "60minute", "15minute"):
            rows.append(self._multi_timeframe_row(symbol, timeframe, window))
        analyzed_rows = [row for row in rows if row["status"] == "analyzed"]
        alignment = _multi_timeframe_alignment(analyzed_rows)
        return {
            "symbol": symbol,
            "alignment": alignment["label"],
            "bias": alignment["bias"],
            "summary": alignment["summary"],
            "analyzed_count": len(analyzed_rows),
            "rows": rows,
        }

    def _multi_timeframe_row(self, symbol: str, timeframe: str, window) -> dict[str, Any]:
        path = candle_path(self.daily_data_dir, timeframe, symbol)
        try:
            candles, source = self._load_timeframe_with_summary(symbol, timeframe, window)
        except FileNotFoundError:
            return {
                "timeframe": timeframe,
                "label": timeframe_label(timeframe),
                "status": "missing",
                "message": "Candle CSV not found.",
                "path": str(path),
            }
        if len(candles) < 20:
            return {
                "timeframe": timeframe,
                "label": timeframe_label(timeframe),
                "status": "insufficient",
                "message": f"Only {len(candles)} candles available; at least 20 needed.",
                "path": str(path),
                "candle_count": len(candles),
            }

        technical = analyze_technical(candles)
        structure = analyze_market_structure(candles)
        volume_stats = _volume_stats(candles)
        return {
            "timeframe": timeframe,
            "label": timeframe_label(timeframe),
            "status": "analyzed",
            "message": "Analyzed",
            "path": source["path"],
            "candle_count": len(candles),
            "from": source["from"],
            "to": source["to"],
            "close": technical.close,
            "technical_trend": technical.trend,
            "structure_trend": structure.trend,
            "score": technical.score,
            "rsi14": technical.rsi14,
            "ema20": technical.ema20,
            "sma20": technical.sma20,
            "atr14": technical.atr14,
            "support": structure.support,
            "resistance": structure.resistance,
            "invalidation": structure.invalidation,
            "volume": volume_stats["volume"],
            "avg_volume20": volume_stats["avg_volume20"],
            "volume_ratio20": technical.volume_ratio20,
            "volume_signal": _volume_signal(technical.volume_ratio20),
            "reasons": technical.reasons,
        }


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
        "option_zone": _scan_option_zone(result["setup"]["bucket"], chart["structure"]),
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


def _coverage_row(name: str, status: str, detail: str, source: str) -> dict[str, str]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "source": source,
    }


def _candle_source_summary(
    symbol: str,
    timeframe: str,
    path: Path,
    raw_candles,
    analyzed_candles,
) -> dict[str, Any]:
    normalized = normalize_timeframe(timeframe)
    source = source_timeframe(normalized)
    return {
        "symbol": symbol.upper(),
        "timeframe": normalized,
        "timeframe_label": timeframe_label(normalized),
        "source_timeframe": source,
        "source_timeframe_label": timeframe_label(source),
        "derived": normalized != source,
        "path": str(path),
        "exists": True,
        "raw_count": len(raw_candles),
        "analyzed_count": len(analyzed_candles),
        "from": analyzed_candles[0].timestamp if analyzed_candles else None,
        "to": analyzed_candles[-1].timestamp if analyzed_candles else None,
    }


def _missing_candle_source_summary(symbol: str, timeframe: str, path: Path) -> dict[str, Any]:
    normalized = normalize_timeframe(timeframe)
    source = source_timeframe(normalized)
    return {
        "symbol": symbol.upper(),
        "timeframe": normalized,
        "timeframe_label": timeframe_label(normalized),
        "source_timeframe": source,
        "source_timeframe_label": timeframe_label(source),
        "derived": normalized != source,
        "path": str(path),
        "exists": False,
        "raw_count": 0,
        "analyzed_count": 0,
        "from": None,
        "to": None,
    }


def _source_detail(source: dict[str, Any]) -> str:
    if not source.get("exists"):
        return "Candle CSV was not found."
    derived = ""
    if source.get("derived"):
        derived = f" Derived from {source['source_timeframe_label']} candles."
    return (
        f"{source['analyzed_count']} analyzed candles from {source.get('from')} to {source.get('to')}. "
        f"Raw source has {source['raw_count']} candles.{derived}"
    )


def _fmt_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}%"


def _volume_stats(candles) -> dict[str, float | int | None]:
    if not candles:
        return {"volume": None, "avg_volume20": None}
    last_volume = candles[-1].volume
    if len(candles) < 20:
        return {"volume": last_volume, "avg_volume20": None}
    avg_volume20 = sum(candle.volume for candle in candles[-20:]) / 20
    return {"volume": last_volume, "avg_volume20": avg_volume20}


def _volume_signal(volume_ratio20: float | None) -> str:
    if volume_ratio20 is None:
        return "unknown"
    if volume_ratio20 >= 1.5:
        return "strong expansion"
    if volume_ratio20 >= 1.2:
        return "expansion"
    if volume_ratio20 <= 0.7:
        return "dry-up"
    return "normal"


def _multi_timeframe_alignment(rows: list[dict[str, Any]]) -> dict[str, str]:
    if not rows:
        return {
            "label": "No MTF data",
            "bias": "unknown",
            "summary": "No Day, 1 hour, or 15 min timeframe had enough candles for analysis.",
        }

    direction_score = sum(_timeframe_direction_points(row) for row in rows)
    bullish_count = sum(1 for row in rows if _timeframe_direction_points(row) > 0)
    bearish_count = sum(1 for row in rows if _timeframe_direction_points(row) < 0)
    volume_expansion_count = sum(1 for row in rows if row.get("volume_signal") in {"expansion", "strong expansion"})

    if bullish_count == len(rows) and len(rows) >= 2:
        label = "Bullish MTF alignment"
        bias = "bullish"
    elif bearish_count == len(rows) and len(rows) >= 2:
        label = "Bearish MTF alignment"
        bias = "bearish"
    elif direction_score >= 2:
        label = "Bullish higher-timeframe tilt"
        bias = "bullish"
    elif direction_score <= -2:
        label = "Bearish higher-timeframe tilt"
        bias = "bearish"
    else:
        label = "Mixed or neutral MTF"
        bias = "neutral"

    volume_note = (
        f"{volume_expansion_count} timeframe(s) show volume expansion."
        if volume_expansion_count
        else "No timeframe shows volume expansion."
    )
    return {
        "label": label,
        "bias": bias,
        "summary": f"{label}; {bullish_count} bullish, {bearish_count} bearish, {len(rows) - bullish_count - bearish_count} neutral/unknown. {volume_note}",
    }


def _timeframe_direction_points(row: dict[str, Any]) -> int:
    points = 0
    if row.get("technical_trend") == "bullish":
        points += 1
    elif row.get("technical_trend") == "bearish":
        points -= 1
    if row.get("structure_trend") == "uptrend":
        points += 1
    elif row.get("structure_trend") == "downtrend":
        points -= 1
    if row.get("timeframe") == "day":
        points *= 2
    return points


def _refresh_timeframes_for_analysis(selected_timeframe: str) -> list[str]:
    requested = [source_timeframe(selected_timeframe), "day", "60minute", "15minute"]
    output = []
    for timeframe in requested:
        if timeframe not in output:
            output.append(timeframe)
    return output


def _normalize_bulk_timeframes(values: list[str]) -> list[str]:
    requested = values or ["day", "60minute", "15minute"]
    order = ["day", "60minute", "15minute"]
    normalized = {source_timeframe(normalize_timeframe(value)) for value in requested if str(value).strip()}
    return [timeframe for timeframe in order if timeframe in normalized]


def _dedupe_targets(targets: list[tuple[str, str]]) -> list[tuple[str, str]]:
    output = []
    seen: set[tuple[str, str]] = set()
    for tradingsymbol, file_stem in targets:
        key = (tradingsymbol.upper(), file_stem)
        if key in seen:
            continue
        output.append(key)
        seen.add(key)
    return output


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _scan_option_zone(bucket: str, structure: dict[str, Any]) -> str:
    support = structure.get("support")
    resistance = structure.get("resistance")
    invalidation = structure.get("invalidation")
    if bucket == "bullish":
        anchor = invalidation or support
        return f"Sell PE below {anchor:.2f}" if anchor else "Sell PE below support"
    if bucket == "bearish":
        return f"Sell CE above {resistance:.2f}" if resistance else "Sell CE above resistance"
    if bucket == "neutral":
        parts = []
        if support:
            parts.append(f"PE below {support:.2f}")
        if resistance:
            parts.append(f"CE above {resistance:.2f}")
        return " / ".join(parts) if parts else "Outside range"
    return "-"


def _option_trade_guide(setup: dict[str, str], structure, option_chain) -> dict[str, Any]:
    bucket = setup.get("bucket")
    if bucket == "bullish":
        return _short_put_guide(structure, option_chain)
    if bucket == "bearish":
        return _short_call_guide(structure, option_chain)
    if bucket == "neutral":
        return _short_strangle_guide(structure, option_chain)
    return {
        "summary": "No options-selling setup from current score/structure.",
        "rows": [
            {
                "action": "Wait",
                "strike_zone": "-",
                "why": "Bias did not qualify for bullish, bearish, or neutral options-selling setup.",
                "risk_check": "Avoid forcing a trade.",
            }
        ],
    }


def _short_put_guide(structure, option_chain) -> dict[str, Any]:
    support = structure.support
    invalidation = structure.invalidation
    anchor = invalidation or support
    rows = []
    if option_chain:
        candidates = [
            row for row in option_chain.rows
            if row.option_type == "PE" and (anchor is None or row.strike <= anchor)
        ]
        candidates = sorted(candidates, key=_option_liquidity_score, reverse=True)[:3]
        rows = [
            {
                "action": "Sell put",
                "strike_zone": f"{candidate.strike:.2f} PE",
                "why": f"Below support/invalidation zone; OI {candidate.oi}, volume {candidate.volume}, IV {_fmt_percent(candidate.implied_volatility)}.",
                "risk_check": f"Exit/adjust if price closes below {anchor:.2f} or PE OI build-up turns adverse." if anchor else "Define stop before entry.",
            }
            for candidate in candidates
        ]
    if not rows:
        rows.append(
            {
                "action": "Sell put",
                "strike_zone": f"Below {anchor:.2f}" if anchor else "Below nearest support",
                "why": "Bullish setup. Prefer OTM PE below support/invalidation with high OI, volume, and acceptable bid-ask spread.",
                "risk_check": "Enable option chain to rank actual PE strikes.",
            }
        )
    return {"summary": "Bullish setup: prefer short PUT below support/invalidation.", "rows": rows}


def _short_call_guide(structure, option_chain) -> dict[str, Any]:
    resistance = structure.resistance
    rows = []
    if option_chain:
        candidates = [
            row for row in option_chain.rows
            if row.option_type == "CE" and (resistance is None or row.strike >= resistance)
        ]
        candidates = sorted(candidates, key=_option_liquidity_score, reverse=True)[:3]
        rows = [
            {
                "action": "Sell call",
                "strike_zone": f"{candidate.strike:.2f} CE",
                "why": f"Above resistance zone; OI {candidate.oi}, volume {candidate.volume}, IV {_fmt_percent(candidate.implied_volatility)}.",
                "risk_check": f"Exit/adjust if price sustains above {resistance:.2f}." if resistance else "Define stop before entry.",
            }
            for candidate in candidates
        ]
    if not rows:
        rows.append(
            {
                "action": "Sell call",
                "strike_zone": f"Above {resistance:.2f}" if resistance else "Above nearest resistance",
                "why": "Bearish setup. Prefer OTM CE above resistance with high OI, volume, and acceptable bid-ask spread.",
                "risk_check": "Enable option chain to rank actual CE strikes.",
            }
        )
    return {"summary": "Bearish setup: prefer short CALL above resistance.", "rows": rows}


def _short_strangle_guide(structure, option_chain) -> dict[str, Any]:
    support = structure.support
    resistance = structure.resistance
    return {
        "summary": "Neutral setup: prefer short strangle outside the range.",
        "rows": [
            {
                "action": "Sell put",
                "strike_zone": f"Below {support:.2f}" if support else "Below range support",
                "why": "Lower leg should sit below buyer-defended support.",
                "risk_check": "Avoid if range is too narrow versus premium and event risk.",
            },
            {
                "action": "Sell call",
                "strike_zone": f"Above {resistance:.2f}" if resistance else "Above range resistance",
                "why": "Upper leg should sit above seller-defended resistance.",
                "risk_check": "Avoid if IV is low or breakout risk is high.",
            },
        ],
    }


def _option_liquidity_score(row) -> float:
    spread_penalty = 0.0
    if row.bid_price and row.ask_price and row.last_price:
        spread_penalty = max(0.0, (row.ask_price - row.bid_price) / row.last_price) * 1000
    return row.oi + (row.volume * 2) - spread_penalty
