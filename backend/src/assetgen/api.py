"""HTTP and WebSocket routes under /api."""

from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Literal

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile,
                     WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse
from PIL import Image

from .archetypes import get_archetype, load_archetypes
from .db import Database, new_id, now
from .defaults import (BASE_MODELS, CAMERA_DIRECTIONS, CAMERA_PRESETS, ENGINES, GEAR_SLOTS, KINDS,
                       SPRITE_LOOKS, STAGES, WEAPON_ATTACKS, WEAPON_CLASSES, deep_merge, profile_defaults)
from .exporters import safe_name
from .pipeline import DUMMY, Conflict, Invalid, Pipeline
from . import delivery, storage, watchdog
from .tools import check_tools
from .schemas import (AssetCreate, BatchCreate, CleanRequest, Kind, Approve, ExamplesRequest, Toggle, VariantCreate, FitRequest, LoraRegister, LoraUpdate,
                      OutfitCreate, OutfitUpdate, ProfileCreate, ProfileUpdate, ProjectCreate, ProjectUpdate, Reject,
                      TrainingCandidate)

MAX_REFERENCE_IMAGES = 3
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
LORA_TYPES = {".safetensors", ".ckpt", ".pt"}

router = APIRouter(prefix="/api")


def pipeline_of(request: Request) -> Pipeline:
    return request.app.state.pipeline


def db_of(request: Request) -> Database:
    return request.app.state.pipeline.db


def _found(record: dict | None, what: str) -> dict:
    if record is None:
        raise HTTPException(404, f"{what} not found")
    return record


def _run(action):
    try:
        return action()
    except KeyError:
        raise HTTPException(404, "asset not found") from None
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except Invalid as exc:
        raise HTTPException(422, str(exc)) from None


# ---- meta -------------------------------------------------------------------

@router.get("/meta", tags=["meta"], summary="Vocabularies, style-profile defaults and the worker's current asset")
def meta(request: Request):
    pipeline = pipeline_of(request)
    return {
        "version": request.app.version,
        "profile_defaults": profile_defaults(),
        "kinds": KINDS, "engines": ENGINES, "base_models": BASE_MODELS, "camera_presets": CAMERA_PRESETS,
        "camera_directions": CAMERA_DIRECTIONS, "sprite_looks": SPRITE_LOOKS, "gear_slots": GEAR_SLOTS,
        "weapon_classes": WEAPON_CLASSES, "weapon_attacks": WEAPON_ATTACKS, "stages": STAGES,
        "gates": {"1": "prompt", "2": "mesh", "3": "rig"},
        "max_reference_images": MAX_REFERENCE_IMAGES,
        "worker": {"current": pipeline.current},
    }


@router.get("/tools", tags=["meta"], summary="Whether each external tool answers")
def tools(request: Request):
    return check_tools(request.app.state.settings)


@router.get("/archetypes", tags=["meta"], summary="List archetype templates")
def list_archetypes():
    return list(load_archetypes().values())


@router.get("/archetypes/{archetype_id}", tags=["meta"], summary="Get an archetype template")
def read_archetype(archetype_id: str):
    try:
        return get_archetype(archetype_id)
    except KeyError:
        raise HTTPException(404, "archetype not found") from None


# ---- projects ---------------------------------------------------------------

def _new_profile(db: Database, name: str, project_id: str | None, settings: dict) -> dict:
    record = deep_merge(profile_defaults(), settings)
    record.update(name=name, project_id=project_id, reference_images=[])
    return db.insert("profiles", record)


@router.get("/projects", tags=["projects"], summary="List projects")
def list_projects(db: Database = Depends(db_of)):
    return db.list("projects")


@router.post("/projects", tags=["projects"], status_code=201, summary="Register a game project")
def create_project(body: ProjectCreate, db: Database = Depends(db_of)):
    if body.default_profile_id:
        _found(db.get("profiles", body.default_profile_id), "profile")
    project = db.insert("projects", {**body.model_dump(), "id": new_id()})
    if not body.default_profile_id:
        profile = _new_profile(db, "Default", project["id"], {})
        project = db.update("projects", project["id"], {"default_profile_id": profile["id"]})
    return project


@router.post("/tools/{tool}/restart", tags=["tools"],
             summary="Restart a tool (ComfyUI, Hunyuan3D...) with its command from assetgen.toml, and wait for it")
async def restart_tool(request: Request, tool: str):
    settings = pipeline_of(request).settings
    if not watchdog.restartable(settings, tool):
        raise HTTPException(422, f"no restart command for {tool}: add [restart.{tool}] to assetgen.toml")
    try:
        return {"tool": tool, "result": await asyncio.to_thread(watchdog.restart_tool, settings, tool)}
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/storage", tags=["tools"], summary="Disk use of the data folder, and what can be cleaned")
def storage_report(request: Request):
    pipeline = pipeline_of(request)
    return storage.report(pipeline.settings.data_dir, pipeline.db)


