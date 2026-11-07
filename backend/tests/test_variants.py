"""Colour variants of a rigged asset."""

from __future__ import annotations

import numpy as np
from PIL import Image

from assetgen.variants import recolour
from conftest import make_project, wait_for


def test_recolour_turns_hue_and_tints():
    red = Image.new("RGB", (4, 4), (200, 30, 30))
    turned = np.asarray(recolour(red, hue=120))[0, 0]
    assert turned[1] > turned[0] and turned[1] > turned[2]  # red turned green
    dark = np.asarray(recolour(red, brightness=0.5))[0, 0]
    assert dark[0] < 120
    tinted = np.asarray(recolour(Image.new("RGB", (2, 2), (128, 128, 128)), tint="#0000ff", tint_strength=1.0))[0, 0]
    assert tinted[2] > 200 and tinted[0] < 20
    clear = recolour(Image.new("RGBA", (2, 2), (200, 30, 30, 0)), hue=90)
    assert clear.getpixel((0, 0))[3] == 0


def test_a_variant_is_a_recoloured_copy_at_gate_3(client, settings, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    asset = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero", "archetype": "biped",
                                              "description": "a red knight"}).json()
    wait_for(client, asset["id"], "waiting", gate=1)
    client.post(f"/api/assets/{asset['id']}/gates/1/approve", json={"candidate": 1})
    wait_for(client, asset["id"], "waiting", gate=2)
    assert client.post(f"/api/assets/{asset['id']}/variants", json={"hue": 90}).status_code == 422  # not rigged
    client.post(f"/api/assets/{asset['id']}/gates/2/approve", json={})
    source = wait_for(client, asset["id"], "waiting", gate=3)

    response = client.post(f"/api/assets/{asset['id']}/variants", json={"name": "Green knight", "hue": 120})
    assert response.status_code == 201, response.text
    variant = response.json()
    assert variant["status"] == "waiting" and variant["gate"] == 3 and variant["name"] == "Green knight"
    assert variant["variant_of"]["id"] == asset["id"] and variant["variant_of"]["hue"] == 120
    root = settings.assets_dir
    old = Image.open(root / asset["id"] / source["outputs"]["prompt"]["candidates"][0]).convert("RGB")
    new = Image.open(root / variant["id"] / variant["outputs"]["prompt"]["candidates"][0]).convert("RGB")
    assert np.abs(np.asarray(old, dtype=int) - np.asarray(new, dtype=int)).mean() > 5  # recoloured
    assert (root / variant["id"] / variant["outputs"]["rig"]["glb"]).is_file()
    assert client.get(f"/api/assets/{asset['id']}").json()["status"] == "waiting"  # the source is untouched

    client.post(f"/api/assets/{variant['id']}/gates/3/approve", json={})
    assert wait_for(client, variant["id"], "done")["outputs"]["export"]["files"]
