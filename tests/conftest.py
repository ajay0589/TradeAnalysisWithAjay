import socket

import pytest

from trading_analysis import diagnostics


@pytest.fixture(autouse=True)
def isolate_external_effects(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "ROOT", tmp_path)
    original = socket.socket.connect

    def connect(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("Regression tests must not contact brokers or Telegram; mock the external transport")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
