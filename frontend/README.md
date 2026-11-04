# VGAC frontend

Vite + React 19 + TypeScript + react-router 7 + three 0.187. Plain CSS (`src/styles.css`, dark neutral UI, 8 px spacing scale, Inter or system font). No Tailwind, no emoji (the info icon is an "i" in a CSS circle).

## Run

    npm install              # npm 8.3 is old; for new packages use: npx -y -p npm@latest npm install <pkg>
    npm run dev              # against the real backend on 127.0.0.1:8765 (Vite proxies /api, including the WebSocket)
    npm run dev:mock         # against the in-browser mock (vite --mode mock, reads .env.mock -> VITE_MOCK=1)
    VITE_MOCK=1 npm run dev  # same thing from bash
    npm run build            # tsc -b && vite build -> dist/ (served by the backend at / when present)

Mock mode (`VITE_MOCK=1`):

- `src/mock/index.ts` patches `window.fetch` for `/api/*` and `window.WebSocket` for `/api/ws/events`, so the real client code paths (including live job progress) run unchanged. It also answers `GET /api/suggest` and `POST /api/assets/{id}/variants`.
- `src/mock/data.ts` seeds two projects and four assets; "Knight" has versions in every stage (concept, turnaround, mesh, prep, rig, export) with files and metrics in the real backend's shape (checks are `{id, name, passed, ok, value, limit, message}` plus `help`, `fix` and `severity` on some entries; `gate_ok` on 3D stages). The knight's prep version fails two checks on purpose so the "Fix automatically" buttons show. "Knight - winter cloak and fur boots" is a variant of the knight. New runs, refines and adjusts create queued versions whose jobs progress over a few seconds and then get fabricated files and metrics.
- `src/mock/images.ts` draws placeholder PNGs with canvas at runtime (concepts, five-view turnarounds, cutouts, textures, bend-test strip, sprite sheet). They are registered as data URLs behind library paths via `setLibraryResolver`.
- The sample GLB is `library/samples/sample.glb`; in mock mode `vite.config.ts` serves `../library` at `/api/library/*` so no backend is needed.
- The store is in memory and resets on reload (ids change on every reload). `window.vgacMock.store` exposes it; `window.vgacMock.setSpeed(4)` speeds up simulated jobs. A concept whose `prompt_override` contains "fail" fails, to exercise error states; the "Treasure chest" asset has a failed turnaround with a ComfyError-style message.

## The plain layer and the technical layer

The UI speaks to a layperson by default and keeps every technical name one click or hover away (UX-REVIEW.md, items A to F plus Variants).

- `src/help.ts` is the help registry. Every stage, parameter, metric, check, model, platform, category and style has an entry `{plain, meaning, why, technical}`:
  - `STAGE_TEXT`: plain name, primary verb, gerund (for status lines), help and a fallback time estimate per stage. `stagePlain()`, `stageVerb()`, `stageHelp()` and `stageEstimate()` prefer the backend's `plain_name`, `plain_verb`, `help` and `estimate_seconds` from `GET /api/stages` and fall back to the registry; `stageEstimate()` also accepts the `estimates` map from `GET /api/suggest`.
  - `MODEL_HELP`, `PLATFORM_HELP` (with a one-line `consequence`), `CATEGORY_HELP` (what it is and how it will be rigged), `STYLE_HELP`, and `TERM_HELP` (parameters and metrics by bare name: seed, steps, cfg, triangles, manifold, watertight, lod, collider, skin weights, clip, lora, cutout, turnaround, silhouette IoU, and so on).
  - `checkSentence(check, ctx)` turns a check into one plain sentence: `check.help` from the backend wins, otherwise a per-id template fills in `value` and `limit` (texture_*_pow2/square/budget and lodN_ratio are matched by pattern), otherwise the raw message. `checkFix()` returns `check.fix` when the backend sends it, or a small registry fallback (triangle_budget -> mesh target_faces, manifold/watertight -> mesh remesh). `checkSeverity()` uses `check.severity`, else treats budget and view-consistency checks as warnings.
  - `looksGood(metrics)` and `firstFailing(metrics)` drive the candidate-card traffic light (`gate_ok` first, then `consistency_ok`, then the checks list).
  - `humanError(raw)` maps raw errors to a sentence: ComfyError/interrupted/out of VRAM -> "The generator stopped; this usually fixes itself. Try again."; Blender/Unity -> "The clean-up step failed on this model. Try again or change something."
  - `SIMPLE_PARAMS` lists the Fine-tune fields shown by default per stage when the schema has no `simple` flags; `isSimpleParam()` uses the backend's `simple` flag when any entry carries one.
  - `suggestFallback()` builds a `{model, model_label, reason, budgets, estimates}` suggestion from the reference data when `GET /api/suggest` is missing; `totalEstimate()` sums the per-stage estimates. `EXAMPLE_PROMPTS` and `PROMPT_TEMPLATE` feed the asset form; `VARIANT_EXAMPLES` feeds the variant panel.
  - `helpFor('kind.name')` is the generic lookup used by the Info component: `stage.concept`, `param.seed`, `metric.triangles`, `check.manifold`, `term.cutout`, `model.zturbo`, `platform.mobile_low`, `category.humanoid`, `style.painted`.
