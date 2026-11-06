"""``uv run assetgen``: serves the API (port 8000 by default)."""

from __future__ import annotations

import uvicorn

from .app import create_app
from .config import load_settings


def run() -> None:
    settings = load_settings()
    print(f"assetgen: data in {settings.data_dir.resolve()}")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
