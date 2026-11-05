"""The WebSocket sends status and progress events for every change."""

from __future__ import annotations

from conftest import make_project


def _until(ws, predicate, limit=500):
    seen = []
    for _ in range(limit):
        event = ws.receive_json()
        seen.append(event)
        if predicate(event):
            return seen
    raise AssertionError("event not received")


def test_websocket_events(client, tmp_path):
    project = make_project(client, "godot", tmp_path / "g")
    with client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json() == {"type": "hello"}
        asset = client.post("/api/assets", json={"project_id": project["id"], "kind": "monster",
                                                 "description": "a green slime"}).json()
        aid = asset["id"]
        events = _until(ws, lambda e: e.get("status") == "waiting" and e["type"] == "status")
        statuses = [e["status"] for e in events if e["type"] == "status"]
        assert statuses[0] == "queued" and "running" in statuses and statuses[-1] == "waiting"
        progress = [e for e in events if e["type"] == "progress"]
        assert progress and all(e["asset_id"] == aid and e["status"] == "running" and e["step"] for e in progress)
        assert events[-1]["gate"] == 1 and events[-1]["asset"]["id"] == aid

        for gate, body in ((1, {"candidate": 1}), (2, None), (3, None)):
            client.post(f"/api/assets/{aid}/gates/{gate}/approve", json=body)
            target = "done" if gate == 3 else "waiting"
            events = _until(ws, lambda e: e["type"] == "status" and e["status"] == target)
            assert events[0]["type"] == "status" and events[0]["status"] == "queued"
            assert any(e["type"] == "progress" for e in events)

        client.put(f"/api/assets/{aid}/training-candidate", json={"value": False})
        event = ws.receive_json()
        assert event["type"] == "status" and event["asset"]["training_candidate"] is False

        client.delete(f"/api/assets/{aid}")
        assert ws.receive_json()["type"] == "deleted"


def test_plain_get_on_ws_is_documented(client):
    assert client.get("/api/ws").status_code == 426
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/ws" in paths
