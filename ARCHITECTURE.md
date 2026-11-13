# Video Game Asset Creator: architecture

How the application is built, for a developer who has just cloned the repository. It describes the code as committed. How to install and use the app is in [README.md](README.md) and [docs/FIRST-TIME-SETUP.md](docs/FIRST-TIME-SETUP.md); settings files, running from source and the project layout are in [docs/ADVANCED.md](docs/ADVANCED.md); the endpoints are in [docs/API.md](docs/API.md); the backend module map and how to add an engine are in [docs/BACKEND.md](docs/BACKEND.md); building the launcher is in [docs/BUILD-THE-EXE.md](docs/BUILD-THE-EXE.md). This file does not repeat them.

## 1. What it is

A single-user Windows application that runs entirely on the owner's computer. One picture (drawn from a description, or supplied by the person) becomes a rigged, textured, game-ready model in six steps. Nothing is uploaded, and no model is bundled: every model is downloaded by each user from its publisher under its own licence (section 11).

| Step | Plain name in the app | Stage id in the code |
|---|---|---|
| 1 | Describe | `concept` |
| 2 | Check all sides | `turnaround` |
| 3 | Build the 3D model | `mesh` |
| 4 | Clean up for the game | `prep` |
| 5 | Make it move | `rig` |
| 6 | Hand off to the game | `export` |

The stage ids are the keys in the database, the API, the folders and the job kinds. The plain names live in `backend/vgac/guidance.py` (`STAGE_GUIDE`). The order is `reference.STAGE_ORDER`, the one definition of it.

## 2. The pieces and how they start

```
 Electron launcher (desktop/)
   |  starts and watches
   v
 Backend: FastAPI, port 8765 (backend/vgac)  <--- serves frontend/dist at /, REST at /api, events at /api/ws/events
   |  job queue: one worker per graphics card, one for the host
   |-- ComfyUI instance A (config key "a", port 8188)   pictures and 3D models
   |-- ComfyUI instance B (config key "b", port 8189)   pictures, 3D models, rigging
   |-- Blender, headless (host)   clean-up, rig repair, clips, export packing, renders
   |-- Unity Editor, batch mode (host)   import into unity-sandbox/ or a person's own project
   v
 library/ (SQLite database and every generated file)
```

| Piece | Where | What it does |
|---|---|---|
| Launcher | `desktop/` (`main.js`, `services.js`, `procs.js`, `appbridge.js`, `preload*.js`, `status.*`) | Electron. `services.js` probes four services every 2 seconds (ComfyUI A, ComfyUI B, backend, UI), starts the ones it may own, builds `frontend/dist` when it is missing, and stops only what it started. It opens the app in its own window and shows a status window and tray icon. The app page can reach three narrow calls through `preload-app.js` (taskbar count, a system notification, start the engine), checked by `appbridge.js` against the backend's origin. Its settings are `desktop/config.json`. |
| Backend | `backend/vgac/` (entry `main.py`) | Python 3.13, FastAPI, SQLAlchemy over SQLite. Holds the data model, the job queue, the step modules and every rule. At start it creates the tables, starts the queue, and resumes autopilot runs that were going. Run by hand with `backend/run.ps1` (auto-reload) or by the launcher with uvicorn. |
| Frontend | `frontend/` | Vite, React 19, TypeScript, react-router, Three.js (the model viewer). Built to `frontend/dist`, which the backend serves at `/` with client-side routes falling back to `index.html`. `src/api.ts` is the only code that talks to the backend; `src/events.ts` listens to the websocket and falls back to polling `/api/jobs` every 3 seconds. Routes (`src/main.tsx`): projects, `projects/:pid`, `assets/:aid/:stage?`, gallery, glossary, credits, health. The step screens are in `src/pipeline/`, one view per step in `src/pipeline/views/`. Mock mode (`npm run dev:mock`, `src/mock/`) runs the screens in the browser with no backend. |
| ComfyUI engines | outside the repository; started by `runtime/start-comfy-a.ps1` and `runtime/start-comfy-b.ps1` | Run the AI models. The backend talks to each over HTTP (`comfy.py`): it submits a graph to `/prompt`, polls `/history`, downloads from `/view`, and copies inputs into the shared input folder (`comfy_input_dir`). The graphs are built in `backend/vgac/graphs/` and written for reference to `workflows/` by `python -m vgac.workflows`. The project's own node pack is `comfy-nodes/vgac-nodes` (`VGACLoadTrimesh`, `VGACSaveTrimesh`). Both start scripts set `SKINTOKEN_BLENDER_BIN`, which the rigging node needs. |
| Blender | path in `config.yaml` (`blender_exe`); scripts in `backend/vgac/blender/` | Run as `blender -b --python <script> -- args` by `tools.py`, always from inside a job on the host lane. Blender 5.0 or newer. |
| Unity | `unity_exe`, `unity_project` in `config.yaml`; project in `unity-sandbox/` | Used only in step 6: a batch-mode import of the finished FBX into the sandbox (clips, prefab with LOD group, snapshot strip), or a hand-off into the person's own project (section 5, step 6). Skipped with a recorded reason when `unity_exe` is not set. |

