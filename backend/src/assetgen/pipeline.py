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
import re
import shutil
import threading
import traceback
from pathlib import Path

import numpy as np
from PIL import Image

from .adapters import Adapters
from .archetypes import ARCHETYPES_DIR, get_archetype, load_archetypes
from .config import Settings
from .db import Database, now
from .defaults import (AUTO_MESH_RETRIES, AUTO_PICTURES, AUTO_REDRAWS, EXAMPLE_LORA_WEIGHT, EXAMPLE_NEGATIVE,
                       EXAMPLE_SEED, EXAMPLE_SUBJECTS, MESH_MODELS, PAIR_NEGATIVE, PAIR_PIECES)
from .defaults import (CAMERA_DIRECTIONS, CAMERA_MIRRORED, DEFAULT_GRIPS, PAPER_DOLL_FRAMING, GATE_OF_STAGE, PROFILE_SETTING_KEYS,
                       STAGE_OF_GATE, STAGES, WEAPON_ATTACKS, deep_merge, profile_defaults, seed_of)
from .events import EventHub
from . import checks as qc
from . import effects, exporters, grips, imaging, motion_sheet, pose, variants, watchdog


MOTION_CANDIDATES = 4  # versions of each skill animation made from one prompt; the judge picks one

log = logging.getLogger("assetgen.pipeline")

# The test dummy's id where a hero's goes (outfits, fits): the biped base body.
DUMMY = "dummy"

# MV-Adapter's view names to Hunyuan3D-2mv's (both: the character's own left and right sides).
HUNYUAN_VIEW = {"front": "front", "right": "right", "back": "back", "left": "left"}

# Added to the negative prompt when the pose guide is used.
POSE_NEGATIVE = "hands touching the legs, arms against the body, hands on hips, arms crossed"


# The GPU model each stage starts with: ``llm`` (LM Studio), ``image`` (ComfyUI), ``mesh``
# (Hunyuan3D) and ``rig`` (UniRig). A stage that uses a second model steps back into the queue
# (``StepBack``) while other assets wait for the model on the GPU now, so a batch runs model by
# model (every prompt, then every drawing, then every judging...) instead of swapping per asset.
STAGE_MODEL = {"prompt": "llm", "mesh": "mesh", "rig": "rig", "export": None}
# Skill effects: the language model plans, ComfyUI draws ("image"), Wan 2.2 in ComfyUI animates ("video").
EFFECT_STAGE_MODEL = {"prompt": "llm", "mesh": "image", "rig": None, "export": None}
COMFYUI_MODELS = ("image", "video")


