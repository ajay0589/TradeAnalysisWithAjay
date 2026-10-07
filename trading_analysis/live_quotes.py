"""One read-only index quote stream shared by all scanners, with REST fallback."""
from datetime import datetime
from pathlib import Path
import threading
import time

from trading_analysis import diagnostics
from trading_analysis.live_timing import IST, as_ist, quote_is_fresh
from trading_analysis.scheduler.market_hours import is_scan_window, is_market_hours

KEYS = {"NIFTY": "NSE:NIFTY 50", "BANKNIFTY": "NSE:NIFTY BANK", "SENSEX": "BSE:SENSEX"}


class LiveQuoteService:
    def __init__(self):
        self._lock = threading.RLock()
        self._owners = set()
        self._quotes = {}
        self._thread = None
        self._wake = threading.Event()
        self._ticker = None
        self._tokens = {}
        self._error = None
        self._connected = False
        self._stream_attempted = False

    def acquire(self, owner):
        with self._lock:
            self._owners.add(owner)
            if not self._thread or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="index-live-quotes", daemon=True)
                self._thread.start()
        self._wake.set()

    def release(self, owner):
        with self._lock:
            self._owners.discard(owner)
        self._wake.set()

    def publish(self, symbol, price, quote_time, *, received_at=None, source="websocket"):
        now = received_at or datetime.now(IST)
        try:
            row = {"price": float(price), "quote_time": as_ist(quote_time).isoformat(),
                   "received_at": as_ist(now).isoformat(), "source": source}
        except (TypeError, ValueError, AttributeError):
            return
        with self._lock:
            old = self._quotes.get(symbol)
            if not old or row["quote_time"] >= old["quote_time"]:
                self._quotes[symbol] = row

    def get(self, symbol):
        with self._lock:
            return dict(self._quotes.get(symbol) or {})

    def status(self):
        now = datetime.now(IST)
        with self._lock:
            return {"running": bool(self._owners), "stream_connected": self._connected,
                    "mode": "streaming" if self._connected else "REST fallback" if self._owners else "stopped",
                    "error": self._error, "quotes": {key: {**row, "fresh": quote_is_fresh(row, now)}
                                                      for key, row in self._quotes.items()}}

    def _start_stream(self):
        from kiteconnect import KiteTicker
        from trading_analysis.brokers.zerodha import load_instruments_csv, resolve_instrument_token
        from trading_analysis.web_services import _zerodha_client

        client = _zerodha_client()
        for symbol, key in KEYS.items():
            exchange, name = key.split(":", 1)
            path = Path(f"data/raw/zerodha/instruments_{exchange}.csv")
            if path.exists():
                token = resolve_instrument_token(load_instruments_csv(path), exchange, name)
                self._tokens[int(token)] = symbol
        if not self._tokens:
            raise ValueError("Index instrument masters are missing")
        ticker = KiteTicker(client.api_key, client.access_token, reconnect=True, reconnect_max_tries=10)
        ticker.on_connect = self._on_connect
        ticker.on_ticks = self._on_ticks
        ticker.on_error = self._on_error
        ticker.on_close = self._on_error
        self._ticker = ticker
        ticker.connect(threaded=True)

    def _on_connect(self, ws, response):
        self._connected = True
        self._error = None
        ws.subscribe(list(self._tokens))
        ws.set_mode(ws.MODE_FULL, list(self._tokens))
        diagnostics.record("quote_stream_connected", area="indexes")

    def _on_ticks(self, ws, ticks):
        for row in ticks:
            symbol = self._tokens.get(row.get("instrument_token"))
            if symbol:
                self.publish(symbol, row.get("last_price"), row.get("exchange_timestamp"))

    def _on_error(self, ws, code, reason):
        self._connected = False
        self._error = diagnostics.clean(f"Stream {code}: {reason}")
        diagnostics.record("quote_stream_issue", area="indexes", status="waiting", error=self._error)

    def _close_stream(self):
        ticker, self._ticker = self._ticker, None
        if ticker:
            # The SDK reactor is process-wide and cannot be restarted after stop().
            from twisted.internet import reactor
            reactor.callFromThread(ticker.close)
        self._connected = False
        self._stream_attempted = False

    def _loop(self):
        last_rest = 0
        try:
            while True:
                with self._lock:
                    if not self._owners:
                        self._close_stream()
                        self._thread = None
                        return
                if is_scan_window():
                    if is_market_hours() and not self._stream_attempted:
                        self._stream_attempted = True
                        try:
                            self._start_stream()
                        except Exception as exc:
                            self._on_error(None, "unavailable", str(exc))
                    now = datetime.now(IST)
                    missing = [key for key in KEYS if not quote_is_fresh(self.get(key), now, max_age=5)]
                    if missing and time.monotonic() - last_rest >= 5:
                        last_rest = time.monotonic()
                        try:
                            from trading_analysis.web_services import _zerodha_client
                            rows = _zerodha_client().quotes([KEYS[key] for key in missing])
                            for key in missing:
                                row = rows.get(KEYS[key], {})
                                self.publish(key, row.get("last_price"), row.get("timestamp"), source="REST")
                        except Exception as exc:
                            self._error = diagnostics.clean(str(exc))
                            diagnostics.record("quote_fallback_failed", area="indexes", status="failed", error=self._error)
                self._wake.wait(1)
                self._wake.clear()
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._close_stream()
                    self._thread = None


LIVE_QUOTES = LiveQuoteService()
