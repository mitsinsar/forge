"""Mock adapters: generated placeholders instead of the real tools."""

from __future__ import annotations

import json
import math
import random
import re
import time
from pathlib import Path

from PIL import Image, ImageDraw, PngImagePlugin

from ... import imaging
from ...defaults import GEAR_SLOTS, LOOPING_CLIPS
from ..base import Progress
from .draw import Camera, draw_parts, font
from .glb import Animation, GlbBuilder, Part, build_mesh, read_glb_json
from .shapes import (BIPED, asset_parts, clip_duration, color_of, hue_hint, pose, quat, seed_of,
                     to_rgb255)

SLOT_WORDS = {
    "head": ["helmet", "helm", "hat", "hood", "crown", "mask", "circlet", "cap"],
    "chest": ["armor", "armour", "chest", "breastplate", "robe", "tunic", "vest", "cuirass", "mail"],
    "legs": ["pants", "trousers", "greaves", "leggings", "legs", "skirt", "kilt"],
    "hands": ["gloves", "gauntlets", "bracers", "hands", "mitts"],
    "feet": ["boots", "shoes", "sandals", "feet", "sabatons"],
    "cape": ["cape", "cloak", "mantle", "shawl"],
}
WEAPON_WORDS = {
    "bow": ["bow", "longbow", "crossbow"],
    "staff": ["staff", "wand", "rod", "sceptre", "scepter"],
    "two_handed": ["greatsword", "great sword", "claymore", "halberd", "greataxe", "great axe",
                   "warhammer", "maul", "two-handed", "two handed", "zweihander", "scythe", "spear"],
}
DIRECTION_YAW = {"down": 0, "down_right": 45, "right": 90, "up_right": 135, "up": 180,
                 "up_left": 225, "left": 270, "down_left": 315}
CAMERA_TILT = {"side": 0, "three_quarter": 30, "topdown_4": 50, "topdown_8": 50}


class MockBase:
    mode = "mock"

    def __init__(self, step_delay: float = 0.0):
        self.step_delay = step_delay

    def _wait(self) -> None:
        if self.step_delay > 0:
            time.sleep(self.step_delay)


