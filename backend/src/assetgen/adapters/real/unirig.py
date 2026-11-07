"""UniRig (https://github.com/VAST-AI-Research/UniRig) as the rig adapter: it predicts a
skeleton and skin weights for the cleaned mesh, and the predicted joints are matched to the
archetype's template joints.

1. LOD0 of the gate 2 mesh is written alone as ``rig/unirig/input.glb``.
2. UniRig runs as three subprocesses of its own Python (``python_path``, in ``unirig_path``,
   list arguments, no shell): skeleton prediction, skin prediction, and the merge that puts
   that skeleton and its weights on our mesh, ``rig/unirig/rigged.glb``. A finished step's
   output is kept, so a retried stage resumes.
3. The predicted joints are matched to the template joints (``archetypes/<id>/skeleton.json``)
   by position, each side scaled to its own mesh bounds, and checked along the chains. The
   fit score is how well the two skeletons cover each other; below ``FALLBACK_BELOW`` the rig
   is a fallback (extra limbs, wings on a biped) and gate 3 warns about it.

The template skeleton is what the asset is rigged with in every case, so the archetype's
clips always apply; UniRig's weights are used for skinning when the fit is good (see
``blender_scripts/rig_animate.py``).
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path

import numpy as np

from ... import imaging
from ...archetypes import ARCHETYPES_DIR
from ...config import Settings
from ...defaults import FIT_BY_HEIGHT

FALLBACK_BELOW = 0.6
GOOD_FIT = 0.9  # below this, UniRig runs again with another seed (up to unirig_tries runs)
SEEDS = (12345, 7, 2024, 99, 31337)
NEAR = 0.1  # matching distance, as a share of the mesh's height (or longest side)
SUFFIXES = "obj,fbx,FBX,dae,glb,gltf,vrm"
TAIL_LINES = 15


class RigError(RuntimeError):
    """A readable failure of the rig stage."""


# ---- GLB helpers ---------------------------------------------------------------------

def _node_matrices(gltf: dict) -> list[np.ndarray]:
    """World matrix of every node (glTF axes)."""
    nodes = gltf.get("nodes", [])
    local = []
    for node in nodes:
        if "matrix" in node:
            local.append(np.array(node["matrix"], dtype=float).reshape(4, 4).T)
            continue
        t = np.array(node.get("translation", [0, 0, 0]), dtype=float)
        x, y, z, w = node.get("rotation", [0, 0, 0, 1])
        s = np.array(node.get("scale", [1, 1, 1]), dtype=float)
        r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        m = np.eye(4)
        m[:3, :3] = r * s
        m[:3, 3] = t
        local.append(m)
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    world: dict[int, np.ndarray] = {}

    def resolve(i: int) -> np.ndarray:
        if i not in world:
            world[i] = (resolve(parent[i]) @ local[i]) if i in parent else local[i]
        return world[i]

    return [resolve(i) for i in range(len(nodes))]


def to_blender(points: np.ndarray) -> np.ndarray:
    """glTF axes (Y up, +Z front) to Blender axes (Z up, -Y front)."""
    points = np.atleast_2d(points)
    return np.stack([points[:, 0], -points[:, 2], points[:, 1]], axis=1)


def mesh_bounds_glb(gltf: dict, binary: bytes, node_index: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Min and max corner, in Blender axes, of the meshes in the scene (or of one node). Only
    nodes reachable from the scene count: the gate 2 GLB also holds LOD1/LOD2 beside LOD0."""
    worlds = _node_matrices(gltf)
    if node_index is not None:
        nodes = [node_index]
    else:
        stack, nodes = list(gltf["scenes"][gltf.get("scene", 0)]["nodes"]), []
        while stack:
            i = stack.pop()
            if "mesh" in gltf["nodes"][i]:
                nodes.append(i)
            stack.extend(gltf["nodes"][i].get("children", []))
    corners = []
    for i in nodes:
        mesh = gltf["meshes"][gltf["nodes"][i]["mesh"]]
        for prim in mesh["primitives"]:
            acc = gltf["accessors"][prim["attributes"]["POSITION"]]
            lo, hi = np.array(acc["min"], float), np.array(acc["max"], float)
            box = np.array([[a, b, c] for a in (lo[0], hi[0]) for b in (lo[1], hi[1]) for c in (lo[2], hi[2])])
            box = (worlds[i] @ np.c_[box, np.ones(8)].T).T[:, :3]
            corners.append(to_blender(box))
    allc = np.concatenate(corners)
    return allc.min(axis=0), allc.max(axis=0)


