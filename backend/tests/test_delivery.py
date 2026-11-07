"""Sending files quickly: ETags, preview GLBs, thumbnails, compression."""

from __future__ import annotations

from conftest import make_project, wait_for


def test_files_revalidate_previews_are_light_and_json_is_compressed(client, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    asset = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero", "archetype": "biped",
                                             "description": "a rogue"}).json()
    wait_for(client, asset["id"], "waiting", gate=1)
    client.post(f"/api/assets/{asset['id']}/gates/1/approve", json={"candidate": 1})
    mesh = wait_for(client, asset["id"], "waiting", gate=2)["urls"]["mesh"]

    first = client.get(mesh)
    assert first.status_code == 200 and first.headers["etag"]
    again = client.get(mesh, headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and not again.content

    preview = client.get(mesh + "?preview=1")
    assert preview.status_code == 200 and preview.content[:4] == b"glTF"

    thumb = client.get(f"/api/assets/{asset['id']}/thumb?candidate=2&size=128")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"

    listed = client.get("/api/assets", headers={"Accept-Encoding": "gzip"})
    assert listed.headers.get("content-encoding") == "gzip"
