from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from trading_analysis.config import load_dotenv
from trading_analysis import diagnostics
from trading_analysis.network import https_context, network_hint, tls_info, pooled_urlopen as urlopen


@dataclass(frozen=True)
class TelegramNotifier:
    bot_token: str | None = None
    chat_id: str | None = None
    timeout_seconds: int = 8

    @classmethod
    def from_env(cls, prefix: str = "") -> "TelegramNotifier":
        load_dotenv()
        normalized = prefix.upper()
        return cls(
            bot_token=os.getenv(f"{normalized}TELEGRAM_BOT_TOKEN") or None,
            chat_id=os.getenv(f"{normalized}TELEGRAM_CHAT_ID") or None,
        )

    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_message(self, text: str) -> dict[str, Any]:
        started = time.monotonic()
        result = self._send_message(text)
        hint = network_hint(str(result.get("error") or ""))
        if hint:
            result["hint"] = hint
            result["error"] = f"{result['error']} {hint}"
        diagnostics.record("telegram_delivery", area="notifications", status="sent" if result.get("sent") else "failed",
                           error=result.get("error"), duration_ms=int((time.monotonic() - started) * 1000), tls=tls_info())
        return result

    def _send_message(self, text: str) -> dict[str, Any]:
        if os.getenv("TRADING_DISABLE_TELEGRAM") == "1":
            return {"sent": False, "configured": self.configured(), "error": "Delivery disabled for verification", "delivery_status": "disabled"}
        if not self.configured():
            return {"sent": False, "configured": False, "error": "Telegram bot token/chat id not configured."}
        payload = {
            "chat_id": self.chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        }
        request = Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds, context=https_context()) as response:
                body = json.loads(response.read().decode("utf-8"))
            error = None if body.get("ok") else str(body.get("description") or "Telegram rejected the message")
            return {"sent": bool(body.get("ok")), "configured": True, "response": body,
                    "delivery_status": "sent" if body.get("ok") else "failed",
                    "error": error.replace(self.bot_token, "[redacted]") if error else None}
        except HTTPError as exc:
            body = {}
            try:
                body = json.loads(exc.read().decode("utf-8"))
                reason = str(body.get("description") or exc.reason)
            except (ValueError, UnicodeError):
                reason = str(exc.reason)
            finally:
                exc.close()
            return {"sent": False, "configured": True, "delivery_status": "retry" if exc.code == 429 else "failed" if exc.code < 500 else "delivery_unknown",
                    "retry_after": max(5, int((body.get("parameters") or {}).get("retry_after", 30))),
                    "error": f"HTTP {exc.code}: {reason}".replace(self.bot_token, "[redacted]")}
        except URLError as exc:
            import socket
            safe_retry = isinstance(exc.reason, (ConnectionRefusedError, socket.gaierror))
            return {"sent": False, "configured": True, "delivery_status": "retry" if safe_retry else "delivery_unknown", "error": str(exc.reason).replace(self.bot_token, "[redacted]")}
        except Exception as exc:
            return {"sent": False, "configured": True, "delivery_status": "delivery_unknown", "error": str(exc).replace(self.bot_token, "[redacted]")}


def purple_alert_message(alert: dict[str, Any], trade: dict[str, Any] | None = None) -> str:
    alert_type = str(alert.get("alert_type") or "alert").lower()
    profile = _profile_label(alert.get("purple_timeframe"))
    entry_kind = str(alert.get("entry_kind") or "-").title()
    entry_timeframe = (trade or {}).get("entry_timeframe")
    exit_timeframe = (trade or {}).get("exit_timeframe")
    parts = [
        f"PURPLE TOUCH {alert_type.upper()} ALERT",
        f"Symbol: {alert.get('symbol') or '-'}",
        f"Profile: {profile}",
        f"Trade ID: {alert.get('trade_id') or '-'}",
        f"{'Original entry' if alert_type == 'exit' else 'Entry'}: {entry_kind}"
        + (f" ({entry_timeframe})" if entry_timeframe else ""),
        f"Price / Yellow: {_fmt(alert.get('price'))} / {_fmt(alert.get('yellow_line'))}",
        f"Score: {_fmt(alert.get('score'))} ({alert.get('confidence') or '-'})",
        f"Status: {alert.get('status') or '-'}",
        str(alert.get("message") or ""),
    ]
    if exit_timeframe:
        parts.append(f"Exit confirmation timeframe: {exit_timeframe}")
    reasons = alert.get("reasons") or []
    if reasons:
        parts.append("Why: " + "; ".join(str(reason) for reason in reasons[:3]))
    warnings = alert.get("warnings") or []
    if warnings:
        parts.append("Risk context: " + "; ".join(str(warning) for warning in warnings[:2]))
    return "\n".join(part for part in parts if part)


