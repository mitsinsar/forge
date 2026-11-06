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
| Hunyuan3D (mesh) | textured GLB built from ellipsoids in the picked candidate's colour (real adapter: see below) |
| Blender (real: clean-up, LODs, sprites, FBX) | clean-up to the triangle budget with LOD0/1/2 (100 / 50 / 20 %) and triangle counts; animated GLB with the clip set; sprite frames per clip and direction; a placeholder FBX |
| UniRig (rig) | the archetype's template joints with a fit score |

The engine exports (Unity folder, Godot atlases / SpriteFrames / PaperDoll, Aseprite JSON, zip)
are real code and run the same with mock or real adapters. `GET /api/meta` reports each adapter's
mode. Asking for a real adapter that is not built yet stops the server at start-up with a clear
message.

## Real adapters

### Hunyuan3D 2.1 (mesh, gate 2)

Set `[adapters] mesh = "real"` and `hunyuan3d_url` to the machine running Hunyuan3D's
`api_server.py`. For each asset the adapter:

1. cuts the picked candidate out of its plain background (a flood fill from the border, or
   [rembg](https://github.com/danielgatis/rembg) when it is installed:
   `uv pip install "rembg[gpu]"`) and saves it as `mesh/input_cutout.png`;
2. sends it to `POST /send` with shape and texture on, and saves the job id in
   `mesh/hunyuan3d_job.json`, so after a restart the same job is polled instead of a new one
   being started;
3. polls `GET /status/{uid}` (`processing` → `texturing` → `completed`) and keeps the GLB as
   made in `mesh/hunyuan3d.glb`;
4. scales its albedo to the profile's texture size, evens out the baked light and shade when the
   profile's `delight` flag is on (the default; "Even out baked lighting" in the profile editor),
   and writes `mesh/texture.png` and `mesh/raw.glb` with that texture inside.

Errors name the cause: server not reachable, out of GPU memory, or no result within
`hunyuan3d_timeout`. Hunyuan3D's server does not report a crashed job (it stays
`processing`), so a crash shows up as the timeout; its console has the reason.
With `blender = "real"` the mesh is cleaned up and decimated into its LODs as usual; with the
mock Blender, gate 2 shows Hunyuan3D's mesh as made, as a single LOD0. The mock rig animates
a placeholder body of the archetype until the real rig adapter exists.

To compare the de-light pass, untick or tick it in the profile and reject the mesh at gate 2
(a reject makes a new mesh with a new seed).

**Starting the server on Windows** (the RTX 5090 machine, CUDA 12.4+, Python 3.10, Git):

```powershell
git clone https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1
cd Hunyuan3D-2.1
py -3.10 -m venv .venv
.venv\Scripts\activate
pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
# Build the texture extensions, as the repo's README describes:
cd hy3dpaint\custom_rasterizer; pip install -e . ; cd ..\..
cd hy3dpaint\DifferentiableRenderer; python setup.py install ; cd ..\..
# The upscaler the texture step uses:
curl.exe -L -o hy3dpaint\ckpt\RealESRGAN_x4plus.pth https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth
python api_server.py --host 0.0.0.0 --port 8081 --model_path tencent/Hunyuan3D-2.1 --subfolder hunyuan3d-dit-v2-1
```

The first start downloads the models from Hugging Face. Add `--low_vram_mode` if jobs run out
of GPU memory. `http://<machine>:8081/docs` lists the API and `/health` answers
`{"status": "healthy"}`; the header of the web app shows the same check. Allow port 8081 in the
Windows firewall when assetgen runs on another machine. Keep ComfyUI's models unloaded while
it runs: assetgen unloads them before every mesh stage.

## Settings

Defaults, overridden by `assetgen.toml` in the working directory (or the file in `ASSETGEN_CONFIG`),
then by environment variables `ASSETGEN_<NAME>`:

