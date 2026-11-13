# Realistic consistency test (2026-11-12)

Owner's goal: find out where Realistic humanoid assets break when made exactly as a user makes them, and what
decides it. One pass each, no touch-ups, through the app's screens only.

## Rules

1. Every run is made in the app's own screens (browser), with the settings the app offers by default. No fine-tune
   changes, no redo, no "try again", no backend commands, no scripts that start or alter a run.
2. One run per cell. A failed step is a result: record it and stop that run there.
3. The whole test runs on one code state. Record `git rev-parse HEAD` and `git status --short` at the start; if either
   changes or the backend restarts mid-test, note the run it happened before.
4. Reading is allowed: settings and results may be read from the library database and files after a run finishes.
5. No emoji anywhere. Local commits with explicit paths; never push.

## Subjects

All generated people are fictional adults (clearly over 25). Pictures go in `bakeoff/realistic_test/inputs/`, with
`subjects.json` (id, description, prompt, seed, size, face width in pixels).

| id | person | outfit | light |
|---|---|---|---|
| W1-clothed | woman 1 | everyday clothes (jeans, jacket) | flat, even |
| W1-bikini | woman 1 | bikini | flat, even |
| M1-clothed | man 1 | everyday clothes | flat, even |
| M1-shorts | man 1 | swim shorts, bare chest | flat, even |
| W2-clothed | woman 2 | everyday clothes | flat, even |
| W2-underwear | woman 2 | plain sports underwear | flat, even |
| M1-clothed-hard | man 1 | same clothes as M1-clothed | hard, bright, high contrast, strong shadows |
| W1-bikini-hard | woman 1 | same bikini | hard, bright, high contrast, strong shadows |
| P1-photo | real photo control: the T-pose man already in the library | as is | as is |
| W1-clothed-tall | woman 1, same clothes | flat, even; tall picture, 2048 x 3072 (drawn at 1024 x 1536, then the photo upscaler x2) | flat, even |
| W1-clothed-tall-face | the W1-clothed-tall picture plus a separate face close-up of woman 1 (`W1-face.png`, 1024 x 1024, head and shoulders) given at step 0.5 | flat, even |

Same person, different outfit or light: the only thing that changes in a pair is the thing under test. Each picture
is made the way the owner makes them: a base picture, then the Qwen-Image-Edit-2511 A-pose redraw whose workflow is
stored in
`library/projects/23cbb5a3-ca2e-412c-bd99-9e9d1cfdc324/assets/5a6a8525-c908-4128-8a54-96a1b754688b/concept/bb4b84f1-7775-4395-b2c5-372b6f200ea6/image.png`
(1024 x 1024, 4-step Lightning, the A-pose instruction in that file).

Resolution: the face pass draws its own close-up of the face whenever the face in the picture has fewer pixels across
than the texture wants (about 256 on PC mid, about 512 on High). A whole body in a 1024 x 1024 picture leaves the
face near 100 pixels. The two tall rows test whether a bigger picture, and a separate face picture, fix the face.

## Runs

Phase 1: the Realistic look, each of the 11 subjects on PC mid and on High (Showcase): 22 runs, through to the last
step the app reaches. Project names: "RCT Realistic PC" and "RCT Realistic Showcase"; asset name = subject id.

Phase 2 (after phase 1 is reviewed): three subjects repeated twice more with identical settings, for spread.

Phase 3 (after phase 1 is reviewed): other looks, two subjects each on PC mid.

## Record per run

In `bakeoff/realistic_test/runs.json`: subject id, look, quality, project id, asset id, the version id of every step,
start and end time of every step, every setting of every step as the app stored it, every check the app reported
with pass or fail, anything the app asked or warned on screen, and the step where the run stopped if it did.

## Evidence per run

The same pictures every time, in `bakeoff/realistic_test/evidence/<quality>/<subject id>/`: the input picture, the
side views sheet, and the finished model from front, left, back, right, plus a face close-up and each hand, front
and back. Fixed cameras, no chosen angles.

## Scorecard

Each line is 0 (broken), 1 (flawed but usable) or 2 (good), with one sentence of reason, judged from the evidence
pictures against the input picture.

1. Side views: same person, same outfit, same colours in every view.
2. Shape: body intact, no missing, fused or extra limbs, no holes or spikes.
3. Hands: separate fingers, right size.
4. Face shape: reads as the same person, features in the right place.
5. Face paint: natural, not garish, eyes and mouth on the geometry, no doubling.
6. Skin: even tone, no patches, seams or baked shadows.
7. Clothing and hair paint: matches the picture, sharp, no smears.
8. Back and sides: plausible, no front paint wrapped round.
9. Rig: stands and bends without tearing (if the run reached it).

Also per run: the first step where something visibly went wrong, and an overall verdict: usable as is, usable with
one redo, or not usable.
