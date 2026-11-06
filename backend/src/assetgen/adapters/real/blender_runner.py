"""Runs one of the scripts in ``backend/blender_scripts/`` in headless Blender.

The command is a list (no shell), so paths with spaces work on Windows too::

    blender -b --factory-startup --python-exit-code 1 -P <script> -- <args.json> <result.json>

Blender's whole output is saved as the stage's log; ``PROGRESS <fraction> <step>``
lines are passed to the progress callback. A failure raises ``BlenderError``
whose message ends with the log's last lines.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[4] / "blender_scripts"
TAIL_LINES = 12
TIMEOUT = 30 * 60  # seconds; a hung Blender must not block the single worker forever
PROGRESS_LINE = re.compile(r"^PROGRESS\s+([0-9.]+)\s+(.*)$")


class BlenderError(RuntimeError):
    """Blender could not be started or the script failed; the message is meant for the user."""


def resolve_blender(path: Path) -> str:
    """The Blender executable: the configured path, or the command found on PATH."""
    if path.is_file():
        return str(path)
    found = shutil.which(str(path))
    if found:
        return found
    raise BlenderError(f"Blender was not found at {path}. Set blender_path in assetgen.toml to the Blender "
                       "executable (for example C:\\Program Files\\Blender Foundation\\Blender 4.5\\blender.exe).")


def run_script(blender: Path, script: str, args: dict, *, work_dir: Path, log_path: Path,
               progress: Callable[[str, float], None], timeout: float = TIMEOUT) -> dict:
    """Runs ``blender_scripts/<script>`` with ``args`` and returns the JSON result it wrote."""
    executable = resolve_blender(blender)
    script_path = SCRIPTS_DIR / script
    # Blender runs inside work_dir, so every path it is given must be absolute: with a relative
    # data_dir (the default "data") a relative args path would be looked up inside work_dir again.
    work_dir = Path(work_dir).resolve()
    log_path = Path(log_path).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(script).stem
    args_path = work_dir / f"{stem}.args.json"
    result_path = work_dir / f"{stem}.result.json"
    result_path.unlink(missing_ok=True)
    args_path.write_text(json.dumps(args, indent=2), encoding="utf-8")
    command = [executable, "-b", "--factory-startup", "--python-exit-code", "1", "-P", str(script_path),
               "--", str(args_path), str(result_path)]

    log_path.parent.mkdir(parents=True, exist_ok=True)
    tail: deque[str] = deque(maxlen=TAIL_LINES)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                   cwd=work_dir, creationflags=flags)
    except OSError as exc:
        raise BlenderError(f"Could not start Blender at {executable}: {exc}") from exc
    timed_out = threading.Event()

    def kill() -> None:
        timed_out.set()
        process.kill()

    timer = threading.Timer(timeout, kill)
    timer.start()
    try:
        with log_path.open("w", encoding="utf-8") as log:
            log.write(" ".join(f'"{part}"' if " " in part else part for part in command) + "\n\n")
            assert process.stdout is not None
            for line in process.stdout:
                log.write(line)
                log.flush()
                text = line.rstrip()
                if text:
                    tail.append(text)
                match = PROGRESS_LINE.match(text)
                if match:
                    try:
                        progress(match.group(2), float(match.group(1)))
                    except ValueError:
                        pass
        code = process.wait()
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()

    if code != 0 or not result_path.is_file():
        reason = (f"timed out after {int(timeout)} s" if timed_out.is_set()
                  else f"exited with code {code}" if code != 0 else "wrote no result")
        last = "\n".join(line for line in tail if not line.startswith("PROGRESS "))
        raise BlenderError(f"Blender {stem} {reason}. Last lines of {log_path.name}:\n{last}")
    try:
        return json.loads(result_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise BlenderError(f"Blender {stem} wrote an unreadable result ({exc}).") from exc

