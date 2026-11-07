"""Renders a finished mesh (LOD0) from the front, the side and the back, for the vision model to
compare with the picture it was made from (gate 2's "Looks like the picture").

    blender -b --factory-startup -P mesh_views.py -- <args.json> <result.json>

args.json::

    {"mesh_glb": "mesh/mesh.glb", "out_dir": "mesh/judge", "size": 384}

Writes ``front.png``, ``side.png`` and ``back.png`` (orthographic, textured, on light grey) and
prints PROGRESS lines like the other scripts.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from _common import empty_scene, import_gltf, progress, run  # noqa: E402

VIEWS = {"front": 0.0, "side": 90.0, "back": 180.0}  # degrees round the model; the front faces -Y


def main(args: dict) -> dict:
    empty_scene()
    objects = import_gltf(args["mesh_glb"])
    meshes = [o for o in objects if o.type == "MESH"]
    lod0 = [o for o in meshes if o.name.startswith("LOD0")] or meshes
    for mesh in meshes:
        mesh.hide_render = mesh not in lod0
    points = [mesh.matrix_world @ Vector(corner) for mesh in lod0 for corner in mesh.bound_box]
    low = Vector([min(p[i] for p in points) for i in range(3)])
    high = Vector([max(p[i] for p in points) for i in range(3)])
    centre, extent = (low + high) / 2, max(high - low)

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    size = int(args.get("size", 384))
    scene.render.resolution_x = scene.render.resolution_y = size
    world = bpy.data.worlds.new("judge")
    world.color = (0.6, 0.6, 0.62)
    scene.world = world
    camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = extent * 1.15
    scene.collection.objects.link(camera)
    scene.camera = camera
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.0
    scene.collection.objects.link(sun)

    out = Path(args["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for index, (name, degrees) in enumerate(VIEWS.items()):
        angle = math.radians(degrees)
        direction = Vector((math.sin(angle), -math.cos(angle), 0.0))
        camera.location = centre + direction * extent * 3
        camera.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
        sun.rotation_euler = camera.rotation_euler
        path = out / f"{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        files[name] = str(path)
        progress((index + 1) / len(VIEWS), f"Rendered the {name}")
    return {"views": files}


if __name__ == "__main__":
    run(main)
