# VGAC backend

Python 3.13 package `vgac` under `backend/`, FastAPI on port 8765, SQLite in `library/vgac.sqlite`. It implements `docs/API.md` and the pipeline of `ARCHITECTURE.md` section 3 by porting the engines proven in `bakeoff/`. Run from `backend/`:

    .venv\Scripts\python -m uvicorn vgac.main:app --port 8765 --reload
    .venv\Scripts\python -m pytest -q tests

## Module map

| Module | Role |
|---|---|
| `config.py` | `backend/config.yaml` -> `Settings` (instances with optional `output_dir`, library path, Blender and Unity paths, ComfyUI input folder, clip libraries, RAM floor, job timeout). `VGAC_CONFIG` overrides the file; tests use it. |
| `db.py` | SQLAlchemy models `Project`, `Asset`, `Version`, `Job` with uuid4 string ids and JSON text columns (`style_lock`, `parts`, `params`, `files`, `metrics`, `payload`), plus the `*_to_dict` serialisers that produce the API shapes. Schema is `create_all` only: delete `library/vgac.sqlite` after a model change. |
| `library.py` | Path layout `library/projects/<pid>/assets/<aid>/<stage>/<vid>/` and helpers to register files on a version as library-relative paths (served by `GET /api/library/{path}`). |
| `reference.py` | Loads `vgac/data/categories/*.yaml`, `styles.yaml`, `platforms.yaml`, `models.yaml`; the stage Adjust schemas; `budget(category, platform)`. |
| `comfy.py` | Async ComfyUI client: `/prompt` (rejects `node_errors`), `/history` polling, `/view` download, `/upload/image`, `/interrupt`, `stage_input` (copies into the shared input folder so `LoadImage` and `VGACLoadTrimesh` find the file). `ComfyError` carries the history status so SkinToken's `AssertionError` is recognisable. |
| `graphs/` | Pure graph builders: `concept` (six models), `qwen_edit` (angles and instruction edit), `birefnet`, `trellis`, `hy3d`, `skintoken`, `material` (graph plus numpy seamless and map derivation), `judge`. |
| `jobs.py` | Job queue: one `InstanceWorker` per instance `a`, `b`, `host`; routing table `STAGE_INSTANCE` and `MATERIAL_INSTANCE`; job kinds `<stage>.<run|refine|adjust|judge>` or `material.<stage>.<op>`; RAM guard; progress and message updates; cancel (task cancel, ComfyUI interrupt, subprocess kill); orphan re-queue at start; websocket publishing. |
| `events.py` | `EventBus` behind `WS /api/ws/events`: `{"type": "job", "job"}` on every change, `{"type": "version", "version"}` when a version finishes. |
| `stages/base.py` | `StageContext`: asset, project, category, style, budget, version folder, progress, `comfy()`, `cutout()`, `blender()`, `unity()`, lineage lookups (`lineage_file`, `stage_version`), `use_model()` for the licence list. |
| `stages/__init__.py` | Registry and dispatcher `execute(ctx, kind, payload)`; `judge_metrics()` for the synchronous judge. |
| `stages/concept.py` .. `export.py`, `material.py`, `judge.py` | One module per stage, each with `run(sc, params)`, `refine(sc, instruction, view, mask_png, params)`, `adjust(sc, params)` and `judge(sc)`. |
| `quality.py` | Quality gate checks (`{name, passed, message, id, ok, value, limit}`): triangle budget, manifold and watertight, degenerate faces, height, LOD count and ratios, collider vertices, texture power of two / square / budget, bones, single root, weight coverage, influences, normalisation. |
| `imaging.py` | Silhouette, IoU, best yaw and mirror alignment, alpha bbox, colour histograms, white composite, mask apply, contact sheets, sprite atlas. |
| `tools.py` | Blender and Unity subprocess runners (`blender -b --python <script> -- args`, `Unity.exe -batchmode ... -executeMethod`). |
| `blender/` | Headless scripts: `prep.py`, `pack.py`, `render_views.py`, `rig_info.py`, `procanim.py`, `render_anim.py`, `sprites.py`. |
| `sprites.py` | Packs the frames from `blender/sprites.py` into one sheet per clip plus `atlas.json`. |
| `workflows.py` | Writes `workflows/*.json` from the builders. |
| `main.py` | Every endpoint in `docs/API.md`, CORS for localhost, the websocket, the library file server, the built frontend at `/`. |

## Data flow

1. `POST /api/assets/{id}/stages/{stage}/run` creates a `Version` (status `queued`, params = stage defaults merged with the body) whose parent is the selected version of the previous stage, and a `Job` on the instance the routing table names. The first version of a stage is selected automatically.
2. The instance worker picks the oldest queued job, checks free RAM, sets job and version to `running`, and calls `stages.execute`, which builds a `StageContext` and calls the module's `run`, `refine` or `adjust`.
3. The module writes files into the version folder, registers them with `sc.register(key, path)`, records `sc.set_metrics(...)` and `sc.set_params(...)` as it goes (progress is visible through the job and the websocket), and raises on failure.
4. The worker marks job and version `done` or `error` (`cancelled` jobs give the version status `error` with error `cancelled`) and publishes both.

