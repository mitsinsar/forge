# Configuration

Forge has two levels of settings:

- **App settings** — where the tools are, ports, timeouts. In `backend/assetgen.toml` or
  environment variables. One per machine.
- **Style profiles** — how assets look and how much they cost. Edited in the app, one or more per
  game project.

## App settings (`assetgen.toml`)

Forge reads, in order (later wins):

1. built-in defaults,
2. `assetgen.toml` in the working directory (`backend/` when you run `uv run assetgen`), or the
   file named by `ASSETGEN_CONFIG`,
3. environment variables `ASSETGEN_<NAME>`, e.g. `ASSETGEN_PORT=9000`.

`assetgen.toml` is git-ignored; it's your machine's file. Restart Forge after changing it.

### A typical file

```toml
# The language model (LM Studio)
qwen_url   = "http://127.0.0.1:1234/v1"
qwen_model = "google/gemma-3-12b"        # the id `lms ls` prints

# Images
comfyui_url = "http://127.0.0.1:8188"
comfyui_dir = "C:/tools/ComfyUI"          # lets Forge copy LoRAs into models/loras

# Meshes
mesh_tool     = "hunyuan3d"
hunyuan3d_url = "http://127.0.0.1:8081"

# Rigging and Blender
unirig_path  = "C:/tools/UniRig"
python_path  = "C:/tools/UniRig/.venv/Scripts/python.exe"
blender_path = "C:/Program Files/Blender Foundation/Blender 4.5/blender.exe"

# Optional: let the Setup page (and Forge itself) start, stop and restart the tools
[restart.llm]
command = ["lms", "server", "start", "--port", "1234"]
stop    = ["lms", "server", "stop"]

[restart.image]
command = ["C:/tools/ComfyUI/venv/Scripts/python.exe", "main.py", "--port", "8188"]
cwd     = "C:/tools/ComfyUI"
port    = 8188

[restart.mesh]
command = ["cmd", "/c", "C:/tools/Hunyuan3D-2.1/start_server.bat"]
cwd     = "C:/tools/Hunyuan3D-2.1"
port    = 8081
```

### All settings

| Setting | Default | What it is |
|---|---|---|
| `host`, `port` | `127.0.0.1`, `8000` | Where the app listens |
| `data_dir` | `data` | Database, per-asset files, uploads |
| `frontend_dir` | `../frontend/dist` | The built web app served at `/` |
| `qwen_url` | `http://127.0.0.1:11434/v1` | OpenAI-compatible LLM server (LM Studio: port 1234) |
| `qwen_model` | `qwen3` | Model id; a vision model for the visual checks |
| `comfyui_url` | `http://127.0.0.1:8188` | ComfyUI |
| `comfyui_dir` | — | ComfyUI's folder (for copying LoRAs) |
| `comfyui_timeout` | `600` | Seconds one candidate may take |
| `comfyui_workflows_dir` | `backend/workflows` | API-format workflows per base model |
| `pose_controlnet` | `openpose_sdxl_xinsir.safetensors` | Pose guide model (empty: off) |
| `pose_strength` | `0.8` | How strongly the pose guide holds the A-pose |
| `mesh_tool` | `hunyuan3d` | `hunyuan3d` or `modly` |
| `hunyuan3d_url` | `http://127.0.0.1:8081` | Hunyuan3D 2.1 API server |
| `hunyuan3d_timeout`, `hunyuan3d_poll` | `1800`, `2` | Seconds per mesh job / between status checks |
| `modly_url`, `modly_model`, `modly_params`, `modly_timeout` | | The Modly alternative for meshes |
| `blender_path` | `blender` | Blender 4.5 LTS executable |
| `unirig_path`, `python_path` | `UniRig`, `python` | UniRig folder and its own Python |
| `unirig_timeout` | `900` | Seconds per UniRig step |
| `unirig_tries` | `3` | UniRig runs while the skeleton fits badly; best kept |
| `auto_retries` | `2` | Rounds of redrawing gate-1 candidates that fail checks |
| `hymotion_path`, `hymotion_python`, `hymotion_model` | | HY-Motion (skill animations) |
| `mvadapter_root`, `mvadapter_python`, `mvadapter_models`, `mvadapter_sdxl`, `mvadapter_timeout` | | MV-Adapter (multi-view meshes) |
| `wan_unet`, `wan_text_encoder`, `wan_vae` | | Wan 2.2 model files in ComfyUI (effects) |
| `[restart.<tool>]` | | `command`, `cwd`, and `port` or `stop`, for `llm`, `image`, `mesh` |

The Setup page shows, for each tool, which settings it read.

## Style profiles

Edit on **Style profiles** in the app. A profile belongs to a project; each asset uses its
project's default profile unless you pick another (and can override any field per asset).

| Section | Fields |
|---|---|
| **Models** | Language model, image model (`sdxl`, `flux`, `qwen-image`), 3D model (`hunyuan3d-2.1` or `hunyuan3d-2-mini` via Modly), *one model on the GPU at a time* |
| **Pictures** | Candidates per round, prompt suffix, negative prompt |
| **LoRAs** | LoRAs of the same base model, with weights (trigger words go at the front of prompts) |
| **References** | Up to 3 style reference images (IP-Adapter on SDXL, Redux on Flux) |
| **Mesh quality** | Quality preset, steps, surface resolution, guidance, remove floating pieces, multi-view, even out baked lighting |
| **Camera & sprites** | Camera preset, look (`hd` / `pixel`), fps, supersampling, palette size, frame size per kind |
| **Budgets** | Triangles and texture size per kind, LOD levels as shares of LOD0 |
| **Clips** | Clip set, extra clips for bipeds (e.g. generated `skill_*` clips) |
| **Autopilot** | Export by itself when everything passes |

### Image models

| Model | Licence | Notes |
|---|---|---|
| **SDXL** | Open RAIL++-M | Default. Supports the pose ControlNet, LoRAs and IP-Adapter references. |
| **Qwen-Image** | Apache 2.0 | Recommended: follows prompts much better. LoRAs (model only), no references. |
| **Flux.1 dev** | Non-commercial | LoRAs and Redux references. |

A workflow is "installed" when ComfyUI has every file its JSON in `backend/workflows/` loads. You
can rebuild a workflow in ComfyUI and export it with *Save (API)* as long as it keeps the node
titles `model`, `clip`, `positive`, `negative`, `latent`, `sampler` and `save`.
