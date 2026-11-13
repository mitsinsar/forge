# Troubleshooting

Start with the **Setup** page (`/setup`): it checks every tool live and tells you what's missing.
Every stage writes its tool's log into the asset's folder (`backend/data/assets/<id>/<stage>/`),
and a failed stage shows the last lines of that log as its error.

## A header badge is red / "unreachable"

The tool isn't answering at the URL in `assetgen.toml`.

- Start it (or press **Start** on the Setup page if it has a `[restart.<tool>]` entry).
- Check the URL and port in `assetgen.toml` match what the tool prints when it starts.
- After editing `assetgen.toml`, **restart Forge** and press **Check again**.

## A stage failed

Read the error on the asset page, fix the cause, and press **Retry**: the stage resumes from the
files it already wrote. Common causes:

| Error mentions | Likely cause | Fix |
|---|---|---|
| *CUDA out of memory* | Two models on the GPU at once | Keep *one model on the GPU at a time* on in the style profile; close other GPU apps; add `[restart.*]` entries so Forge can restart a tool that holds memory |
| A missing model file | ComfyUI doesn't have a file the workflow loads | The error names the file and the `models/` folder it goes in |
| A missing node | A ComfyUI custom node isn't installed | Install it (e.g. IPAdapter_plus for SDXL references) or remove the references |
| *timed out* | The tool is slow on its first run (downloading models) | Wait and retry, or raise the matching `*_timeout` setting |
| Blender | Wrong `blender_path` or Blender version | Use Blender 4.5 LTS; the stage folder has `blender_<script>.log` |
| UniRig | Wrong `unirig_path` / `python_path`, missing `spconv`/`torch_scatter` | Re-check its environment; on Windows apply `backend/patches/unirig-windows.patch` |

## Gate 1 pictures are poor or keep failing

- **Cut off / touches the image edge** — add "full body, centered" to the profile's prompt
  suffix; reject with a note.
- **Arms against the body** — install the OpenPose ControlNet (SDXL only).
- **Holds an item** — characters should be drawn empty-handed; make the weapon as its own asset.
- **"Not checked" warnings** — your language model can't see images. Use a vision model.
- **Doesn't follow the description** — switch the image model to **Qwen-Image**.

## Mesh problems at gate 2

- **Back looks wrong** — turn on *Multi-view mesh* (needs MV-Adapter).
- **Floating bits / ground plate** — keep *Remove floating pieces* on; reject to rebuild with a
  new seed; or pick a picture with a plain background.
- **Baked shadows in the texture** — keep *Even out baked lighting* on.
- **Too many / too few triangles** — change the kind's triangle budget in the profile.

## Rig problems at gate 3

- **An elbow or knee bends the wrong way** — **Adjust joints**, move or face the joint forward,
  then **Re-rig with these joints**.
- **Bad skeleton fit** — raise `unirig_tries`; reject to rerun with other seeds.
- **A weapon sits wrong in the hand** — edit the grip fields and re-rig, or move it by hand on
  **Outfits**.
- **Gear clips through the body** — fit it on the hero who'll wear it (the *Wearer* card), not the
  dummy.

## Exports

- **Unity doesn't make a prefab** — check the Console for `AssetForge` messages; run
  **Tools → AssetForge → Rebuild All Prefabs**. URP is expected (Standard shader is used with a
  warning otherwise).
- **The export folder has an id suffix (`Grey_Wolf_1a2b3c`)** — another asset, or something
  not made by Forge, already had that folder. Forge never deletes a folder it doesn't own.
- **Godot sprites look tiny** — the paper-doll framing leaves room for weapon swings so layers
  line up; scale the `AnimatedSprite2D` node, or change frame size in the profile.

## Windows notes

- Forge and its tools are tested on Windows; the commands in the docs use `venv\Scripts\python`.
- If Forge runs inside a sandboxed or packaged shell, files written into `%APPDATA%` /
  `%LOCALAPPDATA%` may be redirected. Keep tool installs in a plain folder such as `C:\tools`.

## Still stuck?

Open an issue with: the asset page's error, the stage log from `backend/data/assets/<id>/<stage>/`,
your GPU and VRAM, OS, and the Setup page's state. See [CONTRIBUTING.md](../CONTRIBUTING.md).
