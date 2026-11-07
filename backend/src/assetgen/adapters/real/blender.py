"""Headless Blender adapter: mesh clean-up with LODs (gate 2), the Godot sprite
frames and the Unity FBX.

The work happens in the scripts in ``backend/blender_scripts/``, run by
``blender_runner``. ``rig_and_animate`` skins the mesh to the archetype's template
skeleton and retargets its clips; gear and weapons are fitted on the biped base body
(``blender_scripts/attach.py``).
"""

from __future__ import annotations

import json
import shutil
import struct
from pathlib import Path

from ...config import Settings
from ...defaults import CAMERA_TILT, DIRECTION_YAW, MODEL_SIZES
from ...imaging import finish_sprites, read_glb
from .blender_runner import BlenderError, run_script

from ...defaults import FIT_BY_HEIGHT as UPRIGHT  # noqa: E402


def merge_glb_extras(source: Path, target: Path, extra: dict | None = None) -> None:
    """Copies the root ``extras`` of ``source`` (plus ``extra``) into ``target``.

    Blender keeps no root extras, and the later stages read the asset's hints
    (such as the triangle budget) from them."""
    try:
        extras = dict(read_glb(source)[0].get("extras") or {})
    except (OSError, ValueError):
        extras = {}
    extras.update(extra or {})
    if not extras:
        return
    data = target.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + length])
    gltf["extras"] = {**extras, **(gltf.get("extras") or {})}
    payload = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    rest = data[20 + length:]
    body = struct.pack("<I4s", len(payload), b"JSON") + payload + rest
    target.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


