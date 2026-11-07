"""Quality checks at the gates, and gate 1 redrawing candidates that fail them."""

from __future__ import annotations

from PIL import Image, ImageDraw

from assetgen import checks as qc
from conftest import make_project, wait_for
from test_pose import figure


def test_one_whole_empty_handed_figure_passes(tmp_path):
    path = figure(tmp_path / "a.png", arms_out=True)
    found = qc.candidate_checks(path, kind="hero", posed_biped=True,
                                vision={"held_item": False, "figures": 1, "whole_body": True})
    assert qc.worst(found) == qc.PASS, found
    assert {c["id"] for c in found} == {"figures", "whole", "arms", "props"}


def test_props_two_figures_and_cut_off_bodies_fail(tmp_path):
    held = qc.candidate_checks(figure(tmp_path / "a.png", arms_out=True), kind="hero", posed_biped=True,
                               vision={"held_item": True, "held_item_name": "a shield", "figures": 1, "whole_body": True})
    assert next(c for c in held if c["id"] == "props") == {"id": "props", "label": "Empty hands", "status": "fail",
                                                           "detail": "Holds a shield"}
    two = qc.candidate_checks(figure(tmp_path / "b.png", arms_out=True, extra_figure=True), kind="hero",
                              posed_biped=False, vision=None)
    assert next(c for c in two if c["id"] == "figures")["status"] == qc.WARN  # silhouette only: warns
    seen = qc.candidate_checks(figure(tmp_path / "b.png", arms_out=True, extra_figure=True), kind="hero",
                               posed_biped=False, vision={"held_item": False, "figures": 2, "whole_body": True})
    assert next(c for c in seen if c["id"] == "figures")["status"] == qc.FAIL
    assert next(c for c in two if c["id"] == "props")["status"] == qc.WARN  # no vision model: not checked

    cut = tmp_path / "c.png"
    image = Image.new("RGB", (200, 200), (200, 200, 200))
    ImageDraw.Draw(image).rectangle((95, 120, 104, 199), fill=(150, 20, 20))  # a leg runs off the bottom
    image.save(cut)
    assert next(c for c in qc.candidate_checks(cut, kind="weapon", posed_biped=False, vision=None)
                if c["id"] == "whole")["status"] == qc.WARN  # the silhouette alone only warns
    told = qc.candidate_checks(cut, kind="weapon", posed_biped=False,
                               vision={"held_item": False, "figures": 1, "whole_body": False})
    assert next(c for c in told if c["id"] == "whole")["status"] == qc.FAIL


def test_mesh_and_rig_checks():
    assert qc.worst(qc.mesh_checks({"pieces": 1, "largest_piece_share": 1.0, "open_edges": 0, "edges": 100})) == qc.PASS
    assert qc.worst(qc.mesh_checks({"pieces": 9, "largest_piece_share": 0.6, "open_edges": 0, "edges": 100})) == qc.FAIL
    good = qc.rig_checks(skeleton={"unirig_file": "x", "fit_score": 0.91}, stretch={"walk": 0.03, "run": 0.05})
    assert qc.worst(good) == qc.PASS
    bad = qc.rig_checks(skeleton={"unirig_file": "x", "fit_score": 0.5, "attempts": [{}, {}, {}]},
                        stretch={"walk": 0.2, "attack_slash": 0.9})
    fit, stretch = bad
    assert fit["status"] == qc.FAIL and "best of 3" in fit["detail"]
    assert stretch["status"] == qc.FAIL and stretch["detail"].startswith("Most stretched: walk")  # standard clips


def test_gate_1_redraws_candidates_that_fail(client, settings, tmp_path):
    client.app.state.pipeline.settings.auto_retries = 2
    llm = client.app.state.pipeline.adapters.llm
    llm.inspections = [{"held_item": True, "held_item_name": "a sword", "figures": 1, "whole_body": True}]
    project = make_project(client, "unity", tmp_path / "g")
    response = client.post("/api/assets", json={"project_id": project["id"], "kind": "monster",
                                                "archetype": "quadruped", "description": "a grey wolf"})
    asset = wait_for(client, response.json()["id"], "waiting", gate=1)
    prompt = asset["outputs"]["prompt"]
    assert prompt["redrawn"] >= 1 and any("Redrew" in line for line in prompt["log"])
    assert [c["candidate"] for c in prompt["candidate_checks"]] == list(range(1, len(prompt["candidates"]) + 1))
    llm.inspections = []


