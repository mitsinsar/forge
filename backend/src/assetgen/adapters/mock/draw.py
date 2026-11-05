"""Draws the placeholder parts to 2D images (candidates and sprite frames)."""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFont

from .glb import Part
from .shapes import pose

Vec = tuple[float, float, float]


def _rotate(v: Vec, axis: Vec, angle: float) -> Vec:
    """Rodrigues rotation of ``v`` about a unit ``axis``."""
    if not angle:
        return v
    c, s = math.cos(angle), math.sin(angle)
    kx, ky, kz = axis
    vx, vy, vz = v
    dot = kx * vx + ky * vy + kz * vz
    cross = (ky * vz - kz * vy, kz * vx - kx * vz, kx * vy - ky * vx)
    return tuple(v[i] * c + cross[i] * s + (kx, ky, kz)[i] * dot * (1 - c) for i in range(3))


class Camera:
    """Orthographic camera: ``yaw`` turns the model (0 = facing the viewer,
    pi/2 = facing right), ``tilt`` pitches the camera down."""

    def __init__(self, size: int, pixels_per_metre: float, yaw: float = 0.0, tilt: float = 0.0,
                 ground: float = 0.9):
        self.size = size
        self.ppm = pixels_per_metre
        self.yaw = yaw
        self.tilt = tilt
        self.ground = ground  # ground line as a fraction of the height

    def _view(self, v: Vec) -> Vec:
        x, y, z = _rotate(v, (0, 1, 0), self.yaw)
        ct, st = math.cos(self.tilt), math.sin(self.tilt)
        return x, y * ct - z * st, z * ct + y * st  # screen x, screen up, depth

    def project(self, v: Vec) -> tuple[float, float, float]:
        sx, sy, depth = self._view(v)
        origin_y = self.size * self.ground - (self.size * 0.35 * math.sin(self.tilt))
        return self.size / 2 + sx * self.ppm, origin_y - sy * self.ppm, depth

    def extent(self, axes: list[Vec]) -> tuple[float, float]:
        views = [self._view(a) for a in axes]
        hx = math.sqrt(sum(v[0] ** 2 for v in views)) * self.ppm
        hy = math.sqrt(sum(v[1] ** 2 for v in views)) * self.ppm
        return hx, hy


def _shade(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(c * factor))) for c in color)


def draw_parts(image: Image.Image, camera: Camera, layers: list[tuple[list[Part], tuple[int, int, int]]],
               clip: str | None = None, t: float = 0.0, outline: bool = True) -> None:
    """Draws every layer's parts, far ones first, posed for ``clip`` at ``t``."""
    translation, (axis, angle), scale = pose(clip, t) if clip else ((0, 0, 0), ((0, 0, 1), 0), (1, 1, 1))
    shapes = []
    for parts, color in layers:
        for part in parts:
            center = tuple(part.center[i] * scale[i] for i in range(3))
            center = _rotate(center, axis, angle)
            center = tuple(center[i] + translation[i] for i in range(3))
            axes = [_rotate(tuple(part.radii[i] * scale[i] if j == i else 0.0 for j in range(3)), axis, angle)
                    for i in range(3)]
            x, y, depth = camera.project(center)
            hx, hy = camera.extent(axes)
            shapes.append((depth, x, y, hx, hy, color))
    shapes.sort(key=lambda s: s[0])
    draw = ImageDraw.Draw(image)
    width = max(1, round(camera.size / 160))
    for depth, x, y, hx, hy, color in shapes:
        fill = _shade(color, 0.85 + 0.25 * max(-1.0, min(1.0, depth)))
        box = (x - hx, y - hy, x + hx, y + hy)
        draw.ellipse(box, fill=(*fill, 255), outline=(*_shade(color, 0.45), 255) if outline else None,
                     width=width)
        # a small highlight gives the blobs some volume
        draw.ellipse((x - hx * 0.45, y - hy * 0.7, x - hx * 0.05, y - hy * 0.3),
                     fill=(*_shade(fill, 1.18), 255))


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()
