"""Adds clips (skill animations) to a rigged character without rigging it again.

Arguments (args.json)::

    {"blend": ".../<hero>/rig/rigged.blend", "rig_args": ".../<hero>/rig/rig_animate.args.json",
     "clips": [{"name": "skill_firebolt", "file": ".../skill_firebolt.glb", "loop": false}],
     "out_glb": ".../skill_firebolt.glb", "save_blend": true, "keep_all": false}

The hero's rig stage kept its scene (``rigged.blend``) and its arguments. This opens the scene,
measures the template again the way the rig stage did (a fresh copy fitted on the mesh gives the
clip's scale, place and reference pose; the hero's own bones are the fitted rest), and retargets
each clip with ``rig_animate.retarget``, so it plays exactly as it would after a new rig.
``save_blend`` keeps the new clips in ``rigged.blend`` (weapons and gear fitted on the hero from
then on can play them). The GLB has the hero with ``idle`` and the new clips, or every clip it
has with ``keep_all`` (its new ``animated.glb``).

Result: ``{"clips": [{"name", "duration", "loop"}]}``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy  # noqa: E402

from _common import progress, run, select_only  # noqa: E402
from rig_animate import (append_template, clip_first_pose, fit_template, natural_pose, rest_directions,  # noqa: E402
                         rest_rotations, retarget)


def main(args: dict) -> dict:
    progress(0.02, "Opening the rigged hero")
    bpy.ops.wm.open_mainfile(filepath=args["blend"])
    rig = json.loads(Path(args["rig_args"]).read_text(encoding="utf-8"))
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    mesh = bpy.data.objects.get("mesh") or next(o for o in bpy.data.objects if o.type == "MESH")
    for action in bpy.data.actions:  # the baked clips stay through the cleanups of retarget
        action.use_fake_user = True

    # The template as the rig stage first fitted it: the clips' scale, place and reference pose.
    names = set(bpy.data.objects.keys())
    template = append_template(rig["template_blend"], rig["template_object"])
    if template.name in names or template == arm:  # the same name: the appended copy is renamed
        template = next(o for o in bpy.data.objects if o.name not in names and o.type == "ARMATURE")
    measures = json.loads(Path(rig["template_json"]).read_text(encoding="utf-8"))
    scale, offset = fit_template(template, mesh, measures, rig.get("fit_axis", "longest"))
    reference = rest_directions(template)
    bpy.data.objects.remove(template, do_unlink=True)
    fitted = rest_rotations(arm)
    idle = next((c for c in rig["clips"] if c["name"] == "idle"), None)
    natural = natural_pose(clip_first_pose(idle["file"], scale, offset), reference, rest_directions(arm)) if idle else {}

    made = []
    for index, clip in enumerate(args["clips"]):
        progress(0.1 + 0.75 * index / max(len(args["clips"]), 1), f"Retargeting {clip['name']}")
        old = bpy.data.actions.get(clip["name"])
        if old is not None:  # made again: the new one replaces it
            bpy.data.actions.remove(old)
        result = retarget(arm, clip, scale, offset, reference, fitted, natural, intended=set(rig.get("twists") or {}))
        made.append({k: result[k] for k in ("name", "duration", "loop")})
    if arm.animation_data:
        arm.animation_data.action = None
    if args.get("save_blend"):
        bpy.ops.wm.save_as_mainfile(filepath=args["blend"], compress=True)

    progress(0.9, "Writing the GLB")
    keep = {"idle", *(c["name"] for c in made)}
    for action in list(bpy.data.actions):
        if not args.get("keep_all") and action.name not in keep:
            bpy.data.actions.remove(action)
    out = Path(args["out_glb"])
    out.parent.mkdir(parents=True, exist_ok=True)
    select_only([mesh, arm], active=arm)
    bpy.ops.export_scene.gltf(filepath=str(out), export_format="GLB", use_selection=True, export_yup=True,
                              export_apply=False, export_animations=True, export_animation_mode="ACTIONS",
                              export_force_sampling=True, export_skins=True, export_def_bones=False,
                              export_materials="EXPORT")
    progress(1.0, "Clips added")
    return {"clips": made}


if __name__ == "__main__":
    run(main)
