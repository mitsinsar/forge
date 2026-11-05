"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .adapters import build_adapters
from .api import router
from .config import Settings, load_settings
from .db import Database
from .events import EventHub
from .pipeline import Pipeline

try:
    VERSION = version("assetgen")
except PackageNotFoundError:  # running from a source tree
    VERSION = "0.0.0"

DESCRIPTION = """Local backend of the asset generator: a text description becomes a game-ready,
animated asset through three approval gates (preview image, mesh, animated result), then is exported
into a Unity or Godot project.

ComfyUI, Hunyuan3D, Blender, UniRig and Qwen sit behind adapters; `GET /api/meta` shows which are
mocks. Live progress comes over the WebSocket at `/api/ws`.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    adapters = build_adapters(settings)  # fails fast if a real adapter is asked for but missing

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

    app = FastAPI(title="Asset generator", version=VERSION, description=DESCRIPTION, lifespan=lifespan)
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
            return FileResponse(target)
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})
