# UX review: making VGAC approachable, clear and fun

Date: 2026-11-04. Written from the perspective of a UX analyst and interaction designer, after using the app against the real backend with the first three assets (knight, wolf, chest). Recommendations are prioritised P0 (do before anyone else uses it), P1 (next), P2 (later).

## 1. Who the user is, and what the app currently assumes

The owner's goal: a layperson (a game maker who is not a 3D artist) describes a thing and gets a usable game asset, with the chance to steer it along the way. The current UI was built from the pipeline outward: six stages named after the engine steps (Concept, Turnaround, Mesh, Prep, Rig, Export), each with Review, Refine and Adjust sections, metric chips (silhouette IoU, non-manifold edges, weight coverage) and a raw job log. It is complete and honest, and it reads like a factory control panel. Someone who knows the words will be productive in minutes. Someone who does not will stall at the first screen, because the app never tells them what to do next or why.

The core problem is not missing features. It is that the app exposes its own structure instead of the user's intent. The user's intent is a sentence: "I want a wolf for my game that walks." The app should carry that sentence through to the end and only ask questions it cannot answer by itself.

## 2. The mental model, explained (answer to "what is the intent of the workflow")

Three nouns:

- **Project** is one game, or one visually coherent set. It owns the style lock (the look every asset shares: painting style, palette, model choice) and the default platform target (mobile, PC). Everything made inside a project matches. You would make a second project for a different game or a different art style, not for a second character.
- **Asset** is one distinct thing: a character, a creature, a prop, a tileable material. The knight and the wolf are two assets in the same project. An asset carries its prompt, its category (which decides the pose, the rig type and the budgets) and its platform target.
- **Version** is one attempt at one stage of one asset. When you regenerate a concept you get more versions in the concept stage; when you refine one, the result is a new version whose parent is the one you refined. Every later stage builds on the version you selected in the stage before. So the history of an asset is a tree, and the app keeps every branch. Nothing is lost by trying something.

What a version is not: a different subject. "The same knight in winter armour" is today either a refine of the concept (if you want to keep the identity and change clothes) or a second asset. That is the one place the model is thin. The honest fix is a fourth noun, **Variant**: an asset derived from another asset's selected versions with a change in the prompt (outfit, colour, damage state), sharing the parent's identity. It is a P1 addition (section 6).

The stages, in the user's words, are: describe it, check it from all sides, build it, clean it up, make it move, hand it to the game. That is the vocabulary the UI should use. The technical names stay available for people who want them.

## 3. Findings, screen by screen

### Projects screen
1. No empty state. A first-time user sees "New project" and a text field. There is nothing that says what a project is or that they need one.
2. "Style lock" is the single most powerful idea in the app and it is presented as a form of fields (prompt block, LoRA list, palette, negative). A layperson cannot fill it. It needs presets ("Painted fantasy", "Clean cartoon", "Pixel retro", "Realistic") that fill the fields, with the raw fields behind "Advanced".
3. Platform targets are named mobile_low, pc_mid, high. Say "Phone (small and fast)", "PC and console", "Showcase (large)" with a one-line consequence each.

### Asset creation form
4. Category, style preset, platform, prompt, parts: good set, no guidance. Each needs a one-line plain explanation and an example. The prompt needs a template and two examples per category ("A sturdy young knight in worn plate armour, short dark hair, no helmet").
5. No model suggestion. The app knows the best engine for each style and platform; it should say so ("We will use Z-Image Turbo for this: fastest painted look at 1024 px") and let the user change it only in Advanced.
6. The user has to make five decisions before seeing anything. Make the prompt the only required field; everything else defaults from the project and can be changed later.

### Pipeline page
7. Six stages in the rail, all visible, all equal weight. The next action is not highlighted. There should be exactly one primary button on screen at any time: "Make concept art", then "Check all sides", then "Build the 3D model", and so on. The rail shows progress; it does not have to be the navigation.
8. No sense of time. Stages take 20 seconds to 10 minutes. Show an estimate ("about 2 minutes") and a progress bar with the step name in plain words ("painting the front view, 3 of 5").
9. Review, Refine and Adjust are three different verbs for "change it" and the user cannot tell which to use. Rename and reframe as a single question: "Happy with this?" with three answers: "Yes, keep it" (select), "Try again" (regenerate), "Change something" (opens a panel where the first option is a text box "Describe the change" and, below it, "Fine-tune" with sliders). Reject can be a small secondary action.
10. Metrics chips dominate the candidate cards (silhouette IoU, elapsed seconds, seed, model). For the layperson these are noise. Show one thing per card: a thumbnail and a traffic-light "looks good / needs attention" with the reason in a tooltip. Raw metrics go into a collapsible "Details" area.
11. Quality gate failures read like a compiler ("FAIL manifold: 8 (limit 0)"). Each needs a plain sentence and, where the app can fix it, a button: "The model has 210 more triangles than a phone can handle. Fix automatically." Most gate items are fixable by a re-run with a parameter, so the fix button can be a prefilled Adjust.
12. Adjust forms show engine parameters (seed, steps, cfg, LoRA strength). Layperson-facing controls should be few and named by effect: "More detail / faster", "Smaller file / more detail", "Smoother skin bending". Keep the raw form under "Advanced".
13. The job log is useful for the builder and alarming for everyone else (kinds like turnaround.run on b, ComfyError text). Replace it with a single status line in the header ("Building the 3D model, about 2 minutes left") and keep the log behind a "Details" toggle.
14. Errors are raw exceptions. Each failure needs a human sentence and a next step ("The image generator stopped. This usually fixes itself; try again." with a Retry button).
15. The turnaround stage shows five views but never explains why they exist. One line: "We look at your character from every side so the 3D model gets the back right. If a view looks wrong, regenerate just that view."
16. The rig stage shows a bone list and check names. The user wants to see it move. Lead with the animation strip or the viewer playing the walk cycle, with "Does the movement look natural?" and two answers.
17. The export stage lists files. It should lead with "Your asset is ready" and one big action: "Save to game folder" (or "Open in Unity"), with the file list below.
18. Nothing celebrates completion. The first finished asset deserves a turntable with the walk cycle playing and a clear "Done" moment.
19. Tooltips do not exist. Every label that is a term of art needs an info icon with: the plain meaning, why it matters, and the technical name for people who want to search for it.

