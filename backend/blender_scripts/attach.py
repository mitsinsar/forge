"""Gate 3 for gear and weapons: fit the item on the biped base body and play the clips.

Called by ``rig_animate.py`` when its arguments have ``attach``::

    {"mesh_glb": "mesh/mesh.glb", "base_body_blend": ".../biped/base_body.blend",
     "template_object": "biped_template",
     "wearer_blend": ".../<hero>/rig/rigged.blend" | null,
     "clips": [{"name": "walk", "file": ".../clips/walk.glb", "loop": true}, ...],
     "attach": {"kind": "gear", "slot": "head"}
             | {"kind": "weapon", "weapon_class": "one_handed", "socket": "weapon_r",
                "grip_matrix": [[...4x4, Blender model space...]] | null},
     "out_glb": "rig/animated.glb", "mask_json": "rig/body_mask.json"}

**Gear** (``fit_gear``): the item (LOD0) is scaled and moved onto its slot's region of the body
(the ``slot_<slot>`` vertex group) by their bounds: centred, covering the region's width and
depth (head, chest, legs, cape) or its length along the limb (hands, feet: each side on its own;
one glove or boot is mirrored for the other side); a helmet's top sits just above the head's. A
Shrinkwrap in "outside" mode with an offset
then pushes out whatever still sits inside the body, so nothing clips at rest. The body's weights
are copied onto it (Data Transfer, nearest face; a helmet follows the head bone only), and the body faces it covers (a ray from the
face along its normal hits the item within ``MASK_REACH``) are recorded as the mask and left out
of the preview body.

**Weapons** (``detect_grip``): rigid, never skinned. The grip is found on the weapon's longest
axis: the handle is its thinner end (bows: the middle; staffs: 45 % up), and the grip frame has
+Y pointing to the blade and +Z across the blade's width. The weapon is parented to the socket
bone so the grip frame sits on the bone's frame (+Y out of the fist on the thumb side, +Z along
the fingers). A grip given at gate 3 (``grip_matrix``) replaces the detected one. Two-handed
weapons also get a second grip, 12 cm towards the blade, for ``weapon_l``.

**Wearer**: with ``wearer_blend`` the item is fitted on that character instead of the base body:
its skinned mesh and armature are loaded from the scene its rig stage kept, its slot regions are
worked out the same way (each vertex's strongest bone), and its own baked clips are played, so
nothing is retargeted.

The preview GLB has the body (named ``mannequin``, which the FBX export and the sprite render
leave out or hide), the item and the armature with every clip.

Result: ``{"clips", "gear": {...}} | {"clips", "weapon": {"grip_matrix", "second_grip_matrix",
"length", "handle_end"}}``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

import rig_animate as rig
from _common import progress, select_only, slot_groups

MARGIN = 1.08        # the item covers its region by this much
OFFSET = 0.008       # metres between the body and pushed-out gear
MASK_REACH = 0.06    # a body face is under the gear when the gear is this close along its normal
SECOND_GRIP = 0.12   # metres from the main grip to the second hand's (two-handed)


# ---- loading ----------------------------------------------------------------------

def load_base_body(blend: str, armature: str):
    with bpy.data.libraries.load(blend, link=False) as (source, target):
        target.objects = [name for name in source.objects if name in (armature, "mannequin")]
    for obj in target.objects:
        bpy.context.scene.collection.objects.link(obj)
    arm = bpy.data.objects.get(armature)
    body = bpy.data.objects.get("mannequin")
    if arm is None or body is None:
        raise ValueError(f"{blend} needs the objects {armature} and mannequin; rebuild the templates")
    return arm, body


def load_wearer(blend: str, armature: str, clips: list[str]):
    """A rigged character's armature, its skinned mesh (as ``mannequin``, with slot regions) and its
    baked clips, from the ``rigged.blend`` its rig stage kept."""
    with bpy.data.libraries.load(blend, link=False) as (source, target):
        target.objects = [name for name in source.objects if name in (armature, "mesh")]
        target.actions = [name for name in source.actions if name in clips]
    for obj in target.objects:
        bpy.context.scene.collection.objects.link(obj)
    # The appended objects themselves: names can clash with the item's (which is also "mesh").
    arm = next((o for o in target.objects if o.type == "ARMATURE"), None)
    body = next((o for o in target.objects if o.type == "MESH"), None)
    if arm is None or body is None:
        raise ValueError(f"{blend} has no {armature} and mesh; re-rig the wearer")
    missing = [c for c in clips if c not in {a.name for a in target.actions}]
    if missing:
        raise ValueError(f"the wearer has no clip {', '.join(missing)}; re-rig it so it gets every clip")
    for action in target.actions:
        action.use_fake_user = True
    body.name = body.data.name = "mannequin"
    if arm.animation_data:
        arm.animation_data.action = None
    slot_groups(body, None)
    if "weapon_r" not in arm.data.bones:
        raise ValueError("the wearer's skeleton has no weapon sockets; re-rig it")
    return arm, body


def world_points(obj, indices=None) -> list[Vector]:
    vertices = obj.data.vertices
    chosen = vertices if indices is None else (vertices[i] for i in indices)
    return [obj.matrix_world @ v.co for v in chosen]


def group_indices(obj, name: str) -> list[int]:
    group = obj.vertex_groups.get(name)
    if group is None:
        raise ValueError(f"the base body has no vertex group {name}")
    return [v.index for v in obj.data.vertices if any(g.group == group.index and g.weight > 0.5 for g in v.groups)]


def principal_axis(points: list[Vector]) -> tuple[Vector, Vector]:
    """Centre and longest axis (power iteration on the covariance)."""
    centre = sum(points, Vector()) / len(points)
    cov = Matrix(((0.0,) * 3,) * 3)
    for p in points:
        d = p - centre
        for i in range(3):
            for j in range(3):
                cov[i][j] += d[i] * d[j]
    axis = Vector((0.3, 0.2, 1.0)).normalized()
    for _ in range(50):
        axis = (cov @ axis).normalized()
    return centre, axis


# ---- gear ---------------------------------------------------------------------------

def place(obj, matrix: Matrix) -> None:
    obj.matrix_world = matrix @ obj.matrix_world
    select_only([obj])
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def fit_box(item, region: list[Vector], axes: tuple[int, ...], anchor: str) -> float:
    """Scales ``item`` uniformly so it covers ``region`` along ``axes`` (by MARGIN) and moves it
    there: centred, or with its bottom on the region's bottom (``anchor`` "bottom") or its front
    against the region's back ("back"). Returns the scale."""
    i_lo, i_hi = rig.bounds(world_points(item))
    r_lo, r_hi = rig.bounds(region)
    i_size, r_size = i_hi - i_lo, r_hi - r_lo
    scale = max(r_size[a] * MARGIN / max(i_size[a], 1e-6) for a in axes)
    place(item, Matrix.Diagonal((scale, scale, scale, 1.0)))
    i_lo, i_hi = rig.bounds(world_points(item))
    move = (r_lo + r_hi) / 2 - (i_lo + i_hi) / 2
    if anchor == "bottom":
        move.z = r_lo.z - i_lo.z
    elif anchor == "top":
        move.z = r_hi.z + r_size.z * (MARGIN - 1) / 2 - i_hi.z
    elif anchor == "back":
        move.y = (r_lo.y + r_hi.y) / 2 - i_lo.y
        move.z = r_hi.z - i_hi.z
    place(item, Matrix.Translation(move))
    return scale