def _words(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "


def _has(text: str, word: str) -> bool:
    return f" {word} " in text


class MockLLM(MockBase):
    """Keyword rules standing in for Qwen's structured JSON answer."""

    def expand(self, *, description, kind, archetypes, archetype_override, slot, weapon_class, notes):
        self._wait()
        text = _words(description)
        if kind in ("gear", "weapon"):
            archetype = "biped"  # items are previewed on the biped mannequin
        elif archetype_override:
            archetype = archetype_override
        else:
            archetype = next((a for a, spec in archetypes.items() if a != "biped"
                              and any(_has(text, k) for k in spec.get("keywords", []))), "biped")
        if kind == "gear" and not slot:
            slot = next((s for s in GEAR_SLOTS if any(_has(text, w) for w in SLOT_WORDS[s])), "chest")
        if kind == "weapon" and not weapon_class:
            weapon_class = next((c for c, words in WEAPON_WORDS.items() if any(_has(text, w) for w in words)),
                                "one_handed")
        rest_pose = archetypes[archetype]["rest_pose"]
        if kind == "gear":
            subject = f"a single {slot} armour piece, {description.strip()}, worn by a plain grey mannequin, {rest_pose}"
        elif kind == "weapon":
            subject = f"a single {weapon_class.replace('_', '-')} weapon, {description.strip()}, upright, centred, no hands"
        else:
            subject = f"a {kind} {archetype}: {description.strip()}, {rest_pose}"
        prompt = subject
        if notes:
            prompt += ". Changes asked for: " + "; ".join(n.strip() for n in notes if n.strip())
        return {
            "archetype": archetype,
            "prompt": prompt,
            "negative_prompt": "extra limbs, cropped feet, dynamic pose, motion blur",
            "slot": slot if kind == "gear" else None,
            "weapon_class": weapon_class if kind == "weapon" else None,
        }


class MockImage(MockBase):
    """Draws the placeholder body with a different colour per candidate."""

    size = 512

    def __init__(self, step_delay: float = 0.0):
        super().__init__(step_delay)
        self.loaded = False
        self.unload_count = 0

    def render_candidates(self, *, prompt, negative_prompt, base_model, loras, reference_images, count, seed,
                          out_dir, progress: Progress, hints, log=None):
        self.loaded = True
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for index in range(count):
            path = out_dir / f"candidate_{index + 1}.png"
            progress(f"Rendering candidate {index + 1} of {count}", index / count)
            if not path.exists():
                self._wait()
                self._render_one(path, index, seed, prompt, base_model, hints)
            paths.append(path)
        progress("Candidates ready", 1.0)
        return paths

    def _render_one(self, path: Path, index: int, seed: int, prompt: str, base_model: str, hints: dict) -> None:
        rng = random.Random(seed + index)
        color = color_of(seed, index, hue=hue_hint(prompt))
        jitter = [round(rng.uniform(0.85, 1.15), 3) for _ in range(3)]
        parts = asset_parts(hints["kind"], hints["archetype"], hints.get("slot"), hints.get("weapon_class"))
        parts = [Part(p.center, tuple(r * j for r, j in zip(p.radii, jitter)), p.weight) for p in parts]
        image = Image.new("RGBA", (self.size, self.size))
        top, bottom = to_rgb255(color_of(seed, index, "bg")), (236, 232, 226)
        for y in range(self.size):  # soft vertical gradient
            mix = y / self.size
            row = tuple(int(top[i] * (1 - mix) * 0.25 + bottom[i] * (0.75 + 0.25 * mix)) for i in range(3))
            image.paste((*row, 255), (0, y, self.size, y + 1))
        framing = BIPED if hints["kind"] in ("gear", "weapon") else parts
        camera = Camera(self.size, _ppm(framing, self.size * 0.78), yaw=0.35, tilt=0.12, ground=0.88)
        layers = []
        if hints["kind"] in ("gear", "weapon"):
            layers.append((BIPED, (170, 170, 170)))
        layers.append((parts, to_rgb255(color)))
        draw_parts(image, camera, layers)
        draw = ImageDraw.Draw(image)
        draw.text((14, 12), f"MOCK · {base_model} · #{index + 1}", fill=(40, 40, 40, 255), font=font(18))
        draw.text((14, self.size - 30), (hints.get("name") or "")[:40], fill=(40, 40, 40, 255), font=font(16))
        meta = PngImagePlugin.PngInfo()
        meta.add_text("assetgen", json.dumps({"color": color, "jitter": jitter, "prompt": prompt}))
        image.convert("RGB").save(path, pnginfo=meta)

    def unload(self):
        self.loaded = False
        self.unload_count += 1


def _ppm(parts: list[Part], height_px: float) -> float:
    top = max(p.center[1] + p.radii[1] for p in parts)
    width = max(max(abs(p.center[0]) + p.radii[0], abs(p.center[2]) + p.radii[2]) for p in parts) * 2
    return height_px / max(top, width, 0.5)


def _parts_from(info: dict) -> list[Part]:
    return [Part(tuple(p["center"]), tuple(p["radii"]), p.get("weight", 1)) for p in info]


def _parts_info(parts: list[Part]) -> list[dict]:
    return [{"center": list(p.center), "radii": list(p.radii), "weight": p.weight} for p in parts]


class MockMesh(MockBase):
    """Builds an ellipsoid body in the colour of the picked candidate."""

    def image_to_mesh(self, *, image, texture_size, out_path, progress, hints, delight=True):
        progress("Reconstructing shape", 0.1)
        self._wait()
        meta = {}
        with Image.open(image) as img:
            if "assetgen" in img.info:
                meta = json.loads(img.info["assetgen"])
        color = tuple(meta.get("color", (0.7, 0.7, 0.7)))
        jitter = meta.get("jitter", [1, 1, 1])
        parts = asset_parts(hints["kind"], hints["archetype"], hints.get("slot"), hints.get("weapon_class"))
        parts = [Part(p.center, tuple(r * j for r, j in zip(p.radii, jitter)), p.weight) for p in parts]
        progress("Painting texture", 0.6)
        self._wait()
        builder = GlbBuilder(extras={"mock": True, "color": color, "parts": _parts_info(parts),
                                     "texture_size": texture_size, "hints": hints})
        material = builder.add_material("albedo", color)
        builder.add_node("raw", builder.add_mesh("raw", build_mesh(parts, 20000), material))
        builder.write(out_path)
        texture = out_path.with_name("texture.png")
        with Image.open(image) as img:
            albedo = img.convert("RGB").resize((texture_size, texture_size), Image.Resampling.LANCZOS)
        (imaging.delight(albedo) if delight else albedo).save(texture)
        progress("Mesh ready", 1.0)
        return {"glb": out_path, "texture": texture}


class MockRig(MockBase):
    def predict_skeleton(self, *, mesh, archetype, out_dir, progress):
        progress("Predicting skeleton", 0.2)
        self._wait()
        result = {"archetype": archetype["id"], "joints": archetype["joints"], "fit_score": 0.93,
                  "fallback": False, "sockets": archetype.get("sockets", {})}
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "skeleton.json"
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        progress("Skeleton fitted", 1.0)
        return {**result, "file": str(path)}


class MockBlender(MockBase):
    def cleanup_and_lods(self, *, raw_mesh, triangles, lod_ratios, out_dir, progress):
        info = read_glb_json(raw_mesh).get("extras") or {}
        if "parts" not in info:  # a real mesh (Hunyuan3D): shown as made until real Blender decimates it
            return self._pass_through(raw_mesh, triangles, out_dir, progress)
        parts, color = _parts_from(info["parts"]), tuple(info["color"])
        progress("Cleaning up mesh", 0.2)
        self._wait()
        builder = GlbBuilder(extras={**info, "triangle_budget": triangles})
        material = builder.add_material("albedo", color)
        lods = []
        ratios = [1.0, *lod_ratios]
        width = max(abs(p.center[0]) + p.radii[0] for p in parts) * 2 + 0.3
        for level, ratio in enumerate(ratios):
            progress(f"Decimating LOD{level}", 0.3 + 0.6 * level / len(ratios))
            self._wait()
            mesh = build_mesh(parts, int(triangles * ratio))
            lod = {"level": level, "ratio": ratio, "triangles": mesh.triangles}
            index = builder.add_mesh(f"LOD{level}", mesh, material, extras=lod)
            # LOD0 at the origin, the lower LODs beside it for comparison.
            builder.add_node(f"LOD{level}", index, translation=(level * width, 0, 0), extras=lod)
            lods.append(lod)
        glb = builder.write(out_dir / "mesh.glb")
        progress("LODs ready", 1.0)
        return {"glb": glb, "lods": lods}

    def _pass_through(self, raw_mesh, triangles, out_dir, progress):
        progress("Keeping the mesh as made (mock Blender)", 0.5)
        gltf, binary = imaging.read_glb(raw_mesh)
        color = (0.7, 0.7, 0.7)
        albedo = imaging.albedo_image(gltf, binary)
        if albedo is not None:
            color = tuple(c / 255 for c in albedo.convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0)))
        count = imaging.triangle_count(gltf)
        gltf["extras"] = {**(gltf.get("extras") or {}), "passthrough": True, "color": list(color),
                          "triangle_budget": triangles}
        glb = out_dir / "mesh.glb"
        imaging.write_glb(glb, gltf, binary)
        progress("Mesh kept", 1.0)
        return {"glb": glb, "lods": [{"level": 0, "ratio": 1.0, "triangles": count}]}

    def rig_and_animate(self, *, mesh_glb, skeleton, archetype, clips, attach, out_path, progress):
        info = read_glb_json(mesh_glb).get("extras") or {}
        if "parts" not in info:  # a real mesh: the mock rig animates a placeholder body of the archetype
            info = {**info, "parts": _parts_info(asset_parts("hero", archetype["id"])),
                    "color": info.get("color", (0.7, 0.7, 0.7))}
        parts, color = _parts_from(info["parts"]), tuple(info["color"])
        if attach and attach.get("kind") == "weapon":
            parts = asset_parts("weapon", "biped", weapon_class=attach.get("weapon_class"),
                                grip=attach.get("grip"))
        progress("Skinning to template skeleton", 0.1)
        self._wait()
        builder = GlbBuilder(extras={**info, "parts": _parts_info(parts), "skeleton": skeleton["joints"],
                                     "attach": attach})
        root = builder.add_node("Armature")
        material = builder.add_material("albedo", color)
        budget = info.get("triangle_budget", 4000)
        if attach:  # the item worn by or held in the hand of the mannequin
            grey = builder.add_material("mannequin", (0.62, 0.62, 0.62))
            builder.add_node("mannequin", builder.add_mesh("mannequin", build_mesh(BIPED, 3000), grey), root)
        builder.add_node("mesh", builder.add_mesh("mesh", build_mesh(parts, budget), material), root)
        clip_info = []
        for index, clip in enumerate(clips):
            progress(f"Retargeting {clip}", 0.2 + 0.75 * index / len(clips))
            duration = clip_duration(clip)
            times = [duration * i / 24 for i in range(25)]
            tr, rot, sc = [], [], []
            for t in times:
                translation, (axis, angle), scale = pose(clip, t)
                tr.append(translation)
                rot.append(quat(axis, angle))
                sc.append(scale)
            loop = clip in LOOPING_CLIPS
            builder.add_animation(root, Animation(clip, times, tr, rot, sc), extras={"loop": loop})
            clip_info.append({"name": clip, "duration": duration, "loop": loop})
        self._wait()
        glb = builder.write(out_path)
        progress("Animated preview ready", 1.0)
        return {"glb": glb, "clips": clip_info}

    def render_sprites(self, *, animated_glb, clips, directions, camera_preset, frame_size, supersample, fps,
                       look, palette_size, out_dir, progress):
        info = read_glb_json(animated_glb)["extras"]
        parts, color = _parts_from(info["parts"]), to_rgb255(tuple(info["color"]))
        hints = info.get("hints", {})
        framing = BIPED if hints.get("kind") in ("gear", "weapon") else parts
        if hints.get("kind") == "boss":
            framing = asset_parts("boss", hints.get("archetype", "biped"))
        big = frame_size * supersample
        tilt = CAMERA_TILT[camera_preset] * math.pi / 180
        result: dict[str, dict[str, list[Path]]] = {}
        total = len(directions) * len(clips)
        done = 0
        for direction in directions:
            camera = Camera(big, _ppm(framing, big * 0.66), yaw=DIRECTION_YAW[direction] * math.pi / 180,
                            tilt=tilt, ground=0.88)
            result[direction] = {}
            for clip in clips:
                progress(f"Rendering {clip['name']} ({direction})", done / total)
                frames = max(1, round(clip["duration"] * fps))
                paths = []
                for frame in range(frames):
                    path = out_dir / direction / clip["name"] / f"{frame:03d}.png"
                    if not path.exists():
                        image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
                        draw_parts(image, camera, [(parts, color)], clip["name"], frame / fps)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        _downscale(image, frame_size, look, palette_size).save(path)
                    paths.append(path)
                result[direction][clip["name"]] = paths
                done += 1
            self._wait()
        progress("Sprites rendered", 1.0)
        return result

    def export_fbx(self, *, animated_glb, lods, rig_type, out_path, progress):
        progress("Exporting FBX", 0.5)
        self._wait()
        gltf = read_glb_json(animated_glb)
        clips = [a["name"] for a in gltf.get("animations", [])]
        joints = gltf.get("extras", {}).get("skeleton", [])
        lines = [
            "; FBX 7.4.0 project file",
            "; MOCK placeholder written by the assetgen mock Blender adapter.",
            "; The real adapter exports this file with headless Blender.",
            "FBXHeaderExtension:  {",
            "\tFBXHeaderVersion: 1003",
            "\tFBXVersion: 7400",
            '\tCreator: "assetgen mock"',
            "}",
            f'; RigType: "{rig_type}"',
            f"; Joints: {len(joints)}",
            *[f'; Joint: "{j}"' for j in joints],
            *[f'; Mesh: "LOD{lod["level"]}" Triangles: {lod["triangles"]}' for lod in lods],
            *[f'; Take: "{clip}"' for clip in clips],
        ]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        progress("FBX written", 1.0)
        return out_path


def _downscale(image: Image.Image, size: int, look: str, palette_size: int) -> Image.Image:
    if look == "pixel":
        small = image.resize((size, size), Image.Resampling.NEAREST)
        alpha = small.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
        rgb = small.convert("RGB").quantize(colors=palette_size, method=Image.Quantize.MEDIANCUT).convert("RGB")
        rgb.putalpha(alpha)
        return rgb
    return image.resize((size, size), Image.Resampling.LANCZOS)


__all__ = ["MockLLM", "MockImage", "MockMesh", "MockRig", "MockBlender", "seed_of"]
