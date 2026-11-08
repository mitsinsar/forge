"""End-to-end runs with the test doubles, from creation through gates 1-3 to done."""

from __future__ import annotations

import io
import json
import zipfile

from conftest import make_project, wait_for


def run_to_done(client, asset_id: str, candidate: int = 2) -> dict:
    asset = wait_for(client, asset_id, "waiting", gate=1)
    assert len(asset["urls"]["candidates"]) == 4
    assert client.post(f"/api/assets/{asset_id}/gates/1/approve", json={"candidate": candidate}).status_code == 200
    asset = wait_for(client, asset_id, "waiting", gate=2)
    assert asset["urls"]["mesh"]
    assert client.post(f"/api/assets/{asset_id}/gates/2/approve").status_code == 200
    asset = wait_for(client, asset_id, "waiting", gate=3)
    assert asset["urls"]["animated"]
    assert client.post(f"/api/assets/{asset_id}/gates/3/approve").status_code == 200
    return wait_for(client, asset_id, "done")


def test_unity_hero_end_to_end(client, tmp_path):
    game = tmp_path / "UnityGame"
    project = make_project(client, "unity", game)
    created = client.post("/api/assets", json={
        "project_id": project["id"], "kind": "hero", "name": "Ember Knight",
        "description": "a knight in ember-red plate armour with a horned helmet",
    })
    assert created.status_code == 201, created.text
    asset_id = created.json()["id"]

    asset = run_to_done(client, asset_id)
    assert asset["archetype"] == "biped"
    assert asset["chosen_candidate"] == 2
    assert asset["training_candidate"] is True

    # LOD0/1/2 at 100%, 50% and 20% of the hero budget of 8,000 triangles
    lods = asset["outputs"]["mesh"]["lods"]
    assert [lod["level"] for lod in lods] == [0, 1, 2]
    assert 7000 <= lods[0]["triangles"] <= 8000
    assert abs(lods[1]["triangles"] / lods[0]["triangles"] - 0.5) < 0.1
    assert abs(lods[2]["triangles"] / lods[0]["triangles"] - 0.2) < 0.1

    clips = [c["name"] for c in asset["outputs"]["rig"]["clips"]]
    # A biped hero is a paper-doll body: it also plays every weapon class's attacks.
    from assetgen.defaults import WEAPON_ATTACKS
    attacks = list(dict.fromkeys(c for clips in WEAPON_ATTACKS.values() for c in clips))
    assert clips == ["idle", "walk", "run", "attack_1", "attack_2", "hit", "death", "cast", "skill",
                     *[c for c in attacks if c not in ("attack_1", "attack_2")]]
    assert asset["outputs"]["rig"]["rig_type"] == "Humanoid"

    folder = game / "Assets" / "Generated" / "hero" / "Ember_Knight"
    for name in ("Ember_Knight.fbx", "asset.json",
                 "Textures/Ember_Knight_Albedo.png", "Textures/Ember_Knight_Albedo.png.meta"):
        assert (folder / name).is_file(), name
    sidecar = json.loads((folder / "asset.json").read_text())
    assert sidecar["rig"] == "Humanoid" and len(sidecar["lods"]) == 3 and len(sidecar["clips"]) == len(clips)
    assert sidecar["prefab"] == "Ember_Knight.prefab"
    assert sidecar["material"]["file"] == "Materials/Ember_Knight.mat"  # made by the import script
    # the editor import script came with the export
    for name in ("Editor/AssetForgeImporter.cs", "Editor/AssetForge.Editor.asmdef",
                 "Runtime/AssetForgeGear.cs", "Runtime/AssetForge.Runtime.asmdef"):
        assert (game / "Assets" / "Generated" / name).is_file(), name
    asmdef = json.loads((game / "Assets" / "Generated" / "Editor" / "AssetForge.Editor.asmdef").read_text())
    assert asmdef["includePlatforms"] == ["Editor"]
    assert asset["outputs"]["export"]["importer"]["written"]

    download = client.get(f"/api/assets/{asset_id}/download")
    assert download.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(download.content)).namelist()
    assert "Ember_Knight/Ember_Knight.fbx" in names and "Ember_Knight/asset.json" in names

    # every URL the frontend gets is served
    for url in [*asset["urls"]["candidates"], asset["urls"]["mesh"], asset["urls"]["animated"]]:
        response = client.get(url)
        assert response.status_code == 200, url
    assert client.get(asset["urls"]["mesh"]).content[:4] == b"glTF"


