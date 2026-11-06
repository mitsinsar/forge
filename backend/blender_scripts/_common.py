"""Shared helpers of the headless Blender scripts (plain ``bpy``, Blender 4.5 LTS and later).

Each script is started as::

    blender -b --factory-startup -P <script> -- <args.json> <result.json>

reads its arguments from ``args.json``, prints ``PROGRESS <fraction> <step>``
lines that the backend turns into the asset's progress, and writes its result
to ``result.json``. Any exception prints a traceback and exits with code 1.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import bpy


def progress(fraction: float, step: str) -> None:
    print(f"PROGRESS {max(0.0, min(1.0, fraction)):.3f} {step}", flush=True)


def run(main) -> None:
    """Calls ``main(args) -> result`` with the JSON arguments after ``--``."""
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) != 2:
        print("usage: blender -b -P <script> -- <args.json> <result.json>", flush=True)
        sys.exit(2)
    args_path, result_path = Path(argv[0]), Path(argv[1])
    try:
        args = json.loads(args_path.read_text(encoding="utf-8"))
        result = main(args)
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - every failure ends the run with the reason last
        traceback.print_exc()
        sys.stdout.flush()
        print(f"ERROR {type(exc).__name__}: {exc}", flush=True)
        sys.exit(1)


def empty_scene() -> None:
    """Removes everything the factory start-up file holds (cube, camera, light)."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.actions, bpy.data.cameras,
                       bpy.data.lights, bpy.data.armatures):
        for block in list(collection):
            if block.users == 0:
                collection.remove(block)


def import_gltf(path: str) -> list:
    """Imports a GLB and returns the objects it added."""
    if not Path(path).is_file():
        raise FileNotFoundError(f"no such file: {path}")
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    return [obj for obj in bpy.data.objects if obj not in before]


def triangles(obj) -> int:
    return sum(len(poly.vertices) - 2 for poly in obj.data.polygons)


def select_only(objects, active=None) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active or (objects[0] if objects else None)
