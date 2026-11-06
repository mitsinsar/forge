"""Engine exports: the Unity folder, the Godot sprite folder and the zip.

These run for real on both mock and real data: they only arrange files the
adapters produced (Pillow packs the atlases), so nothing here is mocked.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import uuid
import zipfile
from pathlib import Path

from PIL import Image

from .archetypes import get_archetype

WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}

# Body faces hidden under each gear slot (applied by the import script).
SLOT_BODY_MASK = {
    "head": ["head_top"], "chest": ["torso", "upper_arms"], "legs": ["upper_legs", "lower_legs"],
    "hands": ["hands"], "feet": ["feet"], "cape": [],
}


def safe_name(name: str) -> str:
    """A file and folder name that is valid on Windows, macOS and Linux."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_.") or "asset"
    if cleaned.upper() in WINDOWS_RESERVED:
        cleaned += "_"
    return cleaned[:64]


def _guid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"assetgen:{seed}").hex


def _texture_meta(guid: str) -> str:
    return (f"fileFormatVersion: 2\nguid: {guid}\nTextureImporter:\n  serializedVersion: 12\n"
            f"  mipmaps:\n    enableMipMap: 1\n  textureType: 0\n  sRGBTexture: 1\n")


def write_unity(*, out_dir: Path, name: str, asset: dict, fbx: Path, texture: Path, lods: list[dict],
                clips: list[dict], rig_type: str) -> dict:
    """Assets/Generated/<type>/<name>/ contents."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "Textures").mkdir(parents=True)
    shutil.copy2(fbx, out_dir / f"{name}.fbx")
    albedo = out_dir / "Textures" / f"{name}_Albedo.png"
    shutil.copy2(texture, albedo)
    texture_guid = _guid(f"{asset['id']}:albedo")
    albedo.with_name(albedo.name + ".meta").write_text(_texture_meta(texture_guid), encoding="utf-8")
    sidecar = {
        "generator": "assetgen",
        "id": asset["id"],
        "name": asset["name"],
        "kind": asset["kind"],
        "archetype": asset["archetype"],
        "rig": rig_type,
        "fbx": f"{name}.fbx",
        "prefab": f"{name}.prefab",
        "material": {"file": f"Materials/{name}.mat", "shader": "Universal Render Pipeline/Lit",
                     "albedo": f"Textures/{name}_Albedo.png"},
        "lods": [{"level": lod["level"], "ratio": lod["ratio"], "triangles": lod["triangles"]} for lod in lods],
        "clips": [{"name": c["name"], "loop": c["loop"], "duration": c["duration"]} for c in clips],
    }
    if asset["kind"] == "gear":
        sidecar["gear"] = {"slot": asset["slot"], "skeleton": "biped", "skinned": True,
                           "hide_body": SLOT_BODY_MASK.get(asset["slot"], []),
                           "bones": list(get_archetype("biped")["joints"])}
    if asset["kind"] == "weapon":
        sidecar["weapon"] = {"class": asset["weapon_class"], "socket": asset["grip"].get("socket", "weapon_r"),
                             "grip": asset["grip"], "prefab": f"{name}.prefab"}
    (out_dir / "asset.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return sidecar


def _pack(frames: list[Path], frame_size: int) -> tuple[Image.Image, int]:
    columns = max(1, math.ceil(math.sqrt(len(frames))))
    rows = math.ceil(len(frames) / columns)
    atlas = Image.new("RGBA", (columns * frame_size, rows * frame_size), (0, 0, 0, 0))
    for index, path in enumerate(frames):
        with Image.open(path) as frame:
            atlas.paste(frame.convert("RGBA"), ((index % columns) * frame_size, (index // columns) * frame_size))
    return atlas, columns


def _tres(res_dir: str, layer: str, atlases: dict[str, dict], fps: int, frame_size: int,
          loops: dict[str, bool]) -> str:
    ext, subs, animations = [], [], []
    sub_id = 0
    for ext_index, (direction, atlas) in enumerate(atlases.items(), start=1):
        ext_id = f"{ext_index}_{direction}"
        ext.append(f'[ext_resource type="Texture2D" path="{res_dir}/{atlas["file"]}" id="{ext_id}"]')
        for clip, (start, count) in atlas["clips"].items():
            frames = []
            for index in range(start, start + count):
                sub_id += 1
                x = (index % atlas["columns"]) * frame_size
                y = (index // atlas["columns"]) * frame_size
                subs.append(f'[sub_resource type="AtlasTexture" id="AtlasTexture_{sub_id}"]\n'
                            f'atlas = ExtResource("{ext_id}")\n'
                            f"region = Rect2({x}, {y}, {frame_size}, {frame_size})")
                frames.append('{\n"duration": 1.0,\n"texture": SubResource("AtlasTexture_%d")\n}' % sub_id)
            animations.append('{\n"frames": [%s],\n"loop": %s,\n"name": &"%s_%s",\n"speed": %.1f\n}'
                              % (", ".join(frames), "true" if loops[clip] else "false", clip, direction, fps))
    header = f'[gd_resource type="SpriteFrames" load_steps={len(ext) + sub_id + 1} format=3]'
    body = "\n\n".join([header, "\n".join(ext), *subs])
    return body + "\n\n[resource]\nanimations = [" + ", ".join(animations) + "]\n"


def _paper_doll(res_dir: str, layers: list[str], first_animation: str, pixel: bool) -> str:
    lines = [f"[gd_scene load_steps={len(layers) + 1} format=3]", ""]
    for index, layer in enumerate(layers, start=1):
        lines.append(f'[ext_resource type="SpriteFrames" path="{res_dir}/{layer}.tres" id="{index}_{layer}"]')
    lines += ["", '[node name="PaperDoll" type="Node2D"]']
    if pixel:
        lines.append("texture_filter = 1")
    for index, layer in enumerate(layers, start=1):
        lines += ["", f'[node name="{layer}" type="AnimatedSprite2D" parent="."]',
                  f'sprite_frames = ExtResource("{index}_{layer}")', f'animation = &"{first_animation}"']
    return "\n".join(lines) + "\n"


def write_godot(*, out_dir: Path, res_dir: str, name: str, asset: dict, layer: str,
                sprites: dict[str, dict[str, list[Path]]], clips: list[dict], frame_size: int, fps: int,
                camera_preset: str, mirrored: dict[str, str], look: str) -> dict:
    """res://generated/<type>/<name>/ contents: an atlas PNG and Aseprite JSON
    per layer and direction, a SpriteFrames .tres per layer and PaperDoll.tscn."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    loops = {c["name"]: c["loop"] for c in clips}
    atlases: dict[str, dict] = {}
    for direction, by_clip in sprites.items():
        frames, ranges = [], {}
        for clip in (c["name"] for c in clips):
            ranges[clip] = (len(frames), len(by_clip[clip]))
            frames += by_clip[clip]
        atlas, columns = _pack(frames, frame_size)
        file = f"{layer}_{direction}.png"
        atlas.save(out_dir / file)
        atlases[direction] = {"file": file, "columns": columns, "clips": ranges}
        aseprite = {
            "frames": {
                f"{layer}_{direction} {i}.png": {
                    "frame": {"x": (i % columns) * frame_size, "y": (i // columns) * frame_size,
                              "w": frame_size, "h": frame_size},
                    "rotated": False, "trimmed": False,
                    "spriteSourceSize": {"x": 0, "y": 0, "w": frame_size, "h": frame_size},
                    "sourceSize": {"w": frame_size, "h": frame_size},
                    "duration": round(1000 / fps),
                } for i in range(len(frames))
            },
            "meta": {
                "app": "assetgen", "image": file, "format": "RGBA8888",
                "size": {"w": atlas.width, "h": atlas.height}, "scale": "1",
                "frameTags": [{"name": clip, "from": start, "to": start + count - 1, "direction": "forward"}
                              for clip, (start, count) in ranges.items()],
            },
        }
        (out_dir / f"{layer}_{direction}.json").write_text(json.dumps(aseprite, indent=1), encoding="utf-8")
    (out_dir / f"{layer}.tres").write_text(_tres(res_dir, layer, atlases, fps, frame_size, loops),
                                           encoding="utf-8")
    first = f"{clips[0]['name']}_{next(iter(sprites))}"
    (out_dir / "PaperDoll.tscn").write_text(_paper_doll(res_dir, [layer], first, look == "pixel"),
                                            encoding="utf-8")
    sidecar = {
        "generator": "assetgen", "id": asset["id"], "name": asset["name"], "kind": asset["kind"],
        "archetype": asset["archetype"], "layer": layer, "layers": [layer],
        "camera_preset": camera_preset, "directions": list(sprites), "mirrored": mirrored,
        "frame_size": frame_size, "fps": fps, "look": look,
        "clips": [{"name": c["name"], "loop": c["loop"], "frames": atlases[next(iter(atlases))]["clips"][c["name"]][1]}
                  for c in clips],
        "sprite_frames": f"{res_dir}/{layer}.tres", "scene": f"{res_dir}/PaperDoll.tscn",
    }
    if asset["kind"] == "gear":
        sidecar["slot"] = asset["slot"]
    if asset["kind"] == "weapon":
        sidecar["weapon"] = {"class": asset["weapon_class"], "grip": asset["grip"]}
    (out_dir / "asset.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return sidecar


def folder_owner(folder: Path) -> str | None:
    """Id of the asset an export folder belongs to; None when the folder does
    not exist, "" when it exists but was not written by assetgen."""
    if not folder.exists():
        return None
    try:
        return str(json.loads((folder / "asset.json").read_text(encoding="utf-8"))["id"])
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def unity_importer_outputs(name: str) -> list[str]:
    """Files the Unity import script makes in an export folder. A re-export keeps
    them (and the .meta files Unity wrote), so the prefab is updated in place and
    scenes keep their references."""
    return [f"{name}.prefab", f"{name}.controller", f"Materials/{name}.mat", "Materials"]


def install(src: Path, dest: Path, asset_id: str, keep: list[str] | None = None) -> None:
    """Copies an export folder into the game project, replacing only the
    asset's own earlier export. Refuses to touch another asset's folder.

    With ``keep`` (paths relative to the folder) the folder is updated instead
    of replaced: the new files overwrite the old ones, files of the earlier
    export that are gone are removed, and the ``keep`` files and the ``.meta``
    file of every file or folder still there stay as they are."""
    owner = folder_owner(dest)
    if owner not in (None, asset_id):
        raise RuntimeError(f"{dest} belongs to another asset; not overwriting it")
    if dest.exists() and keep is not None:
        new = {p.relative_to(src).as_posix() for p in src.rglob("*")}
        kept = set(keep)
        for path in sorted(dest.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            rel = path.relative_to(dest).as_posix()
            if rel in new or rel in kept or any(rel.startswith(k + "/") for k in kept) or path.suffix == ".meta":
                continue
            if path.is_dir():
                if not any(path.iterdir()):
                    path.rmdir()
            else:
                path.unlink()
        shutil.copytree(src, dest, dirs_exist_ok=True)
        for meta in dest.rglob("*.meta"):
            if not meta.with_suffix("").exists():
                meta.unlink()
        return
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)


UNITY_SCRIPTS_DIR = Path(__file__).parent / "unity"
UNITY_SCRIPTS_DEST = Path("Assets") / "Generated"
_VERSION_LINE = re.compile(r"assetgen-importer-version:\s*(\d+)")


def script_version(path: Path) -> int | None:
    """Version of a shipped Unity script (its ``assetgen-importer-version`` line)."""
    try:
        with path.open(encoding="utf-8") as file:
            head = file.read(400)
    except OSError:
        return None
    match = _VERSION_LINE.search(head)
    return int(match.group(1)) if match else None


def install_unity_importer(project_root: Path) -> dict:
    """Writes the editor import script (and the small runtime script gear uses)
    to ``Assets/Generated/{Editor,Runtime}/`` of a Unity project when they are
    missing or older than the bundled version. Returns the version and the
    files written."""
    bundled = script_version(UNITY_SCRIPTS_DIR / "Editor" / "AssetForgeImporter.cs") or 0
    dest_root = project_root / UNITY_SCRIPTS_DEST
    installed = script_version(dest_root / "Editor" / "AssetForgeImporter.cs")
    written: list[str] = []
    for source in sorted(UNITY_SCRIPTS_DIR.rglob("*")):
        if not source.is_file() or source.suffix not in (".cs", ".asmdef"):
            continue
        rel = source.relative_to(UNITY_SCRIPTS_DIR)
        target = dest_root / rel
        if target.exists() and installed is not None and installed >= bundled:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        written.append((UNITY_SCRIPTS_DEST / rel).as_posix())
    return {"version": max(bundled, installed or 0), "written": written}


def make_zip(src: Path, zip_path: Path, root: str) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                archive.write(path, f"{root}/{path.relative_to(src).as_posix()}")
    return zip_path
