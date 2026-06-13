from __future__ import annotations

from trading_analysis.models import CombinedSignal


def render_signal_table(signals: list[CombinedSignal]) -> str:
    headers = [
        "Symbol",
        "Score",
        "Label",
        "Close",
        "RSI14",
        "ATR14",
        "Vol x20",
        "Trend",
        "Top Reasons",
    ]
    rows = [
        [
            signal.symbol,
            str(signal.score),
            signal.label,
            _fmt(signal.technical.close),
            _fmt(signal.technical.rsi14),
            _fmt(signal.technical.atr14),
            _fmt(signal.technical.volume_ratio20),
            signal.technical.trend,
            "; ".join((signal.technical.reasons + signal.fundamental.reasons)[:3]),
        ]
        for signal in sorted(signals, key=lambda item: item.score, reverse=True)
    ]
    return _plain_table(headers, rows)


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _plain_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows)) if rows else len(headers[index])
        for index in range(len(headers))
    ]
    header_line = " | ".join(headers[index].ljust(widths[index]) for index in range(len(headers)))
    separator = "-+-".join("-" * width for width in widths)
    body = [
        " | ".join(row[index].ljust(widths[index]) for index in range(len(headers)))
        for row in rows
    ]
    return "\n".join([header_line, separator, *body])

