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
    assert next(c for c in two if c["id"] == "figures")["status"] == qc.FAIL
    assert next(c for c in two if c["id"] == "props")["status"] == qc.WARN  # no vision model: not checked

    cut = tmp_path / "c.png"
    image = Image.new("RGB", (200, 200), (200, 200, 200))
    ImageDraw.Draw(image).rectangle((80, 120, 120, 199), fill=(150, 20, 20))  # legs run off the bottom
    image.save(cut)
    assert next(c for c in qc.candidate_checks(cut, kind="weapon", posed_biped=False, vision=None)
                if c["id"] == "whole")["status"] == qc.FAIL


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
