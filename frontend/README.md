# assetgen — web frontend

React + Vite + TypeScript app for the asset generator. It talks to the FastAPI backend in
`../backend` (all routes under `/api`, live events over the `/api/ws` WebSocket).

## Run

Start the backend first (`cd backend && uv run assetgen`, port 8000), then:

```
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api (and the WebSocket) to 127.0.0.1:8000
```

Only npm scripts are used, so the same commands work on Windows, macOS and Linux.

| Script | What it does |
|---|---|
| `npm run dev` | Dev server with hot reload |
| `npm run build` | Type-checks, then writes the production app to `dist/` |
| `npm run lint` | ESLint, no warnings allowed |
| `npm run typecheck` | `tsc --noEmit` |

After `npm run build`, `uv run assetgen` serves the app itself at http://127.0.0.1:8000/ (the
`/api` routes keep working); delete `dist/` to stop that.

## Screens

* **Assets** — every asset with live status (queued, running with step and progress, waiting at
  gate N, done, failed), filtered by project, kind and status, as a gallery (each asset's preview
  thumbnail; Godot assets play their walk sprites on hover) or a table. "Needs me" shows the
  assets waiting at a gate.
* **New asset** — project, kind, description, optional name, archetype override (characters
  only), slot (gear), weapon class (weapons) and style profile.
* **Asset** — stepper for preview → mesh → animate → export and the current gate:
  gate 1 picks one of the candidates (or reruns with another archetype), gate 2 shows the mesh
  with its LODs in a 3D viewer, gate 3 plays the animated GLB's clips (plus the sprite frames
  for Godot projects and grip fields for weapons). Every gate can be rejected with a note, which
  reruns only that stage. Done shows the export folder, files, zip download, re-export and the
  training-candidate flag; failed shows the error and Retry.
* **Checks and autopilot** — every gate shows its quality checks (each candidate gets a badge
  with its first problem). New asset has Autopilot, How many and Vary (a batch); the asset page
  shows the autopilot log and a switch, plus Colour variant and More like this.
* **Outfits** — pick a rigged hero, then gear per slot and a weapon; the 3D view plays the hero
  with every item fitted on it (an item not yet fitted on that hero is fitted by a worker job and
  appears when ready). Outfits can be saved, loaded ("Wear") and deleted.
* **Projects**, **Style profiles** (all fields, reference images, LoRAs of the same base model,
  and an example sheet: a knight, a wolf and a sword in the profile's style) and the **LoRA
  library** (upload or register by path; each LoRA's Examples show the same three subjects with
  and without it at a chosen weight, plus your own example images).

## Layout

```
src/
  api/client.ts      typed API client (the only module that calls fetch)
  api/queries.ts     TanStack Query hooks and cache keys: all server state lives here
  api/live.ts        WebSocket → cache updates (progress patches, status refetches)
  ui/theme.css       the one theme: tokens and every kit component's styles
  ui/kit.tsx         the shared UI kit (Button, Card, PageHeader, Tabs, Field, Input, Select,
                     TextArea, Checkbox, Dialog, Badge, ProgressBar, Notice, EmptyState,
                     Stepper, Table, Thumb, …)
  viewers/           three.js model viewer and canvas sprite player
  components/        asset status and the gate views, built from the kit
  pages/             one file per screen
```

Screens only use kit components; new kinds of UI go into the kit first.
