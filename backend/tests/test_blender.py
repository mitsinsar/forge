"""The real Blender adapter: the runner (with a fake Blender), and the scripts
on small sample meshes when Blender is installed (skipped otherwise).

Blender is looked for at ``ASSETGEN_BLENDER_PATH``, on PATH and in the usual
install folders of macOS and Windows.
"""

from __future__ import annotations

import glob
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from assetgen.adapters.real.blender import BlenderTools, merge_glb_extras
from assetgen.adapters.real.blender_runner import SCRIPTS_DIR, BlenderError, run_script
from assetgen.archetypes import get_archetype
from assetgen.config import load_settings
from assetgen.defaults import CAMERA_DIRECTIONS
from assetgen.imaging import finish_sprites
from conftest import make_app, make_project, wait_for
from fakes import FakeBlender, FakeRig, _parts_info
from fakes.glb import GlbBuilder, build_mesh, read_glb_json
from fakes.shapes import Part, asset_parts

HELPERS = Path(__file__).parent / "blender"
TEMPLATE_JOINTS = json.loads((Path(__file__).parents[1] / "src" / "assetgen" / "archetypes" / "biped"
                              / "skeleton.json").read_text(encoding="utf-8"))["joints"]


def find_blender() -> str | None:
    candidates = [os.environ.get("ASSETGEN_BLENDER_PATH"), shutil.which("blender"),
                  "/Applications/Blender.app/Contents/MacOS/Blender",
                  *sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender*\blender.exe"), reverse=True)]
    return next((c for c in candidates if c and Path(c).is_file()), None)


BLENDER = find_blender()
needs_blender = pytest.mark.skipif(BLENDER is None, reason="Blender is not installed")


def progress_log():
    seen: list[tuple[str, float]] = []
    return seen, lambda step, fraction: seen.append((step, fraction))


def write_raw(path: Path, parts: list[Part], triangles: int = 20000, extras: dict | None = None) -> Path:
    builder = GlbBuilder(extras=extras or {})
    material = builder.add_material("albedo", (0.6, 0.3, 0.2))
    builder.add_node("raw", builder.add_mesh("raw", build_mesh(parts, triangles), material))
    return builder.write(path)


def lod_bounds(glb: Path) -> dict[str, tuple[list[float], list[float]]]:
    """min/max of each LOD node's positions (glTF, Y up)."""
    gltf = read_glb_json(glb)
    result = {}
    for node in gltf["nodes"]:
        if "mesh" in node:
            accessor = gltf["accessors"][gltf["meshes"][node["mesh"]]["primitives"][0]["attributes"]["POSITION"]]
            result[node["name"]] = (accessor["min"], accessor["max"])
    return result


def inspect_fbx(fbx: Path, tmp_path: Path) -> dict:
    report = tmp_path / "report.json"
    subprocess.run([BLENDER, "-b", "--factory-startup", "--python-exit-code", "1", "-P",
                    str(HELPERS / "inspect_fbx.py"), "--", str(fbx), str(report)],
                   check=True, capture_output=True, timeout=300)
    return json.loads(report.read_text())


# ---- runner and adapter wiring (no Blender needed) -----------------------------


@pytest.fixture
def fake_blender(tmp_path):
    """An executable standing in for Blender: prints progress, then succeeds or fails."""
    if os.name == "nt":
        pytest.skip("the fake Blender is a POSIX script")
    body = tmp_path / "fake_blender.py"
    body.write_text("""import json, sys
args_path, result_path = sys.argv[sys.argv.index("--") + 1:]
args = json.load(open(args_path))
print("Blender 4.5.0 LTS")
print("PROGRESS 0.250 Cleaning up the mesh")
print("PROGRESS 0.750 Making LOD1 (50%)")
if args.get("fail"):
    for n in range(30):
        print(f"noise {n}")
    print("Traceback (most recent call last):")
    print("ValueError: the raw mesh has no mesh objects")
    sys.exit(1)
json.dump({"ok": True, "args": args}, open(result_path, "w"))
""")
    script = tmp_path / "fake-blender"  # a sh wrapper: shebangs cannot hold paths with spaces
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{body}" "$@"\n')
    script.chmod(0o755)
    return script


def test_runner_reports_progress_and_keeps_the_log(fake_blender, tmp_path):
    seen, progress = progress_log()
    result = run_script(fake_blender, "cleanup_lods.py", {"x": 1}, work_dir=tmp_path / "w",
                        log_path=tmp_path / "w" / "stage.log", progress=progress)
    assert result == {"ok": True, "args": {"x": 1}}
    assert seen == [("Cleaning up the mesh", 0.25), ("Making LOD1 (50%)", 0.75)]
    log = (tmp_path / "w" / "stage.log").read_text()
    assert "Blender 4.5.0 LTS" in log and str(SCRIPTS_DIR / "cleanup_lods.py") in log


def test_runner_failure_carries_the_last_log_lines(fake_blender, tmp_path):
    with pytest.raises(BlenderError) as caught:
        run_script(fake_blender, "cleanup_lods.py", {"fail": True}, work_dir=tmp_path,
                   log_path=tmp_path / "stage.log", progress=lambda s, f: None)
    message = str(caught.value)
    assert "exited with code 1" in message and "stage.log" in message
    assert message.rstrip().endswith("ValueError: the raw mesh has no mesh objects")
    assert "noise 29" in message and "noise 0" not in message


def test_missing_blender_is_explained(tmp_path):
    with pytest.raises(BlenderError, match="blender_path"):
        run_script(tmp_path / "no-blender", "cleanup_lods.py", {}, work_dir=tmp_path,
                   log_path=tmp_path / "l.log", progress=lambda s, f: None)


class RealBlenderFakeRig(BlenderTools):
    """The real Blender for clean-up, sprites and FBX; skinning comes from the fake rig until ticket #11."""

    def rig_and_animate(self, **kwargs):
        return FakeBlender().rig_and_animate(**kwargs)


def real_blender_app(settings):
    return make_app(settings, blender=RealBlenderFakeRig(settings))


def test_glb_extras_survive_blender(tmp_path):
    source = write_raw(tmp_path / "raw.glb", asset_parts("hero", "biped"), 500, {"color": [1, 0, 0], "parts": []})
    target = write_raw(tmp_path / "mesh.glb", asset_parts("hero", "biped"), 500)
    merge_glb_extras(source, target, {"triangle_budget": 800})
    gltf = read_glb_json(target)
    assert gltf["extras"] == {"color": [1, 0, 0], "parts": [], "triangle_budget": 800}
    assert gltf["nodes"][0]["name"] == "raw"
    data = target.read_bytes()
    assert int.from_bytes(data[8:12], "little") == len(data)


# ---- the scripts in Blender ----------------------------------------------------


@needs_blender
def test_relative_folders_work(tmp_path):
    """The app's default data_dir is the relative "data"; Blender runs inside the stage folder."""
    raw = write_raw(tmp_path / "raw.glb", asset_parts("hero", "biped"), 3000)
    tools = BlenderTools(load_settings(data_dir=Path("data"), blender_path=BLENDER))
    result = tools.cleanup_and_lods(raw_mesh=Path("raw.glb"), kind="hero", triangles=1000, lod_ratios=[0.5],
                                    out_dir=Path("data") / "assets" / "a1" / "mesh", progress=lambda s, f: None)
    assert (tmp_path / "data" / "assets" / "a1" / "mesh" / "mesh.glb").is_file()
    assert result["lods"][0]["triangles"] <= 1000


@needs_blender
@pytest.mark.parametrize("remove", [True, False])
def test_floaters_are_removed_but_touching_plates_stay(tmp_path, remove):
    body = asset_parts("hero", "biped")
    plate = Part((0.0, 1.15, 0.15), (0.16, 0.16, 0.05), 0.4)  # an armour plate pressed onto the chest
    rock = Part((0.75, 1.0, 0.2), (0.08, 0.07, 0.08), 0.3)  # a blob floating beside the arm
    raw = write_raw(tmp_path / "raw.glb", [*body, plate, rock], 40000)
    tools = BlenderTools(load_settings(data_dir=tmp_path, blender_path=BLENDER))
    result = tools.cleanup_and_lods(raw_mesh=raw, kind="hero", triangles=6000, lod_ratios=[],
                                    out_dir=tmp_path / "mesh", progress=lambda s, f: None, remove_floaters=remove)
    removed = result["removed"]
    assert removed["floater_pieces"] == (1 if remove else 0)
    low, high = lod_bounds(result["glb"])["LOD0"]
    width = high[0] - low[0]  # about 0.7 m for the body alone, about 1.2 m with the rock beside it
    assert (width > 0.95) is (not remove)
    assert high[2] - low[2] > 0.3  # the chest plate is still there in front of the torso


@needs_blender
def test_cleanup_hits_the_budget_scales_and_makes_lods(tmp_path):
    parts = asset_parts("hero", "biped")
    extras = {"color": [0.6, 0.3, 0.2], "parts": _parts_info(parts), "hints": {"kind": "hero"}}
    # A floating speck far smaller than the body: debris to remove.
    raw = write_raw(tmp_path / "raw.glb", [*parts, Part((0.6, 1.0, 0.0), (0.01, 0.01, 0.01), 0.01)],
                    extras=extras)
    tools = BlenderTools(load_settings(data_dir=tmp_path, blender_path=BLENDER))
    seen, progress = progress_log()
    result = tools.cleanup_and_lods(raw_mesh=raw, kind="hero", triangles=8000, lod_ratios=[0.5, 0.2],
                                    out_dir=tmp_path / "mesh", progress=progress)

    lods = result["lods"]
    assert [(lod["level"], lod["ratio"]) for lod in lods] == [(0, 1.0), (1, 0.5), (2, 0.2)]
    assert 7000 <= lods[0]["triangles"] <= 8000
    for lod in lods[1:]:
        assert lod["triangles"] <= round(lods[0]["triangles"] * lod["ratio"])
        assert abs(lod["triangles"] / lods[0]["triangles"] - lod["ratio"]) < 0.05
    assert seen and seen[-1] == ("LODs ready", 1.0)
    assert (tmp_path / "mesh" / "blender_cleanup_lods.log").is_file()

    report = json.loads((tmp_path / "mesh" / "cleanup_lods.result.json").read_text())
    assert report["removed"]["debris_faces"] > 0 and report["removed"]["merged_vertices"] > 0

    bounds = lod_bounds(result["glb"])
    assert sorted(bounds) == ["LOD0", "LOD1", "LOD2"]
    low, high = bounds["LOD0"]
    assert abs(low[1]) < 1e-3 and abs(high[1] - 1.8) < 0.02  # feet at 0, 1.8 m tall (Y up)
    assert abs(low[0] + high[0]) < 0.02 and abs(low[2] + high[2]) < 0.02  # centred over the origin
    gltf = read_glb_json(result["glb"])
    assert gltf["extras"]["hints"] == {"kind": "hero"} and gltf["extras"]["triangle_budget"] == 8000


@needs_blender
def test_weapons_are_scaled_by_their_longest_side(tmp_path):
    raw = write_raw(tmp_path / "raw.glb", [Part((0, 0.3, 0), (0.03, 0.3, 0.05))], 3000)
    tools = BlenderTools(load_settings(data_dir=tmp_path, blender_path=BLENDER))
    result = tools.cleanup_and_lods(raw_mesh=raw, kind="weapon", triangles=800, lod_ratios=[0.5, 0.2],
                                    out_dir=tmp_path / "mesh", progress=lambda s, f: None)
    assert result["lods"][0]["triangles"] <= 800
    low, high = lod_bounds(result["glb"])["LOD0"]
    assert abs(max(h - lo for h, lo in zip(high, low)) - 1.0) < 0.02


@needs_blender
def test_cleanup_error_shows_blenders_last_lines(tmp_path):
    raw = tmp_path / "raw.glb"
    raw.write_bytes(b"not a glb")
    tools = BlenderTools(load_settings(data_dir=tmp_path, blender_path=BLENDER))
    with pytest.raises(BlenderError) as caught:
        tools.cleanup_and_lods(raw_mesh=raw, kind="hero", triangles=8000, lod_ratios=[0.5],
                               out_dir=tmp_path / "mesh", progress=lambda s, f: None)
    message = str(caught.value)
    assert "Blender cleanup_lods exited with code 1" in message
    assert "Last lines of blender_cleanup_lods.log" in message and "ERROR" in message


def _mesh_stage(tmp_path: Path, tools: BlenderTools) -> tuple[Path, list[dict]]:
    parts = asset_parts("hero", "biped")
    raw = write_raw(tmp_path / "raw.glb", parts, extras={
        "color": [0.6, 0.3, 0.2], "parts": _parts_info(parts), "hints": {"kind": "hero", "archetype": "biped"}})
    result = tools.cleanup_and_lods(raw_mesh=raw, kind="hero", triangles=6000, lod_ratios=[0.5, 0.2],
                                    out_dir=tmp_path / "mesh", progress=lambda s, f: None)
    return result["glb"], result["lods"]


@needs_blender
@pytest.mark.parametrize("arch_id", ["biped", "quadruped"])
def test_rig_and_animate_retargets_the_template_clips(tmp_path, arch_id):
    """Without UniRig weights (fallback): automatic weights, the template's clips baked onto it."""
    tools = BlenderTools(load_settings(data_dir=tmp_path, blender_path=BLENDER))
    raw = write_raw(tmp_path / "raw.glb", asset_parts("hero", arch_id), 6000)
    mesh = tools.cleanup_and_lods(raw_mesh=raw, kind="hero" if arch_id == "biped" else "monster", triangles=3000,
                                  lod_ratios=[0.5], out_dir=tmp_path / "mesh", progress=lambda s, f: None)
    clips = ["idle", "walk", "attack_1"]
    result = tools.rig_and_animate(mesh_glb=mesh["glb"], skeleton={"fallback": True}, archetype=get_archetype(arch_id),
                                   clips=clips, attach=None, out_path=tmp_path / "rig" / "animated.glb",
                                   progress=lambda s, f: None)
    assert [c["name"] for c in result["clips"]] == clips
    assert result["clips"][0]["loop"] is True and result["clips"][2]["loop"] is False
    assert all(c["duration"] > 0 for c in result["clips"]) and result["skinning"] == "automatic"
    gltf = read_glb_json(result["glb"])
    assert sorted(a["name"] for a in gltf["animations"]) == sorted(clips)
    (skin,) = gltf["skins"]
    joints = {gltf["nodes"][j]["name"] for j in skin["joints"]}
    assert set(get_archetype(arch_id)["joints"]) <= joints
    meshes = [n for n in gltf["nodes"] if "mesh" in n]
    assert len(meshes) == 1 and "skin" in meshes[0]  # LOD0 only, skinned
    record = json.loads((tmp_path / "rig" / "rig_animate.result.json").read_text())
    assert 0.1 < record["scale"] < 3.0  # the source packs come in their own sizes (the wolf is 5.5 m long)


def _accessor(gltf: dict, binary: bytes, index: int) -> np.ndarray:
    accessor = gltf["accessors"][index]
    view = gltf["bufferViews"][accessor["bufferView"]]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[accessor["type"]]
    dtype = {5126: np.float32, 5121: np.uint8, 5123: np.uint16, 5125: np.uint32}[accessor["componentType"]]
    data = np.frombuffer(binary, dtype=dtype, count=accessor["count"] * width,
                         offset=view.get("byteOffset", 0) + accessor.get("byteOffset", 0))
    return data.reshape(accessor["count"], width)


def _attach(tmp_path: Path, mesh: Path, attach: dict, clips=("walk", "attack_1")) -> tuple[dict, dict, bytes]:
    from assetgen.imaging import read_glb
    tools = BlenderTools(load_settings(data_dir=tmp_path, blender_path=BLENDER))
    biped = get_archetype("biped")
    result = tools.rig_and_animate(mesh_glb=mesh, skeleton={"joints": biped["joints"]}, archetype=biped,
                                   clips=list(clips), attach=attach, out_path=tmp_path / "rig" / "animated.glb",
                                   progress=lambda s, f: None)
    gltf, binary = read_glb(result["glb"])
    assert sorted(a["name"] for a in gltf["animations"]) == sorted(clips)
    return result, gltf, binary


@needs_blender
def test_a_box_helmet_lands_on_the_head_with_the_bodys_weights(tmp_path):
    from test_unirig import box_glb
    mesh = tmp_path / "helmet.glb"
    mesh.write_bytes(box_glb([-0.5, -0.5, 0.4], [0.5, 0.5, 1.4], extra_lod=False))  # a 1 m box anywhere
    result, gltf, binary = _attach(tmp_path, mesh, {"kind": "gear", "slot": "head"})

    assert result["gear"]["inside_after"] == 0  # nothing of it inside the body at rest
    assert result["gear"]["masked_faces"] > 50
    mask = json.loads((tmp_path / "rig" / "body_mask.json").read_text())
    assert mask["slot"] == "head" and len(mask["faces"]) == result["gear"]["masked_faces"]

    names = [n.get("name") for n in gltf["nodes"]]
    gear = gltf["nodes"][names.index("gear")]
    primitive = gltf["meshes"][gear["mesh"]]["primitives"][0]
    position = gltf["accessors"][primitive["attributes"]["POSITION"]]
    head = TEMPLATE_JOINTS["Head"]["head"]  # Blender axes; the GLB is Y up
    centre = [(a + b) / 2 for a, b in zip(position["min"], position["max"])]
    assert abs(centre[0] - head[0]) < 0.05 and abs(centre[2] + head[1]) < 0.08
    assert position["min"][1] < head[2] + 0.05 < position["max"][1]  # around the head, not floating
    joints = gltf["skins"][gear["skin"]]["joints"]
    used = {names[joints[j]] for j in np.unique(_accessor(gltf, binary, primitive["attributes"]["JOINTS_0"])[
        _accessor(gltf, binary, primitive["attributes"]["WEIGHTS_0"]) > 0.01])}
    assert used == {"Head"}
    assert "mannequin" in names  # the base body plays the clips with it


@needs_blender
def test_a_stick_weapons_grip_is_at_its_thin_end(tmp_path):
    mesh = tmp_path / "stick.glb"
    builder = GlbBuilder()
    material = builder.add_material("albedo", (0.5, 0.5, 0.5))
    parts = [Part((0, 0.11, 0), (0.015, 0.11, 0.015), 1),  # the handle, 22 cm (glTF: Y up)
             Part((0, 0.62, 0), (0.05, 0.40, 0.008), 3)]  # a wide, flat blade above it
    builder.add_node("LOD0", builder.add_mesh("LOD0", build_mesh(parts, 4000), material))
    builder.write(mesh)
    result, gltf, _ = _attach(tmp_path, mesh, {"kind": "weapon", "weapon_class": "one_handed",
                                               "socket": "weapon_r", "grip_matrix": None},
                              clips=("walk", "attack_slash"))

    weapon = result["weapon"]
    frame = np.array(weapon["grip_matrix"])
    assert weapon["handle_end"] == "low" and not weapon["manual"]
    assert 0.03 < frame[2, 3] < 0.2 and abs(frame[0, 3]) < 0.02  # in the handle (Blender: Z up)
    assert frame[2, 1] > 0.95  # +Y of the grip points up the blade
    names = [n.get("name") for n in gltf["nodes"]]
    socket = gltf["nodes"][names.index("weapon_r")]
    assert names.index("weapon") in socket.get("children", [])  # held by the socket bone, not skinned
    assert "skin" not in gltf["nodes"][names.index("weapon")]

    # Unity gets the weapon alone and rigid, in its own model space.
    tools = BlenderTools(load_settings(data_dir=tmp_path, blender_path=BLENDER))
    tools.export_fbx(animated_glb=result["glb"], mesh_glb=mesh, lods=[{"level": 0}], rig_type="Humanoid",
                     out_path=tmp_path / "export" / "Stick.fbx", progress=lambda s, f: None, kind="weapon")
    record = json.loads((tmp_path / "export" / "export_fbx.result.json").read_text())
    assert [m["name"] for m in record["meshes"]] == ["Stick_LOD0"] and not record["meshes"][0]["skinned"]
    assert record["clips"] == []


@needs_blender
def test_a_grip_given_at_gate_3_replaces_the_detected_one(tmp_path):
    mesh = tmp_path / "stick.glb"
    builder = GlbBuilder()
    material = builder.add_material("albedo", (0.5, 0.5, 0.5))
    builder.add_node("LOD0", builder.add_mesh("LOD0", build_mesh([Part((0, 0.5, 0), (0.02, 0.5, 0.02), 1)], 2000),
                                              material))
    builder.write(mesh)
    given = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0.7], [0, 0, 0, 1]]
    result, _, _ = _attach(tmp_path, mesh, {"kind": "weapon", "weapon_class": "two_handed", "socket": "weapon_r",
                                            "grip_matrix": given}, clips=("walk",))
    assert result["weapon"]["manual"] and np.allclose(result["weapon"]["grip_matrix"], given)
    second = np.array(result["weapon"]["second_grip_matrix"])
    assert np.allclose(second[:3, 3], [0, 0.12, 0.7])  # the second hand 12 cm towards the blade (+Y)


