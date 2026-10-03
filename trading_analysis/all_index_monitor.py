from __future__ import annotations

from typing import Any

from trading_analysis.index_scanner import IndexScannerService
from trading_analysis.instrument_master_service import InstrumentMasterService
from trading_analysis.nifty.auto_scan_service import NiftyAutoScanService
from trading_analysis.notifications.telegram import TelegramNotifier


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
        scanners = {"NIFTY": {"running": nifty["running"], "phase": ", ".join(nifty["active_jobs"]) or "waiting",
                               "started_at": nifty["started_at"], "stopped_at": nifty["stopped_at"],
                               "last_cycle_at": nifty["last_cycle_completed_at"],
                               "telegram_configured": TelegramNotifier.from_env("NIFTY_").configured(),
                               "errors": nifty["errors"]},
                    "BANKNIFTY": self._index_row(bank), "SENSEX": self._index_row(sensex)}
        return {"all_running": all((nifty["running"], bank["running"], sensex["running"])),
                "any_running": any((nifty["running"], bank["running"], sensex["running"])),
                "masters_ready": self.masters.status()["ready_for_all"],
                "unconfigured_telegram": [symbol for symbol, row in scanners.items() if not row["telegram_configured"]],
                "scanners": scanners}

    @staticmethod
    def _index_row(state: dict[str, Any]) -> dict[str, Any]:
        return {"running": state["running"], "phase": state["phase"],
                "started_at": state["started_at"], "stopped_at": state["stopped_at"],
                "last_cycle_at": state["last_cycle_at"],
                "telegram_configured": bool(state["telegram_destination"]),
                "errors": state["errors"]}

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
