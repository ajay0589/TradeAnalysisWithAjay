from __future__ import annotations

import csv
import json
import shutil
import threading
import time
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
from datetime import timedelta
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from trading_analysis.analysis.entry_context import build_entry_context
from trading_analysis.analysis.fundamental import analyze_fundamentals
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
from trading_analysis.data_sources.nse_equity import (
    build_sector_map_from_csv_rows,
    build_sector_map_from_symbol_overrides,
)
from trading_analysis.data_sources.nse_fii_dii import fetch_fii_dii_activity, write_fii_dii_csv


DEFAULT_REFRESH_DAYS = {
    "month": 1460,
    "week": 730,
    "day": 365,
    "60minute": 90,
    "15minute": 45,
}

MULTI_TIMEFRAMES = ("month", "week", "day", "60minute", "15minute")

MULTI_TIMEFRAME_MIN_DAYS = {
    "month": 1460,
    "week": 730,
    "day": 365,
    "60minute": 90,
    "15minute": 45,
}

INDEX_DEFINITIONS = {
    "NIFTY": {
        "symbol": "NIFTY",
        "name": "Nifty 50 Index",
        "exchange": "NSE",
        "tradingsymbol": "NIFTY 50",
        "data_stem": "NIFTY_50",
        "option_underlying": "NIFTY",
        "spot_quote_key": "NSE:NIFTY 50",
        "aliases": ("NIFTY", "NIFTY 50", "NIFTY_50"),
    },
    "BANKNIFTY": {
        "symbol": "BANKNIFTY",
        "name": "Nifty Bank Index",
        "exchange": "NSE",
        "tradingsymbol": "NIFTY BANK",
        "data_stem": "NIFTY_BANK",
        "option_underlying": "BANKNIFTY",
        "spot_quote_key": "NSE:NIFTY BANK",
        "aliases": ("BANKNIFTY", "BANK NIFTY", "NIFTY BANK", "NIFTY_BANK"),
    },
    "SENSEX": {
        "symbol": "SENSEX",
        "name": "S&P BSE Sensex Index",
        "exchange": "BSE",
        "tradingsymbol": "SENSEX",
        "data_stem": "SENSEX",
        "option_underlying": "SENSEX",
        "spot_quote_key": "BSE:SENSEX",
        "aliases": ("SENSEX", "BSE SENSEX", "S&P BSE SENSEX"),
    },
}

INDEX_ALIASES = {
    " ".join(alias.upper().replace("_", " ").split()): symbol
    for symbol, definition in INDEX_DEFINITIONS.items()
    for alias in definition["aliases"]
}


