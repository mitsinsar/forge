"""What every image-to-mesh adapter does once its tool has sent a textured GLB.

The albedo is scaled to the profile's texture size, optionally de-lit, saved as
``texture.png`` beside ``out_path`` and put back into the GLB written there.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

from PIL import Image

from ... import imaging

OOM_WORDS = ("out of memory", "outofmemory", "cuda error: out of memory")


class MeshError(RuntimeError):
    """A readable failure of the mesh stage."""


def is_oom(text: str) -> bool:
    lowered = (text or "").lower()
    return any(word in lowered for word in OOM_WORDS)


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def finish_textured_glb(*, download: Path, out_path: Path, texture_size: int, delight: bool, progress,
                        tool: str, source: str, no_texture_hint: str) -> dict:
    """Turns the GLB a tool made (``download``) into the mesh stage's ``out_path`` and ``texture.png``.

    ``tool`` names the tool in errors; ``source`` is recorded in the GLB's asset extras;
    ``no_texture_hint`` says how to get a texture when the GLB has none."""
    out_path = Path(out_path)
    texture = out_path.with_name("texture.png")
    progress("Extracting the albedo texture", 0.9)
    try:
        gltf, binary = imaging.read_glb(download)
    except ValueError as exc:
        download.unlink(missing_ok=True)  # a retry downloads it again
        raise MeshError(f"{tool} sent a file that is not a GLB ({exc}).") from exc
    index = imaging.albedo_image_index(gltf)
    if index is None:
        raise MeshError(f"{tool} sent a mesh without an albedo texture. {no_texture_hint}")
    with Image.open(io.BytesIO(imaging.image_bytes(gltf, binary, index))) as albedo:
        albedo = albedo.convert("RGBA" if albedo.mode in ("RGBA", "LA", "P") else "RGB")
        if albedo.size != (texture_size, texture_size):
            albedo = albedo.resize((texture_size, texture_size), Image.Resampling.LANCZOS)
    if delight:
        progress("Evening out baked lighting", 0.95)
        albedo = imaging.delight(albedo)
    png = imaging.png_bytes(albedo)
    write_atomic(texture, png)
    gltf, binary = imaging.replace_image(gltf, binary, index, png)
    gltf.setdefault("asset", {}).setdefault("extras", {}).update(
        {"source": source, "delight": bool(delight), "texture_size": texture_size})
    tmp = out_path.with_name(out_path.name + ".part")
    imaging.write_glb(tmp, gltf, binary)
    os.replace(tmp, out_path)
    progress("Mesh ready", 1.0)
    return {"glb": out_path, "texture": texture, "triangles": imaging.triangle_count(gltf)}
