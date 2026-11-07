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

    [restart.llm]            # LM Studio: its own commands, the app itself stays open
    command = ["lms", "server", "start", "--port", "1234"]
    stop = ["lms", "server", "stop"]

A restart runs ``stop`` (or stops whatever listens on ``port``), starts ``command`` detached (its
output goes to ``data/logs/<tool>.log``) and waits until the tool's health check answers. A stop
does the first half and waits until it no longer answers. When a stage fails because its tool did
not answer or ran out of memory, the pipeline restarts the tool once and runs the stage again.
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


def stoppable(settings: Settings, tool: str) -> bool:
    spec = (settings.restart or {}).get(tool) or {}
    return bool(spec.get("stop") or spec.get("port"))


def _run_stop(spec: dict) -> None:
    """The tool's own ``stop`` command, else stopping whatever listens on its ``port``."""
    if spec.get("stop"):
        stop = spec["stop"] if isinstance(spec["stop"], list) else [spec["stop"]]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        subprocess.run([str(c) for c in stop], cwd=spec.get("cwd") or None, capture_output=True, timeout=60,
                       check=False, creationflags=flags)
    elif spec.get("port"):
        _stop_port(int(spec["port"]))


def stop_tool(settings: Settings, tool: str, wait: float = 30.0, poll: float = 1.0) -> str:
    """Stops ``tool`` and waits until it no longer answers; returns what happened."""
    spec = (settings.restart or {}).get(tool) or {}
    if not (spec.get("stop") or spec.get("port")):
        raise RuntimeError(f"no way to stop {tool}: add a stop command or port to [restart.{tool}] in assetgen.toml")
    _run_stop(spec)
    started = time.monotonic()
    while time.monotonic() - started < wait:
        ok, _ = _check(tool, settings)
        if not ok:
            return "stopped"
        time.sleep(poll)
    raise RuntimeError(f"{tool} still answers {wait:.0f} s after being stopped")


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


def _quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _launch(command: list[str], cwd: str | None, log: Path) -> None:
    """Starts ``command`` on its own: not Forge's child, so restarting or closing Forge (even by
    killing its process tree) leaves the tool running. On Windows a short-lived PowerShell starts
    it and exits; elsewhere it gets its own session."""
    if os.name == "nt":
        arguments = ",".join(_quote(a) for a in command[1:]) or "' '"
        script = (f"Start-Process -FilePath {_quote(command[0])} -ArgumentList @({arguments}) -WindowStyle Hidden "
                  f"-RedirectStandardOutput {_quote(log)} -RedirectStandardError {_quote(log.with_suffix('.err.log'))}"
                  + (f" -WorkingDirectory {_quote(cwd)}" if cwd else ""))
        # No pipes: the tool would inherit them and keep them open, and this would wait for it.
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=60, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if done.returncode != 0:
            raise RuntimeError(f"could not start {command[0]} (is the path in [restart] right?)")
        return
    with log.open("ab") as out:
        subprocess.Popen(command, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True)


def restart_tool(settings: Settings, tool: str, wait: float = 240.0, poll: float = 2.0) -> str:
    """Restarts ``tool`` and waits until it answers; returns what happened. Raises when it can't."""
    spec = (settings.restart or {}).get(tool) or {}
    command = spec.get("command")
    if not command:
        raise RuntimeError(f"no restart command for {tool}: add [restart.{tool}] to assetgen.toml")
    if isinstance(command, str):
        command = [command]
    if spec.get("stop") or spec.get("port"):
        _run_stop(spec)
        time.sleep(1.0)
    logs = (Path(settings.data_dir) / "logs").resolve()
    logs.mkdir(parents=True, exist_ok=True)
    _launch([str(c) for c in command], spec.get("cwd") or None, logs / f"{tool}.log")
    started = time.monotonic()
    while time.monotonic() - started < wait:
        ok, detail = _check(tool, settings)
        if ok:
            return f"restarted, answered after {round(time.monotonic() - started)} s"
        time.sleep(poll)
    raise RuntimeError(f"{tool} was restarted but did not answer within {wait:.0f} s (see data/logs/{tool}.log)")
