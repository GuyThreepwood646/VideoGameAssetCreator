# Photoreal: what is missing and how to close it

Date: 2026-11-04. Scope: the Photoreal look (`backend/vgac/data/styles.yaml`, id `realistic_pbr`) across all eleven categories. Builds on `docs/GREAT-ASSETS.md` (gap table G01 to G51), `docs/GREAT-ASSETS-PLAN.md` (UX rules and phases 0 to 10) and `docs/CHARACTER-PARTS-RESEARCH.md` (licence verdicts reused here, not repeated).

The owner's point stands: one generated shell with one baked texture set falls short of photoreal in every category, not only for people. A photograph is made of many materials that behave differently in light (glass lets light through, a lamp gives light off, skin glows softly where it is thin, car paint has a lacquer on top, fur stands off the skin, an eye is a wet ball in a socket). The app today makes every one of them as the same opaque painted surface.

How to read it:

1. Section 1 is the gap per category, in one table, and the seven shared root causes behind it.
2. Section 2 has one verdict table per remedy family: what the user gets, how it would work here, the free tool with its licence, size, fit on a 12 GB card, engine support, effort and risk.
3. Section 3 is the phased plan in the GREAT-ASSETS-PLAN format: plain tool name, default for Photoreal, check sentence and Fix label, files, time on the card, how a non-technical person meets it, what to verify by eye.
4. Section 4 is the "Good to know" note per category for the Photoreal look.
5. Section 5 lists everything ruled out on licence grounds. Section 6 is sources.

Rules followed: research only, nothing downloaded, nothing run on a graphics card, no code changed. Licences were read from the project's own page, model card or LICENSE file where possible; "(secondary)" marks a licence or figure confirmed only from a mirror, aggregator or article. "(estimate)" marks a time or size not measured here. Engine behaviour marked "(engine knowledge, not re-checked)" comes from the engines' standard material models and should be confirmed in the sandbox before the phase that relies on it.

Effort uses the plan's scale: S up to one agent-day, M two to four days, L one to two weeks.

---

## 0. What the exported files carry today (read from the code)

| Place | What it holds | Source |
|---|---|---|
| Mesh stage bake | `BakeTextureFromVoxel` writes base colour, metallic and roughness from the TRELLIS.2 voxel texture; `BakeNormalMapFromMesh` and `BakeAmbientOcclusion` write normal and AO from the dense mesh. TRELLIS.2's voxel texture has six channels (base colour, metallic, roughness, **alpha**), but the native ComfyUI node ignores alpha and keeps every mesh opaque: "alpha (idx 5) ignored - meshes kept opaque" | `backend/vgac/graphs/trellis.py` lines 55 to 72; ComfyUI `comfy_extras/nodes_mesh_postprocess.py` lines 1343 to 1347 and 1383 to 1410; TRELLIS.2 models opacity [T1] |
| Mesh stage shape | `RemeshMesh` in `solid` mode closes the shell: thin sheets (glass panes, leaves, membranes, cloth edges) become thick or filled | `graphs/trellis.py` line 55 |
| Prep | De-light for Photoreal, albedo clamp to 30 to 240 sRGB, metal sharpened toward 0 or 1, roughness floor 0.04 | `backend/vgac/stages/prep.py` `texture_pass`; `backend/vgac/imaging.py` `delight`, `clamp_albedo`, `sharpen_metallic` |
| Export | `T_<Name>_MetallicSmoothness.png` (Unity) and `T_<Name>_ORM.png` (glTF, Unreal, Godot), plus the separate maps | `backend/vgac/stages/export.py` `PACK_KEYS`, lines 40 to 47 and 123 to 157 |
| GLB | One Principled BSDF: base colour, normal map, ORM as metallicRoughness and occlusion. No emission, no alpha, no KHR material extensions | `backend/vgac/blender/pack.py` lines 471 to 511 |
| Unity material | URP Lit (or Standard, or HDRP Lit) with `_BaseMap`, `_BumpMap`, `_OcclusionMap`, `_MetallicGlossMap`. No emission, no alpha clip or transparency, no detail map, no height map, no clear coat | `unity-sandbox/Assets/Editor/VgacImport.cs` lines 871 to 930 |
| Unity snapshot | Default scene objects, solid grey background, the default directional light | `VgacImport.cs` `Snapshot`, lines 1592 to 1616 |
| App viewer | Dark solid background, one hemisphere light, two directional lights, sRGB output, **no environment map and no tone mapping** | `frontend/src/Viewer.tsx` lines 511 to 530 |

Consequence for the viewer: three.js `MeshStandardMaterial` reflects its environment. With no environment map, metal (which has almost no diffuse colour) reflects nothing and shows up dark and flat, and every surface lacks the soft reflections that make PBR read as real. The app's own preview therefore cannot show whether an asset looks photoreal, which hides every other improvement in this document.

---

## 1. The gap

### 1.1 Per category

Feature names used in the last column: TR transparency or transmission, EM emission, SSS subsurface or translucency, CC clear coat, AN anisotropy, AT alpha-tested cards (hair, fur, feathers, leaves), EYE separate eye geometry, DN detail normals and texel density, HT height or displacement, DC decals and lettering, PL presentation lighting.

| Category | What photoreal needs | What the output lacks today | Features |
|---|---|---|---|
| humanoid | Wet eyes with a clear cornea that catch a highlight; teeth and inner mouth; skin that is soft where thin (ears, nose, fingers); hair as strands or cards with alpha; pores and fabric weave up close; separate fingers | Eyes, teeth, hair and brows are painted on one closed shell; skin is opaque paint; hair is a solid helmet; detail stops at the voxel texture (about 1.2 to 1.8 mm per cell on a 1.8 m figure at the 1536 or 1024 grid); mitten hands | EYE, SSS, AT, DN, PL |
| quadruped | Fur that stands off the skin and breaks the silhouette; wet nose and eyes; translucent ears | Fur painted on a smooth surface; silhouette is a clean shell; eyes painted | AT (shells or cards), EYE, SSS, DN |
| winged | Feathers as overlapping cards with alpha; thin membranes that light shines through | Wings are a solid slab (solid remesh), feathers painted | AT, SSS, TR, EYE |
| multileg | Glossy chitin (a lacquer-like layer), fine hairs, compound eyes, thin translucent wings for insects | One opaque layer; hairs and thin legs thickened; wings solid | CC, AT, TR, EYE, DN |
| serpent | Scales with real relief and a sheen; wet eyes; inside of the mouth | Scales are the strongest category already; relief is only as fine as the voxel texture; eyes painted | DN, HT, EYE, CC (wet look) |
| prop_rigid | Glass, gems, flames, lit lanterns, screens; brushed or polished metal; worn edges; readable lettering and labels | Glass and glow painted as solid colour; brushed metal is plain metal; text is either missing (the concept negative strips it) or gibberish | TR, EM, AN, DN, DC |
| prop_articulated | As rigid props; inside surfaces when opened | As rigid props; inside is solid (G22) | TR, EM, DN |
| vehicle | Glass windows, headlights and tail lights that glow, lacquered paint with a clear coat, chrome, rubber, badges and number plates | One opaque layer; windows and lights painted; paint has no lacquer; badges smeared | TR, EM, CC, AN, DC, DN |
| environment | Fine surface relief up close (bricks, grout, cracks), tiling detail at the right texel density, parallax on flat walls, glass windows, lamps | Unique atlas only (density falls as the piece grows: a 3 m wall on 2048 px gets about 600 px per metre); no height map; windows and lamps painted | DN, HT, TR, EM |
| foliage | Leaves as two-sided alpha cards that light shines through; wind; separate trunk material | Leaf mass is a solid blob; one opaque material; on a 15 m tree the voxel cell is about 1 cm, so a 5 cm leaf has five cells | AT, SSS (leaf translucency), TR |
| material | Height that matches the colours (for parallax and blending), measured roughness and metal, scale in metres | Height and roughness worked out from colour; metal always black in v1 (G43) | HT, DN, accurate maps |
| All | Judged under realistic light with tone mapping, in the app and in the engine | No environment light in the viewer; Unity snapshot uses default light on grey | PL |

### 1.2 Shared root causes

