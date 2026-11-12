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

## Phase 0 results (2026-11-12)

Score: `backend/vgac/handscore.py` (pure functions), run by `bakeoff/hands_measure.py`; sheets and `scores.json` in `bakeoff/out/hands/` (one PNG per mesh: left hand row, right hand row; front, back, top, and the cut at 70 percent). The hand is the mesh beyond a wrist that sits one tenth of the model's height back from the arm's far end (the vertex farthest from the shoulder joint outside the legs). "Pieces" is the count of separate, solid pieces in the cut at 70 percent of the hand (4 or 5 would be fingers, 1 a mitten); "pieces_90" is the same cut at 90 percent. Spikes are long thin triangles beyond the wrist. The thumb of a flat open hand lies in the cut's plane and is usually not counted, so a good open hand scores 3 or 4.

Calibration: the first version counted 1 piece on a hand whose four fingers were plainly separate on the sheet (the cut was too coarse and the closing too strong), and 2 or 3 pieces on the knight where the cut also crossed his tabard hem. Fixed with a finer grid (192 cells), counting at several closing strengths and keeping the largest count, only counting surface within 0.55 hand lengths of the arm axis, and dropping slivers thinner than 3 cells. After that the numbers agree with what the sheets show on every mesh below. Limits: it needs an A or T pose with the arms out (the heir's hands are claws held in front, so its numbers are only roughly right); a hand length of 10.5 percent of the height is assumed.

Existing meshes (as built, remesh on, TRELLIS target 39,200 or 9,800 for the heir):

| Mesh | pieces 70 / 90 (left, right) | hand triangles (left, right) | spikes | hand share of the model | What the sheet shows |
|---|---|---|---|---|---|
| T-pose man | 1 / 1, 1 / 1 | 956, 934 | 2, 3 | 4.8 % | flat mitten with a long thumb and finger lines, no gaps |
| Boy | 1 / 2, 1 / 2 | 1426, 1418 | 0, 0 | 7.3 % | cupped mitten with four short stubs at the end |
| Knight | 2 / 1, 2 / 1 | 1702, 1646 | 8, 5 | 8.5 % | gauntlet cuff and a rough hand with two or three fat finger bunches |
| TheHeir | 1 / 1, 2 / 2 | 230, 199 | 1, 1 | 4.4 % | claws, partly fused |

No existing hand has four separate fingers. A hand is 5 to 8 percent of the triangles, about 1,000 to 1,700 each, but nearly all of them make the fused blob.

The three rebuilds (same seed and input view as the original, new versions on the existing assets; none selected):

| Variant | T-pose man pieces 70 / 90 | tris | spikes | Boy pieces 70 / 90 | tris | spikes |
|---|---|---|---|---|---|---|
| Original | 1 / 1 | 956, 934 | 2, 3 | 1 / 2 | 1426, 1418 | 0, 0 |
| As is (rebuilt) | 1 / 1 | 961, 955 | 1, 1 | 1 / 2 | 1443, 1400 | 0, 1 |
| `remesh` off | 1 / 0 | 684, 609 | 159, 187 | 1 / 2 | 500, 582 | 196, 182 |
| Input view edited (hands open, back toward the camera, fingers straight and apart) | **4 / 3**, **4 / 3** | 1074, 1020 | 1, 1 | **3 / 2**, **3 / 2** | 1067, 1040 | 0, 0 |

Reading:

1. The build is repeatable: the same seed gives the same hand to within a few triangles, so the differences above are the variants, not luck.
2. `remesh` off is far worse: the surface comes out torn, with 150 to 200 spike triangles per hand and fewer triangles in the hand. The remesh does not close finger gaps that were there; it is what makes the surface usable. Do not ship it as a hand fix. (This corrects the plan's suspicion.)
3. The one change that gives separate fingers is the picture: with the input view edited so each hand is open with its back to the camera and the fingers straight and slightly apart, both assets build hands with four separate finger sections and a clean thumb (sheets `tman_edit.png` and `boy_edit.png`). So the limit is not as hard as the plan feared: when the fingers are separate and face-on in the picture, the build resolves them (not explained; the finger sections are thin but survive the remesh). What was missing was the picture, not only the resolution. This makes phase 2 (a separate hand build) much less necessary.
4. The edit costs nothing in triangles (hand share 5.3 to 5.4 percent, about the same as before).

## Phase 1 results (2026-11-12)

Shipped (all humanoid only):

1. Hand weights in the weighted decimation (`blender/headweight.py` `hand_zones`, `--hand-gain` in `blender/prep.py`, `headtris.HAND_GAIN` 3.0, `stages/prep.py`, clean-up option `hand_triangles`, on by default with the head weighting). The hands join the head's vertex group, and the factor search aims at 2 times the head's even share plus 3 times the hands'.
2. `headtris.HEAD_ROOM` 2.0 to 2.5 (3D step target 49,000 at pc_mid). At room 2 the boy's two hands had 2,106 triangles to start from and an even decimation already kept 1,184 of them, so even all of them was 1.78 times and doubling was out of reach.
3. The `hand_edit` option of the 3D step (`stages/mesh.py` `HAND_EDIT`): the build's input picture is edited once before the build so the hands are open, back to the camera, fingers straight and apart. Off by default (opt-in, one more picture edit, about a minute). It is the change that gives separate fingers.
4. Hand wording in the humanoid `pose_block` (palms inward replaced by open hands, backs toward the camera, five straight fingers slightly apart, thumb out).

Not shipped: `remesh` off (worse), a clean mitten at the Phone budget (not needed by anything phase 0 showed; unmeasured).

In the app on the boy (same seed 1838800639, quarter view, new versions, none selected; sheets `prep_boy*.png`):

| Clean-up result (pc_mid, 19,900 triangles) | hand triangles (both) | hand share | head triangles | pieces 70 / 90 | spikes | checks failed |
|---|---|---|---|---|---|---|
| Before (prep 427f6dbe, no hand weights, room 2) | 1,615 | 8.1 % | 3,399 | 1 / 2 | 4, 5 | none |
| New wording + hand weights + room 2.5 | 3,313 (even would leave 1,714) | 16.6 % | 4,699 | 1 / 1 | 0, 1 | none |
| Same plus `hand_edit` | 2,694 (even 1,375) | 13.5 % | 3,871 | 4 / 2 | 0, 0 | none |

- Hand share at least doubles against the previous clean-up (8.1 to 16.6 percent, 2.05 times). Against an even decimation of the same model it is 1.93 and 1.96 times: the cap is 97 percent of what the 3D model has in the hand, and an even decimation already keeps about half of it, so exactly 2 times is out of reach at room 2.5.
- Head triangles do not drop: 3,399 to 4,699 (3,871 with the edit, a different model). Room 2.5 gives the head more to keep.
- No clean-up check failed (`manifold` and `watertight` included; the rig step's `joins` check was not run on these versions): `checks_failed` is empty on both runs.
- The new wording alone (`boy_posewording.png`) shows open palms and four finger stubs instead of a cupped mitten, with 17 percent more hand triangles, but the fingers are still fused (1 piece at 70 percent). The turnaround draws the quarter view from the concept and does not follow the wording strongly. The picture edit is what separates the fingers (4 pieces in the app: `boy_handedit_app.png`, `prep_boy_handedit.png`).
- Weighted decimation keeps the edited hands intact through clean-up (4 pieces before and after).

Tests: `tests/test_handscore.py` (score on synthetic hands, the Blender rule against the score's rule, options, metrics), `tests/test_headtris.py` (room 2.5), `tests/test_hair_library_pipeline.py` (the new prep param in the contract). Full suite: 1065 passed.

Unverified: the wording's effect on a concept drawn from text rather than from a photo; `hand_edit` on assets other than the two tried (it can drift the character; it asks to keep everything else, and the sheets show the sleeves unchanged on both, but the colours and the face were not compared); the weighted hands on a cel or low-poly look (the weighting applies where the head weighting does).

Phase 2: not started. Phase 0 shows a separate hand build is not needed to get four separate fingers on these two assets; the picture edit does that at a minute's cost. What remains is finger bones (phase 3), which now has a clean hand to work with. Worth doing a phase 2 trial only if `hand_edit` fails on more assets (gloves, claws, gauntlets) or a larger hand is wanted.

