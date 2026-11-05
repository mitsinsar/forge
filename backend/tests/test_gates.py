"""Gate rules: reject reruns only that stage; wrong-state actions are refused."""

from __future__ import annotations

from pathlib import Path

from conftest import make_project, wait_for


def _new(client, project, **extra):
    body = {"project_id": project["id"], "kind": "hero", "description": "a hooded rogue", **extra}
    response = client.post("/api/assets", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _mtimes(folder: Path) -> dict[str, int]:
    return {p.relative_to(folder).as_posix(): p.stat().st_mtime_ns for p in folder.rglob("*") if p.is_file()}


def test_reject_gate1_reruns_prompt_with_note_and_archetype(client, settings, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    asset = _new(client, project)
    first = wait_for(client, asset["id"], "waiting", gate=1)
    first_prompt = first["outputs"]["prompt"]["prompt"]
    response = client.post(f"/api/assets/{asset['id']}/gates/1/reject",
                           json={"note": "make the cloak dark green", "archetype": "blob"})
    assert response.status_code == 200, response.text
    again = wait_for(client, asset["id"], "waiting", gate=1)
    assert again["archetype"] == "blob"
    assert "make the cloak dark green" in again["outputs"]["prompt"]["prompt"]
    assert again["outputs"]["prompt"]["prompt"] != first_prompt
    assert again["attempts"]["prompt"] == 2
    assert again["notes"][0]["note"] == "make the cloak dark green"


def test_reject_gate2_keeps_prompt_stage(client, settings, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    asset = _new(client, project)
    wait_for(client, asset["id"], "waiting", gate=1)
    client.post(f"/api/assets/{asset['id']}/gates/1/approve", json={"candidate": 3})
    before = wait_for(client, asset["id"], "waiting", gate=2)
    root = settings.assets_dir / asset["id"]
    prompt_files = _mtimes(root / "prompt")
    mesh_glb = (root / "mesh" / "mesh.glb").stat().st_mtime_ns

    assert client.post(f"/api/assets/{asset['id']}/gates/2/reject", json={"note": "thinner legs"}).status_code == 200
    after = wait_for(client, asset["id"], "waiting", gate=2)
    assert _mtimes(root / "prompt") == prompt_files  # gate 1 work untouched
    assert (root / "mesh" / "mesh.glb").stat().st_mtime_ns != mesh_glb  # mesh redone
    assert after["chosen_candidate"] == 3
    assert after["outputs"]["prompt"] == before["outputs"]["prompt"]
    assert after["attempts"] == {"mesh": 2}


def test_reject_gate3_weapon_grip(client, settings, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    asset = _new(client, project, kind="weapon", description="a short sword")
    wait_for(client, asset["id"], "waiting", gate=1)
    client.post(f"/api/assets/{asset['id']}/gates/1/approve", json={"candidate": 1})
    wait_for(client, asset["id"], "waiting", gate=2)
    client.post(f"/api/assets/{asset['id']}/gates/2/approve")
    wait_for(client, asset["id"], "waiting", gate=3)
    root = settings.assets_dir / asset["id"]
    mesh_files = _mtimes(root / "mesh")
    response = client.post(f"/api/assets/{asset['id']}/gates/3/reject",
                           json={"note": "hold it lower", "grip": {"position": [0, -0.2, 0]}})
    assert response.status_code == 200, response.text
    after = wait_for(client, asset["id"], "waiting", gate=3)
    assert after["grip"]["position"] == [0, -0.2, 0] and after["grip"]["socket"] == "weapon_r"
    assert after["outputs"]["rig"]["grip"]["position"] == [0, -0.2, 0]
    assert _mtimes(root / "mesh") == mesh_files


def test_wrong_state_and_bad_input(client, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    asset = _new(client, project)
    wait_for(client, asset["id"], "waiting", gate=1)
    aid = asset["id"]
    assert client.post(f"/api/assets/{aid}/gates/2/approve").status_code == 409
    assert client.post(f"/api/assets/{aid}/gates/1/approve").status_code == 422  # no candidate
    assert client.post(f"/api/assets/{aid}/gates/1/approve", json={"candidate": 9}).status_code == 422
    assert client.post(f"/api/assets/{aid}/gates/1/reject", json={"grip": {"socket": "weapon_l"}}).status_code == 422
    assert client.post(f"/api/assets/{aid}/gates/1/reject", json={"archetype": "dragon"}).status_code == 422
    assert client.post(f"/api/assets/{aid}/retry").status_code == 409
    assert client.post(f"/api/assets/{aid}/export").status_code == 409
    assert client.get(f"/api/assets/{aid}/download").status_code == 404
    assert client.put(f"/api/assets/{aid}/training-candidate", json={"value": True}).status_code == 409
    assert client.get(f"/api/assets/{aid}/files/../../assetgen.sqlite3").status_code == 404
    assert client.post("/api/assets", json={"project_id": project["id"], "kind": "gear", "description": "x",
                                            "archetype": "blob"}).status_code == 422
    assert client.post("/api/assets", json={"project_id": project["id"], "kind": "hero", "description": "x",
                                            "slot": "head"}).status_code == 422
    assert client.post("/api/assets", json={"project_id": "nope", "kind": "hero",
                                            "description": "x"}).status_code == 404


def test_training_candidate_toggle_and_filters(client, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    asset = _new(client, project)
    wait_for(client, asset["id"], "waiting", gate=1)
    client.post(f"/api/assets/{asset['id']}/gates/1/approve", json={"candidate": 1})
    response = client.put(f"/api/assets/{asset['id']}/training-candidate", json={"value": True})
    assert response.status_code == 200 and response.json()["training_candidate"] is True
    listed = client.get("/api/assets", params={"training_candidate": True, "project_id": project["id"]}).json()
    assert [a["id"] for a in listed] == [asset["id"]]
    client.put(f"/api/assets/{asset['id']}/training-candidate", json={"value": False})
    assert client.get("/api/assets", params={"training_candidate": True}).json() == []
    assert len(client.get("/api/assets", params={"kind": "hero", "q": "rogue"}).json()) == 1
    assert client.get("/api/assets", params={"kind": "boss"}).json() == []


def test_delete_asset_removes_files(client, settings, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    asset = _new(client, project)
    wait_for(client, asset["id"], "waiting", gate=1)
    assert (settings.assets_dir / asset["id"]).is_dir()
    assert client.delete(f"/api/assets/{asset['id']}").status_code == 204
    assert client.get(f"/api/assets/{asset['id']}").status_code == 404
    assert not (settings.assets_dir / asset["id"]).exists()