def fit_along(item, region: list[Vector], up: Vector) -> float:
    """Turns ``item``'s up (+Z, as drawn) onto the region's long axis (pointing ``up``-wards
    along it, towards the fingers or toes), then scales it to the region's length and centres it."""
    centre, axis = principal_axis(region)
    if axis.dot(up) < 0:
        axis = -axis
    place(item, Matrix.Translation(-sum(world_points(item), Vector()) / len(item.data.vertices)))
    place(item, Vector((0, 0, 1)).rotation_difference(axis).to_matrix().to_4x4())
    lengths = [p.dot(axis) for p in world_points(item)]
    reach = [p.dot(axis) for p in region]
    scale = (max(reach) - min(reach)) * MARGIN / max(max(lengths) - min(lengths), 1e-6)
    place(item, Matrix.Diagonal((scale, scale, scale, 1.0)))
    place(item, Matrix.Translation(centre - sum(world_points(item), Vector()) / len(item.data.vertices)))
    return scale


def split_pair(item) -> list:
    """A pair (gloves, boots) as two objects, left and right of the item's middle; a single piece
    stays one object."""
    centre_x = sum(p.x for p in world_points(item)) / len(item.data.vertices)
    bm = bmesh.new()
    bm.from_mesh(item.data)
    bm.verts.ensure_lookup_table()
    islands, seen = [], set()
    for vert in bm.verts:
        if vert.index in seen:
            continue
        stack, island = [vert], []
        seen.add(vert.index)
        while stack:
            v = stack.pop()
            island.append(v.index)
            for edge in v.link_edges:
                other = edge.other_vert(v)
                if other.index not in seen:
                    seen.add(other.index)
                    stack.append(other)
        islands.append(island)
    left = {i for island in islands
            if sum(bm.verts[j].co.x for j in island) / len(island) > centre_x for i in island}
    bm.free()
    total = len(item.data.vertices)
    if not (0.25 * total < len(left) < 0.75 * total):
        return [item]
    other = item.copy()
    other.data = item.data.copy()
    bpy.context.scene.collection.objects.link(other)
    for obj, keep_left in ((item, False), (other, True)):
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if (v.index in left) != keep_left], context="VERTS")
        bm.to_mesh(obj.data)
        bm.free()
    return [item, other]  # right piece (lower x: the character's right), left piece


