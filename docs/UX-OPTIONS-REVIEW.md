# UX review of the new hand, pose and texture options (2026-11-12)

Scope: the options added with the hands work (docs/HANDS-PLAN.md, "Pose ready results") and the texture fidelity rounds: the second three-quarter view, Pose for 3D first, Close the mouth too, Open the hands first, Paint the picture onto the model, and the head and hand triangle weighting. Checked in the running app (backend on 8765, built frontend) on the project "Pose ready check 11-12" (Soldier cane pose on, Soldier cane pose off), and in the code.

Inherited decisions this review keeps:
- The owner's wording rule (STATUS.md, 2026-11-05): the standard term first, the plain word once in brackets. So "Weighted decimation (more triangles on the head)" keeps its form; new options that have no standard term get plain labels, like their neighbours "Build the head on its own" and "Match the face to the picture".
- Fine-tune's two layers: the simple layer (`PARAM_HELP[name].simple`, mock fallback `SIMPLE_PARAMS`) and an "Advanced (N more settings)" disclosure.
- The hint under a setting is `PARAM_HELP[name].help.meaning` from the backend; the "i" tooltip is `TERM_HELP[name]` in frontend/src/help.ts.
- docs/UI-REVIEW-2026-11-12.md is a separate, wider review; nothing here changes its findings.

## Root cause found on the way

Five of the six options had no `guidance.PARAM_HELP` entry, so the hint under each control was its own internal name ("pose ready", "hand edit", "source project", "hand triangles", "neutral mouth"). The careful descriptions in reference.py were never shown, and none of them had an "i" tooltip. The two face options next to them (`face_closeup_guided`, `face_sides`) had the same fault. A new test (backend/tests/test_option_help.py) now fails when a new option ships without help; three older options are listed as known gaps (`part_legs`, `head_sway`, `sprite_frames`).

A second fault affected every yes-or-no setting in Fine-tune: the hint sat between the box and its label, so the label ended up at the far right of the cell. The box, the label and the hint now read in that order.

## One row per option

