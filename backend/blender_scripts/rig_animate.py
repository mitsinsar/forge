"""Gate 3: rig the cleaned mesh with the archetype's template skeleton and retarget its clips.

Arguments (args.json)::

    {"mesh_glb": "mesh/mesh.glb", "template_blend": ".../skeleton.blend", "template_object": "biped_template",
     "template_json": ".../skeleton.json", "fit_axis": "z" | "longest",
     "clips": [{"name": "idle", "file": ".../clips/idle.glb", "loop": true}, ...],
     "unirig_glb": ".../rigged.glb" | null, "mapping": {"bone_0": "pelvis", ...} | null,
     "out_glb": "rig/animated.glb"}

Steps:

1. Import the mesh and keep LOD0; append the template armature and scale it uniformly to
   the mesh (along Z for upright archetypes, else the template's longest axis), standing on
   the same ground and centred over it.
2. Skin LOD0 to it: UniRig's weights (from its merged GLB, its bones renamed to the matched
   template joints, copied vertex by vertex from the nearest UniRig vertex; weight on bones more
   than two steps in the tree from the vertex's nearest bone is dropped, since UniRig bleeds
   weight between limbs that touch; without a predicted neck or head, the head's spine weight
   moves to the template's head bone) when given; else Blender's automatic weights; vertices
   either leaves unweighted go to their nearest bone. Then the mesh is cut where faces that
   follow far-apart bones meet (hands fused to the thighs), so those parts separate when they
   move instead of stretching.
3. For every clip: import the clip's armature at the same scale and place, copy each bone's
   world rotation onto the template bone (Copy Rotation, world space), bake that into a new
   action named after the clip, then move the hips by the clip's travel from its rest. World
   rotations make clips made on a different rest pose (the biped's clips have a T-pose rest,
   its template an A-pose) play correctly; copying only rotations keeps the asset's own bone
   lengths and stance, so nothing stretches.
4. Export one GLB with the skinned mesh, the armature and one animation per clip.

Result: ``{"clips": [{"name", "duration", "loop"}], "scale", "skinning", "unweighted_before_fix",
"cross_limb_weights_dropped", "joints_placed", "joints"}`` (``joints``: each deforming joint's
rest head in the mesh's space, Blender axes, and its parent: what gate 3's joint editor shows).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402
from mathutils.kdtree import KDTree  # noqa: E402

from _common import empty_scene, import_gltf, progress, run, select_only  # noqa: E402

FPS = 30


def bounds(points) -> tuple[Vector, Vector]:
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return lo, hi


def load_lod0(path: str):
    objects = import_gltf(path)
    meshes = [o for o in objects if o.type == "MESH"]
    if not meshes:
        raise ValueError("the gate 2 mesh has no mesh objects")
    lod0 = next((m for m in meshes if m.name.startswith("LOD0")), max(meshes, key=lambda m: len(m.data.polygons)))
    for obj in objects:
        if obj is not lod0:
            bpy.data.objects.remove(obj, do_unlink=True)
    matrix = lod0.matrix_world.copy()
    lod0.parent = None
    lod0.matrix_world = matrix
    select_only([lod0])
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    lod0.name = "mesh"
    for group in list(lod0.vertex_groups):
        lod0.vertex_groups.remove(group)
    return lod0


def append_template(blend: str, name: str):
    bpy.ops.wm.append(directory=f"{blend}/Object/", filename=name)
    arm = bpy.data.objects.get(name)
    if arm is None or arm.type != "ARMATURE":
        raise ValueError(f"{blend} has no armature named {name}")
    return arm


def fit_template(arm, mesh, template: dict, axis: str) -> tuple[float, Vector]:
    """Scales and moves the template armature onto the mesh; returns (scale, offset) so clip
    armatures can be placed the same way."""
    m_lo, m_hi = bounds([v.co for v in mesh.data.vertices])
    t_lo = Vector(template["mesh_bounds"]["min"])
    t_hi = Vector(template["mesh_bounds"]["max"])
    t_size, m_size = t_hi - t_lo, m_hi - m_lo
    index = 2 if axis == "z" else max(range(3), key=lambda i: t_size[i])
    scale = m_size[index] / max(t_size[index], 1e-6)
    t_centre = (t_lo + t_hi) / 2
    m_centre = (m_lo + m_hi) / 2
    offset = Vector((m_centre.x - scale * t_centre.x, m_centre.y - scale * t_centre.y, m_lo.z - scale * t_lo.z))
    arm.scale = (scale, scale, scale)
    arm.location = offset
    select_only([arm])
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return scale, offset


def pose_to_targets(arm, targets: dict) -> int:
    """Moves the template's joints onto the asset: each targeted bone's head goes to its target
    and the bone turns towards its child's target (lengths kept); the result becomes the rest
    pose. Returns how many bones were placed."""
    if not targets:
        return 0
    select_only([arm])
    bpy.ops.object.mode_set(mode="POSE")
    depth = {b.name: len(b.parent_recursive) for b in arm.data.bones}
    placed = 0
    for pose_bone in sorted(arm.pose.bones, key=lambda b: depth[b.name]):
        name = pose_bone.name
        if name in targets:
            matrix = pose_bone.matrix.copy()
            matrix.translation = Vector(targets[name])
            pose_bone.matrix = matrix
            bpy.context.view_layer.update()
            placed += 1
        if not pose_bone.children:
            continue
        tail = pose_bone.tail.copy()
        child = min(pose_bone.children, key=lambda c: (c.head - tail).length)  # the one the bone points at
        if child.name not in targets:
            continue  # turning towards a side branch (a clavicle) would swing the main one (the neck)
        head = pose_bone.head.copy()
        current, wanted = child.head - head, Vector(targets[child.name]) - head
        if current.length < 1e-6 or wanted.length < 1e-6:
            continue
        turn = current.rotation_difference(wanted).to_matrix().to_4x4()
        pose_bone.matrix = Matrix.Translation(head) @ turn @ Matrix.Translation(-head) @ pose_bone.matrix
        bpy.context.view_layer.update()
    bpy.ops.pose.armature_apply(selected=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    return placed


def proxy_automatic_weights(mesh, arm) -> None:
    """Automatic (heat) weights computed on a watertight voxel copy of the mesh and copied back.
    Heat weighting fails on the open, self-intersecting meshes image-to-3D models make."""
    height = max(mesh.dimensions) or 1.0
    proxy = mesh.copy()
    proxy.data = mesh.data.copy()
    bpy.context.scene.collection.objects.link(proxy)
    for group in list(proxy.vertex_groups):
        proxy.vertex_groups.remove(group)
    remesh = proxy.modifiers.new("Remesh", "REMESH")
    remesh.mode, remesh.voxel_size = "VOXEL", height / 120
    select_only([proxy])
    bpy.ops.object.modifier_apply(modifier=remesh.name)
    select_only([proxy, arm], active=arm)
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    for group in list(mesh.vertex_groups):
        mesh.vertex_groups.remove(group)
    transfer = mesh.modifiers.new("Weights", "DATA_TRANSFER")
    transfer.object = proxy
    transfer.use_vert_data = True
    transfer.data_types_verts = {"VGROUP_WEIGHTS"}
    transfer.vert_mapping = "POLYINTERP_NEAREST"
    transfer.layers_vgroup_select_src = "ALL"
    transfer.layers_vgroup_select_dst = "NAME"
    select_only([mesh])
    bpy.ops.object.datalayout_transfer(modifier=transfer.name)
    bpy.ops.object.modifier_apply(modifier=transfer.name)
    bpy.data.objects.remove(proxy, do_unlink=True)


def nearest_bone_weights(mesh, arm, only_unweighted: bool) -> int:
    """Gives each (unweighted) vertex fully to the bone whose segment is nearest."""
    segments = [(b.name, b.head_local.copy(), b.tail_local.copy()) for b in arm.data.bones if b.use_deform]
    groups = {name: (mesh.vertex_groups.get(name) or mesh.vertex_groups.new(name=name)) for name, _, _ in segments}
    fixed = 0
    for vertex in mesh.data.vertices:
        if only_unweighted and any(g.weight > 1e-4 for g in vertex.groups):
            continue
        best, best_d = None, float("inf")
        for name, head, tail in segments:
            seg = tail - head
            t = 0.0 if seg.length_squared == 0 else max(0.0, min(1.0, (vertex.co - head).dot(seg) / seg.length_squared))
            d = (vertex.co - (head + seg * t)).length
            if d < best_d:
                best, best_d = name, d
        groups[best].add([vertex.index], 1.0, "REPLACE")
        fixed += 1
    return fixed


def unweighted(mesh) -> int:
    return sum(1 for v in mesh.data.vertices if not any(g.weight > 1e-4 for g in v.groups))


def unirig_weights(mesh, arm, unirig_glb: str, mapping: dict) -> int:
    """Copies UniRig's weights onto ``mesh``, each UniRig bone renamed to its matched template joint
    (several UniRig bones on one joint add up), from the nearest vertex of UniRig's copy of the mesh."""
    objects = import_gltf(unirig_glb)
    sources = [o for o in objects if o.type == "MESH" and o.vertex_groups]
    if not sources:
        raise ValueError("UniRig's result has no weighted mesh")
    points, weights = [], []
    for src in sources:
        names = {g.index: g.name for g in src.vertex_groups}
        for vertex in src.data.vertices:
            points.append(src.matrix_world @ vertex.co)
            row: dict[str, float] = {}
            for g in vertex.groups:
                joint = mapping.get(names.get(g.group, ""))
                if joint and g.weight > 1e-4:
                    row[joint] = row.get(joint, 0.0) + g.weight
            weights.append(row)
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    tree = KDTree(len(points))
    for i, p in enumerate(points):
        tree.insert(p, i)
    tree.balance()
    near = bone_neighbourhoods(arm)
    used = set(mapping.values())
    segments = [(b.name, b.head_local.copy(), b.tail_local.copy()) for b in arm.data.bones if b.name in used]
    groups: dict[str, object] = {}
    dropped = 0
    for vertex in mesh.data.vertices:
        _, index, _ = tree.find(vertex.co)
        anchor = nearest_bone(vertex.co, segments)
        full = weights[index]
        # UniRig bleeds weight across limbs that touch (hands hanging by the thighs): keep only
        # bones close to the vertex's nearest bone in the skeleton tree.
        row = {j: w for j, w in full.items() if j in near[anchor]}
        if len(row) < len(full):
            dropped += 1
        if not row:
            row = {anchor: 1.0}
        total = sum(row.values())
        for joint, w in row.items():
            group = groups.get(joint) or mesh.vertex_groups.get(joint) or mesh.vertex_groups.new(name=joint)
            groups[joint] = group
            group.add([vertex.index], w / total, "REPLACE")
    return dropped