1. **One closed shell.** The solid remesh closes every thin or open part. There is no room for a cornea over an iris, a glass pane, a leaf card or a membrane. Separate eyes, glass and cards all need geometry the shell does not have.
2. **One opaque material, and the alpha thrown away.** Base colour, normal and ORM only; nothing for emission, alpha, transmission, clear coat, translucency or detail. TRELLIS.2 predicts opacity [T1], but the bake node drops it.
3. **Detail is capped by the voxel texture, not by the texture size.** The bake samples a voxel field of 1024 or 1536 cells across the longest side (`upsample` in the recipe). A 4096 px atlas samples that field more finely but cannot add detail the field does not hold. Large assets (cars, trees, walls) suffer most, because the grid stretches over the whole object.
4. **No micro detail.** The normal map is baked from the dense TRELLIS mesh, so it holds nothing finer than that mesh: no pores, weave, grain or brushing.
5. **Material values are guesses.** TRELLIS.2's metallic and roughness are coarse; prep pushes them into range but cannot know that a region is gold, rubber or glass.
6. **No material identity.** Nothing in the pipeline knows "this part is glass, this is skin, this is a headlight". Every remedy below (detail, glow, glass, clear coat) needs that knowledge, so it is the shared foundation (phase 3).
7. **No realistic presentation.** The viewer and the Unity snapshot show assets under flat lights with no reflections and no tone mapping, so the owner cannot judge photoreal results, and neither can users.

---

## 2. Remedies and verdicts

Engine column abbreviations: BI Unity built-in Standard, URP Unity URP Lit (Complex Lit where stated), UE Unreal 5, GD Godot 4, glTF with the KHR extension named.

### 2.1 Preview and presentation

| Remedy | What it gives the user | How it would work here | Tool or resource, licence, commercial use | Size | 12 GB fit | Engines | Effort | Risk |
|---|---|---|---|---|---|---|---|---|
| Image-based light and tone mapping in the app viewer | "See it the way it will look in a game: metal shines, glass and gloss reflect the room." | `Viewer.tsx`: `PMREMGenerator` on an environment, `scene.environment` set, `renderer.toneMapping` to the Khronos PBR Neutral or ACES curve, exposure control; a "Light" chooser with four presets; the existing lights stay as an optional "Flat" preset for checking shapes | three.js `RoomEnvironment` ships inside three.js (MIT, part of the existing dependency), so the default "Studio" preset needs no download. Outdoor, overcast and night presets from Poly Haven HDRIs, CC0, may be bundled and redistributed [L1] | 0 for Studio; about 1 to 2 MB per 1k HDRI (estimate) | CPU and browser GPU only | Viewer only (the engine has its own lights); the GLB is unchanged | S | Low. The viewer must not suggest the game will look like this; label the preset "Preview light" |
| Showcase lighting in the Unity import | "Open the showcase scene and it looks like the preview." | `VgacImport.cs` writes `Assets/VGAC/_Showcase/VGAC Showcase.unity` once: HDRI skybox (a bundled Poly Haven 1k HDRI imported as a cubemap), a reflection probe, a directional sun matched to the HDRI, a URP Volume with tone mapping when URP is active; `Snapshot` renders in that scene | Poly Haven HDRI, CC0 [L1]; URP Volume and reflection probe are engine features | About 1 to 2 MB (estimate) | CPU (Unity) | URP and BI (BI has no tone mapping without the post-processing package; the scene still gets HDRI light and reflections) (engine knowledge, not re-checked) | S | Low. Must never touch the user's own scenes, only add one in `Assets/VGAC` |
| Turntable "beauty" render for the finish card | "A picture good enough to show people." | Blender Eevee render of LOD0 under the same HDRI, written next to the existing strip | Blender (GPL, tool); HDRI CC0 | none new | Eevee on either card, a few seconds (estimate) | n/a | S | Low |

### 2.2 Texture super-resolution and detail passes

| Remedy | What it gives | How it would work here | Tool, licence, commercial use | Size | 12 GB fit | Engines | Effort | Risk |
|---|---|---|---|---|---|---|---|---|
| GAN upscaler on the baked base colour | "Crisper edges and less blur up close." Does not add real detail the voxel field never had | Bake at the platform size, upscale base colour (and roughness) x2 or x4 with ComfyUI's native "Upscale Image (using Model)" node, downsample to the target size, then re-run dilation. Never on the normal map (a GAN invents fake bumps; resample normals with bicubic and renormalise) | Real-ESRGAN: BSD-3-Clause code and weights (secondary) [U2]. SwinIR: Apache-2.0 (secondary) [U3]. HAT: Apache-2.0 [U1]. All allow commercial use and place no claim on outputs | Real-ESRGAN x4plus about 64 MB (estimate); HAT-L about 160 MB (estimate) | Fits easily; about 15 to 40 s for a 2048 atlas on the 3060 (estimate) | All (it is just a sharper PNG) | S | Low to medium: hallucinated texture on faces; seams if dilation is not re-run |
| Diffusion upscaler (SeedVR2) | "Sharper still", with invented detail | As above, swapping the model | SeedVR2 weights Apache-2.0; ComfyUI node Apache-2.0 [U4] | 3B fp16 about 6 to 7 GB (estimate) | Reports range from an 8 GB GGUF setup to "at least 18 GB"; tiling node available [U4]. Tight on 10 to 12 GB | All | S to M | Medium: it is a video model; invents detail unrelated to the 3D surface |
| Tiled diffusion detail pass on the UV atlas (Ultimate SD Upscale style, Z-Image with the Tile ControlNet) | Invented fine detail | Tiles over the atlas image | Z-Image Tile ControlNet: Apache-2.0 (secondary: the model card excerpt had no licence line) [U7] | about 3 to 6 GB (estimate) | Fits | All | M | **High. Do not build.** A UV atlas is a jigsaw: a tile spans unrelated islands, invents a pattern that crosses island borders, and neighbouring islands that meet on the model get different inventions, so seams appear on the 3D surface. Rotated and scaled islands also make the model draw detail at random sizes |
| Detail pass on rendered views, projected back | Invented detail that respects the 3D surface | Render 4 to 6 views, tile-upscale each with Z-Image Tile, project back to the atlas with visibility weights | As above | as above | Fits, minutes per asset (estimate) | All | L | High: view seams, lighting baked into the projection, drift from the concept. Expert-only later at best |

Other upscaler families: models on OpenModelDB carry their own licences, many of them non-commercial. 4x-UltraSharp and 4x-UltraSharpV2 are CC BY-NC-SA 4.0 and are ruled out [U5]; some community models are CC BY 4.0 (for example the Nomos family, secondary) [U6], which allows commercial use with credit. The app should ship one permissively licensed default (Real-ESRGAN x4plus or HAT) and read the licence per model if it ever lists more.

### 2.3 Micro detail: detail normal maps

| Remedy | What it gives | How it would work here | Tool or resource, licence | Size | 12 GB fit | Engines | Effort | Risk |
|---|---|---|---|---|---|---|---|---|
| Detail normals baked into the asset's own normal map | "Up close, leather has grain, metal has fine scratches, cloth has weave." | Per material region (2.4), pick a tileable CC0 normal from a bundled shelf. In Blender, project it in object space with box (triplanar) mapping at its real-world size, bake to tangent space on the final mesh, and blend with the asset normal (reoriented normal blending), weighted by a strength per material and faded where the base normal is already strong. One normal map out, so it works in every engine | ambientCG: CC0, commercial and redistribution allowed [L2]. 3D Textures (3dtextures.me): CC0, commercial allowed [L3]. cgbookcase: CC0 (secondary; its licence page did not load) [L4]. Poly Haven textures: CC0, redistribution allowed [L1] | A curated shelf of about 20 to 30 normals at 1k, about 40 to 80 MB (estimate) | CPU (Blender bake), one to three minutes (estimate) | All: it is the normal map | M | Medium: detail at the wrong scale looks fake; mitigated by real-world sizes recorded per texture and a strength cap |
| Engine detail maps (second texture tiled at its own scale) | Sharp detail at any distance, not limited by the atlas | Write the chosen detail normal and a detail mask; set them on the material | Same CC0 shelf | as above | n/a | URP Lit "Detail Inputs": detail albedo, detail normal, mask from the mask's alpha, tiling [E3]; BI Standard has detail albedo and detail normal with a mask (engine knowledge, not re-checked); GD StandardMaterial3D "Detail" (engine knowledge, not re-checked); UE needs a material function (no import path). glTF has no detail map | S once 2.4 exists | Low technically, but only **one** detail texture per material in URP and BI, so a multi-material asset can only show one. **Expert-only** |

Picking a detail per region automatically is covered in 2.4.

### 2.4 Knowing what each part is made of (the shared foundation)

