# assetgen — asset generator backend

FastAPI + SQLite backend of the asset generator. A text description becomes a game-ready,
animated asset through three approval gates, then is exported into a Unity or Godot project.

```
create ─► prompt ─► gate 1 (pick 1 of 4 images) ─► mesh ─► gate 2 (approve mesh + LODs)
       ─► rig ─► gate 3 (approve animated result) ─► export ─► done (+ zip)
```

## Run

```
cd backend
uv run assetgen          # http://127.0.0.1:8000, API docs at /docs
uv run pytest
```

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 if needed). Works the same on
Windows, macOS and Linux.

The web app lives in `../frontend` (see its README). When `frontend/dist` exists (after
`npm run build` there), `uv run assetgen` also serves the app at `/`; the `/api` routes always
come first. The folder can be moved with the `frontend_dir` setting.

## Mock-first

ComfyUI, Hunyuan3D, Blender, UniRig and Qwen each sit behind an adapter
(`src/assetgen/adapters/base.py`). Today every adapter is a mock that returns generated
placeholders, so the whole API works end to end without a GPU:

| Tool | Mock output |
|---|---|
| Qwen (LLM) | archetype, slot / weapon class and prompt from keyword rules; forces the archetype's rest pose; folds in reject notes |
| ComfyUI (image) | N candidate PNGs (default 4) of a placeholder body, coloured after colour words in the description |
| Hunyuan3D (mesh) | textured GLB built from ellipsoids in the picked candidate's colour |
| Blender | clean-up to the triangle budget with LOD0/1/2 (100 / 50 / 20 %) and triangle counts; animated GLB with the clip set; sprite frames per clip and direction; a placeholder FBX |
| UniRig (rig) | the archetype's template joints with a fit score |

The engine exports (Unity folder, Godot atlases / SpriteFrames / PaperDoll, Aseprite JSON, zip)
are real code and run the same with mock or real adapters. `GET /api/meta` reports each adapter's
mode. Asking for a real adapter that is not built yet stops the server at start-up with a clear
message.

## Settings

Defaults, overridden by `assetgen.toml` in the working directory (or the file in `ASSETGEN_CONFIG`),
then by environment variables `ASSETGEN_<NAME>`:

```toml
host = "127.0.0.1"
port = 8000
data_dir = "data"              # SQLite database, data/assets/<id>/<stage>/, uploads
mock_step_delay = 0.15         # seconds per mock step, so progress is visible
comfyui_url = "http://127.0.0.1:8188"
qwen_url = "http://127.0.0.1:11434/v1"
qwen_model = "qwen3"
hunyuan3d_url = "http://127.0.0.1:8081"
blender_path = "blender"
unirig_path = "UniRig"
python_path = "python"
frontend_dir = "../frontend/dist" # built web app served at / (default: the repo's frontend/dist)

[adapters]                     # "mock" or "real" each
llm = "mock"
image = "mock"
mesh = "mock"
rig = "mock"
blender = "mock"
```

Environment example: `ASSETGEN_PORT=9000`, `ASSETGEN_ADAPTERS__MESH=real`.

## Jobs and files

A single in-process worker runs one job (one stage of one asset) at a time, oldest first. Each
stage writes to `data/assets/<id>/<stage>/` (`prompt`, `mesh`, `rig`, `sprites`, `export`) and
ends with a `manifest.json`. For Godot projects the rig stage also renders the sprite frames
(into `sprites/`, listed under `outputs.rig.sprites` with fps, frame size, directions and the
mirrored ones) so gate 3 can play them; the export reuses those frames. After a restart, assets that were running go back in the queue; a
stage with a manifest is not redone and the adapters skip files that already exist, so work
resumes where it stopped. Approving a gate keeps everything upstream; rejecting reruns only that
stage. The image model is unloaded before the mesh stage, so it never shares the GPU with
Hunyuan3D.

Exports go into the project's folder:

* **Unity** — `<path>/Assets/Generated/<kind>/<Name>/`: `<Name>.fbx` (mesh, skeleton, clips,
  LODs; Humanoid for bipeds, Generic otherwise), `Materials/<Name>.mat` (URP Lit),
  `Textures/<Name>_Albedo.png` (+ `.meta`), `<Name>.prefab` for weapons (grip offset) and
  `asset.json` for the editor import script (LODs, clips, slot and hidden body parts for gear,
  socket and grip for weapons).
* **Godot** — `<path>/generated/<kind>/<Name>/` (`res://generated/<kind>/<Name>/`): one atlas PNG
  and Aseprite JSON per layer and direction, `<layer>.tres` SpriteFrames (one animation per
  `<clip>_<direction>`, fps and loop set), `PaperDoll.tscn` and `asset.json`. Layers are `body`
  for characters, the slot for gear and `weapon` for weapons. Mirrored directions (for example
  `left` on the side camera) are drawn with `flip_h`.

`<Name>` is the asset's `export_name`, picked at its first export and kept: the asset's name made
safe for file systems, with a short id suffix (`Grey_Wolf_1a2b3c`) when another asset of the same
project and kind already has that folder, or when a folder that assetgen did not write for this
asset is already there. Re-export replaces only the asset's own folder; the copy step refuses to
delete a folder whose `asset.json` belongs to another asset. The same folder is zipped for
`GET /api/assets/{id}/download`.

## API

All routes are under `/api`; `/docs` has every request and response field.