def _clean(monkeypatch):
    """Every candidate passes gate 1's checks (the placeholders would not)."""
    monkeypatch.setattr(qc, "candidate_checks", lambda path, **kw: [qc.check("figures", "One subject", qc.PASS, "One")])


def test_autopilot_takes_an_asset_to_the_export_when_checks_pass(client, tmp_path, monkeypatch):
    _clean(monkeypatch)
    project = make_project(client, "unity", tmp_path / "g")
    created = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero", "archetype": "biped",
                                               "description": "a hooded rogue", "autopilot": True}).json()
    done = wait_for(client, created["id"], "done", timeout=60)
    assert done["chosen_candidate"] == 1
    assert [(e["gate"], e["action"]) for e in done["autopilot_log"]] == [(1, "approved"), (2, "approved"), (3, "approved")]


def test_autopilot_stops_where_a_check_fails_and_resumes_when_turned_on(client, tmp_path, monkeypatch):
    project = make_project(client, "unity", tmp_path / "g")
    monkeypatch.setattr(qc, "candidate_checks", lambda path, **kw: [qc.check("props", "Empty hands", qc.FAIL, "Holds a shield")])
    created = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero", "archetype": "biped",
                                               "description": "a knight", "autopilot": True}).json()
    stopped = wait_for(client, created["id"], "waiting", gate=1)
    assert stopped["autopilot_log"][-1]["action"] == "stopped"
    assert "every candidate failed" in stopped["step"]

    off = client.put(f"/api/assets/{created['id']}/autopilot", json={"value": False}).json()
    assert off["autopilot"] is False and off["status"] == "waiting"
    _clean(monkeypatch)
    client.post(f"/api/assets/{created['id']}/gates/1/reject", json={"note": "no shield"})
    again = wait_for(client, created["id"], "waiting", gate=1)  # autopilot is off: it waits
    on = client.put(f"/api/assets/{again['id']}/autopilot", json={"value": True}).json()
    assert on["status"] == "queued" and on["chosen_candidate"] == 1  # approved at once


def test_a_batch_makes_varied_assets(client, tmp_path):
    project = make_project(client, "unity", tmp_path / "g")
    response = client.post("/api/assets/batch", json={"count": 3, "vary": True, "asset": {
        "project_id": project["id"], "kind": "monster", "description": "a goblin", "name": "Goblin"}})
    assert response.status_code == 201, response.text
    batch = response.json()
    assert [a["name"] for a in batch] == ["Goblin 1", "Goblin 2", "Goblin 3"]
    assert [a["description"] for a in batch] == [f"a goblin, variant {n}" for n in (1, 2, 3)]
    assert len({a["batch_id"] for a in batch}) == 1
    listed = client.get(f"/api/assets?batch_id={batch[0]['batch_id']}").json()
    assert sorted(a["id"] for a in listed) == sorted(a["id"] for a in batch)


def test_a_stage_whose_tool_died_restarts_it_and_runs_again(client, tmp_path, monkeypatch):
    from assetgen import watchdog
    pipeline = client.app.state.pipeline
    pipeline.settings.restart = {"mesh": {"command": ["start-hunyuan"], "port": 8081}}
    restarts = []
    monkeypatch.setattr(watchdog, "restart_tool", lambda settings, tool: restarts.append(tool) or "restarted")
    mesh = pipeline.adapters.mesh
    original = mesh.image_to_mesh
    calls = []

    def flaky(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Could not reach Hunyuan3D at http://127.0.0.1:8081 (connection refused)")
        return original(**kwargs)

    monkeypatch.setattr(mesh, "image_to_mesh", flaky)
    project = make_project(client, "unity", tmp_path / "g")
    asset = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero", "archetype": "biped",
                                             "description": "a rogue"}).json()
    wait_for(client, asset["id"], "waiting", gate=1)
    client.post(f"/api/assets/{asset['id']}/gates/1/approve", json={"candidate": 1})
    done = wait_for(client, asset["id"], "waiting", gate=2)
    assert restarts == ["mesh"] and len(calls) == 2 and done["auto_restarts"] == {"mesh": 1}


