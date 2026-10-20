"""Independent MCX refresh/check workers sharing the broker's rate limiter."""
from copy import deepcopy
from datetime import datetime, timedelta
import csv
import io
import json
from pathlib import Path
import threading
import time
import zipfile

from trading_analysis import diagnostics
from trading_analysis.brokers.zerodha import load_instruments_csv, write_instruments_csv, merge_candles_csv, write_candles_csv
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.live_timing import IST, as_ist
from trading_analysis.notifications.telegram import TelegramNotifier
from trading_analysis.trade_context import result_mark
from .repository import McxRepository, date_range, performance
from .rules import (ROOTS, VERSION, RISK, CALENDAR_URL, expected_bar, closed_rows, market_window, market_seconds_between,
                    select_contracts, quote_read, technical_read, entry_check, trade_from_signal, exit_reason)


class McxSwingScanner:
    def __init__(self, path="data/db/mcx_swing.db", data_dir="data/raw/mcx", client_factory=None):
        self.repository = McxRepository(path)
        self.data_dir = Path(data_dir)
        self.client_factory = client_factory
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads = []
        self._sources = {}
        self._quotes = {}
        self._attempted = {}
        self._master_day = None
        self._last_tick = None
        self.contracts = self.repository.state("contracts", {})
        self.health = self.repository.state("health", {})
        self.state = {**self.repository.state("monitor", {}), "running": False, "phase": "stopped",
                      "current": "", "completed": 0, "total": 0, "next_check": None}
        self.settings = {"telegram_enabled": False}
        self.decisions = {}
        for c in self.contracts.values():
            for frame in ("day", "60minute"):
                path = self._path(c, frame)
                if path.exists():
                    try:
                        self._sources.setdefault(c["symbol"], {})[frame] = self._read_cache(c, frame)
                    except (ValueError, OSError):
                        self.health.setdefault(c["symbol"]+":"+frame, {})["error"] = "Cached candles could not be read; download again"
        self.repository.interrupt_open("server_restart_or_monitor_not_running")

    def _client(self):
        if self.client_factory:
            return self.client_factory()
        from trading_analysis.web_services import _zerodha_client
        return _zerodha_client()

    def _save_monitor(self):
        self.repository.save_state("monitor", self.state)

    def start(self, settings=None, download_only=False):
        settings = settings or {}
        if set(settings) - {"telegram_enabled"} or type(settings.get("telegram_enabled", False)) is not bool:
            raise ValueError("Invalid MCX settings")
        if settings.get("telegram_enabled") and not TelegramNotifier.from_env("MCX_").configured():
            raise ValueError("Configure MCX_TELEGRAM_BOT_TOKEN and MCX_TELEGRAM_CHAT_ID first")
        with self._lock:
            if any(t.is_alive() for t in self._threads):
                raise ValueError("MCX worker already running or stopping")
            self.settings = {"telegram_enabled": settings.get("telegram_enabled", False)}
            self._stop.clear()
            self._master_day = None
            self._attempted.clear()
            now = datetime.now(IST)
            self.state.update(running=not download_only, phase="downloading" if download_only else "starting",
                              started_at=now.isoformat(), stopped_at=None, error=None, current="MCX instrument master")
            self.repository.event("download_started" if download_only else "monitor_started", now, settings=self.settings)
            self._save_monitor()
            self._threads = [threading.Thread(target=self._refresh_loop, args=(download_only,), daemon=True, name="mcx-candles")]
            if not download_only:
                self._threads.append(threading.Thread(target=self._checker_loop, daemon=True, name="mcx-checker"))
            for worker in self._threads:
                worker.start()
        return self.status()

    def stop(self):
        with self._lock:
            self._stop.set()
            self.state.update(running=False, phase="stopping" if any(t.is_alive() for t in self._threads) else "stopped",
                              stopped_at=datetime.now(IST).isoformat(), next_check=None)
            self.repository.interrupt_open("monitor_stopped")
            self.repository.event("monitor_stop_requested", datetime.now(IST))
            self._save_monitor()
        return self.status()

    def _error(self, scope, exc):
        text = str(diagnostics.clean(str(exc)))
        with self._lock:
            self.state["error"] = f"{scope}: {text}"
        self.repository.event("error", datetime.now(IST), scope=scope, error=text)
        diagnostics.record("mcx_error", area="mcx", status="failed", scope=scope, error=text)

    def _refresh_loop(self, once=False):
        try:
            while not self._stop.is_set():
                try:
                    self.refresh(archive=once)
                except Exception as exc:
                    self._error("refresh", exc)
                if once or self._stop.wait(30):
                    break
        finally:
            with self._lock:
                self.state["current"] = ""
                if once:
                    self.state.update(phase="stopped" if self._stop.is_set() else "download_complete_with_errors" if self.state.get("error") else "download_complete",
                                      stopped_at=datetime.now(IST).isoformat())
                self._save_monitor()

    def _checker_loop(self):
        try:
            while not self._stop.is_set():
                try:
                    self.cycle()
                except Exception as exc:
                    self._error("checker", exc)
                    self.repository.interrupt_open("checker_error")
                with self._lock:
                    self.state["next_check"] = (datetime.now(IST) + timedelta(seconds=20)).isoformat()
                if self._stop.wait(20):
                    break
        finally:
            with self._lock:
                self.state.update(running=False, phase="stopped", next_check=None)
                self._save_monitor()

    def _path(self, contract, frame):
        return self.data_dir / contract["symbol"] / f"{frame}.csv"

    def _read_cache(self, contract, frame):
        path = self._path(contract, frame)
        downloaded = self.health.get(contract["symbol"]+":"+frame, {}).get("last_success")
        # A forming bar from an older cache must not become 'closed' merely
        # because wall time advanced. Only verified post-close data is admissible.
        return closed_rows(load_candles(path), frame, as_ist(downloaded)) if path.exists() and downloaded else []

    def _targets(self):
        with self._lock:
            targets = {c["symbol"]: dict(c) for c in self.contracts.values()}
        for trade in self.repository.trades(open_only=True):
            targets[trade["symbol"]] = {k: trade[k] for k in ("root", "symbol", "expiry", "token", "tick_size")}
        return list(targets.values())

    def refresh(self, now=None, archive=False):
        now = now or datetime.now(IST)
        client = self._client()
        if self._master_day != now.date():
            path = self.data_dir / "instruments_MCX.csv"
            if path.exists() and datetime.fromtimestamp(path.stat().st_mtime, IST).date() == now.date():
                master = load_instruments_csv(path)
            else:
                master = client.instruments("MCX")
                if not master or not all({"exchange", "tradingsymbol", "instrument_token", "expiry"} <= r.keys() for r in master):
                    raise ValueError("Invalid MCX instrument master")
                if self._stop.is_set():
                    return
                write_instruments_csv(path, master)
                write_instruments_csv(self.data_dir / "instruments" / f"MCX-{now.date()}.csv", master)
            contracts = select_contracts(master, now)
            if self._stop.is_set():
                return
            with self._lock:
                self.contracts = contracts
                self._master_day = now.date()
                self.repository.save_state("contracts", contracts)
            self.repository.event("contracts_selected", now, contracts=contracts)
        targets = self._targets()
        with self._lock:
            self.state.update(completed=0, total=len(targets)*2)
        for contract in targets:
            for frame, days in (("day", 400), ("60minute", 90)):
                if self._stop.is_set():
                    return
                symbol, path = contract["symbol"], self._path(contract, frame)
                key = symbol + ":" + frame
                try:
                    with self._lock:
                        rows = self._sources.get(symbol, {}).get(frame)
                    if rows is None:
                        rows = self._read_cache(contract, frame)
                        with self._lock:
                            self._sources.setdefault(symbol, {})[frame] = rows
                    expected = expected_bar(frame, now)
                    completed = closed_rows(rows, frame, now)
                    previous = self._attempted.get(key)
                    ready = completed and as_ist(completed[-1].timestamp) == expected and len(completed) >= 60
                    if ready and not archive and key in self.health and self.health[key].get("last_success"):
                        continue
                    if previous and (now - previous).total_seconds() < 60:
                        continue
                    # Never look up expired tokens: they can be reassigned by the exchange.
                    if contract["expiry"] < now.date().isoformat():
                        raise ValueError("Expired contract; retained open trade needs manual reconciliation")
                    with self._lock:
                        self.state["current"] = f"{symbol} {frame}"
                        self.health[key] = {**self.health.get(key, {}), "status": "updating", "started_at": now.isoformat()}
                    self._attempted[key] = now
                    start = now - timedelta(days=days) if len(rows) < 60 else as_ist(rows[-1].timestamp) - timedelta(days=3)
                    pulled = client.historical_candles(contract["token"], frame, start, now, include_oi=True)
                    pulled = closed_rows(pulled, frame, now)
                    if not pulled:
                        raise ValueError("No completed candles returned")
                    if self._stop.is_set():
                        return
                    merged = {c.timestamp: c for c in rows}
                    merged.update({c.timestamp: c for c in pulled})
                    rows = [merged[t] for t in sorted(merged)]
                    write_candles_csv(path, rows)
                    with self._lock:
                        self._sources.setdefault(symbol, {})[frame] = rows
                        self.health[key].update(status="ready", last_success=datetime.now(IST).isoformat(),
                                                latest=as_ist(rows[-1].timestamp).isoformat(), bars=len(rows), error=None)
                    self.repository.event("candles_refreshed", datetime.now(IST), symbol=symbol, frame=frame,
                                          bars=len(rows), first=as_ist(rows[0].timestamp).isoformat(), latest=as_ist(rows[-1].timestamp).isoformat())
                except Exception as exc:
                    with self._lock:
                        h = self.health.setdefault(key, {})
                        h.update(status="failed", error=str(diagnostics.clean(str(exc))), failures=h.get("failures", 0)+1)
                    self._error(key, exc)
                finally:
                    with self._lock:
                        self.state["completed"] += 1
            if archive and not self._stop.is_set() and contract["expiry"] >= now.date().isoformat():
                try:
                    rows = client.historical_candles(contract["token"], "day", now-timedelta(days=730), now, include_oi=True, continuous=True)
                    if self._stop.is_set():
                        return
                    merge_candles_csv(self.data_dir / "continuous_context_only" / f"{contract['root']}.csv", rows)
                    self.repository.event("continuous_context_downloaded", now, root=contract["root"], bars=len(rows), price_use="context_only_not_trade_pnl")
                except Exception as exc:
                    self._error(contract["root"] + " continuous", exc)
        with self._lock:
            self.state.update(current="", last_refresh=datetime.now(IST).isoformat())
            self.repository.save_state("health", self.health)

    def cycle(self, now=None):
        now = now or datetime.now(IST)
        if not market_window(now):
            with self._lock:
                self.state["phase"] = "waiting_mcx_session"
            return
        targets = self._targets()
        if not targets:
            return
        started = time.monotonic()
        quotes = self._client().quotes(["MCX:" + c["symbol"] for c in targets if c["expiry"] >= now.date().isoformat()])
        observed = now if self.client_factory else datetime.now(IST)
        parsed = {c["symbol"]: quote_read(quotes.get("MCX:" + c["symbol"], {}), observed) for c in targets}
        with self._lock:
            if self._stop.is_set():
                return
            self._quotes = parsed
            sources = {s: dict(v) for s, v in self._sources.items()}
            selected = dict(self.contracts)
            self.state["phase"] = "checking"
        if self._last_tick and market_seconds_between(self._last_tick, observed) > 120:
            self.repository.interrupt_open("monitoring_gap_over_120_seconds")
        open_trades = self.repository.trades(open_only=True)
        if any(not parsed.get(t["symbol"], {}).get("fresh") for t in open_trades):
            self.repository.interrupt_open("open_contract_quote_unavailable")
        for trade in open_trades:
            try:
                quote = parsed.get(trade["symbol"], {})
                reason = exit_reason(trade, quote, sources.get(trade["symbol"], {}), observed)
                with self._lock:
                    if self._stop.is_set():
                        return
                    if reason:
                        self.repository.close(trade, quote, observed, reason)
                        self.repository.event("trade_closed", observed, trade_id=trade["id"], reason=reason)
            except Exception as exc:
                self._error(trade["symbol"] + " exit", exc)
        for root in ROOTS:
            contract = selected.get(root)
            if not contract:
                with self._lock:
                    self.decisions[root] = {"root": root, "reason": "no_contract_outside_expiry_buffer", "checked_at": observed.isoformat()}
                continue
            try:
                signal = technical_read(sources.get(contract["symbol"], {}), observed)
                quote = parsed.get(contract["symbol"], {})
                reason = entry_check(contract, signal, quote, observed)
                if self._master_day != observed.date():
                    reason = "instrument_master_not_refreshed_today"
                key = f"{VERSION}:{contract['symbol']}:{signal.get('bar_time', expected_bar('60minute', observed).isoformat())}"
                decision = {**signal, "id": key, "root": root, "symbol": contract["symbol"], "expiry": contract["expiry"],
                            "reason": reason, "quote": quote, "checked_at": observed.isoformat()}
                with self._lock:
                    if self._stop.is_set():
                        return
                    self.decisions[root] = decision
                    if self.repository.seen(key):
                        self.decisions[root]["reason"] = "closed_candle_already_processed"
                        continue
                    # Data and quote failures can recover within the five-minute window.
                    retryable = not signal.get("ready") or reason in {"quote_stale_or_missing", "instrument_master_not_refreshed_today"}
                    if not retryable:
                        trade = trade_from_signal(contract, signal, quote, observed, self.settings["telegram_enabled"]) if reason == "eligible" else None
                        if trade and min(trade["stop_level"], trade["target_level"]) <= 0:
                            trade = None
                            decision["reason"] = "invalid_risk_levels"
                        created = self.repository.record(decision, trade)
                        if created:
                            self.decisions[root]["reason"] = "entry_created"
                            self.repository.event("trade_opened", observed, trade_id=created["id"], symbol=created["symbol"])
            except Exception as exc:
                self._error(root, exc)
        with self._lock:
            self._last_tick = observed
            self.state.update(phase="idle", last_completed=observed.isoformat(), duration_seconds=round(time.monotonic()-started, 3))
            self._save_monitor()
        self.repository.event("cycle_completed", observed, duration_seconds=self.state["duration_seconds"],
                              decisions={r: d.get("reason") for r, d in self.decisions.items()})
        self.repository.outbox.start()

    def status(self, from_date=None, to_date=None):
        now = datetime.now(IST)
        start, end = date_range(from_date, to_date, now)
        with self._lock:
            state = deepcopy(self.state)
            state["busy"] = any(t.is_alive() for t in self._threads)
            if not state["busy"] and state["phase"] == "stopping":
                state["phase"] = "stopped"
            sources, contracts, quotes = dict(self._sources), deepcopy(self.contracts), deepcopy(self._quotes)
            health, decisions = deepcopy(self.health), deepcopy(self.decisions)
        freshness = []
        for quote in quotes.values():
            quote["fresh"] = bool(quote.get("fresh") and 0 <= (now-as_ist(quote["quote_time"])).total_seconds() <= RISK["max_quote_age_seconds"])
        for contract in self._targets():
            for frame in ("day", "60minute"):
                key = contract["symbol"] + ":" + frame
                h = health.get(key, {})
                rows = closed_rows(sources.get(contract["symbol"], {}).get(frame, []), frame, now)
                latest = as_ist(rows[-1].timestamp) if rows else None
                expected = expected_bar(frame, now)
                value = "fresh" if latest == expected else "stale" if latest or h.get("latest") else "missing"
                if state["busy"] and h.get("status") == "updating":
                    value = "updating"
                freshness.append({**h, "symbol": contract["symbol"], "frame": frame, "state": value,
                                  "latest_closed": latest.isoformat() if latest else None, "expected": expected.isoformat()})
        trades = self.repository.trades()
        selected_trades = []
        for t in trades:
            quote = quotes.get(t["symbol"], {})
            side_price = quote.get("bid" if t["direction"] == "bullish" else "ask")
            mark = {**quote, "price": side_price} if quote.get("fresh") else {}
            t["result"] = result_mark(t["entry_price"], t["direction"], t["status"], t.get("exit_price"), mark, now, t.get("exit_time"))
            t["open_seconds"] = (as_ist(t["exit_time"]) - as_ist(t["entry_time"])).total_seconds() if t.get("exit_time") else (now-as_ist(t["entry_time"])).total_seconds()
            if t["entry_time"][:10] <= end and (not t.get("exit_time") or t["exit_time"][:10] >= start):
                selected_trades.append(t)
        charts = {root: [{"time": as_ist(c.timestamp).isoformat(), "close": c.close} for c in
                         closed_rows(sources.get(contract["symbol"], {}).get("day", []), "day", now)[-60:]]
                  for root, contract in contracts.items()}
        metric_trades = [t for t in selected_trades if t["status"] == "open" or start <= t["exit_time"][:10] <= end]
        return {**state, "version": VERSION, "roots": ROOTS, "contracts": contracts, "quotes": quotes, "charts": charts,
                "freshness": freshness, "decisions": list(decisions.values()), "trades": selected_trades, "trade_history": trades,
                "metrics": performance(metric_trades), "metrics_by_root": {r: performance([t for t in metric_trades if t["root"] == r]) for r in ROOTS},
                "risk": RISK, "settings": dict(self.settings),
                "telegram_configured": TelegramNotifier.from_env("MCX_").configured(),
                "calendar_verified": now.year == 2026, "calendar_source": CALENDAR_URL,
                "market_window": market_window(now), "timezone": "Asia/Kolkata", "as_of": now.isoformat(),
                "from_date": start, "to_date": end,
                "events": self.repository.records("events", start, end)[:100],
                "delivery": [r for r in self.repository.outbox.history() if start <= r["created_at"][:10] <= end][:100]}

    def export(self, from_date=None, to_date=None):
        report = self.status(from_date, to_date)
        report.pop("trade_history", None)
        report["decisions"] = self.repository.records("decisions", report["from_date"], report["to_date"])
        report["events"] = self.repository.records("events", report["from_date"], report["to_date"])
        report["delivery"] = [r for r in self.repository.outbox.history() if report["from_date"] <= r["created_at"][:10] <= report["to_date"]]
        report["limitations"] = ["Paper futures bid/ask observations; no broker fills or orders.",
                                  "5 bps per side is an assumption, not actual brokerage or commodity taxes.",
                                  "Interrupted trades excluded from win rate. Contract rollover is not a profit event.",
                                  "India VIX is not an MCX volatility proxy; ATR% is stored instead."]
        return diagnostics.clean(report)

    def export_zip(self, from_date=None, to_date=None):
        report = self.export(from_date, to_date)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("mcx-review.json", json.dumps(report, default=str, ensure_ascii=True, indent=2))
            fields = ["id", "root", "symbol", "expiry", "direction", "status", "entry_time", "entry_price", "signal_time",
                      "exit_time", "exit_price", "open_seconds", "stop_level", "target_level", "net_r", "exit_reason",
                      "monitoring_interrupted", "performance_eligible", "version"]
            csv_file = io.StringIO(newline="")
            writer = csv.DictWriter(csv_file, fieldnames=fields)
            writer.writeheader()
            for t in report["trades"]:
                row = {k: t.get(k) for k in fields}
                row = {k: "'"+v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for k, v in row.items()}
                writer.writerow(row)
            archive.writestr("trades.csv", csv_file.getvalue())
            # Preserve exact source inputs for reproducible reviews, including older
            # expiries represented in the selected trade history.
            symbols = {c["symbol"] for c in report["contracts"].values()} | {t["symbol"] for t in report["trades"]}
            for symbol in sorted(symbols):
                if not symbol.isalnum():
                    continue
                for frame in ("day", "60minute"):
                    path = self.data_dir / symbol / f"{frame}.csv"
                    if path.exists():
                        archive.writestr(f"candles/{symbol}/{frame}.csv", path.read_bytes())
            archive.writestr("README.txt", "MCX swing paper audit. All timestamps IST.\n" + "\n".join(report["limitations"]))
        return buffer.getvalue()
