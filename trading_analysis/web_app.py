from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from trading_analysis.nifty.auto_scan_service import NiftyAutoScanService
from trading_analysis.nifty.service import NiftyDeskService
from trading_analysis.index_scanner import IndexScannerService
from trading_analysis.instrument_master_service import InstrumentMasterService
from trading_analysis.all_index_monitor import AllIndexMonitor
from trading_analysis.web_services import AnalysisService


ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "web"


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class TradingRequestHandler(BaseHTTPRequestHandler):
    service = AnalysisService()
    nifty_service = NiftyDeskService(analysis_service=service)
    nifty_auto_service = NiftyAutoScanService(nifty_service=nifty_service)
    index_scanner_service = IndexScannerService(analysis_service=service)
    instrument_master_service = InstrumentMasterService(analysis_service=service)
    all_index_monitor = AllIndexMonitor(nifty_auto_service, index_scanner_service, instrument_master_service)

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
            elif parsed.path == "/api/strategies":
                self._send_json(self.service.strategies())
            elif parsed.path == "/api/strategy-info":
                params = parse_qs(parsed.query)
                self._send_json(self.service.strategy_info(_required(params, "strategy")))
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
            elif parsed.path == "/api/krishna-purple-touch-scan":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.service.scan_krishna_purple_touch(
                        purple_timeframe=params.get("purple_timeframe", ["week"])[0],
                        days=_optional_int(params.get("days", [None])[0]),
                        from_date=params.get("from_date", [None])[0] or None,
                        to_date=params.get("to_date", [None])[0] or None,
                        limit=_optional_limit(params.get("limit", ["50"])[0]),
                    )
                )
            elif parsed.path == "/api/krishna-purple-touch-alerts":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.service.krishna_purple_touch_alerts(
                        limit=_optional_int(params.get("limit", ["50"])[0]) or 50,
                        page_size=_optional_int(params.get("page_size", ["25"])[0]) or 25,
                        entry_page=_optional_int(params.get("entry_page", ["1"])[0]) or 1,
                        exit_page=_optional_int(params.get("exit_page", ["1"])[0]) or 1,
                        trade_page=_optional_int(params.get("trade_page", ["1"])[0]) or 1,
                        entry_profile=params.get("entry_profile", [None])[0] or None,
                        entry_kind=params.get("entry_kind", [None])[0] or None,
                        exit_profile=params.get("exit_profile", [None])[0] or None,
                        exit_kind=params.get("exit_kind", [None])[0] or None,
                        trade_profile=params.get("trade_profile", [None])[0] or None,
                        trade_kind=params.get("trade_kind", [None])[0] or None,
                        entry_status=params.get("entry_status", [None])[0] or None,
                        entry_symbol=params.get("entry_symbol", [None])[0] or None,
                        entry_from_date=params.get("entry_from", [None])[0] or None,
                        entry_to_date=params.get("entry_to", [None])[0] or None,
                        entry_sort=params.get("entry_sort", ["created_at"])[0],
                        entry_order=params.get("entry_order", ["desc"])[0],
                        exit_symbol=params.get("exit_symbol", [None])[0] or None,
                        exit_from_date=params.get("exit_from", [None])[0] or None,
                        exit_to_date=params.get("exit_to", [None])[0] or None,
                        exit_sort=params.get("exit_sort", ["created_at"])[0],
                        exit_order=params.get("exit_order", ["desc"])[0],
                        trade_status=params.get("trade_status", ["open"])[0] or None,
                        trade_symbol=params.get("trade_symbol", [None])[0] or None,
                        trade_from_date=params.get("trade_from", [None])[0] or None,
                        trade_to_date=params.get("trade_to", [None])[0] or None,
                        trade_sort=params.get("trade_sort", ["opened_at"])[0],
                        trade_order=params.get("trade_order", ["desc"])[0],
                    )
                )
            elif parsed.path == "/api/krishna-purple-monitor/status":
                self._send_json(self.service.purple_monitor_status())
            elif parsed.path == "/api/krishna-purple-setups":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.service.krishna_purple_setups(
                        page=_optional_int(params.get("page", ["1"])[0]) or 1,
                        page_size=_optional_int(params.get("page_size", ["25"])[0]) or 25,
                        profile=params.get("profile", [None])[0] or None,
                        status=params.get("status", [None])[0] or None,
                        symbol=params.get("symbol", [None])[0] or None,
                        early_status=params.get("early_status", [None])[0] or None,
                        final_status=params.get("final_status", [None])[0] or None,
                        sort_by=params.get("sort", ["updated_at"])[0],
                        sort_order=params.get("order", ["desc"])[0],
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
                        use_entry_trigger=params.get("entry_trigger", ["false"])[0].lower() == "true",
                        trigger_holding_bars=_optional_int(params.get("trigger_holding_bars", [None])[0]) or 6,
                        target_r_multiple=float(params.get("target_r_multiple", ["2"])[0] or 2),
                    )
                )
            elif parsed.path == "/api/backtest-strategy":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.service.backtest_strategy(
                        strategy_id=params.get("strategy", params.get("strategy_id", [""]))[0],
                        symbols=_split_symbols(params.get("symbols", [""])[0]),
                        timeframe=params.get("timeframe", ["day"])[0],
                        from_date=params.get("from_date", [None])[0] or None,
                        to_date=params.get("to_date", [None])[0] or None,
                        days=_optional_int(params.get("days", [None])[0]),
                        strategy_params=_optional_json(params.get("params", ["{}"])[0]),
                        backtest_params=_optional_json(params.get("backtest_params", ["{}"])[0]),
                        limit_symbols=_optional_limit(params.get("limit_symbols", ["50"])[0]),
                    )
                )
            elif parsed.path == "/api/nifty/context":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.nifty_service.nifty_context(
                        mode=params.get("mode", ["auto"])[0],
                        weekly_expiry=params.get("weekly_expiry", [None])[0] or None,
                        monthly_expiry=params.get("monthly_expiry", [None])[0] or None,
                        include_option_chain=params.get("include_option_chain", ["true"])[0].lower() == "true",
                        include_iv=params.get("include_iv", ["true"])[0].lower() == "true",
                        refresh=params.get("refresh", ["false"])[0].lower() == "true",
                        timeframe=params.get("timeframe", ["15minute"])[0],
                        days=_optional_int(params.get("days", [None])[0]) or 30,
                        to_date=params.get("to_date", [None])[0] or None,
                    )
                )
            elif parsed.path == "/api/nifty/auto/status":
                self._send_json(self.nifty_auto_service.status())
            elif parsed.path == "/api/index-scanners/status":
                params = parse_qs(parsed.query)
                self._send_json(self.index_scanner_service.status(params.get("symbol", ["BANKNIFTY"])[0]))
            elif parsed.path == "/api/instrument-masters/status":
                self._send_json(self.instrument_master_service.status())
            elif parsed.path == "/api/index-scanners/all/status":
                self._send_json(self.all_index_monitor.status())
            elif parsed.path == "/api/index-scanners/diagnostics":
                params = parse_qs(parsed.query)
                self._send_json(self.all_index_monitor.diagnostics(params.get("date", [None])[0]))
            elif parsed.path == "/api/nifty/data/latest":
                self._send_json(self.nifty_auto_service.latest_data())
            elif parsed.path == "/api/nifty/trades":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.nifty_auto_service.trades(
                        status=params.get("status", [None])[0] or None,
                        limit=_optional_int(params.get("limit", ["200"])[0]) or 200,
                    )
                )
            elif parsed.path == "/api/nifty/option-snapshots":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.nifty_auto_service.option_snapshots(
                        limit=_optional_int(params.get("limit", ["20"])[0]) or 20,
                        expiry=params.get("expiry", [None])[0] or None,
                    )
                )
            elif parsed.path.startswith("/api/nifty/option-snapshots/"):
                snapshot_id = _tail_id_from_path(parsed.path, "option-snapshots")
                self._send_json(self.nifty_auto_service.option_snapshot(snapshot_id))
            elif parsed.path == "/api/nifty/iv-history":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.nifty_auto_service.iv_history(
                        lookback_days=_optional_int(params.get("lookback_days", ["252"])[0]) or 252,
                    )
                )
            elif parsed.path == "/api/nifty/context-snapshots":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.nifty_auto_service.context_snapshots(
                        limit=_optional_int(params.get("limit", ["50"])[0]) or 50,
                    )
                )
            elif parsed.path.startswith("/api/nifty/context-snapshots/"):
                snapshot_id = _tail_id_from_path(parsed.path, "context-snapshots")
                self._send_json(self.nifty_auto_service.context_snapshot(snapshot_id))
            elif parsed.path == "/api/nifty/alerts/backtest":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.nifty_auto_service.alert_backtest(
                        timeframe=params.get("timeframe", ["15minute"])[0],
                        limit=_optional_int(params.get("limit", ["500"])[0]) or 500,
                        horizons=_optional_int_list(params.get("horizons", ["3,5,10,15"])[0]),
                        horizon=params.get("horizon", [None])[0] or None,
                        strategy_id=params.get("strategy_id", [None])[0] or None,
                        direction=params.get("direction", [None])[0] or None,
                        alert_type=params.get("alert_type", [None])[0] or None,
                    )
                )
            elif parsed.path.startswith("/api/nifty/alerts/") and parsed.path.endswith("/outcomes"):
                alert_id = _alert_id_from_path(parsed.path)
                self._send_json(self.nifty_auto_service.alert_outcomes(alert_id))
            elif parsed.path == "/api/nifty/alerts":
                params = parse_qs(parsed.query)
                self._send_json(
                    self.nifty_auto_service.recent_alerts(
                        limit=_optional_int(params.get("limit", ["50"])[0]) or 50,
                        active_only=params.get("active_only", ["false"])[0].lower() == "true",
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
            elif parsed.path == "/api/index-scanners/start":
                payload = self._read_json()
                self._send_json(self.index_scanner_service.start(
                    str(payload.get("symbol") or "BANKNIFTY"),
                    int(payload.get("interval_seconds") or 180),
                ))
            elif parsed.path == "/api/index-scanners/stop":
                payload = self._read_json()
                self._send_json(self.index_scanner_service.stop(str(payload.get("symbol") or "BANKNIFTY")))
            elif parsed.path == "/api/index-scanners/run-once":
                payload = self._read_json()
                self._send_json(self.index_scanner_service.run_once(
                    str(payload.get("symbol") or "BANKNIFTY"), refresh=bool(payload.get("refresh", True))))
            elif parsed.path == "/api/instrument-masters/refresh":
                self._read_json()
                if self.all_index_monitor.status()["any_running"]:
                    raise ValueError("Stop NIFTY, Bank Nifty, and Sensex monitors before refreshing instrument masters.")
                self._send_json(self.instrument_master_service.start_refresh())
            elif parsed.path == "/api/index-scanners/all/start":
                payload = self._read_json()
                self._send_json(self.all_index_monitor.start(
                    nifty_seconds=int(payload.get("nifty_seconds") or 60),
                    bank_seconds=int(payload.get("bank_seconds") or 180),
                    sensex_seconds=int(payload.get("sensex_seconds") or 180)))
            elif parsed.path == "/api/index-scanners/all/stop":
                self._read_json()
                self._send_json(self.all_index_monitor.stop())
            elif parsed.path == "/api/index-scanners/telegram-test":
                payload = self._read_json()
                self._send_json(self.all_index_monitor.test_telegram(str(payload.get("symbol") or "")))
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
            elif parsed.path == "/api/job/stop":
                payload = self._read_json()
                self._send_json(self.service.stop_job(str(payload.get("job_id") or "")))
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
            elif parsed.path == "/api/backtest-strategy":
                payload = self._read_json()
                symbols = payload.get("symbols")
                if isinstance(symbols, str):
                    symbols = _split_symbols(symbols)
                elif symbols is None:
                    symbols = None
                else:
                    symbols = [str(symbol).strip() for symbol in symbols if str(symbol).strip()]
                self._send_json(
                    self.service.backtest_strategy(
                        strategy_id=str(payload.get("strategy_id") or payload.get("strategy") or ""),
                        symbols=symbols,
                        timeframe=str(payload.get("timeframe") or "day"),
                        from_date=payload.get("from_date") or None,
                        to_date=payload.get("to_date") or None,
                        days=_optional_int(payload.get("days")),
                        strategy_params=payload.get("strategy_params") or payload.get("params") or {},
                        backtest_params=payload.get("backtest_params") or {},
                        limit_symbols=_optional_limit(str(payload.get("limit_symbols") or "50")),
                    )
                )
            elif parsed.path == "/api/krishna-purple-touch-live-scan":
                payload = self._read_json()
                self._send_json(
                    self.service.run_krishna_purple_manual_scan(
                        purple_timeframe=str(payload.get("purple_timeframe") or "all"),
                        days=_optional_int(payload.get("days")),
                        from_date=payload.get("from_date") or None,
                        to_date=payload.get("to_date") or None,
                        limit=_optional_limit(str(payload.get("limit") or "all")),
                        force=bool(payload.get("force")),
                        send_telegram=bool(payload.get("send_telegram", True)),
                    )
                )
            elif parsed.path == "/api/krishna-purple-touch-live-scan/cancel":
                self._read_json()
                self._send_json(self.service.cancel_krishna_purple_manual_scan())
            elif parsed.path == "/api/krishna-purple-monitor/start":
                payload = self._read_json()
                self._send_json(
                    self.service.start_purple_monitor(
                        intervals=dict(payload.get("intervals") or {}),
                        send_telegram=bool(payload.get("send_telegram", True)),
                        force=bool(payload.get("force")),
                    )
                )
            elif parsed.path == "/api/krishna-purple-monitor/stop":
                self._read_json()
                self._send_json(self.service.stop_purple_monitor())
            elif parsed.path == "/api/krishna-purple-entry-checker/run":
                payload = self._read_json()
                self._send_json(
                    self.service.check_krishna_purple_entries(
                        profiles=list(payload.get("profiles") or []),
                        send_telegram=bool(payload.get("send_telegram", True)),
                        force=bool(payload.get("force")),
                    )
                )
            elif parsed.path == "/api/krishna-purple-exit-checker/run":
                payload = self._read_json()
                self._send_json(
                    self.service.check_krishna_purple_exits(
                        profiles=list(payload.get("profiles") or []),
                        send_telegram=bool(payload.get("send_telegram", True)),
                        force=bool(payload.get("force")),
                    )
                )
            elif parsed.path == "/api/krishna-purple-touch-backtest":
                payload = self._read_json()
                backtest_params = dict(payload.get("params") or {})
                backtest_params["profiles"] = ["week"]
                raw_symbols = payload.get("symbols")
                if isinstance(raw_symbols, str):
                    symbols = _split_symbols(raw_symbols)
                elif raw_symbols:
                    symbols = [str(symbol).strip() for symbol in raw_symbols if str(symbol).strip()]
                else:
                    symbols = None
                self._send_json(
                    self.service.backtest_krishna_purple_touch(
                        symbols=symbols,
                        params=backtest_params,
                        limit_symbols=_optional_limit(str(payload.get("limit_symbols") or "20")),
                    )
                )
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
            elif parsed.path == "/api/nifty/strategy-suggestions":
                payload = self._read_json()
                self._send_json(
                    self.nifty_service.nifty_strategy_suggestions(
                        mode=str(payload.get("mode") or "auto"),
                        weekly_expiry=payload.get("weekly_expiry") or None,
                        monthly_expiry=payload.get("monthly_expiry") or None,
                        allowed_strategies=list(payload.get("allowed_strategies") or []),
                        risk_profile=str(payload.get("risk_profile") or "defined"),
                        refresh=bool(payload.get("refresh")),
                        to_date=payload.get("to_date") or None,
                    )
                )
            elif parsed.path == "/api/nifty/payoff":
                self._send_json(self.nifty_service.nifty_payoff(self._read_json()))
            elif parsed.path == "/api/nifty/backtest":
                self._send_json(self.nifty_service.nifty_backtest(self._read_json()))
            elif parsed.path == "/api/nifty/auto/start":
                payload = self._read_json()
                self._send_json(
                    self.nifty_auto_service.start(
                        scan_interval_seconds=_optional_int(payload.get("scan_interval_seconds")),
                    )
                )
            elif parsed.path == "/api/nifty/auto/stop":
                self._send_json(self.nifty_auto_service.stop())
            elif parsed.path == "/api/nifty/auto/run-once":
                payload = self._read_json()
                self._send_json(self.nifty_auto_service.run_once(force=bool(payload.get("force"))))
            elif parsed.path == "/api/nifty/scanner-backtest":
                payload = self._read_json()
                self._send_json(
                    self.nifty_auto_service.scanner_backtest(
                        horizon=str(payload.get("horizon") or "intraday"),
                        method=str(payload.get("method") or "scanner"),
                        strategy=str(payload.get("strategy") or "ema_pullback"),
                        from_date=payload.get("from_date") or None,
                        to_date=payload.get("to_date") or None,
                        direction=str(payload.get("direction") or "both"),
                        target_r_multiple=float(payload.get("target_r_multiple") or 2.0),
                        max_holding_bars=_optional_int(payload.get("max_holding_bars")),
                        cost_bps_per_side=float(payload.get("cost_bps_per_side") if payload.get("cost_bps_per_side") is not None else 2.0),
                    )
                )
            elif parsed.path.startswith("/api/nifty/alerts/") and parsed.path.endswith("/ack"):
                alert_id = _alert_id_from_path(parsed.path)
                self._send_json(self.nifty_auto_service.acknowledge_alert(alert_id))
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


def _split_symbols(value: str | None) -> list[str] | None:
    if not value or not value.strip():
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def _optional_json(value: str | None) -> dict:
    if not value or not value.strip():
        return {}
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ValueError("JSON parameter must be an object.")
    return loaded


def _optional_int_list(value: str | None) -> list[int] | None:
    if not value or not value.strip():
        return None
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def _alert_id_from_path(path: str) -> int:
    parts = [part for part in path.split("/") if part]
    if len(parts) < 4:
        raise ValueError("Missing alert id.")
    return int(parts[3])


def _tail_id_from_path(path: str, marker: str) -> int:
    parts = [part for part in path.split("/") if part]
    index = parts.index(marker)
    return int(parts[index + 1])


def main() -> None:
    parser = argparse.ArgumentParser(description="Trading analysis web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()

    server = ReusableThreadingHTTPServer((args.host, args.port), TradingRequestHandler)
    print(f"Trading analysis UI running at http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
