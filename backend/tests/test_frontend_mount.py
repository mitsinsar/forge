"""The built web app is served at / when frontend/dist exists, with /api routes first."""

from __future__ import annotations

from fastapi.testclient import TestClient

from assetgen.app import create_app
from assetgen.config import load_settings


def test_serves_built_app_and_keeps_api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    settings = load_settings(data_dir=tmp_path / "data", mock_step_delay=0.0, frontend_dir=dist)
    with TestClient(create_app(settings)) as client:
        assert client.get("/").text == "<html>app</html>"
        assert client.get("/assets/app.js").text == "console.log(1)"
        assert client.get("/assets/abc123").text == "<html>app</html>"  # the app's own route
        assert client.get("/api/meta").json()["adapters"]
        assert client.get("/api/nope").status_code == 404
        assert client.get("/../secret").status_code in (200, 404)  # never leaves dist


def test_no_dist_no_mount(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings(data_dir=tmp_path / "data", mock_step_delay=0.0, frontend_dir=tmp_path / "missing")
    with TestClient(create_app(settings)) as client:
        assert client.get("/").status_code == 404
