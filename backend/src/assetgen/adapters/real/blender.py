"""Headless Blender adapter: mesh clean-up with LODs (gate 2) and the Unity FBX.

The work happens in the scripts in ``backend/blender_scripts/``, run by
``blender_runner``. Rigging and sprite rendering still use the mock until the
rig ticket lands, so a run with the real Blender finishes end to end.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from ...config import Settings
from ...defaults import MODEL_SIZES
from ..mock import MockBlender
from ..mock.glb import read_glb_json
from .blender_runner import run_script


def merge_glb_extras(source: Path, target: Path, extra: dict | None = None) -> None:
    """Copies the root ``extras`` of ``source`` (plus ``extra``) into ``target``.

    Blender keeps no root extras, and the later stages read the asset's hints
    from them (the mock rig rebuilds its shape from them)."""
    try:
        extras = dict(read_glb_json(source).get("extras") or {})
    except (OSError, ValueError):
        extras = {}
    extras.update(extra or {})
    if not extras:
        return
    data = target.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + length])
    gltf["extras"] = {**extras, **(gltf.get("extras") or {})}
    payload = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    rest = data[20 + length:]
    body = struct.pack("<I4s", len(payload), b"JSON") + payload + rest
    target.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


class BlenderTools:
    mode = "real"

    def __init__(self, settings: Settings):
        self.blender = settings.blender_path
        self.mock = MockBlender(settings.mock_step_delay)

    def _run(self, script: str, args: dict, out_dir: Path, progress) -> dict:
        return run_script(self.blender, script, args, work_dir=out_dir,
                          log_path=out_dir / f"blender_{Path(script).stem}.log", progress=progress)

    def cleanup_and_lods(self, *, raw_mesh, kind, triangles, lod_ratios, out_dir, progress):
        out_dir = Path(out_dir)
        glb = out_dir / "mesh.glb"
        size, fit = MODEL_SIZES.get(kind, MODEL_SIZES["hero"])
        result = self._run("cleanup_lods.py", {
            "raw_mesh": str(Path(raw_mesh).resolve()), "out_glb": str(glb.resolve()), "triangles": int(triangles),
            "lod_ratios": [float(r) for r in lod_ratios], "height": size, "fit": fit}, out_dir, progress)
        merge_glb_extras(Path(raw_mesh), glb, {"triangle_budget": int(triangles)})
        return {"glb": glb, "lods": result["lods"]}

    def rig_and_animate(self, **kwargs):
        return self.mock.rig_and_animate(**kwargs)

    def render_sprites(self, **kwargs):
        return self.mock.render_sprites(**kwargs)

    def export_fbx(self, *, animated_glb, mesh_glb, lods, rig_type, out_path, progress):
        out_path = Path(out_path)
        self._run("export_fbx.py", {
            "animated_glb": str(Path(animated_glb).resolve()), "mesh_glb": str(Path(mesh_glb).resolve()),
            "out_fbx": str(out_path.resolve()), "name": out_path.stem,
            "lod_levels": [int(lod["level"]) for lod in lods if int(lod["level"]) > 0]},
            out_path.parent, progress)
        return out_path
