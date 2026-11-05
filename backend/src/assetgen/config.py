"""Settings: tool paths, service URLs and adapter modes.

Values come from (lowest to highest priority):
  1. the defaults below,
  2. a TOML file (``assetgen.toml`` in the working directory, or the path in
     ``ASSETGEN_CONFIG``),
  3. environment variables ``ASSETGEN_<FIELD>`` (for example
     ``ASSETGEN_PORT=9000`` or ``ASSETGEN_ADAPTERS__MESH=real``).

Nothing here is shell- or OS-specific, so the same settings work on Windows.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

ADAPTER_NAMES = ("llm", "image", "mesh", "rig", "blender")


@dataclass
class AdapterModes:
    """``mock`` or ``real`` per external tool."""

    llm: str = "mock"  # Qwen via an OpenAI-compatible endpoint
    image: str = "mock"  # ComfyUI
    mesh: str = "mock"  # Hunyuan3D 2.1
    rig: str = "mock"  # UniRig
    blender: str = "mock"  # headless Blender


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("data")
    # Seconds the mock adapters wait per step, so progress is visible in the UI.
    mock_step_delay: float = 0.15
    adapters: AdapterModes = field(default_factory=AdapterModes)
    # Real tools (used once the real adapters replace the mocks).
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
        current = getattr(target, f.name)
        value = values[f.name]
        if isinstance(current, AdapterModes):
            if isinstance(value, AdapterModes):
                setattr(target, f.name, value)
            else:
                _apply(current, value)
        else:
            setattr(target, f.name, _coerce(value, current))


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

    for name in ADAPTER_NAMES:
        mode = getattr(settings.adapters, name)
        if mode not in ("mock", "real"):
            raise ValueError(f"adapters.{name} must be 'mock' or 'real', got {mode!r}")
    return settings
