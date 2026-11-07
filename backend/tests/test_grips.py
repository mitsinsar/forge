"""Grip frames between Blender's model space and asset.json's Unity numbers."""

from __future__ import annotations

import numpy as np

from assetgen.grips import rotation_yxz, to_blender, to_unity, euler_yxz


def test_euler_round_trip():
    for degrees in ([10, 20, 30], [-45, 90 - 1e-3, 5], [0, 180, 0], [80, -30, 170]):
        assert np.allclose(rotation_yxz(euler_yxz(rotation_yxz(degrees))), rotation_yxz(degrees), atol=1e-4)


def test_blender_unity_round_trip_and_axes():
    frame = np.eye(4)
    frame[:3, 3] = [0.01, 0.02, 0.15]  # 15 cm up the handle in Blender (Z up)
    unity = to_unity(frame.tolist())
    assert np.allclose(unity["position"], [-0.01, 0.15, -0.02])  # Unity: Y up, X mirrored
    assert np.allclose(unity["rotation"], [0, 0, 0])
    assert np.allclose(to_blender(unity["position"], unity["rotation"]), frame)

    turned = np.eye(4)
    turned[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]  # 90 degrees about Blender's Z (up)
    back = np.array(to_blender(**to_unity(turned.tolist())))
    assert np.allclose(back, turned, atol=1e-6)
