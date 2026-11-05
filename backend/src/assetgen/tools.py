"""Health check of the external tools switched to ``real``.

URL tools (Qwen, ComfyUI, Hunyuan3D) answer if an HTTP GET gets any reply;
path tools (Blender, UniRig and its Python) answer if the path exists or the
command is on PATH. Nothing here starts a tool.
"""

from __future__ import annotations

import shutil
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import ADAPTER_NAMES, Settings

TOOL_NAMES = {"llm": "Qwen", "image": "ComfyUI", "mesh": "Hunyuan3D", "rig": "UniRig", "blender": "Blender"}
TIMEOUT = 2.0


def _url_check(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            return True, f"{url} answered HTTP {response.status}"
    except urllib.error.HTTPError as exc:  # the server is up, the path just isn't a page
        return True, f"{url} answered HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, f"{url} did not answer ({getattr(exc, 'reason', exc)})"


def _path_check(label: str, path: Path) -> tuple[bool, str]:
    if path.exists() or shutil.which(str(path)):
        return True, f"{label} found at {path}"
    return False, f"{label} not found at {path}"


def _check(name: str, settings: Settings) -> tuple[bool, str]:
    if name == "llm":
        return _url_check(settings.qwen_url.rstrip("/") + "/models")
    if name == "image":
        return _url_check(settings.comfyui_url.rstrip("/") + "/system_stats")
    if name == "mesh":
        return _url_check(settings.hunyuan3d_url)
    if name == "blender":
        return _path_check("Blender", settings.blender_path)
    unirig = _path_check("UniRig", settings.unirig_path)
    python = _path_check("Python", settings.python_path)
    return unirig[0] and python[0], f"{unirig[1]}; {python[1]}"


def check_tools(settings: Settings) -> list[dict]:
    """One entry per tool: ``{tool, name, mode, ok, detail}``. ``ok`` is ``None`` for mocks."""
    real = [n for n in ADAPTER_NAMES if getattr(settings.adapters, n) == "real"]
    with ThreadPoolExecutor(max_workers=len(real) or 1) as pool:
        results = dict(zip(real, pool.map(lambda n: _check(n, settings), real)))
    report = []
    for name in ADAPTER_NAMES:
        ok, detail = results.get(name, (None, "mock adapter, nothing to check"))
        report.append({"tool": name, "name": TOOL_NAMES[name], "mode": getattr(settings.adapters, name),
                       "ok": ok, "detail": detail})
    return report
