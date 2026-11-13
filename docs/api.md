# HTTP API

Everything the web app does goes through Forge's HTTP API, so scripts, game tools, CI jobs or
other apps can drive it the same way.

- **Interactive reference:** `http://127.0.0.1:8000/docs` (every request and response field)
- **OpenAPI schema:** `http://127.0.0.1:8000/openapi.json`
- **Live events:** WebSocket at `ws://127.0.0.1:8000/api/ws`

The API has no authentication and listens on `127.0.0.1` by default. Don't expose it to a network
you don't trust (see [SECURITY.md](../SECURITY.md)).

## Quick examples

```bash
# List projects, then create a hero and let the autopilot take it to gate 3
curl http://127.0.0.1:8000/api/projects
curl -X POST http://127.0.0.1:8000/api/assets -H "Content-Type: application/json" \
     -d '{"project_id": "<project id>", "kind": "hero", "description": "a knight in red plate armour", "autopilot": true}'

# Read it back (status, stage, gate, outputs, file URLs)
curl http://127.0.0.1:8000/api/assets/<asset id>

# Approve gate 1 with candidate 2, then gates 2 and 3
curl -X POST http://127.0.0.1:8000/api/assets/<asset id>/gates/1/approve -H "Content-Type: application/json" -d '{"candidate": 2}'
curl -X POST http://127.0.0.1:8000/api/assets/<asset id>/gates/2/approve
curl -X POST http://127.0.0.1:8000/api/assets/<asset id>/gates/3/approve

# Reject gate 1 with a note: four new pictures
curl -X POST http://127.0.0.1:8000/api/assets/<asset id>/gates/1/reject -H "Content-Type: application/json" -d '{"note": "no cape, darker armour"}'

# Ten swords from one description, varied by the language model
curl -X POST http://127.0.0.1:8000/api/assets/batch -H "Content-Type: application/json" \
     -d '{"count": 10, "vary": true, "asset": {"project_id": "<project id>", "kind": "weapon", "weapon_class": "one_handed", "description": "a rune-etched longsword", "autopilot": true}}'

# Download the export as a zip
curl -o knight.zip http://127.0.0.1:8000/api/assets/<asset id>/download

# The tools: status, restart, stop, and what is missing
curl http://127.0.0.1:8000/api/tools
curl -X POST http://127.0.0.1:8000/api/tools/image/restart
curl -X POST http://127.0.0.1:8000/api/tools/mesh/stop
curl http://127.0.0.1:8000/api/setup
```

Asset fields: `project_id`, `kind` (`hero`, `monster`, `boss`, `gear`, `weapon`, `effect`),
`description`, and optionally `name`, `archetype`, `slot` (gear), `weapon_class` (weapons),
`height`, `profile_id`, `overrides` (any profile fields), `autopilot`, `autopilot_export`.
Check `/docs` for the exact body of every request.

## Asset status

| Field | Values |
|---|---|
| `status` | `queued`, `running` (with `step` and `progress` 0–1), `waiting` (at a `gate`), `done`, `failed` (with `error`), `stopped` |
| `stage` | `prompt`, `mesh`, `rig`, `export` |
| `gate` | `1`, `2`, `3` or `null` |

