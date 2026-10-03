from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from trading_analysis.brokers.zerodha import write_instruments_csv
from trading_analysis.web_services import AnalysisService, _zerodha_client


IST = ZoneInfo("Asia/Kolkata")
EXCHANGES = ("NSE", "NFO", "BSE", "BFO")
MAX_MASTER_AGE = timedelta(days=7)


class InstrumentMasterService:
    def __init__(self, analysis_service: AnalysisService | None = None,
                 fetcher: Callable[[str], list[dict[str, str]]] | None = None) -> None:
        analysis = analysis_service or AnalysisService()
        self.paths = {
            "NSE": Path(analysis.nse_instruments_path),
            "NFO": Path(analysis.nfo_instruments_path),
            "BSE": Path(analysis.bse_instruments_path),
            "BFO": Path(analysis.nfo_instruments_path).with_name("instruments_BFO.csv"),
        }
        self.fetcher = fetcher or (lambda exchange: _zerodha_client().instruments(exchange))
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {"running": False, "current_exchange": None,
                                       "started_at": None, "completed_at": None,
                                       "results": {}, "errors": {}}

    def status(self) -> dict[str, Any]:
        now = datetime.now(IST)
        masters = {}
        for exchange, path in self.paths.items():
            if not path.exists():
                masters[exchange] = {"state": "missing", "path": str(path), "updated_at": None, "size_bytes": 0}
                continue
            updated = datetime.fromtimestamp(path.stat().st_mtime, tz=IST)
            state = "fresh" if path.stat().st_size > 0 and now - updated <= MAX_MASTER_AGE else "stale"
            masters[exchange] = {"state": state, "path": str(path), "updated_at": updated.isoformat(timespec="seconds"),
                                 "size_bytes": path.stat().st_size}
        with self._lock:
            job = dict(self._state)
            job["results"] = dict(self._state["results"])
            job["errors"] = dict(self._state["errors"])
        return {"masters": masters, "ready_for_all": all(row["state"] == "fresh" for row in masters.values()),
                "job": job}

    def start_refresh(self) -> dict[str, Any]:
        with self._lock:
            if not (self._thread and self._thread.is_alive()):
                self._state = {"running": True, "current_exchange": None,
                               "started_at": datetime.now(IST).isoformat(timespec="seconds"),
                               "completed_at": None, "results": {}, "errors": {}}
                self._thread = threading.Thread(target=self._refresh_all, name="zerodha-instrument-masters", daemon=True)
                self._thread.start()
        return self.status()

    def _refresh_all(self) -> None:
        for exchange in EXCHANGES:
            with self._lock:
                self._state["current_exchange"] = exchange
            try:
                rows = self.fetcher(exchange)
                if not rows or not {"exchange", "tradingsymbol", "instrument_token"}.issubset(rows[0]):
                    raise ValueError(f"{exchange} instrument response was empty or incomplete")
                write_instruments_csv(self.paths[exchange], rows)
                with self._lock:
                    self._state["results"][exchange] = len(rows)
            except Exception as exc:
                with self._lock:
                    self._state["errors"][exchange] = str(exc)
        with self._lock:
            self._state["running"] = False
            self._state["current_exchange"] = None
            self._state["completed_at"] = datetime.now(IST).isoformat(timespec="seconds")
