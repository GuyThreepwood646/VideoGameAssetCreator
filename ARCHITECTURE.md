# Video Game Asset Creator: architecture v2

Date: 2026-11-03. Supersedes v1. Incorporates REVIEW.md and the phase 0 bake-off (bakeoff/BAKEOFF.md). Build status lives in STATUS.md.

## 1. Mission

A local, free, single-user desktop tool that turns a text prompt into a game-ready asset: a rigged, textured, LOD-ed, collidable mesh delivered as an engine prefab, with the user able to accept, refine or adjust at every stage. Runs entirely on this machine. No paid services, no cloud uploads, no model with a non-commercial output licence.

## 2. Definition of done for one asset

An asset is "game-ready" when every item below holds. These are the acceptance criteria the quality gate (stage 5) checks before export, written in the terms a technical artist or an engine importer uses.

### 2.1 Geometry

1. Single watertight manifold mesh per part (no non-manifold edges, no open boundaries unless the part is intentionally open such as a cloth plane), no floating islands below 0.1 percent of the bounding volume, no degenerate or zero-area triangles, no duplicate vertices within weld tolerance.
2. Triangle budget met for the platform target at LOD0, with a LOD chain: LOD0 100 percent, LOD1 about 50 percent, LOD2 about 25 percent, LOD3 about 10 percent (characters) or 2 to 3 levels for props. Screen-relative transition heights recorded in the prefab.
3. Topology appropriate to deformation: for skinned characters, quad-dominant with edge flow around knees, elbows, shoulders, hips and neck, achieved by remeshing; for rigid props and mobile low-poly targets, a silhouette-preserving triangle decimation is acceptable.
4. Bilateral symmetry enforced where the category declares the subject symmetric.
5. Transform conventions: scale 1.0 with real-world dimensions in metres (category size table), origin at the base centre (feet or contact point) for characters and props, at the grid pivot for modular pieces, forward is +Z and up is +Y on export (glTF and FBX conventions handled by the exporter), no residual object-level rotation or scale.
6. Smooth shading with split normals only at hard edges above the crease angle (default 60 degrees); normals and tangents exported (MikkTSpace).
7. A convex collision mesh (convex hull or a decimated shell under 255 vertices) per part, plus a capsule proxy for characters.

### 2.2 UVs and textures

8. One UV set (UV0) per material: non-overlapping islands, padding at least 4 texels at the target resolution, consistent texel density, packed to the 0..1 range, seams placed away from the face and visible flat areas. An optional UV1 for lightmaps on static environment pieces.
9. A PBR metallic-roughness texture set at the platform resolution, power-of-two square: base colour (sRGB, lighting-free "delit" albedo), normal (tangent space, OpenGL or DirectX convention recorded, baked from the dense mesh), ORM or separate occlusion, roughness and metallic (linear), optional emissive. No baked-in shadows or specular highlights in the base colour.
10. Texture resolution by target: mobile low 512 to 1024, PC mid 2048, high 4096. Materials count: one per asset by default; one per part only when parts need separate shaders (glass, emissive).

### 2.3 Parts and articulation

11. Separate meshes for anything that must move or be swapped independently: held weapons and tools, shields, helmets, chest lids, doors, wheels, turrets. Each part has its own pivot at its hinge or grip and is parented under the asset root.
12. For articulated props and vehicles: a bone or transform per moving part placed on the hinge axis, with limits recorded (open angle, rotation axis).

### 2.4 Rig