@needs_blender
def test_export_of_the_fake_rig_reimports_with_lods_and_clips(tmp_path):
    tools = RealBlenderFakeRig(load_settings(data_dir=tmp_path, blender_path=BLENDER))
    mesh_glb, lods = _mesh_stage(tmp_path, tools)
    biped = get_archetype("biped")
    skeleton = FakeRig().predict_skeleton(mesh=mesh_glb, archetype=biped, out_dir=tmp_path / "rig",
                                           progress=lambda s, f: None)
    animated = tools.rig_and_animate(mesh_glb=mesh_glb, skeleton=skeleton, archetype=biped,
                                     clips=["idle", "walk", "attack_1"], attach=None,
                                     out_path=tmp_path / "rig" / "animated.glb", progress=lambda s, f: None)
    fbx = tools.export_fbx(animated_glb=animated["glb"], mesh_glb=mesh_glb, lods=lods, rig_type="Humanoid",
                           out_path=tmp_path / "export" / "Ember_Knight.fbx", progress=lambda s, f: None)
    assert fbx.read_bytes()[:18] == b"Kaydara FBX Binary"

    report = inspect_fbx(fbx, tmp_path)
    meshes = {o["name"]: o for o in report["objects"] if o["type"] == "MESH"}
    assert sorted(meshes) == ["Ember_Knight_LOD0", "Ember_Knight_LOD1", "Ember_Knight_LOD2"]
    assert len({o["parent"] for o in meshes.values()}) == 1  # one parent, so Unity makes a LOD Group
    assert meshes["Ember_Knight_LOD1"]["triangles"] == lods[1]["triangles"]
    assert meshes["Ember_Knight_LOD2"]["triangles"] == lods[2]["triangles"]
    assert {a["take"] for a in report["actions"]} == {"idle", "walk", "attack_1"}
    assert max(meshes["Ember_Knight_LOD0"]["dimensions"]) == pytest.approx(1.8, abs=0.1)  # scale 1


