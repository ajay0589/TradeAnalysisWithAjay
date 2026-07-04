from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from trading_analysis.nifty.auto_scan_service import NiftyAutoScanService
from trading_analysis.scheduler.alerts import generate_nifty_alerts
from trading_analysis.scheduler.market_hours import is_market_hours
from trading_analysis.scheduler.runner import MarketScanScheduler
from trading_analysis.storage import MarketJobRepository, NiftyAlertRepository
from trading_analysis.web_app import ReusableThreadingHTTPServer, TradingRequestHandler


class NiftyAutoScanTests(unittest.TestCase):
    def test_market_hours_detection_for_weekday_open(self) -> None:
        now = datetime(2026, 7, 6, 9, 20, tzinfo=ZoneInfo("Asia/Kolkata"))

        self.assertTrue(is_market_hours(now))

    def test_market_hours_false_after_close(self) -> None:
        now = datetime(2026, 7, 6, 15, 31, tzinfo=ZoneInfo("Asia/Kolkata"))

        self.assertFalse(is_market_hours(now))

    def test_market_hours_false_on_weekend(self) -> None:
        now = datetime(2026, 7, 4, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

        self.assertFalse(is_market_hours(now))

    def test_alert_generated_for_high_score_bullish_candidate(self) -> None:
        alerts = generate_nifty_alerts(_context("bullish"), [_candidate("nifty_bull_call_spread", "bullish", 85)], min_score=70)

        self.assertTrue(alerts)
        self.assertEqual(alerts[0]["direction"], "bullish")
        self.assertEqual(alerts[0]["alert_type"], "strategy_candidate")

    def test_duplicate_alert_suppressed_within_15_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = NiftyAlertRepository(Path(tmp) / "auto.db")
            repo.create_alert(**_alert(score=80, severity="watch"))

            suppressed = repo.suppress_duplicate(
                "strategy_candidate",
                "nifty_bull_call_spread",
                "bullish",
                min_minutes=15,
                score=85,
                severity="watch",
            )

            self.assertTrue(suppressed)

    def test_duplicate_alert_allowed_when_score_improves_materially(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = NiftyAlertRepository(Path(tmp) / "auto.db")
            repo.create_alert(**_alert(score=70, severity="watch"))

            suppressed = repo.suppress_duplicate(
                "strategy_candidate",
                "nifty_bull_call_spread",
                "bullish",
                min_minutes=15,
                score=82,
                severity="watch",
            )

            self.assertFalse(suppressed)

    def test_market_job_start_finish_fail_stored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = MarketJobRepository(Path(tmp) / "jobs.db")
            job_id = repo.start_job("context", {"mode": "auto"})
            finished = repo.finish_job(job_id, {"ok": True})
            failed_id = repo.start_job("scan")
            failed = repo.fail_job(failed_id, "boom")

            self.assertEqual(finished["status"], "completed")
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(len(repo.latest_jobs()), 2)

    def test_scheduler_run_once_executes_jobs_in_order(self) -> None:
        fake = FakeJobs()
        scheduler = MarketScanScheduler(
            job_runner=fake,
            clock=lambda: datetime(2026, 7, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        )

        result = scheduler.run_once()

        self.assertTrue(result["ran"])
        self.assertEqual(fake.calls, ["candles", "option_chain", "iv", "context", "scan", "cleanup"])

    def test_api_status_alerts_and_acknowledge_return_json(self) -> None:
        class FakeAutoService:
            def status(self):
                return {"running": False, "market_hours": True, "recent_jobs": [], "recent_alerts": [], "active_alerts_count": 1}

            def recent_alerts(self, limit=50, active_only=False):
                return {"alerts": [{"id": 7, "title": "Test", "is_active": True}], "count": 1, "active_count": 1}

            def acknowledge_alert(self, alert_id):
                return {"alert": {"id": alert_id, "is_active": False}}

            def start(self):
                return self.status()

            def stop(self):
                return self.status()

            def run_once(self, force=False):
                return {"ran": True, "force": force, **self.status()}

        original = TradingRequestHandler.nifty_auto_service
        TradingRequestHandler.nifty_auto_service = FakeAutoService()
        server = ReusableThreadingHTTPServer(("127.0.0.1", 0), TradingRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            self.assertFalse(_http_json(f"{base}/api/nifty/auto/status")["running"])
            self.assertEqual(_http_json(f"{base}/api/nifty/alerts")["count"], 1)
            self.assertFalse(_http_json(f"{base}/api/nifty/alerts/7/ack", {})["alert"]["is_active"])
            self.assertTrue(_http_json(f"{base}/api/nifty/auto/run-once", {"force": True})["ran"])
        finally:
            server.shutdown()
            server.server_close()
            TradingRequestHandler.nifty_auto_service = original

    def test_auto_scan_service_recent_alerts_json_serializable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = NiftyAutoScanService(db_path=Path(tmp) / "service.db", scheduler=FakeScheduler())
            service.alert_repository.create_alert(**_alert(score=80, severity="watch"))

            payload = service.recent_alerts()

            json.dumps(payload)
            self.assertEqual(payload["count"], 1)


class FakeJobs:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def update_nifty_candles_job(self, refresh=False):
        self.calls.append("candles")
        return {"job": {"status": "completed"}, "result": {"refresh": refresh}}

    def update_nifty_option_chain_job(self, refresh=False):
        self.calls.append("option_chain")
        return {"job": {"status": "completed"}, "result": {"refresh": refresh}}

    def record_nifty_iv_job(self):
        self.calls.append("iv")
        return {"job": {"status": "completed"}, "result": {}}

    def run_nifty_context_job(self, mode="auto"):
        self.calls.append("context")
        return {"job": {"status": "completed"}, "result": {"mode": mode}}

    def run_nifty_opportunity_scan_job(self, mode="auto"):
        self.calls.append("scan")
        return {"job": {"status": "completed"}, "result": {"mode": mode}}

    def cleanup_market_jobs_job(self, days=7):
        self.calls.append("cleanup")
        return {"job": {"status": "completed"}, "result": {"days": days}}


class FakeScheduler:
    def start(self):
        return {"running": True, "market_hours": True}

    def stop(self):
        return {"running": False, "market_hours": True}

    def status(self):
        return {"running": False, "market_hours": True, "last_results": {}}

    def run_once(self, force=False):
        return {"ran": True, "running": False, "market_hours": True, "force": force}


def _context(bias: str) -> dict:
    return {
        "mode": "auto",
        "technical": {
            "spot": 24500,
            "bias_intraday": bias,
            "bias_swing": bias,
            "bias_positional": bias,
            "support_levels": [24350],
            "resistance_levels": [24600],
            "warnings": [],
        },
        "options": {
            "spot": 24500,
            "option_bias": bias,
            "selected_weekly_expiry": "2026-07-09",
            "support_by_oi": 24300,
            "resistance_by_oi": 24700,
            "warnings": [],
        },
        "iv": {"iv_regime": "normal", "atm_iv": 15, "iv_rank": 45, "iv_percentile": 50, "enough_history": True},
    }


def _candidate(strategy_id: str, direction: str, score: int) -> dict:
    return {
        "strategy_id": strategy_id,
        "label": strategy_id.replace("_", " ").title(),
        "required_view": direction,
        "structure": "directional",
        "suitability_score": score,
        "confidence": "high",
        "expiry_plan": "weekly",
        "reasons": ["Technical and option context align."],
        "risks": ["Requires confirmation."],
    }


def _alert(score: int, severity: str) -> dict:
    return {
        "alert_type": "strategy_candidate",
        "mode": "auto",
        "severity": severity,
        "title": "Bullish spread candidate",
        "message": "NIFTY bullish setup candidate; wait for confirmation.",
        "strategy_id": "nifty_bull_call_spread",
        "direction": "bullish",
        "score": score,
        "confidence": "high",
        "reasons": ["Context alignment"],
        "risks": ["Invalidation can change"],
    }


def _http_json(url: str, payload: dict | None = None) -> dict:
    if payload is None:
        with urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
