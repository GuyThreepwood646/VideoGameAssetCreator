# Five known faults: causes and what to do about them

Research date: 2026-11-05. Written for the owner. Nothing under backend/, frontend/ or unity-sandbox/ was changed, no job was started through the app, Unity was not started and no asset was rebuilt or reselected. Every experiment ran on copies or read-only, from scripts in `runtime/tmp/faults` (Python with the app's own functions, Blender 5.2 with absolute paths, and for fault 2 picture jobs sent straight to the image engine). All pictures named below are in `runtime/tmp/faults` unless another folder is given.

Markers: "proven" means a picture or a number on this machine shows it; "likely, not proven" means it is my best reading of the code and the evidence, without a test that settles it.

| | Fault | Real cause, in one line | Fix size |
|---|---|---|---|
| 1 | Dark patches on the neck | The 3D step's normal map (a texture that fakes small bumps) has its green channel upside down; every later step copies the error in. Proven. | Small |
| 2 | Blank faces on long-haired cel characters | Since the hair sentence was removed, nothing in a Cel prompt asks for a face, and the anime painter sometimes leaves it out. A face sentence fixed 8 of 8 tests. | Small |
| 3 | No hair detail on beards or under hoods | The hair search only starts at the crown, and the face finder takes a jutting beard for the nose. Proven on the Ranger. | Larger |
| 4 | Fur pass not good enough | No keep-out for eyes, nose, mouth and claws, and the pass sets fur to hair's shine. Both proven; a fixed run is better but not clearly worth it at game distance. | Small to fix, larger to judge |
| 5 | Characters face away in the Unity strips | The shared clips are imported "based upon original" orientation without axis conversion, so the clip turns the body round. Likely a real in-game fault, not proven without Unity. | Small, plus a Unity test |

---

## 1. Dark brown patches on the fresh knight's neck

### What is seen

Dark brown, blocky patches on the skin of the neck inside the collar of the fresh knight (asset a4d5cfde, prep 0c2cc197, export 83e675d5), in Unity (`runtime/tmp/hairmat/sheet.png` rows 2 and 3, `sheet_round1.png` rows 1 to 3) and in Blender. The patches are straight-edged, the shape of the model's triangles.

### The cause (proven)

1. **It is the normal map, not the metal map or the colour.** The neck rendered in Blender from the shipped GLB four ways: as shipped, without the normal map, without metal, without both (`f1/fresh_lit_variants.png`). The patches are there whenever the normal map is on, with or without metal, and gone whenever it is off. The colour (`f1/fresh_neck.png`, first tile) is plain skin there.
2. **The normal map's green channel is upside down.** glTF, Blender and Unity read a normal map with green meaning "up the texture" (the OpenGL convention). The 3D step's map was written the other way (the DirectX convention). Flipping only the green channel of the 3D step's own map, and changing nothing else:
   - in Blender, the first knight's 3D-step model: the angular facets on her face, neck and armour disappear (`f1/blender_flip_green_mesh_first.png`, left as made, right green flipped);
   - in the app's own software renderer, the same on both knights (`f1/convention_test.png`) and on the Ranger, the goblin and the wolf (`f1/convention_test2.png`). Flipping red instead does not fix it, which rules out a general scramble.
   Why it shows as triangles: a baked normal map exists mainly to cancel the flat facets of a lower-detail model. With green reversed, the up-down half of that correction is doubled instead of cancelled, so each triangle gets its own wrong tilt and catches the light differently.
3. **Where it comes from.** The 3D step (`backend/vgac/graphs/trellis.py`, `convert_graph`, node `nrm`: `BakeNormalMapFromMesh`) bakes the map; `stages/mesh.py` (`_trellis`) downloads it and registers it as is; the GLB from the same graph carries the same image. The clean-up step then re-bakes every map in Blender onto the new texture layout (`blender/rebake.py`), which reads the wrong map as if it were right and writes the error out again, now in the correct convention. So from clean-up on, the error is part of the picture: flipping green on the shipped export map makes it worse, not better (`f1/blender_flip_green.png`). The fix has to happen at the 3D step.
4. A smaller second effect, likely, not proven: after the green flip a thin bright strip is still left inside the fresh knight's collar (`f1/convention_test.png`, bottom row, second tile). The bake reaches 5 cm (in the model's own units; about 8.5 cm on a 1.7 m character, `cage_distance: 0.05`) out from the surface, and inside a collar that is far enough to hit the collar instead of the neck. 90 to 100 percent of the texels with a badly tilted normal have another surface facing them within that reach, against 30 to 58 percent of all texels (`f1/normal_quant.json`).

