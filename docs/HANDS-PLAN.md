# Hands plan (2026-11-11)

Owner's question: hands consistently come out oddly in the mesh; how do we improve them.

## Cause (from the code)

A hand is too small for the build to resolve.

- The 3D step builds from a 1024 px crop of the drawn three-quarter view. A hand is about 60 to 90 px long there and a finger 6 to 10 px wide. With "palms inward" (humanoid.yaml `pose_block`) the three-quarter view sees the hands nearly edge-on.
- TRELLIS.2's first structure grid is 32 cells across the whole figure: a hand is about 3 cells, a finger less than one. This is the same cause `headgraft.py` documents for smudged faces.
- `RemeshMesh` (768, fill 20, 20 smoothing passes) probably closes the gaps between fingers (inferred, not measured).
- Clean-up decimates with the head weighted (`headtris.py`, `blender/headweight.py`); hands get no protection.
- The rig (SkinToken, Mixamo template) has no finger bones (gap G25 in docs/GREAT-ASSETS.md).

## What the industry does, and whether it fits here

| Approach | Verdict here |
|---|---|
| Force an A or T pose before the build (Meshy, Rodin) | Already done; only the hand wording is missing. Cheap. |
| Parametric hand fitting (MANO, HaMeR, WiLoR) and hand refiners built on them | Not usable: MANO and WiLoR weights are non-commercial. |
| Part-aware generators (PartCrafter, OmniPart, Hunyuan3D-Part) | Parts are object-level, not fingers; VRAM and licences are poor fits. Skip. |
| Multi-view-conditioned build | Our drawn views disagree at finger scale; likely to blur hands more. Unverified on 12 GB. Skip for now. |
| Template hand swap (game studios; MakeHuman assets are CC0) | Works for bare-handed realistic people only; brings finger bones. Opt-in, later. |
| Mitten hands at low triangle budgets | Standard low-poly practice. Use at the Phone budget. |

## Ideas beyond published practice, with critique

1. **Hand build and graft, in a canonical pose (the head-build pattern).** Draw one close-up of the character's own hand with the edit model (open palm facing the camera, fingers up and spread, same sleeve or glove), build it alone so it gets the whole 32-cell grid, mirror it for the other side, graft at the wrist. Because we choose the pose, the finger layout is known in advance, which makes procedural finger bones tractable later (cluster cross-sections along the up axis). Keeps gauntlets and claws, which a template hand cannot.
   Risks: no keystones like nose and chin, so orientation must come from the wrist axis and thumb side; an open hand is thin, so a flatness check cannot tell a failed build from a good one; the wrist seam needs a colour match; one extra build per asset; the redrawn hand may drift from the character.
2. **Hand-friendly input picture.** Fingers straight and slightly apart, and hands turned so the three-quarter view sees the back of the hand rather than its edge. The 32-cell limit remains: expect cleaner mittens, not five fingers.
3. **Hand-weighted decimation, gentler remesh.** Only preserves what the build produced.
4. **Finger-gap carving from the view silhouettes.** Rejected: the drawn views disagree at finger scale and carving would cut real fingers.

## Phases

### Phase 0: measure (no product change)

- A hand score, new file `backend/vgac/handscore.py` plus a bake-off script `bakeoff/hands_measure.py`. Per hand (the mesh beyond the wrist, found from the arm's far end in the A or T pose): number of separate cross-sections at 70 percent of hand length (4 is fingers, 1 is a mitten or blob), triangle count beyond the wrist, count of spike triangles, and a close-up render sheet.
- Run it on: T-pose man `59deb0fb.../mesh/77d0a387...`, boy `30c82898.../mesh/071647d3...`, knight `79cda8e8.../mesh/d1da23d0...`, TheHeir `ea2b48ed.../mesh/89c2dbdc...` (full paths under `library/projects/`).
- Rebuild the T-pose man and the boy through the app's API three ways: as is; `remesh` off; input view edited to spread fingers with the back of the hand to the camera (`mesh.refine` or a one-off Qwen edit).
- Accept: the score agrees with the eye on the render sheet, and we know which variant, if any, raises the cross-section count on both assets.

### Phase 1: ship what phase 0 proved

Touch points: `data/categories/humanoid.yaml` (`pose_block` hand wording), `graphs/trellis.py` (remesh inputs), `blender/headweight.py` and `headtris.py` (hand weights), `stages/prep.py` (clean mitten at the Phone budget). Accept: hand triangle share at least doubles at pc_mid, head triangle count drops under 10 percent, no new `joins` failures, full test suite passes, one fresh in-app run looks right.

### Phase 2: hand build trial

`bakeoff/hand_graft.py`, modelled on `bakeoff/head_graft.py` and `backend/vgac/headgraft.py`. Promote to `backend/vgac/handgraft.py` and `stages/mesh.py` only if 4 of 5 assets get four separate fingers and a wrist seam within the head build's colour tolerance. Fall back silently on any failure, as the head build does.

### Phase 3: finger bones (only after phase 2)

Procedural finger chains on the canonical built hand, or the CC0 template hand for bare-handed realistic people. `blender/rig_fix.py` already names finger chains. Accept: Unity Humanoid avatar valid with fingers mapped; a fist clip closes without spikes.

## Not verified

MANO's licence text, the remesh effect on finger gaps, multi-view TRELLIS.2 on 12 GB.
