"""Fixed vocabularies and the style-profile defaults from the spec."""

from __future__ import annotations

import copy

KINDS = ("hero", "monster", "boss", "gear", "weapon")
ENGINES = ("unity", "godot")
BASE_MODELS = ("qwen-image", "sdxl", "flux")
CAMERA_PRESETS = ("side", "three_quarter", "topdown_4", "topdown_8")
SPRITE_LOOKS = ("hd", "pixel")
GEAR_SLOTS = ("head", "chest", "legs", "hands", "feet", "cape")
WEAPON_CLASSES = ("one_handed", "two_handed", "bow", "staff")
WEAPON_SOCKETS = ("weapon_r", "weapon_l")

# Directions rendered per camera preset. Directions in MIRRORED are drawn
# by flipping their partner with flip_h in Godot instead of rendering them.
CAMERA_DIRECTIONS: dict[str, list[str]] = {
    "side": ["right"],
    "three_quarter": ["down_right", "up_right"],
    "topdown_4": ["down", "right", "up", "left"],
    "topdown_8": ["down", "down_right", "right", "up_right", "up", "up_left", "left", "down_left"],
}
CAMERA_MIRRORED: dict[str, dict[str, str]] = {
    "side": {"left": "right"},
    "three_quarter": {"down_left": "down_right", "up_left": "up_right"},
    "topdown_4": {},
    "topdown_8": {},
}

STANDARD_CLIPS = ["idle", "walk", "run", "attack_1", "attack_2", "hit", "death"]
BIPED_EXTRA_CLIPS = ["cast", "skill"]
LOOPING_CLIPS = {"idle", "walk", "run"}

# Gate 3 shows gear and weapons on the mannequin playing walk and attack.
WEAPON_ATTACKS: dict[str, list[str]] = {
    "one_handed": ["attack_slash", "attack_stab"],
    "two_handed": ["attack_cleave", "attack_overhead"],
    "bow": ["attack_shoot", "attack_quick_shot"],
    "staff": ["attack_cast", "attack_strike"],
}

STAGES = ("prompt", "mesh", "rig", "export")
GATE_OF_STAGE = {"prompt": 1, "mesh": 2, "rig": 3}
STAGE_OF_GATE = {v: k for k, v in GATE_OF_STAGE.items()}

PROFILE_DEFAULTS: dict = {
    "base_model": "sdxl",
    "prompt_suffix": "game asset, full body, plain background, flat even lighting",
    "negative_prompt": "cropped, blurry, text, watermark, multiple characters, cast shadow",
    "loras": [],
    "camera_preset": "side",
    "candidates": 4,
    "clips": list(STANDARD_CLIPS),
    "biped_extra_clips": list(BIPED_EXTRA_CLIPS),
    "render": {
        "look": "hd",
        "fps": 12,
        "supersample": 2,
        "palette_size": 32,
        "frame_size": {"hero": 256, "monster": 128, "boss": 256, "gear": 256, "weapon": 256},
    },
    "budgets": {
        "triangles": {"hero": 8000, "monster": 4000, "boss": 15000, "gear": 1500, "weapon": 1000},
        "texture": {"hero": 1024, "monster": 512, "boss": 1024, "gear": 512, "weapon": 512},
        "lod_ratios": [0.5, 0.2],
    },
}

# Fields of a profile that can be overridden per asset.
PROFILE_SETTING_KEYS = tuple(PROFILE_DEFAULTS.keys())


def profile_defaults() -> dict:
    return copy.deepcopy(PROFILE_DEFAULTS)


def deep_merge(base: dict, override: dict | None) -> dict:
    """Returns ``base`` with ``override`` merged in, recursing into dicts."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
