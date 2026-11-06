# Photoreal textures at 2048 px and up: research and plan

Written 2026-11-06. Research and write-up only: no app code, models, installs or GPU jobs were changed or run. The only measurements are read-only CPU probes of the files under `bakeoff/out/convert/`.

Read first: STATUS.md decisions D1 (TRELLIS.2 default, Hunyuan3D-2mv alternative), D3 (conversion in native ComfyUI mesh nodes, Blender for the rest) and D4 (texturing is mesh-first; the turnaround is for approval and Hunyuan input only). `docs/PHOTOREAL-PLAN.md` already covers presentation, a GAN upscaler, detail normals, material identity, glow, glass, eyes and fur; this report does not repeat those phases. It goes after one question: why the colour texture stays soft and blotchy when the texture size goes up, and what free, local tools would fix it.

## Summary

1. **The texture size is not the limit. The source is.** On the knight, three ceilings sit close together: the concept picture gives about 5.2 px per cm on the side it shows, the TRELLIS.2 colour field holds about 5.7 cells per cm (1024 grid) or 8.5 (1536 grid), and a 2048 atlas gives about 7.5 texels per cm. A 4096 atlas (about 15 texels per cm) only samples the same field more finely. Measured: the knight's 2048 base colour differs from a copy shrunk to 1024 and blown back up by only 47 dB PSNR, which is close to invisible. Half of its texels carry no detail of their own.
2. **"Blotchy" comes from the colour field, not the bake.** The bake node samples a sparse voxel colour field trilinearly; colour detail smaller than two or three cells is smeared, and every cell gets one PBR value, so areas of one colour show as soft patches at close range.
3. **The far side has even less.** TRELLIS.2 builds from one picture; the back and sides are invented by the model, at the same cell size but with less detail.
4. **The fix is new detail, not more texels.** Every option that helps either paints more pixels per centimetre of surface (high-resolution views of the model, projected back) or adds detail of a known scale (CC0 detail normals and materials). Upscalers and bigger atlases only sharpen edges.
5. **The app already has the right machinery for this.** The face pass (`vgac/face.py`) renders the model, has an image model redraw it sharp, and projects the result back through the UVs. It raised face sharpness from about 0.56 to 1.65. Extending that to the whole model is the top recommendation (section 4), ahead of new model downloads.

---

## 1. Our current method, step by step

### 1.1 The 3D step (`backend/vgac/stages/mesh.py`, `backend/vgac/graphs/trellis.py`, same graph as `bakeoff/convert.py`)

One ComfyUI job on instance A (RTX 3060), node for node:

| # | Node | What it does to the texture | Settings |
|---|---|---|---|
| 1 | `LoadImage`, `InvertMask`, `ImageCropToMask` | Cuts the subject out and fits it in **1024 x 1024** with a 1.1 pad, black background | fixed |
| 2 | `CLIPVisionLoader` (DINOv3 ViT-L), `Trellis2Conditioning` | Image features the model conditions on | fixed |
| 3 | Structure, shape and `Trellis2UpsampleStage` samplers | Shape at 512, cascaded to `upsample` (1024 default; 1536 for the Photoreal recipe in `styles.yaml`) | 12 + 20 + 12 steps |
| 4 | `Trellis2TextureStage`, `KSampler` (12 steps, CFG 1.0), `VaeDecodeTextureTrellis` | Predicts a **sparse voxel colour field** with base colour, metallic and roughness per cell. In the ComfyUI source (`comfy_extras/nodes_trellis2.py`) its resolution is `coord_resolution * 16`, so **1024 or 1536 cells across the longest side** | seed + 1 |
| 5 | `RemeshMesh` (solid, 768, 20 smoothing passes) | Closes the dense shell. Not used for colour | |
| 6 | `DecimateMesh` | To the triangle budget (20,000 for a PC humanoid) | midpoint |
| 7 | `UnwrapMesh` (segmenter `pec`) | Atlas at the texture size; the knight gets **677 islands** at 20k triangles | padding 2 x max(4, size/256), capped at 16 |
| 8 | `BakeTextureFromVoxel` | For each texel: back-project to the dense mesh, then sample the voxel field **trilinearly over occupied cells**, nearest cell where none of the 8 corners is filled (`comfy_extras/nodes_mesh_postprocess.py`, `_sample_voxel_attrs_per_texel`) | texture_size = platform or recipe size |
| 9 | `BakeNormalMapFromMesh`, `BakeAmbientOcclusion` | Normal and AO from the dense remeshed shell; AO at half size | cage 0.05, AO 64 samples |
| 10 | `ApplyTextureToMesh`, `SaveGLB`, `SaveImage` | GLB plus base colour, metallic, roughness, normal, AO PNGs | 8 bit |

Then in Python on the host:

- `normalfix.fix_download`: flips TRELLIS's upside-down green channel once.
- `finish_maps`: grows every map max(8, size/128) texels into empty space (16 at 2048); records `atlas_used`, gutter and UV hash.
- `texcolour.build_with_rerolls`: compares the base colour's brightness with the picture's; under 0.5 of it, builds again with the next seed (up to 3 builds) and keeps the closest.
- The silhouette audit and hair ranking do not touch textures.

Hunyuan3D-2mv (the alternative engine) gives shape only. It has no texture path in the app.

### 1.2 The clean-up step (`backend/vgac/stages/prep.py`, `backend/vgac/blender/rebake.py`)

