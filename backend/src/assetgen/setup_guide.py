"""What Forge needs on this machine, checked live, with the steps to install what is missing.

``GET /api/setup`` answers one entry per part (the language model, ComfyUI, Hunyuan3D, UniRig,
Blender and the optional MV-Adapter): whether it is ready, the checks behind that, the settings it
reads and numbered install steps with commands to copy. The web app's Setup page shows them; the
repository's INSTALL.md has the same steps in prose.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from .config import Settings
from .tools import TIMEOUT, _check

REPO = Path(__file__).resolve().parents[3]
PATCHES = REPO / "backend" / "patches"


def _item(label: str, ok: bool, detail: str, optional: bool = False) -> dict:
    return {"label": label, "ok": bool(ok), "detail": detail, "optional": optional}


def _restart_check(settings: Settings, tool: str) -> dict:
    spec = (settings.restart or {}).get(tool) or {}
    ok = bool(spec.get("command"))
    return _item("Restart and stop buttons", ok,
                 f"[restart.{tool}] is set" if ok else f"add [restart.{tool}] to assetgen.toml (last step)", optional=True)


def _file_check(label: str, path: Path, optional: bool = False) -> dict:
    return _item(label, path.is_file(), f"found at {path}" if path.is_file() else f"missing: {path}", optional)


def _model_listed(settings: Settings) -> dict:
    url = settings.qwen_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            ids = [m.get("id") for m in json.loads(response.read().decode("utf-8")).get("data", [])]
    except (OSError, ValueError):
        return _item("Model", False, f"could not list models at {url}")
    if settings.qwen_model in ids:
        return _item("Model", True, f"{settings.qwen_model} is available")
    return _item("Model", False, f"{settings.qwen_model} is not among {', '.join(i for i in ids if i) or 'no models'}")


def _step(text: str, command: str | None = None) -> dict:
    return {"text": text, "command": command}


def setup_report(settings: Settings, patches: str | None = None) -> list[dict]:
    """``patches``: how steps name Forge's patch folder (default: its full path on this machine)."""
    patches = patches or PATCHES.as_posix()
    comfy_dir = Path(settings.comfyui_dir) if settings.comfyui_dir else None
    from .adapters.real.mvviews import MultiViewImages  # noqa: PLC0415 - light, but keep the import local

    parts: list[dict] = []

    # ---- language model ----
    ok, detail = _check("llm", settings)
    parts.append({
        "id": "llm", "name": "Language model (LM Studio)", "required": True,
        "role": "Writes the image prompts, inspects the candidates and varies descriptions. Any OpenAI-compatible "
                "server works; LM Studio is the tested one. It needs a vision model.",
        "settings": {"qwen_url": settings.qwen_url, "qwen_model": settings.qwen_model},
        "checks": [_item("Server answers", ok, detail), *([_model_listed(settings)] if ok else []),
                   _restart_check(settings, "llm")],
        "steps": [
            _step("Install LM Studio from https://lmstudio.ai and open it once. Its command line tool `lms` comes with it."),
            _step("Download a vision model (Gemma 3 or 4, or Qwen2.5-VL; about 8 to 20 GB).",
                  "lms get google/gemma-3-12b"),
            _step("List the downloaded models to get the exact id for qwen_model.", "lms ls"),
            _step("Start the server on port 1234.", "lms server start --port 1234"),
            _step("Tell Forge where it is, in backend/assetgen.toml (create the file if it doesn't exist).",
                  'qwen_url = "http://127.0.0.1:1234/v1"\nqwen_model = "google/gemma-3-12b"'),
            _step("Optional: let the Setup page restart and stop it.",
                  '[restart.llm]\ncommand = ["lms", "server", "start", "--port", "1234"]\nstop = ["lms", "server", "stop"]'),
        ],
    })

    # ---- ComfyUI ----
    ok, detail = _check("image", settings)
    checks = [_item("Server answers", ok, detail),
              _item("Folder", bool(comfy_dir and comfy_dir.is_dir()),
                    f"comfyui_dir = {comfy_dir}" if comfy_dir else "comfyui_dir is not set (needed to check models)")]
    if comfy_dir and comfy_dir.is_dir():
        checks += [_file_check("SDXL checkpoint", comfy_dir / "models" / "checkpoints" / "sd_xl_base_1.0.safetensors"),
                   _file_check("Pose ControlNet", comfy_dir / "models" / "controlnet" / settings.pose_controlnet,
                               optional=True)]
    checks.append(_restart_check(settings, "image"))
    parts.append({
        "id": "image", "name": "ComfyUI (images)", "required": True,
        "role": "Draws the four candidate images at gate 1 with SDXL (Flux and Qwen-Image workflows are in "
                "backend/workflows).",
        "settings": {"comfyui_url": settings.comfyui_url, "comfyui_dir": settings.comfyui_dir},
        "checks": checks,
        "steps": [
            _step("Get ComfyUI.", "git clone https://github.com/comfyanonymous/ComfyUI C:/tools/ComfyUI"),
            _step("Make its Python environment and install PyTorch for your GPU (CUDA 12.8 shown; see pytorch.org "
                  "for others), then its requirements.",
                  "cd C:/tools/ComfyUI\npython -m venv venv\nvenv/Scripts/python -m pip install torch torchvision "
                  "--index-url https://download.pytorch.org/whl/cu128\nvenv/Scripts/python -m pip install -r requirements.txt"),
            _step("Download the SDXL base model (6.9 GB) into models/checkpoints.",
                  "curl -L -o models/checkpoints/sd_xl_base_1.0.safetensors https://huggingface.co/stabilityai/"
                  "stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors"),
            _step("Optional, for the pose guide (characters stand in an A-pose): the OpenPose ControlNet (2.5 GB).",
                  f"curl -L -o models/controlnet/{settings.pose_controlnet} https://huggingface.co/xinsir/"
                  "controlnet-openpose-sdxl-1.0/resolve/main/diffusion_pytorch_model.safetensors"),
            _step("Start it.", "venv/Scripts/python main.py --port 8188"),
            _step("Tell Forge where it is, in backend/assetgen.toml.",
                  'comfyui_url = "http://127.0.0.1:8188"\ncomfyui_dir = "C:/tools/ComfyUI"'),
            _step("Optional: let the Setup page restart and stop it.",
                  '[restart.image]\ncommand = ["C:/tools/ComfyUI/venv/Scripts/python.exe", "main.py", "--port", "8188"]\n'
                  'cwd = "C:/tools/ComfyUI"\nport = 8188'),
        ],
    })

    # ---- Hunyuan3D ----
    ok, detail = _check("mesh", settings)
    parts.append({
        "id": "mesh", "name": "Hunyuan3D 2.1 (meshes)", "required": True,
        "role": "Turns the picked image into a textured mesh (shape and PBR texture). Needs about 24 GB of GPU "
                "memory with texturing.",
        "settings": {"mesh_tool": settings.mesh_tool, "hunyuan3d_url": settings.hunyuan3d_url},
        "checks": [_item("Server answers", ok, detail), _restart_check(settings, "mesh")],
        "steps": [
            _step("Get Hunyuan3D 2.1.", "git clone https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1 C:/tools/Hunyuan3D-2.1"),
            _step("Install it as its README says: a Python 3.10 environment, PyTorch for your GPU, requirements.txt, "
                  "then build the two texture extensions (hy3dpaint/custom_rasterizer and "
                  "hy3dpaint/DifferentiableRenderer)."),
            _step("Apply Forge's patch: background removal that works, quality settings per style profile, job errors "
                  "that reach the app, multi-view shapes and freed GPU memory between jobs.",
                  f"cd C:/tools/Hunyuan3D-2.1\ngit apply {patches}/hunyuan3d-2.1.patch"),
            _step("Start the API server (the models download on the first run).",
                  "python api_server.py --host 127.0.0.1 --port 8081 --model_path tencent/Hunyuan3D-2.1 "
                  "--subfolder hunyuan3d-dit-v2-1"),
            _step("Tell Forge where it is, in backend/assetgen.toml.",
                  'mesh_tool = "hunyuan3d"\nhunyuan3d_url = "http://127.0.0.1:8081"'),
            _step("Optional: let the Setup page restart and stop it (a .bat or .sh that activates its environment "
                  "and runs the command above).",
                  '[restart.mesh]\ncommand = ["cmd", "/c", "C:/tools/Hunyuan3D-2.1/start_server.bat"]\n'
                  'cwd = "C:/tools/Hunyuan3D-2.1"\nport = 8081'),
        ],
    })

    # ---- UniRig ----
    ok, detail = _check("rig", settings)
    parts.append({
        "id": "rig", "name": "UniRig (skeletons)", "required": True,
        "role": "Predicts each character's skeleton and skin weights at the rig stage. Forge runs it with its own "
                "Python; no server.",
        "settings": {"unirig_path": str(settings.unirig_path), "python_path": str(settings.python_path)},
        "checks": [_item("Folder and Python", ok, detail)],
        "steps": [
            _step("Get UniRig.", "git clone https://github.com/VAST-AI-Research/UniRig C:/tools/UniRig"),
            _step("Make its Python 3.11 environment and install it as its README says (PyTorch for your GPU, "
                  "requirements.txt, spconv, torch_scatter and torch_cluster for that PyTorch)."),
            _step("On Windows, apply Forge's patch (paths and a float type UniRig gets wrong there).",
                  f"cd C:/tools/UniRig\ngit apply {patches}/unirig-windows.patch"),
            _step("Tell Forge where it is, in backend/assetgen.toml (its checkpoints download on the first run).",
                  'unirig_path = "C:/tools/UniRig"\npython_path = "C:/tools/UniRig/.venv/Scripts/python.exe"'),
        ],
    })

    # ---- Blender ----
    ok, detail = _check("blender", settings)
    parts.append({
        "id": "blender", "name": "Blender 4.5 LTS", "required": True,
        "role": "Cleans meshes and makes LODs, skins and animates them, fits gear, renders sprites and writes the "
                "exports. Runs in the background; no server.",
        "settings": {"blender_path": str(settings.blender_path)},
        "checks": [_item("Blender", ok, detail)],
        "steps": [
            _step("Install Blender 4.5 LTS from https://www.blender.org/download/lts/ (or `winget install "
                  "BlenderFoundation.Blender`)."),
            _step("Tell Forge where it is, in backend/assetgen.toml.",
                  'blender_path = "C:/Program Files/Blender Foundation/Blender 4.5/blender.exe"'),
        ],
    })

    # ---- MV-Adapter (optional) ----
    ready, detail = MultiViewImages(settings).ready()
    parts.append({
        "id": "multiview", "name": "MV-Adapter (optional: multi-view meshes)", "required": False,
        "role": "Draws the back and sides of the picked image so Hunyuan3D builds the shape from four views "
                "(the mesh setting multi_view). Off by default.",
        "settings": {"mvadapter_root": str(settings.mvadapter_root), "mvadapter_python": str(settings.mvadapter_python),
                     "mvadapter_models": str(settings.mvadapter_models)},
        "checks": [_item("MV-Adapter", ready, detail)],
        "steps": [
            _step("Get MV-Adapter.", "git clone https://github.com/huanngzh/MV-Adapter C:/tools/MV-Adapter"),
            _step("Make its Python environment with PyTorch for your GPU, its requirements and the extras Forge uses.",
                  "cd C:/tools/MV-Adapter\npython -m venv .venv\n.venv/Scripts/python -m pip install torch torchvision "
                  "--index-url https://download.pytorch.org/whl/cu128\n.venv/Scripts/python -m pip install -r "
                  "requirements.txt rembg onnxruntime \"triton-windows<3.7\""),
            _step("Download the adapter weights (3.6 GB) and the fp16 SDXL VAE into one folder.",
                  "huggingface-cli download huanngzh/mv-adapter mvadapter_i2mv_sdxl.safetensors --local-dir "
                  "C:/tools/MV-Adapter-models\nhuggingface-cli download madebyollin/sdxl-vae-fp16-fix --local-dir "
                  "C:/tools/MV-Adapter-models/sdxl-vae-fp16-fix"),
            _step("The patched Hunyuan3D server downloads its multi-view model (tencent/Hunyuan3D-2mv, 5 GB) on the "
                  "first multi-view job."),
            _step("Tell Forge where it is, in backend/assetgen.toml (it reuses ComfyUI's SDXL checkpoint).",
                  'mvadapter_root = "C:/tools/MV-Adapter"\nmvadapter_python = "C:/tools/MV-Adapter/.venv/Scripts/python.exe"\n'
                  'mvadapter_models = "C:/tools/MV-Adapter-models"'),
        ],
    })

    for part in parts:
        part["ok"] = all(c["ok"] for c in part["checks"] if not c["optional"])
    return parts


