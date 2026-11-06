"""The real Hunyuan3D adapter against a fake ``api_server.py``: success, resume,
unreachable, out of memory and timeout; background removal, the albedo
texture and the de-light pass; and the mesh stage end to end."""

from __future__ import annotations

import base64
import io
import json
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from assetgen import imaging
from assetgen.adapters.real.hunyuan3d import CUTOUT_FILE, DOWNLOAD_FILE, JOB_FILE, Hunyuan3DMesh, MeshError
from assetgen.app import create_app
from assetgen.config import load_settings
from conftest import make_project, wait_for

GREY = (200, 200, 200)


def textured_glb(albedo: Image.Image) -> bytes:
    """One textured triangle, the albedo stored as a PNG in the binary chunk."""
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    uvs = struct.pack("<6f", 0, 0, 1, 0, 0, 1)
    indices = struct.pack("<3H", 0, 1, 2) + b"\x00\x00"
    png = imaging.png_bytes(albedo)
    binary = positions + uvs + indices + png
    gltf = {
        "asset": {"version": "2.0", "generator": "fake hunyuan3d"},
        "scenes": [{"nodes": [0]}], "scene": 0, "nodes": [{"mesh": 0, "name": "mesh"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
        "textures": [{"source": 0}],
        "images": [{"bufferView": 3, "mimeType": "image/png"}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 0]},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2"},
            {"bufferView": 2, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": 24},
            {"buffer": 0, "byteOffset": 60, "byteLength": 6},
            {"buffer": 0, "byteOffset": 68, "byteLength": len(png)},
        ],
        "buffers": [{"byteLength": len(binary)}],
    }
    return imaging.glb_bytes(gltf, binary)


def lit_albedo(size: int = 256) -> Image.Image:
    """A red albedo with light baked in: bright on top, dark at the bottom."""
    y = np.linspace(1.4, 0.5, size, dtype=np.float32)[:, None, None]
    base = np.array([180, 60, 50], dtype=np.float32)[None, None, :]
    pixels = np.clip(base * y * np.ones((size, size, 1), dtype=np.float32), 0, 255).astype(np.uint8)
    # Some small detail that must survive.
    pixels[::16, :, :] = (40, 30, 20)
    return Image.fromarray(pixels, mode="RGB")


class FakeHunyuan:
    """``api_server.py`` look-alike. ``behaviour``: ok (processing, texturing,
    completed), oom (the job fails with CUDA out of memory), oom_send (POST /send
    answers 500 with an OOM message) or hang (processing forever)."""

    def __init__(self, behaviour: str = "ok"):
        self.behaviour = behaviour
        self.requests: list[dict] = []
        self.polls: dict[str, int] = {}
        self.glb = textured_glb(lit_albedo())
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake.requests.append({"method": "GET", "path": self.path})
                if self.path == "/health":
                    return self._json({"status": "healthy", "worker_id": "abc123"})
                if self.path.startswith("/status/"):
                    uid = self.path.rsplit("/", 1)[1]
                    count = fake.polls[uid] = fake.polls.get(uid, 0) + 1
                    if fake.behaviour == "oom":
                        return self._json({"status": "error",
                                           "message": "CUDA out of memory. Tried to allocate 2.00 GiB"})
                    if fake.behaviour == "hang" or count == 1:
                        return self._json({"status": "processing"})
                    if count == 2:
                        return self._json({"status": "texturing"})
                    return self._json({"status": "completed",
                                       "model_base64": base64.b64encode(fake.glb).decode()})
                self._json({"detail": "Not Found"}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"method": "POST", "path": self.path, "body": body})
                if fake.behaviour == "oom_send":
                    return self._json({"text": "torch.OutOfMemoryError: CUDA out of memory", "error_code": 1}, 500)
                self._json({"uid": f"job-{len(fake.requests)}"})

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

    def sends(self) -> list[dict]:
        return [r for r in self.requests if r["method"] == "POST"]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_hunyuan():
    servers = []

    def start(behaviour: str = "ok") -> FakeHunyuan:
        server = FakeHunyuan(behaviour)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def adapter(tmp_path, url: str, timeout: float = 10.0) -> Hunyuan3DMesh:
    settings = load_settings(data_dir=tmp_path, hunyuan3d_url=url, hunyuan3d_timeout=timeout, hunyuan3d_poll=0.01)
    return Hunyuan3DMesh(settings)


def candidate(tmp_path):
    """A preview candidate: a blue figure on the neutral grey background."""
    image = Image.new("RGB", (128, 128), GREY)
    pixels = np.asarray(image).copy()
    pixels[24:110, 44:84] = (40, 60, 160)
    path = tmp_path / "input.png"
    Image.fromarray(pixels).save(path)
    return path