1. Blender prep: facing, scale, pivot, weld, optional symmetrize, smooth shading.
2. **Rebake** when prep changes the surface (symmetrize, decimation back to the budget, the head-weighted decimation) or on request: Cycles "selected to active" from the 3D step's low-poly model and its maps onto the final mesh, emission bakes, 4 samples, source images sampled `Linear`, no cage, extrusion 1 percent and ray distance 2 percent of the diagonal, margin `EXTEND`, optional repack with `pack_islands(shape_method="CONCAVE")`.
3. **Texture pass** (`texture_pass`): light score (correlation of base-colour brightness with AO and with facing up); `imaging.delight` when the look says remove: one global least-squares fit of log brightness against AO and the up component of the normal, divided out; `clamp_albedo` to 30 to 240 sRGB; `sharpen_metallic` toward 0 or 1; roughness floor 0.04.
4. **Face detail pass** (humanoids): render the head orthographically, have Qwen-Image-Edit redraw it sharp (or use a 1024 close-up when the guard passes), fit on nose, eye line and mouth, project back through the UVs on the face only; faces under 256 texels across get bigger islands and a rebake. Knight: 450 texels across the face (257 before), sharpness 1.29 to 1.64; a later run 0.56 to 1.65.
5. **Hair pass**: hair mask by colour vote, more texels for hair in the same relayout, strands painted along a flow field; optional second hair material at half the platform size.
6. **Look**: shrink maps to the recipe's size; palette limits for crisp looks.

### 1.3 Size caps (`backend/vgac/data/categories/*.yaml`, `styles.yaml`, `reference.py`)

| Platform | Humanoid | Prop | Vehicle, environment |
|---|---|---|---|
| mobile_low | 1024 | 512 | 1024 |
| pc_mid | 2048 | 2048 | 2048 |
| high (Showcase) | 4096 | 4096 | 4096 |

Recipe caps only go down (256 for pixel and retro looks, 512 for low-poly). Photoreal raises `upsample` to 1536 and keeps the platform size.

### 1.4 Measured on the bake-off files (read-only probe, CPU)

Script: a scratch probe (not in the repo) that loads each `bakeoff/out/convert/*/model.glb` and its base colour, rasterises the UV layout, and measures texel density per triangle, texels per TRELLIS cell, atlas share and how much detail the base colour holds above half and quarter size (PSNR of the map against itself shrunk and blown back up, bicubic, inside the islands with a 3-texel border left out; higher means less real detail).

| Build | Texture | Islands | Atlas used | Texels per cm (if 1.8 m tall) | Texels across one colour cell | PSNR vs half size | PSNR vs quarter size |
|---|---|---|---|---|---|---|---|
| Knight, 20k tris | 2048 | 677 | 0.55 | 7.2 to 7.7 | 1.3 | **47.1 dB** | 40.5 dB |
| Knight, 5k tris | 1024 | 251 | 0.57 | 3.8 to 4.1 | 0.7 | 40.7 dB | 36.0 dB |
| Wolf, 20k tris | 2048 | 706 | 0.58 | (not a person) | 1.1 | 43.2 dB | 37.9 dB |
| Chest, 5k tris | 1024 | 130 | 0.72 | (not a person) | 0.4 | 42.3 dB | 38.5 dB |

The concept picture behind the knight is 934 px tall in its 1024 crop: about **5.2 px per cm**, and only for the side it shows.

What the numbers say:

- At 1024 the atlas is the limit (0.7 texels per cell, and halving it loses visible detail: 40.7 dB). At 2048 the atlas has passed the source (1.3 texels per cell, 47 dB). At 4096 it would be about 2.6 texels per cell: three quarters of the texels would be interpolated. This is why 2048 and up looks soft rather than sharp.
- Texel density is even across the model (90th over 10th percentile 1.08 to 1.19), so the unwrap is not the cause of local blur.
- The bake-off builds used padding 1. The app now asks for 16 at 2048, plus the face and hair relayouts, which is where the app's `atlas_used` of about 0.37 to 0.40 comes from. With 677 islands, the gutter around each island costs a large share of the sheet. That loses about 20 percent of linear texel density against the bake-off builds, but while the source is the ceiling it does not show; it starts to matter once the source has more detail (section 4).

### 1.5 Weak points, in order of how much they cost

| # | Weak point | Where | Effect |
|---|---|---|---|
| W1 | Colour detail is capped by the TRELLIS colour field (1.76 mm cells on a 1.8 m figure at 1024, 1.17 mm at 1536) and smeared by trilinear sampling and the texture VAE | `VaeDecodeTextureTrellis`, `BakeTextureFromVoxel` | Soft at any size over about 2048; worse on big assets (a 4 m vehicle at 1024 cells has 4 mm cells) |
| W2 | One picture at 1024 px is the only source of real detail, and only for the side it shows | `ImageCropToMask` 1024, single view (D4) | Back and sides are invented and softer; the turnaround views, which hold the other sides, are never used for colour |
| W3 | One PBR value per cell; metallic and roughness are coarse guesses | TRELLIS texture stage | Patchy shine, metal bleeding at edges (the app already sharpens metal) |
| W4 | Light from the picture ends up in the colour; the de-light is one global fit against AO and facing-up | `imaging.delight` | Removes broad top-light only; leaves key-light direction, cast shadows, rim light and highlights. Photoreal concepts are already asked for even light, which helps more than the fix |
| W5 | Second resampling on rebake: low-poly atlas to final atlas through Cycles, `Linear`, 4 samples | `blender/rebake.py` | About half a texel of extra blur whenever prep rebakes (head-weighted decimation does this on every eligible humanoid) |
| W6 | Many small islands and large gutters | `UnwrapMesh` `pec`, padding 16 | atlas_used 0.37 to 0.40 in the app against 0.70 to 0.85 that hand-packed game assets reach; costs 20 to 30 percent linear density once the source is sharp |
| W7 | No micro detail | Normal map baked from the dense TRELLIS shell | Nothing finer than about 2 mm on a person: no pores, weave, grain; this is most of what "photoreal up close" means (PHOTOREAL-PLAN phase 4 covers it) |
| W8 | Dark-colour seeds | TRELLIS texture stage | Already caught by `texcolour` rerolls |
| W9 | No texture on the Hunyuan3D path | `_hy3d` | The alternative engine is shape only |

---

## 2. How the industry does it (checked 2026-11-06)

