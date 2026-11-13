# Speed audit: where one asset's 22 minutes go, and what to change

Written 2026-11-13, after the realistic consistency test. Read-only audit: no code was changed, nothing was run on the
graphics cards. Every number is labelled **measured** (stored data or a log), **code** (read from the source), or
**inferred** (my estimate; say so when you quote it).

Sources: `library/vgac.sqlite` (job and version tables), `bakeoff/realistic_test/analysis/metrics/*.csv`, file write
times of the stored run M1-clothed (PC mid, one asset alone, 2026-11-13 06:07 to 06:28), `runtime/logs/backend.log`
(two assets in flight, 11:26 to 12:08), `runtime/logs/comfy-b.restart.err.log` (the 3080's engine), the ComfyUI
0.45.3 source in the local install, live read-only queries of both cards, and a web research pass (links in section 8).

## 1. Summary

### Time budget, one asset alone, PC mid (run M1-clothed, 21.9 minutes; all measured)

| Step | Total | Card busy | Processor only | Largest slices |
|---|---|---|---|---|
| Side views (on the 3060) | 6.5 min | 6.5 | 0.1 | 6 views x (61 s draw + 4 s cut-out) |
| 3D model (on the 3060) | 4.9 min | 2.5 | 2.3 | TRELLIS graph 151 s; colour match 68 s; checks 56 s |
| Clean-up (host + 3060 bursts) | 9.4 min | 2.3 | 7.1 | projection 200 s; sharpening 79 s; face close-up 57 s; face relayout 47 s; face paint 41 s |
| Rig (on the 3080) | 0.6 min | 0.25 | 0.3 | SkinToken 15 s |
| Export (host) | 0.5 min | 0 | 0.5 | Unity import 19 s |
| **Total** | **21.9 min** | **11.6 (53 percent)** | **10.3 (47 percent)** | waiting between steps: under 5 s |

Three facts that the step totals hide:

1. **Every card job of a single asset runs on the slower card.** `library/prefs.json` has `default_instance: "a"`
   (the 3060), and `jobs.route` tries the default card first for every step. The 3080 does the same drawing in about
   half the time (36 s against 58 s per view, measured; 31 against 61 in the logs) and sat idle 96 percent of the run.
2. **The drawing model's loaders run again for every single view.** The cut-out job between two views evicts the
   previous graph's cached loaders (ComfyUI's default cache; confirmed in the engine log and in its source).
3. **Close to half the run is single-machine processor work in plain numpy**, and the heaviest piece (profiled) is the projection's turn search: about 30,000 small mask warps done one at a
   time in float64. None of that needs a card to go faster.

### The five changes I would make first

| # | Change | One asset | Two in flight (each) | Confidence |
|---|---|---|---|---|
| 1 | Draw on the 3080 by default (pictures, close-ups, sharpening); 3D placement decided by one measurement | 3 to 4.5 min | 1 to 2 min | High for drawing (26 jobs), low for 3D on the 3080 (4 jobs, 2 fast and 2 slow) |
| 2 | Stop the per-view reload: cut-out inside the view's own graph, and/or `--cache-ram` with a second value | 0.5 to 1.5 min | same | Medium: cause is certain, size of gain is not measured |
| 3 | Sharpen the pictures while the 3D model builds (inputs exist when step 2 ends), on the other card | 1.3 min | 0.5 to 1.3 min | High on the mechanism; needs two cards |
| 4 | Make the projection's turn search and picture loading cheap, batch the rasteriser, float32 filters; one fidelity sweep instead of two in step 3 | 2.5 to 4 min | 3 to 5 min | Medium: the shares are profiled, the achievable factor is my estimate |
| 5 | Exports overtake a waiting clean-up; step 3's processor work releases the card first | 0 | up to 10 min for the asset that was stuck; 1.5 to 4 min of card time per asset | High |

Together, for one asset: roughly 22 minutes down to 13 to 15, with no change to any model, step count, resolution or
check. None of the five touches what is drawn or painted; items 2 and 4 need an output comparison to prove that.

### Biggest uncertainty

