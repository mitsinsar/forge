"""The real Qwen adapter against a faked OpenAI-compatible endpoint, the adapter set
the app builds and the tool check."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from assetgen.adapters import build_adapters
from assetgen.adapters.real.qwen import BIPED_NEGATIVE, CREATURE_NEGATIVE, LLMError, QwenLLM
from assetgen.archetypes import load_archetypes
from assetgen.config import load_settings
from conftest import make_app, make_project, wait_for


class FakeQwen:
    """A tiny OpenAI-compatible server. ``replies`` are the message contents it
    answers, in order; the last one repeats. Every request body is kept.
    ``refuse`` lists response_format types answered with HTTP 400, as LM Studio
    does for json_object."""

    def __init__(self, replies: list[str], refuse: tuple[str, ...] = ()):
        self.replies = list(replies)
        self.refuse = refuse
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self._json({"object": "list", "data": [{"id": "qwen3"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, "body": body})
                kind = (body.get("response_format") or {}).get("type")
                if kind in fake.refuse:
                    self._json({"error": "'response_format.type' must be 'json_schema' or 'text'"}, status=400)
                    return
                content = fake.replies.pop(0) if len(fake.replies) > 1 else fake.replies[0]
                self._json({"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]})

            def _json(self, data, status=200):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def reply(**fields) -> str:
    data = {"archetype": "biped", "prompt": "a knight", "negative_prompt": "extra limbs",
            "slot": None, "weapon_class": None}
    data.update(fields)
    return json.dumps(data)


@pytest.fixture
def fake_qwen():
    servers = []

    def start(*replies: str, refuse: tuple[str, ...] = ()) -> FakeQwen:
        server = FakeQwen(list(replies), refuse)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def adapter_for(server: FakeQwen, tmp_path) -> QwenLLM:
    return QwenLLM(load_settings(data_dir=tmp_path, qwen_url=server.url, qwen_model="qwen3:14b"))


def expand(llm: QwenLLM, **kwargs) -> dict:
    args = {"description": "a knight in red plate", "kind": "hero", "archetypes": load_archetypes(),
            "archetype_override": None, "slot": None, "weapon_class": None, "notes": []}
    args.update(kwargs)
    return llm.expand(**args)


def test_valid_reply_and_request_shape(fake_qwen, tmp_path):
    server = fake_qwen(reply(archetype="quadruped", prompt="a red wolf, standing on four legs"))
    result = expand(adapter_for(server, tmp_path), description="a red wolf", kind="monster")
    assert result == {"archetype": "quadruped", "prompt": "a red wolf, standing on four legs",
                      "negative_prompt": f"extra limbs, {CREATURE_NEGATIVE}", "slot": None, "weapon_class": None,
                      "flags": []}
    (request,) = server.requests
    assert request["path"] == "/v1/chat/completions"
    body = request["body"]
    assert body["model"] == "qwen3:14b"
    assert body["response_format"]["type"] == "json_schema"
    schema = body["response_format"]["json_schema"]["schema"]
    assert set(schema["required"]) == {"archetype", "prompt", "negative_prompt", "slot", "weapon_class"}
    system = body["messages"][0]["content"]
    for archetype in load_archetypes().values():  # every archetype with its rest pose
        assert archetype["id"] in system and archetype["rest_pose"] in system
    assert "background" in system and "lighting" in system
    assert "a red wolf" in body["messages"][1]["content"]


def test_lm_studio_json_schema_only(fake_qwen, tmp_path):
    """LM Studio refuses json_object; json_schema is asked first and works."""
    server = fake_qwen(reply(prompt="lm studio knight"), refuse=("json_object",))
    assert expand(adapter_for(server, tmp_path))["prompt"] == "lm studio knight"
    assert [r["body"]["response_format"]["type"] for r in server.requests] == ["json_schema"]


@pytest.mark.parametrize("refuse, used", [
    (("json_schema",), "json_object"),
    (("json_schema", "json_object"), None),
])
def test_refused_format_falls_back_and_is_remembered(fake_qwen, tmp_path, refuse, used):
    server = fake_qwen(reply(prompt="first"), reply(prompt="second"), refuse=refuse)
    llm = adapter_for(server, tmp_path)
    assert expand(llm)["prompt"] == "first"
    assert expand(llm)["prompt"] == "second"
    answered = [r["body"].get("response_format", {}).get("type") for r in server.requests
                if (r["body"].get("response_format") or {}).get("type") not in refuse]
    assert answered == [used, used]  # the second call goes straight to the working format
    assert len(server.requests) == len(refuse) + 2


def test_other_http_errors_still_fail(fake_qwen, tmp_path):
    server = fake_qwen(reply(), refuse=("json_schema", "json_object", None))
    with pytest.raises(LLMError, match="answered HTTP 400"):
        expand(adapter_for(server, tmp_path))
    assert len(server.requests) == 3


@pytest.mark.parametrize("kind, archetype, held_items, front_view", [
    ("hero", "biped", True, True),
    ("monster", "quadruped", True, False),  # its rest pose is a side view
    ("weapon", "biped", False, False),
    ("gear", "biped", False, False),
])
def test_characters_are_empty_handed(fake_qwen, tmp_path, kind, archetype, held_items, front_view):
    server = fake_qwen(reply(archetype=archetype, negative_prompt="extra limbs", slot="head", weapon_class="bow"))
    result = expand(adapter_for(server, tmp_path), kind=kind)
    assert (CREATURE_NEGATIVE in result["negative_prompt"]) is held_items
    assert (BIPED_NEGATIVE in result["negative_prompt"]) is front_view
    assert result["negative_prompt"].startswith("extra limbs")
    system = server.requests[0]["body"]["messages"][0]["content"]
    assert ("Empty-handed" in system) is held_items


def test_invalid_reply_then_valid(fake_qwen, tmp_path):
    server = fake_qwen("Sure! Here is a knight: {not json", reply(prompt="a knight, A-pose"))
    result = expand(adapter_for(server, tmp_path))
    assert result["prompt"] == "a knight, A-pose"
    assert len(server.requests) == 2
    retry = server.requests[1]["body"]["messages"]
    assert retry[-2] == {"role": "assistant", "content": "Sure! Here is a knight: {not json"}
    assert "not usable" in retry[-1]["content"]


def test_two_invalid_replies_fail_readably(fake_qwen, tmp_path):
    server = fake_qwen("nope", '{"archetype": "biped"}')
    with pytest.raises(LLMError, match="did not give a usable prompt after 2 tries"):
        expand(adapter_for(server, tmp_path))
    assert len(server.requests) == 2


def test_thinking_and_fences_are_stripped(fake_qwen, tmp_path):
    server = fake_qwen("<think>hmm</think>\n```json\n" + reply(prompt="fenced") + "\n```")
    assert expand(adapter_for(server, tmp_path))["prompt"] == "fenced"


def test_archetype_override_wins(fake_qwen, tmp_path):
    server = fake_qwen(reply(archetype="biped", prompt="a spider queen"))
    result = expand(adapter_for(server, tmp_path), description="a spider queen", kind="boss",
                    archetype_override="spider")
    assert result["archetype"] == "spider" and result["flags"] == []
    assert '"spider"' in server.requests[0]["body"]["messages"][0]["content"]


@pytest.mark.parametrize("kind, given, field, expected", [
    ("gear", {"slot": "head"}, "slot", "head"),
    ("gear", {}, "slot", "legs"),  # Qwen's choice when the user gave none
    ("weapon", {"weapon_class": "bow"}, "weapon_class", "bow"),
    ("weapon", {}, "weapon_class", "staff"),
])
def test_gear_and_weapons_forced_to_biped(fake_qwen, tmp_path, kind, given, field, expected):
    server = fake_qwen(reply(archetype="quadruped", prompt="an item", slot="legs", weapon_class="staff"))
    result = expand(adapter_for(server, tmp_path), kind=kind, description="an item", **given)
    assert result["archetype"] == "biped"
    assert result[field] == expected
    other = "weapon_class" if field == "slot" else "slot"
    assert result[other] is None
    system = server.requests[0]["body"]["messages"][0]["content"]
    assert ("mannequin" in system) if kind == "gear" else ("side-on" in system)


def test_unknown_archetype_falls_back_and_is_flagged(fake_qwen, tmp_path):
    server = fake_qwen(reply(archetype="dragon"))
    result = expand(adapter_for(server, tmp_path), kind="monster")
    assert result["archetype"] == "biped"
    assert "dragon" in result["flags"][0]


def test_notes_are_in_the_request(fake_qwen, tmp_path):
    server = fake_qwen(reply())
    expand(adapter_for(server, tmp_path), notes=["make the helmet taller", "  ", "less gold"])
    system = server.requests[0]["body"]["messages"][0]["content"]
    assert "- make the helmet taller" in system and "- less gold" in system


def test_unreachable_endpoint_is_readable(tmp_path):
    llm = QwenLLM(load_settings(data_dir=tmp_path, qwen_url="http://127.0.0.1:9/v1"))
    with pytest.raises(LLMError, match="Could not reach Qwen"):
        expand(llm)


def test_the_app_builds_only_real_adapters(tmp_path):
    from assetgen.adapters.real.blender import BlenderTools
    from assetgen.adapters.real.comfyui import ComfyUIImage
    from assetgen.adapters.real.hunyuan3d import Hunyuan3DMesh
    from assetgen.adapters.real.unirig import UniRigRig

    adapters = build_adapters(load_settings(data_dir=tmp_path))
    assert isinstance(adapters.llm, QwenLLM) and isinstance(adapters.image, ComfyUIImage)
    assert isinstance(adapters.mesh, Hunyuan3DMesh) and isinstance(adapters.blender, BlenderTools)
    assert isinstance(adapters.rig, UniRigRig)


def test_an_old_adapters_table_is_ignored(tmp_path):
    (tmp_path / "assetgen.toml").write_text('port = 9002\n[adapters]\nllm = "mock"\n')
    assert load_settings(data_dir=tmp_path).port == 9002


def test_preview_stage_uses_qwen_and_tools_report(fake_qwen, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    server = fake_qwen("not json", reply(archetype="biped", prompt="QWEN PROMPT a knight in red plate"))
    settings = load_settings(data_dir=tmp_path / "data", qwen_url=server.url, comfyui_url="http://127.0.0.1:9")
    with TestClient(make_app(settings, llm=QwenLLM(settings))) as client:
        project = make_project(client, "godot", tmp_path / "game")
        created = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero",
                                                   "description": "a knight in red plate"})
        asset = wait_for(client, created.json()["id"], "waiting", gate=1)
        assert asset["outputs"]["prompt"]["prompt"].startswith("QWEN PROMPT a knight in red plate")
        assert len(server.requests) == 2

        tools = {t["tool"]: t for t in client.get("/api/tools").json()}
        assert tools["llm"]["ok"] is True
        assert tools["image"]["name"] == "ComfyUI" and tools["image"]["ok"] is False  # checked even when down
        assert tools["rig"]["name"] == "UniRig" and "UniRig not found" in tools["rig"]["detail"]


def test_failed_qwen_fails_the_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings(data_dir=tmp_path / "data", qwen_url="http://127.0.0.1:9/v1")
    with TestClient(make_app(settings, llm=QwenLLM(settings))) as client:
        project = make_project(client, "godot", tmp_path / "game")
        created = client.post("/api/assets", json={"project_id": project["id"], "kind": "hero",
                                                   "description": "a knight"})
        asset = wait_for(client, created.json()["id"], "failed")
        assert "Could not reach Qwen" in asset["error"]
        tools = {t["tool"]: t for t in client.get("/api/tools").json()}
        assert tools["llm"]["ok"] is False and "did not answer" in tools["llm"]["detail"]


def test_tools_report_paths(tmp_path, monkeypatch):
    from assetgen.tools import check_tools
    blender = tmp_path / "blender.exe"
    blender.write_text("")
    settings = load_settings(data_dir=tmp_path, blender_path=blender, unirig_path=tmp_path / "missing")
    tools = {t["tool"]: t for t in check_tools(settings)}
    assert tools["blender"]["ok"] is True
    assert tools["rig"]["ok"] is False and "UniRig not found" in tools["rig"]["detail"]
