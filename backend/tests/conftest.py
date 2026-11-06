from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from assetgen.adapters import Adapters
from assetgen.app import create_app
from assetgen.config import load_settings
from fakes import FakeBlender, FakeImage, FakeLLM, FakeMesh, FakeRig


@pytest.fixture(autouse=True)
def _no_local_config(tmp_path, monkeypatch):
    """Every test runs in its own folder, so the developer's backend/assetgen.toml is never picked up."""
    monkeypatch.chdir(tmp_path)
    for name in [n for n in os.environ if n.startswith("ASSETGEN_")]:
        monkeypatch.delenv(name)


def fake_adapters(**real) -> Adapters:
    """The test doubles for every tool; pass a real adapter by name to use it instead
    (for example ``mesh=Hunyuan3DMesh(settings)`` against a fake server)."""
    adapters = {"llm": FakeLLM(), "image": FakeImage(), "mesh": FakeMesh(), "rig": FakeRig(),
                "blender": FakeBlender()}
    return Adapters(**{**adapters, **real})


def make_app(settings, **real):
    """The app with the test doubles (and any real adapters given by name)."""
    return create_app(settings, adapters=fake_adapters(**real))


@pytest.fixture
def settings(tmp_path):
    return load_settings(data_dir=tmp_path / "data")


@pytest.fixture
def client(settings):
    with TestClient(make_app(settings)) as test_client:
        yield test_client


def wait_for(client: TestClient, asset_id: str, status: str, gate: int | None = None, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    asset = None
    while time.monotonic() < deadline:
        asset = client.get(f"/api/assets/{asset_id}").json()
        if asset["status"] == "failed" and status != "failed":
            raise AssertionError(f"asset failed: {asset['error']}\n{asset.get('error_detail')}")
        if asset["status"] == status and (gate is None or asset["gate"] == gate):
            return asset
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {status} (gate {gate}); last: {asset and asset['status']}")


def make_project(client: TestClient, engine: str, path) -> dict:
    response = client.post("/api/projects", json={"name": f"My {engine} game", "engine": engine, "path": str(path)})
    assert response.status_code == 201, response.text
    return response.json()