| Remedy | What it gives | How it would work here | Tool, licence | Size | 12 GB fit | Effort | Risk |
|---|---|---|---|---|---|---|---|
| Regions from the existing maps | Candidate parts with one material each | Cluster texels in UV space on base colour (Lab), metallic and roughness, then clean up by UV island and by mesh connectivity (faces whose texels share a cluster); merge clusters under 1 percent of the area | Own numpy code | none | CPU, seconds | S | Low; over-splits patterned cloth (merge by the name step) |
| Regions from rendered views | Better parts where colour fails (steel sword on steel armour) | Render 6 to 12 views in Blender, segment with SAM 2, vote per face (the method in CHARACTER-PARTS-RESEARCH 2.1, row "SAM on multi-view renders") | SAM 2.1: Apache-2.0, hiera-small checkpoint about 184 MB [M1]. SAM 3 for text prompts ("glass", "headlight"): Meta SAM Licence, commercial with a weapons-development and military clause (reused verdict, CHARACTER-PARTS [P24]) | 184 MB (SAM 2.1 small) | Seconds per view | M | Medium: thin parts bleed |
| Naming each region | "We found: steel, leather, cloth, glass." | Crop each region in two renders, ask a local vision model to pick from a fixed list (steel, iron, gold, brass, copper, chrome, painted metal, car paint, rubber, plastic, glass, gem, leather, cloth, wood, varnished wood, stone, brick, concrete, skin, fur, feathers, scales, chitin, hair, bark, leaf, emissive light, flame, screen) with a confidence; the prompt words of the asset are given as a hint | Gemma 3 12B, already installed and used as the judge (Gemma terms already accepted for this app; outputs unrestricted). Alternative: Qwen3-VL 8B, Apache-2.0, about 6 GB at Q4 (secondary) [M2] | Gemma already on disk | Gemma cold start measured at 365 s on the 3080 (STATUS.md); all regions in one call | M | Medium: wrong names; the user corrects them by clicking (3.3) |
| Named-material presets | Real values for known materials: gold is metallic with a warm colour, rubber is rough and dark, glass is smooth | A table of physically measured base colour ranges, metallic and roughness ranges per name; prep blends the region's maps toward the preset inside the range (it keeps the painted variation, it only corrects the level) | Own table; values from public PBR charts (to be sourced at build time) | none | CPU | S | Low |

### 2.5 Height and parallax

| Remedy | What it gives | How | Tool, licence | 12 GB | Engines | Effort | Risk |
|---|---|---|---|---|---|---|---|
| Height from the normal map | "Bricks and cobbles look deep when you walk past them." | Integrate the normal map (Frankot-Chellappa in Fourier space) per UV island; per-island normalisation; for tileables integrate across the wrap | Own numpy | CPU | URP Lit and BI: height map with parallax (engine knowledge, not re-checked); GD: height map with parallax occlusion (engine knowledge, not re-checked); UE: bump offset or parallax occlusion material function. glTF core has no height map | S | Low for tileables; on atlases, islands do not join, so parallax shows steps at seams. Use for material and environment only |
| Height from a depth estimator | Height for the material category from the generated swatch | Depth Anything V2 **Small** on the tile | Apache-2.0; Base, Large and Giant are CC BY-NC 4.0 and ruled out [D1]. Depth Anything 3 Base Apache-2.0; Large has conflicting tags, treat as non-commercial [D2] | Small about 100 MB (estimate), seconds | as above | S | Medium: depth of a picture is not surface height (dark grout can read as near); blend with the normal-integrated height |
| True displacement or tessellation | Real silhouette relief | Not for this audience | n/a | n/a | UE Nanite displacement; others vary | n/a | **Do not build** (section 3.5) |

### 2.6 Better material maps from images

| Model | What it gives | Licence (code; weights) | Commercial | Size, fit | Verdict |
|---|---|---|---|---|---|
| Marigold IID Appearance v1-1 | Albedo, roughness, metallic from one image (InteriorVerse decomposition) | Apache-2.0 code [R4]; weights CreativeML Open RAIL++-M [R1] | Yes, with the RAIL use restrictions passed on in the manifest | 0.9B, fine-tuned from Stable Diffusion 2 [R1]; fits; seconds per view (estimate) | **Use** as a second opinion on the concept views for roughness and metal (phase 10) |
| Marigold IID Lighting v1-1 | Albedo, diffuse shading, non-diffuse residual | RAIL++-M [R2] | Yes, with restrictions | Same size class | **Use** as a stronger de-light source: the shading layer tells prep which darkening is light, not colour |
| Marigold Normals v1-1 | Sharp normals from an image | RAIL++-M (v0-1 was Apache-2.0) [R3] | Yes | Same size class | **Use** (phase 10) |
| StableNormal | Sharp, stable normals from an image | Code Apache-2.0 [R6]; weights `stable-normal-v0-1` Apache-2.0 (the card does not name its base model) [R6] | Yes | Fits (estimate) | **Use**, preferred for normals: the cleaner licence |
| Lotus | Depth and normals | Code Apache-2.0 [R7]; weight licence not read in this pass | Code yes; weights to confirm | Fits (estimate) | Watch; confirm the weights' card first |
| DSINE | Normals | The LICENSE file shown on GitHub names other software (ElasticFusion text) and is unclear [R8] | Unclear | Small | **Ruled out until clarified** |
| IntrinsicAnything | Albedo and specular from images under unknown light | Code Apache-2.0 [R9]; weights licence not found | Code yes | Unknown | Watch |
| RGB↔X (Adobe) | Albedo, normal, roughness, metal, irradiance | Adobe Research Licence, non-commercial research only [R5] | **No** | | **Ruled out** |
| CHORD (Ubisoft La Forge), with official ComfyUI nodes, December 2025 | PBR maps from a generated texture image | Ubisoft Machine Learning Licence (Research-Only, Copyleft) [R12] | **No** | | **Ruled out** |
| StableMaterials | Text or image to a tileable PBR material (generator, not an estimator) | OpenRAIL on the model card [R10] | Yes, with use restrictions | SD-based, fits (estimate) | Possible for the material category later; overlaps with the existing Z-Image material engine |
| MatFuse | Controllable material generation | No licence found (mirror shows an empty licence label) [R11] | Unknown, so no | | **Ruled out until a licence is published** |

How the image models would be used: run on the concept and turnaround views (high resolution, sharp, no voxel cap), project the result onto the mesh where each view sees the surface head-on, and blend only the **high-frequency** part into the baked maps (the TRELLIS maps keep the low frequencies, which are consistent all round). Visible problem: the back and sides only have the turnaround views (lower quality), so detail density differs front to back. Projection needs the mesh to line up with the picture; the mesh stage's silhouette IoU (0.84 to 0.87 on recent runs) is a usable gate: only project when it is above about 0.85.

### 2.7 Glow and glass

