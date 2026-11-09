"""Skill effects (asset kind ``effect``): a poison arrow, a firebolt, a healing glow.

An effect is a few parts, each a flipbook (a looping or one-shot sprite animation with alpha):

``cast``        on the caster's hands while the skill starts (one-shot)
``projectile``  what flies from the caster to the target (loops while it flies)
``impact``      where it lands (one-shot)
``aura``        stays on a character (loops)

and the trail behind a projectile, made by the engine's own particles (``particles``). The
language model plans the parts (``normalize_plan``); the image model draws each part on black, Wan
2.2 animates it, and the frames become a flipbook here: brightness is the alpha (effects glow
on black, which is how game VFX are usually keyed), a looping part's ends are cross-faded so it
loops without a jump, and a one-shot part fades out at its end. ``write_godot`` makes the scene:
an ``AnimatedSprite2D`` per part, ``GPUParticles2D`` for the trail and ``play(from, to)``.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from . import checks as qc

EFFECT_KINDS = ("projectile", "burst", "aura")
PARTS = ("cast", "projectile", "impact", "aura")
LOOPING = {"projectile": True, "aura": True, "cast": False, "impact": False}
# The parts each kind of effect needs (and the one gate 1's candidates show).
NEEDS = {"projectile": ("projectile", "impact"), "burst": ("impact",), "aura": ("aura",)}
MAIN = {"projectile": "projectile", "burst": "impact", "aura": "aura"}
BLENDS = ("add", "mix")

FRAME_SIZE = 256  # flipbook frames, pixels
FLIPBOOK_FRAMES = 16
FPS = 16
VIDEO_FRAMES = 33  # Wan 2.2: 4k+1 frames at 24 fps
VIDEO_SIZE = 512
CROSSFADE = 4  # frames blended at a loop's seam

PICTURE_SUFFIX = ("game VFX sprite, isolated on a pure black background, centered, nothing else in the picture, "
                  "glowing, high contrast, no text, no frame")
PICTURE_NEGATIVE = ("person, character, hand, body, ground, floor, scenery, landscape, sky, background, frame, "
                    "border, text, watermark, cropped, cut off")
MOTION_SUFFIX = "static camera, centered, pure black background, seamless motion, no camera movement"
MOTION_NEGATIVE = ("camera movement, zoom, pan, scene, background, ground, person, text, watermark, blurry, "
                   "flicker, cut, still image")

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize_plan(plan: dict, description: str) -> dict:
    """The language model's plan, completed and clamped: a known kind, the parts it needs (each with a
    picture and motion prompt), a palette of hex colours and trail particle settings."""
    kind = plan.get("kind") if plan.get("kind") in EFFECT_KINDS else "projectile"
    palette = [c for c in (plan.get("palette") or []) if isinstance(c, str) and HEX.match(c)][:4] or ["#ffaa33", "#ff4400"]
    given = {p.get("part"): p for p in plan.get("parts") or [] if isinstance(p, dict) and p.get("part") in PARTS}
    wanted = list(NEEDS[kind]) + (["cast"] if "cast" in given and kind != "aura" else [])
    parts = []
    for name in wanted:
        part = given.get(name) or {}
        picture = " ".join(str(part.get("picture") or f"{description}, the {name}").split())
        motion = " ".join(str(part.get("motion") or f"the {name} glows and swirls").split())
        blend = part.get("blend") if part.get("blend") in BLENDS else "add"
        try:
            scale = round(max(0.25, min(3.0, float(part.get("scale") or 1.0))), 2)
        except (TypeError, ValueError):
            scale = 1.0
        parts.append({"part": name, "picture": picture, "motion": motion, "loop": LOOPING[name],
                      "blend": blend, "scale": scale})
    raw = plan.get("particles") if isinstance(plan.get("particles"), dict) else {}

    def number(key: str, default: float, low: float, high: float) -> float:
        try:
            return round(max(low, min(high, float(raw.get(key, default)))), 2)
        except (TypeError, ValueError):
            return default

    particles = {"enabled": kind == "projectile" and raw.get("enabled", True) is not False,
                 "amount": int(number("amount", 32, 4, 200)), "lifetime": number("lifetime", 0.5, 0.1, 3.0),
                 "speed": number("speed", 40, 0, 400), "spread": number("spread", 30, 0, 180),
                 "gravity": number("gravity", 0, -400, 400), "size": number("size", 6, 1, 40)}
    try:
        speed = round(max(100.0, min(2000.0, float(plan.get("travel_speed") or 600))), 0)
    except (TypeError, ValueError):
        speed = 600.0
    return {"kind": kind, "palette": palette, "parts": parts, "main": MAIN[kind], "particles": particles,
            "travel_speed": speed}


# ---- frames to flipbooks -------------------------------------------------------------------

def key_alpha(image: Image.Image) -> Image.Image:
    """RGBA from an effect on black: alpha is the brightest channel, and the colour is divided by it
    (un-premultiplied), so it shows the same over any background (blend "add" or "mix")."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    alpha = rgb.max(axis=2)
    alpha = np.clip((alpha - 0.08) / 0.92, 0.0, 1.0)  # a not-quite-black background stays clear
    alpha *= edge_feather(*alpha.shape)  # nothing ends in a hard edge at the frame's border
    colour = np.where(alpha[..., None] > 1e-3, rgb / np.maximum(alpha[..., None], 1e-3), 0.0)
    out = np.dstack([np.clip(colour, 0, 1), alpha]) * 255.0
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def edge_feather(height: int, width: int, margin: float = 0.08) -> np.ndarray:
    """1 inside, falling to 0 over the outer ``margin`` of the frame on every side."""
    def ramp(n: int) -> np.ndarray:
        x = np.minimum(np.arange(n), np.arange(n)[::-1]) / max(1.0, n * margin)
        return np.clip(x, 0.0, 1.0)
    return np.outer(ramp(height), ramp(width))