Starting engines. The backend starts a ComfyUI instance itself when a job needs it and nothing answers (`instances.py`). It runs the instance's `start_script` detached, remembers the process in `library/engines.json`, and only ever stops a process it started. A copy started by someone else (ComfyUI Desktop, the launcher) is waited for and left alone. `process_match` identifies an already-running copy; `discover` finds an instance by the graphics card it runs on, because ComfyUI Desktop picks its own port. The launcher and the backend share this logic by agreement: the launcher treats a ComfyUI it did not start as foreign.

Settings are `backend/config.yaml` (typed by `config.py`; the `VGAC_CONFIG` environment variable names another file, which the tests use) and the preferences the person sets in the app, kept in `library/prefs.json` (`prefs.py`: cards turned off, kinds of work each card may do, default card, share when busy, free memory after each job, stop an engine we started after N idle minutes).

## 3. Repository layout

| Path | Contents |
|---|---|
| `backend/vgac/` | The package. Top level: HTTP and rules (`main.py`, `pipeline.py`, `jobs.py`, `autopilot.py`, `db.py`, `library.py`, `reference.py`, `guidance.py`, `quality.py`, `comfy.py`, `instances.py`, `prefs.py`, `preflight.py`); the optional parts (`user_addons.py`, `my_hair.py`, `moves.py`, `items.py`, `hairlib.py`, `own_images.py`, `compose.py`, `model_import.py`); the picture and mesh maths each step uses (`srcproject.py`, `face.py`, `facing.py`, `hair.py`, ...). Subfolders: `stages/` (one module per step plus the face and hair passes), `graphs/` (ComfyUI graph builders), `blender/` (headless Blender scripts), `data/` (YAML and the hair library). |
| `backend/tests/` | pytest suite (section 12). |
| `frontend/` | The web interface. |
| `desktop/` | The launcher. |
| `comfy-nodes/vgac-nodes/` | The project's ComfyUI node pack. |
| `workflows/` | Reference copies of every ComfyUI graph at default settings (generated, not read by the app). |
| `unity-sandbox/` | The Unity project used for batch import. `Assets/Editor/VgacImport.cs`, `VgacSpringCheck.cs`, `Assets/VgacScripts/` and `Assets/VgacShaders/` are copied into a person's own project on hand-off. |
| `setup/` | `Check-Setup.ps1`, `Setup-Extras.ps1`, `downloads.json` (section 11). |
| `runtime/` | Only the two ComfyUI start scripts are tracked. The rest is created at run time (section 11). |
| `bakeoff/`, `tools/` | Experiment scripts and measurement or build scripts (for example `tools/hairlib_build.py`, which builds the hair library; `tools/e2e*.py`, which drive a running app). Not imported by the app. |
| `docs/` | The six kept guides (see top). Everything else under `docs/` is ignored by git. |

## 4. Data model

Four tables in `library/vgac.sqlite` (`db.py`). Ids are uuid4 strings. Structured fields are JSON text columns with dict accessors. The schema is `create_all` plus a forward-only list of added columns (`_ADDED_COLUMNS`) applied at start, and `migrate_looks`, which maps old look ids to the current ones (it copies the database file first when it has to change something).

**Project**: `name`, `style_lock` (the look: `prompt_block`, `model`, `loras`, `palette`, `negative`, `preset`), `platform_target`, optional `unity_project_path` (an absolute folder with `Assets` and `ProjectSettings`). Every asset in a project shares the style lock, so assets match.

**Asset**: belongs to a project. `category`, `style_preset`, `platform_target`, `prompt`, `parts`, `concept_model`. Plus: `base_asset_id` and `variant_change` (an asset made by "Make a variant"; the base's kept concept is copied as the variant's first concept version and refined with the change, so the identity holds); `autopilot` (the run state); `held_item` and `equipment` (items, section 10); `hair` (the hair-library choice); `face_matters` (turns the face pass off for a character whose face is hidden).

**Version**: one result of one step. `asset_id`, `stage`, `parent_version_id`, `status`, `params`, `files`, `metrics`, `job_id`, `error`, `selected`.

