from __future__ import annotations

import os
import ssl
import threading
from functools import lru_cache
from pathlib import Path
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, unquote
from urllib.request import getproxies, proxy_bypass

import urllib3


_CONTEXT_LOCK = threading.Lock()
_POOL_LOCK = threading.Lock()


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


@lru_cache(maxsize=8)
def _pool(context, proxy):
    options = {"ssl_context": context, "cert_reqs": ssl.CERT_REQUIRED, "maxsize": 4}
    if proxy:
        parsed = urlsplit(proxy)
        headers = None
        if parsed.username:
            headers = urllib3.make_headers(proxy_basic_auth=f"{unquote(parsed.username)}:{unquote(parsed.password or '')}")
        return urllib3.ProxyManager(proxy, proxy_headers=headers, proxy_ssl_context=context, **options)
    return urllib3.PoolManager(**options)


def pooled_urlopen(request, timeout=20, context=None):
    """Reuse verified HTTPS connections without automatically retrying POSTs or redirects."""
    context = context or https_context()
    url = request.full_url
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise ValueError("External transport requires HTTPS")
    proxy = None if proxy_bypass(parsed.hostname) else getproxies().get("https")
    with _POOL_LOCK:
        pool = _pool(context, proxy)
    try:
        response = pool.request(request.get_method(), url, body=request.data,
                                headers=dict(request.header_items()), preload_content=False,
                                timeout=urllib3.Timeout(connect=timeout, read=timeout),
                                retries=False, redirect=False)
    except urllib3.exceptions.HTTPError as exc:
        raise URLError(str(exc)) from exc
    if response.status >= 300:
        try:
            body = response.read()
        finally:
            response.close()
            response.release_conn()
        raise HTTPError(url, response.status, response.reason, response.headers, BytesIO(body))
    return _PooledResponse(response)


class _PooledResponse:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def read(self):
        return self.response.read()

    def __exit__(self, *args):
        # read() releases a fully consumed connection; an incomplete response must be discarded.
        self.response.close()
        self.response.release_conn()
