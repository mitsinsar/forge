"""``uv run assetgen``: serves the API (port 8000 by default)."""

from __future__ import annotations

import os
import socket

import uvicorn

from .app import create_app
from .config import load_settings


def _sockets(host: str, port: int) -> list[socket.socket]:
    """The listening sockets: the host, plus the IPv6 loopback for a loopback host. Browsers try
    ``localhost`` over IPv6 first, and on Windows a refused attempt costs 0.2 s per connection."""
    addresses = [(socket.AF_INET6 if ":" in host else socket.AF_INET, host)]
    if host in ("127.0.0.1", "localhost") and socket.has_ipv6:
        addresses = [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")]
    found = []
    for family, address in addresses:
        sock = socket.socket(family, socket.SOCK_STREAM)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((address, port))
        except OSError:
            sock.close()
            if found:  # IPv6 unavailable: IPv4 alone still works
                continue
            raise
        found.append(sock)
    return found


def run() -> None:
    settings = load_settings()
    print(f"assetgen: data in {settings.data_dir.resolve()}")
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host=settings.host, port=settings.port))
    server.run(sockets=_sockets(settings.host, settings.port))


if __name__ == "__main__":
    run()