13. Deform skeleton: single root bone at the origin, hips as the first child for bipeds and quadrupeds, a strict hierarchy with no loops, no bone scaling, bone roll consistent along chains, rest pose in A-pose (bipeds) or neutral standing (quadrupeds).
14. Naming: Mixamo-compatible names for bipeds so the Unity Humanoid avatar maps automatically (Hips, Spine, Spine1, Spine2, Neck, Head, LeftArm, LeftForeArm, LeftHand and so on); a documented template per non-humanoid category (quadruped, winged, serpent, multi-leg).
15. Skinning: linear blend skinning, at most 4 influences per vertex, weights normalised to 1.0, no vertex unweighted, no weight bleeding across limbs (checked by the limb separation test), smooth falloff at joints (checked by the bend test at 35 and 90 degrees).
16. A control rig is optional and never exported; only deform bones leave the tool.
17. No facial rig or blendshapes in version 1. Eyes and mouth stay part of the head mesh. Stated here so nobody expects otherwise.

### 2.5 Animation

18. Humanoids ship with a locomotion and action set retargeted from CC0 libraries through the Humanoid avatar: idle, walk, run, jump, attack, hit, death at minimum.
19. Non-humanoids ship with the category's authored cycle set (idle, walk, run, turn; open and close for articulated props), looping, root motion stripped, foot contacts marked.
20. Every clip plays on the exported rig without retargeting errors in the engine.

### 2.6 Engine deliverable

21. A Unity prefab: root GameObject with the LODGroup, MeshRenderer or SkinnedMeshRenderer per LOD, the material with all textures assigned and correct colour spaces, colliders, Animator with an avatar (Humanoid or Generic) and the clip set, correct import scale. Also the raw files: FBX with rig and clips, GLB, textures, and the manifest.
22. A manifest JSON: category, style preset, platform target, triangle counts per LOD, texture sizes, bone count, parts list, every model used with version and licence, every seed, and the full version lineage from concept to export.
23. Sprite sheets (optional output): the rigged asset rendered from 8 directions for each clip, with a JSON atlas.

### 2.7 Project-level

24. Every asset in a project is generated under the project's style lock (prompt block, model set, LoRA set, palette) so assets match each other.
25. Licence summary per project: no asset contains output from a model whose terms forbid the project's use.

## 3. Pipeline

```
 1 Concept        2 Preview turnaround   3 Mesh + texture     4 Game-ready         5 Rig + anim        6 Export
 prompt->image    1 image->views for     TRELLIS.2 (default)  remesh/decimate      parts, skeleton,    prefab, FBX,
 style lock       approval and as        or Hunyuan3D-2mv     symmetry, UVs,       weights, clips,     GLB, textures,
 category         Hunyuan3D input        PBR texture voxel    bake, LODs,          bend tests          manifest,
                                                              collider, gate                           sprites
```

Every stage keeps the Review / Refine / Adjust loop from v1 (section 5). Every stage writes a version with lineage.

### Stage 1: Concept

Unchanged from v1 (models by style preset, pipeline-readiness block, Qwen-Image-Edit refinement, categories as data files) plus:
- **Style lock**: a project holds one style anchor (prompt block, model, LoRAs, palette). Every job in the project inherits it. The user edits it once.
- **Character LoRA (optional)**: for a recurring character, train a Z-Image LoRA from approved images so later variants (outfits, expressions, poses) stay on-model. The training setup from the mascot work is reused.
- **Pose control**: an A-pose or standing-pose depth or skeleton template per category fed through Z-Image Fun ControlNet Union 2.1, so concepts come out in rig-ready poses instead of hoping the prompt holds.
- **Parts intent**: the user marks what must be separate (weapon, shield, lid) so later stages know to segment.

### Stage 2: Preview turnaround

Role narrowed by the bake-off and review: it exists for design approval before paying for a mesh, and as the input for Hunyuan3D-2mv. It is not the texture source.
- Qwen-Image-Edit-2511 + multiple-angles LoRA, five views, BiRefNet cutouts, as proven.
- Mandatory consistency checks per view: silhouette height, width-to-height ratio against the expected axis ratio for that view, colour histogram distance. The chest turnaround failed two views in the bake-off; these checks exist to catch that. Failing views re-roll twice, then are flagged.

### Stage 3: Mesh and texture

