# Head build plan: a head built on its own, put on the body

Status: plan, 2026-11-06. Evidence: `docs/PHOTOREAL-TEXTURE-RESEARCH.md` section 7 (bake-off prototypes
`bakeoff/head_build.py`, `head_graft.py`, `face_reshape.py`, `face_proportions.py`). Nothing here is built in the app yet.

## Key decisions

1. **Bald route only in v1.** The head is built from the bald head-and-shoulders crop. With hair, the tight crop built
   as a flat board twice, and the looser crop's face gained much less. The bald route already builds the body bald and
   adds hair afterwards, so the head build slots in without changing how hair works.
2. **A second 3D build in step 3, not a new step.** Same builder (TRELLIS.2 on card A), same seed, same settings, from a
   crop step 3 makes itself. The body build is unchanged. The head build adds about 3 to 8 minutes.
3. **Join by colour, not distance.** The prototype failed three ways with distance rules (old head showing through,
   chin cut off, collar chewed) and worked once the parts were told apart by colour: the new head keeps its head and,
   below the chin, only skin; the body drops its old skin above the neck and anything strictly inside the new head.
4. **One model, one texture at the end.** The join is two meshes with two textures; Blender joins them and bakes one
   atlas (the clean-up step already relayouts and re-bakes: `blender/faceuv.py`, `blender/rebake.py`), which can also
   give the face its larger share of texels in the same pass.
5. **Guarded, with the body build as the fall back.** Every check that fails keeps today's model and says why, the way
   the face close-up guard does. Never a half-joined model.
6. **Hair from the haired build, as a third hair source (stage 2 below).** Optional, after the head build works.

## 1. Where it runs

| Step | Today on the bald route | Added |
|---|---|---|
| 2 Check all sides | bald front, left, right pictures | nothing (the head crop is cut in step 3 from the bald front) |
| 3 Build the 3D model | body built from `bald_front_cutout`; hairstyles ranked on it (`hairfit.rank`) | crop; head build; flat-board check; graft; graft checks; hairstyles ranked on the grafted model |
| 4 Clean up | weld, size, decimation, rebake, face pass, hair pass, `hairfit.attach` | join the two meshes into one, one atlas; the face pass then runs on the grafted face |
| 5 Make it move | skinning on the prepared model | nothing expected (one mesh after step 4); to verify |

Order in step 3 (`stages/mesh.py`, after `_trellis_build` for the body):

1. **Crop**: the top 26 percent of the bald figure's height, square, centred on the head, enlarged to 1024, with alpha
   (`head_build.py`). The input must carry alpha: an RGB picture with a near-black background made the builder build
   the whole square.
2. **Head build**: `convert_graph` with the body's seed, colour grid and texture size. In a version folder as
   `head.glb` and `head_*.png`.
3. **Flat-board check**: depth under a tenth of the width is a failed build: one retry with the next seed, then fall
   back.
4. **Graft** (new `vgac/headgraft.py` from `bakeoff/head_graft.py`):
   - keystones from each model's side outline, read from skin pixels only, the top from the whole outline;
   - scale the head so top-to-chin equals the body's; tops and nose tips meet; centred across;
   - cut at the body's neck (the deepest point of the outline between the chin and a collar);
   - colour classes from the nose (skin) and the crown (hair, only when it differs from skin);
   - head keeps above the chin plus 15 percent of the head height everything, lower only skin, down to a short stub;
   - body drops skin above the stub's end and what lies strictly inside the new head above the chin.
5. **Graft checks** (fall back on any):
   - width after scaling within 0.85 to 1.15 of the body head's (the knight: 0.97);
   - keystones found on both (nose tip, chin bottom, neck);
   - no old-head skin left outside the new head (count of body skin faces above the neck after the cut);
   - outline match of the grafted model against the bald front, left and right pictures not lower than the body's.
6. **Hairstyles ranked on the grafted model** (it is still bald).

## 2. Hair from the haired build (stage 2, optional)

The owner's idea: take the hair off, do the work on the bald head, then put the haired build's own hair back. Why it
can work: the colour classes that find the hair in the graft (the crown's colour) already separate hair from skin and
armour, and the two builds agree on head size (0.97).

- **Source**: the haired full-body build (step 3 builds it today when hair is not from the library) or a haired build
  made for this.
- **Extract**: the hair triangles by the crown colour, as their own mesh with their own texture.
- **Place**: by the face's keystones (nose tip, chin, ears), not the top of the head (the hair changes the top).
- **Attach** where library hair attaches (`hairfit.attach`, separate files), so the rig and hand-off treat it the same.
- **Gain**: the hairstyle is the picture's own, which no library style matches exactly, and the eye under a fringe is
  now painted (the bald head has a whole face under it).
- **Limits**: the hair is as soft as today's modelled hair (the library's is cleaner); the hair shell has no proper
  underside, so the scalp under it needs the hair's colour where gaps show (the library hair's scalp tint, if there is
  one, or a new one).
- **Choice for the person**: hair from the library (clean, a close style) or from the picture (exact style, softer).
- Needs its own bake-off first: `bakeoff/hair_transplant.py` on the knight, judged on front, three-quarter, side and back.

## 3. Proportions (later)

The head-only build was the closest to the painting but still a little long below the mouth. `bakeoff/face_reshape.py`
moves the model's face to measured keystones; on the head build the change was small (23 px of a 1024 head frame) and
clean. Step 2's side views agree with the 3D builds more than with the front painting, so the reference must be the
front picture. Add only after the head build ships and a front-picture chin measure is reliable.

## 4. Wording

- Step 3 option, on by default on the bald route for people: "Build the head on its own (sharper face, about 5 minutes
  more)".
- Checks: "The head was built on its own and joined at the neck." When it fell back: "The head built on its own did not
  fit, so the body's own head is used: {reason}." Reasons: "it came out flat", "it was a different size from the body's
  head", "its chin or neck could not be found", "part of the old head still showed".
- Fine tune (advanced): head crop size (default 26 percent of the height), on or off.

## 5. Lanes

1. **Graft engine** (offline, pure, tests): `vgac/headgraft.py` from the prototype, with synthetic tests (two boxes for
   collar and neck, a sphere head) and the knight files as a slow test.
2. **Step 3 pipeline**: crop, head build, flat check, graft, checks, files, params, Stop and Resume (the head build's
   seed written on the version, as the face pass does).
3. **Step 4 join**: Blender join and one atlas; the face pass on the grafted face; check the decimation keeps the face.
4. **UI and wording**: the option, the check lines, the version card showing both builds' pictures.
5. **Hair transplant** (stage 2): bake-off, then a third hair source.

## 6. Risks, most likely first

1. **No collar.** The knight's high collar hides the join. An open neck or a low neckline will show a seam: needs a
   blend across the cut (match the skin tone and weld the two neck rings) and a test character without a collar.
2. **Other characters' crops.** Tested on one knight. A character with a hood, a helmet, a beard or a non-human head
   (elf ears, animal) may build flat or misplace keystones: try four more before shipping.
3. **Rigging across the join.** One mesh after step 4, so expected to skin normally; verify the neck bends.
4. **Time.** Step 3 roughly doubles on card A.
5. **The face pass** must run on the bald head correctly: needs the bald face finder fix (branch
   `claude/infallible-hellman-1ed977`, not merged yet).

## 7. Before starting

- Merge the bald face finder fix.
- Commit or set aside this session's uncommitted work (first-build settings, picture viewer, combined change buttons,
  the colour grid wording, bake-off scripts, the research report).
