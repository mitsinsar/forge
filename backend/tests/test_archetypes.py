"""The archetype templates (ticket #10): every archetype has its skeleton.blend and one
GLB per clip whose skeleton holds the archetype's joints, plus clips.json and LICENCES.md.
The rebuild test runs the Blender build script when Blender is installed."""

from __future__ import annotations

import json
import subprocess

import pytest

from assetgen import imaging
from assetgen.archetypes import ARCHETYPES_DIR, load_archetypes
from assetgen.defaults import BIPED_EXTRA_CLIPS, LOOPING_CLIPS, STANDARD_CLIPS, WEAPON_ATTACKS
from test_blender import BLENDER, needs_blender

SCRIPT = ARCHETYPES_DIR.parents[2] / "blender_scripts" / "build_archetypes.py"
IDS = ["biped", "quadruped", "flyer", "blob", "spider", "serpent"]


def clip_glb(folder, clip: str) -> dict:
    return imaging.read_glb(folder / "clips" / f"{clip}.glb")[0]


@pytest.mark.parametrize("arch_id", IDS)
def test_template_files_and_joints(arch_id):
    spec = load_archetypes()[arch_id]
    folder = ARCHETYPES_DIR / arch_id
    assert spec["has_skeleton_blend"]
    header = (folder / "skeleton.blend").read_bytes()[:7]
    assert header == b"BLENDER" or header[:4] == b"\x28\xb5\x2f\xfd"  # plain, or zstd-compressed
    assert (folder / "skeleton.blend").stat().st_size > 10_000
    assert len(spec["joints"]) == len(set(spec["joints"])) > 0
    for socket_bone in spec.get("sockets", {}).values():
        assert socket_bone in spec["joints"]

    expected = set(STANDARD_CLIPS)
    if arch_id == "biped":
        expected |= set(BIPED_EXTRA_CLIPS) | {c for clips in WEAPON_ATTACKS.values() for c in clips}
    assert expected <= set(spec["clips"])

    record = json.loads((folder / "clips.json").read_text(encoding="utf-8"))
    assert set(record) == set(spec["clips"])
    for clip in spec["clips"]:
        gltf = clip_glb(folder, clip)
        assert [a["name"] for a in gltf["animations"]] == [clip]
        (skin,) = gltf["skins"]  # the bones come back as a skeleton, not loose nodes
        skin_joints = {gltf["nodes"][j]["name"] for j in skin["joints"]}
        assert set(spec["joints"]) <= skin_joints, clip
        assert record[clip]["duration"] > 0 and record[clip]["loop"] == (clip in LOOPING_CLIPS)

    licences = (folder / "LICENCES.md").read_text(encoding="utf-8")
    assert all(f"| {clip} |" in licences for clip in spec["clips"])
    assert ("CC0" in licences) or ("generated" in licences)


def test_biped_is_rigged_like_a_humanoid():
    spec = load_archetypes()["biped"]
    assert spec["rig_type"] == "Humanoid"
    for bone in ("pelvis", "spine_01", "head", "upperarm_l", "hand_r", "thigh_l", "foot_r"):
        assert any(j.lower() == bone for j in spec["joints"]), bone
    assert spec["sockets"] == {"weapon_r": "hand_r", "weapon_l": "hand_l"}


@needs_blender
def test_build_script_rebuilds_the_generated_archetypes(tmp_path):
    """Spider and serpent need no source pack, so they rebuild anywhere Blender is."""
    subprocess.run([BLENDER, "-b", "--factory-startup", "--python-exit-code", "1", "-P", str(SCRIPT), "--",
                    "spider", "serpent", "--out", str(tmp_path)], check=True, capture_output=True, timeout=600)
    for arch_id in ("spider", "serpent"):
        spec = load_archetypes()[arch_id]
        built = tmp_path / arch_id
        assert (built / "skeleton.blend").is_file()
        assert json.loads((built / "clips.json").read_text()).keys() == json.loads(
            (ARCHETYPES_DIR / arch_id / "clips.json").read_text()).keys()
        for clip in spec["clips"]:
            gltf = clip_glb(built, clip)
            assert {gltf["nodes"][j]["name"] for j in gltf["skins"][0]["joints"]} >= set(spec["joints"])
