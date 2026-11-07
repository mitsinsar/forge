"""A small fake ComfyUI: the HTTP routes the image adapter uses and the ``/ws``
progress WebSocket, on one port like the real server.

``models`` are the model files it "has" (a missing one fails validation like
ComfyUI does), ``nodes`` the node classes it knows, ``hang`` makes prompts
never finish. Everything it receives is kept for the tests to look at.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import queue
import re
import select
import struct
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

CORE_NODES = {"CheckpointLoaderSimple", "UNETLoader", "CLIPLoader", "DualCLIPLoader", "VAELoader",
              "CLIPTextEncode", "EmptyLatentImage", "EmptySD3LatentImage", "KSampler", "VAEDecode", "SaveImage",
              "FluxGuidance", "ModelSamplingAuraFlow", "LoraLoader", "LoraLoaderModelOnly", "LoadImage",
              "ImageBatch", "CLIPVisionLoader", "CLIPVisionEncode", "StyleModelLoader", "StyleModelApply",
              "ControlNetLoader", "ControlNetApplyAdvanced"}
IPADAPTER_NODES = {"IPAdapterUnifiedLoader", "IPAdapterAdvanced"}
MODEL_INPUTS = {"ckpt_name", "unet_name", "clip_name", "clip_name1", "clip_name2", "vae_name", "lora_name",
                "style_model_name", "control_net_name"}
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ws_frame(text: str) -> bytes:
    payload = text.encode("utf-8")
    header = bytes([0x81])
    if len(payload) < 126:
        header += bytes([len(payload)])
    elif len(payload) < 65536:
        header += bytes([126]) + struct.pack(">H", len(payload))
    else:
        header += bytes([127]) + struct.pack(">Q", len(payload))
    return header + payload


class FakeComfyUI:
    def __init__(self, models: set[str] | None = None, nodes: set[str] | None = None, loras: list[str] | None = None,
                 steps: int = 3, hang: bool = False):
        self.models = set(models or ())
        self.loras = list(loras or [])
        self.nodes = set(nodes if nodes is not None else CORE_NODES | IPADAPTER_NODES)
        self.steps = steps
        self.hang = hang
        self.prompts: list[dict] = []  # the workflows queued, in order
        self.uploads: dict[str, bytes] = {}
        self.freed: list[dict] = []
        self.cancelled: list[str] = []
        self.history: dict[str, dict] = {}
        self.images: dict[str, bytes] = {}
        self.sockets: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._stopping = False
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                if url.path == "/ws":
                    return fake._websocket(self)
                if url.path == "/system_stats":
                    return self._json({"system": {"comfyui_version": "fake"}})
                if url.path == "/models/loras":
                    return self._json(fake.loras)
                if url.path.startswith("/object_info/"):
                    name = urllib.parse.unquote(url.path.rsplit("/", 1)[1])
                    if name == "ControlNetLoader" and name in fake.nodes:  # lists its model files
                        files = sorted(m for m in fake.models if "openpose" in m)
                        return self._json({name: {"input": {"required": {"control_net_name": [files]}}}})
                    return self._json({name: {"input": {}}} if name in fake.nodes else {})
                if url.path.startswith("/history/"):
                    prompt_id = url.path.rsplit("/", 1)[1]
                    return self._json({prompt_id: fake.history[prompt_id]} if prompt_id in fake.history else {})
                if url.path == "/view":
                    name = urllib.parse.parse_qs(url.query)["filename"][0]
                    if name not in fake.images:
                        return self._json({"error": "not found"}, 404)
                    return self._raw(fake.images[name], "image/png")
                return self._json({"error": "not found"}, 404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                path = urllib.parse.urlparse(self.path).path
                if path == "/prompt":
                    status, answer = fake._prompt(json.loads(body))
                    return self._json(answer, status)
                if path == "/upload/image":
                    return self._json(fake._upload(self.headers["Content-Type"], body))
                if path == "/free":
                    fake.freed.append(json.loads(body))
                    return self._raw(b"", "text/plain")
                if path in ("/queue", "/interrupt"):
                    fake.cancelled.append(path)
                    return self._raw(b"", "text/plain")
                return self._json({"error": "not found"}, 404)

            def _json(self, data, status=200):
                self._raw(json.dumps(data).encode(), "application/json", status)

            def _raw(self, data: bytes, kind: str, status=200):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self._stopping = True
        self.server.shutdown()
        self.server.server_close()

    # ---- WebSocket -------------------------------------------------------------

    def _websocket(self, handler: BaseHTTPRequestHandler) -> None:
        key = handler.headers["Sec-WebSocket-Key"]
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        handler.send_response(101)
        handler.send_header("Upgrade", "websocket")
        handler.send_header("Connection", "Upgrade")
        handler.send_header("Sec-WebSocket-Accept", accept)
        handler.end_headers()
        handler.wfile.flush()
        outbox: queue.Queue = queue.Queue()
        with self._lock:
            self.sockets.append(outbox)
        connection = handler.connection
        try:
            connection.sendall(ws_frame(json.dumps({"type": "status", "data": {"status": {}, "sid": "x"}})))
            while not self._stopping:
                readable, _, _ = select.select([connection], [], [], 0.05)
                if readable:
                    incoming = connection.recv(4096)
                    if not incoming:
                        break  # the client went away
                    if incoming[0] & 0x0F == 0x8:  # close frame: answer it and stop
                        connection.sendall(b"\x88\x00")
                        break
                try:
                    message = outbox.get_nowait()
                except queue.Empty:
                    continue
                connection.sendall(message if isinstance(message, bytes) and message[:1] == b"\x82"
                                   else ws_frame(message))
        except OSError:
            pass
        finally:
            with self._lock:
                self.sockets.remove(outbox)
            handler.close_connection = True

    def broadcast(self, event: dict) -> None:
        with self._lock:
            for outbox in self.sockets:
                outbox.put(json.dumps(event))

    # ---- routes ------------------------------------------------------------------

    def _prompt(self, body: dict) -> tuple[int, dict]:
        workflow = body["prompt"]
        node_errors = {}
        for node_id, node in workflow.items():
            if node["class_type"] not in self.nodes:
                return 400, {"error": {"type": "invalid_prompt",
                                       "message": f"Cannot execute because node {node['class_type']} does not exist.",
                                       "details": f"Node ID '#{node_id}'", "extra_info": {}}, "node_errors": {}}
            for name, value in node["inputs"].items():
                known = self.loras if name == "lora_name" else self.models
                if name in MODEL_INPUTS and value not in known:
                    node_errors[node_id] = {"errors": [{
                        "type": "value_not_in_list", "message": "Value not in list",
                        "details": f"{name}: '{value}' not in {sorted(known)}",
                        "extra_info": {"input_name": name, "received_value": value}}],
                        "dependent_outputs": [], "class_type": node["class_type"]}
        if node_errors:
            return 400, {"error": {"type": "prompt_outputs_failed_validation",
                                   "message": "Prompt outputs failed validation", "details": "", "extra_info": {}},
                         "node_errors": node_errors}
        prompt_id = str(uuid.uuid4())
        self.prompts.append(workflow)
        threading.Thread(target=self._execute, args=(prompt_id, workflow), daemon=True).start()
        return 200, {"prompt_id": prompt_id, "number": len(self.prompts), "node_errors": {}}

    def _execute(self, prompt_id: str, workflow: dict) -> None:
        time.sleep(0.02)
        self.broadcast({"type": "execution_start", "data": {"prompt_id": prompt_id}})
        if self.hang:
            return
        sampler = next(k for k, n in workflow.items() if n["class_type"] == "KSampler")
        save = next(k for k, n in workflow.items() if n["class_type"] == "SaveImage")
        self.broadcast({"type": "executing", "data": {"node": sampler, "prompt_id": prompt_id}})
        for step in range(1, self.steps + 1):
            self.broadcast({"type": "progress", "data": {"value": step, "max": self.steps, "prompt_id": prompt_id,
                                                         "node": sampler}})
        with self._lock:  # a binary preview frame, which the client must skip
            for outbox in self.sockets:
                outbox.put(b"\x82\x04\x00\x00\x00\x01")
        seed = workflow[sampler]["inputs"]["seed"]
        prefix = workflow[save]["inputs"]["filename_prefix"]
        subfolder, _, stem = prefix.rpartition("/")
        filename = f"{stem}_{seed}_00001_.png"
        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), ((seed * 40) % 256, 90, 160)).save(buffer, "PNG")
        self.images[filename] = buffer.getvalue()
        self.history[prompt_id] = {"outputs": {save: {"images": [{"filename": filename, "subfolder": subfolder,
                                                                  "type": "output"}]}},
                                   "status": {"status_str": "success", "completed": True}}
        self.broadcast({"type": "executed", "data": {"node": save, "prompt_id": prompt_id}})
        self.broadcast({"type": "execution_success", "data": {"prompt_id": prompt_id}})
        self.broadcast({"type": "executing", "data": {"node": None, "prompt_id": prompt_id}})

    def _upload(self, content_type: str, body: bytes) -> dict:
        boundary = re.search(r"boundary=(\S+)", content_type).group(1).encode()
        fields, filename, data = {}, None, b""
        for part in body.split(b"--" + boundary):
            if b"\r\n\r\n" not in part:
                continue
            head, value = part.split(b"\r\n\r\n", 1)
            value = value[:-2] if value.endswith(b"\r\n") else value
            name = re.search(rb'name="([^"]+)"', head).group(1).decode()
            found = re.search(rb'filename="([^"]+)"', head)
            if found:
                filename, data = found.group(1).decode(), value
            else:
                fields[name] = value.decode()
        subfolder = fields.get("subfolder", "")
        self.uploads[f"{subfolder}/{filename}" if subfolder else filename] = data
        return {"name": filename, "subfolder": subfolder, "type": fields.get("type", "input")}
