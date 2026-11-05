"""Placeholder bodies and motion shared by the mock adapters.

Every mock output (candidate images, meshes, animations, sprite frames) is
drawn from the same ellipsoid parts and the same motion curves, so the
placeholders of one asset look consistent from gate to gate.
"""

from __future__ import annotations

import colorsys
import hashlib
import math

from .glb import Part

BIPED = [
    Part((0, 1.15, 0), (0.22, 0.32, 0.14), 3),  # torso
    Part((0, 1.62, 0), (0.13, 0.15, 0.13), 2),  # head
    Part((-0.27, 1.1, 0), (0.07, 0.30, 0.07), 1),  # arms
    Part((0.27, 1.1, 0), (0.07, 0.30, 0.07), 1),
    Part((-0.12, 0.45, 0), (0.09, 0.45, 0.09), 1.5),  # legs
    Part((0.12, 0.45, 0), (0.09, 0.45, 0.09), 1.5),
]
QUADRUPED = [
    Part((0, 0.75, 0), (0.22, 0.24, 0.55), 3),
    Part((0, 0.95, 0.62), (0.13, 0.14, 0.2), 2),
    Part((0, 0.85, -0.6), (0.04, 0.04, 0.22), 0.5),
] + [Part((x, 0.33, z), (0.07, 0.33, 0.07), 1) for x in (-0.14, 0.14) for z in (-0.38, 0.38)]
FLYER = [
    Part((0, 1.0, 0), (0.18, 0.18, 0.4), 3),
    Part((0, 1.15, 0.42), (0.11, 0.11, 0.14), 1.5),
    Part((-0.65, 1.05, 0), (0.55, 0.03, 0.25), 2),
    Part((0.65, 1.05, 0), (0.55, 0.03, 0.25), 2),
    Part((0, 0.95, -0.5), (0.05, 0.05, 0.25), 0.5),
]
SPIDER = [
    Part((0, 0.35, 0.15), (0.18, 0.13, 0.18), 2),
    Part((0, 0.42, -0.25), (0.26, 0.22, 0.3), 3),
] + [Part((side * 0.36, 0.2, z), (0.26, 0.1, 0.045), 0.6)
     for side in (-1, 1) for z in (0.3, 0.12, -0.06, -0.24)]
SERPENT = [Part((0, 0.18 + 0.02 * i, 0.9 - 0.22 * i), (0.13 - 0.008 * i, 0.12 - 0.008 * i, 0.15), 1)
           for i in range(9)] + [Part((0, 0.32, 1.12), (0.12, 0.1, 0.17), 1.5)]
BLOB = [
    Part((0, 0.38, 0), (0.48, 0.38, 0.48), 4),
    Part((-0.15, 0.55, 0.38), (0.07, 0.09, 0.05), 0.5),
    Part((0.15, 0.55, 0.38), (0.07, 0.09, 0.05), 0.5),
]
ARCHETYPE_PARTS = {"biped": BIPED, "quadruped": QUADRUPED, "flyer": FLYER,
                   "spider": SPIDER, "serpent": SERPENT, "blob": BLOB}