Whether the 3080 can hold both the drawing model and the 3D model in rotation without paying a reload each time.
The only cold load on record took 187 s. Two of four 3D builds on the 3080 took 55 to 60 s of card time and two took
178 to 192 s, and I cannot tell from stored data whether the slow ones were a reload, contention, or the settings
(they had a different triangle target). Experiment 1 in section 7 settles it in under half an hour and decides the
card layout for everything else.

Second: my offline timing of the projection ran while another session had all processor threads at 100 percent, so
its absolute seconds are not usable; only the shares are (section 2.4).

## 2. The time budget in full

### 2.1 Step 2, side views: 392.7 s (measured)

Job on the 3060. Six views, each one drawing job then one cut-out job (file write times):

| Slice | Seconds | Kind | Source |
|---|---|---|---|
| Draw one view on the 3060 | 60 to 62 | card | measured (file times; log says 56 to 61) |
| Cut-out (BiRefNet) | 4 | card | measured |
| Checks, copy, download | about 0.7 | processor | measured (gap between cut-out and next submit) |
| x 6 views | 393 | | |

The same on the 3080 (two assets in flight at the time): draw 25 to 35 s, cut-out 2 to 3 s (measured,
`backend.log` 11:27 to 11:31). Over the test: 58 s per view on the 3060, 36 s median on the 3080 (measured, B5).

Inside one draw on the 3080 (measured, engine log progress bar): about 8 s "model initialising" before the first
step, then 4 steps at 3.4 to 5.4 s each (about 17 s), leaving 5 to 10 s for text encoding, loaders and decode. The
3060's engine is run by ComfyUI Desktop and its log is not in `runtime/logs`, so the same split on the 3060 is
**not known**. Smallest measurement: read ComfyUI Desktop's own log for one view, or run experiment 2.

Model loading is inside these figures and cannot be separated from stored data. What is certain (measured in the log,
confirmed in code): for every view the engine prints `VAE load device`, `CLIP/text encoder model load device`,
`Requested to load QwenImageTEModel_`, `Requested to load QwenImage ... 840 patches attached`. That is the VAE
loader, the text encoder loader, the model loader and both LoRA loaders executing again, six times per asset.

Why (code, ComfyUI 0.45.3 `main.py` and `execution.py`): the default cache is "RAM pressure". After every node it
releases cache entries not used by the current graph whenever free memory is below the "inactive" threshold, whose
default is 100 percent of system memory. Free memory is always below total memory, so every entry from the previous
graph goes, model loaders first. The app alternates draw graph, cut-out graph, draw graph, so the draw graph's loaders
are always "previous". The cut-out's own loader reloads each time too.

### 2.2 Step 3, 3D model: 292.7 s (measured)

| Slice | Seconds | Kind | Source |
|---|---|---|---|
| TRELLIS graph on the 3060 (sampling, decode, remesh, decimate, unwrap, three bakes) | 151 | card, with processor work inside the engine | measured (`elapsed_seconds`) |
| Download maps, grow into seams | 13 | processor | measured (file times) |
| Colour match to the picture (`colourmatch.match_base_colour`) | 68 | processor | measured (file times) |
| Outline audit render (Blender) | 3 | processor | measured |
| Checks (`mesh.judge`: fidelity, colour, normal) | 56 | processor | measured (job end minus last file) |

The job holds its card for the last 140 s without using it (C16 already notes this; range over the test 90 to 268 s).

The graph's 151 s cannot be split between sampling and the engine's own mesh processing from stored data. Smallest
measurement: one build with ComfyUI's per-node timing visible (the engine's log, or the `/history` entry's
execution messages).

Redundant work (code): the colour match calls `texfidelity.score` (a sweep of camera turns, each one a full software
rasterisation), and the checks call `texfidelity.measure_for_stage`, which runs the same sweep again plus eight more
views for sharpness. One sweep, kept, would serve both.

On the 3080 the same graph: 55.5 s and 60.4 s in two jobs, 178 s and 192 s in two others (measured). Unresolved, see
the summary.

### 2.3 Step 4, clean-up: 561.4 s (measured, file times)

