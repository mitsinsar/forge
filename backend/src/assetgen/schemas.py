"""Request bodies (and the shapes /docs shows for them)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_EXTRA_LODS = 8

Engine = Literal["unity", "godot"]
Kind = Literal["hero", "monster", "boss", "gear", "weapon"]
BaseModelName = Literal["qwen-image", "sdxl", "flux"]
CameraPreset = Literal["side", "three_quarter", "topdown_4", "topdown_8"]
Slot = Literal["head", "chest", "legs", "hands", "feet", "cape"]
WeaponClass = Literal["one_handed", "two_handed", "bow", "staff"]


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


class ProfileSettings(Strict):
    """Every field a style profile holds; each can also be overridden per asset."""

    base_model: BaseModelName | None = None
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


class LoraUpdate(Strict):
    name: str | None = None
    path: str | None = None
    base_model: BaseModelName | None = None
    tags: list[str] | None = None
    licence: str | None = None
    commercial_use: bool | None = None
    source_url: str | None = None


# ---- assets -----------------------------------------------------------------

class AssetCreate(Strict):
    project_id: str
    kind: Kind
    description: str = Field(min_length=1, max_length=2000)
    name: str | None = Field(None, max_length=80, description="Defaults to the first words of the description.")
    archetype: str | None = Field(None, description="Override Qwen's archetype (heroes, monsters, bosses).")
    slot: Slot | None = Field(None, description="Gear only.")
    weapon_class: WeaponClass | None = Field(None, description="Weapons only.")
    profile_id: str | None = Field(None, description="Defaults to the project's default profile.")
    overrides: ProfileSettings | None = Field(None, description="Per-asset overrides of profile fields.")


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


class TrainingCandidate(Strict):
    value: bool
