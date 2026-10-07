"""NIFTY market-hour scan scheduler."""

from trading_analysis.scheduler.market_hours import is_market_day, is_market_hours, next_market_open

__all__ = ["MarketScanScheduler", "is_market_day", "is_market_hours", "next_market_open"]


def __getattr__(name):
    if name == "MarketScanScheduler":
        from trading_analysis.scheduler.runner import MarketScanScheduler
        return MarketScanScheduler
    raise AttributeError(name)
