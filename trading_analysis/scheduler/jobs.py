from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Callable

from trading_analysis.candles import candle_path
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.nifty.service import NiftyDeskService
from trading_analysis.scheduler.alerts import generate_nifty_alerts
from trading_analysis.storage import (
    DEFAULT_DB_PATH,
    MarketJobRepository,
    NiftyAlertRepository,
    NiftyCandleRepository,
    NiftyContextRepository,
    NiftyIVObservationRepository,
    NiftyOptionChainRepository,
)


class NiftyMarketJobs:
    def __init__(
        self,
        nifty_service: NiftyDeskService | None = None,
        job_repository: MarketJobRepository | None = None,
        alert_repository: NiftyAlertRepository | None = None,
        context_repository: NiftyContextRepository | None = None,
        candle_repository: NiftyCandleRepository | None = None,
        option_repository: NiftyOptionChainRepository | None = None,
        iv_repository: NiftyIVObservationRepository | None = None,
    ) -> None:
        self.nifty_service = nifty_service or NiftyDeskService()
        self.job_repository = job_repository or MarketJobRepository(DEFAULT_DB_PATH)
        self.alert_repository = alert_repository or NiftyAlertRepository(DEFAULT_DB_PATH)
        self.context_repository = context_repository or NiftyContextRepository(DEFAULT_DB_PATH)
        self.candle_repository = candle_repository or NiftyCandleRepository(DEFAULT_DB_PATH)
        self.option_repository = option_repository or NiftyOptionChainRepository(DEFAULT_DB_PATH)
        self.iv_repository = iv_repository or NiftyIVObservationRepository(DEFAULT_DB_PATH)

    def update_nifty_candles_job(self, refresh: bool = False) -> dict[str, Any]:
        return self._record(
            "update_nifty_candles",
            {"refresh": refresh},
            lambda: self._update_nifty_candles(refresh=refresh),
        )

    def update_nifty_option_chain_job(self, refresh: bool = False) -> dict[str, Any]:
        return self._record(
            "update_nifty_option_chain",
            {"refresh": refresh},
            lambda: self._update_nifty_option_chain(refresh=refresh),
        )

    def record_nifty_iv_job(self) -> dict[str, Any]:
        return self._record("record_nifty_iv", {}, self._record_nifty_iv)

    def run_nifty_context_job(self, mode: str = "auto") -> dict[str, Any]:
        return self._record(
            "run_nifty_context",
            {"mode": mode},
            lambda: self._run_nifty_context(mode=mode),
        )

    def run_nifty_opportunity_scan_job(self, mode: str = "auto", min_score: int = 70) -> dict[str, Any]:
        return self._record(
            "run_nifty_opportunity_scan",
            {"mode": mode, "min_score": min_score},
            lambda: self._run_nifty_opportunity_scan(mode=mode, min_score=min_score),
        )

    def cleanup_market_jobs_job(self, days: int = 7) -> dict[str, Any]:
        return self._record(
            "cleanup_market_jobs",
            {"days": days},
            lambda: self.job_repository.cleanup(days=days),
        )

    def _record(self, job_name: str, params: dict[str, Any], fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        job_id = self.job_repository.start_job(job_name, params=params)
        try:
            result = fn()
            job = self.job_repository.finish_job(job_id, result=result)
            return {"job": job, "result": result}
        except Exception as exc:
            self.job_repository.fail_job(job_id, str(exc))
            return {"job": {"id": job_id, "job_name": job_name, "status": "failed", "error": str(exc)}, "error": str(exc)}

    def _update_nifty_candles(self, refresh: bool) -> dict[str, Any]:
        context = self.nifty_service.nifty_context(
            mode="auto",
            include_option_chain=False,
            include_iv=False,
            refresh=refresh,
            timeframe="15minute",
            days=45,
        )
        summary = context.get("summary") or {}
        candle_db = self._persist_cached_candles()
        return {
            "symbol": "NIFTY",
            "refresh": refresh,
            "candle_sources": summary.get("candle_sources") or {},
            "refresh_results": summary.get("refresh_results") or [],
            "candle_db": candle_db,
            "warnings": context.get("warnings") or [],
            "errors": context.get("errors") or [],
        }

    def _update_nifty_option_chain(self, refresh: bool) -> dict[str, Any]:
        fetched: dict[str, Any] | None = None
        analysis_service = getattr(self.nifty_service, "analysis_service", None)
        if refresh and analysis_service is not None and hasattr(analysis_service, "refresh_option_chain_snapshot"):
            fetched = analysis_service.refresh_option_chain_snapshot(
                symbol="NIFTY",
                expiry=None,
                strikes_around=20,
                all_strikes=False,
                max_snapshots=5,
            )
        snapshot_id = None
        saved_rows = 0
        if fetched:
            raw_file = fetched.get("latest_snapshot")
            rows = _read_option_rows(raw_file)
            analysis_for_db = {**fetched, "rows": rows, "spot": fetched.get("spot") or fetched.get("spot_price")}
            snapshot_id = self.option_repository.save_snapshot(analysis_for_db, raw_file=raw_file)
            if snapshot_id and rows:
                saved_rows = self.option_repository.save_rows(snapshot_id, rows)
        files = _latest_option_files(self.nifty_service.option_chain_dir)
        return {
            "symbol": "NIFTY",
            "refresh": refresh,
            "fetched": fetched,
            "option_snapshot_id": snapshot_id,
            "saved_rows": saved_rows,
            "cached_snapshots": len(files),
            "latest_snapshot": str(files[0]) if files else None,
        }

    def _run_nifty_context(self, mode: str) -> dict[str, Any]:
        context = self.nifty_service.nifty_context(mode=mode, include_option_chain=True, include_iv=True)
        self._attach_latest_data_links(context)
        warnings = list(context.get("warnings") or [])
        try:
            context["context_snapshot_id"] = self.context_repository.save_context_result(context)
        except Exception as exc:
            warnings.append(f"Context snapshot persistence failed: {exc}")
        context["warnings"] = warnings
        return context

    def _record_nifty_iv(self) -> dict[str, Any]:
        context = self.nifty_service.nifty_context(
            mode="auto",
            include_option_chain=True,
            include_iv=True,
            refresh=True,
            timeframe="15minute",
            days=45,
        )
        iv = context.get("iv") or {}
        options = context.get("options") or {}
        latest_option = self.option_repository.load_latest_snapshot()
        observation_id = self.iv_repository.record_observation(
            symbol="NIFTY",
            expiry=options.get("selected_weekly_expiry"),
            atm_strike=options.get("atm_strike"),
            atm_iv=iv.get("atm_iv"),
            weekly_atm_iv=(options.get("weekly_chain_summary") or {}).get("atm_iv") if options else None,
            monthly_atm_iv=(options.get("monthly_chain_summary") or {}).get("atm_iv") if options else None,
            source_snapshot_id=(latest_option or {}).get("id"),
            source_file=(latest_option or {}).get("raw_file"),
        )
        return {
            "symbol": "NIFTY",
            "iv_observation_id": observation_id,
            "atm_iv": iv.get("atm_iv"),
            "iv_rank": iv.get("iv_rank"),
            "iv_percentile": iv.get("iv_percentile"),
            "iv_regime": iv.get("iv_regime"),
            "warnings": context.get("warnings") or [],
            "errors": context.get("errors") or [],
        }

    def _run_nifty_opportunity_scan(self, mode: str, min_score: int) -> dict[str, Any]:
        context = self.nifty_service.nifty_strategy_suggestions(mode=mode, refresh=False)
        candidates = list(context.get("candidates") or [])
        data_links = self._attach_latest_data_links(context)
        warnings = list(context.get("warnings") or [])
        context_snapshot_id = None
        candidate_ids: list[int] = []
        try:
            context_snapshot_id = self.context_repository.save_context_result(context)
            candidate_ids = self.context_repository.save_strategy_candidates(context_snapshot_id, candidates)
            context["context_snapshot_id"] = context_snapshot_id
        except Exception as exc:
            warnings.append(f"Context/candidate persistence failed: {exc}")
        generated = generate_nifty_alerts(context, candidates, min_score=min_score)
        created = []
        suppressed = 0
        for alert in generated:
            if context_snapshot_id is not None:
                alert["context_snapshot_id"] = context_snapshot_id
            alert["metadata"] = {**(alert.get("metadata") or {}), **data_links}
            if self.alert_repository.suppress_duplicate(
                str(alert.get("alert_type") or ""),
                alert.get("strategy_id"),
                alert.get("direction"),
                min_minutes=15,
                score=alert.get("score"),
                severity=alert.get("severity"),
            ):
                suppressed += 1
                continue
            created.append(self.alert_repository.create_alert(**alert))
        return {
            "symbol": "NIFTY",
            "mode": mode,
            "candidate_count": len(candidates),
            "alerts_generated": len(generated),
            "alerts_created": len(created),
            "alerts_suppressed": suppressed,
            "alerts": created,
            "context_snapshot_id": context_snapshot_id,
            "candidate_ids": candidate_ids,
            "data_links": data_links,
            "warnings": warnings,
            "errors": context.get("errors") or [],
        }

    def _persist_cached_candles(self) -> dict[str, Any]:
        saved: dict[str, Any] = {}
        for timeframe in ("day", "60minute", "15minute"):
            path = candle_path(self.nifty_service.candle_root, timeframe, "NIFTY_50")
            try:
                candles = load_candles(path)
            except FileNotFoundError:
                saved[timeframe] = {"saved": 0, "error": f"Missing candle file: {path}"}
                continue
            count = self.candle_repository.upsert_candles("NIFTY", timeframe, candles, source="zerodha_csv")
            saved[timeframe] = {
                "saved": count,
                "latest_timestamp": candles[-1].timestamp.isoformat(timespec="seconds") if candles else None,
            }
        return saved

    def _attach_latest_data_links(self, context: dict[str, Any]) -> dict[str, Any]:
        latest_option = self.option_repository.load_latest_snapshot()
        latest_iv = self.iv_repository.latest()
        latest_candle = self.candle_repository.latest_timestamp("NIFTY", "15minute") or self.candle_repository.latest_timestamp("NIFTY", "day")
        links = {
            "latest_candle_timestamp": latest_candle,
            "latest_option_snapshot_id": (latest_option or {}).get("id"),
            "latest_iv_observation_id": (latest_iv or {}).get("id"),
        }
        summary = context.setdefault("summary", {})
        summary["data_links"] = links
        return links


def update_nifty_candles_job(refresh: bool = False) -> dict[str, Any]:
    return NiftyMarketJobs().update_nifty_candles_job(refresh=refresh)


def update_nifty_option_chain_job(refresh: bool = False) -> dict[str, Any]:
    return NiftyMarketJobs().update_nifty_option_chain_job(refresh=refresh)


def record_nifty_iv_job() -> dict[str, Any]:
    return NiftyMarketJobs().record_nifty_iv_job()


def run_nifty_context_job(mode: str = "auto") -> dict[str, Any]:
    return NiftyMarketJobs().run_nifty_context_job(mode=mode)


def run_nifty_opportunity_scan_job(mode: str = "auto", min_score: int = 70) -> dict[str, Any]:
    return NiftyMarketJobs().run_nifty_opportunity_scan_job(mode=mode, min_score=min_score)


def cleanup_market_jobs_job(days: int = 7) -> dict[str, Any]:
    return NiftyMarketJobs().cleanup_market_jobs_job(days=days)


def _latest_option_files(directory: str | Path) -> list[Path]:
    root = Path(directory)
    if not root.exists():
        return []
    return sorted(root.glob("NIFTY_*.csv"), key=lambda item: item.stat().st_mtime, reverse=True)


def _read_option_rows(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    csv_path = Path(path)
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
