"""Stages resume after a restart, and per-asset overrides reach the outputs."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from assetgen.adapters import RealAdapterMissing
from assetgen.app import create_app
from assetgen.config import load_settings
from assetgen.db import Database
from conftest import make_project, wait_for


def test_running_asset_resumes_after_restart(settings, tmp_path):
    with TestClient(create_app(settings)) as client:
        project = make_project(client, "unity", tmp_path / "g")
        asset = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero",
                                                 "description": "a knight"}).json()
        wait_for(client, asset["id"], "waiting", gate=1)

    # Pretend the server died half-way through the prompt stage: one candidate
    # and the expansion are on disk, the rest is missing, the asset says running.
    prompt_dir = settings.assets_dir / asset["id"] / "prompt"
    (prompt_dir / "manifest.json").unlink()
    for index in (2, 3, 4):
        (prompt_dir / f"candidate_{index}.png").unlink()
    kept = (prompt_dir / "candidate_1.png").stat().st_mtime_ns
    db = Database(settings.db_path)
    db.update("assets", asset["id"], {"status": "running", "gate": None, "outputs": {}})
    db.close()

    with TestClient(create_app(settings)) as client:
        resumed = wait_for(client, asset["id"], "waiting", gate=1)
        assert len(resumed["outputs"]["prompt"]["candidates"]) == 4
        assert (prompt_dir / "candidate_1.png").stat().st_mtime_ns == kept  # not redone


def test_overrides_pixel_topdown(client, tmp_path):
    game = tmp_path / "godot"
    project = make_project(client, "godot", game)
    asset = client.post("/api/assets", json={
        "project_id": project["id"], "kind": "monster", "name": "Cave Bat", "description": "a cave bat",
        "overrides": {"camera_preset": "topdown_8", "candidates": 2,
                      "render": {"look": "pixel", "frame_size": {"monster": 64}}},
    }).json()
    first = wait_for(client, asset["id"], "waiting", gate=1)
    assert first["archetype"] == "flyer" and len(first["urls"]["candidates"]) == 2
    client.post(f"/api/assets/{asset['id']}/gates/1/approve", json={"candidate": 2})
    wait_for(client, asset["id"], "waiting", gate=2)
    client.post(f"/api/assets/{asset['id']}/gates/2/approve")
    wait_for(client, asset["id"], "waiting", gate=3)
    client.post(f"/api/assets/{asset['id']}/gates/3/approve")
    done = wait_for(client, asset["id"], "done")
    assert len(done["outputs"]["export"]["directions"]) == 8
    folder = game / "generated" / "monster" / "Cave_Bat"
    assert len(list(folder.glob("body_*.png"))) == 8
    assert "texture_filter = 1" in (folder / "PaperDoll.tscn").read_text()
    sidecar = json.loads((folder / "asset.json").read_text())
    assert sidecar["frame_size"] == 64 and sidecar["mirrored"] == {}
    with Image.open(folder / "body_down.png") as atlas:
        assert atlas.width % 64 == 0


def test_settings_from_env_and_real_adapter_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "assetgen.toml").write_text('port = 9001\ncomfyui_url = "http://gpu:8188"\n[adapters]\nllm = "mock"\n')
    monkeypatch.setenv("ASSETGEN_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("ASSETGEN_ADAPTERS__MESH", "real")
    settings = load_settings()
    assert settings.port == 9001 and settings.comfyui_url == "http://gpu:8188"
    assert settings.data_dir == tmp_path / "d" and settings.adapters.mesh == "real"
    with pytest.raises(RealAdapterMissing):
        create_app(settings)
    monkeypatch.setenv("ASSETGEN_ADAPTERS__MESH", "fake")
    with pytest.raises(ValueError):
        load_settings()
