# Changes to make after the realistic consistency test

Started 2026-11-13. The test in `docs/REALISTIC-CONSISTENCY-TEST.md` ended that day after 11 PC mid runs and 2
Showcase runs (the owner ended it early). Nothing below is built yet. Part A is what the owner has decided, part B
the evidence behind it, part C candidates from the analysis that are not decided, part D the face root-cause
findings (to be added). Mark an item done with its commit when it lands.

## A. Decided

### A1. Face painting: one reference, working outward from the front

Owner's proposal, agreed. In `backend/vgac/srcproject.py` and the clean-up step:

1. Fit the front picture to the model first. For the head, fit by eyes, nose and mouth (the landmarks the face pass
   already finds), not by the body outline alone.
2. Inside the face region, replace what the front picture did not paint with a plain skin tone, so no step 3 feature
   is left beside a painted one.
3. Fit every further view against what has been painted so far, not against the step 3 colour, working outward so
   each view shares as much surface as possible with the one before it:
   front; then the left side (`quarter`, then `left`) and the right side (`quarter2`, then `right`); then the back,
   against both sides; then the front again last, keeping priority.
4. Keep the side views off the face region itself.

First trial, because it is the cheapest and the two control runs point at it (B3): keep the picture projection off
the face region and let the face pass own the face. If that fixes most faces, the chain matters mainly for ears, hair
and the sides of the head.

To watch: a wrong front fit carries every view with it; small misfits add up along the chain, so check the back,
where the two sides meet, for a seam; paint landing off the sculpted nose or eye sockets is not fixed by any
repainting order.

### A2. Both three-quarter views for every asset

Step 2 supplies what clean-up needs. Today it draws both for humanoids by default (all 13 test runs had both).

1. Where a person switches the second three-quarter view off (`second_quarter: false`): warn only, saying the face
   may come out worse.
2. Draw the second three-quarter view for the other character categories too (`SECOND_QUARTER_CATEGORIES` in
   `backend/vgac/stages/turnaround.py`). Cost: about one more view's time in step 2 for those assets.
3. Older assets made before the second view existed: nothing to do, they are all test data.

### A3. Check all sides on every available graphics card

Step 2 should use all available cards, with a minimum of one.

- Today it is one job on one card, views drawn one after another (`stages/turnaround.py`, the loop over `todo`).
- Shape: front first, then the left side and the right side at the same time, one card each where two are free,
  then the back (see A5).
- To settle: the left and right views are checked against each other; the sheet method draws all views in one job
  and cannot be split; the drawing model is large for a 10 GB card and loading it pushes out what that card held.
- Evidence: per view, the 3060 took 58 seconds and the 3080 a median of 36 seconds, but 2 of 3 side-view jobs moved
  onto the 3080 timed out once and were re-queued (cause unknown). Look at that before relying on it.

### A4. A second clean-up lane

Clean-up and export share the single "host" worker (`backend/vgac/jobs.py`), so a second asset waits. In the test an
export (35 seconds of work) waited 10 minutes twice, and clean-ups waited 3 to 5 minutes.

- Clean-up holds little card memory for most of its run. Its card work comes in short bursts (sharpening the
  pictures, drawing the face close-up, hair), sent to a card directly and not through the app's queue; the rest is
  processor work (Blender, the picture projection with up to 6 worker threads, face and hair painting).
- Trial: two lanes, not more; each lane's bursts sent to a different card through the app's queue; exports allowed
  to overtake a waiting clean-up; a cap on worker threads per lane.
- Only when the machine has room (A5). The owner's machine has 20 cores and 96 GB; most will not.

### A5. Guardrails for all parallel work (A1, A3, A4)

1. Order is enforced, not assumed. A draw or a fit that relies on an earlier one does not start until that earlier
   result exists and passed. If it is missing or failed, the dependent work waits or stops with a plain message; it
   never falls back silently. The two sides of the chain are independent, which is what makes running them together
   safe. The same shape serves step 2 (drawing on the cards) and clean-up (fitting, which is processor work).
2. Look before starting. Before going parallel the app checks what is actually free: memory on each card, whether a
   card is busy with another asset, processor cores and system memory. With no room it runs one at a time as today.
   Starting point: `jobs.py` already polls each card's `system_stats` and already holds a job when memory is low.

### A6. Wrong-way facing in clean-up

Fix it, and add a check that can catch it (B2, C1).

