"""Weapon grips between Blender's model space and the numbers in ``asset.json``.

The rig stage finds the grip as a 4x4 frame in the weapon's Blender model space (Z up, -Y
front): its origin is where the hand holds the weapon, +Y points to the blade and +Z across it.
``asset.grip`` keeps that frame the way Unity sees the imported FBX (``position`` in metres and
``rotation`` as Unity Euler degrees, applied Z, then X, then Y), because the Unity import script
places the model at minus the grip under the ``weapon_r`` / ``weapon_l`` socket bone. The FBX
export (Y up, -Z forward) and Unity's import (X mirrored) turn Blender's (x, y, z) into
(-x, z, -y).
"""

from __future__ import annotations

import math

import numpy as np

TO_UNITY = np.array([[-1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])


def euler_yxz(rotation: np.ndarray) -> list[float]:
    """Unity Euler degrees (x, y, z) of a rotation matrix R = Ry · Rx · Rz."""
    x = math.asin(max(-1.0, min(1.0, -rotation[1, 2])))
    if abs(math.cos(x)) > 1e-6:
        y = math.atan2(rotation[0, 2], rotation[2, 2])
        z = math.atan2(rotation[1, 0], rotation[1, 1])
    else:  # gimbal lock: put it all in y
        y = math.atan2(-rotation[2, 0], rotation[0, 0])
        z = 0.0
    return [round(math.degrees(a), 3) + 0.0 for a in (x, y, z)]


def rotation_yxz(degrees: list[float]) -> np.ndarray:
    x, y, z = (math.radians(float(d)) for d in degrees)
    rx = np.array([[1, 0, 0], [0, math.cos(x), -math.sin(x)], [0, math.sin(x), math.cos(x)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(z), -math.sin(z), 0], [math.sin(z), math.cos(z), 0], [0, 0, 1]])
    return ry @ rx @ rz


def to_unity(matrix: list[list[float]]) -> dict:
    """``{"position", "rotation"}`` of a Blender model-space grip frame."""
    frame = np.array(matrix, dtype=float)
    rotation = TO_UNITY @ frame[:3, :3] @ TO_UNITY.T
    position = TO_UNITY @ frame[:3, 3]
    return {"position": [round(float(v), 5) + 0.0 for v in position], "rotation": euler_yxz(rotation)}


def to_blender(position: list[float], rotation: list[float]) -> list[list[float]]:
    """The Blender model-space 4x4 frame of a grip given as ``asset.json`` numbers."""
    frame = np.eye(4)
    frame[:3, :3] = TO_UNITY.T @ rotation_yxz(rotation) @ TO_UNITY
    frame[:3, 3] = TO_UNITY.T @ np.array(position, dtype=float)
    return frame.round(8).tolist()