def run(mesh: Hunyuan3DMesh, tmp_path, texture_size=128, delight=True):
    steps = []
    out = tmp_path / "raw.glb"
    result = mesh.image_to_mesh(image=candidate(tmp_path), texture_size=texture_size, out_path=out,
                                progress=lambda s, f: steps.append((s, f)),
                                hints={"asset_id": "a1", "attempt": 1, "kind": "hero"}, delight=delight)
    return result, steps


# ---- success ------------------------------------------------------------------

def test_success_writes_glb_and_albedo(fake_hunyuan, tmp_path):
    server = fake_hunyuan()
    result, steps = run(adapter(tmp_path, server.url), tmp_path, texture_size=128)

    assert result["glb"] == tmp_path / "raw.glb" and result["texture"] == tmp_path / "texture.png"
    with Image.open(result["texture"]) as texture:
        assert texture.size == (128, 128)
    gltf, binary = imaging.read_glb(result["glb"])
    assert gltf["asset"]["extras"]["source"] == "hunyuan3d-2.1"
    with imaging.albedo_image(gltf, binary) as embedded:
        assert embedded.size == (128, 128)  # the GLB carries the same scaled albedo
    assert imaging.triangle_count(gltf) == 1
    assert (tmp_path / DOWNLOAD_FILE).is_file() and not (tmp_path / JOB_FILE).exists()

    (send,) = server.sends()
    assert send["path"] == "/send"
    assert send["body"]["texture"] is True and send["body"]["remove_background"] is False
    names = [s for s, _ in steps]
    assert names[0] == "Removing the background"
    assert "Building the shape" in names and "Painting the texture" in names and names[-1] == "Mesh ready"
    assert all(0 <= f <= 1 for _, f in steps)


def test_background_is_removed_before_sending(fake_hunyuan, tmp_path):
    server = fake_hunyuan()
    run(adapter(tmp_path, server.url), tmp_path)
    sent = Image.open(io.BytesIO(base64.b64decode(server.sends()[0]["body"]["image"])))
    assert sent.mode == "RGBA"
    alpha = np.asarray(sent.getchannel("A"))
    assert alpha[0, 0] == 0 and alpha[5, 64] == 0 and alpha[127, 127] == 0  # grey background gone
    assert alpha[60, 64] == 255  # the figure stays
    assert (tmp_path / CUTOUT_FILE).is_file()


def test_busy_background_is_left_to_hunyuan(tmp_path):
    noise = Image.fromarray(np.random.default_rng(1).integers(0, 255, (64, 64, 3), dtype=np.uint8))
    cut, method = imaging.remove_background(noise)
    assert method == "none" and cut.mode == "RGBA"


def test_delight_evens_out_baked_light(fake_hunyuan, tmp_path):
    server = fake_hunyuan()
    lit_dir, flat_dir = tmp_path / "lit", tmp_path / "flat"
    lit_dir.mkdir()
    flat_dir.mkdir()
    run(adapter(lit_dir, server.url), lit_dir, texture_size=256, delight=False)
    result, _ = run(adapter(flat_dir, server.url), flat_dir, texture_size=256, delight=True)

    def row_brightness(path):
        pixels = np.asarray(Image.open(path).convert("L")).astype(np.float32)
        # Rows between the dark detail lines, away from the texture's border where any blur is one-sided.
        return pixels[24:232:16].mean(axis=1)

    lit, flat = row_brightness(lit_dir / "texture.png"), row_brightness(flat_dir / "texture.png")
    assert lit.max() - lit.min() > 60  # top to bottom light baked in
    assert flat.max() - flat.min() < (lit.max() - lit.min()) * 0.4
    flat_rgb = np.asarray(Image.open(flat_dir / "texture.png")).astype(int)
    assert (flat_rgb[40, :, 0] > flat_rgb[40, :, 2]).all()  # still red
    assert flat_rgb[48, 10].sum() < flat_rgb[40, 10].sum()  # the dark detail lines survive
    gltf, _ = imaging.read_glb(result["glb"])
    assert gltf["asset"]["extras"]["delight"] is True


# ---- resume -------------------------------------------------------------------

def test_resume_polls_the_saved_job(fake_hunyuan, tmp_path):
    server = fake_hunyuan()
    candidate(tmp_path)
    (tmp_path / CUTOUT_FILE).write_bytes((tmp_path / "input.png").read_bytes())
    (tmp_path / JOB_FILE).write_text(json.dumps({"uid": "job-earlier", "url": server.url, "sent_at": time.time()}))
    result, steps = run(adapter(tmp_path, server.url), tmp_path)

    assert server.sends() == []  # no new job
    assert server.polls["job-earlier"] == 3
    assert ("Resuming the Hunyuan3D job", 0.1) in steps
    assert result["glb"].is_file() and not (tmp_path / JOB_FILE).exists()


