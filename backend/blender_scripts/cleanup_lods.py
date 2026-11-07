"""Gate 2 mesh: clean up the raw Hunyuan3D mesh, decimate it to the triangle
budget and make the LODs.

Arguments (args.json)::

    {"raw_mesh": "raw.glb", "out_glb": "mesh.glb", "triangles": 8000,
     "lod_ratios": [0.5, 0.2], "height": 1.8, "fit": "height" | "longest",
     "remove_floaters": true}

Steps: import, join, merge by distance, remove loose parts, floaters (with
``remove_floaters``: small pieces that do not touch the main body, which
image-to-3D models leave around it) and interior faces, fix normals, apply transforms, scale to ``height`` (along Z, or along
the longest side for ``fit = "longest"``), put the origin at the feet,
decimate to ``triangles``, then copy and decimate the LODs to ``lod_ratios``
of LOD0. The GLB holds one node per level, named ``LOD0``, ``LOD1``...

Result: ``{"lods": [{"level", "ratio", "triangles"}], "removed": {...}}``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402
from mathutils.kdtree import KDTree  # noqa: E402

from _common import empty_scene, import_gltf, progress, run, select_only, triangles  # noqa: E402

# Islands smaller than both of these are floating debris and are removed.
DEBRIS_FACE_SHARE = 0.005
DEBRIS_SIZE_SHARE = 0.05
# Floaters: pieces under this share of the faces whose nearest point is farther than
# FLOATER_GAP_SHARE of the model's size from the main body. Pieces that touch the body
# (separate armour plates, a sword in the hand) are kept.
FLOATER_FACE_SHARE = 0.05
FLOATER_GAP_SHARE = 0.01
FLOATER_SAMPLES = 400  # vertices of a piece checked against the body


def join_meshes(objects) -> bpy.types.Object:
    meshes = [obj for obj in objects if obj.type == "MESH"]
    if not meshes:
        raise ValueError("the raw mesh has no mesh objects")
    for obj in meshes:  # keep the world transform, drop the importer's parents
        matrix = obj.matrix_world.copy()
        obj.parent = None
        obj.matrix_world = matrix
    for obj in objects:
        if obj.type != "MESH":
            bpy.data.objects.remove(obj, do_unlink=True)
    select_only(meshes)
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return obj


def diagonal(points) -> float:
    if not points:
        return 0.0
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return (hi - lo).length


def islands(bm) -> list[list]:
    """Groups of faces connected through shared edges."""
    seen: set = set()
    groups = []
    for face in bm.faces:
        if face.index in seen:
            continue
        stack, group = [face], []
        seen.add(face.index)
        while stack:
            current = stack.pop()
            group.append(current)
            for edge in current.edges:
                for other in edge.link_faces:
                    if other.index not in seen:
                        seen.add(other.index)
                        stack.append(other)
        groups.append(group)
    return groups


def body_tree(group) -> KDTree:
    verts = {v for f in group for v in f.verts}
    tree = KDTree(len(verts))
    for i, v in enumerate(verts):
        tree.insert(v.co, i)
    tree.balance()
    return tree


def gap_to(tree: KDTree, group) -> float:
    """The smallest distance from a sample of the piece's vertices to the body."""
    verts = list({v for f in group for v in f.verts})
    step = max(1, len(verts) // FLOATER_SAMPLES)
    return min(tree.find(v.co)[2] for v in verts[::step])


def clean(obj, remove_floaters: bool = True) -> dict:
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    size = diagonal([v.co for v in bm.verts]) or 1.0
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=max(size * 1e-4, 1e-6))
    merged = before - len(bm.verts)

    loose = [v for v in bm.verts if not v.link_faces]
    loose_edges = [e for e in bm.edges if not e.link_faces]
    bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")
    bmesh.ops.delete(bm, geom=[v for v in loose if v.is_valid], context="VERTS")

    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    groups = islands(bm)
    total = len(bm.faces)
    debris, floaters, floater_pieces = [], [], 0
    if len(groups) > 1:
        largest = max(groups, key=len)
        tree = body_tree(largest) if remove_floaters else None
        for group in groups:
            if group is largest:
                continue
            points = [v.co for f in group for v in f.verts]
            if len(group) < total * DEBRIS_FACE_SHARE and diagonal(points) < size * DEBRIS_SIZE_SHARE:
                debris.extend(group)
            elif tree is not None and len(group) < total * FLOATER_FACE_SHARE \
                    and gap_to(tree, group) > size * FLOATER_GAP_SHARE:
                floaters.extend(group)
                floater_pieces += 1
    bmesh.ops.delete(bm, geom=debris + floaters, context="FACES")
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()

    # Interior faces: faces whose every edge is shared by more than two faces.
    select_only([obj])
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type="FACE")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_interior_faces()
    bpy.ops.object.mode_set(mode="OBJECT")
    interior = sum(1 for poly in mesh.polygons if poly.select)
    if interior:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.delete(type="FACE")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.normals_make_consistent(inside=False)
        bpy.ops.object.mode_set(mode="OBJECT")
    if mesh.has_custom_normals:
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    for poly in mesh.polygons:
        poly.use_smooth = True
    mesh.update()
    return {"merged_vertices": merged, "loose_vertices": len(loose), "debris_faces": len(debris),
            "floater_faces": len(floaters), "floater_pieces": floater_pieces, "interior_faces": interior}