@router.post("/storage/clean", tags=["tools"], summary="Delete the chosen cleanable categories")
def storage_clean(request: Request, body: CleanRequest):
    pipeline = pipeline_of(request)
    return storage.clean(pipeline.settings.data_dir, pipeline.db, body.categories)


@router.get("/projects/{project_id}/wearers", tags=["projects"],
            summary="The project's rigged biped heroes, which gear and weapons can be fitted on")
def project_wearers(request: Request, project_id: str):
    pipeline = pipeline_of(request)
    _found(pipeline.db.get("projects", project_id), "project")
    return [{"id": a["id"], "name": a["name"], "height": a.get("height")} for a in pipeline.wearers(project_id)]


@router.get("/projects/{project_id}", tags=["projects"], summary="Get a project")
def read_project(project_id: str, db: Database = Depends(db_of)):
    return _found(db.get("projects", project_id), "project")


@router.patch("/projects/{project_id}", tags=["projects"], summary="Update a project")
def update_project(project_id: str, body: ProjectUpdate, db: Database = Depends(db_of)):
    _found(db.get("projects", project_id), "project")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("default_profile_id"):
        _found(db.get("profiles", changes["default_profile_id"]), "profile")
    return db.update("projects", project_id, changes)


@router.delete("/projects/{project_id}", tags=["projects"], status_code=204,
               summary="Delete a project (only when it has no assets)")
def delete_project(project_id: str, db: Database = Depends(db_of)):
    _found(db.get("projects", project_id), "project")
    if db.list("assets", project_id=project_id):
        raise HTTPException(409, "delete the project's assets first")
    for profile in db.list("profiles", project_id=project_id):
        db.update("profiles", profile["id"], {"project_id": None})
    db.delete("projects", project_id)
    return Response(status_code=204)


# ---- style profiles ---------------------------------------------------------

def _check_loras(db: Database, profile: dict) -> None:
    for entry in profile.get("loras") or []:
        lora = db.get("loras", entry["id"])
        if lora is None:
            raise HTTPException(422, f"LoRA {entry['id']} not found")
        if lora["base_model"] != profile["base_model"]:
            raise HTTPException(422, f"LoRA {lora['name']!r} is for {lora['base_model']}, "
                                     f"the profile uses {profile['base_model']}")


def _profile_view(request: Request, profile: dict) -> dict:
    # Settings added since the profile was saved show with their defaults.
    view = deep_merge(profile_defaults(), profile)
    view["reference_images"] = [
        {**r, "url": str(request.url_for("read_reference", profile_id=profile["id"], ref_id=r["id"]))}
        for r in profile.get("reference_images", [])
    ]
    return view


@router.get("/profiles", tags=["profiles"], summary="List style profiles")
def list_profiles(request: Request, project_id: str | None = None, db: Database = Depends(db_of)):
    return [_profile_view(request, p) for p in db.list("profiles", project_id=project_id)]


@router.post("/profiles", tags=["profiles"], status_code=201, summary="Create a style profile")
def create_profile(request: Request, body: ProfileCreate, db: Database = Depends(db_of)):
    if body.project_id:
        _found(db.get("projects", body.project_id), "project")
    settings = body.model_dump(exclude_none=True, exclude={"name", "project_id"})
    candidate = deep_merge(profile_defaults(), settings)
    _check_loras(db, candidate)
    return _profile_view(request, _new_profile(db, body.name, body.project_id, settings))


@router.get("/profiles/{profile_id}", tags=["profiles"], summary="Get a style profile")
def read_profile(request: Request, profile_id: str, db: Database = Depends(db_of)):
    return _profile_view(request, _found(db.get("profiles", profile_id), "profile"))


@router.patch("/profiles/{profile_id}", tags=["profiles"], summary="Update a style profile")
def update_profile(request: Request, profile_id: str, body: ProfileUpdate, db: Database = Depends(db_of)):
    profile = _found(db.get("profiles", profile_id), "profile")
    changes = body.model_dump(exclude_none=True)
    if changes.get("project_id"):
        _found(db.get("projects", changes["project_id"]), "project")
    merged = deep_merge(profile, changes)
    _check_loras(db, merged)
    return _profile_view(request, db.update("profiles", profile_id, merged))


@router.delete("/profiles/{profile_id}", tags=["profiles"], status_code=204, summary="Delete a style profile")
def delete_profile(profile_id: str, request: Request, db: Database = Depends(db_of)):
    profile = _found(db.get("profiles", profile_id), "profile")
    if any(p.get("default_profile_id") == profile_id for p in db.list("projects")):
        raise HTTPException(409, "the profile is a project's default; pick another default first")
    folder = pipeline_of(request).settings.uploads_dir / "references"
    for ref in profile.get("reference_images", []):
        (folder / ref["file"]).unlink(missing_ok=True)
    db.delete("profiles", profile_id)
    return Response(status_code=204)