def bone_neighbourhoods(arm, hops: int = 2) -> dict[str, set[str]]:
    """For each bone, the bones at most ``hops`` steps away in the tree (itself included)."""
    links: dict[str, set[str]] = {b.name: set() for b in arm.data.bones}
    for bone in arm.data.bones:
        if bone.parent:
            links[bone.name].add(bone.parent.name)
            links[bone.parent.name].add(bone.name)
    result = {}
    for name in links:
        seen, frontier = {name}, {name}
        for _ in range(hops):
            frontier = {n for f in frontier for n in links[f]} - seen
            seen |= frontier
        result[name] = seen
    return result


def nearest_bone(point, segments) -> str:
    best, best_d = segments[0][0], float("inf")
    for name, head, tail in segments:
        seg = tail - head
        t = 0.0 if seg.length_squared == 0 else max(0.0, min(1.0, (point - head).dot(seg) / seg.length_squared))
        d = (point - (head + seg * t)).length
        if d < best_d:
            best, best_d = name, d
    return best


def head_weights(mesh, arm, used: set[str]) -> int:
    """When UniRig found no neck or head bone, its upper-spine bone carries the head and the head
    can't follow the clips' head motion. Moves the spine weight of vertices above the middle of
    the template's neck to its head bone. Returns how many vertices moved."""
    neck, head = arm.data.bones.get("neck_01"), arm.data.bones.get("Head")
    if neck is None or head is None or used & {"neck_01", "Head"}:
        return 0
    cut_z = (neck.head_local.z + head.head_local.z) / 2
    spine = {b.name for b in neck.parent_recursive}
    groups = {g.index: g.name for g in mesh.vertex_groups}
    target = mesh.vertex_groups.get("Head") or mesh.vertex_groups.new(name="Head")
    moved = 0
    for vertex in mesh.data.vertices:
        if vertex.co.z < cut_z:
            continue
        share = 0.0
        for g in list(vertex.groups):
            if groups.get(g.group) in spine and g.weight > 0:
                share += g.weight
                mesh.vertex_groups[g.group].remove([vertex.index])
        if share > 0:
            target.add([vertex.index], share, "ADD")
            moved += 1
    return moved