- Default: TRELLIS.2 shape plus TRELLIS.2 texture stage, giving a dense mesh and a PBR texture voxel (base colour, metallic, roughness) in one ComfyUI job. Proven for shape; texture validated in phase 0.5.
- Alternative per asset: Hunyuan3D-2mv from the four approved views when the subject is long-bodied or a side was guessed wrong. Hunyuan3D 2.1 (on disk) for PBR textures on that path, licence recorded.
- Input view choice: 3/4 for creatures, front for symmetric props; user can override.
- Silhouette audit at 15-degree steps against the turnaround, scored per view.

### Stage 4: Game-ready conversion (new, first-class)

Runs inside ComfyUI's native mesh nodes where they exist, Blender headless for the rest.
1. Remesh to a watertight solid (RemeshMesh, solid mode) to remove double shells and close holes.
2. Part segmentation when the asset has declared parts: a free part-segmentation model (Hunyuan3D-Part / P3-SAM or PartField; chosen in phase 1 by test), each part becomes its own mesh with its own pivot.
3. Topology by target: characters get a quad remesh (Blender Quadriflow by default; Instant Meshes if it proves better in phase 0.5); props and mobile targets get DecimateMesh with feature preservation. Symmetrize when the category says so (Blender).
4. LOD chain by successive decimation; collision hull by convex hull (trimesh) and a capsule for characters.
5. UVs: UnwrapMesh (ComfyUI, GPU charting) or xatlas in Blender; padding and density per target.
6. Bake: base colour, metallic and roughness from the texture voxel onto the low-poly atlas (BakeTextureFromVoxel), normal and AO from the dense mesh (BakeNormalMapFromMesh, BakeAmbientOcclusion). Delighting pass on base colour when the source is an image projection.
7. Optional re-texture path (Refine): render the low-poly's depth and normals from N views, repaint with ControlNet in the project style, project and bake. Mesh-first, so views are consistent by construction.
8. Quality gate: every numbered criterion in section 2.1 and 2.2 checked by script; failures shown as named fixes in Adjust.

### Stage 5: Rig and animation

- Engine per category: Make-It-Animatable for bipeds (Mixamo skeleton with fingers, Humanoid-ready) when it installs cleanly in phase 0.5, otherwise SkinToken with its Mixamo naming template; SkinToken for quadrupeds, winged, multi-leg and serpent (proven on the wolf); hinge bones placed from part boundaries for articulated props and vehicles; nothing for rigid props and environment pieces (SkinToken refusing the chest is treated as "rigid", not an error).
- Rigify metarig fitted to the predicted joints as the optional control rig for animators; export deform bones only.
- Tests: bend test at 35 and 90 degrees per joint chain, limb separation test, weight normalisation check, rendered as a strip the user reviews.
- Animation: CC0 humanoid library retargeted through Unity Humanoid; authored cycle sets per non-humanoid template shipped with the app; procedural foot IK in Unity (Animation Rigging). Text-to-motion and video-to-motion deferred.
- Adjust: joint gizmos, mirror, add or remove chain bones, named weight fixes (smooth around bone, separate limbs, rebuild bone, lock rigid).

### Stage 6: Export

- Unity prefab assembled by an editor script in a sandbox Unity project (batch mode) or in the user's open project through the Unity MCP server when they choose a target project: LODGroup, renderers, material, colliders, Animator and avatar, clips, import settings. Also Godot and Unreal-friendly glTF and FBX.
- Manifest, sprite sheets, licence summary.

## 4. Categories (data files)

Humanoid, Quadruped, Winged, Multi-leg, Serpent/fish, Vehicle/mech, Prop rigid, Prop articulated, Environment piece, Foliage, **Tileable material** (new: prompt to seamless PBR texture set, no mesh). Each file holds: pose prompt block and pose template, view set, size range, symmetry flag, default parts, topology method, rig engine and template, test clips, LOD ratios, triangle budgets per target.