def mirror_copy(item):
    other = item.copy()
    other.data = item.data.copy()
    bpy.context.scene.collection.objects.link(other)
    place(other, Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0)))
    select_only([other])
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.flip_normals()
    bpy.ops.object.mode_set(mode="OBJECT")
    return other


def join(objects):
    select_only(objects, active=objects[0])
    if len(objects) > 1:
        bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def push_outside(item, body) -> None:
    wrap = item.modifiers.new("Outside", "SHRINKWRAP")
    wrap.target = body
    wrap.wrap_method = "NEAREST_SURFACEPOINT"
    wrap.wrap_mode = "OUTSIDE"
    wrap.offset = OFFSET
    select_only([item])
    bpy.ops.object.modifier_apply(modifier=wrap.name)


def inside_count(item, body) -> int:
    """Item vertices inside the body (behind the nearest body surface)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    tree = BVHTree.FromObject(body, depsgraph)
    count = 0
    for p in world_points(item):
        hit, normal, _, _ = tree.find_nearest(p)
        if hit is not None and (p - hit).dot(normal) < -1e-4:
            count += 1
    return count


def copy_weights(item, body, arm) -> None:
    transfer = item.modifiers.new("Weights", "DATA_TRANSFER")
    transfer.object = body
    transfer.use_vert_data = True
    transfer.data_types_verts = {"VGROUP_WEIGHTS"}
    transfer.vert_mapping = "POLYINTERP_NEAREST"
    transfer.layers_vgroup_select_src = "ALL"
    transfer.layers_vgroup_select_dst = "NAME"
    select_only([item])
    bpy.ops.object.datalayout_transfer(modifier=transfer.name)
    bpy.ops.object.modifier_apply(modifier=transfer.name)
    for group in list(item.vertex_groups):  # the slot regions are not bones
        if group.name.startswith("slot_"):
            item.vertex_groups.remove(group)
    select_only([item, arm], active=arm)
    bpy.ops.object.parent_set(type="ARMATURE")
    if rig.unweighted(item):
        rig.nearest_bone_weights(item, arm, only_unweighted=True)


def mask_faces(body, item) -> list[int]:
    """Body faces the item covers: a ray from the face along its normal meets the item close by."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    tree = BVHTree.FromObject(item, depsgraph)
    to_world = body.matrix_world
    turn = to_world.to_3x3().inverted().transposed()
    covered = []
    for face in body.data.polygons:
        centre = to_world @ face.center
        normal = (turn @ face.normal).normalized()
        hit = tree.ray_cast(centre + normal * 1e-4, normal, MASK_REACH)
        if hit[0] is not None:
            covered.append(face.index)
    return covered


def hide_faces(body, faces: list[int]) -> None:
    if not faces:
        return
    bm = bmesh.new()
    bm.from_mesh(body.data)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[i] for i in faces], context="FACES_ONLY")
    bm.to_mesh(body.data)
    bm.free()


RIGID = {"head": "Head"}  # slots whose item moves with one bone only (a helmet doesn't bend)

SLOT_FIT = {  # how each slot's item is placed on its region
    "head": ("box", (0, 1), "top"),
    "chest": ("box", (0, 1), "centre"),
    "legs": ("box", (2,), "centre"),
    "cape": ("box", (0, 2), "back"),
    "hands": ("pair", None, "along"),
    "feet": ("pair", (0, 1), "bottom"),
}


