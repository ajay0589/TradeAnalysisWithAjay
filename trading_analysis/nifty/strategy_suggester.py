from __future__ import annotations

from trading_analysis.nifty.models import NiftyIVContext, NiftyOptionContext, NiftyStrategyCandidate, NiftyTechnicalContext


def suggest_nifty_strategies(
    technical_context: NiftyTechnicalContext,
    option_context: NiftyOptionContext,
    iv_context: NiftyIVContext,
    mode: str = "auto",
    allowed_strategies: list[str] | None = None,
    risk_profile: str = "defined",
) -> list[NiftyStrategyCandidate]:
    allowed = set(allowed_strategies or [])
    risk = (risk_profile or "defined").lower()
    horizon = _horizon(mode, technical_context)
    candidates = [
        _directional_candidate("nifty_bull_call_spread", "Bull Call Spread", "directional", "bullish", horizon, technical_context, option_context, iv_context, defined=True),
        _directional_candidate("nifty_bear_put_spread", "Bear Put Spread", "directional", "bearish", horizon, technical_context, option_context, iv_context, defined=True),
        _directional_candidate("nifty_bull_put_spread", "Bull Put Spread", "directional", "bullish", horizon, technical_context, option_context, iv_context, defined=True, theta=True),
        _directional_candidate("nifty_bear_call_spread", "Bear Call Spread", "directional", "bearish", horizon, technical_context, option_context, iv_context, defined=True, theta=True),
        _neutral_candidate("nifty_iron_condor", "Iron Condor", "neutral", horizon, technical_context, option_context, iv_context, defined=True),
        _neutral_candidate("nifty_iron_fly", "Iron Fly", "neutral", horizon, technical_context, option_context, iv_context, defined=True, compact=True),
        _neutral_candidate("nifty_short_strangle", "Short Strangle", "neutral", horizon, technical_context, option_context, iv_context, defined=False),
        _neutral_candidate("nifty_short_straddle", "Short Straddle", "neutral", horizon, technical_context, option_context, iv_context, defined=False, compact=True),
        _volatility_candidate("nifty_long_straddle", "Long Straddle", horizon, technical_context, option_context, iv_context, compact=True),
        _volatility_candidate("nifty_long_strangle", "Long Strangle", horizon, technical_context, option_context, iv_context),
        _calendar_candidate("nifty_call_calendar", "Call Calendar", "calendar", "bullish", horizon, technical_context, option_context, iv_context),
        _calendar_candidate("nifty_put_calendar", "Put Calendar", "calendar", "bearish", horizon, technical_context, option_context, iv_context),
        _calendar_candidate("nifty_double_calendar", "Double Calendar", "calendar", "neutral", horizon, technical_context, option_context, iv_context),
        _calendar_candidate("nifty_weekly_monthly_short_straddle_calendar", "Weekly/Monthly Straddle Calendar", "calendar", "neutral", horizon, technical_context, option_context, iv_context),
        _calendar_candidate("nifty_weekly_short_monthly_long_hedge", "Weekly Short / Monthly Hedge", "calendar", "neutral", horizon, technical_context, option_context, iv_context),
    ]
    output = []
    for candidate in candidates:
        if candidate is None:
            continue
        if allowed and candidate.strategy_id not in allowed:
            continue
        if risk == "defined" and _is_undefined(candidate):
            continue
        if risk == "undefined" and not _is_undefined(candidate):
            continue
        if candidate.suitability_score >= 45:
            output.append(candidate)
    return sorted(output, key=lambda item: item.suitability_score, reverse=True)


