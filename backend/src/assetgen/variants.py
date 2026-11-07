"""Colour variants: a copy of a rigged asset with its texture recoloured ("same knight, black
armour"). The mesh, rig and clips are kept, so a variant takes seconds, fits the same gear and
plays in the same outfits. The recolour is applied to the texture inside every file that holds
it (the gate 2 and gate 3 GLBs, the texture PNG) and to the preview image."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from . import imaging


def recolour(image: Image.Image, *, hue: float = 0.0, saturation: float = 1.0, brightness: float = 1.0,
             tint: str | None = None, tint_strength: float = 0.0) -> Image.Image:
    """``hue`` turns colours by that many degrees, ``saturation`` and ``brightness`` scale them, and
    ``tint`` (``#rrggbb``) pulls them towards one colour by ``tint_strength`` (0..1) while keeping
    their lightness. Transparency is kept."""
    alpha = image.getchannel("A") if image.mode in ("RGBA", "LA") else None
    hsv = np.asarray(image.convert("RGB").convert("HSV"), dtype=np.float32)
    hsv[..., 0] = (hsv[..., 0] + hue / 360.0 * 255.0) % 255.0
    hsv[..., 1] = np.clip(hsv[..., 1] * saturation, 0, 255)
    hsv[..., 2] = np.clip(hsv[..., 2] * brightness, 0, 255)
    rgb = np.asarray(Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB"), dtype=np.float32)
    if tint and tint_strength > 0:
        colour = np.array([int(tint[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float32)
        light = rgb.mean(axis=2, keepdims=True) / 255.0
        tinted = np.clip(colour * light * 2.0, 0, 255)  # mid-grey takes the tint colour itself
        rgb = rgb * (1 - tint_strength) + tinted * tint_strength
    out = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
    if alpha is not None:
        out.putalpha(alpha)
    return out


def recolour_file(path: Path, **params) -> None:
    """Recolours an image file, or every image embedded in a GLB, in place."""
    if path.suffix.lower() == ".glb":
        gltf, binary = imaging.read_glb(path)
        for index in range(len(gltf.get("images") or [])):
            data = imaging.image_bytes(gltf, binary, index)
            if not data:
                continue
            with Image.open(io.BytesIO(data)) as image:
                png = imaging.png_bytes(recolour(image.convert("RGBA"), **params))
            gltf, binary = imaging.replace_image(gltf, binary, index, png)
        imaging.write_glb(path, gltf, binary)
    else:
        with Image.open(path) as image:
            mode = image.mode
            result = recolour(image.convert("RGBA") if "A" in mode else image.convert("RGB"), **params)
        result.save(path)