### Cross-cutting
20. There is no first-run guide. A three-step overlay the first time ("1. Describe your character. 2. We build it step by step and show you each result. 3. Say yes or ask for changes.") plus a persistent "Next step" panel would carry a layperson through.
21. There is no autopilot. A user who trusts the defaults should be able to press "Make it" once and come back in ten minutes to a finished asset, with the app pausing only where it is unsure (a failed check it cannot fix, a view that failed consistency). Builders keep the stage-by-stage mode.
22. Words: Mesh, Prep, Rig, Turnaround, IoU, manifold, LoRA, seed, CFG, cutout. Each is fine with a tooltip; none should be the only label on a button.
23. Feedback loop: the app never asks the user whether a result was good after they move on, so it cannot learn what "good" means for this project. A single thumbs up or down on select would seed the judge later.

## 4. Principles for the redesign

1. **Intent first.** The prompt is the centre. Everything else is a default the app chose and explains.
2. **One next step.** Exactly one primary action at a time, named in plain words, with a time estimate.
3. **Two layers, always.** Plain layer by default; technical layer one click away (Advanced, Details, the technical name inside every tooltip). Never remove the technical layer; the owner uses it.
4. **Questions, not sections.** Review, Refine, Adjust become "Happy with this?" with three answers.
5. **Explain checks in sentences with a fix button.** A check the app can fix is a button, not a warning.
6. **Show, do not list.** Lead every stage with the picture or the moving model; put numbers behind Details.
7. **Progress you can feel.** Stage rail as a progress strip, estimates, a finishing moment.
8. **Autopilot with brakes.** Run everything by default; stop only when a result needs a human.

## 5. Recommendations, prioritised

| # | Change | Effort | Priority |
|---|---|---|---|
| A | Help registry: plain name, one-line meaning, why it matters, technical name, for every stage, parameter, check and metric; rendered as info icons and tooltips everywhere | small | P0 |
| B | Guide panel "Your next step": contextual instruction per stage state, with the one primary action and a time estimate; first-run three-step overlay | small | P0 |
| C | Model and budget suggestion on the asset form and in Adjust, with the reason ("fast painted look for phones") | small | P0 |
| D | Plain-language quality checks with fix buttons (prefilled Adjust runs) | small | P0 |
| E | Rename stages and verbs in the plain layer (Describe, Check all sides, Build, Clean up, Make it move, Hand off; Happy with this? Keep / Try again / Change something) while keeping technical names in tooltips and the URL | small | P0 |
| F | Status line and progress with estimates in the header; job log behind Details; human error sentences with Retry | medium | P0 |
| G | Autopilot: "Make it" runs all stages, auto-selects, pauses on failed checks it cannot fix | medium | P1 |
| H | Style presets that fill the style lock; raw fields under Advanced | small | P1 |
| I | Simple Adjust controls named by effect, raw parameters under Advanced | medium | P1 |
| J | Variants: derive an asset from another with a prompt change, keeping identity | medium | P1 |
| K | Finishing moment: turntable with walk cycle, "Save to game folder" | small | P1 |
| L | Thumbs up or down on select, stored per version, used by the judge later | small | P2 |
| M | Glossary page and example gallery (one finished asset per category) | small | P2 |

Items A to F are being implemented now. G and H follow.

## 6. Variants (design note for P1)

A Variant is an asset whose `base_asset_id` points at another asset and whose prompt is the base prompt plus a change. Creating one copies the base's selected concept as the starting image and runs a Refine with the change ("same knight, winter cloak and fur boots"), so the identity holds. Downstream stages run as usual. In the grid a variant shows under its base, and the manifest records the lineage. Cost: one column, one endpoint, one grid grouping.

## 7. How to know it worked

- Time from opening the app to a finished asset with no help: target under 15 minutes of user attention for a character on autopilot.
- Number of decisions a first-time user must make before seeing art: 1 (the prompt).
- Share of stages reached without opening Details or Advanced: target above 80 percent.
- Every failed check has a plain sentence; every fixable one has a button.
