"""Builds the adapter set from the settings."""

from __future__ import annotations

from ..config import ADAPTER_NAMES, Settings
from .base import Adapters
from .mock import MockBlender, MockImage, MockLLM, MockMesh, MockRig
from .real import REAL_ADAPTERS, real_adapter_class

MOCKS = {"llm": MockLLM, "image": MockImage, "mesh": MockMesh, "rig": MockRig, "blender": MockBlender}


class RealAdapterMissing(RuntimeError):
    pass


def build_adapters(settings: Settings) -> Adapters:
    """Mock or real per tool. Every tool asked for as ``real`` must have a real
    class; otherwise this fails before anything starts, naming all of them."""
    missing = [name for name in ADAPTER_NAMES
               if getattr(settings.adapters, name) == "real" and name not in REAL_ADAPTERS]
    if missing:
        names = ", ".join(missing)
        fixes = "; ".join(f'adapters.{n} = "mock" (or ASSETGEN_ADAPTERS__{n.upper()}=mock)' for n in missing)
        built = ", ".join(sorted(REAL_ADAPTERS)) or "none"
        raise RealAdapterMissing(
            f"No real adapter is built yet for: {names}. Real adapters so far: {built}. Set {fixes}.")
    chosen = {}
    for name in ADAPTER_NAMES:
        if getattr(settings.adapters, name) == "real":
            chosen[name] = real_adapter_class(name)(settings)
        else:
            chosen[name] = MOCKS[name](settings.mock_step_delay)
    return Adapters(**chosen)


__all__ = ["Adapters", "build_adapters", "RealAdapterMissing"]
