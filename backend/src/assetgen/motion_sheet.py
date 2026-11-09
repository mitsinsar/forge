"""A picture of a generated body motion for the vision judge (and the user): a stick figure at six
moments, seen from the front (top row) and from its right side (bottom row). The left limbs are
blue, the right ones red. Drawn straight from HY-Motion's motion JSON, no Blender needed."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

MOMENTS = 6
CELL = (170, 230)  # width, height in pixels
HEIGHT_M = 2.3  # what a cell's height shows, metres
LEFT, RIGHT, TORSO = (60, 120, 235), (225, 60, 60), (70, 70, 70)


def positions(motion: dict) -> np.ndarray:
    """World joint positions, ``(frames, joints, 3)``, Y up (forward kinematics on the rest pose)."""
    rest = np.asarray(motion["rest"], float)
    parents = motion["parents"]
    rotations = np.asarray(motion["rotations"], float).reshape(motion["frames"], len(parents), 3, 3)
    translation = np.asarray(motion["translation"], float)
    frames = rotations.shape[0]
    world = np.zeros((frames, len(parents), 3, 3))
    out = np.zeros((frames, len(parents), 3))
    for j, parent in enumerate(parents):
        if parent < 0:
            world[:, j] = rotations[:, j]
            out[:, j] = rest[j] + translation
        else:
            world[:, j] = world[:, parent] @ rotations[:, j]
            out[:, j] = out[:, parent] + world[:, parent] @ (rest[j] - rest[parent])
    return out


def _colour(name: str) -> tuple[int, int, int]:
    return LEFT if name.startswith("L_") else RIGHT if name.startswith("R_") else TORSO


def draw_sheet(motion_json: Path, out_png: Path) -> Path:
    motion = json.loads(Path(motion_json).read_text(encoding="utf-8"))
    joints = positions(motion)
    names, parents = motion["joints"], motion["parents"]
    floor = float(joints[:, :, 1].min())
    frames = joints.shape[0]
    picks = [round(i * (frames - 1) / (MOMENTS - 1)) for i in range(MOMENTS)]
    centre = joints[:, 0].mean(axis=0)
    width, height = CELL
    scale = height / HEIGHT_M
    sheet = Image.new("RGB", (width * MOMENTS, (height + 20) * 2), "white")
    pen = ImageDraw.Draw(sheet)
    fps = float(motion.get("fps") or 30)
    # (screen x from a joint, label): the front looks at the face (its left on the right), the side
    # looks at its right side (forward points right).
    views = [(lambda p: p[0] - centre[0], "front"), (lambda p: p[2] - centre[2], "right side")]
    for row, (across, title) in enumerate(views):
        top = 20 + row * (height + 20)
        for column, frame in enumerate(picks):
            left = column * width

            def at(p):
                return (left + width / 2 + across(p) * scale, top + height - 12 - (p[1] - floor) * scale)

            pen.line([(left + 8, top + height - 12), (left + width - 8, top + height - 12)], fill=(190, 190, 190))
            pen.rectangle([left, top, left + width - 1, top + height - 1], outline=(225, 225, 225))
            pose = joints[frame]
            # Draw the far side first so the near limbs stay on top.
            order = sorted(range(len(parents)), key=lambda j: pose[j][2] if row == 0 else -pose[j][0])
            for j in order:
                if parents[j] >= 0:
                    pen.line([at(pose[parents[j]]), at(pose[j])], fill=_colour(names[j]), width=5)
            head = names.index("Head") if "Head" in names else len(names) - 1
            x, y = at(pose[head])
            pen.ellipse([x - 9, y - 18, x + 9, y], outline=TORSO, width=3)
            pen.text((left + 6, top + 4), f"{frame / fps:.1f}s", fill=(120, 120, 120))
        pen.text((6, top - 15), title, fill=(40, 40, 40))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_png)
    return out_png


SIDE = re.compile(r"\b(right|left)\s+(palm|hand|arm|fist|wrist|elbow)", re.I)
# The hand doing the action ("explosively thrusts the right palm"), when the prompt names both.
ACTION = re.compile(r"\b(?:thrusts?|punch(?:es)?|throws?|hurls?|swings?|extends?|push(?:es)?|strikes?|slash(?:es)?|"
                    r"jabs?|points?|shoots?|stabs?|lashes|flings?|tosses|launch(?:es)?)\s+(?:out\s+|forward\s+)?"
                    r"(?:the|his|her|their|a)?\s*(right|left)\s+(?:palm|hand|arm|fist|wrist)", re.I)
TRAVEL = re.compile(r"\b(dash|dashes|charge|charges|leap|leaps|run|runs|jump|jumps|lunge|lunges)\b", re.I)


def measure(motion_json: Path, prompt: str) -> list[dict]:
    """Checks measured on the joints, which the vision judge reads poorly from a stick figure:
    ``[{"id", "label", "ok", "detail", "penalty"}]``. The right hand when the prompt names it reaches
    forward further than the left; the body doesn't walk away or end turned; the arms really move."""
    motion = json.loads(Path(motion_json).read_text(encoding="utf-8"))
    joints = positions(motion)
    names = motion["joints"]
    if not {"Pelvis", "L_Wrist", "R_Wrist", "L_Hip", "R_Hip"} <= set(names):
        return []
    at = lambda name: joints[:, names.index(name)]  # noqa: E731
    pelvis = at("Pelvis")
    found = []
    sides = ({m.group(1).lower() for m in ACTION.finditer(prompt)}
             or {m.group(1).lower() for m in SIDE.finditer(prompt)})
    if len(sides) == 1:
        side = sides.pop()
        other = "left" if side == "right" else "right"
        reach = {s: float((at(f"{s[0].upper()}_Wrist") - pelvis)[:, 2].max()) for s in ("right", "left")}
        ok = reach[side] >= reach[other] - 0.05
        found.append({"id": "hand", "label": f"The {side} hand leads", "ok": ok, "penalty": 0 if ok else 4,
                      "detail": f"forward reach: {side} {reach[side]:.2f} m, {other} {reach[other]:.2f} m"})
    drift = float(np.linalg.norm((pelvis[-1] - pelvis[0])[[0, 2]]))
    ok = drift <= 0.5 or bool(TRAVEL.search(prompt))
    found.append({"id": "drift", "label": "Stays on the spot", "ok": ok, "penalty": 0 if ok else 2,
                  "detail": f"ends {drift:.2f} m from where it started"})
    hips = at("L_Hip") - at("R_Hip")
    yaw = np.degrees(np.arctan2(hips[:, 2], hips[:, 0]))
    turned = float(abs((yaw[-1] - yaw[0] + 180) % 360 - 180))
    ok = turned <= 45
    found.append({"id": "facing", "label": "Ends facing forward", "ok": ok, "penalty": 0 if ok else 2,
                  "detail": f"ends turned {turned:.0f} degrees"})
    moved = max(float(np.linalg.norm((at(w) - pelvis) - (at(w) - pelvis)[0], axis=1).max()) for w in ("L_Wrist", "R_Wrist"))
    ok = moved >= 0.3
    found.append({"id": "action", "label": "The arms move", "ok": ok, "penalty": 0 if ok else 3,
                  "detail": f"a hand moves at most {moved:.2f} m"})
    return found
