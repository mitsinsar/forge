"""Builds the archetype templates the rig stage uses (ticket #10): for each archetype
``archetypes/<id>/skeleton.blend`` (the template armature in the archetype's rest pose)
and ``archetypes/<id>/clips/<clip>.glb`` (one in-place animation per clip, on the
source armature), plus ``clips.json`` (where each clip came from) and ``LICENCES.md``.
``archetype.json`` gets the skeleton's joint names, sockets and sources.

Run it again at any time to rebuild everything from the source packs (or to swap a
source, such as Truebones clips later, by changing ``SOURCES`` below)::

    blender -b --factory-startup -P blender_scripts/build_archetypes.py -- [ids...] [--out DIR]

The source packs are not in git (see backend/.gitignore); download them as
backend/README.md describes. ``spider`` and ``serpent`` are generated here and need
no source.
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy  # noqa: E402
from mathutils import Matrix, Quaternion, Vector  # noqa: E402

from _common import empty_scene, import_gltf, progress  # noqa: E402

ARCHETYPES = Path(__file__).resolve().parents[1] / "src" / "assetgen" / "archetypes"
FPS = 30
LOOPING = {"idle", "walk", "run"}
STANDARD = ["idle", "walk", "run", "attack_1", "attack_2", "hit", "death"]
WEAPON_CLIPS = ["attack_slash", "attack_stab", "attack_cleave", "attack_overhead",
                "attack_shoot", "attack_quick_shot", "attack_cast", "attack_strike"]

UAL = "biped/source/Universal Animation Library[Standard]/Unreal-Godot/UAL1_Standard.glb"
QUATERNIUS_LICENCE = ("CC0 1.0 (public domain), Quaternius. "
                      "https://creativecommons.org/publicdomain/zero/1.0/")

# Per archetype: the source file (relative to archetypes/), its page, and which source
# clip each of our clips uses. A clip marked "*" is a stand-in until a better one exists.
SOURCES: dict[str, dict] = {
    "biped": {
        "file": UAL,
        "pack": "Universal Animation Library (Standard), Quaternius",
        "url": "https://quaternius.itch.io/universal-animation-library",
        "rig_type": "Humanoid",
        "sockets": {"weapon_r": "hand_r", "weapon_l": "hand_l"},
        "a_pose": True,
        "base_body": True,
        "clips": {
            "idle": "Idle_Loop", "walk": "Walk_Loop", "run": "Jog_Fwd_Loop", "attack_1": "Sword_Attack",
            "attack_2": "Punch_Cross", "hit": "Hit_Chest", "death": "Death01", "cast": "Spell_Simple_Shoot",
            "skill": "Roll", "attack_slash": "Sword_Attack", "attack_stab": "Punch_Jab*",
            "attack_cleave": "Sword_Attack*", "attack_overhead": "Sword_Attack*", "attack_shoot": "Pistol_Shoot*",
            "attack_quick_shot": "Pistol_Shoot*", "attack_cast": "Spell_Simple_Shoot", "attack_strike": "Punch_Cross",
        },
    },
    "quadruped": {
        "file": "_quaternius/animals/glTF/Wolf.gltf",
        "pack": "Ultimate Animated Animal Pack (Wolf), Quaternius",
        "url": "https://quaternius.com/packs/ultimateanimatedanimals.html",
        "rig_type": "Generic",
        "clips": {"idle": "Idle", "walk": "Walk", "run": "Gallop", "attack_1": "Attack", "attack_2": "Gallop_Jump",
                  "hit": "Idle_HitReact1", "death": "Death"},
    },
    "flyer": {
        "file": "_quaternius/monsters/Flying/glTF/Dragon.gltf",
        "pack": "Ultimate Monsters (Flying: Dragon), Quaternius",
        "url": "https://quaternius.com/packs/ultimatemonsters.html",
        "rig_type": "Generic",
        "clips": {"idle": "Flying_Idle", "walk": "Flying_Idle*", "run": "Fast_Flying", "attack_1": "Headbutt",
                  "attack_2": "Punch", "hit": "HitReact", "death": "Death"},
    },
    "blob": {
        "file": "_quaternius/monsters/Blob/glTF/GreenBlob.gltf",
        "pack": "Ultimate Monsters (Blob: GreenBlob), Quaternius",
        "url": "https://quaternius.com/packs/ultimatemonsters.html",
        "rig_type": "Generic",
        "clips": {"idle": "Idle", "walk": "Walk", "run": "Jump*", "attack_1": "Bite_Front", "attack_2": "Jump",
                  "hit": "HitRecieve", "death": "Death"},
    },
    "spider": {"procedural": "spider", "rig_type": "Generic", "clips": {c: "procedural" for c in STANDARD}},
    "serpent": {"procedural": "serpent", "rig_type": "Generic", "clips": {c: "procedural" for c in STANDARD}},
}


# ---- scene helpers --------------------------------------------------------------

def reset() -> None:
    empty_scene()
    for block in list(bpy.data.actions):
        bpy.data.actions.remove(block)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = FPS, 1.0


def armature_of(objects):
    armatures = [o for o in objects if o.type == "ARMATURE"]
    if len(armatures) != 1:
        raise RuntimeError(f"expected one armature, found {len(armatures)}")
    return armatures[0]


def template_joints(arm, meshes) -> list[str]:
    """The bones that deform the source mesh, plus their ancestors (helper bones such as IK
    targets and fingertip ends are left out)."""
    weighted: set[str] = set()
    for mesh in meshes:
        names = {group.index: group.name for group in mesh.vertex_groups}
        for vertex in mesh.data.vertices:
            for group in vertex.groups:
                if group.weight > 1e-4 and group.group in names:
                    weighted.add(names[group.group])
    keep: set[str] = set()
    for bone in arm.data.bones:
        if bone.name in weighted:
            node = bone
            while node is not None:
                keep.add(node.name)
                node = node.parent
    return [bone.name for bone in arm.data.bones if bone.name in keep]


def main_root(arm):
    """The root bone that most bones descend from (the one that moves the whole body)."""
    roots = [b for b in arm.data.bones if b.parent is None]
    return max(roots, key=lambda b: len(b.children_recursive))


def make_in_place(arm, action) -> float:
    """Removes horizontal travel of the main root, so the clip plays on the spot. Returns
    the largest horizontal drift removed, in metres."""
    root = main_root(arm)
    group = action.fcurves
    axes_world = root.matrix_local.to_3x3()  # bone axes in armature space
    drift = 0.0
    for index in range(3):
        axis = axes_world.col[index].normalized()
        if abs(axis.z) > 0.7:
            continue  # this location channel is (mostly) up and down: keep the bob
        curve = group.find(f'pose.bones["{root.name}"].location', index=index)
        if curve is None or not curve.keyframe_points:
            continue
        first = curve.keyframe_points[0].co[1]
        for point in curve.keyframe_points:
            drift = max(drift, abs(point.co[1] - first))
            delta = point.co[1] - first
            point.co[1] = first
            point.handle_left[1] -= delta
            point.handle_right[1] -= delta
    return drift


def clear_pose(arm) -> None:
    """Back to the rest pose: no action, every pose bone at identity (the last exported clip
    otherwise leaves its final frame on the bones)."""
    if arm.animation_data:
        arm.animation_data.action = None
    for pose_bone in arm.pose.bones:
        pose_bone.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()


def pose_a(arm) -> None:
    """Poses the biped's arms 45 degrees down from its T-pose (the A-pose the prompts ask for)."""
    clear_pose(arm)
    for side, angle in (("l", 45.0), ("r", -45.0)):
        bone = arm.pose.bones.get(f"upperarm_{side}")
        if bone is None:
            continue
        head = bone.matrix.translation.copy()
        turn = Matrix.Translation(head) @ Matrix.Rotation(math.radians(angle), 4, "Y") @ Matrix.Translation(-head)
        bone.matrix = turn @ bone.matrix
        bpy.context.view_layer.update()