The forehead blotch seen earlier was the metal map, as the brief says. That is a separate fault and it is also general (below).

### How widespread

Every character the 3D step has built has the green channel reversed: it is the same node with the same settings for every asset. On the five checked it shows on the face or neck of all four humanoids and on the wolf's chest and ear (`f1/lit_with_without_normal.png` for the shipped clean-up maps; `f1/lit_mesh_step.png` for the 3D step's). It is most visible where the lower-detail model differs most from the 3D builder's detailed surface: necks inside collars, cheeks under hair, the goblin's face under the helmet's brim, the Ranger's face in his hood, the edges of armour plates. Earlier notes such as "skin-coloured plates beside the cheeks" and "a soft line down her cheek" on the first knight may be partly this fault (likely, not proven).

Skin marked as metal, the second fault the brief asks about, is also general on armoured characters. Share of skin-coloured texels on the head and neck with metal over 0.5 (`f1_metal.py`, `f1/metal_heads.png`): first knight 98 percent (her whole face is about 0.68 metal as shipped), goblin 44 percent (about 0.49), fresh knight 3.5 percent after the hair pass (13.5 percent before), Ranger 0.2 percent. The 3D builder spreads metal from armour and helmets onto the face.

### Options, cheapest first

1. **Flip the green channel when the 3D step downloads its maps.** In `stages/mesh.py`, `_trellis`, after `download_output` for the key `normal`, invert green in `normal.png`, and do the same to the normal image inside `model.glb` (the GLB image rewriting already exists in `imaging.glb_replace_images`; it needs a "flip normal green" option). About 1 second per asset. Risk: low; if a future version of the 3D node changes its convention the flip would turn wrong again, so add the check in option 3. Only new 3D builds benefit.
2. **Repair assets already made.** A small one-off tool that flips green in a chosen 3D-step version's `normal.png` and GLB, then "Rebuild from clean-up" (autopilot `from_stage` prep) for that asset. Costs the clean-up, rig and export time again: roughly 5 to 10 minutes per character on this machine. Risk: medium only in that it makes new versions; nothing is lost.
3. **A check that would have caught it.** In the 3D step or clean-up, render the head lit with and without the normal map in the app's own renderer (`hair.render_lit`) and measure the extra edge energy along triangle edges; fail softly over a limit ("The surface bumps do not match the model"). About 3 seconds. Risk: the limit needs tuning on a few assets.
4. **Skin is never metal.** In the face pass (`stages/face_detail.py`, after the face is painted) set metallic to 0 on the face region and on skin-coloured texels joined to it; the hair pass already does this for hair (`hair.paint_hair` and `clear_hair_metal`). About 1 to 2 seconds. Risk: a metal mask or a metal-skinned creature would be made matte; skip when the description says metal or robot.
5. **The leftover strip inside collars.** Lower `cage_distance` in `graphs/trellis.py` from 0.05 to about 0.02 so the bake does not reach the collar. Untested (it needs a 3D build); risk: where the simplified model is further than 2 cm from the detailed one the bake misses and leaves flat patches.

### Recommendation

Options 1 and 4 now (small: under an hour together, plus tests), option 3 with them, then option 2 on the characters the owner wants to keep. Option 5 only if the collar strip is still visible after that.

---

## 2. Blank faces on cel characters with long hair

### What is seen

