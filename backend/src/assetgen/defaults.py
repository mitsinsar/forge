"""Fixed vocabularies and the style-profile defaults from the spec."""

from __future__ import annotations

import copy
import hashlib

KINDS =("hero", "monster", "boss", "gear", "weapon")
ENGINES = ("unity", "godot")
BASE_MODELS = ("qwen-image", "sdxl", "flux")
CAMERA_PRESETS = ("side", "three_quarter", "topdown_4", "topdown_8")
SPRITE_LOOKS = ("hd", "pixel")
GEAR_SLOTS = ("head", "chest", "legs", "hands", "feet", "cape")
# Slots worn on both sides (gloves, boots, leg armour): only the right piece is drawn and built, and
# it is mirrored for the left when fitted. A drawn pair comes out of Hunyuan3D as one glued shape.
PAIR_SLOTS = ("hands", "feet", "legs")
PAIR_PIECES = {  # what is drawn for each pair slot
    "hands": "one single right glove or gauntlet, open and flat, standing upright with all fingers straight, "
             "together and pointing up, the back of the hand towards the viewer",
    "feet": "one single right boot, standing upright on its sole, seen from the front at a slight angle",
    "legs": "the armour of one single right leg (thigh guard, knee cop and shin guard as one piece), "
            "standing upright, seen from the front",
}
PAIR_NEGATIVE = "pair, two of them, both left and right, matching set, second piece, two boots, two gloves"
WEAPON_CLASSES = ("one_handed", "two_handed", "bow", "staff", "shield")
WEAPON_SOCKETS = ("weapon_r", "weapon_l")

