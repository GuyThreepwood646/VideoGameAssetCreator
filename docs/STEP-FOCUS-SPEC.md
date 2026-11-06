# Step pages after the asset is finished: keep the step in focus

UX-STEP-FOCUS. Status: built 2026-11-06. Traces to UX-REVIEW.md finding 18 (the finishing moment) and principle 7 (progress you can feel), without letting that moment take over every step.

## Problem (measured on TheHeir, 1024 x 768 window, page 1280 px wide)

Once the asset is finished by Autopilot, every step page (1 to 6) opened with the full finishing card: what you get, a 360 px turntable, the outdoor picture, every download, the Unity hand-off and "Using it in Unity". The card was 842 px tall, so the step's own heading, versions and model started at 981 px, entirely below the first screen. Someone going back to step 3 to look at or change the 3D model saw the finished product instead of step 3.

## Inherited decisions

- Tokens: CSS variables in frontend/src/styles.css (--ok, --warn, --accent, --text, --muted, --s1 to --s5, --radius-card, --control-h 32 px). No literals added.
- The guide card at the top of a step holds the page's one primary button (GuidePanel header note). The finished bar keeps that rule.
- The asset header's status line already says "Autopilot finished. Your asset is ready to hand to the game." on every step; the bar does not repeat it.
- Step 6 ("Hand off to the game") is where the finished product lives; its full finishing card stays as it is.
- Desktop app (Electron, mouse first) with an app-wide floor `body { min-width: 1280px }` (docs/PROJECTS-PAGE-SPEC.md); narrower windows scroll sideways by design. Touch sizes apply under `@media (hover: none)`.
- No analytics port exists in the frontend; none is added (open question below).

## Design

Steps 1 to 5, when the asset is finished and the version kept at this step is done (guide state "selected"): a one-row finished bar replaces the finishing card.

| Part | Content |
|---|---|
| Kicker | "Step 3 of 6: Build the 3D model" with the step's Info button |
| Line 1 (what you are looking at) | "You kept version 1, and it passed every check." (or "..., though a check needs attention.") |
| Line 2 (what it means here) | "The whole asset is finished. A change here makes a new version; the finished files stay until you rebuild the steps after it." |
| Warn line (only when the finished asset has a check marked "needs a look") | warn dot + "1 check on the finished asset needs a look." |
| Primary action | "See the finished asset" link to step 6 |
| Secondary action | "Download FBX" (or GLB), the export's file |

Any other state on a finished asset (a new result to judge, running, stopped, failed) shows the normal guide card for that state, because then the step needs a decision. "Make it" stays hidden on a finished asset, as before.

Step 6 keeps the full finishing card when Autopilot finished or the person finished by hand, as before.

## State matrix

| State | Behaviour |
|---|---|
| First visit to a step after Autopilot finished | finished bar |
| Returning (navigating back and forth) | finished bar; same height every time, step content starts right under it |
| Default (finished, version kept) | finished bar |
| Loading or waiting (a job running at this step) | normal running guide (progress, Stop) |
| Empty | N/A: a finished asset has a kept version at every step |
| Error (the newest version here failed) | normal error guide (Try again) |
| Offline (backend down) | unchanged: the top bar shows "backend down"; the bar's links still work, downloads fail as before |
| Interrupted (window hidden, reopened) | no timed content; nothing to resume |
| Disabled or locked | N/A: no locked steps on a finished asset |
| Success (new result made here, waiting to be judged) | normal review guide ("Happy with this?") |
| Stopped | normal stopped guide (Resume) |
| Smallest supported window (1280 x 720) | bar stays one row; text column wraps first |
| Narrower than the 1280 px floor | page scrolls sideways (app-wide rule, unchanged); the bar wraps its actions under the text if its own column gets narrow |
| Largest window (1920 x 1080) | bar stays one row; the second line is capped at 72 characters so it stays readable |
| Other orientation | N/A (desktop app) |
| Maximum text scale (browser zoom 200%) | bar wraps as at the smallest width; no fixed heights |
| Reduce Motion on | no motion in the bar |
| Screen reader on | section labelled "Step 3 of 6, finished asset"; buttons and links have text labels |
| Step 6 | full finishing card (unchanged) |

## Controls

| Control | Hit area | Tokens | States | Label |
|---|---|---|---|---|
| See the finished asset (primary link) | --control-h (32 px), 44 px under hover: none | .btn.primary: --accent, --accent-ink | default, hover, focus ring | "See the finished asset" |
| Download FBX (secondary link) | --control-h, 44 px under hover: none | .btn | default, hover, focus | "Download FBX" |
| Info (step) | existing Info button | existing | existing | existing |

## Accessibility (desk check)

| Check | Threshold | Result |
|---|---|---|
| Text contrast, --text on the green-tinted panel | 4.5:1 | --text #e4e6ea on about #232a27: about 12:1 |
| Muted text, --muted on the tinted panel | 4.5:1 | #9aa0a8 on about #232a27: about 6:1 |
| Colour not the only signal | text with every dot | warn line says "needs a look" in words |
| Target size | 24 px minimum, 44 px touch | 32 px mouse, 44 px under hover: none |
| Zoom to 200% | no loss of content | wraps, no fixed height |
| Reduce Motion | no movement | none used |

## Desk evaluation

Cognitive walkthrough, "I go back to step 3 to look at the model": the step heading, versions and model are now on the first screen (bar about 90 px instead of 842 px); "See the finished asset" is the one primary action, so getting back to the product is one click. "I want to change the model": the bar's second line says a change makes a new version and the finished files stay, which answers "will I break my finished asset?" before it is asked.

## Acceptance criteria

1. On steps 1 to 5 of a finished asset with a kept, done version, the finishing card is not rendered; the finished bar is. Check: page at /assets/<id>/mesh shows no .finish-card and one .finish-bar.
2. On step 6 of a finished asset the finishing card is unchanged.
3. At 1280 x 720 the step's own section starts within the first screen on step 3 of TheHeir. Check: measured top under 720 px.
4. A new result waiting to be judged, a running job, a stopped or a failed version on a finished asset show their own guide card, not the bar.
5. The bar has no content wider than itself at 1280, 1440 and 1920 px wide.

## Open questions

- The app-wide 1280 px floor: the in-app browser pane and smaller laptop windows (1024 to 1280 px) scroll sideways and hide the right edge of every page. Lifting it is a whole-app layout change. Owner: the owner.

- Analytics: no event port in the frontend. If one is added, fire `finished_bar_open_finished` on "See the finished asset" to learn how often people jump back to the product. Owner: the owner.

## Verification (2026-11-06, TheHeir 818aadfa, running app)

| Check | Result |
|---|---|
| Steps 1 to 5 at 1280 x 720 | finished bar 113 px on every step, no finishing card; the step's own section starts at 236 px on every step (was 981 px on step 3) |
| Step 6 at 1280 x 720 | full finishing card, unchanged (842 px) |
| 1440 x 900 and 1920 x 1080 | bar one row, 113 px, nothing wider than the bar |
| 200% zoom at 1440 px | nothing wider than the bar |
| Links | "See the finished asset" opens step 6; "Download FBX" points at the export's FBX |
| Primary button height | 36 px (mouse); the 44 px touch rule under hover: none was not checked on a touch device |
| Not checked live | the review, running, stopped and failed states on a finished asset (no such version on TheHeir; the code path is the unchanged guide card) and a screen reader pass |

Gate: `npm run build` (tsc and vite) clean; frontend only, so no backend restart.
