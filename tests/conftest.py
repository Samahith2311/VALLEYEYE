from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def block_live_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    original_connect = socket.socket.connect

    def deny_connect(sock: socket.socket, address: object) -> None:
        loopback_hosts = {"127.0.0.1", "::1", "localhost"}
        if isinstance(address, tuple) and address and address[0] in loopback_hosts:
            original_connect(sock, address)
            return
        raise AssertionError("tests must not open live network connections")

    monkeypatch.setattr(socket.socket, "connect", deny_connect)
    yield
