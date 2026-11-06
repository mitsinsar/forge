"""Hunyuan3D 2.1 through its ``api_server.py`` (default port 8081).

1. The picked candidate's plain background is cut out here (``rembg`` when
   installed, else a flood fill from the border) and sent as an RGBA PNG.
2. ``POST /send`` with shape + texture starts a job and answers its ``uid``,
   which is saved beside the output so a restarted app polls the same job
   instead of starting a new one.
3. ``GET /status/{uid}`` answers ``processing``, then ``texturing``, then
   ``completed`` with the GLB as base64 (or ``error`` with a message).
4. The GLB as Hunyuan3D made it is kept as ``hunyuan3d.glb``; its albedo is
   scaled to the profile's texture size, optionally de-lit, saved as
   ``texture.png`` and put back into the GLB written to ``out_path``.
"""

from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

from PIL import Image

from ... import imaging
from ...config import Settings
from .mesh_common import MeshError, finish_textured_glb, quality_params
from .mesh_common import is_oom as _is_oom
from .mesh_common import write_atomic as _write_atomic

JOB_FILE = "hunyuan3d_job.json"
DOWNLOAD_FILE = "hunyuan3d.glb"
CUTOUT_FILE = "input_cutout.png"
REQUEST_TIMEOUT = 60.0


class Hunyuan3DMesh:

    def __init__(self, settings: Settings):
        self.url = settings.hunyuan3d_url.rstrip("/")
        self.timeout = float(settings.hunyuan3d_timeout)
        self.poll = float(settings.hunyuan3d_poll)

    # ---- HTTP -----------------------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if _is_oom(detail):
                raise self._oom(detail) from exc
            raise MeshError(f"Hunyuan3D at {self.url} answered HTTP {exc.code} to {method} {path}: {detail}") from exc
        except TimeoutError as exc:
            raise MeshError(f"Hunyuan3D at {self.url} did not answer {method} {path} within "
                            f"{REQUEST_TIMEOUT:.0f} s.") from exc
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise MeshError(f"Could not reach Hunyuan3D at {self.url} ({reason}). Is api_server.py running on "
                            "the Windows machine, and is hunyuan3d_url set to its address?") from exc
        except ValueError as exc:
            raise MeshError(f"Hunyuan3D at {self.url} did not answer JSON to {method} {path}.") from exc

    def _oom(self, detail: str) -> MeshError:
        return MeshError("Hunyuan3D ran out of GPU memory. Close other GPU programs (ComfyUI should be "
                         "unloaded by now), or restart api_server.py with --low_vram_mode, then retry. "
                         f"Server said: {detail.strip()[:300]}")

    # ---- the job --------------------------------------------------------------

    def _submit(self, cutout: Path, has_alpha: bool, hints: dict, quality: dict) -> str:
        seed = zlib.crc32(f"{hints.get('asset_id', '')}:{hints.get('attempt', 1)}".encode()) % (2 ** 32)
        body = {
            "image": base64.b64encode(cutout.read_bytes()).decode("ascii"),
            # The cut-out is already transparent; let Hunyuan3D's own rembg run only if ours found nothing.
            "remove_background": not has_alpha,
            "texture": True,
            "seed": seed,
            **quality_params(quality),
        }
        answer = self._request("POST", "/send", body)
        uid = answer.get("uid")
        if not isinstance(uid, str) or not uid:
            raise MeshError(f"Hunyuan3D at {self.url} did not answer a job id to POST /send: {answer}")
        return uid

    def _load_job(self, job_file: Path) -> dict | None:
        try:
            job = json.loads(job_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if job.get("url") != self.url or time.time() - float(job.get("sent_at", 0)) > self.timeout:
            return None  # another server, or too old to still be running
        return job

    def _wait(self, job: dict, job_file: Path, progress) -> bytes:
        uid, polls = job["uid"], 0
        while True:
            answer = self._request("GET", f"/status/{uid}")
            status = answer.get("status")
            if status == "completed":
                try:
                    return base64.b64decode(answer["model_base64"], validate=True)
                except (KeyError, ValueError) as exc:
                    raise MeshError("Hunyuan3D said the job is done but sent no readable GLB.") from exc
            if status == "error":
                job_file.unlink(missing_ok=True)
                message = str(answer.get("message") or answer.get("error") or "no message")
                if _is_oom(message):
                    raise self._oom(message)
                raise MeshError(f"Hunyuan3D failed: {message}")
            elapsed = time.time() - float(job["sent_at"])
            if elapsed > self.timeout:
                job_file.unlink(missing_ok=True)  # a retry starts a new job
                raise MeshError(
                    f"Hunyuan3D did not finish within {self.timeout:.0f} s (last status: {status!r}). Check the "
                    "api_server.py console: a crash there (often out of GPU memory) leaves the job 'processing'. "
                    "Raise hunyuan3d_timeout if the machine is just slow.")
            polls += 1
            if status == "texturing":
                progress("Painting the texture", min(0.85, 0.55 + 0.01 * polls))
            else:
                progress("Building the shape", min(0.5, 0.12 + 0.01 * polls))
            time.sleep(self.poll)

    # ---- the adapter ------------------------------------------------------------

    def image_to_mesh(self, *, image, texture_size, out_path, progress, hints, delight=True, quality=None):
        out_path = Path(out_path)
        directory = out_path.parent
        download = directory / DOWNLOAD_FILE
        background = "kept"
        if not download.is_file():
            cutout = directory / CUTOUT_FILE
            if cutout.is_file():
                with Image.open(cutout) as cut:
                    has_alpha = cut.mode == "RGBA"
            else:
                progress("Removing the background", 0.02)
                with Image.open(image) as source:
                    cut, background = imaging.remove_background(source)
                has_alpha = background != "none"
                buffer = io.BytesIO()
                (cut if has_alpha else cut.convert("RGB")).save(buffer, format="PNG")
                _write_atomic(cutout, buffer.getvalue())

            job_file = directory / JOB_FILE
            job = self._load_job(job_file)
            if job:
                progress("Resuming the Hunyuan3D job", 0.1)
            else:
                progress("Sending the image to Hunyuan3D", 0.05)
                job = {"uid": self._submit(cutout, has_alpha, hints, quality or {}), "url": self.url,
                       "sent_at": time.time()}
                job_file.write_text(json.dumps(job), encoding="utf-8")
            glb = self._wait(job, job_file, progress)
            _write_atomic(download, glb)
            job_file.unlink(missing_ok=True)

        result = finish_textured_glb(
            download=download, out_path=out_path, texture_size=texture_size, delight=delight, progress=progress,
            tool="Hunyuan3D", source="hunyuan3d-2.1",
            no_texture_hint="Is the texture model (hunyuan3d-paintpbr-v2-1) installed on the server?")
        return {**result, "background": background}