```toml
host = "127.0.0.1"
port = 8000
data_dir = "data"              # SQLite database, data/assets/<id>/<stage>/, uploads
mock_step_delay = 0.15         # seconds per mock step, so progress is visible
comfyui_url = "http://127.0.0.1:8188"
comfyui_dir = ""               # ComfyUI folder (with models/); LoRAs it lacks are copied to models/loras
comfyui_timeout = 600          # seconds one candidate may take in ComfyUI before the stage fails
comfyui_workflows_dir = ""     # API-format workflows per base model (empty: backend/workflows)
qwen_url = "http://127.0.0.1:11434/v1"   # Ollama; LM Studio: "http://127.0.0.1:1234/v1"
qwen_model = "qwen3"                       # LM Studio: the model id it lists, e.g. "qwen3-14b"
hunyuan3d_url = "http://127.0.0.1:8081"
hunyuan3d_timeout = 1800       # seconds one image-to-mesh job may take
hunyuan3d_poll = 2             # seconds between status checks
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

## ComfyUI (image adapter)

With `[adapters] image = "real"`, gate 1's candidates come from ComfyUI at `comfyui_url`. Each
style profile's `base_model` picks an API-format workflow in `workflows/`:

| Workflow | Model files (folder under ComfyUI's `models/`) | LoRAs | Reference images |
|---|---|---|---|
| `sdxl.json` | `sd_xl_base_1.0.safetensors` (`checkpoints/`) | `LoraLoader` (model + CLIP) | IP-Adapter, needs the [ComfyUI_IPAdapter_plus](https://github.com/cubiq/ComfyUI_IPAdapter_plus) nodes and their **PLUS (high strength)** SDXL files: `ip-adapter-plus_sdxl_vit-h.safetensors` (`ipadapter/`) and `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` (`clip_vision/`) |
| `flux.json` | `flux1-dev.safetensors` (`diffusion_models/`), `t5xxl_fp16.safetensors` and `clip_l.safetensors` (`text_encoders/`), `ae.safetensors` (`vae/`) | `LoraLoader` (model + CLIP) | Flux Redux (core nodes): `flux1-redux-dev.safetensors` (`style_models/`) and `sigclip_vision_patch14_384.safetensors` (`clip_vision/`) |
| `qwen-image.json` | `qwen_image_fp8_e4m3fn.safetensors` (`diffusion_models/`), `qwen_2.5_vl_7b_fp8_scaled.safetensors` (`text_encoders/`), `qwen_image_vae.safetensors` (`vae/`) | `LoraLoaderModelOnly` | none: Qwen-Image text-to-image has no reference input, so they are skipped |

How a render goes:

* One prompt per missing candidate (seed + n − 1) is queued with `POST /prompt`; progress comes
  over the `/ws` WebSocket (sampler steps), the image is fetched with `/history` and `/view` and
  written as `candidate_<n>.png`. Candidates already on disk are kept, so a retried stage only
  renders the missing ones.
* The profile's LoRAs (only those of the same base model) are chained after the model loader
  with their weights. ComfyUI's `GET /models/loras` is matched by file name; a LoRA ComfyUI does
  not have is copied into `<comfyui_dir>/models/loras` (set `comfyui_dir`, or copy it there
  yourself).
* The profile's 2–3 reference images are uploaded with `/upload/image` (into `input/assetgen/`)
  and applied as style references. When the model or ComfyUI can't use them, the render goes on
  without them.
* What was used or skipped is kept in the prompt stage's `log` and shown at gate 1.
* Before the mesh stage, `POST /free` with `unload_models` and `free_memory` empties ComfyUI's VRAM
  for Hunyuan3D.
* Clear stage errors: ComfyUI not reachable, a missing model file (with the folder it goes in, from
  ComfyUI's validation answer), a missing node, an error while running, and a candidate taking longer
  than `comfyui_timeout` (the prompt is then removed from ComfyUI's queue and interrupted).

The adapter finds the nodes it fills by title, so a workflow can be rebuilt in ComfyUI and exported
with "Save (API)" as long as it keeps these node titles: `model` (MODEL loader, or the checkpoint
loader), `clip` (CLIP loader, unless `model` is a checkpoint), `positive`, `negative`, `latent`,
`sampler` (`KSampler`) and `save` (`SaveImage`). Size, steps, CFG and sampler are read from the file.

## Real Blender

With `[adapters] blender = "real"`, headless Blender (4.5 LTS or later, at `blender_path`) runs the
scripts in `blender_scripts/` as `blender -b --factory-startup -P <script> -- <args.json> <result.json>`:

- `cleanup_lods.py` (mesh stage): merges by distance, removes loose geometry, floating debris and
  interior faces, fixes normals, scales to the kind's standard size (hero and gear 1.8 m tall,
  monster 1.5 m, boss 2.7 m, weapon 1.0 m long) with the origin at the feet, decimates to the
  triangle budget and makes the LODs at the profile's `lod_ratios`.
- `export_fbx.py` (Unity export): mesh, armature and one take per clip, plus `<name>_LOD1`,
  `<name>_LOD2` beside `<name>_LOD0` (skinned like LOD0) for Unity's LOD Group; Y up, scale 1,
  no leaf bones, baked animation.
- `render_sprites.py` (Godot, rig stage): Eevee on a transparent background with an
  orthographic camera from each direction of the camera preset (`side`: right only, mirrored
  with `flip_h`; `three_quarter`: down-right and up-right, tilted 30°; `topdown_4` and
  `topdown_8`: 4 or 8 directions, tilted 50°). One framing holds the animation's full bounds over
  every clip and direction, with the feet centred on the same row, so the character keeps its
  size and ground line in every frame. Toon look: three flat light bands (Shader to RGB through a
  constant ramp) times the albedo, and a one-pixel dark outline (inverted hull). Frames are
  sampled at the profile's `fps` per clip and rendered at `frame_size × supersample`; the
  backend then downscales them with Pillow: smooth (Lanczos) for the `hd` look, nearest
  neighbour with hard alpha and one shared `palette_size` palette over all frames for `pixel`.
  Fully transparent frames at the end of a clip that does not loop are dropped. A
  `sprites.json` beside the frames lets a resumed or repeated render with the same settings
  reuse them.

Blender's output is kept as `blender_<script>.log` in the stage folder, its `PROGRESS` lines become
the asset's progress, and a failure puts the log's last lines in the asset's error. Rigging still uses the mock until
its real version lands.

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
  LODs), `Textures/<Name>_Albedo.png` (+ `.meta`) and `asset.json` for the editor import script
  (rig type, LODs, clips, slot, bones and hidden body parts for gear, socket and grip for weapons).
  The first export also puts the import script into the project (see below), which makes
  `<Name>.prefab`, `Materials/<Name>.mat` and `<Name>.controller` beside them in Unity.
* **Godot** — `<path>/generated/<kind>/<Name>/` (`res://generated/<kind>/<Name>/`): one atlas PNG
  and Aseprite JSON per layer and direction, `<layer>.tres` SpriteFrames (one animation per
  `<clip>_<direction>`, fps and loop set), `PaperDoll.tscn` and `asset.json`. Layers are `body`
  for characters, the slot for gear and `weapon` for weapons. Mirrored directions (for example
  `left` on the side camera) are drawn with `flip_h`.