def apply_a_pose(arm) -> None:
    """Makes the A-pose the armature's rest pose."""
    pose_a(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.pose.armature_apply(selected=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def posed_mesh_bounds(meshes) -> dict:
    """Bounds of the meshes as the armature currently deforms them."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    for mesh in meshes:
        evaluated = mesh.evaluated_get(depsgraph)
        data = evaluated.to_mesh()
        points += [evaluated.matrix_world @ v.co for v in data.vertices]
        evaluated.to_mesh_clear()
    return {"min": [round(min(p[i] for p in points), 5) for i in range(3)],
            "max": [round(max(p[i] for p in points), 5) for i in range(3)]}


def keep_only_joints(arm, joints: list[str]) -> None:
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for bone in list(arm.data.edit_bones):
        if bone.name not in joints:
            arm.data.edit_bones.remove(bone)
    bpy.ops.object.mode_set(mode="OBJECT")


def skin_proxy(arm):
    """A one-triangle mesh skinned to the armature. glTF only keeps bones as a skeleton (a
    skin) when a mesh uses them; without it importers bring the bones back as plain nodes."""
    proxy = bpy.data.objects.get(f"{arm.name}_proxy")
    if proxy is not None:
        return proxy
    mesh = bpy.data.meshes.new(f"{arm.name}_proxy")
    mesh.from_pydata([(0, 0, 0), (0.001, 0, 0), (0, 0.001, 0)], [], [(0, 1, 2)])
    proxy = bpy.data.objects.new(f"{arm.name}_proxy", mesh)
    bpy.context.scene.collection.objects.link(proxy)
    group = proxy.vertex_groups.new(name=main_root(arm).name)
    group.add([0, 1, 2], 1.0, "REPLACE")
    proxy.parent = arm
    proxy.modifiers.new("Armature", "ARMATURE").object = arm
    return proxy


def export_clip(arm, action, name: str, path: Path) -> dict:
    """One GLB holding the armature (as a skin) and ``action``, renamed to the clip name."""
    clip = action.copy()
    clip.name = name
    arm.animation_data_create()
    arm.animation_data.action = clip
    start, end = clip.frame_range
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = int(start), int(end)
    proxy = skin_proxy(arm)
    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    proxy.select_set(True)
    bpy.context.view_layer.objects.active = arm
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True, export_yup=True,
                              export_animations=True, export_animation_mode="ACTIVE_ACTIONS",
                              export_force_sampling=True, export_frame_range=False, export_def_bones=False,
                              export_skins=True, export_morph=False, export_materials="NONE")
    clear_pose(arm)
    bpy.data.actions.remove(clip)
    name_animation(path, name)
    return {"duration": round((end - start) / FPS, 4), "frames": int(end - start) + 1}


def name_animation(path: Path, name: str) -> None:
    """Sets the GLB's animation name to the clip name (the exporter names an active action
    "Animation"), editing the JSON chunk in place."""
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + length])
    animations = gltf.get("animations", [])
    if len(animations) != 1:
        raise RuntimeError(f"{path.name}: expected one animation, found {len(animations)}")
    animations[0]["name"] = name
    payload = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    rest = data[20 + length:]
    body = struct.pack("<I4s", len(payload), b"JSON") + payload + rest
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


def mesh_bounds(meshes) -> dict:
    points = [m.matrix_world @ v.co for m in meshes for v in m.data.vertices]
    return {"min": [round(min(p[i] for p in points), 5) for i in range(3)],
            "max": [round(max(p[i] for p in points), 5) for i in range(3)]}


def joint_bounds(arm, pad: float = 0.1) -> dict:
    points = [arm.matrix_world @ p for b in arm.data.bones for p in (b.head_local, b.tail_local)]
    lo = [min(p[i] for p in points) for i in range(3)]
    hi = [max(p[i] for p in points) for i in range(3)]
    size = max(h - l for l, h in zip(lo, hi))
    return {"min": [round(v - pad * size, 5) for v in lo], "max": [round(v + pad * size, 5) for v in hi]}


# The base body's slot regions: the bones whose vertices each gear slot covers (by each vertex's
# strongest bone). "cape" is the back of the torso and thighs instead (see slot_groups).
SLOT_BONES = {
    "head": ("Head", "neck_01"),
    "chest": ("spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r", "upperarm_l", "upperarm_r"),
    "legs": ("pelvis", "thigh_l", "thigh_r", "calf_l", "calf_r"),
    "hands": ("hand_l", "hand_r", "lowerarm_l", "lowerarm_r"),
    "feet": ("foot_l", "foot_r", "ball_l", "ball_r"),
}
FINGERS = ("index", "middle", "ring", "pinky", "thumb")  # their vertices count as the hand's
CAPE_BONES = ("spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r", "pelvis", "thigh_l", "thigh_r")


def add_sockets(arm, sockets: dict) -> None:
    """Weapon socket bones (not deforming) on the hands: the head in the palm, the bone pointing
    out of the fist on the thumb side (where a held blade points), its Z axis along the fingers."""
    if not sockets:
        return
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    bones = arm.data.edit_bones
    for socket, hand_name in sockets.items():
        hand = bones[hand_name]
        side = hand_name.rsplit("_", 1)[1]
        middle, index, pinky = (bones[f"{f}_01_{side}"].head.copy() for f in ("middle", "index", "pinky"))
        bone = bones.new(socket)
        bone.head = hand.head.lerp(middle, 0.55)
        bone.tail = bone.head + (index - pinky).normalized() * 0.1
        bone.align_roll(middle - hand.head)
        bone.parent = hand
        bone.use_connect = False
        bone.use_deform = False
    bpy.ops.object.mode_set(mode="OBJECT")


def make_base_body(arm, meshes):
    """One mesh of the source body in the armature's current (A-)pose, its weights kept: copies
    of the skinned meshes with the pose applied, joined. Bound to the armature again later, once
    that pose is its rest."""
    copies = []
    for mesh in meshes:
        copy = mesh.copy()
        copy.data = mesh.data.copy()
        bpy.context.scene.collection.objects.link(copy)
        bpy.ops.object.select_all(action="DESELECT")
        copy.select_set(True)
        bpy.context.view_layer.objects.active = copy
        for modifier in list(copy.modifiers):
            if modifier.type == "ARMATURE":
                bpy.ops.object.modifier_apply(modifier=modifier.name)
            else:
                copy.modifiers.remove(modifier)
        world = copy.matrix_world.copy()
        copy.parent = None
        copy.matrix_world = world
        copies.append(copy)
    bpy.ops.object.select_all(action="DESELECT")
    for copy in copies:
        copy.select_set(True)
    bpy.context.view_layer.objects.active = copies[0]
    if len(copies) > 1:
        bpy.ops.object.join()
    body = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    body.name = body.data.name = "mannequin"
    return body


def slot_groups(body, joints: list) -> dict:
    """Drops weights of bones the template doesn't keep and adds a ``slot_<slot>`` vertex group per
    gear slot. Returns the vertex count of each slot."""
    for group in list(body.vertex_groups):
        if group.name not in joints:
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


def save_base_body(arm, body, path: Path) -> None:
    """base_body.blend: the template armature (sockets included) and the mannequin skinned to it."""
    body.parent = arm
    body.modifiers.new("Armature", "ARMATURE").object = arm
    for obj in list(bpy.data.objects):
        if obj not in (arm, body):
            bpy.data.objects.remove(obj, do_unlink=True)
    if arm.animation_data:
        arm.animation_data.action = None
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=True, copy=True)
    bpy.data.objects.remove(body, do_unlink=True)


def write_skeleton_json(arm, bounds: dict, path: Path) -> None:
    """skeleton.json: each joint's head, tail and parent in the template's rest pose, and the
    bounds of the mesh the template was made for (Blender axes: Z up, front towards -Y, metres).
    The rig stage scales the template to an asset by comparing these bounds with the asset's."""
    joints = {b.name: {"head": [round(v, 5) for v in arm.matrix_world @ b.head_local],
                       "tail": [round(v, 5) for v in arm.matrix_world @ b.tail_local],
                       "parent": b.parent.name if b.parent else None,
                       **({"socket": True} if not b.use_deform else {})} for b in arm.data.bones}
    path.write_text(json.dumps({"axes": "blender: Z up, -Y front, metres", "mesh_bounds": bounds,
                                "joints": joints}, indent=1) + "\n", encoding="utf-8")


def save_skeleton(arm, path: Path) -> None:
    """skeleton.blend: only the armature, in its rest pose, no animation."""
    for obj in list(bpy.data.objects):
        if obj is not arm:
            bpy.data.objects.remove(obj, do_unlink=True)
    if arm.animation_data:
        arm.animation_data.action = None
    for pose_bone in arm.pose.bones:
        pose_bone.matrix_basis = Matrix.Identity(4)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.textures):
        for block in list(collection):
            if block.users == 0:
                collection.remove(block)
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=True, copy=True)


