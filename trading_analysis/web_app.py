from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from trading_analysis.web_services import AnalysisService


ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "web"


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class TradingRequestHandler(BaseHTTPRequestHandler):
    service = AnalysisService()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
            elif parsed.path == "/app.css":
                self._send_file(WEB_ROOT / "app.css", "text/css; charset=utf-8")
            elif parsed.path == "/app.js":
                self._send_file(WEB_ROOT / "app.js", "application/javascript; charset=utf-8")
            elif parsed.path == "/api/health":
                self._send_json({"status": "ok"})
            elif parsed.path == "/api/symbols":
                self._send_json(self.service.symbols())
            elif parsed.path == "/api/zerodha/status":
                self._send_json(self.service.zerodha_status())
            elif parsed.path == "/api/zerodha/login-url":
                self._send_json(self.service.zerodha_login_url())
            elif parsed.path == "/api/job":
                params = parse_qs(parsed.query)
                self._send_json(self.service.job_status(_required(params, "job_id")))
            elif parsed.path == "/api/sector-map/status":
                self._send_json(self.service.sector_map_status())
            elif parsed.path == "/api/fii-dii":
                self._send_json(self.service.fii_dii_activity(refresh=False))
            elif parsed.path == "/api/option-expiries":
                params = parse_qs(parsed.query)
                self._send_json(self.service.option_expiries(_required(params, "symbol")))
            elif parsed.path == "/api/option-snapshots":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.service.option_snapshots(
                        _required(params, "symbol"),
                        expiry=params.get("expiry", [None])[0] or None,
                    )
                )
            elif parsed.path == "/api/analyze":
                params = parse_qs(parsed.query)
                symbol = _required(params, "symbol")
                include_chain = params.get("option_chain", ["false"])[0].lower() == "true"
                previous_snapshot = params.get("previous_snapshot", [None])[0] or None
                strikes_around = int(params.get("strikes_around", ["10"])[0])
                expiry = params.get("expiry", [None])[0] or None
                all_strikes = params.get("all_strikes", ["false"])[0].lower() == "true"
                timeframe = params.get("timeframe", ["day"])[0]
                from_date = params.get("from_date", [None])[0] or None
                to_date = params.get("to_date", [None])[0] or None
                days = _optional_int(params.get("days", [None])[0])
                refresh = params.get("refresh", ["false"])[0].lower() == "true"
                self._send_json(
                    self.service.analyze_symbol(
                        symbol,
                        include_option_chain=include_chain,
                        previous_snapshot=previous_snapshot,
                        strikes_around=strikes_around,
                        expiry=expiry,
                        all_strikes=all_strikes,
                        timeframe=timeframe,
                        from_date=from_date,
                        to_date=to_date,
                        days=days,
                        refresh=refresh,
                    )
                )
            elif parsed.path == "/api/scan":
                params = parse_qs(parsed.query)
                scan_type = params.get("type", ["bullish"])[0]
                limit = _optional_limit(params.get("limit", ["all"])[0])
                self._send_json(
                    self.service.scan(
                        scan_type,
                        limit=limit,
                        timeframe=params.get("timeframe", ["day"])[0],
                        from_date=params.get("from_date", [None])[0] or None,
                        to_date=params.get("to_date", [None])[0] or None,
                        days=_optional_int(params.get("days", [None])[0]),
                        include_option_chain=params.get("option_chain", ["false"])[0].lower() == "true",
                        option_chain_limit=_optional_int(params.get("option_chain_limit", [None])[0]) or 5,
                        expiry=params.get("expiry", [None])[0] or None,
                        strikes_around=_optional_int(params.get("strikes_around", [None])[0]) or 10,
                    )
                )
            elif parsed.path == "/api/scan-opportunities":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.service.scan_opportunities(
                        opportunity_type=params.get("type", ["all"])[0],
                        direction=params.get("direction", [None])[0] or None,
                        timeframe=params.get("timeframe", ["day"])[0],
                        from_date=params.get("from_date", [None])[0] or None,
                        to_date=params.get("to_date", [None])[0] or None,
                        days=_optional_int(params.get("days", [None])[0]),
                        limit=_optional_limit(params.get("limit", ["50"])[0]),
                    )
                )
            elif parsed.path == "/api/krishna-setup-scan":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.service.scan_krishna_setup(
                        days=_optional_int(params.get("days", [None])[0]) or 365,
                        from_date=params.get("from_date", [None])[0] or None,
                        to_date=params.get("to_date", [None])[0] or None,
                        limit=_optional_limit(params.get("limit", ["50"])[0]),
                    )
                )
            elif parsed.path == "/api/krishna-setup-backtest":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.service.backtest_krishna_setup(
                        symbol=params.get("symbol", [None])[0] or None,
                        days=_optional_int(params.get("days", [None])[0]) or 730,
                        from_date=params.get("from_date", [None])[0] or None,
                        to_date=params.get("to_date", [None])[0] or None,
                        holding_days=_optional_int(params.get("holding_days", [None])[0]) or 10,
                        limit_symbols=_optional_limit(params.get("limit_symbols", ["50"])[0]),
                    )
                )
            else:
                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/zerodha/access-token":
                payload = self._read_json()
                request_token = str(payload.get("request_token") or "").strip()
                if not request_token:
                    raise ValueError("Paste the redirected Zerodha URL or request_token.")
                self._send_json(self.service.update_zerodha_access_token(request_token))
            elif parsed.path == "/api/bulk-candles":
                payload = self._read_json()
                self._send_json(
                    self.service.start_bulk_candle_download(
                        timeframes=list(payload.get("timeframes") or []),
                        days=_optional_int(payload.get("days")),
                        from_date=payload.get("from_date") or None,
                        to_date=payload.get("to_date") or None,
                        limit=_optional_int(payload.get("limit")),
                        sleep_seconds=float(payload.get("sleep_seconds") or 0.35),
                    )
                )
            elif parsed.path == "/api/sector-map/from-csv":
                payload = self._read_json()
                self._send_json(
                    self.service.generate_sector_map_from_csv_text(
                        csv_text=str(payload.get("csv_text") or ""),
                        include_all=bool(payload.get("include_all")),
                    )
                )
            elif parsed.path == "/api/fii-dii/refresh":
                self._send_json(self.service.fii_dii_activity(refresh=True))
            elif parsed.path == "/api/export-report":
                self._send_json(self.service.export_report(self._read_json()))
            elif parsed.path == "/api/option-chain-monitor/start":
                payload = self._read_json()
                raw_symbols = payload.get("symbols") or []
                if isinstance(raw_symbols, str):
                    symbols = [part.strip() for part in raw_symbols.split(",")]
                else:
                    symbols = [str(part).strip() for part in raw_symbols]
                self._send_json(
                    self.service.start_option_chain_monitor(
                        symbols=symbols,
                        expiry=payload.get("expiry") or None,
                        interval_minutes=_optional_int(payload.get("interval_minutes")) or 15,
                        strikes_around=_optional_int(payload.get("strikes_around")) or 10,
                        all_strikes=bool(payload.get("all_strikes")),
                        max_snapshots=_optional_int(payload.get("max_snapshots")) or 5,
                        run_once=bool(payload.get("run_once")),
                    )
                )
            elif parsed.path == "/api/option-chain-monitor/stop":
                payload = self._read_json()
                self._send_json(self.service.stop_job(str(payload.get("job_id") or "")))
            else:
                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args) -> None:
        return

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self._send_json({"error": "File not found"}, status=HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body or "{}")


def _required(params: dict[str, list[str]], name: str) -> str:
    value = params.get(name, [""])[0].strip()
    if not value:
        raise ValueError(f"Missing required parameter: {name}")
    return value


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_limit(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    if cleaned in {"", "all"}:
        return None
    return int(cleaned)


def main() -> None:
    parser = argparse.ArgumentParser(description="Trading analysis web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ReusableThreadingHTTPServer((args.host, args.port), TradingRequestHandler)
    print(f"Trading analysis UI running at http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