`<Name>` is the asset's `export_name`, picked at its first export and kept: the asset's name made
safe for file systems, with a short id suffix (`Grey_Wolf_1a2b3c`) when another asset of the same
project and kind already has that folder, or when a folder that assetgen did not write for this
asset is already there. Re-export replaces only the asset's own folder; the copy step refuses to
delete a folder whose `asset.json` belongs to another asset. In a Unity folder a re-export keeps the
`.meta` files Unity wrote and the prefab, controller and material the import script made, so their
GUIDs stay and scenes keep their references. The same folder is zipped for
`GET /api/assets/{id}/download`.

### Unity import script

Every Unity export checks the game project for the editor import script and writes it when it is
missing or older than the one bundled with assetgen (`src/assetgen/unity/`, versioned by the
`assetgen-importer-version` line at the top; a newer copy in the project is left alone). The export's
`importer` output lists what was written. Nothing has to be set up in the game project:

| File in the project | What it is |
|---|---|
| `Assets/Generated/Editor/AssetForgeImporter.cs` | The `AssetPostprocessor` that turns each export folder into a prefab |
| `Assets/Generated/Editor/AssetForge.Editor.asmdef` | Editor-only assembly (`includePlatforms: ["Editor"]`), so builds never include it |
| `Assets/Generated/Runtime/AssetForgeGear.cs` + `AssetForge.Runtime.asmdef` | The small component on gear prefabs that binds them to a character's bones |

Written for Unity 6 LTS with URP (checked with 6000.3). When Unity imports `<Name>.fbx` and its
`asset.json`:

1. **Model import** — material import off, scale 1. `rig` `Humanoid` makes a Humanoid avatar from the
   model, anything else Generic; weapons are imported without a rig or clips, gear as Generic without
   an avatar. Each entry of `clips` becomes a clip of that name, matched to the FBX take of the same
   name (`idle`, or `Armature|idle`), with its loop flag and cut to its `duration`. A change to
   `asset.json` re-imports the FBX.
2. **Material** — `Materials/<Name>.mat`, URP Lit with `Textures/<Name>_Albedo.png` as base map
   (Standard shader, with a warning, when URP is missing).
3. **Animator controller** (heroes, monsters, bosses) — `<Name>.controller` with one state per clip,
   `idle` as the default state.
4. **Prefab** — `<Name>.prefab`: the FBX as child `Model` with the material on every renderer and
   the Animator (controller and avatar) on it; a `LODGroup` on the root with LOD0/1/2 from the
   `<Name>_LOD<n>` meshes. Level *n* is shown down to a screen height of 0.5 × ratio(*n*+1) / ratio(0),
   so 25 % and 10 % for the default ratios 1, 0.5, 0.2, and the last level culls at 1 %.
   * **Weapons** — the prefab root is the grip: `Model` sits at minus the grip `position`, turned
     back by its `rotation`. Parent the prefab to the `weapon_r` (or `weapon_l`, see `socket`) bone
     with zero local position and rotation.
   * **Gear** — the skinned meshes keep their bones, and an `AssetForgeGear` component (slot,
     `hide_body`) binds them to the wearer's bones by name when the gear is parented under a
     character (it looks for the nearest `Animator` above it), or when `BindTo(characterRoot)` is
     called. Prefixes such as `mixamorig:` are ignored when matching. The import warns about bones
     that are not in the biped skeleton (`gear.bones` in `asset.json`).