def nifty_trade_message(event_kind: str, trade: dict[str, Any], alert: dict[str, Any] | None = None) -> str:
    event = event_kind.upper()
    parts = [
        f"{trade.get('symbol') or 'NIFTY'} {event} ALERT",
        "SPOT SIGNAL - indicative prices, not executed fills",
        f"Trade ID: {trade.get('trade_id') or '-'}",
        f"Horizon: {str(trade.get('horizon') or '-').title()} ({trade.get('entry_timeframe') or '-'})",
        f"Direction / Strategy: {str(trade.get('direction') or '-').title()} / {trade.get('strategy_id') or '-'}",
        f"Entry spot: {_fmt(trade.get('entry_price'))} at {_ist_time(trade.get('entry_time'))}",
    ]
    if event_kind.lower() == "exit":
        parts.extend(
            [
                f"Exit reference/spot: {_fmt(trade.get('exit_price'))} at {_ist_time(trade.get('exit_time'))}",
                f"Open time: {trade.get('open_duration') or '-'}",
                f"Result: {_fmt(trade.get('directional_return_percent'))}%",
                f"Reason: {trade.get('exit_reason') or '-'}",
            ]
        )
    else:
        parts.extend(
            [
                f"Stop / Target: {_fmt(trade.get('stop_level'))} / {_fmt(trade.get('target_level'))}",
                f"Risk/Reward: 1:{_fmt(trade.get('target_r_multiple'))}",
                f"Score: {_fmt(trade.get('score'))} ({trade.get('confidence') or '-'})",
            ]
        )
    if alert and alert.get("message"):
        parts.append(str(alert["message"]))
    metadata = trade.get("metadata") or {}
    if event_kind.lower() == "entry" and metadata.get("signal_candle_closed_at"):
        parts.append(f"Price basis: {metadata.get('price_basis') or 'candle reference'}")
        parts.append(f"Signal candle close: {_fmt(metadata.get('reference_price') or metadata.get('candle_close'))} at {_ist_time(metadata['signal_candle_closed_at'])}")
        parts.append(f"Signal detected: {_ist_time(metadata.get('signal_detected_at') or trade.get('created_at'))}")
        quote = metadata.get("spot_quote") or {}
        parts.append(f"Spot observed: {_ist_time(quote.get('quote_time'))} ({quote.get('source') or 'unavailable'})")
    if event_kind.lower() == "exit":
        parts.append(f"Exit price basis: {metadata.get('exit_price_basis') or 'legacy candle reference'}")
        if metadata.get("exit_reference_price") is not None:
            parts.append(f"Candle/level reference: {_fmt(metadata['exit_reference_price'])}")
        if metadata.get("recovered_event"):
            parts.append("RECOVERED HISTORICAL EXIT - not a new live trading instruction")
    parts.append(
        "Context: "
        f"option {metadata.get('option_bias') or '-'}, PCR {_fmt(metadata.get('pcr_oi'))}, "
        f"IV {metadata.get('iv_regime') or '-'} (rank {_fmt(metadata.get('iv_rank'))})"
    )
    return "\n".join(parts)


def _ist_time(value):
    if not value:
        return "-"
    from trading_analysis.live_timing import as_ist
    try:
        return as_ist(value).strftime("%d %b %Y %I:%M:%S %p IST")
    except (ValueError, TypeError):
        return str(value)


def _profile_label(value: Any) -> str:
    return {
        "month": "Monthly",
        "week": "Weekly",
        "day": "Daily",
    }.get(str(value or ""), str(value or "-"))


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)
