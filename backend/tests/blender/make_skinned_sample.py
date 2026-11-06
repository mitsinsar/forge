"""Test helper run inside Blender: writes a small skinned, animated GLB (a
two-bone column with clips "idle" and "attack_1"), like the rig stage will.

    blender -b --factory-startup -P make_skinned_sample.py -- <out.glb>
"""

import sys

import bpy

out = sys.argv[sys.argv.index("--") + 1]
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

bpy.ops.object.armature_add(location=(0, 0, 0))
rig = bpy.context.object
rig.name = "Armature"
bpy.ops.object.mode_set(mode="EDIT")
lower = rig.data.edit_bones[0]
lower.name, lower.head, lower.tail = "Hips", (0, 0, 0), (0, 0, 0.9)
upper = rig.data.edit_bones.new("Spine")
upper.head, upper.tail, upper.parent = (0, 0, 0.9), (0, 0, 1.8), lower
bpy.ops.object.mode_set(mode="OBJECT")

bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.3, depth=1.8, location=(0, 0, 0.9))
body = bpy.context.object
body.name = "mesh"
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.subdivide(number_cuts=6)
bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
rig.select_set(True)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.parent_set(type="ARMATURE_AUTO")

for clip, angle in (("idle", 0.2), ("attack_1", 1.0)):
    action = bpy.data.actions.new(clip)
    rig.animation_data_create()
    rig.animation_data.action = action
    bone = rig.pose.bones["Spine"]
    for frame, value in ((0, 0.0), (12, angle), (24, 0.0)):
        bone.rotation_mode = "XYZ"
        bone.rotation_euler = (value, 0, 0)
        bone.keyframe_insert("rotation_euler", frame=frame)
    track = rig.animation_data.nla_tracks.new()
    track.name = clip
    track.strips.new(clip, 0, action)
    rig.animation_data.action = None

for obj in list(bpy.data.objects):  # Blender 5 adds widget objects for the bone shapes
    if obj not in (rig, body):
        bpy.data.objects.remove(obj, do_unlink=True)

bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_animation_mode="NLA_TRACKS")
