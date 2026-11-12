# Mouth plan (2026-11-11)

Owner's question: can the mesh be made to match the mouth in the picture, or the picture the mesh; talking may be added to assets later.

## What happens today (from the code)

- The expression is never normalised: side-view and close-up prompts keep "the same expression", and the mesh is built from the drawn three-quarter view with whatever mouth the picture has.
- The face fit knows the mouth as one point. `facemarks.py` reduces MediaPipe's lip mask to a centroid; `face.fit_landmarks` is one scale and a shift from the eye line to the mouth centre. Mouth width, corners, lip line and opening are not fitted. A "double lip" (colour about 20 px of 1024 below the geometric lips) is on record in docs/PHOTOREAL-TEXTURE-RESEARCH.md.
- Since the texture-fidelity work the body pass paints the original picture straight onto the head (`srcproject.py`), so the painted mouth sits where the picture's mouth is under the fitted camera, whether or not the geometry has lips there.
- Teeth and the mouth interior are paint on a closed surface. The rig has no jaw bone, no shape keys.

## What the industry does, and whether it fits here

| Approach | Verdict here |
|---|---|
| Neutral closed-mouth input before the build (MetaHuman, Character Creator inputs) | Feasible: one edit. But it conflicts with "the texture matches my picture" unless the mouth paint changes with it. Make it the talk-ready option, not the default. |
| FLAME fitters (DECA, EMOCA, SMIRK) | Licence risk (mostly non-commercial), no mouth interior. Skip. |
| ICT-FaceKit template (MIT; 53 ARKit-style shapes, teeth, tongue) | The clean-licence route to full visemes; needs registration to our head and a cut mouth. High effort; last. |
| Neural Face Rigging | Windows unsupported, heavy, needs cut meshes. Skip. |
| Jaw bone only | Open and close only; needs a slit and an interior. |
| Mouth sprite flipbook (stylised games) with uLipSync (MIT) | Cheap; right for Cel and low-poly looks, wrong for photoreal on its own. |

## Ideas beyond published practice, with critique

1. **Find the geometric mouth from an untextured render.** Run the landmark finder on a shaded (matcap) render of the bare mesh from the fitted camera, as well as on the picture. The difference is the true geometry-to-picture miss, which nobody measures today. Risk: landmark finders are unreliable on bare renders; fall back to the curvature valley between the lips.
2. **Lip warp of the mesh to the picture.** Move lip-region vertices in the view plane so the mesh's lip line and corners land on the picture's (Laplacian or the keystone method already in `bakeoff/face_reshape.py`), a few percent of face width at most, UVs untouched. Fixes offsets and width. Does not fix an open picture on a closed mesh; must refuse when the openings disagree.
3. **Image-driven visemes without cutting the mesh.** Have the edit model draw the character's mouth in a handful of viseme shapes; for each, take the lip landmarks, turn their movement into vertex movement on the lip region (a shape key), and keep the drawn mouth interior as a small texture patch. At run time a shape key and a patch change together (uLipSync drives both). This sidesteps the slit, the mouth bag and template registration. Risks: identity drift between drawn frames (the close-up guard can score it), texture switching at run time, flat teeth seen from the side.
4. **Slit, procedural mouth bag, jaw bone.** Only on a closed neutral mouth; ragged cuts on a decimated mesh are the main risk.

## Phases

### Static match now

**Phase 0: measure.** Lip points (both corners, upper and lower mid, inner opening) for the picture, for the textured render, and for the bare shaded render of the same mesh from the fitted camera. Check first whether the ComfyUI MediaPipe node returns points; otherwise the `mediapipe` Python package on CPU (Apache-2.0), or the lip mask's extremes. New files: `backend/vgac/lipmarks.py`, `bakeoff/mouth_measure.py`. Report per asset, as shares of face width: corner distance, lip-line offset, width ratio, opening in the picture against the mesh. Assets: boy (fresh project), knight, bald knight, mage, T-pose man. Decision: offsets point to phases 1 and 2; opening mismatches point to phase 3.

