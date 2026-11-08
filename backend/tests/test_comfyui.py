"""The real ComfyUI image adapter against a fake ComfyUI (HTTP + /ws WebSocket)."""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from assetgen.adapters.real.comfyui import DEFAULT_WORKFLOWS_DIR, ComfyUIError, ComfyUIImage, find_node
from assetgen.config import load_settings
from conftest import make_app, make_project, wait_for
from fake_comfyui import CORE_NODES, FakeComfyUI

MODELS = {
    "sdxl": {"sd_xl_base_1.0.safetensors"},
    "flux": {"flux1-dev.safetensors", "t5xxl_fp16.safetensors", "clip_l.safetensors", "ae.safetensors",
             "sigclip_vision_patch14_384.safetensors", "flux1-redux-dev.safetensors"},
    "qwen-image": {"qwen_image_fp8_e4m3fn.safetensors", "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                   "qwen_image_vae.safetensors"},
}
ALL_MODELS = set().union(*MODELS.values())


@pytest.fixture
def fake_comfy():
    servers = []

    def start(**kwargs) -> FakeComfyUI:
        kwargs.setdefault("models", ALL_MODELS)
        server = FakeComfyUI(**kwargs)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def adapter(server_url: str, tmp_path, **settings) -> ComfyUIImage:
    return ComfyUIImage(load_settings(data_dir=tmp_path / "data", comfyui_url=server_url, **settings))


def png(color=(200, 30, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buffer, "PNG")
    return buffer.getvalue()


def render(image: ComfyUIImage, tmp_path, *, base_model="sdxl", loras=(), references=(), count=2, seed=100,
           progress=None, log=None):
    return image.render_candidates(
        prompt="a knight, A-pose", negative_prompt="extra limbs", base_model=base_model, loras=list(loras),
        reference_images=list(references), count=count, seed=seed, out_dir=tmp_path / "prompt",
        progress=progress or (lambda step, fraction: None), hints={"asset_id": "abc"}, log=log)


def node_of(workflow: dict, class_type: str) -> list[dict]:
    return [n for n in workflow.values() if n["class_type"] == class_type]


def test_workflows_have_the_titled_nodes():
    for base_model in MODELS:
        workflow = json.loads((DEFAULT_WORKFLOWS_DIR / f"{base_model}.json").read_text())
        for title in ("model", "positive", "negative", "latent", "sampler", "save"):
            assert find_node(workflow, title), (base_model, title)
        assert all(n["class_type"] in CORE_NODES for n in workflow.values())


def test_queue_progress_and_download(fake_comfy, tmp_path):
    server = fake_comfy()
    steps = []
    paths = render(adapter(server.url, tmp_path), tmp_path, progress=lambda s, f: steps.append((s, f)))

    assert [p.name for p in paths] == ["candidate_1.png", "candidate_2.png"]
    assert all(Image.open(p).size == (64, 64) for p in paths)
    assert len(server.prompts) == 2
    seeds = [node_of(w, "KSampler")[0]["inputs"]["seed"] for w in server.prompts]
    assert seeds == [100, 101]  # seed + n - 1, one prompt per candidate
    texts = [n["inputs"]["text"] for n in node_of(server.prompts[0], "CLIPTextEncode")]
    assert texts == ["a knight, A-pose", "extra limbs"]
    assert node_of(server.prompts[1], "SaveImage")[0]["inputs"]["filename_prefix"] == "assetgen/abc_c2"

    fractions = [f for _, f in steps]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    assert any("Candidate 1 of 2: step 2/3" == s for s, _ in steps)
    assert any("Candidate 2 of 2: step 3/3" == s for s, _ in steps)
    assert not list((tmp_path / "prompt").glob("*.part"))


def test_resume_keeps_existing_candidates(fake_comfy, tmp_path):
    server = fake_comfy()
    (tmp_path / "prompt").mkdir()
    kept = png()
    (tmp_path / "prompt" / "candidate_1.png").write_bytes(kept)

    paths = render(adapter(server.url, tmp_path), tmp_path, count=3)

    assert paths[0].read_bytes() == kept
    assert [node_of(w, "KSampler")[0]["inputs"]["seed"] for w in server.prompts] == [101, 102]
    assert all(p.is_file() for p in paths)
    render(adapter(server.url, tmp_path), tmp_path, count=3)  # all there: nothing queued
    assert len(server.prompts) == 2


def test_loras_are_chained_with_their_weights(fake_comfy, tmp_path):
    comfy_dir = tmp_path / "ComfyUI"
    (comfy_dir / "models" / "loras").mkdir(parents=True)
    elsewhere = tmp_path / "library" / "painterly.safetensors"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"weights")
    server = fake_comfy(loras=["styles\\icons.safetensors"])
    loras = [{"name": "Icons", "path": str(tmp_path / "x" / "icons.safetensors"), "weight": 0.8},
             {"name": "Painterly", "path": str(elsewhere), "weight": 0.45}]
    lines = []

    real = adapter(server.url, tmp_path, comfyui_dir=str(comfy_dir))
    original = real.prepare_loras

    def prepare(given):  # ComfyUI rescans its folder: the copied file becomes known
        result = original(given)
        server.loras.append("painterly.safetensors")
        return result

    real.prepare_loras = prepare
    render(real, tmp_path, loras=loras, count=1, log=lines.append)

    assert (comfy_dir / "models" / "loras" / "painterly.safetensors").read_bytes() == b"weights"
    workflow = server.prompts[0]
    first, second = node_of(workflow, "LoraLoader")
    assert (first["inputs"]["lora_name"], first["inputs"]["strength_model"], first["inputs"]["strength_clip"]) \
        == ("styles\\icons.safetensors", 0.8, 0.8)
    assert (second["inputs"]["lora_name"], second["inputs"]["strength_model"]) == ("painterly.safetensors", 0.45)
    first_id = next(k for k, n in workflow.items() if n is first)
    second_id = next(k for k, n in workflow.items() if n is second)
    assert first["inputs"]["model"] == ["4", 0] and first["inputs"]["clip"] == ["4", 1]
    assert second["inputs"]["model"] == [first_id, 0] and second["inputs"]["clip"] == [first_id, 1]
    assert node_of(workflow, "KSampler")[0]["inputs"]["model"] == [second_id, 0]
    assert all(n["inputs"]["clip"] == [second_id, 1] for n in node_of(workflow, "CLIPTextEncode"))
    assert node_of(workflow, "VAEDecode")[0]["inputs"]["vae"] == ["4", 2]  # untouched
    assert "Icons (0.8)" in lines[0] and "Painterly (0.45)" in lines[0]


def test_lora_outside_comfyui_without_comfyui_dir_is_a_clear_error(fake_comfy, tmp_path):
    server = fake_comfy()
    lora = {"name": "Painterly", "path": str(tmp_path / "painterly.safetensors"), "weight": 1.0}
    with pytest.raises(ComfyUIError, match="comfyui_dir is not set"):
        render(adapter(server.url, tmp_path), tmp_path, loras=[lora])
    assert server.prompts == []


def test_qwen_image_uses_model_only_loras_and_skips_references(fake_comfy, tmp_path):
    server = fake_comfy(loras=["qwen-style.safetensors"])
    reference = tmp_path / "ref.png"
    reference.write_bytes(png())
    lines = []
    render(adapter(server.url, tmp_path), tmp_path, base_model="qwen-image", count=1, log=lines.append,
           loras=[{"name": "Qwen style", "path": "qwen-style.safetensors", "weight": 0.7}], references=[reference])

    workflow = server.prompts[0]
    (lora,) = node_of(workflow, "LoraLoaderModelOnly")
    assert lora["inputs"] == {"lora_name": "qwen-style.safetensors", "strength_model": 0.7, "model": ["37", 0]}
    lora_id = next(k for k, n in workflow.items() if n is lora)
    assert node_of(workflow, "ModelSamplingAuraFlow")[0]["inputs"]["model"] == [lora_id, 0]
    assert node_of(workflow, "LoadImage") == [] and server.uploads == {}
    assert any("Qwen-Image has no reference-image input" in line for line in lines)


def test_sdxl_references_go_through_ip_adapter(fake_comfy, tmp_path):
    server = fake_comfy()
    references = []
    for index, color in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255)]):
        path = tmp_path / f"ref{index}.png"
        path.write_bytes(png(color))
        references.append(path)
    lines = []
    render(adapter(server.url, tmp_path), tmp_path, references=references, count=1, log=lines.append)

    assert len(server.uploads) == 3 and all(name.startswith("assetgen/") for name in server.uploads)
    workflow = server.prompts[0]
    assert sorted(n["inputs"]["image"] for n in node_of(workflow, "LoadImage")) == sorted(server.uploads)
    assert len(node_of(workflow, "ImageBatch")) == 2
    (apply,) = node_of(workflow, "IPAdapterAdvanced")
    apply_id = next(k for k, n in workflow.items() if n is apply)
    assert node_of(workflow, "KSampler")[0]["inputs"]["model"] == [apply_id, 0]
    assert node_of(workflow, "IPAdapterUnifiedLoader")[0]["inputs"]["model"] == ["4", 0]
    assert any("3 style reference image(s) applied through IP-Adapter" in line for line in lines)


