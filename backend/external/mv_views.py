"""Front, right, back and left views of a character from its front image, with MV-Adapter (SDXL).

Run with MV-Adapter's own Python (``mvadapter_python`` in assetgen.toml), like UniRig::

    python mv_views.py <args.json> <result.json>

args.json::

    {"mvadapter_root": "C:/tools/MV-Adapter", "sdxl_checkpoint": ".../sd_xl_base_1.0.safetensors",
     "adapter_dir": "C:/tools/MV-Adapter-models", "vae_dir": ".../sdxl-vae-fp16-fix",
     "image": "front.png" (cut out with rembg here unless it is already transparent),
     "prompt": "a goblin warrior...",
     "out_dir": "views", "seed": 42, "steps": 40, "size": 768}

The SDXL weights come from the checkpoint file ComfyUI already has (no diffusers download); only
small config files are fetched once. Writes ``<view>.png`` for front, left, back and right (the
character's left side, back and right side) and prints PROGRESS lines like the Blender scripts.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

# Azimuth in MV-Adapter's frame, degrees. Its second image shows the character's left side (the
# character faces image-left) and the fourth its right, the same order Hunyuan3D-2mv reads.
VIEWS = {"front": 0, "left": 90, "back": 180, "right": 270}


def progress(fraction: float, step: str) -> None:
    print(f"PROGRESS {max(0.0, min(1.0, fraction)):.3f} {step}", flush=True)


def main(args: dict) -> dict:
    sys.path.insert(0, str(Path(args["mvadapter_root"]).resolve()))
    try:
        import nvdiffrast.torch  # noqa: F401 - only MV-Adapter's mesh renderer needs it
    except ImportError:  # the cameras used here don't; a stub lets mvadapter.utils import
        import types

        stub = types.ModuleType("nvdiffrast")
        stub.torch = types.ModuleType("nvdiffrast.torch")
        sys.modules["nvdiffrast"], sys.modules["nvdiffrast.torch"] = stub, stub.torch
    import torch
    from diffusers import AutoencoderKL, StableDiffusionXLPipeline
    from PIL import Image

    from mvadapter.pipelines.pipeline_mvadapter_i2mv_sdxl import MVAdapterI2MVSDXLPipeline
    from mvadapter.schedulers.scheduling_shift_snr import ShiftSNRScheduler
    from mvadapter.utils.geometry import get_plucker_embeds_from_cameras_ortho
    from mvadapter.utils.mesh_utils import get_orthogonal_camera
    sys.path.insert(0, str(Path(args["mvadapter_root"]).resolve() / "scripts"))
    from inference_i2mv_sdxl import preprocess_image

    dtype, device = torch.float16, "cuda"
    size = int(args.get("size", 768))
    progress(0.02, "Loading SDXL and MV-Adapter")
    vae = AutoencoderKL.from_pretrained(args["vae_dir"], torch_dtype=dtype)
    base = StableDiffusionXLPipeline.from_single_file(args["sdxl_checkpoint"], vae=vae, torch_dtype=dtype)
    pipe = MVAdapterI2MVSDXLPipeline(**{k: v for k, v in base.components.items()
                                        if k in ("vae", "text_encoder", "text_encoder_2", "tokenizer", "tokenizer_2",
                                                 "unet", "scheduler")})
    del base
    pipe.scheduler = ShiftSNRScheduler.from_scheduler(pipe.scheduler, shift_mode="interpolated", shift_scale=8.0)
    pipe.init_custom_adapter(num_views=len(VIEWS))
    pipe.load_custom_adapter(args["adapter_dir"], weight_name="mvadapter_i2mv_sdxl.safetensors")
    pipe.to(device=device, dtype=dtype)
    pipe.cond_encoder.to(device=device, dtype=dtype)
    pipe.vae.enable_slicing()

    progress(0.15, "Drawing the views")
    count = len(VIEWS)
    cameras = get_orthogonal_camera(elevation_deg=[0] * count, distance=[1.8] * count, left=-0.55, right=0.55,
                                    bottom=-0.55, top=0.55, azimuth_deg=[a - 90 for a in VIEWS.values()],
                                    device=device)
    control = ((get_plucker_embeds_from_cameras_ortho(cameras.c2w, [1.1] * count, size) + 1.0) / 2.0).clamp(0, 1)
    source = Image.open(args["image"])
    if source.mode != "RGBA" or source.getchannel("A").getextrema()[0] == 255:
        progress(0.12, "Cutting out the front image")
        from rembg import new_session, remove  # the same u2net model Hunyuan3D's server uses

        source = remove(source.convert("RGB"), session=new_session("u2net"))
    reference = preprocess_image(source.convert("RGBA"), size, size)
    steps = int(args.get("steps", 40))

    def on_step(pipeline, step, timestep, kwargs):
        progress(0.15 + 0.8 * (step + 1) / steps, f"Drawing the views: step {step + 1}/{steps}")
        return kwargs

    images = pipe(
        args.get("prompt") or "high quality", height=size, width=size, num_inference_steps=steps,
        guidance_scale=float(args.get("guidance", 3.0)), num_images_per_prompt=count, control_image=control,
        control_conditioning_scale=1.0, reference_image=reference, reference_conditioning_scale=1.0,
        negative_prompt="watermark, ugly, deformed, noisy, blurry, low contrast, holding an object",
        generator=torch.Generator(device=device).manual_seed(int(args.get("seed", 42))),
        callback_on_step_end=on_step,
    ).images
    out = Path(args["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, image in zip(VIEWS, images):
        path = out / f"{name}.png"
        image.save(path)
        files[name] = str(path)
    progress(1.0, "Views ready")
    return {"views": files, "size": size}


if __name__ == "__main__":
    try:
        args_path, result_path = Path(sys.argv[1]), Path(sys.argv[2])
        result = main(json.loads(args_path.read_text(encoding="utf-8")))
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - the reason is the last line printed
        traceback.print_exc()
        print(f"ERROR {type(exc).__name__}: {exc}", flush=True)
        sys.exit(1)
