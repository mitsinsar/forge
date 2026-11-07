"""Builds the adapter set from the settings: always the real tools."""

from __future__ import annotations

from ..config import ADAPTER_NAMES, Settings
from .base import Adapters
from .real import real_adapter_class


def build_adapters(settings: Settings) -> Adapters:
    """The real adapter of every tool (the mesh tool chosen by ``mesh_tool``)."""
    from .real.mvviews import MultiViewImages  # noqa: PLC0415

    from .real import MESH_ADAPTERS  # noqa: PLC0415

    adapters = {name: real_adapter_class(name, settings.mesh_tool)(settings) for name in ADAPTER_NAMES}
    meshes = {tool: (adapters["mesh"] if tool == settings.mesh_tool else real_adapter_class("mesh", tool)(settings))
              for tool in MESH_ADAPTERS}
    return Adapters(**adapters, views=MultiViewImages(settings), meshes=meshes)


__all__ = ["Adapters", "build_adapters"]