def lod0_only(src: Path, dest: Path) -> None:
    """A copy of the gate 2 mesh whose scene holds only LOD0 (or its first mesh node)."""
    gltf, binary = imaging.read_glb(src)
    nodes = gltf["nodes"]
    index = next((i for i, n in enumerate(nodes) if n.get("name") == "LOD0" and "mesh" in n),
                 next(i for i, n in enumerate(nodes) if "mesh" in n))
    scene = gltf["scenes"][gltf.get("scene", 0)]
    scene["nodes"] = [index]
    imaging.write_glb(dest, gltf, binary)


def predicted_skeleton(rigged: Path) -> dict:
    """UniRig's joints from the merged GLB: names, Blender-axes positions and parents."""
    gltf, _ = imaging.read_glb(rigged)
    skins = gltf.get("skins") or []
    if not skins:
        raise RigError(f"UniRig's result {rigged.name} has no skeleton (no glTF skin).")
    joints = skins[0]["joints"]
    worlds = _node_matrices(gltf)
    parent = {c: i for i, n in enumerate(gltf["nodes"]) for c in n.get("children", [])}
    names = [gltf["nodes"][j].get("name", f"joint_{j}") for j in joints]
    positions = to_blender(np.array([worlds[j][:3, 3] for j in joints]))
    in_skin = set(joints)
    parents = []
    for j in joints:
        p = parent.get(j)
        while p is not None and p not in in_skin:
            p = parent.get(p)
        parents.append(gltf["nodes"][p].get("name") if p is not None else None)
    return {"names": names, "positions": positions, "parents": parents}


# ---- matching ------------------------------------------------------------------------

def fit_axis(template_lo: np.ndarray, template_hi: np.ndarray, by_height: bool) -> int:
    return 2 if by_height else int(np.argmax(template_hi - template_lo))


def _normalise(points: np.ndarray, lo: np.ndarray, hi: np.ndarray, axis: int) -> np.ndarray:
    """One uniform scale (the extent along ``axis``), centred over the bounds and standing on
    their floor: the same fit the rig step uses, so bulky armour doesn't squeeze the joints."""
    origin = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
    return (points - origin) / max(hi[axis] - lo[axis], 1e-6)


MIN_LIMB = 0.08     # a template branch shorter than this (share of height) is a detail, such as a toe
END_MATCH = 0.45    # how far apart (share of height) a template limb end and a predicted one may be
EXTRA_LIMB = 0.3    # a predicted limb at least this long that matches nothing is an extra limb
EXTRA_PENALTY = 0.25
FINGER = re.compile(r"^(index|middle|ring|pinky|thumb)_")  # template details: the hand is the limb end
HAND_SPAN = 0.15    # predicted leaves that branch from one point within this length are fingers (or toes)
TINY = 0.015        # a predicted leaf this close to its parent is noise
PROPORTION_OK = 0.2   # a fitted bone within 20 % of the template's length (scaled) costs nothing...
PROPORTION_BAD = 1.3  # ...and one about 2.5 times too long or short (log2) costs fully
SYMMETRY_OK = 0.05    # a left and right bone within 5 % of each other's length cost nothing...
SYMMETRY_BAD = 0.3    # ...and 30 % apart cost fully (lengths: a posed arm still has equal bones)


def _tree(names: list[str], parents: list[str | None]) -> tuple[dict, list[str]]:
    children: dict[str, list[str]] = {n: [] for n in names}
    for name, parent in zip(names, parents):
        if parent is not None:
            children[parent].append(name)
    return children, [n for n, p in zip(names, parents) if p is None]


def _major(names, parents, pos, tails: dict | None = None) -> tuple[dict[str, list[str]], list[str]]:
    """The skeleton without its details (toes): each joint's major children, and the limb ends
    (major joints with no major children). A leaf reaches as far as its bone's tail when known
    (the template's Head is short to its joint but long to its tail)."""
    children, roots = _tree(names, parents)
    reach: dict[str, float] = {}

    def reach_of(n: str) -> float:
        if n not in reach:
            own = float(np.linalg.norm(tails[n] - pos[n])) if tails and n in tails else 0.0
            reach[n] = max((np.linalg.norm(pos[c] - pos[n]) + reach_of(c) for c in children[n]), default=own)
        return reach[n]

    major = {n: [c for c in children[n] if np.linalg.norm(pos[c] - pos[n]) + reach_of(c) >= MIN_LIMB]
             for n in names}
    ends, stack = [], list(roots)
    while stack:
        n = stack.pop()
        if major[n]:
            stack.extend(major[n])
        else:
            ends.append(n)
    return major, ends


