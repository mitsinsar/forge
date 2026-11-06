"""Adapter interfaces for the external tools.

The pipeline only talks to these interfaces. The real adapters in ``real/``
implement them; the tests swap in the doubles in ``backend/tests/fakes``.

Every long call takes a ``progress(step, fraction)`` callback.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

Progress = Callable[[str, float], None]


class LLMAdapter(Protocol):
    """Qwen via an OpenAI-compatible endpoint, answering structured JSON."""

    def expand(self, *, description: str, kind: str, archetypes: dict[str, dict],
               archetype_override: str | None, slot: str | None, weapon_class: str | None,
               notes: list[str]) -> dict:
        """Returns ``{"archetype", "prompt", "negative_prompt", "slot", "weapon_class"}``.

        The prompt must force the archetype's rest pose and fold in the
        reviewer's ``notes`` from earlier rejected rounds."""


class ImageAdapter(Protocol):
    """ComfyUI (Qwen-Image, SDXL or Flux per style profile)."""

    def render_candidates(self, *, prompt: str, negative_prompt: str, base_model: str,
                          loras: list[dict], reference_images: list[Path], count: int, seed: int,
                          out_dir: Path, progress: Progress, hints: dict,
                          log: Callable[[str], None] | None = None) -> list[Path]:
        """Renders ``count`` PNGs into ``out_dir`` as candidate_<n>.png and
        returns their paths. Existing files are kept (resume). ``log`` takes
        lines for the stage log shown at gate 1 (for example reference images
        a base model can't use)."""

    def unload(self) -> None:
        """Frees the image model's VRAM before the mesh stage."""


class MeshAdapter(Protocol):
    """Hunyuan3D 2.1: image to textured mesh."""

    def image_to_mesh(self, *, image: Path, texture_size: int, out_path: Path,
                      progress: Progress, hints: dict, delight: bool = True) -> dict:
        """Removes the image's background, writes a textured GLB to
        ``out_path`` and its albedo texture beside it as texture.png at
        ``texture_size``. With ``delight`` the albedo's baked light and shade
        are evened out. Returns ``{"glb": path, "texture": path}``."""


class RigAdapter(Protocol):
    """UniRig: skeleton and skin-weight prediction."""

    def predict_skeleton(self, *, mesh: Path, archetype: dict, out_dir: Path, progress: Progress) -> dict:
        """Returns the template joints fitted to the mesh:
        ``{"joints": [...], "fit_score": 0..1, "fallback": bool, "file": path}``."""


class BlenderAdapter(Protocol):
    """Headless Blender (``blender -b -P script.py``)."""

    def cleanup_and_lods(self, *, raw_mesh: Path, kind: str, triangles: int, lod_ratios: list[float],
                         out_dir: Path, progress: Progress) -> dict:
        """Cleans up, scales to the kind's standard size with the origin at the
        feet, decimates to the budget and makes the LODs (``lod_ratios`` of
        LOD0). Writes ``out_dir/mesh.glb`` with one node per level (``LOD0``,
        ``LOD1``...). Returns ``{"glb": path, "lods": [{"level", "ratio", "triangles"}]}``."""

    def rig_and_animate(self, *, mesh_glb: Path, skeleton: dict, archetype: dict, clips: list[str],
                        attach: dict | None, out_path: Path, progress: Progress) -> dict:
        """Skins the mesh to the fitted template skeleton and retargets the
        clips. ``attach`` places gear or a weapon on the mannequin. Returns
        ``{"glb": path, "clips": [{"name", "duration", "loop"}]}``."""

    def render_sprites(self, *, animated_glb: Path, clips: list[dict], directions: list[str],
                       camera_preset: str, frame_size: int, supersample: int, fps: int, look: str,
                       palette_size: int, out_dir: Path, progress: Progress) -> dict[str, dict[str, list[Path]]]:
        """Eevee orthographic render with toon or pixel shading at
        ``supersample``x, downscaled to ``frame_size``. Returns
        ``{direction: {clip: [frame paths]}}``."""

    def export_fbx(self, *, animated_glb: Path, mesh_glb: Path, lods: list[dict], rig_type: str,
                   out_path: Path, progress: Progress) -> Path:
        """Exports mesh, skeleton and clips from ``animated_glb`` and the lower
        LODs from ``mesh_glb`` to one FBX for Unity (``<name>_LOD<n>`` meshes,
        one take per clip)."""


class Adapters:
    def __init__(self, llm: LLMAdapter, image: ImageAdapter, mesh: MeshAdapter, rig: RigAdapter,
                 blender: BlenderAdapter):
        self.llm = llm
        self.image = image
        self.mesh = mesh
        self.rig = rig
        self.blender = blender

