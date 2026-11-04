# Critical review of goals and methods

Date: 2026-11-03, written after the phase 0 bake-off. Constraint: every tool and model must be free to use.

## 1. What "game-ready" actually requires

The stated goal is "fully fleshed out and functional assets for use in video games." Written out as a checklist, a character asset that a Unity project can drop in and use needs all of the following. Items marked GAP are not in ARCHITECTURE.md v1 at all; items marked WEAK are mentioned but under-specified.

| Requirement | Status in v1 |
|---|---|
| Clean topology: quad-dominant on characters, edge loops at joints, manifold, no floaters, sensible triangle budget | WEAK. "Quad remesh or decimate" is one line. This is the single biggest quality risk and the bake-off shows why: TRELLIS.2 output is 4.7 to 24.6 million triangles of scan-like soup. Decimation alone gives long thin triangles that deform badly at joints. |
| Symmetry for characters and symmetric props | WEAK. A slider is mentioned. Should be an explicit mirror step: generate from a 3/4 view for better depth, then symmetrize in Blender. |
| UV layout with sane islands, no overlap, packed, consistent texel density | GAP. "Unwrap" is a word in the post chain. Blender's Smart UV Project produces hundreds of islands with visible seams. |
| Texture set: albedo, normal, roughness, metallic, AO, at a platform-appropriate size, with lighting removed from the albedo | WEAK. Concept art has baked lighting and shadows; projecting it straight onto the mesh bakes those shadows in. A delighting pass is needed (Hunyuan3D-Delight is already on disk). |
| LOD chain (LOD0 to LOD2 or 3) | GAP. Every real-time asset needs it; it is cheap to generate by decimation once the base mesh is good, and Unity's LODGroup consumes it directly. |
| Collision mesh (convex hull or simplified) | GAP. Cheap, needed for anything the player touches. |
| Pivot, scale in metres, forward axis, A/T-pose | Covered. |
| Separated parts: weapon in hand, helmet, shield, chest lid, door, wheels | GAP. Image-to-3D fuses everything into one shell. A knight holding a sword becomes one mesh with a sword growing out of the fist. Articulated props cannot be rigged at all without part separation. Free part segmentation models exist (Hunyuan3D-Part / P3-SAM, PartField, SAMPart3D). |
| Deform skeleton with a game-friendly hierarchy and naming, 4 influences per vertex max, no bone scale | WEAK. SkinToken gives `bone_N` names; its Mixamo template option fixes naming for humanoids. Finger bones: SkinToken produced 28 bones for the knight with no fingers. Mitten hands are acceptable in many stylized games but must be a stated choice. |
| Facial setup: eyes and mouth as separate geometry or blendshapes | GAP. Needed only for close-up characters; should be an explicit "not in v1" so nobody expects it. |
| Animation set, not just test clips: idle, walk, run, attack, hit, death for characters; open/close for articulated props | GAP. v1 only promises test clips to judge the rig. |
| Material and shader setup in the engine (URP or HDRP material, texture import settings, prefab with LODGroup, collider, Animator) | WEAK. "Materials assigned" is mentioned. The prefab is what a developer actually uses. |
| Licence record per asset: every model whose output went into it and that model's terms | GAP. Some models here allow commercial output but not commercial model use, some have revenue caps. The manifest should carry this. |
| Reproducibility: seeds, graphs, versions | Covered by the version tree. |

## 2. Things the goals leave out

1. **Project-level style lock.** Each asset is generated alone. A game needs twenty assets that look like they belong together. The cheapest free lever is a per-project style anchor (one prompt block, one model, one LoRA set) applied to every job, and for recurring characters a trained character LoRA so outfits and expressions can be regenerated on-model. The LoRA training setup already exists on this machine from the mascot work.
2. **Tileable PBR materials.** Environment art is mostly surfaces, not objects. A seamless-texture generator (prompt to tiling albedo plus normal and roughness) is a small addition with large payoff and is entirely free with the installed models.
3. **Modular environment kits.** The category exists but nothing says how pieces snap: grid size, pivot rules, matching edge profiles. Worth defining before the first environment asset.
4. **Variants.** Colour and damage variants of an existing asset are a recolour or a texture edit, not a regeneration. Cheap to add once texturing exists.
5. **A real quality gate before export.** Automated checks are planned, but the list should be concrete: manifold, no degenerate triangles, scale within the category range, no vertex with more than 4 influences, no bone with scale, UV overlap under 1 percent, texture sizes a power of two.

## 3. Methods: where there is a better free way

### 3a. Reverse stages 2 and 3 for texture consistency

The current order is concept, AI turnaround, mesh. AI multi-view is good but not pixel-consistent, and the bake-off showed it failing on the chest (two of four views were the wrong angle). A better order for the texture:

1. Concept image.
2. Mesh from the concept with TRELLIS.2 (proven: 50 to 366 seconds, strong geometry).
3. Render the mesh from any number of angles to depth and normal maps.
4. Repaint those renders with Z-Image Fun ControlNet Union 2.1 (installed) guided by the depth map and the concept's style. The views are now guaranteed consistent with the geometry.
5. Project the repainted views onto the mesh as texture, delight, bake.

The AI turnaround keeps a role: it is the fastest way for the user to approve a design before paying for a mesh, and it is the only input Hunyuan3D-2mv accepts. But it should not be the texture source.

