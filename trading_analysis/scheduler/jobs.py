from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Callable
import time

from trading_analysis import diagnostics

from trading_analysis.candles import candle_path
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.nifty.service import NiftyDeskService
from trading_analysis.nifty.iv_context import record_nifty_iv_snapshot
from trading_analysis.nifty.live_scanner import HORIZON_CONFIG, build_live_entry_signal, evaluate_live_exit
from trading_analysis.notifications.telegram import TelegramNotifier, nifty_trade_message
from trading_analysis.scheduler.alerts import generate_nifty_alerts
from trading_analysis.scheduler.market_hours import is_scan_window
from trading_analysis.storage import (
    DEFAULT_DB_PATH,
    MarketJobRepository,
    NiftyAlertRepository,
    NiftyCandleRepository,
    NiftyContextRepository,
    NiftyIVObservationRepository,
    NiftyOptionChainRepository,
    NiftyTradeRepository,
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
        trade_repository: NiftyTradeRepository | None = None,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self.nifty_service = nifty_service or NiftyDeskService()
        self.job_repository = job_repository or MarketJobRepository(DEFAULT_DB_PATH)
        self.alert_repository = alert_repository or NiftyAlertRepository(DEFAULT_DB_PATH)
        self.context_repository = context_repository or NiftyContextRepository(DEFAULT_DB_PATH)
        self.candle_repository = candle_repository or NiftyCandleRepository(DEFAULT_DB_PATH)
        self.option_repository = option_repository or NiftyOptionChainRepository(DEFAULT_DB_PATH)
        self.iv_repository = iv_repository or NiftyIVObservationRepository(DEFAULT_DB_PATH)
        repository_db = getattr(self.alert_repository, "db_path", DEFAULT_DB_PATH)
        self.trade_repository = trade_repository or NiftyTradeRepository(repository_db)
        self.notifier = notifier or TelegramNotifier.from_env("NIFTY_")
        self.scan_progress: dict[str, Any] = {"completed": 0, "total": 3, "current": None, "status": "idle"}

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

    def run_nifty_exit_scan_job(self) -> dict[str, Any]:
        return self._record("run_nifty_exit_scan", {}, self._run_nifty_exit_scan)

    def cleanup_market_jobs_job(self, days: int = 7) -> dict[str, Any]:
        return self._record(
            "cleanup_market_jobs",
            {"days": days},
            lambda: self.job_repository.cleanup(days=days),
        )

    def _record(self, job_name: str, params: dict[str, Any], fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        job_id = self.job_repository.start_job(job_name, params=params)
        started = time.monotonic()
        diagnostics.record("job_started", area="nifty", job=job_name, job_id=job_id)
        try:
            with diagnostics.scope("nifty"):
                result = fn()
            job = self.job_repository.finish_job(job_id, result=result)
            diagnostics.record("job_finished", area="nifty", job=job_name, job_id=job_id,
                               duration_ms=int((time.monotonic() - started) * 1000),
                               warnings=result.get("warnings"), errors=result.get("errors"),
                               horizons=result.get("horizons"), telegram=result.get("telegram"))
            return {"job": job, "result": result}
        except Exception as exc:
            diagnostics.record("job_finished", area="nifty", status="failed", job=job_name, job_id=job_id,
                               error=str(exc), duration_ms=int((time.monotonic() - started) * 1000))
            if job_name == "run_nifty_opportunity_scan":
                self.scan_progress.update({"current": None, "status": "failed"})
            self.job_repository.fail_job(job_id, str(exc))
            return {"job": {"id": job_id, "job_name": job_name, "status": "failed", "error": str(exc)}, "error": str(exc)}

    def _update_nifty_candles(self, refresh: bool) -> dict[str, Any]:
        context = self.nifty_service.nifty_context(
            mode="auto",
            include_option_chain=False,
            include_iv=False,
            refresh=refresh,
            refresh_due_only=True,
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
            refresh=False,
            timeframe="15minute",
            days=45,
        )
        iv = context.get("iv") or {}
        options = context.get("options") or {}
        if options.get("atm_iv") is not None:
            record_nifty_iv_snapshot(
                expiry=options.get("selected_weekly_expiry"), atm_strike=options.get("atm_strike"),
                atm_iv=options["atm_iv"],
                weekly_atm_iv=(options.get("weekly_chain_summary") or {}).get("atm_iv"),
                monthly_atm_iv=(options.get("monthly_chain_summary") or {}).get("atm_iv"),
                path=self.nifty_service.iv_history_path,
            )
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
        if not is_scan_window():
            self.scan_progress = {"completed": 0, "total": 0, "current": None, "status": "skipped"}
            return {"symbol": "NIFTY", "mode": mode, "status": "skipped", "reason": "outside_scan_window",
                    "horizons": {}, "alerts_created": 0, "alerts": [], "telegram": {"sent": 0, "errors": []}}
        horizons = [mode] if mode in HORIZON_CONFIG else list(HORIZON_CONFIG)
        self.scan_progress = {"completed": 0, "total": len(horizons), "current": None, "status": "running"}
        results: dict[str, Any] = {}
        created: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []
        telegram = {"configured": self.notifier.configured(), "sent": 0, "errors": []}
        for horizon in horizons:
            self.scan_progress["current"] = f"{horizon} entry checks"
            context = self.nifty_service.nifty_strategy_suggestions(mode=horizon, refresh=False)
            candidates = list(context.get("candidates") or [])
            data_links = self._attach_latest_data_links(context)
            context_snapshot_id = None
            candidate_ids: list[int] = []
            try:
                context_snapshot_id = self.context_repository.save_context_result(context)
                candidate_ids = self.context_repository.save_strategy_candidates(context_snapshot_id, candidates)
            except Exception as exc:
                warnings.append(f"{horizon} context persistence failed: {exc}")
            config = HORIZON_CONFIG[horizon]
            candles = self._cached_candles(config["timeframe"])
            checks: dict[str, Any] = {}
            signal = build_live_entry_signal(context, candidates, horizon, candles, min_score=min_score, diagnostics=checks)
            horizon_result: dict[str, Any] = {
                "candidate_count": len(candidates),
                "checks": checks,
                "context_snapshot_id": context_snapshot_id,
                "candidate_ids": candidate_ids,
                "entry_created": False,
                "reason": "No closed-candle setup met every entry gate.",
            }
            if signal:
                signal["context_snapshot_id"] = context_snapshot_id
                signal["metadata"] = {**(signal.get("metadata") or {}), **data_links}
                opened = self.trade_repository.open_trade(signal)
                if opened.get("created"):
                    trade = opened["trade"]
                    alert = self.alert_repository.create_alert(**self._entry_alert(signal, trade))
                    trade = self.trade_repository.attach_entry_alert(trade["trade_id"], int(alert["id"]))
                    delivery = self._send_nifty_telegram("entry", trade, alert)
                    self.alert_repository.update_telegram_status(int(alert["id"]), delivery["status"], delivery.get("error"))
                    telegram["sent"] += int(delivery["sent"])
                    if delivery.get("error"):
                        telegram["errors"].append(delivery["error"])
                    created.append(alert)
                    horizon_result.update({"entry_created": True, "trade": trade, "alert": alert, "reason": "Entry created."})
                else:
                    horizon_result["reason"] = "An open trade already exists for this horizon, or this candle was already processed."
            results[horizon] = horizon_result
            self.scan_progress["completed"] += 1
            warnings.extend(context.get("warnings") or [])
            errors.extend(context.get("errors") or [])
        self.scan_progress.update({"current": None, "status": "completed"})
        return {
            "symbol": "NIFTY",
            "mode": mode,
            "horizons": results,
            "candidate_count": sum(row["candidate_count"] for row in results.values()),
            "alerts_created": len(created),
            "alerts": created,
            "telegram": telegram,
            "warnings": list(dict.fromkeys(warnings)),
            "errors": list(dict.fromkeys(errors)),
        }

    def _run_nifty_exit_scan(self) -> dict[str, Any]:
        if not is_scan_window():
            return {"status": "skipped", "reason": "outside_scan_window", "open_checked": 0,
                    "trades_closed": 0, "alerts": [], "telegram": {"sent": 0, "errors": []}}
        checked = closed = 0
        alerts: list[dict[str, Any]] = []
        telegram = {"configured": self.notifier.configured(), "sent": 0, "errors": []}
        for trade in self.trade_repository.open_trades():
            config = HORIZON_CONFIG[str(trade["horizon"])]
            candles = self._cached_candles(config["timeframe"])
            outcome = evaluate_live_exit(trade, candles)
            if not outcome:
                continue
            checked += 1
            if outcome.get("checked_only"):
                self.trade_repository.mark_checked(trade["trade_id"], outcome["candle_timestamp"], float(outcome["price"]))
                continue
            closed_trade = self.trade_repository.close_trade(
                trade["trade_id"],
                exit_time=outcome["candle_timestamp"],
                exit_price=float(outcome["price"]),
                exit_reason=str(outcome["reason"]),
            )
            alert = self.alert_repository.create_alert(**self._exit_alert(closed_trade))
            closed_trade = self.trade_repository.attach_exit_alert(trade["trade_id"], int(alert["id"]))
            delivery = self._send_nifty_telegram("exit", closed_trade, alert)
            self.alert_repository.update_telegram_status(int(alert["id"]), delivery["status"], delivery.get("error"))
            telegram["sent"] += int(delivery["sent"])
            if delivery.get("error"):
                telegram["errors"].append(delivery["error"])
            alerts.append(alert)
            closed += 1
        return {"open_checked": checked, "trades_closed": closed, "alerts": alerts, "telegram": telegram}

    def _cached_candles(self, timeframe: str) -> list[Any]:
        candles = self.candle_repository.load_candles("NIFTY", timeframe)
        if candles:
            return candles
        path = candle_path(self.nifty_service.candle_root, timeframe, "NIFTY_50")
        try:
            return load_candles(path)
        except FileNotFoundError:
            return []

    def _entry_alert(self, signal: dict[str, Any], trade: dict[str, Any]) -> dict[str, Any]:
        horizon = str(signal["horizon"])
        return {
            "alert_type": "nifty_entry",
            "event_kind": "entry",
            "trade_id": trade["trade_id"],
            "mode": "live",
            "horizon": horizon,
            "severity": "important",
            "symbol": "NIFTY",
            "spot": signal["entry_price"],
            "strategy_id": signal.get("strategy_id"),
            "direction": signal["direction"],
            "score": signal.get("score"),
            "confidence": signal.get("confidence"),
            "title": f"NIFTY {horizon.title()} {signal['direction'].title()} Entry",
            "message": f"A closed {signal['entry_timeframe']} candle passed technical, candle, option-chain, and risk gates.",
            "trigger_level": signal["entry_price"],
            "invalidation_level": signal["stop_level"],
            "expiry": None,
            "reasons": signal.get("reasons") or [],
            "risks": signal.get("risks") or [],
            "metadata": signal.get("metadata") or {},
            "context_snapshot_id": signal.get("context_snapshot_id"),
            "telegram_status": "pending",
        }

    def _exit_alert(self, trade: dict[str, Any]) -> dict[str, Any]:
        return {
            "alert_type": "nifty_exit",
            "event_kind": "exit",
            "trade_id": trade["trade_id"],
            "mode": "live",
            "horizon": trade["horizon"],
            "severity": "important",
            "symbol": "NIFTY",
            "spot": trade["exit_price"],
            "strategy_id": trade.get("strategy_id"),
            "direction": trade["direction"],
            "score": trade.get("score"),
            "confidence": trade.get("confidence"),
            "title": f"NIFTY {str(trade['horizon']).title()} Exit",
            "message": f"Trade closed after {trade['open_duration']}: {trade['exit_reason']}.",
            "trigger_level": trade["exit_price"],
            "invalidation_level": trade["stop_level"],
            "expiry": None,
            "reasons": [f"Exit reason: {trade['exit_reason']}"],
            "risks": [],
            "metadata": trade.get("metadata") or {},
            "context_snapshot_id": trade.get("context_snapshot_id"),
            "telegram_status": "pending",
        }

    def _send_nifty_telegram(self, event: str, trade: dict[str, Any], alert: dict[str, Any]) -> dict[str, Any]:
        if not self.notifier.configured():
            return {"sent": False, "status": "not_configured", "error": None}
        result = self.notifier.send_message(nifty_trade_message(event, trade, alert))
        return {
            "sent": bool(result.get("sent")),
            "status": "sent" if result.get("sent") else "failed",
            "error": result.get("error"),
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
            "latest_option_snapshot_at": (latest_option or {}).get("captured_at"),
            "latest_iv_observation_id": (latest_iv or {}).get("id"),
            "latest_iv_observation_at": (latest_iv or {}).get("captured_at"),
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
