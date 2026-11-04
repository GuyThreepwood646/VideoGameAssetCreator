# Build status log

Durable record of what is done, what is decided and what is next, so work can resume after a context reset. Newest entries at the bottom of each section. Keep it short and factual.

## Mandate (from the owner, 2026-11-03)

Incorporate REVIEW.md into the plan, define the end goal in industry terms (ARCHITECTURE.md section 2), run phase 0.5, pick the best-fit engines from the results, then build the complete application without further input. Only constraint: everything free.

## Environment facts

- ComfyUI A (3D): http://127.0.0.1:8188 on RTX 3060. ComfyUI B (images, rigging): http://127.0.0.1:8189 on RTX 3080, launch with runtime/start-comfy-b.ps1.
- Blender 5.2.3: F:/Tools/blender-5.2.3-windows-x64/blender.exe. Unity 6000.4.3f1 at C:/Program Files/Unity/Hub/Editor/6000.4.3f1/Editor/Unity.exe. The owner's open Unity project (CardGames) must not be touched; the sandbox is unity-sandbox/.
- Custom nodes in the ComfyUI install: ComfyUI-SkinToken, vgac-nodes (VGACLoadTrimesh, VGACSaveTrimesh).
- Backend: backend/.venv, `uvicorn vgac.main:app --port 8765`. Frontend: frontend/, `npm run dev`.

## Decisions

- D1 Mesh generator default TRELLIS.2; Hunyuan3D-2mv per-asset alternative. (bake-off)
- D6 Humanoid clips: Quaternius Universal Animation Library (CC0) imported as Humanoid in Unity; AnimatorController built by VgacImport.cs.
- D2 Rig engine per category: SkinToken for every rigged category, with its Mixamo naming template for bipeds. Make-It-Animatable was cloned (F:/Tools/Make-It-Animatable) and rejected for v1: it needs a separate Python 3.11 + torch 2.1 + pytorch3d environment and ships Auto-Rig Pro (a paid add-on) as a submodule. Revisit only if finger bones become a requirement.
- D3 Game-ready conversion runs in ComfyUI native mesh nodes where possible (remesh, decimate, unwrap, bake), Blender for symmetry, LODs, colliders, rig fitting, FBX.
- D4 Texturing is mesh-first; the AI turnaround is for approval and Hunyuan input only.
- D5 Judge defaults to local and free; Claude judge opt-in.

## Phase 0.5 log

- Started 2026-11-03. convert.py (TRELLIS.2 shape + texture + native post chain, 20k faces, 2048 textures) running on the knight.
- Unity sandbox created at unity-sandbox/. Assets/Editor/VgacImport.cs builds a prefab in batch mode (LODGroup from _LODn names, convex collider, material, avatar) and writes unity_report.json. Verified on the rigged knight: 4 LODs, prefab saved.
- bakeoff/finish.py (Blender): scale to height, base pivot, optional symmetrize, LODs by decimation, convex collider, FBX + GLB export. Verified on the rigged knight.

- 2026-11-03 convert.py proven: knight image -> TRELLIS.2 shape+texture -> native remesh/decimate/unwrap/bake chain -> 20k-tri GLB with base/metallic/roughness/normal/AO maps in 160 s (instance A). Texture quality good; atlas fragmented but usable.
- SkinToken on the converted knight with skeleton_template Mixamo: 31 bones with mixamorig names (Hips, Spine..Head, arms, legs; some Extra_ bones), 35 s.
- finish.py weld fixes manifold (2 non-manifold edges, 0 boundary after weld); LODs 20k/10k/5k/2k; collider 54 verts.
- Unity batch import (VgacImport.cs with -vgacHumanoid -vgacClips): prefab with 4 LODs, 31 bones, humanoidValid=true, base/normal/ao assigned, AnimatorController with 46 CC0 clips from Universal Animation Library 1 (Standard). End-to-end definition of done reached for the humanoid path except visual playback proof.
- CC0 animation libraries (Quaternius UAL 1 and 2, Standard tiers) extracted to data/animations/ual1 and ual2 (rig: Rigify DEF- names with fingers; Unity Humanoid maps it).
- Phase 1 build started 2026-11-03: backend agent (backend/, workflows/) and frontend agent (frontend/) working against docs/API.md.

