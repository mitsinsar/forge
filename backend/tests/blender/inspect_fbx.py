"""Test helper run inside Blender: re-imports an FBX and writes what it holds.

    blender -b --factory-startup -P inspect_fbx.py -- <file.fbx> <report.json>
"""

import json
import sys

import bpy

fbx, report = sys.argv[sys.argv.index("--") + 1:][:2]
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
for action in list(bpy.data.actions):
    bpy.data.actions.remove(action)
bpy.ops.import_scene.fbx(filepath=fbx)
objects = [{"name": o.name, "type": o.type, "parent": o.parent.name if o.parent else None,
            "triangles": sum(len(p.vertices) - 2 for p in o.data.polygons) if o.type == "MESH" else None,
            "vertex_groups": len(o.vertex_groups) if o.type == "MESH" else None,
            "dimensions": list(o.dimensions)}
           for o in bpy.data.objects]
# The FBX importer names each take's action "<object>|<take>".
actions = [{"name": a.name, "take": a.name.split("|")[-1], "frames": list(a.frame_range)} for a in bpy.data.actions]
with open(report, "w", encoding="utf-8") as fh:
    json.dump({"objects": objects, "actions": actions}, fh, indent=2)