@router.post("/profiles/{profile_id}/references", tags=["profiles"], status_code=201,
             summary="Upload a reference image (at most 3 per profile)")
async def upload_reference(request: Request, profile_id: str, file: UploadFile = File(...),
                           db: Database = Depends(db_of)):
    profile = _found(db.get("profiles", profile_id), "profile")
    references = list(profile.get("reference_images", []))
    if len(references) >= MAX_REFERENCE_IMAGES:
        raise HTTPException(409, f"a profile holds at most {MAX_REFERENCE_IMAGES} reference images")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMAGE_TYPES:
        raise HTTPException(422, "reference images must be PNG, JPEG or WebP")
    ref_id = new_id()
    folder = pipeline_of(request).settings.uploads_dir / "references"
    folder.mkdir(parents=True, exist_ok=True)
    stored = f"{profile_id}_{ref_id}{suffix}"
    (folder / stored).write_bytes(await file.read())
    references.append({"id": ref_id, "file": stored, "filename": file.filename, "uploaded_at": now()})
    return _profile_view(request, db.update("profiles", profile_id, {"reference_images": references}))


@router.get("/profiles/{profile_id}/references/{ref_id}", tags=["profiles"], summary="Download a reference image")
def read_reference(request: Request, profile_id: str, ref_id: str, db: Database = Depends(db_of)):
    profile = _found(db.get("profiles", profile_id), "profile")
    ref = _found(next((r for r in profile.get("reference_images", []) if r["id"] == ref_id), None),
                 "reference image")
    return FileResponse(pipeline_of(request).settings.uploads_dir / "references" / ref["file"])


@router.delete("/profiles/{profile_id}/references/{ref_id}", tags=["profiles"],
               summary="Remove a reference image")
def delete_reference(request: Request, profile_id: str, ref_id: str, db: Database = Depends(db_of)):
    profile = _found(db.get("profiles", profile_id), "profile")
    references = profile.get("reference_images", [])
    ref = _found(next((r for r in references if r["id"] == ref_id), None), "reference image")
    (pipeline_of(request).settings.uploads_dir / "references" / ref["file"]).unlink(missing_ok=True)
    remaining = [r for r in references if r["id"] != ref_id]
    return _profile_view(request, db.update("profiles", profile_id, {"reference_images": remaining}))


# ---- LoRA library -----------------------------------------------------------

def _lora_view(lora: dict) -> dict:
    return {**lora, "file_exists": Path(lora["path"]).is_file()}


@router.get("/loras", tags=["loras"], summary="List LoRAs")
def list_loras(base_model: str | None = None, db: Database = Depends(db_of)):
    return [_lora_view(l) for l in db.list("loras", base_model=base_model)]


@router.post("/loras", tags=["loras"], status_code=201, summary="Register a LoRA by its file path")
def register_lora(body: LoraRegister, db: Database = Depends(db_of)):
    return _lora_view(db.insert("loras", {**body.model_dump(), "uploaded": False}))


@router.post("/loras/upload", tags=["loras"], status_code=201, summary="Upload a LoRA file")
async def upload_lora(request: Request, file: UploadFile = File(...), name: str = Form(""),
                      base_model: str = Form(...), tags: str = Form("", description="Comma-separated"),
                      licence: str = Form(""), commercial_use: bool = Form(False), source_url: str = Form(""),
                      db: Database = Depends(db_of)):
    if base_model not in BASE_MODELS:
        raise HTTPException(422, f"base_model must be one of {', '.join(BASE_MODELS)}")
    original = Path(file.filename or "lora.safetensors")
    if original.suffix.lower() not in LORA_TYPES:
        raise HTTPException(422, "LoRA files must be .safetensors, .ckpt or .pt")
    lora_id = new_id()
    folder = pipeline_of(request).settings.uploads_dir / "loras"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{lora_id}_{safe_name(original.stem)}{original.suffix.lower()}"
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    record = {"id": lora_id, "name": name or original.stem, "path": str(target.resolve()),
              "base_model": base_model, "tags": [t.strip() for t in tags.split(",") if t.strip()],
              "licence": licence, "commercial_use": commercial_use, "source_url": source_url, "uploaded": True}
    return _lora_view(db.insert("loras", record))


@router.get("/loras/{lora_id}", tags=["loras"], summary="Get a LoRA")
def read_lora(lora_id: str, db: Database = Depends(db_of)):
    return _lora_view(_found(db.get("loras", lora_id), "LoRA"))