### A7. Hold or warn when the face fit is poor; warn on hard light and glasses

- The app already records a face fit score and paints regardless (B1). Hold or warn below a limit.
- A note at the picture step that hard, high-contrast light gets baked into the model.
- A warning for glasses.

### A8. Hair library: download on first launch, then remove from the repository

1. Find where each hair style came from. Starting points: `backend/vgac/data/hairlib/CREDITS.txt` and
   `catalogue.json`, the `hair_packs` list in `setup/downloads.json`, `tools/hairlib_*.py`,
   `docs/HAIR-LIBRARY-PLAN.md`, `docs/HAIR-LIBRARY-RESEARCH.md`, and the source packs under `runtime/hairpacks/`.
2. Add code that gets the library on first launch (links to the source packs plus a build step, or a hosted copy of
   the built library). The app already copes with a missing library.
3. Update the setup scripts, which today say the library ships with the project.
4. Only then remove `backend/vgac/data/hairlib` (15.6 MB) from the repository and its history with a force push, as
   was done for the animation packs on 2026-11-13.

## B. Evidence from the test

One run per cell throughout, so a difference between two runs can be chance.

### B1. Scorecard (11 PC mid runs, judged from pictures)

- Usable as is: 3 (W1-bikini, W1-clothed-tall, P1-photo). Usable with one redo: 2 (M1-clothed, W1-clothed).
  Not usable: 6.
- Bodies are good: shape intact in all 11, no missing, fused or extra limbs, clean backs and sides.
- First visible fault is at clean-up (step 4) for five runs, at step 2 for the two hard-light runs, and at the face
  picture step for W1-clothed-tall-face.
- The subject decided the face more than the clothing: woman 1 best in all her runs, man 1 doubled in all three,
  woman 2 (glasses, loose curls) melted in both. Sparse clothing did not hurt the face.
- Face fit score: 0.23 to 0.57 on the four worst faces (W2-clothed, M1-shorts, M1-clothed, W2-underwear), 0.75 to
  0.92 on the rest.
- Every face's detail was invented: the face is about 88 pixels wide in a 1024 picture, the texture wants up to 512.
  The app drew its own close-up in all 11 runs, used it in 7, rejected it in 4.
- The real photo was no better on the face, so generated pictures are not the cause.

### B2. Facing

There is one facing decision, for the whole model, made from outlines only; the face pass has none and always looks
at the head from straight ahead (`face.head_camera(head, 0, ...)` in `stages/face_detail.py`).

- M1-shorts came out facing backwards as a whole model: face painted on the back of the head, animations walking
  away (`clips_face_forward` 178). Cause: step 2 drew the left and right views both facing the same way, so the two
  side outlines voted against each other and "turned 180" won by 0.011 (every other run: 0.067 to 0.16).
- W1-clothed: the step 3 model was built turned about 62 to 75 degrees. The 270 quarter turn was the right one, but
  the turn is snapped to the nearest quarter (`quality.auto_turn`), which left about 27 degrees, and the face pass
  painted as if the head were straight. (An earlier note here said the model faced front; that was wrong.)
- W1-clothed-tall and one other were about 15 degrees off for the same reason.

### B3. Two control runs: the face pass alone gives a clean face

On both Showcase runs the picture projection refused itself, so only the face pass painted the face, onto the soft
step 3 face. Both faces are clean: single features, no creases.

- Showcase W1-clothed (Realistic, valid): refused for "silhouette match 0.817 is under 0.85"; fit 0.81.
- Showcase W1-bikini: made with the Painted look by mistake (C6); refused for "no closer to the picture than the
  model's own colour". The Realistic PC run of the same picture, where projection painted 83 percent of the model
  first, has creases and a doubled mouth corner before and after the face pass.

Limits: two runs, both woman 1, both Showcase. On W1-bikini the rest of the model lost the picture's colour and the
edge of the painted face shows at the hairline, so "projection off everywhere" is not the fix.

### B4. Hard light

Shadows are baked into face and body (half the face black; legs and back dark brown against a pale chest). Step 2
draws different shadows in each view. The app's light score read 0.000 on the hard-lit bikini run.

### B5. Speed

- PC mid, one at a time: 21 to 24 minutes per asset (side views 6.9, 3D model 4.7, clean-up 10.2, rig 0.8,
  export 0.6). Two in flight: about 18 minutes each.
- Showcase: side views 8.4, 3D model 10.4, clean-up 25.8 minutes.
- A Showcase 3D build ran on the 10 GB card without running out of memory.

