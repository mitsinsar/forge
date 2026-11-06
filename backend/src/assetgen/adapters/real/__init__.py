"""Real adapters, one module per tool.

To add one, write ``adapters/real/<tool>.py`` with a class that takes the
``Settings`` and implements the matching interface in ``adapters/base.py``,
then add it to ``REAL_ADAPTERS`` below. Modules are imported only when their
tool is switched to ``real``.
"""

from __future__ import annotations

from importlib import import_module

# adapter name -> (module in this package, class name)
REAL_ADAPTERS: dict[str, tuple[str, str]] = {
    "llm": ("qwen", "QwenLLM"),
    "blender": ("blender", "BlenderTools"),
}


def real_adapter_class(name: str) -> type | None:
    entry = REAL_ADAPTERS.get(name)
    if entry is None:
        return None
    module, cls = entry
    return getattr(import_module(f"{__name__}.{module}"), cls)
