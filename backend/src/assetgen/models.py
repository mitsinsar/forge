"""The models a style profile can pick for each step, and whether each is installed.

* text: the language model (LM Studio's ``/v1/models``; embedding models left out). Empty in a
  profile means ``qwen_model`` from the settings.
* image: the base models Forge has ComfyUI workflows for, installed when ComfyUI has every model
  file its workflow loads (``models/<folder>`` under ``comfyui_dir``).
* mesh: the image-to-3D models (``defaults.MESH_MODELS``), available when their tool answers.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from .config import Settings
from .defaults import MESH_MODELS
from .tools import TIMEOUT, _url_check

IMAGE_LABELS = {"sdxl": "SDXL 1.0", "flux": "Flux.1 dev (non-commercial licence)", "qwen-image": "Qwen-Image (Apache 2.0)"}
WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"


def _text_models(settings: Settings) -> list[dict]:
    try:
        with urllib.request.urlopen(settings.qwen_url.rstrip("/") + "/models", timeout=TIMEOUT) as response:
            ids = [m.get("id") for m in json.loads(response.read().decode("utf-8")).get("data", [])]
    except (OSError, ValueError):
        ids = []
    found = [{"id": i, "label": i, "installed": True} for i in ids if i and "embed" not in i.lower()]
    if settings.qwen_model not in ids:
        found.insert(0, {"id": settings.qwen_model, "label": settings.qwen_model, "installed": False})
    return found


def _image_models(settings: Settings) -> list[dict]:
    from .adapters.real.comfyui import CLASS_FOLDERS, MODEL_FOLDERS  # noqa: PLC0415

    comfy = Path(settings.comfyui_dir) if settings.comfyui_dir else None
    found = []
    for workflow in sorted(WORKFLOWS.glob("*.json")):
        name = workflow.stem
        needed = []
        for node in json.loads(workflow.read_text(encoding="utf-8")).values():
            for key, value in (node.get("inputs") or {}).items():
                folder = MODEL_FOLDERS.get(key) or CLASS_FOLDERS.get(node.get("class_type", ""))
                if folder and isinstance(value, str) and value.endswith((".safetensors", ".ckpt", ".pt", ".bin")):
                    needed.append((folder, value))
        missing = [f"{folder}/{file}" for folder, file in needed
                   if comfy is None or not any((comfy / "models" / d / file).is_file()
                                               for d in (folder, "unet" if folder == "diffusion_models" else folder,
                                                         "clip" if folder == "text_encoders" else folder))]
        found.append({"id": name, "label": IMAGE_LABELS.get(name, name), "installed": not missing,
                      "missing": missing})
    return found


def _mesh_models(settings: Settings) -> list[dict]:
    urls = {"hunyuan3d": settings.hunyuan3d_url.rstrip("/") + "/health",
            "modly": settings.modly_url.rstrip("/") + "/model/all"}
    return [{"id": model, "label": info["label"], "tool": info["tool"],
             "installed": _url_check(urls[info["tool"]])[0]} for model, info in MESH_MODELS.items()]


def model_choices(settings: Settings) -> dict:
    return {"text": _text_models(settings), "text_default": settings.qwen_model,
            "image": _image_models(settings), "mesh": _mesh_models(settings)}