| Slice | Seconds | Kind | Note |
|---|---|---|---|
| Blender prep (decimate, rebake) | 14 | processor | 10 s inside Blender, rebake itself 6.6 s |
| Texture pass (measure, de-light, PBR) | 19 | processor | |
| Sharpening 8 pictures | 79 | card (3060) | one picture with the photo model 41 s, the other seven about 5 s each |
| Picture projection (`srcproject.apply_files`) | 200 | processor | 130 to 255 over the test |
| Find hair, find face | 9 | processor | |
| Face close-up draw | 57 | card (3060) | includes reloading the drawing model after the upscaler |
| Close-up guard | 13 | processor | |
| Face relayout (`faceuv.py`) | 47 | processor | UV packing about 38 s (two passes), bake 6 s, Blender start under 1 s |
| Paint the body again on the new layout | 20 | processor | |
| Fit and paint the face | 41 | processor | |
| Before and after sheet | 13 | processor | |
| Skin metal | 6 | processor | |
| Hair pass and final checks | 40 | processor | not split further |

Card 136 s (24 percent), processor 425 s (76 percent). The face pass total recorded by the app (197 s) is the rows
from "find face" to "sheet".

### 2.4 Inside the projection (code, plus an offline profile)

Code facts, certain:
- `backend/requirements.txt` has numpy and pillow only. No scipy, OpenCV, numba or torch in the backend.
- `face.rasterise` loops over triangles in Python (one iteration per triangle, 20,000 to 49,000 per view). It runs for
  every fitted view at 2048, for the head region, and inside `texfidelity`. It holds the interpreter lock, so the
  projection's six worker threads take turns through it.
- `srcproject.outline` (the turn search) was already vectorised; the full rasteriser was not.
- Images are float64 throughout. Blurs are three box blurs by cumulative sum; sampling is hand-written bilinear and
  bicubic; the optical flow is 3 x 60 iterations at each of 5 levels.
- `WORKERS = min(6, cores - 1)`. This machine has 8 performance cores and 4 efficiency cores (20 threads), not 20
  cores (measured, i7-12700K).

Offline profile (measured, with a caveat). `srcproject.apply_files` on a copy of M1-clothed's clean-up files, six
worker threads, every function wrapped with a timer. Another session had all processor threads at 100 percent, so
the run took 998 s of wall time against about 200 s in the app: **use the shares, not the seconds.** It also ran the
working tree's code, which is newer than the test's. Thread-seconds, inclusive:

| Where | Thread-seconds | Share of the view fitting |
|---|---|---|
| All view fitting (`fit_view`, 9 views) | 2099 | 100 percent |
| Turn search: `fit_similarity`, 204 fits, 29,801 mask warps through `face.sample` | 1101 | 52 percent |
| Rasteriser (`Raster`, `face.view_raster`, 60 to 73 renders) | 280 | 13 percent |
| Optical flow (`hs_flow`, 4 views) | 259 | 12 percent |
| Bicubic resample (`catmull`, 68 calls) | 218 | 10 percent |
| Outside the fitting: reading and resizing the pictures (`prepare_picture`, 10 calls) | 586 | |
| Outside: face region and head fit (`face_zone`, `fit_head`) | 268 | |
| Outside: compose, blend, finish | 119 + 84 | |

So the largest single cost is not the rasteriser, as I expected from reading the code, but the turn search: each of
204 candidate turns is refined by about 150 trial warps of a 256 x 256 mask, each a float64 bilinear sample done with
numpy fancy indexing. Reading the pictures is second. The rasteriser and the flow are about an eighth each.

### 2.5 Rig and export (measured)

Rig 34 to 63 s: SkinToken on the 3080 15 to 35 s, then renders, hit boxes, retarget, springs. Export 31 to 49 s:
Blender pack 8 s, Unity import 19 s, snapshots 6 s. Nothing here is worth work.

### 2.6 Waiting and the queue

- One asset alone: gaps between jobs are under 1 s each (measured).
- Polling: each card job is polled once a second (`ComfyClient.wait`), so each of roughly 30 card jobs per asset
  finishes on average half a second before the app notices: about 15 s per asset (inferred).
- Two in flight (measured, `backend.log`): the second asset's clean-up waited the full length of the first's (10 min);
  an export waited behind a clean-up; the face relayout took 108 to 123 s against 47 s alone, because two clean-ups'
  and two 3D steps' processor work overlapped.
