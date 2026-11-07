"""The A-pose guide image and gate 1's arms check."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from assetgen.pose import A_POSE, arms_check, guide_image


def figure(path, arms_out: bool, extra_figure: bool = False, size: int = 512):
    """A grey-background stick figure: arms angled out (clear) or straight down against the body."""
    image = Image.new("RGB", (size, size), (200, 200, 200))
    draw = ImageDraw.Draw(image)
    s = size / 512

    def body(dx: float):
        draw.ellipse((236 * s + dx, 40 * s, 276 * s + dx, 90 * s), fill=(150, 20, 20))
        draw.rectangle((216 * s + dx, 90 * s, 296 * s + dx, 280 * s), fill=(150, 20, 20))
        draw.rectangle((220 * s + dx, 280 * s, 250 * s + dx, 480 * s), fill=(150, 20, 20))
        draw.rectangle((262 * s + dx, 280 * s, 292 * s + dx, 480 * s), fill=(150, 20, 20))
        if arms_out:
            draw.line((216 * s + dx, 100 * s, 120 * s + dx, 270 * s), fill=(150, 20, 20), width=int(22 * s))
            draw.line((296 * s + dx, 100 * s, 392 * s + dx, 270 * s), fill=(150, 20, 20), width=int(22 * s))
        else:
            draw.rectangle((194 * s + dx, 95 * s, 216 * s + dx, 300 * s), fill=(150, 20, 20))
            draw.rectangle((296 * s + dx, 95 * s, 318 * s + dx, 300 * s), fill=(150, 20, 20))

    body(-110 * s if extra_figure else 0)
    if extra_figure:  # a second, separate figure off to the side
        draw.rectangle((420 * s, 60 * s, 440 * s, 470 * s), fill=(150, 20, 20))
        draw.rectangle((460 * s, 60 * s, 480 * s, 470 * s), fill=(150, 20, 20))
        draw.rectangle((496 * s, 60 * s, 506 * s, 470 * s), fill=(150, 20, 20))
    image.save(path)
    return path


def test_arms_out_are_clear(tmp_path):
    result = arms_check(figure(tmp_path / "a.png", arms_out=True))
    assert result["arms_clear"] is True and result["clear_share"] >= 0.6


def test_arms_against_the_body_are_flagged(tmp_path):
    result = arms_check(figure(tmp_path / "a.png", arms_out=False))
    assert result["arms_clear"] is False and result["clear_share"] < 0.6


def test_a_second_figure_does_not_count_as_arms(tmp_path):
    assert arms_check(figure(tmp_path / "a.png", arms_out=False, extra_figure=True))["arms_clear"] is False


def test_an_empty_image_has_no_figure(tmp_path):
    path = tmp_path / "empty.png"
    Image.new("RGB", (128, 128), (200, 200, 200)).save(path)
    assert arms_check(path)["arms_clear"] is None


def test_guide_image_draws_the_wrists_wide_of_the_hips(tmp_path):
    path = guide_image(tmp_path / "guide.png", size=256)
    pixels = np.asarray(Image.open(path).convert("RGB"))
    assert pixels.shape == (256, 256, 3) and pixels[0, 0].tolist() == [0, 0, 0]
    for name in ("r_wrist", "l_wrist", "neck", "l_ankle"):
        x, y = A_POSE[name]
        assert pixels[int(y * 256), int(x * 256)].any(), name
    assert A_POSE["r_wrist"][0] < A_POSE["r_hip"][0] - 0.15 and A_POSE["l_wrist"][0] > A_POSE["l_hip"][0] + 0.15