@router.patch("/loras/{lora_id}", tags=["loras"], summary="Update a LoRA")
def update_lora(lora_id: str, body: LoraUpdate, db: Database = Depends(db_of)):
    lora = _found(db.get("loras", lora_id), "LoRA")
    changes = body.model_dump(exclude_unset=True)
    if "base_model" in changes and changes["base_model"] != lora["base_model"]:
        used = [p["name"] for p in db.list("profiles") if any(e["id"] == lora_id for e in p.get("loras", []))]
        if used:
            raise HTTPException(409, f"the LoRA is used by profiles: {', '.join(used)}")
    return _lora_view(db.update("loras", lora_id, changes))


@router.delete("/loras/{lora_id}", tags=["loras"], status_code=204, summary="Delete a LoRA")
def delete_lora(lora_id: str, db: Database = Depends(db_of)):
    lora = _found(db.get("loras", lora_id), "LoRA")
    for profile in db.list("profiles"):
        kept = [e for e in profile.get("loras", []) if e["id"] != lora_id]
        if len(kept) != len(profile.get("loras", [])):
            db.update("profiles", profile["id"], {"loras": kept})
    if lora.get("uploaded"):
        Path(lora["path"]).unlink(missing_ok=True)
    db.delete("loras", lora_id)
    return Response(status_code=204)


# ---- assets -----------------------------------------------------------------

def asset_view(request: Request, asset: dict) -> dict:
    """The stored asset plus URLs for every file the frontend shows."""
    view = {k: v for k, v in asset.items() if k not in ("training_candidate_set",)}

    def url(rel: str | None) -> str | None:
        return str(request.url_for("read_asset_file", asset_id=asset["id"], path=rel)) if rel else None

    outputs = asset.get("outputs") or {}
    prompt, mesh, rig, export = (outputs.get(s) or {} for s in ("prompt", "mesh", "rig", "export"))
    view["urls"] = {
        "candidates": [url(c) for c in prompt.get("candidates", [])],
        "chosen_image": url(prompt["candidates"][asset["chosen_candidate"] - 1])
        if asset.get("chosen_candidate") and prompt.get("candidates") else None,
        "mesh": url(mesh.get("glb")), "texture": url(mesh.get("texture")), "animated": url(rig.get("glb")),
        "zip": str(request.url_for("download_asset", asset_id=asset["id"])) if export.get("zip") else None,
        "export_files": [url(f"{export['folder']}/{f}") for f in export.get("files", [])] if export else [],
        "thumb": str(request.url_for("read_asset_thumb", asset_id=asset["id"])) if prompt.get("candidates") else None,
        # Gear and weapons fitted on other heroes (outfits): hero id -> animated GLB.
        "fits": {hero: url(fit["glb"]) for hero, fit in (asset.get("fits") or {}).items()},
    }
    return view


@router.get("/assets", tags=["assets"], summary="List assets with filters")
def list_assets(request: Request, project_id: str | None = None,
                kind: Kind | None = None,
                status: Literal["queued", "running", "waiting", "done", "failed"] | None = None,
                archetype: str | None = None, training_candidate: bool | None = None,
                q: str | None = Query(None, description="Text in the name or description"),
                batch_id: str | None = Query(None, description="Only the assets of this batch"),
                db: Database = Depends(db_of)):
    assets = db.list("assets", order="created_at DESC", project_id=project_id, kind=kind, status=status,
                     archetype=archetype, training_candidate=training_candidate)
    if q:
        needle = q.lower()
        assets = [a for a in assets if needle in a["name"].lower() or needle in a["description"].lower()]
    if batch_id:
        assets = [a for a in assets if a.get("batch_id") == batch_id]
    return [asset_view(request, a) for a in assets]


def _default_name(description: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", description)
             if w.lower() not in ("a", "an", "the", "of", "with", "and")][:3]
    return " ".join(w.capitalize() for w in words) or "Asset"


@router.post("/assets", tags=["assets"], status_code=201, summary="Create an asset and queue its first stage")
def create_asset(request: Request, body: AssetCreate):
    return _create_asset(request, body)


MAX_SOURCE_BYTES = 30 * 1024 * 1024


@router.post("/assets/from-image", tags=["assets"], status_code=201,
             summary="Create an asset from your own image (skips the generated previews)")
async def create_asset_from_image(request: Request, file: UploadFile = File(..., description="PNG, JPEG or WebP"),
                                  asset: str = Form(..., description="The same JSON as POST /assets")):
    try:
        body = AssetCreate.model_validate_json(asset)
    except ValueError as exc:
        raise HTTPException(422, f"asset: {exc}") from None
    data = await file.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise HTTPException(413, "the image is larger than 30 MB")
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if opened.format not in ("PNG", "JPEG", "WEBP"):
                raise HTTPException(422, "the image must be PNG, JPEG or WebP")
            image = opened.convert("RGBA")
    except (OSError, Image.DecompressionBombError):
        raise HTTPException(422, "the file is not a readable image") from None
    return _create_asset(request, body, source=image)


