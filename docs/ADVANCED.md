# Advanced concepts

This is the deeper half of the [README](../README.md). You do not need any of it to make assets; read it when you want to change how the app is set up, run it from source, or understand what happens inside each step.

Contents:

1. [How the app is put together](#how-the-app-is-put-together)
2. [Settings files](#settings-files)
3. [One graphics card](#one-graphics-card)
4. [Styles, models and add-ons](#styles-models-and-add-ons)
5. [What each step does under the hood](#what-each-step-does-under-the-hood)
6. [Sending assets straight into Unity](#sending-assets-straight-into-unity)
7. [Running from source](#running-from-source)
8. [The API](#the-api)
9. [Project layout](#project-layout)

---

## How the app is put together

The app is four services on your own PC. The desktop launcher starts the ones that are not already running and shows their state in a small status window.

| Service | Address | What it does |
|---|---|---|
| **ComfyUI A** ("engine A") | `http://127.0.0.1:8188` | Builds 3D models (TRELLIS.2, Hunyuan3D). Normally ComfyUI Desktop on the first card |
| **ComfyUI B** ("engine B") | `http://127.0.0.1:8189` | Pictures, side views and rigging (Z-Image, Qwen-Image-Edit, SkinToken). The same ComfyUI install launched a second time on the other card by `runtime/start-comfy-b.ps1` |
| **Backend** | `http://127.0.0.1:8765` | Python (FastAPI). Holds the job queue, runs Blender and Unity, stores everything in `library/` |
| **App screens** | served by the backend | React and Three.js, built into `frontend/dist` |

Work is split into three queues: `a`, `b` and `host` (Blender and Unity on the PC itself). Each queue runs one job at a time, so a card never runs two models at once. A job waits while free system RAM is below `min_free_ram_gb`, and jobs left running by a crash are put back in the queue on restart.

The **Health** page shows each card, lets you switch one off, and picks a default card. With **"Use the other card when one is busy"** ticked, a waiting step moves to an idle card that can do it.

On quit, the launcher stops only the processes it started. ComfyUI A belongs to ComfyUI Desktop and is never touched.

---

## Settings files

The setup helper (`setup\Setup-Extras.ps1 -Settings`) writes suggested copies (`*.proposed.*`) with your PC's paths. It never edits the real files.

### `backend/config.yaml`

| Key | Meaning |
|---|---|
| `instances.a`, `instances.b` | Each ComfyUI: `url`, `gpu` label, `role`, the `start_script` the backend may use to start it, and `discover` (ports to look on when the usual one does not answer) |
| `instances.b.output_dir` | Engine B's output folder. SkinToken writes its rigged model there |
| `library_path` | Where projects, assets, every version and the SQLite file `vgac.sqlite` are stored |
| `blender_exe` | Blender 5.x. Only 5.2.3 is tested |
| `unity_exe`, `unity_project` | The Unity Editor and the sandbox project used to build prefabs (`unity-sandbox/`) |
| `comfy_input_dir` | ComfyUI's shared input folder (both engines read pictures from it) |
| `loras_dir` | ComfyUI's `loras` folder. Your own add-ons go in its `VGAC/mine/` folder |
| `clips_libraries` | Paths to the animation packs (`ual1`, `ual2`) |
| `min_free_ram_gb` | Hold jobs while free RAM is below this. Default 24; lower it on a PC with less than 64 GB |
| `job_timeout_seconds` | How long one ComfyUI job may run before it fails |
| `stage_instances` | Optional. Which engine runs each step. Default `{concept: b, turnaround: b, mesh: a, prep: host, rig: b, export: host}` |
| `material_stage_instances` | The same, for the `material` category |
| `judge_instance` | Which engine runs the local picture judge (Gemma 3, optional) |
| `instance_capabilities` | Which steps each card can take over when the other is switched off. Rigging stays on `b` only (see below) |

Restart the backend after editing this file. From the launcher: **Services > Restart backend**. Closing the app window is not enough if the backend was started outside the launcher.

### `desktop/config.json`

The launcher's paths: `projectRoot`, ComfyUI A's `desktopExe`, the start script, log and process match for ComfyUI B and the backend, and the ports. Edit, then restart the launcher. A built launcher reads `desktop/dist/config.json`.

### `runtime/start-comfy-a.ps1`, `runtime/start-comfy-b.ps1`

Start each ComfyUI on its card and port. The B script also sets `SKINTOKEN_BLENDER_BIN`, which is how the rigging node finds Blender. Without that variable, rigging cannot run.

---

## One graphics card

The app is set up for two cards, but one card of 10 GB or more is expected to work. Not every step has been measured on one card, so treat this as untested.

1. Run **one ComfyUI only**: engine B (port 8189, `runtime/start-comfy-b.ps1`). Rigging only runs on B.
2. On the **Health** page, switch off the card you do not have. Pictures and 3D models then run on the one card, one step at a time.
3. The setup helper's `-Settings` part detects one card and writes `stage_instances`, `material_stage_instances` and `judge_instance` all set to `b`, with no engine A start script.
4. Do not run two ComfyUIs on one card; it is likely to run out of graphics memory.

---

## Styles, models and add-ons

Data files in `backend/vgac/data/` drive these; editing them needs a backend restart.

| File | What it holds |
|---|---|
| `style_presets.yaml` | The looks offered in **New project** (Painted fantasy, Clean cartoon, Pixel retro, Realistic, Clay toy): model, prompt block, negative prompt, palette |
| `styles.yaml` | Style data the picture steps use |
| `models.yaml` | Every model file the app knows, with its exact file name, engine, licence line and whether it is required |
| `addons.yaml` | LoRA add-ons (pixel art, clay, PS1 look, and the two the side views need) |
| `platforms.yaml` | Triangle and texture budgets per platform (Phone, PC and console, and so on) |
| `categories/` | Per-category rules: height, whether it gets a skeleton, which steps it skips |
| `items.yaml` | Item kinds for **Add an item** and where they attach |
| `hairlib/` | The hair library (26 CC0 styles); credits in its `CREDITS.txt` |
| `credits.yaml` | Source of truth for the Credits page and `CREDITS.md` |

A project's **style lock** (its look) fixes the picture model, prompt block, palette and add-ons for every asset in it. Under **Advanced** in the New asset form you can override the model for one asset.

Picture models available: Z-Image Turbo (default, fast painted), Z-Image Base, FLUX.2 Klein 4B, Anima Turbo, Z-Image Pixel Art, Chroma1-HD. Licences differ; see [FIRST-TIME-SETUP.md](FIRST-TIME-SETUP.md#5-comfyui-its-node-packs-and-the-models-by-hand).

---

## What each step does under the hood

| Step | Technical name | Engine | What runs |
|---|---|---|---|
| 1. Describe | `concept` | B | Z-Image Turbo (or the project's model) plus a BiRefNet cutout. **Change something** is a Qwen-Image-Edit-2511 instruction edit |
| 2. Check all sides | `turnaround` | B | Qwen-Image-Edit-2511 with the multiple-angles LoRA: five views with cutouts and consistency checks (height ratio, colour histogram, left/right mirror overlap). Re-rolls up to twice by itself |
| 3. Build the 3D model | `mesh` | A | TRELLIS.2 shape and texture, then ComfyUI's remesh, decimate, unwrap and bake to the platform budget (or Hunyuan3D-2mv from four views). A Blender silhouette audit every 15 degrees |
| 4. Clean up for the game | `prep` | host | Blender: scale to the category height, base pivot, weld, symmetrize, smooth shading, manifold report; face detail pass and hair fitting |
| 5. Make it move | `rig` | B, then host | SkinToken (Mixamo template for humanoids), bend tests at 35 and 90 degrees, idle and walk clips with a rendered strip. Skipped for rigid props |
| 6. Hand off to the game | `export` | host | Blender pack (LODs, convex collider under 255 vertices, FBX with clips, GLB), Unity sandbox import with the CC0 clip library, optional sprite sheets, `manifest.json`, quality checks |

The `material` category (tileable surfaces) runs Describe, a seamless blend with derived maps in place of the 3D step, and an export of the texture set; clean-up and rigging are skipped.

**Autopilot** runs the steps in order. Where a step makes two candidates, it picks by the step's measured checks, and it stops only when a check fails in a way it cannot fix itself.

Every result is a **version** in `library/projects/<project>/assets/<asset>/<stage>/<version>/`, with its files and metrics. Changing an early step makes a new version there; the finished files stay until you rebuild the later steps.

More detail: [ARCHITECTURE.md](../ARCHITECTURE.md) and [BACKEND.md](BACKEND.md).

---

## Sending assets straight into Unity

Every export is copied to `exports/<project>/<asset>/`. A project can also have a **Unity project path** (`unity_project_path` on the project, set through the API). When it is set, each export also imports the FBX into that Unity project with the same importer the sandbox uses.

If the Unity project is open at the time, the files wait in `Assets/VGAC/<Name>/_incoming/` and import themselves the next time you switch to Unity (or use **Tools > VGAC > Import pending assets**).

The files copied into your Unity project (the importer, the spring-bone script and the hair shaders) are MIT-licensed, so they never put your game under the GPL.

---

## Running from source

### First-time setup by hand

The setup helper does these, but by hand it is:

Backend (from `backend/`):

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

```bash
.venv\Scripts\python scripts\make_sample_glb.py
```

Frontend and launcher (from `frontend/`, then `desktop/`):

```bash
npm install
```

### Run each part yourself

Backend on port 8765 with auto-reload (from `backend/`):

```bash
.\run.ps1
```

Frontend dev server on port 5173, which forwards `/api` and the event WebSocket to the backend (from `frontend/`):

```bash
npm run dev
```

Then open http://localhost:5173. For a single-origin build, run `npm run build` in `frontend/`; the backend serves `frontend/dist` at http://127.0.0.1:8765/ (restart the backend after the first build).

Restart a running backend with `backend/restart.ps1`, which also clears stale copies that would otherwise keep serving old code.

### Mock mode (screens only, no backend or graphics card)

An in-browser mock of the whole API, with simulated jobs and placeholder pictures:

```bash
npm run dev:mock
```

The mock store lives in memory and resets on reload. `window.vgacMock.setSpeed(4)` makes simulated jobs run faster. See [frontend/README.md](../frontend/README.md).

### The desktop launcher

From `desktop/`: `npm run start` runs it from source, `npm run dist` builds the portable exe in `desktop/dist/`, and `make-shortcut.ps1` puts a VGAC shortcut on the desktop. Close every running VGAC window before rebuilding. Step by step: [BUILD-THE-EXE.md](BUILD-THE-EXE.md).

### Tests

From `backend/`:

```bash
.venv\Scripts\python -m pytest -q tests
```

Regenerate the reference ComfyUI graphs in `workflows/`:

```bash
.venv\Scripts\python -m vgac.workflows
```

---

## The API

The full contract is [API.md](API.md). In short:

| Area | Endpoints |
|---|---|
| Reference data | `GET /api/health`, `/api/categories`, `/api/styles`, `/api/platforms`, `/api/stages` (the Fine-tune schemas) |
| Projects | `GET\|POST /api/projects`, `GET\|PUT\|DELETE /api/projects/{id}` |
| Assets | `GET\|POST /api/projects/{pid}/assets`, `GET\|PUT\|DELETE /api/assets/{id}` |
| Steps | `POST /api/assets/{id}/stages/{stage}/run`, `POST /api/versions/{vid}/refine\|adjust\|select\|reject\|judge`, `GET /api/versions/{vid}` |
| Jobs | `GET /api/jobs?asset_id=&status=`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel`, `POST /api/queue/{instance}/pause?paused=`, `WS /api/ws/events` |
| Files | `GET /api/library/{path}` serves files from the library folder |

---

## Project layout

| Folder | What is in it |
|---|---|
| `backend/` | Python 3.13 package `vgac`: FastAPI, SQLAlchemy, the ComfyUI client, the job queue, the step modules, Blender scripts and data files. Own venv in `backend/.venv` |
| `frontend/` | Vite, React 19, TypeScript, Three.js viewer |
| `desktop/` | The Electron launcher |
| `setup/` | `Check-Setup.ps1`, `Setup-Extras.ps1` and `downloads.json` (links, sizes, checksums) |
| `comfy-nodes/` | The project's own ComfyUI node pack, `vgac-nodes` |
| `workflows/` | ComfyUI graphs per engine, generated from the graph builders |
| `unity-sandbox/` | The Unity project used to build prefabs |
| `data/animations/` | The CC0 animation packs |
| `library/` | Your projects, assets and versions, plus `vgac.sqlite` (not in Git) |
| `exports/` | Finished deliverables, one folder per project and asset |
| `runtime/` | ComfyUI start scripts, engine B's folders and logs |
| `bakeoff/` | The phase 0 experiments that chose the engines ([BAKEOFF.md](../bakeoff/BAKEOFF.md)) |