def test_godot_monster_end_to_end(client, tmp_path):
    game = tmp_path / "godot_game"
    project = make_project(client, "godot", game)
    created = client.post("/api/assets", json={
        "project_id": project["id"], "kind": "monster", "name": "Moss Wolf",
        "description": "a mossy forest wolf with glowing green eyes",
    })
    asset_id = created.json()["id"]
    asset = run_to_done(client, asset_id, candidate=1)
    assert asset["archetype"] == "quadruped"
    assert asset["outputs"]["rig"]["rig_type"] == "Generic"
    assert 3000 <= asset["outputs"]["mesh"]["lods"][0]["triangles"] <= 4000

    folder = game / "generated" / "monster" / "Moss_Wolf"
    for name in ("body_right.png", "body_right.json", "body.tres", "PaperDoll.tscn", "asset.json"):
        assert (folder / name).is_file(), name
    tres = (folder / "body.tres").read_text()
    assert tres.startswith('[gd_resource type="SpriteFrames"')
    assert 'path="res://generated/monster/Moss_Wolf/body_right.png"' in tres
    for clip in ("idle", "walk", "run", "attack_1", "attack_2", "hit", "death"):
        assert f'"name": &"{clip}_right"' in tres
    assert '"speed": 12.0' in tres
    scene = (folder / "PaperDoll.tscn").read_text()
    assert 'type="AnimatedSprite2D"' in scene and "body.tres" in scene
    sidecar = json.loads((folder / "asset.json").read_text())
    assert sidecar["frame_size"] == 128 and sidecar["mirrored"] == {"left": "right"}
    aseprite = json.loads((folder / "body_right.json").read_text())
    assert {t["name"] for t in aseprite["meta"]["frameTags"]} >= {"idle", "death"}

    from PIL import Image
    with Image.open(folder / "body_right.png") as atlas:
        assert atlas.width % 128 == 0 and atlas.height % 128 == 0

    names = zipfile.ZipFile(io.BytesIO(client.get(f"/api/assets/{asset_id}/download").content)).namelist()
    assert "Moss_Wolf/body_right.png" in names and "Moss_Wolf/body_right.json" in names


def test_gear_and_weapon(client, tmp_path):
    project = make_project(client, "godot", tmp_path / "idle")
    helmet = client.post("/api/assets", json={"project_id": project["id"], "kind": "gear",
                                              "description": "a dented iron helmet"}).json()
    sword = client.post("/api/assets", json={"project_id": project["id"], "kind": "weapon",
                                             "description": "a heavy greatsword"}).json()
    helmet = run_to_done(client, helmet["id"])
    assert helmet["slot"] == "head" and helmet["outputs"]["rig"]["preview_clips"] == ["walk", "attack_1"]
    assert helmet["outputs"]["export"]["layer"] == "head"
    sword = run_to_done(client, sword["id"])
    assert sword["weapon_class"] == "two_handed"
    assert sword["grip"]["socket"] == "weapon_r"
    assert "attack_cleave" in [c["name"] for c in sword["outputs"]["rig"]["clips"]]


def test_boots_are_drawn_one_at_a_time_and_exported_with_a_pair_icon(client, tmp_path):
    project = make_project(client, "godot", tmp_path / "boots")
    boots = client.post("/api/assets", json={"project_id": project["id"], "kind": "gear", "slot": "feet",
                                             "name": "Iron Boots", "description": "iron boots"}).json()
    boots = run_to_done(client, boots["id"])
    prompt = boots["outputs"]["prompt"]
    assert prompt["prompt"].startswith("one single right boot")
    assert "two boots" in prompt["negative_prompt"]
    icon = boots["outputs"]["export"]["icon"]
    assert icon == "Iron_Boots_icon.png" and icon in boots["outputs"]["export"]["files"]
    from PIL import Image
    with Image.open(tmp_path / "boots" / "generated" / "gear" / "Iron_Boots" / icon) as image:
        assert image.mode == "RGBA" and image.size == (512, 512)


def test_a_finished_hero_can_be_rigged_again(client, tmp_path):
    project = make_project(client, "godot", tmp_path / "again")
    hero = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero",
                                            "description": "a knight"}).json()
    hero = run_to_done(client, hero["id"])
    assert hero["outputs"].get("export")
    again = client.post(f"/api/assets/{hero['id']}/redo", json={"stage": "rig"})
    assert again.status_code == 200 and again.json()["status"] == "queued"
    hero = wait_for(client, hero["id"], "waiting", gate=3)
    assert hero["outputs"].get("rig") and not hero["outputs"].get("export")
    assert hero["attempts"]["rig"] == 2