def _path(node: str, parent_of: dict) -> list[str]:
    """Root to ``node``."""
    path = [node]
    while parent_of.get(path[-1]) is not None:
        path.append(parent_of[path[-1]])
    return path[::-1]


def _lca(nodes: list[str], parent_of: dict) -> str:
    paths = [_path(n, parent_of) for n in nodes]
    common = None
    for step in zip(*paths):
        if all(s == step[0] for s in step):
            common = step[0]
        else:
            break
    return common


def _arc(path: list[str], pos: dict) -> np.ndarray:
    return np.concatenate([[0.0], np.cumsum([np.linalg.norm(pos[b] - pos[a]) for a, b in zip(path, path[1:])])])


def _along(path: list[str], pos: dict, fraction: float) -> np.ndarray:
    """The point at ``fraction`` of the way along ``path`` (by length)."""
    arc = _arc(path, pos)
    if arc[-1] <= 1e-9:
        return np.array(pos[path[0]], float)
    target = fraction * arc[-1]
    i = int(np.clip(np.searchsorted(arc, target) - 1, 0, len(path) - 2))
    seg = arc[i + 1] - arc[i]
    t = 0.0 if seg <= 1e-9 else (target - arc[i]) / seg
    return pos[path[i]] + t * (pos[path[i + 1]] - pos[path[i]])


def _predicted_ends(names, parents, pos) -> tuple[list[str], set[str]]:
    """UniRig's limb ends: its leaves, with each cluster of leaves that branch from one point
    within ``HAND_SPAN`` (fingers, toes) replaced by that point (the hand). A short single leaf,
    such as a head joint just above the neck, stays an end; a tiny tip hands the end to its
    parent. Returns the ends and which of them are such branch points."""
    children, _ = _tree(names, parents)
    parent_of = dict(zip(names, parents))
    leaves = []
    for n in names:
        if children[n] or parent_of[n] is None:
            continue
        while (parent_of.get(parent_of[n]) is not None and len(children[parent_of[n]]) == 1
               and np.linalg.norm(pos[n] - pos[parent_of[n]]) < TINY):
            n = parent_of[n]
        if np.linalg.norm(pos[n] - pos[parent_of[n]]) >= TINY and n not in leaves:
            leaves.append(n)
    branch_of = {}
    for leaf in leaves:
        node, length = leaf, 0.0
        while parent_of.get(node) is not None:
            up = parent_of[node]
            length += float(np.linalg.norm(pos[node] - pos[up]))
            node = up
            if length > HAND_SPAN or len(children[node]) >= 2:
                break
        if len(children[node]) >= 2 and length <= HAND_SPAN:
            branch_of[leaf] = node
    ends: list[str] = []
    branches: set[str] = set()
    for leaf in leaves:
        point = branch_of.get(leaf)
        if point is not None and sum(1 for other in leaves if branch_of.get(other) == point) >= 2:
            if point not in ends:
                ends.append(point)
                branches.add(point)
        else:
            ends.append(leaf)
    return ends, branches


def twin(name: str) -> str | None:
    """The other side's joint (hand_l and hand_r, Ear.L and Ear.R, leg_l1_3 and leg_r1_3)."""
    for pattern, swap in ((r"_(l|r)$", {"l": "r", "r": "l"}), (r"\.(L|R)$", {"L": "R", "R": "L"}),
                          (r"_(l|r)(?=\d)", {"l": "r", "r": "l"})):
        found = re.search(pattern, name)
        if found:
            start, end = found.span(1)
            return name[:start] + swap[found.group(1)] + name[end:]
    return None