def _directional_candidate(
    strategy_id: str,
    label: str,
    structure: str,
    direction: str,
    horizon: str,
    technical: NiftyTechnicalContext,
    options: NiftyOptionContext,
    iv: NiftyIVContext,
    defined: bool,
    theta: bool = False,
) -> NiftyStrategyCandidate | None:
    tech_bias = _bias_for_horizon(technical, horizon)
    score = 35
    reasons: list[str] = []
    risks: list[str] = []
    confirmations = ["Price confirmation near planned trigger level", "Option-chain bias should not flip before entry"]
    if tech_bias == direction:
        score += 25
        reasons.append(f"{horizon} technical bias is {direction}.")
    if options.option_bias in {direction, "neutral"}:
        score += 20
        reasons.append(f"Option-chain context is {options.option_bias}, not against the {direction} view.")
    if direction == "bullish" and options.support_by_oi:
        score += 8
        reasons.append(f"Put OI support is visible near {options.support_by_oi:.0f}.")
    if direction == "bearish" and options.resistance_by_oi:
        score += 8
        reasons.append(f"Call OI resistance is visible near {options.resistance_by_oi:.0f}.")
    if iv.iv_regime in {"low", "normal"} and not theta:
        score += 7
        reasons.append(f"IV regime is {iv.iv_regime}, friendlier for debit spread candidates.")
    if iv.iv_regime in {"high", "extreme"} and theta:
        score += 7
        reasons.append(f"IV regime is {iv.iv_regime}, friendlier for credit spread candidates.")
    if tech_bias not in {direction, "unclear"}:
        risks.append(f"Technical bias is {tech_bias}, so this setup needs extra confirmation.")
        score -= 25
    if options.option_bias not in {direction, "neutral", "unclear"}:
        risks.append(f"Option bias is {options.option_bias}, which conflicts with the {direction} view.")
        score -= 20
    score = _bound(score)
    if score < 45:
        return None
    return _candidate(
        strategy_id=strategy_id,
        label=label,
        horizon=horizon,
        structure=structure,
        score=score,
        required_view=direction,
        expiry_plan=_expiry_plan(horizon, options),
        legs=_placeholder_legs(direction, theta),
        max_profit_note="Defined by spread width minus/plus net premium; calculate exact payoff with selected strikes.",
        max_loss_note="Defined by spread width and net premium; validate before any action.",
        breakeven_note="Depends on selected strikes and net premium.",
        margin_note="Defined-risk spread; broker margin still depends on selected contracts.",
        best_when=f"{direction.title()} technical and option-chain context stay aligned.",
        avoid_when="Avoid when bias flips, IV regime changes sharply, or spot is near invalidation.",
        adjustment_notes="Review if spot closes beyond invalidation or OI build-up flips.",
        reasons=reasons,
        risks=risks,
        confirmations=confirmations,
    )


def _neutral_candidate(
    strategy_id: str,
    label: str,
    structure: str,
    horizon: str,
    technical: NiftyTechnicalContext,
    options: NiftyOptionContext,
    iv: NiftyIVContext,
    defined: bool,
    compact: bool = False,
) -> NiftyStrategyCandidate | None:
    score = 30
    reasons: list[str] = []
    risks: list[str] = []
    if _bias_for_horizon(technical, horizon) == "neutral":
        score += 20
        reasons.append(f"{horizon} technical context is neutral.")
    if options.option_bias == "neutral" and options.support_by_oi and options.resistance_by_oi:
        score += 25
        reasons.append(f"OI range is visible from {options.support_by_oi:.0f} to {options.resistance_by_oi:.0f}.")
    if iv.iv_regime in {"high", "extreme"}:
        score += 15
        reasons.append(f"IV regime is {iv.iv_regime}, which can suit premium collection candidates.")
    elif iv.iv_regime == "low":
        score -= 15
        risks.append("IV rank is low, so short-volatility payoff quality may be weaker.")
    if _bias_for_horizon(technical, horizon) in {"bullish", "bearish"}:
        score -= 20
        risks.append("Directional bias is active; neutral structures need range confirmation.")
    if options.option_bias == "volatile":
        score -= 25
        risks.append("Option-chain context is volatile.")
    score = _bound(score)
    if score < 45:
        return None
    return _candidate(
        strategy_id=strategy_id,
        label=label,
        horizon=horizon,
        structure=structure,
        score=score,
        required_view="neutral range",
        expiry_plan=_expiry_plan(horizon, options),
        legs=_neutral_legs(compact, defined, options),
        max_profit_note="Limited to net credit for short premium structures; exact value needs selected premiums.",
        max_loss_note="Defined for iron structures; undefined for naked short straddle/strangle.",
        breakeven_note="Approximate breakevens are short strike(s) adjusted by net premium.",
        margin_note="Defined-risk structures use wings; undefined-risk structures require larger broker margin.",
        best_when="Spot stays inside OI range, IV remains normal/high, and breakout confirmation is absent.",
        avoid_when="Avoid during clear trend breakout, event risk, or fast IV expansion.",
        adjustment_notes="Review if spot reaches range edge or OI support/resistance shifts materially.",
        reasons=reasons,
        risks=risks,
        confirmations=["Range hold confirmation", "OI walls remain stable", "No fresh breakout candle"],
    )