## 5. Interaction loop (unchanged)

Review (keep or regenerate), Refine (describe a change, optional region brush), Adjust (small set of sliders, gizmos, named fixes). Blender hidden. Export-to-Blender button for experts.

## 6. Stack and runtime (unchanged from v1, with additions)

- Backend FastAPI + SQLite + per-instance workers; frontend React + Three.js; ComfyUI instance A (3060, 3D) and B (3080, images and rigging); Blender 5.2.3 headless; Unity sandbox project for batch validation.
- Scheduling: one heavy job per instance, and a host-level rule that a Blender job on a mesh above 5 million triangles does not run while a ComfyUI 3D job is loading (the bake-off measured a 12x slowdown when they overlapped).
- Judge: automated metrics always; local vision model by default; Claude vision judge opt-in (paid).

## 7. Licences

| Model or tool | Terms | Use |
|---|---|---|
| TRELLIS.2, Z-Image (base and Turbo), Qwen-Image-Edit, Klein 4B, Chroma1-HD, pixel LoRAs, BiRefNet, SkinToken, Make-It-Animatable, Blender, xatlas, Instant Meshes, trimesh | Permissive (MIT, Apache 2.0, GPL for Blender as a tool) | Default |
| Hunyuan3D 2.0, 2mv, 2.1, Part | Tencent community licence: free, a 1-million-monthly-users clause, not granted in the EU, UK or South Korea | Opt-in per asset, recorded in manifest |
| Anima | Outputs usable commercially, model non-commercial | Allowed for outputs, recorded |
| CC0 animation and model packs | CC0 | Default |
| Claude vision judge | Paid per call | Opt-in only |
| This app (backend, frontend, launcher, Blender scripts, `comfy-nodes/vgac-nodes`) | GPL-3.0-or-later, Copyright (C) 2026 Runtime Forge LLC (`LICENSE`) | Blender and ComfyUI are GPL tools it calls; the app is GPL to match Blender's rule for scripts that use its Python interface |
| Files copied into a user's Unity project (`unity-sandbox/Assets/VgacScripts`, `VgacShaders`, `Editor/VgacImport.cs`) | MIT, notice kept in each file | Deliberately not GPL: they end up inside users' games |
| Gemma 3 (local vision judge), models generally | Own terms, downloaded by each user, never bundled | Credits page, `CREDITS.md`, `THIRD-PARTY-NOTICES.md` |

## 8. Build plan

| Phase | Delivers | State |
|---|---|---|
| 0 | Installs, instance B, bake-off, app skeleton | Done 2026-11-03 |
| 0.5 | Game-ready conversion proven on knight and wolf: textured low-poly from TRELLIS.2 native chain, topology comparison, LODs, collider, rig per category, a clip on the knight, Unity sandbox import. Engine decisions recorded in STATUS.md | In progress |
| 1 | Backend stage modules and workflow JSON for stages 1 to 6, category data files, quality gate, manifest | |
| 2 | Frontend: project and asset list, stage pipeline, Review/Refine/Adjust panels, viewer with wireframe, bones, clips, UV and texture views, job monitor | |
| 3 | Style lock, tileable materials, sprite sheets, local judge, Unity export through sandbox | |
| 4 | End-to-end runs on all categories, fixes, docs | |

## 9. Risks

1. Topology quality for deforming characters is the hardest free problem; phase 0.5 measures it. If quad remeshing disappoints, mobile-style triangle meshes ship first and quad remesh stays an option.
2. Part segmentation models are young; if none works on this hardware, parts come from a user-drawn split plane in Adjust.
3. Make-It-Animatable may not install beside the ComfyUI venv; SkinToken with the Mixamo template is the fallback.
4. CC0 animation libraries must be fetched; if downloads are blocked, authored cycles are the fallback for bipeds too.
5. Unity batch validation needs a licence-activated editor; the sandbox project is created with the installed 6000.4 editor.
