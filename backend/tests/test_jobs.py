"""Side jobs: items fitted on other heroes (outfits), example sheets for LoRAs and style profiles."""

from __future__ import annotations

import io
import time
from pathlib import Path

from PIL import Image

from conftest import make_project, wait_for


def wait_job(client, job_id: str, timeout: float = 20.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def _asset(client, project, **body) -> dict:
    response = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero",
                                                "description": "a hooded rogue", **body})
    assert response.status_code == 201, response.text
    return response.json()


def _to_gate3(client, asset_id: str) -> dict:
    wait_for(client, asset_id, "waiting", gate=1)
    client.post(f"/api/assets/{asset_id}/gates/1/approve", json={"candidate": 1})
    wait_for(client, asset_id, "waiting", gate=2)
    client.post(f"/api/assets/{asset_id}/gates/2/approve", json={})
    return wait_for(client, asset_id, "waiting", gate=3)


def test_an_item_is_fitted_on_another_hero_and_kept_per_hero(client, settings, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    elf = _to_gate3(client, _asset(client, project, archetype="biped", name="Elf")["id"])
    (Path(settings.data_dir) / "assets" / elf["id"] / "rig" / "rigged.blend").write_bytes(b"blend")
    helm = _to_gate3(client, _asset(client, project, kind="gear", slot="head", description="a helm")["id"])

    job = client.post(f"/api/assets/{helm['id']}/fits", json={"wearer_id": elf["id"]})
    assert job.status_code == 202, job.text
    same = client.post(f"/api/assets/{helm['id']}/fits", json={"wearer_id": elf["id"]}).json()
    assert same["id"] == job.json()["id"] or same["status"] == "done"  # no second job while one waits
    done = wait_job(client, job.json()["id"])
    assert done["status"] == "done", done
    item = client.get(f"/api/assets/{helm['id']}").json()
    assert item["fits"][elf["id"]]["wearer"]["name"] == "Elf"
    assert item["urls"]["fits"][elf["id"]].split("?")[0].endswith(f"fits/{elf['id']}/animated.glb")
    assert client.get(item["urls"]["fits"][elf["id"]]).status_code == 200
    assert item["status"] == "waiting"  # the item's own gate is untouched

    # Rigging the hero again drops the fits made on its old skeleton.
    client.post(f"/api/assets/{elf['id']}/gates/3/reject", json={"joints": {}})
    wait_for(client, elf["id"], "waiting", gate=3)
    item = client.get(f"/api/assets/{helm['id']}").json()
    assert elf["id"] not in (item.get("fits") or {})
    assert not (Path(settings.data_dir) / "assets" / helm["id"] / "fits" / elf["id"]).exists()
    (Path(settings.data_dir) / "assets" / elf["id"] / "rig" / "rigged.blend").write_bytes(b"blend")

    assert client.post(f"/api/assets/{helm['id']}/fits", json={"wearer_id": helm["id"]}).status_code == 422
    assert client.post(f"/api/assets/{elf['id']}/fits", json={"wearer_id": elf["id"]}).status_code == 422


def test_lora_and_profile_example_sheets(client, settings, tmp_path):
    lora_file = tmp_path / "ink.safetensors"
    lora_file.write_bytes(b"lora")
    lora = client.post("/api/loras", json={"name": "Ink", "path": str(lora_file), "base_model": "sdxl"}).json()
    job = client.post(f"/api/loras/{lora['id']}/examples", json={"weight": 0.6}).json()
    assert wait_job(client, job["id"])["status"] == "done"
    lora = client.get(f"/api/loras/{lora['id']}").json()
    examples = lora["examples"]
    assert examples["weight"] == 0.6 and [i["name"] for i in examples["items"]] == ["knight", "wolf", "sword"]
    for item in examples["items"]:
        for variant in ("with", "without"):
            assert client.get(f"/api/examples/{item[variant]}").status_code == 200

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 200, 30)).save(buffer, "PNG")
    added = client.post(f"/api/loras/{lora['id']}/example-images",
                        files={"file": ("mine.png", buffer.getvalue(), "image/png")})
    assert added.status_code == 201, added.text
    (path,) = added.json()["example_images"]
    assert client.get(f"/api/examples/{path}").status_code == 200
    removed = client.delete(f"/api/loras/{lora['id']}/example-images/{Path(path).name}")
    assert removed.json()["example_images"] == []
    assert client.get("/api/examples/../assetgen.sqlite3").status_code == 404

    project = make_project(client, "unity", tmp_path / "g")
    profile_id = project["default_profile_id"]
    job = client.post(f"/api/profiles/{profile_id}/examples").json()
    assert wait_job(client, job["id"])["status"] == "done"
    profile = client.get(f"/api/profiles/{profile_id}").json()
    assert len(profile["examples"]["items"]) == 3 and "without" not in profile["examples"]["items"][0]