## C. Candidates from the analysis (not decided, not yet verified by the orchestrator)

1. Facing check is circular. `facing` in clean-up compares the turn applied with the same estimate that chose it, so
   it passed on all 11. The finish card said "front facing forward" on the run that faces backwards. Use
   `clips_face_forward` and the Unity facing number instead, and show them on the card.
2. The finish card hides most failures. `face_matches_picture` (7 runs), `metal_mixed` (2), `clips_face_forward`
   (1), `hair_matches_picture` (1) and the step 3 texture checks never appear on it; two runs with a failed face
   check got a clean card. No blocking check failed on any run, so autopilot cannot stop on quality. Show every
   failed warning, and add a short stop-and-ask list.
3. Metal on bare skin. Skin read as metal up to 0.97 before the fix and still 0.35 and 0.27 after on two runs
   (shiny copper skin on W1-bikini-hard). It also makes the hair pass stand back ("helmet"). Force skin and hair to
   non-metal.
4. De-lighting runs before the picture projection, which paints the pictures' shadows back in, and the light is not
   measured again afterwards. Measure and de-light after projection; add a measure that sees cast shadows.
5. Time estimates shown are stale: clean-up shows 20 seconds against a measured 10 minutes.
6. The project's look is not enforced. An asset was created with the Painted look inside a Realistic project and
   nothing warned. (Whether the app or the run session chose it is not established.)
7. `hitbox_cover` failed on 9 of 11 rigs: the capsules are fitted at the 80th percentile and the check asks for 0.85.
   It is the only warning on 9 finish cards. Align the two.
8. `face_matches_picture` shows sharpness against 1.0 whatever the reason it failed. Show the failing reason.
9. Silent skips: the picture upscaler failed on all 8 pictures of one run with no note (after the reboot); one run's
   texture checks vanished when the fit was poor. Retry or say "not measured".
10. A bigger picture is not used as a paint source: the tall picture's views were refused at outline match 0.55 to
    0.57 against 0.85, and its upscale cost 175 seconds. Make the fit accept the tall shape, or do not offer it.
11. The separate face picture changed the outfit and hair on W1-clothed-tall-face with no check. Compare the
    composed picture with the inputs.
12. Finger bones vary from 0 to 40 between runs (mitten hands on M1-clothed) and the Unity avatar check still
    passes. Cause unknown.
13. Paint flaws no check flagged: a rust-brown stain at a pocket, hem or collar on three runs; pale stripes on bare
    thighs and arms (W2-underwear); glossy blotches (M1-shorts); sleeve colour on a palm.
14. The front side view is drawn arms-down on the woman 1 runs while the other views are A-pose (outline match 0.43
    to 0.51 against the model). No check looks at pose. Effect unmeasured.
15. About 60 percent of the texture sheet is empty on every run. A repack could add detail. Layout code not examined.
16. Step 3 colour and sharpness warnings that step 4 always repairs: relabel or suppress. The 3D job also holds its
    card for 1.5 to 4.5 minutes of processor work after the card work is done.
17. Dead checks that could not have failed on this data: mesh `triangle_budget` (limit 49,000 against a 20,000
    budget), `normal_map_upright`, turnaround `coverage` and `height_ratio`, `hitbox_overreach`.
18. The Unity strip picture shows the same pose in every cell, so it is not evidence that the animations play.

## D. Face root-cause findings (analysis of 2026-11-13)

Evidence, scripts and pictures: `bakeoff/realistic_test/analysis/face/` (not in git). Method: the clean-up's picture
projection was run again offline for five runs and each view painted on its own, to see which view put each doubled
feature there. Spot-checked by the orchestrator: the same-way side views of M1-shorts (picture), the squashing of
non-square pictures, the face pass's fixed straight-ahead view, the quarter-turn snap and the 0.55 mirror limit (code).

### D1. What caused the bad faces

The face sheet's "before" column is the model after the picture projection and before the face pass. In the five
runs examined every doubled feature traces to a picture in the wrong place, or a picture that should not have been on
the face at all. Differences between the drawn views' faces explain little of it for now.

