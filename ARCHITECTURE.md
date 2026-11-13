# Video Game Asset Creator: architecture (quick guide)

**Maintenance rule.** Any change to how the app is built (a new module or step, the job queue or card routing, the data model, the order of clean-up's passes, where files live, how the parts start) updates the affected lines of this file in the same piece of work. Keep entries to a few lines. No history, no plans: only what exists.

A local Windows app that turns one picture into a rigged, game-ready 3D model in six steps. Nothing is uploaded; no model is bundled. Not repeated here: [README.md](README.md) (install and use), [docs/FIRST-TIME-SETUP.md](docs/FIRST-TIME-SETUP.md), [docs/ADVANCED.md](docs/ADVANCED.md) (settings, running from source), [docs/API.md](docs/API.md), [docs/BACKEND.md](docs/BACKEND.md) (module map, adding an engine), [docs/BUILD-THE-EXE.md](docs/BUILD-THE-EXE.md).

## 1. Parts and how they start

| Part | Where | Notes |
|---|---|---|
| Launcher | `desktop/` (`services.js` supervises, `main.js` windows) | Electron. Probes ComfyUI A, ComfyUI B, backend and UI every 2 s, starts what it may own, builds `frontend/dist` if missing, stops only what it started. Settings: `desktop/config.json`. |
| Backend | `backend/vgac/`, entry `main.py`, port 8765 | FastAPI, SQLAlchemy, SQLite. Serves `frontend/dist` at `/`, REST at `/api`, events at `/api/ws/events`. Starts the job queue and resumes autopilot runs at startup. |
| Frontend | `frontend/` (React 19, Vite, Three.js) | `src/api.ts` is the only code that calls the backend; `src/pipeline/` holds the step screens; `npm run dev:mock` runs it with no backend (`src/mock/`). |
| ComfyUI engines | outside the repo; `runtime/start-comfy-a.ps1`, `start-comfy-b.ps1` | Run the AI models over HTTP (`comfy.py`). Graphs are built in `graphs/`; `python -m vgac.workflows` writes reference copies to `workflows/`. Own node pack: `comfy-nodes/vgac-nodes`. The backend starts an instance itself when a job needs it (`instances.py`, `start_script`, `process_match`, `discover`), records it in `library/engines.json`, and stops only copies it started. |
| Blender | `blender_exe`; scripts in `backend/vgac/blender/` | Headless, run by `tools.py` from host-lane jobs. |
| Unity | `unity_exe`, `unity-sandbox/` | Step 6 only: batch import of the FBX, or hand-off to the person's own project (`unity_project_path`). Skipped, with the reason recorded, when not configured. |

Settings: `backend/config.yaml` (`config.py`; `VGAC_CONFIG` names another file, which tests use). Preferences set in the app: `library/prefs.json` (`prefs.py`).

## 2. Data model (`db.py`, `library.py`)

- **Project**: name, platform, `style_lock` (the look), optional `unity_project_path`.
- **Asset**: category, look (`style_preset`), platform, prompt; `base_asset_id` (variant), `autopilot` state, `held_item`/`equipment` (items), `hair` (library choice), `face_matters`.
- **Version**: one result of one step: `stage`, `parent_version_id`, `status` (`queued`, `running`, `done`, `error`, `rejected`; "stopped" = rejected with error `cancelled`), `params`, `files` (key to library-relative path), `metrics` (including `checks`), `selected` (kept; one per asset and stage).
- **Job**: the queue row: `instance` (`a`, `b`, `host`), `kind` (`<stage>.<op>` or `material.<stage>.<op>`; ops `run refine adjust upload upscale compose judge`), `status`, `progress`, `message`, `payload`.
- A run's parent is the kept version of the previous step (`pipeline.run_stage`; the material category skips step 2). A **redo** is a sibling with the same parent. **Refine** (a change in words) and **adjust** (settings) make a child version of the same step with the parent's params as the start. Nothing is overwritten. Steps find inputs by walking up the parents, then the kept version of each step (`StageContext.lineage_file`).
- Files: `library/projects/<project>/assets/<asset>/<stage>/<version>/`, served by `GET /api/library/{path}`. Exports are also copied to `exports/<project>/<asset>/` beside `library/`, which also holds `vgac.sqlite`, `prefs.json`, `engines.json` and the registries of the person's own parts.
- Schema changes: add a nullable column to `_ADDED_COLUMNS` in `db.py`; there is no other migration.

## 3. The six steps

Each is `backend/vgac/stages/<stage>.py` with `run`, `refine`, `adjust`, `judge`; `stages/base.py` gives it a `StageContext`. Settings: `reference.STAGE_SCHEMAS`. Checks go in `metrics.checks`.

| Step (stage id) | In | Out | Work | Main modules |
|---|---|---|---|---|
| 1 Describe (`concept`) | description, or the person's own picture(s) | picture and cut-out | card B: one of six painters by look, BiRefNet cut-out; refine = Qwen-Image-Edit | `stages/concept.py`, `own_images.py`, `compose.py`, `readiness.py` |
| 2 Check all sides (`turnaround`) | kept picture | front, left, back, right, three-quarter views (two for humanoids) with cut-outs; bald copy for library hair | card B: Qwen-Image-Edit with a LoRA by `turn_method`; can borrow the other card to draw both sides at once; per-view checks incl. `same_side`, `pose_held`; redraws a failing view up to twice | `stages/turnaround.py`, `baldpic.py` |
| 3 Build the 3D model (`mesh`) | kept views | `glb`, texture maps | card A: TRELLIS.2 (or Hunyuan3D-2mv); normal fix, colour match, rebuild when too dark; optional head built on its own; silhouette audit in Blender; or the person's own model | `stages/mesh.py`, `graphs/trellis.py`, `headgraft.py`, `texcolour.py`, `colourmatch.py`, `normalfix.py`, `model_import.py` |
| 4 Clean up (`prep`) | kept 3D model | `prep.glb`, final maps, report | host (Blender) plus CPU and an image card for the face pass | `stages/prep.py` and the list below |
| 5 Make it move (`rig`) | clean-up model | rigged `glb`, `anim_glb`, hit boxes | card B: SkinToken by category `rig_engine` (`none` = rigid, passes through); then Blender: bend renders, hit boxes, moves, spring bones, hair bind | `stages/rig.py`, `moves.py`, `springs.py`, `blender/` |
| 6 Hand off (`export`) | rig (or clean-up) model | FBX, GLBs (LODs, Godot), texture packs, `sockets.json`, `manifest.json`, optional sprites, Unity report | host: collision shapes, Blender pack, Unity import, delivery | `stages/export.py`, `colliders.py`, `sockets.py`, `blender/pack.py` |

The **material** category (tileable surface) uses `stages/material.py` for steps 1, 3 and 6 on card B and skips 2, 4 and 5.

**Clean-up order** (`stages/prep.py` `_prep`):
1. facing decision, outline test then a toes test as tie-break (`facing.py`)
2. normal-map repair for old 3D versions (`normalfix.py`)
3. Blender `prep.py`: turn, size, pivot, weld, weighted decimation to the budget, shading, optional symmetrize, grid snap for scenery, rebake
4. texture pass: delight, physically based fix, look applied
5. picture projection into the base colour (`srcproject.py`)
6. hair found (`stages/hair_detail.py`)
7. face detail: close-up, fit, paint; held back when the fit scores under 0.65 (`stages/face_detail.py`, `face.py`, `facecloseup.py`, `facemarks.py`)
8. skin never metal (`skinmetal.py`)
9. hair detail painted, optional own hair material (`hair.py`, `hairmat.py`)
10. library hairstyle fitted to the body (`hairfit.attach`)
11. checks computed

Face and hair passes skip (reason recorded) for non-humanoids, crisp or flat looks, when off, or when nothing is found.

## 4. Job queue (`jobs.py`)

- One asyncio worker per ComfyUI instance in `config.yaml` plus one for the host; one job at a time each. Every worker waits while free RAM is under `min_free_ram_gb`.
- **Routing** (`route()`): the person's default card if it can run the work; else the step's card from `stage_instances` (`material_stage_instances`, `judge_instance`); else the first card that is on, allowed that kind (`allowed_kinds`) and capable (`instance_capabilities`; rigging is `b` only); else the job waits with a message. Host steps always run on the host.
- **Card off**: queued jobs move, the running job is stopped and requeued, never cancelled.
- **Share when busy**: an idle card takes the oldest waiting job from a busy card; a job is never split and a running job never moves.
- **Borrowing**: a running step may `lend` another card for part of its work (step 2 does). Lent only if on, allowed, idle, nothing waiting, engine already answering, enough free memory (`LEND_MIN_ROOM_GB`). Handed back at end, failure or stop; turning it off revokes it.
- **Host lane**: first come first served, except an export goes first unless an earlier job of the same asset is still queued or running.
- **Stop** kills the task and child processes and interrupts ComfyUI; the version becomes "stopped"; **Resume** requeues with the same settings and seeds. **Timeout** (`job_timeout_seconds` 3600, `blender_timeout_seconds` 1800) is an ordinary failure. **Engine lost** mid-job: requeued once, then fails. **Backend restart**: running jobs return to queued.
- Websocket events: `job`, `version`, `gpus`, `autopilot` (`events.py`).

## 5. Where things are defined

| What | Where |
|---|---|
| Looks (styles, "presets" in the UI) | `backend/vgac/data/styles.yaml` |
| Platforms (size tiers: triangle and texture budgets) | `data/platforms.yaml` |
| Categories (rig engine, size axis, pose block, clips, LODs, budgets) | `data/categories/*.yaml` |
| Recipe for look x category x platform | `reference.recipe()`; each step records what it used in `metrics.recipe` |
| Models and licences; add-ons | `data/models.yaml`; `data/addons.yaml` |
| Item words; hair library; credits | `data/items.yaml`; `data/hairlib/`, `hairstyles.py`; `data/credits.yaml` |
| Step order and ids | `reference.STAGE_ORDER` |
| Check wording, severity (`block` or `warn`) and fixes | `guidance.CHECK_GUIDE`, filled when the check is made (`quality.check`; geometry, LOD, texture and rig checks in `quality.py`) |
| Plain stage names, parameter help | `guidance.STAGE_GUIDE`, `guidance.PARAM_HELP` |
| Setup check | `preflight.py` (`GET /api/setup`) |

Autopilot (`autopilot.py`): runs steps in order; step 1 makes two candidates and metrics pick; a version is kept when it has no failed check at the `stop_on` level (`block` default, `warn`, `never`), otherwise it pauses and offers the check's fix.

The person's own parts: add-ons (`user_addons.py`, `library/my_addons.json`, `VGAC/mine/` under `loras_dir`), pictures (`own_images.py`, `uploads.py`), 3D model (`model_import.py`), hairstyles (`my_hair.py`, `library/my_hair/`), moves (`moves.py`, `library/my_moves/`), items (`items.py`, `sockets.py`).

## 6. Not in the repository

| Not tracked | Source |
|---|---|
| `library/`, `exports/` | created at run time |
| `runtime/` except the two start scripts | logs, ComfyUI folders, `models/face_landmarker.task` |
| `backend/.venv/`, `node_modules/`, `frontend/dist/`, `desktop/dist/` | `setup/Check-Setup.ps1 -Install` and the build commands |
| AI models, ComfyUI node packs, Blender, Unity | each user; `setup/Setup-Extras.ps1` with `setup/downloads.json` (licence confirmations are typed by the user) |
| `data/animations/` (two CC0 clip packs, `clips_libraries`) | downloaded by the user |
| research, plans, audits, `STATUS.md`, `REVIEW.md`, `docs/*` except the six guides | owner's computer only |

Licences: the app is GPL-3.0-or-later; the files copied into a person's Unity project (`unity-sandbox/Assets/Editor`, `VgacScripts`, `VgacShaders`) are MIT. Model licences: `data/models.yaml`.

## 7. Conventions

- Anything the person reads is plain language, no stage ids or model file names as labels, no emoji. Wording lives in `guidance.py` (`tests/test_ui_plain_names.py` and `tests/test_option_help.py` enforce it).
- One source per fact (the table in section 5); do not copy it.
- A pass that is skipped records its reason in metrics and says it in plain words; it does not fail the step.
- Code with tests but not yet seen on a graphics card is marked `UNTESTED ON A CARD` in the comment and commit message, with a per-version switch (see `stages/turnaround.py`).
- Tests: from `backend/`, `.venv\Scripts\python -m pytest -q tests` (temporary library, no ComfyUI needed; a few need Blender, `VGAC_TEST_BLENDER`). Launcher bridge: `node desktop/appbridge.test.js`. Frontend: `npm run build`, `npm run lint`.
- Loading a change: frontend, `npm run build` in `frontend/` and reload; backend, restart the process (reopening the app does not restart a backend the launcher did not start; check the queue is empty first); launcher, `npm run dist` in `desktop/` with all VGAC processes closed. Ship frontend and backend together.
