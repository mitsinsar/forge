"""Image and GLB-texture helpers shared by the mesh and Blender adapters.

* ``remove_background`` cuts the subject out of a preview candidate. The
  candidates are rendered on a plain neutral background, so a flood fill from
  the border is enough; ``rembg`` is used instead when it is installed.
* ``delight`` evens out the brightness that Hunyuan3D bakes into its albedo
  (light from above, shade underneath), keeping the colours and fine detail.
* ``read_glb`` / ``write_glb`` / ``albedo_image`` / ``replace_image`` read the
  albedo texture of a GLB and put a new one in its place.
* ``finish_sprites`` turns Blender's supersampled sprite renders into the
  final frames: smooth downscale for the HD look; nearest neighbour and one
  shared palette for the pixel look.
"""

from __future__ import annotations

import base64
import io
import json
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# ---- background removal -----------------------------------------------------

BACKGROUND_TOLERANCE = 28  # per channel, 0-255
EDGE_SAMPLES = 64  # flood-fill seeds per image edge


def _rembg_cutout(image: Image.Image) -> Image.Image | None:
    try:
        from rembg import remove  # optional: uv pip install "rembg[gpu]"
    except ImportError:
        return None
    return remove(image).convert("RGBA")


def _flood_cutout(image: Image.Image) -> Image.Image | None:
    """Fills the background from the border pixels that share the corners'
    colour; ``None`` when the border is not a plain background."""
    rgb = image.convert("RGB")
    pixels = np.asarray(rgb).astype(np.int16)
    h, w, _ = pixels.shape
    corners = np.array([pixels[0, 0], pixels[0, -1], pixels[-1, 0], pixels[-1, -1]])
    background = np.median(corners, axis=0)
    if np.abs(corners - background).max() > BACKGROUND_TOLERANCE:
        return None
    border = np.concatenate([pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]])
    if (np.abs(border - background).max(axis=1) <= BACKGROUND_TOLERANCE).mean() < 0.6:
        return None
    # Flood fill a copy with a marker colour; what changed is background.
    sentinel = tuple(0 if c >= 128 else 255 for c in background)  # far from the background
    filled = rgb.copy()
    seeds = set()
    for i in range(EDGE_SAMPLES):
        x, y = round(i * (w - 1) / (EDGE_SAMPLES - 1)), round(i * (h - 1) / (EDGE_SAMPLES - 1))
        seeds.update({(x, 0), (x, h - 1), (0, y), (w - 1, y)})
    for x, y in sorted(seeds):
        value = np.asarray(filled.getpixel((x, y)), dtype=np.int16)
        if np.abs(value - background).max() <= BACKGROUND_TOLERANCE:
            ImageDraw.floodfill(filled, (x, y), sentinel, thresh=BACKGROUND_TOLERANCE)
    changed = np.any(np.asarray(filled).astype(np.int16) != pixels, axis=2)
    alpha = Image.fromarray(np.where(changed, 0, 255).astype(np.uint8))
    # Pull the edge in by a pixel and soften it, so no background halo is left.
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.7))
    cut = rgb.convert("RGBA")
    cut.putalpha(alpha)
    return cut


def remove_background(image: Image.Image) -> tuple[Image.Image, str]:
    """The image with a transparent background, and how it was cut out
    (``rembg``, ``flood`` or ``none`` when no plain background was found)."""
    if image.mode == "RGBA" and np.asarray(image.getchannel("A")).min() < 255:
        return image, "none"  # already cut out
    cut = _rembg_cutout(image)
    if cut is not None:
        return cut, "rembg"
    cut = _flood_cutout(image)
    if cut is not None:
        return cut, "flood"
    return image.convert("RGBA"), "none"


# ---- de-lighting ------------------------------------------------------------

DELIGHT_STRENGTH = 0.85  # 1.0 flattens all large-scale shading


def _box_blur(values: np.ndarray, radius: int, axis: int) -> np.ndarray:
    """Mean over a window of ``2 * radius + 1`` along ``axis``, edges repeated."""
    pad = [(0, 0)] * values.ndim
    pad[axis] = (radius + 1, radius)
    padded = np.pad(values, pad, mode="edge").astype(np.float64)
    sums = np.cumsum(padded, axis=axis)
    size = 2 * radius + 1
    upper = np.take(sums, range(size, sums.shape[axis]), axis=axis)
    lower = np.take(sums, range(0, sums.shape[axis] - size), axis=axis)
    return ((upper - lower) / size).astype(np.float32)


