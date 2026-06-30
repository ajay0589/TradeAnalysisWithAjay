from __future__ import annotations

from typing import Any


def calculate_strategy_payoff(
    spot: float,
    legs: list[dict[str, Any]],
    lot_size: int = 75,
    spot_range: list[float] | None = None,
) -> dict[str, Any]:
    if spot <= 0:
        raise ValueError("Spot must be positive.")
    clean_legs = [_clean_leg(leg) for leg in legs]
    if not clean_legs:
        raise ValueError("At least one option leg is required.")
    range_values = spot_range or _default_spot_range(spot)
    table = [
        {
            "spot": value,
            "payoff": _round(sum(_leg_payoff(value, leg) for leg in clean_legs) * lot_size),
        }
        for value in range_values
    ]
    payoffs = [row["payoff"] for row in table]
    net_premium = sum((_signed_premium(leg) * lot_size) for leg in clean_legs)
    return {
        "spot": spot,
        "lot_size": lot_size,
        "legs": clean_legs,
        "net_premium": _round(net_premium),
        "payoff_table": table,
        "max_profit_note": _bounded_note(max(payoffs), payoffs, "profit"),
        "max_loss_note": _bounded_note(min(payoffs), payoffs, "loss"),
        "breakeven_note": _breakeven_note(table),
        "greeks": {
            "delta": None,
            "theta": None,
            "vega": None,
            "note": "Greeks are placeholders until live or historical Greeks are available.",
        },
    }


def _clean_leg(leg: dict[str, Any]) -> dict[str, Any]:
    side = str(leg.get("side") or "").lower()
    option_type = str(leg.get("option_type") or "").upper()
    if side not in {"buy", "sell"}:
        raise ValueError("Each leg side must be buy or sell.")
    if option_type not in {"CE", "PE"}:
        raise ValueError("Each leg option_type must be CE or PE.")
    strike = float(leg.get("strike"))
    premium = float(leg.get("premium"))
    if strike <= 0 or premium < 0:
        raise ValueError("Strike must be positive and premium cannot be negative.")
    return {
        "side": side,
        "option_type": option_type,
        "strike": strike,
        "premium": premium,
        "expiry": leg.get("expiry"),
    }


def _default_spot_range(spot: float) -> list[float]:
    start = spot * 0.9
    step = (spot * 0.2) / 20
    return [round(start + (index * step), 2) for index in range(21)]


def _leg_payoff(spot: float, leg: dict[str, Any]) -> float:
    intrinsic = max(0.0, spot - leg["strike"]) if leg["option_type"] == "CE" else max(0.0, leg["strike"] - spot)
    payoff = intrinsic - leg["premium"]
    return payoff if leg["side"] == "buy" else -payoff


def _signed_premium(leg: dict[str, Any]) -> float:
    return leg["premium"] if leg["side"] == "sell" else -leg["premium"]


def _bounded_note(value: float, payoffs: list[float], label: str) -> str:
    edge_value = payoffs[0] if label == "loss" else payoffs[-1]
    opposite_edge = payoffs[-1] if label == "loss" else payoffs[0]
    if label == "profit" and value in {edge_value, opposite_edge}:
        return f"Approximate max profit in shown range: {value:.2f}; true payoff may be unbounded."
    if label == "loss" and value in {edge_value, opposite_edge}:
        return f"Approximate max loss in shown range: {value:.2f}; true payoff may be unbounded."
    return f"Approximate max {label} in shown range: {value:.2f}."


def _breakeven_note(table: list[dict[str, float]]) -> str:
    levels: list[float] = []
    for previous, current in zip(table, table[1:]):
        if previous["payoff"] == 0:
            levels.append(previous["spot"])
        if previous["payoff"] * current["payoff"] < 0:
            span = current["spot"] - previous["spot"]
            denom = abs(previous["payoff"]) + abs(current["payoff"])
            levels.append(previous["spot"] + (span * abs(previous["payoff"]) / denom))
    if not levels:
        return "No breakeven found inside the displayed spot range."
    return "Approximate breakeven(s): " + ", ".join(f"{level:.2f}" for level in levels)


def _round(value: float) -> float:
    return round(float(value), 2)
