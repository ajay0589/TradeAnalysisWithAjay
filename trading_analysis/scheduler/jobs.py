from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from trading_analysis.nifty.service import NiftyDeskService
from trading_analysis.scheduler.alerts import generate_nifty_alerts
from trading_analysis.storage import DEFAULT_DB_PATH, MarketJobRepository, NiftyAlertRepository, NiftyContextRepository


class NiftyMarketJobs:
    def __init__(
        self,
        nifty_service: NiftyDeskService | None = None,
        job_repository: MarketJobRepository | None = None,
        alert_repository: NiftyAlertRepository | None = None,
        context_repository: NiftyContextRepository | None = None,
    ) -> None:
        self.nifty_service = nifty_service or NiftyDeskService()
        self.job_repository = job_repository or MarketJobRepository(DEFAULT_DB_PATH)
        self.alert_repository = alert_repository or NiftyAlertRepository(DEFAULT_DB_PATH)
        self.context_repository = context_repository or NiftyContextRepository(DEFAULT_DB_PATH)

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
        return {
            "symbol": "NIFTY",
            "refresh": refresh,
            "candle_sources": summary.get("candle_sources") or {},
            "refresh_results": summary.get("refresh_results") or [],
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
        files = _latest_option_files(self.nifty_service.option_chain_dir)
        return {
            "symbol": "NIFTY",
            "refresh": refresh,
            "fetched": fetched,
            "cached_snapshots": len(files),
            "latest_snapshot": str(files[0]) if files else None,
        }

    def _run_nifty_context(self, mode: str) -> dict[str, Any]:
        context = self.nifty_service.nifty_context(mode=mode, include_option_chain=True, include_iv=True)
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
        return {
            "symbol": "NIFTY",
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
            "warnings": warnings,
            "errors": context.get("errors") or [],
        }


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