class BlenderTools:

    def __init__(self, settings: Settings):
        self.blender = settings.blender_path

    def _run(self, script: str, args: dict, out_dir: Path, progress) -> dict:
        return run_script(self.blender, script, args, work_dir=out_dir,
                          log_path=out_dir / f"blender_{Path(script).stem}.log", progress=progress)

    def cleanup_and_lods(self, *, raw_mesh, kind, triangles, lod_ratios, out_dir, progress, remove_floaters=True):
        out_dir = Path(out_dir)
        glb = out_dir / "mesh.glb"
        size, fit = MODEL_SIZES.get(kind, MODEL_SIZES["hero"])
        result = self._run("cleanup_lods.py", {
            "raw_mesh": str(Path(raw_mesh).resolve()), "out_glb": str(glb.resolve()), "triangles": int(triangles),
            "lod_ratios": [float(r) for r in lod_ratios], "height": size, "fit": fit,
            "remove_floaters": bool(remove_floaters)}, out_dir, progress)
        merge_glb_extras(Path(raw_mesh), glb, {"triangle_budget": int(triangles)})
        return {"glb": glb, "lods": result["lods"], "removed": result.get("removed")}

    def rig_and_animate(self, *, mesh_glb, skeleton, archetype, clips, attach, out_path, progress):
        """Skins the mesh to the archetype's template skeleton and retargets its clips
        (``blender_scripts/rig_animate.py``). ``skeleton`` is the rig adapter's result; its
        UniRig weights are used unless the fit fell back."""
        from ...archetypes import ARCHETYPES_DIR  # noqa: PLC0415 - keep this module's imports light

        out_path = Path(out_path)
        folder = ARCHETYPES_DIR / archetype["folder"]
        record = json.loads((folder / "clips.json").read_text(encoding="utf-8"))
        missing = [c for c in clips if not (folder / "clips" / f"{c}.glb").is_file()]
        if missing:
            raise BlenderError(f"The {archetype['id']} archetype has no clip for: {', '.join(missing)}. Rebuild "
                               "the templates with blender_scripts/build_archetypes.py.")
        clip_args = [{"name": c, "file": str((folder / "clips" / f"{c}.glb").resolve()),
                      "loop": bool(record.get(c, {}).get("loop"))} for c in clips]
        if attach:
            body = folder / "base_body.blend"
            if not body.is_file():
                raise BlenderError(f"{body} is missing; rebuild the biped template with "
                                   "blender_scripts/build_archetypes.py.")
            result = self._run("rig_animate.py", {
                "mesh_glb": str(Path(mesh_glb).resolve()), "base_body_blend": str(body.resolve()),
                "template_object": f"{archetype['id']}_template", "clips": clip_args, "attach": attach,
                "out_glb": str(out_path.resolve()), "mask_json": str((out_path.parent / "body_mask.json").resolve())},
                out_path.parent, progress)
            return {"glb": out_path, "clips": result["clips"],
                    **{k: result[k] for k in ("gear", "weapon") if k in result}}
        use_unirig = bool(skeleton.get("unirig_file")) and not skeleton.get("fallback")
        result = self._run("rig_animate.py", {
            "mesh_glb": str(Path(mesh_glb).resolve()),
            "template_blend": str((folder / "skeleton.blend").resolve()),
            "template_object": f"{archetype['id']}_template",
            "template_json": str((folder / "skeleton.json").resolve()),
            "fit_axis": "z" if archetype["id"] in UPRIGHT else "longest",
            "clips": clip_args,
            "unirig_glb": skeleton.get("unirig_file") if use_unirig else None,
            "mapping": skeleton.get("mapping") if use_unirig else None,
            "targets": skeleton.get("targets") if use_unirig else None,
            "out_glb": str(out_path.resolve())}, out_path.parent, progress)
        return {"glb": out_path, "clips": result["clips"], "skinning": result["skinning"]}

    def render_sprites(self, *, animated_glb, clips, directions, camera_preset, frame_size, supersample, fps,
                       look, palette_size, out_dir, progress, framing=None):
        out_dir = Path(out_dir)
        record = out_dir / "sprites.json"
        settings = {"animated_glb": str(Path(animated_glb).resolve()), "clips": clips, "directions": directions,
                    "camera_preset": camera_preset, "frame_size": int(frame_size), "supersample": int(supersample),
                    "fps": int(fps), "look": look, "palette_size": int(palette_size), "framing": framing}
        done = _read_record(record, settings)
        if done is not None:  # already rendered with these settings (resume after a restart)
            progress("Sprites rendered", 1.0)
            return done

        raw_dir = out_dir / "raw"
        shutil.rmtree(raw_dir, ignore_errors=True)
        result = self._run("render_sprites.py", {
            "animated_glb": settings["animated_glb"], "out_dir": str(raw_dir.resolve()),
            "clips": [{"name": c["name"], "duration": float(c["duration"])} for c in clips],
            "directions": [{"name": d, "yaw": DIRECTION_YAW[d]} for d in directions],
            "tilt": CAMERA_TILT[camera_preset], "size": int(frame_size) * int(supersample),
            "frame_size": int(frame_size), "fps": int(fps), "outline": 1.0, "framing": framing},
            out_dir, lambda step, fraction: progress(step, 0.9 * fraction))
        progress("Downscaling the frames", 0.92)
        raw = {d: {c: [Path(p) for p in paths] for c, paths in by_clip.items()}
               for d, by_clip in result["frames"].items()}
        frames = finish_sprites(raw, {c["name"]: bool(c.get("loop", True)) for c in clips}, out_dir,
                                frame_size=int(frame_size), look=look, palette_size=int(palette_size))
        shutil.rmtree(raw_dir, ignore_errors=True)
        record.write_text(json.dumps({"settings": settings, "frames": {
            d: {c: [p.relative_to(out_dir).as_posix() for p in paths] for c, paths in by_clip.items()}
            for d, by_clip in frames.items()}}, indent=2), encoding="utf-8")
        progress("Sprites rendered", 1.0)
        return frames

    def export_fbx(self, *, animated_glb, mesh_glb, lods, rig_type, out_path, progress, kind="hero"):
        out_path = Path(out_path)
        self._run("export_fbx.py", {
            "rigid": kind == "weapon",
            "animated_glb": str(Path(animated_glb).resolve()), "mesh_glb": str(Path(mesh_glb).resolve()),
            "out_fbx": str(out_path.resolve()), "name": out_path.stem,
            "lod_levels": [int(lod["level"]) for lod in lods if int(lod["level"]) > 0]},
            out_path.parent, progress)
        return out_path


def _read_record(record: Path, settings: dict) -> dict[str, dict[str, list[Path]]] | None:
    """The frames of an earlier render with the same settings, when all its files are still there."""
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("settings") != settings:
        return None
    frames = {d: {c: [record.parent / p for p in paths] for c, paths in by_clip.items()}
              for d, by_clip in data.get("frames", {}).items()}
    if all(p.is_file() for by_clip in frames.values() for paths in by_clip.values() for p in paths):
        return frames
    return None