def match(template: dict, predicted: dict, asset_bounds: tuple[np.ndarray, np.ndarray],
          by_height: bool = True) -> dict:
    """Aligns UniRig's predicted skeleton with the template by position and chain.

    1. Both skeletons are scaled uniformly to their own mesh and stripped of details (fingers).
    2. Their limb ends (head, hands, feet, tail...) are paired by position.
    3. Each template branch joint (where limbs meet) corresponds to the predicted joint where
       the paired predicted limbs meet; every template joint between two such key joints gets
       the point at the same share of length along the predicted chain between their partners.

    4. A hand whose fingers UniRig didn't split (a fist, a gauntlet) ends in one chain through
       the hand: the wrist goes where the template's hand starts along it, not at its end.

    Returns ``targets`` (where each major template joint should be, on the asset), the
    ``mapping`` from each predicted joint to the template joint nearest its place (used for its
    skin weights), and a ``fit_score``: the share of the body's limbs found (head, hands, feet
    for a biped; fingers are details), times how close the fitted bones are to the template's
    proportions and how alike UniRig's left and right bone lengths are, less a penalty for each long
    predicted limb that matches nothing (wings on a biped)."""
    # Sockets hold props; fingers are details (gauntlets and fists have none to find).
    t_names = [n for n, j in template["joints"].items() if not j.get("socket") and not FINGER.match(n)]
    t_parent = {n: template["joints"][n]["parent"] for n in t_names}
    t_raw = {n: np.array(template["joints"][n]["head"], float) for n in t_names}
    t_lo, t_hi = np.array(template["mesh_bounds"]["min"], float), np.array(template["mesh_bounds"]["max"], float)
    axis = fit_axis(t_lo, t_hi, by_height)
    t_pos = {n: _normalise(t_raw[n][None], t_lo, t_hi, axis)[0] for n in t_names}
    t_tail = {n: _normalise(np.array(template["joints"][n]["tail"], float)[None], t_lo, t_hi, axis)[0]
              for n in t_names if template["joints"][n].get("tail")}

    p_names = list(predicted["names"])
    p_parent = dict(zip(p_names, predicted["parents"]))
    p_raw = {n: np.array(v, float) for n, v in zip(p_names, predicted["positions"])}
    p_pos = {n: _normalise(p_raw[n][None], *asset_bounds, axis)[0] for n in p_names}

    t_major, t_ends = _major(t_names, [t_parent[n] for n in t_names], t_pos, t_tail)
    p_ends, p_branches = _predicted_ends(p_names, [p_parent[n] for n in p_names], p_pos)
    # How far past each template limb end its fingers reach (the hand's own length), in the
    # template's units, along its longest finger.
    all_parent = {n: j["parent"] for n, j in template["joints"].items()}
    all_raw = {n: np.array(j["head"], float) for n, j in template["joints"].items()}
    reach_past: dict[str, float] = {}
    for name in t_names:
        best = 0.0
        for finger in (n for n in template["joints"] if FINGER.match(n)):
            path = _path(finger, all_parent)
            if name in path:
                best = max(best, float(_arc(path[path.index(name):], all_raw)[-1]))
        if best:
            reach_past[name] = best
    p_children, _ = _tree(p_names, [p_parent[n] for n in p_names])

    pairs = sorted((float(np.linalg.norm(t_pos[a] - p_pos[b])), a, b) for a in t_ends for b in p_ends)
    ends: dict[str, str] = {}
    for cost, a, b in pairs:
        if cost <= END_MATCH and a not in ends and b not in ends.values():
            ends[a] = b

    # Key joints: matched ends, and template branch joints with their predicted partner.
    keys: dict[str, str] = dict(ends)
    for n in t_names:
        if len(t_major[n]) >= 2:
            under = [e for e in ends if n in _path(e, t_parent)]
            if len(under) >= 2:
                keys[n] = _lca([ends[e] for e in under], p_parent)

    targets: dict[str, list[float]] = {}
    for end in ends:
        path = _path(end, t_parent)
        key_idx = [i for i, n in enumerate(path) if n in keys]
        for lo_i, hi_i in zip(key_idx, key_idx[1:]):
            a, d = path[lo_i], path[hi_i]
            p_path = _path(keys[d], p_parent)
            if keys[a] not in p_path:
                continue  # the partners are not on one chain: leave this stretch alone
            p_path = p_path[p_path.index(keys[a]):]
            t_arc = _arc(path[lo_i:hi_i + 1], t_raw)
            # A fist's chain runs on through the hand: count the template's hand length too.
            total = t_arc[-1] + (reach_past.get(d, 0.0) if d == end and keys[d] not in p_branches else 0.0)
            for j, name in enumerate(path[lo_i:hi_i + 1]):
                if name not in targets:
                    fraction = t_arc[j] / total if total > 1e-9 else 0.0
                    targets[name] = [round(float(v), 5) for v in _along(p_path, p_raw, fraction)]

    height = float(asset_bounds[1][axis] - asset_bounds[0][axis]) or 1.0

    def bone(name: str) -> float | None:
        parent = t_parent.get(name)
        if name in targets and parent in targets:
            return float(np.linalg.norm(np.array(targets[name]) - np.array(targets[parent])))
        return None

    # Left and right bones should be the same length, whatever pose the arms are in.
    asymmetry: list[float] = []
    for name in sorted(targets):
        other = twin(name)
        if other and name < other:
            mine, theirs = bone(name), bone(other)
            if mine and theirs:
                miss = abs(math.log2(mine / theirs)) - math.log2(1 + SYMMETRY_OK)
                asymmetry.append(min(1.0, max(0.0, miss) / (math.log2(1 + SYMMETRY_BAD) - math.log2(1 + SYMMETRY_OK))))

    # How close each fitted bone's length is to the template's (both scaled to their meshes).
    off: list[float] = []
    for name in targets:
        parent = t_parent.get(name)
        if parent in targets:
            want = float(np.linalg.norm(t_pos[name] - t_pos[parent]))
            got = (bone(name) or 0.0) / height
            if want > 1e-4 and got > 1e-6:
                miss = abs(math.log2(got / want)) - math.log2(1 + PROPORTION_OK)
                off.append(min(1.0, max(0.0, miss) / (PROPORTION_BAD - math.log2(1 + PROPORTION_OK))))

    mapping: dict[str, str] = {}
    if targets:
        names = list(targets)
        spots = np.array([targets[n] for n in names])
        for name in p_names:
            mapping[name] = names[int(np.argmin(np.linalg.norm(spots - p_raw[name], axis=1)))]

    def limb_length(end: str) -> float:
        path = _path(end, p_parent)
        start = next((i for i in range(len(path) - 2, -1, -1) if len(p_children[path[i]]) >= 2), 0)
        return float(_arc(path[start:], p_pos)[-1])

    extra = [e for e in p_ends if e not in ends.values() and limb_length(e) >= EXTRA_LIMB]
    found = len(ends) / len(t_ends) if t_ends else 0.0
    proportions = 1.0 - (float(np.mean(off)) if off else 0.0)
    symmetry = 1.0 - (float(np.mean(asymmetry)) if asymmetry else 0.0)
    fit = max(0.0, found * proportions * symmetry - EXTRA_PENALTY * len(extra))
    return {"mapping": mapping, "targets": targets, "fit_score": round(fit, 3),
            "limbs_found": f"{len(ends)}/{len(t_ends)}", "proportions": round(proportions, 3),
            "symmetry": round(symmetry, 3), "matched_ends": ends, "extra_limbs": extra}


