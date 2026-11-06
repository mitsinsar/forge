"""Preview candidates rendered by ComfyUI (Qwen-Image, SDXL or Flux).

Each base model has an API-format workflow in ``backend/workflows/<base_model>.json``
(the ``comfyui_workflows_dir`` setting). The adapter finds the nodes it fills by
their title (``_meta.title``), so a workflow exported from ComfyUI with
"Save (API)" can replace a file as long as it keeps these titles:

``model``     the loader whose output 0 is the MODEL (LoRAs are chained after it)
``clip``      the CLIP loader (left out when ``model`` is a checkpoint loader, whose output 1 is CLIP)
``positive``  / ``negative``  the two ``CLIPTextEncode`` nodes
``latent``    the empty latent (``width``, ``height``, ``batch_size``)
``sampler``   the ``KSampler`` (``seed``; reference images hook into its ``model`` or ``positive`` input)
``save``      the ``SaveImage`` node

One prompt is queued per missing candidate (seed + n - 1), progress is read
from the ``/ws`` WebSocket, the image is fetched with ``/view`` and written as
``candidate_<n>.png``; files already there are kept, so a stage resumes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect as ws_connect

from ...config import Settings
from ..base import Progress

log = logging.getLogger("assetgen.comfyui")


class ComfyUIError(RuntimeError):
    """A readable failure of the preview stage."""


# How each base model takes LoRAs and style reference images.
#   lora: the loader node class. LoraLoader patches model and CLIP; LoraLoaderModelOnly the model only.
#   reference: "ipadapter" (ComfyUI_IPAdapter_plus nodes, patch the sampler's model),
#              "redux" (Flux Redux, core nodes, patch the sampler's positive conditioning) or None.
BASE_MODELS: dict[str, dict] = {
    "sdxl": {"label": "SDXL", "lora": "LoraLoader", "reference": "ipadapter"},
    "flux": {"label": "Flux", "lora": "LoraLoader", "reference": "redux"},
    "qwen-image": {"label": "Qwen-Image", "lora": "LoraLoaderModelOnly", "reference": None},
}

# Reference-image settings (style references, so they guide the look, not the pose).
IPADAPTER = {"preset": "PLUS (high strength)", "weight": 0.6, "weight_type": "style transfer",
             "combine_embeds": "average", "start_at": 0.0, "end_at": 1.0, "embeds_scaling": "V only"}
REDUX = {"clip_vision": "sigclip_vision_patch14_384.safetensors",
         "style_model": "flux1-redux-dev.safetensors", "strength": 0.5}
REFERENCE_NODES = {"ipadapter": ["IPAdapterUnifiedLoader", "IPAdapterAdvanced"],
                   "redux": ["CLIPVisionLoader", "CLIPVisionEncode", "StyleModelLoader", "StyleModelApply"]}

# The models/ subfolder for each loader input, to say where a missing file goes.
MODEL_FOLDERS = {"ckpt_name": "checkpoints", "unet_name": "diffusion_models", "clip_name": "text_encoders",
                 "clip_name1": "text_encoders", "clip_name2": "text_encoders", "vae_name": "vae",
                 "lora_name": "loras", "style_model_name": "style_models", "preset": "ipadapter"}
CLASS_FOLDERS = {"CLIPVisionLoader": "clip_vision", "IPAdapterUnifiedLoader": "ipadapter"}

DEFAULT_WORKFLOWS_DIR = Path(__file__).resolve().parents[4] / "workflows"
HTTP_TIMEOUT = 30.0


def load_workflow(directory: Path, base_model: str) -> dict:
    path = Path(directory) / f"{base_model}.json"
    if not path.is_file():
        raise ComfyUIError(f"No ComfyUI workflow for base model {base_model!r}: {path} is missing.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ComfyUIError(f"The ComfyUI workflow {path} is not valid JSON ({exc}).") from exc


def find_node(workflow: dict, title: str, required: bool = True) -> str | None:
    for node_id, node in workflow.items():
        if (node.get("_meta") or {}).get("title") == title:
            return node_id
    if required:
        raise ComfyUIError(f'The ComfyUI workflow has no node titled "{title}".')
    return None


class WorkflowBuilder:
    """Fills a workflow in place: prompts, seed, LoRA chain and reference images."""

    def __init__(self, workflow: dict):
        self.workflow = copy.deepcopy(workflow)
        self._next_id = max((int(k) for k in self.workflow if str(k).isdigit()), default=0) + 1000

    def add(self, class_type: str, inputs: dict, title: str) -> str:
        node_id = str(self._next_id)
        self._next_id += 1
        self.workflow[node_id] = {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}
        return node_id

    def rewire(self, old: list, new: list, skip: set[str]) -> None:
        """Every input linked to output ``old`` is linked to ``new`` instead (but not in ``skip``)."""
        for node_id, node in self.workflow.items():
            if node_id in skip:
                continue
            for name, value in node["inputs"].items():
                if isinstance(value, list) and len(value) == 2 and [str(value[0]), value[1]] == old:
                    node["inputs"][name] = list(new)

    def node(self, title: str) -> dict:
        return self.workflow[find_node(self.workflow, title)]

    def set_text(self, prompt: str, negative: str) -> None:
        self.node("positive")["inputs"]["text"] = prompt
        self.node("negative")["inputs"]["text"] = negative

    def set_seed(self, seed: int) -> None:
        self.node("sampler")["inputs"]["seed"] = int(seed) % (2 ** 63)
        self.node("latent")["inputs"]["batch_size"] = 1

    def set_prefix(self, prefix: str) -> None:
        self.node("save")["inputs"]["filename_prefix"] = prefix

    def add_loras(self, loras: list[dict], loader: str) -> None:
        """Chains one LoRA loader per entry after the model (and CLIP) loader."""
        model_id = find_node(self.workflow, "model")
        model_out = [model_id, 0]
        if self.workflow[model_id]["class_type"] == "CheckpointLoaderSimple":
            clip_out = [model_id, 1]
        else:
            clip_id = find_node(self.workflow, "clip", required=loader == "LoraLoader")
            clip_out = [clip_id, 0] if clip_id else None
        for index, lora in enumerate(loras, start=1):
            weight = float(lora["weight"])
            if loader == "LoraLoader":
                node_id = self.add("LoraLoader", {"lora_name": lora["comfy_name"], "strength_model": weight,
                                                  "strength_clip": weight, "model": model_out, "clip": clip_out},
                                   f"lora {index}: {lora['name']}")
                self.rewire(model_out, [node_id, 0], skip={node_id})
                self.rewire(clip_out, [node_id, 1], skip={node_id})
                model_out, clip_out = [node_id, 0], [node_id, 1]
            else:
                node_id = self.add("LoraLoaderModelOnly", {"lora_name": lora["comfy_name"],
                                                           "strength_model": weight, "model": model_out},
                                   f"lora {index}: {lora['name']}")
                self.rewire(model_out, [node_id, 0], skip={node_id})
                model_out = [node_id, 0]

    def add_ipadapter(self, images: list[str]) -> None:
        """IP-Adapter (ComfyUI_IPAdapter_plus) on the sampler's model, all references averaged."""
        sampler = self.node("sampler")
        batch = self._image_batch(images)
        loader = self.add("IPAdapterUnifiedLoader", {"preset": IPADAPTER["preset"], "model": sampler["inputs"]["model"]},
                          "reference loader")
        apply = self.add("IPAdapterAdvanced", {
            "model": [loader, 0], "ipadapter": [loader, 1], "image": batch, "weight": IPADAPTER["weight"],
            "weight_type": IPADAPTER["weight_type"], "combine_embeds": IPADAPTER["combine_embeds"],
            "start_at": IPADAPTER["start_at"], "end_at": IPADAPTER["end_at"],
            "embeds_scaling": IPADAPTER["embeds_scaling"]}, "reference images")
        sampler["inputs"]["model"] = [apply, 0]

    def add_redux(self, images: list[str]) -> None:
        """Flux Redux: each reference image is added to the sampler's positive conditioning."""
        sampler = self.node("sampler")
        vision = self.add("CLIPVisionLoader", {"clip_name": REDUX["clip_vision"]}, "reference vision")
        style = self.add("StyleModelLoader", {"style_model_name": REDUX["style_model"]}, "reference style model")
        conditioning = sampler["inputs"]["positive"]
        for index, image in enumerate(images, start=1):
            loaded = self.add("LoadImage", {"image": image}, f"reference {index}")
            encoded = self.add("CLIPVisionEncode", {"clip_vision": [vision, 0], "image": [loaded, 0],
                                                    "crop": "center"}, f"reference {index} encode")
            conditioning = [self.add("StyleModelApply", {
                "conditioning": conditioning, "style_model": [style, 0], "clip_vision_output": [encoded, 0],
                "strength": REDUX["strength"], "strength_type": "multiply"}, f"reference {index} apply"), 0]
        sampler["inputs"]["positive"] = conditioning

    def _image_batch(self, images: list[str]) -> list:
        loaded = [[self.add("LoadImage", {"image": image}, f"reference {i}"), 0] for i, image in enumerate(images, 1)]
        batch = loaded[0]
        for other in loaded[1:]:
            batch = [self.add("ImageBatch", {"image1": batch, "image2": other}, "reference batch"), 0]
        return batch