def delight(image: Image.Image, strength: float = DELIGHT_STRENGTH) -> Image.Image:
    """Divides out the low-frequency brightness of the albedo (the baked
    light and shade) so every region sits near the texture's mean brightness.
    Colour and detail smaller than about a sixteenth of the texture stay."""
    has_alpha = image.mode in ("RGBA", "LA")
    rgba = image.convert("RGBA")
    rgb = np.asarray(rgba.convert("RGB")).astype(np.float32) / 255.0
    alpha = np.asarray(rgba.getchannel("A")).astype(np.float32) / 255.0
    luma = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    # Unused texels (transparent or pure black atlas gaps) don't count.
    mask = ((alpha > 0.5) & (luma > 0.02)).astype(np.float32)
    if mask.sum() < 16:
        return image
    radius = max(2.0, max(image.size) / 16)

    def blur(values: np.ndarray) -> np.ndarray:
        for _ in range(3):  # three box blurs come close to a Gaussian
            values = _box_blur(values, int(radius), axis=0)
            values = _box_blur(values, int(radius), axis=1)
        return values

    weight = blur(mask)
    local = blur(luma * mask) / np.maximum(weight, 1e-4)
    mean = float((luma * mask).sum() / mask.sum())
    gain = (mean / np.maximum(local, 0.03)) ** strength
    gain = np.clip(gain, 0.4, 2.5)[..., None]
    out = np.clip(rgb * gain, 0.0, 1.0)
    out = np.where(mask[..., None] > 0, out, rgb)
    result = Image.fromarray((out * 255 + 0.5).astype(np.uint8), mode="RGB")
    if has_alpha:
        result.putalpha(rgba.getchannel("A"))
    return result


# ---- GLB albedo texture -----------------------------------------------------

GLB_MAGIC, JSON_CHUNK, BIN_CHUNK = b"glTF", b"JSON", b"BIN\x00"


def read_glb(path: Path) -> tuple[dict, bytes]:
    """The JSON chunk and the binary chunk of a GLB file."""
    return parse_glb(Path(path).read_bytes(), Path(path).name)


def parse_glb(data: bytes, name: str = "the file") -> tuple[dict, bytes]:
    if len(data) < 20:
        raise ValueError(f"{name} is too short to be a GLB")
    magic, version, _ = struct.unpack_from("<4sII", data, 0)
    if magic != GLB_MAGIC or version != 2:
        raise ValueError(f"{name} is not a glTF 2.0 binary")
    offset, gltf, binary = 12, None, b""
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from("<I4s", data, offset)
        chunk = data[offset + 8:offset + 8 + length]
        if kind == JSON_CHUNK:
            gltf = json.loads(chunk)
        elif kind == BIN_CHUNK:
            binary = chunk
        offset += 8 + length
    if gltf is None:
        raise ValueError(f"{name} has no JSON chunk")
    return gltf, binary


def glb_bytes(gltf: dict, binary: bytes) -> bytes:
    raw = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    raw += b" " * (-len(raw) % 4)
    binary = binary + b"\x00" * (-len(binary) % 4)
    chunks = struct.pack("<I4s", len(raw), JSON_CHUNK) + raw
    if binary:
        chunks += struct.pack("<I4s", len(binary), BIN_CHUNK) + binary
    return struct.pack("<4sII", GLB_MAGIC, 2, 12 + len(chunks)) + chunks


def write_glb(path: Path, gltf: dict, binary: bytes) -> None:
    Path(path).write_bytes(glb_bytes(gltf, binary))


def albedo_image_index(gltf: dict) -> int | None:
    """Index of the image used as base colour by the first textured material."""
    for material in gltf.get("materials", []):
        info = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
        if info is not None:
            texture = gltf["textures"][info["index"]]
            if "source" in texture:
                return texture["source"]
    return None


def image_bytes(gltf: dict, binary: bytes, index: int) -> bytes:
    """The bytes of image ``index``: from the binary chunk, or from a base64 ``data:`` URI
    (Hunyuan3D 2.1 writes its textures that way)."""
    image = gltf["images"][index]
    if "bufferView" not in image:
        uri = image.get("uri") or ""
        if uri.startswith("data:") and ";base64," in uri:
            return base64.b64decode(uri.split(";base64,", 1)[1])
        raise ValueError("the albedo image is not stored inside the GLB")
    view = gltf["bufferViews"][image["bufferView"]]
    start = view.get("byteOffset", 0)
    return binary[start:start + view["byteLength"]]


def albedo_image(gltf: dict, binary: bytes) -> Image.Image | None:
    index = albedo_image_index(gltf)
    if index is None:
        return None
    image = Image.open(io.BytesIO(image_bytes(gltf, binary, index)))
    image.load()
    return image


def replace_image(gltf: dict, binary: bytes, index: int, png: bytes) -> tuple[dict, bytes]:
    """Puts ``png`` in place of image ``index`` and repacks the binary chunk
    (every buffer view of buffer 0, 4-byte aligned). An image held as a ``data:`` URI
    moves into the binary chunk."""
    views = gltf.setdefault("bufferViews", [])
    image = gltf["images"][index]
    if "bufferView" not in image:
        image.pop("uri", None)
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": 0})
        image["bufferView"] = len(views) - 1
        if not gltf.get("buffers"):
            gltf["buffers"] = [{"byteLength": 0}]
    target = image["bufferView"]
    packed = bytearray()
    for i, view in enumerate(views):
        if view.get("buffer", 0) != 0:
            continue
        start = view.get("byteOffset", 0)
        data = png if i == target else binary[start:start + view["byteLength"]]
        packed += b"\x00" * (-len(packed) % 4)
        view["byteOffset"] = len(packed)
        view["byteLength"] = len(data)
        packed += data
    gltf["images"][index]["mimeType"] = "image/png"
    gltf["buffers"][0]["byteLength"] = len(packed)
    return gltf, bytes(packed)