def _volatility_candidate(
    strategy_id: str,
    label: str,
    horizon: str,
    technical: NiftyTechnicalContext,
    options: NiftyOptionContext,
    iv: NiftyIVContext,
    compact: bool = False,
) -> NiftyStrategyCandidate | None:
    score = 35
    reasons: list[str] = []
    risks: list[str] = []
    if technical.candle_signal in {"momentum_up", "momentum_down"} or options.option_bias == "volatile":
        score += 20
        reasons.append("Price or option-chain context shows volatility risk.")
    if iv.iv_regime in {"low", "normal", "unknown"}:
        score += 15
        reasons.append(f"IV regime is {iv.iv_regime}, which can be acceptable for long volatility candidates.")
    if iv.iv_regime in {"high", "extreme"}:
        score -= 10
        risks.append("IV is already high; long-volatility candidates need a strong move expectation.")
    score = _bound(score)
    if score < 45:
        return None
    return _candidate(
        strategy_id=strategy_id,
        label=label,
        horizon=horizon,
        structure="volatility",
        score=score,
        required_view="large move, direction uncertain",
        expiry_plan=_expiry_plan(horizon, options),
        legs=_vol_legs(compact),
        max_profit_note="Upside/downside payoff expands with a large move after premium cost.",
        max_loss_note="Limited to net premium paid.",
        breakeven_note="Approximate breakevens are ATM strike plus/minus net premium.",
        margin_note="Debit structure; premium paid is the main capital outlay.",
        best_when="Compression or OI instability is followed by breakout/breakdown confirmation.",
        avoid_when="Avoid when IV rank is extreme without strong move evidence.",
        adjustment_notes="Reassess if IV collapses or price fails to expand after entry trigger.",
        reasons=reasons,
        risks=risks,
        confirmations=["Breakout or breakdown confirmation", "Volume expansion", "IV should not collapse immediately"],
    )


def _calendar_candidate(
    strategy_id: str,
    label: str,
    structure: str,
    direction: str,
    horizon: str,
    technical: NiftyTechnicalContext,
    options: NiftyOptionContext,
    iv: NiftyIVContext,
) -> NiftyStrategyCandidate | None:
    if horizon == "intraday":
        return None
    score = 45
    reasons = ["Calendar candidates are context-only until weekly/monthly premium skew is checked."]
    risks = ["Calendar payoff needs accurate front/back month premiums and volatility behavior."]
    if direction == _bias_for_horizon(technical, horizon) or direction == "neutral":
        score += 10
    if options.selected_weekly_expiry and options.selected_monthly_expiry:
        score += 10
        reasons.append("Weekly and monthly expiries are both available.")
    if iv.iv_regime in {"high", "extreme"}:
        score += 5
    score = _bound(score)
    return _candidate(
        strategy_id=strategy_id,
        label=label,
        horizon=horizon,
        structure=structure,
        score=score,
        required_view=direction,
        expiry_plan="Use weekly/monthly comparison; exact strikes require payoff review.",
        legs=[],
        max_profit_note="Calendar max profit is path and IV dependent.",
        max_loss_note="Risk depends on net debit/credit and hedge construction.",
        breakeven_note="Requires selected premiums; use payoff viewer after entering legs.",
        margin_note="Margin depends on calendar spread construction and broker rules.",
        best_when="Front expiry IV is meaningfully richer than back expiry and spot is near planned short strike.",
        avoid_when="Avoid when a strong trend breakout is active or front IV is not rich.",
        adjustment_notes="Review on IV spread collapse, OI bias flip, or strike breach.",
        reasons=reasons,
        risks=risks,
        confirmations=["Weekly/monthly IV spread should be visible", "Spot should remain near planned short strike"],
    )


