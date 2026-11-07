"""Unity export: the animated GLB (gate 3) and the LODs (gate 2) as one FBX.

Arguments (args.json)::

    {"animated_glb": "animated.glb", "mesh_glb": "mesh.glb", "out_fbx": "Name.fbx",
     "name": "Name", "lod_levels": [1, 2], "rigid": false}

``rigid`` (weapons): the item is written alone in its own model space, without the armature,
the preview mannequin or clips (the weapon is parented to a socket bone in the preview).

The animated mesh becomes ``<name>_LOD0``; the gate-2 meshes ``LOD1``,
``LOD2``... become ``<name>_LOD1``... beside it under the same parent, which
is what Unity's model importer turns into a LOD Group. When LOD0 is skinned,
the other levels get its weights and the same armature. Every clip is an NLA
strip of its own, so each is written as its own take (animation clip).

FBX settings for Unity: Y up, -Z forward, scale 1 (``FBX_SCALE_ALL``), no
leaf bones, every bone (the weapon sockets don't deform but must be there), baked animation.

Result: ``{"meshes": [{"name", "triangles", "skinned"}], "clips": [names]}``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy  # noqa: E402

from _common import empty_scene, import_gltf, progress, run, select_only, triangles  # noqa: E402

# Objects of the gate-3 preview that are not part of the asset.
PREVIEW_ONLY = re.compile(r"^mannequin", re.I)


def top(obj):
    while obj.parent is not None:
        obj = obj.parent
    return obj


def armature_of(obj):
    for modifier in obj.modifiers:
        if modifier.type == "ARMATURE" and modifier.object is not None:
            return modifier.object
    return None


def alive(obj) -> bool:
    try:
        return obj.name in bpy.data.objects
    except ReferenceError:  # removed
        return False


def drop_extras(objects) -> None:
    """Removes the preview mannequin and the bone display shapes the glTF importer adds."""
    shapes = {bone.custom_shape for obj in objects if obj.type == "ARMATURE" for bone in obj.pose.bones}
    for obj in [o for o in objects if o in shapes or (o.type == "MESH" and PREVIEW_ONLY.match(o.name))]:
        bpy.data.objects.remove(obj, do_unlink=True)


def make_rigid(objects) -> None:
    """Puts the meshes back in their own model space and removes the armatures."""
    for obj in objects:
        if alive(obj) and obj.type == "MESH":
            obj.parent = None
            obj.matrix_world.identity()
            for modifier in list(obj.modifiers):
                if modifier.type == "ARMATURE":
                    obj.modifiers.remove(modifier)
    for obj in objects:
        if alive(obj) and obj.type == "ARMATURE":
            bpy.data.objects.remove(obj, do_unlink=True)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)


def lod0_from(objects, name: str):
    drop_extras(objects)
    meshes = [o for o in objects if alive(o) and o.type == "MESH"]
    if not meshes:
        raise ValueError("the animated GLB has no mesh")
    if len(meshes) > 1:
        select_only(meshes)
        bpy.ops.object.join()
    lod0 = meshes[0] if len(meshes) == 1 else bpy.context.view_layer.objects.active
    lod0.name = lod0.data.name = f"{name}_LOD0"
    if lod0.parent is None:  # Unity groups LODs that share a parent
        root = bpy.data.objects.new(name, None)
        lod0.users_collection[0].objects.link(root)
        lod0.parent = root
    return lod0


def skin_like(target, source, armature) -> None:
    """Copies the vertex weights of ``source`` onto ``target`` and binds it to ``armature``."""
    select_only([target])
    transfer = target.modifiers.new("Weights", "DATA_TRANSFER")
    transfer.object = source
    transfer.use_vert_data = True
    transfer.data_types_verts = {"VGROUP_WEIGHTS"}
    transfer.vert_mapping = "POLYINTERP_NEAREST"
    transfer.layers_vgroup_select_src = "ALL"
    transfer.layers_vgroup_select_dst = "NAME"
    bpy.ops.object.datalayout_transfer(modifier=transfer.name)
    bpy.ops.object.modifier_apply(modifier=transfer.name)
    deform = target.modifiers.new("Armature", "ARMATURE")
    deform.object = armature


def add_lods(mesh_glb: str, lod0, name: str, levels: list[int]) -> list:
    added = import_gltf(mesh_glb)
    drop_extras(added)
    added = [obj for obj in added if alive(obj)]
    by_name = {obj.name: obj for obj in added}
    armature = armature_of(lod0)
    lods = []
    for level in levels:
        obj = by_name.get(f"LOD{level}")
        if obj is None or obj.type != "MESH":
            raise ValueError(f"the gate-2 mesh has no LOD{level}")
        world = obj.matrix_world.copy()
        obj.parent = lod0.parent
        obj.matrix_world = world
        obj.name = obj.data.name = f"{name}_LOD{level}"
        if armature is not None:
            skin_like(obj, lod0, armature)
        lods.append(obj)
    for obj in added:
        if obj not in lods:
            bpy.data.objects.remove(obj, do_unlink=True)
    return lods


def clips_to_nla(objects) -> list[str]:
    """Puts every clip on an NLA strip of its own and clears the active actions,
    so the FBX exporter writes one take per strip."""
    clips: list[str] = []
    end = 1
    for obj in objects:
        data = obj.animation_data
        if data is None:
            continue
        stripped = {strip.action for track in data.nla_tracks for strip in track.strips}
        if data.action is not None and data.action not in stripped:
            track = data.nla_tracks.new()
            track.name = data.action.name
            track.strips.new(data.action.name, int(data.action.frame_range[0]), data.action)
        data.action = None
        for track in data.nla_tracks:
            track.mute = False
            for strip in track.strips:
                strip.mute = False
                end = max(end, int(strip.frame_end))
                if strip.name not in clips:
                    clips.append(strip.name)
    bpy.context.scene.frame_start = 0
    bpy.context.scene.frame_end = end
    return clips


def main(args: dict) -> dict:
    name = args["name"]
    progress(0.05, "Importing the animated mesh")
    empty_scene()
    animated = import_gltf(args["animated_glb"])
    if args.get("rigid"):
        drop_extras(animated)
        make_rigid(animated)
        animated = [obj for obj in animated if alive(obj)]
    lod0 = lod0_from(animated, name)

    progress(0.3, "Adding the LODs")
    lods = add_lods(args["mesh_glb"], lod0, name, [int(level) for level in args.get("lod_levels", [])])

    progress(0.5, "Laying out the clips")
    clips = clips_to_nla(list(bpy.data.objects))

    progress(0.6, "Writing the FBX")
    out = Path(args["out_fbx"])
    out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=str(out), use_selection=False, object_types={"EMPTY", "ARMATURE", "MESH"},
        global_scale=1.0, apply_unit_scale=True, apply_scale_options="FBX_SCALE_ALL",
        axis_forward="-Z", axis_up="Y", use_mesh_modifiers=True, mesh_smooth_type="FACE",
        add_leaf_bones=False, primary_bone_axis="Y", secondary_bone_axis="X", use_armature_deform_only=False,
        bake_anim=True, bake_anim_use_all_bones=True, bake_anim_use_nla_strips=True,
        bake_anim_use_all_actions=False, bake_anim_force_startend_keying=True, bake_anim_step=1.0,
        bake_anim_simplify_factor=0.0, path_mode="AUTO")
    progress(1.0, "FBX written")
    meshes = [lod0, *lods]
    return {"meshes": [{"name": m.name, "triangles": triangles(m), "skinned": armature_of(m) is not None}
                       for m in meshes],
            "clips": clips}


if __name__ == "__main__":
    run(main)
