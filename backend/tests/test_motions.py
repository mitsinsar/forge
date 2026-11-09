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


def test_four_versions_are_judged_and_the_best_is_used(client):
    llm = client.app.state.pipeline.adapters.llm
    llm.motion_judgements = [[{"match": 4, "problems": ["weak"]}, {"match": 8, "problems": []},
                              {"match": 8, "problems": []}, None]]
    created = client.post("/api/motions", json={"title": "Smash", "description": "slams the ground"}).json()
    done = wait_motion(client, created["id"])
    assert [c["judge"] for c in done["candidates"]] == [4, 8, 8, None]
    assert [c["match"] for c in done["candidates"]] == [4, 8, 8, 7]  # 7: not judged, nothing measured wrong
    assert len({c["seed"] for c in done["candidates"]}) == 4
    assert done["chosen"] == 2  # the best score; the first of a tie
    assert client.get(done["candidates"][0]["url"]).headers["content-type"] == "image/png"
    # Another version: only the clip is made again.
    picked = client.post(f"/api/motions/{created['id']}/pick", json={"candidate": 4})
    assert picked.status_code == 200 and picked.json()["status"] == "queued"
    again = wait_motion(client, created["id"])
    assert again["chosen"] == 4 and [c["judge"] for c in again["candidates"]] == [4, 8, 8, None]
    assert client.post(f"/api/motions/{created['id']}/pick", json={"candidate": 5}).status_code == 422
    # Made again: new versions, judged again.
    client.post(f"/api/motions/{created['id']}/redo", json={})
    redone = wait_motion(client, created["id"])
    assert redone["chosen"] == 1 and all(c["judge"] is None for c in redone["candidates"])


def test_the_wrong_hand_walking_away_and_turning_are_measured(tmp_path):
    import json
    import math
    from assetgen.motion_sheet import measure
    names = ["Pelvis", "L_Hip", "R_Hip", "L_Wrist", "R_Wrist"]
    rest = [[0, 1, 0], [0.1, 1, 0], [-0.1, 1, 0], [0.7, 1.4, 0], [-0.7, 1.4, 0]]
    frames = 30

    def turn(angle):  # about Y, row-major
        c, s = math.cos(angle), math.sin(angle)
        return [c, 0, s, 0, 1, 0, -s, 0, c]

    identity = [1, 0, 0, 0, 1, 0, 0, 0, 1]
    # The pelvis turns 90 degrees (the left wrist swings forward) and walks 1 m.
    rotations = [[turn(-math.pi / 2 * f / (frames - 1))] + [identity] * 4 for f in range(frames)]
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"fps": 30, "frames": frames, "joints": names, "parents": [-1, 0, 0, 0, 0],
                                "rest": rest, "rotations": rotations,
                                "translation": [[0, 0, f / (frames - 1)] for f in range(frames)]}))
    found = {c["id"]: c["ok"] for c in measure(path, "A person thrusts the right palm forward")}
    assert found == {"hand": False, "drift": False, "facing": False, "action": True}
    both = "A person pulls the right arm back, raises the left arm, then explosively thrusts the right palm forward"
    assert not measure(path, both)[0]["ok"]  # the action's hand, though both are named
    assert measure(path, "A person dashes forward and thrusts the left palm")[1]["ok"]  # a dash may travel