- `src/components/Info.tsx`: `<Info term="term.seed"/>` renders the info icon with a tooltip (plain name, meaning, why it matters, technical name in monospace). Opens on hover and keyboard focus, Escape closes, flips to the right near the viewport edge, renders nothing for unknown terms. `Field` takes a `term` prop to add one next to its label.
- Simple and Advanced: the Fine-tune form (`FineTune` in `src/pipeline/StagePanel.tsx`) shows the simple fields first (concept: painter, Size as one select that sets width and height, Pose for 3D; turnaround: views; mesh: builder and "Detail level" as a select relative to the platform budget; prep: height and mirror; rig: skeleton type; export: detail levels, collision shape, formats, sprite sheet) and the rest under an "Advanced" disclosure, each with its technical name in the hint. Only changed parameters are posted to `/api/versions/{vid}/adjust`. Raw metrics, the raw check list, params, files and "Recompute checks" live under "Details" beneath the version; the job log is behind the "Details" toggle in the asset header.

## Screens

- `/` Projects: three-sentence empty state (project, asset, version), list, create (platform targets with plain names and a one-line consequence), and the style lock editor for the selected project (style words, painter, style add-ons, palette, avoid) with Info icons on every field. Style presets are a later item.
- `/projects/:pid` Assets: grid with concept cutout thumbnail, name, category and six stage-progress dots; variants are grouped in a strip under their base asset (by `base_asset_id`). The create form makes the description the only required field (template placeholder, two example prompts per category to click), defaults category/look/platform from the project, shows "We suggest <model>, because <reason>; about N minutes to a finished asset" from `GET /api/suggest` (updates as you change category, look or platform), and keeps name, painter and parts under "Advanced". The painter chosen there is remembered per asset in localStorage and applied to the first concept run, because the create endpoint has no model field.
- `/assets/:aid/:stage?` Pipeline. Left: progress strip with plain stage names (current highlighted, future stages quiet, technical name in the title and an Info icon beside each). Header: one status line driven by the active job ("Building the 3D model, about 2 minutes left, step 3 of 6"), or the last error as a sentence with "Show details", or "Nothing running. N of 6 steps done."; "Make a variant" and the job-log "Details" toggle on the right. Main: the "Your next step" guide card (what just happened, what to do now, the one primary action with a time estimate; states: blocked, empty, running with progress and Cancel, error with Retry and raw details, review, selected, finished with a download), then the stage section: candidate cards (thumbnail, traffic light "looks good" / "needs attention" with the first failing sentence in the tooltip, short id), the viewer, plain-language quality checks with "Fix automatically" (adjust on this version when the fix stage matches, otherwise `run` on the named stage and navigate there), "Happy with this?" with "Yes, keep it" (the primary button while nothing is kept), "Try again" (with a count for concept), "Change something" (opens "Describe the change" and, below it, "Fine-tune"), a small Reject, and "Details".
  - First-run overlay: three steps, shown once (`vgac.guideSeen` in localStorage, `src/guide.ts`); "Show guide again" in the top bar reopens it.
  - Stage viewers: concept picture with cutout toggle; turnaround five views with a one-line explanation, per-view traffic light, per-view plain checks and "Redraw this view"; mesh/prep with the Three.js viewer, triangle count, outline match per view and texture thumbnails with plain names; rig leads with the animated model (anim_glb when present) and the bent poses; export leads with "Your asset is ready" and the download buttons, with the detail levels and the Unity report under "What is inside".
  - Variants: "Make a variant" opens a panel ("What changes?" with examples, optional name defaulting to "<base> - <change>") that posts `POST /api/assets/{aid}/variants` and navigates to the new asset, where the guide says "We are repainting <base> with your change" while the refine runs; the rail shows "variant of <base>" with a link back.
- `/health` the phase 0 health panel; the top bar shows a compact version.

## Code map

- `src/api.ts` typed client for every endpoint in `docs/API.md` (plus `suggest`, `createVariant`), `libraryUrl()`, stage helpers, the `AdjustParam` shape (with `help` and `simple`), the `Check` shape (with `help`, `fix`, `severity`) and `Asset.base_asset_id` / `variant_change`.
- `src/help.ts` the help registry (above). `src/guide.ts` first-run flag and the per-asset preferred painter.
- `src/events.ts` WebSocket event hub with the polling fallback; `useEvents`, `useEventMode`.
- `src/refdata.ts` cached categories / styles / platforms / stages.
- `src/Viewer.tsx` the GLB viewer; `src/glbSnapshot.ts` offscreen thumbnails.
- `src/pages/*` the three screens; `src/pipeline/GuidePanel.tsx` the next-step card and `guideState()`; `src/pipeline/StagePanel.tsx` candidates, viewer, checks, Happy with this?, Change something, Fine-tune; `src/pipeline/views/*` per-stage viewers; `src/components/ui.tsx` chips, fields, `PlainChecks`, `TrafficDot`, `Disclosure`, chip input, status badges; `src/components/Info.tsx` the tooltip.