def skin(mesh, arm, unirig_glb: str | None, mapping: dict | None) -> tuple[str, int, int]:
    method = "unirig"
    dropped = 0
    if unirig_glb and mapping:
        try:
            dropped = unirig_weights(mesh, arm, unirig_glb, mapping)
            head_weights(mesh, arm, set(mapping.values()))
        except Exception as exc:  # noqa: BLE001 - fall back to Blender's own weights
            print(f"UniRig weights not used: {exc}", flush=True)
            method = "automatic"
    else:
        method = "automatic"
    select_only([mesh, arm], active=arm)
    # ARMATURE_AUTO computes heat weights; ARMATURE keeps the UniRig weights already on the mesh.
    bpy.ops.object.parent_set(type="ARMATURE_AUTO" if method == "automatic" else "ARMATURE")
    if method == "automatic" and unweighted(mesh) > 0.05 * len(mesh.data.vertices):
        method = "automatic (voxel proxy)"
        proxy_automatic_weights(mesh, arm)
    missing = unweighted(mesh)
    if missing:
        nearest_bone_weights(mesh, arm, only_unweighted=True)
    return method, missing, dropped


def bone_hops(arm) -> dict[str, dict[str, int]]:
    """Steps between every two bones in the tree."""
    links: dict[str, set[str]] = {b.name: set() for b in arm.data.bones}
    for bone in arm.data.bones:
        if bone.parent:
            links[bone.name].add(bone.parent.name)
            links[bone.parent.name].add(bone.name)
    hops = {}
    for start in links:
        seen, frontier, step = {start: 0}, [start], 0
        while frontier:
            step += 1
            frontier = [n for f in frontier for n in links[f] if n not in seen]
            for n in frontier:
                seen[n] = step
        hops[start] = seen
    return hops


