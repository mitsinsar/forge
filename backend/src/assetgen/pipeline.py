"""The asset pipeline: stage work, gates and the single job worker.

An asset made from the user's own image (``source == "upload"``) skips the
image model: the prompt stage only settles the archetype (asking the LLM when
the user left it automatic) and offers that image as the single candidate.

Stages run in order: prompt -> (gate 1) -> mesh -> (gate 2) -> rig -> (gate 3)
-> export -> done. Each stage writes into data/assets/<id>/<stage>/ and ends by
writing manifest.json; a stage whose manifest exists is not run again, and the
adapters skip files that already exist, so an interrupted stage resumes where
it stopped. Rejecting at a gate clears only that stage (and anything after it).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import traceback
from pathlib import Path

from .adapters import Adapters
from .archetypes import get_archetype, load_archetypes
from .config import Settings
from .db import Database, now
from .defaults import (CAMERA_DIRECTIONS, CAMERA_MIRRORED, DEFAULT_GRIPS, GATE_OF_STAGE, PROFILE_SETTING_KEYS,
                       STAGE_OF_GATE, STAGES, WEAPON_ATTACKS, deep_merge, profile_defaults, seed_of)
from .events import EventHub
from . import exporters

log = logging.getLogger("assetgen.pipeline")


class Conflict(Exception):
    """The asset is not in a state that allows the action (HTTP 409)."""


class Invalid(Exception):
    """The request does not make sense for this asset (HTTP 422)."""


class Pipeline:
    def __init__(self, settings: Settings, db: Database, adapters: Adapters, hub: EventHub):
        self.settings = settings
        self.db = db
        self.adapters = adapters
        self.hub = hub
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.current: str | None = None

    # ---- paths and settings -------------------------------------------------

    def asset_dir(self, asset_id: str) -> Path:
        return self.settings.assets_dir / asset_id

    def stage_dir(self, asset_id: str, stage: str) -> Path:
        return self.asset_dir(asset_id) / stage

    def source_image(self, asset_id: str) -> Path:
        """The user's own image for an asset made from an upload (kept outside every stage folder)."""
        return self.asset_dir(asset_id) / "source.png"

    def asset_settings(self, asset: dict) -> dict:
        """The style profile with the asset's overrides merged in."""
        profile = self.db.get("profiles", asset["profile_id"]) if asset.get("profile_id") else None
        base = profile_defaults()
        if profile:
            base = deep_merge(base, {k: profile[k] for k in PROFILE_SETTING_KEYS if k in profile})
        merged = deep_merge(base, asset.get("overrides") or {})
        merged["reference_images"] = profile.get("reference_images", []) if profile else []
        return merged

    def clips_for(self, asset: dict, settings: dict) -> list[str]:
        clips = list(settings["clips"])
        if asset["archetype"] == "biped":
            clips += [c for c in settings["biped_extra_clips"] if c not in clips]
        if asset["kind"] == "weapon":
            clips += [c for c in WEAPON_ATTACKS[asset["weapon_class"]] if c not in clips]
        return clips

    @staticmethod
    def preview_clips(asset: dict, clips: list[str]) -> list[str]:
        if asset["kind"] == "gear":
            return ["walk", "attack_1"]
        if asset["kind"] == "weapon":
            return ["walk", WEAPON_ATTACKS[asset["weapon_class"]][0]]
        return clips

    # ---- state changes ------------------------------------------------------

    def _event(self, kind: str, asset: dict) -> dict:
        return {"type": kind, "asset_id": asset["id"], "status": asset["status"], "stage": asset["stage"],
                "gate": asset.get("gate"), "step": asset.get("step"), "progress": asset.get("progress", 0.0),
                "error": asset.get("error"), "at": asset["updated_at"]}

    def publish_status(self, asset: dict) -> None:
        event = self._event("status", asset)
        event["asset"] = asset
        self.hub.publish(event)

    def update(self, asset_id: str, **changes) -> dict | None:
        asset = self.db.update("assets", asset_id, changes)
        if asset is not None:
            self.publish_status(asset)
        return asset

    def _progress(self, asset_id: str, stage: str):
        def report(step: str, fraction: float) -> None:
            asset = self.db.update("assets", asset_id, {"step": step, "progress": round(float(fraction), 3)})
            if asset is not None:
                self.hub.publish(self._event("progress", asset))
        return report

    def enqueue(self, asset_id: str, stage: str, step: str = "Waiting for the worker") -> dict:
        asset = self.update(asset_id, status="queued", stage=stage, gate=None, step=step, progress=0.0,
                            error=None, queued_at=now())
        self._wake.set()
        return asset

    def _clear_from(self, asset_id: str, stage: str) -> None:
        """Removes the files of ``stage`` and every later stage."""
        for name in (*STAGES[STAGES.index(stage):], "sprites"):
            shutil.rmtree(self.stage_dir(asset_id, name), ignore_errors=True)

    def _require(self, asset_id: str) -> dict:
        asset = self.db.get("assets", asset_id)
        if asset is None:
            raise KeyError(asset_id)
        return asset

    def _require_gate(self, asset: dict, gate: int) -> None:
        if gate not in STAGE_OF_GATE:
            raise Invalid("gate must be 1, 2 or 3")
        if asset["status"] != "waiting" or asset.get("gate") != gate:
            where = f"waiting at gate {asset.get('gate')}" if asset["status"] == "waiting" else asset["status"]
            raise Conflict(f"asset is {where}, not waiting at gate {gate}")

    def approve(self, asset_id: str, gate: int, candidate: int | None = None) -> dict:
        asset = self._require(asset_id)
        self._require_gate(asset, gate)
        outputs = dict(asset.get("outputs") or {})
        changes: dict = {}
        if gate == 1:
            count = len(outputs.get("prompt", {}).get("candidates", []))
            if candidate is None:
                raise Invalid("gate 1 needs the picked candidate")
            if not 1 <= candidate <= count:
                raise Invalid(f"candidate must be between 1 and {count}")
            changes["chosen_candidate"] = candidate
        approvals = dict(asset.get("approvals") or {})
        approvals[str(gate)] = {"at": now(), **({"candidate": candidate} if gate == 1 else {})}
        changes["approvals"] = approvals
        self.db.update("assets", asset_id, changes)
        next_stage = STAGES[STAGES.index(STAGE_OF_GATE[gate]) + 1]
        self._clear_from(asset_id, next_stage)
        for stage in STAGES[STAGES.index(next_stage):]:
            outputs.pop(stage, None)
        self.db.update("assets", asset_id, {"outputs": outputs})
        return self.enqueue(asset_id, next_stage)

    def reject(self, asset_id: str, gate: int, note: str | None = None, archetype: str | None = None,
               grip: dict | None = None) -> dict:
        asset = self._require(asset_id)
        self._require_gate(asset, gate)
        stage = STAGE_OF_GATE[gate]
        changes: dict = {}
        if archetype is not None:
            if gate != 1:
                raise Invalid("the archetype can only be changed at gate 1")
            if asset["kind"] in ("gear", "weapon"):
                raise Invalid("gear and weapons always use the biped mannequin")
            if archetype not in load_archetypes():
                raise Invalid(f"unknown archetype {archetype!r}")
            changes["archetype_override"] = archetype
        if grip is not None:
            if gate != 3 or asset["kind"] != "weapon":
                raise Invalid("the grip can only be changed for weapons at gate 3")
            changes["grip"] = {**DEFAULT_GRIPS[asset["weapon_class"]], **(asset.get("grip") or {}), **grip}
        notes = list(asset.get("notes") or [])
        if note and note.strip():
            notes.append({"gate": gate, "stage": stage, "note": note.strip(), "at": now()})
        attempts = dict(asset.get("attempts") or {})
        attempts[stage] = attempts.get(stage, 1) + 1
        outputs = {k: v for k, v in (asset.get("outputs") or {}).items()
                   if k not in STAGES[STAGES.index(stage):]}
        changes.update(notes=notes, attempts=attempts, outputs=outputs)
        if gate == 1:
            changes["chosen_candidate"] = None
        self.db.update("assets", asset_id, changes)
        self._clear_from(asset_id, stage)
        return self.enqueue(asset_id, stage)

    def retry(self, asset_id: str) -> dict:
        asset = self._require(asset_id)
        if asset["status"] != "failed":
            raise Conflict(f"only failed assets can be retried (asset is {asset['status']})")
        return self.enqueue(asset_id, asset["stage"])  # keeps finished files: the stage resumes

    def reexport(self, asset_id: str) -> dict:
        asset = self._require(asset_id)
        if not (asset["status"] == "done" or (asset["status"] == "failed" and asset["stage"] == "export")):
            raise Conflict(f"only finished assets can be re-exported (asset is {asset['status']})")
        self._clear_from(asset_id, "export")
        outputs = {k: v for k, v in (asset.get("outputs") or {}).items() if k != "export"}
        self.db.update("assets", asset_id, {"outputs": outputs})
        return self.enqueue(asset_id, "export")

    def delete(self, asset_id: str) -> bool:
        if not self.db.delete("assets", asset_id):
            return False
        if self.current != asset_id:  # the worker removes the files itself when it finishes
            shutil.rmtree(self.asset_dir(asset_id), ignore_errors=True)
        self.hub.publish({"type": "deleted", "asset_id": asset_id, "at": now()})
        return True

    # ---- the worker ---------------------------------------------------------

    def start(self) -> None:
        # Resume: anything that was running when the server stopped goes back in the queue.
        for asset in self.db.list("assets", status="running"):
            self.db.update("assets", asset["id"], {"status": "queued", "step": "Resuming after restart"})
        self._task = asyncio.create_task(self._loop(), name="assetgen-worker")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _next(self) -> dict | None:
        queued = self.db.list("assets", order="queued_at", status="queued")
        return queued[0] if queued else None

    async def _loop(self) -> None:
        while True:
            asset = self._next()
            if asset is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            await self.run_one(asset["id"])

    async def run_one(self, asset_id: str) -> None:
        asset = self.db.get("assets", asset_id)
        if asset is None or asset["status"] != "queued":
            return
        stage = asset["stage"]
        self.current = asset_id
        self.update(asset_id, status="running", step="Starting", progress=0.0, error=None)
        try:
            outputs = await asyncio.to_thread(self.run_stage, asset_id, stage)
        except Exception as exc:  # noqa: BLE001 - every failure is reported on the asset
            if self.db.get("assets", asset_id) is None:  # deleted while it ran
                shutil.rmtree(self.asset_dir(asset_id), ignore_errors=True)
                return
            log.exception("stage %s of %s failed", stage, asset_id)
            self.update(asset_id, status="failed", step=f"{stage} failed", error=f"{exc}",
                        error_detail=traceback.format_exc(limit=5))
            return
        finally:
            self.current = None
        asset = self.db.get("assets", asset_id)
        if asset is None:  # deleted while it ran
            shutil.rmtree(self.asset_dir(asset_id), ignore_errors=True)
            return
        all_outputs = {**(asset.get("outputs") or {}), stage: outputs}
        if stage in GATE_OF_STAGE:
            gate = GATE_OF_STAGE[stage]
            self.update(asset_id, outputs=all_outputs, status="waiting", gate=gate,
                        step=f"Waiting at gate {gate}", progress=1.0)
        else:
            changes = {"outputs": all_outputs, "status": "done", "gate": None, "step": "Done", "progress": 1.0}
            if not asset.get("training_candidate_set"):
                changes["training_candidate"] = True  # approved assets are LoRA training candidates
            self.update(asset_id, **changes)

    # ---- stages (run in the worker thread) ----------------------------------

    def run_stage(self, asset_id: str, stage: str) -> dict:
        directory = self.stage_dir(asset_id, stage)
        manifest = directory / "manifest.json"
        if manifest.is_file():  # finished before a restart: nothing to redo
            return json.loads(manifest.read_text(encoding="utf-8"))
        directory.mkdir(parents=True, exist_ok=True)
        asset = self._require(asset_id)
        progress = self._progress(asset_id, stage)
        outputs = getattr(self, f"_stage_{stage}")(asset, directory, progress)
        manifest.write_text(json.dumps(outputs, indent=2), encoding="utf-8")
        return outputs

    def _rel(self, asset_id: str, path: Path) -> str:
        return Path(path).relative_to(self.asset_dir(asset_id)).as_posix()

    def _hints(self, asset: dict) -> dict:
        return {"asset_id": asset["id"], "name": asset["name"], "kind": asset["kind"],
                "archetype": asset.get("archetype") or "biped", "slot": asset.get("slot"),
                "weapon_class": asset.get("weapon_class"),
                "attempt": (asset.get("attempts") or {}).get(asset["stage"], 1),
                "notes": [n["note"] for n in asset.get("notes") or [] if n["stage"] == asset["stage"]]}

    def _needs_llm(self, asset: dict) -> bool:
        """Whether an uploaded image still needs the LLM to pick something from the description."""
        if asset["kind"] == "gear":
            return not asset.get("slot")
        if asset["kind"] == "weapon":
            return not asset.get("weapon_class")
        return not asset.get("archetype_override")

    def _stage_prompt(self, asset: dict, directory: Path, progress) -> dict:
        settings = self.asset_settings(asset)
        upload = asset.get("source") == "upload"
        expansion_file = directory / "expansion.json"
        if expansion_file.is_file():
            expansion = json.loads(expansion_file.read_text(encoding="utf-8"))
        elif upload and not self._needs_llm(asset):
            # Everything is chosen: no text model needed for the user's own image.
            biped = asset["kind"] in ("gear", "weapon")
            expansion = {"archetype": "biped" if biped else asset["archetype_override"], "prompt": "",
                         "negative_prompt": "", "slot": asset.get("slot"),
                         "weapon_class": asset.get("weapon_class"), "flags": []}
            expansion_file.write_text(json.dumps(expansion, indent=2), encoding="utf-8")
        else:
            progress("Expanding the description", 0.0)
            notes = [n["note"] for n in asset.get("notes") or [] if n["stage"] == "prompt"]
            expansion = self.adapters.llm.expand(
                description=asset["description"], kind=asset["kind"], archetypes=load_archetypes(),
                archetype_override=asset.get("archetype_override"), slot=asset.get("slot"),
                weapon_class=asset.get("weapon_class"), notes=notes)
            if expansion["archetype"] not in load_archetypes():
                expansion.setdefault("flags", []).append(
                    f"The archetype {expansion['archetype']!r} is not a template; biped is used instead.")
                expansion["archetype"] = "biped"
            expansion_file.write_text(json.dumps(expansion, indent=2), encoding="utf-8")
        changes = {"archetype": expansion["archetype"]}
        if asset["kind"] == "gear":
            changes["slot"] = expansion.get("slot") or asset.get("slot") or "chest"
        if asset["kind"] == "weapon":
            changes["weapon_class"] = expansion.get("weapon_class") or asset.get("weapon_class") or "one_handed"
            changes["grip"] = asset.get("grip") or dict(DEFAULT_GRIPS[changes["weapon_class"]])
        asset = self.db.update("assets", asset["id"], changes)
        attempt = (asset.get("attempts") or {}).get("prompt", 1)
        if upload:
            progress("Using your image", 0.5)
            candidate = directory / "candidate_1.png"
            shutil.copy2(self.source_image(asset["id"]), candidate)
            return {"source": "upload", "expansion": expansion, "prompt": "", "negative_prompt": "",
                    "base_model": None, "loras": [], "attempt": attempt, "log": [],
                    "candidates": [self._rel(asset["id"], candidate)]}
        prompt = ", ".join(p for p in (expansion["prompt"], settings.get("prompt_suffix")) if p)
        negative = ", ".join(p for p in (expansion.get("negative_prompt"), settings.get("negative_prompt")) if p)
        loras = self._profile_loras(settings)
        references = [self.settings.uploads_dir / "references" / r["file"] for r in settings["reference_images"]]

        def scaled(step, fraction):
            progress(step, 0.1 + 0.9 * fraction)

        stage_log: list[str] = []
        paths = self.adapters.image.render_candidates(
            prompt=prompt, negative_prompt=negative, base_model=settings["base_model"], loras=loras,
            reference_images=references, count=int(settings["candidates"]), seed=seed_of(asset["id"], attempt),
            out_dir=directory, progress=scaled, hints=self._hints(asset), log=stage_log.append)
        return {"expansion": expansion, "prompt": prompt, "negative_prompt": negative,
                "base_model": settings["base_model"], "loras": loras, "attempt": attempt,
                "log": stage_log, "candidates": [self._rel(asset["id"], p) for p in paths]}

    def _profile_loras(self, settings: dict) -> list[dict]:
        result = []
        for entry in settings.get("loras") or []:
            lora = self.db.get("loras", entry["id"])
            if lora and lora["base_model"] == settings["base_model"]:
                result.append({"id": lora["id"], "name": lora["name"], "path": lora["path"],
                               "weight": entry.get("weight", 1.0)})
        return result

    def _stage_mesh(self, asset: dict, directory: Path, progress) -> dict:
        settings = self.asset_settings(asset)
        self.adapters.image.unload()  # the image model and Hunyuan3D never share the GPU
        chosen = self.stage_dir(asset["id"], "prompt") / f"candidate_{asset['chosen_candidate']}.png"
        source = directory / "input.png"
        shutil.copy2(chosen, source)
        raw = directory / "raw.glb"
        texture = directory / "texture.png"
        delight = bool(settings.get("delight", True))
        if not (raw.is_file() and texture.is_file()):
            self.adapters.mesh.image_to_mesh(
                image=source, texture_size=int(settings["budgets"]["texture"][asset["kind"]]), out_path=raw,
                progress=lambda s, f: progress(s, 0.5 * f), hints=self._hints(asset), delight=delight)
        triangles = int(settings["budgets"]["triangles"][asset["kind"]])
        result = self.adapters.blender.cleanup_and_lods(
            raw_mesh=raw, kind=asset["kind"], triangles=triangles, lod_ratios=list(settings["budgets"]["lod_ratios"]),
            out_dir=directory, progress=lambda s, f: progress(s, 0.5 + 0.5 * f))
        return {"glb": self._rel(asset["id"], result["glb"]), "texture": self._rel(asset["id"], texture),
                "triangle_budget": triangles, "lods": result["lods"], "delight": delight}

    def _stage_rig(self, asset: dict, directory: Path, progress) -> dict:
        settings = self.asset_settings(asset)
        archetype = get_archetype(asset["archetype"])
        mesh = self.stage_dir(asset["id"], "mesh") / "mesh.glb"
        skeleton = self.adapters.rig.predict_skeleton(
            mesh=mesh, archetype=archetype, out_dir=directory, progress=lambda s, f: progress(s, 0.3 * f))
        clips = self.clips_for(asset, settings)
        attach = None
        if asset["kind"] == "gear":
            attach = {"kind": "gear", "slot": asset["slot"]}
        elif asset["kind"] == "weapon":
            attach = {"kind": "weapon", "weapon_class": asset["weapon_class"], "grip": asset["grip"]}
        project = self.db.get("projects", asset["project_id"]) or {}
        godot = project.get("engine") == "godot"
        result = self.adapters.blender.rig_and_animate(
            mesh_glb=mesh, skeleton=skeleton, archetype=archetype, clips=clips, attach=attach,
            out_path=directory / "animated.glb",
            progress=lambda s, f: progress(s, 0.3 + (0.4 if godot else 0.7) * f))
        output = {"glb": self._rel(asset["id"], result["glb"]), "clips": result["clips"],
                  "preview_clips": self.preview_clips(asset, clips), "rig_type": archetype["rig_type"],
                  "skeleton": {"joints": len(skeleton["joints"]), "fit_score": skeleton["fit_score"],
                               "fallback": skeleton["fallback"]},
                  "grip": asset.get("grip") if asset["kind"] == "weapon" else None}
        if godot:
            sprites = self._render_sprites(asset, settings, result["glb"], result["clips"],
                                           lambda s, f: progress(s, 0.7 + 0.3 * f))
            output["sprites"] = {k: v for k, v in sprites.items() if k != "frames"}  # paths stay out of the db
        return output

    def _render_sprites(self, asset: dict, settings: dict, animated: Path, clips: list[dict], progress) -> dict:
        """Renders the sprite frames into the asset's sprites folder (files already there are kept).

        Godot projects render them in the rig stage so gate 3 can play them; the export reuses them."""
        preset = settings["camera_preset"]
        render = settings["render"]
        frame_size = int(render["frame_size"][asset["kind"]])
        fps = int(render["fps"])
        frames = self.adapters.blender.render_sprites(
            animated_glb=animated, clips=clips, directions=CAMERA_DIRECTIONS[preset],
            camera_preset=preset, frame_size=frame_size, supersample=int(render["supersample"]),
            fps=fps, look=render["look"], palette_size=int(render["palette_size"]),
            out_dir=self.stage_dir(asset["id"], "sprites"), progress=progress)
        return {"frames": frames, "camera_preset": preset, "directions": CAMERA_DIRECTIONS[preset],
                "mirrored": CAMERA_MIRRORED[preset], "fps": fps, "frame_size": frame_size,
                "files": {d: {c: [self._rel(asset["id"], p) for p in paths] for c, paths in by_clip.items()}
                          for d, by_clip in frames.items()}}

    @staticmethod
    def _export_parent(engine: str, kind: str) -> Path:
        """Folder inside the game project that holds the exports of one kind."""
        return (Path("Assets") / "Generated" if engine == "unity" else Path("generated")) / kind

    def _claim_export_name(self, asset: dict, project: dict) -> str:
        """The asset's own export folder name, unique within its project and kind.

        Picked at the first export and kept, so re-exports only ever replace
        this asset's folder. When another asset of the project (or a folder
        already in the game project that is not this asset's) has the name,
        a short id suffix is added."""
        if asset.get("export_name"):
            return asset["export_name"]
        parent = (Path(project["path"]).expanduser() / self._export_parent(project["engine"], asset["kind"])
                  if project.get("path") else None)
        claimed = {(a.get("export_name") or "").lower()
                   for a in self.db.list("assets", project_id=asset["project_id"], kind=asset["kind"])
                   if a["id"] != asset["id"]}

        def free(candidate: str) -> bool:
            if candidate.lower() in claimed:
                return False
            return parent is None or exporters.folder_owner(parent / candidate) in (None, asset["id"])

        base = exporters.safe_name(asset["name"])
        name = base if free(base) else f"{base}_{asset['id'][:6]}"
        if not free(name):
            name = f"{base}_{asset['id']}"
        self.db.update("assets", asset["id"], {"export_name": name})
        return name

    def _stage_export(self, asset: dict, directory: Path, progress) -> dict:
        settings = self.asset_settings(asset)
        project = self.db.get("projects", asset["project_id"])
        if project is None:
            raise RuntimeError("the asset's project no longer exists")
        outputs = asset.get("outputs") or {}
        rig, mesh = outputs["rig"], outputs["mesh"]
        engine = project["engine"]
        name = self._claim_export_name(asset, project)
        animated = self.asset_dir(asset["id"]) / rig["glb"]
        build = directory / engine / name
        result: dict = {"engine": engine, "name": name}
        if engine == "unity":
            fbx = self.adapters.blender.export_fbx(
                animated_glb=animated, mesh_glb=self.asset_dir(asset["id"]) / mesh["glb"], lods=mesh["lods"],
                rig_type=rig["rig_type"], out_path=directory / "fbx" / f"{name}.fbx", progress=lambda s, f: progress(s, 0.6 * f))
            progress("Writing the Unity folder", 0.7)
            exporters.write_unity(out_dir=build, name=name, asset=asset, fbx=fbx,
                                  texture=self.asset_dir(asset["id"]) / mesh["texture"], lods=mesh["lods"],
                                  clips=rig["clips"], rig_type=rig["rig_type"])
        else:
            rendered = self._render_sprites(asset, settings, animated, rig["clips"],
                                            lambda s, f: progress(s, 0.8 * f))
            sprites, preset, frame_size = rendered["frames"], rendered["camera_preset"], rendered["frame_size"]
            render = settings["render"]
            progress("Packing atlases", 0.85)
            layer = {"gear": asset.get("slot"), "weapon": "weapon"}.get(asset["kind"], "body")
            res_dir = f"res://generated/{asset['kind']}/{name}"
            exporters.write_godot(out_dir=build, res_dir=res_dir, name=name, asset=asset, layer=layer,
                                  sprites=sprites, clips=rig["clips"], frame_size=frame_size,
                                  fps=int(render["fps"]), camera_preset=preset,
                                  mirrored=CAMERA_MIRRORED[preset], look=render["look"])
            result.update(directions=CAMERA_DIRECTIONS[preset], frame_size=frame_size, layer=layer,
                          sprites_dir="sprites")
        relative = self._export_parent(engine, asset["kind"]) / name
        progress("Zipping", 0.9)
        zip_path = exporters.make_zip(build, directory / f"{name}.zip", name)
        installed = None
        if project.get("path"):
            destination = Path(project["path"]).expanduser() / relative
            progress("Copying into the game project", 0.95)
            if engine == "unity":
                result["importer"] = exporters.install_unity_importer(Path(project["path"]).expanduser())
                exporters.install(build, destination, asset["id"], keep=exporters.unity_importer_outputs(name))
            else:
                exporters.install(build, destination, asset["id"])
            installed = str(destination)
        result.update(
            folder=self._rel(asset["id"], build), zip=self._rel(asset["id"], zip_path),
            installed_to=installed, project_relative=relative.as_posix(),
            files=sorted(p.relative_to(build).as_posix() for p in build.rglob("*") if p.is_file()),
            exported_at=now())
        return result
