from __future__ import annotations

from typing import Any


def generate_nifty_alerts(
    context_result: dict[str, Any],
    strategy_candidates: list[dict[str, Any]],
    min_score: int = 70,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    technical = context_result.get("technical") or {}
    options = context_result.get("options") or {}
    iv = context_result.get("iv") or {}
    mode = str(context_result.get("mode") or "auto")
    spot = _float((technical or {}).get("spot") or (options or {}).get("spot"))

    for candidate in strategy_candidates:
        score = int(candidate.get("suitability_score") or candidate.get("score") or 0)
        if score < min_score:
            continue
        direction = _candidate_direction(candidate)
        confidence = str(candidate.get("confidence") or "low")
        severity = _candidate_severity(score, confidence, direction, technical, options, iv)
        label = str(candidate.get("label") or candidate.get("strategy_id") or "NIFTY setup")
        horizon = str(candidate.get("horizon") or context_result.get("mode") or "auto")
        alerts.append(
            {
                "alert_type": "strategy_candidate",
                "mode": mode,
                "horizon": horizon,
                "severity": severity,
                "symbol": "NIFTY",
                "spot": spot,
                "strategy_id": candidate.get("strategy_id"),
                "direction": direction,
                "score": score,
                "confidence": confidence,
                "title": f"{label} candidate",
                "message": _candidate_message(label, direction, score, confidence, technical, options, iv),
                "trigger_level": _trigger_level(direction, technical, options),
                "invalidation_level": _invalidation_level(direction, technical, options),
                "expiry": _expiry(candidate, options),
                "reasons": list(candidate.get("reasons") or []),
                "risks": list(candidate.get("risks") or []),
            }
        )

    iv_regime = str(iv.get("iv_regime") or "unknown")
    if iv_regime in {"high", "extreme"}:
        alerts.append(
            {
                "alert_type": "iv_regime",
                "mode": mode,
                "horizon": mode,
                "severity": "risk" if iv_regime == "extreme" else "important",
                "symbol": "NIFTY",
                "spot": spot,
                "strategy_id": None,
                "direction": "volatile",
                "score": iv.get("iv_rank"),
                "confidence": "medium" if iv.get("enough_history") else "low",
                "title": f"NIFTY IV regime is {iv_regime}",
                "message": "Volatility context changed; strategy candidates need fresh risk context and confirmation.",
                "trigger_level": None,
                "invalidation_level": None,
                "expiry": options.get("selected_weekly_expiry"),
                "reasons": [
                    f"ATM IV {iv.get('atm_iv')}",
                    f"IV rank {iv.get('iv_rank')}",
                    f"IV percentile {iv.get('iv_percentile')}",
                ],
                "risks": ["Higher IV can expand option prices and change range assumptions."],
            }
        )

    option_bias = str(options.get("option_bias") or "")
    if option_bias in {"bullish", "bearish"}:
        technical_biases = {
            str(technical.get("bias_intraday") or ""),
            str(technical.get("bias_swing") or ""),
            str(technical.get("bias_positional") or ""),
        }
        if option_bias in technical_biases:
            alerts.append(
                {
                    "alert_type": "bias_alignment",
                    "mode": mode,
                    "horizon": mode,
                    "severity": "info",
                    "symbol": "NIFTY",
                    "spot": spot,
                    "strategy_id": None,
                    "direction": option_bias,
                    "score": None,
                    "confidence": "medium",
                    "title": f"NIFTY {option_bias} context alignment",
                    "message": "Technical and option-chain context are pointing in the same direction; wait for setup confirmation.",
                    "trigger_level": _trigger_level(option_bias, technical, options),
                    "invalidation_level": _invalidation_level(option_bias, technical, options),
                    "expiry": options.get("selected_weekly_expiry"),
                    "reasons": [
                        f"Option bias: {option_bias}",
                        f"Intraday: {technical.get('bias_intraday')}",
                        f"Swing: {technical.get('bias_swing')}",
                    ],
                    "risks": list((technical.get("warnings") or []) + (options.get("warnings") or [])),
                }
            )
    return alerts


def _candidate_direction(candidate: dict[str, Any]) -> str:
    text = " ".join(
        str(candidate.get(key) or "")
        for key in ("required_view", "strategy_id", "label", "structure")
    ).lower()
    if "bull" in text:
        return "bullish"
    if "bear" in text:
        return "bearish"
    if "neutral" in text or "strangle" in text or "condor" in text or "straddle" in text:
        return "neutral"
    if "volatility" in text or "long straddle" in text:
        return "volatile"
    return "watch"


def _candidate_severity(
    score: int,
    confidence: str,
    direction: str,
    technical: dict[str, Any],
    options: dict[str, Any],
    iv: dict[str, Any],
) -> str:
    option_bias = str(options.get("option_bias") or "")
    aligned = direction in {option_bias, str(technical.get("bias_intraday") or ""), str(technical.get("bias_swing") or "")}
    iv_regime = str(iv.get("iv_regime") or "")
    if iv_regime == "extreme":
        return "risk"
    if score >= 80 and confidence == "high" and aligned:
        return "important"
    return "watch"


def _candidate_message(
    label: str,
    direction: str,
    score: int,
    confidence: str,
    technical: dict[str, Any],
    options: dict[str, Any],
    iv: dict[str, Any],
) -> str:
    return (
        f"{label} is a {direction} setup candidate with score {score} and {confidence} confidence. "
        f"Technical bias: intraday {technical.get('bias_intraday')}, swing {technical.get('bias_swing')}. "
        f"Option bias: {options.get('option_bias')}. IV regime: {iv.get('iv_regime')}."
    )


def _trigger_level(direction: str, technical: dict[str, Any], options: dict[str, Any]) -> float | None:
    if direction == "bullish":
        levels = list(technical.get("resistance_levels") or [])
        return _float(levels[0]) if levels else _float(options.get("resistance_by_oi"))
    if direction == "bearish":
        levels = list(technical.get("support_levels") or [])
        return _float(levels[0]) if levels else _float(options.get("support_by_oi"))
    return None


def _invalidation_level(direction: str, technical: dict[str, Any], options: dict[str, Any]) -> float | None:
    if direction == "bullish":
        levels = list(technical.get("support_levels") or [])
        return _float(levels[0]) if levels else _float(options.get("support_by_oi"))
    if direction == "bearish":
        levels = list(technical.get("resistance_levels") or [])
        return _float(levels[0]) if levels else _float(options.get("resistance_by_oi"))
    return None


def _expiry(candidate: dict[str, Any], options: dict[str, Any]) -> str | None:
    plan = str(candidate.get("expiry_plan") or "")
    if "monthly" in plan.lower():
        return options.get("selected_monthly_expiry") or options.get("selected_weekly_expiry")
    return options.get("selected_weekly_expiry") or options.get("selected_monthly_expiry")


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