There is a free Blender add-on that already implements step 4 and 5 against a local ComfyUI: StableGen (open source). It should be evaluated before writing a projection texturer by hand.

### 3b. Retopology and UVs, specifically

- Quad remesh: Blender's built-in Quadriflow is free and adequate for props. For characters, Instant Meshes (open source) and QuadWild (research, free) give better flow. Plan a small comparison on the knight.
- For mobile low-poly targets, a good triangle decimation with silhouette preservation is acceptable and quads are not required. The platform target should pick the method.
- UVs: xatlas (MIT, Python wrapper available) produces packed atlases with far fewer seams than Smart UV Project. Use it as the default unwrapper.
- Symmetry: after remesh, mirror the better half across the character's sagittal plane when the category says the subject is symmetric.

### 3c. Rigging engine per category, not one engine

- Humanoids: Make-It-Animatable (MIT code, Apache weights) outputs a Mixamo-standard skeleton with fingers, which maps straight onto Unity's Humanoid avatar and every Mixamo-compatible CC0 clip. That beats SkinToken for bipeds.
- Everything else (quadrupeds, winged, multi-leg, creatures): SkinToken, which the bake-off showed producing a genuine 35-bone quadruped skeleton with a tail chain.
- Rigid props: nothing. SkinToken itself refused the chest (empty skinning prediction), which is the right answer.
- Articulated props and vehicles: part segmentation first, then one bone per part placed at the part boundary. No AI rigger handles this today.
- Rigify remains the control-rig layer on top for animators, but export only the deform bones.

### 3d. Animation beyond test clips

- Humanoids: CC0 animation libraries (Quaternius Universal Animation Library and similar) retarget through Unity's Humanoid system with no extra tooling. That gives a full locomotion and combat set for free and offline.
- Quadrupeds and creatures: no free retargeter of quality exists for arbitrary skeletons. Author a cycle set per Rigify template once (idle, walk, trot, run, turn) and ship it. Add procedural foot IK in Unity (Animation Rigging package, free) so cycles adapt to terrain.
- Later and optional: text-to-motion models (MDM, MoMask and successors) are free and humanoid-only; video-to-motion through SAM3DBody (installed) on generated video clips could produce creature motion. Both are research-grade; keep them out of v1.

### 3e. Hunyuan3D-Omni and Hunyuan3D 2.1

- Hunyuan3D 2.1 (7.3 GB file already on disk) adds PBR texture generation to the mesh pass. Same Tencent licence as 2mv.
- Hunyuan3D-Omni accepts a skeleton or pose as a condition, which would guarantee an A-pose mesh regardless of what the concept did. Worth a test when the rig stage is built.

### 3f. The judge

The Claude vision judge costs API tokens, so under the "everything free" rule it must be optional and off by default. The local VLM path should be the default judge, with Claude as an opt-in upgrade. The automated metrics are free and should carry most of the weight.

## 4. Hidden non-free or conditional items

| Item | Condition |
|---|---|
| Hunyuan3D models (2.0, 2mv, 2.1, Omni) | Free, Tencent community licence with a 1M USD annual revenue clause and regional restrictions. Record per asset. |
| Anima | Outputs may be sold; the model itself is non-commercial. Record per asset. |
| Unity | Free (Personal) under Unity's revenue threshold. |
| Claude vision judge | Paid per call. Opt-in only. |
| Everything else in the plan (TRELLIS.2, Z-Image, Qwen-Image-Edit, Klein 4B, Chroma, BiRefNet, SkinToken, Make-It-Animatable, Blender, xatlas, Instant Meshes, StableGen, CC0 animation packs) | Free with permissive licences. |

## 5. Recommended changes to the plan, in priority order

1. Add a **stage 3b, game-ready conversion**, as a first-class stage: remesh (method by platform target), symmetrize, xatlas UVs, bake normal and AO from the dense mesh, delight, LOD chain, collision mesh, quality gate. This is where "fully functional" is won or lost, and it is currently one bullet.
2. Reorder texturing to mesh-first (3a). Keep the AI turnaround as design approval and as Hunyuan3D input.
3. Add **part segmentation** before rigging for articulated props, vehicles and characters holding things.
4. Rigging engine per category (3c) and a **CC0 humanoid animation library** retargeted through Unity Humanoid; authored cycle sets for non-humanoids.
5. Add **project style lock** and optional character LoRA training as a stage 1 feature.
6. Add **tileable material generation** as a sixth category.
7. Make the **Unity prefab** the export unit: mesh with LODGroup, collider, material, avatar, Animator with the shipped clips.
8. Put the **licence record** in the manifest.
9. Default the judge to local and free.

## 6. The experiment that should come next (phase 0.5)

The bake-off answered the generation question. It did not touch the part of the pipeline that determines whether an asset is usable. Before building stage UI, take the knight and the wolf meshes all the way through: remesh (Quadriflow vs Instant Meshes), symmetrize, xatlas, bake, texture by mesh-first repaint (own projection vs StableGen), rig with Make-It-Animatable (knight) and SkinToken (wolf), import into Unity as prefabs with LODs and a CC0 walk cycle on the knight, and judge the result in the Unity viewport. Everything in that list is free. If that works end to end, the app design is sound; if it does not, better to know now.
