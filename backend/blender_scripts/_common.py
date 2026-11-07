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


# A body's gear-slot regions (the biped base body's, or a wearer's): the bones whose vertices each
# gear slot covers, by each vertex's strongest bone. "cape" is the back of the torso and thighs
# instead (see slot_groups).
SLOT_BONES = {
    "head": ("Head", "neck_01"),
    "chest": ("spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r", "upperarm_l", "upperarm_r"),
    "legs": ("pelvis", "thigh_l", "thigh_r", "calf_l", "calf_r"),
    "hands": ("hand_l", "hand_r", "lowerarm_l", "lowerarm_r"),
    "feet": ("foot_l", "foot_r", "ball_l", "ball_r"),
}
FINGERS = ("index", "middle", "ring", "pinky", "thumb")  # their vertices count as the hand's
CAPE_BONES = ("spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r", "pelvis", "thigh_l", "thigh_r")


def slot_groups(body, joints: list) -> dict:
    """Drops weights of bones not in ``joints`` (unless None) and adds a ``slot_<slot>`` vertex group
    per gear slot. Returns the vertex count of each slot."""
    for group in list(body.vertex_groups):
        if group.name.startswith("slot_") or (joints is not None and group.name not in joints):
            body.vertex_groups.remove(group)
    names = {g.index: g.name for g in body.vertex_groups}
    centre_y = sum(v.co.y for v in body.data.vertices) / max(len(body.data.vertices), 1)
    members = {slot: [] for slot in [*SLOT_BONES, "cape"]}
    for vertex in body.data.vertices:
        if not vertex.groups:
            continue
        strongest = names[max(vertex.groups, key=lambda g: g.weight).group]
        if strongest.split("_")[0] in FINGERS:
            strongest = "hand_" + strongest[-1]
        for slot, bones in SLOT_BONES.items():
            if strongest in bones:
                members[slot].append(vertex.index)
        if strongest in CAPE_BONES and vertex.co.y > centre_y:  # the back (the front faces -Y)
            members["cape"].append(vertex.index)
    for slot, indices in members.items():
        group = body.vertex_groups.new(name=f"slot_{slot}")
        group.add(indices, 1.0, "REPLACE")
    return {slot: len(indices) for slot, indices in members.items()}
