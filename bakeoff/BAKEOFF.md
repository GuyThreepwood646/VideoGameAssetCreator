# Phase 0 bake-off report

Date: 2026-11-03. Three subjects (knight, wolf, treasure chest) taken from prompt to rigged mesh on this machine, with every tool free. Scripts in this folder; outputs under `out/`.

## Setup

| Stage | Engine | GPU | Script |
|---|---|---|---|
| Concept | Z-Image Turbo, 1024 square, 8 steps | RTX 3080 (instance B, port 8189) | `gen_concepts.py` |
| Turnaround | Qwen-Image-Edit-2511 fp8 + Lightning 4-step + fal multiple-angles LoRA; BiRefNet cutout | RTX 3080 | `turnaround.py` |
| Mesh | TRELLIS.2 int8 (one RGBA image, 1024 upsample) and Hunyuan3D-2mv (four views over white, 20 steps, CFG 7.5) | RTX 3060 (GameDevComfy, port 8188) | `mesh.py` |
| Audit | Blender 5.2.3 headless renders every 15 degrees; silhouette IoU against the turnaround cutouts | CPU | `render_views.py`, `metrics.py` |
| Rig | SkinToken (grpo_1400 checkpoint) through ComfyUI-SkinToken, mesh decimated first by the `vgac-nodes` loader | RTX 3080 | `rig.py`, `rig_info.py`, `overlay_bones.py` |

## Timings

| Subject | Concept (2 seeds) | Turnaround (5 views) | TRELLIS.2 | Hunyuan3D-2mv | SkinToken rig |
|---|---|---|---|---|---|
| Knight | 19 s, 12 s | 47, 37, 37, 68, 75 s | 50 s, 4.76 M tris, 85 MB | 110 s, 0.20 M tris, 3.5 MB | 125 s (TRELLIS mesh, includes first-run model download), 25 s (Hunyuan mesh) |
| Wolf | 15 s, 12 s | 53, 39, 283, 605, 36 s | 90 s, 12.8 M tris, 229 MB | 115 s, 0.38 M tris, 6.7 MB | 150 s |
| Chest | 11 s, 13 s | 35 s each | 366 s, 24.6 M tris, 442 MB | 135 s, 0.50 M tris, 8.9 MB | refused (see rigging) |

The two slow wolf views (283 s and 605 s) ran while Hunyuan3D was loading on the other GPU and Blender was importing a 12 million triangle mesh on the CPU. The same job type took 35 to 50 s otherwise. That is the evidence behind the one-heavy-job-at-a-time rule in ARCHITECTURE.md section 6: system RAM and CPU are shared even when the GPUs are not.

## Mesh quality

Silhouette intersection-over-union against the four turnaround cutouts, best alignment over 15-degree yaw offsets and mirror:

| Subject | Engine | Triangles | Mean IoU | Front | Left | Back | Right |
|---|---|---|---|---|---|---|---|
| Knight | TRELLIS.2 | 4,759,616 | 0.845 | 0.872 | 0.820 | 0.884 | 0.803 |
| Knight | Hunyuan3D-2mv | 199,550 | 0.832 | 0.849 | 0.838 | 0.860 | 0.782 |
| Wolf | TRELLIS.2 | 12,776,816 | 0.695 | 0.700 | 0.685 | 0.783 | 0.612 |
| Wolf | Hunyuan3D-2mv | 383,550 | 0.738 | 0.790 | 0.737 | 0.744 | 0.679 |
| Chest | TRELLIS.2 | 24,594,824 | 0.830 | 0.905 | 0.797 | 0.833 | 0.783 |
| Chest | Hunyuan3D-2mv | 495,270 | 0.821 | 0.900 | 0.795 | 0.830 | 0.760 |

What the eye adds to the numbers (sheets: `out/mesh/<subject>/compare.png`):

- TRELLIS.2 wins on detail every time: hair strands, plate edges, fur clumps, hinge and padlock hardware. Hunyuan3D-2mv is smoother and rounder; on the knight it merged the hair into a helmet shape.
- Hunyuan3D-2mv wins on faithfulness to the sides and back when the views are good: the wolf's body length and stance match the turnaround better (higher IoU on three of four views) because it saw all four sides, while TRELLIS.2 guessed the far side from one three-quarter image.
- When the views are bad, Hunyuan3D-2mv copies the mistake: the chest's turnaround had a wrong-angle right view, and the Hunyuan mesh grew padlock-like hardware on more than one face. TRELLIS.2, fed only the concept, did not.
- Both generators output far too many triangles for a game and both output triangle soup. TRELLIS.2 at 1024 upsample produced 4.8 to 24.6 million; the chest took 6 minutes mostly in the upsample. Decimation or remeshing is mandatory and is the next thing to prove (REVIEW.md section 6).

Answer to the standing question "is Hunyuan3D-2mv better, and by how much": not overall. It is slightly worse on detail and about equal on silhouette for compact subjects, and about 4 points better on silhouette for a long-bodied animal whose far side a single image cannot show. Its real value is as a second opinion when the single-image result guesses wrong, which the app can offer per asset as planned. The licence difference (MIT vs Tencent community licence) stays a factor.

## Turnaround quality