def test_unity_weapon_writes_prefab(client, tmp_path):
    game = tmp_path / "unity"
    project = make_project(client, "unity", game)
    bow = client.post("/api/assets", json={"project_id": project["id"], "kind": "weapon", "name": "Yew Bow",
                                           "description": "a yew longbow"}).json()
    run_to_done(client, bow["id"])
    folder = game / "Assets" / "Generated" / "weapon" / "Yew_Bow"
    sidecar = json.loads((folder / "asset.json").read_text())
    assert sidecar["weapon"]["class"] == "bow" and sidecar["weapon"]["socket"] == "weapon_l"
    assert sidecar["weapon"]["prefab"] == "Yew_Bow.prefab"  # made in Unity by the import script
    assert sidecar["weapon"]["grip"]["position"] == [0.0, 0.0, 0.0]


def test_unity_gear_lists_the_biped_bones(client, tmp_path):
    game = tmp_path / "unity"
    project = make_project(client, "unity", game)
    helmet = client.post("/api/assets", json={"project_id": project["id"], "kind": "gear", "name": "Iron Helm",
                                              "description": "a dented iron helmet"}).json()
    run_to_done(client, helmet["id"])
    sidecar = json.loads((game / "Assets" / "Generated" / "gear" / "Iron_Helm" / "asset.json").read_text())
    assert sidecar["gear"]["slot"] == "head" and sidecar["gear"]["skinned"] is True
    assert "pelvis" in sidecar["gear"]["bones"] and sidecar["gear"]["hide_body"] == ["head_top"]


def test_reexport_keeps_what_unity_made(client, tmp_path):
    game = tmp_path / "unity"
    project = make_project(client, "unity", game)
    asset = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero", "name": "Ash Mage",
                                             "description": "a mage in grey robes"}).json()
    asset = run_to_done(client, asset["id"])
    folder = game / "Assets" / "Generated" / "hero" / "Ash_Mage"
    # what Unity and the import script add after the first import
    unity_made = {"Ash_Mage.fbx.meta": "guid: fbx", "asset.json.meta": "guid: json", "Ash_Mage.prefab": "prefab",
                  "Ash_Mage.prefab.meta": "guid: prefab", "Ash_Mage.controller": "controller",
                  "Materials.meta": "guid: folder", "Materials/Ash_Mage.mat": "material",
                  "Materials/Ash_Mage.mat.meta": "guid: material"}
    for rel, text in unity_made.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text)
    (folder / "stray.txt").write_text("left over")
    (folder / "stray.txt.meta").write_text("guid: stray")
    (folder / "Ash_Mage.fbx").write_text("old fbx")
    assert client.post(f"/api/assets/{asset['id']}/export").status_code == 200
    wait_for(client, asset["id"], "done")
    for rel, text in unity_made.items():
        assert (folder / rel).read_text() == text, rel
    assert not (folder / "stray.txt").exists() and not (folder / "stray.txt.meta").exists()
    assert (folder / "Ash_Mage.fbx").read_text() != "old fbx"


def test_importer_script_is_replaced_only_by_a_newer_version(tmp_path):
    from assetgen import exporters

    bundled = exporters.script_version(exporters.UNITY_SCRIPTS_DIR / "Editor" / "AssetForgeImporter.cs")
    assert bundled and bundled >= 1
    first = exporters.install_unity_importer(tmp_path)
    assert sorted(first["written"]) == ["Assets/Generated/Editor/AssetForge.Editor.asmdef",
                                        "Assets/Generated/Editor/AssetForgeImporter.cs",
                                        "Assets/Generated/Runtime/AssetForge.Runtime.asmdef",
                                        "Assets/Generated/Runtime/AssetForgeGear.cs"]
    assert exporters.install_unity_importer(tmp_path)["written"] == []
    script = tmp_path / "Assets" / "Generated" / "Editor" / "AssetForgeImporter.cs"
    script.write_text("// assetgen-importer-version: 0\n// older\n")
    assert "Assets/Generated/Editor/AssetForgeImporter.cs" in exporters.install_unity_importer(tmp_path)["written"]
    assert exporters.script_version(script) == bundled
    script.write_text(f"// assetgen-importer-version: {bundled + 1}\n// newer, from a later assetgen\n")
    assert exporters.install_unity_importer(tmp_path)["written"] == []
    assert "newer" in script.read_text()


