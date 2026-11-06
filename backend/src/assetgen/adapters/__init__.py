"""Builds the adapter set from the settings: always the real tools."""

from __future__ import annotations

from ..config import ADAPTER_NAMES, Settings
from .base import Adapters
from .real import REAL_ADAPTERS, real_adapter_class

RIG_NOT_BUILT = ("Rigging is not built yet: the UniRig adapter and clip retargeting are ticket #11 "
                 "(after the archetype templates, ticket #10). Assets stop after gate 2 until then.")


class RigNotBuilt(RuntimeError):
    """Raised by the rig stage until the UniRig adapter exists."""


class PendingRig:
    """Stands in for UniRig until ticket #11: the rig stage fails with a clear message."""

    def predict_skeleton(self, **kwargs):
        raise RigNotBuilt(RIG_NOT_BUILT)


def build_adapters(settings: Settings) -> Adapters:
    """The real adapter of every tool (rigging fails clearly until it is built)."""
    chosen = {name: real_adapter_class(name)(settings) for name in ADAPTER_NAMES if name in REAL_ADAPTERS}
    chosen.setdefault("rig", PendingRig())
    return Adapters(**chosen)


__all__ = ["Adapters", "build_adapters", "PendingRig", "RigNotBuilt", "RIG_NOT_BUILT"]