**Phase 1: fuller fit.** Mouth corners in `face.fit_landmarks` (separate x and y scale at the mouth). Accept: corner distance under 2 percent of face width; face tests pass.

**Phase 2: lip warp.** Promote a lip version of `bakeoff/face_reshape.py` into clean-up, capped at a few percent of face width, refused when openings disagree. Accept: no double lip on the head-graft knight; in-app run on the boy.

### Talk-ready later

**Phase 3: talk-ready option.** A neutral closed-mouth redraw before the side views (`stages/turnaround.py`), with the mouth paint taken from it; the original kept as the "smile". Off by default until talking exists. Accept: opening ratio under a threshold, identity guard passes.

**Phase 4: visemes, two bake-offs.** (a) image-driven shape keys and patches (idea 3); (b) slit, bag and jaw bone, then ICT-FaceKit transfer. Pick on 15 viseme poses rendered without tears on three assets, and a clean Unity import.

## Not recommended

FLAME-based fitters and Neural Face Rigging.

## Phase 0 results (2026-11-11)

Built: `backend/vgac/lipmarks.py` (lip points: both corners, outer upper and lower middle, inner edges and opening) and `bakeoff/mouth_measure.py` (three images per asset, numbers and a sheet; sheets in `bakeoff/out/mouth/`, not committed).

Lip points. The ComfyUI MediaPipe nodes cannot return points: MediaPipeFaceLandmarker outputs FACE_LANDMARKS and a bounding box that no node turns into numbers, and MediaPipeFaceMask outputs masks. So the `mediapipe` Python package (1.2.3, Apache-2.0, CPU) was installed into `backend/.venv` (it also pulled in opencv-contrib, matplotlib and their dependencies) and Google's face landmarker model (3.7 MB, `face_landmarker.task`, Apache-2.0) is kept at `runtime/models/face_landmarker.task` (gitignored like the rest of runtime; `VGAC_FACE_LANDMARKER` overrides the path). A fresh checkout needs the package and the file; `lipmarks.available()` is False without them and nothing else changes. A lip-mask extremes fallback (`lipmarks.from_mask`) exists but was not needed.

Bare render. MediaPipe found a face on the bare skin-coloured shaded render for four of seven assets (boy, man, mage, bald ponytail); I checked each sheet and the points sit on the geometric lip crease and its ends. For three (knight, caped hero, viking) it found nothing and the lip line is the horizontal curvature valley under the nose (`lipmarks.valley_row`): only the line is reported for those, no corners, width or opening.