def test_outfits_are_saved_and_checked(client, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    hero = _asset(client, project, archetype="biped")
    helm = _asset(client, project, kind="gear", slot="head", description="a helm")
    outfit = client.post("/api/outfits", json={"project_id": project["id"], "name": "Night watch",
                                               "hero_id": hero["id"], "items": [helm["id"]]})
    assert outfit.status_code == 201, outfit.text
    assert client.post("/api/outfits", json={"project_id": project["id"], "name": "Bad", "hero_id": helm["id"],
                                             "items": []}).status_code == 422
    changed = client.patch(f"/api/outfits/{outfit.json()['id']}", json={"items": []}).json()
    assert changed["items"] == [] and changed["name"] == "Night watch"
    assert [o["name"] for o in client.get(f"/api/outfits?project_id={project['id']}").json()] == ["Night watch"]
    assert client.delete(f"/api/outfits/{outfit.json()['id']}").status_code == 204


def test_the_test_dummy_is_built_once_and_dressed_like_a_hero(client, settings, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    first = client.get("/api/dummy").json()
    assert first["ready"] is False and first["job"]["kind"] == "dummy"
    assert wait_job(client, first["job"]["id"])["status"] == "done"
    dummy = client.get("/api/dummy").json()
    assert dummy["ready"] and dummy["name"] == "Test dummy" and "walk" in [c["name"] for c in dummy["clips"]]
    assert client.get(dummy["url"]).status_code == 200

    helm = _to_gate3(client, _asset(client, project, kind="gear", slot="head", description="a helm")["id"])
    job = client.post(f"/api/assets/{helm['id']}/fits", json={"wearer_id": "dummy"})
    assert job.status_code == 202, job.text
    assert wait_job(client, job.json()["id"])["status"] == "done"
    item = client.get(f"/api/assets/{helm['id']}").json()
    assert item["fits"]["dummy"]["wearer"]["name"] == "Test dummy"
    outfit = client.post("/api/outfits", json={"project_id": project["id"], "name": "Trial", "hero_id": "dummy",
                                               "items": [helm["id"]]})
    assert outfit.status_code == 201, outfit.text


def test_a_placement_is_kept_per_hero_and_reaches_the_fit(client, settings, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    helm = _to_gate3(client, _asset(client, project, kind="gear", slot="head", description="a helm")["id"])
    up = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0.1], [0, 0, 0, 1]]
    job = client.post(f"/api/assets/{helm['id']}/fits", json={"wearer_id": "dummy", "placement": up}).json()
    assert wait_job(client, job["id"])["status"] == "done"
    assert client.app.state.pipeline.adapters.blender.last_attach["placement"] == up
    again = client.post(f"/api/assets/{helm['id']}/fits", json={"wearer_id": "dummy", "placement": up}).json()
    assert wait_job(client, again["id"])["status"] == "done"
    item = client.get(f"/api/assets/{helm['id']}").json()
    assert item["placements"]["dummy"][2][3] == 0.2  # added to the kept one
    assert item["fits"]["dummy"]["placed"] and "?v=" in item["urls"]["fits"]["dummy"]
    bad = client.post(f"/api/assets/{helm['id']}/fits", json={"wearer_id": "dummy", "placement": [[1, 0]]})
    assert bad.status_code == 422
    reset = client.post(f"/api/assets/{helm['id']}/fits", json={"wearer_id": "dummy", "reset_placement": True}).json()
    assert wait_job(client, reset["id"])["status"] == "done"
    assert "dummy" not in (client.get(f"/api/assets/{helm['id']}").json().get("placements") or {})


def test_a_queued_stage_can_be_stopped_and_resumed(client, settings, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    hero = _to_gate3(client, _asset(client, project, archetype="biped")["id"])
    pipeline = client.app.state.pipeline
    pipeline.db.update("assets", hero["id"], {"status": "queued", "stage": "rig", "gate": None, "autopilot": True})
    pipeline.db.insert("jobs", {"kind": "fit", "target_id": hero["id"], "params": {"wearer_id": "x"}, "status": "queued",
                                "step": "Waiting", "progress": 0.0, "error": None, "result": None, "queued_at": "2000"})
    report = client.post("/api/queue/stop").json()
    assert report["stopped"] == 1 and report["jobs_stopped"] == 1
    stopped = client.get(f"/api/assets/{hero['id']}").json()
    assert stopped["status"] == "stopped" and not stopped["autopilot"]
    assert client.post(f"/api/assets/{hero['id']}/stop").status_code == 409  # nothing left to stop
    assert client.post(f"/api/assets/{hero['id']}/retry").status_code == 200  # Resume
    assert wait_for(client, hero["id"], "waiting", gate=3)["stage"] == "rig"


def test_lora_trigger_words_lead_the_prompts_that_use_them(client, tmp_path):
    from assetgen.pipeline import Pipeline
    assert Pipeline.with_triggers("a knight", [{"trigger": "hkstyle"}]) == "hkstyle, a knight"
    assert Pipeline.with_triggers("hkstyle, a knight", [{"trigger": "hkstyle"}]) == "hkstyle, a knight"  # once
    assert Pipeline.with_triggers("a knight", [{"trigger": ""}, {}]) == "a knight"
    lora_file = tmp_path / "knights.safetensors"
    lora_file.write_bytes(b"lora")
    lora = client.post("/api/loras", json={"name": "Knights", "path": str(lora_file), "base_model": "sdxl",
                                           "trigger": "hkstyle"}).json()
    assert lora["trigger"] == "hkstyle"
    assert client.patch(f"/api/loras/{lora['id']}", json={"trigger": "hkstyle, knight"}).json()["trigger"] == "hkstyle, knight"
    job = client.post(f"/api/loras/{lora['id']}/examples", json={"weight": 0.8}).json()
    assert wait_job(client, job["id"])["status"] == "done"
    examples = client.get(f"/api/loras/{lora['id']}").json()["examples"]
    assert all(item["prompt"] for item in examples["items"])