class ComfyUIImage:

    def __init__(self, settings: Settings):
        self.url = settings.comfyui_url.rstrip("/")
        self.comfyui_dir = Path(settings.comfyui_dir) if settings.comfyui_dir else None
        self.workflows_dir = Path(settings.comfyui_workflows_dir) if settings.comfyui_workflows_dir \
            else DEFAULT_WORKFLOWS_DIR
        self.timeout = float(settings.comfyui_timeout)
        self.client_id = uuid.uuid4().hex

    # ---- HTTP ----------------------------------------------------------------

    def _request(self, method: str, path: str, *, body: bytes | None = None, headers: dict | None = None,
                 timeout: float = HTTP_TIMEOUT) -> bytes:
        request = urllib.request.Request(self.url + path, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise self._unreachable(exc) from exc

    def _unreachable(self, exc: BaseException) -> ComfyUIError:
        reason = getattr(exc, "reason", None) or exc
        return ComfyUIError(f"Could not reach ComfyUI at {self.url} ({reason}). Is it running, and is "
                            f"comfyui_url right?")

    def _json(self, method: str, path: str, data=None):
        body = json.dumps(data).encode("utf-8") if data is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        try:
            raw = self._request(method, path, body=body, headers=headers)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise ComfyUIError(f"ComfyUI answered HTTP {exc.code} to {method} {path}: {detail}") from exc
        return json.loads(raw) if raw.strip() else None

    def _has_nodes(self, classes: list[str]) -> list[str]:
        """The node classes of ``classes`` that ComfyUI does not have."""
        missing = []
        for name in classes:
            try:
                info = self._json("GET", f"/object_info/{urllib.parse.quote(name)}")
            except ComfyUIError as exc:
                if "Could not reach" in str(exc):
                    raise
                info = None
            if not info or name not in info:
                missing.append(name)
        return missing

    def upload_image(self, path: Path) -> str:
        """Uploads a reference image to ComfyUI's input/assetgen/ and returns its LoadImage name."""
        data = Path(path).read_bytes()
        name = hashlib.sha256(data).hexdigest()[:16] + (Path(path).suffix.lower() or ".png")
        boundary = uuid.uuid4().hex
        parts = []
        for field, value in (("type", "input"), ("subfolder", "assetgen"), ("overwrite", "true")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\n'
                     f'Content-Type: application/octet-stream\r\n\r\n'.encode() + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        try:
            raw = self._request("POST", "/upload/image", body=b"".join(parts),
                                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        except urllib.error.HTTPError as exc:
            raise ComfyUIError(f"ComfyUI refused the reference image {Path(path).name} "
                               f"(HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}).") from exc
        answer = json.loads(raw)
        return f"{answer['subfolder']}/{answer['name']}" if answer.get("subfolder") else answer["name"]

    # ---- LoRAs ---------------------------------------------------------------

    def prepare_loras(self, loras: list[dict]) -> list[dict]:
        """Gives each LoRA the name ComfyUI knows it by, copying its file into
        ``<comfyui_dir>/models/loras`` when ComfyUI does not have it yet."""
        if not loras:
            return []
        known = self._json("GET", "/models/loras") or []
        by_file = {Path(name.replace("\\", "/")).name: name for name in known}
        prepared = []
        for lora in loras:
            source = Path(lora["path"])
            name = by_file.get(source.name)
            if name is None:
                name = self._copy_lora(lora, source)
            prepared.append({**lora, "comfy_name": name})
        return prepared

    def _copy_lora(self, lora: dict, source: Path) -> str:
        if self.comfyui_dir is None:
            raise ComfyUIError(
                f"The LoRA {lora['name']!r} ({source.name}) is not in ComfyUI's models/loras folder, and "
                f"comfyui_dir is not set, so it can't be copied there. Set comfyui_dir to the ComfyUI folder "
                f"(the one with models/) or copy the file into models/loras yourself.")
        if not source.is_file():
            raise ComfyUIError(f"The LoRA {lora['name']!r} file is missing: {source}.")
        target_dir = self.comfyui_dir / "models" / "loras"
        if not target_dir.is_dir():
            raise ComfyUIError(f"comfyui_dir is set to {self.comfyui_dir}, but {target_dir} does not exist. "
                               f"Point comfyui_dir at the ComfyUI folder that has models/loras.")
        target = target_dir / source.name
        if not (target.is_file() and target.stat().st_size == source.stat().st_size):
            shutil.copy2(source, target)
            log.info("copied LoRA %s to %s", source, target)
        return source.name

    # ---- rendering -------------------------------------------------------------

    def render_candidates(self, *, prompt, negative_prompt, base_model, loras, reference_images, count, seed,
                          out_dir, progress: Progress, hints, log: Callable[[str], None] | None = None):
        say = log or (lambda line: None)
        model = BASE_MODELS.get(base_model)
        if model is None:
            raise ComfyUIError(f"Unknown base model {base_model!r}; ComfyUI workflows exist for "
                               f"{', '.join(BASE_MODELS)}.")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = [out_dir / f"candidate_{n}.png" for n in range(1, count + 1)]
        missing = [n for n, path in enumerate(paths, start=1) if not path.is_file()]
        if not missing:
            progress("Candidates ready", 1.0)
            return paths

        progress(f"Preparing the {model['label']} workflow", 0.0)
        builder = WorkflowBuilder(load_workflow(self.workflows_dir, base_model))
        builder.set_text(prompt, negative_prompt or "")
        builder.add_loras(self.prepare_loras(loras), model["lora"])
        if loras:
            used = ", ".join(f"{lora['name']} ({float(lora['weight']):g})" for lora in loras)
            say(f"LoRAs: {used}.")
        self._add_references(builder, model, [Path(p) for p in reference_images], say)

        done_before = count - len(missing)
        with self._socket() as socket:
            for position, number in enumerate(missing):
                builder.set_seed(seed + number - 1)
                builder.set_prefix(f"assetgen/{hints.get('asset_id', 'asset')}_c{number}")

                def report(step: str, fraction: float, number=number, position=position):
                    progress(f"Candidate {number} of {count}: {step}",
                             (done_before + position + fraction) / count)

                image = self._run(socket, builder.workflow, report)
                temporary = paths[number - 1].with_suffix(".part")
                temporary.write_bytes(image)
                temporary.replace(paths[number - 1])
        progress("Candidates ready", 1.0)
        return paths

    def _add_references(self, builder: WorkflowBuilder, model: dict, images: list[Path], say) -> None:
        images = [p for p in images if p.is_file()]
        if not images:
            return
        kind = model["reference"]
        if kind is None:
            say(f"{model['label']} has no reference-image input in this workflow, so the "
                f"{len(images)} style reference image(s) were not used.")
            return
        absent = self._has_nodes(REFERENCE_NODES[kind])
        if absent:
            say(f"ComfyUI lacks the node(s) {', '.join(absent)} needed for reference images on "
                f"{model['label']}, so the {len(images)} reference image(s) were not used. "
                + ("Install ComfyUI_IPAdapter_plus." if kind == "ipadapter" else "Update ComfyUI."))
            return
        names = [self.upload_image(path) for path in images]
        if kind == "ipadapter":
            builder.add_ipadapter(names)
        else:
            builder.add_redux(names)
        say(f"{len(names)} style reference image(s) applied through "
            f"{'IP-Adapter' if kind == 'ipadapter' else 'Flux Redux'}.")

    def _socket(self):
        scheme = "wss" if self.url.startswith("https") else "ws"
        address = f"{scheme}://{self.url.split('://', 1)[-1]}/ws?clientId={self.client_id}"
        try:
            return ws_connect(address, open_timeout=HTTP_TIMEOUT, ping_interval=None, max_size=64 * 2 ** 20)
        except (OSError, TimeoutError, WebSocketException) as exc:
            raise self._unreachable(exc) from exc

    def _queue(self, workflow: dict) -> str:
        body = json.dumps({"prompt": workflow, "client_id": self.client_id}).encode("utf-8")
        try:
            raw = self._request("POST", "/prompt", body=body, headers={"Content-Type": "application/json"})
        except urllib.error.HTTPError as exc:
            raise self._validation_error(exc) from exc
        return json.loads(raw)["prompt_id"]

    def _validation_error(self, exc: urllib.error.HTTPError) -> ComfyUIError:
        text = exc.read().decode("utf-8", "replace")
        try:
            answer = json.loads(text)
        except ValueError:
            return ComfyUIError(f"ComfyUI refused the workflow (HTTP {exc.code}): {text[:300]}")
        problems = []
        for node_id, node in (answer.get("node_errors") or {}).items():
            for error in node.get("errors") or []:
                extra = error.get("extra_info") or {}
                field, value = extra.get("input_name"), extra.get("received_value")
                if error.get("type") == "value_not_in_list" and field in MODEL_FOLDERS:
                    folder = CLASS_FOLDERS.get(node.get("class_type"), MODEL_FOLDERS[field])
                    problems.append(f"the model file {value!r} is missing: put it in ComfyUI's models/{folder} "
                                    f"(see backend/README.md)")
                else:
                    problems.append(f"{node.get('class_type', 'node')} #{node_id}: "
                                    f"{error.get('details') or error.get('message')}")
        if not problems:
            error = answer.get("error") or {}
            problems.append(error.get("message") or text[:300])
        missing_model = any("model file" in p for p in problems)
        lead = "ComfyUI is missing a model file" if missing_model else "ComfyUI refused the workflow"
        return ComfyUIError(f"{lead}: {'; '.join(dict.fromkeys(problems))}.")

    def _run(self, socket, workflow: dict, report: Callable[[str, float], None]) -> bytes:
        prompt_id = self._queue(workflow)
        report("queued in ComfyUI", 0.0)
        deadline = time.monotonic() + self.timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                self._cancel(prompt_id)
                raise ComfyUIError(f"ComfyUI did not finish a candidate within {self.timeout:g} s "
                                   f"(comfyui_timeout). The prompt was cancelled.")
            try:
                message = socket.recv(timeout=min(left, 5.0))
            except TimeoutError:
                continue
            except ConnectionClosed as exc:
                raise ComfyUIError(f"ComfyUI at {self.url} closed the progress connection ({exc}).") from exc
            if not isinstance(message, str):
                continue  # binary previews
            event = json.loads(message)
            data = event.get("data") or {}
            if data.get("prompt_id") not in (None, prompt_id):
                continue
            kind = event.get("type")
            if kind == "progress" and data.get("max"):
                report(f"step {data['value']}/{data['max']}", 0.95 * data["value"] / data["max"])
            elif kind == "execution_start":
                report("started", 0.0)
            elif kind == "execution_error":
                raise ComfyUIError(f"ComfyUI failed in {data.get('node_type', 'a node')}: "
                                   f"{(data.get('exception_message') or '').strip()}")
            elif kind == "execution_interrupted":
                raise ComfyUIError("The render was interrupted in ComfyUI.")
            elif kind == "execution_success" or (kind == "executing" and data.get("node") is None
                                                  and data.get("prompt_id") == prompt_id):
                break
        report("downloading", 0.97)
        return self._download(prompt_id, find_node(workflow, "save"))

    def _download(self, prompt_id: str, save_node: str) -> bytes:
        history = (self._json("GET", f"/history/{prompt_id}") or {}).get(prompt_id) or {}
        outputs = history.get("outputs") or {}
        images = (outputs.get(save_node) or {}).get("images") or next(
            (o["images"] for o in outputs.values() if o.get("images")), [])
        if not images:
            raise ComfyUIError("ComfyUI finished but returned no image.")
        image = images[0]
        query = urllib.parse.urlencode({"filename": image["filename"], "subfolder": image.get("subfolder", ""),
                                        "type": image.get("type", "output")})
        try:
            return self._request("GET", f"/view?{query}")
        except urllib.error.HTTPError as exc:
            raise ComfyUIError(f"ComfyUI could not send the image {image['filename']} (HTTP {exc.code}).") from exc

    def _cancel(self, prompt_id: str) -> None:
        for path, data in (("/queue", {"delete": [prompt_id]}), ("/interrupt", {"prompt_id": prompt_id})):
            try:
                self._json("POST", path, data)
            except ComfyUIError as exc:
                log.warning("could not cancel ComfyUI prompt %s: %s", prompt_id, exc)

    def unload(self) -> None:
        """Unloads every model and frees ComfyUI's VRAM before the mesh stage."""
        try:
            self._json("POST", "/free", {"unload_models": True, "free_memory": True})
        except ComfyUIError as exc:
            if "Could not reach" not in str(exc):
                raise
            log.warning("ComfyUI was not reachable to free its models: %s", exc)