"Verified" means read on the primary page (paper, repository README or LICENSE, model card) during this research. "Not verified" means secondary sources or unread pages; treat those as leads, not facts.

### 2.1 The pattern everyone converged on

1. **Shape first, then texture in image space.** The open 3D generators that paint well (Hunyuan3D-2.1, Step1X-3D, MV-Adapter's texture pipeline, UniTEX) do not trust a 3D colour field for fine detail. They render the finished mesh's depth, normals and positions from 6 to 12 cameras. An image diffusion model then generates those views, conditioned on that geometry and on the reference picture. The views are projected back into UV space, weighted by how squarely each camera sees each texel, and what no camera saw is filled.
2. **Super-resolution on the views, not on the atlas.** Hunyuan3D-2.1 Paint makes Real-ESRGAN x4plus a required download for its views; MV-Adapter's texture pipeline uses Real-ESRGAN x2plus. Upscaling a view keeps detail consistent across neighbouring surfaces; upscaling a UV atlas does not (PHOTOREAL-PLAN 3.2 already rules that out).
3. **Unseen areas are inpainted.** MV-Adapter uses LaMa for holes in views; Paint3D and TEXGen do it in UV space with a model conditioned on a position map.
4. **PBR comes from a separate estimator or a PBR-aware painter.** Hunyuan3D-2.1 Paint (built on MaterialMVP and RomanTex) paints albedo and metallic-roughness views together. The others paint RGB, then estimate albedo, roughness and metal (Marigold-IID, Material Anything).
5. **Light is kept out at the source.** Photogrammetry practice says the same as our own bake-off: flat, even light in the input matters more than any later fix. Agisoft's Remove Lighting states that "delighting in strong shadows is not supported", and Unity's 2017 De-Lighting tool needed AO, normals and bent normals.
6. **Fine detail comes from libraries at a known scale.** Micro detail (pores, weave, grain) is layered from tileable materials or detail normals, not generated per asset (PHOTOREAL-PLAN phase 4).
7. **Studios.** GDC 2026 State of the Industry (January 2026): 36 percent of respondents use generative AI at work, mostly outside art. Visual and technical artists are the most negative (64 percent think it harms the industry). No solid published case study of AI mesh texturing in production was found. Adobe Substance 3D Sampler has offered text-to-texture for tileable materials since 2024.

### 2.2 Texturing and projection models

| Tool | What it is | Version, date | Licence (commercial) | VRAM (stated) | PBR | ComfyUI | Fit for us |
|---|---|---|---|---|---|---|---|
| TRELLIS.2 texture stage (Microsoft) | Native 3D "O-Voxel" PBR attributes; also a shape-conditioned texturing script (`example_texturing.py`) | 4B; paper arXiv 2512.14692, 16 Dec 2025 (verified) | MIT (verified). The official repo's nvdiffrast and nvdiffrec dependencies carry NVIDIA licences (not verified; the native ComfyUI nodes we use do not need them) | README: "at least 24GB"; runs on our 3060 through the native int8 nodes | Base colour, metallic, roughness, opacity | Native nodes (in use) | In use. The paper confirms texels are filled by trilinear interpolation of neighbouring voxels on a 512, 1024 or 1536 grid (1536 by test-time scaling). Base layer only |
| Hunyuan3D-2.1 Paint (Tencent) | Multi-view PBR diffusion from mesh plus picture, baked to UV; built on RomanTex and MaterialMVP | Released 13 Jun 2025; report arXiv 2506.15442 (verified) | Tencent Hunyuan 3D 2.1 Community Licence: does not apply in the EU, UK or South Korea; licence needed over 1 million monthly users; outputs may not train other models (verified) | About 21 GB for texture (6 views, 512); 29 GB with shape (verified) | Albedo plus metallic-roughness | visualbruno/ComfyUI-Hunyuan3d-2-1, ComfyUI-3D-Pack (not verified) | **No.** Twice our VRAM, and the territory clause matters now that the app will be shared |
| Hunyuan3D-2.0 Paint and Delight | Multi-view RGB painter plus a 1.3B image delighter | 21 Jan 2025 | Assumed the same Tencent community terms as 2mv (not verified) | 16 GB with shape; low-VRAM mode exists | RGB only | kijai/ComfyUI-Hunyuan3DWrapper | No: RGB only, tight on VRAM, same territory clause |
| Hunyuan3D 2.5, 3.0, Omni | 2.5 and 3.0 are hosted only; Omni (arXiv 2509.21245, Sep 2025) is controllable geometry | 2025 | n/a | n/a | n/a | n/a | Nothing usable locally for texture |
| MV-Adapter (+ texture pipeline) | Multi-view adapter for SDXL or SD2.1 with geometry conditioning; text or image to texture with Real-ESRGAN and LaMa | Texture pipeline 15 May 2025; ICCV 2025 (verified) | Code Apache-2.0; SDXL and SD2.1 weights CreativeML OpenRAIL(++)-M, commercial with use restrictions | About 14 GB image-to-multiview SDXL; "sd21" variant for under 10 GB (verified) | RGB only | ComfyUI-MVAdapter (views only, updated 26 Jun 2025); texturing via ComfyUI-3D-Pack | The best licence-clean ready-made projector. SDXL is tight on 12 GB and SD2.1 looks dated. Needs its own environment or 3D-Pack; both are heavy installs |
| Step1X-3D (StepFun) | SDXL texture module from MV-Adapter with texture sync | 13 May 2025 (verified) | Apache-2.0 | 27 to 29 GB geometry plus texture | Not stated | No | No, as packaged |
| UniTEX | FLUX LoRA texturing plus a large texturing model, de-light training code | arXiv 2505.23253; CVPR 2026 | Repo Apache-2.0; if the base is FLUX.1-dev, non-commercial (not verified) | Not stated | Not stated | No | Watch; check the base model |
| SeqTex | Generates the UV map with a video model (Wan2.1) | Code 12 Dec 2025 | Repo licence not confirmed | Tested on 80 GB | Not stated | No | No |
| LaFiTe (arXiv 2512.04786), NaTex (arXiv 2511.16317) | 3D-native colour-field texturing | Dec and Nov 2025 | No public weights found | n/a | n/a | No | Watch |
| Material Anything (3DTopia) | PBR for any mesh: albedo, roughness, metallic, bump | Weights Jun 2025; CVPR 2025 (verified) | MIT; SD base weights carry their own licence | Not stated (renders at 512) | Yes | No | Worth a test later as a PBR pass; VRAM unknown |
| Paint3D | 2K lighting-free UV textures with a UV-position ControlNet | Code 26 Apr 2024 | Apache-2.0 | Not stated | Albedo | No | Dated; its UV-space inpainting idea is useful |
| TEXGen | Feed-forward UV-space model | Dec 2024 | No licence stated, so all rights reserved | 24 GB | Albedo | No | No |
| SyncMVD | SD1.5 with depth ControlNet, synchronised views | 2023 | MIT | Not stated | RGB | No | Fits, but dated |
| TexGaussian, Make-It-Real, Meta 3D TextureGen | Feed-forward PBR; material assignment by GPT-4V; Meta paper | 2024 to 2025 | Not verified. Make-It-Real is MIT but needs a hosted model; TextureGen has no code | n/a | n/a | No | No |
| StableProjectorz | Free desktop projection painter on Forge or A1111, keeps existing UVs; 3D mode with TRELLIS | v2.4.5; AGPL-3.0 since Jan 2026 (not verified) | AGPL tool; textures follow the models used | GTX 1080 or better | Not confirmed | No | A manual touch-up tool for experts outside the app, not something to automate |

### 2.3 De-lighting and material estimation

| Tool | Gives | Date | Licence | VRAM | Fit |
|---|---|---|---|---|---|
| Marigold-IID Lighting v1-1 (prs-eth) | Albedo, diffuse shading, non-diffuse residual | Journal extension May 2025 | CreativeML Open RAIL++-M: commercial, with use restrictions passed on (card; also PHOTOREAL-PLAN [R2]) | SD2 class, a few GB; best near 768 px | **Good.** Run on the picture before the 3D step, or per view in a repaint |
| Marigold-IID Appearance v1-1 | Albedo, roughness, metallic | same | same | same | Good second opinion for roughness and metal |
| StableDelight (Stable-X) | Removes specular highlights only | 2024 | Code Apache-2.0; weight licence not stated on GitHub | Small | Partial: highlights only, not shadows |
| Hunyuan3D-Delight-v2-0 | Image delighter | Jan 2025 | Tencent terms assumed | Small | Territory clause |
| DiffusionRenderer (NVIDIA, Cosmos) | Inverse rendering to base colour, normal, roughness, metal | 12 Jun 2025 | Code Apache-2.0; NVIDIA Open Model Licence weights (terms not verified) | 16 GB minimum, 48 recommended | No |
| RGB-X (Adobe) | Albedo, normal, roughness, metal | 2024 | Adobe Research Licence, non-commercial | | Excluded |
| IntrinsicAnything | Albedo and specular | ECCV 2024 | Weights licence not found | | Excluded until verified |

### 2.4 Super-resolution and inpainting

| Tool | Licence | Notes | Fit |
|---|---|---|---|
| Real-ESRGAN x4plus, x2plus | BSD-3-Clause (verified) | Light; sharpens edges, adds little real detail | Fine on rendered views; low value on the atlas |
| HAT, SwinIR | Apache-2.0 (PHOTOREAL-PLAN [U1][U3]) | Same role | Same |
| SeedVR2 (ByteDance) | Weights Apache-2.0 (PHOTOREAL-PLAN [U4]) | The node pack is already installed on instance A; its weights are not on disk | Strong for views; 3B is tight on 10 to 12 GB even with tiling |
| FLUX.2 klein 4B | Apache-2.0 (9B is non-commercial) | Already installed (`flux-2-klein-4b-fp8`) | Commercial-safe img2img or tile refiner |
| Z-Image (base and Turbo), Qwen-Image-Edit-2511 | Apache-2.0 (`models.yaml`) | Already installed; Qwen edit already redraws the face | The refiner for a view repaint, no download |
| Z-Image-Turbo-Fun-Controlnet-Union-2.1 | Licence to confirm on its card before use | Already on disk in `models/model_patches`; depth and edge conditioning | Lets a redraw follow the model's own depth and edges exactly |
| LaMa | Apache-2.0 (verified) | Trained at 256, works to about 2K | Holes no view saw |
| SUPIR | Non-commercial | Also needs 12 GB plus 16 GB for LLaVA | Excluded |
| FLUX.1-dev | Non-commercial licence; its clauses on outputs conflict | | Excluded |
| 4x-UltraSharp (V1 and V2) | CC BY-NC-SA 4.0 | **`4xUltrasharp_4xUltrasharpV10.pth` is in the shared `upscale_models` folder; the app must never pick it** | Excluded |
| Other upscalers on disk (4xNomos8k_atd_jpg, 4xLSDIR, Remacri, NMKD, 4xRealWebPhoto, 4xFaceUpDAT, SwinIR BSRGAN) | Each needs its card read; some Nomos models are CC BY 4.0 (secondary) | | Do not use until each licence is read |

### 2.5 Texel density, packing and baking practice

- **Density targets** (Maxon Cinema 4D docs; Rebusfarm guide): 10.24 px/cm (1024 px/m) for first person or hero assets, 5.12 px/cm for third person or background, 2.56 or less for top-down. These are examples, not rules. Our knight at 2048 gets about 7.5 texels/cm on the body, between the two. The voxel field and the picture hold only about 5 to 6 px/cm, so today's real detail is at the third-person level whatever the texture size.
- **Packing:** no published standard was found. Guides call 80 to 85 percent fill aggressive; Polycount threads put even density and mip-safe gutters ahead of raw fill. A working figure for automatic packs of organic models is 60 to 75 percent (our estimate, not from a source).
- **Padding:** about 8 px at 1K, 16 at 2K, 32 at 4K between islands (Polycount; Substance dilation docs; the numbers combine several sources). Our 16 at 2048 is normal. The cost comes from the number of islands, not the gutter size.
- **Baking:** a cage (or extrusion plus max ray distance) keeps rays continuous across hard edges. Blender's `BakeSettings` has `use_cage`, `cage_extrusion`, `max_ray_distance`, `margin`, and `margin_type`. Its default, `ADJACENT_FACES`, fills the margin from the neighbouring face across the seam, which hides seams better than `EXTEND`, the one we use.
- **Blender packing:** `pack_islands` with Shape Method Exact (Concave), rotation on, and Average Islands Scale first. Blender 5.1 (17 Mar 2026) added Minimum Stretch (SLIM) and No Flip to the geometry-nodes UV Unwrap node, and a custom pack region to the Pack UV Islands node. The Blender 5.2 release notes could not be read.

---

## 3. Best free options for us, ranked

Only options that are free for commercial game use and run locally. Gain is the expected improvement in how sharp and real the colour looks up close at 2048 and 4096. Effort is S (a day or less), M (a few days) or L (a week or more). "Where" is the step it belongs in.

| Rank | Option | Gain | Effort | Licence | VRAM and card | Where | New download |
|---|---|---|---|---|---|---|---|
| 1 | **Whole-model view repaint**: generalise the face pass to the whole model (steps listed under the table) | High: raises real detail from about 5 px/cm toward 10 or more where the cameras see squarely, on all sides, not only the front | M to L (the renderer, projector, fit check and tone merge exist in `vgac/face.py`) | Apache-2.0 (Qwen edit, Z-Image); Union ControlNet licence to confirm | Qwen edit fp8 already runs on the 3080 for the face pass; Z-Image fits either card | Clean-up step: a new pass after the rebake and before the face pass (the face pass then refines the face on top) | None for Qwen edit; the Union ControlNet is already on disk |
| 2 | **TRELLIS colour grid at 1536 for every smooth look on PC and Showcase** (today only Photoreal) | Medium: 1.5 times the colour cells per cm (5.7 to 8.5 on a person), and fewer blotches | S (a recipe value; the param already exists) | MIT | Already runs on the 3060 for Photoreal; "several times slower" (`reference.py` comment) | 3D step (recipe `mesh.upsample`) | None |
| 3 | **Bake fixes**: (a) rebake from the dense TRELLIS mesh and voxel field, or at least sample the source maps `Cubic` with more samples, so prep does not resample twice; (b) margin type `ADJACENT_FACES`; (c) fewer, larger islands (Blender seams plus Concave pack, Average Islands Scale) to lift `atlas_used` from about 0.4 to 0.6 or more | Low today; medium after rank 1, because it only matters once the source has detail: about 1.25 times the linear density and cleaner seams | S to M | Blender GPL (tool) | CPU | Clean-up step (`blender/rebake.py`, `blender/faceuv.py` packing) | None |
| 4 | **De-light the picture before the 3D step** with Marigold-IID Lighting, for Photoreal only: take its albedo layer, or divide out its diffuse shading at a set strength, before TRELLIS sees the picture | Medium for Photoreal. TRELLIS copies the picture's light into the colour; this removes key-light direction, cast shadows and highlights that the current AO fit cannot see | M | CreativeML Open RAIL++-M (commercial with use restrictions; record in the manifest as for Gemma) | SD2 class, fits either card | 3D step input (or the end of the concept step) | Yes, about 2 to 3 GB (estimate) |
| 5 | **CC0 detail normals and materials** (PHOTOREAL-PLAN phase 4, needs phase 3 regions) | High close up for skin, cloth, leather and metal; nothing at a distance | M | CC0 (ambientCG, Poly Haven) | CPU (Blender bake) | Clean-up step | A small CC0 shelf |
| 6 | **Super-resolution on the rendered views** inside rank 1 (Real-ESRGAN, HAT, or SeedVR2 if its weights are fetched) | Low to medium on top of rank 1 | S once rank 1 exists | BSD-3 or Apache-2.0 | Fits; SeedVR2 3B is tight | Inside rank 1 | Small (Real-ESRGAN, HAT) or about 7 GB (SeedVR2) |
| 7 | **MV-Adapter texture pipeline** as a ready-made alternative to rank 1 | High in principle, but its SDXL views are 768 px and RGB only, and the install (its own environment or ComfyUI-3D-Pack) is heavy | L | Apache-2.0 code, OpenRAIL++-M SDXL | About 14 GB for SDXL (over our cards); SD2.1 under 10 GB | New texture step or clean-up | Yes, several GB |
| 8 | **Material Anything** for metal and roughness after a repaint | Medium for metal and shine; cost unknown | M | MIT code; SD base licence | Not stated | Clean-up | Yes |
| 9 | **GAN upscale of the baked atlas** (PHOTOREAL-PLAN phase 2) | Low: crisper edges on the blotches. Our measurement shows the 2048 atlas already holds only half-size detail | S | BSD-3 or Apache-2.0 | Fits | Clean-up | Small |

How rank 1 would work:

1. Render the final model (base colour, depth and normals) from 8 to 12 fixed cameras at 1024 to 1536 px.
2. Redraw each render sharp. Either Qwen-Image-Edit-2511 with the picture as the second image, or Z-Image with the Union ControlNet following depth and edges.
3. Keep colour and broad tone from today's texture and take only fine detail from the redraw (the face pass's `merge_detail` rule).
4. Project back through the UVs, weighted by how squarely each camera sees each texel, and feather between views.
5. Fill what no view saw from today's texture.

