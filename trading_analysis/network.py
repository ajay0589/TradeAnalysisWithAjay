from __future__ import annotations

import os
import ssl
import threading
from functools import lru_cache
from pathlib import Path


_CONTEXT_LOCK = threading.Lock()


def tls_info() -> dict:
    try:
        import truststore
    except ImportError:
        provider = "python-default"
    else:
        provider = "system-truststore"
    return {"provider": provider, "certificate_verification": True,
            "custom_ca_configured": bool(os.getenv("TRADING_CA_BUNDLE")),
            "proxy_environment_configured": any(os.getenv(key) for key in
                                                 ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"))}


def https_context() -> ssl.SSLContext:
    bundle = os.getenv("TRADING_CA_BUNDLE", "").strip()
    modified = Path(bundle).stat().st_mtime_ns if bundle else None
    with _CONTEXT_LOCK:
        return _context(bundle, modified)


@lru_cache(maxsize=4)
def _context(bundle: str, modified: int | None) -> ssl.SSLContext:
    try:
        import truststore
    except ImportError:
        context = ssl.create_default_context()
    else:
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if bundle:
        context.load_verify_locations(cafile=bundle)
    return context


def network_hint(error: str) -> str | None:
    if "CERTIFICATE_VERIFY_FAILED" in error or "certificate verify failed" in error:
        return ("HTTPS certificate trust failed. Install requirements.txt with the Python used to start this server, "
                "then restart it. If your network uses a private CA, set TRADING_CA_BUNDLE to its trusted PEM file.")
    return None