- Phase 0.5 closed 2026-11-03: engines fixed (see bakeoff/BAKEOFF.md phase 0.5 section). Mobile variant (5k tris, 1024 textures) converts in 111 s.

- Wolf path proven: SkinToken (28 bones, 30 s) -> finish.py (LODs 20k/10k/5k/4k, collider 78, manifold after weld) -> Unity Generic prefab with avatar and textures.
- bakeoff/procanim.py: procedural idle and walk cycles for any skeleton by geometric chain classification (legs, spine, tail, head), exported as glTF animations via NLA (Blender 5 needs strip.action_slot). Wolf walk looks like a diagonal gait. bakeoff/render_anim.py renders clip frame strips (clear the active action first, the glTF importer leaves one assigned).
- VgacImport.Snapshot renders prefab clip frames in Unity batch mode (needs graphics, no -nographics); knight walk, sword idle and jump frames confirm Humanoid retargeting works.
- D7 Non-humanoid animation: procedural cycles (procanim.py) shipped with the app; humanoids get the CC0 library plus the procedural cycles as fallback.

- Wolf in Unity with its own clips: finish.py now pushes actions to NLA and exports FBX with bake_anim; VgacImport builds an AnimatorController from the model's own clips for Generic rigs. Report: 4 LODs, 28 bones, clips idle and walk; Snapshot shows the walk playing.
- bakeoff/material.py: tileable material engine (Z-Image Turbo surface, deterministic offset blend for seams, derived height/normal/roughness/metallic maps, 2x2 tile preview). AI seam inpainting was tried and rejected (invents objects).
- D8 Material category: material.py approach; maps are approximations in v1.

- bakeoff/sprites.py + pack_sprites.py: 8-direction sprite sheets per clip with atlas.json, verified on the wolf (walk and idle).

- bakeoff/judge.py: free local vision judge = Gemma 3 12B (text_encoders/gemma_3_12B_it.safetensors) via CLIPLoader type ltxv + native TextGenerate with an image input; returns JSON scores/defects/summary. 365 s cold on the 3080. Qwen2.5-VL fails in TextGenerate (no stop_tokens). Verified on a deliberately broken image: it reported the added black bar and lowered the lighting score, but missed cropped legs. Advisory quality; metrics stay authoritative.
- D10 Git: repository with one commit per milestone, never pushed unless asked (owner, 2026-11-04).
- D11 Autopilot: two concept candidates, automated metrics pick, local judge opt-in (owner).
- D12 Delivery: always an export folder exports/<project>/<asset>/; Unity import into a project path when the VGAC project sets one; never CardGames unless told (owner).
- D13 Launcher: Electron desktop wrapper that starts both ComfyUI instances, the backend and the UI (owner).
- D9 Judge: metrics always; local Gemma judge on demand; Claude judge opt-in later.

- Chest (prop_rigid) path: convert 5k/1024 in 576 s (dense source, GPU B busy at the time), finish symmetric: 0 non-manifold edges, LODs 5.2k/2.6k/1.3k, collider 100 verts; Unity prefab with 3 LODs, collider, textures, no rig. All three categories (humanoid, quadruped, rigid prop) now reach a Unity prefab.

- Frontend finished by its agent (react-router pages, Review/Refine/Adjust panels, viewer with bones and clips, mock mode via `npm run dev:mock`); `npm run build` verified clean.

