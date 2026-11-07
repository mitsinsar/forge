"""Quality checks at the gates: each a ``{"id", "label", "status", "detail"}`` with status ``pass``,
``warn`` or ``fail``.

* Gate 1, per candidate (``candidate_checks``): the silhouette (one figure, nothing cut off by the
  edges, arms clear of the body for biped characters) and what the vision model sees (a held
  weapon or prop, several figures). Candidates that fail are redrawn with another seed, up to
  ``auto_retries`` rounds.
* Gate 2 (``mesh_checks``): loose pieces and holes in the cleaned mesh.
* Gate 3 (``rig_checks``): how well the template fitted UniRig's skeleton, and how much each clip
  stretches the mesh.

Autopilot approves a gate only when nothing failed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .pose import arms_check, silhouette, touches_edge

PASS, WARN, FAIL = "pass", "warn", "fail"
ORDER = {PASS: 0, WARN: 1, FAIL: 2}


def check(check_id: str, label: str, status: str, detail: str) -> dict:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def worst(checks: list[dict]) -> str:
    return max((c["status"] for c in checks), key=ORDER.__getitem__, default=PASS)


def _shapes(mask: np.ndarray) -> list[int]:
    """Sizes of the 4-connected regions of ``mask``, largest first."""
    height, width = mask.shape
    seen = np.zeros(mask.shape, dtype=bool)
    sizes = []
    for start in zip(*np.nonzero(mask)):
        if seen[start]:
            continue
        seen[start] = True
        stack, size = [start], 0
        while stack:
            y, x = stack.pop()
            size += 1
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        sizes.append(size)
    return sorted(sizes, reverse=True)


def candidate_checks(path: Path, *, kind: str, posed_biped: bool, vision: dict | None) -> list[dict]:
    checks = []
    with Image.open(path) as image:
        image.thumbnail((256, 256))
        mask = silhouette(image)
    edge = touches_edge(mask)
    sizes = _shapes(mask)
    pieces = sum(1 for s in sizes if sizes and s >= 0.2 * sizes[0])
    # The vision model counts figures; the silhouette splits on low contrast (dark clothes on a
    # dark background), so on its own it only warns.
    seen = int(vision.get("figures", 1)) if vision else None
    if seen is not None and seen > 1:
        checks.append(check("figures", "One subject", FAIL, f"{seen} separate figures or objects"))
    elif seen is None and pieces > 1:
        checks.append(check("figures", "One subject", WARN, f"The outline falls into {pieces} parts"))
    else:
        checks.append(check("figures", "One subject", PASS, "One figure"))
    # Cut off: the vision model says so; the silhouette (thrown off by vignettes) only warns
    # when there is no vision model.
    if vision is not None:
        whole = vision.get("whole_body", True)
        checks.append(check("whole", "Nothing cut off", PASS if whole else FAIL,
                            "Whole in the frame" if whole else "Cut off by the image edge"))
    elif not sizes:
        checks.append(check("whole", "Nothing cut off", FAIL, "No figure found on the background"))
    else:
        checks.append(check("whole", "Nothing cut off", WARN if edge else PASS,
                            "Seems to touch the image edge" if edge else "Whole in the frame"))
    if posed_biped:
        arms = arms_check(path)
        status = PASS if arms["arms_clear"] else FAIL if arms["arms_clear"] is False else WARN
        checks.append(check("arms", "Arms clear of the body", status,
                            f"{round(arms['clear_share'] * 100)} % of the chest-to-hip rows show a gap"))
    if kind in ("gear", "weapon"):  # the item alone: anyone wearing or holding it ends up in the mesh
        if vision is None:
            checks.append(check("alone", "The item alone", WARN, "Not checked: the text model can't see images"))
        else:
            person = vision.get("person")
            checks.append(check("alone", "The item alone", FAIL if person else PASS,
                                "A person or mannequin is in the picture" if person else "No one wears or holds it"))
    if kind in ("hero", "monster", "boss"):
        if vision is None:
            checks.append(check("props", "Empty hands", WARN, "Not checked: the text model can't see images"))
        else:
            held = vision.get("held_item")
            checks.append(check("props", "Empty hands", FAIL if held else PASS,
                                f"Holds {vision.get('held_item_name') or 'something'}" if held else "Holds nothing"))
    return checks


def mesh_checks(stats: dict | None, kind: str = "hero") -> list[dict]:
    if not stats:
        return []
    share = float(stats.get("largest_piece_share", 1.0))
    loose = 1 - share
    open_share = stats.get("open_edges", 0) / max(stats.get("edges", 1), 1)
    floor = float(stats.get("floor_ratio", 1.0))
    found = [check("floor", "No floor under the feet", FAIL if floor > 4 else WARN if floor > 2.5 else PASS,
                   f"The bottom is {floor:g}x as wide as the ankles: a floor plate from the image's ground or "
                   "shadow, which tears when the character moves. Reject gate 1 for another image, or reject gate 2."
                   if floor > 2.5 else "The model stands on its own feet")] if kind in ("hero", "monster", "boss") else []
    return found + [
        check("pieces", "One solid piece", FAIL if loose > 0.25 else WARN if loose > 0.08 else PASS,
              f"{stats.get('pieces', 1)} pieces; {round(loose * 100)} % of the vertices outside the main one"),
        check("holes", "Few holes", WARN if open_share > 0.03 else PASS,
              f"{round(open_share * 100, 1)} % of the edges border a hole"),
    ]


STANDARD_CLIPS = ("idle", "walk", "run", "attack_1", "attack_2", "hit", "death")


FOLLOW_WARN = 12.0  # degrees a leg may lean sideways off the clip (against the first frame) before a warning...
FOLLOW_FAIL = 20.0  # ...and before a failure (a knee bending sideways drifts about 25)


def rig_checks(*, skeleton: dict | None, stretch: dict | None, gear: dict | None = None,
               clips: list[dict] | None = None) -> list[dict]:
    checks = []
    if skeleton and skeleton.get("unirig_file"):
        fit = float(skeleton.get("fit_score", 0))
        tries = skeleton.get("attempts") or []
        detail = f"Fit {round(fit * 100)} %" + (f" (best of {len(tries)} UniRig runs)" if len(tries) > 1 else "")
        parts = [f"limbs {skeleton['limbs_found']}" if skeleton.get("limbs_found") else ""]
        parts += [f"{label} {round(float(skeleton[key]) * 100)} %" for key, label in
                  (("proportions", "proportions"), ("symmetry", "left/right"))
                  if skeleton.get(key) is not None and float(skeleton[key]) < 0.95]
        if any(parts):
            detail += ": " + ", ".join(p for p in parts if p)
        checks.append(check("fit", "Skeleton fits", FAIL if fit < 0.6 else WARN if fit < 0.9 else PASS, detail))
    if stretch:
        shares = {c: s for c, s in stretch.items() if c in STANDARD_CLIPS} or stretch
        bad = sorted(shares, key=shares.get, reverse=True)
        top = shares[bad[0]] if bad else 0.0
        status = FAIL if top > 0.12 else WARN if top > 0.06 else PASS
        detail = ", ".join(f"{c} {round(shares[c] * 100)} %" for c in bad[:3])
        checks.append(check("stretch", "Little stretching", status, f"Most stretched: {detail}"))
    follows = [c for c in (clips or []) if c.get("follow")]
    if follows:
        standard = [c for c in follows if c["name"] in STANDARD_CLIPS] or follows
        bad = sorted(standard, key=lambda c: c["follow"]["degrees"], reverse=True)
        top = bad[0]["follow"]["degrees"]
        status = FAIL if top > FOLLOW_FAIL else WARN if top > FOLLOW_WARN else PASS
        named = ", ".join(f"{c['name']} {c['follow']['bone'].replace('_', ' ')} {round(c['follow']['degrees'])}°"
                          for c in bad[:3])
        checks.append(check("follow", "Follows the animation", status,
                            f"Legs lean sideways off the clip: {named}" if status != PASS else f"Legs within {round(top)}° of the clip"))
    if gear:
        inside = int(gear.get("inside_after", 0))
        checks.append(check("clipping", "Nothing inside the body", WARN if inside > 0 else PASS,
                            f"{inside} points inside the body at rest" if inside else "Clear of the body"))
    return checks
