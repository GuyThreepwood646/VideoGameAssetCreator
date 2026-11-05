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
- GET /api/categories -> [ {id, label, rig_engine, symmetric, pose_block, size_m, parts_default, budgets{platform: {triangles, texture}}, views, lod_ratios, topology, input_view_default, skips: [stage]} ] (`skips`: stages the category does not have; runs build on the stage before them)
- GET /api/models -> [ {id, label, description, name, licence, version, default_steps, negative} ] the concept models, from backend/vgac/data/models.yaml (entries with `concept: true`); concept_model, style_lock.model and the concept `model` param must be one of these ids
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

Housekeeping (2026-11-04, UX review items L and M)
- POST /api/versions/{vid}/feedback body { "thumbs": "up" | "down" | null, "note"?: str } -> Version. Stored on metrics.feedback { thumbs, note, at } (null clears it); never leaves the machine. Publishes a "version" event.
- GET /api/gallery -> [ { asset_id, asset_name, project_id, project_name, category, style_preset, platform_target, created_at, finished_at, export_version_id, thumbnail, preview, delivered_to, fbx, glb } ] for every asset with a finished export (the kept one when there is one), newest first. thumbnail is the kept concept image, preview the export GLB (or the prep/mesh GLB), all library-relative.
- Mesh check silhouette_iou uses the category's `silhouette_min` (quadruped, multileg, winged 0.5; serpent, foliage 0.45; vehicle 0.55; everything else 0.6).

Behaviour notes (2026-11-04 audit)
- Version.job_id is set for every run, refine and adjust (it was always null before).
- A cancelled or rejected queued or running version ends as status "rejected" (never "error"), and a reject taken while the job runs stands when the job finishes.
- A server stop while a job runs puts the job back to "queued" ("re-queued after restart"); autopilots in state "running" resume at startup.
- Stage run parents: the kept version of the previous stage, walking back only over stages the category lists in `skips` (material skips turnaround). Missing a stage the category needs gives 409 naming that stage.
- Stage params are validated against the Adjust schema (400 with a plain message); mask_png must be base64 PNG data; unity_project_path must be an existing Unity project folder.
- The API refuses foreign Origin headers on state-changing requests, unknown Host headers, and foreign-origin websocket connections. The SQLite database is never served from /api/library.

Graphics cards on and off (2026-11-04)
- Each ComfyUI instance is one graphics card (a = RTX 3060, b = RTX 3080). The person can turn a card off, limit what it may do, and have it free its memory after each step. Saved in `<library_path>/prefs.json` as { "disabled_instances": [instance], "allowed_kinds": {instance: [kind]}, "free_after_job": {instance: bool} }, read at backend start (before any job is picked) and written on every change. Default: every card on, every kind allowed, free_after_job false.
- Kinds: `pictures` (concept, turnaround and every material step), `model` (mesh), `rigging` (rig), `review` (the local judge). What a card can run at all comes from config `instance_capabilities` (default a: concept, turnaround, mesh, material, judge; b: the same plus rig; rig is b only because SkinToken finds Blender only through SKINTOKEN_BLENDER_BIN, set by runtime/start-comfy-b.ps1). A card's effective capabilities are those intersected with its allowed kinds. prep and export run on the host and never need a card.
- Routing (jobs.route): the instance from stage_instances (or judge_instance) unless it is off or not allowed that kind; else the first card that is on, allowed and capable; else the job is still created on the preferred card, queued, with message "Waiting for <gpu> to be turned back on" (card off) or "Waiting for a graphics card that is allowed to do <kind>" (kind not allowed), and its version stays queued. Nothing is lost.
- GET /api/gpus -> [ { instance, gpu, url, enabled, reachable, vram_total_mb?, vram_free_mb? (when reachable, from the cached /system_stats), current_job_id, current_job { id, kind, stage, op, material, asset_id, asset_name, progress, message } | null, queued, can_run [effective capabilities], note (plain sentence, such as "Rigging only runs on this card."), allowed_kinds [kind], possible_kinds [kind the config allows], effective_kinds [kind], kind_notes { kind: sentence } for kinds the card cannot do (such as "Rigging only runs on the RTX 3080's instance."), free_after_job } ].
- PUT /api/gpus/{instance} body { "enabled"?: bool, "allowed_kinds"?: [kind], "free_after_job"?: bool } -> the same list. 404 for an unknown card, 400 for an unknown kind. Turning a card off: its worker picks nothing more; queued jobs on it move to the card route() gives (message "Moved to <gpu> while <gpu> is off") or stay parked with the waiting message; the running job is stopped and put back to queued the same way (never cancelled, its version back to queued); then ComfyUI POST /free {unload_models, free_memory} releases the card's memory (errors ignored). Turning it on: parked jobs it can run start, parked jobs on other cards that it can take move to it; jobs that moved away stay. Unticking a kind moves the queued jobs of that kind the same way (a running one finishes). free_after_job: the worker calls POST /free after every job it finishes on that card.
- GET /api/health: each instance gains `enabled`; each queue entry gains `enabled` (separate from `paused`, POST /api/queue/{instance}/pause).
- WS /api/ws/events: {"type": "gpus", "gpus": [the list above]} on every change of these settings and after a running job has been moved.
- Desktop launcher: reads GET /api/gpus every tick (prefs.json before the backend answers). While b is off it does not start ComfyUI B, stops the ComfyUI B it started (once), and shows "Off (turned off in the app)" with "Start anyway", which sends PUT /api/gpus/b {"enabled": true}. ComfyUI A (Comfy Desktop) is never stopped; it is only marked "Off in the app".

