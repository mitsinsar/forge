# Installing Forge

Forge runs on one machine with an NVIDIA GPU (24 GB or more; 32 GB for multi-view meshes) on
Windows or Linux. It has two parts: the Forge app (a Python backend and a web front end) and the
tools it drives (a language model server, ComfyUI, Hunyuan3D, UniRig and Blender). The app's
**Setup** page (`/setup`) checks each tool live and shows these same steps, with buttons to copy
the commands and to restart or stop the tools.

This file is generated from `backend/src/assetgen/setup_guide.py`
(`cd backend; uv run python -m assetgen.setup_guide > ../INSTALL.md`).

## 0. The Forge app

1. Install [git](https://git-scm.com), [uv](https://docs.astral.sh/uv/) (it brings Python 3.12)
   and [Node.js](https://nodejs.org) 20 or later.
2. Get Forge and build the web app.

   ```
   git clone <this repository> forge
   cd forge/frontend
   npm ci
   npm run build
   ```

3. Start it (the first run installs the Python packages).

   ```
   cd ../backend
   uv run assetgen
   ```

4. Open http://127.0.0.1:8000/setup. Each part below starts red: install them in order, put their
   settings in `backend/assetgen.toml` (TOML, one `key = value` per line; or environment variables
   named `ASSETGEN_<KEY>`), restart Forge and press **Check again**.

The paths below (`C:/tools/...`) are examples. On Linux use your own paths and `venv/bin/python`
instead of `venv/Scripts/python`.

## 1. Language model (LM Studio)

Writes the image prompts, inspects the candidates and varies descriptions. Any OpenAI-compatible server works; LM Studio is the tested one. It needs a vision model.

1. Install LM Studio from https://lmstudio.ai and open it once. Its command line tool `lms` comes with it.

2. Download a vision model (Gemma 3 or 4, or Qwen2.5-VL; about 8 to 20 GB).

   ```
   lms get google/gemma-3-12b
   ```

3. List the downloaded models to get the exact id for qwen_model.

   ```
   lms ls
   ```

4. Start the server on port 1234.

   ```
   lms server start --port 1234
   ```

5. Tell Forge where it is, in backend/assetgen.toml (create the file if it doesn't exist).

   ```
   qwen_url = "http://127.0.0.1:1234/v1"
   qwen_model = "google/gemma-3-12b"
   ```

6. Optional: let the Setup page restart and stop it.

   ```
   [restart.llm]
   command = ["lms", "server", "start", "--port", "1234"]
   stop = ["lms", "server", "stop"]
   ```


## 2. ComfyUI (images)

Draws the four candidate images at gate 1 with SDXL (Flux and Qwen-Image workflows are in backend/workflows).

1. Get ComfyUI.

   ```
   git clone https://github.com/comfyanonymous/ComfyUI C:/tools/ComfyUI
   ```

2. Make its Python environment and install PyTorch for your GPU (CUDA 12.8 shown; see pytorch.org for others), then its requirements.

   ```
   cd C:/tools/ComfyUI
   python -m venv venv
   venv/Scripts/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
   venv/Scripts/python -m pip install -r requirements.txt
   ```

3. Download the SDXL base model (6.9 GB) into models/checkpoints.

   ```
   curl -L -o models/checkpoints/sd_xl_base_1.0.safetensors https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors
   ```

4. Optional, for the pose guide (characters stand in an A-pose): the OpenPose ControlNet (2.5 GB).

   ```
   curl -L -o models/controlnet/openpose_sdxl_xinsir.safetensors https://huggingface.co/xinsir/controlnet-openpose-sdxl-1.0/resolve/main/diffusion_pytorch_model.safetensors
   ```

5. Start it.

   ```
   venv/Scripts/python main.py --port 8188
   ```

6. Tell Forge where it is, in backend/assetgen.toml.

   ```
   comfyui_url = "http://127.0.0.1:8188"
   comfyui_dir = "C:/tools/ComfyUI"
   ```

7. Optional: let the Setup page restart and stop it.

   ```
   [restart.image]
   command = ["C:/tools/ComfyUI/venv/Scripts/python.exe", "main.py", "--port", "8188"]
   cwd = "C:/tools/ComfyUI"
   port = 8188
   ```


## 3. Hunyuan3D 2.1 (meshes)

Turns the picked image into a textured mesh (shape and PBR texture). Needs about 24 GB of GPU memory with texturing.

1. Get Hunyuan3D 2.1.

   ```
   git clone https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1 C:/tools/Hunyuan3D-2.1
   ```

2. Install it as its README says: a Python 3.10 environment, PyTorch for your GPU, requirements.txt, then build the two texture extensions (hy3dpaint/custom_rasterizer and hy3dpaint/DifferentiableRenderer).

3. Apply Forge's patch: background removal that works, quality settings per style profile, job errors that reach the app, multi-view shapes and freed GPU memory between jobs.

   ```
   cd C:/tools/Hunyuan3D-2.1
   git apply <forge>/backend/patches/hunyuan3d-2.1.patch
   ```

4. Start the API server (the models download on the first run).

   ```
   python api_server.py --host 127.0.0.1 --port 8081 --model_path tencent/Hunyuan3D-2.1 --subfolder hunyuan3d-dit-v2-1
   ```

5. Tell Forge where it is, in backend/assetgen.toml.

   ```
   mesh_tool = "hunyuan3d"
   hunyuan3d_url = "http://127.0.0.1:8081"
   ```

6. Optional: let the Setup page restart and stop it (a .bat or .sh that activates its environment and runs the command above).

   ```
   [restart.mesh]
   command = ["cmd", "/c", "C:/tools/Hunyuan3D-2.1/start_server.bat"]
   cwd = "C:/tools/Hunyuan3D-2.1"
   port = 8081
   ```


## 4. UniRig (skeletons)

Predicts each character's skeleton and skin weights at the rig stage. Forge runs it with its own Python; no server.

1. Get UniRig.

   ```
   git clone https://github.com/VAST-AI-Research/UniRig C:/tools/UniRig
   ```

2. Make its Python 3.11 environment and install it as its README says (PyTorch for your GPU, requirements.txt, spconv, torch_scatter and torch_cluster for that PyTorch).

3. On Windows, apply Forge's patch (paths and a float type UniRig gets wrong there).

   ```
   cd C:/tools/UniRig
   git apply <forge>/backend/patches/unirig-windows.patch
   ```

4. Tell Forge where it is, in backend/assetgen.toml (its checkpoints download on the first run).

   ```
   unirig_path = "C:/tools/UniRig"
   python_path = "C:/tools/UniRig/.venv/Scripts/python.exe"
   ```


## 5. Blender 4.5 LTS

Cleans meshes and makes LODs, skins and animates them, fits gear, renders sprites and writes the exports. Runs in the background; no server.

1. Install Blender 4.5 LTS from https://www.blender.org/download/lts/ (or `winget install BlenderFoundation.Blender`).

2. Tell Forge where it is, in backend/assetgen.toml.

   ```
   blender_path = "C:/Program Files/Blender Foundation/Blender 4.5/blender.exe"
   ```


## 6. MV-Adapter (optional: multi-view meshes)

Draws the back and sides of the picked image so Hunyuan3D builds the shape from four views (the mesh setting multi_view). Off by default.

1. Get MV-Adapter.

   ```
   git clone https://github.com/huanngzh/MV-Adapter C:/tools/MV-Adapter
   ```

2. Make its Python environment with PyTorch for your GPU, its requirements and the extras Forge uses.

   ```
   cd C:/tools/MV-Adapter
   python -m venv .venv
   .venv/Scripts/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
   .venv/Scripts/python -m pip install -r requirements.txt rembg onnxruntime "triton-windows<3.7"
   ```

3. Download the adapter weights (3.6 GB) and the fp16 SDXL VAE into one folder.

   ```
   huggingface-cli download huanngzh/mv-adapter mvadapter_i2mv_sdxl.safetensors --local-dir C:/tools/MV-Adapter-models
   huggingface-cli download madebyollin/sdxl-vae-fp16-fix --local-dir C:/tools/MV-Adapter-models/sdxl-vae-fp16-fix
   ```

4. The patched Hunyuan3D server downloads its multi-view model (tencent/Hunyuan3D-2mv, 5 GB) on the first multi-view job.

5. Tell Forge where it is, in backend/assetgen.toml (it reuses ComfyUI's SDXL checkpoint).

   ```
   mvadapter_root = "C:/tools/MV-Adapter"
   mvadapter_python = "C:/tools/MV-Adapter/.venv/Scripts/python.exe"
   mvadapter_models = "C:/tools/MV-Adapter-models"
   ```


## Using Forge from scripts

Everything the web app does goes through Forge's HTTP API, documented at
http://127.0.0.1:8000/docs (OpenAPI at `/openapi.json`). `POST /api/tools/{tool}/restart` and
`/stop` restart or stop a tool; `GET /api/setup` returns this guide with live checks.