def test_sdxl_references_skipped_without_ip_adapter_nodes(fake_comfy, tmp_path):
    server = fake_comfy(nodes=CORE_NODES)
    reference = tmp_path / "ref.png"
    reference.write_bytes(png())
    lines = []
    render(adapter(server.url, tmp_path), tmp_path, references=[reference], count=1, log=lines.append)
    assert node_of(server.prompts[0], "LoadImage") == []
    assert any("IPAdapterUnifiedLoader" in line and "not used" in line for line in lines)


def test_flux_references_go_through_redux(fake_comfy, tmp_path):
    server = fake_comfy()
    references = []
    for index in range(2):
        path = tmp_path / f"ref{index}.png"
        path.write_bytes(png((index * 100, 0, 0)))
        references.append(path)
    render(adapter(server.url, tmp_path), tmp_path, base_model="flux", references=references, count=1)

    workflow = server.prompts[0]
    first, second = node_of(workflow, "StyleModelApply")
    first_id = next(k for k, n in workflow.items() if n is first)
    second_id = next(k for k, n in workflow.items() if n is second)
    assert first["inputs"]["conditioning"] == ["26", 0]  # after FluxGuidance
    assert second["inputs"]["conditioning"] == [first_id, 0]
    assert node_of(workflow, "KSampler")[0]["inputs"]["positive"] == [second_id, 0]