def test_reexport_overwrites(client, tmp_path):
    game = tmp_path / "unity"
    project = make_project(client, "unity", game)
    asset = client.post("/api/assets", json={"project_id": project["id"], "kind": "boss", "name": "Old King",
                                             "description": "a giant undead king"}).json()
    asset = run_to_done(client, asset["id"])
    folder = game / "Assets" / "Generated" / "boss" / "Old_King"
    (folder / "stray.txt").write_text("left over")
    mesh_before = asset["outputs"]["mesh"]
    assert client.post(f"/api/assets/{asset['id']}/export").status_code == 200
    asset = wait_for(client, asset["id"], "done")
    assert not (folder / "stray.txt").exists() and (folder / "Old_King.fbx").is_file()
    assert asset["outputs"]["mesh"] == mesh_before  # nothing upstream reran
    assert 13000 <= mesh_before["lods"][0]["triangles"] <= 15000


def test_same_name_assets_get_their_own_export_folders(client, tmp_path):
    game = tmp_path / "godot_game"
    project = make_project(client, "godot", game)
    first, second = (client.post("/api/assets", json={
        "project_id": project["id"], "kind": "monster", "name": "Grey Wolf", "description": "a grey wolf",
    }).json() for _ in range(2))
    first = run_to_done(client, first["id"])
    second = run_to_done(client, second["id"])

    parent = game / "generated" / "monster"
    first_folder = parent / first["export_name"]
    second_folder = parent / second["export_name"]
    assert first["export_name"] == "Grey_Wolf"
    assert second["export_name"] == f"Grey_Wolf_{second['id'][:6]}"
    for asset, folder in ((first, first_folder), (second, second_folder)):
        assert (folder / "body.tres").is_file() and (folder / "PaperDoll.tscn").is_file()
        assert json.loads((folder / "asset.json").read_text())["id"] == asset["id"]
        assert asset["outputs"]["export"]["installed_to"] == str(folder)
    # the .tres points at its own folder, not the other wolf's
    assert f"res://generated/monster/{second['export_name']}/body_right.png" in (second_folder / "body.tres").read_text()

    # re-exporting either one only replaces its own folder
    second_files = sorted(p.relative_to(second_folder) for p in second_folder.rglob("*"))
    assert client.post(f"/api/assets/{first['id']}/export").status_code == 200
    again = wait_for(client, first["id"], "done")
    assert again["export_name"] == "Grey_Wolf"
    assert sorted(p.relative_to(second_folder) for p in second_folder.rglob("*")) == second_files
    assert json.loads((first_folder / "asset.json").read_text())["id"] == first["id"]


def test_export_never_overwrites_a_folder_it_did_not_write(client, tmp_path):
    game = tmp_path / "unity_game"
    project = make_project(client, "unity", game)
    own = game / "Assets" / "Generated" / "hero" / "Ember_Knight"
    own.mkdir(parents=True)
    (own / "handmade.txt").write_text("keep me")
    asset = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero", "name": "Ember Knight",
                                             "description": "a knight"}).json()
    asset = run_to_done(client, asset["id"])
    assert (own / "handmade.txt").read_text() == "keep me"
    assert asset["export_name"] == f"Ember_Knight_{asset['id'][:6]}"
    assert (own.parent / asset["export_name"] / f"{asset['export_name']}.fbx").is_file()


def test_install_refuses_another_assets_folder(tmp_path):
    import pytest
    from assetgen.exporters import install

    dest = tmp_path / "Grey_Wolf"
    dest.mkdir()
    (dest / "asset.json").write_text(json.dumps({"id": "other"}))
    src = tmp_path / "build"
    src.mkdir()
    (src / "asset.json").write_text(json.dumps({"id": "mine"}))
    with pytest.raises(RuntimeError):
        install(src, dest, "mine")
    assert json.loads((dest / "asset.json").read_text())["id"] == "other"


def test_weapon_triangle_limit_default_and_per_asset_override(client, tmp_path):
    project = make_project(client, "unity", tmp_path / "Game")
    base = {"project_id": project["id"], "kind": "weapon", "description": "a rusty iron sword"}

    default = client.post("/api/assets", json=base).json()
    custom = client.post("/api/assets", json={**base, "overrides": {"budgets": {"triangles": {"weapon": 400}}}}).json()

    for asset_id, limit in ((default["id"], 800), (custom["id"], 400)):
        wait_for(client, asset_id, "waiting", gate=1)
        client.post(f"/api/assets/{asset_id}/gates/1/approve", json={"candidate": 1})
        asset = wait_for(client, asset_id, "waiting", gate=2)
        assert asset["outputs"]["mesh"]["triangle_budget"] == limit
        assert asset["outputs"]["mesh"]["lods"][0]["triangles"] <= limit
