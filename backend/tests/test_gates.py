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


def test_failed_stage_can_be_retried(client, settings, tmp_path, monkeypatch):
    project = make_project(client, "unity", tmp_path / "g")
    pipeline = client.app.state.pipeline
    original = pipeline.adapters.mesh.image_to_mesh

    def broken(**kwargs):
        raise RuntimeError("Hunyuan3D is not answering")

    monkeypatch.setattr(pipeline.adapters.mesh, "image_to_mesh", broken)
    asset = _new(client, project)
    wait_for(client, asset["id"], "waiting", gate=1)
    client.post(f"/api/assets/{asset['id']}/gates/1/approve", json={"candidate": 1})
    failed = wait_for(client, asset["id"], "failed")
    assert failed["stage"] == "mesh" and "not answering" in failed["error"]

    monkeypatch.setattr(pipeline.adapters.mesh, "image_to_mesh", original)
    assert client.post(f"/api/assets/{asset['id']}/retry").status_code == 200
    assert wait_for(client, asset["id"], "waiting", gate=2)["error"] is None


def test_delete_while_running_cleans_up(client, settings, tmp_path):
    import threading

    project = make_project(client, "unity", tmp_path / "g")
    pipeline = client.app.state.pipeline
    release = threading.Event()
    original = pipeline.adapters.llm.expand

    def slow(**kwargs):
        release.wait(5)
        return original(**kwargs)

    pipeline.adapters.llm.expand = slow
    try:
        asset = _new(client, project)
        wait_for(client, asset["id"], "running")
        assert client.delete(f"/api/assets/{asset['id']}").status_code == 204
        release.set()
        import time
        for _ in range(200):
            if pipeline.current is None and not (settings.assets_dir / asset["id"]).exists():
                break
            time.sleep(0.02)
        assert not (settings.assets_dir / asset["id"]).exists()
    finally:
        pipeline.adapters.llm.expand = original