def test_unload_frees_models(fake_comfy, tmp_path):
    server = fake_comfy()
    adapter(server.url, tmp_path).unload()
    assert server.freed == [{"unload_models": True, "free_memory": True}]


def test_unreachable_comfyui(tmp_path):
    with pytest.raises(ComfyUIError, match="Could not reach ComfyUI at http://127.0.0.1:9"):
        render(adapter("http://127.0.0.1:9", tmp_path), tmp_path)


def test_missing_model_file(fake_comfy, tmp_path):
    server = fake_comfy(models=ALL_MODELS - {"ae.safetensors"})
    with pytest.raises(ComfyUIError) as error:
        render(adapter(server.url, tmp_path), tmp_path, base_model="flux")
    message = str(error.value)
    assert message.startswith("ComfyUI is missing a model file")
    assert "'ae.safetensors'" in message and "models/vae" in message


def test_timeout_cancels_the_prompt(fake_comfy, tmp_path):
    server = fake_comfy(hang=True)
    with pytest.raises(ComfyUIError, match="did not finish a candidate within 0.5 s"):
        render(adapter(server.url, tmp_path, comfyui_timeout=0.5), tmp_path, count=1)
    assert server.cancelled == ["/queue", "/interrupt"]
    assert not (tmp_path / "prompt" / "candidate_1.png").exists()