Graphics engines started on demand (2026-11-04)
- Config, per instance (backend/config.yaml): `start_script` (PowerShell script, relative to the project root or absolute), `start_timeout_seconds` (default 180), `process_match` (a piece of the command line that identifies an already running copy), `external_owner` (who usually runs it, for the note). a: runtime/start-comfy-a.ps1 (port 8188, --cuda-device 1, own user and output folders under runtime/), match `--cuda-device 1`, external_owner ComfyUI Desktop. b: runtime/start-comfy-b.ps1, match `ComfyUI\main.py --port 8189` (the launcher's processMatch). An instance without start_script is never started by the backend.
- Before a job runs on a card with a start script (backend/vgac/instances.py, Supervisor.ensure_running): if the instance answers /system_stats it runs. Otherwise, if a process matching process_match exists, or the port is already listening, or the copy the backend started is still loading, the backend waits for it; else it runs the script detached and hidden (powershell -NoProfile -ExecutionPolicy Bypass -File, output to runtime/logs/comfy-<instance>.out.log and .err.log), records that it owns it (pid, also in `<library_path>/engines.json`, adopted after a backend restart only when pid and start time still match) and waits up to start_timeout_seconds. While it starts the job stays queued with message "Starting the graphics engine on <gpu> (about a minute)" and its version stays queued.
- A failed start never fails the job: the card counts as unavailable (as if off) for routing until POST /api/gpus/{instance}/start or the card is turned off and on. Its queued jobs move to another card that can run them (message "Moved to <gpu>: could not start the graphics engine on <gpu>") or wait with "Could not start the graphics engine on <gpu>: <reason>. It will try again when you press Try again or turn the card off and on." A card that is off is never started; host jobs never start anything.
- The backend stops only a process tree it started itself (taskkill /PID <pid> /T /F): when the card is turned off (after the POST /free), after `stop_when_idle_minutes` without a running or queued job (checked every 15 s), or from the Stop button. An instance started by ComfyUI Desktop or the launcher is never stopped.
- prefs.json gains `stop_when_idle_minutes` {instance: minutes}; 0 = never (default).
- GET /api/gpus rows gain `state` (running | starting | stopped | failed | off), `owned` (the backend started it), `can_start` (a start script is configured), `stop_when_idle_minutes`, `last_error` (the reason, when failed). For an engine that is running and not owned, `note` ends with "Started by <external_owner>; the app leaves it running." (or "Started outside the app; ...").
- PUT /api/gpus/{instance} accepts `stop_when_idle_minutes` (0 to 1440; 422 outside).
- POST /api/gpus/{instance}/start -> the list. Forgets a failed start and starts the engine in the background (the row says "starting"). 409 with a plain message when the card is off or has no start script; 404 for an unknown card.
- POST /api/gpus/{instance}/stop -> the list. 409 with a plain message for an engine the backend did not start ("... was not started by the app, so the app will not stop it.") or one busy with a step.
- WS: a "gpus" event on every engine state change (starting, running, failed, stopped).
- Desktop launcher: reads `state` and `owned` from GET /api/gpus. It skips its own one-time auto-start of ComfyUI B while the backend reports b "starting", shows an engine the backend started as "Running (started by the app when a step needed it)", and still treats any ComfyUI B it did not start itself as foreign (never stopped on quit or when card b is off).
