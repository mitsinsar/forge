"""Assets made from the user's own image: no image model, one candidate, the rest of the pipeline as usual."""

from __future__ import annotations

import io
import json

from PIL import Image

from conftest import make_project, wait_for


def png(color=(200, 40, 40, 255), size=(64, 96), fmt="PNG") -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGBA" if fmt != "JPEG" else "RGB", size, color if fmt != "JPEG" else color[:3])
    image.save(buffer, fmt)
    return buffer.getvalue()


def upload(client, project, data: bytes, filename="hero.png", **fields):
    body = {"project_id": project["id"], "kind": "hero", "description": "a red knight", **fields}
    return client.post("/api/assets/from-image", data={"asset": json.dumps(body)},
                       files={"file": (filename, data, "application/octet-stream")})


def no_image_model(client):
    """Makes any call to the image model fail the test."""
    def fail(**kwargs):
        raise AssertionError("the image model must not run for an uploaded image")
    client.app.state.pipeline.adapters.image.render_candidates = fail


def count_llm_calls(client) -> list:
    llm = client.app.state.pipeline.adapters.llm
    calls, original = [], llm.expand

    def expand(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    llm.expand = expand
    return calls


def test_upload_runs_the_whole_pipeline(client, tmp_path):
    project = make_project(client, "unity", tmp_path / "game")
    no_image_model(client)
    calls = count_llm_calls(client)
    data = png(fmt="JPEG")
    response = upload(client, project, data, filename="knight.jpg", archetype="biped")
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset["source"] == "upload" and asset["name"] == "Red Knight"

    asset = wait_for(client, asset["id"], "waiting", gate=1)
    prompt = asset["outputs"]["prompt"]
    assert prompt["source"] == "upload" and prompt["candidates"] == ["prompt/candidate_1.png"]
    assert asset["archetype"] == "biped"
    assert calls == []  # the archetype was chosen, so the text model was not asked
    candidate = client.get(asset["urls"]["candidates"][0].replace("http://testserver", ""))
    with Image.open(io.BytesIO(candidate.content)) as image:
        assert image.format == "PNG" and image.size == (64, 96)

    assert client.post(f"/api/assets/{asset['id']}/gates/1/approve", json={"candidate": 2}).status_code == 422
    client.post(f"/api/assets/{asset['id']}/gates/1/approve", json={"candidate": 1}).raise_for_status()
    asset = wait_for(client, asset["id"], "waiting", gate=2)
    assert (client.app.state.pipeline.stage_dir(asset["id"], "mesh") / "input.png").read_bytes() == \
        (client.app.state.pipeline.source_image(asset["id"])).read_bytes()
    client.post(f"/api/assets/{asset['id']}/gates/2/approve").raise_for_status()
    wait_for(client, asset["id"], "waiting", gate=3)
    client.post(f"/api/assets/{asset['id']}/gates/3/approve").raise_for_status()
    asset = wait_for(client, asset["id"], "done")
    assert asset["outputs"]["export"]["files"]


def test_automatic_archetype_asks_the_text_model(client, tmp_path):
    project = make_project(client, "godot", tmp_path / "game")
    no_image_model(client)
    calls = count_llm_calls(client)
    asset = upload(client, project, png(), kind="monster", description="a giant cave spider").json()
    asset = wait_for(client, asset["id"], "waiting", gate=1)
    assert len(calls) == 1 and calls[0]["description"] == "a giant cave spider"
    assert asset["archetype"] in {"biped", "quadruped", "flyer", "serpent", "spider", "blob"}


def test_archetype_change_at_gate_1_keeps_the_image(client, tmp_path):
    project = make_project(client, "unity", tmp_path / "game")
    no_image_model(client)
    asset = upload(client, project, png(), kind="monster", archetype="biped").json()
    wait_for(client, asset["id"], "waiting", gate=1)
    client.post(f"/api/assets/{asset['id']}/gates/1/reject", json={"archetype": "quadruped"}).raise_for_status()
    asset = wait_for(client, asset["id"], "waiting", gate=1)
    assert asset["archetype"] == "quadruped"
    assert asset["outputs"]["prompt"]["candidates"] == ["prompt/candidate_1.png"]


def test_gear_and_weapons_from_an_image(client, tmp_path):
    project = make_project(client, "unity", tmp_path / "game")
    no_image_model(client)
    calls = count_llm_calls(client)
    gear = upload(client, project, png(), kind="gear", slot="head", description="an iron helmet").json()
    weapon = upload(client, project, png(), kind="weapon", weapon_class="bow", description="a yew bow").json()
    gear = wait_for(client, gear["id"], "waiting", gate=1)
    weapon = wait_for(client, weapon["id"], "waiting", gate=1)
    assert (gear["archetype"], gear["slot"]) == ("biped", "head")
    assert (weapon["archetype"], weapon["weapon_class"]) == ("biped", "bow")
    assert weapon["grip"] and calls == []


def test_bad_uploads_are_refused(client, tmp_path):
    project = make_project(client, "unity", tmp_path / "game")
    assert upload(client, project, b"not an image").status_code == 422
    gif = io.BytesIO()
    Image.new("RGB", (8, 8)).save(gif, "GIF")
    assert "PNG, JPEG or WebP" in upload(client, project, gif.getvalue(), filename="a.gif").json()["detail"]
    assert upload(client, project, png(), description="").status_code == 422
    missing = client.post("/api/assets/from-image", data={"asset": json.dumps(
        {"project_id": "nope", "kind": "hero", "description": "x"})}, files={"file": ("a.png", png())})
    assert missing.status_code == 404
    assert client.get("/api/assets").json() == []


def test_delete_removes_the_uploaded_image(client, tmp_path):
    project = make_project(client, "unity", tmp_path / "game")
    asset = upload(client, project, png(), archetype="biped").json()
    wait_for(client, asset["id"], "waiting", gate=1)
    source = client.app.state.pipeline.source_image(asset["id"])
    assert source.is_file()
    assert client.delete(f"/api/assets/{asset['id']}").status_code == 204
    assert not source.exists()