- Showcase: clean-up 18.7 to 25.8 min. The texture pass alone went from 19 s to 154 s at 4096 (measured), which is
  the float64 filters scaling with pixel count.

### 2.7 Memory on the 3080 right now (measured, live)

Of 10 GB: the idle engine holds 1.45 GB; the desktop holds about 1.8 GB (window manager 0.61, the app's own window
0.35, a browser 0.23, the Claude app 0.23, others). The 3060 holds 0.24 GB. The Intel graphics already reports an
active 1920 x 1080 mode, so it may already drive a screen.

## 3. Opportunities, ranked

Each: what, where, gain and what it rests on, effort, accuracy risk and check, dependencies, one-card machines, how to
verify.

### O1. Draw on the faster card

- What: make the 3080 the home of pictures (step 2, clean-up's close-up and sharpening). Decide 3D by experiment 1.
- Where: `library/prefs.json` `default_instance` (today "a"); the built-in table in `config.py` already says
  turnaround on b, mesh on a, but the default card overrides it. For other machines: pick the default by a one-off
  timing of one view per card, not by memory size.
- Gain: step 2 from 6.5 to about 3.5 to 4 min (6 x 35 to 40 s). Close-up 57 to about 30 s. Rests on 22 jobs on the
  3060 and 4 on the 3080, same graph.
- Effort: a setting, plus a benchmark-and-suggest for other machines (small).
- Accuracy risk: none in principle (same graph, same seed gives the same class of picture; cards differ in floating
  point only). Check: redraw three stored assets with their seeds on the 3080 and compare the step 2 check numbers.
- Depends on: the timeout fix a1fe5f3 (done). Watch the first cold load (187 s once).
- One card: not applicable.
- Verify: `turnaround.run` median under 250 s on the job table.

### O2. Stop reloading the drawing model for every view

- What: two independent fixes; do both.
  a. Put the cut-out nodes at the end of the view graph (`graphs/qwen_edit.angle_graph` plus the nodes of
     `graphs/birefnet.cutout_graph`, two save nodes). Consecutive views then share every loader node, so the cache
     keeps them. The same for clean-up's bursts: one graph for all eight sharpenings (the upscale node takes a batch),
     and the close-up's helpers in the close-up's graph.
  b. Start both engines with `--cache-ram 10 24` (or similar): the second value is the free-memory level below which
     other graphs' cached loaders are dropped; today it is effectively "always". `runtime/start-comfy-a.ps1`,
     `start-comfy-b.ps1`. The 3060's engine is usually started by ComfyUI Desktop, which the scripts do not control.
- Gain: unmeasured. The 3080 spends about 14 of 31 s per view outside sampling; the floor is text encoding plus
  decode, a few seconds. My estimate is 5 to 10 s per view on the 3080 and more on the 3060: 0.5 to 1.5 min per
  asset. A public report of the same symptom on Windows saw 23 to 28 s runs become 120 to 190 s after a workflow
  switch, and names `--cache-ram 8 20` as a large improvement (section 8, link 2).
- Effort: small (a) and trivial (b).
- Accuracy risk: none (identical nodes, identical inputs). Check: same seed before and after gives the same image.
- Depends on: nothing. Helps a one-card machine the most, since everything alternates on one engine there.
- Verify: the engine log shows the loader lines once per job, not once per view; experiment 2.

### O3. Sharpen the pictures while the 3D model builds

- What: the eight pictures clean-up sharpens (the original, the built-from view, six drawn views) all exist when
  step 2 ends. Run the sharpening then, on whichever card is not building the 3D model, and store the results on the
  step 2 version; clean-up uses them if present and makes them itself if not.
- Where: `srcproject.upscale_sources` (move the card work out), `stages/turnaround.py` or a small follow-on job in
  `autopilot.py`; `jobs.py` for the lend.
- Gain: 79 s off clean-up (80 to 175 over the test). Certain on a two-card machine.
- Effort: medium (a new place for the files, a fallback path, the guardrail that clean-up waits for it).
- Accuracy risk: none (same model, same input). Check: byte-compare the upscaled files.
- Depends on: guardrails (item 19). Replaces planned item 20a, which only halves the same 79 s.
- One card: no gain (the card is building the model).
- Verify: `srcproject_upscale.seconds` near zero in clean-up.

### O4. Vectorise the processor work

- What, in order of expected return:
  a0. The turn search (`srcproject.fit_similarity`, `warp_mask`): 52 percent of the fitting. It warps a binary mask
     about 150 times per candidate turn. Options that keep the same search and the same score: sample the mask
     nearest-neighbour in float32 or uint8 with precomputed integer grids; score all eight neighbours of a step in
     one stacked call; or one `warpAffine` per trial if OpenCV is added. Also `prepare_picture`: ten pictures are
     read and resized to 2048 with the same hand-written sampler; load each once and resize with Pillow.
  a. `face.rasterise`: test triangles in batches by bounding-box size, as `srcproject.outline` already does, keeping
     the same inside test and depth rule. 13 percent of the fitting, and it also serves the face pass, `texfidelity`,
     the colour match and the checks.
  b. Float32 instead of float64 for images, and one fast separable filter for `face.blur`, `masked_blur`,
     `sample`, `catmull` (OpenCV's `remap`, `boxFilter` and `GaussianBlur` are the obvious candidates; that adds a
     dependency, numpy-only float32 is the no-dependency half).
  c. One fidelity sweep per 3D build instead of two (`colourmatch` keeps its `texfidelity.score` result for
     `mesh.fidelity_checks`).
  d. The face relayout packs twice with the exact "concave" packer (about 19 s a pass). Estimate the scale with the
     fast box packer and do one exact pass.
- Gain: (a0) alone should take a third to a half off the projection (it and picture loading are over half the
  profiled time, and both can drop by several times): about 1 to 1.5 min. With (a) and (b), 2 to 4 times on the
  projection, the face paint and the 3D checks: 2 to 3 min per asset on PC mid, far more on Showcase (the 4096
  texture pass took 154 s). (c) about 55 s. (d) about 20 s. The shares are profiled; the factors are inferred.
- Effort: (a0) half a day; (a) one day with tests; (b) one to two days; (c) and (d) an hour or two each.
- Accuracy risk: (a0) the search must pick the same turn and fit: assert the same `yaw`, `iou` and refusals on all
  stored runs (several views sit within 0.03 of the 0.85 limit, so even rounding differences can flip a refusal; keep
  the bilinear-then-threshold rule if nearest-neighbour changes any). (a) must be exact: assert identical triangle maps on the stored models. (b) changes results in the
  fifth decimal; check that the fit numbers (`iou`, yaw, `feature_match_flow`) and the painted share are unchanged on
  the 11 stored runs, and view the atlases side by side. (d) changes the layout slightly; check face texels across.
  Do not replace the optical flow's algorithm (see section 6).
- Depends on: nothing. Do it before the outward chain (item 10), which adds fitting passes on top.
- One card: full gain, and larger in proportion on machines with fewer cores.
- Verify: `srcproject_seconds`, `texture_pass_seconds`, the face pass seconds.

### O5. Queue: exports overtake, and step 3 lets go of its card

- What: (a) an export never waits behind a clean-up (planned in item 18; do this part first, alone). (b) the 3D
  job's last 140 s of processor work releases the card before it starts, so the next asset's side views or this
  asset's sharpening can begin.
- Where: `jobs.py` (`InstanceWorker._next_queued` for the host; a "card no longer needed" call in `stages/mesh.py`
  after the download).
- Gain: nothing for one asset. Two in flight: an export stops waiting 10 min; each 3D job frees 1.5 to 4.5 min of
  card time.
- Effort: small each. Accuracy risk: none. One card: (b) helps most there.
- Verify: `wait_s` of exports under 5 s; card idle time during `mesh.run`.

### O6. Start step 3 before step 2 has finished

- What: the 3D model is built from one picture, the three-quarter view (`mesh` `input_view`), not from the set. With
  two cards: draw front, quarter, quarter2 first (quarter2 is needed for the same-side check on quarter), then start
  the 3D build on the second card while the first draws left, right and back.
- Gain: up to the time of three views (about 1.7 min on the 3080) for one asset. But it only pays if the 3D build
  runs on the 3060 at 151 s; if experiment 1 shows the 3080 builds in 60 s warm with a cheap swap, running everything
  on the 3080 is about as fast and simpler. Decide after experiment 1.
- Effort: medium (step order, autopilot, guardrail: a redrawn quarter view after the build started means the build
  is thrown away).
- Accuracy risk: the drawing order changes, which matters once the outward chain (A1, item 10) is in: keep the
  chain's dependencies, only the order of independent branches moves.
- One card: no.

### O7. Small ones (seconds each, cheap)

- The negative prompt is encoded for every view although `cfg` is 1.0 and the sampler never uses it
  (`graphs/qwen_edit.py`, node `neg`: a second pass of the picture through the 7B encoder). Replace with a zeroed
  copy of the positive. Risk: none if `cfg` stays 1.0; verify same seed, same image. Gain: unmeasured, a few seconds
  per draw.
- Poll every 0.25 s, or use the engine's websocket: about 15 s per asset.
- The app's own window holds 0.35 GB on the 3080; see the display answer in section 4.

## 4. Direct answers

### Could Ollama or other local language or vision models improve speed or accuracy?

**Speed: no.** Nothing in the 22 minutes is a decision a language model would make faster. The Gemma judge is not in
the timed path at all: it runs only on request (`stages/judge.py`), for the style description, and for one optional
face check at the picture step. The retries that cost time (a redrawn view, a rejected close-up) are decided by
numeric checks that take milliseconds.

**Accuracy: in two narrow places, and Ollama is not the best tool for either.**

1. The checks that failed in the test were geometric: a view drawn from the wrong side, arms in a different pose, a
   face 47 mm off, a head turned 27 degrees. A general vision model answers these worse than a measurement. The app
   already has MediaPipe's face landmarker on the processor (`facemarks.local`); its head-pose matrix would give the
   head's turn directly for item 2 and item 8, and a small pose model on the processor (RTMPose through `rtmlib`,
   ONNX, Apache-2.0 code, check the weights' terms) would give limb positions for item 13 better than outline
   widths. These cost no card memory. This is where I would spend effort.
2. Warnings that need recognition, not measurement: glasses, hard light, "the outfit changed" (A7, C11). A small
   vision model can do these once, at the picture step. Compared with what exists: the Gemma file the app uses is
   24 GB through ComfyUI, and loading it on a card evicts the drawing model, which then pays a reload. A 4B-class
   model in Ollama or llama.cpp, pinned to the processor or to the 3060 (`CUDA_VISIBLE_DEVICES`, `keep_alive`), would
   not disturb the drawing card, and Ollama accepts a JSON schema with pictures (section 8, link 5). I found no
   trustworthy speed figures for these cards; it would need a ten-minute trial.

My recommendation: do not add Ollama now. It is another install for every user, and the checks worth adding are
better served by the two small specialised models. If the glasses and hard-light warnings prove unreliable with
simple measures, try a 4B vision model on the processor then, behind the existing judge interface.

Not known: whether any local vision model can reliably flag a doubled or melted face. If it could, it would be worth
having as a stop-and-ask check (C2). That is a one-hour trial on the 11 stored face sheets, on the processor.

### Are there card parallelisation opportunities beyond those planned?

- **Which model on which card: yes, the largest single gain (O1).** Pictures on the 3080. Where the 3D model goes
  depends on experiment 1.
- **Keeping models loaded: yes (O2),** and the drawing model should stay on one card through steps 2 and 4; today the
  upscaler and the cut-out graphs push its loaders out between uses.
- **Display on the Intel graphics: worth a free trial, not a top item.** It would return about 1.8 GB of the 3080's
  10 GB (measured now). The drawing model is 19.6 GB and never fits, so the gain is less weight streaming per step,
  not a different regime; I cannot put a number on it and found no measurement published. It matters more for 3D on
  the 3080 (a Showcase build already fits, with less to spare). Downsides: the desktop and browser then render on
  the Intel graphics; games and anything using the Meta virtual display need checking. Trial: experiment 5.
- **Two engines on one card: a trap.** One drawing job already uses the whole card and streams weights that do not
  fit; a second engine halves the memory of each.
- **Several views in one pass (batching): probably not worth it on these cards, unmeasured.** About 17 of 31 s is
  sampling; a batch of two doubles the activations while the model is already streamed in pieces. Once O2 removes the
  per-job overhead, the remaining gain is the sampling itself, which batching does not reduce on a saturated card.
  Per-item prompts also need a custom node. Listed as experiment 6, low priority.
- **Overlap for one asset: yes.** Sharpening during the 3D build (O3); the 3D build during the last three views
  (O6); the 3D step's report-only checks can run while clean-up starts (they change nothing clean-up reads; the
  colour match must finish first).
