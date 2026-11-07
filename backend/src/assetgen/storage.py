"""Disk use of the data folder, and cleaning what can be rebuilt or is no longer used."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .db import Database

CATEGORIES = {
    "unirig_scratch": "UniRig's working files (point clouds, FBX files, unused retries); its result is kept",
    "thumbnails": "Thumbnails and light preview models (made again when shown)",
    "orphans": "Files of assets that no longer exist",
    "stale_fits": "Items fitted on heroes that no longer exist",
    "old_examples": "Example sheets of deleted LoRAs and style profiles",
}


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _unirig_scratch(asset_dir: Path) -> list[Path]:
    work = asset_dir / "rig" / "unirig"
    if not work.is_dir():
        return []
    keep: set[Path] = set()
    record = asset_dir / "rig" / "skeleton.json"
    if record.is_file():
        used = json.loads(record.read_text(encoding="utf-8")).get("unirig_file")
        if used:
            keep.add(Path(used).resolve())
    found = []
    for path in work.rglob("*"):
        if path.is_dir() and path.name == "npz":
            found.append(path)
        elif path.is_file() and path.suffix.lower() == ".fbx":
            found.append(path)
    for retry in work.glob("seed_*"):
        if not any(k.is_relative_to(retry.resolve()) for k in keep):
            found.append(retry)
    return [p for p in found if not any(p.resolve() == k for k in keep)]


def _targets(data_dir: Path, db: Database) -> dict[str, list[Path]]:
    assets_dir = data_dir / "assets"
    ids = {a["id"] for a in db.list("assets")}
    found: dict[str, list[Path]] = {key: [] for key in CATEGORIES}
    if assets_dir.is_dir():
        for folder in assets_dir.iterdir():
            if not folder.is_dir():
                continue
            if folder.name not in ids:
                found["orphans"].append(folder)
                continue
            found["unirig_scratch"] += _unirig_scratch(folder)
            found["thumbnails"] += [*folder.rglob("*_thumb*.jpg"), *folder.rglob("*.preview.glb")]
            fits = folder / "fits"
            if fits.is_dir():
                found["stale_fits"] += [f for f in fits.iterdir() if f.is_dir() and f.name not in ids]
    examples = data_dir / "examples"
    for owner, table in (("loras", "loras"), ("profiles", "profiles")):
        folder = examples / owner
        if folder.is_dir():
            known = {r["id"] for r in db.list(table)}
            found["old_examples"] += [f for f in folder.iterdir() if f.is_dir() and f.name not in known]
    return found


def report(data_dir: Path, db: Database) -> dict:
    """Bytes in the data folder and in each cleanable category."""
    found = _targets(Path(data_dir), db)
    return {"total_bytes": _size(Path(data_dir)) if Path(data_dir).exists() else 0,
            "categories": [{"id": key, "label": label, "items": len(found[key]),
                            "bytes": sum(_size(p) for p in found[key])} for key, label in CATEGORIES.items()]}


def clean(data_dir: Path, db: Database, categories: list[str]) -> dict:
    found = _targets(Path(data_dir), db)
    freed = 0
    for key in categories:
        for path in found.get(key, []):
            freed += _size(path)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
    return {"freed_bytes": freed, **report(data_dir, db)}
