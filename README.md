# Video Game Asset Creator

Local desktop tool that takes a text prompt to a rigged, game-ready 3D asset. See ARCHITECTURE.md for the design. This is the phase 0 skeleton: a FastAPI backend with a SQLite database and a per-instance job queue, and a React frontend with a health panel and a Three.js GLB viewer. Nothing user-facing beyond that yet.

## Layout

- `backend/` Python 3.13 package `vgac` (FastAPI, SQLAlchemy, httpx ComfyUI client, asyncio job queue, stage modules, Blender scripts, category data). Own venv at `backend/.venv`. See `docs/BACKEND.md`.
- `backend/config.yaml` ComfyUI instances (and instance B's output folder), library path, Blender and Unity paths, ComfyUI input folder, clip libraries, RAM floor.
- `frontend/` Vite + React 19 + TypeScript + react-router, Three.js viewer. Screens: projects and style lock, asset grid, six-stage pipeline with Review / Refine / Adjust, live job log. `VITE_MOCK=1` runs it against an in-browser mock backend.
- `workflows/` ComfyUI API-format JSON graphs per engine, written by `python -m vgac.workflows` from the graph builders.
- `library/` asset storage and the SQLite file `library/vgac.sqlite`. Gitignored.
- `bakeoff/` phase 0 experiment scripts and outputs (TRELLIS.2 vs Hunyuan3D-2mv, SkinToken rigging). See `bakeoff/BAKEOFF.md`.
- `runtime/` ComfyUI instance B user and output folders plus logs. Gitignored.

## Machine setup this project relies on

- GameDevComfy (ComfyUI Desktop) on the RTX 3060 at http://127.0.0.1:8188 handles 3D jobs.
- A second ComfyUI instance on the RTX 3080 at http://127.0.0.1:8189 handles image jobs and SkinToken rigging. It is the same install launched a second time; the launch line is in `runtime/start-comfy-b.ps1`.
- Blender 5.2.3 LTS portable at `F:\Tools\blender-5.2.3-windows-x64\blender.exe` (the old 3.4 under Program Files is untouched). Run headless with `blender -b --python script.py -- args`.
- Custom nodes added to the ComfyUI install: `ComfyUI-SkinToken` (auto-rigging, weights under `F:\Projects\ComfyUI\models\skintoken` via a junction) and `vgac-nodes` (TRIMESH loader with decimation, so native 3D outputs can feed SkinToken).

## First-time setup

Backend (from `backend/`):

    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
    .venv\Scripts\python scripts\make_sample_glb.py

Frontend (from `frontend/`):

    npm install

## Run

Backend, port 8765, auto-reload (from `backend/`):

    .\run.ps1

or

    .venv\Scripts\python -m uvicorn vgac.main:app --port 8765 --reload

Frontend dev server, port 5173, proxies `/api` (HTTP and the `/api/ws/events` WebSocket) to the backend (from `frontend/`):

    npm run dev

Then open http://localhost:5173. For a single-origin build, run `npm run build` in `frontend/`; the backend serves `frontend/dist` at http://127.0.0.1:8765/ when it exists (restart the backend after the first build).

Frontend without the backend (mock mode). `VITE_MOCK=1` installs an in-browser mock of the whole docs/API.md contract (projects, assets, versions in every stage, jobs with simulated progress over a fake WebSocket, canvas-generated placeholder images) and the dev server serves `library/` itself so the sample GLB loads:

    VITE_MOCK=1 npm run dev        # bash
    npm run dev:mock               # any shell (vite --mode mock reads frontend/.env.mock)

The mock store lives in memory and resets on a page reload; `window.vgacMock.store` exposes it and `window.vgacMock.setSpeed(4)` makes simulated jobs run faster. See `frontend/README.md` for the screens and the data layer.

## Desktop launcher

`desktop/` is an Electron launcher (decision D13). It shows a status window for the four services (ComfyUI A, ComfyUI B, backend, UI), starts ComfyUI B and the backend when they are not already running (and builds `frontend/dist` once if it is missing), then opens the app at http://127.0.0.1:8765/ in its own window with a tray icon and a Services menu (restart backend, restart ComfyUI B, open logs and exports folders). On quit it stops only the processes it started; ComfyUI A belongs to ComfyUI Desktop and is never touched. Paths and ports live in `desktop/config.json`.

    cd desktop
    npm install                      # once (then `node node_modules/electron/install.js` if the Electron binary did not download)
    npm run start                    # run from source
    npm run dist                     # portable exe in desktop/dist/
    powershell -ExecutionPolicy Bypass -File make-shortcut.ps1   # VGAC.lnk on the desktop (built exe if present, else from source)

## Backend

The backend is the phase 1 build: every endpoint in `docs/API.md`, six stage modules that port the proven bake-off engines, a per-instance job queue with websocket events, category and style data files, the quality gate and the manifest. Module map, data flow and how to add an engine are in `docs/BACKEND.md`.

Pipeline per asset (stage -> engine -> instance):

1. `concept` Z-Image Turbo (or Z-Image base, FLUX.2 Klein, Anima, pixel LoRA, Chroma by style preset or project style lock) plus a BiRefNet cutout; Refine is a Qwen-Image-Edit-2511 instruction edit. Instance B.
2. `turnaround` Qwen-Image-Edit-2511 + multiple-angles LoRA, five views with cutouts and consistency checks (height ratio, colour histogram, left/right mirror IoU), auto re-roll up to twice. Instance B.
3. `mesh` TRELLIS.2 shape + texture + native remesh/decimate/unwrap/bake chain to the category and platform budget (or Hunyuan3D-2mv from four views), then a Blender silhouette audit at 15 degree steps. Instance A, audit on the host.
4. `prep` Blender: scale to the category height, base pivot, weld, symmetrize, smooth shading, manifold report. Host.
5. `rig` SkinToken (Mixamo template for humanoids, model names for other creatures, skipped for rigid categories), rig inspection with bend tests at 35 and 90 degrees, procedural idle and walk clips with a rendered strip. Instance B, then Blender on the host.
6. `export` Blender pack (LODs, convex collider under 255 vertices, FBX with clips, GLB), Unity sandbox import with the CC0 clip library for humanoids and a clip snapshot strip, optional sprite sheets, `manifest.json`, quality checks. Host.

The `material` category (tileable PBR surface) runs concept (surface render), mesh (deterministic seamless blend and derived maps) and export (texture zip and manifest) on instance B and the host; prep and rig record a skip.

Run the tests from `backend/`:

    .venv\Scripts\python -m pytest -q tests

Regenerate the reference graphs in `workflows/`:

    .venv\Scripts\python -m vgac.workflows

## API

The full contract is `docs/API.md`. In short:

- Reference data: `GET /api/health`, `/api/categories` (11 entries), `/api/styles`, `/api/platforms`, `/api/stages` (Adjust schemas).
- Projects: `GET|POST /api/projects`, `GET|PUT|DELETE /api/projects/{id}`.
- Assets: `GET|POST /api/projects/{pid}/assets`, `GET|PUT|DELETE /api/assets/{id}` (full object with `selected` and `stages`).
- Stages: `POST /api/assets/{id}/stages/{stage}/run` (`count` up to 4 for concept returns a list), `POST /api/versions/{vid}/refine|adjust|select|reject|judge`, `GET /api/versions/{vid}`.
- Jobs: `GET /api/jobs?asset_id=&status=`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel`, `POST /api/queue/{instance}/pause?paused=`, `WS /api/ws/events`.
- Files: `GET /api/library/{path}` serves files from the library folder (`library/projects/<pid>/assets/<aid>/<stage>/<vid>/`).

The job queue runs one asyncio worker per instance (`a`, `b`, `host`), never runs two jobs on one instance, holds a job while free system RAM is under `min_free_ram_gb`, and re-queues jobs left running by a crash.