- **Processor work onto a card: not first.** The projection's cost is a Python loop and float64 filters; fixing those
  on the processor (O4) keeps results checkable against today's and helps every machine. A card rasteriser
  (nvdiffrast) needs a compiler toolchain on Windows and has a non-commercial-style licence; a torch port would add
  torch to the backend and compete with the drawing model for card memory. Revisit only if O4 leaves the projection
  above a minute. Blender's bakes take 6 s; not worth moving.

## 5. The planned parallel items

- **Item 17, step 2 on both cards.** With the 3060 as home card (today) it would save about 2 min. With the 3080 as
  home card (O1) an equal split saves almost nothing: front 31 s, then the 3080 finishes its two side views in 70 s
  and waits for the 3060's two (about 125 s), then the back: about 195 s against 210 s on the 3080 alone. Change it
  to an unequal split (the 3060 draws one view, or only `right`), or use the second card for the 3D build instead
  (O6). Do O1 and O2 first; they are cheaper and larger.
- **Item 18, second clean-up lane.** The overtaking export will pay at once; build it first, by itself. The second
  lane will pay less than hoped: clean-up is 76 percent processor work, the projection already uses 6 threads, and
  the machine has 8 performance cores. Measured with two assets in flight, the face relayout took 2.3 to 2.6 times
  longer. Expect two lanes to finish two clean-ups in perhaps 60 to 70 percent of the serial time, not 50. O4 first
  makes the lane both less necessary and more effective.