def separate_touching_limbs(mesh, arm, far: int = 4) -> int:
    """Image-to-3D meshes fuse body parts that touch (hands resting on the thighs). Faces that
    follow bones more than ``far`` steps apart in the tree (a hand and a thigh) are cut apart, and
    each cut vertex keeps only the weights that fit its side, so the parts separate when they
    move instead of stretching a sheet of faces between them. Returns the number of cut edges."""
    hops = bone_hops(arm)
    names = {g.index: g.name for g in mesh.vertex_groups}

    def apart(a: str, b: str) -> bool:
        return a in hops and b in hops and hops[a].get(b, 99) > far

    bm = bmesh.new()
    bm.from_mesh(mesh.data)
    layer = bm.verts.layers.deform.verify()
    dominant = {}
    for face in bm.faces:
        sums: dict[int, float] = {}
        for vert in face.verts:
            for group, weight in vert[layer].items():
                sums[group] = sums.get(group, 0.0) + weight
        dominant[face.index] = names.get(max(sums, key=sums.get)) if sums else None
    cut = [e for e in bm.edges if len(e.link_faces) == 2
           and dominant[e.link_faces[0].index] and dominant[e.link_faces[1].index]
           and apart(dominant[e.link_faces[0].index], dominant[e.link_faces[1].index])]
    if not cut:
        bm.free()
        return 0
    face_bone = {f: dominant[f.index] for f in bm.faces}
    bmesh.ops.split_edges(bm, edges=cut)
    for vert in bm.verts:
        sides = [face_bone.get(f) for f in vert.link_faces if face_bone.get(f)]
        if not sides:
            continue
        weights = vert[layer]
        keep = {g: w for g, w in weights.items()
                if names.get(g) and not any(apart(names[g], side) for side in sides)}
        if not keep:
            index = mesh.vertex_groups[sides[0]].index if sides[0] in mesh.vertex_groups else None
            keep = {index: 1.0} if index is not None else dict(weights.items())
        total = sum(keep.values()) or 1.0
        weights.clear()
        for g, w in keep.items():
            weights[g] = w / total
    bm.to_mesh(mesh.data)
    bm.free()
    mesh.data.update()
    return len(cut)


def rest_rotations(arm) -> dict:
    """Each bone's rest orientation in world space."""
    return {b.name: (arm.matrix_world @ b.matrix_local).to_3x3().normalized().to_quaternion() for b in arm.data.bones}


def rest_directions(arm) -> dict:
    """Each bone's rest direction (head to tail) in world space."""
    return {b.name: (arm.matrix_world.to_3x3() @ (b.tail_local - b.head_local)).normalized() for b in arm.data.bones}


