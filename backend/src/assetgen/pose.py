"""The biped pose guide and the gate 1 arms check.

Image-to-3D models fuse parts that touch: hands resting on the thighs become one piece with
them, and the rig can't pull them apart cleanly. So biped characters are drawn in a wide
A-pose, guided by an OpenPose skeleton image through a ControlNet (``guide_image``), and gate 1
warns about candidates whose arms still touch the body (``arms_check``).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# OpenPose (COCO 18) keypoints of a figure facing the camera in a wide A-pose (arms 45 degrees
# down, hands well clear of the hips, legs slightly apart), as fractions of a square image.
# Its right side is on the image's left.
A_POSE = {
    "nose": (0.500, 0.165), "neck": (0.500, 0.245),
    "r_shoulder": (0.405, 0.255), "r_elbow": (0.300, 0.355), "r_wrist": (0.205, 0.455),
    "l_shoulder": (0.595, 0.255), "l_elbow": (0.700, 0.355), "l_wrist": (0.795, 0.455),
    "r_hip": (0.450, 0.520), "r_knee": (0.435, 0.715), "r_ankle": (0.420, 0.905),
    "l_hip": (0.550, 0.520), "l_knee": (0.565, 0.715), "l_ankle": (0.580, 0.905),
    "r_eye": (0.485, 0.150), "l_eye": (0.515, 0.150), "r_ear": (0.465, 0.160), "l_ear": (0.535, 0.160),
}
ORDER = ["nose", "neck", "r_shoulder", "r_elbow", "r_wrist", "l_shoulder", "l_elbow", "l_wrist", "r_hip",
         "r_knee", "r_ankle", "l_hip", "l_knee", "l_ankle", "r_eye", "l_eye", "r_ear", "l_ear"]
# The OpenPose limb list and colours (the ones the ControlNet was trained on).
LIMBS = [(2, 3), (2, 6), (3, 4), (4, 5), (6, 7), (7, 8), (2, 9), (9, 10), (10, 11), (2, 12), (12, 13),
         (13, 14), (2, 1), (1, 15), (15, 17), (1, 16), (16, 18)]
COLOURS = [(255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0), (85, 255, 0), (0, 255, 0),
           (0, 255, 85), (0, 255, 170), (0, 255, 255), (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255),
           (170, 0, 255), (255, 0, 255), (255, 0, 170), (255, 0, 85)]


def guide_image(path: Path, size: int = 1024) -> Path:
    """Writes the A-pose OpenPose skeleton (black background) to ``path``."""
    image = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    points = [(A_POSE[name][0] * size, A_POSE[name][1] * size) for name in ORDER]
    width = size / 128  # OpenPose's stick width of 4 at 512 px
    for index, (a, b) in enumerate(LIMBS):
        (x1, y1), (x2, y2) = points[a - 1], points[b - 1]
        length = math.hypot(x2 - x1, y2 - y1)
        angle = math.atan2(y2 - y1, x2 - x1)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        outline = []
        for step in range(0, 360, 10):
            t = math.radians(step)
            ex, ey = length / 2 * math.cos(t), width * math.sin(t)
            outline.append((cx + ex * math.cos(angle) - ey * math.sin(angle),
                            cy + ex * math.sin(angle) + ey * math.cos(angle)))
        draw.polygon(outline, fill=tuple(int(c * 0.6) for c in COLOURS[index]))
    for index, (x, y) in enumerate(points):
        draw.ellipse((x - width, y - width, x + width, y + width), fill=COLOURS[index])
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def _smooth(line: np.ndarray, width: int) -> np.ndarray:
    """A border line with anything narrower than ``width`` (a foot, a shadow) median-filtered away."""
    half = max(1, width // 2)
    padded = np.pad(line, ((half, half), (0, 0)), mode="edge")
    return np.stack([np.median(padded[i:i + 2 * half + 1], axis=0) for i in range(len(line))])


def background_model(pixels: np.ndarray) -> np.ndarray:
    """The plain background behind the figure, from the four image borders blended across the image
    (a Coons patch), so vertical and radial gradients and vignettes are followed."""
    height, width, _ = pixels.shape
    span = max(height, width) // 6
    top, bottom = _smooth(pixels[0], span), _smooth(pixels[-1], span)
    left, right = _smooth(pixels[:, 0], span), _smooth(pixels[:, -1], span)
    u = np.linspace(0, 1, width)[None, :, None]
    v = np.linspace(0, 1, height)[:, None, None]
    corners = (1 - u) * (1 - v) * top[0] + u * (1 - v) * top[-1] + (1 - u) * v * bottom[0] + u * v * bottom[-1]
    return (1 - v) * top[None] + v * bottom[None] + (1 - u) * left[:, None] + u * right[:, None] - corners


def touches_edge(mask: np.ndarray, margin: int = 2) -> bool:
    """Whether the figure (its largest shape) reaches an image edge: something is cut off."""
    shape = largest_shape(mask)
    if not shape.any():
        return False
    rows, cols = np.nonzero(shape)
    height, width = shape.shape
    return bool(rows.min() < margin or cols.min() < margin or rows.max() >= height - margin
                or cols.max() >= width - margin)


def silhouette(image: Image.Image, threshold: int = 28) -> np.ndarray:
    """The figure's pixels: those that differ from the background model (or, for a cut-out image,
    the opaque ones)."""
    if image.mode == "RGBA" and np.asarray(image.getchannel("A")).min() < 255:
        return np.asarray(image.getchannel("A")) > 16
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    difference = np.abs(pixels - background_model(pixels)).max(axis=2)
    return difference > threshold


def largest_shape(mask: np.ndarray) -> np.ndarray:
    """The largest 4-connected region of ``mask`` (a second figure or a loose prop is dropped)."""
    if not mask.any():
        return mask
    height, width = mask.shape
    labels = np.zeros(mask.shape, dtype=np.int32)
    best, best_size, label = 0, 0, 0
    for start in zip(*np.nonzero(mask)):
        if labels[start]:
            continue
        label += 1
        labels[start] = label
        stack, size = [start], 0
        while stack:
            y, x = stack.pop()
            size += 1
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = label
                    stack.append((ny, nx))
        if size > best_size:
            best, best_size = label, size
    return labels == best


def _runs(row: np.ndarray, min_gap: int) -> int:
    """Separate foreground runs in a row, closing gaps narrower than ``min_gap``."""
    filled = np.flatnonzero(row)
    if filled.size == 0:
        return 0
    return 1 + int(np.count_nonzero(np.diff(filled) > min_gap))


def arms_check(path: Path) -> dict:
    """Whether the arms stand clear of the body, from the silhouette: in a clear A-pose most rows
    from the chest to the hips cross three separate parts (arm, body, arm). Returns
    ``{"arms_clear": bool, "clear_share": 0..1}``; ``arms_clear`` is None when no figure is found."""
    with Image.open(path) as image:
        image.thumbnail((256, 256))  # plenty for the silhouette, and quick to label
        mask = largest_shape(silhouette(image))
    rows = np.flatnonzero(mask.any(axis=1))
    if rows.size < 20:
        return {"arms_clear": None, "clear_share": 0.0}
    top, bottom = rows[0], rows[-1]
    height = bottom - top
    # Chest to hips: 30 to 50 % of the figure's height (arms hang there in an A-pose).
    band = range(top + int(0.30 * height), top + int(0.50 * height))
    min_gap = max(2, mask.shape[1] // 200)
    separated = sum(1 for y in band if _runs(mask[y], min_gap) >= 3)
    share = separated / max(len(band), 1)
    return {"arms_clear": share >= 0.6, "clear_share": round(share, 2)}
