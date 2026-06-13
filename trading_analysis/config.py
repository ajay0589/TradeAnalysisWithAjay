from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from trading_analysis.models import WatchlistItem, fundamentals_from_mapping


@dataclass(frozen=True)
class BrokerCredentials:
    zerodha_api_key: str | None
    zerodha_api_secret: str | None
    zerodha_access_token: str | None
    angel_one_api_key: str | None
    angel_one_client_code: str | None
    angel_one_pin: str | None
    angel_one_totp_secret: str | None


@dataclass(frozen=True)
class Settings:
    trading_mode: str
    broker_credentials: BrokerCredentials


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        trading_mode=os.getenv("TRADING_MODE", "paper").lower(),
        broker_credentials=BrokerCredentials(
            zerodha_api_key=_env("ZERODHA_API_KEY"),
            zerodha_api_secret=_env("ZERODHA_API_SECRET"),
            zerodha_access_token=_env("ZERODHA_ACCESS_TOKEN"),
            angel_one_api_key=_env("ANGEL_ONE_API_KEY"),
            angel_one_client_code=_env("ANGEL_ONE_CLIENT_CODE"),
            angel_one_pin=_env("ANGEL_ONE_PIN"),
            angel_one_totp_secret=_env("ANGEL_ONE_TOTP_SECRET"),
        ),
    )


def load_watchlist(path: str | Path) -> list[WatchlistItem]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    symbols = payload.get("symbols", [])
    return [
        WatchlistItem(
            symbol=item["symbol"].upper(),
            exchange=item.get("exchange", "NSE").upper(),
            instrument_type=item.get("instrument_type", "EQ").upper(),
            data_file=item.get("data_file"),
            notes=item.get("notes", ""),
            fundamentals=fundamentals_from_mapping(item.get("fundamentals")),
        )
        for item in symbols
    ]


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value if value else None