@needs_blender
def test_export_of_a_skinned_rig_skins_every_lod(tmp_path):
    tools = BlenderTools(load_settings(data_dir=tmp_path, blender_path=BLENDER))
    mesh_glb, lods = _mesh_stage(tmp_path, tools)
    animated = tmp_path / "skinned.glb"
    subprocess.run([BLENDER, "-b", "--factory-startup", "--python-exit-code", "1", "-P",
                    str(HELPERS / "make_skinned_sample.py"), "--", str(animated)],
                   check=True, capture_output=True, timeout=300)
    fbx = tools.export_fbx(animated_glb=animated, mesh_glb=mesh_glb, lods=lods, rig_type="Humanoid",
                           out_path=tmp_path / "export" / "Pillar.fbx", progress=lambda s, f: None)
    result = json.loads((tmp_path / "export" / "export_fbx.result.json").read_text())
    assert [m["skinned"] for m in result["meshes"]] == [True, True, True]

    report = inspect_fbx(fbx, tmp_path)
    by_type = {}
    for obj in report["objects"]:
        by_type.setdefault(obj["type"], []).append(obj)
    assert [o["name"] for o in by_type["ARMATURE"]] == ["Armature"]
    meshes = {o["name"]: o for o in by_type["MESH"]}
    assert sorted(meshes) == ["Pillar_LOD0", "Pillar_LOD1", "Pillar_LOD2"]
    assert all(o["parent"] == "Armature" and o["vertex_groups"] == 2 for o in meshes.values())
    assert {a["take"] for a in report["actions"]} == {"idle", "attack_1"}