# ---- the adapter ---------------------------------------------------------------------

class UniRigRig:

    def __init__(self, settings: Settings):
        self.root = Path(settings.unirig_path)
        self.python = Path(settings.python_path)
        self.timeout = float(settings.unirig_timeout)
        self.tries = max(1, min(len(SEEDS), int(settings.unirig_tries)))

    def _run(self, args: list[str], log: Path, step: str) -> None:
        if not (self.root / "run.py").is_file():
            raise RigError(f"UniRig was not found at {self.root}. Set unirig_path in assetgen.toml to the UniRig "
                           "folder (the one with run.py).")
        command = [str(self.python), *args]
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        tail: deque[str] = deque(maxlen=TAIL_LINES)
        try:
            with log.open("a", encoding="utf-8") as out:
                out.write(f"\n$ {' '.join(command)}\n")
                out.flush()
                process = subprocess.Popen(command, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                           env=env, creationflags=flags)
                started = time.monotonic()
                assert process.stdout is not None
                for line in process.stdout:
                    out.write(line)
                    if line.strip():
                        tail.append(line.rstrip())
                    if time.monotonic() - started > self.timeout:
                        process.kill()
                        raise RigError(f"UniRig's {step} step took longer than {self.timeout:.0f} s.")
                code = process.wait()
        except OSError as exc:
            raise RigError(f"Could not start UniRig's Python at {self.python} ({exc}). Set python_path in "
                           "assetgen.toml to UniRig's own environment.") from exc
        if code != 0:
            last = "\n".join(tail)
            raise RigError(f"UniRig's {step} step failed (exit code {code}). Last lines of {log.name}:\n{last}")

    def _extract(self, source: Path, work: Path, log: Path, stamp: str) -> None:
        self._run(["-m", "src.data.extract", "--config=configs/data/quick_inference.yaml",
                   f"--require_suffix={SUFFIXES}", "--force_override=true", "--num_runs=1", "--id=0",
                   f"--time={stamp}", "--faces_target_count=50000", f"--input={source}", f"--output_dir={work}"],
                  log, "mesh extraction")

    def _attempt(self, mesh: Path, work: Path, seed: int, progress) -> Path:
        """UniRig's skeleton, skin and merge steps with one seed, in ``work`` (kept files are reused)."""
        work.mkdir(parents=True, exist_ok=True)
        log = work / "unirig.log"
        source = work / "input.glb"
        skeleton_fbx, skin_fbx, rigged = work / "skeleton.fbx", work / "skin.fbx", work / "rigged.glb"
        npz = work / "npz"
        stamp = time.strftime("%Y_%m_%d_%H_%M_%S")
        if rigged.is_file():
            return rigged
        lod0_only(Path(mesh), source)
        if not skeleton_fbx.is_file():
            progress("UniRig: predicting the skeleton", 0.05)
            shutil.rmtree(npz, ignore_errors=True)
            self._extract(source, npz, log, stamp)
            self._run(["run.py", "--task=configs/task/quick_inference_skeleton_articulationxl_ar_256.yaml",
                       f"--seed={seed}", f"--input={source}", f"--output={skeleton_fbx}", f"--npz_dir={npz}"],
                      log, "skeleton")
        if not skin_fbx.is_file():
            progress("UniRig: predicting the skin weights", 0.4)
            shutil.rmtree(npz, ignore_errors=True)
            self._extract(skeleton_fbx, npz, log, stamp)
            self._run(["run.py", "--task=configs/task/quick_inference_unirig_skin.yaml", f"--seed={seed}",
                       f"--input={skeleton_fbx}", f"--output={skin_fbx}", f"--npz_dir={npz}",
                       "--data_name=raw_data.npz"], log, "skin")
        progress("UniRig: putting the skeleton on the mesh", 0.75)
        self._run(["-m", "src.inference.merge", f"--require_suffix={SUFFIXES}", "--num_runs=1", "--id=0",
                   f"--source={skin_fbx}", f"--target={source}", f"--output={rigged}"], log, "merge")
        if not rigged.is_file():
            raise RigError(f"UniRig's merge step wrote no {rigged.name}; see {log.name}.")
        return rigged

    def predict_skeleton(self, *, mesh, archetype, out_dir, progress):
        """Runs UniRig and fits the template to its skeleton. UniRig's skeleton varies from run to run,
        so when the fit is below ``GOOD_FIT`` it runs again with other seeds (``unirig_tries`` runs in
        all, each in its own folder) and keeps the best fit."""
        out_dir = Path(out_dir)
        template_file = ARCHETYPES_DIR / archetype["folder"] / "skeleton.json"
        if not template_file.is_file():
            raise RigError(f"The {archetype['id']} archetype has no skeleton.json; rebuild the templates with "
                           "blender_scripts/build_archetypes.py.")
        template = json.loads(template_file.read_text(encoding="utf-8"))
        best, attempts = None, []
        for number, seed in enumerate(SEEDS[:max(1, self.tries)]):
            work = (out_dir / "unirig").resolve() if number == 0 else (out_dir / "unirig" / f"seed_{seed}").resolve()
            span = (number / self.tries, (number + 1) / self.tries)

            def step(text, fraction, span=span, number=number):
                progress(text if number == 0 else f"{text} (try {number + 1})", span[0] + (span[1] - span[0]) * 0.9 * fraction)

            rigged = self._attempt(Path(mesh), work, seed, step)
            progress("Matching the skeleton to the template", span[0] + (span[1] - span[0]) * 0.95)
            predicted = predicted_skeleton(rigged)
            gltf, binary = imaging.read_glb(work / "input.glb")
            result = match(template, predicted, mesh_bounds_glb(gltf, binary), archetype["id"] in FIT_BY_HEIGHT)
            attempts.append({"seed": seed, "fit_score": result["fit_score"]})
            if best is None or result["fit_score"] > best[0]["fit_score"]:
                best = (result, predicted, rigged)
            if result["fit_score"] >= GOOD_FIT:
                break
        result, predicted, rigged = best
        fallback = result["fit_score"] < FALLBACK_BELOW
        record = {"archetype": archetype["id"], "joints": list(template["joints"]), "fallback": fallback,
                  "predicted_joints": len(predicted["names"]), "unirig_file": str(rigged), "attempts": attempts,
                  **result}
        path = out_dir / "skeleton.json"
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        progress("Skeleton fitted", 1.0)
        return {**record, "file": str(path)}