- 2026-11-04 Phase 1 backend built by its agent: all API.md endpoints, 11 categories, stage engines ported from bakeoff (graphs compared node-for-node in tests), Blender scripts under backend/vgac/blender, Unity import+snapshot wiring, material category, sprites, local judge; 35 tests pass; concept smoke test ran live. See docs/BACKEND.md.
- 2026-11-04 First full end-to-end run started via tools/e2e.py (humanoid, mobile_low).

- 2026-11-04 Integration: frontend fixed for the backend's "host" pseudo-instance (HealthPanel devices guard). Real knight e2e: concept (2 candidates) and turnaround ran through the API and rendered in the UI; then mesh (111 s, 4993 tris, IoU 0.835, 17/17 checks), prep (3 s), rig (33 s, 34 bones, idle+walk), export (16 s, FBX, manifest, Unity report and strip): ALL STAGES OK over the API. Quality gate flags to investigate: prep reports triangle_budget/manifold/watertight failing (5210 tris after symmetrize vs 5000 budget; the weld may not run before the check), rig reports weight_coverage/weights_normalised failing (check thresholds against SkinToken output, which the bakeoff measured at 99.7 percent coverage). Material e2e: concept ok (263 s), mesh returned 409 because the parent lookup required a turnaround; main.py now walks back to the nearest selected earlier stage (patched on disk, backend not restarted yet; the `--reload` flag or a restart picks it up).
- Session ended on usage limit 2026-11-04. Backend (port 8765, no reload) and frontend dev server (port 5174) may still be running.

- 2026-11-04 resume: knight e2e had completed ALL STAGES OK. Instance B then died with "CUDA error: unknown error" on every call (context lost after a long idle); killed and relaunched through runtime/start-comfy-b.ps1 with Start-Process (the earlier bash background launch dies when the session that started it ends). Backend should detect this state: health shows reachable but every job fails at the loader.
- Quality gate fixes: prep.py now welds, fills holes and fixes normals after symmetrize and decimates back to the platform budget (`--budget`, passed by the prep stage); new blender/rig_fix.py (unweighted verts inherit the nearest weighted vertex, limit 4, normalise) runs in the rig stage before inspection. Backend restarted with both.

- Bug found and fixed: the API tests used the real instance URLs from config and the cancel test sent a live /interrupt to instance B, killing whatever job was running (two e2e concepts died this way). tests/conftest.py now uses unroutable ports and stubs ComfyClient.interrupt.
- Rig repair verified on the knight: coverage 1.0, 0 unnormalised, stray importer Icosphere no longer counted or exported (all Blender scripts import with bone_shape_type NONE). Prep with --budget: 4974 tris, 8 non-manifold edges within the new 0.2 percent tolerance.

- Known UI issue: the rig stage bone overlay (and three.js SkeletonHelper) does not sit on the mesh; rig_info now also writes bone_segments_gltf (Y-up) and RigView prefers it, but the SkinToken GLB's armature node carries an offset cancelled by bind matrices. Fix later by transforming segments with the skinned mesh's world matrix in the viewer.
- Verified in the browser against the real backend: projects, asset grid, pipeline page, concept candidates, rig panel (checks, bone list, viewer), export panel (LOD table, Unity report, clip playback).

- Root cause of several oddities: three uvicorn processes from the first backend launch stayed alive; later restarts failed to bind (log: winerror 10048) and died, so the old code kept serving. Every backend change before 2026-11-04 06:40 was never live. Use backend/restart.ps1 (kills by command line, then starts one). Wolf e2e: ALL STAGES OK on the old code (rig 6/6; mesh IoU 0.548 flagged; prep budget 20807 vs 20000 and manifold flagged, both addressed by the now-live prep fixes).
- Guidance layer added: backend/vgac/guidance.py (plain stage names and verbs, help per stage and parameter, check sentences with severity and fix, model suggestion with estimates), exposed through /api/stages, check entries and GET /api/suggest. UX review in docs/UX-REVIEW.md; frontend agent implementing items A to F.

- Material e2e: ALL STAGES OK on the live backend (concept, seamless maps, prep and rig skipped, export zip; 15/15 checks). Chest e2e running.

