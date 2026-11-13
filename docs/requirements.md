# Requirements

Forge runs entirely on your own machine. The app itself is light; the AI tools it drives are not.
This page lists what you need before you start. **[INSTALL.md](../INSTALL.md)** has the
step-by-step install, and the app's **Setup** page (`/setup`) checks each part live.

## Hardware

| | Minimum | Recommended | Notes |
|---|---|---|---|
| **GPU** | NVIDIA, 24 GB VRAM | NVIDIA, 32 GB VRAM (developed on an RTX 5090) | CUDA is required. Hunyuan3D with texturing needs about 24 GB; HY-Motion about 26 GB. AMD and Apple GPUs are not supported by the tools. |
| **System RAM** | 32 GB | 64 GB | Models that make way on the GPU are kept in RAM between steps. |
| **Disk** | 100 GB free | 200 GB+ on an SSD | Model weights (see below) plus the pipeline's files for each asset (candidates, meshes, rigs, sprites). |

Only **one model holds the GPU at a time** by default (a style profile option), so a single
24–32 GB card is enough: Forge unloads the language model before drawing, the image model before
building meshes, and so on.

## Operating system

- **Windows 10/11** — the main, tested platform (patches for UniRig on Windows are included).
- **Linux** — supported; use `venv/bin/python` wherever the docs show `venv/Scripts/python.exe`.
- **macOS** — the Forge app and its tests run, but the GPU tools (Hunyuan3D, UniRig, HY-Motion)
  need CUDA, so you can't produce assets on a Mac.

## Software for the Forge app

| Tool | Version | Used for |
|---|---|---|
| [git](https://git-scm.com) | any | getting the code |
| [uv](https://docs.astral.sh/uv/) | recent | the Python backend (it installs Python 3.12 for you) |
| [Node.js](https://nodejs.org) | 20 or later | building the web app |

## The tools Forge drives

Each tool runs on its own (as a server or as a command Forge calls). Install them in this order;
the Setup page turns each row green when it answers.

| Tool | Required? | What it does in Forge | GPU memory | Download size |
|---|---|---|---|---|
| **LM Studio** + a vision model (Gemma 3/4, Qwen2.5-VL) | required | Writes image prompts, inspects candidates, judges pictures, meshes and motions, varies descriptions | up to ~16 GB (Gemma 4 26B) | 8–20 GB |
| **ComfyUI** + SDXL base | required | Draws the four gate-1 candidates | — | 6.9 GB |
| ↳ OpenPose ControlNet (SDXL) | optional | Holds characters in an A-pose so arms don't fuse to the body | — | 2.5 GB |
| ↳ Qwen-Image | optional, recommended | Follows prompts far better than SDXL | ~21 GB | ~30 GB |
| ↳ Wan 2.2 TI2V 5B | needed for skill effects | Animates effect flipbooks | — | — |
| **Hunyuan3D 2.1** (with Forge's patch) | required | Image → textured mesh (shape + PBR texture) | ~24 GB with texturing | models download on first run |
| **UniRig** | required | Predicts the skeleton and skin weights | — | checkpoints download on first run |
| **Blender 4.5 LTS** | required | Clean-up, LODs, retargeting clips, sprite rendering, FBX export | — | — |
| **HY-Motion 1.0** | optional | Text → skill animation for bipeds | ~26 GB | 4 GB + 17.7 GB text encoders |
| **MV-Adapter** + Hunyuan3D-2mv | optional | Multi-view meshes (better backs) | ~14 GB | 3.6 GB + 5 GB |

Any OpenAI-compatible server can replace LM Studio (Ollama works), but it needs a model that can
**see images**, or the visual checks are skipped with a "not checked" warning.

## Game engines (for the exports)

| Engine | Version checked | What Forge writes |
|---|---|---|
| **Unity** | Unity 6 LTS (6000.3) with URP | Rigged FBX with LODs, albedo texture, `asset.json` and an editor import script that builds prefabs, materials and animator controllers |
| **Godot** | Godot 4.x (checked with 4.7.2) | Sprite sheets, `SpriteFrames` resources, a `PaperDoll.tscn` scene; effects get a ready `.tscn` + `.gd` |

Neither engine has to be running; Forge writes straight into the project folder you register.

## Licences to check before you ship

Forge itself is Apache 2.0. The models it drives each have their own licence, and some restrict
use:

- **Hunyuan3D 2.1** and **HY-Motion 1.0** — Tencent community licences; not for use in the EU,
  UK or South Korea, and with conditions above a user threshold.
- **Flux.1 dev** — non-commercial.
- **SDXL** — CreativeML Open RAIL++-M. **Qwen-Image** — Apache 2.0.
- **LoRAs** you add carry their own terms; Forge stores a licence and "commercial use" flag for each.
- The bundled archetype animations are Quaternius's **CC0** packs.

Read each one before you ship assets made with it.
