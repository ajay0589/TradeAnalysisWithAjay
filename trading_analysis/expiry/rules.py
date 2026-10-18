"""Closed-candle rules shared by live observation and recorded-quote replay."""
from datetime import timedelta, time

from trading_analysis.analysis.technical import ema, atr
from trading_analysis.live_timing import as_ist
from trading_analysis.scalping.rules import aggregate, fresh, usable_option, premium_evidence

VERSION = "expiry-v1-paper"
SYMBOLS = ("NIFTY", "SENSEX")
SETUPS = {"Expiry_1": "Opening-range retest", "Expiry_2": "Trend pullback",
          "Expiry_3": "Compression breakout", "Expiry_4": "Failed-breakout reversal"}
POLICY = {"capital": 5000000, "trade_budget": 12500, "portfolio_budget": 25000,
          "daily_loss": 25000, "entries_per_setup": 2, "hold_minutes": 60,
          "entry_from": "09:35", "entry_to": "13:30", "exit_at": "15:10",
          "quote_age_seconds": 3, "signal_age_seconds": 15, "slippage_points_per_leg": .10,
          "fee_rate_per_side": .0025, "fee_per_leg_per_side": 25,
          "cost_basis": "Conservative scenario allowance, not a statutory fee calculator",
          "max_debit_width": .65, "portfolio_priority": list(SETUPS),
          "breadth_filter": "not_enabled", "oi_filter": "context_only"}
RULES = [
    "Expiry_1: 09:15-09:30 range; prior 5m close outside, current retest within 0.15 ATR and close beyond the level; aligned 15m EMA20/50 trend.",
    "Expiry_2: aligned 15m EMA20/50 and rising/falling EMA20; preceding three 5m bars approach EMA20 within 0.15 ATR, hold EMA50; close beyond the previous bar.",
    "Expiry_3: preceding six 5m bars span at most 60% of the preceding twenty-bar range; close breaks the six-bar range with body >= 0.5 ATR and range >= 1.2 ATR; aligned 15m trend.",
    "Expiry_4: prior 5m bar breaches and closes back inside opening-range or previous-session extreme; next close passes rejection-bar extreme. Flat 15m EMA20/50 separation <= 0.5 ATR only.",
    "All: completed candles only; structural risk 0.3-2.5 ATR; signal range <= 2.5 ATR; price extension <= 1.5 ATR from 5m EMA20.",
    "ATM CE/PE mid-price and spot agreement over 1m and 3m; OI recorded, never required. Evidence pair resets one minute after five-minute boundaries; execution chooses current ATM independently.",
    "Equal-quantity same-expiry verticals: buy ATM, sell farther strike nearest one structural-risk distance. Debit <= 65% of width; one lot initially; quote depth must cover it.",
    "Exit on structural stop, one-risk-unit activation of trailing three closed 5m bar stop, 60 minutes, or 15:10 IST. No overnight, averaging, or roll.",
    "No constituent breadth or futures VWAP filter in v1. Compression participation is option-price confirmation, not fabricated spot volume.",
    "Independent research ledgers are not additive. Portfolio accepts at most one position per index and enforces combined budgets. Both are paper-only.",
]


def technical(rows, now):
    now = as_ist(now)
    source = [c for c in rows if as_ist(c.timestamp) + timedelta(minutes=1) <= now][-5000:]
    base, higher = aggregate(source, 5), aggregate(source, 15)
    result = {"signals": {}, "reason": "warmup", "indicators": {}}
    if len(base) < 60 or len(higher) < 55:
        return result
    c, p = base[-1], base[-2]
    closed = as_ist(c.timestamp) + timedelta(minutes=5)
    result.update(bar_time=c.timestamp.isoformat(), closed_at=closed.isoformat())
    if closed.date() != now.date() or not 0 <= (now - closed).total_seconds() < 300:
        return {**result, "reason": "stale_candles"}
    recent = [b for b in base if b.timestamp.date()==now.date()][-7:]
    if any(b.timestamp - a.timestamp != timedelta(minutes=5) for a, b in zip(recent,recent[1:])):
        return {**result, "reason": "candle_gap"}
    if (now - (as_ist(higher[-1].timestamp) + timedelta(minutes=15))).total_seconds() >= 900:
        return {**result, "reason": "higher_candle_gap"}
    bc, hc = [b.close for b in base[-250:]], [b.close for b in higher[-250:]]
    vol, hv = atr(base[-250:]), atr(higher[-250:])
    if not vol or not hv:
        return {**result, "reason": "invalid_atr"}
    fast, slow, prior = ema(hc, 20), ema(hc, 50), ema(hc[:-1], 20)
    up, down = hc[-1] > fast > slow and fast > prior, hc[-1] < fast < slow and fast < prior
    flat = abs(fast - slow) <= .5 * hv
    e20, e50 = ema(bc[:-1], 20), ema(bc[:-1], 50)
    result["indicators"] = dict(close=c.close, atr=vol, ema20=e20, higher_ema20=fast, higher_ema50=slow,
                                trend="bullish" if up else "bearish" if down else "unclear", flat=flat)
    if not time(9, 35) <= now.time().replace(tzinfo=None) < time(13, 30):
        return {**result, "reason": "outside_entry_window"}
    if c.high - c.low > 2.5 * vol or abs(c.close - e20) > 1.5 * vol:
        return {**result, "reason": "extended_signal"}
    green, red = c.close > c.open, c.close < c.open
    candidates = {}
    today = [b for b in base if b.timestamp.date() == now.date()]
    opening = [b for b in today if time(9, 15) <= b.timestamp.time() < time(9, 30)]
    if len(opening) == 3:
        hi, lo = max(b.high for b in opening), min(b.low for b in opening)
        result["indicators"].update(opening_high=hi, opening_low=lo)
        if p.timestamp.time() >= time(9, 30):
            candidates["Expiry_1"] = (up and p.close > hi and hi-.15*vol <= c.low <= hi+.15*vol and c.close > hi and green,
                down and p.close < lo and lo-.15*vol <= c.high <= lo+.15*vol and c.close < lo and red, c.low, c.high)
        previous_days = [b.timestamp.date() for b in base if b.timestamp.date() < now.date()]
        prior_day = [b for b in base if previous_days and b.timestamp.date() == max(previous_days)]
        levels_hi = [hi] + ([max(b.high for b in prior_day)] if len(prior_day) == 75 else [])
        levels_lo = [lo] + ([min(b.low for b in prior_day)] if len(prior_day) == 75 else [])
        candidates["Expiry_4"] = (flat and green and any(p.low < level < p.close for level in levels_lo) and c.close > p.high,
            flat and red and any(p.high > level > p.close for level in levels_hi) and c.close < p.low, min(p.low,c.low), max(p.high,c.high))
    pull = base[-4:-1]
    candidates["Expiry_2"] = (up and min(b.low for b in pull) <= e20+.15*vol and min(b.low for b in pull) > e50 and green and c.close > p.high,
        down and max(b.high for b in pull) >= e20-.15*vol and max(b.high for b in pull) < e50 and red and c.close < p.low,
        min(b.low for b in base[-4:]), max(b.high for b in base[-4:]))
    narrow, wide = base[-7:-1], base[-21:-1]
    upper, lower = max(b.high for b in narrow), min(b.low for b in narrow)
    compressed = len(today) >= 7 and upper-lower <= .6*(max(b.high for b in wide)-min(b.low for b in wide))
    expansion = c.high-c.low >= 1.2*vol and abs(c.close-c.open) >= .5*vol
    candidates["Expiry_3"] = (compressed and expansion and up and c.close > upper, compressed and expansion and down and c.close < lower, lower, upper)
    for setup, (long, short, low, high) in candidates.items():
        if not (long or short):
            continue
        stop = low-.1*vol if long else high+.1*vol
        risk = abs(c.close-stop)
        if .3*vol <= risk <= 2.5*vol:
            result["signals"][setup] = dict(direction="bullish" if long else "bearish", reference=c.close, stop=stop, risk=risk, atr=vol)
    result["reason"] = "evaluated"
    return result


