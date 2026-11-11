# Texture fidelity plan: sharp textures that match the source picture

Written 2026-11-11. Builds on `docs/PHOTOREAL-TEXTURE-RESEARCH.md` (2026-11-06) and fresh web research the same day. This file is the shared brief for the three implementation lanes below.

## 1. What happens today (verified in code)

1. The 3D step crops the concept to 1024 x 1024 and gives TRELLIS.2 only DINOv3 features of it (`backend/vgac/graphs/trellis.py:25`). No pixel of the picture ever reaches the texture, except on the face.
2. TRELLIS predicts a sparse voxel colour field (1024 cells; 12 steps, CFG 1.0). `BakeTextureFromVoxel` samples it trilinearly into an atlas of about 677 small islands (`pec` unwrap, padding 16 at 2048, `atlas_used` 0.37 to 0.40).
3. Prep resamples that atlas again through Cycles (`blender/rebake.py`: `Linear`, 4 samples, `EXTEND`) on every head-weighted humanoid, a third time in the face relayout (`blender/faceuv.py:473`) and a fourth in the head graft (`blender/headjoin.py:163`).
4. Only the face pass (`vgac/face.py`, `stages/face_detail.py`) projects picture pixels through the UVs. It raised face sharpness from 0.56 to 1.65.
5. The only picture-versus-texture check is mean brightness (`texcolour.py`). Nothing measures hue, sharpness or likeness on the body.
6. Unity imports base colour with default block compression, bilinear, no anisotropy (`VgacImport.cs:1316`).

Measured earlier: the knight's 2048 atlas holds only half-size detail (47 dB against its own half-size copy). The texture size is not the limit; the source of the colour is.

## 2. What the research says (2026-11-11)

- Every painter that produces sharp, faithful textures (Hunyuan3D-Paint, MV-Adapter, StableGen 0.4.0) does the same thing: views in image space, super-resolved before projection, projected into UV space with one dominant view per texel (weight = view weight x cos^4, front 1.0, sides about 0.5, back lower), unseen texels filled from a fallback. Averaging views and re-baking are what make textures soft.
- No downloadable painter is a clean fit: Hunyuan3D-2.1 Paint officially needs 21 GB for texture and its licence excludes the EU, UK and South Korea; MV-Adapter SDXL needs about 14 GB and paints 768 px RGB; TRELLIS.2's own texturing script is the same colour field we already have; Hunyuan3D 3.x is hosted only. StableGen is a manual Blender tool, not something to automate. Pixal3D (pixel-aligned front view) is worth a later look; nothing was verifiable about its mesh-texturing mode, VRAM or licence.
- So the route is our own projection: the picture itself for the side it shows, the turnaround views for the other sides, TRELLIS colour underneath. This extends decision D4 (the turnaround becomes a colour source), which the owner's goal of matching the source picture calls for.
- Alignment is the main risk: the generated mesh never matches the picture exactly. Fit the camera by silhouette, then warp the picture to the rendered model with dense optical flow before projecting. Guard with a silhouette match and refuse a view that does not fit.
- Bake practice: project straight into the final atlas, one resample only; colour bakes with the Standard view transform; margins by adjacent faces; fewer, larger islands only pay once the source is sharp.
- Verify by rendering the model from the picture's camera and comparing with the picture: masked SSIM, colour difference, and Laplacian-variance sharpness ratio (target near 1).

## 3. The plan

### Lane A. Measure it (CPU only)
A fidelity score for any textured model against its picture, usable from `bakeoff/` and from the app.
- `backend/vgac/texfidelity.py`: render the model unlit from the front camera, fit it to the picture cutout, then report masked SSIM, mean colour difference (Lab, low frequency), sharpness ratio (render over picture, Laplacian variance at matched scale), "real detail dB" and 8-view sharpness (ported from `bakeoff/texture_measure.py`).
- `bakeoff/texture_fidelity.py`: runs it on the knight, wolf and chest and writes a baseline table.
- Quality checks `texture_sharp` and `texture_matches_picture_colour` added in report-only mode (never fail a run yet).

### Lane B. Paint from the picture (the main gain)
Project the source picture, then the turnaround views, into the final atlas.
1. Upscale the picture and each view in image space first (the concept step's existing upscaler), never the atlas.
2. Render silhouette, depth and normals of the final mesh from each view's camera; fit scale, shift and small rotation by silhouette match; refuse under 0.85.
3. Warp the picture onto the render with dense optical flow (the TRELLIS-coloured render is the target), limited to a few percent of the frame.
4. Project through the UVs with a depth test, weight = view weight x cos^4, cut off past about 75 degrees, feathered. Front 1.0, quarter and side views 0.5, back 0.5 where it is the only view.
5. Where no view lands, keep the TRELLIS colour, shifted in low frequencies to agree with the projected colour at the border so there is no tone step.
6. Run after the last rebake and before the face pass, so the projected texels are never resampled again.
Prototype in `bakeoff/`, measure, then ship as a prep pass behind a parameter that stays off until the numbers are reviewed.
Pass bar on the knight, wolf and chest: front sharpness ratio at least 0.8 (or 1.5 times today's), front SSIM clearly up, colour difference down, seam step no more than 10 percent above today's, no doubled features on a contact sheet.

### Lane C. Stop losing what we have (bake chain and delivery)
- `blender/rebake.py`: `Cubic` source sampling, more samples, `ADJACENT_FACES` margin, optional 2x bake with a Lanczos downsample; measured before and after.
- One generation: the face relayout and head-graft rebakes take the 3D step's original maps as the source, not an already rebaked copy.
- Colour resizes use Lanczos instead of bilinear (`imaging.read_map`, export ORM path).
- Unity importer: high-quality compression (BC7 on desktop) and anisotropic filtering for base colour on smooth looks.
- Trials, reported with numbers, defaults changed only on a clear win: TRELLIS texture steps 12 against 24; fewer, larger islands (target `atlas_used` 0.6 with the same gutter).

### Later, not in this round
De-lighting the picture with Marigold-IID before the 3D step; CC0 detail normals; a look at Pixal3D; a texture path for Hunyuan3D-2mv (lane B's projection gives it one for free once it works).

## 4. Rules for every lane
- Free and local only. No new model downloads without the owner's yes.
- Test subjects are the bake-off files under `bakeoff/out/`; never `library/projects`. Do not restart the backend or the ComfyUI instances; check a ComfyUI queue is empty before sending a job.
- Each lane edits only its own files (listed in its brief) and commits nothing; the orchestrating session reviews and commits.
- No emoji anywhere. Plain words in anything the user reads.