@needs_blender
def test_unity_run_with_real_blender_finishes_end_to_end(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings(data_dir=tmp_path / "data", blender_path=BLENDER)
    game = tmp_path / "UnityGame"
    with TestClient(real_blender_app(settings)) as client:
        project = make_project(client, "unity", game)
        asset_id = client.post("/api/assets", json={
            "project_id": project["id"], "kind": "monster", "name": "Moss Wolf",
            "description": "a mossy forest wolf"}).json()["id"]
        wait_for(client, asset_id, "waiting", gate=1)
        client.post(f"/api/assets/{asset_id}/gates/1/approve", json={"candidate": 1})
        asset = wait_for(client, asset_id, "waiting", gate=2, timeout=300)
        lods = asset["outputs"]["mesh"]["lods"]
        assert lods[0]["triangles"] <= 4000
        assert [lod["ratio"] for lod in lods] == [1.0, 0.5, 0.2]
        assert client.get(asset["urls"]["mesh"]).content[:4] == b"glTF"
        client.post(f"/api/assets/{asset_id}/gates/2/approve")
        wait_for(client, asset_id, "waiting", gate=3, timeout=120)
        client.post(f"/api/assets/{asset_id}/gates/3/approve")
        wait_for(client, asset_id, "done", timeout=300)
    fbx = game / "Assets" / "Generated" / "monster" / "Moss_Wolf" / "Moss_Wolf.fbx"
    assert fbx.read_bytes()[:18] == b"Kaydara FBX Binary"


@needs_blender
def test_blender_failure_shows_on_the_asset(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings(data_dir=tmp_path / "data", blender_path=BLENDER)
    with TestClient(real_blender_app(settings)) as client:
        app_adapters = client.app.state.pipeline.adapters
        mesh_adapter = app_adapters.mesh

        def broken_mesh(*, out_path, **kwargs):  # Hunyuan3D wrote something that is not a GLB
            result = mesh_adapter.__class__.image_to_mesh(mesh_adapter, out_path=out_path, **kwargs)
            out_path.write_bytes(b"broken")
            return result

        monkeypatch.setattr(mesh_adapter, "image_to_mesh", broken_mesh)
        project = make_project(client, "unity", tmp_path / "game")
        asset_id = client.post("/api/assets", json={
            "project_id": project["id"], "kind": "hero", "name": "Broken", "description": "a knight"}).json()["id"]
        wait_for(client, asset_id, "waiting", gate=1)
        client.post(f"/api/assets/{asset_id}/gates/1/approve", json={"candidate": 1})
        asset = wait_for(client, asset_id, "failed", timeout=300)
    assert "Blender cleanup_lods exited with code 1" in asset["error"]
    assert "Last lines of blender_cleanup_lods.log" in asset["error"]


# ---- sprite frames --------------------------------------------------------------


def _raw_frame(path: Path, size: int, filled: bool, shade: int = 0) -> Path:
    """A supersampled render: a soft-edged gradient disc on transparency, or nothing."""
    pixels = np.zeros((size, size, 4), np.uint8)
    if filled:
        y, x = np.mgrid[0:size, 0:size]
        distance = np.hypot(x - size / 2, y - size / 2) / (size * 0.4)
        pixels[..., 0] = (x * 255 // size + shade) % 256
        pixels[..., 1] = y * 255 // size
        pixels[..., 2] = 90
        pixels[..., 3] = np.clip((1.1 - distance) * 10 * 255, 0, 255).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels, "RGBA").save(path)
    return path


def _raw_set(tmp_path: Path, filled: dict[str, list[bool]], directions=("right", "down")) -> dict:
    return {d: {clip: [_raw_frame(tmp_path / "raw" / d / clip / f"{i}.png", 64, f, shade=i * 40)
                       for i, f in enumerate(flags)] for clip, flags in filled.items()} for d in directions}


def test_pixel_frames_share_one_palette_and_have_hard_alpha(tmp_path):
    raw = _raw_set(tmp_path, {"idle": [True] * 3, "walk": [True] * 4})
    frames = finish_sprites(raw, {"idle": True, "walk": True}, tmp_path / "out", frame_size=32, look="pixel",
                            palette_size=8)
    colours = set()
    for direction in ("right", "down"):
        assert [len(frames[direction][c]) for c in ("idle", "walk")] == [3, 4]
        for path in frames[direction]["idle"] + frames[direction]["walk"]:
            assert path == tmp_path / "out" / direction / path.parent.name / path.name
            pixels = np.asarray(Image.open(path))
            assert pixels.shape == (32, 32, 4)
            assert set(np.unique(pixels[..., 3])) <= {0, 255}
            colours |= {tuple(p) for p in pixels[pixels[..., 3] == 255][:, :3]}
    assert 2 <= len(colours) <= 8  # one shared palette over every frame and clip


def test_pixel_downscale_is_nearest_neighbour(tmp_path):
    blocks = np.indices((4, 4)).sum(axis=0) % 2 == 0  # a checkerboard of 2x2 blocks, red and blue
    big = np.where(blocks.repeat(2, 0).repeat(2, 1)[..., None], [255, 0, 0, 255], [0, 0, 255, 255]).astype(np.uint8)
    path = tmp_path / "raw" / "right" / "idle" / "0.png"
    path.parent.mkdir(parents=True)
    Image.fromarray(big, "RGBA").save(path)
    frames = finish_sprites({"right": {"idle": [path]}}, {"idle": True}, tmp_path / "out", frame_size=4,
                            look="pixel", palette_size=4)
    small = np.asarray(Image.open(frames["right"]["idle"][0]))
    expected = np.where(blocks[..., None], [255, 0, 0], [0, 0, 255])
    assert (small[..., :3] == expected).all()  # every pixel is one source pixel, no blended purple


def test_hd_frames_are_smoothly_downscaled(tmp_path):
    raw = _raw_set(tmp_path, {"idle": [True]}, directions=("right",))
    frames = finish_sprites(raw, {"idle": True}, tmp_path / "out", frame_size=32, look="hd", palette_size=8)
    pixels = np.asarray(Image.open(frames["right"]["idle"][0]))
    assert pixels.shape == (32, 32, 4) and pixels[0, 0, 3] == 0
    assert len(set(np.unique(pixels[..., 3]))) > 2  # soft edge kept


def test_empty_end_frames_of_non_looping_clips_are_trimmed(tmp_path):
    raw = _raw_set(tmp_path, {"death": [True, True, False, False], "idle": [True, False]})
    raw["down"]["death"][2] = _raw_frame(tmp_path / "raw" / "down" / "death" / "2b.png", 64, True)
    frames = finish_sprites(raw, {"death": False, "idle": True}, tmp_path / "out", frame_size=16, look="hd",
                            palette_size=8)
    # frame 2 still shows something facing down, so both directions keep 3 frames
    assert [len(frames[d]["death"]) for d in ("right", "down")] == [3, 3]
    assert [len(frames[d]["idle"]) for d in ("right", "down")] == [2, 2]  # loops are never trimmed
    raw = _raw_set(tmp_path / "b", {"death": [False, False]})
    frames = finish_sprites(raw, {"death": False}, tmp_path / "b" / "out", frame_size=16, look="hd",
                            palette_size=8)
    assert len(frames["right"]["death"]) == 1


def _animated_sample(tmp_path: Path, clips: list[str]) -> tuple[Path, list[dict]]:
    """The fake rig's animated GLB of a small biped (as the rig stage writes it today)."""
    parts = asset_parts("hero", "biped")
    mesh = tmp_path / "mesh.glb"
    builder = GlbBuilder(extras={"color": [0.6, 0.3, 0.2], "parts": _parts_info(parts),
                                 "hints": {"kind": "hero", "archetype": "biped"}})
    material = builder.add_material("albedo", (0.6, 0.3, 0.2))
    builder.add_node("LOD0", builder.add_mesh("LOD0", build_mesh(parts, 1500), material))
    builder.write(mesh)
    biped = get_archetype("biped")
    skeleton = FakeRig().predict_skeleton(mesh=mesh, archetype=biped, out_dir=tmp_path / "rig",
                                           progress=lambda s, f: None)
    result = FakeBlender().rig_and_animate(mesh_glb=mesh, skeleton=skeleton, archetype=biped, clips=clips,
                                            attach=None, out_path=tmp_path / "rig" / "animated.glb",
                                            progress=lambda s, f: None)
    return result["glb"], result["clips"]


def _feet_and_height(path: Path) -> tuple[int, int, int]:
    """(lowest opaque row, opaque rows, centre column of the opaque pixels)."""
    alpha = np.asarray(Image.open(path))[..., 3]
    rows = np.flatnonzero(alpha.max(axis=1) > 0)
    columns = np.flatnonzero(alpha.max(axis=0) > 0)
    return int(rows[-1]), int(rows[-1] - rows[0] + 1), int((columns[0] + columns[-1]) // 2)


@needs_blender
def test_sprites_render_every_clip_and_direction_at_the_frame_size(tmp_path):
    animated, clips = _animated_sample(tmp_path, ["idle", "walk", "death"])
    tools = BlenderTools(load_settings(data_dir=tmp_path, blender_path=BLENDER))
    seen, progress = progress_log()
    fps, size = 8, 64
    frames = tools.render_sprites(animated_glb=animated, clips=clips, directions=CAMERA_DIRECTIONS["three_quarter"],
                                  camera_preset="three_quarter", frame_size=size, supersample=2, fps=fps,
                                  look="hd", palette_size=32, out_dir=tmp_path / "sprites", progress=progress)
    assert sorted(frames) == ["down_right", "up_right"]
    for direction, by_clip in frames.items():
        assert sorted(by_clip) == ["death", "idle", "walk"]
        for clip in clips:
            paths = by_clip[clip["name"]]
            assert len(paths) == round(clip["duration"] * fps), (direction, clip)
            for path in paths:
                image = Image.open(path)
                assert image.size == (size, size) and image.mode == "RGBA"
                alpha = np.asarray(image)[..., 3]
                assert alpha[0, 0] == 0 and alpha[-1, -1] == 0 and alpha[0, -1] == 0  # transparent background
                assert alpha.max() == 255
    assert seen[-1] == ("Sprites rendered", 1.0)
    assert not (tmp_path / "sprites" / "raw").exists()

    # one framing everywhere: the feet stand on the same row and the body keeps its height
    standing = [_feet_and_height(frames[d][c][0]) for d in frames for c in ("idle", "walk")]
    assert max(f for f, _, _ in standing) - min(f for f, _, _ in standing) <= 1
    assert max(h for _, h, _ in standing) - min(h for _, h, _ in standing) <= 3
    assert all(abs(x - size // 2) <= 3 for _, _, x in standing)  # centred over the feet, so flip_h mirrors

    # rendered again with the same settings: the frames are reused, Blender is not started
    log = tmp_path / "sprites" / "blender_render_sprites.log"
    stamp = log.stat().st_mtime_ns
    again = tools.render_sprites(animated_glb=animated, clips=clips, directions=CAMERA_DIRECTIONS["three_quarter"],
                                 camera_preset="three_quarter", frame_size=size, supersample=2, fps=fps,
                                 look="hd", palette_size=32, out_dir=tmp_path / "sprites", progress=progress)
    assert again == frames and log.stat().st_mtime_ns == stamp


@needs_blender
def test_pixel_sprites_use_nearest_neighbour_and_the_palette_size(tmp_path):
    animated, clips = _animated_sample(tmp_path, ["walk"])
    tools = BlenderTools(load_settings(data_dir=tmp_path, blender_path=BLENDER))
    frames = tools.render_sprites(animated_glb=animated, clips=clips, directions=CAMERA_DIRECTIONS["topdown_4"],
                                  camera_preset="topdown_4", frame_size=48, supersample=3, fps=6, look="pixel",
                                  palette_size=6, out_dir=tmp_path / "sprites", progress=lambda s, f: None)
    assert sorted(frames) == ["down", "left", "right", "up"]
    colours = set()
    for by_clip in frames.values():
        assert len(by_clip["walk"]) == 6
        for path in by_clip["walk"]:
            pixels = np.asarray(Image.open(path))
            assert pixels.shape == (48, 48, 4)
            assert set(np.unique(pixels[..., 3])) <= {0, 255}
            colours |= {tuple(p) for p in pixels[pixels[..., 3] == 255][:, :3]}
    assert 2 <= len(colours) <= 6


@needs_blender
def test_godot_run_with_real_blender_shows_sprites_at_gate_3(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings(data_dir=tmp_path / "data", blender_path=BLENDER)
    with TestClient(real_blender_app(settings)) as client:
        project = make_project(client, "godot", tmp_path / "GodotGame")
        asset_id = client.post("/api/assets", json={
            "project_id": project["id"], "kind": "monster", "name": "Moss Wolf",
            "description": "a mossy forest wolf", "overrides": {
                "camera_preset": "topdown_4", "clips": ["idle", "walk", "death"],
                "render": {"fps": 6, "frame_size": {"monster": 48}}}}).json()["id"]
        wait_for(client, asset_id, "waiting", gate=1)
        client.post(f"/api/assets/{asset_id}/gates/1/approve", json={"candidate": 1})
        wait_for(client, asset_id, "waiting", gate=2, timeout=300)
        client.post(f"/api/assets/{asset_id}/gates/2/approve")
        asset = wait_for(client, asset_id, "waiting", gate=3, timeout=300)
        sprites = asset["outputs"]["rig"]["sprites"]
        assert sprites["directions"] == ["down", "right", "up", "left"] and sprites["frame_size"] == 48
        clips = {c["name"]: c for c in asset["outputs"]["rig"]["clips"]}
        for direction in sprites["directions"]:
            assert set(sprites["files"][direction]) == set(clips)
            assert len(sprites["files"][direction]["walk"]) == round(clips["walk"]["duration"] * 6)
        frame = client.get(f"/api/assets/{asset_id}/files/{sprites['files']['up']['walk'][0]}")
        assert frame.status_code == 200 and Image.open(io.BytesIO(frame.content)).size == (48, 48)
        client.post(f"/api/assets/{asset_id}/gates/3/approve")
        done = wait_for(client, asset_id, "done", timeout=300)
    assert any(f.endswith(".tres") for f in done["outputs"]["export"]["files"])