Ruled out: Hunyuan3D-2.1 Paint (21 GB; EU, UK and South Korea excluded), Hunyuan3D-2.0 Paint (RGB only; same terms), Step1X-3D and DiffusionRenderer (VRAM), TEXGen (no licence), SUPIR, FLUX.1-dev, RGB-X and 4x-UltraSharp (non-commercial), tiled diffusion on the atlas (seams; PHOTOREAL-PLAN 3.2).

### 3.1 How rank 1 fits with earlier decisions

- **PHOTOREAL-PLAN section 2.2** rated "detail pass on rendered views, projected back" as L effort and high risk (view seams, light baked into the projection, drift from the concept), expert-only at best. Since then the face pass has shipped the same loop on the face, and its code already answers most of those risks:
  - Render and projection share one fixed orthographic camera, so the detail cannot drift.
  - A registration guard refuses a redraw that moved.
  - Colour and broad tone stay from the existing texture and only fine lightness detail comes from the redraw, so there is no hue drift and no new light.
  - Edges are feathered.

  The remaining risks for the whole body are seams between views and invented detail that contradicts the picture. Plan step 2 below measures both before the pass becomes a default.
- **D4 (texturing is mesh-first; the turnaround is for approval and Hunyuan input only)** stays as it is, because the repaint redraws renders of the model itself. Passing a turnaround view as the second picture for the matching camera (as the face pass passes the painted head) would extend D4. That is the owner's call, so the plan treats it as an option to test, not a default.
- **D3** is unchanged: like the face pass, the repaint runs in ComfyUI (image model) and in numpy or Blender (render and projection).

