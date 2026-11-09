"""Skill effects: planned parts, flipbooks with keyed alpha, and a Godot scene that plays them."""

from __future__ import annotations

import numpy as np
from PIL import Image

from assetgen import checks as qc
from assetgen import effects
from conftest import make_project, wait_for


def test_the_plan_is_completed_and_clamped():
    plan = effects.normalize_plan({"kind": "laser", "palette": ["#00ff00", "green"], "parts": [
        {"part": "impact", "picture": "a green splash", "scale": 9, "blend": "glow"},
        {"part": "cast", "picture": "hands glow"}], "particles": {"amount": 999, "speed": "fast"}}, "poison arrow")
    assert plan["kind"] == "projectile" and plan["palette"] == ["#00ff00"]  # unknown kind and colour dropped
    assert [p["part"] for p in plan["parts"]] == ["projectile", "impact", "cast"]  # the projectile was added
    impact = plan["parts"][1]
    assert impact["scale"] == 3.0 and impact["blend"] == "add" and impact["loop"] is False
    assert plan["parts"][0]["loop"] is True and "poison arrow" in plan["parts"][0]["picture"]
    assert plan["particles"]["amount"] == 200 and plan["particles"]["speed"] == 40 and plan["particles"]["enabled"]
    aura = effects.normalize_plan({"kind": "aura", "parts": [{"part": "cast"}]}, "a holy glow")
    assert [p["part"] for p in aura["parts"]] == ["aura"] and not aura["particles"]["enabled"]


def test_black_is_clear_and_light_keeps_its_colour():
    pixels = np.zeros((64, 64, 3), np.uint8)
    pixels[30, 30] = (255, 128, 0)  # bright orange
    pixels[32, 32] = (64, 32, 0)  # the same orange, dimmer
    pixels[0, 10] = (255, 255, 255)  # at the border: feathered away (no hard edge)
    keyed = np.asarray(effects.key_alpha(Image.fromarray(pixels)))
    assert keyed[20, 20, 3] == 0 and keyed[0, 10, 3] == 0  # black: clear
    assert keyed[30, 30, 3] == 255 and tuple(keyed[30, 30, :3]) == (255, 128, 0)
    assert 40 < keyed[32, 32, 3] < 70 and keyed[32, 32, 0] > 240  # dim: see-through, the colour un-premultiplied


def test_a_loop_is_cross_faded_and_a_one_shot_fades_out(tmp_path):
    frames = []
    for n in range(33):
        image = Image.new("RGB", (64, 64))
        image.paste((255, 100, 0), (10 + n, 20, 30 + n, 40))
        frames.append(tmp_path / f"f{n}.png")
        image.save(frames[-1])
    loop = effects.make_flipbook(frames, tmp_path / "loop", loop=True, size=32)
    once = effects.make_flipbook(frames, tmp_path / "once", loop=False, size=32)
    assert len(loop) == len(once) == effects.FLIPBOOK_FRAMES
    alpha = lambda p: np.asarray(Image.open(p).getchannel("A"), float).sum()  # noqa: E731
    assert alpha(once[-1]) < 0.3 * alpha(once[0])
    sheet = effects.pack_sheet(loop, tmp_path / "sheet.png")
    assert (sheet["columns"], sheet["rows"], sheet["size"]) == (4, 4, 32)
    assert Image.open(tmp_path / "sheet.png").size == (128, 128)


def test_pictures_not_on_black_fail(tmp_path):
    grey = tmp_path / "grey.png"
    Image.new("RGB", (64, 64), (120, 120, 120)).save(grey)
    found = {c["id"]: c["status"] for c in effects.picture_checks(grey)}
    assert found["black"] == qc.FAIL and found["subject"] == qc.FAIL


def test_a_poison_arrow_goes_to_a_godot_scene(client, tmp_path):
    project = make_project(client, "godot", tmp_path / "game")
    created = client.post("/api/assets", json={"project_id": project["id"], "kind": "effect",
                                               "description": "a poison arrow", "autopilot": True,
                                               "autopilot_export": True}).json()
    done = wait_for(client, created["id"], "done", timeout=60)
    outputs = done["outputs"]
    assert outputs["prompt"]["plan"]["kind"] == "projectile"
    parts = {p["part"]: p for p in outputs["mesh"]["parts"]}
    assert set(parts) == {"projectile", "impact"} and len(parts["projectile"]["frames"]) == effects.FLIPBOOK_FRAMES
    assert all(c["status"] != qc.FAIL for c in outputs["mesh"]["checks"])
    assert outputs["rig"]["sequence"]["order"] == ["projectile", "impact"]
    files = outputs["export"]["files"]
    name = outputs["export"]["name"]
    assert {f"{name}.tscn", f"{name}.gd", f"{name}_frames.tres", f"{name}_projectile.png", f"{name}_impact.png",
            f"{name}_dot.png", f"{name}_icon.png", "asset.json"} <= set(files)
    installed = tmp_path / "game" / "generated" / "effect" / name
    scene = (installed / f"{name}.tscn").read_text(encoding="utf-8")
    assert 'type="GPUParticles2D"' in scene and 'node name="projectile" type="AnimatedSprite2D"' in scene
    assert "func play(from: Vector2, to: Vector2)" in (installed / f"{name}.gd").read_text(encoding="utf-8")
    tres = (installed / f"{name}_frames.tres").read_text(encoding="utf-8")
    assert '"name": &"projectile"' in tres and '"loop": true' in tres and '"loop": false' in tres


def test_two_arrows_fail_and_the_judges_notes_redraw(client, tmp_path):
    llm = client.app.state.pipeline.adapters.llm
    llm.effect_judgements = [{"count": 2, "match": 3, "problems": ["two crossed arrows"]}] * 4
    project = make_project(client, "godot", tmp_path / "game")
    created = client.post("/api/assets", json={"project_id": project["id"], "kind": "effect",
                                               "description": "a poison arrow", "autopilot": True}).json()
    ready = wait_for(client, created["id"], "waiting", gate=3, timeout=60)
    log = [(e["gate"], e["action"]) for e in ready["autopilot_log"]]
    assert log[0] == (1, "redrew") and "two crossed arrows" in ready["notes"][0]["note"]
    llm.effect_judgements = []


def test_effects_refuse_character_fields(client, tmp_path):
    project = make_project(client, "godot", tmp_path / "game")
    answer = client.post("/api/assets", json={"project_id": project["id"], "kind": "effect", "description": "x",
                                              "height": 2.0})
    assert answer.status_code == 422


def test_a_diagonal_projectile_is_turned_level(tmp_path):
    from PIL import ImageDraw
    path = tmp_path / "arrow.png"
    image = Image.new("RGB", (256, 256))
    ImageDraw.Draw(image).line((60, 200, 200, 60), fill=(80, 255, 60), width=12)  # up to the right, 45 degrees
    image.save(path)
    assert abs(abs(effects.level(path)) - 45) < 3
    grey = np.asarray(Image.open(path).convert("L")) > 40
    ys, xs = np.nonzero(grey)
    assert xs.max() - xs.min() > 4 * (ys.max() - ys.min())  # now long across, thin up and down
    round_path = tmp_path / "ball.png"
    ball = Image.new("RGB", (128, 128))
    ImageDraw.Draw(ball).ellipse((40, 40, 88, 88), fill=(255, 200, 0))
    ball.save(round_path)
    assert effects.level(round_path) == 0.0