class AnalysisService:
    def __init__(
        self,
        watchlist_path: str | Path = "config/watchlist.fno.json",
        daily_data_dir: str | Path = "data/raw/candles",
        hourly_data_dir: str | Path = "data/raw/candles/60minute",
        sector_map_path: str | Path = "config/sector_map.generated.json",
        nse_instruments_path: str | Path = "data/raw/zerodha/instruments_NSE.csv",
        bse_instruments_path: str | Path = "data/raw/zerodha/instruments_BSE.csv",
        nfo_instruments_path: str | Path = "data/raw/zerodha/instruments_NFO.csv",
        fii_dii_path: str | Path = "data/raw/nse/fii_dii.csv",
        option_chain_dir: str | Path = "data/raw/option_chain",
        reports_dir: str | Path = "reports",
        benchmark_file: str = "NIFTY_50.csv",
    ) -> None:
        self.watchlist_path = Path(watchlist_path)
        self.daily_data_dir = Path(daily_data_dir)
        self.hourly_data_dir = Path(hourly_data_dir)
        self.sector_map_path = Path(sector_map_path)
        self.nse_instruments_path = Path(nse_instruments_path)
        self.bse_instruments_path = Path(bse_instruments_path)
        self.nfo_instruments_path = Path(nfo_instruments_path)
        self.fii_dii_path = Path(fii_dii_path)
        self.option_chain_dir = Path(option_chain_dir)
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
        requested_timeframes = _normalize_bulk_requested_timeframes(timeframes)
        normalized_timeframes = _normalize_bulk_timeframes(timeframes)
        effective_days = _bulk_window_days(requested_timeframes, days)
        window = candle_window(from_date=from_date, to_date=to_date, days=None if from_date else effective_days)
        if window.from_time is None:
            window = type(window)(
                from_time=window.to_time - timedelta(days=effective_days),
                to_time=window.to_time,
                days=effective_days,
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
            "requested_timeframes": requested_timeframes,
            "source_timeframes": normalized_timeframes,
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
        payload = self._load_or_create_sector_map()
        if not payload:
            return {
                "exists": False,
                "path": str(self.sector_map_path),
                "mapped": 0,
                "unmapped": 0,
                "sectors": 0,
                "generated_on": None,
            }
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
        sector_map = self._merge_missing_sector_overrides(sector_map)
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
        option_underlying = self._option_underlying(symbol)
        contracts = option_contracts_for_symbol(load_instruments_csv(self.nfo_instruments_path), option_underlying)
        expiries = sorted({contract.expiry.isoformat() for contract in contracts})
        return {
            "symbol": symbol,
            "option_underlying": option_underlying,
            "expiries": expiries,
            "nearest": nearest_expiry(contracts).isoformat() if contracts else None,
        }

    def option_snapshots(self, symbol: str, expiry: str | None = None) -> dict[str, Any]:
        symbol = self.resolve_symbol(symbol)
        selected_expiry = self._selected_snapshot_expiry(symbol, expiry)
        snapshots = self._snapshot_rows(symbol, selected_expiry)
        return {
            "symbol": symbol,
            "expiry": selected_expiry.isoformat() if selected_expiry else None,
            "snapshots": snapshots,
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

    def start_option_chain_monitor(
        self,
        symbols: list[str],
        expiry: str | None = None,
        interval_minutes: int = 15,
        strikes_around: int = 10,
        all_strikes: bool = False,
        max_snapshots: int = 5,
        run_once: bool = False,
    ) -> dict[str, Any]:
        normalized_symbols = [self.resolve_symbol(symbol) for symbol in symbols if str(symbol).strip()]
        if not normalized_symbols:
            raise ValueError("Enter at least one stock/index symbol for option-chain monitoring.")
        interval_minutes = max(1, int(interval_minutes or 15))
        max_snapshots = max(1, int(max_snapshots or 5))
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "type": "option_chain_monitor",
            "status": "queued",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "started_at": None,
            "finished_at": None,
            "symbols": normalized_symbols,
            "expiry": expiry,
            "interval_minutes": interval_minutes,
            "strikes_around": strikes_around,
            "all_strikes": all_strikes,
            "max_snapshots": max_snapshots,
            "run_once": run_once,
            "completed": 0,
            "successes": 0,
            "failures": 0,
            "current": "",
            "results": [],
            "errors": [],
            "stop_requested": False,
            "next_run_at": None,
        }
        with self._jobs_lock:
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run_option_chain_monitor,
            args=(job_id,),
            daemon=True,
        )
        thread.start()
        return self.job_status(job_id)

    def stop_job(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            if job_id not in self._jobs:
                raise ValueError(f"Unknown job id: {job_id}")
            self._jobs[job_id]["stop_requested"] = True
            if self._jobs[job_id].get("status") in {"queued", "running", "sleeping"}:
                self._jobs[job_id]["status"] = "stopping"
        return self.job_status(job_id)

    def symbols(self) -> dict[str, Any]:
        watchlist = self._watchlist_symbols()
        names = self._symbol_names()
        index_rows = [self._symbol_row(symbol, INDEX_DEFINITIONS[symbol]["name"], "index") for symbol in INDEX_DEFINITIONS]
        stock_rows = [self._symbol_row(symbol, names.get(symbol, ""), "stock") for symbol in watchlist]
        rows = index_rows + stock_rows
        available = [row for row in rows if row["has_daily"]]
        return {
            "total": len(rows),
            "total_fno_symbols": len(watchlist),
            "total_indexes": len(index_rows),
            "available": len(available),
            "missing": len(rows) - len(available),
            "symbols": rows,
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
                    refresh_results.extend(self.refresh_candles(symbol, refresh_timeframe, _refresh_window_for_analysis(window, refresh_timeframe)))
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
        fundamentals = self._fundamental_context(symbol)

        if normalized_timeframe == "60minute":
            hourly = chart_candles
            hourly_source = chart_source
        else:
            hourly, hourly_source = self._load_optional_timeframe_with_summary(symbol, "60minute", window)
        hourly_technical = analyze_technical(hourly) if hourly and len(hourly) >= 20 else None
        hourly_structure = analyze_market_structure(hourly) if hourly and len(hourly) >= 10 else None

        relative_strength = self._relative_strength(symbol, chart_candles, normalized_timeframe, window)
        option_chain = None
        option_snapshot = None
        warnings: list[str] = []
        for refresh_error in refresh_errors:
            warnings.append(f"Zerodha candle refresh failed; using cached candles: {refresh_error}")
        if include_option_chain:
            try:
                option_chain, option_snapshot = self._option_chain(symbol, previous_snapshot, strikes_around, expiry, all_strikes)
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
        daily_context = chart_candles if normalized_timeframe == "day" else self._load_optional_timeframe(symbol, "day", window)
        intraday_context = self._load_optional_timeframe(symbol, "15minute", window)
        entry_context = build_entry_context(
            chart_candles=chart_candles,
            daily_candles=daily_context,
            intraday_candles=intraday_context,
            bucket=setup["bucket"],
        )
        option_trade_guide = _option_trade_guide(setup, chart_structure, option_chain)
        entry_trigger = _entry_trigger_panel(
            setup=setup,
            chart_technical=chart_technical,
            chart_structure=chart_structure,
            multi_timeframe=multi_timeframe,
            option_chain=option_chain,
            entry_context=entry_context,
        )
        analysis_summary = self._analysis_summary(
            symbol=symbol,
            timeframe=normalized_timeframe,
            chart_source=chart_source,
            hourly_source=hourly_source,
            multi_timeframe=multi_timeframe,
            relative_strength=relative_strength,
            include_option_chain=include_option_chain,
            option_chain=option_chain,
            option_snapshot=option_snapshot,
            entry_trigger=entry_trigger,
            entry_context=entry_context,
            fundamentals=fundamentals,
            refresh_requested=refresh,
            refresh_results=refresh_results,
            refresh_error="; ".join(refresh_errors) if refresh_errors else None,
            warnings=warnings + list(decision.warnings),
            window=window,
        )

        return {
            "symbol": symbol,
            "analysis_header": _analysis_header(symbol, normalized_timeframe, chart_candles, chart_technical, option_chain),
            "setup": setup,
            "decision": asdict(decision),
            "multi_timeframe": multi_timeframe,
            "entry_trigger": entry_trigger,
            "entry_context": entry_context,
            "option_trade_guide": option_trade_guide,
            "option_snapshot": option_snapshot,
            "analysis_summary": analysis_summary,
            "fundamentals": fundamentals,
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
            "structure_timeframes": _structure_timeframe_rows(multi_timeframe),
            "relative_strength": asdict(relative_strength),
            "option_chain": asdict(option_chain) if option_chain else None,
            "warnings": warnings,
        }

    def refresh_candles(self, symbol: str, timeframe: str, window) -> list[dict[str, Any]]:
        source = source_timeframe(timeframe)
        interval = fetch_interval(source)
        client = _zerodha_client()
        fetch_window = _window_with_default_from(window, timeframe)
        targets = [self._candle_target(symbol)]

        benchmark_symbol = self.benchmark_file.replace("_", " ").removesuffix(".csv")
        targets.append(("NSE", benchmark_symbol.upper(), Path(self.benchmark_file).stem))

        sector_config = sector_config_for_symbol(self._load_or_create_sector_map(), symbol)
        if sector_config:
            targets.append(("NSE", sector_config["index_symbol"].upper(), Path(sector_config["data_file"]).stem))

        results = []
        seen: set[tuple[str, str, str]] = set()
        for exchange, tradingsymbol, file_stem in targets:
            key = (exchange, tradingsymbol, file_stem)
            if key in seen:
                continue
            seen.add(key)
            token = resolve_instrument_token(self._instruments_for_exchange(exchange), exchange, tradingsymbol)
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
                    "exchange": exchange,
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
            for exchange, tradingsymbol, file_stem in targets:
                for timeframe in timeframes:
                    current = f"{exchange}:{tradingsymbol} {timeframe}"
                    self._update_job(job_id, current=current)
                    try:
                        instruments = self._instruments_for_exchange(exchange)
                        token = resolve_instrument_token(instruments, exchange, tradingsymbol)
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
                                "exchange": exchange,
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

    def _run_option_chain_monitor(self, job_id: str) -> None:
        self._update_job(job_id, status="running", started_at=datetime.now().isoformat(timespec="seconds"))
        while True:
            job = self.job_status(job_id)
            if job.get("stop_requested"):
                self._update_job(job_id, status="stopped", finished_at=datetime.now().isoformat(timespec="seconds"), current="")
                return

            for symbol in job["symbols"]:
                if self.job_status(job_id).get("stop_requested"):
                    self._update_job(job_id, status="stopped", finished_at=datetime.now().isoformat(timespec="seconds"), current="")
                    return
                current = f"{symbol} option chain"
                self._update_job(job_id, status="running", current=current)
                try:
                    analysis, snapshot = self._option_chain(
                        symbol=symbol,
                        previous_snapshot=None,
                        strikes_around=int(job["strikes_around"]),
                        expiry=job.get("expiry"),
                        all_strikes=bool(job["all_strikes"]),
                        max_snapshots=int(job["max_snapshots"]),
                    )
                    self._append_job_result(
                        job_id,
                        {
                            "symbol": symbol,
                            "expiry": analysis.expiry.isoformat(),
                            "contracts": analysis.contract_count,
                            "pcr_oi": analysis.pcr_oi,
                            "max_pain": analysis.max_pain,
                            "history_snapshot": snapshot.get("history_snapshot"),
                            "buildup_analysis": snapshot.get("buildup_analysis"),
                            "previous_snapshot_found": snapshot.get("previous_snapshot_found"),
                        },
                    )
                except Exception as exc:
                    self._append_job_error(job_id, f"{symbol}: {exc}")
                finally:
                    self._increment_job(job_id)

            if job.get("run_once"):
                self._update_job(job_id, status="completed", finished_at=datetime.now().isoformat(timespec="seconds"), current="", next_run_at=None)
                return

            next_run_at = datetime.now() + timedelta(minutes=int(job["interval_minutes"]))
            self._update_job(job_id, status="sleeping", current="", next_run_at=next_run_at.isoformat(timespec="seconds"))
            sleep_seconds = max(1, int(job["interval_minutes"]) * 60)
            for _ in range(sleep_seconds):
                if self.job_status(job_id).get("stop_requested"):
                    self._update_job(job_id, status="stopped", finished_at=datetime.now().isoformat(timespec="seconds"), current="")
                    return
                time.sleep(1)

    def _bulk_targets(self, symbols: list[str]) -> list[tuple[str, str, str]]:
        targets = [self._candle_target(symbol) for symbol in symbols]
        targets.extend(self._candle_target(symbol) for symbol in INDEX_DEFINITIONS)
        sector_map = self._load_or_create_sector_map()
        for symbol in symbols:
            sector_config = sector_config_for_symbol(sector_map, symbol)
            if sector_config:
                targets.append(("NSE", sector_config["index_symbol"].upper(), Path(sector_config["data_file"]).stem))
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
        index_symbol = _index_symbol_for(value)
        if index_symbol:
            return index_symbol

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
        include_option_chain: bool = False,
        option_chain_limit: int = 5,
        expiry: str | None = None,
        strikes_around: int = 10,
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
        option_chain_attempts = 0
        option_chain_successes = 0
        option_chain_errors = []
        if include_option_chain:
            option_chain_limit = max(0, option_chain_limit)
            for row in limited_rows[:option_chain_limit]:
                option_chain_attempts += 1
                try:
                    row["option_chain_context"] = self._scan_option_chain_context(
                        symbol=row["symbol"],
                        expiry=expiry,
                        strikes_around=strikes_around,
                    )
                    option_chain_successes += 1
                except Exception as exc:
                    row["option_chain_context"] = {
                        "status": "failed",
                        "summary": str(exc),
                    }
                    option_chain_errors.append({"symbol": row["symbol"], "error": str(exc)})
        available_symbols = self._available_count(normalized_timeframe)
        return {
            "type": scan_type,
            "strategy": _strategy_for_scan(scan_type),
            "timeframe": normalized_timeframe,
            "timeframe_label": timeframe_label(normalized_timeframe),
            "available_symbols": available_symbols,
            "total_fno_symbols": len(self._watchlist_symbols()),
            "matched_symbols": len(rows),
            "limit": limit,
            "results": limited_rows,
            "errors": (errors + option_chain_errors)[:20],
            "summary": {
                "candle_source": "local cached candle CSV files",
                "latest_candles_pulled": False,
                "option_chain_pulled": option_chain_successes > 0,
                "option_chain_requested": include_option_chain,
                "option_chain_attempts": option_chain_attempts,
                "option_chain_successes": option_chain_successes,
                "analyzed_symbols": available_symbols,
                "matched_symbols": len(rows),
                "shown_symbols": len(limited_rows),
                "error_count": len(errors) + len(option_chain_errors),
                "points": [
                    f"Scanned F&O symbols with {timeframe_label(normalized_timeframe)} candle files available.",
                    "Latest candle refresh is handled before the scan when the UI checkbox is enabled.",
                    (
                        f"Pulled option chain for {option_chain_successes}/{option_chain_attempts} top shown candidate(s)."
                        if include_option_chain
                        else "Did not pull option chain during scan; enable Option chain for top candidates to enrich scan rows."
                    ),
                    "Ranking used price trend, market structure, relative strength, support/resistance, and cached volume from candle CSVs.",
                ],
            },
        }

    def _watchlist_symbols(self) -> list[str]:
        if not self.watchlist_path.exists():
            return []
        return [item.symbol for item in load_watchlist(self.watchlist_path)]

    def _watchlist_items_by_symbol(self) -> dict[str, Any]:
        if not self.watchlist_path.exists():
            return {}
        return {item.symbol.upper(): item for item in load_watchlist(self.watchlist_path)}

    def _load_or_create_sector_map(self) -> dict[str, Any]:
        sector_map = load_sector_map(self.sector_map_path)
        if sector_map:
            merged = self._merge_missing_sector_overrides(sector_map)
            if merged != sector_map:
                self.sector_map_path.parent.mkdir(parents=True, exist_ok=True)
                self.sector_map_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
            return merged

        symbols = self._watchlist_symbols()
        if not symbols:
            return {}
        sector_map = build_sector_map_from_symbol_overrides(symbols, self._nse_instruments())
        self.sector_map_path.parent.mkdir(parents=True, exist_ok=True)
        self.sector_map_path.write_text(json.dumps(sector_map, indent=2), encoding="utf-8")
        return sector_map

    def _merge_missing_sector_overrides(self, sector_map: dict[str, Any]) -> dict[str, Any]:
        symbols = self._watchlist_symbols()
        if not symbols:
            return sector_map
        mapped = set(sector_map.get("symbols", {}))
        candidates = [symbol for symbol in symbols if symbol.upper() not in mapped]
        if not candidates:
            return sector_map

        fallback = build_sector_map_from_symbol_overrides(
            candidates,
            self._nse_instruments(),
            source="Missing-symbol fallback from Nifty Indices sectoral catalog + built-in F&O symbol overrides",
        )
        merged = dict(sector_map)
        merged["sectors"] = {**sector_map.get("sectors", {}), **fallback.get("sectors", {})}
        merged["symbols"] = {**sector_map.get("symbols", {}), **fallback.get("symbols", {})}
        merged_unmapped = {
            symbol: value
            for symbol, value in sector_map.get("unmapped", {}).items()
            if symbol not in fallback.get("symbols", {})
        }
        merged["unmapped"] = {**merged_unmapped, **fallback.get("unmapped", {})}
        if "sector_source_catalog" not in merged and fallback.get("sector_source_catalog"):
            merged["sector_source_catalog"] = fallback["sector_source_catalog"]
        merged["source"] = f"{sector_map.get('source', 'sector map')} + built-in missing-symbol fallback"
        return merged

    def _fundamental_context(self, symbol: str) -> dict[str, Any]:
        if _index_definition_for(symbol):
            return {
                "status": "not_applicable",
                "score": None,
                "reasons": ["Index-level fundamentals are not applicable."],
                "inputs": {},
                "source": "Index instrument",
            }

        item = self._watchlist_items_by_symbol().get(symbol.upper())
        if not item:
            return {
                "status": "missing",
                "score": None,
                "reasons": ["Symbol is not present in the watchlist fundamentals source."],
                "inputs": {},
                "source": str(self.watchlist_path),
            }

        inputs = {
            key: value
            for key, value in asdict(item.fundamentals).items()
            if value is not None
        }
        signal = analyze_fundamentals(item.fundamentals)
        return {
            "status": "analyzed" if inputs else "missing",
            "score": signal.score,
            "reasons": list(signal.reasons),
            "inputs": inputs,
            "source": str(self.watchlist_path),
        }

    def _symbol_row(self, symbol: str, name: str, instrument_group: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "name": name,
            "type": instrument_group,
            "has_daily": self._has_candles(symbol, "day"),
            "has_hourly": self._has_candles(symbol, "60minute"),
            "has_15minute": self._has_candles(symbol, "15minute"),
        }

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

    def _instruments_for_exchange(self, exchange: str) -> list[dict[str, str]]:
        exchange = exchange.upper()
        if exchange == "NSE":
            path = self.nse_instruments_path
        elif exchange == "BSE":
            path = self.bse_instruments_path
        else:
            raise ValueError(f"Unsupported candle exchange: {exchange}")
        if not path.exists():
            raise FileNotFoundError(
                f"{exchange} instrument cache not found: {path}. "
                f"Run: python -m trading_analysis.cli zerodha-instruments --exchange {exchange} --output {path}"
            )
        return load_instruments_csv(path)

    def _relative_strength(self, symbol: str, chart_candles, timeframe: str, window) -> Any:
        benchmark = self._load_optional_timeframe(Path(self.benchmark_file).stem, timeframe, window)
        sector_map = self._load_or_create_sector_map()
        sector_config = sector_config_for_symbol(sector_map, symbol)
        sector = None
        if sector_config:
            sector = self._load_optional_timeframe(Path(sector_config["data_file"]).stem, timeframe, window)
        return analyze_relative_strength(
            stock_candles=chart_candles,
            nifty_candles=benchmark,
            sector_candles=sector,
        )

    def _scan_option_chain_context(self, symbol: str, expiry: str | None, strikes_around: int) -> dict[str, Any]:
        analysis, snapshot = self._option_chain(
            symbol=symbol,
            previous_snapshot=None,
            strikes_around=strikes_around,
            expiry=expiry,
            all_strikes=False,
        )
        build_counts = Counter(row.buildup for row in analysis.rows)
        return {
            "status": "analyzed",
            "expiry": analysis.expiry.isoformat(),
            "spot_price": analysis.spot_price,
            "pcr_oi": analysis.pcr_oi,
            "max_pain": analysis.max_pain,
            "atm_iv": analysis.atm_iv,
            "atm_iv_change": analysis.atm_iv_change,
            "total_volume": analysis.total_volume,
            "total_oi_change_percent": analysis.total_oi_change_percent,
            "highest_call_oi_strike": analysis.highest_call_oi_strike,
            "highest_put_oi_strike": analysis.highest_put_oi_strike,
            "previous_snapshot_found": snapshot.get("previous_snapshot_found"),
            "history_snapshot": snapshot.get("history_snapshot"),
            "buildup_analysis": snapshot.get("buildup_analysis"),
            "buildup_counts": dict(build_counts),
            "summary": (
                f"PCR {_fmt_decimal(analysis.pcr_oi)}, max pain {_fmt_decimal(analysis.max_pain)}, "
                f"ATM IV {_fmt_decimal(analysis.atm_iv)}, OI% {_fmt_decimal(analysis.total_oi_change_percent)}."
            ),
        }

    def _option_chain(
        self,
        symbol: str,
        previous_snapshot: str | None,
        strikes_around: int,
        expiry: str | None = None,
        all_strikes: bool = False,
        max_snapshots: int = 5,
    ) -> Any:
        client = _zerodha_client()
        instruments = load_instruments_csv(self.nfo_instruments_path)
        option_underlying = self._option_underlying(symbol)
        contracts = option_contracts_for_symbol(instruments, option_underlying)
        if not contracts:
            raise ValueError(f"No NFO option contracts found for {option_underlying}")

        selected_expiry = date.fromisoformat(expiry) if expiry else nearest_expiry(contracts)
        contracts = option_contracts_for_symbol(instruments, option_underlying, expiry=selected_expiry)
        spot_key = self._spot_quote_key(symbol)
        spot_quote = client.quotes([spot_key]).get(spot_key, {})
        spot_price = _optional_float(spot_quote.get("last_price"))
        selected = contracts if all_strikes else select_strikes_around_spot(contracts, spot_price, strikes_around)
        quotes = client.quotes([contract.kite_key for contract in selected])
        default_snapshot = self._latest_snapshot_path(symbol, selected_expiry)
        previous_path = Path(previous_snapshot) if previous_snapshot else default_snapshot
        previous_exists = previous_path.exists()
        previous = load_option_chain_snapshot(previous_path) if previous_exists else {}
        analysis = analyze_option_chain(symbol, selected_expiry, selected, quotes, spot_price, previous)
        archived_previous = self._archive_latest_snapshot(symbol, selected_expiry)
        history_snapshot = self._history_snapshot_path(symbol, selected_expiry)
        write_option_chain_snapshot(history_snapshot, analysis)
        write_option_chain_snapshot(default_snapshot, analysis)
        buildup_analysis = self._write_option_buildup_analysis(analysis, history_snapshot, previous_exists)
        self._prune_option_chain_history(symbol, selected_expiry, max_snapshots)
        return analysis, {
            "symbol": symbol,
            "expiry": selected_expiry.isoformat(),
            "previous_snapshot": str(previous_path),
            "previous_snapshot_found": previous_exists,
            "latest_snapshot": str(default_snapshot),
            "history_snapshot": str(history_snapshot),
            "archived_previous_latest": str(archived_previous) if archived_previous else None,
            "buildup_analysis": str(buildup_analysis),
            "max_history_snapshots": max_snapshots,
        }

    def _has_candles(self, symbol: str, timeframe: str) -> bool:
        return candle_path(self.daily_data_dir, timeframe, self._data_stem(symbol)).exists()

    def _available_count(self, timeframe: str) -> int:
        return sum(1 for symbol in self._watchlist_symbols() if self._has_candles(symbol, timeframe))

    def _data_stem(self, symbol_or_stem: str) -> str:
        index = _index_definition_for(symbol_or_stem)
        return index["data_stem"] if index else symbol_or_stem

    def _candle_target(self, symbol: str) -> tuple[str, str, str]:
        index = _index_definition_for(symbol)
        if index:
            return (index["exchange"], index["tradingsymbol"], index["data_stem"])
        return ("NSE", symbol.upper(), safe_symbol_filename(symbol))

    def _option_underlying(self, symbol: str) -> str:
        index = _index_definition_for(symbol)
        return index["option_underlying"] if index else symbol.upper()

    def _spot_quote_key(self, symbol: str) -> str:
        index = _index_definition_for(symbol)
        return index["spot_quote_key"] if index else f"NSE:{symbol.upper()}"

    def _selected_snapshot_expiry(self, symbol: str, expiry: str | None):
        if expiry:
            return date.fromisoformat(expiry)
        try:
            option_underlying = self._option_underlying(symbol)
            contracts = option_contracts_for_symbol(load_instruments_csv(self.nfo_instruments_path), option_underlying)
            return nearest_expiry(contracts) if contracts else None
        except Exception:
            return None

    def _latest_snapshot_path(self, symbol: str, expiry: date) -> Path:
        return self.option_chain_dir / f"{symbol}_{expiry.isoformat()}.csv"

    def _history_snapshot_dir(self) -> Path:
        return self.option_chain_dir / "history"

    def _buildup_analysis_dir(self) -> Path:
        return self.option_chain_dir / "buildup"

    def _history_snapshot_path(self, symbol: str, expiry: date, when: datetime | None = None) -> Path:
        when = when or datetime.now()
        base = self._history_snapshot_dir() / f"{symbol}_{expiry.isoformat()}_{when.strftime('%Y%m%d_%H%M%S')}.csv"
        if not base.exists():
            return base
        for suffix in range(1, 100):
            candidate = self._history_snapshot_dir() / f"{symbol}_{expiry.isoformat()}_{when.strftime('%Y%m%d_%H%M%S')}_{suffix}.csv"
            if not candidate.exists():
                return candidate
        return self._history_snapshot_dir() / f"{symbol}_{expiry.isoformat()}_{when.strftime('%Y%m%d_%H%M%S_%f')}.csv"

    def _archive_latest_snapshot(self, symbol: str, expiry: date) -> Path | None:
        latest = self._latest_snapshot_path(symbol, expiry)
        if not latest.exists():
            return None
        snapshot_time = _snapshot_time_from_file(latest)
        archive_time = _parse_snapshot_time(snapshot_time) or datetime.fromtimestamp(latest.stat().st_mtime)
        archive = self._history_snapshot_path(symbol, expiry, archive_time)
        self._history_snapshot_dir().mkdir(parents=True, exist_ok=True)
        if archive.exists():
            return archive
        shutil.copy2(latest, archive)
        return archive

    def _write_option_buildup_analysis(self, analysis, snapshot_path: Path, previous_found: bool) -> Path:
        counts = Counter(row.buildup for row in analysis.rows)
        by_side = {
            option_type: Counter(row.buildup for row in analysis.rows if row.option_type == option_type)
            for option_type in ("CE", "PE")
        }
        payload = {
            "snapshot_time": datetime.now().isoformat(timespec="seconds"),
            "symbol": analysis.symbol,
            "expiry": analysis.expiry.isoformat(),
            "snapshot": str(snapshot_path),
            "previous_snapshot_found": previous_found,
            "pcr_oi": analysis.pcr_oi,
            "max_pain": analysis.max_pain,
            "atm_iv": analysis.atm_iv,
            "total_volume": analysis.total_volume,
            "total_oi_change": analysis.total_oi_change,
            "total_oi_change_percent": analysis.total_oi_change_percent,
            "buildup_counts": dict(counts),
            "call_buildup_counts": dict(by_side["CE"]),
            "put_buildup_counts": dict(by_side["PE"]),
            "top_oi_rows": [
                {
                    "tradingsymbol": row.tradingsymbol,
                    "strike": row.strike,
                    "option_type": row.option_type,
                    "oi": row.oi,
                    "oi_change": row.oi_change,
                    "volume": row.volume,
                    "buildup": row.buildup,
                }
                for row in sorted(analysis.rows, key=lambda item: item.oi, reverse=True)[:10]
            ],
        }
        output = self._buildup_analysis_dir() / f"{analysis.symbol}_{analysis.expiry.isoformat()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return output

    def _prune_option_chain_history(self, symbol: str, expiry: date, max_snapshots: int) -> None:
        max_snapshots = max(1, max_snapshots)
        self._prune_matching_files(
            self._history_snapshot_dir().glob(f"{symbol}_{expiry.isoformat()}_*.csv"),
            max_snapshots,
        )
        self._prune_matching_files(
            self._buildup_analysis_dir().glob(f"{symbol}_{expiry.isoformat()}_*.json"),
            max_snapshots,
        )

    def _prune_matching_files(self, paths, max_files: int) -> None:
        sorted_paths = sorted(
            [path for path in paths if path.exists()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in sorted_paths[max_files:]:
            path.unlink()

    def _snapshot_rows(self, symbol: str, expiry: date | None) -> list[dict[str, Any]]:
        paths: list[tuple[Path, str]] = []
        if expiry:
            latest = self._latest_snapshot_path(symbol, expiry)
            if latest.exists():
                paths.append((latest, "latest"))
            history_pattern = f"{symbol}_{expiry.isoformat()}_*.csv"
        else:
            history_pattern = f"{symbol}_*.csv"

        history_dir = self._history_snapshot_dir()
        if history_dir.exists():
            paths.extend((path, "history") for path in history_dir.glob(history_pattern))

        rows = [_snapshot_row(path, kind) for path, kind in paths]
        rows = sorted(rows, key=lambda row: row["modified_at"] or "", reverse=True)
        deduped = []
        seen: set[str] = set()
        for row in rows:
            if row["path"] in seen:
                continue
            deduped.append(row)
            seen.add(row["path"])
        return deduped

    def _load_timeframe(self, symbol_or_stem: str, timeframe: str, window):
        candles, _summary = self._load_timeframe_with_summary(symbol_or_stem, timeframe, window)
        return candles

    def _load_optional_timeframe(self, symbol_or_stem: str, timeframe: str, window):
        try:
            return self._load_timeframe(symbol_or_stem, timeframe, window)
        except FileNotFoundError:
            return None

    def _load_timeframe_with_summary(self, symbol_or_stem: str, timeframe: str, window):
        data_stem = self._data_stem(symbol_or_stem)
        path = candle_path(self.daily_data_dir, timeframe, data_stem)
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
            data_stem = self._data_stem(symbol_or_stem)
            return None, _missing_candle_source_summary(symbol_or_stem, timeframe, candle_path(self.daily_data_dir, timeframe, data_stem))

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
        option_snapshot: dict[str, Any] | None,
        entry_trigger: dict[str, Any],
        entry_context: dict[str, Any],
        fundamentals: dict[str, Any],
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

        rows.append(
            _coverage_row(
                "Entry trigger",
                "analyzed",
                f"{entry_trigger['status']}: {entry_trigger['summary']}",
                "MTF direction, 15-minute trigger, support/invalidation distance, option-chain build-up, OI/PCR/max pain, and volume",
            )
        )
        rows.append(
            _coverage_row(
                "Entry context",
                "analyzed",
                entry_context["summary"],
                "Fibonacci, VWAP, EMA20/EMA50, previous day high/low, opening range, and volume confirmation",
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
                "Monthly, Weekly, Daily, 1 hour, and 15 min candle CSVs",
            )
        )

        rows.extend(self._relative_strength_rows(symbol, timeframe, window, relative_strength))

        if include_option_chain and option_chain:
            snapshot_detail = ""
            if option_snapshot:
                if option_snapshot.get("previous_snapshot_found"):
                    snapshot_detail = f" Compared with {option_snapshot.get('previous_snapshot')}."
                else:
                    snapshot_detail = f" Previous snapshot was not found at {option_snapshot.get('previous_snapshot')}."
            rows.append(
                _coverage_row(
                    "Option chain",
                    "analyzed",
                    (
                        f"Nearest expiry {option_chain.expiry}; {option_chain.contract_count} contracts; "
                        f"PCR {option_chain.pcr_oi}; max pain {option_chain.max_pain}."
                        f"{snapshot_detail}"
                    ),
                    option_snapshot.get("history_snapshot") if option_snapshot else f"{self.nfo_instruments_path}",
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

        rows.append(
            _coverage_row(
                "Fundamentals",
                fundamentals["status"],
                _fundamental_detail(fundamentals),
                fundamentals["source"],
            )
        )
        rows.append(
            _coverage_row(
                "FII/DII flow",
                "not_analyzed",
                "Market-wide FII/DII data is not included in this stock-specific UI score yet.",
                "data/raw/nse/fii_dii.csv",
            )
        )

        index = _index_definition_for(symbol)
        return {
            "symbol": symbol,
            "instrument": {
                "symbol": symbol,
                "name": index["name"] if index else symbol,
                "type": "index" if index else "stock",
            },
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

        sector_map = self._load_or_create_sector_map()
        sector_config = sector_config_for_symbol(sector_map, symbol)
        if not sector_config:
            unmapped = sector_map.get("unmapped", {}).get(symbol.upper(), {}) if sector_map else {}
            reason = unmapped.get("reason") or "No sector mapping found for this stock."
            return rows + [
                _coverage_row(
                    "Relative strength vs sector",
                    "not_applicable" if unmapped.get("sector") == "NA" else "missing",
                    f"Sector mapping is NA. {reason}" if unmapped.get("sector") == "NA" else reason,
                    str(self.sector_map_path),
                ),
                _coverage_row(
                    "Sector vs Nifty",
                    "not_applicable" if unmapped.get("sector") == "NA" else "missing",
                    f"Sector mapping is NA. {reason}" if unmapped.get("sector") == "NA" else reason,
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
        for timeframe in MULTI_TIMEFRAMES:
            rows.append(self._multi_timeframe_row(symbol, timeframe, window))
        analyzed_rows = [row for row in rows if row["status"] == "analyzed"]
        alignment = _multi_timeframe_alignment(analyzed_rows)
        lookback_summary = ", ".join(
            f"{timeframe_label(timeframe)} {MULTI_TIMEFRAME_MIN_DAYS[timeframe]}d"
            for timeframe in MULTI_TIMEFRAMES
        )
        return {
            "symbol": symbol,
            "alignment": alignment["label"],
            "bias": alignment["bias"],
            "summary": f"{alignment['summary']} Minimum lookbacks: {lookback_summary}.",
            "analyzed_count": len(analyzed_rows),
            "rows": rows,
        }

    def _multi_timeframe_row(self, symbol: str, timeframe: str, window) -> dict[str, Any]:
        row_window = _multi_timeframe_window(window, timeframe)
        path = candle_path(self.daily_data_dir, timeframe, self._data_stem(symbol))
        try:
            candles, source = self._load_timeframe_with_summary(symbol, timeframe, row_window)
        except FileNotFoundError:
            return {
                "timeframe": timeframe,
                "label": timeframe_label(timeframe),
                "status": "missing",
                "message": "Candle CSV not found.",
                "path": str(path),
                "lookback_days": row_window.days,
                "from": row_window.from_time,
                "to": row_window.to_time,
            }
        if len(candles) < 20:
            return {
                "timeframe": timeframe,
                "label": timeframe_label(timeframe),
                "status": "insufficient",
                "message": f"Only {len(candles)} candles available; at least 20 needed.",
                "path": str(path),
                "candle_count": len(candles),
                "lookback_days": row_window.days,
                "from": source.get("from"),
                "to": source.get("to"),
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
            "lookback_days": row_window.days,
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


def _analysis_header(symbol: str, timeframe: str, chart_candles, chart_technical, option_chain) -> dict[str, Any]:
    index = _index_definition_for(symbol)
    live_price = option_chain.spot_price if option_chain and option_chain.spot_price is not None else None
    candle_time = chart_candles[-1].timestamp if chart_candles else None
    return {
        "symbol": symbol.upper(),
        "name": index["name"] if index else symbol.upper(),
        "instrument_type": "index" if index else "stock",
        "timeframe": timeframe,
        "timeframe_label": timeframe_label(timeframe),
        "analyzed_at": datetime.now().isoformat(timespec="seconds"),
        "latest_price": live_price if live_price is not None else chart_technical.close,
        "latest_price_time": datetime.now().isoformat(timespec="seconds") if live_price is not None else candle_time,
        "latest_price_source": "Zerodha spot quote" if live_price is not None else "latest analyzed candle close",
        "candle_count": len(chart_candles),
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


def _structure_timeframe_rows(multi_timeframe: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in multi_timeframe.get("rows", []):
        rows.append(
            {
                "timeframe": row.get("timeframe"),
                "label": row.get("label"),
                "status": row.get("status"),
                "message": row.get("message"),
                "candle_count": row.get("candle_count"),
                "close": row.get("close"),
                "technical_trend": row.get("technical_trend"),
                "structure_trend": row.get("structure_trend"),
                "support": row.get("support"),
                "resistance": row.get("resistance"),
                "invalidation": row.get("invalidation"),
                "path": row.get("path"),
            }
        )
    return rows


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


def _multi_timeframe_window(window, timeframe: str):
    normalized = normalize_timeframe(timeframe)
    minimum_days = MULTI_TIMEFRAME_MIN_DAYS[normalized]
    to_time = window.to_time or datetime.now()
    if window.days is not None:
        days = max(window.days, minimum_days)
        return type(window)(from_time=to_time - timedelta(days=days), to_time=to_time, days=days)
    if window.from_time is not None:
        return window
    return type(window)(from_time=to_time - timedelta(days=minimum_days), to_time=to_time, days=minimum_days)


def _coverage_row(name: str, status: str, detail: str, source: str) -> dict[str, str]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "source": source,
    }


def _fundamental_detail(fundamentals: dict[str, Any]) -> str:
    status = fundamentals.get("status")
    reasons = "; ".join(fundamentals.get("reasons") or [])
    if status == "not_applicable":
        return reasons or "Fundamentals are not applicable."
    if status == "missing":
        return (
            "No stock fundamentals were supplied in the watchlist. "
            "Add roe_percent, debt_to_equity, sales_growth_yoy_percent, "
            "profit_growth_yoy_percent, and pledged_percent to enable scoring."
        )
    score = fundamentals.get("score")
    inputs = fundamentals.get("inputs") or {}
    input_summary = ", ".join(f"{key}={value}" for key, value in inputs.items()) or "no inputs"
    return f"Fundamental score {score}; {reasons}. Inputs: {input_summary}."


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


def _refresh_window_for_analysis(window, timeframe: str):
    normalized = normalize_timeframe(timeframe)
    if normalized == "day":
        return _multi_timeframe_window(window, "month")
    return _multi_timeframe_window(window, normalized)


def _normalize_bulk_requested_timeframes(values: list[str]) -> list[str]:
    requested = values or ["day", "60minute", "15minute"]
    order = ["month", "week", "day", "60minute", "15minute"]
    normalized = {normalize_timeframe(value) for value in requested if str(value).strip()}
    return [timeframe for timeframe in order if timeframe in normalized]


def _normalize_bulk_timeframes(values: list[str]) -> list[str]:
    order = ["day", "60minute", "15minute"]
    normalized = {source_timeframe(timeframe) for timeframe in _normalize_bulk_requested_timeframes(values)}
    return [timeframe for timeframe in order if timeframe in normalized]


def _bulk_window_days(requested_timeframes: list[str], days: int | None) -> int:
    base_days = days or 90
    derived_minimum = max(
        (MULTI_TIMEFRAME_MIN_DAYS[timeframe] for timeframe in requested_timeframes if timeframe in {"month", "week"}),
        default=0,
    )
    return max(base_days, derived_minimum)


def _dedupe_targets(targets: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    output = []
    seen: set[tuple[str, str, str]] = set()
    for exchange, tradingsymbol, file_stem in targets:
        key = (exchange.upper(), tradingsymbol.upper(), file_stem)
        if key in seen:
            continue
        output.append(key)
        seen.add(key)
    return output


def _index_lookup_key(value: str) -> str:
    return " ".join(value.upper().replace("_", " ").split())


def _index_symbol_for(value: str) -> str | None:
    return INDEX_ALIASES.get(_index_lookup_key(value))


def _index_definition_for(value: str) -> dict[str, Any] | None:
    symbol = _index_symbol_for(value)
    return INDEX_DEFINITIONS.get(symbol) if symbol else None


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _snapshot_time_from_file(path: Path) -> str | None:
    try:
        rows = _read_csv_rows(path)
    except Exception:
        return None
    for row in rows:
        value = row.get("snapshot_time")
        if value:
            return value
    return None


def _parse_snapshot_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _snapshot_row(path: Path, kind: str) -> dict[str, Any]:
    stat = path.stat()
    snapshot_time = _snapshot_time_from_file(path)
    modified_at = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    display_time = snapshot_time or modified_at
    return {
        "kind": kind,
        "path": str(path),
        "name": path.name,
        "snapshot_time": snapshot_time,
        "modified_at": modified_at,
        "label": f"{display_time} ({kind})",
        "size": stat.st_size,
    }


ENTRY_ALLOWED = "Entry allowed"
ENTRY_WAIT = "Wait"
ENTRY_AVOID = "Avoid"
ENTRY_EXIT = "Exit/Adjust"


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


def _entry_trigger_panel(
    setup: dict[str, str],
    chart_technical,
    chart_structure,
    multi_timeframe: dict[str, Any],
    option_chain,
    entry_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bucket = setup.get("bucket")
    if bucket not in {"bullish", "bearish", "neutral"}:
        return {
            "status": ENTRY_AVOID,
            "status_key": _entry_status_key(ENTRY_AVOID),
            "summary": "No qualified options-selling setup yet.",
            "rows": [
                _entry_factor(
                    "Setup quality",
                    ENTRY_AVOID,
                    "Current score/structure did not qualify for bullish, bearish, or neutral options selling.",
                )
            ],
            "candidates": [],
        }

    spot = option_chain.spot_price if option_chain and option_chain.spot_price else chart_technical.close
    candidates = _entry_candidates(bucket, chart_structure, option_chain)
    common_rows = [
        _entry_mtf_factor(bucket, multi_timeframe),
        _entry_intraday_factor(bucket, multi_timeframe),
        _entry_context_factor(entry_context),
        _entry_distance_factor(bucket, chart_structure, spot),
        _entry_option_context_factor(bucket, option_chain),
        _entry_volume_factor(multi_timeframe),
    ]

    candidate_rows = [
        _entry_candidate_row(bucket, candidate, chart_structure, spot, common_rows, option_chain)
        for candidate in candidates
    ]
    if option_chain and not candidate_rows:
        candidate_rows.append(
            {
                "action": _entry_action(bucket),
                "strike": None,
                "option_type": "-",
                "status": ENTRY_WAIT,
                "status_key": _entry_status_key(ENTRY_WAIT),
                "score": 0,
                "entry_trigger": "No option-chain strike matched the support/resistance rule.",
                "risk_trigger": "Wait for a strike outside the invalidation zone with usable OI and volume.",
                "reasons": [],
                "blockers": ["No qualifying strike"],
            }
        )

    panel_status = _panel_entry_status(common_rows, candidate_rows)
    return {
        "status": panel_status,
        "status_key": _entry_status_key(panel_status),
        "summary": _entry_summary(panel_status, bucket, candidate_rows),
        "rows": common_rows,
        "candidates": candidate_rows,
    }


def _entry_candidates(bucket: str, structure, option_chain) -> list:
    if not option_chain:
        return []
    if bucket == "bullish":
        anchor = structure.invalidation or structure.support
        candidates = [
            row for row in option_chain.rows
            if row.option_type == "PE" and (anchor is None or row.strike <= anchor)
        ]
        return sorted(candidates, key=_option_liquidity_score, reverse=True)[:3]
    if bucket == "bearish":
        resistance = structure.resistance
        candidates = [
            row for row in option_chain.rows
            if row.option_type == "CE" and (resistance is None or row.strike >= resistance)
        ]
        return sorted(candidates, key=_option_liquidity_score, reverse=True)[:3]

    candidates = []
    if structure.support is not None:
        put_rows = [
            row for row in option_chain.rows
            if row.option_type == "PE" and row.strike <= structure.support
        ]
        candidates.extend(sorted(put_rows, key=_option_liquidity_score, reverse=True)[:1])
    if structure.resistance is not None:
        call_rows = [
            row for row in option_chain.rows
            if row.option_type == "CE" and row.strike >= structure.resistance
        ]
        candidates.extend(sorted(call_rows, key=_option_liquidity_score, reverse=True)[:1])
    return candidates


def _entry_mtf_factor(bucket: str, multi_timeframe: dict[str, Any]) -> dict[str, str]:
    bias = multi_timeframe.get("bias") or "unknown"
    expected = {"bullish": "bullish", "bearish": "bearish", "neutral": "neutral"}[bucket]
    if bias == expected:
        return _entry_factor("MTF direction", ENTRY_ALLOWED, f"Multi-timeframe bias is {bias}.")
    if bucket == "neutral" and bias in {"neutral", "unknown"}:
        return _entry_factor("MTF direction", ENTRY_ALLOWED, f"Multi-timeframe bias is {bias}.")
    if bias in {"neutral", "unknown"}:
        return _entry_factor("MTF direction", ENTRY_WAIT, f"Multi-timeframe bias is {bias}; wait for alignment.")
    return _entry_factor("MTF direction", ENTRY_AVOID, f"Multi-timeframe bias is {bias}, opposite of the {expected} setup.")


def _entry_intraday_factor(bucket: str, multi_timeframe: dict[str, Any]) -> dict[str, str]:
    row = _mtf_row(multi_timeframe, "15minute")
    if not row or row.get("status") != "analyzed":
        return _entry_factor("15-min price trigger", ENTRY_WAIT, "15-minute candles are missing or insufficient.")
    technical = row.get("technical_trend")
    structure = row.get("structure_trend")
    close = row.get("close")
    support = row.get("support")
    resistance = row.get("resistance")
    invalidation = row.get("invalidation")

    if bucket == "bullish":
        floor = invalidation or support
        if floor is not None and close is not None and close <= floor:
            return _entry_factor("15-min price trigger", ENTRY_EXIT, f"15-min close {close:.2f} is below trigger floor {floor:.2f}.")
        if technical == "bullish" and structure in {"uptrend", "range"}:
            return _entry_factor("15-min price trigger", ENTRY_ALLOWED, "15-min trend is bullish and price is holding above support.")
        if technical == "bearish" or structure == "downtrend":
            return _entry_factor("15-min price trigger", ENTRY_AVOID, "15-min structure is bearish; do not sell puts into downside momentum.")
        return _entry_factor("15-min price trigger", ENTRY_WAIT, "Wait for a bullish 15-min close/retest before selling puts.")

    if bucket == "bearish":
        ceiling = invalidation or resistance
        if ceiling is not None and close is not None and close >= ceiling:
            return _entry_factor("15-min price trigger", ENTRY_EXIT, f"15-min close {close:.2f} is above trigger ceiling {ceiling:.2f}.")
        if technical == "bearish" and structure in {"downtrend", "range"}:
            return _entry_factor("15-min price trigger", ENTRY_ALLOWED, "15-min trend is bearish and price is holding below resistance.")
        if technical == "bullish" or structure == "uptrend":
            return _entry_factor("15-min price trigger", ENTRY_AVOID, "15-min structure is bullish; do not sell calls into upside momentum.")
        return _entry_factor("15-min price trigger", ENTRY_WAIT, "Wait for a bearish 15-min close/rejection before selling calls.")

    if structure == "range" and support is not None and resistance is not None and close is not None and support < close < resistance:
        return _entry_factor("15-min price trigger", ENTRY_ALLOWED, "15-min price is inside the range.")
    if close is not None and ((resistance is not None and close >= resistance) or (support is not None and close <= support)):
        return _entry_factor("15-min price trigger", ENTRY_EXIT, "15-min price is breaking out of the range.")
    return _entry_factor("15-min price trigger", ENTRY_WAIT, "Wait for 15-min range confirmation before selling both sides.")


def _entry_context_factor(entry_context: dict[str, Any] | None) -> dict[str, str]:
    if not entry_context:
        return _entry_factor("Pullback/level context", ENTRY_ALLOWED, "Entry context was not supplied by this caller.")
    status = entry_context.get("status")
    summary = entry_context.get("summary") or "Entry context is not available."
    if status == "supportive":
        return _entry_factor("Pullback/level context", ENTRY_ALLOWED, summary)
    return _entry_factor("Pullback/level context", ENTRY_WAIT, summary)


def _entry_distance_factor(bucket: str, structure, spot: float | None) -> dict[str, str]:
    if spot is None:
        return _entry_factor("Support/invalidation distance", ENTRY_WAIT, "Spot price is not available.")
    threshold_percent = 0.25
    if bucket == "bullish":
        anchor = structure.invalidation or structure.support
        if anchor is None:
            return _entry_factor("Support/invalidation distance", ENTRY_WAIT, "Support/invalidation is not available.")
        distance = ((spot - anchor) / spot) * 100
        if spot <= anchor:
            return _entry_factor("Support/invalidation distance", ENTRY_EXIT, f"Spot {spot:.2f} is below invalidation/support {anchor:.2f}.")
        if distance < threshold_percent:
            return _entry_factor("Support/invalidation distance", ENTRY_WAIT, f"Only {distance:.2f}% above invalidation/support {anchor:.2f}; too close for a fresh short put.")
        return _entry_factor("Support/invalidation distance", ENTRY_ALLOWED, f"Spot is {distance:.2f}% above invalidation/support {anchor:.2f}.")

    if bucket == "bearish":
        anchor = structure.invalidation or structure.resistance
        if anchor is None:
            return _entry_factor("Support/invalidation distance", ENTRY_WAIT, "Resistance/invalidation is not available.")
        distance = ((anchor - spot) / spot) * 100
        if spot >= anchor:
            return _entry_factor("Support/invalidation distance", ENTRY_EXIT, f"Spot {spot:.2f} is above invalidation/resistance {anchor:.2f}.")
        if distance < threshold_percent:
            return _entry_factor("Support/invalidation distance", ENTRY_WAIT, f"Only {distance:.2f}% below invalidation/resistance {anchor:.2f}; too close for a fresh short call.")
        return _entry_factor("Support/invalidation distance", ENTRY_ALLOWED, f"Spot is {distance:.2f}% below invalidation/resistance {anchor:.2f}.")

    support = structure.support
    resistance = structure.resistance
    if support is None or resistance is None:
        return _entry_factor("Support/invalidation distance", ENTRY_WAIT, "Range support/resistance is not available.")
    lower_distance = ((spot - support) / spot) * 100
    upper_distance = ((resistance - spot) / spot) * 100
    if spot <= support or spot >= resistance:
        return _entry_factor("Support/invalidation distance", ENTRY_EXIT, f"Spot {spot:.2f} is outside the {support:.2f}-{resistance:.2f} range.")
    if min(lower_distance, upper_distance) < threshold_percent:
        return _entry_factor("Support/invalidation distance", ENTRY_WAIT, f"Spot is too close to a range edge: {lower_distance:.2f}% lower room, {upper_distance:.2f}% upper room.")
    return _entry_factor("Support/invalidation distance", ENTRY_ALLOWED, f"Spot has room inside range: {lower_distance:.2f}% lower, {upper_distance:.2f}% upper.")


def _entry_option_context_factor(bucket: str, option_chain) -> dict[str, str]:
    if not option_chain:
        return _entry_factor("OI/PCR/max pain", ENTRY_WAIT, "Option chain was not loaded; enable Option chain before entry.")
    pcr = option_chain.pcr_oi
    spot = option_chain.spot_price
    max_pain = option_chain.max_pain
    pieces = [
        f"PCR {_fmt_decimal(pcr)}",
        f"max pain {_fmt_decimal(max_pain)}",
        f"highest PE OI {_fmt_decimal(option_chain.highest_put_oi_strike)}",
        f"highest CE OI {_fmt_decimal(option_chain.highest_call_oi_strike)}",
    ]
    detail = "; ".join(pieces) + "."
    if pcr is None:
        return _entry_factor("OI/PCR/max pain", ENTRY_WAIT, f"{detail} PCR is unavailable.")
    if bucket == "bullish":
        if pcr < 0.8:
            return _entry_factor("OI/PCR/max pain", ENTRY_AVOID, f"{detail} PCR is weak for put selling.")
        if pcr < 1.0:
            return _entry_factor("OI/PCR/max pain", ENTRY_WAIT, f"{detail} PCR is not strongly supportive yet.")
        if spot and max_pain and max_pain > spot * 1.01:
            return _entry_factor("OI/PCR/max pain", ENTRY_WAIT, f"{detail} Max pain is above spot; wait for confirmation.")
        return _entry_factor("OI/PCR/max pain", ENTRY_ALLOWED, f"{detail} Put-side context is supportive.")
    if bucket == "bearish":
        if pcr > 1.3:
            return _entry_factor("OI/PCR/max pain", ENTRY_AVOID, f"{detail} PCR is too put-heavy for fresh call selling.")
        if pcr > 1.1:
            return _entry_factor("OI/PCR/max pain", ENTRY_WAIT, f"{detail} PCR is not strongly bearish yet.")
        if spot and max_pain and max_pain < spot * 0.99:
            return _entry_factor("OI/PCR/max pain", ENTRY_WAIT, f"{detail} Max pain is below spot; wait for confirmation.")
        return _entry_factor("OI/PCR/max pain", ENTRY_ALLOWED, f"{detail} Call-side context is supportive.")
    if 0.8 <= pcr <= 1.2:
        return _entry_factor("OI/PCR/max pain", ENTRY_ALLOWED, f"{detail} PCR is range-friendly.")
    return _entry_factor("OI/PCR/max pain", ENTRY_WAIT, f"{detail} PCR is directional, so avoid forcing a strangle.")


def _entry_volume_factor(multi_timeframe: dict[str, Any]) -> dict[str, str]:
    row = _mtf_row(multi_timeframe, "15minute") or _mtf_row(multi_timeframe, "60minute") or _mtf_row(multi_timeframe, "day")
    if not row or row.get("status") != "analyzed":
        return _entry_factor("Volume confirmation", ENTRY_WAIT, "No analyzed intraday volume data available.")
    signal = row.get("volume_signal") or "unknown"
    ratio = row.get("volume_ratio20")
    label = row.get("label") or timeframe_label(row.get("timeframe") or "day")
    detail = f"{label} volume is {signal}; Vol x20 {_fmt_decimal(ratio)}."
    if signal in {"expansion", "strong expansion"}:
        return _entry_factor("Volume confirmation", ENTRY_ALLOWED, detail)
    if signal == "dry-up":
        return _entry_factor("Volume confirmation", ENTRY_WAIT, f"{detail} Wait for participation on the trigger candle.")
    return _entry_factor("Volume confirmation", ENTRY_WAIT, f"{detail} Volume is acceptable but not a trigger yet.")


def _entry_candidate_row(
    bucket: str,
    candidate,
    structure,
    spot: float | None,
    common_rows: list[dict[str, str]],
    option_chain,
) -> dict[str, Any]:
    strike_status = _entry_strike_status(bucket, candidate, structure, spot)
    buildup_status = _entry_buildup_status(bucket, candidate)
    liquidity_status = _entry_liquidity_status(candidate, option_chain)
    statuses = [row["status"] for row in common_rows] + [
        strike_status["status"],
        buildup_status["status"],
        liquidity_status["status"],
    ]
    status = _combined_entry_status(statuses)
    blockers = [
        item["detail"] for item in (strike_status, buildup_status, liquidity_status)
        if item["status"] in {ENTRY_AVOID, ENTRY_EXIT}
    ]
    if status == ENTRY_WAIT:
        blockers.extend(
            item["detail"] for item in (strike_status, buildup_status, liquidity_status)
            if item["status"] == ENTRY_WAIT
        )
    return {
        "action": _entry_action_for_option(candidate.option_type),
        "strike": candidate.strike,
        "option_type": candidate.option_type,
        "status": status,
        "status_key": _entry_status_key(status),
        "score": _entry_score(statuses),
        "entry_trigger": _entry_trigger_text(bucket, candidate, structure),
        "risk_trigger": _entry_risk_text(bucket, candidate, structure),
        "reasons": [
            strike_status["detail"],
            buildup_status["detail"],
            liquidity_status["detail"],
        ],
        "blockers": blockers[:4],
    }


def _entry_strike_status(bucket: str, candidate, structure, spot: float | None) -> dict[str, str]:
    if spot is None:
        return _entry_factor("Strike placement", ENTRY_WAIT, "Spot price is unavailable.")
    if candidate.option_type == "PE":
        anchor = structure.invalidation or structure.support
        if candidate.strike >= spot:
            return _entry_factor("Strike placement", ENTRY_AVOID, f"{candidate.strike:.2f} PE is not OTM below spot {spot:.2f}.")
        if anchor is not None and candidate.strike > anchor:
            return _entry_factor("Strike placement", ENTRY_AVOID, f"{candidate.strike:.2f} PE is above invalidation/support {anchor:.2f}.")
        distance = ((spot - candidate.strike) / spot) * 100
        if bucket == "bullish" and distance < 0.25:
            return _entry_factor("Strike placement", ENTRY_WAIT, f"{candidate.strike:.2f} PE is only {distance:.2f}% below spot.")
        return _entry_factor("Strike placement", ENTRY_ALLOWED, f"{candidate.strike:.2f} PE is {distance:.2f}% below spot.")

    anchor = structure.invalidation or structure.resistance
    if candidate.strike <= spot:
        return _entry_factor("Strike placement", ENTRY_AVOID, f"{candidate.strike:.2f} CE is not OTM above spot {spot:.2f}.")
    if anchor is not None and candidate.strike < anchor:
        return _entry_factor("Strike placement", ENTRY_AVOID, f"{candidate.strike:.2f} CE is below invalidation/resistance {anchor:.2f}.")
    distance = ((candidate.strike - spot) / spot) * 100
    if bucket == "bearish" and distance < 0.25:
        return _entry_factor("Strike placement", ENTRY_WAIT, f"{candidate.strike:.2f} CE is only {distance:.2f}% above spot.")
    return _entry_factor("Strike placement", ENTRY_ALLOWED, f"{candidate.strike:.2f} CE is {distance:.2f}% above spot.")


def _entry_buildup_status(bucket: str, candidate) -> dict[str, str]:
    label = candidate.buildup
    option = candidate.option_type
    if label == "Needs previous OI snapshot":
        return _entry_factor("Option-chain build-up", ENTRY_WAIT, f"{option} build-up needs a previous snapshot.")
    if label in {"Short build-up", "Long unwinding"}:
        return _entry_factor("Option-chain build-up", ENTRY_ALLOWED, f"{candidate.strike:.2f} {option} shows {label}.")
    if label == "Neutral":
        return _entry_factor("Option-chain build-up", ENTRY_WAIT, f"{candidate.strike:.2f} {option} build-up is neutral.")
    if option == "PE" and bucket == "bullish":
        return _entry_factor("Option-chain build-up", ENTRY_EXIT, f"{candidate.strike:.2f} PE shows {label}; put writers are not in control.")
    if option == "CE" and bucket == "bearish":
        return _entry_factor("Option-chain build-up", ENTRY_EXIT, f"{candidate.strike:.2f} CE shows {label}; call writers are not in control.")
    return _entry_factor("Option-chain build-up", ENTRY_AVOID, f"{candidate.strike:.2f} {option} shows {label}.")


def _entry_liquidity_status(candidate, option_chain) -> dict[str, str]:
    side_rows = [row for row in option_chain.rows if row.option_type == candidate.option_type] if option_chain else []
    highest_side_oi = max((row.oi for row in side_rows), default=0)
    oi_ratio = (candidate.oi / highest_side_oi) if highest_side_oi else 0
    if candidate.oi <= 0 or candidate.volume <= 0:
        return _entry_factor("Strike OI/volume", ENTRY_WAIT, f"OI {candidate.oi}, volume {candidate.volume}; liquidity confirmation is weak.")
    if oi_ratio < 0.25:
        return _entry_factor("Strike OI/volume", ENTRY_WAIT, f"OI {candidate.oi}, volume {candidate.volume}; strike is below 25% of top same-side OI.")
    return _entry_factor("Strike OI/volume", ENTRY_ALLOWED, f"OI {candidate.oi}, volume {candidate.volume}; same-side OI strength {oi_ratio:.0%}.")


def _entry_factor(factor: str, status: str, detail: str) -> dict[str, str]:
    return {
        "factor": factor,
        "status": status,
        "status_key": _entry_status_key(status),
        "detail": detail,
    }


def _panel_entry_status(common_rows: list[dict[str, str]], candidate_rows: list[dict[str, Any]]) -> str:
    statuses = [row["status"] for row in common_rows] + [row["status"] for row in candidate_rows]
    if ENTRY_EXIT in statuses:
        return ENTRY_EXIT
    if ENTRY_AVOID in statuses:
        return ENTRY_AVOID
    if any(row["status"] == ENTRY_ALLOWED for row in candidate_rows):
        return ENTRY_ALLOWED
    return ENTRY_WAIT


def _combined_entry_status(statuses: list[str]) -> str:
    if ENTRY_EXIT in statuses:
        return ENTRY_EXIT
    if ENTRY_AVOID in statuses:
        return ENTRY_AVOID
    if statuses and all(status == ENTRY_ALLOWED for status in statuses):
        return ENTRY_ALLOWED
    return ENTRY_WAIT


def _entry_score(statuses: list[str]) -> int:
    points = 50
    for status in statuses:
        if status == ENTRY_ALLOWED:
            points += 8
        elif status == ENTRY_WAIT:
            points -= 3
        elif status == ENTRY_AVOID:
            points -= 18
        elif status == ENTRY_EXIT:
            points -= 30
    return max(0, min(100, points))


def _entry_summary(status: str, bucket: str, candidates: list[dict[str, Any]]) -> str:
    allowed = [candidate for candidate in candidates if candidate["status"] == ENTRY_ALLOWED]
    if status == ENTRY_ALLOWED and allowed:
        best = max(allowed, key=lambda row: row["score"])
        return f"{_entry_bias_label(bucket)} entry trigger is active; best candidate {best['strike']:.2f} {best['option_type']}."
    if status == ENTRY_EXIT:
        return "Invalidation or adverse option-chain evidence is active; avoid fresh entry and adjust any open position."
    if status == ENTRY_AVOID:
        return "Setup exists, but one or more entry filters are against the trade."
    return "Setup is on watch; wait for price, volume, and option-chain confirmation before entry."


def _entry_trigger_text(bucket: str, candidate, structure) -> str:
    if candidate.option_type == "PE":
        anchor = structure.invalidation or structure.support
        if anchor:
            return f"Sell only after a bullish 15-min close/retest holds above {anchor:.2f} and {candidate.strike:.2f} PE build-up is supportive."
        return "Sell only after a bullish 15-min close/retest and supportive PE build-up."
    if candidate.option_type == "CE":
        anchor = structure.invalidation or structure.resistance
        if anchor:
            return f"Sell only after a bearish 15-min close/rejection holds below {anchor:.2f} and {candidate.strike:.2f} CE build-up is supportive."
        return "Sell only after a bearish 15-min close/rejection and supportive CE build-up."
    return "Wait for range confirmation and supportive build-up on both legs."


def _entry_risk_text(bucket: str, candidate, structure) -> str:
    if candidate.option_type == "PE":
        anchor = structure.invalidation or structure.support
        return f"Exit/adjust if spot closes below {anchor:.2f} or PE build-up turns adverse." if anchor else "Exit/adjust if support breaks or PE build-up turns adverse."
    if candidate.option_type == "CE":
        anchor = structure.invalidation or structure.resistance
        return f"Exit/adjust if spot closes above {anchor:.2f} or CE build-up turns adverse." if anchor else "Exit/adjust if resistance breaks or CE build-up turns adverse."
    return "Exit/adjust on range breakout or adverse build-up."


def _entry_status_key(status: str) -> str:
    return status.lower().replace("/", "_").replace(" ", "_")


def _entry_action(bucket: str) -> str:
    return {
        "bullish": "Sell put",
        "bearish": "Sell call",
        "neutral": "Sell strangle",
    }.get(bucket, "Wait")


def _entry_action_for_option(option_type: str) -> str:
    if option_type == "PE":
        return "Sell put"
    if option_type == "CE":
        return "Sell call"
    return "Wait"


def _entry_bias_label(bucket: str) -> str:
    return {
        "bullish": "Bullish put-sell",
        "bearish": "Bearish call-sell",
        "neutral": "Neutral strangle",
    }.get(bucket, "Options-selling")


def _mtf_row(multi_timeframe: dict[str, Any], timeframe: str) -> dict[str, Any] | None:
    return next((row for row in multi_timeframe.get("rows", []) if row.get("timeframe") == timeframe), None)


def _fmt_decimal(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


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