# ---- built from a source pack ---------------------------------------------------

def build_from_pack(arch_id: str, spec: dict, out: Path) -> dict:
    reset()
    objects = import_gltf(str(ARCHETYPES / spec["file"]))
    arm = armature_of(objects)
    # Only meshes skinned to the armature: some packs carry loose props (an icosphere in UAL).
    meshes = [o for o in objects if o.type == "MESH"
              and (o.parent is arm or any(m.type == "ARMATURE" and m.object is arm for m in o.modifiers))]
    joints = template_joints(arm, meshes)
    actions = {a.name: a for a in bpy.data.actions}

    clips_out = out / "clips"
    shutil.rmtree(clips_out, ignore_errors=True)
    record: dict[str, dict] = {}
    for clip, source in spec["clips"].items():
        stand_in = source.endswith("*")
        source = source.rstrip("*")
        action = actions.get(source) or next((a for n, a in actions.items() if n.startswith(source)), None)
        if action is None:
            raise RuntimeError(f"{arch_id}: no clip named {source!r} in {spec['file']}; found {sorted(actions)}")
        drift = make_in_place(arm, action)
        info = export_clip(arm, action, clip, clips_out / f"{clip}.glb")
        record[clip] = {"source": source, "stand_in": stand_in, "loop": clip in LOOPING,
                        "in_place_drift_removed_m": round(drift, 3), **info}

    if spec.get("a_pose"):
        pose_a(arm)  # measure the mesh in the rest pose the template will have
        bounds = posed_mesh_bounds(meshes)
    else:
        clear_pose(arm)
        bounds = mesh_bounds(meshes)
    body = make_base_body(arm, meshes) if spec.get("base_body") else None  # in the template's pose
    for mesh in meshes:
        bpy.data.objects.remove(mesh, do_unlink=True)
    clear_pose(arm)
    keep_only_joints(arm, joints)
    if spec.get("a_pose"):
        apply_a_pose(arm)
    add_sockets(arm, spec.get("sockets"))
    arm.name = arm.data.name = f"{arch_id}_template"
    write_skeleton_json(arm, bounds, out / "skeleton.json")
    slots = None
    if body is not None:
        slots = slot_groups(body, joints)
        save_base_body(arm, body, out / "base_body.blend")
    save_skeleton(arm, out / "skeleton.blend")
    return {"joints": joints, "clips": record, "source": spec, "slots": slots}


