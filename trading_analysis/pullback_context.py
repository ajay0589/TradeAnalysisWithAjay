"""As-of daily market/sector gates shared by pullback research and live scans."""
from bisect import bisect_right
from collections import Counter
from pathlib import Path

from trading_analysis.analysis.market_structure import analyze_market_structure
from trading_analysis.analysis.relative_strength import load_sector_map, sector_config_for_symbol
from trading_analysis.analysis.technical import ema, sma
from trading_analysis.candles import candle_path
from trading_analysis.data_sources.csv_loader import load_candles
from trading_analysis.live_timing import as_ist, expected_closed_bar
from trading_analysis.nifty.live_scanner import candle_close_time

BENCHMARKS = {f"NIFTY {n}": f"NIFTY_{n}" for n in (50, 100, 200, 500)}
PULLBACKS = {"bullish_pullback", "bearish_pullback"}


def filter_settings(values=None):
    values = dict(values or {})
    if set(values) - {"market", "sector", "benchmark", "structure"}:
        raise ValueError("Unknown pullback context filter")
    result = {"market": False, "sector": False, "benchmark": "NIFTY 50", "structure": True, **values}
    if result["benchmark"] not in BENCHMARKS:
        raise ValueError("Choose NIFTY 50, NIFTY 100, NIFTY 200 or NIFTY 500")
    if any(not isinstance(result[key], bool) for key in ("market", "sector", "structure")):
        raise ValueError("Context switches must be boolean")
    return result


def daily_regime(candles, require_structure=True):
    if len(candles) < 55:
        return {"direction": "missing", "reason": "At least 55 closed daily candles required"}
    values = [row.close for row in candles]
    fast, slow, previous = ema(values, 20), sma(values, 50), sma(values[:-5], 50)
    structure = analyze_market_structure(candles).trend
    direction = "neutral"
    if values[-1] > slow and fast > slow > previous and (not require_structure or structure == "uptrend"):
        direction = "bullish"
    elif values[-1] < slow and fast < slow < previous and (not require_structure or structure == "downtrend"):
        direction = "bearish"
    return {"direction": direction, "close": values[-1], "ema20": fast, "sma50": slow,
            "sma50_five_bars_ago": previous, "structure": structure,
            "candle_time": as_ist(candles[-1].timestamp).isoformat()}


class PullbackContext:
    def __init__(self, service, settings=None):
        self.settings = filter_settings(settings)
        self.root = service.daily_data_dir
        self.sectors = load_sector_map(service.sector_map_path) if self.settings["sector"] else {}
        self._series = {}
        self._readings = {}
        self.counts = Counter()

    def sources(self, symbol):
        result = []
        if self.settings["market"]:
            benchmark = self.settings["benchmark"]
            result.append(("market", benchmark, BENCHMARKS[benchmark]))
        if self.settings["sector"]:
            sector = sector_config_for_symbol(self.sectors, symbol)
            result.append(("sector", sector["index_symbol"] if sector else "unmapped",
                           Path(sector["data_file"]).stem if sector else None))
        return result

    def reading(self, stem, decision_time):
        if stem is None:
            return {"direction": "missing", "reason": "No sector mapping"}
        if stem not in self._series:
            path = candle_path(self.root, "day", stem)
            rows = sorted(load_candles(path), key=lambda row: row.timestamp) if path.exists() else []
            self._series[stem] = (rows, [candle_close_time(row.timestamp, "day") for row in rows])
        rows, closed = self._series[stem]
        end = bisect_right(closed, as_ist(decision_time))
        if end == 0:
            return {"direction": "missing", "reason": "No daily candle available as of decision"}
        expected = expected_closed_bar("day", decision_time).date()
        if as_ist(rows[end - 1].timestamp).date() != expected:
            return {"direction": "stale", "reason": "Required closed daily candle unavailable",
                    "candle_time": as_ist(rows[end - 1].timestamp).isoformat(), "expected_date": str(expected)}
        key = (stem, end)
        if key not in self._readings:
            self._readings[key] = daily_regime(rows[:end], self.settings["structure"])
        return self._readings[key]

    def check(self, symbol, side, decision_time):
        desired = "bullish" if side == "long" else "bearish"
        evidence = {}
        reasons = []
        for kind, label, stem in self.sources(symbol):
            row = self.reading(stem, decision_time)
            evidence[kind] = {"index": label, **row}
            if row["direction"] != desired:
                reasons.append(f"{kind}_{row['direction']}")
        result = {"passed": not reasons, "reason": ", ".join(reasons) or "aligned",
                  "decision_time": as_ist(decision_time).isoformat(), "evidence": evidence}
        self.counts[result["reason"]] += 1
        return result
