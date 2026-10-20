"""One streaming feed for every scalping experiment; bounded, synchronized evidence."""
from collections import deque
from datetime import datetime, timedelta, time
from pathlib import Path
import threading

from trading_analysis.brokers.zerodha import resolve_instrument_token, merge_candles_csv
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.live_timing import IST, as_ist
from trading_analysis.models import Candle
from trading_analysis.scalping.rules import INDICES, fresh


class ScalpingFeed:
    def __init__(self, analysis, root):
        self.analysis, self.root = analysis, Path(root)
        self.lock = threading.RLock()
        self.rows = {s: [] for s in INDICES}
        self.quotes, self.tokens, self.pairs, self.forming = {}, {}, {}, {}
        self.history = {s: deque(maxlen=1200) for s in INDICES}
        self.catalog = {}
        self.state = {s: {"phase": "stopped", "error": None} for s in INDICES}
        self.ticker = None
        self.connected = False
        self.attempted = False
        self.active = False
        self.generation = 0
        self.last_refresh = {}
        self.last_ticks = {}
        self.retained = set()

    def path(self, symbol):
        return self.root / f"{symbol}_minute.csv"

    def start(self):
        with self.lock:
            self.active = True

    def stop(self):
        from trading_analysis.live_quotes import LIVE_QUOTES
        LIVE_QUOTES.release(f"derivatives-context-{id(self)}")
        with self.lock:
            self.active = False
            self.generation += 1
            ticker, self.ticker = self.ticker, None
            self.connected = self.attempted = False
            self.quotes.clear()
            self.tokens.clear()
            self.pairs.clear()
            self.forming.clear()
            self.last_ticks.clear()
            for h in self.history.values():
                h.clear()
        if ticker:
            from twisted.internet import reactor
            reactor.callFromThread(ticker.close)

    def _connect(self, generation):
        from kiteconnect import KiteTicker
        from trading_analysis.web_services import _zerodha_client
        client = _zerodha_client()
        ticker = KiteTicker(client.api_key, client.access_token, reconnect=True, reconnect_max_tries=50)
        ticker.on_connect = self._connected
        ticker.on_ticks = self.on_ticks
        ticker.on_close = ticker.on_error = self._error
        with self.lock:
            if not self.active or generation != self.generation:
                return
            self.ticker = ticker
            ticker.connect(threaded=True)
            from trading_analysis.live_quotes import LIVE_QUOTES
            LIVE_QUOTES.acquire(f"derivatives-context-{id(self)}")

    def _connected(self, ws, response):
        with self.lock:
            if ws is not self.ticker or not self.active:
                return
            self.connected = True
            for state in self.state.values():
                state.pop("stream_error", None)
            self.forming.clear()
            self.last_ticks.clear()
            tokens = list(self.tokens)
        if tokens:
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)

    def _error(self, ws, code, reason):
        from trading_analysis.diagnostics import clean, record
        with self.lock:
            if ws is not self.ticker:
                return
            self.connected = False
            for state in self.state.values():
                state["stream_error"] = clean(f"{code}: {reason}")
            self.forming.clear()
            for h in self.history.values():
                h.clear()
        record("scalping_stream_error", area="scalping", error=str(reason), status="failed")

    def _subscribe(self, additions):
        with self.lock:
            new = [token for token in additions if token not in self.tokens]
            self.tokens.update(additions)
            ticker = self.ticker if self.connected else None
        if ticker and new:
            from twisted.internet import reactor
            def update():
                ticker.subscribe(new)
                ticker.set_mode(ticker.MODE_FULL, new)
            reactor.callFromThread(update)

    def refresh(self, symbols, now=None):
        """Single rate-limited worker: warm-up/gap repair never blocks analysis/exits."""
        from trading_analysis.web_services import _zerodha_client
        now = as_ist(now or datetime.now(IST))
        generation = self.generation
        try:
            vix = int(resolve_instrument_token(self.analysis._instruments_for_exchange("NSE"), "NSE", "INDIA VIX"))
            self._subscribe({vix: {"symbol": "INDIAVIX", "kind": "vix"}})
        except (ValueError, OSError, AttributeError):
            pass
        for symbol in symbols:
            if not self.active or generation != self.generation:
                return
            exchange, name, derivatives, root = INDICES[symbol]
            try:
                token = int(resolve_instrument_token(self.analysis._instruments_for_exchange(exchange), exchange, name))
                self._subscribe({token: {"symbol": symbol, "kind": "spot"}})
                with self.lock:
                    cached = list(self.rows[symbol])
                if not cached and self.path(symbol).exists():
                    cached = load_candles(self.path(symbol))
                if (now - self.last_refresh.get(symbol, now - timedelta(days=1))).total_seconds() >= 60:
                    self.last_refresh[symbol] = now
                    with self.lock:
                        self.state[symbol].update(phase="refreshing", error=None)
                    start = as_ist(cached[-1].timestamp) - timedelta(minutes=5) if len(cached) >= 600 else now - timedelta(days=7)
                    downloaded = _zerodha_client().historical_candles(str(token), "minute", start, now)
                    complete = [c for c in downloaded if as_ist(c.timestamp) + timedelta(minutes=1) <= now]
                    if not complete:
                        raise ValueError("No completed one-minute candles returned")
                    merge_candles_csv(self.path(symbol), complete)
                    with self.lock:
                        merged = {as_ist(c.timestamp): c for c in cached + complete + self.rows[symbol]}
                        # Historical closed candles repair incomplete stream bars.
                        merged.update({as_ist(c.timestamp): c for c in complete})
                        self.rows[symbol] = [merged[k] for k in sorted(merged)][-5000:]
                        self.state[symbol].update(phase="ready", last_refresh=now.isoformat(), error=None)
                if generation != self.generation or not self.active:
                    return
                if symbol not in self.catalog or self.catalog[symbol][0] != now.date():
                    self.catalog[symbol] = (now.date(), self.analysis._instruments_for_exchange(derivatives))
                quote = self.quotes.get(symbol, {})
                price = quote.get("price") or (self.rows[symbol][-1].close if self.rows[symbol] else None)
                if price:
                    self.select_pair(symbol, price, now)
            except Exception as exc:
                from trading_analysis.diagnostics import clean
                with self.lock:
                    self.state[symbol].update(phase="error", error=clean(str(exc)))
        if self.active and not self.attempted and generation == self.generation:
            self.attempted = True
            try:
                self._connect(generation)
            except Exception:
                self.attempted = False
                raise

    def select_pair(self, symbol, price, now):
        _, _, exchange, name = INDICES[symbol]
        candidates = [r for r in self.catalog.get(symbol, (None, []))[1]
                      if r.get("name") == name and r.get("instrument_type") in {"CE", "PE"}
                      and str(r.get("expiry", ""))[:10] >= now.date().isoformat()]
        if not candidates:
            return
        expiry = min(str(r["expiry"])[:10] for r in candidates)
        candidates = [r for r in candidates if str(r["expiry"])[:10] == expiry]
        strikes = sorted({float(r["strike"]) for r in candidates})
        strike = min(strikes, key=lambda s: (abs(s - price), s))
        index = strikes.index(strike)
        selected = strikes[max(0, index - 1):index + 2]
        additions = {int(r["instrument_token"]): {"symbol": symbol, "kind": r["instrument_type"],
                     "strike": float(r["strike"]), "expiry": expiry, "exchange": exchange,
                     "tradingsymbol": r["tradingsymbol"]} for r in candidates if float(r["strike"]) in selected}
        pair = {meta["kind"]: token for token, meta in additions.items() if meta["strike"] == strike}
        if set(pair) != {"CE", "PE"}:
            return
        self._subscribe(additions)
        with self.lock:
            retired = [token for token, meta in self.tokens.items() if meta["symbol"] == symbol
                       and meta["kind"] in {"CE", "PE"} and token not in additions and token not in self.retained]
            for token in retired:
                self.tokens.pop(token, None)
                self.quotes.pop(token, None)
            if retired and self.connected and self.ticker:
                from twisted.internet import reactor
                reactor.callFromThread(self.ticker.unsubscribe, retired)
            identity = f"{symbol}:{expiry}:{strike}"
            if self.pairs.get(symbol, {}).get("identity") != identity:
                self.history[symbol].clear()
            self.pairs[symbol] = {**pair, "identity": identity, "strike": strike, "expiry": expiry}

    def on_ticks(self, ws, ticks):
        now = datetime.now(IST)
        with self.lock:
            if ws is not self.ticker or not self.active:
                return
            for tick in ticks:
                token = tick.get("instrument_token")
                meta = self.tokens.get(token)
                if not meta or not tick.get("exchange_timestamp"):
                    continue
                stamp = as_ist(tick["exchange_timestamp"])
                row = {**meta, "token": token, "price": float(tick.get("last_price") or 0),
                       "quote_time": stamp.isoformat(), "received_at": now.isoformat(), "source": "websocket",
                       "oi": tick.get("oi", 0), "volume": tick.get("volume_traded", 0)}
                depth = tick.get("depth") or {}
                for side, key in (("buy", "bid"), ("sell", "ask")):
                    best = (depth.get(side) or [{}])[0]
                    row[key], row[key + "_qty"] = best.get("price", 0), best.get("quantity", 0)
                key = meta["symbol"] if meta["kind"] in {"spot", "vix"} else token
                previous = self.quotes.get(key)
                if previous and as_ist(previous["quote_time"]) > stamp:
                    continue
                self.quotes[key] = row
                if meta["kind"] == "vix":
                    from trading_analysis.live_quotes import LIVE_QUOTES
                    LIVE_QUOTES.publish("INDIAVIX", row["price"], stamp, received_at=now, source="scalping_websocket")
                if meta["kind"] == "spot" and fresh(row, now):
                    self._bar(meta["symbol"], row, now)

    def _bar(self, symbol, row, now):
        stamp, price = as_ist(row["quote_time"]), row["price"]
        minute = stamp.replace(second=0, microsecond=0)
        if not time(9, 15) <= stamp.time().replace(tzinfo=None) < time(15, 30):
            return
        old = self.forming.get(symbol)
        if old and minute < old["minute"]:
            return
        if old and minute != old["minute"]:
            if old["valid"] and (old["minute"] + timedelta(minutes=1) - old["last"]).total_seconds() <= 3:
                candle = Candle(old["minute"], old["open"], old["high"], old["low"], old["close"], 0)
                existing = {as_ist(c.timestamp): c for c in self.rows[symbol]}
                existing.setdefault(old["minute"], candle)
                self.rows[symbol] = [existing[k] for k in sorted(existing)]
                self.rows[symbol] = self.rows[symbol][-5000:]
            old = None
        if old is None:
            old = {"minute": minute, "open": price, "high": price, "low": price, "close": price,
                   "last": stamp, "valid": (stamp - minute).total_seconds() <= 3}
        else:
            old["valid"] &= (stamp - old["last"]).total_seconds() <= 5
            old.update(high=max(old["high"], price), low=min(old["low"], price), close=price, last=stamp)
        self.forming[symbol] = old

    def snapshot(self, symbol, now):
        with self.lock:
            spot = dict(self.quotes.get(symbol, {}))
            pair = dict(self.pairs.get(symbol, {}))
            # Pair selection is refreshed by the background worker. Refuse stale ATM selection.
            strikes = sorted({m["strike"] for m in self.tokens.values() if m["symbol"] == symbol and m["kind"] != "spot" and m.get("expiry") == pair.get("expiry")})
            if spot and strikes and pair.get("strike") != min(strikes, key=lambda s: (abs(s - spot["price"]), s)):
                self.history[symbol].clear()
                pair = {}
            sample = {"time": now.isoformat(), "spot": spot, **pair,
                      "CE": dict(self.quotes.get(pair.get("CE"), {})), "PE": dict(self.quotes.get(pair.get("PE"), {}))}
            history = self.history[symbol]
            if pair and (not history or (now - as_ist(history[-1]["time"])).total_seconds() >= 1):
                history.append(sample)
            return list(self.rows[symbol]), spot, list(history), sample