def spread_quote(long, short, now, quantity, spot=None, entry=True):
    """Conservative executable sides; never use last price as a fill."""
    if not all(usable_option(q, now) for q in (long, short)):
        return None
    if abs((as_ist(long["quote_time"])-as_ist(short["quote_time"])).total_seconds()) > 2:
        return None
    if spot and (not fresh(spot, now) or any(abs((as_ist(q["quote_time"])-as_ist(spot["quote_time"])).total_seconds()) > 2 for q in (long, short))):
        return None
    sides = ("ask", "bid") if entry else ("bid", "ask")
    if any(q[side+"_qty"] < quantity for q, side in zip((long,short), sides)):
        return None
    slip = POLICY["slippage_points_per_leg"]
    a, b = long[sides[0]], short[sides[1]]
    value = a-b + (2*slip if entry else -2*slip)
    fees = (a+b)*quantity*POLICY["fee_rate_per_side"] + 2*POLICY["fee_per_leg_per_side"]
    return dict(value=value, fees=fees, long_price=a+(slip if entry else -slip),
                short_price=b+(-slip if entry else slip), quote_time=now.isoformat())


def select_spread(signal, snapshot, quotes, now):
    expiry = now.date().isoformat()
    if snapshot.get("expiry") != expiry:
        return None, "not_expiry_day"
    side = "CE" if signal["direction"] == "bullish" else "PE"
    reference = quotes.get(snapshot.get(side), {})
    available = [q for q in quotes.values() if q.get("kind")==side and q.get("symbol")==reference.get("symbol") and q.get("expiry")==expiry]
    price = snapshot.get("spot",{}).get("price",0)
    long = min(available,key=lambda q:(abs(q["strike"]-price),q["strike"])) if available else {}
    if not long or long.get("expiry") != expiry:
        return None, "missing_long_quote"
    sign = 1 if side == "CE" else -1
    choices = [q for q in quotes.values() if q.get("kind") == side and q.get("symbol") == long.get("symbol")
               and q.get("expiry") == expiry and (q.get("strike",0)-long["strike"])*sign > 0]
    if not choices:
        return None, "missing_short_quote"
    short = min(choices, key=lambda q: (abs(abs(q["strike"]-long["strike"])-signal["risk"]), q["strike"]))
    quantity = int(long.get("lot_size") or 0)
    if quantity <= 0 or short.get("lot_size") != quantity:
        return None, "invalid_lot_size"
    fill = spread_quote(long, short, now, quantity, snapshot.get("spot"))
    if not fill:
        return None, "stale_wide_unsynchronized_or_insufficient_depth"
    width = abs(short["strike"]-long["strike"])
    # Reserve round-trip scenario costs plus premium; one lot, not the whole capital.
    risk = fill["value"]*quantity + 2*fill["fees"]
    if not 0 < fill["value"] <= POLICY["max_debit_width"]*width:
        return None, "poor_spread_payoff"
    if risk > POLICY["trade_budget"]:
        return None, "one_lot_exceeds_budget"
    return dict(long=long, short=short, quantity=quantity, width=width, entry_fill=fill,
                debit=fill["value"], risk_budget=risk), "qualified"