---

## 4. Recommended plan

Each step is a small, separate change with a number that says whether it helped. The tests are described here, not run; each needs the owner's yes because it uses the GPUs. They use only the bake-off subjects under `bakeoff/` (knight, wolf, chest), never `library/projects`.

New measures used below (all CPU and cheap; they could live in `imaging.py`):

- **Real detail (dB):** PSNR of the base colour against itself shrunk to half size and blown back up, inside the islands. Lower means more real detail. The knight at 2048 today: 47.1 dB. A texture that uses its full size sits near 40 dB or below.
- **Texels per colour cell:** atlas texel density times the TRELLIS cell size. Above about 1.5, the atlas is bigger than the source.
- **View sharpness:** `face.sharpness` (standard deviation of the fine detail) on 8 fixed renders of the whole model at 1024 px, with the same cameras before and after.
- **Seam step:** mean colour difference across UV seams, measured on the 3D surface by sampling both sides of each seam edge, before and after.

### Step 1. Raise the cheap ceiling (ranks 2 and 3a; S)

Test: build the knight, wolf and chest twice with the same seed, at `upsample` 1024 and 1536, each at 2048 and 4096, through `bakeoff/convert.py` on instance A. That is 4 builds per subject, roughly 3 to 10 minutes each. Then rebake one of each with `Linear` and with `Cubic` sampling in `blender/rebake.py`.