def fit(obj, height: float, mode: str) -> float:
    """Scales to the standard size and puts the origin at the feet (centre of the base)."""
    points = [v.co for v in obj.data.vertices]
    if not points:
        raise ValueError("nothing is left of the mesh after clean-up")
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    dims = hi - lo
    current = max(dims) if mode == "longest" else dims.z
    scale = height / current if current > 1e-9 else 1.0
    feet = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    obj.data.transform(Matrix.Scale(scale, 4) @ Matrix.Translation(-feet))
    obj.data.update()
    obj.matrix_world = Matrix.Identity(4)
    return scale


def decimate(obj, target: int) -> int:
    """Collapses ``obj`` to at most ``target`` triangles (a few passes, as collapse is approximate)."""
    target = max(int(target), 4)
    select_only([obj])
    for _ in range(4):
        count = triangles(obj)
        if count <= target:
            break
        modifier = obj.modifiers.new("Decimate", "DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = max(min(target / count, 1.0) * 0.995, 1e-4)
        modifier.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    return triangles(obj)


def copy_object(obj, name: str):
    copy = obj.copy()
    copy.data = obj.data.copy()
    copy.name = copy.data.name = name
    for collection in obj.users_collection:
        collection.objects.link(copy)
    return copy


def mesh_stats(obj) -> dict:
    """What gate 2's checks read: separate pieces (vertices welded first, so UV seams don't count)
    and open edges (border of a hole)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    open_edges = sum(1 for e in bm.edges if len(e.link_faces) == 1)
    seen, pieces, sizes = set(), 0, []
    for start in bm.verts:
        if start.index in seen:
            continue
        pieces += 1
        stack, size = [start], 0
        seen.add(start.index)
        while stack:
            vert = stack.pop()
            size += 1
            for edge in vert.link_edges:
                other = edge.other_vert(vert)
                if other.index not in seen:
                    seen.add(other.index)
                    stack.append(other)
        sizes.append(size)
    total = max(len(bm.verts), 1)
    bm.free()
    return {"pieces": pieces, "largest_piece_share": round(max(sizes or [0]) / total, 3),
            "open_edges": open_edges, "edges": len(obj.data.edges)}


def main(args: dict) -> dict:
    progress(0.02, "Importing the raw mesh")
    empty_scene()
    obj = join_meshes(import_gltf(args["raw_mesh"]))
    raw_triangles = triangles(obj)

    progress(0.15, "Cleaning up the mesh")
    removed = clean(obj, bool(args.get("remove_floaters", True)))

    progress(0.35, "Scaling and placing the origin")
    scale = fit(obj, float(args["height"]), args.get("fit", "height"))

    budget = int(args["triangles"])
    progress(0.45, f"Decimating LOD0 to {budget:,} triangles")
    obj.name = obj.data.name = "LOD0"
    lods = [{"level": 0, "ratio": 1.0, "triangles": decimate(obj, budget)}]
    if lods[0]["triangles"] > budget:
        raise RuntimeError(f"could not decimate LOD0 to {budget} triangles (got {lods[0]['triangles']})")

    ratios = [float(r) for r in args.get("lod_ratios", [])]
    for level, ratio in enumerate(ratios, start=1):
        progress(0.6 + 0.3 * (level - 1) / max(len(ratios), 1), f"Making LOD{level} ({round(ratio * 100)}%)")
        lod = copy_object(obj, f"LOD{level}")
        lods.append({"level": level, "ratio": ratio,
                     "triangles": decimate(lod, round(lods[0]["triangles"] * ratio))})

    stats = mesh_stats(obj)
    progress(0.92, "Writing the GLB")
    out = Path(args["out_glb"])
    out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=str(out), export_format="GLB", use_selection=False, export_yup=True,
                              export_apply=True, export_animations=False)
    progress(1.0, "LODs ready")
    return {"lods": lods, "raw_triangles": raw_triangles, "removed": removed, "scale": scale, "stats": stats}


if __name__ == "__main__":
    run(main)
