"""A generated body motion (HY-Motion's SMPL-H skeleton, see ``external/hy_motion.py``) as a clip
GLB on the biped's bone names, like the clips in ``archetypes/biped/clips/``.

Arguments (args.json)::

    {"motion": ".../motion.json", "name": "skill_firebolt", "out_glb": ".../skill_firebolt.glb",
     "in_place": false}

The armature is built from the motion's T-pose rest (Y up there, Z up here; it faces -Y like the
library clips), its bones named after the biped's (``BONES``) and pointing at their child joint,
so ``rig_animate.retarget`` takes it like any library clip: it copies each bone's turn, not its
length. Every frame poses the bones by the motion's rotations (each joint's turn against its
parent, in the T-pose's world-aligned frames) and moves the pelvis by the motion's translation.
``in_place`` takes the travel along the ground out (the hips still rise and fall).

Result: ``{"duration", "frames", "travel_m"}``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

from _common import empty_scene, progress, run  # noqa: E402
from build_archetypes import export_clip  # noqa: E402

# HY-Motion's (SMPL-H) body joints to the biped's bones (the hands' fingers are not predicted).
BONES = {
    "Pelvis": "pelvis", "Spine1": "spine_01", "Spine2": "spine_02", "Spine3": "spine_03",
    "Neck": "neck_01", "Head": "Head",
    "L_Collar": "clavicle_l", "L_Shoulder": "upperarm_l", "L_Elbow": "lowerarm_l", "L_Wrist": "hand_l",
    "R_Collar": "clavicle_r", "R_Shoulder": "upperarm_r", "R_Elbow": "lowerarm_r", "R_Wrist": "hand_r",
    "L_Hip": "thigh_l", "L_Knee": "calf_l", "L_Ankle": "foot_l", "L_Foot": "ball_l",
    "R_Hip": "thigh_r", "R_Knee": "calf_r", "R_Ankle": "foot_r", "R_Foot": "ball_r",
}
# Y up (the motion) to Z up (Blender): x stays, y becomes z, z becomes -y.
AXES = Matrix(((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)))


def to_blender(v) -> Vector:
    return AXES @ Vector(v)


def build_armature(joints: list[str], parents: list[int], rest: list[Vector]):
    """The T-pose armature, every bone named after the biped's and pointing at its child joint
    (leaves: on along their parent's direction; the head up; the toes forward)."""
    data = bpy.data.armatures.new("motion")
    arm = bpy.data.objects.new("motion", data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    children = {i: [c for c, p in enumerate(parents) if p == i] for i in range(len(joints))}
    edit = {}
    for i, name in enumerate(joints):
        bone = data.edit_bones.new(BONES[name])
        head = rest[i]
        kids = children[i]
        if name == "Head":
            tail = head + Vector((0.0, 0.0, 0.18))
        elif name.endswith("_Foot"):
            tail = head + Vector((0.0, -0.08, 0.0))
        elif name == "Pelvis":  # towards the spine, so it stands up like the biped's
            tail = rest[joints.index("Spine1")]
        elif kids:
            tail = sum((rest[c] for c in kids), Vector()) / len(kids)
        else:
            tail = head + (head - rest[parents[i]]).normalized() * 0.08
        if (tail - head).length < 1e-3:
            tail = head + Vector((0.0, 0.0, 0.05))
        bone.head, bone.tail = head, tail
        edit[i] = bone
    for i, parent in enumerate(parents):
        if parent >= 0:
            edit[i].parent = edit[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def main(args: dict) -> dict:
    empty_scene()
    motion = json.loads(Path(args["motion"]).read_text(encoding="utf-8"))
    joints, parents = motion["joints"], motion["parents"]
    unknown = [j for j in joints if j not in BONES]
    if unknown:
        raise ValueError(f"joints without a biped bone: {', '.join(unknown)}")
    # The motion's rest is around the pelvis (its feet about a metre under the origin); the library
    # clips' armatures stand on the ground, and rig_animate moves the hips by their travel from it.
    rest = [to_blender(p) for p in motion["rest"]]
    ground = Vector((0.0, 0.0, min(p.z for p in rest)))
    rest = [p - ground for p in rest]
    arm = build_armature(joints, parents, rest)
    bones = {i: arm.data.bones[BONES[j]] for i, j in enumerate(joints)}
    rest_matrix = {i: b.matrix_local.copy() for i, b in bones.items()}
    order = sorted(range(len(joints)), key=lambda i: len(bones[i].parent_recursive))
    scene = bpy.context.scene
    scene.render.fps = int(motion["fps"])
    action = bpy.data.actions.new(args["name"])
    arm.animation_data_create()
    arm.animation_data.action = action
    for pose_bone in arm.pose.bones:
        pose_bone.rotation_mode = "QUATERNION"
    translation = [to_blender(t) + ground for t in motion["translation"]]
    start = translation[0].copy()
    frames = int(motion["frames"])

    def pose(frame: int, lift: float) -> dict[int, Matrix]:
        """Each bone's posed matrix (armature space) at the frame."""
        local = [AXES @ Matrix([row[0:3], row[3:6], row[6:9]]) @ AXES.transposed()
                 for row in motion["rotations"][frame]]
        world: dict[int, Matrix] = {}  # each joint's turn from the T-pose, in Blender's world
        posed: dict[int, Matrix] = {}
        for i in order:
            parent = parents[i]
            world[i] = (world[parent] @ local[i]) if parent >= 0 else local[i]
            if parent >= 0:
                head = posed[parent].translation + world[parent] @ (rest[i] - rest[parent])
            else:
                move = translation[frame] - (Vector((start.x, start.y, 0.0)) if args.get("in_place") else Vector())
                head = rest[i] + move + Vector((0.0, 0.0, lift))
            posed[i] = Matrix.LocRotScale(head, (world[i] @ rest_matrix[i].to_3x3()).to_quaternion(), None)
        return posed

    # The lowest planted ankle at the rest's ankle height, so the feet stand on the floor and don't
    # hover or sink (the motion's own ground is its soles, under joints set differently).
    feet = [i for i, j in enumerate(joints) if j.endswith("_Ankle")]
    lowest = min(min(pose(f, 0.0)[i].translation.z for i in feet) for f in range(frames))
    lift = min(rest[i].z for i in feet) - lowest
    for frame in range(frames):
        if frame % 20 == 0:
            progress(0.1 + 0.8 * frame / frames, "Posing the motion")
        posed = pose(frame, lift)
        for i in order:
            parent = parents[i]
            base = (posed[parent] @ rest_matrix[parent].inverted() @ rest_matrix[i]) if parent >= 0 else rest_matrix[i]
            pose_bone = arm.pose.bones[bones[i].name]
            pose_bone.matrix_basis = base.inverted() @ posed[i]
            pose_bone.keyframe_insert("rotation_quaternion", frame=frame)
            pose_bone.keyframe_insert("location", frame=frame)
    progress(0.92, "Writing the clip")
    out = Path(args["out_glb"])
    result = export_clip(arm, action, args["name"], out)
    travel = translation[-1] - translation[0]
    result["travel_m"] = round(Vector((travel.x, travel.y)).length, 3)
    progress(1.0, "Clip ready")
    return result


if __name__ == "__main__":
    run(main)