@router.post("/assets/batch", tags=["assets"], status_code=201,
             summary="Create several assets from one description (optionally varied by the LLM), as one batch")
def create_batch(request: Request, body: BatchCreate):
    pipeline = pipeline_of(request)
    descriptions = [body.asset.description] * body.count
    if body.vary and body.count > 1:
        try:
            descriptions = pipeline.adapters.llm.vary(description=body.asset.description, kind=body.asset.kind,
                                                       count=body.count)
        except Exception as exc:  # noqa: BLE001 - the user sees why
            raise HTTPException(502, f"The LLM could not write the variations: {exc}") from exc
    batch_id = uuid.uuid4().hex[:12]
    base = (body.asset.name or "").strip()
    created = []
    for number, description in enumerate(descriptions, start=1):
        asset = body.asset.model_copy(update={"description": description[:2000],
                                              "name": f"{base} {number}" if base else None})
        created.append(_create_asset(request, asset, batch_id=batch_id))
    return created


@router.post("/assets/{asset_id}/variants", tags=["assets"], status_code=201,
             summary="A colour variant: a copy of a rigged asset with its texture recoloured, at gate 3")
def make_variant(request: Request, asset_id: str, body: VariantCreate):
    pipeline = pipeline_of(request)
    _found(pipeline.db.get("assets", asset_id), "asset")
    colours = body.model_dump(exclude={"name"})
    return asset_view(request, _run(lambda: pipeline.make_variant(asset_id, name=body.name, colours=colours)))


@router.put("/assets/{asset_id}/autopilot", tags=["assets"],
            summary="Turn autopilot on or off (on: approves the current gate now if its checks allow)")
def set_autopilot(request: Request, asset_id: str, body: Toggle):
    pipeline = pipeline_of(request)
    _found(pipeline.db.get("assets", asset_id), "asset")
    return asset_view(request, pipeline.set_autopilot(asset_id, body.value))