def fit_gear(item, body, arm, slot: str) -> dict:
    region_ids = group_indices(body, f"slot_{slot}")
    region = world_points(body, region_ids)
    mode, axes, anchor = SLOT_FIT[slot]
    if mode == "box":
        scales = [fit_box(item, region, axes, anchor)]
    else:
        sides = {"r": [p for p in region if p.x < 0], "l": [p for p in region if p.x >= 0]}
        pieces = split_pair(item)
        if len(pieces) == 1:
            pieces.append(mirror_copy(item))
        scales = []
        for piece, side in zip(pieces, ("r", "l")):
            if anchor == "along":
                hand = arm.data.bones[f"hand_{side}"]
                scales.append(fit_along(piece, sides[side], arm.matrix_world.to_3x3() @ (hand.tail_local - hand.head_local)))
            else:
                scales.append(fit_box(piece, sides[side], axes, anchor))
        item = join(pieces)
    item.name = item.data.name = "gear"
    before = inside_count(item, body)
    push_outside(item, body)
    after = inside_count(item, body)
    if slot in RIGID:
        for group in list(item.vertex_groups):
            item.vertex_groups.remove(group)
        item.vertex_groups.new(name=RIGID[slot]).add(range(len(item.data.vertices)), 1.0, "REPLACE")
        select_only([item, arm], active=arm)
        bpy.ops.object.parent_set(type="ARMATURE")
    else:
        copy_weights(item, body, arm)
    covered = mask_faces(body, item)
    return {"item": item, "slot": slot, "scale": round(sum(scales) / len(scales), 4), "inside_before": before,
            "inside_after": after, "masked_faces": covered}


# ---- weapons --------------------------------------------------------------------------

def dense_points(item, pieces: int = 48) -> list[Vector]:
    """The item's vertices after splitting every edge longer than 1/``pieces`` of its size, so a
    mesh with few, long faces is measured along its whole length."""
    bm = bmesh.new()
    bm.from_mesh(item.data)
    size = max(item.dimensions) or 1.0
    for _ in range(8):
        long = [e for e in bm.edges if e.calc_length() > size / pieces]
        if not long:
            break
        bmesh.ops.subdivide_edges(bm, edges=long, cuts=1, use_grid_fill=False)
    points = [v.co.copy() for v in bm.verts]
    bm.free()
    return points


