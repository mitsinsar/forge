"""The Modly mesh adapter against a fake Modly server: success, the request it sends,
resume, failures (texture extensions missing, out of memory, unreachable, timeout,
a mesh without texture), the mesh_tool switch and the mesh stage end to end."""

from __future__ import annotations

import io
import json
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from assetgen import imaging
from assetgen.adapters import build_adapters
from assetgen.adapters.real.hunyuan3d import Hunyuan3DMesh
from assetgen.adapters.real.mesh_common import MeshError
from assetgen.adapters.real.modly import DOWNLOAD_FILE, RUN_FILE, ModlyMesh
from assetgen.config import load_settings
from assetgen.tools import check_tools
from conftest import make_app, make_project, wait_for
from test_hunyuan3d import candidate, lit_albedo, textured_glb

TEXTURE_ERROR = ("Traceback (most recent call last):\n  ...\nRuntimeError: C++ extensions for texture generation "
                 "are not compiled.\nBuild them with: ...\n\nOriginal error: No module named 'xatlas'")


def untextured_glb() -> bytes:
    gltf, binary = imaging.parse_glb(textured_glb(lit_albedo(32)))
    for key in ("materials", "textures", "images"):
        gltf.pop(key)
    gltf["meshes"][0]["primitives"][0].pop("material")
    return imaging.glb_bytes(gltf, binary)


def form_fields(body: bytes, content_type: str) -> dict:
    boundary = content_type.split("boundary=")[1].encode()
    fields = {}
    for part in body.split(b"--" + boundary)[1:-1]:
        head, _, value = part.partition(b"\r\n\r\n")
        name = re.search(rb'name="([^"]+)"', head).group(1).decode()
        fields[name] = value[:-2]  # the trailing \r\n
    return fields


