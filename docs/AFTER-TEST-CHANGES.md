# Changes to make after the realistic consistency test

A running list, started 2026-11-13. Nothing here is started while the test in `docs/REALISTIC-CONSISTENCY-TEST.md`
is running: the test needs one unchanged code state. Add to the end; mark an item done with its commit when it lands.

## 1. Check all sides on every available graphics card

Owner, 2026-11-13. Step 2 (Check all sides) should use all available cards, with a minimum of one.

- Today the step is one job on one card; the views are drawn one after another (`backend/vgac/stages/turnaround.py`,
  the loop over `todo`). It averaged 6.9 minutes over the first 7 test runs.
- Each view is drawn from the input picture with its own seed, so views can be drawn on different cards with the same
  model and settings. Expected saving: up to about half the step. Expected quality change: none, but unmeasured.
- To settle before building: the front view has to land first (the other views are checked against it); the
  left and right views are checked against each other; the sheet method draws all views in one job and cannot be
  split; the drawing model is large for a 10 GB card and loading it pushes out whatever that card held.
- Evidence to use: the test records which card ran each step, so runs whose Check all sides ran on the 3080 can be
  compared with runs on the 3060.

## 2. Hair library: download on first launch, then remove from the repository

Owner, 2026-11-13.

1. Find where each hair style came from. Starting points: `backend/vgac/data/hairlib/CREDITS.txt` and
   `catalogue.json`, the `hair_packs` list in `setup/downloads.json`, `tools/hairlib_build.py` and the other
   `tools/hairlib_*.py`, `docs/HAIR-LIBRARY-PLAN.md`, `docs/HAIR-LIBRARY-RESEARCH.md`, and the source packs kept
   locally under `runtime/hairpacks/`.
2. Add code that gets the hair library on first launch (download links for the source packs and a build step, or a
   hosted copy of the built library). The app already copes with a missing library
   (`hairlib.py`: "The hair library is not installed in this copy of the app").
3. Update the setup scripts, which today say the library ships with the project (`setup/Check-Setup.ps1`,
   `setup/Setup-Extras.ps1`).
4. Only then: remove `backend/vgac/data/hairlib` (15.6 MB) from the repository and from its history with a force
   push, the same way the animation packs were removed on 2026-11-13.