def _create_asset(request: Request, body: AssetCreate, source: Image.Image | None = None,
                  batch_id: str | None = None) -> dict:
    pipeline = pipeline_of(request)
    db = pipeline.db
    project = _found(db.get("projects", body.project_id), "project")
    profile_id = body.profile_id or project.get("default_profile_id")
    if profile_id:
        _found(db.get("profiles", profile_id), "profile")
    if body.archetype is not None:
        if body.kind in ("gear", "weapon"):
            raise HTTPException(422, "gear and weapons always use the biped mannequin")
        if body.archetype not in load_archetypes():
            raise HTTPException(422, f"unknown archetype {body.archetype!r}")
    if body.slot and body.kind != "gear":
        raise HTTPException(422, "slot is only for gear")
    if body.weapon_class and body.kind != "weapon":
        raise HTTPException(422, "weapon_class is only for weapons")
    if body.height is not None and body.kind in ("gear", "weapon"):
        raise HTTPException(422, "height is only for characters; gear and weapons fit their wearer")
    overrides = body.overrides.model_dump(exclude_none=True) if body.overrides else {}
    if overrides.get("loras"):
        base = deep_merge(pipeline.asset_settings({"profile_id": profile_id}), overrides)
        _check_loras(db, base)
    asset = db.insert("assets", {
        "project_id": project["id"], "profile_id": profile_id, "kind": body.kind,
        "name": (body.name or "").strip() or _default_name(body.description), "description": body.description,
        "archetype_override": body.archetype, "archetype": body.archetype, "slot": body.slot,
        "weapon_class": body.weapon_class, "grip": None, "overrides": overrides, "height": body.height,
        "wearer_id": None, "autopilot": body.autopilot, "batch_id": batch_id,
        "status": "queued", "stage": "prompt", "gate": None, "step": "Waiting for the worker", "progress": 0.0,
        "error": None, "notes": [], "attempts": {}, "approvals": {}, "chosen_candidate": None, "outputs": {},
        "training_candidate": False, "training_candidate_set": False, "queued_at": now(),
        "source": "upload" if source is not None else "generated",
    })
    if source is not None:
        path = pipeline.source_image(asset["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        source.save(path)
    pipeline.enqueue(asset["id"], "prompt")
    return asset_view(request, db.get("assets", asset["id"]))


@router.get("/assets/{asset_id}", tags=["assets"], summary="Get an asset with its outputs and settings")
def read_asset(request: Request, asset_id: str):
    pipeline = pipeline_of(request)
    asset = _found(pipeline.db.get("assets", asset_id), "asset")
    view = asset_view(request, asset)
    view["settings"] = pipeline.asset_settings(asset)
    return view


@router.delete("/assets/{asset_id}", tags=["assets"], status_code=204, summary="Delete an asset and its files")
def delete_asset(request: Request, asset_id: str):
    if not pipeline_of(request).delete(asset_id):
        raise HTTPException(404, "asset not found")
    return Response(status_code=204)


@router.post("/assets/{asset_id}/gates/{gate}/approve", tags=["gates"],
             summary="Approve a gate (gate 1 needs the picked candidate) and queue the next stage")
def approve_gate(request: Request, asset_id: str, gate: int, body: Approve | None = None):
    pipeline = pipeline_of(request)
    asset = _run(lambda: pipeline.approve(asset_id, gate, (body or Approve()).candidate))
    return asset_view(request, asset)


@router.post("/assets/{asset_id}/gates/{gate}/reject", tags=["gates"],
             summary="Reject a gate with a note (and archetype at gate 1, grip at gate 3); reruns only that stage")
def reject_gate(request: Request, asset_id: str, gate: int, body: Reject | None = None):
    pipeline = pipeline_of(request)
    body = body or Reject()
    grip = body.grip.model_dump(exclude_none=True) if body.grip else None
    if body.joints and any(len(p) != 3 for p in body.joints.values()):
        raise HTTPException(422, "each joint needs three coordinates")
    if body.twists and any(abs(v) > 180 for v in body.twists.values()):
        raise HTTPException(422, "a twist is between -180 and 180 degrees")
    asset = _run(lambda: pipeline.reject(asset_id, gate, body.note, body.archetype, grip, body.joints,
                                         height=body.height, wearer=body.wearer, twists=body.twists))
    return asset_view(request, asset)


@router.post("/assets/{asset_id}/retry", tags=["assets"], summary="Retry a failed stage (resumes its files)")
def retry_asset(request: Request, asset_id: str):
    return asset_view(request, _run(lambda: pipeline_of(request).retry(asset_id)))


@router.post("/assets/{asset_id}/export", tags=["assets"],
             summary="Re-export a finished asset (overwrites the game-project folder)")
def reexport_asset(request: Request, asset_id: str):
    return asset_view(request, _run(lambda: pipeline_of(request).reexport(asset_id)))


@router.put("/assets/{asset_id}/training-candidate", tags=["assets"],
            summary="Flag or unflag an asset as a LoRA training candidate")
def set_training_candidate(request: Request, asset_id: str, body: TrainingCandidate):
    pipeline = pipeline_of(request)
    asset = _found(pipeline.db.get("assets", asset_id), "asset")
    if not asset.get("chosen_candidate") and not (asset.get("approvals") or {}).get("1"):
        raise HTTPException(409, "only assets approved at gate 1 can be training candidates")
    updated = pipeline.update(asset_id, training_candidate=body.value, training_candidate_set=True)
    return asset_view(request, updated)


@router.get("/assets/{asset_id}/download", tags=["assets"], summary="Download the export as a zip")
def download_asset(request: Request, asset_id: str):
    pipeline = pipeline_of(request)
    asset = _found(pipeline.db.get("assets", asset_id), "asset")
    export = (asset.get("outputs") or {}).get("export")
    if not export:
        raise HTTPException(404, "the asset has not been exported yet")
    path = pipeline.asset_dir(asset_id) / export["zip"]
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.get("/assets/{asset_id}/thumb", tags=["assets"],
            summary="A small JPEG of the asset's preview (the picked candidate, or the first) for galleries")
def read_asset_thumb(request: Request, asset_id: str,
                     candidate: int | None = Query(None, ge=1, description="This candidate (default: the picked one)"),
                     size: int = Query(384, ge=64, le=1024)):
    pipeline = pipeline_of(request)
    asset = _found(pipeline.db.get("assets", asset_id), "asset")
    candidates = ((asset.get("outputs") or {}).get("prompt") or {}).get("candidates") or []
    if not candidates:
        raise HTTPException(404, "no preview yet")
    number = candidate or asset.get("chosen_candidate") or 1
    source = pipeline.asset_dir(asset_id) / candidates[min(number, len(candidates)) - 1]
    if not source.is_file():
        raise HTTPException(404, "no preview yet")
    return delivery.send(request, delivery.thumbnail(source, size), "image/jpeg")


@router.get("/assets/{asset_id}/files/{path:path}", tags=["assets"],
            summary="Download a file the pipeline made (candidates, GLBs, sprites, export files)")
def read_asset_file(request: Request, asset_id: str, path: str,
                    preview: bool = Query(False, description="GLBs: a light copy with JPEG textures, for viewers")):
    pipeline = pipeline_of(request)
    _found(pipeline.db.get("assets", asset_id), "asset")
    root = pipeline.asset_dir(asset_id).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "file not found")
    if preview and target.suffix == ".glb":
        target = delivery.preview_glb(target)
    media = "model/gltf-binary" if target.suffix == ".glb" else None
    return delivery.send(request, target, media)


# ---- item fits, side jobs and examples --------------------------------------

@router.get("/dummy", tags=["assets"],
            summary="The test dummy (the biped base body with every clip): ready, or the job building it")
def read_dummy(request: Request):
    pipeline = pipeline_of(request)
    glb = pipeline.dummy_glb()
    clips_file = glb.parent / "clips.json"
    if glb.is_file() and clips_file.is_file():
        return {"id": DUMMY, "name": "Test dummy", "height": 1.8, "ready": True,
                "url": str(request.url_for("read_dummy_glb")) + f"?v={int(glb.stat().st_mtime)}",
                "clips": json.loads(clips_file.read_text(encoding="utf-8"))}
    return {"id": DUMMY, "name": "Test dummy", "height": 1.8, "ready": False, "job": pipeline.queue_job("dummy", DUMMY)}


@router.get("/dummy/animated.glb", tags=["assets"], summary="The test dummy's animated GLB")
def read_dummy_glb(request: Request):
    glb = pipeline_of(request).dummy_glb()
    if not glb.is_file():
        raise HTTPException(404, "the test dummy is not built yet: GET /api/dummy builds it")
    return delivery.send(request, glb, "model/gltf-binary")


@router.post("/assets/{asset_id}/fits", tags=["assets"], status_code=202,
             summary="Fit finished gear or a weapon on a rigged hero (queued job; kept per hero)")
def fit_item(request: Request, asset_id: str, body: FitRequest):
    pipeline = pipeline_of(request)
    item = _found(pipeline.db.get("assets", asset_id), "asset")
    if item["kind"] not in ("gear", "weapon"):
        raise HTTPException(422, "only gear and weapons are fitted on a hero")
    if not (item.get("outputs") or {}).get("mesh"):
        raise HTTPException(409, "the item has no mesh yet: approve its gate 2 first")
    if body.wearer_id != DUMMY and body.wearer_id not in {w["id"] for w in pipeline.wearers(item["project_id"])}:
        raise HTTPException(422, "the wearer must be a rigged biped hero of the same project, or the test dummy")
    return pipeline.queue_job("fit", asset_id, {"wearer_id": body.wearer_id})


@router.get("/jobs", tags=["jobs"], summary="List side jobs (item fits, example renders)")
def list_jobs(target_id: str | None = None, kind: str | None = None, status: str | None = None,
              db: Database = Depends(db_of)):
    return db.list("jobs", order="queued_at DESC", target_id=target_id, kind=kind, status=status)[:100]


@router.get("/jobs/{job_id}", tags=["jobs"], summary="Get a side job")
def read_job(job_id: str, db: Database = Depends(db_of)):
    return _found(db.get("jobs", job_id), "job")


@router.post("/loras/{lora_id}/examples", tags=["loras"], status_code=202,
             summary="Render the example sheet: three subjects with and without the LoRA (queued job)")
def lora_examples(request: Request, lora_id: str, body: ExamplesRequest | None = None):
    pipeline = pipeline_of(request)
    _found(pipeline.db.get("loras", lora_id), "LoRA")
    params = {"weight": body.weight} if body and body.weight is not None else {}
    return pipeline.queue_job("lora_examples", lora_id, params)


@router.post("/profiles/{profile_id}/examples", tags=["profiles"], status_code=202,
             summary="Render the example sheet: three subjects in this style (queued job)")
def profile_examples(request: Request, profile_id: str):
    pipeline = pipeline_of(request)
    _found(pipeline.db.get("profiles", profile_id), "profile")
    return pipeline.queue_job("profile_examples", profile_id)


@router.post("/loras/{lora_id}/example-images", tags=["loras"], status_code=201,
             summary="Add your own example image to a LoRA (PNG, JPEG or WebP)")
async def add_lora_image(request: Request, lora_id: str, file: UploadFile = File(...)):
    pipeline = pipeline_of(request)
    lora = _found(pipeline.db.get("loras", lora_id), "LoRA")
    data = await file.read()
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(413, "the image is larger than 30 MB")
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if opened.format not in ("PNG", "JPEG", "WEBP"):
                raise HTTPException(422, "the image must be PNG, JPEG or WebP")
            image = opened.convert("RGB")
    except (OSError, Image.DecompressionBombError):
        raise HTTPException(422, "the file is not a readable image") from None
    name = f"{uuid.uuid4().hex[:12]}.jpg"
    folder = pipeline.examples_dir("loras", lora_id) / "yours"
    folder.mkdir(parents=True, exist_ok=True)
    image.thumbnail((1024, 1024))
    image.save(folder / name, quality=90)
    images = [*(lora.get("example_images") or []), f"loras/{lora_id}/yours/{name}"]
    return _lora_view(pipeline.db.update("loras", lora_id, {"example_images": images}))


@router.delete("/loras/{lora_id}/example-images/{name}", tags=["loras"], summary="Remove one of your example images")
def remove_lora_image(request: Request, lora_id: str, name: str):
    pipeline = pipeline_of(request)
    lora = _found(pipeline.db.get("loras", lora_id), "LoRA")
    path = f"loras/{lora_id}/yours/{name}"
    images = [i for i in lora.get("example_images") or [] if i != path]
    (pipeline.examples_dir("loras", lora_id) / "yours" / Path(name).name).unlink(missing_ok=True)
    return _lora_view(pipeline.db.update("loras", lora_id, {"example_images": images}))


@router.get("/examples/{path:path}", tags=["loras"], summary="An example image (LoRA or style profile sheets)")
def read_example(request: Request, path: str):
    root = (pipeline_of(request).settings.data_dir / "examples").resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "file not found")
    return delivery.send(request, target)


