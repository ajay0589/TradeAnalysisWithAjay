from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from trading_analysis.config import load_dotenv


@dataclass(frozen=True)
class TelegramNotifier:
    bot_token: str | None = None
    chat_id: str | None = None
    timeout_seconds: int = 8

    @classmethod
    def from_env(cls) -> "TelegramNotifier":
        load_dotenv()
        return cls(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        )

    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_message(self, text: str) -> dict[str, Any]:
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
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
            return {"sent": bool(body.get("ok")), "configured": True, "response": body}
        except URLError as exc:
            return {"sent": False, "configured": True, "error": str(exc.reason)}
        except Exception as exc:
            return {"sent": False, "configured": True, "error": str(exc)}


def purple_alert_message(alert: dict[str, Any], trade: dict[str, Any] | None = None) -> str:
    profile = _profile_label(alert.get("purple_timeframe"))
    parts = [
        f"Purple Touch {str(alert.get('alert_type') or '').title()} Alert",
        f"Symbol: {alert.get('symbol') or '-'}",
        f"Profile: {profile}",
        f"Entry: {alert.get('entry_kind') or '-'}",
        f"Trade ID: {alert.get('trade_id') or '-'}",
        f"Price / Yellow: {_fmt(alert.get('price'))} / {_fmt(alert.get('yellow_line'))}",
        f"Score: {_fmt(alert.get('score'))} ({alert.get('confidence') or '-'})",
        f"Status: {alert.get('status') or '-'}",
        str(alert.get("message") or ""),
    ]
    if trade and trade.get("exit_timeframe"):
        parts.append(f"Exit timeframe: {trade.get('exit_timeframe')}")
    reasons = alert.get("reasons") or []
    if reasons:
        parts.append("Why: " + "; ".join(str(reason) for reason in reasons[:3]))
    warnings = alert.get("warnings") or []
    if warnings:
        parts.append("Risk context: " + "; ".join(str(warning) for warning in warnings[:2]))
    return "\n".join(part for part in parts if part)


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
