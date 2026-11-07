# Forge

Forge turns a text description into a game-ready, animated 3D asset on your own machine, with you
approving each step. Everything runs locally on one NVIDIA GPU; no cloud service is involved.

```
describe ─► language model writes the prompt ─► ComfyUI draws 4 candidates ─► gate 1: pick one
        ─► Hunyuan3D builds a textured mesh ─► Blender cleans it, makes LODs ─► gate 2: approve
        ─► UniRig rigs it, Blender plays the clips (or renders sprites) ─► gate 3: approve
        ─► export to a Unity or Godot project
```

- **Kinds**: heroes, monsters, bosses, gear and weapons; six body types (biped, quadruped, flyer,
  serpent, spider, blob), each with idle, walk, run, attack, hit and death clips.
- **Unity**: rigged FBX with LODs, an `asset.json` sidecar and an editor script that builds prefabs.
  **Godot**: paper-doll sprite sheets, SpriteFrames and a ready scene.
- **Style profiles** hold the look (LoRAs, reference images, render settings) and budgets
  (triangles, texture size, LODs) so a whole game's assets match.
- **Checks at every gate** (cut-off figures, floor plates, skeleton fit, stretching, limbs that
  don't follow the animation), with redraws, an autopilot for batches, a joint editor, colour
  variants, outfits and a test dummy for gear.

## Install

See **[INSTALL.md](INSTALL.md)**: the Forge app itself, then the tools it drives. Once the app
runs, its **Setup** page (`http://127.0.0.1:8000/setup`) checks each tool live, shows the install
steps for anything missing, and has buttons to restart or stop the tools.

Short version, for the app:

```
cd frontend && npm ci && npm run build
cd ../backend && uv run assetgen        # http://127.0.0.1:8000
```

## Use it from scripts

Everything the web app does goes through Forge's HTTP API, so scripts, game tools or other apps
can drive it the same way. The interactive reference is at `http://127.0.0.1:8000/docs`
(OpenAPI schema at `/openapi.json`); live progress comes over the WebSocket at `/api/ws`.

```
# Create a hero, let the autopilot take it through the gates, then read it back
curl -X POST http://127.0.0.1:8000/api/assets -H "Content-Type: application/json" \
     -d '{"project_id": "<project id>", "kind": "hero", "description": "a knight in red plate armour", "autopilot": true}'
curl http://127.0.0.1:8000/api/assets/<asset id>

# The tools: status, restart, stop, and what is missing
curl http://127.0.0.1:8000/api/tools
curl -X POST http://127.0.0.1:8000/api/tools/image/restart
curl -X POST http://127.0.0.1:8000/api/tools/mesh/stop
curl http://127.0.0.1:8000/api/setup
```

## Layout

| Folder | What is there |
|---|---|
| `backend/` | The FastAPI app (`src/assetgen`), Blender scripts, ComfyUI workflows, patches for Hunyuan3D and UniRig, tests. See [backend/README.md](backend/README.md). |
| `frontend/` | The React web app. See [frontend/README.md](frontend/README.md). |
| `INSTALL.md` | Installing everything, step by step (generated from `backend/src/assetgen/setup_guide.py`). |

Tests: `cd backend && uv run pytest` (Blender tests run when Blender is found; the rest need no GPU).

## Licence

Forge is licensed under the [Apache License 2.0](LICENSE): anyone may use, change and share it,
including commercially, as long as they keep the copyright and licence notices ([NOTICE](NOTICE))
and mark files they change.

The archetype animations are Quaternius's CC0 packs (see each `archetypes/<id>/LICENCES.md`).
The tools and models Forge drives (ComfyUI, SDXL, Hunyuan3D, UniRig, MV-Adapter, the language
model) are not part of this repository and each comes with its own licence; some, such as
Hunyuan3D's community licence, limit where and how they may be used. Read them before you ship
assets made with them.