Helped if:

- real detail at 4096 drops by at least 3 dB with 1536 (more detail);
- view sharpness rises by at least 15 percent;
- the texture colour ratio (`texcolour`) stays within 0.9 to 1.1 of the 1024 build;
- the owner accepts the extra time (record the seconds).

If 1536 helps, make it the recipe default for smooth looks on PC and Showcase. If 4096 shows no gain over 2048 with either grid, leave the Showcase colour map at 2048 and spend the memory elsewhere.

### Step 2. Prototype the whole-model view repaint in `bakeoff/` (rank 1; M)

This is a bake-off script, not app code:

- Reuse the renderer and projector in `vgac/face.py`.
- Use 8 cameras (four sides and four three-quarter views, slightly raised), plus top and bottom for props.
- Variant A: Qwen-Image-Edit-2511 redraw, with the "keep everything the same, sharper" wording the face pass uses.
- Variant B: Z-Image with the Union ControlNet on depth and edges, after its licence card is read.
- Keep colour from today's texture and take detail from the redraw.
- Weight each view by the cosine of the viewing angle to the power 4, feather 16 texels, and let texels no view sees keep today's colour.

Helped if, on all three subjects:

- view sharpness is at least 1.5 times today's on every camera, back views included;
- real detail at 4096 is at least 5 dB lower;
- the seam step is no more than 10 percent above today's;
- the texture colour ratio stays within 0.9 to 1.1;
- the silhouette audit is unchanged (the shape is not touched);
- by eye on a contact sheet: no doubled features, no invented faces or text, no visible view borders at 1:1 or at 1/8 size.

If it passes, it becomes a clean-up pass ("Sharpen all sides"), on by default for Photoreal and Painted on PC and Showcase, with a check that reports view sharpness before and after.

### Step 3. Keep the picture's light out (rank 4; M; Photoreal only)

Test: run Marigold-IID Lighting on the photoreal bake-off concepts (`bakeoff/out/recipes/`), and build each one twice, from the original and from the de-lit picture, with the same seed.

Helped if:

- the clean-up step's light score is under its limit without the AO-fit de-light running, or the fit's strength falls by at least half;
- the texture colour ratio stays within 0.85 to 1.15 (de-lighting brightens shadows);
- under the viewer's preview light (PHOTOREAL-PLAN phase 1), the back and underside are no darker than the front.

If it is adopted, record the RAIL use terms in `models.yaml` and the manifest.

### Step 4. Then spend texels where they now count (ranks 3c and 5; M)

Only after step 2 has put real detail into the maps: use fewer, larger UV islands and a Concave pack with Average Islands Scale in the clean-up rebake, then add the CC0 detail normals of PHOTOREAL-PLAN phase 4.

Helped if:

- `atlas_used` rises from about 0.4 to at least 0.6, with the gutter still at least 8 texels at 2048;
- body texel density rises by at least 20 percent;
- the seam step is no worse;
- with detail normals, a 1:1 crop of leather, cloth and metal shows grain at a real-world scale the owner accepts by eye.

---

## 5. Sources

Dates are publication or release dates as stated on the source. All were read or checked on 2026-11-06 unless marked otherwise.