def test_gate_one_shows_comfyui_candidates_and_mesh_stage_unloads(fake_comfy, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    server = fake_comfy(loras=["painterly.safetensors"])
    settings = load_settings(data_dir=tmp_path / "data", comfyui_url=server.url)
    with TestClient(make_app(settings, image=ComfyUIImage(settings))) as client:
        lora = client.post("/api/loras", json={"name": "Painterly", "path": str(tmp_path / "painterly.safetensors"),
                                               "base_model": "sdxl"}).json()
        profile = client.post("/api/profiles", json={"name": "Painted", "base_model": "sdxl",
                                                     "loras": [{"id": lora["id"], "weight": 0.6}],
                                                     "candidates": 2}).json()
        client.post(f"/api/profiles/{profile['id']}/references", files={"file": ("ref.png", png(), "image/png")})
        project = make_project(client, "godot", tmp_path / "game")
        created = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero",
                                                   "description": "a knight", "profile_id": profile["id"]})
        asset = wait_for(client, created.json()["id"], "waiting", gate=1)

        candidates = asset["urls"]["candidates"]
        assert len(candidates) == 2
        downloaded = client.get(candidates[0]).content
        assert downloaded in server.images.values()
        assert len(server.prompts) == 2
        workflow = server.prompts[0]
        assert node_of(workflow, "LoraLoader")[0]["inputs"]["strength_model"] == 0.6
        assert node_of(workflow, "IPAdapterAdvanced") and len(server.uploads) == 1
        log = asset["outputs"]["prompt"]["log"]
        assert any("Painterly (0.6)" in line for line in log)
        assert server.freed == [{"unload_models": True, "free_memory": True}]  # after drawing: the judge gets the GPU

        client.post(f"/api/assets/{asset['id']}/gates/1/approve", json={"candidate": 2})
        wait_for(client, asset["id"], "waiting", gate=2)
        assert server.freed == [{"unload_models": True, "free_memory": True}] * 2  # and again before the mesh
        tools = {t["tool"]: t for t in client.get("/api/tools").json()}
        assert tools["image"]["ok"] is True


def test_failed_comfyui_fails_the_preview_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings(data_dir=tmp_path / "data", comfyui_url="http://127.0.0.1:9")
    with TestClient(make_app(settings, image=ComfyUIImage(settings))) as client:
        project = make_project(client, "godot", tmp_path / "game")
        created = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero",
                                                   "description": "a knight"})
        asset = wait_for(client, created.json()["id"], "failed")
        assert "Could not reach ComfyUI" in asset["error"]


POSE_MODEL = "openpose_sdxl_xinsir.safetensors"


def render_posed(image: ComfyUIImage, tmp_path, *, base_model="sdxl", log=None):
    from assetgen.pose import guide_image
    guide = guide_image(tmp_path / "pose_guide.png", size=64)
    return image.render_candidates(
        prompt="a knight", negative_prompt="", base_model=base_model, loras=[], reference_images=[], count=1,
        seed=1, out_dir=tmp_path / "prompt", progress=lambda s, f: None,
        hints={"asset_id": "abc", "pose_guide": str(guide)}, log=log)


def test_sdxl_pose_guide_holds_the_conditioning(fake_comfy, tmp_path):
    server = fake_comfy(models=ALL_MODELS | {POSE_MODEL})
    lines = []
    render_posed(adapter(server.url, tmp_path), tmp_path, log=lines.append)
    workflow = server.prompts[0]
    (loader,) = node_of(workflow, "ControlNetLoader")
    assert loader["inputs"]["control_net_name"] == POSE_MODEL
    (apply,) = node_of(workflow, "ControlNetApplyAdvanced")
    apply_id = next(k for k, n in workflow.items() if n is apply)
    sampler = node_of(workflow, "KSampler")[0]["inputs"]
    assert sampler["positive"] == [apply_id, 0] and sampler["negative"] == [apply_id, 1]
    assert apply["inputs"]["positive"] == ["6", 0] and apply["inputs"]["strength"] == 0.8
    assert len(server.uploads) == 1 and node_of(workflow, "LoadImage")[0]["inputs"]["image"] in server.uploads
    assert any(line.startswith("Pose guide:") for line in lines)


def test_pose_guide_skipped_without_the_controlnet_or_on_other_models(fake_comfy, tmp_path):
    server = fake_comfy()  # no openpose model
    lines = []
    render_posed(adapter(server.url, tmp_path), tmp_path, log=lines.append)
    assert node_of(server.prompts[0], "ControlNetLoader") == []
    assert any("pose guide was skipped" in line and POSE_MODEL in line for line in lines)

    server = fake_comfy(models=ALL_MODELS | {POSE_MODEL})
    lines = []
    render_posed(adapter(server.url, tmp_path / "flux"), tmp_path / "flux", base_model="flux", log=lines.append)
    assert node_of(server.prompts[0], "ControlNetLoader") == []
    assert any("No pose guide for Flux" in line for line in lines)


def test_history_written_after_the_success_message_is_waited_for(fake_comfy, tmp_path, monkeypatch):
    monkeypatch.setattr("assetgen.adapters.real.comfyui.HISTORY_WAIT", 0.01)
    server = fake_comfy(history_late=3)
    (path,) = render(adapter(server.url, tmp_path), tmp_path, count=1)
    assert path.is_file()