Newest finished prep version of each (shares of the face's width on the render, which is 400 to 630 px of 1024; the viking face is only 200 px wide, so its numbers are coarse). Paint against geometry is the render's lips against the bare mesh's, same camera, so it is exact; picture against paint is eye-aligned (the picture's head crop is framed by `face.painted_head`, which differs from the render's framing by up to 7 percent, so the raw offset is mostly that framing; the eye-aligned one asks whether the paint follows the picture).

| asset | method on bare render | paint vs geometry: corners / lip line (+ is lower) / width ratio | picture vs paint, eye-aligned: corners / line | opening, share of width: picture / paint / geometry |
|---|---|---|---|---|
| boy (ec17b469) | mediapipe | 7.5% / +5.7% / x1.14 | 1.1% / -0.7% | 8.4% / 8.8% / 2.3% |
| knight (869cad2d) | valley | - / -4.2% / - | 1.0% / +1.3% | 0.3% / 0.1% / closed |
| T-pose man (6649a3bd) | mediapipe | 0.7% / -0.5% / x1.00 | 7.5% / +10.9% | 10.0% / 2.7% / 0.4% |
| mage (a153fb83) | mediapipe | 3.3% / -4.5% / x1.12 | 2.4% / +3.2% | 0.1% / 0.2% / 0.2% |
| bald ponytail (be0b09f2) | mediapipe | 5.5% / +3.3% / x1.07 | 0.6% / +0.4% | 0.7% / 0.4% / 0.1% |
| caped hero (3ee85841) | valley | - / -5.2% / - | 2.8% / +2.7% | 0.3% / 0.2% / closed |
| viking (803ea973) | valley | - / -3.2% / - | 8.0% / -5.1% | 3.0% / 2.7% / closed |

(The "bald-route knight" has no finished prep version; the bald ponytail is the bald-route asset that has one.)

Reading.

1. For the five closed-mouth characters (knight, mage, bald ponytail, caped hero, viking) the miss is an offset and a width error, not an opening: the painted lip line sits 3 to 5 percent of the face's width from the geometric crease (above it on knight, mage, caped hero and viking, below it on the bald ponytail). Where corners could be measured (mage, bald ponytail) they are 3 to 6 percent off and the painted mouth is 7 to 12 percent wider than the geometric one. This matches the "double lip" on record. The paint follows the picture (1 to 3 percent after the eyes are put together; the viking's face is too small to say); it is the mesh that does not have its lips where the picture has them. Fixable by a fuller fit and a small lip warp (phases 1 and 2).
2. The boy has an offset (5.7 percent line, 7.5 percent corners, width x1.14) and an opening mismatch: the picture and the paint grin with 8 to 9 percent of face width between the lips, the geometry is a closed crease (the 2.3 percent the finder reports there is shading, not a hole). A lip warp alone would move the crease but not open it; this needs phase 3 or 4 to match.
3. The T-pose man's paint and geometry agree with each other (0.7 percent) but neither is the picture: the picture is an open smile (10 percent opening, teeth) and the paint is a closed mouth inside the moustache (2.7 percent), 11 percent of face width higher than the picture's line after eye alignment. This prep (6649a3bd, 2026-11-11 04:15) looks older than the picture-painted ones, so it is probably a texture-fidelity issue rather than a mouth-fit one; re-run it on a fresh prep before drawing a conclusion. Opening mismatch either way.
4. Which kind dominates: offsets and width, on 6 of 7 assets (all but the man); an opening mismatch on 2 of 7 (boy, man), both of them photographs with a smile. Closed mouths are the common case.

Caveats: the bare render's corners are read off a crease whose ends fade, so a mouth width ratio of 1.07 to 1.14 may include some bias toward narrow; the lip-line offset (3 to 6 percent) is larger than that effect and was seen on all seven. Valley-only rows are one number from one crease, checked by eye on three sheets. Seven assets is a small sample.

### Phase 1 (done, offline only)

`face.fit_landmarks` takes optional `mouth_corners` in both landmark dicts and then scales sideways by the model's mouth width over the picture's (kept within 15 percent of the upright scale and within the usual limit of 1), shifts sideways by the mouth's middle, and reports `corner_residual_px`; without corners on both sides it is unchanged. `face.landmarks` adds the corners (`face.add_mouth_corners`, from the mediapipe package) to MediaPipe's result when the package and model are present and the lips found match the ones MediaPipe's node found; otherwise as before. Offline check (`mouth_measure.py` "fit": the picture fitted to the bare mesh's landmarks, lips found again, compared with the geometry's; corner distance as a share of face width, old fit then new): boy 3.3% to 1.4%, mage 2.0% to 0.5%, bald ponytail 2.6% to 1.4%, man 5.4% to 5.1% (the picture's open smile lifts its corners, which no scale fixes). Mouth width ratio 1.14, 1.11, 1.08 to 0.98, 1.01, 0.99. The eye residual rises by 0.5 to 1.3 percent of face width (limit in `face_detail` is 12 percent). Three of four meet the plan's under-2-percent corner target; the three assets without a bare-render finder (knight, caped, viking) cannot be checked this way. Not done: an in-app run (left to the owner's main session), and the fit only helps the face pass (`face_detail`), not the body pass in `srcproject.py`, which fits the picture by silhouette; the paint-against-geometry offsets above are measured on that body-pass paint, so phase 2 (moving the lips) is the step that addresses them directly.