- Knight and wolf: all five views consistent in identity, colour and proportion. The subject-relative left/right mapping of the angle LoRA prompts is confirmed correct.
- Chest: the "left" view barely rotated (padlock still visible) and the "right" view came out as a three-quarter back. The planned per-view consistency check must catch this; a silhouette-width check against the expected axis ratio would have flagged both.
- Cutouts with BiRefNet were clean on all 18 images (alpha 0 at the corner, 254 at the centre).

## Rigging

| Mesh | Result |
|---|---|
| Knight, TRELLIS.2 | 28 bones, single root, hips, knees, ankles, shoulders, elbows, wrists, spine, neck, head. No fingers. 99.99 percent of vertices weighted. Bend test (spine chain rotated 35 degrees) folds the torso as a unit with the legs fixed. |
| Knight, Hunyuan3D | 22 bones, same layout minus a few spine and foot joints. |
| Wolf, TRELLIS.2 | 35 bones: spine, neck, head, four legs with three joints each, tail chain. A genuine quadruped skeleton from a model that was never told it was a wolf. |
| Chest, TRELLIS.2 | Refused: SkinToken's skinning prediction returned empty and the node asserted. A rigid prop should get no skeleton, so this is the correct outcome, but the app must catch the error and treat it as "rigid". |

Bone names are `bone_N`; the node's Mixamo template option should be used for humanoids so Unity's Humanoid avatar can map them. The decimation target was not honoured exactly (150k requested, 250k and 899k produced); the trimesh quadric call needs checking.

Sheet: `out/rig/rig_sheet.png` (skeleton overlay and bend test per rig).

## What changed in the plan because of this

1. Both generators stay. TRELLIS.2 is the default; Hunyuan3D-2mv is the alternative when the single-image mesh guesses a side wrong or the subject is long-bodied.
2. The silhouette audit needs 15-degree steps, not 45; at 45 degrees the wolf scored 0.61 purely from misalignment.
3. Decimation or remeshing is a required step, not an option, and its quality is the next experiment.
4. The per-view consistency check in stage 2 is not optional; props are where the angle LoRA fails.
5. Rigging engine per category: SkinToken for creatures (proven), a Mixamo-skeleton rigger for humanoids (fingers and direct Unity Humanoid mapping), nothing for rigid props.
6. Job scheduling must count CPU and system RAM, not only VRAM.

## Phase 0.5: game-ready conversion (2026-11-03)

Goal: prove the path from a concept image to an engine prefab that meets ARCHITECTURE.md section 2, with free tools only.

| Step | Tool | Knight (pc_mid) | Knight (mobile_low) | Wolf (pc_mid) |
|---|---|---|---|---|
| Shape + texture + remesh + decimate + unwrap + bake (one ComfyUI job, `convert.py`) | TRELLIS.2 shape and texture stages, native RemeshMesh (solid), DecimateMesh, UnwrapMesh, BakeTextureFromVoxel, BakeNormalMapFromMesh, BakeAmbientOcclusion | 160 s, 19,998 tris, 2048 textures | 111 s, 4,990 tris, 1024 textures | 220 s, 19,988 tris, 2048 textures |
| Rig | SkinToken via VGACLoadTrimesh (Mixamo template for the knight, model names for the wolf) | 35 s, 31 bones, mixamorig names | not run | see STATUS.md |
| Finish (`finish.py`, Blender) | weld, scale to height, base pivot, symmetrize (option), LODs by decimation, convex hull collider, FBX + GLB | LODs 20k/10k/5k/2k, collider 54 verts, 2 non-manifold edges, 0 boundary edges | | |
| Engine import (`VgacImport.cs`, Unity 6000.4 batch) | ModelImporter Humanoid, textures to URP Lit or Standard, LODGroup from _LODn names, convex MeshCollider, Avatar, AnimatorController from the CC0 clip library | prefab saved, 4 LODs, humanoidValid=true, base/normal/ao assigned, 46 clips | | |

Findings:
1. The whole mesh-to-textured-low-poly conversion runs inside ComfyUI's native nodes in one job. No Blender retopology comparison was needed for v1: the DecimateMesh output at 20k and 5k keeps the silhouette and bakes cleanly. Quad remeshing stays a later option for deformation quality.
2. UnwrapMesh splits vertices along chart borders; a weld in Blender restores a manifold mesh without touching UVs.
3. SkinToken's Mixamo template gives Unity-mappable names directly (Hips, Spine, Spine1, Spine2, Neck, Head, arms, legs). Unity's Humanoid mapper validated the avatar on the first try. Finger bones are absent (Extra_ bones appear instead); acceptable for v1.
4. The Quaternius Universal Animation Library (CC0) imports as Humanoid; 46 clips in the free Standard tier attach to the knight through retargeting with no manual mapping.
5. Make-It-Animatable was rejected: separate Python 3.11 and old torch environment, and it vendors Auto-Rig Pro (paid) as a submodule.
6. itch.io's download endpoint rejects scripted keys; OpenGameArt hosts the same CC0 zips with direct links.

Decisions recorded in STATUS.md (D1 to D6). Phase 1 (application build) started on these engines.

## Historical note (2026-11-04)

The Blender scripts in this folder (procanim.py, rig_info.py, render_views.py, render_anim.py, sprites.py, finish.py) are the experiment-era ancestors of backend/vgac/blender/*.py. They import GLBs without the bone_shape_type fix and the Icosphere removal and read sys.argv directly. They are kept as the record of the bake-off and are not maintained; the backend copies (now sharing backend/vgac/blender/_common.py) are the live versions.