- **Item 19, guardrails.** Right, and needed for O3 and O6 too. Count performance cores, not threads: treat this
  machine as about 10, not 20.
- **Item 20a, sharpening on both cards.** Replace with O3. Half the 79 s is one picture on the photo model, so a
  split saves at most about 38 s, while moving the work into step 3's shadow saves all of it.
- **Item 20b, two close-up candidates.** Keep, as an accuracy item. It is not free in time when the cards differ: the
  pair is ready when the 3060's is (57 s against 31 s). Draw the second on the 3060 only if the first is rejected, or
  accept the 26 s.
- **Item 10, the outward chain.** It adds fits to a pass that already takes 130 to 255 s. Land O4 (a) before it.

Nothing planned should be stopped outright. The order I would use: O1, O2, O5 (a), O4 (c) and (a0), experiment 1,
O3, O4 (a)(b), then items 17 and 18 re-scoped by what the experiments show.

## 6. Considered and rejected

| Idea | Why not |
|---|---|
| Fewer than 4 drawing steps, or a 2-step distillation | No 2-step build exists for this model version. 4 is the floor of the published distillation; users disagree on 4 against 8 for quality, none report 4 as too many. Accuracy first. |
| Nunchaku (4-bit) build of the drawing model | Only community builds for this version, quality metrics "pending" for the 4-step files, and no confirmed way to apply the angles add-on on top. Wheels for this PyTorch version not confirmed. Revisit when an official build exists. |
| GGUF Q4 or Q5 of the drawing model | Reports are weak but point to slower steps than fp8 on a partly offloaded card and softer results. |
| `torch.compile` in the engine | Needs Triton, which is not installed and is not a supported Windows path; the engine's log shows it absent. |
| TRELLIS texture steps 24 to 12, or a lower cascade | The project already measured 12 steps as visibly softer; 24 was chosen for accuracy. |
| Skipping the sharpening | Measured in the project: front sharpness 0.47 without, 0.73 with. Move it (O3), do not drop it. |
| A faster photo upscaler for the original | The 41 s is spent on the one picture that carries the real face. Hide it in step 3's shadow instead. |
| Projection at 1024 instead of 2048 | It paints a 2048 atlas; halving it halves the detail the pass exists to add. |
| Replacing the hand-written optical flow with OpenCV DIS or a learned flow | The stiff field was tuned so armour and faces do not melt; a different algorithm moves paint. Speed up its primitives only (O4 b). |
| Skipping or sampling the checks | They are the only thing that caught the backwards model. Deduplicate them (O4 c), do not thin them. |
| Two engines per card; one engine across two cards | See section 4. Multi-card node packs spread memory, they do not run in parallel. |
| A long-lived Blender worker | Blender starts in under a second here; the time is in packing and decimation, not start-up. |
| Moving Blender bakes to a card | 6 s per bake set. |
| Gemma or another large vision model in the main path | 24 GB through the engine; evicts the drawing model. See section 4. |

