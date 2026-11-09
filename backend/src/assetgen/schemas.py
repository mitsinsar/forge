"""Request bodies (and the shapes /docs shows for them)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_EXTRA_LODS = 8

Engine = Literal["unity", "godot"]
Kind = Literal["hero", "monster", "boss", "gear", "weapon", "effect"]
BaseModelName = Literal["qwen-image", "sdxl", "flux"]
CameraPreset = Literal["side", "three_quarter", "topdown_4", "topdown_8"]
Slot = Literal["head", "chest", "legs", "hands", "feet", "cape"]
WeaponClass = Literal["one_handed", "two_handed", "bow", "staff", "shield"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- projects ---------------------------------------------------------------

class ProjectCreate(Strict):
    name: str = Field(min_length=1, max_length=80)
    engine: Engine
    path: str = Field("", description="The game project's folder (Unity project root or Godot res:// root).")
    default_profile_id: str | None = Field(
        None, description="Leave out to create a 'Default' style profile for the project.")


class ProjectUpdate(Strict):
    name: str | None = Field(None, min_length=1, max_length=80)
    engine: Engine | None = None
    path: str | None = None
    default_profile_id: str | None = None


# ---- style profiles ---------------------------------------------------------

class LoraRef(Strict):
    id: str
    weight: float = Field(1.0, ge=-2, le=2)


class FrameSizes(Strict):
    hero: int | None = Field(None, ge=16, le=2048)
    monster: int | None = Field(None, ge=16, le=2048)
    boss: int | None = Field(None, ge=16, le=2048)
    gear: int | None = Field(None, ge=16, le=2048)
    weapon: int | None = Field(None, ge=16, le=2048)


class RenderSettings(Strict):
    look: Literal["hd", "pixel"] | None = None
    fps: int | None = Field(None, ge=1, le=60)
    supersample: int | None = Field(None, ge=1, le=4)
    palette_size: int | None = Field(None, ge=2, le=256)
    frame_size: FrameSizes | None = None


class PerKind(Strict):
    hero: int | None = Field(None, ge=1)
    monster: int | None = Field(None, ge=1)
    boss: int | None = Field(None, ge=1)
    gear: int | None = Field(None, ge=1)
    weapon: int | None = Field(None, ge=1)


class Budgets(Strict):
    triangles: PerKind | None = None
    texture: PerKind | None = None
    lod_ratios: list[Annotated[float, Field(gt=0, lt=1)]] | None = Field(
        None, min_length=0, max_length=MAX_EXTRA_LODS, description="Extra LODs as fractions of LOD0, largest first.")


class MeshQuality(Strict):
    steps: int | None = Field(None, ge=1, le=100, description="Diffusion steps of the image-to-mesh model.")
    resolution: int | None = Field(None, ge=128, le=1024, description="Octree (marching cubes) resolution.")
    guidance: float | None = Field(None, ge=1, le=15, description="How closely the shape follows the image.")
    remove_floaters: bool | None = Field(None, description="Drop small pieces that do not touch the body.")
    multi_view: bool | None = Field(None, description="Draw right, back and left views first (MV-Adapter) and "
                                                      "build the shape from all four (Hunyuan3D-2mv).")
    model: Literal["hunyuan3d-2.1", "hunyuan3d-2-mini"] | None = Field(
        None, description="The image-to-3D model: Hunyuan3D 2.1 (shape and PBR texture) or Hunyuan3D 2 mini turbo "
                          "(Modly; fast).")


class ProfileSettings(Strict):
    """Every field a style profile holds; each can also be overridden per asset."""

    base_model: BaseModelName | None = None
    text_model: str | None = Field(None, max_length=200, description="The language model (an LM Studio model id) "
                                                                     "that writes prompts and judges results; empty: "
                                                                     "the default from settings.")
    one_model_at_a_time: bool | None = Field(None, description="Unload the language model while the pictures are "
                                                                "drawn and the image model after them, so only "
                                                                "one model holds the GPU.")
    prompt_suffix: str | None = None
    negative_prompt: str | None = None
    loras: list[LoraRef] | None = None
    camera_preset: CameraPreset | None = None
    delight: bool | None = None
    mesh: MeshQuality | None = None
    candidates: int | None = Field(None, ge=1, le=8)
    clips: list[str] | None = None
    biped_extra_clips: list[str] | None = None
    render: RenderSettings | None = None
    budgets: Budgets | None = None


class ProfileCreate(ProfileSettings):
    name: str = Field(min_length=1, max_length=80)
    project_id: str | None = None


class ProfileUpdate(ProfileSettings):
    name: str | None = Field(None, min_length=1, max_length=80)
    project_id: str | None = None


# ---- LoRAs ------------------------------------------------------------------

class LoraRegister(Strict):
    name: str = Field(min_length=1, max_length=120)
    path: str = Field(description="Path of the .safetensors file on the machine that runs ComfyUI.")
    base_model: BaseModelName
    tags: list[str] = []
    licence: str = ""
    commercial_use: bool = False
    source_url: str = ""
    trigger: str = Field("", max_length=200, description="Words the LoRA was trained on; put at the front of every "
                                                         "prompt that uses it (e.g. \"hkstyle\").")


class LoraUpdate(Strict):
    name: str | None = None
    path: str | None = None
    base_model: BaseModelName | None = None
    tags: list[str] | None = None
    licence: str | None = None
    commercial_use: bool | None = None
    source_url: str | None = None
    trigger: str | None = Field(None, max_length=200)


# ---- assets -----------------------------------------------------------------

class AssetCreate(Strict):
    project_id: str
    kind: Kind
    description: str = Field(min_length=1, max_length=2000)
    name: str | None = Field(None, max_length=80, description="Defaults to the first words of the description.")
    archetype: str | None = Field(None, description="Override Qwen's archetype (heroes, monsters, bosses).")
    slot: Slot | None = Field(None, description="Gear only.")
    weapon_class: WeaponClass | None = Field(None, description="Weapons only.")
    height: float | None = Field(None, ge=0.3, le=10.0, description="Characters only: height in metres "
                                 "(default by kind: hero 1.8, monster 1.5, boss 2.7).")
    profile_id: str | None = Field(None, description="Defaults to the project's default profile.")
    overrides: ProfileSettings | None = Field(None, description="Per-asset overrides of profile fields.")
    autopilot: bool = Field(False, description="Approve each gate by itself when nothing failed its checks; "
                                               "redraw, rebuild or try another picture when something did.")
    autopilot_export: bool = Field(False, description="With autopilot: also approve gate 3 (export) when its checks "
                                                      "pass. Off: it stops at gate 3 for you.")


class BatchCreate(Strict):
    asset: AssetCreate = Field(description="What every asset of the batch is made from.")
    count: int = Field(ge=1, le=20)
    vary: bool = Field(False, description="Have the LLM write a different description for each asset.")


class VariantCreate(Strict):
    name: str | None = Field(None, max_length=80, description="Defaults to the source's name plus \"variant\".")
    hue: float = Field(0.0, ge=-180, le=180, description="Turn the colours by this many degrees.")
    saturation: float = Field(1.0, ge=0, le=3)
    brightness: float = Field(1.0, ge=0.1, le=3)
    tint: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$", description="Pull the colours towards this one.")
    tint_strength: float = Field(0.0, ge=0, le=1)


class CleanRequest(Strict):
    categories: list[Literal["unirig_scratch", "thumbnails", "orphans", "stale_fits", "old_examples"]]


class Toggle(Strict):
    value: bool


class AutopilotSet(Strict):
    value: bool
    export: bool | None = Field(None, description="Also approve gate 3 (export) when everything passes.")


class Redo(Strict):
    stage: Literal["prompt", "mesh", "rig", "export"]
    note: str | None = Field(None, max_length=2000, description="Kept like a rejection note.")


class Approve(Strict):
    candidate: int | None = Field(None, ge=1, description="Gate 1: the picked candidate (1-based).")


class Grip(Strict):
    socket: Literal["weapon_r", "weapon_l"] | None = None
    position: list[float] | None = Field(None, min_length=3, max_length=3)
    rotation: list[float] | None = Field(None, min_length=3, max_length=3)


class Reject(Strict):
    note: str | None = Field(None, max_length=2000, description="Folded into the next attempt.")
    archetype: str | None = Field(None, description="Gate 1 only: switch archetype.")
    grip: Grip | None = Field(None, description="Gate 3, weapons only: adjust the grip.")
    height: float | None = Field(None, ge=0.3, le=10.0,
                                 description="Gate 2, characters only: rescale the mesh to this height in metres.")
    wearer: str | None = Field(None, description="Gate 3, gear and weapons only: refit on this rigged biped "
                                                  "hero of the project (its id); \"\" for the base mannequin.")
    joints: dict[str, list[float]] | None = Field(
        None, description="Gate 3, characters only: joints moved in the joint editor, by name, as rest positions "
                          "in the mesh's space (Blender axes: Z up, -Y front, metres). They replace UniRig's for "
                          "these joints and add up over rejections; {} goes back to the detected joints "
                          "(and clears the twists).")
    twists: dict[str, float] | None = Field(
        None, description="Gate 3, characters only: degrees each joint is turned about its own bone (which way a "
                          "knee or elbow faces); add up over rejections like joints.")


class FitRequest(Strict):
    wearer_id: str = Field(description="A rigged biped hero of the same project, or \"dummy\".")
    placement: list[list[float]] | None = Field(
        None, description="A move of the item on this hero, added to any kept one: a 4x4 matrix (rows) in "
                          "Blender world space at the rest pose (Z up, -Y front, metres), applied to the fitted item.")
    reset_placement: bool = Field(False, description="Forget this hero's placement and fit the item as detected.")


class ExamplesRequest(Strict):
    weight: float | None = Field(None, ge=0.0, le=2.0, description="LoRAs: the weight to show it at (default 0.8).")


class OutfitCreate(Strict):
    project_id: str
    name: str = Field(min_length=1, max_length=80)
    hero_id: str
    items: list[str] = Field(default_factory=list, description="Gear and weapon asset ids, one per slot.")


class OutfitUpdate(Strict):
    name: str | None = Field(None, min_length=1, max_length=80)
    hero_id: str | None = None
    items: list[str] | None = None


class MotionCreate(Strict):
    title: str = Field(min_length=1, max_length=60, description="The skill's name, e.g. \"Firebolt\" (the clip is skill_<title>).")
    description: str = Field(min_length=1, max_length=600, description="What the character does; the language model "
                             "turns it into the motion prompt.")
    prompt: str | None = Field(None, max_length=600, description="Your own motion prompt (\"A person ...\"), used as it is.")
    duration: float | None = Field(None, ge=1.0, le=10.0, description="Seconds (empty: the language model guesses).")
    in_place: bool = Field(True, description="Take the travel along the ground out (the hips still rise and fall).")


class MotionRedo(Strict):
    note: str | None = Field(None, max_length=600, description="What to change; the prompt is written again with it.")
    prompt: str | None = Field(None, max_length=600, description="Your own motion prompt instead.")
    duration: float | None = Field(None, ge=1.0, le=10.0)
    in_place: bool | None = None


class MotionUse(Strict):
    profile_id: str = Field(description="The style profile whose bipeds get this clip.")


class TrainingCandidate(Strict):
    value: bool
