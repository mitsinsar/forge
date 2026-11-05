"""Godot projects get their sprite frames at gate 3, so they can be checked before export."""

from __future__ import annotations

from conftest import make_project, wait_for


def to_gate_3(client, project_id: str, kind: str, description: str) -> dict:
    asset_id = client.post("/api/assets", json={"project_id": project_id, "kind": kind,
                                                "description": description}).json()["id"]
    wait_for(client, asset_id, "waiting", gate=1)
    client.post(f"/api/assets/{asset_id}/gates/1/approve", json={"candidate": 1})
    wait_for(client, asset_id, "waiting", gate=2)
    client.post(f"/api/assets/{asset_id}/gates/2/approve")
    return wait_for(client, asset_id, "waiting", gate=3)


def test_godot_monster_has_sprite_frames_at_gate_3(client, tmp_path):
    project = make_project(client, "godot", tmp_path / "GodotGame")
    asset = to_gate_3(client, project["id"], "monster", "a grey wolf")
    sprites = asset["outputs"]["rig"]["sprites"]
    assert sprites["directions"] == ["right"] and sprites["mirrored"] == {"left": "right"}
    assert sprites["fps"] == 12 and sprites["frame_size"] == 128
    clips = {c["name"]: c for c in asset["outputs"]["rig"]["clips"]}
    files = sprites["files"]["right"]
    assert set(files) == set(clips)
    assert len(files["walk"]) == round(clips["walk"]["duration"] * 12)
    frame = client.get(f"/api/assets/{asset['id']}/files/{files['walk'][0]}")
    assert frame.status_code == 200 and frame.content[:4] == b"\x89PNG"

    # the export reuses the frames and still writes the Godot folder
    client.post(f"/api/assets/{asset['id']}/gates/3/approve")
    done = wait_for(client, asset["id"], "done")
    assert any(f.endswith(".tres") for f in done["outputs"]["export"]["files"])


def test_unity_hero_has_no_sprites(client, tmp_path):
    project = make_project(client, "unity", tmp_path / "UnityGame")
    asset = to_gate_3(client, project["id"], "hero", "a knight in red armour")
    assert "sprites" not in asset["outputs"]["rig"]
