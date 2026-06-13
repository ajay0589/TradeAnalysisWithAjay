from __future__ import annotations


class AngelOneSmartApiClient:
    """Angel One SmartAPI adapter placeholder.

    The official Python SDK is optional and should be installed only when you are
    ready to connect credentials. We keep this thin wrapper separate from the
    analysis engine so broker login code never leaks into scoring logic.
    """

    package_name = "smartapi-python"

    def __init__(self, api_key: str, client_code: str) -> None:
        self.api_key = api_key
        self.client_code = client_code

    def sdk_available(self) -> bool:
        try:
            import SmartApi  # noqa: F401
        except ImportError:
            return False
        return True

    def connection_status(self) -> str:
        if self.sdk_available():
            return "SDK available. Session generation can be wired after TOTP setup."
        return "SDK not installed. Install smartapi-python when Angel One integration is needed."

