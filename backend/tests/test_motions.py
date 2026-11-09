"""Skill animations: a description becomes a clip (HY-Motion) any biped of a profile can play."""

from __future__ import annotations

import time

from conftest import make_project, wait_for
from test_checks import _clean


def wait_motion(client, motion_id: str, status: str = "done", timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        motion = client.get(f"/api/motions/{motion_id}").json()
        if motion["status"] == status:
            return motion
        if motion["status"] == "failed":
            raise AssertionError(motion["error"])
        time.sleep(0.05)
    raise AssertionError(f"still {motion['status']}: {motion['step']}")


def test_a_skill_animation_is_written_made_and_previewed(client):
    created = client.post("/api/motions", json={"title": "Fire Bolt", "description": "hurls a bolt of fire"}).json()
    assert created["name"] == "skill_fire_bolt" and created["status"] == "queued"
    done = wait_motion(client, created["id"])
    assert done["prompt"] == "A person performs fire bolt" and done["duration"] == 2.0  # the language model wrote it
    assert done["clip"]["frames"] == 60 and done["url"]
    assert client.get(done["url"]).status_code == 200
    # A second one with the same title gets its own clip name.
    again = client.post("/api/motions", json={"title": "Fire bolt", "description": "x", "prompt": "A person waves"}).json()
    assert again["name"] == "skill_fire_bolt_2"
    assert wait_motion(client, again["id"])["prompt"] == "A person waves"  # your own prompt is used as it is


def test_a_note_rewrites_the_prompt_and_a_new_seed_is_used(client):
    pipeline = client.app.state.pipeline
    created = client.post("/api/motions", json={"title": "Heal", "description": "raises both hands"}).json()
    wait_motion(client, created["id"])
    redone = client.post(f"/api/motions/{created['id']}/redo", json={"note": "slower"}).json()
    assert redone["attempt"] == 2 and redone["status"] == "queued"
    done = wait_motion(client, created["id"])
    assert done["prompt"].endswith("slower")
    busy = pipeline.db.update("motions", created["id"], {"status": "running"})
    assert client.post(f"/api/motions/{busy['id']}/redo", json={}).status_code == 409


def test_a_profiles_heroes_get_the_skill_clip(client, tmp_path, monkeypatch):
    _clean(monkeypatch)
    project = make_project(client, "unity", tmp_path / "g")
    created = client.post("/api/motions", json={"title": "Poison Arrow", "description": "draws a bow"}).json()
    wait_motion(client, created["id"])
    profile = client.post(f"/api/motions/{created['id']}/use", json={"profile_id": project["default_profile_id"]}).json()
    assert profile["biped_extra_clips"][-1] == "skill_poison_arrow"
    hero = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero", "archetype": "biped",
                                            "description": "a ranger", "autopilot": True}).json()
    rigged = wait_for(client, hero["id"], "waiting", gate=3, timeout=60)
    assert "skill_poison_arrow" in [c["name"] for c in rigged["outputs"]["rig"]["clips"]]
    assert client.delete(f"/api/motions/{created['id']}").status_code == 204
    assert client.get(f"/api/motions/{created['id']}").status_code == 404