Lineage: a stage finds its inputs with `sc.lineage_file(key, stages)`, which walks up the parent chain and falls back to the selected version of each stage, so a mesh adjust can be re-run from the same concept, and export finds the textures on the mesh version without copying them into prep and rig (those stages store references to the same files under the texture keys).

## Stage summary

| Stage | Instance | Engine | Files | Metrics |
|---|---|---|---|---|
| concept | b | Z-Image Turbo default; zbase, klein, anima, pixel, chroma by style or style lock; BiRefNet cutout. Refine: Qwen-Image-Edit-2511 instruction edit, optional mask | `image`, `cutout`, `cutout_white` | seed, model, cutout bbox stats |
| turnaround | b | Qwen-Image-Edit-2511 + angles LoRA (turn_method standard), the turnaround sheet LoRA (sheet) or the camera-angle LoRA (shadow_free), cutout per view | `<view>` (raw), `<view>_cutout`, `<view>_white`, `sheet` (sheet only) | `views{view: {height_ratio, hist_dist, mirror_iou, rerolls, passed, checks, method}}`, `views_passed`, `views_total`, `turn_method`, `sheet{seed, figures, split}` |
| mesh | a (+host) | TRELLIS.2 full chain to the budget; hy3d alternative; silhouette audit | `glb`, `base_color`, `metallic`, `roughness`, `normal`, `ao`, `input`, `preview`, `sheet` | triangles, `silhouette_iou`, `views{view: {silhouette_iou}}`, checks |
| prep | host | Blender prep | `glb`, `report`, texture references | triangles, manifold counts, dims, checks |
| rig | b (+host) | SkinToken Mixamo or model names; skip for `rig_engine: none` and for SkinToken's empty prediction (`rigid: true`); rig_info; procanim; strips | `glb`, `rig_info`, `rest`, `bend_35`, `bend_90`, `bones`, `bend_strip`, `anim_glb`, `anim_report`, `anim_strip`, texture references | bones, bone_names, weight_coverage, clips, rigid, checks |
| export | host | Blender pack, Unity import and snapshot, sprites, manifest | `fbx`, `glb`, textures, `pack_report`, `unity_report`, `unity_strip`, `sprites_dir`, `sprite_<clip>`, `sprite_sheet`, `sprite_atlas`, `manifest` | `lods[{level, triangles, screen_height}]`, collider_verts, bones, clips, `unity_report`, checks |

The `material` category: concept = surface render (`image`), mesh = seamless blend plus `base_color`, `normal`, `roughness`, `height`, `metallic`, `tile_preview` (metric `seam_error`), prep and rig = `skipped: true`, export = `zip` plus `manifest`.

Judge: `POST /api/versions/{id}/judge` with `mode: metrics` recomputes the measurable checks synchronously; `mode: local` queues a `<stage>.judge` job on instance B that runs Gemma 3 12B through ComfyUI's `TextGenerate` and stores `metrics.judge = {scores, defects, summary, model}` (or `raw` when the reply has no JSON).

## Adding an engine

1. Write the graph builder in `vgac/graphs/<engine>.py` as a pure function returning the API-format dict, with `filename_prefix` taken from an argument. Port it from a proven script rather than from memory, and add a test in `tests/test_graphs.py` that compares it with that script.
2. Add the model to `vgac/data/models.yaml` with its licence; call `sc.use_model("<id>")` in the stage so the manifest lists it.
3. In the stage module, branch on a param (as `mesh.py` does on `engine`) and implement the run: `name = await sc.stage_input(path, "<unique name>")`, `entry = await sc.comfy(graph, instance=..., poll=..., message=...)`, download with `sc.download_image` or `sc.download_mesh`, register files and metrics.
4. If the engine runs on a different instance from the stage, change `STAGE_INSTANCE` in `jobs.py` (one instance per stage; a stage may still call `sc.comfy(..., instance="b")` for a short helper such as the cutout, and Blender always runs on the host from inside the job).
5. For host tools, add the script to `vgac/blender/` and call `await sc.blender("<script>.py", [args])`; the stdout lands in `<version dir>/<script>.log`.
6. Expose new knobs in `reference.STAGE_SCHEMAS` so `GET /api/stages` and the Adjust panel show them, and regenerate `workflows/` with `python -m vgac.workflows`.

## Configuration notes

- Instance B is started by `runtime/start-comfy-b.ps1` with `--output-directory runtime/comfy-b-output`; `config.yaml` names that folder as `instances.b.output_dir` because SkinToken saves its GLB there and only reports the path as text.
- `comfy_input_dir` must be the `--input-directory` of both instances; files are copied there under `vgac_<version>_...` names.
- Unity runs against `unity-sandbox/` with `VgacImport.Run` (`-nographics`) and `VgacImport.Snapshot` (needs graphics). A Unity licence must be active for batch mode.
- The RAM floor `min_free_ram_gb` holds every worker, including the host, so a Blender job does not start while system memory is scarce.