## 7. Measurements I could not make, in priority order

Each fits in under an hour with the cards free.

1. **3D on the 3080, warm and after a swap.** With the 3080 idle: run one view (drawing model loads), then one PC mid
   3D build, then one view, then one build. Record each "Prompt executed" time. Tells: warm 3D time on the 3080, and
   the cost of swapping the two models on 10 GB. Decides O1's layout and O6.
2. **Cost of the per-view reload.** On one card: three views back to back with no cut-out between (queue the three
   draw graphs directly), then the same three with a cut-out graph between each. Then repeat the second with the
   engine started with `--cache-ram 10 24`. Tells: the size of O2, and which fix is enough.
3. **Projection profile on a quiet machine.** Repeat the profile of section 2.4 with nothing else running (about
   four minutes), then again with a cheaper `warp_mask`. Tells: true seconds per slice and the factor for O4 (a0).
4. **Where the 3D graph's 151 s go.** One build, reading per-node times from the engine. Tells: whether the engine's
   own mesh processing (remesh, unwrap, bakes) is a large share, which would cap the gain from a faster card.
5. **Display on the Intel graphics.** Move the monitor cable (or set the primary display), reboot, read the 3080's
   used memory at idle, run three views. Compare with today's 31 to 36 s.
6. **A batch of two views in one pass** on the 3080, same prompt for both as a first test. If it is not clearly under
   twice one view's sampling time, drop the idea.