def edge_darkness(image: Image.Image) -> float:
    """The share of the picture's outer sixteenth that is black."""
    grey = np.asarray(image.convert("L"), dtype=np.float32) / 255.0
    h, w = grey.shape
    edge = np.concatenate([grey[: h // 16].ravel(), grey[-h // 16:].ravel(), grey[:, : w // 16].ravel(),
                           grey[:, -w // 16:].ravel()])
    return float((edge < 0.12).mean())


def clean_picture(path: Path) -> bool:
    """A picture whose background isn't black (image models often ignore "on black") gets its subject
    cut out (rembg) and put on black, so brightness can be its alpha and Wan animates it on black.
    True when it was changed."""
    from . import imaging  # noqa: PLC0415 - rembg loads only when a picture needs it

    with Image.open(path) as image:
        picture = image.convert("RGB")
    if edge_darkness(picture) > 0.9:
        return False
    cut = imaging._rembg_cutout(picture)  # noqa: SLF001 - the shared rembg cut-out
    if cut is None:
        return False
    black = Image.new("RGB", picture.size)
    black.paste(cut, (0, 0), cut)
    black.save(path)
    return True


def level(path: Path) -> float:
    """Turns the picture so its subject's long axis is horizontal (a projectile flies along it);
    the degrees it was turned."""
    with Image.open(path) as image:
        picture = image.convert("RGB")
    grey = np.asarray(picture.convert("L"), dtype=np.float32) / 255.0
    ys, xs = np.nonzero(grey > 0.15)
    if len(xs) < 50:
        return 0.0
    weights = grey[ys, xs]
    cx, cy = np.average(xs, weights=weights), np.average(ys, weights=weights)
    cov = np.cov(np.vstack([xs - cx, ys - cy]), aweights=weights)
    values, vectors = np.linalg.eigh(cov)
    if values[1] < 1.5 * max(values[0], 1e-6):
        return 0.0  # round: no long axis to line up
    vx, vy = vectors[:, 1]
    angle = float(np.degrees(np.arctan2(vy, vx)))  # image y points down
    angle = (angle + 90) % 180 - 90  # the axis, not a direction
    if abs(angle) < 3:
        return 0.0
    # Centred on the subject, turned about it (PIL turns counter-clockwise on screen).
    shifted = picture.transform(picture.size, Image.Transform.AFFINE,
                                (1, 0, cx - picture.width / 2, 0, 1, cy - picture.height / 2))
    shifted.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0)).save(path)
    return round(angle, 1)


def mirror(path: Path) -> None:
    with Image.open(path) as image:
        flipped = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    flipped.save(path)


def pick_frames(count_in: int, count_out: int) -> list[int]:
    """``count_out`` evenly spread frame numbers of ``count_in`` (the first one kept)."""
    if count_in <= count_out:
        return list(range(count_in))
    return [round(i * (count_in - 1) / (count_out - 1)) for i in range(count_out)]


def make_flipbook(frames: list[Path], out_dir: Path, *, loop: bool, size: int = FRAME_SIZE,
                  count: int = FLIPBOOK_FRAMES) -> list[Path]:
    """The video's frames as a flipbook: ``count`` frames at ``size`` pixels with keyed alpha. A loop's
    last ``CROSSFADE`` frames are blended into its first ones (the seam disappears); a one-shot
    fades out over its last quarter."""
    out_dir.mkdir(parents=True, exist_ok=True)
    chosen = pick_frames(len(frames), count + (CROSSFADE if loop else 0))
    images = []
    for index in chosen:
        with Image.open(frames[index]) as frame:
            images.append(np.asarray(key_alpha(frame).resize((size, size), Image.Resampling.LANCZOS), dtype=np.float32))
    if loop and len(images) > 2 * CROSSFADE:
        body, tail = images[:-CROSSFADE], images[-CROSSFADE:]
        for i in range(CROSSFADE):  # the tail fades into the start
            w = (i + 1) / (CROSSFADE + 1)
            body[i] = body[i] * w + tail[i] * (1 - w)
        images = body
    elif not loop:
        fade_from = int(len(images) * 0.75)
        for i in range(fade_from, len(images)):
            images[i] = images[i].copy()
            images[i][..., 3] *= 1 - (i - fade_from + 1) / (len(images) - fade_from + 1)
    paths = []
    for number, array in enumerate(images):
        path = out_dir / f"frame_{number:02d}.png"
        Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), "RGBA").save(path)
        paths.append(path)
    return paths