- `status`: `queued`, `running`, `done`, `error`, `rejected`. "Stopped" is not a status: a version is stopped when it is `rejected` with the error text `cancelled` (`db.is_stopped`); the API adds `stopped: true`.
- `params`: the settings the version ran with: the stage's defaults from `reference.STAGE_SCHEMAS`, then what the request sent, then anything the step decided and recorded (`sc.set_params`, for example the seed it drew).
- `files`: a map from a key (`image`, `cutout`, `glb`, `base_color`, `fbx`, ...) to a path relative to the library root, forward slashes. Served by `GET /api/library/{path}`.
- `metrics`: everything measured, including `checks` (section 8), the `models` used, and per-step records.
- `selected` ("kept"): at most one version per asset and stage. The first version of a stage is kept automatically; `POST /api/versions/{id}/select` moves it.

**Job**: one unit of queued work. `instance` (`a`, `b` or `host`), `kind`, `asset_id`, `version_id`, `status` (`queued`, `running`, `done`, `error`, `cancelled`), `progress`, `message`, `payload`, `result`, `error`, timestamps. The kind is `<stage>.<op>`, or `material.<stage>.<op>` for the material category. The ops are `run`, `refine`, `adjust`, `upload`, `upscale`, `compose`, `judge` (dispatched in `stages/__init__.py`). The job table is the queue; nothing is held only in memory.

How versions relate. A step's run makes a version whose parent is the kept version of the previous step (`pipeline.run_stage`; the parent must be `done`; for the material category, which skips step 2, the walk back skips only stages the category lists in `skips`). Concept is the only step that makes several candidates at once (a `count`). A **redo** is a new run of the same step: a sibling with the same parent. A **refine** (describe a change in words, optionally a mask or a view) and an **adjust** (change settings) make a new version of the same step whose parent is the version being changed and whose params start from the parent's. Nothing is overwritten: every try stays as a version. A stage finds its inputs by walking up the parent chain and then falling back to the kept version of each step (`StageContext.lineage_file`), so an adjust of step 3 can rerun from the same picture, and the export finds textures on the 3D version without copying them through steps 4 and 5.

Deleting a version removes it, its job rows and its folder; a kept version, or one still queued, is refused, and versions made from it go only when asked (`with_later`).

Files on disk: `library/projects/<project>/assets/<asset>/<stage>/<version>/`. The person's own close-up of a face is `assets/<asset>/own/face_closeup.png`. Finished deliverables are also copied to `exports/<project>/<asset>/` beside the library folder (or under `export_path`); `GET /api/gallery` lists every asset with a finished export. The library folder also holds `prefs.json`, `engines.json`, and the registries of the person's own add-ons, moves and hairstyles (section 10).

## 5. The six steps

