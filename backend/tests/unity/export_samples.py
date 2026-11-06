"""Exports a sample hero, weapon and gear into a Unity project, for checking the
editor import script in Unity (see README, "Checking the Unity import script").

    uv run python tests/unity/export_samples.py <UnityProject> [--data <dir>] [--skinned] [--reexport]

Runs the pipeline in-process with the mock adapters and, when Blender is
installed, the real Blender adapter (so the FBX files are real). The first run
creates the assets and approves every gate; ``--reexport`` exports the same
assets again (keep the same ``--data``), which is how a re-export is checked.

The mock rig animates the whole model without a skeleton, which Unity cannot
make a Humanoid avatar of. ``--skinned`` (needs Blender) swaps the hero's and
the gear's animated GLB for a skinned biped with the archetype's Mixamo bone
names (``make_biped_glb.py``), as the real rig stage will make them.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from assetgen.app import create_app
from assetgen.config import load_settings

SAMPLES = [
    {"kind": "hero", "name": "Ember Knight", "description": "a knight in ember-red plate armour"},
    {"kind": "weapon", "name": "Iron Sword", "description": "a plain iron sword", "weapon_class": "one_handed"},
    {"kind": "gear", "name": "Iron Helm", "description": "a dented iron helmet", "slot": "head"},
]


def wait(client: TestClient, asset_id: str, status: str, gate: int | None = None, timeout: float = 600) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        asset = client.get(f"/api/assets/{asset_id}").json()
        if asset["status"] == "failed":
            sys.exit(f"{asset['name']} failed: {asset['error']}")
        if asset["status"] == status and (gate is None or asset["gate"] == gate):
            return asset
        time.sleep(0.2)
    sys.exit(f"timed out waiting for {status}")


def use_skinned_biped(client: TestClient, blender: str) -> None:
    tools = client.app.state.pipeline.adapters.blender
    original = tools.rig_and_animate

    def rig_and_animate(*, attach, out_path, **kwargs):
        result = original(attach=attach, out_path=out_path, **kwargs)
        if attach and attach.get("kind") == "weapon":
            return result
        part = "head" if attach and attach.get("slot") == "head" else "body"
        job = Path(out_path).with_name("biped_args.json")
        job.write_text(json.dumps({"out": str(Path(out_path).resolve()), "part": part, "clips": result["clips"]}))
        subprocess.run([blender, "-b", "--factory-startup", "--python-exit-code", "1", "-P",
                        str(Path(__file__).with_name("make_biped_glb.py")), "--", str(job.resolve())],
                       check=True, capture_output=True, timeout=300)
        return result

    tools.rig_and_animate = rig_and_animate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--data", type=Path, default=Path("unity-samples-data"))
    parser.add_argument("--reexport", action="store_true")
    parser.add_argument("--skinned", action="store_true")
    args = parser.parse_args()
    blender = shutil.which("blender") or "/Applications/Blender.app/Contents/MacOS/Blender"
    real_blender = Path(blender).is_file()
    settings = load_settings(data_dir=args.data, mock_step_delay=0.0, blender_path=blender,
                             adapters={"blender": "real" if real_blender else "mock"})
    print(f"Blender: {'real (' + blender + ')' if real_blender else 'mock (placeholder FBX)'}")
    with TestClient(create_app(settings)) as client:
        if args.skinned:
            if not real_blender:
                sys.exit("--skinned needs Blender")
            use_skinned_biped(client, blender)
        if args.reexport:
            for asset in client.get("/api/assets").json():
                client.post(f"/api/assets/{asset['id']}/export")
                done = wait(client, asset["id"], "done")
                print(f"re-exported {done['name']} -> {done['outputs']['export']['installed_to']}")
            return
        project = client.post("/api/projects", json={"name": "Unity check", "engine": "unity",
                                                     "path": str(args.project.resolve())}).json()
        for sample in SAMPLES:
            asset = client.post("/api/assets", json={"project_id": project["id"], **sample}).json()
            wait(client, asset["id"], "waiting", gate=1)
            client.post(f"/api/assets/{asset['id']}/gates/1/approve", json={"candidate": 1})
            for gate in (2, 3):
                wait(client, asset["id"], "waiting", gate=gate)
                client.post(f"/api/assets/{asset['id']}/gates/{gate}/approve")
            done = wait(client, asset["id"], "done")
            export = done["outputs"]["export"]
            print(f"exported {done['name']} -> {export['installed_to']}")
            print(f"  importer: {json.dumps(export.get('importer'))}")


if __name__ == "__main__":
    main()
