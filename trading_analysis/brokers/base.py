from __future__ import annotations

from datetime import datetime
from typing import Protocol

from trading_analysis.models import Candle


class HistoricalDataClient(Protocol):
    def historical_candles(
        self,
        instrument_token: str,
        interval: str,
        from_time: datetime,
        to_time: datetime,
    ) -> list[Candle]:
        """Return historical candles for one instrument."""

