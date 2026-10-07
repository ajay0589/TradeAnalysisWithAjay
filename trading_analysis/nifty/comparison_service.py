"""Read-only research endpoints shared by the three index profiles."""
from datetime import datetime, timedelta

from trading_analysis.candles import candle_path, candle_window
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.live_timing import IST, as_ist
from trading_analysis.nifty.comparison_backtest import compare_setups
from trading_analysis.option_history import OptionHistory

SYMBOLS = {"NIFTY": "NIFTY_50", "BANKNIFTY": "NIFTY_BANK", "SENSEX": "SENSEX"}


def parameters(payload):
    symbol = str(payload.get("symbol") or "NIFTY").upper()
    horizon = payload.get("horizon") or "intraday"
    trigger = payload.get("trigger") or "standard"
    if symbol not in SYMBOLS or horizon not in {"intraday", "swing", "positional"}:
        raise ValueError("Choose an index and a valid horizon")
    if trigger not in {"standard", "15m_5m"} or trigger == "15m_5m" and horizon != "intraday":
        raise ValueError("15m trend / 5m trigger requires Intraday")
    now = datetime.now(IST)
    end = min(now, as_ist(payload["to_date"]) + timedelta(days=1) - timedelta(microseconds=1)) if payload.get("to_date") else now
    start = as_ist(payload["from_date"]) if payload.get("from_date") else end - timedelta(days=90)
    if start > end or (end - start).days > 1826:
        raise ValueError("Choose a valid date range of at most five years")
    frame = "5minute" if trigger == "15m_5m" else {"intraday": "15minute", "swing": "60minute", "positional": "day"}[horizon]
    return symbol, horizon, trigger, start, end, frame


def run_comparison(payload, analysis, db_path):
    symbol, horizon, trigger, start, end, frame = parameters(payload)
    def candles(timeframe):
        path = candle_path(analysis.daily_data_dir, timeframe, SYMBOLS[symbol])
        return load_candles(path) if path.exists() else []
    snapshots = OptionHistory(db_path).load(symbol, start - timedelta(minutes=45), end)
    result = compare_setups(candles(frame), horizon, snapshots, trigger=trigger,
                            trend_candles=candles("15minute") if trigger == "15m_5m" else None,
                            from_date=start.isoformat(), to_date=end.date().isoformat(),
                            direction=payload.get("direction") or "both",
                            target_r=float(payload.get("target_r_multiple", 2)),
                            max_bars=int(payload.get("max_holding_bars", 8)),
                            cost_bps=float(payload.get("cost_bps_per_side", 2)))
    return {**result, "symbol": symbol, "method": "comparison"}


def refresh_comparison_data(payload, analysis):
    symbol, _, trigger, start, end, frame = parameters(payload)
    warmup = 365 if frame == "day" else 21
    window = candle_window(from_date=(start - timedelta(days=warmup)).date().isoformat(),
                           to_date=end.date().isoformat())
    results = []
    for timeframe in (["5minute", "15minute"] if trigger == "15m_5m" else [frame]):
        results.extend(analysis.refresh_candles(symbol, timeframe, window))
    return {"symbol": symbol, "results": results, "option_history_downloaded": False}