| Method | Path | What it does |
|---|---|---|
| GET | `/api/meta` | Adapter modes, style-profile defaults, kinds, engines, base models, camera presets and directions, slots, weapon classes |
| GET | `/api/archetypes` | Archetype templates (biped, quadruped, flyer, spider, serpent, blob): rest pose, rig type, joints, clips |
| GET | `/api/archetypes/{id}` | One archetype |
| GET | `/api/projects` | List game projects |
| POST | `/api/projects` | Register a project: `name`, `engine` (`unity`\|`godot`), `path`, `default_profile_id` (a "Default" profile is made when left out) |
| GET | `/api/projects/{id}` | Get a project |
| PATCH | `/api/projects/{id}` | Update a project |
| DELETE | `/api/projects/{id}` | Delete a project (409 while it has assets) |
| GET | `/api/profiles?project_id=` | List style profiles |
| POST | `/api/profiles` | Create a profile: `name`, `project_id`, `base_model`, `prompt_suffix`, `negative_prompt`, `loras[]` (`id`, `weight`; only LoRAs of the same base model), `camera_preset`, `candidates`, `clips`, `biped_extra_clips`, `render` (`look`, `fps`, `supersample`, `palette_size`, `frame_size` per kind), `budgets` (`triangles` and `texture` per kind, `lod_ratios`). Missing fields take the defaults |
| GET | `/api/profiles/{id}` | Get a profile |
| PATCH | `/api/profiles/{id}` | Update any profile fields (nested fields merge) |
| DELETE | `/api/profiles/{id}` | Delete a profile (409 while it is a project's default) |
| POST | `/api/profiles/{id}/references` | Upload a reference image (multipart `file`; PNG, JPEG, WebP; at most 3) |
| GET | `/api/profiles/{id}/references/{ref_id}` | Download a reference image |
| DELETE | `/api/profiles/{id}/references/{ref_id}` | Remove a reference image |
| GET | `/api/loras?base_model=` | List LoRAs (`file_exists` tells whether the path is there) |
| POST | `/api/loras` | Register a LoRA by path: `name`, `path`, `base_model`, `tags`, `licence`, `commercial_use`, `source_url` |
| POST | `/api/loras/upload` | Upload a LoRA (multipart `file` + the same fields as form fields; `tags` comma-separated) |
| GET | `/api/loras/{id}` | Get a LoRA |
| PATCH | `/api/loras/{id}` | Update a LoRA (base model change refused while profiles use it) |
| DELETE | `/api/loras/{id}` | Delete a LoRA (removed from profiles; uploaded file deleted) |
| GET | `/api/assets` | List assets, newest first; filters `project_id`, `kind`, `status`, `archetype`, `training_candidate`, `q` (text) |
| POST | `/api/assets` | Create an asset and queue it: `project_id`, `kind` (`hero`\|`monster`\|`boss`\|`gear`\|`weapon`), `description`, `name`, `archetype` (override), `slot` (gear), `weapon_class` (weapons), `profile_id`, `overrides` (any profile fields) |
| GET | `/api/assets/{id}` | One asset with its outputs, file URLs and effective settings |
| DELETE | `/api/assets/{id}` | Delete an asset and its files |
| POST | `/api/assets/{id}/gates/{n}/approve` | Approve gate `n` (1–3) and queue the next stage; gate 1 needs `{"candidate": k}` |
| POST | `/api/assets/{id}/gates/{n}/reject` | Reject gate `n` and rerun only that stage: `note` (folded into the next attempt), `archetype` (gate 1), `grip` (`socket`, `position`, `rotation`; gate 3, weapons) |
| POST | `/api/assets/{id}/retry` | Retry a failed stage (resumes its files) |
| POST | `/api/assets/{id}/export` | Re-export a finished asset (overwrites the project folder) |
| PUT | `/api/assets/{id}/training-candidate` | `{"value": true\|false}`: LoRA training-candidate flag (set automatically when an asset is done) |
| GET | `/api/assets/{id}/download` | The export as a zip |
| GET | `/api/assets/{id}/files/{path}` | Any file the pipeline made (candidates, GLBs, sprites, export files); the asset's `urls` point here |
| WS | `/api/ws` | Live events (a plain GET answers 426 and documents them in `/docs`) |

Errors: 404 unknown id, 409 wrong state (for example approving a gate the asset is not waiting
at), 422 invalid input.

### Asset status

`status` is `queued`, `running` (with `step` text and `progress` 0–1), `waiting` (at `gate` 1, 2
or 3), `done` or `failed` (with `error`); `stage` is `prompt`, `mesh`, `rig` or `export`.

### WebSocket events

```jsonc
{"type": "hello"}                                           // once, on connect
{"type": "status", "asset_id": "…", "status": "waiting", "stage": "prompt", "gate": 1,
 "step": "Waiting at gate 1", "progress": 1.0, "error": null, "at": "…", "asset": { … }}
{"type": "progress", "asset_id": "…", "status": "running", "stage": "mesh",
 "step": "Decimating LOD1", "progress": 0.62, "gate": null, "error": null, "at": "…"}
{"type": "deleted", "asset_id": "…", "at": "…"}
```

A `status` event (with the full asset) is sent on every change: created, queued, running,
waiting, done, failed, approve, reject, retry, re-export and the training-candidate toggle.

## Layout

```
src/assetgen/
  main.py          uv run assetgen
  app.py           FastAPI app and lifespan (database, worker, event hub)
  api.py           routes
  schemas.py       request bodies
  pipeline.py      stages, gates and the job worker
  exporters.py     Unity folder, Godot sprites, zip
  adapters/        base.py (interfaces), mock/ (placeholders)
  archetypes/<id>/archetype.json
  config.py  db.py  defaults.py  events.py
tests/             pytest, all on mock adapters
```