def test_storage_report_and_clean(client, settings, tmp_path):
    orphan = settings.assets_dir / "gone123"
    orphan.mkdir(parents=True)
    (orphan / "big.bin").write_bytes(b"x" * 5000)
    report = client.get("/api/storage").json()
    orphans = next(c for c in report["categories"] if c["id"] == "orphans")
    assert orphans["items"] == 1 and orphans["bytes"] == 5000
    cleaned = client.post("/api/storage/clean", json={"categories": ["orphans"]}).json()
    assert cleaned["freed_bytes"] == 5000 and not orphan.exists()
    assert client.post("/api/tools/mesh/restart").status_code == 422  # no restart command set


def test_follows_the_animation():
    def clip(name, bone, degrees):
        return {"name": name, "follow": {"bone": bone, "degrees": degrees}}

    good = qc.rig_checks(skeleton=None, stretch=None, clips=[clip("walk", "calf_l", 6.0), clip("idle", "hand_r", 2.0)])
    assert good[0]["id"] == "follow" and good[0]["status"] == qc.PASS
    bad = qc.rig_checks(skeleton=None, stretch=None, clips=[clip("walk", "calf_r", 24.0), clip("run", "foot_l", 13.0)])
    assert bad[0]["status"] == qc.FAIL and bad[0]["detail"].startswith("Legs lean sideways off the clip: walk calf r 24°")
    assert qc.rig_checks(skeleton=None, stretch=None, clips=[{"name": "walk", "follow": None}]) == []


def test_tools_can_be_stopped_and_the_setup_report_lists_every_part(client, monkeypatch):
    from assetgen import watchdog
    settings = client.app.state.pipeline.settings
    settings.restart = {"llm": {"command": ["lms", "server", "start"], "stop": ["lms", "server", "stop"]},
                        "image": {"command": ["python", "main.py"], "port": 8188}}
    ran = []
    monkeypatch.setattr(watchdog.subprocess, "run", lambda command, **kw: ran.append(command))
    monkeypatch.setattr(watchdog, "_stop_port", lambda port: ran.append(["port", port]))
    monkeypatch.setattr(watchdog, "_check", lambda tool, s: (False, "down"))
    assert client.post("/api/tools/llm/stop").json() == {"tool": "llm", "result": "stopped"}
    assert client.post("/api/tools/image/stop").status_code == 200
    assert ran == [["lms", "server", "stop"], ["port", 8188]]  # its own command, else its port
    assert client.post("/api/tools/mesh/stop").status_code == 422  # nothing says how
    tools = {t["tool"]: t for t in client.get("/api/tools").json()}
    assert tools["llm"]["stoppable"] and tools["image"]["stoppable"] and not tools["mesh"]["stoppable"]

    parts = client.get("/api/setup").json()
    assert [p["id"] for p in parts] == ["llm", "image", "mesh", "rig", "blender", "multiview"]
    mesh = next(p for p in parts if p["id"] == "mesh")
    assert mesh["required"] and any("hunyuan3d-2.1.patch" in (s["command"] or "") for s in mesh["steps"])
    assert not next(p for p in parts if p["id"] == "multiview")["required"]
    assert all(p["steps"] and p["checks"] for p in parts)


def test_install_md_is_generated_from_the_setup_guide():
    from pathlib import Path
    from assetgen.setup_guide import install_markdown
    install = Path(__file__).resolve().parents[2] / "INSTALL.md"
    assert install.read_text(encoding="utf-8") == install_markdown(), (
        "INSTALL.md is out of date: cd backend; uv run python -m assetgen.setup_guide > ../INSTALL.md")