def pack_sheet(frames: list[Path], out_path: Path) -> dict:
    """One sprite sheet (a near-square grid) of the flipbook's frames: ``{"file", "columns", "rows",
    "frames", "size"}``."""
    with Image.open(frames[0]) as first:
        size = first.width
    columns = max(1, math.ceil(math.sqrt(len(frames))))
    rows = math.ceil(len(frames) / columns)
    sheet = Image.new("RGBA", (columns * size, rows * size), (0, 0, 0, 0))
    for index, path in enumerate(frames):
        with Image.open(path) as frame:
            sheet.paste(frame.convert("RGBA"), ((index % columns) * size, (index // columns) * size))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return {"file": out_path.name, "columns": columns, "rows": rows, "frames": len(frames), "size": size}


def particle_texture(out_path: Path, colour: str, size: int = 32) -> Path:
    """A soft round dot in the palette's first colour, for the trail particles."""
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    y, x = np.mgrid[0:size, 0:size]
    d = np.hypot(x - (size - 1) / 2, y - (size - 1) / 2) / (size / 2)
    alpha = np.clip(1 - d, 0, 1) ** 2 * 255
    out = np.dstack([np.full_like(alpha, r), np.full_like(alpha, g), np.full_like(alpha, b), alpha])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out.astype(np.uint8), "RGBA").save(out_path)
    return out_path


# ---- checks ---------------------------------------------------------------------------------

def picture_checks(path: Path, vision: dict | None = None) -> list[dict]:
    """Gate 1 for an effect: drawn on black (the edges dark, so brightness can be the alpha), and
    something there that isn't cut off at the edges; with the vision model's answer, one thing
    that matches its prompt (``vision``: ``inspect_effect``'s)."""
    try:
        with Image.open(path) as image:
            grey = np.asarray(image.convert("L"), dtype=np.float32) / 255.0
    except OSError:
        return [qc.check("image", "Readable image", qc.FAIL, "The image could not be read")]
    dark = edge_darkness(Image.fromarray((grey * 255).astype(np.uint8)))
    lit = float((grey > 0.2).mean())
    found = [qc.check("black", "On black", qc.PASS if dark > 0.9 else qc.WARN if dark > 0.7 else qc.FAIL,
                      f"{dark:.0%} of the edges are black" + ("" if dark > 0.9 else ": the cut-out shows a box")),
             qc.check("subject", "Something to see", qc.PASS if 0.02 < lit < 0.6 else qc.FAIL,
                      f"{lit:.0%} of the picture is lit" + (": almost empty" if lit <= 0.02 else
                                                            ": fills the picture" if lit >= 0.6 else ""))]
    if vision is not None:
        count = vision.get("count", 1)
        found.append(qc.check("one", "One of it", qc.PASS if count <= 1 else qc.FAIL,
                              "one" if count <= 1 else f"{count} of them: draw one"))
        if vision.get("match") is not None:
            found.append(qc.judged("match", "Matches the prompt", vision["match"], vision.get("problems")))
    return found


def flipbook_checks(parts: list[dict], root: Path) -> list[dict]:
    """Gate 2: every part animated, lit but not a filled square, and a loop's seam small."""
    found = []
    for part in parts:
        frames = [root / f for f in part["frames"]]
        alphas = []
        for path in frames:
            with Image.open(path) as frame:
                alphas.append(np.asarray(frame.getchannel("A"), dtype=np.float32) / 255.0)
        cover = float(np.mean([(a > 0.1).mean() for a in alphas]))
        moving = float(np.mean([np.abs(alphas[i] - alphas[i - 1]).mean() for i in range(1, len(alphas))]))
        status = qc.PASS if 0.01 < cover < 0.7 and moving > 0.002 else qc.WARN if cover > 0.003 else qc.FAIL
        detail = f"covers {cover:.0%}, moves {moving * 100:.1f}%"
        if moving <= 0.002:
            detail += ": barely moves"
        if cover >= 0.7:
            detail += ": fills its square (the black was not black)"
        found.append(qc.check(f"part_{part['part']}", f"{part['part'].title()} flipbook", status, detail))
        if part["loop"]:
            seam = float(np.abs(alphas[-1] - alphas[0]).mean())
            found.append(qc.check(f"loop_{part['part']}", f"{part['part'].title()} loops smoothly",
                                  qc.PASS if seam < 0.08 else qc.WARN, f"seam difference {seam * 100:.1f}%"))
    return found


# ---- Godot ------------------------------------------------------------------------------------

def _colour(hex_colour: str, alpha: float = 1.0) -> str:
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return f"Color({r:.3f}, {g:.3f}, {b:.3f}, {alpha:.2f})"


GODOT_SCRIPT = '''extends Node2D
## Skill effect "{name}" made by Forge. play(from, to) runs it: the cast at `from`, the
## projectile flying to `to` with its trail, the impact there; `hit` is emitted when it lands.
## Effects without a projectile play their impact (burst) or loop their aura where they are.

signal hit
signal finished

@export var travel_speed := {speed:.1f}  ## pixels per second

func _ready() -> void:
	for part in get_children():
		if part is AnimatedSprite2D:
			part.visible = part.name == "aura"
			if part.name == "aura":
				part.play("aura")
	if has_node("trail"):
		$trail.emitting = false

func play(from: Vector2, to: Vector2) -> void:
	global_position = from
	if has_node("cast"):
		await _once($cast, "cast")
	if has_node("projectile"):
		var flight := $projectile as AnimatedSprite2D
		flight.visible = true
		flight.play("projectile")
		flight.rotation = (to - from).angle()
		if has_node("trail"):
			$trail.rotation = flight.rotation  # the particles drift back along the flight
			$trail.emitting = true
		var tween := create_tween()
		tween.tween_property(self, "global_position", to, from.distance_to(to) / travel_speed)
		await tween.finished
		flight.visible = false
		if has_node("trail"):
			$trail.emitting = false
	else:
		global_position = to
	hit.emit()
	if has_node("impact"):
		await _once($impact, "impact")
	finished.emit()

func _once(sprite: AnimatedSprite2D, animation: String) -> void:
	sprite.visible = true
	sprite.play(animation)
	await sprite.animation_finished
	sprite.visible = false
'''


def write_godot(*, out_dir: Path, res_dir: str, name: str, asset: dict, plan: dict, parts: list[dict],
                root: Path) -> dict:
    """res://generated/effect/<name>/: a sheet per part, ``<name>_frames.tres`` (a SpriteFrames with
    an animation per part), the trail's particle texture, ``<name>.gd`` and ``<name>.tscn``."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    sheets = {}
    for part in parts:
        sheets[part["part"]] = pack_sheet([root / f for f in part["frames"]], out_dir / f"{name}_{part['part']}.png")
    particles = plan["particles"]
    if particles["enabled"]:
        particle_texture(out_dir / f"{name}_dot.png", plan["palette"][0])

    # SpriteFrames: one animation per part.
    ext, subs, animations, sub_id = [], [], [], 0
    for index, part in enumerate(parts, start=1):
        sheet = sheets[part["part"]]
        ext_id = f"{index}_{part['part']}"
        ext.append(f'[ext_resource type="Texture2D" path="{res_dir}/{sheet["file"]}" id="{ext_id}"]')
        frames = []
        for frame in range(sheet["frames"]):
            sub_id += 1
            x, y = (frame % sheet["columns"]) * sheet["size"], (frame // sheet["columns"]) * sheet["size"]
            subs.append(f'[sub_resource type="AtlasTexture" id="AtlasTexture_{sub_id}"]\natlas = ExtResource("{ext_id}")\n'
                        f'region = Rect2({x}, {y}, {sheet["size"]}, {sheet["size"]})')
            frames.append('{\n"duration": 1.0,\n"texture": SubResource("AtlasTexture_%d")\n}' % sub_id)
        animations.append('{\n"frames": [%s],\n"loop": %s,\n"name": &"%s",\n"speed": %.1f\n}'
                          % (", ".join(frames), "true" if part["loop"] else "false", part["part"], FPS))
    tres = "\n\n".join([f'[gd_resource type="SpriteFrames" load_steps={len(ext) + sub_id + 1} format=3]',
                        "\n".join(ext), *subs])
    tres += "\n\n[resource]\nanimations = [" + ", ".join(animations) + "]\n"
    (out_dir / f"{name}_frames.tres").write_text(tres, encoding="utf-8")
    (out_dir / f"{name}.gd").write_text(GODOT_SCRIPT.format(name=name, speed=plan["travel_speed"]), encoding="utf-8")

    # The scene.
    lines = [f'[gd_scene load_steps={5 if particles["enabled"] else 3} format=3]', "",
             f'[ext_resource type="Script" path="{res_dir}/{name}.gd" id="1_script"]',
             f'[ext_resource type="SpriteFrames" path="{res_dir}/{name}_frames.tres" id="2_frames"]']
    if particles["enabled"]:
        lines.append(f'[ext_resource type="Texture2D" path="{res_dir}/{name}_dot.png" id="3_dot"]')
    lines += ["", '[sub_resource type="CanvasItemMaterial" id="add"]', "blend_mode = 1"]
    if particles["enabled"]:
        end = plan["palette"][1] if len(plan["palette"]) > 1 else plan["palette"][0]
        lines += ["", '[sub_resource type="Gradient" id="fade"]',
                  f"colors = PackedColorArray({_colour(plan['palette'][0])[6:-1]}, {_colour(end, 0.0)[6:-1]})",
                  "", '[sub_resource type="GradientTexture1D" id="fade_texture"]', 'gradient = SubResource("fade")',
                  "", '[sub_resource type="ParticleProcessMaterial" id="trail_process"]',
                  "particle_flag_disable_z = true", f"spread = {particles['spread']}",
                  f"initial_velocity_min = {particles['speed'] * 0.5}", f"initial_velocity_max = {particles['speed']}",
                  f"gravity = Vector3(0, {particles['gravity']}, 0)",
                  f"scale_min = {particles['size'] / 32 * 0.5:.3f}", f"scale_max = {particles['size'] / 32:.3f}",
                  'color_ramp = SubResource("fade_texture")', "direction = Vector3(-1, 0, 0)"]
    lines += ["", f'[node name="{name}" type="Node2D"]', 'script = ExtResource("1_script")']
    if particles["enabled"]:
        lines += ["", '[node name="trail" type="GPUParticles2D" parent="."]', 'material = SubResource("add")',
                  f"amount = {particles['amount']}", f"lifetime = {particles['lifetime']}", "local_coords = false",
                  'process_material = SubResource("trail_process")', 'texture = ExtResource("3_dot")',
                  "emitting = false"]
    for part in parts:
        lines += ["", f'[node name="{part["part"]}" type="AnimatedSprite2D" parent="."]',
                  'sprite_frames = ExtResource("2_frames")', f'animation = &"{part["part"]}"',
                  f"scale = Vector2({part['scale']}, {part['scale']})"]
        if part["blend"] == "add":
            lines.append('material = SubResource("add")')
        if part["part"] != "aura":
            lines.append("visible = false")
    (out_dir / f"{name}.tscn").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sidecar = {"generator": "assetgen", "id": asset["id"], "name": asset["name"], "kind": "effect",
               "effect": plan["kind"], "palette": plan["palette"], "fps": FPS, "travel_speed": plan["travel_speed"],
               "parts": [{**{k: part[k] for k in ("part", "loop", "blend", "scale")}, "sheet": sheets[part["part"]]}
                         for part in parts],
               "particles": particles, "scene": f"{res_dir}/{name}.tscn"}
    (out_dir / "asset.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return sidecar


def write_unity(*, out_dir: Path, name: str, asset: dict, plan: dict, parts: list[dict], root: Path) -> dict:
    """A sheet per part, the trail's particle texture and ``<name>.json`` describing them (grid,
    frames, fps, loop, blend), for a Particle System's Texture Sheet Animation or a sprite animator."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    sheets = {part["part"]: pack_sheet([root / f for f in part["frames"]], out_dir / f"{name}_{part['part']}.png")
              for part in parts}
    if plan["particles"]["enabled"]:
        particle_texture(out_dir / f"{name}_dot.png", plan["palette"][0])
    sidecar = {"generator": "assetgen", "id": asset["id"], "name": asset["name"], "kind": "effect",
               "effect": plan["kind"], "palette": plan["palette"], "fps": FPS, "travel_speed": plan["travel_speed"],
               "parts": [{**{k: part[k] for k in ("part", "loop", "blend", "scale")}, "sheet": sheets[part["part"]]}
                         for part in parts],
               "particles": plan["particles"]}
    (out_dir / f"{name}.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return sidecar
