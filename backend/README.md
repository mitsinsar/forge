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

## The tools

ComfyUI, Hunyuan3D, Blender, UniRig and Qwen each sit behind an adapter
(`src/assetgen/adapters/base.py`). The app always uses the real ones in `adapters/real/`, so every
stage needs its tool running; the header of the web app (`GET /api/tools`) shows which answer:

| Tool | Stage | Needs |
|---|---|---|
| Qwen (LLM) | prompt | an OpenAI-compatible server (LM Studio or Ollama) at `qwen_url` with `qwen_model` |
| ComfyUI (image) | prompt (skipped for an uploaded image) | ComfyUI at `comfyui_url` with the workflow's models |
| Hunyuan3D or Modly (mesh) | mesh | `mesh_tool = "hunyuan3d"`: `api_server.py` at `hunyuan3d_url`; `mesh_tool = "modly"`: the Modly app at `modly_url` |
| Blender | mesh, rig (sprites), export | Blender 4.5 LTS at `blender_path` |
| UniRig (rig) | rig | UniRig at `unirig_path` with its own Python at `python_path` (setup below) |

A stage whose tool is down fails with a readable error; fix the tool and press Retry.

The engine exports (Unity folder, Godot atlases / SpriteFrames / PaperDoll, Aseprite JSON, zip)
are plain code. The tests swap the tools for the GPU-free doubles in `tests/fakes/` (placeholder
images, ellipsoid meshes and animations), so the whole pipeline is tested without a GPU.

## Real adapters

### Hunyuan3D 2.1 (mesh, gate 2)

Set `hunyuan3d_url` to the machine running Hunyuan3D's `api_server.py`. For each asset the adapter:

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
Blender then cleans the mesh up and decimates it into its LODs.

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

### Modly (mesh, gate 2), the alternative to Hunyuan3D 2.1