# ---- generated (spider, serpent) --------------------------------------------------

def new_armature(name: str, bones: list[tuple[str, tuple, tuple, str | None]]):
    """``bones``: (name, head, tail, parent); positions in metres, Z up, front towards -Y."""
    data = bpy.data.armatures.new(name)
    arm = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    made = {}
    for bone_name, head, tail, parent in bones:
        edit = data.edit_bones.new(bone_name)
        edit.head, edit.tail = Vector(head), Vector(tail)
        if parent:
            edit.parent = made[parent]
        made[bone_name] = edit
    bpy.ops.object.mode_set(mode="OBJECT")
    for pose_bone in arm.pose.bones:
        pose_bone.rotation_mode = "XYZ"
    return arm


def keyframes(arm, name: str, frames: int, pose) -> object:
    """An action of ``frames`` frames; ``pose(t)`` (t from 0 to 1) answers
    {bone: (rx, ry, rz)} in degrees, plus optional "_loc" {bone: (x, y, z)}."""
    action = bpy.data.actions.new(name)
    arm.animation_data_create()
    arm.animation_data.action = action
    for frame in range(frames + 1):
        t = frame / frames
        values = pose(t)
        locations = values.pop("_loc", {})
        for bone_name, (rx, ry, rz) in values.items():
            pose_bone = arm.pose.bones[bone_name]
            pose_bone.rotation_euler = (math.radians(rx), math.radians(ry), math.radians(rz))
            pose_bone.keyframe_insert("rotation_euler", frame=frame)
        for bone_name, loc in locations.items():
            pose_bone = arm.pose.bones[bone_name]
            pose_bone.location = loc
            pose_bone.keyframe_insert("location", frame=frame)
    arm.animation_data.action = None
    for pose_bone in arm.pose.bones:
        pose_bone.rotation_euler = (0, 0, 0)
        pose_bone.location = (0, 0, 0)
    return action