def test_biped_characters_get_the_pose_guide_and_arms_check(client, settings, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    hero = wait_for(client, _new(client, project, archetype="biped")["id"], "waiting", gate=1)
    prompt = hero["outputs"]["prompt"]
    assert [c["candidate"] for c in prompt["pose_check"]] == list(range(1, len(prompt["candidates"]) + 1))
    assert all(set(c) == {"candidate", "arms_clear", "clear_share"} for c in prompt["pose_check"])
    assert "hands touching the legs" in prompt["negative_prompt"]
    hints = client.app.state.pipeline.adapters.image.last_hints
    assert Path(hints["pose_guide"]).name == "pose_guide.png" and Path(hints["pose_guide"]).is_file()

    gear = wait_for(client, _new(client, project, kind="gear", slot="head", description="a horned helm")["id"],
                    "waiting", gate=1)
    assert gear["outputs"]["prompt"]["pose_check"] is None
    assert "pose_guide" not in client.app.state.pipeline.adapters.image.last_hints


def test_paper_doll_assets_share_clips_and_framing(client, settings, tmp_path):
    from assetgen.defaults import PAPER_DOLL_FRAMING, WEAPON_ATTACKS
    pipeline = client.app.state.pipeline
    profile = pipeline.asset_settings({"profile_id": None, "overrides": {}})
    attacks = {c for clips in WEAPON_ATTACKS.values() for c in clips}
    hero = {"kind": "hero", "archetype": "biped"}
    helmet = {"kind": "gear", "archetype": "biped", "slot": "head"}
    wolf = {"kind": "monster", "archetype": "quadruped"}
    assert attacks <= set(pipeline.clips_for(hero, profile)) == set(pipeline.clips_for(helmet, profile))
    assert not attacks & set(pipeline.clips_for(wolf, profile))
    assert pipeline.paper_doll(hero) and pipeline.paper_doll(helmet) and not pipeline.paper_doll(wolf)
    assert PAPER_DOLL_FRAMING["span"] > 1.8 * 2  # a 1.8 m body with a 1 m weapon swung out


def test_joints_moved_at_gate_3_are_used_and_can_be_reset(client, settings, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    asset = wait_for(client, _new(client, project, archetype="biped")["id"], "waiting", gate=1)
    client.post(f"/api/assets/{asset['id']}/gates/1/approve", json={"candidate": 1})
    wait_for(client, asset["id"], "waiting", gate=2)
    client.post(f"/api/assets/{asset['id']}/gates/2/approve", json={})
    rigged = wait_for(client, asset["id"], "waiting", gate=3)
    joints = rigged["outputs"]["rig"]["joints"]
    assert "calf_l" in joints and joints["calf_l"]["parent"] == "thigh_l" and "weapon_r" not in joints

    moved = [0.1, 0.0, 0.55]
    response = client.post(f"/api/assets/{asset['id']}/gates/3/reject", json={"joints": {"calf_l": moved}})
    assert response.status_code == 200, response.text
    again = wait_for(client, asset["id"], "waiting", gate=3)
    assert again["outputs"]["rig"]["joints"]["calf_l"]["head"] == moved
    assert again["outputs"]["rig"]["joint_overrides"] == ["calf_l"]

    bad = client.post(f"/api/assets/{asset['id']}/gates/3/reject", json={"joints": {"tail_07": [0, 0, 0]}})
    assert bad.status_code in (409, 422) and "tail_07" in bad.text
    client.post(f"/api/assets/{asset['id']}/gates/3/reject", json={"joints": {}})
    reset = wait_for(client, asset["id"], "waiting", gate=3)
    assert reset["outputs"]["rig"]["joint_overrides"] == []
    assert reset["outputs"]["rig"]["joints"]["calf_l"]["head"] != moved


def _to_gate(client, asset_id: str, gate: int) -> dict:
    asset = wait_for(client, asset_id, "waiting", gate=1)
    if gate > 1:
        client.post(f"/api/assets/{asset_id}/gates/1/approve", json={"candidate": 1})
        asset = wait_for(client, asset_id, "waiting", gate=2)
    if gate > 2:
        client.post(f"/api/assets/{asset_id}/gates/2/approve", json={})
        asset = wait_for(client, asset_id, "waiting", gate=3)
    return asset


def test_a_character_height_is_used_by_the_cleanup_and_can_change_at_gate_2(client, settings, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    asset = _to_gate(client, _new(client, project, archetype="biped", height=1.3)["id"], 2)
    blender = client.app.state.pipeline.adapters.blender
    assert asset["height"] == 1.3 and blender.last_height == 1.3
    client.post(f"/api/assets/{asset['id']}/gates/2/reject", json={"height": 1.5})
    again = wait_for(client, asset["id"], "waiting", gate=2)
    assert again["height"] == 1.5 and blender.last_height == 1.5
    refused = client.post(f"/api/assets/{asset['id']}/gates/2/reject", json={"wearer": ""})
    assert refused.status_code in (409, 422)
    gear = client.post("/api/assets", json={"project_id": project["id"], "kind": "gear", "slot": "head",
                                            "description": "a helm", "height": 1.0})
    assert gear.status_code == 422


def test_gear_is_refitted_on_a_chosen_wearer(client, settings, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    hero = _to_gate(client, _new(client, project, archetype="biped", name="Elf")["id"], 3)
    assert client.get(f"/api/projects/{project['id']}/wearers").json() == []  # the fake rig keeps no scene
    (Path(settings.data_dir) / "assets" / hero["id"] / "rig" / "rigged.blend").write_bytes(b"blend")
    assert [w["name"] for w in client.get(f"/api/projects/{project['id']}/wearers").json()] == ["Elf"]

    helm = _to_gate(client, _new(client, project, kind="gear", slot="head", description="a helm")["id"], 3)
    assert helm["outputs"]["rig"]["wearer"] is None
    response = client.post(f"/api/assets/{helm['id']}/gates/3/reject", json={"wearer": hero["id"]})
    assert response.status_code == 200, response.text
    again = wait_for(client, helm["id"], "waiting", gate=3)
    assert again["wearer_id"] == hero["id"] and again["outputs"]["rig"]["wearer"]["name"] == "Elf"
    attach = client.app.state.pipeline.adapters.blender.last_attach
    assert attach["wearer"]["blend"].endswith("rigged.blend")
    assert {c["name"] for c in attach["wearer"]["clips"]} >= {"walk", "attack_1"}

    client.post(f"/api/assets/{helm['id']}/gates/3/reject", json={"wearer": ""})
    back = wait_for(client, helm["id"], "waiting", gate=3)
    assert back["wearer_id"] is None and back["outputs"]["rig"]["wearer"] is None
    bad = client.post(f"/api/assets/{helm['id']}/gates/3/reject", json={"wearer": helm["id"]})
    assert bad.status_code in (409, 422)
