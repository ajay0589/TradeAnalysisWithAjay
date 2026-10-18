"""Expiry feed: reuse the broker stream, retain both legs, freeze ATM evidence."""
from datetime import timedelta

from trading_analysis.scalping.feed import ScalpingFeed
from trading_analysis.scalping.rules import INDICES
from trading_analysis.live_timing import as_ist


class ExpiryFeed(ScalpingFeed):
    def __init__(self, analysis, root):
        super().__init__(analysis, root)
        self.anchors = {}

    def select_pair(self, symbol, price, now):
        # Reset after (not just before) five-minute signals so 3m evidence can warm up.
        boundary=now.replace(minute=(now.minute//5)*5,second=0,microsecond=0)+timedelta(minutes=1)
        if now<boundary:boundary-=timedelta(minutes=5)
        anchor = self.anchors.get(symbol)
        if not anchor or anchor["block"]!=boundary.isoformat():
            anchor = {"block":boundary.isoformat(), "price": price}
            self.anchors[symbol] = anchor
        with self.lock:
            retained = set(self.retained)
            self.retained.update(token for token, meta in self.tokens.items() if meta.get("symbol")==symbol and meta.get("kind") in {"CE","PE"})
        try:
            super().select_pair(symbol, anchor["price"], now)
        finally:
            with self.lock:self.retained = retained
        pair = self.pairs.get(symbol, {})
        exchange, name = INDICES[symbol][2:]
        rows = [r for r in self.catalog.get(symbol, (None, []))[1] if r.get("name") == name
                and str(r.get("expiry"))[:10] == pair.get("expiry") and r.get("instrument_type") in {"CE", "PE"}]
        strikes = sorted({float(r["strike"]) for r in rows})
        if not strikes or pair.get("strike") not in strikes:
            return
        i = strikes.index(pair["strike"])
        selected = strikes[max(0,i-4):i+5]
        additions = {int(r["instrument_token"]): dict(symbol=symbol, kind=r["instrument_type"],
            strike=float(r["strike"]), expiry=str(r["expiry"])[:10], exchange=exchange,
            tradingsymbol=r["tradingsymbol"], lot_size=int(r.get("lot_size") or 0))
            for r in rows if float(r["strike"]) in selected or int(r["instrument_token"]) in self.retained}
        self._subscribe(additions)
        with self.lock:
            retired = [token for token, meta in self.tokens.items() if meta.get("symbol")==symbol and meta.get("kind") in {"CE","PE"} and token not in additions and token not in retained]
            for token in retired:
                self.tokens.pop(token,None);self.quotes.pop(token,None)
            if retired and self.connected and self.ticker:
                from twisted.internet import reactor
                reactor.callFromThread(self.ticker.unsubscribe,retired)
            for token, meta in additions.items():
                if token in self.quotes:
                    self.quotes[token].update(lot_size=meta["lot_size"])

    def snapshot(self, symbol, now):
        with self.lock:
            pair = dict(self.pairs.get(symbol, {}))
            sample = {"time": now.isoformat(), "spot": dict(self.quotes.get(symbol, {})), **pair,
                      "CE": dict(self.quotes.get(pair.get("CE"), {})), "PE": dict(self.quotes.get(pair.get("PE"), {}))}
            history = self.history[symbol]
            if pair and (not history or (now-as_ist(history[-1]["time"])).total_seconds() >= 1):
                history.append(sample)
            return list(self.rows[symbol]), sample["spot"], list(history), sample

    def stop(self):
        super().stop()
        self.anchors.clear()
