# Contributing to Forge

Thanks for helping. Bug reports, fixes, docs, new archetypes, exporters and adapters are all
welcome.

## Before you start

- **Bugs:** open an issue with the [bug template](.github/ISSUE_TEMPLATE/bug_report.yml). Include
  the error from the asset page, the stage log from `backend/data/assets/<id>/<stage>/`, your GPU,
  OS, and what the Setup page shows.
- **Features:** open an issue first for anything bigger than a small fix, so we can agree on the
  approach before you spend time on it.
- **Security problems:** don't open a public issue — see [SECURITY.md](SECURITY.md).

## Development setup

You can work on most of Forge **without a GPU**: the tests run the whole pipeline on GPU-free
doubles.

```bash
git clone https://github.com/mitsinsar/forge.git
cd forge

# Backend
cd backend
uv run pytest                # all tests; Blender tests run only when Blender is installed
uv run assetgen              # http://127.0.0.1:8000, API docs at /docs

# Frontend (in another terminal)
cd frontend
npm ci
npm run dev                  # Vite dev server, proxies /api to the backend
npm run typecheck
npm run build                # what the backend serves at /
```

To produce real assets you need the tools in [docs/requirements.md](docs/requirements.md).

## Where things are

See [docs/architecture.md](docs/architecture.md) for the big picture, and
[backend/README.md](backend/README.md) for every stage in detail.

| Change | Look in |
|---|---|
| A stage, gate or autopilot rule | `backend/src/assetgen/pipeline.py`, `checks.py` |
| A tool integration | `backend/src/assetgen/adapters/real/` (interfaces in `adapters/base.py`) |
| Blender work (clean-up, rig, sprites, FBX) | `backend/blender_scripts/` |
| Unity / Godot output | `backend/src/assetgen/exporters.py`, `effects.py`, `unity/` |
| Install steps and the Setup page | `backend/src/assetgen/setup_guide.py` (also generates `INSTALL.md`) |
| The web app | `frontend/src/pages/`, `components/`, `viewers/` |

## Pull requests

1. Branch from `main`.
2. Keep each PR to one change; describe what it does and how you checked it.
3. Add or update tests in `backend/tests/` for backend changes. Use the doubles in
   `tests/fakes/` and the fake servers rather than real tools.
4. Run `uv run pytest` in `backend/` and `npm run typecheck && npm run build` in `frontend/`.
5. If you change install steps, edit `setup_guide.py` and regenerate `INSTALL.md`:
   `cd backend && uv run python -m assetgen.setup_guide > ../INSTALL.md`.
6. Update the docs in `docs/` when behaviour users see changes.

### Style

- Python 3.12, type hints, small functions; follow the code around you.
- TypeScript strict mode; React function components with hooks.
- User-facing text is plain and short: say what happened and what to do.
- Don't commit generated data (`backend/data/`), model weights, or `assetgen.toml`.

## Licence of contributions

Forge is licensed under the [Apache License 2.0](LICENSE). By submitting a contribution you agree
that it is licensed under the same terms (section 5 of the licence), and that you have the right to
submit it. Don't include code, models or assets whose licence doesn't allow that.

## Code of conduct

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