7. **The 3060's own split of a 61 s view** (sampling against everything else), from ComfyUI Desktop's log.
8. **A small vision model on the 11 stored face sheets**, on the processor: can it tell the six unusable faces from
   the three usable ones.

## 8. Sources for the outside claims

1. ComfyUI start-up flags, cache modes and defaults: https://docs.comfy.org/development/comfyui-server/startup-flags.md
   (matches the local 0.45.3 source, `comfy/cli_args.py` and `main.py`).
2. Models reloading on every prompt after a workflow switch, Windows 11, with `--cache-ram 8 20` as a workaround:
   https://github.com/Comfy-Org/ComfyUI/issues/14276 . Cache and pinned-memory change:
   https://github.com/Comfy-Org/ComfyUI/pull/15027
3. Lightning add-on, 4 and 8 step files: https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning
4. Angles add-on, one pose per prompt: https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA
5. Ollama: card selection and keep-alive https://docs.ollama.com/faq , https://docs.ollama.com/gpu ; structured
   output with pictures https://docs.ollama.com/capabilities/structured-outputs
6. Nunchaku: https://github.com/nunchaku-ai/ComfyUI-nunchaku ,
   https://huggingface.co/nunchaku-ai/nunchaku-qwen-image-edit-2509 (official, older model version only)
7. nvdiffrast licence and Windows build: https://github.com/NVlabs/nvdiffrast
8. rtmlib (pose on the processor): https://github.com/Tau-J/rtmlib ; MediaPipe face landmarker's head matrix:
   https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker
9. The upscale node takes a batch: ComfyUI `comfy_extras/nodes_upscale_model.py`.

Not confirmed by any primary source, and treated as unknown above: published speeds of TRELLIS.2 on 10 to 12 GB
cards; memory freed by moving the display; speed of small vision models on these cards; Blender's card use for bakes
in background mode.
