"""Real adapters, one module per tool.

To add one, write ``adapters/real/<tool>.py`` with a class that takes the
``Settings`` and implements the matching interface in ``adapters/base.py``,
then add it to ``REAL_ADAPTERS`` below. A tool missing here (rig, until
ticket #11) gets a stand-in that fails its stage with a clear message.
"""

from __future__ import annotations

from importlib import import_module

# adapter name -> (module in this package, class name)
REAL_ADAPTERS: dict[str, tuple[str, str]] = {
    "llm": ("qwen", "QwenLLM"),
    "image": ("comfyui", "ComfyUIImage"),
    "mesh": ("hunyuan3d", "Hunyuan3DMesh"),
    "blender": ("blender", "BlenderTools"),
    "rig": ("unirig", "UniRigRig"),
}


# mesh_tool setting -> (module, class) for the mesh adapter
MESH_ADAPTERS: dict[str, tuple[str, str]] = {
    "hunyuan3d": ("hunyuan3d", "Hunyuan3DMesh"),
    "modly": ("modly", "ModlyMesh"),
}


def real_adapter_class(name: str, mesh_tool: str = "hunyuan3d") -> type | None:
    entry = MESH_ADAPTERS[mesh_tool] if name == "mesh" else REAL_ADAPTERS.get(name)
    if entry is None:
        return None
    module, cls = entry
    return getattr(import_module(f"{__name__}.{module}"), cls)