def png_bytes(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=False)
    return out.getvalue()


def triangle_count(gltf: dict) -> int:
    """Triangles over all mesh primitives (indexed or not), counting each
    mesh once."""
    total = 0
    for mesh in gltf.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            if primitive.get("mode", 4) != 4:
                continue
            if "indices" in primitive:
                total += gltf["accessors"][primitive["indices"]]["count"] // 3
            elif "POSITION" in primitive.get("attributes", {}):
                total += gltf["accessors"][primitive["attributes"]["POSITION"]]["count"] // 3
    return total


# ---- sprite frames ----------------------------------------------------------

PALETTE_SAMPLES = 250_000  # opaque pixels sampled over all frames to build the pixel palette
ALPHA_CUT = 128  # pixel look: alpha at or above this is opaque, below is transparent


def downscale_hd(image: Image.Image, size: int) -> Image.Image:
    """Smooth (Lanczos) downscale; premultiplied so the transparent edge keeps no dark fringe."""
    return image.convert("RGBa").resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")


def downscale_pixel(image: Image.Image, size: int) -> Image.Image:
    """Nearest-neighbour downscale with hard (fully opaque or fully transparent) alpha."""
    small = image.convert("RGBA").resize((size, size), Image.Resampling.NEAREST)
    pixels = np.asarray(small).copy()
    opaque = pixels[..., 3] >= ALPHA_CUT
    pixels[..., 3] = np.where(opaque, 255, 0)
    pixels[~opaque, :3] = 0
    return Image.fromarray(pixels, "RGBA")


def shared_palette(frames: list[Image.Image], colors: int, seed: int = 0) -> Image.Image | None:
    """A ``colors``-entry palette (a "P" image) built from the opaque pixels of all
    ``frames``, so every frame and clip uses the same colours; ``None`` when all are empty."""
    opaque = [np.asarray(f)[..., :3][np.asarray(f)[..., 3] > 0] for f in frames]
    pixels = np.concatenate(opaque) if opaque else np.zeros((0, 3), np.uint8)
    if not len(pixels):
        return None
    if len(pixels) > PALETTE_SAMPLES:
        pixels = pixels[np.random.default_rng(seed).choice(len(pixels), PALETTE_SAMPLES, replace=False)]
    strip = Image.fromarray(pixels.reshape(1, -1, 3).astype(np.uint8), "RGB")
    return strip.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def apply_palette(frame: Image.Image, palette: Image.Image | None) -> Image.Image:
    """Locks a hard-alpha frame's colours to ``palette`` (no dithering)."""
    if palette is None:
        return frame
    alpha = frame.getchannel("A")
    rgb = frame.convert("RGB").quantize(palette=palette, dither=Image.Dither.NONE).convert("RGB")
    rgb.putalpha(alpha)
    pixels = np.asarray(rgb).copy()
    pixels[pixels[..., 3] == 0, :3] = 0
    return Image.fromarray(pixels, "RGBA")


def is_empty(image: Image.Image) -> bool:
    return image.getchannel("A").getextrema()[1] == 0


def finish_sprites(raw: dict[str, dict[str, list[Path]]], loops: dict[str, bool], out_dir: Path, *,
                   frame_size: int, look: str, palette_size: int) -> dict[str, dict[str, list[Path]]]:
    """Writes the final frames to ``out_dir/<direction>/<clip>/<n>.png`` and returns their paths.

    ``raw`` holds the supersampled renders per direction and clip. Frames at
    the end of a clip that does not loop and are fully transparent in every
    direction (the death fading out, say) are dropped, keeping at least one."""
    small: dict[str, dict[str, list[Image.Image]]] = {}
    for direction, by_clip in raw.items():
        small[direction] = {}
        for clip, paths in by_clip.items():
            frames = []
            for path in paths:
                with Image.open(path) as image:
                    frames.append(downscale_pixel(image, frame_size) if look == "pixel"
                                  else downscale_hd(image, frame_size))
            small[direction][clip] = frames
    for clip in {c for by_clip in small.values() for c in by_clip}:
        if loops.get(clip, True):
            continue
        lists = [by_clip[clip] for by_clip in small.values() if clip in by_clip]
        while min(len(f) for f in lists) > 1 and all(is_empty(f[-1]) for f in lists):
            for frames in lists:
                frames.pop()
    if look == "pixel":
        palette = shared_palette([f for by_clip in small.values() for fs in by_clip.values() for f in fs],
                                 palette_size)
        small = {d: {c: [apply_palette(f, palette) for f in fs] for c, fs in by_clip.items()}
                 for d, by_clip in small.items()}
    result: dict[str, dict[str, list[Path]]] = {}
    for direction, by_clip in small.items():
        result[direction] = {}
        for clip, frames in by_clip.items():
            folder = out_dir / direction / clip
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.glob("*.png"):
                old.unlink()
            paths = []
            for index, frame in enumerate(frames):
                path = folder / f"{index:03d}.png"
                frame.save(path)
                paths.append(path)
            result[direction][clip] = paths
    return result
