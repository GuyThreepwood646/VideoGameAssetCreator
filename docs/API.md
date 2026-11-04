# VGAC backend API contract (v1)

Base URL http://127.0.0.1:8765. JSON everywhere. All ids are strings (uuid4). Times are ISO 8601 UTC.
Files are served from GET /api/library/{path} where path is relative to the library folder.

## Core objects

Project
```
{ "id", "name", "created_at",
  "style_lock": { "prompt_block": str, "model": "zturbo|zbase|klein|anima|pixel|chroma", "loras": [str], "palette": [str], "negative": str },
  "platform_target": "mobile_low|pc_mid|high" }
```

Asset
```
{ "id", "project_id", "name", "category": str, "style_preset": str, "platform_target": str, "prompt": str,
  "parts": [str], "created_at",
  "selected": { "<stage>": "<version_id>" },          # the chosen version per stage
  "stages": { "<stage>": [Version, ...] } }            # all versions, newest last
```
Stages in order: `concept`, `turnaround`, `mesh`, `prep`, `rig`, `export`.

Version
```
{ "id", "asset_id", "stage", "parent_version_id": str|null, "status": "queued|running|done|error|rejected",
  "params": {...},                                    # what produced it (seed, model, prompt, budget, ...)
  "files": { "<key>": "<library-relative path>" },    # e.g. image, cutout, glb, fbx, base_color, normal, sheet
  "metrics": {...},                                   # e.g. silhouette_iou, triangles, bones, checks[]
  "job_id": str|null, "error": str|null, "created_at", "selected": bool }
```

Job
```
{ "id", "instance": "a|b|host", "kind": str, "asset_id", "version_id", "status": "queued|running|done|error|cancelled",
  "progress": 0..1, "message": str, "created_at", "started_at", "finished_at", "error": str|null }
```

## Endpoints

Health and reference data
- GET /api/health -> instances, host RAM, queue state (exists)
- GET /api/categories -> [ {id, label, rig_engine, symmetric, pose_block, size_m, parts_default, budgets{platform: {triangles, texture}} } ]
- GET /api/styles -> [ {id, label, model, prompt_block} ]
- GET /api/platforms -> [ {id, label, triangles_character, triangles_prop, texture} ]
- GET /api/stages -> [ {id, label, adjust_schema: JSON-schema-like list of params with type/min/max/default} ]

Projects
- GET /api/projects ; POST /api/projects {name, style_lock?, platform_target?}
- GET /api/projects/{id} ; PUT /api/projects/{id} (partial) ; DELETE /api/projects/{id}

Assets
- GET /api/projects/{pid}/assets ; POST /api/projects/{pid}/assets {name, category, style_preset, platform_target?, prompt, parts?}
- GET /api/assets/{id} (full object with stages) ; PUT /api/assets/{id} (name, prompt, category, parts, platform_target) ; DELETE

Running stages (all return {job, version})
- POST /api/assets/{id}/stages/{stage}/run  body { "parent_version_id"?: str, "params"?: {...}, "count"?: int }
  Creates `count` (default 1, max 4 for concept) new Versions with status queued and one Job per version.
  The parent is the selected version of the previous stage unless given. Concept has no parent.
- POST /api/versions/{vid}/refine  body { "instruction": str, "view"?: str, "mask_png"?: base64, "params"?: {} }
  New version in the same stage with parent = vid, produced by the stage's refine engine.
- POST /api/versions/{vid}/adjust  body { "params": {...} }
  New version in the same stage with parent = vid, produced by re-running the deterministic part with new params.
- POST /api/versions/{vid}/select -> marks it selected for its stage (and unselects siblings).
- POST /api/versions/{vid}/reject -> status rejected.
- GET /api/versions/{vid}
- POST /api/versions/{vid}/judge  body { "mode": "metrics|local" } -> metrics updated; returns version.

Jobs
- GET /api/jobs?asset_id=&status= ; GET /api/jobs/{id} ; POST /api/jobs/{id}/cancel
- WS /api/ws/events : server pushes {"type": "job", "job": Job} on every change and {"type": "version", "version": Version} when a version finishes.

Library
- GET /api/library/{path} (exists)

## Stage params (the "Adjust" knobs; also accepted by run)

concept: model, seed, width, height, steps, readiness_block (bool), prompt_override
turnaround: views [front,left,back,right,quarter,top], lora_strength, seed, auto_reroll (bool)
mesh: engine "trellis|hy3d", input_view "concept|front|quarter", target_faces, texture_size, upsample (1024|1536), seed, remesh (bool)
prep: height_m, symmetric (bool), pivot "base|center", smooth_angle
rig: template "auto|mixamo|generic|none", target_faces, group_per_vertex, bottom_center_origin, bend_test (bool)
export: lods [ratios], collider "hull|none", formats [fbx, glb], unity (bool), clips_library "ual1|ual2|none", sprites (bool), sprite_directions, sprite_size

## Additions (2026-11-04)

Guidance layer
- GET /api/stages entries also carry `plain_name`, `plain_verb`, `help {meaning, why, technical}`, `estimate_seconds`; each adjust_schema entry carries `help {meaning, why, technical}` and `simple` (true = shown in the simple layer).
- GET /api/suggest?category=&style=&platform= -> { model, model_label, reason, budgets {triangles, texture}, estimates {stage: seconds}, total_minutes, platform_label, category_label }.
- Version metrics.checks entries also carry `help` (plain sentence), `severity` ("block" | "warn") and, when the app can fix a failure, `fix {stage, params}`: run that stage (or adjust the selected version of it) with those params.

Variants
- Asset gains `base_asset_id` (string | null) and `variant_change` (string | null).
- POST /api/assets/{aid}/variants body { "change": str, "name"?: str, "params"?: {} } -> { asset, job, version } (201).
  Creates a sibling asset in the same project (same category, style, platform; prompt = base prompt + change), copies the base's selected, finished concept version as the variant's first concept version (done, selected), and starts a concept Refine on it with the change as the instruction. 409 when the base has no finished selected concept, 400 when the change is empty.
- The export manifest of a variant carries `variant_of {asset_id, change}`.

Autopilot (decision D11)
- POST /api/assets/{aid}/autopilot body { "until_stage"?: "export", "stop_on"?: "block" | "warn" | "never" } -> state. Runs the stages in order from the first without a kept result; concept makes 2 candidates and the automated metrics pick; a stage that already has finished versions but none kept is judged, not re-run (so Fix and Continue work by re-posting). Pauses on a failed check it cannot accept, errors when a job fails.
- GET /api/assets/{aid}/autopilot -> state; POST /api/assets/{aid}/autopilot/stop -> state.
- State { status: idle|running|paused|done|error, stage, job_id, version_id, reason, stop_on, until_stage, started_at, updated_at, stages_done[] }; Asset carries `autopilot`; WS event {"type": "autopilot", "asset_id", "autopilot"}.

Style presets (item H)
- GET /api/style-presets -> [ { id, label, description, style_preset, example_prompt, style_lock { preset, prompt_block, model, loras, palette, negative } } ]. Apply with PUT /api/projects/{id} { style_lock }.

Delivery (decision D12)
- Project gains `unity_project_path` (string | null; POST/PUT). Every export copies the deliverables to `<export_path or library/../exports>/<project>/<asset>/` (metrics.delivered_to, delivered_files) and, when the project has a Unity project path, imports the FBX there with the same importer script (metrics.unity_project_import).
- Asset gains `concept_model` (POST field; default painter for concept runs).
