"""Builds the adapter set from the settings: always the real tools."""

from __future__ import annotations

from ..config import ADAPTER_NAMES, Settings
from .base import Adapters
from .real import real_adapter_class


def build_adapters(settings: Settings) -> Adapters:
    """The real adapter of every tool (the mesh tool chosen by ``mesh_tool``)."""
    from .real.mvviews import MultiViewImages  # noqa: PLC0415

    return Adapters(**{name: real_adapter_class(name, settings.mesh_tool)(settings) for name in ADAPTER_NAMES},
                    views=MultiViewImages(settings))


__all__ = ["Adapters", "build_adapters"]
