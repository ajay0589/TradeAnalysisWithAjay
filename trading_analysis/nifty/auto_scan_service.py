from __future__ import annotations

from pathlib import Path
from typing import Any

from trading_analysis.candles import candle_path
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.nifty.alert_backtest import DEFAULT_ALERT_HORIZONS, backtest_nifty_alert_signals
from trading_analysis.nifty.models import to_jsonable
from trading_analysis.nifty.service import NiftyDeskService
from trading_analysis.scheduler.jobs import NiftyMarketJobs
from trading_analysis.scheduler.market_hours import is_market_hours
from trading_analysis.scheduler.runner import MarketScanScheduler
from trading_analysis.storage import (
    DEFAULT_DB_PATH,
    MarketJobRepository,
    NiftyAlertOutcomeRepository,
    NiftyAlertRepository,
    NiftyCandleRepository,
    NiftyContextRepository,
    NiftyIVObservationRepository,
    NiftyOptionChainRepository,
)


class NiftyAutoScanService:
    def __init__(
        self,
        nifty_service: NiftyDeskService | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
        scheduler: MarketScanScheduler | None = None,
    ) -> None:
        self.job_repository = MarketJobRepository(db_path)
        self.alert_repository = NiftyAlertRepository(db_path)
        self.context_repository = NiftyContextRepository(db_path)
        self.outcome_repository = NiftyAlertOutcomeRepository(db_path)
        self.candle_repository = NiftyCandleRepository(db_path)
        self.option_repository = NiftyOptionChainRepository(db_path)
        self.iv_repository = NiftyIVObservationRepository(db_path)
        self.nifty_service = nifty_service or NiftyDeskService()
        self.job_runner = NiftyMarketJobs(
            nifty_service=self.nifty_service,
            job_repository=self.job_repository,
            alert_repository=self.alert_repository,
            context_repository=self.context_repository,
            candle_repository=self.candle_repository,
            option_repository=self.option_repository,
            iv_repository=self.iv_repository,
        )
        self.scheduler = scheduler or MarketScanScheduler(job_runner=self.job_runner)

    def start(self) -> dict[str, Any]:
        return self._with_repository_context(self.scheduler.start())

    def stop(self) -> dict[str, Any]:
        return self._with_repository_context(self.scheduler.stop())

    def status(self) -> dict[str, Any]:
        return self._with_repository_context(self.scheduler.status())

    def run_once(self, force: bool = False) -> dict[str, Any]:
        result = self.scheduler.run_once(force=force)
        return self._with_repository_context(result)

    def recent_alerts(self, limit: int = 50, active_only: bool = False) -> dict[str, Any]:
        alerts = self.alert_repository.list_recent_alerts(limit=limit, active_only=active_only)
        return {
            "alerts": alerts,
            "count": len(alerts),
            "active_count": len([alert for alert in alerts if alert.get("is_active")]),
            "market_hours": is_market_hours(),
        }

    def acknowledge_alert(self, alert_id: int) -> dict[str, Any]:
        return {"alert": self.alert_repository.acknowledge_alert(alert_id)}

    def context_snapshots(self, limit: int = 50) -> dict[str, Any]:
        rows = self.context_repository.list_context_snapshots(limit=limit)
        return {"snapshots": rows, "count": len(rows)}

    def context_snapshot(self, context_snapshot_id: int) -> dict[str, Any]:
        snapshot = self.context_repository.load_context_snapshot(context_snapshot_id)
        return {"snapshot": snapshot} if snapshot else {"error": "Context snapshot not found", "id": context_snapshot_id}

    def alert_outcomes(self, alert_id: int) -> dict[str, Any]:
        rows = self.outcome_repository.load_alert_outcomes(alert_id)
        return {"alert_id": alert_id, "outcomes": rows, "count": len(rows)}

    def latest_data(self) -> dict[str, Any]:
        latest_option = self.option_repository.load_latest_snapshot()
        latest_iv = self.iv_repository.latest()
        context_count = len(self.context_repository.list_context_snapshots(limit=100000))
        alert_count = len(self.alert_repository.list_recent_alerts(limit=100000, active_only=False))
        return {
            "latest_candles": {
                timeframe: self.candle_repository.latest_timestamp("NIFTY", timeframe)
                for timeframe in ("day", "60minute", "15minute")
            },
            "latest_option_snapshot": latest_option,
            "latest_iv_observation": latest_iv,
            "counts": {
                "candles": self.candle_repository.counts("NIFTY"),
                "context_snapshots": context_count,
                "alerts": alert_count,
            },
        }

    def option_snapshots(self, limit: int = 20, expiry: str | None = None) -> dict[str, Any]:
        rows = self.option_repository.list_snapshots(symbol="NIFTY", expiry=expiry, limit=limit)
        return {"snapshots": rows, "count": len(rows)}

    def option_snapshot(self, snapshot_id: int) -> dict[str, Any]:
        rows = self.option_repository.load_snapshot_rows(snapshot_id)
        snapshots = self.option_repository.list_snapshots(limit=100000)
        snapshot = next((row for row in snapshots if row.get("id") == snapshot_id), None)
        return {"snapshot": snapshot, "rows": rows, "row_count": len(rows)} if snapshot else {"error": "Option snapshot not found", "id": snapshot_id}

    def iv_history(self, lookback_days: int = 252) -> dict[str, Any]:
        rows = self.iv_repository.load_history(symbol="NIFTY", lookback_days=lookback_days)
        return {"observations": rows, "count": len(rows), "lookback_days": lookback_days}

    def alert_backtest(
        self,
        *,
        timeframe: str = "15minute",
        limit: int = 500,
        horizons: list[int] | None = None,
        horizon: str | None = None,
        strategy_id: str | None = None,
        direction: str | None = None,
        alert_type: str | None = None,
    ) -> dict[str, Any]:
        alerts = self.alert_repository.list_recent_alerts(limit=limit, active_only=False)
        filtered = [
            alert
            for alert in alerts
            if _matches(alert, "horizon", horizon)
            and _matches(alert, "strategy_id", strategy_id)
            and _matches(alert, "direction", direction)
            and _matches(alert, "alert_type", alert_type)
        ]
        path = candle_path(self.nifty_service.candle_root, timeframe, "NIFTY_50")
        warnings: list[str] = []
        try:
            candles = load_candles(path)
        except FileNotFoundError:
            candles = []
            warnings.append(f"Missing NIFTY {timeframe} candles at {path}.")
        payload = backtest_nifty_alert_signals(filtered, candles, horizons=horizons or list(DEFAULT_ALERT_HORIZONS))
        saved_outcomes = 0
        for row in payload.get("rows") or []:
            if row.get("status") != "evaluated" or row.get("alert_id") is None:
                continue
            if self.outcome_repository.save_alert_backtest_result(int(row["alert_id"]), row, timeframe=timeframe):
                saved_outcomes += 1
        return to_jsonable(
            {
                **payload,
                "saved_outcomes": saved_outcomes,
                "timeframe": timeframe,
                "alert_filter": {
                    "limit": limit,
                    "horizon": horizon,
                    "strategy_id": strategy_id,
                    "direction": direction,
                    "alert_type": alert_type,
                },
                "candle_file": str(path),
                "warnings": warnings + list(payload.get("warnings") or []),
            }
        )

    def _with_repository_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        recent_jobs = self.job_repository.latest_jobs(limit=20)
        recent_alerts = self.alert_repository.list_recent_alerts(limit=20, active_only=False)
        active_alerts = self.alert_repository.list_recent_alerts(limit=200, active_only=True)
        return {
            **payload,
            "scheduler": "running" if payload.get("running") else "stopped",
            "market_hours": payload.get("market_hours", is_market_hours()),
            "recent_jobs": recent_jobs,
            "recent_alerts": recent_alerts,
            "active_alerts_count": len(active_alerts),
        }


def _matches(alert: dict[str, Any], key: str, expected: str | None) -> bool:
    if not expected:
        return True
    return str(alert.get(key) or "").lower() == expected.lower()