# Grip point (where the hand holds it) relative to the weapon origin, per weapon class.
DEFAULT_GRIPS = {
    "one_handed": {"socket": "weapon_r", "position": [0.0, -0.06, 0.0], "rotation": [0.0, 0.0, 0.0]},
    "two_handed": {"socket": "weapon_r", "position": [0.0, -0.12, 0.0], "rotation": [0.0, 0.0, 0.0]},
    "bow": {"socket": "weapon_l", "position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0]},
    "staff": {"socket": "weapon_r", "position": [0.0, 0.1, 0.0], "rotation": [0.0, 0.0, 0.0]},
    "shield": {"socket": "weapon_l", "position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0]},
}


def seed_of(*values) -> int:
    """A stable 64-bit seed from any values (the same asset and attempt always get the same seed)."""
    digest = hashlib.sha256("|".join(str(v) for v in values).encode()).digest()
    return int.from_bytes(digest[:8], "big")

# Directions rendered per camera preset. Directions in MIRRORED are drawn
# by flipping their partner with flip_h in Godot instead of rendering them.
CAMERA_DIRECTIONS: dict[str, list[str]] = {
    "side": ["right"],
    "three_quarter": ["down_right", "up_right"],
    "topdown_4": ["down", "right", "up", "left"],
    "topdown_8": ["down", "down_right", "right", "up_right", "up", "up_left", "left", "down_left"],
}
# Camera yaw per direction in degrees around the character (0 looks at its
# face, 90 shows it facing screen right) and the camera's tilt per preset.
DIRECTION_YAW: dict[str, float] = {"down": 0, "down_right": 45, "right": 90, "up_right": 135, "up": 180,
                                   "up_left": 225, "left": 270, "down_left": 315}
# Example renders for LoRAs and style profiles: the same three subjects and seed every time, so
# sheets compare. ``hints`` say what each subject is (the test doubles draw from them).
EXAMPLE_SUBJECTS = [
    {"name": "knight", "prompt": "a knight in plate armour, full body, standing in A-pose, facing the camera, "
                                 "plain light-grey background, even studio lighting",
     "hints": {"kind": "hero", "archetype": "biped"}},
    {"name": "wolf", "prompt": "a grey wolf, full body, side view, plain light-grey background, even studio lighting",
     "hints": {"kind": "monster", "archetype": "quadruped"}},
    {"name": "sword", "prompt": "a steel longsword, upright, side-on, centred, plain light-grey background",
     "hints": {"kind": "weapon", "archetype": "biped", "weapon_class": "one_handed"}},
]
EXAMPLE_NEGATIVE = "blurry, cropped, text, watermark, extra limbs, several subjects"
EXAMPLE_SEED = 20261107
EXAMPLE_LORA_WEIGHT = 0.8

# Paper-doll framing: biped heroes, gear and weapons render with this one camera framing (ortho
# scale, and the height its centre looks at, in metres) so their Godot layers stack. It holds the
# 1.8 m base body swinging a 1 m weapon.
PAPER_DOLL_FRAMING = {"span": 4.8, "height": 0.9}
CAMERA_TILT: dict[str, float] = {"side": 0, "three_quarter": 30, "topdown_4": 50, "topdown_8": 50}
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
    "shield": ["attack_stab", "attack_strike"],  # a jab with the left (a shield bash) and a punch
}

STAGES = ("prompt", "mesh", "rig", "export")
GATE_OF_STAGE = {"prompt": 1, "mesh": 2, "rig": 3}
STAGE_OF_GATE = {v: k for k, v in GATE_OF_STAGE.items()}

# Standard model size per kind after the mesh clean-up, in metres, and what it
# measures: "height" (feet to top) or "longest" (the longest side, for weapons).
# Gear is previewed worn by the mannequin, so it is scaled like a hero.
MODEL_SIZES: dict[str, tuple[float, str]] = {
    "hero": (1.8, "height"), "monster": (1.5, "height"), "boss": (2.7, "height"),
    "gear": (1.8, "height"), "weapon": (1.0, "longest"),
}

# Archetypes whose template is fitted to a mesh by height; the others by the template's longest side.
FIT_BY_HEIGHT = {"biped", "blob"}

PROFILE_DEFAULTS: dict = {
    "base_model": "sdxl",
    "text_model": "",  # empty: settings.qwen_model
    "prompt_suffix": "game asset, full body, plain background, flat even lighting",
    "negative_prompt": "cropped, blurry, text, watermark, multiple characters, cast shadow",
    "loras": [],
    "camera_preset": "side",
    # Even out the light and shade Hunyuan3D bakes into the albedo.
    "delight": True,
    # The image-to-mesh run (gate 2). steps and resolution trade time for detail; guidance
    # is how closely the shape follows the image; remove_floaters drops the small loose
    # pieces image-to-3D models leave floating around the body.
    # The defaults are Hunyuan3D 2.1's own (50 steps, octree 384, guidance 5.0).
    "mesh": {"steps": 50, "resolution": 384, "guidance": 5.0, "remove_floaters": True, "multi_view": False,
             "model": "hunyuan3d-2.1"},
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
        "triangles": {"hero": 8000, "monster": 4000, "boss": 15000, "gear": 1500, "weapon": 800},
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


# The image-to-3D models a profile can pick (mesh.model), and the adapter each runs on.
MESH_MODELS: dict[str, dict] = {
    "hunyuan3d-2.1": {"label": "Hunyuan3D 2.1 (shape and PBR texture)", "tool": "hunyuan3d"},
    "hunyuan3d-2-mini": {"label": "Hunyuan3D 2 mini turbo (Modly, fast)", "tool": "modly"},
}

# Auto mode: how much a judged result may miss before it is redone.
JUDGE_FAIL, JUDGE_WARN = 5, 7  # the vision model's 0-10 "matches" score: below FAIL fails, below WARN warns
AUTO_MESH_RETRIES = 1  # rebuilds of a mesh (new seed) before trying the next picture
AUTO_PICTURES = 3  # pictures a mesh is tried from before gate 1 is redrawn
AUTO_REDRAWS = 1  # redraws of gate 1 (with the judge's notes) before autopilot stops for you
