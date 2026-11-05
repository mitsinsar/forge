"""Builds the adapter set from the settings."""

from __future__ import annotations

from ..config import ADAPTER_NAMES, Settings
from .base import Adapters
from .mock import MockBlender, MockImage, MockLLM, MockMesh, MockRig

MOCKS = {"llm": MockLLM, "image": MockImage, "mesh": MockMesh, "rig": MockRig, "blender": MockBlender}


class RealAdapterMissing(RuntimeError):
    pass


def build_adapters(settings: Settings) -> Adapters:
    chosen = {}
    for name in ADAPTER_NAMES:
        if getattr(settings.adapters, name) == "real":
            raise RealAdapterMissing(
                f"The real {name} adapter is not built yet. Set adapters.{name} = \"mock\" "
                f"(or ASSETGEN_ADAPTERS__{name.upper()}=mock)."
            )
        chosen[name] = MOCKS[name](settings.mock_step_delay)
    return Adapters(**chosen)


__all__ = ["Adapters", "build_adapters", "RealAdapterMissing"]
