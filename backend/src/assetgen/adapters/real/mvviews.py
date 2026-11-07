"""Other views of a subject from its front image, for multi-view meshes: MV-Adapter (SDXL) run as a
subprocess with its own Python, like UniRig (``backend/external/mv_views.py``).

Settings: ``mvadapter_root`` (the MV-Adapter folder), ``mvadapter_python`` (its Python),
``mvadapter_models`` (the folder with ``mvadapter_i2mv_sdxl.safetensors`` and
``sdxl-vae-fp16-fix/``) and ``mvadapter_sdxl`` (the SDXL checkpoint; empty: ComfyUI's
``models/checkpoints/sd_xl_base_1.0.safetensors``)."""

from __future__ import annotations

import json
import os
import subprocess
from collections import deque
from pathlib import Path

from ...config import Settings

SCRIPT = Path(__file__).resolve().parents[4] / "external" / "mv_views.py"
VIEWS = ("front", "right", "back", "left")


class ViewsError(RuntimeError):
    """A readable failure of the multi-view images."""


class MultiViewImages:
    def __init__(self, settings: Settings):
        self.root = Path(settings.mvadapter_root)
        self.python = Path(settings.mvadapter_python)
        self.models = Path(settings.mvadapter_models)
        checkpoint = settings.mvadapter_sdxl or (Path(settings.comfyui_dir) / "models" / "checkpoints"
                                                 / "sd_xl_base_1.0.safetensors" if settings.comfyui_dir else "")
        self.checkpoint = Path(checkpoint) if checkpoint else None
        self.timeout = float(settings.mvadapter_timeout)

    def ready(self) -> tuple[bool, str]:
        for label, path in (("MV-Adapter", self.root / "mvadapter"), ("its Python", self.python),
                            ("the adapter weights", self.models / "mvadapter_i2mv_sdxl.safetensors"),
                            ("the SDXL checkpoint", self.checkpoint or Path("?"))):
            if not path.exists():
                return False, f"{label} not found at {path}"
        return True, f"MV-Adapter at {self.root}"

    def render_views(self, *, front: Path, prompt: str, seed: int, out_dir: Path, progress) -> dict[str, Path]:
        """``{view: png}`` for front, right, back and left (files already there are kept)."""
        out_dir = Path(out_dir)
        files = {view: out_dir / f"{view}.png" for view in VIEWS}
        if all(p.is_file() for p in files.values()):
            return files
        ok, detail = self.ready()
        if not ok:
            raise ViewsError(f"Multi-view meshes need MV-Adapter: {detail}. See backend/README.md, \"Multi-view\".")
        out_dir.mkdir(parents=True, exist_ok=True)
        args = out_dir / "mv_views.args.json"
        result = out_dir / "mv_views.result.json"
        args.write_text(json.dumps({
            "mvadapter_root": str(self.root.resolve()), "sdxl_checkpoint": str(self.checkpoint),
            "adapter_dir": str(self.models.resolve()), "vae_dir": str((self.models / "sdxl-vae-fp16-fix").resolve()),
            "image": str(Path(front).resolve()), "prompt": prompt, "out_dir": str(out_dir.resolve()),
            "seed": int(seed) % (2 ** 31), "steps": 40, "size": 768}), encoding="utf-8")
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        tail: deque[str] = deque(maxlen=15)
        log = out_dir / "mv_views.log"
        with log.open("w", encoding="utf-8") as out:
            process = subprocess.Popen([str(self.python), str(SCRIPT), str(args), str(result)], cwd=self.root,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       text=True, encoding="utf-8", errors="replace", env=env, creationflags=flags)
            assert process.stdout is not None
            for line in process.stdout:
                out.write(line)
                if line.startswith("PROGRESS "):
                    parts = line.split(" ", 2)
                    progress(parts[2].strip() if len(parts) > 2 else "Drawing the views", float(parts[1]))
                elif line.strip():
                    tail.append(line.rstrip())
            code = process.wait(timeout=self.timeout)
        if code != 0 or not all(p.is_file() for p in files.values()):
            raise ViewsError("MV-Adapter failed (exit code %s). Last lines of mv_views.log:\n%s" % (code, "\n".join(tail)))
        return files