def _candidate(
    strategy_id: str,
    label: str,
    horizon: str,
    structure: str,
    score: int,
    required_view: str,
    expiry_plan: str,
    legs: list[dict],
    max_profit_note: str,
    max_loss_note: str,
    breakeven_note: str,
    margin_note: str,
    best_when: str,
    avoid_when: str,
    adjustment_notes: str,
    reasons: list[str],
    risks: list[str],
    confirmations: list[str],
) -> NiftyStrategyCandidate:
    return NiftyStrategyCandidate(
        strategy_id=strategy_id,
        label=label,
        horizon=horizon,
        structure=structure,
        suitability_score=score,
        confidence="high" if score >= 75 else "medium" if score >= 60 else "low",
        required_view=required_view,
        expiry_plan=expiry_plan,
        legs=legs,
        max_profit_note=max_profit_note,
        max_loss_note=max_loss_note,
        breakeven_note=breakeven_note,
        margin_note=margin_note,
        best_when=best_when,
        avoid_when=avoid_when,
        adjustment_notes=adjustment_notes,
        reasons=reasons or ["Setup is a low-confidence candidate; validate all context before use."],
        risks=risks,
        required_confirmations=confirmations,
    )


def _bias_for_horizon(technical: NiftyTechnicalContext, horizon: str) -> str:
    if horizon == "intraday":
        return technical.bias_intraday
    if horizon == "positional":
        return technical.bias_positional
    return technical.bias_swing


def _horizon(mode: str, technical: NiftyTechnicalContext) -> str:
    if mode in {"intraday", "swing", "positional"}:
        return mode
    if technical.timeframe in {"5minute", "15minute"}:
        return "intraday"
    return "swing"


def _expiry_plan(horizon: str, options: NiftyOptionContext) -> str:
    if horizon == "positional" and options.selected_monthly_expiry:
        return f"Prefer monthly expiry context: {options.selected_monthly_expiry}."
    if options.selected_weekly_expiry:
        return f"Prefer weekly expiry context: {options.selected_weekly_expiry}."
    return "Select expiry after option-chain data is available."


def _placeholder_legs(direction: str, theta: bool) -> list[dict]:
    option_type = "PE" if direction == "bullish" and theta else "CE" if direction == "bullish" else "CE" if theta else "PE"
    return [{"side": "candidate", "option_type": option_type, "strike": None, "premium": None}]


def _neutral_legs(compact: bool, defined: bool, options: NiftyOptionContext) -> list[dict]:
    if compact:
        return [{"side": "sell", "option_type": "CE/PE", "strike": options.atm_strike, "premium": None}]
    legs = [
        {"side": "sell", "option_type": "PE", "strike": options.support_by_oi, "premium": None},
        {"side": "sell", "option_type": "CE", "strike": options.resistance_by_oi, "premium": None},
    ]
    if defined:
        legs.extend(
            [
                {"side": "buy", "option_type": "PE", "strike": None, "premium": None},
                {"side": "buy", "option_type": "CE", "strike": None, "premium": None},
            ]
        )
    return legs


def _vol_legs(compact: bool) -> list[dict]:
    strike = "ATM" if compact else "OTM"
    return [
        {"side": "buy", "option_type": "CE", "strike": strike, "premium": None},
        {"side": "buy", "option_type": "PE", "strike": strike, "premium": None},
    ]


def _is_undefined(candidate: NiftyStrategyCandidate) -> bool:
    return candidate.strategy_id in {"nifty_short_straddle", "nifty_short_strangle"}


def _bound(value: int) -> int:
    return max(0, min(100, int(round(value))))