def spider_rig():
    bones = [("root", (0, 0, 0), (0, 0, 0.1), None),
             ("body", (0, 0, 0.32), (0, -0.18, 0.34), "root"),
             ("abdomen", (0, 0.02, 0.34), (0, 0.42, 0.42), "body"),
             ("head", (0, -0.18, 0.34), (0, -0.30, 0.33), "body"),
             ("fang_l", (0.04, -0.30, 0.32), (0.04, -0.36, 0.22), "head"),
             ("fang_r", (-0.04, -0.30, 0.32), (-0.04, -0.36, 0.22), "head")]
    for side, sx, suffix in (("l", 1, "l"), ("r", -1, "r")):
        for index, angle in enumerate((-55, -20, 15, 50), start=1):
            a = math.radians(angle)
            out = Vector((sx * math.cos(a), math.sin(a), 0))
            hip = Vector((sx * 0.08, -0.08 + 0.05 * index, 0.32))
            knee = hip + out * 0.22 + Vector((0, 0, 0.14))
            ankle = knee + out * 0.26 + Vector((0, 0, -0.20))
            foot = ankle + out * 0.10 + Vector((0, 0, -0.26))
            name = f"leg_{suffix}{index}"
            bones += [(f"{name}_1", tuple(hip), tuple(knee), "body"),
                      (f"{name}_2", tuple(knee), tuple(ankle), f"{name}_1"),
                      (f"{name}_3", tuple(ankle), tuple(foot), f"{name}_2")]
    return new_armature("spider_template", bones)


