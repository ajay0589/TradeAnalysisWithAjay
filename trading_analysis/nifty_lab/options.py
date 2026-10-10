"""Price/OI quadrant hypotheses, evaluated only from observations already received."""
from collections import Counter
from datetime import timedelta
from math import isfinite

from trading_analysis.live_timing import as_ist


def number(row, key):
    try:
        value = float(row[key])
        return value if isfinite(value) else None
    except (KeyError, TypeError, ValueError):
        return None


def contracts(snapshot):
    return {(r.get("tradingsymbol"), r.get("strike"), r.get("option_type")): r for r in snapshot["rows"]}


def quadrant(price_change, oi_change):
    if price_change == 0 or oi_change == 0:
        return "unchanged"
    return ("long_buildup" if oi_change > 0 else "short_covering") if price_change > 0 else (
        "short_buildup" if oi_change > 0 else "long_unwinding")


def liquid(row, at):
    price, bid, ask, oi, volume = [number(row, key) for key in ("last_price", "bid_price", "ask_price", "oi", "volume")]
    if any(v is None for v in (price, bid, ask, oi, volume)) or price <= 0 or not 0 < bid <= ask or oi <= 0 or volume < 0:
        return False
    if (ask - bid) / price > .05:
        return False
    try:
        return all(0 <= (at - as_ist(row[key])).total_seconds() <= 120 for key in ("exchange_timestamp", "last_trade_time"))
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def compare(current, previous):
    end, begin = as_ist(current["received_at"]), as_ist(previous["received_at"])
    unavailable = {"bias": "unavailable", "reason": "insufficient_liquid_matched_contracts", "contracts": []}
    if end <= begin or end.date() != begin.date() or current["expiry"] != previous["expiry"]:
        return {**unavailable, "reason": "session_or_expiry_mismatch"}
    new, old = contracts(current), contracts(previous)
    common = set(new) & set(old)
    valid = [key for key in common if key[2] in {"CE", "PE"} and liquid(new[key], end) and liquid(old[key], begin)]
    coverage = len(valid) / max(1, len(new), len(old))
    if len(valid) < 8 or coverage < .7 or any(sum(k[2] == side for k in valid) < 2 for side in ("CE", "PE")):
        return {**unavailable, "coverage": coverage, "matched": len(valid)}
    votes, by_side, classes, details = Counter(), {"CE": Counter(), "PE": Counter()}, Counter(), []
    for key in sorted(valid):
        row, prior = new[key], old[key]
        volume = number(row, "volume") - number(prior, "volume")
        if volume < 0:
            return {**unavailable, "reason": "volume_reset"}
        premium = (number(row, "last_price") / number(prior, "last_price") - 1) * 100
        delta_oi = (number(row, "oi") / number(prior, "oi") - 1) * 100
        label = quadrant(premium, delta_oi)
        qualifies = volume > 0 and abs(premium) >= .5 and abs(delta_oi) >= .5
        bias = "bullish" if (key[2] == "CE") == (premium > 0) else "bearish"
        if qualifies:
            votes[bias] += 1
            by_side[key[2]][bias] += 1
            classes[label] += 1
        details.append({"contract": key[0], "strike": key[1], "option_type": key[2],
                        "price_change_percent": premium, "oi_change_percent": delta_oi,
                        "volume_increment": volume, "interpretation": label,
                        "direction_hypothesis": bias if qualifies else "no_vote"})
    direction = "unclear"
    for side, opposite in (("bullish", "bearish"), ("bearish", "bullish")):
        if votes[side] >= 4 and votes[side] >= 2 * max(1, votes[opposite]) and all(
                by_side[kind][side] >= 2 and by_side[kind][side] > by_side[kind][opposite] for kind in by_side):
            direction = side
    return {"bias": direction, "reason": "aligned_price_oi" if direction != "unclear" else "mixed_or_unchanged",
            "current_snapshot_id": current.get("id"), "previous_snapshot_id": previous.get("id"),
            "coverage": coverage, "matched": len(valid), "votes": dict(votes), "classifications": dict(classes),
            "from_received_at": begin.isoformat(), "to_received_at": end.isoformat(), "contracts": details}


def option_evidence(snapshots, as_of):
    as_of = as_ist(as_of)
    available = sorted((s for s in snapshots if as_ist(s["received_at"]) <= as_of), key=lambda s: as_ist(s["received_at"]))
    base = {"bias": "unavailable", "state": "warming_up", "reason": "need_3_and_6_minute_history", "windows": {},
            "as_of": as_of.isoformat(), "note": "Price/OI hypotheses, not identified buyers/sellers or exchange OI publication times."}
    if not available:
        return base
    current = available[-1]
    end = as_ist(current["received_at"])
    base.update(received_at=end.isoformat(), expiry=current["expiry"], age_seconds=(as_of - end).total_seconds())
    if end.date() != as_of.date() or (as_of - end).total_seconds() > 120:
        return {**base, "state": "stale", "reason": "latest_options_stale"}
    if str(current["expiry"])[:10] < as_of.date().isoformat():
        return {**base, "state": "stale", "reason": "expired_contracts"}
    same = [s for s in available if s["expiry"] == current["expiry"] and as_ist(s["received_at"]).date() == end.date()]
    for minutes in (3, 6, 15):
        cutoff = end - timedelta(minutes=minutes)
        previous = [s for s in same if as_ist(s["received_at"]) <= cutoff]
        if not previous or (cutoff - as_ist(previous[-1]["received_at"])).total_seconds() > 90:
            base["windows"][str(minutes)] = {"bias": "unavailable", "reason": "window_history_gap"}
        else:
            base["windows"][str(minutes)] = compare(current, previous[-1])
    short, medium, longer = [base["windows"][str(m)]["bias"] for m in (3, 6, 15)]
    if "unavailable" in (short, medium):
        return base
    direction = short if short == medium and short in {"bullish", "bearish"} and longer not in ({"bullish", "bearish"} - {short}) else "unclear"
    return {**base, "bias": direction, "state": "ready",
            "reason": "3m_6m_agree_no_15m_conflict" if direction != "unclear" else "option_windows_disagree_or_unchanged"}