| Option (param) | Before | After | Why |
|---|---|---|---|
| Three-quarter (other side) (`quarter2` in `views`; `second_quarter`) | Label fine. For a character it is drawn whenever Three-quarter is, but on a first build the box showed it unticked, and unticking it did nothing (the backend added it back). The views hint said the other views were "for you". | The box shows it ticked when it will be drawn. Unticking it sends `second_quarter: false` with the views (the backend already honours that), ticking it sends `true`. Hint: "Which sides to draw. For a character, Three-quarter (other side) is drawn too, so both cheeks get painted from a picture; untick it to skip it." | What you tick is now what you get, and the hint says why the view matters (the clean-up step paints from it). |
| Pose for 3D first (`pose_ready`) | In Advanced as "Pose the character for 3D first", hint "pose ready", no tooltip, no warning. | In the first layer of step 2, after the other view settings, labelled "Pose for 3D first (A-pose, open empty hands)". The hint itself carries both warnings: leave it off when extra arms, claws or tools are part of the design (it removes them); it can recolour a black-and-white photo and add gloves. Tooltip: best for hands in pockets, held canes, hidden hands; held things come back with Add an item; step 1 is not changed. Still off by default. | It is the change that gives separate fingers on candid photos, so it belongs where people set up step 2, and its risks have to be visible without hovering (the robot lost four tool arms; the black-and-white soldier came back in colour). |
| Close the mouth too (`neutral_mouth`) | In Advanced as "Relaxed closed mouth (with the pose edit)", hint "neutral mouth", always clickable. | Directly after Pose for 3D first, greyed with "Turn on Pose for 3D first to use this." while it is off, and turned off with it. Tooltip says it is new and little tried. | It does nothing without the pose redraw; now the screen says so and never sends a stale "on". It was never measured, so the help says to compare the face. |
| Open the hands first (`hand_edit`, step 3) | In Advanced as "Redraw the hands first", hint "hand edit", always clickable, even when step 2 had already posed the character (the build then skips it silently). | Kept in Advanced, labelled "Open the hands first (hands only)", placed after Head crop so the two head settings stay together. Greyed with "Step 2 already redrew this character with open hands (Pose for 3D first), so this is skipped." when the step 2 version it builds on used Pose for 3D first. Hint explains it against Pose for 3D first and warns about claws and robot hands. | Decision on the overlap: explain, do not hide or merge. It is the lighter fix (hands only, pose and held things meant to stay) for a step 2 made without the pose redraw, and merging would change pipeline behaviour. The greyed state removes the only confusing case. |
| Paint the picture onto the model (`source_project`, step 4) | A checkbox that showed unticked while the pass ran by itself (the soldier's clean-up shows `srcproject_applied: true`); "off" could not be chosen from an empty value. Hint "source project". | A three-way choice: "Automatic (on for characters on smooth looks)", On, Off, with "This version: painted from your pictures." (or "not painted") read from the version. Hint says what it does, the 2 minutes it costs, and when to turn it off (patches). Stays in Advanced next to Rebake. | The control now shows the real state, and Off is reachable. It runs by itself, so Advanced is the right place. |
| Weighted decimation (more triangles on the head) (`head_triangles`) | Label follows the owner's rule; hint started by repeating the label; tooltip said "twice the target" (it is 2.5 since phase 1). | Label unchanged. Hint is the plain meaning only; tooltip corrected to 2.5 and says turning it off also turns off the hands' share. | Correctness, and so the pair reads as a pair. |
| Weighted decimation (more triangles on the hands) (`hand_triangles`) | Hint "hand triangles", no tooltip, clickable when the head weighting was off (where the backend ignores it). | Sits right after the head one (unchanged order), greyed with "Works only while Weighted decimation (more triangles on the head) is on." when that is off; keeps its own tick so turning the head back on restores both. New tooltip. | The two now read as one pair with a visible dependency. |
| Pose for 3D first result (step 2 page) | The redrawn picture (`pose_ready_picture`) was never shown. Every view and the 3D model start from it, so the only way to see a removed arm or a recoloured photo was to infer it from the drawn views. | Step 2 shows "Redrawn for 3D (Pose for 3D first was on)": the step 1 picture next to the redrawn one, each opening full size, with what to check and how to undo it. Follows the Picture / Cutout switch. | The soldier's black-and-white photo against the coloured redraw is obvious at a glance. See item 6 below for what is still missing. |
| Neighbours with the same missing-help fault (`face_closeup_guided`, `face_sides`) | Hints "face closeup guided" and "face sides". | Hints taken from their existing tooltips. | Same root cause; text only. |

## Item 6: can the person see the redrawn picture before later steps?

After this change: yes, once step 2 has finished, at the top of the step 2 page. Not yet: while step 2 is running (the page shows "Waiting for the result..." until every view is drawn: 13 minutes on the soldier, while the redraw itself was ready after 40 seconds), and not before Autopilot moves on to the 3D build.

## Recommendations not implemented (they change pipeline behaviour or need a run on the graphics cards)

1. Make Pose for 3D first a step 1 result instead of a step 2 setting. Step 1 already has "Redraw it ready for 3D" (readiness.HUMANOID_REDRAW, an A-pose redraw without the hand words). Folding the pose-ready words into that fix, offered when a person's picture has hidden or holding hands, would give a normal step 1 version the person judges with Keep / Try again before anything else is drawn. That removes the need to look for the redrawn picture in step 2 and makes the overlap with Open the hands first disappear for new assets.
2. Until then, show the redrawn picture while step 2 runs: publish a version event after `turnaround._pose_ready_picture` registers its files (one `bus.publish_threadsafe` call), and let the running placeholder show `pose_ready_picture` with Stop next to it. About one minute in, the person could stop a bad redraw instead of waiting for all six views.
3. Have Autopilot pause after step 2 when Pose for 3D first was on, or add a soft check on the redraw (colour on a grey picture, a large change in outline area) that pauses it. Autopilot otherwise builds the 3D model from a redraw nobody looked at.
4. `srcproject.original_pictures` does not leave out the step 1 picture when step 2 redrew the pose; on the soldier it was refused by the outline gate (ambiguous turn), which is what kept the black-and-white photo off the coloured model. Leave it out explicitly when the lineage holds a `pose_ready_picture`, as is already done for an edited 3D picture.
5. Show the same Automatic / On / Off choice for the other yes-or-no settings whose empty value is decided for you (`pbr_fix`, `remesh`); on a first build they still show an unticked box. (`head_build` got it in this pass because it sits next to Open the hands first.)
6. Offer Pose for 3D first on the new-asset form when the picture is a photo, and in the step 1 readiness check when hands are hidden or holding something. Candid photos are the common input.
7. Add help for `part_legs`, `head_sway` and `sprite_frames` (listed as known gaps in test_option_help.py), and update the mock schema (frontend/src/mock) with `quarter2`, `pose_ready`, `neutral_mouth`, `hand_edit` and `hand_triangles`, so the demo shows these options.
8. Measure `neutral_mouth` on two or three faces before recommending it; no run has used it.

## Verification

- Frontend built (`npm run build`, tsc clean). Backend restarted with `backend/restart.ps1` while no job was queued or running.
- Tests: backend/tests/test_option_help.py (new, 6), plus test_headtris, test_pose_ready, test_handscore, test_reference, test_single_sources, test_api, test_turn_method, test_srcproject, test_hair_library_pipeline, test_great_assets_laneb, test_great_assets_phase45: 219 passed. Full suite: 1080 passed.
- In the app (1280 x 800), screenshots in docs/ux-options-review/:
  - before-step2-finetune.jpg, before-step2-pose-ready-closeup.png, before-step3-finetune.jpg, before-step4-finetune.jpg
  - after-step2-posed-picture.jpg: the step 1 photo beside the redrawn picture
  - after-step2-finetune-pose-on.jpg: the first layer with Pose for 3D first and Close the mouth too
  - after-step2-mouth-needs-pose.jpg: Close the mouth too greyed after unticking Pose for 3D first
  - after-step3-hand-edit-skipped.jpg (soldier, pose on) and after-step3-hand-edit-available.png (soldier, pose off)
  - after-step4-source-project.jpg: Automatic with "This version: painted from your pictures."
  - after-step4-hands-need-head.jpg: the hands' weighting greyed with the head's off
- The `second_quarter` request was checked by capturing the Apply request in the page with all writes blocked, so nothing was queued: unticking Three-quarter (other side) sent `{"views": ["front","left","back","right","quarter"], "second_quarter": false}`.

Not verified: the first-build view ticks on a character with no step 2 yet (no such asset on the real backend; the demo data's schema predates `quarter2`), a real run with `second_quarter: false`, and a screen reader or 200% zoom pass on the changed controls.