def detect_grip(item, weapon_class: str) -> tuple[Matrix, dict]:
    """The grip frame in the weapon's model space (see the module notes) and what was measured."""
    points = dense_points(item)
    centre, axis = principal_axis(points)
    ts = [(p - centre).dot(axis) for p in points]
    lo, hi = min(ts), max(ts)
    length = hi - lo
    # Two more axes: the width (spread across the long axis) and the thickness.
    side = axis.orthogonal().normalized()
    best = max((Matrix.Rotation(math.radians(a), 3, axis) @ side for a in range(0, 180, 6)),
               key=lambda d: max((p - centre).dot(d) for p in points) - min((p - centre).dot(d) for p in points))
    width_axis = best.normalized()
    bins = 24
    thick = [0.0] * bins
    members: list[list[Vector]] = [[] for _ in range(bins)]
    for p, t in zip(points, ts):
        members[min(bins - 1, int((t - lo) / max(length, 1e-6) * bins))].append(p)
    for i, group in enumerate(members):
        if group:
            spread = [(p - centre).dot(width_axis) for p in group]
            thick[i] = max(spread) - min(spread)
    quarter = bins // 4
    filled = [i for i in range(bins) if members[i]]
    start_bins, end_bins = filled[:quarter], filled[-quarter:]
    start_mean = sum(thick[i] for i in start_bins) / max(len(start_bins), 1)
    end_mean = sum(thick[i] for i in end_bins) / max(len(end_bins), 1)
    if weapon_class == "bow":
        handle_end, grip_t = "middle", lo + 0.5 * length
    elif weapon_class == "staff":
        handle_end = "low" if (centre + axis * lo).z < (centre + axis * hi).z else "high"
        grip_t = lo + 0.45 * length if handle_end == "low" else hi - 0.45 * length
    else:
        handle_end = "low" if start_mean <= end_mean else "high"
        order = range(bins) if handle_end == "low" else range(bins - 1, -1, -1)
        nonempty = [i for i in order if members[i]]
        thinnest = min(thick[i] for i in nonempty[:quarter])
        run = []
        for i in nonempty:
            if thick[i] <= 1.6 * thinnest + 0.005:
                run.append(i)
            elif run:
                break
        middle = run[len(run) // 2] if run else nonempty[0]
        grip_t = lo + (middle + 0.5) / bins * length
    blade = axis if handle_end != "high" else -axis
    if handle_end == "middle":  # a bow: the string side doesn't matter, point "up"
        blade = axis if axis.z >= 0 else -axis
    near = [p for p, t in zip(points, ts) if abs(t - grip_t) <= length / bins]
    point = sum(near, Vector()) / len(near) if near else centre + axis * grip_t
    y = blade.normalized()
    z = (width_axis - y * width_axis.dot(y)).normalized()
    x = y.cross(z)
    frame = Matrix((x, y, z)).transposed().to_4x4()
    frame.translation = point
    return frame, {"length": round(length, 4), "handle_end": handle_end,
                   "thickness_ends": [round(start_mean, 4), round(end_mean, 4)]}


def attach_weapon(item, arm, socket: str, grip: Matrix) -> None:
    bone = arm.data.bones[socket]
    bone_frame = arm.matrix_world @ bone.matrix_local
    bone_frame.normalize()
    item.parent = arm
    item.parent_type = "BONE"
    item.parent_bone = socket
    item.matrix_world = bone_frame @ grip.inverted()


def as_rows(matrix: Matrix) -> list[list[float]]:
    return [[round(v, 6) for v in row] for row in matrix]


# ---- main ---------------------------------------------------------------------------------

def main(args: dict) -> dict:
    attach = args["attach"]
    progress(0.02, "Loading the base body and the item")
    rig.empty_scene()
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = rig.FPS, 1.0
    # "body": no item, the base body alone with its clips (the test dummy outfits dress).
    item = None if attach["kind"] == "body" else rig.load_lod0(args["mesh_glb"])
    wearer = args.get("wearer_blend")
    if wearer:
        arm, body = load_wearer(wearer, args["template_object"], [c["name"] for c in args["clips"]])
    else:
        arm, body = load_base_body(args["base_body_blend"], args["template_object"])
    result: dict = {}
    if attach["kind"] == "gear":
        progress(0.08, f"Fitting the {attach['slot']} gear on the base body")
        fitted = fit_gear(item, body, arm, attach["slot"])
        item = fitted.pop("item")
        covered = fitted.pop("masked_faces")
        Path(args["mask_json"]).write_text(json.dumps({"slot": attach["slot"], "faces": covered}), encoding="utf-8")
        hide_faces(body, covered)
        result["gear"] = {**fitted, "masked_faces": len(covered)}
    elif attach["kind"] == "weapon":
        progress(0.08, "Finding the grip")
        item.name = item.data.name = "weapon"
        detected, measured = detect_grip(item, attach["weapon_class"])
        manual = attach.get("grip_matrix")
        grip = Matrix(manual) if manual else detected
        attach_weapon(item, arm, attach["socket"], grip)
        second = None
        if attach["weapon_class"] == "two_handed":
            second = grip.copy()
            second.translation = grip.translation + grip.to_3x3() @ Vector((0.0, SECOND_GRIP, 0.0))
        result["weapon"] = {"grip_matrix": as_rows(grip), "detected_grip_matrix": as_rows(detected),
                            "second_grip_matrix": as_rows(second) if second else None,
                            "manual": bool(manual), **measured}

    if wearer:  # its clips are already baked on its skeleton
        clips = [{"name": c["name"], "duration": c["duration"], "loop": bool(c.get("loop"))} for c in args["clips"]]
    else:
        reference, fitted_rest = rig.rest_directions(arm), rig.rest_rotations(arm)
        clips = []
        for index, clip in enumerate(args["clips"]):
            progress(0.2 + 0.7 * index / max(len(args["clips"]), 1), f"Retargeting {clip['name']}")
            clips.append(rig.retarget(arm, clip, 1.0, Vector(), reference, fitted_rest))

    progress(0.92, "Writing the animated GLB")
    out = Path(args["out_glb"])
    out.parent.mkdir(parents=True, exist_ok=True)
    select_only([o for o in (body, item, arm) if o is not None], active=arm)
    bpy.ops.export_scene.gltf(filepath=str(out), export_format="GLB", use_selection=True, export_yup=True,
                              export_apply=False, export_animations=True, export_animation_mode="ACTIONS",
                              export_force_sampling=True, export_skins=True, export_def_bones=False,
                              export_materials="EXPORT")
    progress(1.0, "Animated result ready")
    return {"clips": clips, **result}
