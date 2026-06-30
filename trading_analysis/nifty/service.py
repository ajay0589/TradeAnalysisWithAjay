from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Any

from trading_analysis.analysis.options import OptionChainAnalysis, OptionChainRow
from trading_analysis.candles import candle_path, candle_window, prepare_candles
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.nifty.backtest import backtest_nifty_context
from trading_analysis.nifty.iv_context import DEFAULT_IV_HISTORY_PATH, build_nifty_iv_context, record_nifty_iv_snapshot
from trading_analysis.nifty.models import NiftyDeskResult, to_jsonable
from trading_analysis.nifty.option_context import build_nifty_option_context
from trading_analysis.nifty.strategy_payoff import calculate_strategy_payoff
from trading_analysis.nifty.strategy_suggester import suggest_nifty_strategies
from trading_analysis.nifty.technical_context import build_nifty_technical_context


class NiftyDeskService:
    def __init__(
        self,
        candle_root: str | Path = "data/raw/candles",
        option_chain_dir: str | Path = "data/raw/option_chain",
        iv_history_path: str | Path = DEFAULT_IV_HISTORY_PATH,
        analysis_service: Any | None = None,
    ) -> None:
        self.candle_root = Path(candle_root)
        self.option_chain_dir = Path(option_chain_dir)
        self.iv_history_path = Path(iv_history_path)
        self.analysis_service = analysis_service

    def nifty_context(
        self,
        mode: str = "auto",
        weekly_expiry: str | None = None,
        monthly_expiry: str | None = None,
        include_option_chain: bool = True,
        include_iv: bool = True,
        refresh: bool = False,
        timeframe: str = "15minute",
        days: int = 30,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        errors: list[str] = []
        refresh_results = []
        if refresh:
            refresh_results = self._refresh_latest_candles(timeframe=timeframe, days=days, to_date=to_date, warnings=warnings)
        daily_candles = self._load_candles("day", days=max(days, 365), to_date=to_date, warnings=warnings)
        hourly_candles = self._load_candles("60minute", days=max(days, 90), to_date=to_date, warnings=warnings)
        minute15_candles = self._load_candles("15minute", days=max(days, 45), to_date=to_date, warnings=warnings)
        technical = None
        options = None
        iv = None
        try:
            technical = build_nifty_technical_context(
                daily_candles=daily_candles,
                hourly_candles=hourly_candles,
                minute15_candles=minute15_candles,
                mode=mode,
            )
            warnings.extend(technical.warnings)
        except Exception as exc:
            errors.append(str(exc))

        weekly_chain = monthly_chain = None
        if include_option_chain:
            weekly_chain = self._load_option_chain(weekly_expiry, warnings)
            monthly_chain = self._load_option_chain(monthly_expiry, warnings) if monthly_expiry else None
            options = build_nifty_option_context(
                weekly_chain=weekly_chain,
                monthly_chain=monthly_chain,
                spot=technical.spot if technical else None,
            )
            warnings.extend(options.warnings)
        else:
            warnings.append("Option-chain context was not requested.")

        if include_iv:
            current_iv = options.atm_iv if options else None
            current_iv_change = options.atm_iv_change if options else None
            if refresh and options and options.atm_iv is not None:
                record_nifty_iv_snapshot(
                    expiry=options.selected_weekly_expiry,
                    atm_strike=options.atm_strike,
                    atm_iv=options.atm_iv,
                    weekly_atm_iv=options.weekly_chain_summary.get("atm_iv") if options.weekly_chain_summary else None,
                    monthly_atm_iv=options.monthly_chain_summary.get("atm_iv") if options.monthly_chain_summary else None,
                    path=self.iv_history_path,
                )
            iv = build_nifty_iv_context(current_atm_iv=current_iv, current_iv_change=current_iv_change, history_path=self.iv_history_path)
            warnings.extend(iv.warnings)
        else:
            warnings.append("IV context was not requested.")

        result = NiftyDeskResult(
            symbol="NIFTY",
            as_of=datetime.now(),
            mode=mode,
            technical=technical,
            options=options,
            iv=iv,
            candidates=[],
            summary={
                "points": _summary_points(technical, options, iv),
                "refresh_results": refresh_results,
                "candle_sources": {
                    "daily": _candle_source_summary(daily_candles),
                    "60minute": _candle_source_summary(hourly_candles),
                    "15minute": _candle_source_summary(minute15_candles),
                },
                "analysis_date": to_date,
                "timeframe": timeframe,
            },
            warnings=_dedupe(warnings),
            errors=errors,
        )
        return to_jsonable(result)

    def nifty_strategy_suggestions(
        self,
        mode: str = "auto",
        weekly_expiry: str | None = None,
        monthly_expiry: str | None = None,
        allowed_strategies: list[str] | None = None,
        risk_profile: str = "defined",
        refresh: bool = False,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        context = self.nifty_context(
            mode=mode,
            weekly_expiry=weekly_expiry,
            monthly_expiry=monthly_expiry,
            include_option_chain=True,
            include_iv=True,
            refresh=refresh,
            to_date=to_date,
        )
        technical = context.get("technical")
        options = context.get("options")
        iv = context.get("iv")
        if not technical or not options or not iv:
            return {**context, "candidates": [], "errors": context.get("errors", []) + ["Technical, option, and IV context are required for suggestions."]}
        from trading_analysis.nifty.models import NiftyIVContext, NiftyOptionContext, NiftyTechnicalContext

        candidates = suggest_nifty_strategies(
            technical_context=NiftyTechnicalContext(**technical),
            option_context=NiftyOptionContext(**options),
            iv_context=NiftyIVContext(**iv),
            mode=mode,
            allowed_strategies=allowed_strategies,
            risk_profile=risk_profile,
        )
        context["candidates"] = to_jsonable(candidates)
        context["summary"]["candidate_count"] = len(candidates)
        return context

    def nifty_payoff(self, payload: dict[str, Any]) -> dict[str, Any]:
        return calculate_strategy_payoff(
            spot=float(payload.get("spot")),
            lot_size=int(payload.get("lot_size") or 75),
            legs=list(payload.get("legs") or []),
            spot_range=payload.get("spot_range"),
        )

    def nifty_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        warnings: list[str] = []
        candles = self._load_candles("day", days=int(payload.get("days") or 730), warnings=warnings)
        result = backtest_nifty_context(
            candles=candles,
            strategy_id=str(payload.get("strategy_id") or payload.get("strategy") or ""),
            mode=str(payload.get("mode") or "swing"),
            from_date=payload.get("from_date") or None,
            to_date=payload.get("to_date") or None,
            days=int(payload.get("days")) if payload.get("days") else None,
            params=payload.get("params") or {},
            exit_rules=payload.get("exit_rules") or {},
        )
        result["warnings"] = _dedupe(warnings + list(result.get("warnings") or []))
        return to_jsonable(result)

    def _load_candles(self, timeframe: str, days: int, to_date: str | None, warnings: list[str]) -> list:
        path = candle_path(self.candle_root, timeframe, "NIFTY_50")
        try:
            raw = load_candles(path)
        except FileNotFoundError:
            warnings.append(f"Missing NIFTY {timeframe} candles at {path}.")
            return []
        return prepare_candles(raw, timeframe, candle_window(days=days, to_date=to_date))

    def _refresh_latest_candles(self, timeframe: str, days: int, to_date: str | None, warnings: list[str]) -> list[dict[str, Any]]:
        if self.analysis_service is None:
            warnings.append("Refresh requested, but no refresh service is attached. Start the Web UI through scripts/start_web_ui.ps1.")
            return []
        timeframes = _refresh_timeframes(timeframe)
        results: list[dict[str, Any]] = []
        for item in timeframes:
            try:
                window = candle_window(days=_refresh_days(item, days), to_date=to_date)
                # Existing AnalysisService refresh uses read-only historical candle APIs and writes local CSV cache.
                results.extend(self.analysis_service.refresh_candles("NIFTY", item, window))
            except Exception as exc:
                warnings.append(f"Refresh failed for NIFTY {item}: {exc}")
        return results

    def _load_option_chain(self, expiry: str | None, warnings: list[str]) -> OptionChainAnalysis | None:
        path = self._option_snapshot_path(expiry)
        if path is None:
            warnings.append("No cached NIFTY option-chain snapshot found.")
            return None
        try:
            return _read_option_chain_snapshot(path)
        except Exception as exc:
            warnings.append(f"Could not load NIFTY option snapshot {path}: {exc}")
            return None

    def _option_snapshot_path(self, expiry: str | None) -> Path | None:
        if expiry:
            path = self.option_chain_dir / f"NIFTY_{expiry}.csv"
            return path if path.exists() else None
        direct = sorted(self.option_chain_dir.glob("NIFTY_*.csv"), key=lambda item: item.stat().st_mtime, reverse=True)
        return direct[0] if direct else None


def _read_option_chain_snapshot(path: Path) -> OptionChainAnalysis:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("Snapshot has no rows.")
    option_rows = [_row_from_csv(row) for row in rows]
    symbol = rows[0].get("symbol") or "NIFTY"
    expiry = date.fromisoformat(rows[0]["expiry"])
    spot = _infer_spot(option_rows)
    ce_rows = [row for row in option_rows if row.option_type == "CE"]
    pe_rows = [row for row in option_rows if row.option_type == "PE"]
    ce_oi = sum(row.oi for row in ce_rows)
    pe_oi = sum(row.oi for row in pe_rows)
    total_oi = ce_oi + pe_oi
    previous_total = sum(row.previous_oi for row in option_rows if row.previous_oi is not None)
    total_change = total_oi - previous_total if previous_total else None
    return OptionChainAnalysis(
        symbol=symbol.upper(),
        expiry=expiry,
        spot_price=spot,
        contract_count=len(option_rows),
        pcr_oi=(pe_oi / ce_oi) if ce_oi else None,
        max_pain=_max_pain(option_rows),
        atm_iv=_atm_iv(option_rows, spot),
        atm_iv_change=_atm_iv_change(option_rows, spot),
        iv_percentile=None,
        total_volume=sum(row.volume for row in option_rows),
        total_oi_change=total_change,
        total_oi_change_percent=(total_change / previous_total * 100) if previous_total else None,
        highest_call_oi_strike=max(ce_rows, key=lambda row: row.oi).strike if ce_rows else None,
        highest_put_oi_strike=max(pe_rows, key=lambda row: row.oi).strike if pe_rows else None,
        rows=tuple(sorted(option_rows, key=lambda row: (row.strike, row.option_type))),
    )


def _row_from_csv(row: dict[str, str]) -> OptionChainRow:
    return OptionChainRow(
        tradingsymbol=row.get("tradingsymbol", ""),
        strike=float(row.get("strike") or 0),
        option_type=(row.get("option_type") or "").upper(),
        last_price=float(row.get("last_price") or 0),
        previous_close=_float(row.get("previous_close")),
        price_change=_float(row.get("price_change")),
        oi=int(float(row.get("oi") or 0)),
        previous_oi=_int(row.get("previous_oi")),
        oi_change=_int(row.get("oi_change")),
        oi_change_percent=_float(row.get("oi_change_percent")),
        implied_volatility=_float(row.get("implied_volatility")),
        iv_change=_float(row.get("iv_change")),
        volume=int(float(row.get("volume") or 0)),
        bid_price=_float(row.get("bid_price")),
        ask_price=_float(row.get("ask_price")),
        buildup=row.get("buildup") or "Needs previous OI snapshot",
    )


def _infer_spot(rows: list[OptionChainRow]) -> float | None:
    if not rows:
        return None
    strikes = sorted({row.strike for row in rows})
    return strikes[len(strikes) // 2] if strikes else None


def _atm_rows(rows: list[OptionChainRow], spot: float | None) -> list[OptionChainRow]:
    if not rows:
        return []
    strike = min({row.strike for row in rows}, key=lambda value: abs(value - (spot or value)))
    return [row for row in rows if row.strike == strike]


def _atm_iv(rows: list[OptionChainRow], spot: float | None) -> float | None:
    values = [row.implied_volatility for row in _atm_rows(rows, spot) if row.implied_volatility is not None]
    return (sum(values) / len(values)) if values else None


def _atm_iv_change(rows: list[OptionChainRow], spot: float | None) -> float | None:
    values = [row.iv_change for row in _atm_rows(rows, spot) if row.iv_change is not None]
    return (sum(values) / len(values)) if values else None


def _max_pain(rows: list[OptionChainRow]) -> float | None:
    strikes = sorted({row.strike for row in rows})
    if not strikes:
        return None
    pain = {}
    for settlement in strikes:
        value = 0.0
        for row in rows:
            if row.option_type == "CE":
                value += max(0.0, settlement - row.strike) * row.oi
            else:
                value += max(0.0, row.strike - settlement) * row.oi
        pain[settlement] = value
    return min(pain, key=pain.get)


def _summary_points(technical, options, iv) -> list[str]:
    points = []
    if technical:
        points.append(f"Technical context: intraday {technical.bias_intraday}, swing {technical.bias_swing}, positional {technical.bias_positional}.")
    if options:
        points.append(f"Option context: bias {options.option_bias}, PCR {options.pcr_oi}, max pain {options.max_pain}.")
    if iv:
        points.append(f"IV context: regime {iv.iv_regime}, rank {iv.iv_rank}.")
    return points


def _refresh_timeframes(timeframe: str) -> list[str]:
    requested = (timeframe or "15minute").lower()
    output = ["day"]
    if requested in {"60minute", "15minute"}:
        output.append("60minute")
    if requested == "15minute":
        output.append("15minute")
    return output


def _refresh_days(timeframe: str, days: int) -> int:
    return max(days, {"day": 365, "60minute": 90, "15minute": 45}.get(timeframe, days))


def _candle_source_summary(candles: list) -> dict[str, Any]:
    return {
        "count": len(candles),
        "from": candles[0].timestamp if candles else None,
        "to": candles[-1].timestamp if candles else None,
        "latest_close": candles[-1].close if candles else None,
    }


def _float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output
