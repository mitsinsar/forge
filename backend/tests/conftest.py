from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from assetgen.app import create_app
from assetgen.config import load_settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no stray assetgen.toml is picked up
    return load_settings(data_dir=tmp_path / "data", mock_step_delay=0.0)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
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