- Variants implemented in the backend (owner approved): Asset.base_asset_id and variant_change with a SQLite column migration at startup, POST /api/assets/{aid}/variants (copies the base's selected concept, refines with the change), manifest variant_of, test; goes live at the next backend restart (after the chest run). Frontend agent asked to add the Variant UI after the UX pass.

- Chest e2e on the live backend: ALL STAGES OK; prep 6/6 (4994 tris, manifold within tolerance), rig skipped as rigid, export 25/25. All four categories tried (humanoid, quadruped, prop_rigid, material) pass end to end.

- UX pass (UX-REVIEW items A to F plus Variants UI) finished by the frontend agent and verified against the live backend: help registry and Info tooltips, guide panel with first-run overlay, progress strip, 'Happy with this?' loop, suggestions on the asset form, plain-language checks with Fix buttons, simple/advanced Fine-tune, human status line and errors, Variants button and grouping. Backend gained Asset.concept_model (chosen painter stored on create, used as the default for concept runs); live after the next restart.
- Open UX items: H style presets, G autopilot, K finishing moment, L thumbs, M glossary (docs/UX-REVIEW.md section 5).

- Variants verified live: knight variant 'winter cloak with fur trim and a red scarf' kept the identity (same face and armour) in the refined concept; its later stages are running via tools/e2e.py --asset.

- Variant e2e: ALL STAGES OK with the live gates (mesh 17/17, IoU 0.87; prep 6/6; rig 6/6 after weight repair; export 30/31, lod3_ratio miss on a 5k mesh: decimation cannot hit 10 percent exactly at that size; tolerance being widened).

- 2026-11-04: backend restarted on the latest code (Variants, concept_model, guidance layer, widened LOD tolerance). 36 tests pass. Frontend dev server on 5174 serves the new UI against it.

- 2026-11-04: git initialised; milestone commits 4ecb42c (everything so far) and 5b2d48c (autopilot, style presets, delivery). Backend: vgac/autopilot.py (runner, state on Asset.autopilot_json, endpoints, WS events; tested through all stages with mocked engines), data/style_presets.yaml with GET /api/style-presets, Project.unity_project_path, export delivery to exports/<project>/<asset>/ plus optional Unity project import (tools.run_unity project_path override). 38 tests. Live autopilot test running on a new goblin asset; frontend agent building the Make it control and preset chooser; a third agent building the Electron launcher in desktop/.

- Live autopilot (goblin, humanoid, mobile_low, Painted fantasy preset): prompt to delivered asset in 421 s with no input; concept 2 candidates, turnaround 3 min, mesh 17/17 (IoU 0.84), prep 6/6, rig 6/6 (52 bones), export 30/31. The miss: Unity Humanoid mapping failed on the stocky goblin with extra bones, so no CC0 clips attached; VgacImport.cs now falls back to Generic and attaches the procedural clips (confirmed: re-export imported as Generic with clipCount 2).

- Frontend autopilot and presets finished by the agent (Make it control with stop_on, running/paused/done states, finish card with turntable, preset row on projects and creation, asset form autopilot checkbox). Backend autopilot gained `fix` (re-run a stage as an adjust, supersede later stages, continue) and in-flight waiting; 39 tests pass; backend restarted; committed.

## Next

- UX items still open from docs/UX-REVIEW.md: L thumbs up/down on keep, M glossary and example gallery. (G autopilot, H presets, K finishing moment are done.)
- Job step index (step/steps on Job) so the status line is exact; a concept_model picker on the asset form now that the backend stores it.
- Silhouette audit calibration for long-bodied animals (wolf IoU 0.55 flagged although the mesh is good).
- Add Unity snapshot rendering (VgacImport.Snapshot) so the export stage shows animation frames.
- Phase 3 items: style lock UI (frontend has it), tileable material category, sprite sheets, local VLM judge.
