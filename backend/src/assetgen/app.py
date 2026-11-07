"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse

from .adapters import Adapters, build_adapters
from .api import router
from .config import Settings, load_settings
from .db import Database
from .events import EventHub
from .pipeline import Pipeline

try:
    VERSION = version("assetgen")
except PackageNotFoundError:  # running from a source tree
    VERSION = "0.0.0"

DESCRIPTION = """Forge's local API: a text description becomes a game-ready, animated asset through
three approval gates (preview image, mesh, animated result), then is exported into a Unity or Godot
project. Everything the web app does goes through these routes, so scripts and other tools can drive
Forge the same way.

ComfyUI, Hunyuan3D, Blender, UniRig and the language model sit behind adapters; `GET /api/tools`
shows which answer, `POST /api/tools/{tool}/restart` and `/stop` restart or stop one, and
`GET /api/setup` says what is missing and how to install it. Live progress comes over the
WebSocket at `/api/ws`.
"""


def create_app(settings: Settings | None = None, adapters: Adapters | None = None) -> FastAPI:
    """The app with the real tools, or with ``adapters`` given (the tests pass their doubles)."""
    settings = settings or load_settings()
    adapters = adapters or build_adapters(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.assets_dir.mkdir(parents=True, exist_ok=True)
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        db = Database(settings.db_path)
        hub = EventHub()
        hub.bind(asyncio.get_running_loop())
        pipeline = Pipeline(settings, db, adapters, hub)
        app.state.settings = settings
        app.state.hub = hub
        app.state.pipeline = pipeline
        pipeline.start()
        try:
            yield
        finally:
            await pipeline.stop()
            db.close()

    app = FastAPI(title="Forge", version=VERSION, description=DESCRIPTION, lifespan=lifespan)
    # JSON and the app's scripts shrink to a fraction; images and models are left alone (already packed).
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
    app.include_router(router)
    mount_frontend(app, settings.frontend_dir)
    logging.getLogger("assetgen").setLevel(logging.INFO)
    return app


def mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serves the built web app (``npm run build`` in frontend/) at ``/`` when its folder exists.

    Added after the API router, so ``/api`` routes always win. Paths that are not files fall back to
    ``index.html`` so the app's own routes (``/assets/<id>``) load on refresh."""
    if not (dist / "index.html").is_file():
        return
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "not found")
        target = (root / path).resolve()
        if path and target.is_relative_to(root) and target.is_file():
            # Vite names built files by their content, so they never change: cache them for good.
            immutable = path.startswith("assets/")
            return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable"
                                                 if immutable else "no-cache"})
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})