[Modly](https://github.com/lightningpixel/modly) is a desktop app that runs image-to-3D models on
the local GPU through a FastAPI server (port 8765). Set `mesh_tool = "modly"` to use it instead
of Hunyuan3D's `api_server.py`. For each asset the adapter (`adapters/real/modly.py`):

1. sends the picked candidate as it is (Modly removes the background itself) to
   `POST /workflow-runs/from-image` with `model_id = modly_model`, `collection = "assetgen"` and
   `params` = `{"enable_texture": true, "seed": <per asset and attempt>}` plus `modly_params`, and
   saves the run id in `mesh/modly_run.json` so a restart polls the same run;
2. polls `GET /workflow-runs/{run_id}` (its `step` text becomes the asset's progress) until `done`;
3. downloads the GLB with `GET /export/glb?path=<workspace path>` into `mesh/modly.glb`, then
   finishes the texture as for Hunyuan3D (`mesh/texture.png`, `mesh/raw.glb`).

Errors name the cause: Modly not open, the texture extensions not built, out of GPU memory, or no
result within `modly_timeout`. Its meshes are large (about 900k triangles); Blender decimates them
to the budget.

**Setting it up on the RTX 5090 machine** (Modly 0.4.3, Hunyuan3D 2 Mini Turbo):

1. In Modly: Extensions → Install from GitHub →
   `https://github.com/lightningpixel/modly-hunyuan3d-mini-turbo-extension`. On an RTX 50 card it
   installs PyTorch with CUDA 12.8 (about 2.7 GB; a slow download can time out, just retry).
2. Texture needs two C++ parts built by hand, with Visual Studio 2022 (C++) and the CUDA Toolkit
   12.8, in the extension's venv. From an "x64 Native Tools" prompt:

   ```bat
   set PY=%USERPROFILE%\Desktop\Modly\extensions\hunyuan3d-mini-turbo\venv\Scripts\python.exe
   set TEX=%USERPROFILE%\Desktop\Modly\models\hunyuan3d-mini-turbo\generate\_hy3dgen\hy3dgen\texgen
   set CUDA_HOME=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8
   set TORCH_CUDA_ARCH_LIST=12.0
   set DISTUTILS_USE_SDK=1
   %PY% -m pip install setuptools wheel ninja pybind11 xatlas pygltflib
   cd /d %TEX%\custom_rasterizer && %PY% -m pip install . --no-build-isolation
   cd /d %TEX%\differentiable_renderer && %PY% setup.py build_ext --inplace
   ```

   Two fixes were needed with PyTorch 2.11 and current diffusers:
   - `custom_rasterizer/lib/custom_rasterizer_kernel/rasterizer.h`: include `<torch/types.h>`
     instead of `<torch/extension.h>` when `__CUDACC__` is defined (MSVC otherwise stops with
     "'std': ambiguous symbol" in `compiled_autograd.h`).
   - `utils/multiview_utils.py`: pass `trust_remote_code=True` to `DiffusionPipeline.from_pretrained`
     (the paint pipeline is local code in the same source tree).

   Restart Modly after changing them. If Modly downloads the Hunyuan3D source again, redo both.
3. The first textured run downloads the paint weights; after that a textured mesh takes about
   4 minutes, a shape-only one about 15 seconds.

### Joint editor (gate 3, characters)

The rig stage reports every deforming joint's rest position and parent (`outputs.rig.joints`, in
the mesh's space with Blender axes). Gate 3's **Joints** card holds the model in its rest pose and
draws the skeleton over it (fingers left out); a click picks the nearest joint and its arrows move
it, optionally mirrored to the other side. **Re-rig with these joints** sends them with a gate 3
rejection (`joints`); they replace UniRig's targets for those joints, also when its fit fell back,
add up over rejections (`joint_overrides` on the asset) and `{}` goes back to the detected joints.

### Character height

Characters are scaled to their kind's height in the clean-up (hero 1.8 m, monster 1.5 m, boss
2.7 m) unless the asset has its own `height` (in metres, set when creating it or changed at gate 2,
which reruns only the clean-up). The template skeleton is scaled to the mesh, so any height rigs.

### Gear and weapons (rig stage, gate 3)

Gear and weapons are rigged on the biped base body (`blender_scripts/attach.py`, called by
`rig_animate.py`); the preview GLB has the mannequin, the item and the armature playing walk and an
attack.

* **Gear**: the item is scaled and moved onto its slot region by their bounds: centred over the
  region's width and depth (head, chest, cape: the cape against the back, from the shoulders), the
  legs' height, each foot (bottom on the ground) or each forearm and hand (turned along it). A pair
  is split into its two pieces; a single glove or boot is mirrored for the other side. A Shrinkwrap
  (outside mode, 8 mm offset) pushes out whatever is still inside the body, so nothing clips at
  rest. The body's weights are copied onto it (Data Transfer, nearest face); a helmet follows the
  head bone alone. The body faces it covers (a ray along the face's normal meets the item within
  6 cm) are the mask: written to `rig/body_mask.json` and left out of the preview body. Gate 3
  shows the counts (`outputs.rig.gear`).
* **Weapons**: rigid, never skinned, parented to the socket bone. The grip is found on the longest
  axis: the handle is the thinner end, and the grip sits in the middle of its thin run (bows: the
  middle, on `weapon_l`; staffs: 45 % up). Its frame has +Y towards the blade and +Z across the
  blade's width, and sits on the socket bone's frame. Two-handed weapons get a second grip for
  `weapon_l`, 12 cm towards the blade. The found grip is stored on the asset (`grip`, with
  `"source": "auto"`, in Unity's terms: see `src/assetgen/grips.py`); a grip typed in gate 3's grip
  fields is sent with the rejection, marked `"source": "manual"` and used as it is on the rerun.
* **Wearer**: gear and weapons are fitted on the base mannequin, or on a rigged biped hero of the
  project picked in gate 3's **Wearer** card (`wearer` in the rejection; `GET
  /api/projects/{id}/wearers` lists them). The rig stage of every character keeps its exact scene
  as `rig/rigged.blend` (armature, skinned mesh, baked clips); fitting on a wearer loads it, works out
  its slot regions the same way, and plays its own clips instead of retargeting. So the item
  matches that hero's height and build. Heroes rigged before this need a re-rig to be listed.
* **Exports**: the Unity FBX leaves out the mannequin, so gear is a skinned mesh on the shared
  skeleton; a weapon is written rigid in its own model space, without armature or clips, and its
  grip goes to `asset.json`. The Godot render keeps the mannequin as a holdout: invisible, but it
  still hides what is behind it, so the slot's or weapon's layer composites over the body layer in
  `PaperDoll`.
* **Paper doll**: biped heroes, gear and weapons share one sprite framing (`PAPER_DOLL_FRAMING` in
  `defaults.py`: a 4.8 m ortho scale centred 0.9 m up, which holds the 1.8 m body swinging a 1 m
  weapon), and biped heroes and gear also play every weapon class's attacks, so their layers
  line up frame for frame. Checked in Godot 4.7.2 with a knight, a helmet and a sword stacked as
  three `AnimatedSprite2D` layers.

### Quality checks, autopilot and batches

Every gate has checks (`src/assetgen/checks.py`), each `pass`, `warn` or `fail`, shown at the gate
and in the gallery:

* **Gate 1, per candidate**: one subject, nothing cut off, arms clear of the body (biped
  characters) and empty hands (characters). The LLM is asked about each candidate with the image
  (`inspect`: held items, how many figures, cut off); a text-only model skips that check with a
  "not checked" warning. Candidates that fail are redrawn with other seeds, up to `auto_retries`
  rounds (default 2), before the gate.
* **Gate 2**: loose pieces and holes in the cleaned mesh (`cleanup_lods.py` reports them).
* **Gate 3**: the skeleton fit (UniRig runs again with other seeds while it is under 0.75, up to
  `unirig_tries` runs, and keeps the best) and how much each clip stretches the mesh (the share of
  edges stretched past 1.5x or squeezed under 0.6x, measured by `rig_animate.py`).

**Autopilot** (`autopilot` when creating, or `PUT /api/assets/{id}/autopilot`): when the asset
stops at a gate and nothing failed, it approves the gate by itself (gate 1: the best candidate);
otherwise it stops there and says why (`autopilot_log`). **Batches** (`POST /api/assets/batch`):
up to 20 assets from one description, the same or varied by the LLM (`vary`), sharing a
`batch_id` (`GET /api/assets?batch_id=`).

### Colour variants

`POST /api/assets/{id}/variants` copies a rigged asset with its texture recoloured (`hue`,
`saturation`, `brightness`, optional `tint` and `tint_strength`) in every file that holds it, and
puts the copy at gate 3. The mesh, skeleton and clips are kept, so it takes seconds and fits the
same gear (`src/assetgen/variants.py`).

### Multi-view meshes

Image-to-3D only sees the front, so the back of a mesh is guessed. With the mesh setting
`multi_view` (style profile, or per asset on New asset) the mesh stage first draws the right, back
and left views of the picked candidate with **MV-Adapter** (SDXL image-to-multi-view), and
Hunyuan3D builds the shape from all four with **Hunyuan3D-2mv** (turbo); the texture still comes
from the front image. Gate 2 shows the views.

Set up on the RTX 5090 machine (about 13 GB):

* `C:/tools/MV-Adapter`: the [MV-Adapter](https://github.com/huanngzh/MV-Adapter) code with its
  own Python (`.venv`: torch cu128, diffusers, transformers, peft, accelerate, einops, omegaconf,
  jaxtyping, matplotlib, pytorch-lightning, opencv-python-headless, trimesh). It is run by
  `backend/external/mv_views.py` and loads SDXL from ComfyUI's `sd_xl_base_1.0.safetensors`.
* `C:/tools/MV-Adapter-models`: `mvadapter_i2mv_sdxl.safetensors` (huanngzh/mv-adapter) and
  `sdxl-vae-fp16-fix/` (madebyollin/sdxl-vae-fp16-fix).
* `~/.cache/hy3dgen/tencent/Hunyuan3D-2mv/hunyuan3d-dit-v2-mv-turbo` and
  `~/.cache/hy3dgen/tencent/Hunyuan3D-2/hunyuan3d-vae-v2-0-turbo`: the multi-view shape model and
  its decoder.
* `patches/hunyuan-multiview.patch` on the Hunyuan3D 2.1 server: `mv_images` in the request
  (`left`, `back`, `right`, base64) builds the shape with Hunyuan3D-2mv, loaded on first use.

Settings: `mvadapter_root`, `mvadapter_python`, `mvadapter_models`, `mvadapter_sdxl` (empty:
ComfyUI's checkpoint) and `mvadapter_timeout`. MV-Adapter needs about 14 GB of GPU memory;
ComfyUI's models are freed first.

### Restarting tools, and storage

With a `[restart.<tool>]` table in `assetgen.toml` (`command`, `cwd`, `port`; see
`src/assetgen/watchdog.py`), a stage that fails because ComfyUI (`image`) or Hunyuan3D (`mesh`)
did not answer or ran out of memory restarts that tool once and runs again; a red tool badge in
the header restarts it on a click (`POST /api/tools/{tool}/restart`). `GET /api/storage` and
`POST /api/storage/clean` (the Storage card on the Projects page) show and delete what can be
rebuilt or is no longer used: UniRig's working files, thumbnails, files of deleted assets, fits on
deleted heroes and examples of deleted LoRAs and profiles.

```toml
[restart.image]
command = ["C:/path/to/ComfyUI/venv/Scripts/python.exe", "main.py", "--port", "8188"]
cwd = "C:/path/to/ComfyUI"
port = 8188

[restart.mesh]
command = ["cmd", "/c", "C:/tools/Hunyuan3D-2.1/start_server.bat"]
cwd = "C:/tools/Hunyuan3D-2.1"
port = 8081
```

### Side jobs: outfits and example sheets

Besides asset stages, the single worker runs side jobs (`jobs` table, `GET /api/jobs`), oldest
first with the stages, so the GPU is never shared:

* `fit` (`POST /api/assets/{id}/fits` with `wearer_id`): finished gear or a weapon (gate 2 approved)
  fitted on another rigged hero, exactly as at gate 3, kept per hero in `fits/<hero>/animated.glb`
  (`asset.fits`, `urls.fits`). The Outfits screen uses them to swap items between heroes; the
  item's own gate and export are untouched. Outfits are saved in the `outfits` table
  (`/api/outfits`).
* `lora_examples` (`POST /api/loras/{id}/examples`, optional `weight`, default 0.8) and
  `profile_examples` (`POST /api/profiles/{id}/examples`): the example subjects in `defaults.py`
  (`EXAMPLE_SUBJECTS`: a knight, a wolf and a sword, one seed) rendered with and without the LoRA,
  or in the profile's style (its model, LoRAs, prompt suffix, negative and references). Written to
  `data/examples/` and served at `/api/examples/...`. Your own LoRA images:
  `POST /api/loras/{id}/example-images`.

`GET /api/assets/{id}/thumb` is a cached 384 px JPEG of the preview, for galleries.

### Archetype templates (the rig stage's skeletons and clips)

Each archetype folder in `src/assetgen/archetypes/<id>/` holds:

- `skeleton.blend`: the template armature alone, in the archetype's rest pose (the biped's is the
  A-pose the prompts ask for), with the joint names listed in `archetype.json`. The biped's also
  has the weapon sockets `weapon_r` and `weapon_l`: bones on the hands that don't deform (head in
  the palm, pointing out of the fist on the thumb side, Z along the fingers), marked
  `"socket": true` in `skeleton.json`;
- `base_body.blend` (biped only): the template armature and the source pack's mannequin (object
  `mannequin`) skinned to it in the same A-pose, with a `slot_<slot>` vertex group per gear slot
  (by each vertex's strongest bone: head and neck; torso and upper arms; pelvis and legs; forearms,
  hands and fingers; feet; and the back of the torso and thighs for the cape). Gear is fitted on it;
- `clips/<clip>.glb`: one in-place animation per clip, named after the clip, on the source armature
  (kept as a glTF skin by a one-triangle proxy mesh, so importers rebuild it as a skeleton);
- `clips.json` (source clip, duration, loop, stand-ins) and `LICENCES.md`.

| Archetype | Skeleton and clips | Joints | Clips |
|---|---|---|---|
| biped | Quaternius Universal Animation Library (Standard), CC0 | 53 | the 7 standard ones, cast, skill and the 8 weapon attacks (5 are stand-ins) |
| quadruped | Quaternius Ultimate Animated Animal Pack, Wolf, CC0 | 47 | standard 7 |
| flyer | Quaternius Ultimate Monsters, Flying/Dragon, CC0 | 13 | standard 7 (walk is a stand-in) |
| blob | Quaternius Ultimate Monsters, Blob/GreenBlob, CC0 | 4 | standard 7 (run is a stand-in) |
| spider, serpent | generated by the build script | 30, 19 | standard 7 |

The source packs are not in git. To rebuild, download them into `src/assetgen/archetypes/`:

- `biped/source/`: [Universal Animation Library](https://quaternius.itch.io/universal-animation-library),
  the free Standard zip, unpacked (the script reads `Unreal-Godot/UAL1_Standard.glb`);
- `_quaternius/animals/glTF/`: the glTF folder of the
  [Ultimate Animated Animal Pack](https://quaternius.com/packs/ultimateanimatedanimals.html);
- `_quaternius/monsters/{Big,Flying,Blob}/glTF/`: the glTF folders of
  [Ultimate Monsters](https://quaternius.com/packs/ultimatemonsters.html) (Google Drive limits
  scripted downloads; its "Download all" button works);

then run (all archetypes, or only those named; `--out DIR` writes elsewhere):

```powershell
& "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" -b --factory-startup -P blender_scripts/build_archetypes.py -- [biped quadruped ...]
```

It rewrites the templates (and the biped's `base_body.blend`), `clips.json`, `LICENCES.md` and the `joints`, `clips`, `sockets` and
source fields of `archetype.json`. Which source clip feeds each of our clips is the `SOURCES`
table at the top of the script; swapping a pack (Truebones, Mixamo) means changing that table.

### UniRig (rig, gate 3)

[UniRig](https://github.com/VAST-AI-Research/UniRig) predicts a skeleton and skin weights for a
mesh. The rig stage works like this:

1. `adapters/real/unirig.py` writes LOD0 to `rig/unirig/input.glb` and runs UniRig's skeleton,
   skin and merge steps (kept files are reused on a retry; output goes to `unirig.log`). Each step
   may take `unirig_timeout` seconds.
2. It fits the archetype's template skeleton (`archetypes/<id>/skeleton.json`) to the predicted
   one by limb chains: both are scaled into their mesh bounds, short side branches are dropped,
   limb ends are paired, and the template joints get target positions. `fit_score` is the share
   of template limbs found, minus 0.25 per long extra limb (wings on a biped). Below 0.6 the fit
   falls back, and gate 3 warns. The result is `rig/skeleton.json`.
3. `blender_scripts/rig_animate.py` scales the template to the mesh, moves its joints to the
   targets, and skins the mesh: UniRig's weights mapped to the template joints, or (on a fallback)
   Blender's automatic weights, then a voxel-proxy weight transfer, then the nearest bone for
   anything left. The template skeleton is always the final rig, so every clip fits it.
4. Each clip in `archetypes/<id>/clips/` is retargeted with world-space Copy Transforms and baked;
   the GLB has one named animation per clip.

Gear and weapons skip UniRig: they are fitted on the base body (see "Gear and weapons" below). On the RTX 5090 machine UniRig is installed at `C:\tools\UniRig`
(commit `6793c66`) with its own Python 3.11 environment, the paths `unirig_path` and
`python_path` in `assetgen.toml` point at:

```powershell
git clone https://github.com/VAST-AI-Research/UniRig C:\tools\UniRig
cd C:\tools\UniRig
uv venv --python 3.11 .venv
.venv\Scripts\python -m ensurepip
.venv\Scripts\python -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
# requirements.txt without flash_attn (no Windows build; see the patch below)
Get-Content requirements.txt | Where-Object { $_ -notmatch '^flash_attn' } | Set-Content req-win.txt
.venv\Scripts\python -m pip install -r req-win.txt
.venv\Scripts\python -m pip install spconv-cu126==2.3.8 psutil
.venv\Scripts\python -m pip install torch_scatter torch_cluster -f https://data.pyg.org/whl/torch-2.11.0+cu128.html --no-index --only-binary=:all:
.venv\Scripts\python -m pip install numpy==1.26.4
git apply C:\path\to\forge\backend\patches\unirig-windows.patch
```

`patches/unirig-windows.patch` makes UniRig run without `flash_attn` and with PyTorch 2.11:

- `configs/model/unirig_ar_350m_1024_81920_float32.yaml`: `_attn_implementation: sdpa` instead
  of `flash_attention_2` (skeleton model).
- `configs/model/unirig_skin.yaml`: `enable_flash: False` for the point encoder (its built-in
  non-flash attention path).
- `src/model/unirig_skin.py`: when `flash_attn` is missing, a PyTorch stand-in for its `MHA`
  cross attention with the same weight names (`Wq`, `Wkv`, `out_proj`), so the released
  checkpoint loads unchanged.
- `run.py`: `trainer.predict(..., weights_only=False)`, because the released checkpoints (from
  the official `VAST-AI/UniRig` Hugging Face repo) store a python-box `Box`, which PyTorch 2.6+
  will not load with its default `weights_only=True`.

The `.sh` launchers are bash; on Windows run their Python steps directly from `C:\tools\UniRig`
(the checkpoints download from Hugging Face on the first run):

```powershell
$py = ".venv\Scripts\python.exe"
$t = Get-Date -Format "yyyy_MM_dd_HH_mm_ss"
# 1. skeleton
& $py -m src.data.extract --config=configs/data/quick_inference.yaml --require_suffix=obj,fbx,FBX,dae,glb,gltf,vrm --force_override=true --num_runs=1 --id=0 --time=$t --faces_target_count=50000 --input=examples/giraffe.glb --output_dir=tmp
& $py run.py --task=configs/task/quick_inference_skeleton_articulationxl_ar_256.yaml --seed=12345 --input=examples/giraffe.glb --output=results/giraffe_skeleton.fbx --npz_dir=tmp
# 2. skin weights for that skeleton
& $py -m src.data.extract --config=configs/data/quick_inference.yaml --require_suffix=obj,fbx,FBX,dae,glb,gltf,vrm --force_override=true --num_runs=1 --id=0 --time=$t --faces_target_count=50000 --input=results/giraffe_skeleton.fbx --output_dir=tmp
& $py run.py --task=configs/task/quick_inference_unirig_skin.yaml --seed=12345 --input=results/giraffe_skeleton.fbx --output=results/giraffe_skin.fbx --npz_dir=tmp --data_name=raw_data.npz
# 3. put skeleton and weights on the original (textured) mesh
& $py -m src.inference.merge --require_suffix=obj,fbx,FBX,dae,glb,gltf,vrm --num_runs=1 --id=0 --source=results/giraffe_skin.fbx --target=examples/giraffe.glb --output=results/giraffe_rigged.glb
```

On the 5090 the giraffe takes about 3 s for the skeleton and 6 s for the skin; the merged GLB has
one 44-joint skin on every primitive and keeps its texture. Harmless messages: `pip check` says
"bpy 4.2.0 is not supported on this platform" (it imports and runs), and the merge prints a Draco
error (that optional compression library is not bundled; the GLB is written without it).

## Settings

Defaults, overridden by `assetgen.toml` in the working directory (or the file in `ASSETGEN_CONFIG`),
then by environment variables `ASSETGEN_<NAME>`:

```toml
host = "127.0.0.1"
port = 8000
data_dir = "data"              # SQLite database, data/assets/<id>/<stage>/, uploads
comfyui_url = "http://127.0.0.1:8188"
comfyui_dir = ""               # ComfyUI folder (with models/); LoRAs it lacks are copied to models/loras
comfyui_timeout = 600          # seconds one candidate may take in ComfyUI before the stage fails
comfyui_workflows_dir = ""     # API-format workflows per base model (empty: backend/workflows)
pose_controlnet = "openpose_sdxl_xinsir.safetensors"   # pose guide model in models/controlnet (empty: off)
pose_strength = 0.8            # how strongly the pose guide holds the A-pose
qwen_url = "http://127.0.0.1:11434/v1"   # Ollama; LM Studio: "http://127.0.0.1:1234/v1"
qwen_model = "qwen3"                       # LM Studio: the model id it lists, e.g. "qwen3-14b"
hunyuan3d_url = "http://127.0.0.1:8081"
hunyuan3d_timeout = 1800       # seconds one image-to-mesh job may take
hunyuan3d_poll = 2             # seconds between status checks
mesh_tool = "hunyuan3d"        # or "modly"
modly_url = "http://127.0.0.1:8765"
modly_model = "hunyuan3d-mini-turbo/generate"   # a model id from Modly's GET /model/all
modly_params = ""              # extra generation params as JSON, e.g. '{"num_inference_steps": 5}'
modly_timeout = 1800           # seconds one Modly run may take
modly_poll = 2
blender_path = "blender"
unirig_path = "UniRig"
python_path = "python"
unirig_timeout = 900           # seconds one UniRig step may take
unirig_tries = 3               # UniRig runs (other seeds) while the skeleton fits badly; the best is kept
auto_retries = 2               # rounds of redrawing gate 1 candidates that fail their checks
frontend_dir = "../frontend/dist" # built web app served at / (default: the repo's frontend/dist)
```

Environment example: `ASSETGEN_PORT=9000`, `ASSETGEN_COMFYUI_URL=http://gpu:8188`. An old
`[adapters]` table is ignored: there is no mock mode any more.

## ComfyUI (image adapter)

Gate 1's candidates come from ComfyUI at `comfyui_url`. Each
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

### Pose guide and the arms check (biped characters)

Image-to-3D models fuse body parts that touch: hands resting on the thighs become one piece with
them, and the rig can only cut them apart roughly. So heroes, monsters and bosses on the biped
archetype are drawn in a wide A-pose (arms 45 degrees down, hands clear of the hips):

* The prompt stage writes `prompt/pose_guide.png`, an OpenPose skeleton in that pose
  (`src/assetgen/pose.py`), and adds "hands touching the legs, arms against the body..." to the
  negative prompt.
* On SDXL the guide goes through an OpenPose ControlNet (`ControlNetLoader` and
  `ControlNetApplyAdvanced` on the sampler's conditioning, strength `pose_strength`, for the first
  80 % of the steps). The model is [xinsir/controlnet-openpose-sdxl-1.0](https://huggingface.co/xinsir/controlnet-openpose-sdxl-1.0)
  (`diffusion_pytorch_model.safetensors`, 2.5 GB), saved as
  `models/controlnet/openpose_sdxl_xinsir.safetensors`. Without it, or on Flux and Qwen-Image, the
  stage log says the guide was skipped and only the prompt asks for the pose.
* Gate 1 checks each candidate's silhouette (`pose.arms_check`, also on uploaded images): in a
  clear A-pose most rows from the chest to the hips cross three separate parts (arm, body, arm).
  Candidates below 60 % are marked "Arms touch body". The result is the prompt stage's
  `pose_check`.

Gear and weapons are drawn alone, so they get neither.

The adapter finds the nodes it fills by title, so a workflow can be rebuilt in ComfyUI and exported
with "Save (API)" as long as it keeps these node titles: `model` (MODEL loader, or the checkpoint
loader), `clip` (CLIP loader, unless `model` is a checkpoint), `positive`, `negative`, `latent`,
`sampler` (`KSampler`) and `save` (`SaveImage`). Size, steps, CFG and sampler are read from the file.

## Real Blender

Headless Blender (4.5 LTS or later, at `blender_path`) runs the
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
the asset's progress, and a failure puts the log's last lines in the asset's error. `rig_animate.py` (rig stage) skins the
mesh to the template skeleton and retargets the clips; see "UniRig" above.

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
  (rig type, LODs, clips, slot, bones and hidden body parts for gear, socket and grip for weapons,
  and the second hand's grip for two-handed ones).
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
#    a skinned biped skeleton (the fake rig has none, and Humanoid needs real bones).
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
  adapters/        base.py (interfaces), real/ (qwen.py, comfyui.py, hunyuan3d.py, modly.py, mesh_common.py, blender.py)
  imaging.py       background removal, de-light pass, GLB albedo texture
  archetypes/<id>/archetype.json
  config.py  db.py  defaults.py  events.py
blender_scripts/   bpy scripts run by the real Blender adapter
workflows/         ComfyUI API-format workflows: sdxl.json, flux.json, qwen-image.json
tests/             pytest: the pipeline on the doubles in tests/fakes/, the real adapters against fake Qwen,
                   ComfyUI and Hunyuan3D servers,
                   and the Blender scripts when Blender is installed (skipped otherwise)
tests/unity/       the batch-mode check of the Unity import script (not run by pytest)
```