# ---- outfits ----------------------------------------------------------------

def _check_outfit(pipeline, project_id: str, hero_id: str | None, items: list[str] | None) -> None:
    if hero_id is not None and hero_id != DUMMY:
        hero = _found(pipeline.db.get("assets", hero_id), "hero")
        if hero["project_id"] != project_id or hero["kind"] != "hero":
            raise HTTPException(422, "the hero must be a hero of the outfit's project")
    for item_id in items or []:
        item = _found(pipeline.db.get("assets", item_id), "item")
        if item["project_id"] != project_id or item["kind"] not in ("gear", "weapon"):
            raise HTTPException(422, "outfit items are gear and weapons of the outfit's project")


@router.get("/outfits", tags=["outfits"], summary="List saved outfits")
def list_outfits(project_id: str | None = None, db: Database = Depends(db_of)):
    return db.list("outfits", project_id=project_id)


@router.post("/outfits", tags=["outfits"], status_code=201, summary="Save an outfit (a hero and what it wears)")
def create_outfit(request: Request, body: OutfitCreate):
    pipeline = pipeline_of(request)
    _found(pipeline.db.get("projects", body.project_id), "project")
    _check_outfit(pipeline, body.project_id, body.hero_id, body.items)
    return pipeline.db.insert("outfits", body.model_dump())