def natural_pose(idle: dict, reference: dict, fitted_dirs: dict) -> dict:
    """The bones whose fitted direction is closer to the idle clip's first frame than to the
    template's A-pose reference, with the idle rotation as their reference. A mesh made with its
    arms hanging (not in the A-pose the prompts ask for) then keeps them where they are in idle,
    instead of lowering them by the clip's A-pose-to-idle turn into its legs."""
    chosen = {}
    for name, (rotation, direction) in idle.items():
        if name in reference and name in fitted_dirs:
            if fitted_dirs[name].angle(direction, 0.0) < fitted_dirs[name].angle(reference[name], 0.0):
                chosen[name] = rotation
    return chosen


def clip_first_pose(clip_file: str, scale: float, offset: Vector) -> dict:
    """World rotation and direction of every bone of ``clip_file``'s armature on its first frame."""
    objects = import_gltf(clip_file)
    names = [o.name for o in objects]
    source = next(o for o in objects if o.type == "ARMATURE")
    source.scale = (scale, scale, scale)
    source.location = offset
    action = source.animation_data.action if source.animation_data else None
    bpy.context.scene.frame_set(int(round(action.frame_range[0])) if action else 0)
    bpy.context.view_layer.update()
    pose = {}
    for bone in source.pose.bones:
        matrix = source.matrix_world @ bone.matrix
        pose[bone.name] = (matrix.to_3x3().normalized().to_quaternion(),
                           (source.matrix_world.to_3x3() @ (bone.tail - bone.head)).normalized())
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
    for block in list(bpy.data.actions):
        if not block.use_fake_user:
            bpy.data.actions.remove(block)
    return pose


def retarget(arm, clip: dict, scale: float, offset: Vector, reference: dict, fitted: dict,
             natural: dict | None = None) -> dict:
    """Bakes ``clip`` onto ``arm``. Each bone turns against its parent as the clip's bone turns
    against its parent away from the template's reference pose (``reference``: bone directions
    before fitting; the clip's rest differs by the template's A-pose, and its bone axes by how
    the importer guessed them), starting from the fitted rest (``fitted``). So the asset keeps
    its bone lengths, stance and arm angle (bulky arms don't swing into the body); the hips also
    move by the clip's travel. ``natural`` replaces the reference rotation of bones whose mesh
    already stands as the idle clip does (see ``natural_pose``)."""
    objects = import_gltf(clip["file"])
    imported = [o.name for o in objects]  # names: removed objects can't be asked for theirs
    source = next(o for o in objects if o.type == "ARMATURE")
    for obj in objects:
        if obj.type == "MESH":
            bpy.data.objects.remove(obj, do_unlink=True)
    source.scale = (scale, scale, scale)
    source.location = offset
    action = source.animation_data.action if source.animation_data else None
    if action is None:
        raise ValueError(f"{clip['file']} has no animation")
    start, end = (int(round(v)) for v in action.frame_range)
    bpy.context.view_layer.update()

    bones = sorted(arm.data.bones, key=lambda b: len(b.parent_recursive))
    rest = {b.name: arm.matrix_world @ b.matrix_local for b in bones}
    driven = {b.name for b in bones if b.name in source.pose.bones}
    movers = {b.name for b in bones if len(b.parent_recursive) <= 1 and b.name in driven}
    src_rest = {n: source.matrix_world @ source.data.bones[n].head_local for n in movers}
    # The clip bone's orientation in the reference pose: its rest, turned onto the reference direction.
    src_dirs = rest_directions(source)
    src_rots = rest_rotations(source)
    src_ref = {n: src_dirs[n].rotation_difference(reference[n]) @ src_rots[n] for n in driven}
    for n, rotation in (natural or {}).items():  # bones whose mesh stands as in idle, not the A-pose
        if n in src_ref:
            src_ref[n] = rotation
    src_parent = {n: (source.data.bones[n].parent.name if source.data.bones[n].parent else None) for n in driven}
    baked = bpy.data.actions.new(clip["name"])
    baked.use_fake_user = True  # kept for the export, which writes every saved action
    arm.animation_data_create()
    arm.animation_data.action = baked
    for pose_bone in arm.pose.bones:
        pose_bone.rotation_mode = "QUATERNION"
    to_arm = arm.matrix_world.inverted()
    for frame in range(start, end + 1):
        bpy.context.scene.frame_set(frame)
        posed: dict[str, Matrix] = {}
        src_pose = {n: (source.matrix_world @ source.pose.bones[n].matrix).to_3x3().normalized().to_quaternion()
                    for n in driven}
        for bone in bones:
            name = bone.name
            parent = bone.parent.name if bone.parent else None
            base = posed[parent] @ rest[parent].inverted() @ rest[name] if parent else rest[name].copy()
            location = base.translation.copy()
            if name in movers:
                ratio = rest[name].translation.z / src_rest[name].z if abs(src_rest[name].z) > 1e-3 else 1.0
                travel = source.matrix_world @ source.pose.bones[name].head - src_rest[name]
                location = rest[name].translation + travel * ratio
            if name in driven:
                # The clip bone's turn against its parent, compared with the reference pose ...
                up = src_parent[name]
                if up in driven:
                    turn = (src_ref[up].inverted() @ src_ref[name]).inverted() @ (src_pose[up].inverted() @ src_pose[name])
                else:
                    turn = src_ref[name].inverted() @ src_pose[name]
                # ... applied to the fitted bone against its own (posed) parent.
                if parent:
                    above = posed[parent].to_3x3().normalized().to_quaternion()
                    rotation = above @ (fitted[parent].inverted() @ fitted[name]) @ turn
                else:
                    rotation = fitted[name] @ turn
                wanted = Matrix.LocRotScale(location, rotation, None)
            else:
                wanted = base.copy()
                wanted.translation = location
            posed[name] = wanted
            pose_bone = arm.pose.bones[name]
            pose_bone.matrix_basis = (to_arm @ base).inverted() @ (to_arm @ wanted)
            pose_bone.keyframe_insert("rotation_quaternion", frame=frame)
            pose_bone.keyframe_insert("location", frame=frame)
    arm.animation_data.action = None

    for name in imported:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
    for block in list(bpy.data.actions):  # the clip's own action, which holds the clip's name
        if not block.use_fake_user:
            bpy.data.actions.remove(block)
    baked.name = clip["name"]
    for pose_bone in arm.pose.bones:
        pose_bone.matrix_basis.identity()
    return {"name": clip["name"], "duration": round((end - start) / FPS, 4), "loop": bool(clip.get("loop"))}