- TRELLIS.2: github.com/microsoft/TRELLIS.2; paper arXiv 2512.14692 (16 Dec 2025); ComfyUI tutorial docs.comfy.org/tutorials/3d/trellis2. Local source read in the instance A install: `comfy_extras/nodes_trellis2.py` (texture resolution `coord_resolution * 16`) and `comfy_extras/nodes_mesh_postprocess.py` (`_sample_voxel_attrs_per_texel`, trilinear over occupied cells).
- Hunyuan3D-2.1: github.com/Tencent-Hunyuan/Hunyuan3D-2.1 (13 Jun 2025), `hy3dpaint/README.md`, LICENSE; report arXiv 2506.15442 (19 Jun 2025). MaterialMVP arXiv 2503.10289; RomanTex arXiv 2503.19011.
- Hunyuan3D-2.0: github.com/Tencent-Hunyuan/Hunyuan3D-2 (21 Jan 2025). Hunyuan3D-Omni arXiv 2509.21245 (25 Sep 2025).
- MV-Adapter: github.com/huanngzh/MV-Adapter (texture pipeline 15 May 2025; ICCV 2025); ComfyUI-MVAdapter (26 Jun 2025).
- Step1X-3D: github.com/stepfun-ai/Step1X-3D (13 May 2025).
- UniTEX arXiv 2505.23253; SeqTex (code 12 Dec 2025); LaFiTe arXiv 2512.04786 (4 Dec 2025); NaTex arXiv 2511.16317.
- Material Anything: github.com/3DTopia/MaterialAnything (weights Jun 2025; CVPR 2025).
- Paint3D (code 26 Apr 2024); TEXGen (Dec 2024); SyncMVD (2023); Make-It-Real (NeurIPS 2024); Meta 3D TextureGen arXiv 2407.02430.
- StableProjectorz: stableprojectorz.com (v2.4.5; licence not verified).
- Marigold-IID: huggingface.co/prs-eth/marigold-iid-lighting-v1-1 and marigold-iid-appearance-v1-1 (Open RAIL++-M).
- StableDelight and StableNormal: github.com/Stable-X (2024).
- DiffusionRenderer: github.com/nv-tlabs/cosmos-transfer1-diffusion-renderer (12 Jun 2025).
- RGB-X: Adobe Research Licence (PHOTOREAL-PLAN [R5]).
- Real-ESRGAN: github.com/xinntao/Real-ESRGAN (BSD-3-Clause). LaMa: github.com/advimman/lama (Apache-2.0). SUPIR: github.com/Fanghua-Yu/SUPIR (non-commercial). FLUX.1-dev Non-Commercial Licence v1.1.1. FLUX.2 klein 4B (Apache-2.0). 4x-UltraSharp (CC BY-NC-SA 4.0).
- Texel density: Maxon Cinema 4D documentation; Rebusfarm texel density guide. Padding: Polycount wiki and threads; Adobe Substance dilation docs. Photogrammetry de-lighting: Agisoft Metashape manual ("Remove Lighting"); Unity De-Lighting Tool (2017).
- Blender: docs.blender.org/api `bpy.types.BakeSettings` and `bpy.ops.uv.pack_islands`; Blender 5.1 release notes (17 Mar 2026). The 5.2 notes and the manual pages returned errors.
- GDC 2026 State of the Industry report (January 2026).
- Our own: `bakeoff/out/convert/*` (the probe in section 1.4), STATUS.md (face pass numbers), `docs/PHOTOREAL-PLAN.md`, `docs/GREAT-ASSETS-PLAN.md` 3.5, `docs/RECIPES-BAKEOFF.md` section 7.

### Could not verify

- Hunyuan3D-2.0 and Omni licence texts.
- StableNormal and StableDelight weight licences.
- The Z-Image-Turbo-Fun-Controlnet-Union licence.
- Licences of the upscalers already on disk, other than 4x-UltraSharp.
- TRELLIS.2 texturing VRAM below 24 GB outside the native nodes.
- MV-Adapter's UV output size.
- Material Anything's VRAM.
- The date StableProjectorz moved to AGPL.
- The exact FLUX.2 klein release date.
- The Blender 5.2 release notes.
- Any published UV fill-rate standard.

## 7. Trial results (2026-11-06, bake-off files only)

Scripts: `bakeoff/texture_trials.py`, `texture_measure.py`, `repaint_trial.py`, `face_closeup_trial.py`, `keystone_fit.py`,
`bald_knight.py`, `head_build.py`, `face_proportions.py`. Outputs under `bakeoff/out/texture_trials/` and
`bakeoff/out/repaint_src/`.

**Step 1, colour grid 1536 against 1024: does not pass as a default.**

| Build | Detail dB (lower = more) | View sharpness | Seconds |
|---|---|---|---|
| knight 1024 / 2048 | 44.05 | 3.94 | 115 |
| knight 1536 / 2048 | 41.59 | 3.75 | 175 |
| knight 1024 / 4096 | 47.21 | 4.02 | 185 |
| knight 1536 / 4096 | 47.69 | 3.89 | 276 |
| wolf 1024 / 2048 | 44.79 | 1.98 | 191 |
| wolf 1536 / 2048 | 39.70 | 2.98 | 435 |
| chest 1536 / 2048 | not finished: over the 3060's memory, about 80 s per step, stopped after 21 min | | |

The knight gains nothing. The wolf's numbers gain, and its face is closer to the picture (grey forehead, dark eye
mask), but the finer grid redoes the whole colour pass: the body lost its dark saddle, gained pale streaks, and the eyes
turned orange. Kept as an option; its name and help now say so ("Finer colour and shape (slower)").

**Step 2, whole-model repaint (8 views, Qwen on B, merge_detail, projected back): helps, below its bar.**
Old knight build: detail 47.61 to 42.22 dB, view sharpness 1.23x to 1.54x, colour shift 1.1 of 255, one view rightly
dropped for moving. Fresh build (already sharper): 44.05 to 40.24 dB, 1.04x to 1.20x. Visibly crisper plate edges and
engraving; the 1.5x-on-every-view bar is not met. 7 minutes per model.