def spider_clips(arm) -> dict[str, tuple[object, bool]]:
    legs = [(side, i) for side in "lr" for i in range(1, 5)]

    def gait(t, lift, swing, phase_speed=1):
        values = {}
        for side, i in legs:
            group = (i + (side == "r")) % 2  # alternating tetrapod gait
            phase = 2 * math.pi * (t * phase_speed) + math.pi * group
            up = max(0.0, math.sin(phase)) * lift
            sway = math.cos(phase) * swing * (1 if side == "l" else -1)
            values[f"leg_{side}{i}_1"] = (0, 0, sway)
            values[f"leg_{side}{i}_2"] = (up, 0, 0)
        return values

    def idle(t):
        v = {f"leg_{s}{i}_2": (2 * math.sin(2 * math.pi * t + i), 0, 0) for s, i in legs}
        v["abdomen"] = (3 * math.sin(2 * math.pi * t), 0, 0)
        v["_loc"] = {"body": (0, 0, 0.01 * math.sin(2 * math.pi * t))}
        return v

    def walk(t):
        v = gait(t, 25, 12)
        v["_loc"] = {"body": (0, 0, 0.012 * abs(math.sin(4 * math.pi * t)))}
        return v

    def run(t):
        v = gait(t, 35, 18, 1)
        v["abdomen"] = (5 * math.sin(4 * math.pi * t), 0, 0)
        return v

    def attack_1(t):  # rear up, strike with the front legs and fangs
        k = math.sin(math.pi * t)
        v = {f"leg_{s}1_1": (-50 * k, 0, 0) for s in "lr"}
        v.update({f"leg_{s}2_1": (-25 * k, 0, 0) for s in "lr"})
        v["body"] = (-20 * k, 0, 0)
        v["fang_l"], v["fang_r"] = (35 * k, 0, 0), (35 * k, 0, 0)
        return v

    def attack_2(t):  # lunge forward and bite
        k = math.sin(math.pi * t)
        v = {"head": (15 * k, 0, 0), "fang_l": (40 * k, 0, 0), "fang_r": (40 * k, 0, 0)}
        v["_loc"] = {"body": (0, -0.12 * k, -0.04 * k)}
        return v

    def hit(t):
        k = math.sin(math.pi * t) * (1 - t)
        v = {"body": (12 * k, 0, 0), "abdomen": (-10 * k, 0, 0)}
        v.update({f"leg_{s}{i}_2": (15 * k, 0, 0) for s, i in legs})
        return v

    def death(t):  # flips the legs in and drops
        k = min(1.0, t * 1.4)
        v = {f"leg_{s}{i}_1": (-45 * k, 0, 0) for s, i in legs}
        v.update({f"leg_{s}{i}_2": (-60 * k, 0, 0) for s, i in legs})
        v["_loc"] = {"body": (0, 0, -0.18 * k)}
        return v

    return {"idle": (keyframes(arm, "idle", 60, idle), True), "walk": (keyframes(arm, "walk", 30, walk), True),
            "run": (keyframes(arm, "run", 18, run), True), "attack_1": (keyframes(arm, "attack_1", 24, attack_1), False),
            "attack_2": (keyframes(arm, "attack_2", 20, attack_2), False), "hit": (keyframes(arm, "hit", 15, hit), False),
            "death": (keyframes(arm, "death", 36, death), False)}