class StepBack(Exception):
    """A stage stops before a step on another model; it resumes from its files when that model's
    turn comes (``needs``)."""

    def __init__(self, needs: str, step: str):
        super().__init__(step)
        self.needs = needs
        self.step = step


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
        self.loaded: str | None = None  # the model the worker last used (STAGE_MODEL's names)
        self._skill_lock = threading.Lock()  # one hero_clip.py at a time (they may share a hero)

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
        elif self.paper_doll(asset):  # the body and gear layers play every weapon's attacks too
            clips += [c for attacks in WEAPON_ATTACKS.values() for c in attacks]
        return list(dict.fromkeys(clips))  # once each (weapon classes share some attacks)

    @staticmethod
    def paper_doll(asset: dict) -> bool:
        """Assets whose Godot layers stack in one PaperDoll: biped heroes, gear and weapons. They share
        the sprite framing and the clip set."""
        return asset["kind"] in ("gear", "weapon") or (asset["kind"] == "hero" and asset.get("archetype") == "biped")

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
                            error=None, queued_at=now(), needs=None)
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
               grip: dict | None = None, joints: dict | None = None, height: float | None = None,
               wearer: str | None = None, twists: dict | None = None) -> dict:
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
            changes["grip"] = {**DEFAULT_GRIPS[asset["weapon_class"]], **(asset.get("grip") or {}), **grip,
                               "source": "manual"}
        if height is not None:
            if gate != 2 or asset["kind"] in ("gear", "weapon"):
                raise Invalid("the height can only be changed for characters at gate 2")
            changes["height"] = float(height)
        if wearer is not None:
            if gate != 3 or asset["kind"] not in ("gear", "weapon"):
                raise Invalid("the wearer can only be changed for gear and weapons at gate 3")
            if wearer and wearer not in {w["id"] for w in self.wearers(asset["project_id"])}:
                raise Invalid("the wearer must be a rigged biped hero of the same project")
            changes["wearer_id"] = wearer or None
        if joints is not None:
            if gate != 3 or asset["kind"] in ("gear", "weapon"):
                raise Invalid("joints can only be moved for characters at gate 3")
            known = set(get_archetype(asset["archetype"])["joints"])
            unknown = sorted(set(joints) - known)
            if unknown:
                raise Invalid(f"unknown joints for the {asset['archetype']} skeleton: {', '.join(unknown)}")
            # Moved joints add up over rejections; an empty set goes back to the detected ones.
            changes["joint_overrides"] = ({**(asset.get("joint_overrides") or {}),
                                           **{k: [float(v) for v in p] for k, p in joints.items()}} if joints else {})
            if not joints and not twists:
                changes["joint_twists"] = {}
        if twists is not None:
            if gate != 3 or asset["kind"] in ("gear", "weapon"):
                raise Invalid("joints can only be twisted for characters at gate 3")
            known = set(get_archetype(asset["archetype"])["joints"])
            unknown = sorted(set(twists) - known)
            if unknown:
                raise Invalid(f"unknown joints for the {asset['archetype']} skeleton: {', '.join(unknown)}")
            merged = {**(asset.get("joint_twists") or {}), **{k: float(v) for k, v in twists.items()}}
            changes["joint_twists"] = {k: v for k, v in merged.items() if abs(v) > 1e-3}
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
        """Runs a failed or stopped stage again; it resumes from the files it already made."""
        asset = self._require(asset_id)
        if asset["status"] not in ("failed", "stopped"):
            raise Conflict(f"only failed or stopped assets can be retried (asset is {asset['status']})")
        return self.enqueue(asset_id, asset["stage"])  # keeps finished files: the stage resumes

    def stop_asset(self, asset_id: str) -> dict:
        """Takes a queued stage off the queue (``stopped``; retry resumes it). A running stage can't be
        cut off safely (a tool may be mid-job): it finishes and waits at its gate. Autopilot goes off
        either way, so nothing continues by itself."""
        asset = self._require(asset_id)
        if asset["status"] == "queued":
            return self.update(asset_id, status="stopped", autopilot=False, step="Stopped by you", progress=0.0)
        if asset["status"] == "running":
            return self.update(asset_id, autopilot=False)
        raise Conflict(f"only queued or running assets can be stopped (asset is {asset['status']})")

    def stop_all(self) -> dict:
        """Stops every queued asset and side job; running ones finish their step (autopilot off)."""
        stopped = running = jobs = 0
        for asset in self.db.list("assets"):
            if asset["status"] in ("queued", "running"):
                self.stop_asset(asset["id"])
                stopped += asset["status"] == "queued"
                running += asset["status"] == "running"
        for job in self.db.list("jobs", status="queued"):
            self._job_update(job["id"], status="stopped", step="Stopped by you")
            jobs += 1
        return {"stopped": stopped, "finishing": running, "jobs_stopped": jobs}

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
        for job in self.db.list("jobs", status="running"):
            self.db.update("jobs", job["id"], {"status": "queued", "step": "Resuming after restart"})
        self._task = asyncio.create_task(self._loop(), name="assetgen-worker")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    @staticmethod
    def needs(asset: dict) -> str | None:
        """The model a queued asset's next step runs on."""
        stages = EFFECT_STAGE_MODEL if asset.get("kind") == "effect" else STAGE_MODEL
        return asset.get("needs") or stages.get(asset["stage"])

    def _waiting_for(self, model: str | None, besides: str) -> bool:
        """Whether another queued asset's next step runs on ``model``."""
        return model is not None and any(
            a["id"] != besides and self.needs(a) == model for a in self.db.list("assets", status="queued"))

    def _next(self) -> tuple[str, dict] | None:
        """The oldest queued asset stage whose next step runs on the model the GPU holds now; else the
        oldest queued asset stage or job (one worker: the GPU is never shared)."""
        assets = self.db.list("assets", order="queued_at", status="queued")
        same = [a for a in assets if self.loaded and self.needs(a) == self.loaded]
        if same:
            return "asset", same[0]
        queued = [("asset", a) for a in assets[:1]]
        queued += [("job", j) for j in self.db.list("jobs", order="queued_at", status="queued")[:1]]
        return min(queued, key=lambda item: item[1]["queued_at"]) if queued else None

    def _use(self, model: str, settings: dict) -> None:
        """Gets the GPU ready for a step on ``model``: with one model at a time, the others make way
        (each loads again by itself when it is next used). Hunyuan3D never shares it with ComfyUI."""
        before = self.loaded
        if before == model:
            return
        alone = settings.get("one_model_at_a_time", True)
        if (model == "mesh" or (before in COMFYUI_MODELS and model in COMFYUI_MODELS)
                or (alone and model not in COMFYUI_MODELS and before in (None, *COMFYUI_MODELS))):
            self.adapters.image.unload()  # ComfyUI frees whatever it holds (the image model, Wan)
        if alone and model != "llm" and before in (None, "llm"):
            self.adapters.llm.unload(self._text_model(settings))
        self.loaded = model

    def _turn(self, asset: dict, settings: dict, model: str, step: str) -> None:
        """Before a step on ``model``: while other assets wait for the model the GPU holds now, this
        one steps back into the queue (its files are kept); otherwise the GPU switches to ``model``."""
        if self.loaded not in (None, model) and self._waiting_for(self.loaded, asset["id"]):
            raise StepBack(model, step)
        self._use(model, settings)

    async def _loop(self) -> None:
        while True:
            item = self._next()
            if item is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            kind, record = item
            if kind == "asset":
                await self.run_one(record["id"])
            else:
                await self.run_job(record["id"])

    async def _restart_and_retry(self, asset_id: str, stage: str, exc: Exception) -> bool:
        """A stage that failed because its tool did not answer or ran out of memory: restart the tool
        (when assetgen.toml says how) and queue the stage again, once per stage."""
        tool = watchdog.STAGE_TOOL.get(stage)
        if not tool or not watchdog.TOOL_TROUBLE.search(str(exc)) or not watchdog.restartable(self.settings, tool):
            return False
        asset = self.db.get("assets", asset_id)
        tried = dict(asset.get("auto_restarts") or {})
        if tried.get(stage):
            return False
        tried[stage] = 1
        self.update(asset_id, status="running", step=f"Restarting {tool} after: {str(exc)[:120]}", auto_restarts=tried)
        try:
            what = await asyncio.to_thread(watchdog.restart_tool, self.settings, tool)
        except Exception as again:  # noqa: BLE001 - the stage fails as before, with both reasons
            log.warning("restarting %s failed: %s", tool, again)
            return False
        log.info("%s %s; running %s of %s again", tool, what, stage, asset_id)
        self.enqueue(asset_id, stage, step=f"{tool} {what}; running {stage} again")
        return True

    # ---- side jobs: item fits and example renders ---------------------------------

    def queue_job(self, kind: str, target_id: str, params: dict | None = None) -> dict:
        """Queues a job (``fit``, ``lora_examples``, ``profile_examples``); an identical one already
        waiting or running is returned instead of a second one."""
        params = params or {}
        for job in self.db.list("jobs", kind=kind, target_id=target_id):
            if job["status"] in ("queued", "running") and job.get("params") == params:
                return job
        job = self.db.insert("jobs", {"kind": kind, "target_id": target_id, "params": params, "status": "queued",
                                      "step": "Waiting for the worker", "progress": 0.0, "error": None,
                                      "result": None, "queued_at": now()})
        self._publish_job(job)
        self._wake.set()
        return job

    def _publish_job(self, job: dict) -> None:
        self.hub.publish({"type": "job", "job": job, "at": now()})

    def _job_update(self, job_id: str, **changes) -> dict:
        job = self.db.update("jobs", job_id, changes)
        if job:
            self._publish_job(job)
        return job

    async def run_job(self, job_id: str) -> None:
        job = self.db.get("jobs", job_id)
        if job is None or job["status"] != "queued":
            return
        self._job_update(job_id, status="running", step="Starting", progress=0.0, error=None)

        def progress(step: str, fraction: float) -> None:
            self._job_update(job_id, step=step, progress=max(0.0, min(1.0, float(fraction))))

        if job["kind"].endswith("_examples"):  # they draw, then free ComfyUI
            self.loaded = None
        try:
            result = await asyncio.to_thread(getattr(self, f"_job_{job['kind']}"), job, progress)
        except Exception as exc:  # noqa: BLE001 - reported on the job
            log.exception("job %s (%s) failed", job_id, job["kind"])
            self._job_update(job_id, status="failed", step="Failed", error=f"{exc}")
            return
        self._job_update(job_id, status="done", step="Done", progress=1.0, result=result, finished_at=now())

    def dummy_glb(self) -> Path:
        """The test dummy: the biped base body with every biped clip, which outfits dress like a hero."""
        return Path(self.settings.data_dir) / "dummy" / "biped" / "animated.glb"

    def dummy_clips(self) -> list[str]:
        folder = ARCHETYPES_DIR / get_archetype("biped")["folder"]
        return sorted(json.loads((folder / "clips.json").read_text(encoding="utf-8")))

    def _job_dummy(self, job: dict, progress) -> dict:
        out = self.dummy_glb()
        archetype = get_archetype("biped")
        result = self.adapters.blender.rig_and_animate(
            mesh_glb=None, skeleton={"joints": archetype["joints"], "fit_score": 1.0, "fallback": False},
            archetype=archetype, clips=self.dummy_clips(), attach={"kind": "body"}, out_path=out, progress=progress)
        (out.parent / "clips.json").write_text(json.dumps(result["clips"]), encoding="utf-8")
        return {"clips": result["clips"]}

    # ---- skill animations (HY-Motion) -------------------------------------------------

    def motion_dir(self, motion_id: str) -> Path:
        return Path(self.settings.data_dir) / "motions" / motion_id

    def _clip_name(self, title: str) -> str:
        """``skill_<title>``, unique among the generated clips and the biped's own."""
        slug = "_".join("".join(c if c.isalnum() else " " for c in title.lower()).split())[:40] or "skill"
        taken = {m["name"] for m in self.db.list("motions")} | set(self.dummy_clips())
        name, n = f"skill_{slug}", 2
        while name in taken:
            name, n = f"skill_{slug}_{n}", n + 1
        return name

    def create_motion(self, *, title: str, description: str, prompt: str | None = None,
                      duration: float | None = None, in_place: bool = True) -> dict:
        """A skill animation from a description; queued at once (``motion`` job)."""
        motion = self.db.insert("motions", {
            "name": self._clip_name(title), "title": title.strip(), "description": description.strip(),
            "prompt": (prompt or "").strip() or None, "duration": duration, "in_place": bool(in_place),
            "attempt": 1, "notes": [], "status": "queued", "step": "Waiting for the worker", "progress": 0.0,
            "error": None, "clip": None, "preview": None, "candidates": None, "chosen": None})
        self.queue_job("motion", motion["id"])
        return motion

    def redo_motion(self, motion_id: str, *, note: str | None = None, prompt: str | None = None,
                    duration: float | None = None, in_place: bool | None = None) -> dict:
        """Makes the skill animation again (a new seed); a note asks the language model to rewrite the
        prompt with it in mind, a prompt of your own is used as it is."""
        motion = self.db.get("motions", motion_id)
        if motion is None:
            raise KeyError(motion_id)
        if motion["status"] in ("queued", "running"):
            raise Conflict(f"the skill animation is {motion['status']}")
        changes: dict = {"attempt": motion.get("attempt", 1) + 1, "status": "queued", "step": "Waiting for the worker",
                         "progress": 0.0, "error": None, "clip": None, "preview": None, "candidates": None,
                         "chosen": None}
        if note and note.strip():
            changes["notes"] = [*(motion.get("notes") or []), note.strip()]
            changes["prompt"] = None  # written again with the note
        if prompt is not None:
            changes["prompt"] = prompt.strip() or None
        if duration is not None:
            changes["duration"] = float(duration)
        if in_place is not None:
            changes["in_place"] = bool(in_place)
        shutil.rmtree(self.motion_dir(motion_id), ignore_errors=True)
        motion = self.db.update("motions", motion_id, changes)
        self.queue_job("motion", motion_id)
        return motion

    def delete_motion(self, motion_id: str) -> bool:
        motion = self.db.get("motions", motion_id)
        if motion is None or not self.db.delete("motions", motion_id):
            return False
        shutil.rmtree(self.motion_dir(motion_id), ignore_errors=True)
        (Path(self.settings.data_dir) / "motions" / "clips" / f"{motion['name']}.glb").unlink(missing_ok=True)
        return True

    def _job_motion(self, job: dict, progress) -> dict:
        motion = self.db.get("motions", job["target_id"])
        if motion is None:
            raise RuntimeError("the skill animation is gone")
        if self.adapters.motion is None:
            raise RuntimeError("HY-Motion is not set up")

        def report(step: str, fraction: float) -> None:
            progress(step, fraction)
            self.db.update("motions", motion["id"], {"step": step, "progress": round(float(fraction), 3)})

        self.db.update("motions", motion["id"], {"status": "running", "step": "Starting", "error": None})
        try:
            result = self._make_motion(motion, report)
        except Exception as exc:
            self.db.update("motions", motion["id"], {"status": "failed", "step": "Failed", "error": f"{exc}"})
            raise
        self.db.update("motions", motion["id"], {**result, "status": "done", "step": "Done", "progress": 1.0})
        return result

    def _make_motion(self, motion: dict, report) -> dict:
        """``MOTION_CANDIDATES`` motions from one prompt (other seeds), each drawn as a stick-figure
        sheet and scored by the vision judge; the best one (or the one the user picked) becomes the clip."""
        settings = profile_defaults()
        directory = self.motion_dir(motion["id"])
        directory.mkdir(parents=True, exist_ok=True)
        prompt, duration = motion.get("prompt"), motion.get("duration")
        if not prompt:
            report("Writing the motion prompt", 0.02)
            self._use("llm", settings)
            written = self.adapters.llm.motion_prompt(description=f"{motion['title']}: {motion['description']}",
                                                      notes=motion.get("notes") or [], model=self._text_model(settings))
            prompt, duration = written["prompt"], duration or written["duration"]
            self.db.update("motions", motion["id"], {"prompt": prompt, "duration": duration})
        base = seed_of(motion["id"], motion.get("attempt", 1))
        wanted = [{"prompt": prompt, "duration": float(duration or 2.5), "out": str(directory / f"motion_{n}.json"),
                   "seed": (base + 97 * (n - 1)) % 1000} for n in range(1, MOTION_CANDIDATES + 1)]
        if any(not Path(w["out"]).is_file() for w in wanted):
            self._use("motion", settings)
        self.adapters.motion.generate(motions=wanted, work_dir=directory, progress=lambda s, f: report(s, 0.05 + 0.5 * f))
        candidates = motion.get("candidates") or []
        if len(candidates) != len(wanted):
            report("Judging the versions", 0.56)
            self._use("llm", settings)
            sheets = [motion_sheet.draw_sheet(Path(w["out"]), directory / f"sheet_{n}.png")
                      for n, w in enumerate(wanted, start=1)]
            verdicts = None
            for _ in range(2):  # the language model may still be loading after HY-Motion
                try:
                    verdicts = self.adapters.llm.judge_motions(
                        sheets=sheets, description=f"{motion['title']}: {motion['description']}", prompt=prompt,
                        model=self._text_model(settings))
                    break
                except Exception:  # noqa: BLE001 - the measured checks still score the versions
                    log.warning("the motion judge failed on %s", motion["id"], exc_info=True)
            candidates = []
            for n, (w, sheet) in enumerate(zip(wanted, sheets), start=1):
                verdict = (verdicts or [None] * len(wanted))[n - 1]
                measured = motion_sheet.measure(Path(w["out"]), prompt)
                judged = verdict["match"] if verdict else None
                # The judge's score (7 when it can't look) less the measured problems.
                score = max(0, (7 if judged is None else judged) - sum(c["penalty"] for c in measured))
                problems = [f"{c['label']}: no ({c['detail']})" for c in measured if not c["ok"]]
                candidates.append({"candidate": n, "seed": w["seed"],
                                   "sheet": sheet.relative_to(Path(self.settings.data_dir)).as_posix(),
                                   "match": score, "judge": judged, "checks": measured,
                                   "problems": problems + (verdict["problems"] if verdict else [])})
            self.db.update("motions", motion["id"], {"candidates": candidates})
        chosen = motion.get("chosen")
        if not chosen or chosen > len(candidates):  # the judge's pick: the best score, then the first
            chosen = max(candidates, key=lambda c: (c["match"], -c["candidate"]))["candidate"]
        motion_json = directory / f"motion_{chosen}.json"
        clip = self.adapters.blender.motion_to_clip(motion_json=motion_json, name=motion["name"],
                                                    in_place=motion.get("in_place", True), out_dir=directory,
                                                    progress=lambda s, f: report(s, 0.65 + 0.1 * f))
        archetype = get_archetype("biped")
        preview = directory / "preview.glb"
        preview.unlink(missing_ok=True)
        self.adapters.blender.rig_and_animate(
            mesh_glb=None, skeleton={"joints": archetype["joints"], "fit_score": 1.0, "fallback": False},
            archetype=archetype, clips=[motion["name"]], attach={"kind": "body"}, out_path=preview,
            progress=lambda s, f: report(f"Preview: {s}", 0.75 + 0.25 * f))
        return {"prompt": prompt, "duration": duration, "candidates": candidates, "chosen": chosen,
                "clip": {k: clip[k] for k in ("duration", "frames", "travel_m") if k in clip},
                "preview": preview.relative_to(Path(self.settings.data_dir)).as_posix()}

    def pick_motion(self, motion_id: str, candidate: int) -> dict:
        """Uses another of the skill animation's versions (no new generation: only the clip is made again)."""
        motion = self.db.get("motions", motion_id)
        if motion is None:
            raise KeyError(motion_id)
        if motion["status"] in ("queued", "running"):
            raise Conflict(f"the skill animation is {motion['status']}")
        count = len(motion.get("candidates") or [])
        if not 1 <= candidate <= count:
            raise Invalid(f"pick a version from 1 to {count}" if count else "this skill animation has no versions")
        motion = self.db.update("motions", motion_id, {
            "chosen": candidate, "status": "queued", "step": f"Waiting for the worker (version {candidate})",
            "progress": 0.0, "error": None})
        self.queue_job("motion", motion_id)
        return motion

    def use_motion(self, motion_id: str, profile_id: str) -> dict:
        """Adds the skill animation to a style profile's clips for bipeds (heroes rigged from now on
        get it; rigged ones get it when rigged again)."""
        motion = self.db.get("motions", motion_id)
        profile = self.db.get("profiles", profile_id)
        if motion is None or profile is None:
            raise KeyError(motion_id if motion is None else profile_id)
        if motion["status"] != "done":
            raise Conflict("the skill animation isn't made yet")
        extra = list(profile.get("biped_extra_clips") or profile_defaults()["biped_extra_clips"])
        if motion["name"] not in extra:
            extra.append(motion["name"])
        return self.db.update("profiles", profile_id, {"biped_extra_clips": extra})

    # ---- a hero with a skill animation -----------------------------------------

    def hero_clips(self, hero: dict) -> list[str]:
        """The clips the hero was rigged with."""
        return [c["name"] for c in ((hero.get("outputs") or {}).get("rig") or {}).get("clips") or []]

    def hero_skill_glb(self, hero_id: str, clip: str) -> Path:
        """The hero playing a generated skill animation (and idle), made from the scene its rig
        stage kept, so a skill can be tried on it without rigging it again. Kept under
        ``skills/`` until the clip or the rig changes."""
        hero = self._require(hero_id)
        if hero["kind"] != "hero" or hero.get("archetype") != "biped" or not (hero.get("outputs") or {}).get("rig"):
            raise Invalid("skills are tried on rigged biped heroes")
        motion = next((m for m in self.db.list("motions", name=clip) if m["status"] == "done"), None)
        if motion is None:
            raise KeyError(clip)
        rig_dir = self.stage_dir(hero_id, "rig")
        source = Path(self.settings.data_dir) / "motions" / "clips" / f"{clip}.glb"
        out = self.asset_dir(hero_id) / "skills" / f"{clip}.glb"
        with self._skill_lock:
            newest = max((p.stat().st_mtime for p in (source, rig_dir / "rigged.blend") if p.is_file()), default=0)
            if not out.is_file() or out.stat().st_mtime < newest:
                self.adapters.blender.add_clips(rig_dir=rig_dir, clips=[clip], out_glb=out, save_blend=False,
                                                keep_all=False, progress=lambda s, f: None)
        return out

    # ---- character sets -------------------------------------------------------

    def set_members(self, record: dict) -> dict:
        """Every asset and motion the set made, by slot."""
        slots = record["slots"]
        return {"assets": [*slots["hero"], *(i for item in slots["items"] for i in item["options"]),
                           *(i for skill in slots["skills"] for i in skill["effects"])],
                "motions": [i for skill in slots["skills"] for i in skill["motions"]]}

    def pick_set(self, set_id: str, picks: dict) -> dict:
        """Chooses one option per slot (``None``: not chosen yet); the release time of each skill
        is where in its animation the effect leaves the hand (0-1)."""
        record = self.db.get("sets", set_id)
        if record is None:
            raise KeyError(set_id)
        if record["status"] == "building":
            raise Conflict("the set is being built")
        slots = record["slots"]
        current = record.get("picks") or {}

        def chosen(value, options, what):
            if value is not None and value not in options:
                raise Invalid(f"{value} is not one of the {what} options")
            return value

        out = {"hero": chosen(picks.get("hero", current.get("hero")), slots["hero"], "hero")}
        items = picks.get("items") if picks.get("items") is not None else current.get("items") or []
        if len(items) not in (0, len(slots["items"])):
            raise Invalid(f"items needs one pick per item slot ({len(slots['items'])})")
        out["items"] = [chosen(items[n] if items else None, slot["options"], slot["label"])
                        for n, slot in enumerate(slots["items"])]
        skills = picks.get("skills") if picks.get("skills") is not None else current.get("skills") or []
        if len(skills) not in (0, len(slots["skills"])):
            raise Invalid(f"skills needs one pick per skill ({len(slots['skills'])})")
        out["skills"] = []
        before = current.get("skills") or [{} for _ in slots["skills"]]
        for n, slot in enumerate(slots["skills"]):
            pick = {**(before[n] if n < len(before) else {}), **(skills[n] if skills else {})}
            release = float(pick.get("release", 0.45) if pick.get("release") is not None else 0.45)
            out["skills"].append({"effect": chosen(pick.get("effect"), slot["effects"], f"{slot['name']} effect"),
                                  "motion": chosen(pick.get("motion"), slot["motions"], f"{slot['name']} animation"),
                                  "release": max(0.0, min(1.0, release))})
        return self.db.update("sets", set_id, {"picks": out, "status": "open" if record["status"] != "built"
                                               else "changed"})

    def build_set(self, set_id: str) -> dict:
        """Puts the picked set together (``set_build`` job): the skill animations go on the hero,
        the items are fitted on it with them, and everything is exported with a manifest."""
        record = self.db.get("sets", set_id)
        if record is None:
            raise KeyError(set_id)
        picks = record.get("picks") or {}
        slots = record["slots"]
        if not picks.get("hero"):
            raise Invalid("pick the hero first")
        missing = [s["label"] for s, p in zip(slots["items"], picks.get("items") or [None] * len(slots["items"]))
                   if not p]
        missing += [s["name"] for s, p in zip(slots["skills"], picks.get("skills") or [{}] * len(slots["skills"]))
                    if (s["effects"] and not p.get("effect")) or (s["motions"] and not p.get("motion"))]
        if missing:
            raise Invalid(f"pick every slot first: {', '.join(missing)}")
        hero = self._require(picks["hero"])
        if not (hero.get("outputs") or {}).get("rig") or hero["status"] not in ("waiting", "done"):
            raise Conflict(f"the hero {hero['name']} isn't rigged yet")
        for item_id in picks["items"]:
            item = self._require(item_id)
            if not (item.get("outputs") or {}).get("mesh") or item["status"] in ("queued", "running"):
                raise Conflict(f"{item['name']} isn't ready yet")
        for pick in picks["skills"]:
            if pick.get("motion") and (self.db.get("motions", pick["motion"]) or {}).get("status") != "done":
                raise Conflict("a picked skill animation isn't made yet")
            if pick.get("effect"):
                effect = self._require(pick["effect"])
                if not (effect.get("outputs") or {}).get("rig") or effect["status"] in ("queued", "running"):
                    raise Conflict(f"the effect {effect['name']} isn't ready yet")
        self.db.update("sets", set_id, {"status": "building", "build": {"step": "Queued", "error": None}})
        return self.queue_job("set_build", set_id)

    def _job_set_build(self, job: dict, progress) -> dict:
        record = self.db.get("sets", job["target_id"])
        try:
            return self._build_set(record, progress)
        except Exception as exc:
            self.db.update("sets", record["id"], {"status": "failed", "build": {"step": "Failed", "error": f"{exc}"}})
            raise

    def _set_step(self, record: dict, step: str) -> None:
        self.db.update("sets", record["id"], {"build": {"step": step, "error": None}})
        self.hub.publish({"type": "set", "id": record["id"], "at": now()})

    def _with_clips(self, asset: dict, clips: list[str]) -> dict:
        """The asset's own setting override, so a rig again keeps the skill animations."""
        settings = self.asset_settings(asset)
        extra = list(dict.fromkeys([*settings["biped_extra_clips"], *clips]))
        return {**(asset.get("overrides") or {}), "biped_extra_clips": extra}

    def _build_set(self, record: dict, progress) -> dict:
        picks = record["picks"]
        hero = self._require(picks["hero"])
        clips = []
        for pick in picks["skills"]:
            if pick.get("motion"):
                clips.append(self.db.get("motions", pick["motion"])["name"])
        # 1. The skill animations on the hero, kept in its rig (items fitted on it play them too).
        new = [c for c in clips if c not in self.hero_clips(hero)]
        if new:
            self._set_step(record, "Adding the skill animations to the hero")
            progress("Adding the skill animations to the hero", 0.05)
            rig = dict(hero["outputs"]["rig"])
            rig_dir = self.stage_dir(hero["id"], "rig")
            with self._skill_lock:
                result = self.adapters.blender.add_clips(
                    rig_dir=rig_dir, clips=new, out_glb=self.asset_dir(hero["id"]) / rig["glb"], save_blend=True,
                    keep_all=True, progress=lambda s, f: progress(s, 0.05 + 0.3 * f))
            known = {c["name"]: c for c in rig["clips"]}
            known.update({c["name"]: c for c in result["clips"]})
            rig["clips"] = list(known.values())
            outputs = {**hero["outputs"], "rig": rig}
            (rig_dir / "manifest.json").write_text(json.dumps(rig, indent=2), encoding="utf-8")
            self._drop_fits_on(hero["id"])
            hero = self.db.update("assets", hero["id"], {"outputs": outputs,
                                                         "overrides": self._with_clips(hero, clips)})
        # 2. The items fitted on the hero again, with every clip, then exported by autopilot.
        progress("Fitting the items on the hero", 0.4)
        self._set_step(record, "Fitting the items on the hero")
        for item_id in picks["items"]:
            item = self._require(item_id)
            outputs = {k: v for k, v in (item.get("outputs") or {}).items() if k not in ("rig", "export")}
            self.db.update("assets", item_id, {"wearer_id": hero["id"], "overrides": self._with_clips(item, clips),
                                               "outputs": outputs, "autopilot": True, "autopilot_export": True,
                                               "approvals": {k: v for k, v in (item.get("approvals") or {}).items()
                                                             if k != "3"}})
            self._clear_from(item_id, "rig")
            self.enqueue(item_id, "rig")
        # 3. The hero and the effects exported (the effects only when not exported yet).
        progress("Exporting", 0.8)
        self._set_step(record, "Exporting the hero, the items and the effects")
        exports = [hero["id"], *(p["effect"] for p in picks["skills"] if p.get("effect"))]
        for asset_id in exports:
            asset = self._require(asset_id)
            if asset["status"] == "waiting" and asset.get("gate") == 3:
                self.approve(asset_id, 3)
            elif asset_id == hero["id"] or not (asset.get("outputs") or {}).get("export"):
                self.reexport(asset_id)
        self.db.update("sets", record["id"], {"waiting_for": [hero["id"], *picks["items"],
                                                              *(p["effect"] for p in picks["skills"] if p.get("effect"))]})
        self._set_step(record, "Waiting for the exports")
        self.check_sets()
        return {"clips": clips}

    def check_sets(self) -> None:
        """Writes the manifest of every set whose exports have all finished (called after exports)."""
        for record in self.db.list("sets", status="building"):
            waiting = record.get("waiting_for")
            if not waiting:
                continue
            assets = [self.db.get("assets", i) for i in waiting]
            failed = [a for a in assets if a is None or a["status"] == "failed"]
            if failed:
                names = ", ".join(a["name"] if a else "a deleted asset" for a in failed)
                self.db.update("sets", record["id"], {"status": "failed",
                                                      "build": {"step": "Failed", "error": f"{names} failed"}})
                continue
            if all(a["status"] == "done" and (a.get("outputs") or {}).get("export") for a in assets):
                self._write_set_manifest(record)
                continue
            stuck = [a["name"] for a in assets if a["status"] == "waiting"]
            step = (f"Waiting for you: {', '.join(stuck)} stopped at a gate (its checks didn't all pass)" if stuck
                    else "Waiting for the exports")
            if (record.get("build") or {}).get("step") != step:
                self._set_step(record, step)

    def _write_set_manifest(self, record: dict) -> None:
        picks, slots = record["picks"], record["slots"]

        def exported(asset_id: str) -> dict:
            asset = self._require(asset_id)
            export = asset["outputs"]["export"]
            return {"asset_id": asset_id, "name": export["name"], "kind": asset["kind"],
                    "folder": export.get("project_relative"), "installed_to": export.get("installed_to")}

        manifest = {"name": record["name"], "hero": exported(picks["hero"]), "items": [], "skills": []}
        for slot, item_id in zip(slots["items"], picks["items"]):
            asset = self._require(item_id)
            manifest["items"].append({**exported(item_id), "slot": asset.get("slot"),
                                      "weapon_class": asset.get("weapon_class")})
        for slot, pick in zip(slots["skills"], picks["skills"]):
            motion = self.db.get("motions", pick["motion"]) if pick.get("motion") else None
            manifest["skills"].append({
                "name": slot["name"], "description": slot.get("description"),
                "animation": motion["name"] if motion else None,
                "duration": (motion.get("clip") or {}).get("duration") if motion else None,
                "release": pick.get("release", 0.45),
                "effect": exported(pick["effect"]) if pick.get("effect") else None})
        directory = Path(self.settings.data_dir) / "sets" / record["id"]
        directory.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "_", record["name"].lower()).strip("_") or "character_set"
        text = json.dumps(manifest, indent=2)
        (directory / f"{slug}.json").write_text(text, encoding="utf-8")
        project = self.db.get("projects", record["project_id"]) or {}
        installed = None
        if project.get("path"):
            target = Path(project["path"]).expanduser() / "generated" / "sets" / f"{slug}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            installed = str(target)
        self.db.update("sets", record["id"], {"status": "built", "manifest": manifest, "installed_to": installed,
                                              "build": {"step": "Built", "error": None}, "built_at": now()})
        self.hub.publish({"type": "set", "id": record["id"], "at": now()})

    def place_item(self, item_id: str, wearer_id: str, change: list[list[float]] | None) -> dict:
        """Adds ``change`` (a 4x4 move in Blender world space at the rest pose) to the item's kept
        placement on this wearer, or forgets it (``None``). The next fit on that wearer applies it."""
        item = self._require(item_id)
        placements = dict(item.get("placements") or {})
        if change is None:
            placements.pop(wearer_id, None)
        else:
            before = np.array(placements.get(wearer_id) or np.eye(4), float)
            placements[wearer_id] = [[round(float(v), 6) for v in row] for row in np.array(change, float) @ before]
        return self.db.update("assets", item_id, {"placements": placements})

    def _drop_fits_on(self, hero_id: str) -> None:
        """Gear and weapons fitted on this hero were fitted on its old skeleton and clips: dropped
        when it is rigged again (outfits fit them anew)."""
        for item in self.db.list("assets"):
            fits = item.get("fits") or {}
            if hero_id in fits:
                shutil.rmtree(self.asset_dir(item["id"]) / "fits" / hero_id, ignore_errors=True)
                self.db.update("assets", item["id"], {"fits": {k: v for k, v in fits.items() if k != hero_id}})

    def _job_fit(self, job: dict, progress) -> dict:
        """Fits a finished gear or weapon asset on a rigged hero (or the test dummy, ``dummy``), kept
        per hero under ``fits/<hero>``."""
        item = self._require(job["target_id"])
        dummy = job["params"]["wearer_id"] == DUMMY
        wearer = {"id": DUMMY, "name": "Test dummy"} if dummy else self._wearer_info(job["params"]["wearer_id"])
        if not (item.get("outputs") or {}).get("mesh"):
            raise RuntimeError("the item has no mesh yet: approve its gate 2 first")
        directory = self.asset_dir(item["id"]) / "fits" / wearer["id"]
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True)
        attach = self._attach_for(item)
        if not dummy:
            attach["wearer"] = wearer
        placement = (item.get("placements") or {}).get(wearer["id"])
        if placement:
            attach["placement"] = placement
        archetype = get_archetype("biped")
        clips = self.clips_for(item, self.asset_settings(item))
        result = self.adapters.blender.rig_and_animate(
            mesh_glb=self.stage_dir(item["id"], "mesh") / "mesh.glb",
            skeleton={"joints": archetype["joints"], "fit_score": 1.0, "fallback": False}, archetype=archetype,
            clips=clips, attach=attach, out_path=directory / "animated.glb", progress=progress)
        fit = {"glb": self._rel(item["id"], result["glb"]), "clips": result["clips"],
               "wearer": {"id": wearer["id"], "name": wearer["name"]}, "at": now(), "placed": bool(placement),
               **{k: v for k, v in (result.get("gear") or {}).items()}}
        item = self._require(item["id"])
        self.db.update("assets", item["id"], {"fits": {**(item.get("fits") or {}), wearer["id"]: fit}})
        self.publish_status(self._require(item["id"]))
        return fit

    def examples_dir(self, owner: str, owner_id: str) -> Path:
        return self.settings.data_dir / "examples" / owner / owner_id

    def _render_examples(self, owner: str, owner_id: str, variants: dict, progress) -> list[dict]:
        """Renders every example subject once per variant (``{"with": render_args, "without": ...}``),
        same seed throughout. Returns ``[{"name", "prompt", <variant>: path under examples/}]``."""
        root = self.examples_dir(owner, owner_id)
        shutil.rmtree(root, ignore_errors=True)
        items = []
        total = len(EXAMPLE_SUBJECTS) * len(variants)
        done = 0
        for subject in EXAMPLE_SUBJECTS:
            item = {"name": subject["name"], "prompt": subject["prompt"]}
            for variant, render in variants.items():
                out = root / f"{subject['name']}_{variant}"
                prompt = self.with_triggers(", ".join(p for p in (subject["prompt"], render.get("suffix")) if p),
                                            render.get("loras", []))
                negative = ", ".join(p for p in (EXAMPLE_NEGATIVE, render.get("negative")) if p)
                (path,) = self.adapters.image.render_candidates(
                    prompt=prompt, negative_prompt=negative, base_model=render["base_model"],
                    loras=render.get("loras", []), reference_images=render.get("references", []), count=1,
                    seed=EXAMPLE_SEED, out_dir=out,
                    progress=lambda s, f, d=done: progress(f"{subject['name']} ({variant}): {s}", (d + f) / total),
                    hints={**subject["hints"], "asset_id": f"example_{owner_id}_{subject['name']}_{variant}",
                           "name": subject["name"]})
                final = root / f"{subject['name']}_{variant}.png"
                shutil.move(str(path), final)
                shutil.rmtree(out, ignore_errors=True)
                item[variant] = final.relative_to(self.settings.data_dir / "examples").as_posix()
                done += 1
            items.append(item)
        self.adapters.image.unload()
        return items

    def _job_lora_examples(self, job: dict, progress) -> dict:
        lora = self.db.get("loras", job["target_id"])
        if lora is None:
            raise RuntimeError("the LoRA is gone")
        weight = float(job["params"].get("weight", EXAMPLE_LORA_WEIGHT))
        entry = {"id": lora["id"], "name": lora["name"], "path": lora["path"], "weight": weight,
                 "trigger": lora.get("trigger") or ""}
        items = self._render_examples("loras", lora["id"], {
            "with": {"base_model": lora["base_model"], "loras": [entry]},
            "without": {"base_model": lora["base_model"]}}, progress)
        examples = {"made_at": now(), "weight": weight, "items": items}
        self.db.update("loras", lora["id"], {"examples": examples})
        return examples

    def _job_profile_examples(self, job: dict, progress) -> dict:
        profile = self.db.get("profiles", job["target_id"])
        if profile is None:
            raise RuntimeError("the style profile is gone")
        settings = self.asset_settings({"profile_id": profile["id"], "overrides": {}})
        references = [self.settings.uploads_dir / "references" / r["file"] for r in settings["reference_images"]]
        items = self._render_examples("profiles", profile["id"], {
            "with": {"base_model": settings["base_model"], "loras": self._profile_loras(settings),
                     "suffix": settings.get("prompt_suffix"), "negative": settings.get("negative_prompt"),
                     "references": references}}, progress)
        examples = {"made_at": now(), "base_model": settings["base_model"], "items": items}
        self.db.update("profiles", profile["id"], {"examples": examples})
        return examples

    async def run_one(self, asset_id: str) -> None:
        asset = self.db.get("assets", asset_id)
        if asset is None or asset["status"] != "queued":
            return
        stage = asset["stage"]
        self.current = asset_id
        self.update(asset_id, status="running", step="Starting", progress=0.0, error=None)
        try:
            outputs = await asyncio.to_thread(self.run_stage, asset_id, stage)
        except StepBack as back:  # another model's turn first: queued again, behind the others
            if self.db.get("assets", asset_id) is None:
                shutil.rmtree(self.asset_dir(asset_id), ignore_errors=True)
            else:
                self.update(asset_id, status="queued", needs=back.needs, queued_at=now(), progress=0.0,
                            step=f"Waiting to {back.step} (the batch's other assets go first on this model)")
            return
        except Exception as exc:  # noqa: BLE001 - every failure is reported on the asset
            if self.db.get("assets", asset_id) is None:  # deleted while it ran
                shutil.rmtree(self.asset_dir(asset_id), ignore_errors=True)
                return
            log.exception("stage %s of %s failed", stage, asset_id)
            if await self._restart_and_retry(asset_id, stage, exc):
                return
            self.update(asset_id, status="failed", step=f"{stage} failed", error=f"{exc}",
                        error_detail=traceback.format_exc(limit=5))
            self.check_sets()
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
            self.autopilot(asset_id)
            self.check_sets()
        else:
            changes = {"outputs": all_outputs, "status": "done", "gate": None, "step": "Done", "progress": 1.0}
            if not asset.get("training_candidate_set"):
                changes["training_candidate"] = True  # approved assets are LoRA training candidates
            self.update(asset_id, **changes)
            self.check_sets()

    # ---- stages (run in the worker thread) ----------------------------------

    def run_stage(self, asset_id: str, stage: str) -> dict:
        directory = self.stage_dir(asset_id, stage)
        manifest = directory / "manifest.json"
        if manifest.is_file():  # finished before a restart: nothing to redo
            return json.loads(manifest.read_text(encoding="utf-8"))
        directory.mkdir(parents=True, exist_ok=True)
        asset = self._require(asset_id)
        progress = self._progress(asset_id, stage)
        prefix = "_effect" if asset["kind"] == "effect" else "_stage"
        outputs = getattr(self, f"{prefix}_{stage}")(asset, directory, progress)
        manifest.write_text(json.dumps(outputs, indent=2), encoding="utf-8")
        return outputs

    def _rel(self, asset_id: str, path: Path) -> str:
        # Tools may hand back absolute paths while the data dir is relative: compare both resolved.
        return Path(path).resolve().relative_to(self.asset_dir(asset_id).resolve()).as_posix()

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
            self._use("llm", settings)
            notes = [n["note"] for n in asset.get("notes") or [] if n["stage"] == "prompt"]
            expansion = self.adapters.llm.expand(
                description=asset["description"], kind=asset["kind"], archetypes=load_archetypes(),
                archetype_override=asset.get("archetype_override"), slot=asset.get("slot"),
                weapon_class=asset.get("weapon_class"), notes=notes, model=self._text_model(settings))
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
            self._turn(asset, settings, "llm", "judge the picture")
            return {"source": "upload", "expansion": expansion, "prompt": "", "negative_prompt": "",
                    "base_model": None, "loras": [], "attempt": attempt, "log": [],
                    "candidates": [self._rel(asset["id"], candidate)],
                    "pose_check": self._pose_check(asset, [candidate]),
                    "candidate_checks": self._candidate_checks(asset, [candidate])}
        suffix = settings.get("prompt_suffix") or ""
        if asset["kind"] in ("gear", "weapon"):  # an item alone: "full body" draws a person to wear it
            suffix = ", ".join(p.strip() for p in suffix.split(",") if p.strip().lower() not in ("full body", "full-body"))
        loras = self._profile_loras(settings)

        def prompts(expansion: dict) -> tuple[str, str]:
            piece = PAIR_PIECES[asset["slot"]] if self._pair(asset) else ""
            text = self.with_triggers(", ".join(p for p in (piece, expansion["prompt"], suffix) if p), loras)
            neg = ", ".join(p for p in (expansion.get("negative_prompt"), settings.get("negative_prompt"),
                                        PAIR_NEGATIVE if piece else "") if p)
            if self._posed_biped(asset):
                neg = ", ".join(p for p in (neg, POSE_NEGATIVE) if p)
            return text, neg

        prompt, negative = prompts(expansion)
        references = [self.settings.uploads_dir / "references" / r["file"] for r in settings["reference_images"]]

        def scaled(step, fraction):
            progress(step, 0.1 + 0.9 * fraction)

        stage_log: list[str] = []
        hints = self._hints(asset)
        if self._posed_biped(asset):
            hints["pose_guide"] = str(pose.guide_image(directory / "pose_guide.png"))

        def render(seed: int) -> list[Path]:
            self._use("image", settings)
            return self.adapters.image.render_candidates(
                prompt=prompt, negative_prompt=negative, base_model=settings["base_model"], loras=loras,
                reference_images=references, count=int(settings["candidates"]), seed=seed,
                out_dir=directory, progress=scaled, hints=hints, log=stage_log.append)

        # The first drawing is kept (drawn.json), so a stage that steps back for the judge's turn
        # does not draw again when it resumes.
        drawn_file = directory / "drawn.json"
        if drawn_file.is_file():
            drawn = json.loads(drawn_file.read_text(encoding="utf-8"))
            paths = [self.asset_dir(asset["id"]) / p for p in drawn["candidates"]]
            stage_log += drawn["log"]
        else:
            self._turn(asset, settings, "image", "draw the pictures")
            paths = render(seed_of(asset["id"], attempt))
            drawn_file.write_text(json.dumps({"candidates": [self._rel(asset["id"], p) for p in paths],
                                              "log": stage_log}, indent=2), encoding="utf-8")
        # Candidates that fail their checks are redrawn with other seeds, a few rounds at most.
        redrawn: list[int] = []
        retries = max(0, int(self.settings.auto_retries))
        for round_ in range(retries + 1):
            progress("Checking the candidates", 0.97)
            if round_ == 0:
                self._turn(asset, settings, "llm", "judge the pictures")
            else:
                self._use("llm", settings)
            candidate_checks = self._candidate_checks(asset, paths)
            bad = [c["candidate"] for c in candidate_checks if c["status"] == qc.FAIL]
            if not bad or round_ == retries:
                break
            for number in bad:
                paths[number - 1].unlink(missing_ok=True)
            redrawn += bad
            # The judge said what the pictures got wrong: the prompt is written again with that in mind.
            problems = self._judge_problems(candidate_checks, bad)
            if problems and round_ == 0 and not upload:
                progress("Rewriting the prompt with the judge's notes", 0.97)
                notes = [n["note"] for n in asset.get("notes") or [] if n["stage"] == "prompt"]
                try:
                    rewritten = self.adapters.llm.expand(
                        description=asset["description"], kind=asset["kind"], archetypes=load_archetypes(),
                        archetype_override=asset.get("archetype_override") or asset.get("archetype"),
                        slot=asset.get("slot"), weapon_class=asset.get("weapon_class"),
                        notes=[*notes, "The last pictures had these problems, avoid them: " + "; ".join(problems)],
                        model=self._text_model(settings))
                    expansion = {**expansion, "prompt": rewritten["prompt"],
                                 "negative_prompt": rewritten.get("negative_prompt") or expansion.get("negative_prompt")}
                    prompt, negative = prompts(expansion)
                    stage_log.append("Rewrote the prompt from the judge's notes: " + "; ".join(problems))
                except Exception:  # noqa: BLE001 - the redraw goes on with the old prompt
                    log.warning("rewriting the prompt failed", exc_info=True)
            paths = render(seed_of(asset["id"], attempt) + 1000 * (round_ + 1))
        if redrawn:
            stage_log.append(f"Redrew {len(redrawn)} candidate{'s' if len(redrawn) > 1 else ''} that failed the "
                             "checks (held items, several figures, cut off, arms against the body).")
        return {
                "candidate_checks": candidate_checks, "redrawn": len(redrawn),"expansion": expansion, "prompt": prompt, "negative_prompt": negative,
                "base_model": settings["base_model"], "loras": loras, "attempt": attempt,
                "log": stage_log, "candidates": [self._rel(asset["id"], p) for p in paths],
                "pose_check": self._pose_check(asset, paths)}

    @staticmethod
    def _text_model(settings: dict) -> str | None:
        """The language model the asset's profile picked (an LM Studio id), or None for the default."""
        return (settings.get("text_model") or "").strip() or None

    @staticmethod
    def _judge_problems(candidate_checks: list[dict], numbers: list[int]) -> list[str]:
        """What the judge said the given candidates got wrong (unique, in order)."""
        found: list[str] = []
        for entry in candidate_checks:
            if entry["candidate"] in numbers:
                for c in entry["checks"]:
                    if c["id"] == "match" and c["status"] != qc.PASS and ": " in c["detail"]:
                        found += [p.strip() for p in c["detail"].split(": ", 1)[1].split(";") if p.strip()]
        return list(dict.fromkeys(found))[:6]

    def _mesh_tool(self, settings: dict):
        """The image-to-3D adapter of the profile's mesh model."""
        model = (settings.get("mesh") or {}).get("model") or "hunyuan3d-2.1"
        tool = MESH_MODELS.get(model, MESH_MODELS["hunyuan3d-2.1"])["tool"]
        return tool, self.adapters.meshes.get(tool) or self.adapters.mesh

    def _judges_meshes(self) -> bool:
        return (getattr(self.adapters.blender, "render_views", None) is not None
                and getattr(self.adapters.llm, "judge_mesh", None) is not None)

    def _judge_mesh(self, asset: dict, settings: dict, source: Path, mesh_glb: Path, directory: Path,
                    progress) -> dict | None:
        """Auto mode's judge at gate 2: the mesh rendered from three sides, compared with the picture."""
        render = getattr(self.adapters.blender, "render_views", None)
        judge = getattr(self.adapters.llm, "judge_mesh", None)
        if render is None or judge is None:
            return None
        try:
            views = render(mesh_glb=mesh_glb, out_dir=directory / "judge", progress=progress)
            found = judge(picture=source, views=views, description=self._judged_description(asset),
                          kind=asset["kind"],
                          model=self._text_model(settings))
        except Exception:  # noqa: BLE001 - gate 2 goes on without the judge
            log.warning("judging the mesh of %s failed", asset["id"], exc_info=True)
            return None
        if found is None:
            return None
        return {**found, "views": [self._rel(asset["id"], v) for v in views]}

    @staticmethod
    def _pair(asset: dict) -> bool:
        """Gear worn on both sides (gloves, boots, leg armour): one piece is drawn and built, and it is
        mirrored for the other side when fitted."""
        return asset["kind"] == "gear" and asset.get("slot") in qc.PAIR_SLOTS

    def _judged_description(self, asset: dict) -> str:
        """The description the judge holds pictures and meshes to: for a pair slot, one piece of it."""
        if not self._pair(asset):
            return asset["description"]
        return (f"{asset['description']} (drawn as {PAIR_PIECES[asset['slot']]}: only one piece is wanted, "
                "the other side is its mirror image, made later)")

    @staticmethod
    def _posed_biped(asset: dict) -> bool:
        """Biped characters (not gear or weapons, which are drawn alone) get the A-pose guide and check."""
        return asset["kind"] in ("hero", "monster", "boss") and asset.get("archetype") == "biped"

    # ---- skill effects (kind "effect", see effects.py) -----------------------------------
    # prompt: the language model plans the parts; the main part is drawn as gate 1's candidates.
    # mesh:   every part is drawn in the picked look and animated (Wan 2.2) into a flipbook: gate 2.
    # rig:    the parts put together as the effect plays it: gate 3. export: the engine's files.

    def _effect_picture_prompts(self, asset: dict, settings: dict, part: dict, plan: dict) -> tuple[str, str, list]:
        loras = self._profile_loras(settings)
        colours = "colours " + ", ".join(plan["palette"])
        facing = "horizontal, pointing to the right, seen from the side" if part["part"] == "projectile" else ""
        text = self.with_triggers(", ".join(p for p in (part["picture"], facing, colours, effects.PICTURE_SUFFIX) if p),
                                  loras)
        negative = ", ".join(p for p in (effects.PICTURE_NEGATIVE, settings.get("negative_prompt")) if p)
        return text, negative, loras

    def _effect_judged(self, asset: dict, settings: dict, part: dict, paths: list[Path]) -> list[list[dict]]:
        """Each picture's checks: on black and something there, and (the vision model looking) one thing
        that matches the part's prompt."""
        inspect = getattr(self.adapters.llm, "inspect_effect", None)
        results = []
        for path in paths:
            vision = None
            if inspect is not None:
                try:
                    vision = inspect(image=Path(path), description=part["picture"], model=self._text_model(settings))
                except Exception:  # noqa: BLE001 - the checks go on without the vision model
                    log.warning("the vision check of %s failed", path, exc_info=True)
            if part["part"] == "projectile" and vision and vision.get("points") == "left":
                effects.mirror(Path(path))  # levelled already: it flies to the right
            results.append(effects.picture_checks(Path(path), vision))
        return results

    @staticmethod
    def _effect_tidy(part: dict, paths: list[Path], say) -> None:
        """Pictures not on black get their subject cut out onto black; a projectile is turned level."""
        for number, path in enumerate(paths, start=1):
            if effects.clean_picture(Path(path)):
                say(f"Picture {number}: the background wasn't black, so the {part['part']} was cut out onto black.")
            if part["part"] == "projectile":
                turned = effects.level(Path(path))
                if turned:
                    say(f"Picture {number}: turned {turned:g} degrees so the projectile lies level.")

    def _effect_prompt(self, asset: dict, directory: Path, progress) -> dict:
        settings = self.asset_settings(asset)
        notes = [n["note"] for n in asset.get("notes") or [] if n["stage"] == "prompt"]
        plan_file = directory / "plan.json"
        if plan_file.is_file():
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
        else:
            progress("Planning the effect", 0.0)
            self._use("llm", settings)
            raw = self.adapters.llm.effect_plan(description=asset["description"], notes=notes,
                                                model=self._text_model(settings))
            plan = effects.normalize_plan(raw, asset["description"])
            plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        attempt = (asset.get("attempts") or {}).get("prompt", 1)
        main = next(p for p in plan["parts"] if p["part"] == plan["main"])
        prompt, negative, loras = self._effect_picture_prompts(asset, settings, main, plan)
        self._turn(asset, settings, "image", "draw the pictures")
        stage_log: list[str] = []
        paths = self.adapters.image.render_candidates(
            prompt=prompt, negative_prompt=negative, base_model=settings["base_model"], loras=loras,
            reference_images=[], count=int(settings["candidates"]), seed=seed_of(asset["id"], attempt),
            out_dir=directory, progress=lambda s, f: progress(s, 0.1 + 0.85 * f), hints=self._hints(asset),
            log=stage_log.append)
        tidied = directory / "tidied.txt"
        if not tidied.is_file():  # once: a resumed stage doesn't turn them again
            self._effect_tidy(main, paths, stage_log.append)
            tidied.write_text("\n".join(stage_log), encoding="utf-8")
        else:
            stage_log += tidied.read_text(encoding="utf-8").splitlines()
        progress("Checking the candidates", 0.97)
        self._turn(asset, settings, "llm", "judge the pictures")
        checks = [{"candidate": n, "checks": found, "status": qc.worst(found)}
                  for n, found in enumerate(self._effect_judged(asset, settings, main, paths), start=1)]
        return {"plan": plan, "prompt": prompt, "negative_prompt": negative, "base_model": settings["base_model"],
                "loras": loras, "attempt": attempt, "log": stage_log, "pose_check": None, "redrawn": 0,
                "expansion": {"prompt": main["picture"], "flags": []},
                "candidates": [self._rel(asset["id"], p) for p in paths], "candidate_checks": checks}

    def _effect_mesh(self, asset: dict, directory: Path, progress) -> dict:
        settings = self.asset_settings(asset)
        plan = asset["outputs"]["prompt"]["plan"]
        attempt = (asset.get("attempts") or {}).get("mesh", 1)
        chosen = self.stage_dir(asset["id"], "prompt") / f"candidate_{asset['chosen_candidate']}.png"
        # A part made again on its own (redo_effect_part) gets a new seed and its notes.
        tries = asset.get("part_attempts") or {}
        wishes = asset.get("part_notes") or {}

        def noted(part: dict) -> dict:
            asked = wishes.get(part["part"]) or {}
            picture = ", ".join([part["picture"], *asked.get("draw", [])])
            motion = ", ".join([part["motion"], *asked.get("animate", [])])
            return {**part, "picture": picture, "motion": motion}

        def again(part: dict) -> int:
            return 1000 * (int(tries.get(part["part"], 1)) - 1)

        parts = [noted(p) for p in plan["parts"]]
        # 1. A picture of every part: the picked candidate is the main one, the others are drawn like it.
        pictures: dict[str, Path] = {}
        for index, part in enumerate(parts):
            folder = directory / part["part"]
            folder.mkdir(parents=True, exist_ok=True)
            picture = folder / "picture.png"
            if not picture.is_file():
                if part["part"] == plan["main"] and not (folder / "redraw").is_file():
                    shutil.copy2(chosen, picture)
                else:
                    self._use("image", settings)
                    prompt, negative, loras = self._effect_picture_prompts(asset, settings, part, plan)
                    drawn = self.adapters.image.render_candidates(
                        prompt=prompt, negative_prompt=negative, base_model=settings["base_model"], loras=loras,
                        reference_images=[], count=3, seed=seed_of(asset["id"], attempt) + 10 * index + again(part), out_dir=folder / "draw",
                        progress=lambda s, f, i=index: progress(f"Drawing the {parts[i]['part']}: {s}",
                                                                0.3 * (i + f) / len(parts)),
                        hints={**self._hints(asset), "asset_id": f"{asset['id']}_{part['part']}"})
                    self._effect_tidy(part, drawn, lambda line: None)
                    self._use("llm", settings)
                    judged = self._effect_judged(asset, settings, part, drawn)
                    best = min(range(len(drawn)), key=lambda i: (qc.ORDER[qc.worst(judged[i])],
                                                                 sum(qc.ORDER[c["status"]] for c in judged[i])))
                    shutil.copy2(drawn[best], picture)  # of three, the one the checks like best
                    (folder / "checks.json").write_text(json.dumps(judged[best]), encoding="utf-8")
            pictures[part["part"]] = picture
        # 2. Each picture animated (Wan 2.2) and made a flipbook.
        if any(not (directory / p["part"] / "flipbook" / "frame_00.png").is_file() for p in parts):
            self._turn(asset, settings, "video", "animate the parts")
        made = []
        for index, part in enumerate(parts):
            folder = directory / part["part"]
            book = sorted((folder / "flipbook").glob("frame_*.png"))
            if not book:
                self._use("video", settings)
                frames = self.adapters.image.animate_frames(
                    image=pictures[part["part"]], prompt=f"{part['motion']}, {effects.MOTION_SUFFIX}",
                    negative_prompt=effects.MOTION_NEGATIVE, frames=effects.VIDEO_FRAMES, size=effects.VIDEO_SIZE,
                    seed=seed_of(asset["id"], attempt) + 100 + index + again(part), out_dir=folder / "video",
                    progress=lambda s, f, i=index: progress(f"Animating the {parts[i]['part']}: {s}",
                                                            0.3 + 0.65 * (i + f) / len(parts)))
                book = effects.make_flipbook(frames, folder / "flipbook", loop=part["loop"])
            made.append({**part, "picture": self._rel(asset["id"], pictures[part["part"]]),
                         "frames": [self._rel(asset["id"], p) for p in book]})
        main = next(p for p in made if p["part"] == plan["main"])
        icon = directory / "icon.png"
        with Image.open(self.asset_dir(asset["id"]) / main["frames"][len(main["frames"]) // 2]) as frame:
            frame.copy().save(icon)
        # The other parts' pictures get gate 1's checks here (a part filling its square has hard edges).
        def kept(part: dict) -> list[dict]:
            saved = directory / part["part"] / "checks.json"
            if saved.is_file():
                return json.loads(saved.read_text(encoding="utf-8"))
            return effects.picture_checks(self.asset_dir(asset["id"]) / part["picture"])

        drawn_checks = [{**c, "id": f"{p['part']}_{c['id']}", "label": f"{p['part'].title()}: {c['label']}"}
                        for p in made if p["part"] != plan["main"] for c in kept(p)]
        return {"plan": plan, "parts": made, "fps": effects.FPS, "frame_size": effects.FRAME_SIZE,
                "icon": self._rel(asset["id"], icon),
                "checks": effects.flipbook_checks(made, self.asset_dir(asset["id"])) + drawn_checks}

    def _effect_rig(self, asset: dict, directory: Path, progress) -> dict:
        mesh = asset["outputs"]["mesh"]
        plan = mesh["plan"]
        have = {p["part"] for p in mesh["parts"]}
        missing = [p for p in effects.NEEDS[plan["kind"]] if p not in have]
        checks = [qc.check("parts", "Every part", qc.FAIL if missing else qc.PASS,
                           f"missing: {', '.join(missing)}" if missing else ", ".join(sorted(have)))]
        return {"sequence": {"kind": plan["kind"], "travel_speed": plan["travel_speed"], "particles": plan["particles"],
                             "palette": plan["palette"],
                             "order": [p for p in ("cast", "projectile", "impact", "aura") if p in have]},
                "clips": [], "checks": checks}

    def _effect_export(self, asset: dict, directory: Path, progress) -> dict:
        project = self.db.get("projects", asset["project_id"])
        if project is None:
            raise RuntimeError("the asset's project no longer exists")
        mesh = asset["outputs"]["mesh"]
        engine = project["engine"]
        name = self._claim_export_name(asset, project)
        build = directory / engine / name
        root = self.asset_dir(asset["id"])
        progress("Packing the flipbooks", 0.3)
        if engine == "unity":
            effects.write_unity(out_dir=build, name=name, asset=asset, plan=mesh["plan"], parts=mesh["parts"], root=root)
        else:
            effects.write_godot(out_dir=build, res_dir=f"res://generated/effect/{name}", name=name, asset=asset,
                                plan=mesh["plan"], parts=mesh["parts"], root=root)
        result: dict = {"engine": engine, "name": name}
        if mesh.get("icon"):
            shutil.copy2(root / mesh["icon"], build / f"{name}_icon.png")
            result["icon"] = f"{name}_icon.png"
        relative = self._export_parent(engine, "effect") / name
        progress("Zipping", 0.8)
        zip_path = exporters.make_zip(build, directory / f"{name}.zip", name)
        installed = None
        if project.get("path"):
            destination = Path(project["path"]).expanduser() / relative
            progress("Copying into the game project", 0.9)
            exporters.install(build, destination, asset["id"])
            installed = str(destination)
        result.update(folder=self._rel(asset["id"], build), zip=self._rel(asset["id"], zip_path),
                      installed_to=installed, project_relative=relative.as_posix(),
                      files=sorted(p.relative_to(build).as_posix() for p in build.rglob("*") if p.is_file()),
                      exported_at=now())
        return result

    # ---- colour variants ------------------------------------------------------------

    def make_variant(self, source_id: str, *, name: str | None, colours: dict) -> dict:
        """A copy of a rigged asset with its texture recoloured (see ``variants``), waiting at gate 3:
        the mesh, rig and clips are kept, so it takes seconds."""
        source = self._require(source_id)
        outputs = dict(source.get("outputs") or {})
        if source["kind"] == "effect":
            raise Invalid("skill effects have no colour variants yet")
        if not outputs.get("rig"):
            raise Invalid("only assets rigged at gate 3 can have colour variants")
        if source["status"] in ("queued", "running"):
            raise Invalid("wait until the asset stops at a gate or is done")
        outputs.pop("export", None)
        outputs["rig"] = {k: v for k, v in outputs["rig"].items() if k != "sprites"}
        skip = {"id", "created_at", "updated_at", "export_name", "fits", "autopilot_log", "batch_id", "notes"}
        record = {k: v for k, v in source.items() if k not in skip}
        approvals = {k: v for k, v in (source.get("approvals") or {}).items() if k in ("1", "2")}
        record.update(name=name or f"{source['name']} variant", outputs=outputs, approvals=approvals,
                      status="waiting", stage="rig", gate=3, step="Waiting at gate 3", progress=1.0, error=None,
                      queued_at=now(), autopilot=False, training_candidate=False, training_candidate_set=False,
                      variant_of={"id": source_id, "name": source["name"], **colours})
        asset = self.db.insert("assets", record)
        shutil.copytree(self.asset_dir(source_id), self.asset_dir(asset["id"]),
                        ignore=shutil.ignore_patterns("export", "sprites", "fits", "unirig", "*_thumb*.jpg", "*.preview.glb"))
        recolour = [self.asset_dir(asset["id"]) / rel for rel in (
            (outputs.get("mesh") or {}).get("texture"), (outputs.get("mesh") or {}).get("glb"),
            outputs["rig"].get("glb"), *((outputs.get("prompt") or {}).get("candidates") or [])) if rel]
        for path in recolour:
            if path.is_file():
                variants.recolour_file(path, **colours)
        self.publish_status(asset)
        return asset

    # ---- autopilot -------------------------------------------------------------------

    def autopilot(self, asset_id: str) -> None:
        """Auto mode. With autopilot on, the gate the asset waits at is decided by its checks, the
        judge's included (does the picture match the description, does the mesh look like the picture):

        * gate 1: the best candidate (fewest problems, then the judge's score); none usable: redraw
          with the judge's notes (``AUTO_REDRAWS`` times), then stop.
        * gate 2: approve; something failed: rebuild the mesh with a new seed (``AUTO_MESH_RETRIES``),
          then build it from the next-best picture (``AUTO_PICTURES`` in all), then redraw gate 1.
        * gate 3: approve (export) only with ``autopilot_export``; otherwise stop there for the user.
          A failed check stops it too.
        """
        asset = self.db.get("assets", asset_id)
        if not asset or not asset.get("autopilot") or asset["status"] != "waiting":
            return
        gate = asset["gate"]
        outputs = asset.get("outputs") or {}
        auto = dict(asset.get("auto") or {})
        if gate == 1:
            ranked = sorted(outputs.get("prompt", {}).get("candidate_checks") or [],
                            key=lambda c: (qc.ORDER[c["status"]], -self._match(c), c["candidate"]))
            usable = [c for c in ranked if c["status"] != qc.FAIL]
            if not usable:
                checks = outputs.get("prompt", {}).get("candidate_checks") or []
                problems = self._judge_problems(checks, [c["candidate"] for c in checks])
                if auto.get("redraws", 0) < AUTO_REDRAWS:
                    self._autopilot_redraw(asset, auto, "every candidate failed its checks", problems)
                else:
                    self._autopilot_stop(asset, gate, "every candidate failed its checks, after a redraw")
                return
            candidate = usable[0]["candidate"]
            score = self._match(usable[0])
            reason = (f"picked candidate {candidate}"
                      + (f" (judge {score}/10)" if score >= 0 else "")
                      + ("" if usable[0]["status"] == qc.PASS else ", checks: look"))
            self.db.update("assets", asset_id, {"auto": {**auto, "ranking": [c["candidate"] for c in usable],
                                                         "tried": [candidate], "mesh_retries": 0}})
            self._autopilot_log(asset, gate, "approved", reason)
            self.approve(asset_id, gate, candidate)
            return

        checks = outputs.get({2: "mesh", 3: "rig"}[gate], {}).get("checks") or []
        failed = [c for c in checks if c["status"] == qc.FAIL]
        why = "; ".join(f"{c['label']}: {c['detail']}" for c in failed)
        if gate == 2 and failed:
            tried = list(auto.get("tried") or [asset.get("chosen_candidate")])
            left = [c for c in auto.get("ranking") or [] if c not in tried]
            if auto.get("mesh_retries", 0) < AUTO_MESH_RETRIES:
                self.db.update("assets", asset_id, {"auto": {**auto, "mesh_retries": auto.get("mesh_retries", 0) + 1}})
                self._autopilot_log(asset, gate, "rebuilt", f"{why}; building the mesh again with a new seed")
                self.redo(asset_id, "mesh", f"Autopilot: {why}")
            elif left and len(tried) < AUTO_PICTURES:
                self.db.update("assets", asset_id, {"chosen_candidate": left[0],
                                                    "auto": {**auto, "tried": [*tried, left[0]], "mesh_retries": 0}})
                self._autopilot_log(asset, gate, "rebuilt", f"{why}; building the mesh from candidate {left[0]}")
                self.redo(asset_id, "mesh", f"Autopilot: {why}")
            elif auto.get("redraws", 0) < AUTO_REDRAWS:
                problems = (outputs.get("mesh", {}).get("judge") or {}).get("problems") or []
                self._autopilot_redraw(asset, auto, f"no picture gave a good mesh ({why})", problems)
            else:
                self._autopilot_stop(asset, gate, why)
            return
        if failed:
            self._autopilot_stop(asset, gate, why)
            return
        if gate == 3 and not asset.get("autopilot_export"):
            self._autopilot_log(asset, gate, "stopped", "everything passed; exporting is yours (or turn on export "
                                                        "in autopilot)")
            self.update(asset_id, step="Autopilot finished: ready for you at gate 3")
            return
        self._autopilot_log(asset, gate, "approved",
                            "checks passed" if qc.worst(checks) == qc.PASS else "checks: look, nothing failed")
        self.approve(asset_id, gate)

    @staticmethod
    def _match(candidate: dict) -> int:
        """The judge's 0-10 score of a gate 1 candidate (-1 when it wasn't judged)."""
        for c in candidate.get("checks") or []:
            if c["id"] == "match":
                try:
                    return int(c["detail"].split("/", 1)[0])
                except ValueError:
                    return -1
        return -1

    def _autopilot_redraw(self, asset: dict, auto: dict, reason: str, problems: list[str]) -> None:
        self.db.update("assets", asset["id"], {"auto": {**auto, "redraws": auto.get("redraws", 0) + 1,
                                                        "tried": [], "mesh_retries": 0}})
        note = "Autopilot: " + reason + (". Avoid: " + "; ".join(problems) if problems else "")
        self._autopilot_log(asset, asset["gate"], "redrew", f"{reason}; drawing new pictures")
        self.redo(asset["id"], "prompt", note)

    def redo(self, asset_id: str, stage: str, note: str | None = None) -> dict:
        """Runs ``stage`` again from scratch, whatever gate the asset waits at (auto mode): the note
        is kept like a rejection's, the attempt goes up (new seeds) and later outputs are dropped."""
        asset = self._require(asset_id)
        if asset["status"] in ("queued", "running"):
            raise Conflict(f"asset is {asset['status']}; stop it first")
        notes = list(asset.get("notes") or [])
        if note:
            notes.append({"gate": GATE_OF_STAGE.get(stage), "stage": stage, "note": note, "at": now()})
        attempts = dict(asset.get("attempts") or {})
        attempts[stage] = attempts.get(stage, 1) + 1
        outputs = {k: v for k, v in (asset.get("outputs") or {}).items() if k not in STAGES[STAGES.index(stage):]}
        changes = {"notes": notes, "attempts": attempts, "outputs": outputs}
        if stage == "prompt":
            changes["chosen_candidate"] = None
        self.db.update("assets", asset_id, changes)
        self._clear_from(asset_id, stage)
        return self.enqueue(asset_id, stage)

    def redo_effect_part(self, asset_id: str, part: str, mode: str = "animate", note: str | None = None) -> dict:
        """Makes one part of an effect again, keeping the others: ``animate`` keeps its picture and
        animates it again, ``draw`` draws a new picture too. A note is added to that part's picture
        (draw) or motion (animate) prompt. The effect goes back through gate 2."""
        asset = self._require(asset_id)
        if asset["kind"] != "effect":
            raise Invalid("only effects have parts")
        if mode not in ("animate", "draw"):
            raise Invalid("mode is animate or draw")
        if asset["status"] in ("queued", "running"):
            raise Conflict(f"the effect is {asset['status']}; stop it first")
        mesh = (asset.get("outputs") or {}).get("mesh")
        if not mesh:
            raise Conflict("the effect's parts aren't made yet (gate 2)")
        names = [p["part"] for p in mesh["plan"]["parts"]]
        if part not in names:
            raise Invalid(f"the effect has no {part!r} part (it has {', '.join(names)})")
        folder = self.stage_dir(asset_id, "mesh") / part
        (folder.parent / "manifest.json").unlink(missing_ok=True)  # else the finished stage isn't run again
        if mode == "draw":
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "redraw").write_text("drawn again, not the gate 1 pick", encoding="utf-8")
        else:
            for name in ("flipbook", "video"):
                shutil.rmtree(folder / name, ignore_errors=True)
        tries = dict(asset.get("part_attempts") or {})
        tries[part] = int(tries.get(part, 1)) + 1
        wishes = {k: dict(v) for k, v in (asset.get("part_notes") or {}).items()}
        if note and note.strip():
            asked = wishes.setdefault(part, {})
            asked[mode] = [*asked.get(mode, []), note.strip()]
        outputs = {k: v for k, v in (asset.get("outputs") or {}).items() if k not in STAGES[STAGES.index("mesh"):]}
        self.db.update("assets", asset_id, {"part_attempts": tries, "part_notes": wishes, "outputs": outputs})
        for name in (*STAGES[STAGES.index("mesh") + 1:], "sprites"):
            shutil.rmtree(self.stage_dir(asset_id, name), ignore_errors=True)
        verb = "Drawing" if mode == "draw" else "Animating"
        return self.enqueue(asset_id, "mesh", step=f"Waiting for the worker ({verb.lower()} the {part} again)")

    def _autopilot_log(self, asset: dict, gate: int, action: str, reason: str) -> None:
        entries = [*(asset.get("autopilot_log") or []), {"gate": gate, "action": action, "reason": reason, "at": now()}]
        self.db.update("assets", asset["id"], {"autopilot_log": entries})

    def _autopilot_stop(self, asset: dict, gate: int, reason: str) -> None:
        self._autopilot_log(asset, gate, "stopped", reason)
        self.update(asset["id"], step=f"Autopilot stopped at gate {gate}: {reason}")

    def set_autopilot(self, asset_id: str, value: bool, export: bool | None = None) -> dict:
        self._require(asset_id)
        self.update(asset_id, autopilot=bool(value), **({} if export is None else {"autopilot_export": bool(export)}))
        if value:
            self.autopilot(asset_id)
        return self._require(asset_id)

    def _candidate_checks(self, asset: dict, paths: list[Path]) -> list[dict]:
        """Gate 1's checks for each candidate (``checks.candidate_checks``); the vision model is asked
        what it sees, and left out when it can't look at images."""
        result = []
        for number, path in enumerate(paths, start=1):
            try:
                vision = self.adapters.llm.inspect(image=Path(path), kind=asset["kind"],
                                                   description=self._judged_description(asset),
                                                   model=self._text_model(self.asset_settings(asset)))
            except Exception:  # noqa: BLE001 - the checks go on without the vision model
                log.warning("the vision check of %s failed", path, exc_info=True)
                vision = None
            try:
                found = qc.candidate_checks(Path(path), kind=asset["kind"], posed_biped=self._posed_biped(asset),
                                            pair=self._pair(asset), vision=vision)
            except OSError:
                found = [qc.check("image", "Readable image", qc.FAIL, "The image could not be read")]
            result.append({"candidate": number, "checks": found, "status": qc.worst(found)})
        return result

    def _pose_check(self, asset: dict, paths: list[Path]) -> list[dict] | None:
        """Gate 1's arms check for each candidate of a biped character (see ``pose.arms_check``)."""
        if not self._posed_biped(asset):
            return None
        result = []
        for number, path in enumerate(paths, start=1):
            try:
                check = pose.arms_check(Path(path))
            except OSError:
                check = {"arms_clear": None, "clear_share": 0.0}
            result.append({"candidate": number, **check})
        return result

    def _profile_loras(self, settings: dict) -> list[dict]:
        result = []
        for entry in settings.get("loras") or []:
            lora = self.db.get("loras", entry["id"])
            if lora and lora["base_model"] == settings["base_model"]:
                result.append({"id": lora["id"], "name": lora["name"], "path": lora["path"],
                               "weight": entry.get("weight", 1.0), "trigger": lora.get("trigger") or ""})
        return result

    @staticmethod
    def with_triggers(prompt: str, loras: list[dict]) -> str:
        """The LoRAs' trigger words at the front of the prompt (each once): a LoRA trained on a word
        only shows fully when the prompt has it."""
        words = [lora["trigger"] for lora in loras if lora.get("trigger") and lora["trigger"].lower() not in prompt.lower()]
        return ", ".join([*words, prompt]) if words else prompt

    def _stage_mesh(self, asset: dict, directory: Path, progress) -> dict:
        settings = self.asset_settings(asset)
        chosen = self.stage_dir(asset["id"], "prompt") / f"candidate_{asset['chosen_candidate']}.png"
        source = directory / "input.png"
        shutil.copy2(chosen, source)
        raw = directory / "raw.glb"
        texture = directory / "texture.png"
        delight = bool(settings.get("delight", True))
        quality = dict(settings.get("mesh") or {})
        views = None
        tool, mesher = self._mesh_tool(settings)
        built = raw.is_file() and texture.is_file()
        if not built:
            self._use("mesh", settings)
        if quality.get("multi_view") and tool == "hunyuan3d" and not built:
            views = self._mesh_views(asset, source, directory, lambda s, f: progress(s, 0.25 * f))
        if not built:
            mesher.image_to_mesh(
                image=source, texture_size=int(settings["budgets"]["texture"][asset["kind"]]), out_path=raw,
                progress=lambda s, f: progress(s, (0.25 if views else 0.0) + (0.25 if views else 0.5) * f),
                hints=self._hints(asset), delight=delight, quality=quality,
                **({"views": {HUNYUAN_VIEW[v]: p for v, p in views.items() if v != "front"}} if views else {}))
        if self._judges_meshes():
            self._turn(asset, settings, "llm", "judge the mesh")
        triangles = int(settings["budgets"]["triangles"][asset["kind"]])
        result = self.adapters.blender.cleanup_and_lods(
            raw_mesh=raw, kind=asset["kind"], triangles=triangles, lod_ratios=list(settings["budgets"]["lod_ratios"]),
            out_dir=directory, progress=lambda s, f: progress(s, 0.5 + 0.4 * f),
            remove_floaters=bool(quality.get("remove_floaters", True)), height=asset.get("height"))
        progress("Judging the mesh against the picture", 0.9)
        judge = self._judge_mesh(asset, settings, source, result["glb"], directory,
                                 lambda s, f: progress(s, 0.9 + 0.1 * f))
        icon = imaging.make_icon(source, directory / "icon.png", pair=self._pair(asset))
        return {"glb": self._rel(asset["id"], result["glb"]), "texture": self._rel(asset["id"], texture),
                "icon": self._rel(asset["id"], icon),
                "triangle_budget": triangles, "lods": result["lods"], "delight": delight, "quality": quality,
                "removed": result.get("removed"), "stats": result.get("stats"),
                "views": sorted(self._rel(asset["id"], p) for p in (directory / "views").glob("*.png")
                                if p.stem in HUNYUAN_VIEW) if quality.get("multi_view") else None,
                "judge": judge,
                "checks": qc.mesh_checks(result.get("stats"), asset["kind"], judge=judge)}

    def _stage_rig(self, asset: dict, directory: Path, progress) -> dict:
        settings = self.asset_settings(asset)
        archetype = get_archetype(asset["archetype"])
        mesh = self.stage_dir(asset["id"], "mesh") / "mesh.glb"
        clips = self.clips_for(asset, settings)
        attach = self._attach_for(asset)
        if attach:
            # Fitted on the base body, which is the template itself: nothing to predict.
            skeleton = {"joints": archetype["joints"], "fit_score": 1.0, "fallback": False}
        else:
            self._use("rig", settings)
            skeleton = self.adapters.rig.predict_skeleton(
                mesh=mesh, archetype=archetype, out_dir=directory, progress=lambda s, f: progress(s, 0.3 * f))
            skeleton = {**skeleton, "overrides": dict(asset.get("joint_overrides") or {}),
                        "twists": dict(asset.get("joint_twists") or {})}
        wearer = self._wearer(asset) if attach else None
        if wearer:
            attach["wearer"] = wearer
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
        if attach:
            output["wearer"] = {"id": wearer["id"], "name": wearer["name"]} if wearer else None
        output["stretch"] = result.get("stretch")
        output["checks"] = qc.rig_checks(skeleton=None if attach else skeleton, stretch=result.get("stretch"),
                                         gear=result.get("gear"), clips=None if attach else result["clips"])
        if not attach:
            self._drop_fits_on(asset["id"])
        if result.get("joints"):
            output["joints"] = result["joints"]
            output["joint_overrides"] = sorted(asset.get("joint_overrides") or {})
            output["joint_twists"] = dict(asset.get("joint_twists") or {})
        if result.get("gear"):
            output["gear"] = result["gear"]
        if result.get("weapon"):
            output["weapon"] = {k: v for k, v in result["weapon"].items() if not k.endswith("_matrix")}
            output["grip"] = self._found_grip(asset, attach, result["weapon"])
        if godot:
            sprites = self._render_sprites(asset, settings, result["glb"], result["clips"],
                                           lambda s, f: progress(s, 0.7 + 0.3 * f))
            output["sprites"] = {k: v for k, v in sprites.items() if k != "frames"}  # paths stay out of the db
        return output

    def wearers(self, project_id: str) -> list[dict]:
        """The project's characters gear and weapons can be fitted on: biped heroes with a rig."""
        return [a for a in self.db.list("assets", project_id=project_id)
                if a["kind"] == "hero" and a.get("archetype") == "biped" and (a.get("outputs") or {}).get("rig")
                and (self.asset_dir(a["id"]) / "rig" / "rigged.blend").is_file()]

    def _attach_for(self, asset: dict) -> dict | None:
        """How gear or a weapon goes on a body (None for characters)."""
        if asset["kind"] == "gear":
            return {"kind": "gear", "slot": asset["slot"]}
        if asset["kind"] == "weapon":
            grip = asset.get("grip") or dict(DEFAULT_GRIPS[asset["weapon_class"]])
            manual = grip.get("source") == "manual"
            return {"kind": "weapon", "weapon_class": asset["weapon_class"], "grip": grip,
                    "socket": grip.get("socket") or DEFAULT_GRIPS[asset["weapon_class"]]["socket"],
                    "grip_matrix": grips.to_blender(grip["position"], grip["rotation"]) if manual else None}
        return None

    def _wearer(self, asset: dict) -> dict | None:
        """The rigged hero gear or a weapon is fitted on at gate 3 (None: the base mannequin)."""
        return self._wearer_info(asset["wearer_id"]) if asset.get("wearer_id") else None

    def _wearer_info(self, wearer_id: str) -> dict:
        wearer = self.db.get("assets", wearer_id)
        blend = self.asset_dir(wearer_id) / "rig" / "rigged.blend"
        if wearer is None or not (wearer.get("outputs") or {}).get("rig") or not blend.is_file():
            raise RuntimeError("the wearer is gone or no longer rigged; pick another wearer, or re-rig it")
        return {"id": wearer["id"], "name": wearer["name"], "blend": str(blend.resolve()),
                "clips": wearer["outputs"]["rig"]["clips"]}

    def _found_grip(self, asset: dict, attach: dict, found: dict) -> dict:
        """The weapon's grip after the rig stage: the one given at gate 3, or the detected one (and,
        for two-handed weapons, the second hand's on ``weapon_l``). Kept on the asset for the export."""
        grip = dict(attach["grip"])
        if grip.get("source") != "manual" and found.get("grip_matrix"):
            grip.update(grips.to_unity(found["grip_matrix"]), socket=attach["socket"], source="auto")
        if found.get("second_grip_matrix"):
            grip["second"] = {"socket": "weapon_l", **grips.to_unity(found["second_grip_matrix"])}
        else:
            grip.pop("second", None)
        self.db.update("assets", asset["id"], {"grip": grip})
        return grip

    def _mesh_views(self, asset: dict, source: Path, directory: Path, progress) -> dict[str, Path]:
        """Right, back and left views of the picked candidate (MV-Adapter), for a multi-view mesh."""
        if self.adapters.views is None:
            raise RuntimeError("multi-view meshes need MV-Adapter, which is not set up")
        front = directory / "views" / "front_input.png"
        front.parent.mkdir(parents=True, exist_ok=True)
        if not front.is_file():  # MV-Adapter's script cuts it out with rembg
            shutil.copy2(source, front)
        prompt = ((asset.get("outputs") or {}).get("prompt") or {}).get("prompt") or asset["description"]
        self.adapters.image.unload()  # SDXL for the views needs the GPU memory ComfyUI holds
        return self.adapters.views.render_views(front=front, prompt=prompt[:300], seed=seed_of(asset["id"], 1),
                                                out_dir=directory / "views", progress=progress)

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
            out_dir=self.stage_dir(asset["id"], "sprites"), progress=progress,
            framing=dict(PAPER_DOLL_FRAMING) if self.paper_doll(asset) else None)
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
                rig_type=rig["rig_type"], out_path=directory / "fbx" / f"{name}.fbx", kind=asset["kind"],
                progress=lambda s, f: progress(s, 0.6 * f))
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
        icon = self.asset_dir(asset["id"]) / mesh["icon"] if mesh.get("icon") else None
        if icon is not None and icon.is_file():  # the inventory icon (a pair shows both pieces)
            shutil.copy2(icon, build / f"{name}_icon.png")
            result["icon"] = f"{name}_icon.png"
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
