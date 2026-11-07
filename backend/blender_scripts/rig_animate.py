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
   template joints, copied vertex by vertex from the nearest UniRig vertex) when given; else
   Blender's automatic weights; vertices either leaves unweighted go to their nearest bone.
3. For every clip: import the clip's armature at the same scale and place, copy each bone's
   world transform onto the template bone (Copy Transforms, world space), bake that into a new
   action named after the clip. Copying world transforms makes clips made on a different rest
   pose (the biped's clips have a T-pose rest, its template an A-pose) play correctly.
4. Export one GLB with the skinned mesh, the armature and one animation per clip.

Result: ``{"clips": [{"name", "duration", "loop"}], "scale", "skinning", "unweighted_before_fix"}``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
        children = [c for c in pose_bone.children if c.name in targets]
        if not children:
            continue
        tail = pose_bone.tail.copy()
        child = min(children, key=lambda c: (c.head - tail).length)  # the one the bone points at
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
    segments = [(b.name, b.head_local.copy(), b.tail_local.copy()) for b in arm.data.bones]
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


def unirig_weights(mesh, unirig_glb: str, mapping: dict) -> None:
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
    groups: dict[str, object] = {}
    for vertex in mesh.data.vertices:
        _, index, _ = tree.find(vertex.co)
        row = weights[index]
        total = sum(row.values())
        for joint, w in row.items():
            group = groups.get(joint) or mesh.vertex_groups.get(joint) or mesh.vertex_groups.new(name=joint)
            groups[joint] = group
            group.add([vertex.index], w / total, "REPLACE")


def skin(mesh, arm, unirig_glb: str | None, mapping: dict | None) -> tuple[str, int]:
    method = "unirig"
    if unirig_glb and mapping:
        try:
            unirig_weights(mesh, unirig_glb, mapping)
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
    return method, missing


def retarget(arm, clip: dict, scale: float, offset: Vector) -> dict:
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

    for bone in arm.pose.bones:
        for constraint in list(bone.constraints):
            bone.constraints.remove(constraint)
        if bone.name in source.pose.bones:
            constraint = bone.constraints.new("COPY_TRANSFORMS")
            constraint.target, constraint.subtarget = source, bone.name
            constraint.owner_space = constraint.target_space = "WORLD"

    select_only([arm])
    bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.pose.select_all(action="SELECT")
    bpy.ops.nla.bake(frame_start=start, frame_end=end, step=1, only_selected=False, visual_keying=True,
                     clear_constraints=False, clear_parents=False, use_current_action=False, bake_types={"POSE"})
    bpy.ops.object.mode_set(mode="OBJECT")
    baked = arm.animation_data.action
    baked.use_fake_user = True  # kept for the export, which writes every saved action
    arm.animation_data.action = None

    for bone in arm.pose.bones:
        for constraint in list(bone.constraints):
            bone.constraints.remove(constraint)
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
    if args.get("attach"):
        raise RuntimeError("Fitting gear and weapons on the base body is ticket #12; they can't be rigged yet.")
    progress(0.02, "Loading the mesh and the template skeleton")
    empty_scene()
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = FPS, 1.0
    mesh = load_lod0(args["mesh_glb"])
    arm = append_template(args["template_blend"], args["template_object"])
    template = json.loads(Path(args["template_json"]).read_text(encoding="utf-8"))
    scale, offset = fit_template(arm, mesh, template, args.get("fit_axis", "longest"))
    placed = pose_to_targets(arm, args.get("targets") or {})

    progress(0.1, "Skinning the mesh")
    method, missing = skin(mesh, arm, args.get("unirig_glb"), args.get("mapping"))

    clips = []
    for index, clip in enumerate(args["clips"]):
        progress(0.2 + 0.7 * index / max(len(args["clips"]), 1), f"Retargeting {clip['name']}")
        clips.append(retarget(arm, clip, scale, offset))

    progress(0.92, "Writing the animated GLB")
    out = Path(args["out_glb"])
    out.parent.mkdir(parents=True, exist_ok=True)
    select_only([mesh, arm], active=arm)
    bpy.ops.export_scene.gltf(filepath=str(out), export_format="GLB", use_selection=True, export_yup=True,
                              export_apply=False, export_animations=True, export_animation_mode="ACTIONS",
                              export_force_sampling=True, export_skins=True, export_def_bones=False,
                              export_materials="EXPORT")
    progress(1.0, "Animated result ready")
    return {"clips": clips, "scale": round(scale, 4), "skinning": method, "unweighted_before_fix": missing,
            "joints_placed": placed}


if __name__ == "__main__":
    run(main)