SPINE = 16


def serpent_rig():
    bones = [("root", (0, 0, 0), (0, 0, 0.1), None)]
    length = 0.16
    for i in range(1, SPINE + 1):
        y0, y1 = -1.2 + (i - 1) * length, -1.2 + i * length
        bones.append((f"spine_{i:02d}", (0, y0, 0.12), (0, y1, 0.12 - 0.004 * i), "root" if i == 1 else f"spine_{i - 1:02d}"))
    bones += [("head", (0, -1.2, 0.12), (0, -1.42, 0.16), "spine_01"),
              ("jaw", (0, -1.22, 0.10), (0, -1.38, 0.08), "head")]
    return new_armature("serpent_template", bones)


def serpent_clips(arm) -> dict[str, tuple[object, bool]]:
    def wave(t, amplitude, waves, speed):
        return {f"spine_{i:02d}": (0, 0, amplitude * math.sin(2 * math.pi * (speed * t - waves * i / SPINE)))
                for i in range(1, SPINE + 1)}

    def idle(t):
        v = wave(t, 4, 1, 1)
        v["head"] = (4 * math.sin(2 * math.pi * t), 0, 0)
        return v

    def attack_1(t):  # rear up and strike
        k = math.sin(math.pi * min(1.0, t * 1.3))
        v = {f"spine_{i:02d}": (-18 * k if i <= 4 else 0, 0, 0) for i in range(1, SPINE + 1)}
        v["head"] = (25 * k, 0, 0)
        v["jaw"] = (30 * k, 0, 0)
        return v

    def attack_2(t):  # tail whip
        k = math.sin(math.pi * t)
        return {f"spine_{i:02d}": (0, 0, 22 * k * (i / SPINE) ** 2) for i in range(1, SPINE + 1)}

    def hit(t):
        k = math.sin(math.pi * t) * (1 - t)
        v = {f"spine_{i:02d}": (0, 0, -8 * k) for i in range(1, 6)}
        v["head"] = (-15 * k, 0, 0)
        return v

    def death(t):
        k = min(1.0, t * 1.2)
        v = {f"spine_{i:02d}": (0, 0, 10 * k * math.sin(i)) for i in range(1, SPINE + 1)}
        v["head"] = (-20 * k, 0, 0)
        v["jaw"] = (25 * k, 0, 0)
        return v

    return {"idle": (keyframes(arm, "idle", 60, idle), True),
            "walk": (keyframes(arm, "walk", 40, lambda t: wave(t, 14, 1.5, 1)), True),
            "run": (keyframes(arm, "run", 24, lambda t: wave(t, 22, 1.5, 1)), True),
            "attack_1": (keyframes(arm, "attack_1", 24, attack_1), False),
            "attack_2": (keyframes(arm, "attack_2", 24, attack_2), False),
            "hit": (keyframes(arm, "hit", 15, hit), False), "death": (keyframes(arm, "death", 36, death), False)}