A re-export overwrites the files and Unity re-imports them; the prefab is opened and updated in place
(the `Model` child stays a nested instance of the FBX), as are the controller's states and the
material, so their GUIDs and the prefab root do not change. **Tools → AssetForge → Rebuild All
Prefabs** (or `-executeMethod AssetForge.Editor.AssetForgeImporter.RebuildAll`) rebuilds every
folder by hand.

#### Checking the import script in Unity (batch mode)

On a Mac with Unity 6 from Unity Hub and Blender installed, from `backend/`:

```sh
UNITY=/Applications/Unity/Hub/Editor/6000.3.27f2/Unity.app/Contents/MacOS/Unity
TEMPLATE="$(dirname "$UNITY")/../Resources/PackageManager/ProjectTemplates/com.unity.template.3d-cross-platform-17.0.14.tgz"
GAME=/tmp/UrpGame

# 1. A fresh URP project (the "Universal 3D" template)
"$UNITY" -batchmode -nographics -quit -createProject "$GAME" -cloneFromTemplate "$TEMPLATE" -logFile -

# 2. Export a hero, a one-handed sword and a helmet into it. --skinned gives the hero and the helmet
#    a skinned biped skeleton (the mock rig has none, and Humanoid needs real bones).
uv run python tests/unity/export_samples.py "$GAME" --data /tmp/unity-samples --skinned

# 3. Import in Unity and check every prefab (writes a report; exit code 1 when a check fails)
mkdir -p "$GAME/Assets/Editor" && cp tests/unity/AssetForgeCheck.cs "$GAME/Assets/Editor/"
"$UNITY" -batchmode -nographics -projectPath "$GAME" -executeMethod AssetForgeCheck.Run \
  -afReport /tmp/report1.json -quit -logFile /tmp/unity1.log

# 4. Re-export the same assets and check again: same GUIDs, and the scene the first check saved
#    (Assets/AssetForgeCheck.unity) still holds the three prefabs
uv run python tests/unity/export_samples.py "$GAME" --data /tmp/unity-samples --skinned --reexport
"$UNITY" -batchmode -nographics -projectPath "$GAME" -executeMethod AssetForgeCheck.Run \
  -afReport /tmp/report2.json -quit -logFile /tmp/unity2.log
```

The check fails unless: every folder has a prefab with one `LODGroup` of three levels and one URP
Lit material with the albedo; the hero has a valid Humanoid avatar and an Animator whose states are
every clip of `asset.json` (same lengths and loop flags, `idle` first); the weapon's model sits at
the grip and is imported without a rig; the helmet has `AssetForgeGear` and skinned meshes, and binds
to an instance of the hero with no bone missing; and, on the second run, the scene's instances still
point at their prefabs. Compare `prefab_guid`, `root_id` and `fbx_guid` in the two reports to see
the in-place update.

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
| POST | `/api/profiles` | Create a profile: `name`, `project_id`, `base_model`, `prompt_suffix`, `negative_prompt`, `loras[]` (`id`, `weight`; only LoRAs of the same base model), `camera_preset`, `candidates`, `clips`, `biped_extra_clips`, `render` (`look`, `fps`, `supersample`, `palette_size`, `frame_size` per kind), `budgets` (`triangles` and `texture` per kind, `lod_ratios`), `delight` (even out the light baked into Hunyuan3D's albedo). Missing fields take the defaults |
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
| POST | `/api/assets/from-image` | Create an asset from your own image (multipart: `file` as PNG, JPEG or WebP up to 30 MB, and `asset` holding the same JSON as `POST /api/assets`). No image model runs: gate 1 offers that image as the only candidate. The LLM is asked only when the archetype (or gear slot, or weapon class) is left automatic |
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
  exporters.py     Unity folder (and the import script it ships), Godot sprites, zip
  unity/           the Unity editor import script and gear component written into Unity projects
  adapters/        base.py (interfaces), mock/ (placeholders), real/ (qwen.py, comfyui.py, hunyuan3d.py, blender.py)
  imaging.py       background removal, de-light pass, GLB albedo texture
  archetypes/<id>/archetype.json
  config.py  db.py  defaults.py  events.py
blender_scripts/   bpy scripts run by the real Blender adapter
workflows/         ComfyUI API-format workflows: sdxl.json, flux.json, qwen-image.json
tests/             pytest: mock adapters, the real ones against fake Qwen, ComfyUI and Hunyuan3D servers,
                   and the Blender scripts when Blender is installed (skipped otherwise)
tests/unity/       the batch-mode check of the Unity import script (not run by pytest)
```