# Gear in its worn position on the standard base body.
GEAR_PARTS = {
    "head": [Part((0, 1.66, 0), (0.16, 0.12, 0.16), 1)],
    "chest": [Part((0, 1.18, 0), (0.25, 0.3, 0.17), 1)],
    "legs": [Part((-0.12, 0.55, 0), (0.11, 0.35, 0.11), 1), Part((0.12, 0.55, 0), (0.11, 0.35, 0.11), 1)],
    "hands": [Part((-0.27, 0.82, 0), (0.07, 0.07, 0.07), 1), Part((0.27, 0.82, 0), (0.07, 0.07, 0.07), 1)],
    "feet": [Part((-0.12, 0.06, 0.04), (0.08, 0.06, 0.13), 1), Part((0.12, 0.06, 0.04), (0.08, 0.06, 0.13), 1)],
    "cape": [Part((0, 1.0, -0.2), (0.26, 0.55, 0.03), 1)],
}
RIGHT_HAND = (0.27, 0.82, 0.05)
WEAPON_PARTS = {
    "one_handed": [Part((0, 0.45, 0), (0.035, 0.42, 0.01), 2), Part((0, 0.02, 0), (0.12, 0.025, 0.03), 1),
                   Part((0, -0.08, 0), (0.025, 0.09, 0.025), 1)],
    "two_handed": [Part((0, 0.7, 0), (0.06, 0.65, 0.015), 2), Part((0, 0.02, 0), (0.18, 0.03, 0.04), 1),
                   Part((0, -0.16, 0), (0.03, 0.17, 0.03), 1)],
    "bow": [Part((0, 0.38, 0), (0.03, 0.38, 0.03), 1), Part((0, -0.38, 0), (0.03, 0.38, 0.03), 1),
            Part((0, 0, 0), (0.04, 0.08, 0.04), 1)],
    "staff": [Part((0, 0.2, 0), (0.025, 0.85, 0.025), 2), Part((0, 1.08, 0), (0.09, 0.09, 0.09), 1)],
}
# Grip point (where the hand holds it) relative to the weapon origin.
DEFAULT_GRIPS = {
    "one_handed": {"socket": "weapon_r", "position": [0.0, -0.06, 0.0], "rotation": [0.0, 0.0, 0.0]},
    "two_handed": {"socket": "weapon_r", "position": [0.0, -0.12, 0.0], "rotation": [0.0, 0.0, 0.0]},
    "bow": {"socket": "weapon_l", "position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0]},
    "staff": {"socket": "weapon_r", "position": [0.0, 0.1, 0.0], "rotation": [0.0, 0.0, 0.0]},
}


def offset_parts(parts: list[Part], offset: tuple[float, float, float]) -> list[Part]:
    return [Part(tuple(c + o for c, o in zip(p.center, offset)), p.radii, p.weight) for p in parts]


def asset_parts(kind: str, archetype: str, slot: str | None = None,
                weapon_class: str | None = None, grip: dict | None = None) -> list[Part]:
    """The parts of the asset itself (for gear and weapons: the item only)."""
    if kind == "gear":
        return GEAR_PARTS.get(slot or "chest", GEAR_PARTS["chest"])
    if kind == "weapon":
        wclass = weapon_class or "one_handed"
        grip = grip or DEFAULT_GRIPS[wclass]
        hand = RIGHT_HAND if grip.get("socket", "weapon_r") == "weapon_r" else (-RIGHT_HAND[0], *RIGHT_HAND[1:])
        position = grip.get("position", [0, 0, 0])
        return offset_parts(WEAPON_PARTS[wclass], tuple(h - p for h, p in zip(hand, position)))
    parts = ARCHETYPE_PARTS.get(archetype, BIPED)
    if kind == "boss":  # bosses are bigger
        return [Part(tuple(c * 1.5 for c in p.center), tuple(r * 1.5 for r in p.radii), p.weight) for p in parts]
    return parts


def seed_of(*values) -> int:
    digest = hashlib.sha256("|".join(str(v) for v in values).encode()).digest()
    return int.from_bytes(digest[:8], "big")


COLOR_HUES = {"red": 0, "crimson": 350, "ember": 12, "orange": 28, "gold": 45, "golden": 45, "yellow": 52,
              "green": 120, "moss": 95, "mossy": 95, "emerald": 140, "teal": 175, "cyan": 185, "blue": 215,
              "azure": 205, "frost": 195, "ice": 195, "purple": 275, "violet": 270, "pink": 320, "brown": 30,
              "bone": 40, "rust": 18}


def hue_hint(text: str) -> float | None:
    """Hue (0..1) of the first colour word in ``text``, if any."""
    for word in text.lower().replace("-", " ").split():
        word = word.strip(".,;:!?")
        if word in COLOR_HUES:
            return COLOR_HUES[word] / 360
    return None


def color_of(*values, hue: float | None = None) -> tuple[float, float, float]:
    """A pleasant, stable colour for a seed (0..1 floats), near ``hue`` if given."""
    seed = seed_of(*values)
    hinted = hue is not None
    hue = (hue + ((seed % 30) - 15) / 360) % 1.0 if hinted else (seed % 360) / 360
    sat = (0.65 if hinted else 0.45) + (seed >> 9) % 30 / 100
    val = 0.65 + (seed >> 17) % 25 / 100
    return colorsys.hsv_to_rgb(hue, sat, val)


def to_rgb255(color: tuple[float, float, float]) -> tuple[int, int, int]:
    return tuple(int(max(0, min(1, c)) * 255) for c in color)


# ---- motion -----------------------------------------------------------------

CLIP_DURATIONS = {"idle": 1.5, "walk": 1.0, "run": 0.66, "hit": 0.5, "death": 1.25,
                  "cast": 1.0, "skill": 1.0}


def clip_duration(clip: str) -> float:
    if clip.startswith("attack"):
        return 0.75
    return CLIP_DURATIONS.get(clip, 1.0)


def quat(axis: tuple[float, float, float], angle: float) -> tuple[float, float, float, float]:
    s = math.sin(angle / 2)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(angle / 2))


def pose(clip: str, t: float) -> tuple[tuple, tuple, tuple]:
    """Root pose at time ``t`` (seconds): translation, (axis, angle), scale."""
    u = t / clip_duration(clip)  # 0..1
    wave = math.sin(2 * math.pi * u)
    if clip == "idle":
        return (0, 0.02 * wave, 0), ((0, 0, 1), 0), (1, 1 + 0.02 * wave, 1)
    if clip == "walk":
        return (0, 0.04 * abs(wave), 0), ((0, 0, 1), 0.06 * wave), (1, 1, 1)
    if clip == "run":
        return (0, 0.08 * abs(wave), 0), ((1, 0, 0), 0.15 + 0.05 * wave), (1, 1, 1)
    if clip.startswith("attack"):
        lunge = math.sin(math.pi * u)
        return (0, 0, 0.15 * lunge), ((1, 0, 0), 0.35 * lunge), (1, 1, 1)
    if clip == "hit":
        knock = math.sin(math.pi * u)
        return (0, 0, -0.1 * knock), ((1, 0, 0), -0.25 * knock), (1, 1, 1)
    if clip == "death":
        fall = min(1.0, u * 1.3)
        return (0, -0.1 * fall, 0), ((0, 0, 1), (math.pi / 2) * fall), (1, 1, 1)
    if clip == "cast":
        return (0, 0.05 * max(0, wave), 0), ((0, 0, 1), 0), (1 + 0.06 * max(0, wave),) * 3
    if clip == "skill":
        return (0, 0.1 * math.sin(math.pi * u), 0), ((0, 1, 0), 2 * math.pi * u), (1, 1, 1)
    return (0, 0, 0), ((0, 0, 1), 0), (1, 1, 1)