def build_procedural(arch_id: str, spec: dict, out: Path) -> dict:
    reset()
    arm = spider_rig() if spec["procedural"] == "spider" else serpent_rig()
    actions = spider_clips(arm) if spec["procedural"] == "spider" else serpent_clips(arm)
    clips_out = out / "clips"
    shutil.rmtree(clips_out, ignore_errors=True)
    record = {}
    for clip, (action, loop) in actions.items():
        info = export_clip(arm, action, clip, clips_out / f"{clip}.glb")
        record[clip] = {"source": "procedural", "stand_in": False, "loop": loop, **info}
    joints = [bone.name for bone in arm.data.bones]
    clear_pose(arm)
    write_skeleton_json(arm, joint_bounds(arm), out / "skeleton.json")
    save_skeleton(arm, out / "skeleton.blend")
    return {"joints": joints, "clips": record, "source": spec}


# ---- records ---------------------------------------------------------------------

def licences(arch_id: str, spec: dict, record: dict) -> str:
    lines = [f"# {arch_id}: sources and licences", "",
             "Built by `backend/blender_scripts/build_archetypes.py`; run it again to rebuild.", ""]
    if "procedural" in spec:
        lines += ["The skeleton and every clip are generated by the build script (no third-party content).", ""]
    else:
        lines += [f"Skeleton and clips: **{spec['pack']}** ({spec['url']}), file `{spec['file']}`.",
                  f"Licence: {QUATERNIUS_LICENCE}", ""]
    lines += ["| Clip | Source clip | Seconds | Loops | Note |", "|---|---|---|---|---|"]
    for clip, info in record.items():
        note = "stand-in until a better clip exists" if info["stand_in"] else ""
        lines.append(f"| {clip} | {info['source']} | {info['duration']} | {'yes' if info['loop'] else 'no'} | {note} |")
    return "\n".join(lines) + "\n"


def update_archetype_json(arch_id: str, spec: dict, result: dict) -> None:
    path = ARCHETYPES / arch_id / "archetype.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["joints"] = result["joints"]
    data["clips"] = list(result["clips"])
    if "sockets" in spec:
        data["sockets"] = spec["sockets"]
    if "procedural" in spec:
        data["skeleton_source"] = data["clip_source"] = "Generated by build_archetypes.py"
    else:
        data["skeleton_source"] = data["clip_source"] = f"{spec['pack']} (CC0)"
    data["description"] = data["description"].split(".")[0] + "."
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out_root = None
    if "--out" in argv:
        out_root = Path(argv[argv.index("--out") + 1])
        argv = [a for i, a in enumerate(argv) if a != "--out" and (i == 0 or argv[i - 1] != "--out")]
    ids = argv or list(SOURCES)
    summary = {}
    for n, arch_id in enumerate(ids):
        spec = SOURCES[arch_id]
        progress(n / len(ids), f"Building {arch_id}")
        out = (out_root / arch_id) if out_root else (ARCHETYPES / arch_id)
        result = build_procedural(arch_id, spec, out) if "procedural" in spec else build_from_pack(arch_id, spec, out)
        (out / "clips.json").write_text(json.dumps(result["clips"], indent=2) + "\n", encoding="utf-8")
        (out / "LICENCES.md").write_text(licences(arch_id, spec, result["clips"]), encoding="utf-8")
        if out_root is None:
            update_archetype_json(arch_id, spec, result)
        summary[arch_id] = {"joints": len(result["joints"]), "clips": len(result["clips"]),
                            "slots": result.get("slots")}
        print(f"BUILT {arch_id}: {summary[arch_id]['joints']} joints, {summary[arch_id]['clips']} clips", flush=True)
    progress(1.0, "Archetypes built")
    print("SUMMARY " + json.dumps(summary), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - the reason is the last line Blender prints
        import traceback
        traceback.print_exc()
        print(f"ERROR {type(exc).__name__}: {exc}", flush=True)
        sys.exit(1)