class FakeModly:
    """Modly's FastAPI look-alike. ``behaviour``: ok (pending, running, done), texture_error,
    oom, hang (running forever) or no_texture (done, but the GLB has no albedo)."""

    def __init__(self, behaviour: str = "ok"):
        self.behaviour = behaviour
        self.posts: list[dict] = []
        self.gets: list[str] = []
        self.polls: dict[str, int] = {}
        self.glb = untextured_glb() if behaviour == "no_texture" else textured_glb(lit_albedo())
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake.gets.append(self.path)
                url = urllib.parse.urlsplit(self.path)
                if url.path == "/health":
                    return self._json({"status": "ok"})
                if url.path.startswith("/workflow-runs/"):
                    run_id = url.path.rsplit("/", 1)[1]
                    count = fake.polls[run_id] = fake.polls.get(run_id, 0) + 1
                    base = {"run_id": run_id, "output_url": None, "error": None, "scene_candidate": None}
                    if fake.behaviour == "texture_error" and count > 1:
                        return self._json({**base, "status": "error", "progress": 72,
                                           "step": "Freeing VRAM for texture model…", "error": TEXTURE_ERROR})
                    if fake.behaviour == "oom" and count > 1:
                        return self._json({**base, "status": "error", "progress": 12, "step": "Generating 3D shape…",
                                           "error": "torch.OutOfMemoryError: CUDA out of memory."})
                    if fake.behaviour == "hang" or count == 1:
                        return self._json({**base, "status": "running", "progress": 12,
                                           "step": "Generating 3D shape…"})
                    return self._json({**base, "status": "done", "progress": 100, "step": "Done",
                                       "output_url": "/workspace/assetgen/123_abc.glb",
                                       "scene_candidate": {"workspace_path": "assetgen/123_abc.glb"}})
                if url.path == "/export/glb":
                    assert urllib.parse.parse_qs(url.query)["path"] == ["assetgen/123_abc.glb"]
                    self.send_response(200)
                    self.send_header("Content-Type", "model/gltf-binary")
                    self.send_header("Content-Length", str(len(fake.glb)))
                    self.end_headers()
                    return self.wfile.write(fake.glb)
                self._json({"detail": "Not Found"}, 404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fake.posts.append({"path": self.path, "fields": form_fields(body, self.headers["Content-Type"])})
                self._json({"run_id": f"run-{len(fake.posts)}", "status": "pending"})

            def _json(self, data, status=200):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_modly():
    servers = []

    def start(behaviour: str = "ok") -> FakeModly:
        server = FakeModly(behaviour)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def adapter(tmp_path, url: str, timeout: float = 10.0, **extra) -> ModlyMesh:
    return ModlyMesh(load_settings(data_dir=tmp_path, mesh_tool="modly", modly_url=url, modly_timeout=timeout,
                                   modly_poll=0.01, **extra))


def run(mesh: ModlyMesh, tmp_path, texture_size=128, delight=True):
    steps = []
    result = mesh.image_to_mesh(image=candidate(tmp_path), texture_size=texture_size, out_path=tmp_path / "raw.glb",
                                progress=lambda s, f: steps.append((s, f)),
                                hints={"asset_id": "a1", "attempt": 1, "kind": "hero"}, delight=delight)
    return result, steps


def test_success_sends_the_image_and_finishes_the_texture(fake_modly, tmp_path):
    server = fake_modly()
    result, steps = run(adapter(tmp_path, server.url, modly_params='{"num_inference_steps": 5}'), tmp_path)

    (post,) = server.posts
    assert post["path"] == "/workflow-runs/from-image"
    fields = post["fields"]
    assert fields["model_id"] == b"hunyuan3d-mini-turbo/generate" and fields["collection"] == b"assetgen"
    params = json.loads(fields["params"])
    assert params["enable_texture"] is True and params["num_inference_steps"] == 5
    assert isinstance(params["seed"], int)
    assert fields["image"] == (tmp_path / "input.png").read_bytes()  # sent as it is: Modly cuts it out
    assert any(path.startswith("/export/glb?path=") for path in server.gets)

    assert result["glb"] == tmp_path / "raw.glb" and result["background"] == "removed by Modly"
    with Image.open(result["texture"]) as texture:
        assert texture.size == (128, 128)
    gltf, _ = imaging.read_glb(result["glb"])
    assert gltf["asset"]["extras"]["source"] == "modly:hunyuan3d-mini-turbo/generate"
    assert (tmp_path / DOWNLOAD_FILE).is_file() and not (tmp_path / RUN_FILE).exists()
    assert any(step.startswith("Modly: Generating 3D shape") for step, _ in steps)
    fractions = [f for _, f in steps]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0


def test_the_same_seed_for_the_same_attempt(fake_modly, tmp_path):
    server = fake_modly()
    mesh = adapter(tmp_path, server.url)
    run(mesh, tmp_path)
    (tmp_path / DOWNLOAD_FILE).unlink()
    run(mesh, tmp_path)
    seeds = [json.loads(p["fields"]["params"])["seed"] for p in server.posts]
    assert len(seeds) == 2 and seeds[0] == seeds[1]


def test_a_saved_run_is_resumed_not_resent(fake_modly, tmp_path):
    server = fake_modly()
    mesh = adapter(tmp_path, server.url)
    (tmp_path / RUN_FILE).write_text(json.dumps({"run_id": "run-earlier", "url": server.url,
                                                 "model": mesh.model, "sent_at": time.time()}))
    run(mesh, tmp_path)
    assert server.posts == [] and "run-earlier" in server.polls


def test_a_kept_download_is_not_fetched_again(fake_modly, tmp_path):
    server = fake_modly()
    (tmp_path / DOWNLOAD_FILE).write_bytes(server.glb)
    result, _ = run(adapter(tmp_path, server.url), tmp_path)
    assert server.posts == [] and result["glb"].is_file()


def test_missing_texture_extensions_are_explained(fake_modly, tmp_path):
    server = fake_modly("texture_error")
    with pytest.raises(MeshError, match="texture extensions are not built") as error:
        run(adapter(tmp_path, server.url), tmp_path)
    assert "xatlas" in str(error.value)
    assert not (tmp_path / RUN_FILE).exists()  # a retry starts a new run


def test_out_of_memory_is_explained(fake_modly, tmp_path):
    with pytest.raises(MeshError, match="ran out of GPU memory"):
        run(adapter(tmp_path, fake_modly("oom").url), tmp_path)


def test_a_mesh_without_texture_fails_readably(fake_modly, tmp_path):
    with pytest.raises(MeshError, match="Modly sent a mesh without an albedo texture"):
        run(adapter(tmp_path, fake_modly("no_texture").url), tmp_path)


def test_unreachable_modly_is_readable(tmp_path):
    with pytest.raises(MeshError, match="Could not reach Modly .* Is the Modly app open"):
        run(adapter(tmp_path, "http://127.0.0.1:9"), tmp_path)


def test_timeout(fake_modly, tmp_path):
    server = fake_modly("hang")
    with pytest.raises(MeshError, match="did not finish within"):
        run(adapter(tmp_path, server.url, timeout=0.2), tmp_path)
    assert not (tmp_path / RUN_FILE).exists()


def test_bad_extra_params_are_refused(tmp_path):
    with pytest.raises(ValueError, match="modly_params"):
        adapter(tmp_path, "http://127.0.0.1:9", modly_params="{not json")
    with pytest.raises(ValueError, match="JSON object"):
        adapter(tmp_path, "http://127.0.0.1:9", modly_params="[1, 2]")


def test_mesh_tool_switch_and_health_check(fake_modly, tmp_path):
    assert isinstance(build_adapters(load_settings(data_dir=tmp_path)).mesh, Hunyuan3DMesh)
    server = fake_modly()
    settings = load_settings(data_dir=tmp_path, mesh_tool="modly", modly_url=server.url)
    assert isinstance(build_adapters(settings).mesh, ModlyMesh)
    mesh = {t["tool"]: t for t in check_tools(settings)}["mesh"]
    assert mesh["name"] == "Modly" and mesh["ok"] is True and mesh["detail"].endswith("/health answered HTTP 200")
    with pytest.raises(ValueError, match="mesh_tool"):
        load_settings(data_dir=tmp_path, mesh_tool="tripo")


def test_gate_2_shows_the_modly_mesh(fake_modly, tmp_path):
    server = fake_modly()
    settings = load_settings(data_dir=tmp_path / "data", mesh_tool="modly", modly_url=server.url, modly_poll=0.01)
    with TestClient(make_app(settings, mesh=ModlyMesh(settings))) as client:
        project = make_project(client, "unity", tmp_path / "game")
        asset_id = client.post("/api/assets", json={"project_id": project["id"], "kind": "monster",
                                                    "description": "a grey wolf"}).json()["id"]
        wait_for(client, asset_id, "waiting", gate=1)
        client.post(f"/api/assets/{asset_id}/gates/1/approve", json={"candidate": 1}).raise_for_status()
        asset = wait_for(client, asset_id, "waiting", gate=2)
        glb = client.get(asset["urls"]["mesh"].replace("http://testserver", ""))
        gltf, _ = imaging.parse_glb(glb.content)
        assert gltf["asset"]["extras"]["source"] == "modly:hunyuan3d-mini-turbo/generate"
        texture = client.get(asset["urls"]["texture"].replace("http://testserver", ""))
        with Image.open(io.BytesIO(texture.content)) as image:
            assert image.size == (512, 512)  # the monster texture budget
        assert len(server.posts) == 1