@router.patch("/outfits/{outfit_id}", tags=["outfits"], summary="Change an outfit")
def update_outfit(request: Request, outfit_id: str, body: OutfitUpdate):
    pipeline = pipeline_of(request)
    outfit = _found(pipeline.db.get("outfits", outfit_id), "outfit")
    _check_outfit(pipeline, outfit["project_id"], body.hero_id, body.items)
    return pipeline.db.update("outfits", outfit_id, body.model_dump(exclude_none=True))


@router.delete("/outfits/{outfit_id}", tags=["outfits"], status_code=204, summary="Delete an outfit")
def delete_outfit(outfit_id: str, db: Database = Depends(db_of)):
    _found(db.get("outfits", outfit_id), "outfit")
    db.delete("outfits", outfit_id)
    return Response(status_code=204)


# ---- live events ------------------------------------------------------------

WS_DESCRIPTION = """Connect with a WebSocket to receive JSON events (a plain GET answers 426).

* `{"type": "hello"}` once, on connect.
* `{"type": "status", "asset_id", "status", "stage", "gate", "step", "progress", "error", "at", "asset"}`
  on every change of an asset: created, queued, running, waiting at gate N, done, failed, approve,
  reject, retry, re-export, training-candidate toggle. `asset` is the full record.
* `{"type": "progress", "asset_id", "status": "running", "stage", "step", "progress", ...}` while a
  stage runs; `progress` goes from 0 to 1.
* `{"type": "deleted", "asset_id", "at"}` when an asset is deleted.

`status` is one of queued, running, waiting (see `gate`), done, failed.
"""


@router.get("/ws", tags=["events"], summary="WebSocket: asset status and progress events",
            description=WS_DESCRIPTION, status_code=426)
def websocket_info():
    return Response("Connect with a WebSocket client.", status_code=426, headers={"Upgrade": "websocket"})


@router.websocket("/ws")
async def websocket_events(websocket: WebSocket):
    hub = websocket.app.state.hub
    await websocket.accept()
    queue = hub.subscribe()

    async def pump() -> None:
        while True:
            await websocket.send_json(await queue.get())

    await websocket.send_json({"type": "hello"})
    sender = asyncio.create_task(pump())
    try:
        while True:  # the client sends nothing we need; this only notices the disconnect
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        hub.unsubscribe(queue)