| cause | runs (of 5) |
|---|---|
| No frontal picture reached the face (original and drawn front both refused) | 3: M1-clothed, M1-shorts, P1-photo |
| Full side or back views painting inside the face | 3: P1-photo (36 percent of the eyes-nose-mouth box), M1-shorts, W1-clothed |
| Facing wrong or left over | 2: M1-shorts, W1-clothed |
| A view drawn from the wrong side in step 2 | 2: M1-shorts (left and right), M1-clothed (both three-quarter views) |
| Step 3 colour left on more than a fifth of the face | 2: M1-clothed (46 percent), W2-clothed (23) |
| Front picture misplaced on the head by the whole-body fit | 1: W2-clothed (47 mm at the eyes, 12 to 19 mm after the local correction) |

The face pass made things worse in three of the five, because it painted straight ahead onto a head that was turned
or already wrong. This fits the two control runs (B3): onto a clean head it paints a clean face.

Other facts:
- A picture that is not square is squashed to a square before it is fitted (`srcproject.prepare_picture`), so it
  fails the outline match and is refused. The tall picture scored 0.552 squashed against 0.843 padded; the real
  photo 0.577 against 0.921. So neither the tall picture nor the real photo ever painted the body or face, and the
  tall rows of the test did not test what they were meant to.
- The separate face picture never reaches the texture. It only guides a redrawn close-up, which adds fine detail.
- The drawn front view is a poor stand-in for the original: step 2 changes the pose (arms down), and it fitted in 1
  of the 4 runs where it was tried.
- The second three-quarter view was refused in 5 of 11 runs, the first in 2. Nine refusals sat between 0.82 and
  0.85 against the 0.85 limit. Each refusal hands more of the face to a full side view and breaks the chain in A1.
- The structure agreement gate removed nothing in any view of any run.
- Glasses are sculpted as solid pieces; the depth-edge gate then keeps every picture off the eyes.
- Shape limit: once placement is right, about 3 to 4 mm remains at eyes and mouth on W2-clothed and about 7 mm on
  M1-clothed, because the sculpted face is shorter than the picture's. No repainting order removes that.
- Fit score threshold: the 11 scores are 0.23, 0.31, 0.40, 0.57, then 0.75 and up. Hold below 0.65. Not enough alone:
  three flawed faces score 0.75 to 0.80; `face_matches_picture` failed on those three as well, so use both.

### D2. Punch list: face, body and parallel work in one build order

One list for everything decided (owner, 2026-11-13). Items 1 to 12 came from the face analysis; 13 to 19 were added
the same day so the body and the parallel work are covered too. Earlier items remove causes that later items depend on. "New" means it was not in part A before the analysis.

1. Facing (A6). A toes test (direction from shins to feet, seen from above) as tie-break and as a check that can
   fail; hold when it disagrees with the outline decision by more than 60 degrees. On the 11 runs it separates
   cleanly. Untested on robes, skirts and non-standing poses: fall back to today's decision when feet are not found.
   `stages/prep.py resolve_facing`, `quality.py`. New: the test itself.
   **Done 2026-11-13**, commit 446f695 (`vgac/facing.py`). Toes test as tie-break (outline margin under 0.04) and when the tests differ by more than 60 degrees; new `facing_tests` check fails when they disagree. Measured on the 11 runs: M1-shorts turn 180 becomes 0 (toes 0.0, outline margin 0.0107), the other 10 unchanged, `facing_tests` fails on M1-shorts only; toes found on all 11 PC runs and both Showcase runs (feet splay 32 to 84 degrees apart; the limit is 110), and on 60 of 75 stored humanoid models; the other 15 (legs together or hidden by a robe) fall back to the outline. Across all 86 stored mesh models only M1-shorts changes turn. Verify: `tools/verify_face_d2.py a`.
2. The face pass looks where the face is: give it the turn the first picture was fitted at, instead of straight
   ahead. `stages/face_detail.py`. New.
   **Done 2026-11-13**, commit 5a1097f (`face_detail.HEAD_YAW`, `prep.resolve_facing` `face_remainder_deg`). Remainder read from the feet when found. Measured: W1-clothed head yaw -33.4 against -27.5 from the fitted source picture; W1-clothed-tall -23.3; the other runs within 3 degrees of straight. Not changed: the hair pass and skin-metal pass still look from straight ahead (`hair_detail.py:340`, `hair.py`, `skinmetal.py`). Verify: `tools/verify_face_d2.py b`.
