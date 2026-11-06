"""Health check of the external tools.

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
        base = settings.modly_url if settings.mesh_tool == "modly" else settings.hunyuan3d_url
        return _url_check(base.rstrip("/") + "/health")
    if name == "blender":
        return _path_check("Blender", settings.blender_path)
    unirig = _path_check("UniRig", settings.unirig_path)
    python = _path_check("Python", settings.python_path)
    return unirig[0] and python[0], f"{unirig[1]}; {python[1]}"


def check_tools(settings: Settings) -> list[dict]:
    """One entry per tool: ``{tool, name, ok, detail}``. Rigging is never ok until its adapter exists."""
    from .adapters.real import REAL_ADAPTERS

    with ThreadPoolExecutor(max_workers=len(ADAPTER_NAMES)) as pool:
        results = dict(zip(ADAPTER_NAMES, pool.map(lambda n: _check(n, settings), ADAPTER_NAMES)))
    report = []
    for name in ADAPTER_NAMES:
        ok, detail = results[name]
        if name not in REAL_ADAPTERS:
            ok, detail = False, f"adapter not built yet (ticket #11); {detail}"
        label = "Modly" if name == "mesh" and settings.mesh_tool == "modly" else TOOL_NAMES[name]
        report.append({"tool": name, "name": label, "ok": ok, "detail": detail})
    return report