With the Cel look (painter: Anima Turbo, `data/models.yaml` `anima`, no negative prompt, CFG 1) the long-haired mage came out with an empty face, no eyes, nose or mouth, on both seeds tried (`runtime/tmp/hairpics/sheet_cel.png`, `sheet_r2_cel_mage.png`). The old hair sentence brought the face back but changed the character, so Cel characters now get no hair sentence.

### The cause

What is proven: the painter leaves the face out when nothing in the prompt asks for one, and a sentence asking for the face's parts brings it back without changing the character. What is likely, not proven: why this painter does it. It is a known habit of anime-trained models with a small face in a full-body picture, and long hair framing an empty oval makes it worse; but I cannot look inside the model.

The tests (`runtime/tmp/faults/f2_gen.py`, the app's own `concept.build_prompt` with no hair sentence, as the app does for Cel today, then one change to the text; sent straight to the image engine, about 40 to 280 seconds a picture while other agents shared the queue). Four long-haired descriptions (mage with silver hair, elf archer with blonde hair, swordswoman with black hair, a young man with wavy brown hair) on two seeds each, plus the short-haired knight as a control:

| Change to the prompt | Pictures with a full face (of 8) | Notes | Sheet |
|---|---|---|---|
| None (as the app does now) | 5 | Mage blank on both seeds (reproduces the fault); swordswoman seed 2202 drawn from behind | `f2/sheet_base.png` |
| A full stop after the description | 5 | The app joins the description to the style block with a space and no full stop ("...gold embroidery Anime cel-shaded style..."); fixing that alone changes nothing that matters: mage still blank on both seeds, swordswoman 2202 turned round but with no eyes | `f2/sheet_period.png` |
| Pose block before the style block | 4 | Worse: mage seed 1101 drawn from behind, seed 2202 still blank, swordswoman 2202 still from behind | `f2/sheet_order.png` |
| **A face sentence after the style block:** "The face is clearly drawn, with two eyes, eyebrows, a nose and a mouth." | **8** | Both mages and the turned-round swordswoman now face the camera with a full face; outfits, hair colour and length, pose and framing kept; long hair hangs down, not blown sideways. Small drift: the mage on seed 1101 lost her boots; the knight control on seed 1101 has a slightly shorter, spikier bob (seed 2202 almost identical) | `f2/sheet_base_face.png`, `f2/sheet_mage.png`, `f2/sheet_order.png` (last column, with the knight) |

So the cause is in the image model's habits, and the trigger is the prompt: since the hair sentence was taken away for Cel, nothing in a Cel prompt mentions the face at all. The old hair sentence fixed the face because it contained "both eyes clearly visible", not because of its hair words; its hair words were what changed the character. The pose block's "facing the camera directly" is not enough on its own (two of eight pictures were from behind or blank).

A readiness check "the face has no features" was tried by script with the app's own eye-area finder (`hairpic.eye_cover`, copied with one extra number: how much of each eye area is neither skin nor hair; `runtime/tmp/faults/f2_eyefeat.py`). It is not reliable yet: on the blank mage it returns "cannot tell" (silver hair and an empty face are too alike), it gives 0.04 on a correct elf face, and it only caught one of the four blank or turned-round pictures (0.007). A check would need a better face finder (for example asking the local vision model the app already uses for "style from a picture" whether it sees two eyes and a mouth); that was not tested.

### How widespread

Only the Cel look was tested here, and the blank face only appeared with long hair framing the face (mage on both seeds) and once as a figure drawn from behind (swordswoman). The archer, the man with wavy hair and the short-haired knight had faces without help. Clay also gets no hair sentence (`STATUS.md`); it uses a different painter and was not tested, so it is unknown whether Clay has the same problem.

### Options, cheapest first

1. **A face sentence for Cel humanoids**, independent of hair: a new style key (for example `face_sentence` on the `cel` style in `backend/vgac/data/styles.yaml`) added by `stages/concept.build_prompt` right after the style block, for humanoid categories, and skipped when the description covers the face (masked, helmeted with a visor, faceless on purpose; `hairpic.EYE_COVER_WORDS` already lists most). No time cost. Risk: small drift on some seeds (above); test the same sentence on Clay before adding it there.
2. **End the description with a full stop** in `build_prompt` when it has none. No cost, tidier prompts for every look; it does not fix this fault on its own.
3. **A readiness check with the Fix "Redraw with the face drawn"** through the picture editor, as `hair_off_eyes` does. Needs a reliable face finder first (see above). A few seconds per picture with the vision model. Risk: false alarms on stylised faces, which the hand-made measure showed.

### Recommendation

Option 1 now, with option 2 alongside: small (under an hour with tests). Check Clay the same way before giving it the sentence. Leave option 3 until a face finder is tested.

---

## 3. Beards, and hair under a hood, get no hair detail

### What is seen

On the Ranger (asset 276aacf6) the hair pass stands back: "the top of the head is not a usual hair colour (most likely a hood or a hat)". His brown hair at the forehead and temples and his beard stay as the 3D step made them (`runtime/tmp/hair/ranger_sheet.png`).

### The cause (proven)

1. **The hair search only starts at the crown.** `hair.find_hair` takes the colour at the very top of the head (`crown`, top 3 percent of the height) as the hair colour, and grows the hair from there (`grow_tris(crown & candidate, ...)`). Under a hood the crown is the hood, so `verdict` returns `not_hair_colour` and nothing else is tried. Hair that does not reach the crown (a beard, sideburns, hair showing below a hood or a hat, a helmet with a beard) can never be found.
2. **A jutting beard fools the face finder.** `face.find_face` takes the nose to be the nearest point of the head to the front camera. On the Ranger that point is the tip of his beard: the "nose" lands on his chin and the "skin colour" it samples is the beard's (Lab lightness 18.6, a dark brown; his skin is about 53) (`f3/ranger_findface.png`, red circle). The face pass uses the same function, so this may also be why every close-up of his face was rejected for moved eyes (likely, not proven).

### How the hair could be found when the crown is covered

A prototype (`runtime/tmp/faults/f3_proto.py`, nothing changed in the app) starts next to the face instead of at the crown:

1. find the face's skin from the front view as now, but when the sampled "skin" is very dark (lightness under 35), take the lighter colour group that covers most of the face region instead;
2. group the colours of the head and jaw (the top 20 percent of the height, within the head's width), as `find_hair` does;
3. a group is hair when it has a natural hair colour (`hair.natural_hair`: the green hood is out), differs from the skin by at least 25 (the app's colour distance; the current limit is 14), and touches the face's skin;
4. grow it from where it touches the skin, inside the head zone, and drop separate pieces under 60 triangles.

On the Ranger it finds the hair at his forehead and temples under the hood, his sideburns and his beard, 464 triangles, and leaves his eyebrows and eyes out (`f3/ranger_proto2.png`; green is what it takes). With the current limit of 14 it also took the painted shadow under his jaw and his eyebrows (`f3/ranger_proto.png`). On the first knight it takes nothing (her hair is found from the crown anyway), on the fresh knight it finds most of the bob, on the goblin under his helmet it finds nothing (`f3/others_proto.png`).

What can go wrong, and what would guard against it:

| Risk | What happens | Guard |
|---|---|---|
| Brown leather collar or strap touching the jaw | Taken as beard | Only grow above the jaw line plus a margin (the lowest skin point of the face), and require a long shared border with the skin, not a corner |
| Scarf or mask over the mouth | Taken as beard | The same jaw-line rule; skip when the description says scarf, mask or veil |
| Eyebrows | Taken as hair | Pieces under 60 triangles are dropped (worked on the Ranger); the face pass's eye and brow band is a second guard |
| Painted shadows on skin | Taken as hair at the current colour limit | The stricter limit of 25 (worked on the Ranger) |
| Stubble | Close to skin colour | Falls under the colour limit and is skipped, which is the safe side |
| Moustache | A small separate piece, dropped by the piece-size rule on the Ranger | Allow small pieces directly under the nose |
| Grey or white beard on pale skin | Too close to tell apart | Skipped with the existing reason `hair_like_skin` |

Painting would reuse `hair.paint_hair` with a flow that runs down from the hairline at the temples and down and forward along the jaw for a beard. The picture vote (`picture_votes`) can confirm the groups from the turnaround pictures, as it does now.

### Options, cheapest first

1. **Fix the nose search for bearded faces.** In `face.find_nose`/`find_face`, look for the nose between the eye line and the mouth (the upper 60 percent of the face box), not the frontmost point of the whole head. No time cost. Risk: low; it changes the face pass too, so the two knights' face results need a re-check.
2. **A second route in `hair.find_hair`: hair from the face edge**, used when the crown route returns `not_hair_colour`, `crown_mixed` or `helmet`. About 5 to 10 seconds per character. Risk: medium (the table above).
3. **A beard as its own region** with its own flow and a slightly lower strength, and a plain sentence in the clean-up result ("Beard painted; hair under the hood painted"). About 30 to 40 seconds more for the paint. Risk: medium.
4. **A hair mask from the picture** with a small, commercially clean segmentation model (MediaPipe hair segmenter, Apache-2.0, see `docs/HAIR-RESEARCH.md` 4.4). Most robust, but it needs a download, so not part of this research.

### Recommendation

Option 1 first (small), then options 2 and 3 together as one larger piece (about a day with tests, on the Ranger plus at least one bearded and one hatted character made for the test).

---

## 4. Fur on creatures

### What is seen

On the Wolf (asset 8fd5e632) the trial fur pass gave the body direction but ran stripes through the eyes and muzzle, looked glossy, and left a small squarish mark (`runtime/tmp/hair/wolf_sheet.png`).

### The causes (proven, except the mark)

1. **Nothing keeps the face out.** `hair.find_fur` groups the body's colours into five groups and calls everything fur except groups under 4 percent of the surface. The eyes, the lips and the nose's edge share a group with the dark fur of the back and brow, so they are fur too: the trial's mask covers the eyes (`runtime/tmp/hair/wolf/mask_sheet.png`, the eyes are green).
2. **The pass makes fur shiny.** `hair.paint_hair` sets roughness toward 0.55 (`ROUGH`, clipped to 0.3 to 0.75) for every hair texel. The wolf's own roughness from the 3D step was about 0.9; after the trial the middle half of the fur sat at 0.53 to 0.88 and a tenth was under 0.53 (`runtime/tmp/hair/wolf/roughness.png` against `orig_roughness.png`). Fur scatters light and should stay rough.
3. **The squarish mark**: not determined. I did not find it in the trial's renders at the sizes I looked at.
4. **The surface underneath is wrong too**: the wolf's lit "before" already shows faceted chest patches from fault 1 (`f4/wolf_trial2.png`, top row, second tile). Strand bumps on top of a wrong normal map look worse than on a right one.

### Keeping eyes, nose, mouth, claws and pads out

Painted detail stands out from fur by local contrast: the eyes, nose, lip line, claws and pads are small areas much darker (or lighter) than what surrounds them, while fur changes slowly. A keep-out map from the base colour alone (lightness against a blurred copy, the top 3 percent of texels) plus two shape rules (the front 6 percent of the body's length, the snout tip; the lowest 5 percent of the height, the paws) marks exactly those parts on the wolf (`f4/wolf_keepout.png`: red is the contrast rule, blue the shape rules). Grown by about 10 texels and faded, it goes into `paint_hair`'s existing `protect` input.

The trial run again on copies with that keep-out and with the 3D step's roughness left alone (`runtime/tmp/faults/f4_trial.py`, 50 seconds for the paint): the eyes, nose, lips and muzzle are untouched, the stripes through the face are gone, and the fur is no longer glossy (`f4/wolf_trial2.png`: rows are before, the first trial, the new run).

### Is it then clearly better than no fur pass?

Not clearly, in my judgement. Close up the chest and flanks read as fur with direction. At game distance the change is small, the chest stripes look a little combed, and the colour gets busier. It is worth a look by the owner on the wolf before deciding; it should not become the default on this evidence.

### Options, cheapest first

1. **Keep fur rough.** In `hair.paint_hair`, a `fur=True` path that leaves the roughness map as it is, or targets 0.85. No time cost. Risk: none found.
2. **The keep-out map.** A function `hair.fur_protect(mesh, base)` (the contrast rule plus snout and paws) passed as `protect`. About 3 seconds. Risk: a creature with strong painted markings (a tiger's stripes, a dalmatian's spots) would have its markings kept out too, which leaves them painted as they are: the safe side.
3. **Fix fault 1 first**, then judge the fur again; the bump on a correct normal map may change the verdict.
4. **Offer it as an option, off by default**: "Fur detail (experimental)" in the clean-up step's Advanced settings for quadrupeds, with the before and after picture already made for hair.

### Recommendation

Options 1 and 2 are small (under an hour). Do them after fault 1's fix, then rerun the trial and show the owner the wolf before switching anything on. Making fur good enough to be on by default is a larger piece that this research does not settle.

---

## 5. Characters face away in the Unity preview strips

### What is seen

In the snapshot strip of every Humanoid character, the very first picture shows the character facing the camera and every later picture shows the back, through every shared clip (fresh knight `library/.../a4d5cfde.../export/83e675d5.../unity_snapshots`, `f5_fresh_snaps.png`, `f5_pair.png`; Ranger `f5_ranger_wolf_snaps.png`, `f5_ranger_trio.png`). The Wolf (not Humanoid, its own clips) faces the same way in every frame.

### Is it only the camera?

**No, likely not.** The snapshot camera never moves between frames: `Snapshot` frames it once (`FrameShowcase`, a fixed world direction `ShowcaseCamDir = (0.5, 0.2, 0.85)`, in front of the model's +Z) and then only changes the animation time. The model in its bind pose faces the camera (the hair close-ups use the bind pose and show the face). So when a clip is playing, the body itself is turned about 180 degrees relative to its own GameObject. The snapshot plays the clips through a `PlayableGraph` on the prefab's own `Animator`, which is the same evaluation a game uses. A game that moves the character forward along its transform would show it walking backwards in idle and walk, which is a real defect, not a preview one.

The very first picture is the odd one out: it is the first evaluation of the first graph, which the code itself notes "can still show the bind pose"; it shows the clip's limbs but not yet its body rotation. So the strip's frame 0 is the misleading frame, not the others.

### The cause (likely, not proven)

`VgacImport.cs` imports the shared clip library (`clips.fbx`, from Quaternius's Universal Animation Library) as Humanoid and, for every looping in-place clip, `SetClipLoops` sets "Bake Into Pose" with `keepOriginalOrientation = true` (Unity's "Based Upon: Original"). It never sets `bakeAxisConversion`. Read back from the sandbox (`unity-sandbox/Assets/VGAC/Knight_hair_stage_1_a4d5cfde/clips.fbx.meta`): `bakeAxisConversion: 0`, `motionNodeName:` empty, 20 clips with `keepOriginalOrientation: 1`, 26 with 0.

The library's own setup sheet (`data/animations/ual1/Animation Library[Standard]/Unity_Setup.png`) asks for three things: turn on Bake Axis Conversion, Humanoid, and the root motion node set to the rig's root; its example shows Root Transform Rotation "Based Upon (at Start): Body Orientation". The importer does the second only. In Blender the library's rig object carries a half turn about the vertical axis (`Rig` rotation z = 3.142; `runtime/tmp/faults/f5_facing.py`). "Based upon original" bakes the file's own root orientation into the pose, and with that half turn and no axis conversion the body ends up facing backwards. "Body orientation" would line the body up with the GameObject's forward whatever the file says.

A prediction that a Unity test can check: the 20 baked looping clips (idle, walk, run and the other loops) face backwards; the 26 clips that are not baked (attack, hit, death, jump and other one-shots) face forward, because with root motion off their root turn is thrown away. In a game the character would turn round each time it attacks from idle. No existing strip shows a one-shot clip from the shared library, so this is not proven. (The Ranger's "beach walk" from video, his own file, faces a third way, about a quarter turn; that clip has its own root turn and does not settle the question.)

### What a Unity test would need

When the sandbox is free: import one character (the fresh knight) and, in an editor script, for each clip in the controller, evaluate it at half its length on an instance at the origin with no rotation, and record the angle between the hips' forward (or the cross of the shoulder line and up) and the instance's forward. Do it three times: as now; with `bakeAxisConversion = true` on `clips.fbx`; with `keepOriginalOrientation = false` on the baked clips. Then a Play Mode run of the controller with Speed set to walk while a script moves the instance forward, and one frame grab from the side. Expected now: about 180 degrees for the baked loops, about 0 for one-shots; after the change: about 0 for all.

### Options, cheapest first

1. **Import the library as its author says.** In `VgacImport.cs`: `ci.bakeAxisConversion = true` for `clips.fbx`, and in `SetClipLoops` `keepOriginalOrientation = false` (Body Orientation) for baked rotation, at least for the shared library. Raise the importer `Version` so user projects refresh. No time cost per asset. Risk: low to medium; the model's own clips (procedural moves, added moves) go through the same function and need the same test.
2. **A facing check in the importer**, as in the test above, reported per clip with a soft fail over 45 degrees ("Plays facing away from its forward"). About 1 to 2 seconds per asset. Risk: low.
3. **Fix the strip's first frame**: one extra evaluation at another time before the first picture, so frame 0 shows the real pose. No cost.

### Recommendation

Run the Unity test, then options 1, 2 and 3 together: small (under an hour of building, plus the test).

---

## Ranking

Value is how much it improves what a person gets; effort is building time.

| Rank | Fault | Value | Effort | Why this place |
|---|---|---|---|---|
| 1 | 1. Normal map green channel (plus skin never metal) | High: every character's face, neck and armour, in every engine | Small for new assets (under an hour); old assets need a clean-up rebuild | Biggest visible gain for the least work; also improves the wolf and the face pass |
| 2 | 5. Clips facing away | High if confirmed: characters walking backwards in a game | Small, plus a Unity test | A game-breaking fault if the test confirms it |
| 3 | 2. Blank cel faces | Medium: every long-haired Cel character, and a broken concept stops the whole chain | Small (one sentence, under an hour) | Proven fix with eight test pictures; only one look affected |
| 4 | 3. Beards and hair under hoods | Medium: bearded, hooded and hatted characters | Larger (about a day) | The nose fix is small and worth doing at once; the rest is real work |
| 5 | 4. Fur | Low to medium: close-up only | Small to make safe, larger to make worth switching on | Not clearly better at game distance yet |

## Files written for this research

All under `runtime/tmp/faults`:
- Fault 1: `f1_maps.py`, `f1_stages.py`, `f1_quant.py`, `f1_blender.py`, `f1_lit.py`, `f1_lit2.py`, `f1_metal.py`, `f1_metalview.py`, `f1_repair.py`, `f1_repair2.py`, `f1_conv.py`, `f1_conv2.py`, `f1_blender_flip.py`; pictures and numbers in `f1/` (key ones: `fresh_lit_variants.png`, `convention_test.png`, `convention_test2.png`, `blender_flip_green_mesh_first.png`, `blender_flip_green.png`, `lit_with_without_normal.png`, `metal_heads.png`, `normal_quant.json`). `repair_flatten*.png` show that simply flattening strongly tilted texels does not remove the patches (the wrong tilts are mostly small).
- Fault 2: `f2_gen.py`, `f2_sheet.py`, `f2_blank.py` and `f2_eyefeat.py` (the two blank-face measures tried; `f2_eyefeat_src.py` is the copied eye finder), pictures in `f2/img`, rows in `f2/runs.jsonl`, sheets in `f2/`.
- Fault 3: `f3_proto.py`, pictures in `f3/`.
- Fault 4: `f4_detail.py`, `f4_trial.py`, pictures in `f4/`.
- Fault 5: `f5_facing.py`, `f5_fresh_snaps.png`, `f5_pair.png`, `f5_ranger_wolf_snaps.png`, `f5_ranger_trio.png`.
