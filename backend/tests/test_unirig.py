"""The UniRig rig adapter against a fake UniRig folder (its three steps are small Python
scripts that record their arguments), and the matching of predicted joints to the template."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

from assetgen import imaging
from assetgen.adapters.real.unirig import RigError, UniRigRig, lod0_only, match, mesh_bounds_glb, predicted_skeleton
from assetgen.archetypes import ARCHETYPES_DIR, get_archetype
from assetgen.config import load_settings

TEMPLATE = json.loads((ARCHETYPES_DIR / "biped" / "skeleton.json").read_text(encoding="utf-8"))


def to_gltf(p):
    """Blender axes (Z up, -Y front) to glTF axes (Y up, +Z front)."""
    x, y, z = p
    return [x, z, -y]


def box_glb(lo, hi, joints: list[tuple[str, list, str | None]] | None = None, extra_lod=True) -> bytes:
    """A box mesh node "LOD0" spanning lo..hi (Blender axes), optionally skinned to ``joints``
    (name, Blender-axes position, parent name) laid out as a node tree."""
    g_lo, g_hi = to_gltf(lo), to_gltf(hi)
    mn = [min(a, b) for a, b in zip(g_lo, g_hi)]
    mx = [max(a, b) for a, b in zip(g_lo, g_hi)]
    corners = [[x, y, z] for x in (mn[0], mx[0]) for y in (mn[1], mx[1]) for z in (mn[2], mx[2])]
    positions = struct.pack(f"<{24}f", *[c for p in corners for c in p])
    indices = struct.pack("<36H", *[0, 1, 2, 1, 3, 2] * 6)
    binary = positions + indices
    gltf = {"asset": {"version": "2.0"}, "scene": 0,
            "nodes": [{"name": "LOD0", "mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3", "min": mn, "max": mx},
                          {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR"}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 96},
                            {"buffer": 0, "byteOffset": 96, "byteLength": 72}],
            "buffers": [{"byteLength": len(binary)}]}
    scene_nodes = [0]
    if extra_lod:
        gltf["nodes"].append({"name": "LOD1", "mesh": 0, "translation": [5, 0, 0]})
        scene_nodes.append(1)
    if joints:
        index = {}
        for name, pos, _ in joints:
            index[name] = len(gltf["nodes"])
            gltf["nodes"].append({"name": name})
        world = {name: np.array(to_gltf(pos)) for name, pos, _ in joints}
        for name, _, parent in joints:
            node = gltf["nodes"][index[name]]
            node["translation"] = list(world[name] - (world[parent] if parent else 0))
            if parent:
                gltf["nodes"][index[parent]].setdefault("children", []).append(index[name])
        roots = [index[n] for n, _, p in joints if p is None]
        gltf["skins"] = [{"joints": [index[n] for n, _, _ in joints]}]
        gltf["nodes"][0]["skin"] = 0
        scene_nodes += roots
    gltf["scenes"] = [{"nodes": scene_nodes}]
    return imaging.glb_bytes(gltf, binary)


def predicted_from_template(scale=1.0, extra: int = 0) -> list[tuple[str, list, str | None]]:
    """UniRig-like joints (bone_0...) at the template's joint positions, ``scale``d; ``extra`` far
    joints stand for limbs the template doesn't have (wings on a biped)."""
    names = list(TEMPLATE["joints"])
    rename = {n: f"bone_{i}" for i, n in enumerate(names)}
    joints = [(rename[n], [v * scale for v in TEMPLATE["joints"][n]["head"]],
               rename.get(TEMPLATE["joints"][n]["parent"])) for n in names]
    spine = rename["spine_03"]
    for i in range(extra):
        side = 1 if i % 2 else -1
        joints.append((f"bone_w{i}", [side * scale * (1.6 + 0.2 * (i // 2)), scale * 0.6, scale * 1.4],
                       spine if i < 2 else f"bone_w{i - 2}"))
    return joints


FAKE_STEP = """import json, shutil, sys
from pathlib import Path
here = Path(__file__).resolve().parents[{depth}]
args = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
step = {step}
with open(here / "calls.jsonl", "a") as log:
    log.write(json.dumps({{"step": step, "args": args}}) + "\\n")
fail = here / "fail_step.txt"
if fail.exists() and fail.read_text().strip() == step:
    print("Traceback: something broke in " + step)
    sys.exit(3)
{action}
"""


def fake_unirig(root: Path, rigged: bytes) -> Path:
    (root / "src" / "data").mkdir(parents=True)
    (root / "src" / "inference").mkdir(parents=True)
    (root / "rigged_result.glb").write_bytes(rigged)
    (root / "src" / "data" / "extract.py").write_text(FAKE_STEP.format(
        depth=2, step="'extract'", action="Path(args['output_dir']).mkdir(parents=True, exist_ok=True)"))
    (root / "run.py").write_text(FAKE_STEP.format(
        depth=0, step="'skin' if 'skin' in args['task'] else 'skeleton'",
        action="Path(args['output']).write_bytes(b'fake fbx')"))
    (root / "src" / "inference" / "merge.py").write_text(FAKE_STEP.format(
        depth=2, step="'merge'", action="shutil.copy(here / 'rigged_result.glb', args['output'])"))
    return root


def calls(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]


def adapter(tmp_path, root: Path) -> UniRigRig:
    return UniRigRig(load_settings(data_dir=tmp_path, unirig_path=root, python_path=Path(sys.executable)))


def run(rig: UniRigRig, tmp_path, mesh_glb: bytes):
    mesh = tmp_path / "mesh.glb"
    mesh.write_bytes(mesh_glb)
    steps = []
    result = rig.predict_skeleton(mesh=mesh, archetype=get_archetype("biped"), out_dir=tmp_path / "rig",
                                  progress=lambda s, f: steps.append((s, f)))
    return result, steps


LO, HI = TEMPLATE["mesh_bounds"]["min"], TEMPLATE["mesh_bounds"]["max"]


def test_unirig_steps_and_a_good_fit(tmp_path):
    root = fake_unirig(tmp_path / "UniRig", box_glb(LO, HI, predicted_from_template()))
    result, steps = run(adapter(tmp_path, root), tmp_path, box_glb(LO, HI))

    order = [c["step"] for c in calls(root)]
    assert order == ["extract", "skeleton", "extract", "skin", "merge"]
    skeleton_call = calls(root)[1]["args"]
    assert skeleton_call["input"].endswith("input.glb") and "articulationxl" in skeleton_call["task"]
    assert calls(root)[4]["args"]["target"].endswith("input.glb")
    gltf, _ = imaging.read_glb(tmp_path / "rig" / "unirig" / "input.glb")
    assert gltf["scenes"][0]["nodes"] == [0]  # LOD0 only

    assert result["fit_score"] > 0.95 and result["fallback"] is False and result["extra_limbs"] == []
    assert np.allclose(result["targets"]["thigh_l"], TEMPLATE["joints"]["thigh_l"]["head"], atol=1e-4)
    assert result["joints"] == list(TEMPLATE["joints"]) and result["predicted_joints"] == len(TEMPLATE["joints"])
    names = list(TEMPLATE["joints"])
    assert result["mapping"][f"bone_{names.index('pelvis')}"] == "pelvis"
    assert result["mapping"][f"bone_{names.index('hand_l')}"] == "hand_l"
    saved = json.loads(Path(result["file"]).read_text())
    assert saved["fit_score"] == result["fit_score"] and Path(saved["unirig_file"]).is_file()
    assert steps[-1] == ("Skeleton fitted", 1.0)


def test_a_differently_sized_mesh_still_fits(tmp_path):
    """Both skeletons are compared within their own mesh bounds, so size doesn't matter."""
    root = fake_unirig(tmp_path / "UniRig", box_glb([v * 3 for v in LO], [v * 3 for v in HI],
                                                    predicted_from_template(scale=3)))
    result, _ = run(adapter(tmp_path, root), tmp_path, box_glb([v * 3 for v in LO], [v * 3 for v in HI]))
    assert result["fit_score"] > 0.95


def test_extra_limbs_fall_back(tmp_path):
    lo, hi = list(LO), list(HI)
    lo[0], hi[0] = -2.6, 2.6  # a wide wingspan
    root = fake_unirig(tmp_path / "UniRig", box_glb(lo, hi, predicted_from_template(extra=24)))
    result, _ = run(adapter(tmp_path, root), tmp_path, box_glb(lo, hi))
    assert result["fallback"] is True and result["fit_score"] < 0.6
    assert len(result["extra_limbs"]) == 2  # one wing each side


def test_a_kept_result_is_not_rerun(tmp_path):
    root = fake_unirig(tmp_path / "UniRig", box_glb(LO, HI, predicted_from_template()))
    rig = adapter(tmp_path, root)
    run(rig, tmp_path, box_glb(LO, HI))
    before = len(calls(root))
    run(rig, tmp_path, box_glb(LO, HI))
    assert len(calls(root)) == before


def test_a_failed_step_is_readable(tmp_path):
    root = fake_unirig(tmp_path / "UniRig", box_glb(LO, HI, predicted_from_template()))
    (root / "fail_step.txt").write_text("skin")
    with pytest.raises(RigError, match=r"UniRig's skin step failed \(exit code 3\)") as error:
        run(adapter(tmp_path, root), tmp_path, box_glb(LO, HI))
    assert "something broke in skin" in str(error.value)
    assert (tmp_path / "rig" / "unirig" / "skeleton.fbx").is_file()  # a retry resumes after the skeleton


def test_missing_unirig_is_readable(tmp_path):
    with pytest.raises(RigError, match="UniRig was not found"):
        run(adapter(tmp_path, tmp_path / "nowhere"), tmp_path, box_glb(LO, HI))


def test_helpers(tmp_path):
    path = tmp_path / "m.glb"
    path.write_bytes(box_glb(LO, HI, predicted_from_template()))
    gltf, binary = imaging.read_glb(path)
    lo, hi = mesh_bounds_glb(gltf, binary, 0)
    assert np.allclose(lo, LO, atol=1e-5) and np.allclose(hi, HI, atol=1e-5)
    skeleton = predicted_skeleton(path)
    pelvis = skeleton["names"].index(f"bone_{list(TEMPLATE['joints']).index('pelvis')}")
    assert np.allclose(skeleton["positions"][pelvis], TEMPLATE["joints"]["pelvis"]["head"], atol=1e-5)
    lod0_only(path, tmp_path / "lod0.glb")
    assert imaging.read_glb(tmp_path / "lod0.glb")[0]["scenes"][0]["nodes"] == [0]
    report = match(TEMPLATE, skeleton, (lo, hi))
    for joint in ("pelvis", "spine_02", "thigh_l", "calf_r", "upperarm_l", "hand_r"):  # an identical skeleton
        assert np.allclose(report["targets"][joint], TEMPLATE["joints"][joint]["head"], atol=1e-4), joint


def test_a_bad_fit_tries_other_seeds_and_keeps_the_best(tmp_path):
    lo, hi = list(LO), list(HI)
    lo[0], hi[0] = -2.6, 2.6
    root = fake_unirig(tmp_path / "UniRig", box_glb(lo, hi, predicted_from_template(extra=24)))
    result, _ = run(adapter(tmp_path, root), tmp_path, box_glb(lo, hi))
    seeds = [c["args"]["seed"] for c in calls(root) if c["step"] == "skeleton"]
    assert seeds == ["12345", "7", "2024"] and [a["seed"] for a in result["attempts"]] == [12345, 7, 2024]
    assert (tmp_path / "rig" / "unirig" / "seed_7" / "rigged.glb").is_file()