def main(args: dict) -> dict:
    if args.get("attach"):  # gear or a weapon on the base body
        import attach  # noqa: PLC0415 - it imports this module
        return attach.main(args)
    progress(0.02, "Loading the mesh and the template skeleton")
    empty_scene()
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = FPS, 1.0
    mesh = load_lod0(args["mesh_glb"])
    arm = append_template(args["template_blend"], args["template_object"])
    template = json.loads(Path(args["template_json"]).read_text(encoding="utf-8"))
    scale, offset = fit_template(arm, mesh, template, args.get("fit_axis", "longest"))
    reference = rest_directions(arm)  # the pose the clips' rotations are measured from
    placed = pose_to_targets(arm, args.get("targets") or {})
    fitted = rest_rotations(arm)

    progress(0.1, "Skinning the mesh")
    method, missing, dropped = skin(mesh, arm, args.get("unirig_glb"), args.get("mapping"))
    cut = separate_touching_limbs(mesh, arm)

    idle = next((c for c in args["clips"] if c["name"] == "idle"), None)
    natural = natural_pose(clip_first_pose(idle["file"], scale, offset), reference, rest_directions(arm)) if idle else {}

    clips = []
    for index, clip in enumerate(args["clips"]):
        progress(0.2 + 0.7 * index / max(len(args["clips"]), 1), f"Retargeting {clip['name']}")
        clips.append(retarget(arm, clip, scale, offset, reference, fitted, natural))

    progress(0.92, "Writing the animated GLB")
    out = Path(args["out_glb"])
    out.parent.mkdir(parents=True, exist_ok=True)
    select_only([mesh, arm], active=arm)
    bpy.ops.export_scene.gltf(filepath=str(out), export_format="GLB", use_selection=True, export_yup=True,
                              export_apply=False, export_animations=True, export_animation_mode="ACTIONS",
                              export_force_sampling=True, export_skins=True, export_def_bones=False,
                              export_materials="EXPORT")
    progress(1.0, "Animated result ready")
    joints = {b.name: {"head": [round(v, 5) for v in arm.matrix_world @ b.head_local],
                       "parent": b.parent.name if b.parent else None}
              for b in arm.data.bones if b.use_deform}
    return {"clips": clips, "joints": joints, "scale": round(scale, 4), "skinning": method, "unweighted_before_fix": missing, "cross_limb_weights_dropped": dropped, "touching_limb_edges_cut": cut, "idle_reference_bones": sorted(natural),
            "joints_placed": placed}


if __name__ == "__main__":
    run(main)