3. Step 2 redraws a side or three-quarter view drawn from the same side as its partner (as-drawn outline overlap
   higher than mirrored overlap). `stages/turnaround.py view_checks`. New. This also keeps the chain in A1 whole.
   **Done 2026-11-13**, commit 79b35ff (`turnaround.same_side_check`, `imaging.pair_overlap`). Flags a view whose as-drawn overlap exceeds the mirrored one by more than 0.05; the existing auto-reroll (2) redraws it. Measured on 26 stored pairs: flags M1-shorts left/right (0.884 vs 0.599) and M1-clothed quarter/quarter2 (0.700 vs 0.550), nothing else (closest good pair -0.017). The 0.55 limit is unchanged and still applies to left/right only. Verify: `tools/verify_face_d2.py c`.
4. Pad pictures to a square instead of squashing them. `srcproject.prepare_picture`. New. Small and safe.
   **Done 2026-11-13**, commit d9356ff (`srcproject.square_pad`). Outline match of the original: tall picture 0.552 to 0.843, photo 0.574 to 0.921 (needs 0.82), square pictures unchanged. Verify: `tools/verify_face_d2.py d`.
5. Keep left, right and back views off the face region. `srcproject.project_view`. (A1 step 4.)
6. Fit the head on its own by its features, and do it even when the body fit is refused. `srcproject.paint`, reusing
   `face_detail.fit_picture`. (A1 step 1; "even when refused" is new.) Needs the landmark finder during projection.
7. Skin tone under unpainted face texels, respecting hair, beard and glasses. `srcproject.finish`. (A1 step 2.)
8. Refuse a drawn view whose fitted turn contradicts the front (a back view near the front, left and right on one
   side). `srcproject.paint`. New.
9. Hold the face paint when the fit score is under 0.65 or `face_matches_picture` fails, and say so (A7).
10. The outward chain (A1 step 3), for the whole model and not only the head: every further view is fitted against
    what has been painted so far, front first, then the left side (`quarter`, then `left`) and the right side
    (`quarter2`, then `right`) at the same time as two processor threads, then the back against both, the front
    keeping priority. Guardrails in A5 apply: a view waits for the one it depends on. Last among the painting items:
    it needs 3, 6, 8 and 13 first, the projection already takes 130 to 255 seconds, and the local correction still
    cannot line up a face on its own.
11. Say so when the upscaler fails; review the 0.85 outline limit for three-quarter views (measure first); drop or
    re-scale the agreement gate. New.
12. Warn on glasses and hard light at the picture step (A7).

Body (added after the W1-bikini rerun, where clean-up streaked the sides and back while the front and face were good):

13. Step 2 holds the pose. Each drawn view's outline is compared with the model from that angle (or with the pose of
    the front), and a view whose arms or legs are in a different place is redrawn. In the rerun the front and side
    views had the arms down and the back view had them out; painting them on left streaks down the arms and torso and
    costume colour on the hand and hip. `stages/turnaround.py`. Also covers C14.
14. Side and back views on the body: fine detail only, or only where they agree with the model. The "detail" mode
    exists (`source_project_mode`, `srcproject.MODES`) but is not the default and has no control in the app; the
    agreement gate exists but removed nothing in any run. Decide after the comparison clean-up of the W1-bikini
    rerun (version cdc48e26): default for realistic characters, a control in the fine-tune panel, or a finer gate.
15. Items 5 to 8 have body counterparts and should be built as one rule, not a face-only one: a view never paints a
    region that a better-placed view owns, unpainted skin takes the tone of the painted skin beside it, and a view
    whose fitted turn or pose contradicts the front is refused.
16. Draw the face close-up again with a new seed when it is rejected, before falling back to the soft face. The same
    picture gave 271 texels across the face in the first run (close-up rejected at likeness 0.88 against 0.93) and
    451 in the rerun (accepted). About a minute and a half per retry. `stages/face_detail.py`.

Parallel work (A3 to A5; not in the face list before):

17. Step 2 on both cards: front first, then the left side and the right side at the same time, one card each where
    two are free, then the back. First find out why 2 of 3 side-view jobs moved onto the 3080 timed out once.
18. A second clean-up lane, with each lane's card bursts sent through the app's queue to a different card and
    exports allowed to overtake a waiting clean-up.
19. The guardrails for 10, 17 and 18 (A5): order enforced with a plain message when a dependency is missing; check
    free card memory, busy cards, processor cores and system memory before going parallel, else run one at a time.

Test plan after building: rerun the same 11 pictures on PC mid, plus the tall rows once item 4 is in, and score
them with the same scorecard (`bakeoff/realistic_test/analysis/scorecard/`).
