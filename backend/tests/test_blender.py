"""The real Blender adapter: the runner (with a fake Blender), and the scripts
on small sample meshes when Blender is installed (skipped otherwise).

Blender is looked for at ``ASSETGEN_BLENDER_PATH``, on PATH and in the usual
install folders of macOS and Windows.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assetgen.adapters import build_adapters
from assetgen.adapters.mock import MockBlender, MockRig, _parts_info
from assetgen.adapters.mock.glb import GlbBuilder, build_mesh, read_glb_json
from assetgen.adapters.mock.shapes import Part, asset_parts
from assetgen.adapters.real.blender import BlenderTools, merge_glb_extras
from assetgen.adapters.real.blender_runner import SCRIPTS_DIR, BlenderError, run_script
from assetgen.app import create_app
from assetgen.archetypes import get_archetype
from assetgen.config import load_settings
from conftest import make_project, wait_for

HELPERS = Path(__file__).parent / "blender"


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


def test_real_blender_is_registered_and_keeps_rig_and_sprites_on_the_mock(tmp_path):
    adapters = build_adapters(load_settings(data_dir=tmp_path, adapters={"blender": "real"}))
    assert isinstance(adapters.blender, BlenderTools) and adapters.blender.mode == "real"
    assert isinstance(adapters.blender.mock, MockBlender)


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
def test_export_of_the_mock_rig_reimports_with_lods_and_clips(tmp_path):
    tools = BlenderTools(load_settings(data_dir=tmp_path, blender_path=BLENDER, mock_step_delay=0.0))
    mesh_glb, lods = _mesh_stage(tmp_path, tools)
    biped = get_archetype("biped")
    skeleton = MockRig(0).predict_skeleton(mesh=mesh_glb, archetype=biped, out_dir=tmp_path / "rig",
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
    settings = load_settings(data_dir=tmp_path / "data", mock_step_delay=0.0, blender_path=BLENDER,
                             adapters={"blender": "real"})
    game = tmp_path / "UnityGame"
    with TestClient(create_app(settings)) as client:
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
    settings = load_settings(data_dir=tmp_path / "data", mock_step_delay=0.0, blender_path=BLENDER,
                             adapters={"blender": "real"})
    with TestClient(create_app(settings)) as client:
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