Errors: **404** unknown id, **409** wrong state (approving a gate the asset isn't waiting at),
**422** invalid input.

## WebSocket events

```jsonc
{"type": "hello"}                                       // once, on connect
{"type": "status", "asset_id": "…", "status": "waiting", "stage": "prompt", "gate": 1,
 "step": "Waiting at gate 1", "progress": 1.0, "error": null, "at": "…", "asset": { … }}
{"type": "progress", "asset_id": "…", "status": "running", "stage": "mesh",
 "step": "Decimating LOD1", "progress": 0.62, "gate": null, "error": null, "at": "…"}
{"type": "deleted", "asset_id": "…", "at": "…"}
```

A `status` event, carrying the full asset, is sent on every change.

```python
# Wait for an asset to reach a gate, in Python
import asyncio, json, websockets

async def wait_for_gate(asset_id):
    async with websockets.connect("ws://127.0.0.1:8000/api/ws") as ws:
        async for raw in ws:
            ev = json.loads(raw)
            if ev.get("asset_id") == asset_id and ev.get("status") in ("waiting", "done", "failed"):
                return ev

print(asyncio.run(wait_for_gate("<asset id>")))
```

## Routes

### Assets

| Method | Path | What it does |
|---|---|---|
| GET | `/api/assets` | List, newest first; filters `project_id`, `kind`, `status`, `archetype`, `batch_id`, `q` … |
| POST | `/api/assets` | Create an asset and queue it |
| POST | `/api/assets/from-image` | Create from your own image (multipart `file` + `asset` JSON) |
| POST | `/api/assets/batch` | Up to 20 assets from one description |
| GET | `/api/assets/{id}` | One asset with outputs, file URLs and effective settings |
| DELETE | `/api/assets/{id}` | Delete an asset and its files |
| POST | `/api/assets/{id}/gates/{n}/approve` | Approve gate *n*; gate 1 needs `{"candidate": k}` |
| POST | `/api/assets/{id}/gates/{n}/reject` | Reject with a `note` (and `archetype` at gate 1; `grip`, `joints`, `twists`, `wearer` at gate 3) |
| PUT | `/api/assets/{id}/autopilot` | Turn autopilot on or off |
| POST | `/api/assets/{id}/redo` | Run a stage again from scratch, also on a finished asset |
| POST | `/api/assets/{id}/parts/{part}/redo` | Make one effect part again |
| POST | `/api/assets/{id}/retry` | Retry a failed or stopped stage (resumes its files) |
| POST | `/api/assets/{id}/stop` | Stop an asset |
| POST | `/api/queue/stop` | Stop everything queued |
| POST | `/api/assets/{id}/export` | Re-export a finished asset |
| POST | `/api/assets/{id}/variants` | Colour variant of a rigged asset |
| POST | `/api/assets/{id}/fits` | Fit gear or a weapon on a rigged hero (or `"dummy"`) |
| GET | `/api/assets/{id}/download` | The export as a zip |
| GET | `/api/assets/{id}/thumb` | Small JPEG for galleries |
| GET | `/api/assets/{id}/files/{path}` | Any file the pipeline made |
| GET | `/api/assets/{id}/skills/{clip}.glb` | A rigged hero playing a skill animation |
| PUT | `/api/assets/{id}/training-candidate` | Flag as a LoRA training candidate |

### Projects, profiles, LoRAs

| Method | Path | What it does |
|---|---|---|
| GET / POST | `/api/projects` | List / register a game project (`name`, `engine`, `path`) |
| GET / PATCH / DELETE | `/api/projects/{id}` | One project |
| GET | `/api/projects/{id}/wearers` | Rigged biped heroes items can be fitted on |
| GET / POST | `/api/profiles` | List / create style profiles |
| GET / PATCH / DELETE | `/api/profiles/{id}` | One profile (PATCH merges nested fields) |
| POST / GET / DELETE | `/api/profiles/{id}/references[/{ref}]` | Style reference images (max 3) |
| POST | `/api/profiles/{id}/examples` | Render the profile's example sheet |
| GET / POST | `/api/loras`, `/api/loras/upload` | List / register by path / upload LoRAs |
| GET / PATCH / DELETE | `/api/loras/{id}` | One LoRA |
| POST | `/api/loras/{id}/examples` | Render with/without examples |

### Skills, sets, outfits

| Method | Path | What it does |
|---|---|---|
| GET / POST | `/api/motions` | List / make skill animations (HY-Motion) |
| GET / DELETE | `/api/motions/{id}` | One skill animation |
| POST | `/api/motions/{id}/redo`, `/pick`, `/use` | Make again, pick another version, add to a profile |
| GET | `/api/motions/{id}/preview.glb`, `/sheets/{n}.png` | Preview on the dummy, a version's stick-figure sheet |
| GET / POST | `/api/character-sets` | List / make character sets |
| GET / DELETE | `/api/character-sets/{id}` | One set |
| PUT | `/api/character-sets/{id}/picks` | Pick a version per slot |
| POST | `/api/character-sets/{id}/build` | Build and export the picked set |
| GET / POST / PATCH / DELETE | `/api/outfits[/{id}]` | Saved outfits |

### Tools and the machine

| Method | Path | What it does |
|---|---|---|
| GET | `/api/meta` | Kinds, engines, base models, camera presets, slots, weapon classes, profile defaults |
| GET | `/api/archetypes[/{id}]` | Archetype templates: rest pose, rig type, joints, clips |
| GET | `/api/models` | Models each step can use and which are installed |
| GET | `/api/tools` | Whether each tool answers |
| POST | `/api/tools/{tool}/restart`, `/stop` | Restart / stop a tool (needs `[restart.<tool>]`) |
| GET | `/api/setup` | Each part's checks and install steps |
| GET / POST | `/api/storage`, `/api/storage/clean` | Disk use and cleaning |
| GET | `/api/jobs[/{id}]` | Side jobs (fits, examples, motions) |
| GET | `/api/dummy`, `/api/dummy/animated.glb` | The test dummy |
| WS | `/api/ws` | Live events |
