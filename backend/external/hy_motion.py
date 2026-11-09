"""Text to body motion with HY-Motion 1.0 (Tencent, https://github.com/Tencent-Hunyuan/HY-Motion-1.0).

Run with HY-Motion's own Python, in its folder (``hymotion_path`` / ``hymotion_python`` in
assetgen.toml), like MV-Adapter and UniRig::

    python hy_motion.py <args.json> <result.json>

args.json::

    {"model": "ckpts/tencent/HY-Motion-1.0",
     "motions": [{"prompt": "A person ...", "duration": 2.5, "seed": 7, "out": "C:/.../motion.json"}],
     "cfg_scale": 5.0}

The model and its text encoders (Qwen3-8B, CLIP) load once for every motion of the call. Each
``out`` gets the body motion on HY-Motion's SMPL-H skeleton (22 body joints; the hands are left
out): ``{"fps", "frames", "joints": [names], "parents": [index], "rest": [[x, y, z]] (Y up, metres,
T-pose), "rotations": [frame][joint] 3x3 local rotation (against the parent, row-major),
"translation": [frame][x, y, z] (the pelvis, Y up, on the ground)}``. Prints PROGRESS lines like
the Blender scripts.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

BODY_JOINTS = 22  # Pelvis ... R_Wrist: what the model predicts (the hands are a fixed mean pose)
WOODEN = Path("scripts/gradio/static/assets/dump_wooden")


def progress(fraction: float, step: str) -> None:
    print(f"PROGRESS {max(0.0, min(1.0, fraction)):.3f} {step}", flush=True)


def skeleton() -> tuple[list[str], list[int], list[list[float]]]:
    import numpy as np
    names = json.loads((WOODEN / "joint_names.json").read_text(encoding="utf-8"))[:BODY_JOINTS]
    parents = np.frombuffer((WOODEN / "kintree.bin").read_bytes(), dtype=np.int32)[:BODY_JOINTS]
    rest = np.frombuffer((WOODEN / "j_template.bin").read_bytes(), dtype=np.float32).reshape(-1, 3)[:BODY_JOINTS]
    return names, [int(p) for p in parents], [[round(float(v), 5) for v in j] for j in rest]


def main(args: dict) -> dict:
    os.environ.setdefault("USE_HF_MODELS", "0")  # the weights in ckpts/, never a download
    sys.path.insert(0, str(Path.cwd()))
    import torch
    import yaml
    from hymotion.utils.geometry import rot6d_to_rotation_matrix
    from hymotion.utils.loaders import load_object

    model = Path(args["model"])
    progress(0.02, "Loading HY-Motion")
    config = yaml.load((model / "config.yml").read_text(encoding="utf-8"), Loader=yaml.FullLoader)
    pipeline = load_object(config["train_pipeline"], config["train_pipeline_args"],
                           network_module=config["network_module"],
                           network_module_args=config["network_module_args"])
    progress(0.1, "Loading the text encoders")
    pipeline.load_in_demo(str(model / "latest.ckpt"), build_text_encoder=True)
    pipeline.to(torch.device("cuda" if torch.cuda.is_available() else "cpu"))
    pipeline.eval()
    names, parents, rest = skeleton()
    motions = args["motions"]
    written = []
    for index, motion in enumerate(motions):
        progress(0.3 + 0.7 * index / len(motions), f"Moving: {motion['prompt'][:60]}")
        out = pipeline.generate(motion["prompt"], [int(motion.get("seed", 0)) % 1000], float(motion["duration"]),
                                cfg_scale=float(args.get("cfg_scale", 5.0)), use_special_game_feat=True)
        rotations = rot6d_to_rotation_matrix(out["rot6d"][0, :, :BODY_JOINTS])  # (L, 22, 3, 3)
        translation = out["transl"][0]  # (L, 3)
        frames = int(rotations.shape[0])
        record = {
            "prompt": motion["prompt"], "fps": int(pipeline.output_mesh_fps), "frames": frames,
            "joints": names, "parents": parents, "rest": rest,
            "rotations": [[[round(float(v), 5) for v in m.reshape(-1)] for m in frame] for frame in rotations],
            "translation": [[round(float(v), 5) for v in t] for t in translation],
        }
        target = Path(motion["out"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record), encoding="utf-8")
        written.append({"out": str(target), "frames": frames})
    progress(1.0, "Motion ready")
    return {"motions": written}


if __name__ == "__main__":
    args_path, result_path = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        result = {"ok": True, **main(json.loads(args_path.read_text(encoding="utf-8")))}
    except Exception as exc:  # noqa: BLE001 - reported to the adapter
        traceback.print_exc()
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    result_path.write_text(json.dumps(result), encoding="utf-8")
    sys.exit(0 if result["ok"] else 1)
