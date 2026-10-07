from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from trading_analysis.index_scanner import IndexScannerService
from trading_analysis.instrument_master_service import InstrumentMasterService
from trading_analysis.nifty.auto_scan_service import NiftyAutoScanService
from trading_analysis.notifications.telegram import TelegramNotifier
from trading_analysis.live_quotes import LIVE_QUOTES


class AllIndexMonitor:
    def __init__(self, nifty: NiftyAutoScanService, indexes: IndexScannerService,
                 masters: InstrumentMasterService) -> None:
        self.nifty = nifty
        self.indexes = indexes
        self.masters = masters

    def status(self) -> dict[str, Any]:
        nifty = self.nifty.status()
        bank = self.indexes.status("BANKNIFTY")
        sensex = self.indexes.status("SENSEX")
        recent_delivery = next((alert for alert in nifty.get("recent_alerts") or []
                                if alert.get("telegram_status") in {"sent", "failed", "retry", "delivery_unknown", "expired"}), None)
        recent_failure = recent_delivery if recent_delivery and recent_delivery.get("telegram_status") != "sent" else None
        nifty_progress = nifty.get("entry_scan_progress") or {}
        scanners = {"NIFTY": {"running": nifty["running"], "scan_active": bool(nifty["active_jobs"]),
                               "phase": ", ".join(nifty["active_jobs"]) or "waiting",
                               "started_at": nifty["started_at"], "stopped_at": nifty["stopped_at"],
                               "last_cycle_at": nifty.get("last_cycle_completed_at") or nifty.get("last_job_run"),
                               "next_run": (nifty.get("next_runs") or {}).get("opportunity_scan") if nifty["running"] else None,
                               "telegram_configured": TelegramNotifier.from_env("NIFTY_").configured(),
                               "errors": nifty["errors"] + (["Telegram: " + str(recent_failure.get("telegram_error") or "delivery failed")]
                                                              if recent_failure else []),
                               "progress": {"current": nifty_progress.get("current") or ", ".join(nifty["active_jobs"]),
                                            "completed": nifty_progress.get("completed", 0),
                                            "total": nifty_progress.get("total", 0)}},
                    "BANKNIFTY": self._index_row(bank), "SENSEX": self._index_row(sensex)}
        return {"all_running": all((nifty["running"], bank["running"], sensex["running"])),
                "any_running": any((nifty["running"], bank["running"], sensex["running"])),
                "masters_ready": self.masters.status()["ready_for_all"],
                "unconfigured_telegram": [symbol for symbol, row in scanners.items() if not row["telegram_configured"]],
                "scanners": scanners, "live_quotes": LIVE_QUOTES.status(),
                "market_evidence": {"NIFTY": {"rolling": nifty.get("option_evidence") or {}, "signals": nifty.get("signal_states") or {}},
                                    **{symbol: {"rolling": ((state.get("last_result") or {}).get("horizons", {}).get("intraday", {}).get("option") or {}).get("rolling") or {},
                                                "signals": (state.get("last_result") or {}).get("horizons") or {}}
                                       for symbol, state in (("BANKNIFTY", bank), ("SENSEX", sensex))}}}

    @staticmethod
    def _index_row(state: dict[str, Any]) -> dict[str, Any]:
        return {"running": state["running"], "scan_active": state.get("scan_active", False), "phase": state["phase"],
                "started_at": state["started_at"], "stopped_at": state["stopped_at"],
                "last_cycle_at": state["last_cycle_at"],
                "telegram_configured": bool(state["telegram_destination"]),
                "errors": state["errors"], "progress": state.get("progress"),
                "data_service": state.get("data_service"),
                "next_run": state.get("next_run")}

    def start(self, nifty_seconds: int = 60, bank_seconds: int = 180, sensex_seconds: int = 180) -> dict[str, Any]:
        if not all(60 <= value <= 3600 for value in (nifty_seconds, bank_seconds, sensex_seconds)):
            raise ValueError("Intervals must be between 60 and 3600 seconds.")
        master_status = self.masters.status()
        if master_status["job"]["running"]:
            raise ValueError("Wait for the instrument-master refresh to complete before starting scanners.")
        if not master_status["ready_for_all"]:
            missing = ", ".join(f"{exchange} ({row['state']})" for exchange, row in master_status["masters"].items()
                                if row["state"] != "fresh")
            raise ValueError(f"Refresh Zerodha instrument masters in Data Ops first: {missing}.")
        already = self.status()["scanners"]
        started: list[str] = []
        try:
            self.nifty.start(scan_interval_seconds=nifty_seconds)
            if not already["NIFTY"]["running"]:
                started.append("NIFTY")
            self.indexes.start("BANKNIFTY", bank_seconds)
            if not already["BANKNIFTY"]["running"]:
                started.append("BANKNIFTY")
            self.indexes.start("SENSEX", sensex_seconds)
            if not already["SENSEX"]["running"]:
                started.append("SENSEX")
        except Exception:
            for symbol in reversed(started):
                if symbol == "NIFTY":
                    self.nifty.stop()
                else:
                    self.indexes.stop(symbol)
            raise
        return self.status()

    def stop(self) -> dict[str, Any]:
        errors = {}
        for symbol, fn in (("NIFTY", self.nifty.stop),
                           ("BANKNIFTY", lambda: self.indexes.stop("BANKNIFTY")),
                           ("SENSEX", lambda: self.indexes.stop("SENSEX"))):
            try:
                fn()
            except Exception as exc:
                errors[symbol] = str(exc)
        result = self.status()
        result["stop_errors"] = errors
        return result

    @staticmethod
    def test_telegram(symbol: str) -> dict[str, Any]:
        selected = symbol.upper()
        if selected not in {"NIFTY", "BANKNIFTY", "SENSEX"}:
            raise ValueError("Choose NIFTY, BANKNIFTY, or SENSEX.")
        notifier = TelegramNotifier.from_env(f"{selected}_")
        destination = f"{selected}_TELEGRAM_CHAT_ID"
        if selected != "NIFTY" and not notifier.configured():
            notifier = TelegramNotifier.from_env("NIFTY_")
            destination = "NIFTY_TELEGRAM_CHAT_ID"
        if not notifier.configured():
            return {"symbol": selected, "sent": False, "error": f"{selected} Telegram bot token or chat ID is not configured"}
        result = notifier.send_message(f"{selected} scanner TEST MESSAGE (not a trade alert).")
        return {"symbol": selected, "destination": destination, "sent": bool(result.get("sent")),
                "error": result.get("error")}

    def diagnostics(self, day: str | None = None) -> dict[str, Any]:
        selected = date.fromisoformat(day) if day else datetime.now(ZoneInfo("Asia/Kolkata")).date()
        stamp = selected.isoformat()
        jobs = [self._nifty_job(row) for row in self.nifty.job_repository.latest_jobs(limit=None, day=stamp)
                if str(row.get("started_at") or "").startswith(stamp)]
        alerts = [row for row in self.nifty.alert_repository.list_recent_alerts(limit=None, day=stamp)
                  if str(row.get("created_at") or "").startswith(stamp)]
        trades = [row for row in self.nifty.trade_repository.list_trades(status="all", limit=None, day=stamp)
                  if str(row.get("created_at") or "").startswith(stamp)
                  or str(row.get("updated_at") or "").startswith(stamp)]
        from trading_analysis.option_history import OptionHistory
        from trading_analysis.notifications.outbox import AlertOutbox
        start = datetime.combine(selected, datetime.min.time(), ZoneInfo("Asia/Kolkata"))
        history = OptionHistory(self.nifty.trade_repository.db_path)
        return {"schema_version": 2, "date": stamp, "timezone": "Asia/Kolkata", "truncated": False,
                "live_quotes": LIVE_QUOTES.status(),
                "delivery": AlertOutbox(self.nifty.trade_repository.db_path).history(stamp),
                "option_observations": {symbol: history.load(symbol, start, start + timedelta(days=1) - timedelta(microseconds=1))
                                        for symbol in ("NIFTY", "BANKNIFTY", "SENSEX")},
                "generated_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds"),
                "note": "Read-only spot-signal diagnostics; no broker tokens or option orders.",
                "data_service": dict(self.indexes._data_status),
                "NIFTY": {"jobs": jobs, "alerts": alerts, "trades": trades},
                "BANKNIFTY": self.indexes.repository.diagnostics("BANKNIFTY", stamp),
                "SENSEX": self.indexes.repository.diagnostics("SENSEX", stamp)}

    @staticmethod
    def _nifty_job(job: dict[str, Any]) -> dict[str, Any]:
        result = job.get("result") or {}
        if job.get("job_name") == "run_nifty_opportunity_scan":
            detail = {"horizons": result.get("horizons"), "alerts_created": result.get("alerts_created"),
                      "telegram": result.get("telegram"), "warnings": result.get("warnings"), "errors": result.get("errors")}
        elif job.get("job_name") == "run_nifty_exit_scan":
            detail = {"open_checked": result.get("open_checked"), "trades_closed": result.get("trades_closed"),
                      "telegram": result.get("telegram")}
        elif job.get("job_name") == "update_nifty_candles":
            detail = {"candle_db": result.get("candle_db"), "warnings": result.get("warnings"),
                      "errors": result.get("errors")}
        else:
            detail = {key: result.get(key) for key in ("option_snapshot_id", "saved_rows", "atm_iv", "iv_rank", "deleted", "errors")
                      if key in result}
        return {"job_name": job.get("job_name"), "status": job.get("status"),
                "started_at": job.get("started_at"), "finished_at": job.get("finished_at"),
                "duration_ms": job.get("duration_ms"), "error": job.get("error"), "detail": detail}