def test_resume_after_download_needs_no_server(fake_hunyuan, tmp_path):
    server = fake_hunyuan()
    run(adapter(tmp_path, server.url), tmp_path)
    (tmp_path / "raw.glb").unlink()
    (tmp_path / "texture.png").unlink()
    server.close()
    result, _ = run(adapter(tmp_path, server.url), tmp_path)  # only the texture work is redone
    assert result["glb"].is_file() and result["texture"].is_file()


def test_stale_job_is_sent_again(fake_hunyuan, tmp_path):
    server = fake_hunyuan()
    (tmp_path / JOB_FILE).write_text(json.dumps({"uid": "job-old", "url": server.url, "sent_at": time.time() - 99}))
    run(adapter(tmp_path, server.url, timeout=5), tmp_path)
    assert len(server.sends()) == 1 and "job-old" not in server.polls


# ---- failures -----------------------------------------------------------------

def test_unreachable_server_is_readable(tmp_path):
    with pytest.raises(MeshError, match=r"Could not reach Hunyuan3D at http://127.0.0.1:9 .*api_server.py"):
        run(adapter(tmp_path, "http://127.0.0.1:9"), tmp_path)


def test_out_of_memory_during_the_job(fake_hunyuan, tmp_path):
    server = fake_hunyuan("oom")
    with pytest.raises(MeshError, match="ran out of GPU memory.*--low_vram_mode"):
        run(adapter(tmp_path, server.url), tmp_path)
    assert not (tmp_path / JOB_FILE).exists()  # a retry starts a new job


def test_out_of_memory_on_send(fake_hunyuan, tmp_path):
    server = fake_hunyuan("oom_send")
    with pytest.raises(MeshError, match="ran out of GPU memory"):
        run(adapter(tmp_path, server.url), tmp_path)


def test_timeout(fake_hunyuan, tmp_path):
    server = fake_hunyuan("hang")
    with pytest.raises(MeshError, match=r"did not finish within 0 s \(last status: 'processing'\)"):
        run(adapter(tmp_path, server.url, timeout=0.2), tmp_path)
    assert not (tmp_path / JOB_FILE).exists()
    assert len(server.sends()) == 1


# ---- the app ------------------------------------------------------------------

def test_gate_2_shows_the_hunyuan_mesh(fake_hunyuan, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    server = fake_hunyuan()
    settings = load_settings(data_dir=tmp_path / "data", mock_step_delay=0.0, hunyuan3d_url=server.url,
                             hunyuan3d_poll=0.01, adapters={"mesh": "real"})
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/meta").json()["profile_defaults"]["delight"] is True
        project = make_project(client, "unity", tmp_path / "game")
        created = client.post("/api/assets", json={"project_id": project["id"], "kind": "monster",
                                                   "description": "a grey wolf"})
        asset_id = created.json()["id"]
        wait_for(client, asset_id, "waiting", gate=1)
        assert client.post(f"/api/assets/{asset_id}/gates/1/approve", json={"candidate": 1}).status_code == 200
        asset = wait_for(client, asset_id, "waiting", gate=2)

        mesh = asset["outputs"]["mesh"]
        assert mesh["delight"] is True and mesh["lods"][0]["triangles"] == 1
        glb = client.get(asset["urls"]["mesh"].replace("http://testserver", ""))
        assert glb.status_code == 200
        gltf, _ = imaging.parse_glb(glb.content)
        assert gltf["asset"]["extras"]["source"] == "hunyuan3d-2.1" and gltf["extras"]["passthrough"] is True
        texture = client.get(asset["urls"]["texture"].replace("http://testserver", ""))
        assert Image.open(io.BytesIO(texture.content)).size == (512, 512)  # the monster texture budget
        tools = {t["tool"]: t for t in client.get("/api/tools").json()}
        assert tools["mesh"]["ok"] is True and tools["mesh"]["detail"].endswith("/health answered HTTP 200")

        # Through gate 3 too: the mock rig copes with a real mesh.
        assert client.post(f"/api/assets/{asset_id}/gates/2/approve").status_code == 200
        wait_for(client, asset_id, "waiting", gate=3)


def test_delight_flag_in_profiles(client):
    created = client.post("/api/profiles", json={"name": "Lit", "delight": False})
    assert created.status_code == 201 and created.json()["delight"] is False
    patched = client.patch(f"/api/profiles/{created.json()['id']}", json={"delight": True})
    assert patched.json()["delight"] is True
    assert client.post("/api/profiles", json={"name": "Bad", "delight": "maybe"}).status_code == 422
