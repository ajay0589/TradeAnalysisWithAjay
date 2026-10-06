from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import threading
import time
import uuid
from functools import wraps
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlparse


IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent.parent
STARTED_AT = datetime.now(IST).isoformat(timespec="seconds")
INSTANCE_ID = uuid.uuid4().hex[:12]
_AREA = ContextVar("diagnostic_area", default="system")
_REQUEST = ContextVar("diagnostic_request", default=None)
_LOCK = threading.Lock()
_SENSITIVE = re.compile(r"token|secret|password|authorization|cookie|chat_id|api_key|request_token", re.I)
_TOKEN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_LOG_ERROR: str | None = None


def clean(value):
    if isinstance(value, dict):
        return {str(k): "[redacted]" if _SENSITIVE.search(str(k)) else clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, str):
        text = _TOKEN.sub("[redacted]", value)
        for name, secret in os.environ.items():
            if _SENSITIVE.search(name) and secret and len(secret) >= 6:
                text = text.replace(secret, "[redacted]")
        text = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[redacted]@", text)
        text = re.sub(r"(?i)((?:[\w]*token|api_key|secret|password)[\"']?\s*[:=]\s*[\"']?)[^&\s\"'},]+", r"\1[redacted]", text)
        return text[:3000]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return clean(str(value))


@contextmanager
def scope(area: str, request_id: str | None = None):
    area_token = _AREA.set(area)
    request_token = _REQUEST.set(request_id)
    try:
        yield
    finally:
        _AREA.reset(area_token)
        _REQUEST.reset(request_token)


def record(event: str, *, area: str | None = None, status: str = "ok", **detail) -> None:
    global _LOG_ERROR
    try:
        now = datetime.now(IST)
        row = {"timestamp": now.isoformat(timespec="milliseconds"), "instance_id": INSTANCE_ID,
               "pid": os.getpid(), "area": area or _AREA.get(), "request_id": _REQUEST.get(),
               "event": event, "status": status, **clean(detail)}
        folder = ROOT / "logs" / "diagnostics"
        path = folder / f"app-{now.date().isoformat()}-{os.getpid()}.jsonl"
        with _LOCK:
            folder.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 10 * 1024 * 1024:
                path.rename(path.with_name(f"{path.stem}-{time.time_ns()}.jsonl"))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, default=str) + "\n")
        _LOG_ERROR = None
    except Exception as exc:
        _LOG_ERROR = str(exc)


def runtime() -> dict:
    from trading_analysis.network import tls_info

    return {"service": "trading-analysis", "instance_id": INSTANCE_ID, "pid": os.getpid(),
            "started_at": STARTED_AT, "python": platform.python_version(), "executable": sys.executable,
            "project_root": str(ROOT), "code_version": CODE_VERSION, "tls": tls_info(),
            "logging_error": _LOG_ERROR, "launch_id": os.getenv("TRADING_SERVER_LAUNCH_ID")}


def export(day: str | None = None) -> dict:
    selected = date.fromisoformat(day) if day else datetime.now(IST).date()
    events = []
    malformed = 0
    with _LOCK:
        for path in sorted((ROOT / "logs" / "diagnostics").glob(f"app-{selected}-*.jsonl")):
            try:
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            events.append(json.loads(line))
                        except (ValueError, UnicodeError):
                            malformed += 1
            except FileNotFoundError:
                malformed += 1
    events.sort(key=lambda row: row.get("timestamp", ""))
    limit = 50000
    return {"schema_version": 2, "date": str(selected), "timezone": "Asia/Kolkata", "runtime": runtime(),
            "events": [clean(row) for row in events[-limit:]],
            "truncated_events": max(0, len(events) - limit), "incomplete_lines": malformed}


def area_for_path(path: str) -> str:
    if "krishna-purple" in path:
        return "purple"
    if "nifty" in path or "index-scanners" in path:
        return "indexes"
    if "krishna" in path:
        return "krishna"
    if "backtest" in path:
        return "backtest"
    if "scan" in path or "opportunities" in path:
        return "scans"
    if any(key in path for key in ("zerodha", "bulk", "instrument-master", "sector", "fii-dii", "job")):
        return "data"
    if "report" in path:
        return "reports"
    return "analyze"


def response_summary(payload) -> dict:
    if not isinstance(payload, dict):
        return {}
    keys = ("symbol", "status", "job_id", "total", "completed", "successes", "failures", "errors", "warnings",
            "phase", "error", "analyzed_symbols", "signal_count", "trade_count", "entries_created", "exits_created",
            "metrics", "technical", "indicator_suite")
    result = {key: payload[key] for key in keys if key in payload}
    if isinstance(payload.get("decision"), dict):
        decision = payload["decision"]
        result["decision"] = {key: decision.get(key) for key in ("symbol", "score", "bias", "direction", "confidence", "action", "reasons")}
    return result


def audit_http(fn):
    @wraps(fn)
    def wrapper(handler):
        path = urlparse(handler.path).path
        if not path.startswith("/api/") or path.startswith("/api/diagnostics"):
            return fn(handler)
        started = time.monotonic()
        request_id = uuid.uuid4().hex[:12]
        handler._diagnostic_response = {}
        handler._diagnostic_status = 200
        params = parse_qs(urlparse(handler.path).query)
        selected = {key: params[key] for key in ("symbol", "timeframe", "horizon", "days", "refresh", "mode") if key in params}
        with scope(area_for_path(path), request_id):
            record("request_started", method=handler.command, endpoint=path, parameters=selected)
            try:
                return fn(handler)
            except Exception as exc:
                handler._diagnostic_status = 500
                handler._diagnostic_response = {"error": str(exc)}
                raise
            finally:
                status = "failed" if handler._diagnostic_status >= 400 or handler._diagnostic_response.get("errors") or handler._diagnostic_response.get("status") == "failed" else "ok"
                record("request_finished", status=status, method=handler.command, endpoint=path,
                       http_status=handler._diagnostic_status, duration_ms=int((time.monotonic() - started) * 1000),
                       parameters=selected, result=handler._diagnostic_response)
    return wrapper


def background(area: str):
    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            started = time.monotonic()
            with scope(area):
                record("worker_started", worker=fn.__name__)
                try:
                    result = fn(*args, **kwargs)
                    record("worker_finished", worker=fn.__name__, result=response_summary(result),
                           duration_ms=int((time.monotonic() - started) * 1000))
                    return result
                except Exception as exc:
                    record("worker_finished", worker=fn.__name__, status="failed", error=str(exc),
                           duration_ms=int((time.monotonic() - started) * 1000))
                    raise
        return wrapper
    return decorate


def _code_version() -> str:
    digest = hashlib.sha256()
    for folder, suffixes in ((ROOT / "trading_analysis", {".py"}), (ROOT / "web", {".js", ".html", ".css"})):
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix in suffixes:
                digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
                digest.update(path.read_text(encoding="utf-8").encode("utf-8"))
    return digest.hexdigest()[:12]


CODE_VERSION = _code_version()
