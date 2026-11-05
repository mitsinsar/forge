"""HTTP and WebSocket routes under /api."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Literal

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile,
                     WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse

from .archetypes import get_archetype, load_archetypes
from .db import Database, new_id, now
from .defaults import (BASE_MODELS, CAMERA_DIRECTIONS, CAMERA_PRESETS, ENGINES, GEAR_SLOTS, KINDS,
                       SPRITE_LOOKS, STAGES, WEAPON_ATTACKS, WEAPON_CLASSES, deep_merge, profile_defaults)
from .exporters import safe_name
from .pipeline import Conflict, Invalid, Pipeline
from .schemas import (AssetCreate, Kind, Approve, LoraRegister, LoraUpdate, ProfileCreate, ProfileUpdate,
                      ProjectCreate, ProjectUpdate, Reject, TrainingCandidate)

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

@router.get("/meta", tags=["meta"], summary="Adapter modes, vocabularies and style-profile defaults")
def meta(request: Request):
    pipeline = pipeline_of(request)
    return {
        "version": request.app.version,
        "adapters": pipeline.adapters.modes(),
        "profile_defaults": profile_defaults(),
        "kinds": KINDS, "engines": ENGINES, "base_models": BASE_MODELS, "camera_presets": CAMERA_PRESETS,
        "camera_directions": CAMERA_DIRECTIONS, "sprite_looks": SPRITE_LOOKS, "gear_slots": GEAR_SLOTS,
        "weapon_classes": WEAPON_CLASSES, "weapon_attacks": WEAPON_ATTACKS, "stages": STAGES,
        "gates": {"1": "prompt", "2": "mesh", "3": "rig"},
        "max_reference_images": MAX_REFERENCE_IMAGES,
        "worker": {"current": pipeline.current},
    }


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
    view = dict(profile)
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
    }
    return view


@router.get("/assets", tags=["assets"], summary="List assets with filters")
def list_assets(request: Request, project_id: str | None = None,
                kind: Kind | None = None,
                status: Literal["queued", "running", "waiting", "done", "failed"] | None = None,
                archetype: str | None = None, training_candidate: bool | None = None,
                q: str | None = Query(None, description="Text in the name or description"),
                db: Database = Depends(db_of)):
    assets = db.list("assets", order="created_at DESC", project_id=project_id, kind=kind, status=status,
                     archetype=archetype, training_candidate=training_candidate)
    if q:
        needle = q.lower()
        assets = [a for a in assets if needle in a["name"].lower() or needle in a["description"].lower()]
    return [asset_view(request, a) for a in assets]


def _default_name(description: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", description)
             if w.lower() not in ("a", "an", "the", "of", "with", "and")][:3]
    return " ".join(w.capitalize() for w in words) or "Asset"


@router.post("/assets", tags=["assets"], status_code=201, summary="Create an asset and queue its first stage")
def create_asset(request: Request, body: AssetCreate):
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
    overrides = body.overrides.model_dump(exclude_none=True) if body.overrides else {}
    if overrides.get("loras"):
        base = deep_merge(pipeline.asset_settings({"profile_id": profile_id}), overrides)
        _check_loras(db, base)
    asset = db.insert("assets", {
        "project_id": project["id"], "profile_id": profile_id, "kind": body.kind,
        "name": (body.name or "").strip() or _default_name(body.description), "description": body.description,
        "archetype_override": body.archetype, "archetype": body.archetype, "slot": body.slot,
        "weapon_class": body.weapon_class, "grip": None, "overrides": overrides,
        "status": "queued", "stage": "prompt", "gate": None, "step": "Waiting for the worker", "progress": 0.0,
        "error": None, "notes": [], "attempts": {}, "approvals": {}, "chosen_candidate": None, "outputs": {},
        "training_candidate": False, "training_candidate_set": False, "queued_at": now(),
    })
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
    asset = _run(lambda: pipeline.reject(asset_id, gate, body.note, body.archetype, grip))
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


@router.get("/assets/{asset_id}/files/{path:path}", tags=["assets"],
            summary="Download a file the pipeline made (candidates, GLBs, sprites, export files)")
def read_asset_file(request: Request, asset_id: str, path: str):
    pipeline = pipeline_of(request)
    _found(pipeline.db.get("assets", asset_id), "asset")
    root = pipeline.asset_dir(asset_id).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "file not found")
    media = "model/gltf-binary" if target.suffix == ".glb" else None
    return FileResponse(target, media_type=media)


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
