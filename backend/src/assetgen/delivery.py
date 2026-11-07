"""Sending files quickly: ETags (a revisit gets "not modified" instead of the file again), light
preview copies of GLBs for the 3D viewers, and small thumbnails.

The GLBs keep lossless PNG textures (several MB each with Hunyuan3D's PBR maps) for the exports;
the viewers only need a preview, so ``preview_glb`` makes a copy with JPEG textures beside the
file (``<name>.preview.glb``), remade when the file changes."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, Response
from PIL import Image

from . import imaging

PREVIEW_TEXTURE = 1024  # the longest side of a preview texture
JPEG_QUALITY = 85


def etag(path: Path) -> str:
    stat = path.stat()
    return f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'


def send(request: Request, path: Path, media_type: str | None = None, cache: str = "no-cache") -> Response:
    """The file, or 304 when the browser already has this version. ``no-cache`` means "ask each
    time", which with the ETag costs a tiny request instead of the whole file."""
    tag = etag(path)
    headers = {"ETag": tag, "Cache-Control": cache}
    if tag in (request.headers.get("if-none-match") or ""):
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type=media_type, headers=headers)


def _fresh(copy: Path, source: Path) -> bool:
    return copy.is_file() and copy.stat().st_mtime_ns >= source.stat().st_mtime_ns


def preview_glb(source: Path) -> Path:
    """A copy of ``source`` with its textures as JPEG (opaque ones) at most PREVIEW_TEXTURE wide."""
    copy = source.with_name(f"{source.stem}.preview.glb")
    if _fresh(copy, source):
        return copy
    gltf, binary = imaging.read_glb(source)
    for index in range(len(gltf.get("images") or [])):
        try:
            data = imaging.image_bytes(gltf, binary, index)
        except ValueError:
            continue
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            opaque = image.mode not in ("RGBA", "LA", "P") or image.getchannel("A").getextrema()[0] == 255
            small = image.convert("RGB" if opaque else "RGBA")
        small.thumbnail((PREVIEW_TEXTURE, PREVIEW_TEXTURE))
        out = io.BytesIO()
        if opaque:
            small.save(out, "JPEG", quality=JPEG_QUALITY)
        else:
            small.save(out, "PNG", optimize=True)
        gltf, binary = imaging.replace_image(gltf, binary, index, out.getvalue())
        gltf["images"][index]["mimeType"] = "image/jpeg" if opaque else "image/png"
    temporary = copy.with_suffix(".part")
    imaging.write_glb(temporary, gltf, binary)
    temporary.replace(copy)
    return copy


def thumbnail(source: Path, size: int) -> Path:
    """A JPEG of ``source`` at most ``size`` wide, kept beside it."""
    copy = source.with_name(f"{source.stem}_thumb{size}.jpg")
    if not _fresh(copy, source):
        with Image.open(source) as image:
            small = image.convert("RGB")
            small.thumbnail((size, size))
            small.save(copy, quality=85)
    return copy