# ---- INSTALL.md ---------------------------------------------------------------

INTRO = """# Installing Forge

Forge runs on one machine with an NVIDIA GPU (24 GB or more; 32 GB for multi-view meshes) on
Windows or Linux. It has two parts: the Forge app (a Python backend and a web front end) and the
tools it drives (a language model server, ComfyUI, Hunyuan3D, UniRig and Blender). The app's
**Setup** page (`/setup`) checks each tool live and shows these same steps, with buttons to copy
the commands and to restart or stop the tools.

This file is generated from `backend/src/assetgen/setup_guide.py`
(`cd backend; uv run python -m assetgen.setup_guide > ../INSTALL.md`).

## 0. The Forge app

1. Install [git](https://git-scm.com), [uv](https://docs.astral.sh/uv/) (it brings Python 3.12)
   and [Node.js](https://nodejs.org) 20 or later.
2. Get Forge and build the web app.

   ```
   git clone <this repository> forge
   cd forge/frontend
   npm ci
   npm run build
   ```

3. Start it (the first run installs the Python packages).

   ```
   cd ../backend
   uv run assetgen
   ```

4. Open http://127.0.0.1:8000/setup. Each part below starts red: install them in order, put their
   settings in `backend/assetgen.toml` (TOML, one `key = value` per line; or environment variables
   named `ASSETGEN_<KEY>`), restart Forge and press **Check again**.

The paths below (`C:/tools/...`) are examples. On Linux use your own paths and `venv/bin/python`
instead of `venv/Scripts/python`.
"""

OUTRO = """## Using Forge from scripts

Everything the web app does goes through Forge's HTTP API, documented at
http://127.0.0.1:8000/docs (OpenAPI at `/openapi.json`). `POST /api/tools/{tool}/restart` and
`/stop` restart or stop a tool; `GET /api/setup` returns this guide with live checks.
"""


def install_markdown() -> str:
    """INSTALL.md: the intro, then each part's role and steps (commands as code blocks)."""
    lines = [INTRO]
    for number, part in enumerate(setup_report(Settings(), patches="<forge>/backend/patches"), start=1):
        lines.append(f"## {number}. {part['name']}\n")
        lines.append(part["role"] + "\n")
        for index, step in enumerate(part["steps"], start=1):
            lines.append(f"{index}. {step['text']}")
            if step["command"]:
                code = "\n".join("   " + line for line in step["command"].splitlines())
                lines.append(f"\n   ```\n{code}\n   ```\n")
            else:
                lines.append("")
        lines.append("")
    lines.append(OUTRO)
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stdout.write(install_markdown())
