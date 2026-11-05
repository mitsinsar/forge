"""Projects, style profiles, reference images, LoRAs, meta and archetypes."""

from __future__ import annotations

import io

from PIL import Image

from conftest import make_project


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buffer, "PNG")
    return buffer.getvalue()


def test_meta_and_archetypes(client):
    meta = client.get("/api/meta").json()
    assert set(meta["adapters"].values()) == {"mock"}
    assert meta["profile_defaults"]["budgets"]["triangles"] == {
        "hero": 8000, "monster": 4000, "boss": 15000, "gear": 1500, "weapon": 1000}
    assert meta["profile_defaults"]["render"]["fps"] == 12
    archetypes = {a["id"]: a for a in client.get("/api/archetypes").json()}
    assert set(archetypes) == {"biped", "quadruped", "flyer", "spider", "serpent", "blob"}
    assert "cast" in archetypes["biped"]["clips"] and archetypes["biped"]["rig_type"] == "Humanoid"
    assert client.get("/api/archetypes/serpent").json()["rig_type"] == "Generic"
    assert client.get("/api/archetypes/nope").status_code == 404


def test_project_crud_creates_default_profile(client, tmp_path):
    project = make_project(client, "unity", tmp_path)
    profile = client.get(f"/api/profiles/{project['default_profile_id']}").json()
    assert profile["name"] == "Default" and profile["project_id"] == project["id"]
    updated = client.patch(f"/api/projects/{project['id']}", json={"name": "Renamed", "engine": "godot"}).json()
    assert updated["name"] == "Renamed" and updated["engine"] == "godot"
    assert [p["id"] for p in client.get("/api/projects").json()] == [project["id"]]
    assert client.post("/api/projects", json={"name": "x", "engine": "unreal"}).status_code == 422
    asset = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero",
                                             "description": "a knight"}).json()
    assert client.delete(f"/api/projects/{project['id']}").status_code == 409
    client.delete(f"/api/assets/{asset['id']}")
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert client.get(f"/api/projects/{project['id']}").status_code == 404


def test_profile_crud_and_reference_images(client, tmp_path):
    project = make_project(client, "godot", tmp_path)
    created = client.post("/api/profiles", json={
        "name": "Pixel", "project_id": project["id"], "base_model": "flux", "camera_preset": "topdown_8",
        "render": {"look": "pixel", "frame_size": {"monster": 64}}, "budgets": {"triangles": {"monster": 2000}},
    })
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["render"]["look"] == "pixel" and profile["render"]["fps"] == 12  # defaults kept
    assert profile["render"]["frame_size"]["monster"] == 64 and profile["render"]["frame_size"]["hero"] == 256
    assert profile["budgets"]["triangles"]["monster"] == 2000 and profile["budgets"]["triangles"]["boss"] == 15000

    patched = client.patch(f"/api/profiles/{profile['id']}", json={"prompt_suffix": "pixel art"}).json()
    assert patched["prompt_suffix"] == "pixel art" and patched["base_model"] == "flux"
    assert client.patch(f"/api/profiles/{profile['id']}", json={"camera_preset": "iso"}).status_code == 422

    pid = profile["id"]
    assert client.post(f"/api/profiles/{pid}/references",
                       files={"file": ("notes.txt", b"x", "text/plain")}).status_code == 422
    for index in range(3):
        response = client.post(f"/api/profiles/{pid}/references",
                               files={"file": (f"ref{index}.png", _png(), "image/png")})
        assert response.status_code == 201, response.text
    refs = response.json()["reference_images"]
    assert len(refs) == 3
    assert client.post(f"/api/profiles/{pid}/references",
                       files={"file": ("ref4.png", _png(), "image/png")}).status_code == 409
    assert client.get(refs[0]["url"]).content == _png()
    remaining = client.delete(f"/api/profiles/{pid}/references/{refs[0]['id']}").json()["reference_images"]
    assert len(remaining) == 2

    assert len(client.get("/api/profiles", params={"project_id": project["id"]}).json()) == 2
    assert client.delete(f"/api/profiles/{project['default_profile_id']}").status_code == 409
    assert client.delete(f"/api/profiles/{pid}").status_code == 204


def test_loras_register_upload_and_base_model_match(client, tmp_path):
    registered = client.post("/api/loras", json={
        "name": "Painterly", "path": str(tmp_path / "painterly.safetensors"), "base_model": "sdxl",
        "tags": ["style"], "licence": "CC-BY-4.0", "commercial_use": True,
    })
    assert registered.status_code == 201, registered.text
    lora = registered.json()
    assert lora["file_exists"] is False and lora["commercial_use"] is True

    uploaded = client.post("/api/loras/upload", data={"base_model": "flux", "tags": "monsters, dark",
                                                      "licence": "non-commercial", "commercial_use": "false"},
                           files={"file": ("Dark Monsters.safetensors", b"weights", "application/octet-stream")})
    assert uploaded.status_code == 201, uploaded.text
    flux = uploaded.json()
    assert flux["file_exists"] is True and flux["tags"] == ["monsters", "dark"] and flux["name"] == "Dark Monsters"
    assert client.post("/api/loras/upload", data={"base_model": "sd15"},
                       files={"file": ("x.safetensors", b"w")}).status_code == 422

    assert [l["id"] for l in client.get("/api/loras", params={"base_model": "flux"}).json()] == [flux["id"]]

    # profiles only take LoRAs tagged with their base model
    ok = client.post("/api/profiles", json={"name": "SDXL", "base_model": "sdxl", "loras": [{"id": lora["id"]}]})
    assert ok.status_code == 201
    bad = client.post("/api/profiles", json={"name": "Mixed", "base_model": "sdxl", "loras": [{"id": flux["id"]}]})
    assert bad.status_code == 422
    assert client.patch(f"/api/loras/{lora['id']}", json={"base_model": "flux"}).status_code == 409
    assert client.patch(f"/api/loras/{lora['id']}", json={"licence": "MIT"}).json()["licence"] == "MIT"

    path = flux["path"]
    assert client.delete(f"/api/loras/{flux['id']}").status_code == 204
    from pathlib import Path
    assert not Path(path).exists()
    assert client.delete(f"/api/loras/{lora['id']}").status_code == 204
    assert client.get(f"/api/profiles/{ok.json()['id']}").json()["loras"] == []


def test_docs_lists_every_endpoint(client):
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    expected = ["/api/meta", "/api/archetypes", "/api/projects", "/api/projects/{project_id}", "/api/profiles",
                "/api/profiles/{profile_id}", "/api/profiles/{profile_id}/references", "/api/loras",
                "/api/loras/upload", "/api/loras/{lora_id}", "/api/assets", "/api/assets/{asset_id}",
                "/api/assets/{asset_id}/gates/{gate}/approve", "/api/assets/{asset_id}/gates/{gate}/reject",
                "/api/assets/{asset_id}/retry", "/api/assets/{asset_id}/export",
                "/api/assets/{asset_id}/training-candidate", "/api/assets/{asset_id}/download", "/api/ws"]
    for path in expected:
        assert path in paths, path