**Face close-up from the whole picture (stand-in for a user's face shot), haired knight, 4096, no relayout.** Lined
up automatically (wide scale search, then face.align). Sharpness 1.47 to 1.50 against 1.30 to 1.37 for the app's own
close-up, but the app's guard rejected both seeds (likeness 0.944 and 0.926 against 0.93; eye colour), and painted
results look nearly the same. The face's larger problems were elsewhere: the modelled fringe hides one eye, and the
fit fell back to the whole-face match at its scale limit (0.82).

**Proportions.** The 3D builder decides the face from a few patches of the picture (32-cell first grid; the head is
about 4 cells tall), so the lower face is its guess. Haired knight: eye to nose bottom matches the painting (115 against
114 px), mouth to chin 135 against 86. The keystone fit (eyes and mouth from the texture, nose bottom and chin from the
shape) lands every feature within 3 px, against a 71 px chin miss today, but stretching the painting to a wrong shape
makes the face heavier: the shape itself needs to change.

**Bald.** The bald knight's picture (app's bald wording) came back on a background of 1 to 2 instead of 0, and the
builder then built the whole square: inputs must carry alpha (they do in the app). Rebuilt with alpha:
- full body, bald: a smudged, reddish face; lower face still long;
- head and shoulders on their own: a real face in both shape and colour (brows, eyes, nose, lips, ears), the best face
  of every trial; its lower face is still longer than the painting's when eyes and nose are matched.
- The face finder takes the whole bald head as the face on a bald body (box from the crown to the neck, 607 px wide
  against about 300). The app's face pass runs on the bald body before the library hair goes on, so bald-route
  characters are likely affected. Not yet checked on an app build.

**Face reshape prototype (`bakeoff/face_reshape.py`, bald side views from step 2's own graph in `bakeoff/bald_sides.py`).**
Keystones read off the side outline the same way on picture and model (top of the head, nose tip, under the nose, chin
front, chin bottom, stopping at the neck before the collar), picture put on the model's scale by the top of the head and
the nose tip, then the model's face vertices moved up or down between the keystones (front of the head only, fading out
across the neck; the texture moves with the vertices).
- The mechanics work: misses go to about 0 to 1 px at the chin bottom and the face is not damaged, once only the big
  landmarks are used. Finer points (each lip, the fold under the lip) were misread and pulled the lip into a frown.
- Head-only bald model against the head-crop side view: chin bottom 23 px low (of a 1024 frame of head and neck), fixed
  to 0.3 px; outline match 0.922 unchanged. A small, clean change.
- Full bald body against the full-figure side view (cropped to the same share): already within 11 px; outline match 0.90.
- Lesson: step 2's side views come from the same small painted face as the 3D model and agree with the model more than
  with the front painting. The front painting is the reference that matters; reading the chin on it needs care (the high
  collar was read as the chin). Two of the four side views also came out wrong (the head "right" view faced left like
  "left"; one body view had a drawn checkerboard background).

**Head graft prototype (`bakeoff/head_graft.py`).** The bald head built on its own (`head_build.py`) put on the bald full
body (`bald_knight.py`): scaled so its top-to-chin equals the body's (its width then came out 0.97 of the body's head
width, so the two builds agree on size), tops and nose tips matched, cut at the body's neck. What tells the parts apart
is colour, not distance: the new head keeps its head and, below the chin, only skin (the neck, which runs down inside
the collar); the body drops its old skin above the cut and anything strictly inside the new head above the chin, so the
collar stays whole. Distance rules failed three ways first (old head showing at the back, the chin cut off, the collar
chewed). Result: a clean join hidden by the collar from front, three-quarter and side, the head the right size on the
figure, skin tone close to the painting's. Two meshes, two textures. Not yet: a head with hair, a character without a
high collar (the join would show), one texture atlas, rigging across the join.

**The head model's "double lip".** On the head model the texture has the red mouth band about 20 px (of a 1024 head
frame) below where the shape has its lips, and a second dark curve below that: the shadow under the lower lip, painted
low and dark enough to read as a second mouth. Placed by the painting (rows scaled from under the nose by the eye-to-nose
ratio), the mouth belongs where the shape's lips are: the shape is right, the colour field is shifted. The face pass
paints the painting over this, fitted to the shape, which is the fix.

**Guide lines for side views (`bakeoff/side_guides.py`).** Thin coloured lines across the front at the eyes, under the
nose, the mouth and the chin, then step 2's side view graph, two seeds each, with and without words asking to keep
them. The edit model copies the lines exactly (found at the same rows in every result) but does not put the features on
them: chin bottom off by 44 to 59 px with lines against 19 to 49 without. Not worth it; measuring and correcting after
(or redrawing a view that drifts) is the reliable route.

**Haired head graft.** The haired knight's head-and-shoulders crop (`head_build.py <picture> knighthead`, same 26 percent
crop as the bald one) built as a flat board twice (seeds 42 and 7: 2.4 cm and 0.2 cm deep), though its mask was clean.
A looser crop to the chest (`HEAD_CROP=0.42`, `knighthead42`) built properly, but its face is only partly better than
the full body's (both eyes, brows and nose show, still soft and older-looking), because the face is a smaller share of
the picture again. Side by side (`bakeoff/out/texture_trials/faces_compare.png`): bald head-and-shoulders is clearly the
best face, haired head-to-chest second, both full-body builds smudged. The graft itself needs two more fixes for hair,
not done: the face outline must be read from skin only (a bob reaches further forward than the nose; `side_keys` now
does this), and the nose finder lands in the hair on the haired bust (so the skin colour sample is wrong and the scale
came out 1.5 times too wide). Reading: the head-only build pays off on the bald route, where the head is built bald and
the hair comes from the library afterwards.
