"""Restarting a tool that crashed or ran out of GPU memory.

A tool can be restarted when ``assetgen.toml`` says how to start it::

    [restart.image]          # ComfyUI
    command = ["C:/Users/me/ComfyUI/venv/Scripts/python.exe", "main.py", "--port", "8188"]
    cwd = "C:/Users/me/ComfyUI"
    port = 8188

    [restart.mesh]           # Hunyuan3D 2.1
    command = ["cmd", "/c", "C:/tools/Hunyuan3D-2.1/start_server.bat"]
    cwd = "C:/tools/Hunyuan3D-2.1"
    port = 8081

A restart stops whatever listens on ``port``, starts ``command`` detached (its output goes to
``data/logs/<tool>.log``) and waits until the tool's health check answers. When a stage fails
because its tool did not answer or ran out of memory, the pipeline restarts the tool once and
runs the stage again.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from .config import Settings
from .tools import _check

# Errors that mean "the tool is down or broken", not "this input is bad".
TOOL_TROUBLE = re.compile(r"could not reach|did not answer|unreachable|connection (refused|reset|aborted)|"
                          r"out of memory|cuda error|cudnn|remote end closed|timed out", re.I)
STAGE_TOOL = {"prompt": "image", "mesh": "mesh"}


def restartable(settings: Settings, tool: str) -> bool:
    spec = (settings.restart or {}).get(tool) or {}
    return bool(spec.get("command"))


def _stop_port(port: int) -> None:
    if os.name == "nt":
        script = (f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
                  "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }")
        subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=30, check=False)
    else:
        found = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True,
                               check=False)
        for pid in found.stdout.split():
            subprocess.run(["kill", "-9", pid], check=False)


def restart_tool(settings: Settings, tool: str, wait: float = 240.0, poll: float = 2.0) -> str:
    """Restarts ``tool`` and waits until it answers; returns what happened. Raises when it can't."""
    spec = (settings.restart or {}).get(tool) or {}
    command = spec.get("command")
    if not command:
        raise RuntimeError(f"no restart command for {tool}: add [restart.{tool}] to assetgen.toml")
    if isinstance(command, str):
        command = [command]
    if spec.get("port"):
        _stop_port(int(spec["port"]))
        time.sleep(1.0)
    logs = Path(settings.data_dir) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = (logs / f"{tool}.log").open("ab")
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    subprocess.Popen([str(c) for c in command], cwd=spec.get("cwd") or None, stdout=log, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, creationflags=flags, start_new_session=os.name != "nt")
    started = time.monotonic()
    while time.monotonic() - started < wait:
        ok, detail = _check(tool, settings)
        if ok:
            return f"restarted, answered after {round(time.monotonic() - started)} s"
        time.sleep(poll)
    raise RuntimeError(f"{tool} was restarted but did not answer within {wait:.0f} s (see data/logs/{tool}.log)")
