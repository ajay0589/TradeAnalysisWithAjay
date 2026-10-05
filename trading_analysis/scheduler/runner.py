from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from trading_analysis.scheduler.jobs import NiftyMarketJobs
from trading_analysis.scheduler.market_hours import is_market_hours, is_scan_window, next_market_open


DEFAULT_INTERVALS = {
    "candles": 60,
    "option_chain": 180,
    "iv_snapshot": 300,
    "opportunity_scan": 60,
    "trade_exit": 60,
    "cleanup": 1800,
}


class MarketScanScheduler:
    def __init__(
        self,
        job_runner: NiftyMarketJobs | Any | None = None,
        intervals: dict[str, int] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.job_runner = job_runner or NiftyMarketJobs()
        self.intervals = dict(DEFAULT_INTERVALS)
        if intervals:
            self.configure(intervals)
        self.clock = clock or datetime.now
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._active_jobs: set[str] = set()
        self._last_started: dict[str, float] = {}
        self._last_started_at: dict[str, datetime] = {}
        self._last_results: dict[str, dict[str, Any]] = {}
        self._errors: list[str] = []
        self._started_at: str | None = None
        self._stopped_at: str | None = None
        self._last_cycle_started_at: str | None = None
        self._last_cycle_completed_at: str | None = None

    def start(self) -> dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return self.status()
        self._stop_event.clear()
        self._started_at = self.clock().isoformat(timespec="seconds")
        self._stopped_at = None
        self._thread = threading.Thread(target=self._loop, name="nifty-market-scan", daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._stopped_at = self.clock().isoformat(timespec="seconds")
        return self.status()

    def configure(self, intervals: dict[str, int]) -> None:
        for name, seconds in intervals.items():
            if name in DEFAULT_INTERVALS:
                self.intervals[name] = max(1, int(seconds))

    def status(self) -> dict[str, Any]:
        now = self.clock()
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "market_hours": is_market_hours(now),
            "scan_window": is_scan_window(now),
            "next_market_open": next_market_open(now),
            "intervals": dict(self.intervals),
            "active_jobs": sorted(self._active_jobs),
            "last_results": self._last_results,
            "last_job_run": _latest_job_time(self._last_results),
            "started_at": self._started_at,
            "stopped_at": self._stopped_at,
            "last_cycle_started_at": self._last_cycle_started_at,
            "last_cycle_completed_at": self._last_cycle_completed_at,
            "next_runs": {
                name: (started + timedelta(seconds=self.intervals[name])).isoformat(timespec="seconds")
                for name, started in self._last_started_at.items()
                if name in self.intervals
            },
            "errors": list(self._errors[-10:]),
        }

    def run_once(self, force: bool = False) -> dict[str, Any]:
        now = self.clock()
        scan_open = is_scan_window(now)
        if not force and not scan_open:
            return {
                "ran": False,
                "reason": "outside_market_hours",
                "market_hours": False,
                "next_market_open": next_market_open(now),
                "results": {},
            }
        results: dict[str, Any] = {}
        self._last_cycle_started_at = now.isoformat(timespec="seconds")
        for name in ("candles", "option_chain", "iv_snapshot", "trade_exit", "opportunity_scan", "cleanup"):
            if not scan_open and name in {"trade_exit", "opportunity_scan"}:
                results[name] = {"status": "skipped", "reason": "outside_market_hours"}
            else:
                results[name] = self._run_job(name)
        self._last_cycle_completed_at = self.clock().isoformat(timespec="seconds")
        return {"ran": True, "market_hours": is_market_hours(now), "scan_window": scan_open, "results": results}

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if is_scan_window(self.clock()):
                    self._run_due_jobs()
                time.sleep(1)
            except Exception as exc:
                self._errors.append(str(exc))
                time.sleep(2)

    def _run_due_jobs(self) -> None:
        current = time.monotonic()
        for name in ("candles", "trade_exit", "opportunity_scan", "option_chain", "iv_snapshot", "cleanup"):
            last = self._last_started.get(name, 0.0)
            if current - last >= self.intervals[name]:
                self._last_started[name] = current
                self._last_started_at[name] = self.clock()
                threading.Thread(
                    target=self._run_job,
                    args=(name,),
                    name=f"nifty-{name}",
                    daemon=True,
                ).start()

    def _run_job(self, name: str) -> dict[str, Any]:
        with self._lock:
            if name in self._active_jobs:
                return {"status": "skipped", "reason": "already_running"}
            self._active_jobs.add(name)
        try:
            started_at = self.clock().isoformat(timespec="seconds")
            result = self._call_job(name)
            self._last_results[name] = {
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "status": _result_status(result),
                "result": result,
            }
            if result.get("error"):
                self._errors.append(str(result.get("error")))
            return result
        except Exception as exc:
            error = {"status": "failed", "error": str(exc)}
            self._errors.append(str(exc))
            self._last_results[name] = {
                "started_at": self.clock().isoformat(timespec="seconds"),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "status": "failed",
                "result": error,
            }
            return error
        finally:
            with self._lock:
                self._active_jobs.discard(name)

    def _call_job(self, name: str) -> dict[str, Any]:
        if name == "candles":
            return self.job_runner.update_nifty_candles_job(refresh=True)
        if name == "option_chain":
            return self.job_runner.update_nifty_option_chain_job(refresh=True)
        if name == "iv_snapshot":
            return self.job_runner.record_nifty_iv_job()
        if name == "opportunity_scan":
            return self.job_runner.run_nifty_opportunity_scan_job(mode="auto")
        if name == "trade_exit":
            return self.job_runner.run_nifty_exit_scan_job()
        if name == "cleanup":
            return self.job_runner.cleanup_market_jobs_job(days=7)
        raise ValueError(f"Unknown scheduler job: {name}")


def _result_status(result: dict[str, Any]) -> str:
    job = result.get("job") if isinstance(result, dict) else None
    return str((job or {}).get("status") or result.get("status") or ("failed" if result.get("error") else "completed"))


def _latest_job_time(results: dict[str, dict[str, Any]]) -> str | None:
    values = [str(row.get("finished_at")) for row in results.values() if row.get("finished_at")]
    return max(values) if values else None