Each step is a module `backend/vgac/stages/<stage>.py` with `run`, `refine`, `adjust` and usually `judge` (recompute measurable checks from the version's files; `POST /api/versions/{id}/judge` with `mode: metrics` runs it at once, `mode: local` queues the picture judge, section 8). `stages/base.py` gives each a `StageContext`: the asset, project, category, look, budget, version folder, progress reporting, `comfy()` to run a graph, `cutout()`, `blender()`, `unity()`, lineage lookups and `use_model()`, which records a model and its licence for the manifest.

The settings each step accepts ("Fine-tune") are defined once in `reference.STAGE_SCHEMAS` and validated by `reference.validate_params`; a setting that does not apply to a category is dropped.

### Step 1: Describe (`stages/concept.py`)

In: the asset's description, its look, category and platform. Or the person's own picture (`own_images.py`, `uploads.py`), or several of their pictures combined into one (`compose.py`, step 0.5).
Out: `image` and a background cut-out (`cutout`, `cutout_white`). Runs on card B by default.
Engine: one of six painters chosen by the look (`data/models.yaml`, entries marked `concept: true`): Z-Image Turbo, Z-Image Base, FLUX.2 Klein 4B, Anima, Z-Image with a pixel-art add-on, Chroma1-HD. The prompt is the description plus the look's block, hair sentence (`hairpic.py`), the palette, the category's pose block and the look's face sentence. A refine is a Qwen-Image-Edit-2511 instruction edit of the parent picture, optionally limited to a mask. BiRefNet cuts the subject out. Items named in a description are set aside so the character is drawn empty-handed (section 10).
Checks: readiness for 3D (`readiness.py`: one subject, in frame, big enough, arms away from the body); a check that the face is drawn. A failing readiness check carries the instruction its Fix sends to a refine.

### Step 2: Check all sides (`stages/turnaround.py`)

In: the kept concept version. Out: front, left, back, right and three-quarter views (and a second three-quarter view, `quarter2`, for humanoids), each with its raw picture, cut-out and white copy. Runs on card B by default.
Engine: Qwen-Image-Edit-2511 with a LoRA, by `turn_method`: `standard` (multiple-angles LoRA, one job per view), `sheet` (all sides in one picture, split into five figures), `shadow_free` (camera-angle LoRA, clean white views for props). The view graph can include its own cut-out nodes (`cutout_in_graph`, on by default), and can set the unused negative to zeros instead of encoding it again (`zero_negative`, on by default); both are recorded in the version's metrics and can be turned off per version. An optional "pose for 3D first" redraw (`pose_ready`) rebuilds the picture once with open empty hands before any view is drawn. For a character built with a hair-library hairstyle, step 2 also draws a bald copy of the picture (`baldpic.py`) from which step 3 builds.
Two cards: with the standard method and both sides to draw, the step asks the queue to lend it the other card (section 6) and draws the left side on its own card and the right side on the lent one at the same time. If the lent card is revoked or its engine stops answering, the right side finishes on the job's own card and the version says so.
Checks per view, against the front: coverage, height ratio, histogram distance, left-right mirror overlap, `same_side` (a view whose outline matches its partner better as drawn than mirrored was drawn from the wrong side), and for front and back views of humanoids `pose_held` (the arms and legs reach as far out as in the picture). A failing view is redrawn with a new seed up to twice when `auto_reroll` is on, then flagged. The person's own picture for a view is kept as it is, cut out and checked, never redrawn.

### Step 3: Build the 3D model (`stages/mesh.py`)

In: the kept turnaround version. Out: `glb`, texture maps (`base_color`, `metallic`, `roughness`, `normal`, `ao`), preview and input pictures. Runs on card A by default.
Engine: TRELLIS.2 (shape and texture, through ComfyUI) from one cut-out, decimated to the recipe's triangle target; or Hunyuan3D-2mv from the four side views (`engine: hy3d`, Tencent licence recorded in the manifest). The graph is `graphs/trellis.py`. After the download: the normal map's green channel is flipped once (`normalfix.py`); maps are grown into the seams; the base colour is shifted toward the picture's tone (`colourmatch.py`); a build whose base colour is far too dark against the picture is built again with the next seed (`texcolour.py`). For humanoids a model is built with more triangles than the budget so the clean-up step can keep more on the head (`headtris.py`). On the bald route (hair from the library) the head can be built on its own from a head-and-shoulders crop and joined to the body (`headgraft.py`, `blender/headjoin.py`), and the library's hairstyles are ranked on the bald model (`hairfit.rank`). The person's own 3D model can replace all of this (`model_import.py`: GLB, glTF, FBX, OBJ or a zip, imported by Blender on the host with no card used). Refine with an instruction edits the picture first and builds from the edit (TRELLIS only).
Checks: triangle budget, silhouette match at 15-degree steps against the step 2 cut-outs (Blender renders; the table is also what step 4's facing decision reads), texture against the picture, hair fit.

### Step 4: Clean up for the game (`stages/prep.py`)

In: the kept 3D version. Out: `glb` (`prep.glb`), the final texture maps, `prep_report.json`, and for hair-library characters the hair as separate files. Runs on the host (Blender), with the picture work on the CPU and the face pass on an image card.

The order of the passes:

1. Facing decision (`facing.py`): the 3D step's outline test chooses a quarter turn; a second test from the feet (toes point forward) decides when the outline margin is small or the two disagree.
2. Normal map repair for 3D versions made before the fix (`normalfix.py`).
3. Blender `blender/prep.py`: turn to face +Z, scale on the category's size axis (a size written in the description is honoured), pivot, weld, smooth or flat shading from the look, decimation to the budget (weighted to keep the head, `headtris.py`, `blender/headweight.py`), optional symmetrize, and for scenery the grid snap and tiling picture. Maps are baked again onto the final mesh when the surface changed (`blender/rebake.py`).
4. Texture pass: remove painted-in light (delight, by look), bring colours into physically based ranges (Photoreal), apply the look (palette limit and nearest-neighbour shrink for crisp looks).
5. Picture projection (`srcproject.py`): the original picture and the turnaround views are fitted to the model by silhouette (a view that does not fit, or whose turn contradicts the front, is refused) and their pixels are painted into the base colour through the existing UVs. Pictures are padded to a square, small ones are enlarged first, and the head is fitted on its own by its eyes and mouth.
6. Hair detail, find (`stages/hair_detail.py`, `hair.py`): which triangles are hair.
7. Face detail (`stages/face_detail.py`, `face.py`, `facecloseup.py`, `facemarks.py`): the head is rendered from a known camera, Qwen-Image-Edit draws a sharper close-up (redrawn with a new seed up to twice if rejected, or the person's own close-up is used), eyes and mouth are found with MediaPipe, the close-up is fitted to the render and painted back through the UVs. When the face has too few texels its pieces are enlarged and every map is baked again (`blender/faceuv.py`). The paint is held back, and the step says so, when the fit scores under 0.65.
8. Skin is never metal (`skinmetal.py`).
9. Hair detail, paint: strands, shading and a hairline, optionally on a texture of its own (`hairmat.py`).
10. Hair from the library: the chosen hairstyle is fitted to the final body as separate files (`hairfit.attach`).
11. The checks are computed (`judge`).

Skipped, with the reason recorded and said in plain words: the face and hair passes for categories other than humanoid, for crisp and flat looks, when switched off, and when no face or hair can be found with confidence. The material category records a skip for the whole step.
Checks: triangle budget (with the head's share), size, facing and the facing tests agreeing, manifold, texture sizes, delight and colour, look applied, face and hair pass results, hair fit.

### Step 5: Make it move (`stages/rig.py`)

In: the kept clean-up version. Out: the rigged `glb`, `anim_glb` (with clips), bone and bend pictures, hit boxes and the body shape for moving. Runs on card B for SkinToken, then Blender on the host.
Engine: SkinToken (through ComfyUI, `graphs/skintoken.py`) predicts the skeleton and skin weights. The category's `rig_engine` decides: `skintoken_mixamo` (humanoid, Mixamo bone names), `skintoken` (quadruped, multi-leg, winged, serpent, the model's own names), `none` (props, scenery, vehicles, foliage: the clean-up file passes through and `rigid: true` is recorded). SkinToken refusing a mesh with an empty prediction is also treated as rigid, not an error. Then `blender/rig_info.py` reads bones, weight coverage and renders the rest pose and bends at 35 and 90 degrees; `blender/hitboxes.py` fits hit boxes. Moves: `blender/procanim.py` makes procedural clips by the category's `motion` (legs, wave, wings); for humanoids, hand-made clips from the CC0 libraries in `clips_libraries` are retargeted onto the skeleton (`blender/retarget.py`, `blender/gait.py`, list in `data/categories/humanoid.yaml` `library_clips`), plus the person's own and added moves (`moves.py`). Spring bones (`springs.py`, `blender/springs.py`) give parts that hang clear of the body a chain of bones with a baked sway. A library hairstyle is bound into the animation file (`blender/hairbind.py`). A rig of the same body already done is reused as a donor, so only the hair is bound again. An adjust that changes only moves or only hit boxes redoes only those, on the parent's skeleton.
Checks: bones, single root, weight coverage, influences per vertex, normalisation, clips loop, hair bound, springs.

### Step 6: Hand off to the game (`stages/export.py`)

In: the kept rig version (or clean-up version for rigid assets). Out: `fbx` with the clips, `glb` (one model), a Godot GLB with collision shapes, per-LOD GLBs, texture packs (`T_<name>_*`), `sockets.json`, `manifest.json`, `README.txt`, optional sprite sheets and atlas, a beauty render, the Unity report and snapshot strip. Runs on the host.
In order: collision shapes in the backend process (`colliders.py`, CoACD in the backend venv; hull for rigged categories, parts or a primitive for props, scenery and vehicles); Blender pack (`blender/pack.py`: LOD chain by decimation at the category's `lod_ratios`, colliders, sockets as empties on the bones, FBX and GLBs, hair as a second material); Unity import (`tools.run_unity`, importer method `VgacImport.Run` in `unity-sandbox`, then clip snapshots; skipped with `unity_error` recorded when no Unity is configured); sprite sheets when asked (`blender/sprites.py`, `sprites.py`); the manifest; delivery to `exports/`; and, when the project has a `unity_project_path`, the import into that project. If the person's Unity project is open, the files wait in `Assets/VGAC/<Name>/_incoming/` and import when they switch to Unity (`handoff_to_editor`); the result is picked up later (`collect_unity_import`). The manifest records every model used and its licence, the seeds, the full version lineage, triangle counts per LOD, textures, bones, clips and sockets (`stages/base.manifest_provenance`).
Checks: LOD count and ratios, collider present and within limits, textures power of two, square and within budget, bones, hair material shipped. The Unity report (`metrics.unity_report`) is recorded alongside.

The **material** category (tileable material) uses `stages/material.py` for steps 1, 3 and 6 on card B (a surface render, then a seamless blend with derived height, normal, roughness and metallic maps and a tile preview, then a zip). It skips steps 2, 4 and 5.

## 6. The job queue (`backend/vgac/jobs.py`)

One asyncio worker per ComfyUI instance in `config.yaml` (here `a` and `b`) and one for the host. A worker picks the oldest queued job for its instance, one at a time, so a graphics card never runs two jobs and the host runs one Blender or Unity job at a time. Every worker waits while free system memory is under `min_free_ram_gb`.

Routing. `Settings.stage_instances` names the card for each step (`material_stage_instances` for the material category; `judge_instance` for the picture judge). `route()` sends a job to the person's default card if it can run that kind of work, else the step's usual card, else the first card that is on, allowed that kind of work (`allowed_kinds`: pictures, models, rigging, reviews) and capable (`instance_capabilities`; rigging is `b` only), else nowhere, in which case the job waits on the usual card with a message that says why. Host steps always run on the host. Turning a card off moves its queued jobs and stops and requeues its running job (never cancels it); turning it on lets parked jobs come back.

Share when busy. With `share_when_busy` on, a card with nothing queued takes the oldest waiting job from a busy card if it may run it. A job is never split and a running job never moves.

Borrowing a second card. A running step can ask `JobQueue.lend` for another card for part of its work (step 2 does, section 5). The card is lent only if it is on, allowed the work, idle, has nothing waiting that it would take, its engine already answers (it is never started for this) and it has enough free memory (`LEND_MIN_ROOM_GB`). While lent, its worker picks nothing. The lease is handed back when the job ends, fails or is stopped; turning the lent card off revokes it and the job finishes on its own card.

The host lane. Jobs run first come, first served, except that an export goes ahead of other waiting jobs, unless a job for the same asset that was queued before it (its clean-up, its rig) is still queued or running on any card.

Starting engines on demand. Before a job runs on a card whose instance has a `start_script`, the worker asks the supervisor to make sure the engine answers; the job stays queued with a message while it starts. A failed start makes the card unavailable (like a card that is off) until Try again or turning the card off and on.

Stop, timeout, restart.
- Stop (`POST /api/assets/{id}/stop`, or cancel one job): a queued job is cancelled; a running job has its task cancelled, its child processes killed and ComfyUI interrupted. Its version becomes stopped. Resume puts the stopped versions at the end of a step's list (those not followed by a newer run) back in the queue with the same settings, including the random seeds already drawn, routed like a new job. Dismiss sets them aside.
- Timeout: a ComfyUI graph may run `job_timeout_seconds` (3600); routine Blender steps `blender_timeout_seconds` (1800). A timeout is an ordinary failure: the job and version become `error`.
- Engine lost: when a ComfyUI stops answering mid-job (connection refused or reset), the job goes back to queued once and the next pick restarts the engine; a second loss fails the job with a plain message. A workflow error ComfyUI reports is a failure, not a lost engine. A poll that merely times out while the engine is busy is waited out.
- Backend restart: jobs left running are put back to queued, and the inputs a job copied into ComfyUI's input folder are removed after each job.

Progress, messages and every job and version change are pushed on the websocket as `job`, `version`, `gpus` and `autopilot` events (`events.py`; each client has its own queue). `pipeline.py` holds the rules both the HTTP endpoints and the autopilot use (which version is kept, making a version, submitting its job).

## 7. Looks, platforms and categories

All three are data files in `backend/vgac/data/`, loaded by `reference.py` and served by `/api/styles`, `/api/platforms` and `/api/categories`. Reload with `reference.reload()` after editing while the server runs; a backend restart does it too.

- **Looks** (`styles.yaml`, called styles in the code and presets in the interface): `painted`, `cel`, `pixel`, `retro64`, `lowpoly_flat`, `realistic_pbr` (Photoreal), `clay`. Each names its painter model, a prompt block, whether painted-in light is removed (`delight_default`) and a `recipe`.
- **Platforms** (`platforms.yaml`): `mobile_low`, `pc_mid`, `high`, with a triangle budget for characters and for props and a texture size.
- **Categories** (`categories/*.yaml`): humanoid, quadruped, winged, multi-leg, serpent, vehicle, rigid prop, articulated prop, environment piece, foliage, tileable material. Each holds: the rig engine, symmetry, the size and its axis, the pose block for the picture, views, topology, the motion and clips, LOD ratios, collision, and a triangle and texture budget per platform.

A recipe is how a look carries through every step after the picture. `reference.recipe(style, category, platform)` resolves it: the look's `recipe` is merged over built-in defaults, then over the look's `by_category` override for that category, and combined with the category's budget for the platform. The result is the concept painter, steps and negative, the add-ons and their strengths, the turn LoRA strength, the mesh targets (triangle target is a share of the platform budget with a floor, never over it; texture size capped), the surface (smooth or flat shading, crisp or smooth textures, palette, pixel snap, delight, physically based fix) and the engine texture settings. It carries a short digest id, and every step records the part it used (`metrics.recipe`) so the version says what it ran with. Steps 3, 4 and 6 read the recipe; their checks measure against the recipe's targets and say so.

## 8. Checks and guidance

A check is a dict `{name, passed, message, id, ok, value, limit}` plus optional `vars`, made by `quality.check()` (or by a step's own function), appended to the version's `metrics.checks`. `quality.py` holds the geometry, LOD, collider, texture, rig and facing checks that are measurable from files.

The words live in one place, `guidance.CHECK_GUIDE`, keyed by check id (some by pattern: `lod<N>_ratio`, `texture_<map>_<pow2|square|budget>`). An entry has a `severity`, an `ok` sentence and a `fail` sentence (format strings or functions of the check, with formats for whole numbers, percentages, metres and lists), and optionally a `fix` and `fix_label`: the stage and settings (or a refine instruction, or an interface action) that put it right. `describe_check` fills the sentence from `value`, `limit` and `vars` when the check is created, so the stored check carries its own `help`, `severity` and `fix`. `frontend/src/help.ts` `CHECK_TEXT` is only the fallback for mock mode.

Severity. `block` means the result is not good enough to build on: triangle budget, LOD count, collider present, bones, clips loop, texture matches the picture. `warn` is everything else: the person is told and can go on. The interface shows both; the autopilot treats them differently.

Autopilot (`autopilot.py`, `POST /api/assets/{id}/autopilot`). Runs the steps in order up to `until_stage`. Step 1 makes two candidates and the automated metrics pick one; every other step makes one. A version is kept when it finished and has no failed check at the `stop_on` level: `block` (the default) pauses on a failed blocking check, `warn` on any failed check, `never` keeps everything. When it pauses it records the reason and the first failing check; the person can apply its Fix, which is their decision and is judged on its own, or take over. A step that already has finished versions but none kept is re-evaluated rather than run again. State is on the asset and is resumed when the backend starts. After a character is built, items set aside from its description are built one at a time and the character's export is rebuilt with them equipped.

Judges. Besides the measurable checks, `mode: local` runs a local vision model (Gemma 3 12B through ComfyUI's `TextGenerate`, `graphs/judge.py`) on a version's picture and stores `metrics.judge`. The same model reads a style from a picture (`stages/style.py`).

Setup check (`preflight.py`, `GET /api/setup`, the "Is everything ready?" card): tools, engines, models and storage (disk space and memory), each in plain words with what is held up.

## 9. Words the person sees

User-facing wording is plain language and lives in the backend where it can be tested: stage names, parameter help (`guidance.PARAM_HELP`: meaning, why, technical), check sentences, the job queue's messages, and the sentences each step records. Technical names appear only as secondary text. `GET /api/stages`, `/api/suggest` and `/api/recipe` serve this layer; `frontend/src/guide.ts` and `help.ts` hold the interface's own copy.

## 10. Optional parts the person can add

| Part | What it is | Where it lives |
|---|---|---|
| Their own add-ons | LoRA files for the painters, listed beside the tested ones in `data/addons.yaml` | Registry `library/my_addons.json`, files in `VGAC/mine/` under ComfyUI's loras folder (`loras_dir`). Ids start with `mine_` and never replace a built-in one. The app lists nothing else in that folder (`user_addons.py`). |
| Their own pictures | A starting picture, a view for step 2, a close-up of the face, several pictures combined, a palette from a picture | `own_images.py`, `compose.py`; every picture passes `uploads.py` (size and format checks, orientation applied, metadata dropped, saved as PNG) |
| Their own 3D model | Replaces step 3 | `model_import.py` |
| Hair from a library | Ready-made hairstyles fitted to the head after clean-up and bound to the skeleton | Built-in styles in `backend/vgac/data/hairlib/` (built offline by `tools/hairlib_build.py` from CC0 packs plus rule-built styles), loaded by `hairstyles.py`; the choice per asset in `Asset.hair` (`hairlib.py`). |
| Their own hairstyles | A hair mesh added to the library | `my_hair.py`; registry and files under `library/my_hair/` |
| Moves | A character gets the category's default set; the person can add moves from the two CC0 clip packs or their own animation files (FBX, GLB, BVH) and remove defaults | `moves.py`; added and removed moves are rig-step settings; their own files and registry under `library/my_moves/` |
| Items | A sword, shield, bow or backpack made as its own asset and attached to a socket on a character | `items.py`, words in `data/items.yaml`, attachment points in `sockets.py` (`Socket_Hand_R`, `Socket_Back`, ...), grip and pivot in `blender/grip.py` and `blender/item_pivot.py`; the character stores `held_item` or `equipment` |
| Variants | A copy of an asset with one change that keeps the look | `POST /api/assets/{id}/variants` |

The downloads these depend on (models, node packs, Blender, animation packs, hair packs) are handled by `setup/Setup-Extras.ps1` from `setup/downloads.json`; see [docs/FIRST-TIME-SETUP.md](docs/FIRST-TIME-SETUP.md).

## 11. What is not in the repository

| Not tracked | Why and where it comes from |
|---|---|
| `library/` | The person's projects, assets and database. Created at first start (`library_path` in `config.yaml`). |
| `exports/` | Finished deliverables, written by step 6. |
| `runtime/` except the two start scripts | Created at run time: logs, ComfyUI user and output folders, `models/face_landmarker.task` for the lip and eye finder. |
| `backend/.venv/`, `frontend/node_modules/`, `frontend/dist/`, `desktop/node_modules/`, `desktop/dist/` | Created by `setup/Check-Setup.ps1 -Install` and the build commands. |
| AI models, ComfyUI node packs, Blender, Unity | Downloaded or installed by each user. `setup/Setup-Extras.ps1` offers each with its size and licence and never answers licence questions for the user; sources and checksums are in `setup/downloads.json`. |
| `data/animations/` | The two CC0 clip packs (`clips_libraries` in `config.yaml`); the person downloads them. |
| Hair packs | The built-in hairstyles are in the repository as processed data; the original packs are downloaded only to rebuild the library. |
| `unity-sandbox/Library/`, `Assets/VGAC/` and other Unity output | Generated by Unity. |
| Research, plans, audits, reviews and test write-ups (`docs/*` except the six guides, `STATUS.md`, `REVIEW.md`, `bakeoff/BAKEOFF.md`, `bakeoff/out/`) | Working documents kept on the owner's computer. |

Licences. The application is GPL-3.0-or-later (`LICENSE`, copyright Runtime Forge LLC). The files copied into a person's Unity project (`Assets/Editor/VgacImport.cs`, `VgacSpringCheck.cs`, `Assets/VgacScripts/`, `Assets/VgacShaders/`) are MIT so they do not put a game under the GPL. Models keep their own terms and are never bundled; `data/models.yaml` records each model's licence, steps record the ones they used in the manifest, and the credits page, `CREDITS.md` and `THIRD-PARTY-NOTICES.md` list them (`credits.py`, `data/credits.yaml`). Hunyuan3D, Anima and Gemma need the user to type a confirmation at setup.

## 12. Conventions for contributors

- **Plain language in anything the person reads.** No stage ids, platform keys or model file names as labels. The backend test `tests/test_ui_plain_names.py` scans the frontend source for raw keys used as text; `tests/test_option_help.py` requires every setting to have help text. New wording goes in `guidance.py`. No emoji anywhere.
- **One source for each fact.** Stages: `reference.STAGE_ORDER`. Concept painters: `data/models.yaml`. Check wording: `guidance.CHECK_GUIDE`. Settings: `reference.STAGE_SCHEMAS`. Routing: `config.yaml` tables. Do not copy them.
- **Steps never raise for things that are not failures.** A skipped pass records its reason in the metrics and is said in plain words; a pass that must not fail the step catches its own failure and records it.
- **Behaviour not yet seen on a card.** Code that has tests but has not run on a graphics card is marked `UNTESTED ON A CARD` in its comment and its commit message (see `CUTOUT_IN_VIEW_GRAPH` and `ZERO_NEGATIVE` in `stages/turnaround.py`, each with a per-version switch).
- **Tests.** From `backend/`: `.venv\Scripts\python -m pytest -q tests`. `tests/conftest.py` builds a temporary library and config and sets `VGAC_CONFIG`, so tests never touch `library/vgac.sqlite`; its engines point at unused ports, so no ComfyUI is needed. A few tests run real Blender (`VGAC_TEST_BLENDER`, default `F:/Tools/blender-5.2.3-windows-x64/blender.exe`) and are skipped when it is missing. The launcher's bridge has `node desktop/appbridge.test.js`. The frontend is checked by `npm run build` (type check and bundle) and `npm run lint` from `frontend/`.
- **Loading a change into the running app.** Frontend: `npm run build` in `frontend/`, then reload the window. Backend: restart the backend process; closing and reopening the app does not restart a backend the launcher did not start (use Restart on the Backend row of the status window, after checking that no jobs are queued or running). Launcher (`desktop/*.js`): `npm run dist` in `desktop/` with every VGAC process closed ([docs/BUILD-THE-EXE.md](docs/BUILD-THE-EXE.md)). Ship frontend and backend together: a new interface against an old backend silently ignores new request fields.
- **Database changes.** Add a nullable column to `_ADDED_COLUMNS` in `db.py`; there is no other migration.
- **Hair library and credits.** The hair library is built offline (`tools/hairlib_build.py`) and read once; a data change needs a backend restart. The credits page is generated from `credits.yaml`, `models.yaml`, `addons.yaml` and the hair catalogue (`tools/make_credits.py` writes `CREDITS.md`).