| Remedy | What it gives | How it would work here | Tool, licence | 12 GB | Engines | Effort | Risk |
|---|---|---|---|---|---|---|---|
| Keep TRELLIS.2's alpha | See-through and cut-out areas from the generator itself | A copy of `BakeTextureFromVoxel` in `vgac-nodes` that also writes channel 5 (alpha) as a fourth image; same graph otherwise | TRELLIS.2 MIT (already used); own node | none | All | S | Medium: the solid remesh still fills thin parts, so alpha is useful mainly on closed volumes (bottles, gems, lanterns) and on the remesh-off path |
| Select parts by clicking | "Click the lantern glass." | Click on the model in the viewer: the backend selects the region (2.4) under the click, or flood-fills connected faces of similar colour; SAM 2 on the clicked view for parts colour cannot separate. Shift-click adds, a slider grows or shrinks | SAM 2.1 Apache-2.0 [M1]; own region code | 184 MB | All | M (the click-to-region plumbing is shared with phases 3, 5, 6) | Medium |
| Emission | "The lantern, eyes, runes or headlights actually give off light." | Emissive mask from the region; emissive colour from the base colour of that region, brightened; strength by kind (screen 2, lantern 5, headlight 20 as starting values); the base colour under it is darkened so it does not double | Own code | none | glTF `emissiveTexture` plus `KHR_materials_emissive_strength` for values over 1 (Blender writes it automatically above 1.0 [E6]); three.js supports it [E5]; Unity glTFast does not list it, UnityGLTF does [E1][E8]; our Unity importer sets `_EmissionMap`, `_EmissionColor` (HDR) and `_EMISSION` directly (engine knowledge, not re-checked); UE parses the extension [E4]; GD emission with energy multiplier (engine knowledge, not re-checked; Godot's own docs list no glTF extension table [E7]) | S after the selection | Low |
| Cut-out (alpha test) | Lace, chain links, leaves, feather edges, hair edges | Alpha map with a threshold; two-sided where thin | Own code | none | glTF `alphaMode: MASK`; URP Alpha Clipping; BI Cutout; GD alpha scissor; UE masked (engine knowledge, not re-checked) | S | Low |
| Glass, closed volume (bottle, gem, lantern globe) | "Light passes through the bottle." | Glass faces split into a **second material** (GREAT-ASSETS S25 allows a second material only for transparent or emissive parts); roughness from the region; tint from base colour | Own code | none | glTF `KHR_materials_transmission`, `_ior`, `_volume`: three.js supports all three [E5]; Blender exports them [E6]; glTFast URP partial, built-in and HDRP do not support transmission textures [E1]; Unity URP has no transmission: the importer uses a Transparent surface with low alpha and high smoothness (fake glass) (engine knowledge, not re-checked); UE translucent or thin translucent; GD transparency plus refraction (engine knowledge, not re-checked) | M | Medium: transparent sorting in engines; one glass material per asset keeps it manageable |
| Glass, panes in a closed body (car windows, house windows) | Windows that look like glass | **Not see-through:** the solid remesh leaves no interior, so a transparent window would show the inside of the shell. Make them dark, very smooth, slightly reflective opaque glass (or with clear coat) | Own code | none | Every engine (it is an opaque material) | S | Low; say so honestly in the check text |

### 2.8 Eyes

| Remedy | What it gives | How it would work here | Tool, licence | 12 GB | Engines | Effort | Risk |
|---|---|---|---|---|---|---|---|
| Detect the eyes | Where the eyes are on the model | Front and three-quarter renders; the local vision model gives a box per eye; SAM 2 refines it to a mask; project to faces; check left-right symmetry about the head | Gemma 3 (installed) or Qwen3-VL (Apache-2.0) [M2]; SAM 2.1 Apache-2.0 [M1]. A dedicated face-landmark model would be more precise on people but was not licence-checked in this pass | about 0.2 GB beyond the judge | Seconds plus the judge's cold start | M | Medium on people; high on creatures (cartoonish or many eyes) |
| Replace with eye geometry | Eyes that catch light and can look around | Push the eye region in to make a socket (move faces inward along the normal, about the eyeball radius), place a UV sphere eyeball with a slightly bulged cornea shell, parent to the head bone (or two eye bones for look-at), iris texture cropped from the concept's eye or from a CC0 iris set, sclera from a CC0 texture | MakeHuman system assets eyes, CC0 [H5]; a CC0 eyeball on BlendSwap (secondary, textures reported missing in the first upload) [L8]; or procedural in Blender (own) | Under 10 MB | CPU (Blender) | L | High: an eye a few millimetres off looks worse than a painted one. Always shown for approval, never automatic |
| Eye material | Wet, glossy, with depth | Iris and sclera opaque; cornea shell transparent and very smooth | Own | none | UE has an Eye shading model; GD transparency plus clearcoat; URP Complex Lit clear coat on one sphere, or a transparent cornea shell [E2]; glTF clearcoat plus a transparent cornea (engine knowledge, not re-checked except [E2]) | S after geometry | Medium |

### 2.9 Fur, feathers and hair

| Remedy | What it gives | How | Tool, licence | Engines | Effort | Risk |
|---|---|---|---|---|---|---|
| Shell fur shader in Unity | "Fur that stands up and breaks the outline." | The app exports fur masks: length (0 to 1 from fur regions), direction as a flow map (tangent-space, from the nearest bone chain toward its tip, bent toward gravity), density; the importer adds a fur renderer with the shader | hecomi UnityFurURP: MIT [F1]; jiaozi158 ShellFurURP (MIT, contains code and assets from other repositories) [F2]. Built-in: no permissive package checked; a small own shell shader is simpler | URP via the package; BI with an own shader | L | Medium to high: shells cost 16 to 32 extra draws (PC and Showcase only); geometry-shader variants do not run on every platform |
| Shell fur in Godot | Same | Godot import script attaches the add-on | Squiggles Fur (Godot 4, MIT on GitHub, one mirror says AGPLv3: check the LICENSE file) [F4]; Arnklit ShellFur (MIT, Godot 3.5) [F3] | GD | M after Unity | Medium |
| Fur in Unreal | Same | Unreal has Groom (strands) and hair cards; shells need a custom material. The app writes the masks and documents them | Engine features | UE | Document only | n/a |
| Feather and fur cards | Feathers and tufts as alpha cards | Scatter cards along the flow map on fur or feather regions; a small card atlas generated with Z-Image (Apache-2.0) on a plain background and cut with BiRefNet (MIT), both already installed | Already installed | All (alpha-tested cards) | L | High: cards on a solid shell can look like a hedgehog; needs art tuning |
| Hair cards for people | Hair that is not a helmet | Generate guide curves on the scalp region in Blender, convert to cards with own Geometry Nodes, bake a strand texture atlas procedurally | Blender (GPL tool); Daniel Bystedt's free curves-to-cards setup exists but its licence is not stated, so do not bundle [F5]; Unreal's Hair Card Generator is Unreal-only [F6] | All | L | High: the hairstyle must match the concept; **expert-only later** |

### 2.10 Photoreal people

| Option | What it is | Licence, exactly | Usable? | Why |
|---|---|---|---|---|
| MakeHuman (standalone) | Parametric human generator | Code AGPL; bundled system assets CC0; exported models are the user's to use commercially [H1][H4]. Community clothes and skins keep their own licences (some CC-BY) [H2] | Yes for CC0 assets | Desktop app, not a library the backend can drive easily |
| MPFB2 (MakeHuman for Blender) | Blender add-on: body shape, CC0 skins, eyes, eyebrows, eyelashes, teeth, rigs | Add-on code GPLv3; bundled assets CC0 [H3][H1]; "characters you export are yours to use commercially, including selling them" [H2]. The GPL add-on itself must not be shipped inside a proprietary game; exported meshes and textures carry no GPL (project's stated view) [H4] | Yes | Runs in the Blender the app already uses; gives separate eyes, teeth, lashes, clean UVs and a game rig (whether the game rig has finger bones is to be verified) |
| Anny (NAVER LABS Europe, November 2025) | Differentiable PyTorch human body model for all ages, built on MakeHuman assets | Model and code Apache-2.0 [H6]; the MakeHuman-derived assets are CC0 (secondary, from a fork) [H6] | Yes | **The most useful new option:** differentiable, so it can be fitted to a generated mesh by optimisation in the backend venv without Blender |
| MetaHuman | Epic's realistic humans | Characters are "Non-Engine Products" under the Unreal EULA: usable in other engines without royalties; above 1 million USD annual revenue rendering them outside Unreal needs an Unreal seat licence [H7] | Not as an app feature | Authoring happens in Epic's tools with an Epic account; the app cannot generate, bundle or redistribute MetaHumans; outputs carry Epic's terms, not the user's own. Suitable only as a hand-off note for Unreal users |
| SMPL, SMPL-X | Research body models | Non-commercial research; commercial licence through Meshcapade; also bars training for commercial use [H9] | **No** | Licence |
| Daz 3D | Figures and content | An Interactive Licence must be bought per product for game use [H8] | **No** (not free) | Cost and per-product terms |
| Character Creator (Reallusion) | Commercial character tool | Paid (not licence-checked in this pass) | **No** | Not free |

**Fitting a generated character onto a base body (what is realistic).**

1. Fit: optimise Anny's shape parameters and pose (or MPFB's targets) so its surface matches the generated mesh (point-to-surface distance plus a silhouette term). Fits tightly where the character is lightly dressed; armour, cloaks, skirts and big hair are far from the body and cannot be wrapped.
2. Texture: bake the generated mesh's colour onto the base body's clean UVs (Blender selected-to-active), with the face and hands from the base body's UV layout; keep the MPFB CC0 eyes, teeth and lashes as separate parts.
3. Weights and rig: the base body's own rig and weights, so fingers and clean deformation come with it (verify the finger bones).
4. What comes out: a realistic body with real eyes, teeth and better topology, painted with the generated skin and close-fitting clothes. What does not: loose clothing and armour; those stay as the generated shell, so the honest product is "Build on a real body" for lightly clothed people only, with a check that refuses when the fit error is large.
5. Effort L, risk high. Put it late (phase 13).

### 2.11 Foliage

| Remedy | What it gives | How | Tool, licence | Engines | Effort | Risk |
|---|---|---|---|---|---|---|
| Leaf cards and a leaf atlas (plan phase 10, G18) | "Leaves you can see the sky through." | Trunk and branches from TRELLIS.2 with the leaf mass masked off (by colour region plus the alpha channel kept in 2.7); leaf images generated with Z-Image on a plain background, cut with BiRefNet, packed into an atlas with a normal and translucency map; cards scattered over the removed leaf volume facing outward | Z-Image (Apache-2.0), BiRefNet (MIT), already installed | glTF `alphaMode: MASK`, double-sided; URP Alpha Clipping and two-sided; BI Cutout; GD alpha scissor plus backlight; UE masked with Two Sided Foliage (engine knowledge, not re-checked) | L | High (the plan's own spike requirement stands) |
| Leaf translucency | Leaves glow when backlit | Translucency map from leaf thickness (brighter at edges); exported | Own | GD backlight; UE Two Sided Foliage; URP Lit has none (Unity's SpeedTree shaders do, but expect SpeedTree data) (engine knowledge, not re-checked); glTF: no widely supported extension (not verified in this pass) | S after cards | Medium |
| Free plant libraries to complement | Real scanned plants next to generated ones | Offer as links, not bundled generators | Poly Haven plant and tree models, CC0 (example: `tree_small_02`, 4.6 m, about 5 million triangles, so far over game budgets) [L6][L1]; OpenGameArt and Poly Pizza CC0 low-poly plants (stylised) [L7]; Quixel Megaplants free on Fab but under the Fab Standard Licence (no redistribution) and built for Unreal 5 [L5] | n/a | n/a | Scans need decimation and cards before games |

### 2.12 Free scan and asset libraries

| Library | Licence status now | Can the app bundle it? | Use |
|---|---|---|---|
| Poly Haven (HDRIs, textures, models) | CC0; commercial use, redistribution and inclusion in sold products allowed; site text, renders and logos are not CC0; no scraping without permission; API has its own terms [L1] | Yes (download once at build time, not by scraping) | HDRIs for presentation (2.1); detail textures (2.3); scanned props and plants as companions |
| ambientCG | CC0, commercial use and redistribution as files allowed [L2] | Yes | Detail normal shelf |
| 3D Textures (3dtextures.me) | CC0, commercial use allowed [L3] | Yes | Detail shelf |
| cgbookcase | CC0 (secondary; licence page returned 404) [L4] | Yes after confirming | Detail shelf |
| Quixel Megascans | Used to be free with Unreal, then free to everyone until 31 December 2024; since 2025 paid on Fab with about 1,500 assets still free. Free claims since Fab fall under the Fab Standard Licence (any engine, no redistribution); assets claimed earlier on Quixel.com fall under the Epic Content Licence (free only in Unreal) [L5] | **No** | User-supplied only; mention in help |
| MakeHuman and MPFB system assets | CC0 [H1][H5] | Yes (assets, not the GPL add-on code) | Eyes, teeth, lashes; base bodies (phase 13) |
| OpenGameArt, Poly Pizza | Per asset; many CC0 [L7] | Per asset only | Stylised companions; not photoreal |

---

## 3. Phased plan

Ordered by user value over effort. Each phase follows the plan's UX rules: correct by default, plain words, a Fix button on every warning, advanced options never at the cost of clarity. "On" means it runs automatically for the Photoreal look; other looks get it only where stated. Times are for the 3060 12 GB unless stated, and are estimates until measured.

All new checks are `warn` (none would break a game; the autopilot does not stop on them). Copy below is shippable but is open to the narrative pass, as in GREAT-ASSETS-PLAN section 8.

### Phase 1. "Preview in realistic light" (S)

- **Adds:** reflections and tone mapping in the app viewer, four light presets (Studio, Outdoors, Overcast, Night), a "Flat" preset that keeps today's lights for checking shape; a showcase scene in Unity and the snapshot rendered in it; a beauty render on the finish card.
- **Default for Photoreal:** on, preset Outdoors. All other looks: on, preset Studio (stylised assets also look better with reflections; their painted shading is unaffected).
- **How a person meets it:** the model simply looks right the first time. A small "Light" button over the viewer opens four picture tiles. In Unity, the finish card's "Using it in Unity" gains: "Open VGAC Showcase to see it in good light."
- **Check:** none (presentation only).
- **Cost on the card:** none in the app (browser GPU). Unity snapshot unchanged in time. Beauty render a few seconds in Eevee.
- **Files:** `frontend/src/Viewer.tsx` (environment, tone mapping, presets), `frontend/src/help.ts` (`TERM_HELP.preview_light`), new `frontend/public/hdri/` (three CC0 1k HDRIs), `unity-sandbox/Assets/Editor/VgacImport.cs` (showcase scene, `Snapshot` scene, raise `Version`), new `backend/vgac/blender/beauty.py`, `backend/vgac/stages/export.py` (register the render), `frontend/src/pipeline/AutopilotPanel.tsx` (show it).
- **Verify by eye:** the knight's armour shows a visible environment reflection in the viewer and in the Unity snapshot; a matte chest does not look shiny; Reduce Motion still respected.

### Phase 2. "Sharpen the textures" (S)

- **Adds:** a GAN upscale of base colour and roughness after the bake, then the existing dilation; normal maps resampled smoothly, never through the GAN.
- **Default for Photoreal:** on for PC and Showcase (bake at half size, upscale x2 to the target); off on Phone (the budget is the limit there, not sharpness). Other looks: off (painted looks are soft by design; pixel art must never pass through it).
- **Model:** Real-ESRGAN x4plus (BSD-3-Clause, secondary) or HAT (Apache-2.0), one bundled default, recorded in `models.yaml` with its licence.
- **Check** `texture_sharpness` (prep): ok "Textures are about {value} pixels per metre, sharp at a few steps away." fail "Textures are about {value} pixels per metre, so the surface looks soft up close." Fix label "Sharpen the textures"; fix prep `{sharpen: true}`. Fails under 512 px per metre for PC and 1024 for Showcase (the GREAT-ASSETS S31 rules of thumb).
- **Honest note in the help:** "Makes edges crisper. It cannot invent detail that was never there."
- **Cost:** 15 to 40 s per asset; about 64 to 160 MB download.
- **Files:** `backend/vgac/graphs/trellis.py` or a new `backend/vgac/graphs/upscale.py`, `backend/vgac/stages/prep.py`, `backend/vgac/imaging.py` (texel density per metre), `backend/vgac/data/models.yaml`, `backend/vgac/data/styles.yaml` (recipe `mesh.sharpen`), `backend/vgac/guidance.py`, `backend/vgac/reference.py`, `frontend/src/help.ts`.
- **Verify by eye:** before and after crops at 1:1 on the knight's face, the chest's metal bands and a wall; no new seams at 1/8 size; no invented faces in cloth.

### Phase 3. "What is it made of?" (M; the foundation for phases 4 to 7)

- **Adds:** regions from the maps (and SAM 2 on views where colour fails), a name per region from the local vision model, and named-material presets that pull metal, roughness and colour level into the range real materials have.
- **Default for Photoreal:** on, automatic. Other looks: regions and names computed (cheap, used by glow), presets not applied (they would fight a painted look).
- **How a person meets it:** in "Clean up for the game", a line "Made of: steel, leather, cloth, glass." with a "Show me" toggle that colours the model by material. Clicking a coloured part opens a small list of plain material names to correct it. No numbers.
- **Check** `materials_named` (prep): ok "Made of: {list}." fail "We could not tell what {count} part(s) are made of, so they keep the generator's guess." Fix label "Name the materials"; fix `{"ui": "material_picker"}`.
- **Cost:** region clustering seconds on CPU; one judge call with all regions (Gemma cold start 365 s measured on the 3080; warm much less), or Qwen3-VL 8B at about 6 GB; SAM 2 small 184 MB, seconds per view.
- **Files:** new `backend/vgac/materials.py` (regions, presets table), `backend/vgac/judge.py` (region naming prompt), new `backend/vgac/blender/region_views.py`, `backend/vgac/stages/prep.py`, `backend/vgac/guidance.py`, `backend/vgac/reference.py`, `frontend/src/Viewer.tsx` (region overlay, click to pick), new `frontend/src/pipeline/MaterialPicker.tsx`, `frontend/src/api.ts`, `frontend/src/mock/data.ts`, `frontend/src/help.ts`.
- **Verify by eye:** overlay on the knight (steel, leather, cloth, skin), the chest (wood, iron), a car (paint, glass, rubber, chrome); a gold item reads warm and metallic after presets.

### Phase 4. "Add fine surface detail" (M)

- **Adds:** a bundled shelf of CC0 detail normals (ambientCG, 3D Textures, Poly Haven), each with its real-world size; per named region, the matching detail is projected and baked into the asset's own normal map.
- **Default for Photoreal:** on for PC and Showcase; on Phone at half strength. Other looks: off.
- **How a person meets it:** nothing to do. The finish card line: "Fine surface detail: leather grain, brushed steel, cloth weave." Advanced: one "How strong" choice (Subtle, Normal, Strong).
- **Check** `surface_detail` (prep): ok "Fine surface detail added: {list}." fail "Up close the {part} looks smooth, like plastic." Fix label "Add fine surface detail"; fix prep `{detail: "normal"}`.
- **Expert-only:** writing the detail as a separate engine detail map (URP and built-in allow one per material).
- **Cost:** CPU Blender bake, one to three minutes; shelf about 40 to 80 MB.
- **Files:** new `backend/vgac/blender/detail_bake.py`, new `backend/vgac/data/detail_shelf.yaml` (file, licence, source URL, size in metres, materials it suits), `backend/vgac/stages/prep.py`, `backend/vgac/guidance.py`, `backend/vgac/reference.py`, `frontend/src/help.ts`, README or a `THIRD-PARTY.md` entry for the CC0 shelf.
- **Verify by eye:** close-up renders under the Phase 1 light: grain at a believable scale (a leather pore is not a pebble), no visible tiling, no seam lines where islands meet.

### Phase 5. "What glows?" (M)

- **Adds:** an emissive map and strength for named light regions (lantern, flame, screen, rune, headlight, eyes of a monster), found from prompt words and region names; click to add or remove a part.
- **Default for Photoreal:** on when the description or a region name says it glows; otherwise the check offers it. Other looks: on as well (a glowing rune is not a realism question).
- **How a person meets it:** the Night preset in the viewer shows the glow. A line under the model: "Glows: the lantern glass." To change it: "Change something" then "What glows?", click the part, pick "Soft", "Bright" or "Very bright".
- **Check** `glow_unlit` (prep): ok "The parts that should glow give off light: {list}." fail "The {part} looks like it should glow, but it is only painted bright." Fix label "Make it glow"; fix prep `{glow: [region]}`.
- **Engines:** glTF emissive plus `KHR_materials_emissive_strength`; Unity `_EmissionMap` with HDR colour set by the importer; Unreal and Godot from the glTF or FBX plus the manifest.
- **Cost:** seconds (CPU), given phase 3.
- **Files:** `backend/vgac/materials.py`, `backend/vgac/stages/prep.py`, `backend/vgac/blender/pack.py` (emission in the Principled BSDF), `backend/vgac/stages/export.py` (`T_<Name>_Emission.png`, manifest), `unity-sandbox/Assets/Editor/VgacImport.cs` (emission keyword, raise `Version`), `frontend/src/Viewer.tsx`, new `frontend/src/pipeline/GlowGlassTool.tsx` (shared with phase 6), `backend/vgac/guidance.py`, `frontend/src/help.ts`.
- **Verify by eye:** a lantern prop under Night in the viewer and in the Unity showcase scene with bloom; the glass does not also stay painted bright under it.

### Phase 6. "What is glass?" (M)

- **Adds:** TRELLIS.2's alpha kept through the bake (own copy of the bake node); glass and cut-out regions from alpha plus region names; a second material for glass on closed volumes (bottles, gems, globes); dark glossy opaque glass for windows set in a closed body; alpha-tested cut-outs for lace, chains and similar.
- **Default for Photoreal:** on when the generator's alpha or a region name says glass; otherwise offered by the check. Other looks: cut-outs on, glass on.
- **How a person meets it:** "Glass: the bottle (see-through)" or "Glass: the windows (dark glass; the inside of the car is not modelled)". To change: the same click tool as glow.
- **Check** `glass_solid` (prep): ok "Glass parts are made of glass: {list}." fail "The {part} looks like glass but is solid paint." Fix label "Make it glass"; fix prep `{glass: [region]}`. Second sentence when the part is a window: "Windows become dark, shiny glass. You cannot see inside, because the inside is not modelled."
- **Engines:** glTF transmission, IOR and volume for three.js, Blender and Unreal; Unity URP transparent fake glass; Godot transparency plus refraction.
- **Cost:** the bake node change adds nothing measurable; the material split is seconds.
- **Files:** `vgac-nodes` in the ComfyUI install (new `VGACBakeTextureFromVoxelAlpha`), `backend/vgac/graphs/trellis.py`, `backend/vgac/stages/mesh.py`, `backend/vgac/materials.py`, `backend/vgac/blender/pack.py` (second material, KHR settings through the Principled BSDF), `backend/vgac/stages/export.py`, `unity-sandbox/Assets/Editor/VgacImport.cs`, `frontend/src/pipeline/GlowGlassTool.tsx`, `backend/vgac/guidance.py`, `frontend/src/help.ts`.
- **Verify by eye:** a bottle shows the background through it in the viewer; a car's windows read as glass in Outdoors light; nothing becomes accidentally see-through (check the knight).

### Phase 7. "Glossy paint and brushed metal" (S, after phase 3)

- **Adds:** clear coat on regions named car paint, varnished wood, lacquer, chitin and wet scales; anisotropy direction on brushed metal where the engine supports it.
- **Default for Photoreal:** on for those names. Other looks: off.
- **How a person meets it:** no control in the simple layer; the finish line "Glossy paint: a clear lacquer layer on the body paint."
- **Check:** none (a material preset, not a defect).
- **Engines:** glTF `KHR_materials_clearcoat` and `KHR_materials_anisotropy` (three.js supports both [E5]; glTFast clear coat partial on URP [E1]); Unity URP: Complex Lit clear coat (about twice the shading cost [E2]), used only for vehicles on PC and Showcase; anisotropy is not in URP Lit (skip); Godot clearcoat and anisotropy (engine knowledge, not re-checked); Unreal Clear Coat shading model.
- **Cost:** none on the card.
- **Files:** `backend/vgac/materials.py`, `backend/vgac/blender/pack.py`, `unity-sandbox/Assets/Editor/VgacImport.cs`, `backend/vgac/stages/export.py` (manifest), `frontend/src/help.ts`.
- **Verify by eye:** a red car under Outdoors shows a sharp highlight over a softer coloured one; a wooden chest is not clear-coated.

### Phase 8. "Depth for close-ups" (S)

- **Adds:** a height map for the material category and environment pieces (integrated from the normal map, blended with Depth Anything V2 Small on material swatches); assigned as the parallax height in Unity, written for Godot and Unreal.
- **Default for Photoreal:** on for material and environment. Off for everything else (atlas seams show as steps).
- **Check** `height_matches` (material stage): ok "Height and bumps agree, so cracks look deep in the right places." fail "Height and bumps disagree in {value} of the tile, so some cracks may look raised." Fix label "Work out the height again"; fix material `{height: "from_normal"}`.
- **Cost:** seconds; Depth Anything V2 Small about 100 MB.
- **Files:** `backend/vgac/imaging.py`, `backend/vgac/stages/material.py`, `backend/vgac/graphs/material.py`, `backend/vgac/stages/export.py`, `unity-sandbox/Assets/Editor/VgacImport.cs` (`_ParallaxMap`), `backend/vgac/guidance.py`, `frontend/src/help.ts`.
- **Verify by eye:** cobblestone at a grazing angle in the Unity showcase scene; grout reads lower than stones.

### Phase 9. "Realistic eyes" (L)

- **Adds:** eye detection on renders, a socket, separate eyeballs with a cornea, an iris from the concept or a CC0 set, parented to the head bone.
- **Default for Photoreal:** humanoid, quadruped and serpent: **offered by the check, not automatic** (a wrong eye is worse than a painted one). Winged and multileg: off (too varied). Other looks: off.
- **How a person meets it:** the check appears after rigging with a before and after picture; one click accepts. A "Move the eyes" nudge (up, down, closer, wider, bigger, smaller) like the item nudge in CHARACTER-PARTS 6.2.
- **Check** `eyes_painted` (rig): ok "The eyes are separate, so they catch the light." fail "The eyes are painted on, so they look flat when the light moves." Fix label "Give it real eyes"; fix `{"ui": "eye_tool"}`.
- **Cost:** detection one judge call plus SAM 2 (seconds warm); Blender work under a minute.
- **Files:** new `backend/vgac/blender/eyes.py`, `backend/vgac/judge.py`, `backend/vgac/stages/rig.py`, `backend/vgac/blender/pack.py` (eye material), `unity-sandbox/Assets/Editor/VgacImport.cs`, new `frontend/src/pipeline/EyeTool.tsx`, `backend/vgac/guidance.py`, `frontend/src/help.ts`, `data/` for the CC0 iris set.
- **Verify by eye:** front, three-quarter and profile renders of three people and a wolf: eyes level, same size, not floating, not sunk; the highlight moves with the light.

### Phase 10. "Sharper from the picture" (M to L)

- **Adds:** StableNormal (Apache-2.0) or Marigold normals and Marigold IID (RAIL++-M) run on the concept and turnaround views; only their fine detail is projected onto the faces each view sees head-on; Marigold's shading layer feeds the de-light.
- **Default for Photoreal:** on when silhouette IoU is at least 0.85; skipped (with a reason in the finish lines) below that.
- **Check** `picture_detail` (prep): ok "Fine detail from the picture was added to the front and sides." fail "The model does not line up well enough with the picture to copy its fine detail." Fix label "Try again with the picture"; fix mesh `{}` (a new mesh run).
- **Cost:** about 1 to 3 minutes for 4 to 6 views (estimate); models 1 to 3 GB each (estimate).
- **Files:** new `backend/vgac/graphs/estimators.py`, new `backend/vgac/blender/project_views.py`, `backend/vgac/stages/prep.py`, `backend/vgac/data/models.yaml` (licences, RAIL terms in the manifest), `backend/vgac/guidance.py`.
- **Verify by eye:** front against back detail density on the knight (the difference should be acceptable); no double features where the projection is off.

### Phase 11. "Fur that stands up" (L, PC and Showcase only)

- **Adds:** fur length, direction and density masks for fur and feather regions; Unity shell fur from hecomi UnityFurURP (MIT) on URP; an own small shell shader on built-in; masks documented for Godot (Squiggles Fur, licence to confirm) and Unreal.
- **Default for Photoreal:** quadrupeds with a fur region on PC and Showcase: offered by a check, not automatic (it changes the draw cost). Phone: never.
- **Check** `fur_flat` (export): fail "The fur is painted on, so the outline looks smooth." Fix label "Add standing fur (PC only)"; fix export `{fur: "shells"}`.
- **Files:** new `backend/vgac/blender/fur_masks.py`, `backend/vgac/stages/export.py`, `unity-sandbox/Assets/Editor/VgacImport.cs`, `unity-sandbox/Packages` (the MIT package, with its licence file), `frontend/src/help.ts`.
- **Verify by eye:** the wolf under Outdoors in the Unity showcase: fur follows the body toward the tail and down the legs, no shell banding at the silhouette, frame time noted.

### Phase 12. "Leaves that let light through" (L; this is GREAT-ASSETS-PLAN phase 10, G18)

- **Adds:** trunk from TRELLIS.2, leaf mass masked off, a generated leaf atlas, cards scattered over the leaf volume, two-sided alpha-tested material with translucency where the engine has it; wind (G19) and the billboard (G20) from plan phase 9 apply on top.
- **Default for Photoreal:** on for foliage once the plan's research spike (one tree, one bush) passes.
- **Check** `leaves_solid` (prep): fail "The leaves are one solid shape, so no light comes through." Fix label "Make real leaves"; fix mesh `{leaves: "cards"}`.
- **Files:** as plan phase 10, plus `backend/vgac/blender/leaf_cards.py`, `backend/vgac/graphs/leaf_atlas.py`.
- **Verify by eye:** the tree against the Outdoors sky; card edges not visible as planes from below; overdraw on Phone recorded.

### Phase 13. "Build on a real body" (L, humanoid, opt-in)

- **Adds:** fit Anny (Apache-2.0) or an MPFB2 body (CC0 assets) to the generated person, bake the generated colour onto it, use the base body's eyes, teeth, lashes, rig and weights.
- **Default:** offered only for Photoreal humanoids whose clothing is close-fitting (fit error under a threshold); never automatic.
- **Check** `body_fit` (rig): ok "Built on a realistic body with real eyes, teeth and fingers." fail "Clothes or armour stand too far off the body to build on a realistic body; it keeps its own shape." No Fix on the fail (the honest answer is that it cannot).
- **Files:** new `backend/vgac/bodyfit.py` (Anny in the backend venv), new `backend/vgac/blender/body_bake.py`, `backend/vgac/stages/rig.py`, `backend/vgac/data/models.yaml`.
- **Verify by eye:** three lightly dressed people, face at 1:1; the fit refuses a plate-armoured knight.

### 3.1 Expert-only

| Item | Why expert-only |
|---|---|
| Engine detail maps as a second texture (instead of baking) | Only one per material in URP and built-in; the baked version works everywhere and needs no choice |
| Exact emission strength numbers, IOR and clear coat values | Three plain levels cover users; experts edit them in the engine |
| Choosing the upscaler model | One permissive default; other models need a licence read each |
| Fur shell count and length in millimetres | The three-level choice is enough; experts tune the material |
| HDRI choice beyond the four presets | A file picker in Advanced for one's own HDRI |
| Hair cards for people | Needs hairstyle judgement; later and Advanced only |

### 3.2 Do not build

| Item | Why not |
|---|---|
| Tiled diffusion detail on the UV atlas | Tiles cross unrelated islands, invent patterns that break at seams on the 3D surface, and draw detail at random scales on rotated islands |
| Strand hair or groom export | Only Unreal renders grooms well; heavy and far from this audience's needs |
| Subsurface scattering setup for Unity URP | URP Lit has none; a custom skin shader is a maintenance burden. Write a thickness mask in the manifest for Godot and Unreal users only, if asked |
| Generated lettering, badges and number plates | Diffusion text is gibberish and the concept negative already strips text. Engines have decal projectors (URP Decal, Unreal decals) for the user's own logos |
| True displacement or tessellation | Not portable; Nanite users enable it in Unreal |
| MetaHuman, Daz or Character Creator integration | Not free or not automatable; outputs carry the vendor's terms |
| See-through car windows | The closed shell has no interior; dark glass is the honest result |
| Any model in section 5 | Licence |

---

## 4. "Good to know" for the Photoreal look

These replace or extend the `caveat` lines in `styles.yaml` once the phases land. Today's wording is in brackets where it changes.

| Category | Good to know (after phases 1 to 7) |
|---|---|
| humanoid | Armour, cloth and gear can look real; faces are the hardest part, because eyes, teeth and hair are painted on. "Realistic eyes" helps, and hair stays a solid shape. |
| quadruped | Looks real at a distance. Up close the fur is painted on a smooth surface; on PC you can add standing fur. |
| winged | Feathers and wing skin are painted on a solid shape, so wings look thicker than real ones; light does not shine through them. |
| multileg | Glossy shells look good; fine hairs, thin legs and insect wings come out thicker and solid. |
| serpent | Scales look real and get fine relief; eyes can be made separate, the inside of the mouth stays painted. |
| prop_rigid | Wood, metal, stone and leather look best. Lamps and screens can glow and bottles can be real glass; chains and rope still come out thick, and any lettering is left off. |
| prop_articulated | As for props; once lids open, the inside is plain, not a modelled interior. |
| vehicle | Paint gets a glossy lacquer and lights can glow. Windows are dark glass you cannot see through, because the inside is not modelled. |
| environment | Looks real a few steps away, and walls and floors get depth up close. Large pieces get softer, because one texture is stretched over the whole piece. |
| foliage | The weakest category: leaves are one solid shape until "real leaves" is built, so plants look best far away. |
| material | Seamless and real-looking from normal distances; depth and shine are worked out from the picture, so deep cracks and wet surfaces are approximate. |

---

## 5. Ruled out on licence grounds

| Resource | Why | Source |
|---|---|---|
| RGB↔X (Adobe) | Adobe Research Licence: non-commercial research only | [R5] |
| CHORD and ComfyUI-Chord (Ubisoft La Forge) | Ubisoft Machine Learning Licence, Research-Only, Copyleft | [R12] |
| 4x-UltraSharp, 4x-UltraSharpV2 | CC BY-NC-SA 4.0 | [U5] |
| Depth Anything V2 Base, Large, Giant | CC BY-NC 4.0 (Small is Apache-2.0 and allowed) | [D1] |
| Depth Anything 3 Large | Conflicting tags (Apache on the card, CC BY-NC reported upstream): treated as non-commercial until clarified (Base is Apache-2.0) | [D2] |
| DSINE | LICENSE file unclear (text of another project) | [R8] |
| MatFuse | No licence found | [R11] |
| SMPL and SMPL-X (and anything that needs their model files) | Non-commercial; bars training for commercial use; commercial licence only through Meshcapade | [H9] |
| Daz 3D content | Paid Interactive Licence per product for games | [H8] |
| Quixel Megascans | Paid since 2025; free assets under the Fab Standard Licence forbid redistribution; older claims Unreal-only | [L5] |
| MetaHuman (unsuitable rather than unlicensed) | Epic's terms, Unreal seat licence above 1 million USD revenue, no bundling or automation | [H7] |
| Hair cards from curves (Daniel Bystedt) | Free, but no licence stated; do not bundle | [F5] |
| Reused from CHARACTER-PARTS-RESEARCH | PartField, PartPacker (NVIDIA non-commercial); OmniPart (PartField-derived code); Hunyuan3D-Part (territory excludes EU, UK, South Korea); Garment3DGen (CC BY-NC); DressCode (no licence); AMASS-trained motion models; Bandai Namco motion set (CC BY-NC) | CHARACTER-PARTS sections 2, 3, 5 |

To confirm before use (not ruled out): Lotus and IntrinsicAnything weight cards; Squiggles Fur (one mirror says AGPLv3); cgbookcase's own licence page; the Z-Image Tile ControlNet card's licence line.

---

## 6. Sources

Internal: `backend/vgac/data/styles.yaml`; `backend/vgac/stages/prep.py`; `backend/vgac/imaging.py`; `backend/vgac/graphs/trellis.py`; `backend/vgac/stages/export.py`; `backend/vgac/blender/pack.py`; `unity-sandbox/Assets/Editor/VgacImport.cs`; `frontend/src/Viewer.tsx`; `backend/vgac/data/platforms.yaml`; `backend/vgac/data/models.yaml`; `backend/vgac/data/categories/foliage.yaml`; ComfyUI `comfy_extras/nodes_mesh_postprocess.py` (lines 1343 to 1347 and 1383 to 1410, in the install at `C:\Users\evanr\ComfyUI-Installs\ComfyUI (1)\ComfyUI`); `STATUS.md`; `docs/GREAT-ASSETS.md`; `docs/GREAT-ASSETS-PLAN.md`; `docs/CHARACTER-PARTS-RESEARCH.md`.

Generator
- [T1] TRELLIS.2 project page (base colour, metallic, roughness, alpha): https://microsoft.github.io/TRELLIS.2 ; model card: https://huggingface.co/microsoft/TRELLIS.2-4B/blob/main/README.md

Material and normal estimators
- [R1] Marigold IID Appearance v1-1 (RAIL++-M, 0.9B, from SD2): https://huggingface.co/prs-eth/marigold-iid-appearance-v1-1
- [R2] Marigold IID Lighting v1-1: https://huggingface.co/prs-eth/marigold-iid-lighting-v1-1
- [R3] Marigold Normals v1-1: https://huggingface.co/prs-eth/marigold-normals-v1-1/blob/main/README.md ; v0-1 (Apache-2.0): https://huggingface.co/prs-eth/marigold-normals-v0-1
- [R4] Marigold repository: https://github.com/prs-eth/marigold
- [R5] RGB↔X licence: https://huggingface.co/zheng95z/rgb-to-x/raw/main/LICENSE ; project: https://zheng95z.github.io/publications/rgbx24
- [R6] StableNormal: https://github.com/Stable-X/StableNormal ; weights: https://huggingface.co/Stable-X/stable-normal-v0-1
- [R7] Lotus: https://github.com/EnVision-Research/Lotus
- [R8] DSINE licence file: https://github.com/baegwangbin/DSINE/blob/main/LICENSE
- [R9] IntrinsicAnything: https://github.com/zju3dv/IntrinsicAnything
- [R10] StableMaterials: https://huggingface.co/gvecchio/StableMaterials
- [R11] MatFuse (mirror, no licence shown): https://relatedrepos.com/gh/giuvecchio/matfuse
- [R12] CHORD: https://github.com/ubisoft/ubisoft-laforge-chord ; https://blog.comfy.org/p/ubisoft-open-sources-the-chord-model

Upscalers and depth
- [U1] HAT licence (Apache-2.0): https://github.com/XPixelGroup/HAT/blob/main/LICENSE
- [U2] Real-ESRGAN, BSD-3-Clause (secondary): https://awesome.ecosyste.ms/projects/github.com%2Fxinntao%2FReal-ESRGAN ; https://huggingface.co/coding-alt/stable-diffusion-webui/blob/main/html/licenses.html
- [U3] SwinIR, Apache-2.0 (secondary): https://huggingface.co/coding-alt/stable-diffusion-webui/blob/main/html/licenses.html ; https://gitee.com/uho163/SwinIR
- [U4] SeedVR2 ComfyUI node and weights: https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler ; https://docs.comfy.org/tutorials/utility/seedvr2
- [U5] 4x-UltraSharp: https://openmodeldb.info/models/4x-UltraSharp ; V2: https://openmodeldb.info/models/4x-UltraSharpV2
- [U6] Upscaler licence aggregator (secondary): https://huggingface.co/easygoing0114/AI_upscalers
- [U7] Z-Image Turbo Fun ControlNet Union 2.1 (Tile): https://huggingface.co/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1 ; licence (secondary): https://www.aibase.com/news/23294
- [D1] Depth Anything V2 licences: https://github.com/DepthAnything/Depth-Anything-V2/issues/162 ; https://huggingface.co/bigshanedogg/Depth-Anything-V2-Small/blob/main/README.md
- [D2] Depth Anything 3: https://huggingface.co/depth-anything/DA3-BASE ; licence conflict note: https://github.com/mrgnw/spatial-maker/pull/11

Segmentation and vision models
- [M1] SAM 2.1 hiera-small (Apache-2.0, 184 MB): https://huggingface.co/facebook/sam2.1-hiera-small
- [M2] Qwen3-VL 8B (Apache-2.0; VRAM figures secondary): https://huggingface.co/unsloth/Qwen3-VL-8B-Thinking-GGUF ; https://comfyui-wiki.com/en/models/qwen/qwen3-vl

People
- [H1] MakeHuman Community licence page: https://static.makehumancommunity.org/about/license.html
- [H2] MPFB FAQ, selling models: https://static.makehumancommunity.org/mpfb/faq/can_i_sell_models.html
- [H3] CG Channel on MPFB 2 (GPLv3 code, CC0 assets): https://www.cgchannel.com/2025/03/check-out-open-source-blender-character-generation-plugin-mpfb-2/
- [H4] MakeHuman LICENSE.md: https://github.com/makehumancommunity/makehuman/blob/master/LICENSE.md
- [H5] MakeHuman system assets, eyes (CC0): https://opengameart.org/content/makehuman-system-assets-eyes
- [H6] Anny paper (Apache-2.0): https://arxiv.org/abs/2511.03589 ; blog: https://europe.naverlabs.com/blog/anny-a-free-to-use-3d-human-parametric-model-for-all-ages/ ; fork noting CC0 MakeHuman assets (secondary): https://github.com/arunanksharan/anny-body-mesh
- [H7] MetaHuman licence: https://www.metahuman.com/license ; https://www.cgchannel.com/2025/06/you-can-now-sell-metahumans-or-use-them-in-unity-or-godot/
- [H8] Daz Interactive Licences: https://www.daz3d.com/interactive-license-info
- [H9] SMPL-X model licence: https://smpl-x.is.tue.mpg.de/modellicense.html

Fur and hair
- [F1] hecomi UnityFurURP (MIT): https://github.com/hecomi/UnityFurURP
- [F2] jiaozi158 ShellFurURP (MIT): https://github.com/jiaozi158/ShellFurURP
- [F3] Arnklit ShellFurGodot (MIT, Godot 3.5): https://github.com/Arnklit/ShellFurGodot
- [F4] Squiggles Fur (Godot 4): https://awesome.ecosyste.ms/projects/github.com%2Fqueenofsquiggles%2Fsquiggles-fur ; mirror listing AGPLv3: https://godotassetlibrary.com/asset/oYLu0I/squiggles-fur
- [F5] Daniel Bystedt hair cards from curves: https://www.cgchannel.com/?p=156171 ; https://80.lv/articles/free-hair-cards-from-curves-setup-for-blender
- [F6] Unreal Hair Card Generator: https://dev.epicgames.com/documentation/unreal-engine/hair-card-generator-for-grooms-in-unreal-engine

Libraries
- [L1] Poly Haven licence: https://polyhaven.com/license
- [L2] ambientCG: https://ambientcg.com
- [L3] 3D Textures: https://3dtextures.me/about/
- [L4] cgbookcase CC0 (secondary): https://app.cinevva.com/guides/free-textures-hdris-materials
- [L5] Fab and Quixel: https://support.fab.com/s/article/Fab-Transition-FAQs?language=en_US ; https://quixel.com/license ; https://www.cgchannel.com/2024/10/epic-games-has-made-megascans-free-to-all-but-only-until-the-end-of-2024/ ; https://support.fab.com/s/article/Can-I-use-Quixel-Megascans-content-in-my-Fab-product?language=en_US
- [L6] Poly Haven tree example: https://polyhaven.com/a/tree_small_02
- [L7] CC0 plants: https://opengameart.org/content/tree-and-plants-low-poly-3d-models ; https://poly.pizza/bundle/House-Plants-set-Kpj32c7VmF
- [L8] CC0 eyeball (BlendSwap): https://whatever.blendswap.com/blend/31370

Engines and formats
- [E1] Unity glTFast 6.7 features (clear coat partial on URP, transmission limits): https://docs.unity3d.com/Packages/com.unity.cloud.gltfast@6.7/manual/features.html
- [E2] Unity URP Complex Lit (clear coat, about twice the cost): https://docs.unity3d.com/6/Documentation/Manual/urp/shader-complex-lit.html
- [E3] Unity URP Lit (detail inputs, metallic map): https://docs.unity3d.com/Manual/urp/lit-shader
- [E4] Unreal glTF extension list (Interchange parser): https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/GLTFCore/GLTF__EExtension
- [E5] three.js GLTFLoader extensions: https://threejs.org/docs/pages/GLTFLoader.html
- [E6] Blender glTF add-on (emissive strength, clear coat, transmission): https://www.blender.org/manual/en/addons/import_export/scene_gltf2.html ; https://www.khronos.org/blog/blender-gltf-i-o-support-for-gltf-pbr-material-extensions
- [E7] Godot 3D import formats (no glTF extension table published): https://docs.godotengine.org/en/stable/tutorials/assets_pipeline/importing_3d_scenes/available_formats.html
- [E8] UnityGLTF (emissive strength supported): https://github.com/prefrontalcortex/UnityGLTF
