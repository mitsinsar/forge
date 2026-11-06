"""Settings: tool paths and service URLs.

Values come from (lowest to highest priority):
  1. the defaults below,
  2. a TOML file (``assetgen.toml`` in the working directory, or the path in
     ``ASSETGEN_CONFIG``),
  3. environment variables ``ASSETGEN_<FIELD>`` (for example
     ``ASSETGEN_PORT=9000`` or ``ASSETGEN_COMFYUI_URL=http://gpu:8188``).

Keys that are not settings (such as an old ``[adapters]`` table) are ignored.

Nothing here is shell- or OS-specific, so the same settings work on Windows.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

ADAPTER_NAMES = ("llm", "image", "mesh", "rig", "blender")
MESH_TOOLS = ("hunyuan3d", "modly")


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("data")
    # The external tools.
    comfyui_url: str = "http://127.0.0.1:8188"
    # The ComfyUI folder (the one with models/); LoRAs ComfyUI doesn't have are copied to models/loras.
    comfyui_dir: str = ""
    # Seconds one candidate may take in ComfyUI (queue wait included) before the stage fails.
    comfyui_timeout: float = 600.0
    # API-format workflows, one per base model (empty: backend/workflows).
    comfyui_workflows_dir: str = ""
    qwen_url: str = "http://127.0.0.1:11434/v1"
    qwen_model: str = "qwen3"
    hunyuan3d_url: str = "http://127.0.0.1:8081"
    hunyuan3d_timeout: float = 1800.0  # seconds one image-to-mesh job may take
    hunyuan3d_poll: float = 2.0  # seconds between status checks
    # The image-to-mesh tool: "hunyuan3d" (its api_server.py) or "modly" (the Modly desktop app).
    mesh_tool: str = "hunyuan3d"
    modly_url: str = "http://127.0.0.1:8765"
    modly_model: str = "hunyuan3d-mini-turbo/generate"  # a model id from Modly's GET /model/all
    modly_params: str = ""  # extra generation params as a JSON object, e.g. '{"num_inference_steps": 5}'
    modly_timeout: float = 1800.0  # seconds one run may take (the first run downloads the model)
    modly_poll: float = 2.0
    blender_path: Path = Path("blender")
    unirig_path: Path = Path("UniRig")
    python_path: Path = Path("python")
    # The built web app (npm run build in frontend/), served at / when it exists.
    frontend_dir: Path = Path(__file__).resolve().parents[3] / "frontend" / "dist"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "assetgen.sqlite3"

    @property
    def assets_dir(self) -> Path:
        return self.data_dir / "assets"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


def _coerce(value, current):
    if isinstance(current, bool):
        return str(value).lower() in ("1", "true", "yes", "on")
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, Path):
        return Path(value)
    return str(value)


def _apply(target, values: dict) -> None:
    for f in fields(target):
        if f.name not in values:
            continue
        setattr(target, f.name, _coerce(values[f.name], getattr(target, f.name)))


def load_settings(**overrides) -> Settings:
    settings = Settings()
    config_file = Path(os.environ.get("ASSETGEN_CONFIG", "assetgen.toml"))
    if config_file.is_file():
        with config_file.open("rb") as fh:
            _apply(settings, tomllib.load(fh))

    env: dict = {}
    for key, value in os.environ.items():
        if not key.startswith("ASSETGEN_") or key == "ASSETGEN_CONFIG":
            continue
        name = key[len("ASSETGEN_"):].lower()
        if "__" in name:
            group, sub = name.split("__", 1)
            env.setdefault(group, {})[sub] = value
        else:
            env[name] = value
    _apply(settings, env)
    _apply(settings, overrides)
    if settings.mesh_tool not in MESH_TOOLS:
        raise ValueError(f"mesh_tool must be one of {', '.join(MESH_TOOLS)}, got {settings.mesh_tool!r}")
    return settings
