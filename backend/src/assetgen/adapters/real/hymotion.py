"""Skill animations: HY-Motion 1.0 turns a description into body motion, run as a subprocess with its
own Python like MV-Adapter and UniRig (``backend/external/hy_motion.py``).

Settings: ``hymotion_path`` (the HY-Motion-1.0 folder, with ``ckpts/``), ``hymotion_python`` (its
Python) and ``hymotion_model`` (the model folder in it). The model and its text encoders need about
26 GB of GPU memory, so the worker frees the others first (one model at a time)."""

from __future__ import annotations

import json
import os
import subprocess
from collections import deque
from pathlib import Path

from ...config import Settings

SCRIPT = Path(__file__).resolve().parents[4] / "external" / "hy_motion.py"


class MotionError(RuntimeError):
    """A readable failure of the motion model."""


class HYMotion:
    def __init__(self, settings: Settings):
        self.root = Path(settings.hymotion_path)
        self.python = Path(settings.hymotion_python)
        self.model = self.root / settings.hymotion_model
        self.timeout = float(settings.hymotion_timeout)

    def ready(self) -> tuple[bool, str]:
        for label, path in (("HY-Motion", self.root / "hymotion"), ("its Python", self.python),
                            ("the model", self.model / "latest.ckpt"),
                            ("the Qwen3-8B text encoder", self.root / "ckpts" / "Qwen3-8B" / "config.json"),
                            ("the CLIP text encoder", self.root / "ckpts" / "clip-vit-large-patch14" / "config.json")):
            if not path.exists():
                return False, f"{label} not found at {path}"
        return True, f"HY-Motion at {self.root}"

    def generate(self, *, motions: list[dict], work_dir: Path, progress) -> list[Path]:
        """Body motion for each ``{"prompt", "duration", "seed", "out"}`` (``out``: the motion JSON to
        write; ones already there are kept). One run loads the model once for all of them."""
        todo = [m for m in motions if not Path(m["out"]).is_file()]
        if todo:
            ok, detail = self.ready()
            if not ok:
                raise MotionError(f"Skill animations need HY-Motion: {detail}. See backend/README.md, \"HY-Motion\".")
            work_dir = Path(work_dir).resolve()
            work_dir.mkdir(parents=True, exist_ok=True)
            args, result = work_dir / "hy_motion.args.json", work_dir / "hy_motion.result.json"
            args.write_text(json.dumps({
                "model": str(self.model.resolve()), "cfg_scale": 5.0,
                "motions": [{**m, "out": str(Path(m["out"]).resolve())} for m in todo]}), encoding="utf-8")
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "USE_HF_MODELS": "0",
                   "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            tail: deque[str] = deque(maxlen=15)
            log = work_dir / "hy_motion.log"
            with log.open("w", encoding="utf-8") as out:
                process = subprocess.Popen([str(self.python), str(SCRIPT), str(args), str(result)], cwd=self.root,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                           text=True, encoding="utf-8", errors="replace", env=env, creationflags=flags)
                assert process.stdout is not None
                for line in process.stdout:
                    out.write(line)
                    if line.startswith("PROGRESS "):
                        parts = line.split(" ", 2)
                        progress(parts[2].strip() if len(parts) > 2 else "Moving", float(parts[1]))
                    elif line.strip():
                        tail.append(line.rstrip())
                code = process.wait(timeout=self.timeout)
            missing = [m["out"] for m in todo if not Path(m["out"]).is_file()]
            if code != 0 or missing:
                raise MotionError("HY-Motion failed (exit code %s). Last lines of hy_motion.log:\n%s"
                                  % (code, "\n".join(tail)))
        return [Path(m["out"]) for m in motions]
