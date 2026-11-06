"""Modly (https://github.com/lightningpixel/modly) as the image-to-mesh tool.

Modly is a desktop app whose local FastAPI server (default port 8765) runs
image-to-3D models installed as extensions, such as Hunyuan3D 2 Mini Turbo.

1. ``POST /workflow-runs/from-image`` (multipart: ``image``, ``model_id``,
   ``collection``, ``params`` as JSON) starts a run and answers its ``run_id``,
   which is saved beside the output so a restarted app polls the same run.
2. ``GET /workflow-runs/{run_id}`` answers ``pending``/``running`` with a
   0-100 ``progress`` and a ``step``, then ``done`` with the result's
   workspace path, or ``error``/``cancelled``.
3. ``GET /export/glb?path=<workspace path>`` downloads the GLB, kept as
   ``modly.glb``; the texture is then finished as for Hunyuan3D.

Modly removes the background itself, so the candidate is sent as it is.
Texture needs the model's texture extensions built (see backend/README.md).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from pathlib import Path

from ...config import Settings
from .mesh_common import MeshError, finish_textured_glb, is_oom, quality_params, write_atomic

RUN_FILE = "modly_run.json"
DOWNLOAD_FILE = "modly.glb"
COLLECTION = "assetgen"
REQUEST_TIMEOUT = 60.0
DOWNLOAD_TIMEOUT = 300.0


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, data, mime) in files.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                     f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _last_line(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return lines[-1] if lines else "no message"


class ModlyMesh:

    def __init__(self, settings: Settings):
        self.url = settings.modly_url.rstrip("/")
        self.model = settings.modly_model
        self.timeout = float(settings.modly_timeout)
        self.poll = float(settings.modly_poll)
        try:
            self.extra_params = json.loads(settings.modly_params) if settings.modly_params.strip() else {}
        except ValueError as exc:
            raise ValueError(f"modly_params is not valid JSON: {exc}") from exc
        if not isinstance(self.extra_params, dict):
            raise ValueError("modly_params must be a JSON object, such as {\"num_inference_steps\": 5}")

    # ---- HTTP -----------------------------------------------------------------

    def _open(self, request: urllib.request.Request, timeout: float, what: str) -> bytes:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if is_oom(detail):
                raise self._oom(detail) from exc
            raise MeshError(f"Modly at {self.url} answered HTTP {exc.code} to {what}: {detail}") from exc
        except TimeoutError as exc:
            raise MeshError(f"Modly at {self.url} did not answer {what} within {timeout:.0f} s.") from exc
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise MeshError(f"Could not reach Modly at {self.url} ({reason}). Is the Modly app open, and is "
                            "modly_url set to its server?") from exc

    def _json(self, request: urllib.request.Request, what: str) -> dict:
        raw = self._open(request, REQUEST_TIMEOUT, what)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise MeshError(f"Modly at {self.url} did not answer JSON to {what}.") from exc

    def _oom(self, detail: str) -> MeshError:
        return MeshError("Modly ran out of GPU memory. Close other GPU programs (ComfyUI should be unloaded by "
                         f"now) and retry. Modly said: {_last_line(detail)[:300]}")

    # ---- the run --------------------------------------------------------------

    def _submit(self, image: Path, hints: dict, quality: dict) -> str:
        seed = zlib.crc32(f"{hints.get('asset_id', '')}:{hints.get('attempt', 1)}".encode()) % (2 ** 31)
        params = {"enable_texture": True, "seed": seed, **self.extra_params, **quality_params(quality)}
        body, content_type = _multipart(
            {"model_id": self.model, "collection": COLLECTION, "params": json.dumps(params)},
            {"image": (Path(image).name, Path(image).read_bytes(), "image/png")})
        request = urllib.request.Request(self.url + "/workflow-runs/from-image", data=body, method="POST",
                                         headers={"Content-Type": content_type})
        answer = self._json(request, "POST /workflow-runs/from-image")
        run_id = answer.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise MeshError(f"Modly at {self.url} did not answer a run id: {answer}")
        return run_id

    def _load_run(self, run_file: Path) -> dict | None:
        try:
            run = json.loads(run_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if run.get("url") != self.url or run.get("model") != self.model \
                or time.time() - float(run.get("sent_at", 0)) > self.timeout:
            return None  # another server or model, or too old to still be running
        return run

    def _wait(self, run: dict, run_file: Path, progress) -> str:
        """Polls the run; answers the workspace path of the finished GLB."""
        run_id = run["run_id"]
        while True:
            answer = self._json(urllib.request.Request(f"{self.url}/workflow-runs/{run_id}"),
                                f"GET /workflow-runs/{run_id}")
            status = answer.get("status")
            if status == "done":
                path = (answer.get("scene_candidate") or {}).get("workspace_path")
                if not path and isinstance(answer.get("output_url"), str):
                    path = answer["output_url"].removeprefix("/workspace/")
                if not path:
                    raise MeshError(f"Modly finished but named no output file: {answer}")
                return path
            if status in ("error", "cancelled"):
                run_file.unlink(missing_ok=True)  # a retry starts a new run
                error = str(answer.get("error") or status)
                if is_oom(error):
                    raise self._oom(error)
                if "C++ extensions for texture generation are not compiled" in error:
                    raise MeshError("Modly made the shape but could not paint the texture: the model's texture "
                                    "extensions are not built (see backend/README.md, Modly). "
                                    f"Modly said: {_last_line(error)[:300]}")
                raise MeshError(f"Modly failed at '{answer.get('step') or 'unknown step'}': {_last_line(error)[:500]}")
            elapsed = time.time() - float(run["sent_at"])
            if elapsed > self.timeout:
                run_file.unlink(missing_ok=True)
                raise MeshError(f"Modly did not finish within {self.timeout:.0f} s (last step: "
                                f"{answer.get('step')!r}). Check the Modly app; raise modly_timeout if the "
                                "machine is just slow (the first run downloads the model).")
            fraction = max(0.0, min(1.0, float(answer.get("progress") or 0) / 100))
            progress(f"Modly: {str(answer.get('step') or status or 'waiting').rstrip('.… ')}", 0.05 + 0.8 * fraction)
            time.sleep(self.poll)

    def _download(self, workspace_path: str) -> bytes:
        query = urllib.parse.urlencode({"path": workspace_path})
        request = urllib.request.Request(f"{self.url}/export/glb?{query}")
        return self._open(request, DOWNLOAD_TIMEOUT, "GET /export/glb")

    # ---- the adapter ------------------------------------------------------------

    def image_to_mesh(self, *, image, texture_size, out_path, progress, hints, delight=True, quality=None):
        out_path = Path(out_path)
        directory = out_path.parent
        download = directory / DOWNLOAD_FILE
        if not download.is_file():
            run_file = directory / RUN_FILE
            run = self._load_run(run_file)
            if run:
                progress("Resuming the Modly run", 0.05)
            else:
                progress("Sending the image to Modly", 0.02)
                run = {"run_id": self._submit(Path(image), hints, quality or {}), "url": self.url, "model": self.model,
                       "sent_at": time.time()}
                run_file.write_text(json.dumps(run), encoding="utf-8")
            workspace_path = self._wait(run, run_file, progress)
            progress("Downloading the mesh", 0.87)
            write_atomic(download, self._download(workspace_path))
            run_file.unlink(missing_ok=True)
        result = finish_textured_glb(
            download=download, out_path=out_path, texture_size=texture_size, delight=delight, progress=progress,
            tool="Modly", source=f"modly:{self.model}",
            no_texture_hint="Is the model's texture step enabled and its texture extensions built?")
        return {**result, "background": "removed by Modly"}
