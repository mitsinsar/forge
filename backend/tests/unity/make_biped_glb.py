"""Run inside Blender: writes a skinned, animated biped GLB with the archetype's
Mixamo bone names, standing in for the real rig stage (the mock rig has no
skeleton) when checking the Unity import script.

    blender -b --factory-startup -P make_biped_glb.py -- <args.json>

args.json: {"out": "animated.glb", "part": "body" | "head", "clips": [{"name", "duration"}]}
"part" "body" is a whole blocky body (a hero); "head" only a helmet on the head
bone (head gear).
"""

import json
import math
import sys

import bpy

args = json.loads(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8").read())
FPS = 24
P = "mixamorig:"
# name: (head, tail, parent); Blender Z up, the character faces -Y, its left is +X
BONES = {
    "Hips": ((0, 0, 1.00), (0, 0, 1.10), None),
    "Spine": ((0, 0, 1.10), (0, 0, 1.22), "Hips"),
    "Spine1": ((0, 0, 1.22), (0, 0, 1.34), "Spine"),
    "Spine2": ((0, 0, 1.34), (0, 0, 1.46), "Spine1"),
    "Neck": ((0, 0, 1.46), (0, 0, 1.56), "Spine2"),
    "Head": ((0, 0, 1.56), (0, 0, 1.80), "Neck"),
}
for side, x in (("Left", 1), ("Right", -1)):
    BONES.update({
        f"{side}Shoulder": ((0.04 * x, 0, 1.42), (0.18 * x, 0, 1.42), "Spine2"),
        f"{side}Arm": ((0.18 * x, 0, 1.42), (0.45 * x, 0, 1.42), f"{side}Shoulder"),
        f"{side}ForeArm": ((0.45 * x, 0, 1.42), (0.70 * x, 0, 1.42), f"{side}Arm"),
        f"{side}Hand": ((0.70 * x, 0, 1.42), (0.80 * x, 0, 1.42), f"{side}ForeArm"),
        f"{side}UpLeg": ((0.10 * x, 0, 1.00), (0.10 * x, 0, 0.55), "Hips"),
        f"{side}Leg": ((0.10 * x, 0, 0.55), (0.10 * x, 0, 0.10), f"{side}UpLeg"),
        f"{side}Foot": ((0.10 * x, 0, 0.10), (0.10 * x, -0.12, 0.02), f"{side}Leg"),
        f"{side}ToeBase": ((0.10 * x, -0.12, 0.02), (0.10 * x, -0.20, 0.02), f"{side}Foot"),
    })

for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
bpy.context.scene.render.fps = FPS

bpy.ops.object.armature_add(location=(0, 0, 0))
rig = bpy.context.object
rig.name = rig.data.name = "Armature"
bpy.ops.object.mode_set(mode="EDIT")
for bone in list(rig.data.edit_bones):
    rig.data.edit_bones.remove(bone)
for name, (head, tail, parent) in BONES.items():
    bone = rig.data.edit_bones.new(P + name)
    bone.head, bone.tail = head, tail
    if parent:
        bone.parent = rig.data.edit_bones[P + parent]
        bone.use_connect = False
bpy.ops.object.mode_set(mode="OBJECT")


def box(bone: str, thickness: float, pad: float = 0.0):
    head, tail, _ = BONES[bone]
    center = [(h + t) / 2 for h, t in zip(head, tail)]
    size = [max(abs(t - h), thickness) + pad for h, t in zip(head, tail)]
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object
    obj.scale = size
    bpy.ops.object.transform_apply(scale=True)
    group = obj.vertex_groups.new(name=P + bone)
    group.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    return obj


if args.get("part") == "head":
    parts = [box("Head", 0.26, 0.04)]
else:
    parts = [box(name, 0.24 if name.startswith(("Spine", "Hips")) else 0.09) for name in BONES]
bpy.ops.object.select_all(action="DESELECT")
for obj in parts:
    obj.select_set(True)
bpy.context.view_layer.objects.active = parts[0]
if len(parts) > 1:
    bpy.ops.object.join()
mesh = bpy.context.view_layer.objects.active
mesh.name = mesh.data.name = "mesh"
mesh.parent = rig
modifier = mesh.modifiers.new("Armature", "ARMATURE")
modifier.object = rig

rig.animation_data_create()
for index, clip in enumerate(args["clips"]):
    action = bpy.data.actions.new(clip["name"])
    rig.animation_data.action = action
    end = max(2, round(clip["duration"] * FPS))
    swing = 0.15 + 0.1 * (index % 4)
    for frame in (0, end // 2, end):
        phase = math.sin(math.pi * frame / end)
        for name, sign in (("LeftArm", 1), ("RightArm", -1), ("LeftUpLeg", -1), ("RightUpLeg", 1)):
            pose = rig.pose.bones[P + name]
            pose.rotation_mode = "XYZ"
            pose.rotation_euler = (sign * swing * phase, 0, 0)
            pose.keyframe_insert("rotation_euler", frame=frame)
    track = rig.animation_data.nla_tracks.new()
    track.name = clip["name"]
    track.strips.new(clip["name"], 0, action)
    rig.animation_data.action = None

for obj in list(bpy.data.objects):
    if obj not in (rig, mesh):
        bpy.data.objects.remove(obj, do_unlink=True)
bpy.ops.export_scene.gltf(filepath=args["out"], export_format="GLB", export_animation_mode="NLA_TRACKS")
