"""Character sets: versions of a hero, its items and its skills; picks; a build that puts them together."""

from __future__ import annotations

import time
from pathlib import Path

from conftest import make_project, wait_for
from test_checks import _clean


def _wait_set(client, set_id, status, timeout=60):
    deadline = time.monotonic() + timeout
    record = None
    while time.monotonic() < deadline:
        record = client.get(f"/api/character-sets/{set_id}").json()
        if record["status"] == status:
            return record
        assert record["status"] != "failed", record["build"]
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {status}; last: {record and record['status']} {record and record['build']}")


def _wait_motion(client, motion_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        motion = client.get(f"/api/motions/{motion_id}").json()
        if motion["status"] == "done":
            return motion
        assert motion["status"] != "failed", motion["error"]
        time.sleep(0.05)
    raise AssertionError("the motion didn't finish")


def test_a_character_set_is_made_picked_and_built(client, settings, tmp_path, monkeypatch):
    _clean(monkeypatch)
    project = make_project(client, "godot", tmp_path / "game")
    answer = client.post("/api/character-sets", json={
        "project_id": project["id"], "name": "Ember Mage", "variations": 2,
        "character": {"description": "a young fire mage in red robes"},
        "items": [{"kind": "weapon", "description": "a wooden staff with an ember", "weapon_class": "staff"}],
        "skills": [{"name": "Firebolt", "description": "hurls a bolt of fire"}]})
    assert answer.status_code == 201, answer.text
    record = answer.json()
    slots = record["slots"]
    assert len(slots["hero"]) == 2 and len(slots["items"][0]["options"]) == 2
    assert len(slots["skills"][0]["effects"]) == 2 and len(slots["skills"][0]["motions"]) == 2
    hero = client.get(f"/api/assets/{slots['hero'][0]}").json()
    assert hero["archetype"] == "biped" and hero["autopilot"] and "variant 1" in hero["description"]

    # Not picked yet: no build.
    assert client.post(f"/api/character-sets/{record['id']}/build").status_code == 422

    hero = wait_for(client, slots["hero"][0], "waiting", gate=3, timeout=60)
    staff = wait_for(client, slots["items"][0]["options"][1], "waiting", gate=3, timeout=60)
    effect = wait_for(client, slots["skills"][0]["effects"][0], "waiting", gate=3, timeout=60)
    motion = _wait_motion(client, slots["skills"][0]["motions"][1])
    (Path(settings.data_dir) / "assets" / hero["id"] / "rig" / "rigged.blend").write_bytes(b"blend")

    # The skill tried on the hero.
    glb = client.get(f"/api/assets/{hero['id']}/skills/{motion['name']}.glb")
    assert glb.status_code == 200 and glb.content[:4] == b"glTF"
    assert client.get(f"/api/assets/{hero['id']}/skills/skill_nothing.glb").status_code == 404

    bad = client.put(f"/api/character-sets/{record['id']}/picks", json={"hero": staff["id"]})
    assert bad.status_code == 422
    picked = client.put(f"/api/character-sets/{record['id']}/picks", json={
        "hero": hero["id"], "items": [staff["id"]],
        "skills": [{"effect": effect["id"], "motion": motion["id"], "release": 0.3}]}).json()
    assert picked["picks"]["skills"][0]["release"] == 0.3
    again = client.put(f"/api/character-sets/{record['id']}/picks", json={"skills": [{"release": 0.6}]}).json()
    assert again["picks"]["skills"][0]["motion"] == motion["id"]  # kept

    built = client.post(f"/api/character-sets/{record['id']}/build")
    assert built.status_code == 202, built.text
    record = _wait_set(client, record["id"], "built", timeout=90)
    manifest = record["manifest"]
    assert manifest["hero"]["asset_id"] == hero["id"] and manifest["items"][0]["weapon_class"] == "staff"
    assert manifest["skills"][0]["animation"] == motion["name"] and manifest["skills"][0]["release"] == 0.6
    assert manifest["skills"][0]["effect"]["kind"] == "effect"
    hero = client.get(f"/api/assets/{hero['id']}").json()
    assert motion["name"] in [c["name"] for c in hero["outputs"]["rig"]["clips"]]
    staff = client.get(f"/api/assets/{staff['id']}").json()
    assert staff["wearer_id"] == hero["id"] and staff["outputs"]["export"]
    assert (tmp_path / "game" / "generated" / "sets" / "ember_mage.json").is_file()
