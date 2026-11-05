"""Archetype templates. Each archetype is a folder holding archetype.json
(and, once the real tools are in, the skeleton .blend and its clips)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ARCHETYPES_DIR = Path(__file__).parent


@lru_cache
def load_archetypes() -> dict[str, dict]:
    result: dict[str, dict] = {}
    for folder in sorted(ARCHETYPES_DIR.iterdir()):
        spec = folder / "archetype.json"
        if spec.is_file():
            data = json.loads(spec.read_text(encoding="utf-8"))
            data["folder"] = folder.name
            data["has_skeleton_blend"] = (folder / "skeleton.blend").is_file()
            result[data["id"]] = data
    return result


def get_archetype(archetype_id: str) -> dict:
    return load_archetypes()[archetype_id]


def archetype_ids() -> list[str]:
    return list(load_archetypes())
