# Great assets: implementation plan

Date: 2026-11-04. Status: ready to implement. Trace: gap IDs from `docs/GREAT-ASSETS.md` Part 2 (the ten-item shortlist, plus G51, G10, G11, G06 and G18 to G20 from "next in line"); UX principles from `docs/UX-REVIEW.md` section 4.

This plan turns each gap into decisions an engineering agent can build without asking anyone. For every gap it says what the app does by itself, where the user sees it, the exact words, what the user may change, and the engine facts the implementer needs. Two new interactions are designed in full in section 4: "Pieces that move" (G21, G23) and hit boxes (G34).

How to read it:

1. Section 1 lists what is already decided in the app and three conflicts found while reading the code.
2. Section 2 is shared plumbing (Phase 0). Several gaps depend on it, so it comes first.
3. Section 3 has one entry per gap, always in the same five parts: A Default, B Where it surfaces (with copy), C What the user can change, D Engine notes, E Done when.
4. Section 4 has the two designed flows with states, controls and fallbacks.
5. Section 5 is the build order, effort and who touches which files.
6. Section 6 is the copy to change today (G51). Section 7 is the do-not-build list. Section 8 is hand-offs and open questions.

Words used here: "check" is an entry in a version's `metrics.checks` (rendered by `PlainChecks`); "knob" is an Adjust parameter in `reference.STAGE_SCHEMAS` (rendered by `FineTune`); "simple layer" and "Advanced" are the two layers of Fine-tune; "finish card" is `FinishCard` in `frontend/src/pipeline/AutopilotPanel.tsx`. Effort: S is up to one agent-day, M is two to four days, L is one to two weeks.

Copy in this plan is final enough to ship. Every user-facing string is written out; a later narrative pass may polish wording but must keep the lengths and the meaning.

---

## 1. Inherited decisions

These are in force and the plan builds on them, not around them.

| Decision | Where it lives | What this plan does with it |
|---|---|---|
| Three answers to every result: "Yes, keep it", "Try again", "Change something" | `StagePanel.tsx` `HappyBlock`, `ChangePanel` | New tools (the parts editor) open inside "Change something", above Fine-tune |
| One primary action at a time, owned by the guide panel or the Keep answer | `GuidePanel.tsx` | New stage modes reuse the guide states (empty, running, review, selected); no extra primary buttons |
| Plain names with an Info tooltip: plain, meaning, why, technical | `help.ts` `TERM_HELP` via `H(plain, meaning, why, technical)`; backend `guidance.PARAM_HELP[name].help` | Every new term gets one entry in each, same wording |
| Checks are plain sentences with severity `block` or `warn` and an optional Fix that re-runs a stage with params | `guidance.CHECK_GUIDE`, `describe_check`; frontend `checkSentence`, `checkSeverity`, `checkFix` | Contract extended in Phase 0 (section 2.1) |
| Simple versus Advanced knobs: the backend `simple` flag wins; `help.ts` `SIMPLE_PARAMS` is the mock fallback | `guidance.PARAM_HELP`, `help.ts` | Every new knob is added to both |
| Autopilot ("Make it") pauses only on failed `block` checks it cannot fix; default `stop_on: block` | `autopilot.py`, `AutopilotPanel.tsx` | Every new check is `warn` unless the asset would be broken in a game and a Fix exists |
| Finish card: turntable, downloads, Make another, Make a variant | `AutopilotPanel.tsx` `FinishCard` | Gains a "What you get" list and a "Using it in Unity" disclosure (section 2.4) |
| Export folder per asset, Unity import into a sandbox or the project's Unity path, never CardGames | STATUS.md D12 | Unchanged; new files are added beside the existing ones |
| Colour tokens: `--ok`, `--warn`, `--bad`, `--accent`, `--muted`, `--border` | `frontend/src/styles.css` | New overlays use these; no new colours |
| Gates: `pytest -q tests` in `backend/`, `npm run build` and `npm run lint` in `frontend/`, Unity batch import report `unity_report.json` | README, STATUS.md | Every "Done when" names one of these, or a manual look, and says which |

There is no playtest profile (`.claude/playtest-profile.md`) for this repo; this is a desktop web tool, not a game, so the phone-size and store rules of the UX skill do not apply. The app has no analytics port; the record of what users change is the version history itself (`params` per version, feedback thumbs).

### Conflicts and defects found while reading

1. **Passing checks show the failure sentence.** `guidance.describe_check` sets `help` from `CHECK_GUIDE` whether the check passed or not, and `help.ts` `checkSentence` returns `check.help` first. A passing `triangle_budget` therefore reads "The model has more triangles than your platform target allows". The backend sentence also hides the value-filled frontend sentence. Fixed by the check contract in 2.1.
2. **The Unity importer never updates in a user's project.** `stages/export.py` (around line 324) copies `VgacImport.cs` into the target Unity project only when no copy exists. Every Phase 1 importer fix would reach the sandbox and never reach a project that already has an old copy. Fixed in 2.6.
3. **Select knobs show raw values.** `FineTune` `AdjustField` renders options as `String(o)`, so users see `hull`, `none`, `mixamo`, `trellis`, `ual1`. The Fix button's tooltip shows raw JSON (`Re-runs mesh with {"remesh":true}`). Fixed in 2.1 and 2.2.

---

## 2. Shared plumbing (Phase 0)

### 2.1 Check contract: one sentence for pass, one for fail, a Fix label

`CHECK_GUIDE` entries take this shape (backend is the one source for check wording; `help.ts` `CHECK_TEXT` stays only as the mock fallback):

```python
"size_m": {
    "severity": "warn",
    "ok": "The model is {value} m {axis_word}, as intended.",
    "fail": "The model is {value} m {axis_word}; it should be {limit} m.",
    "fix": lambda c: {"stage": "prep", "params": {"size_m": c["limit"]}},
    "fix_label": "Set the size",
},
```

1. `describe_check` picks `ok` or `fail` by `passed`, fills `{value}`, `{limit}` and any keys in the check's optional `vars` dict, and writes the result to `help`. Missing keys must not raise (use a format map that leaves `{name}` as an empty string). Numbers are formatted like `help.ts`: integers with thousands separators, ratios as percentages, metres to two decimals.
2. When the check failed and has a fix, `fix` gains `label` from `fix_label`. Default label when none is given: "Fix automatically".
3. A fix may be `{"ui": "parts_editor"}` or `{"ui": "front_picker"}` instead of a stage re-run (see 2.5).
4. Port every existing `CHECK_GUIDE` entry to `ok` and `fail`, taking the wording from `help.ts` `CHECK_TEXT`. Fix labels for existing checks: `triangle_budget` "Shrink to the budget"; `height_m` (renamed `size_m`, see G12) "Set the size"; `lod_count` "Rebuild the detail levels"; `collider_present` "Add a collision shape"; `weight_coverage` "Attach the loose surface"; `weights_normalised` "Balance the weights"; the frontend fallback for `manifold` and `watertight` "Close the holes".
5. Frontend: `api.ts` `CheckFix` gains `label?: string` and `ui?: 'parts_editor' | 'front_picker'`. `PlainChecks` renders `fix.label ?? 'Fix automatically'` and the tooltip `Makes a new version of "{stage plain name}" with this change.` instead of the JSON.

Done when: a pytest asserts that every `CHECK_GUIDE` entry has `ok` and `fail`, that a passing check never carries the `fail` sentence, and that formatting with missing vars does not raise.

### 2.2 Plain labels for select options

1. `guidance.PARAM_HELP[name]` may carry `"options": {value: label}`. `reference.stages()` copies it into each schema entry as `option_labels`.
2. `FineTune` `AdjustField` shows `option_labels[String(o)] ?? String(o)` for `select` and `multiselect`. `help.ts` gets an `OPTION_LABELS` fallback map for the mock.
3. Labels for existing knobs, to add now:

| Knob | Value: label |
|---|---|
| `engine` | trellis: "TRELLIS (most detail from one picture)"; hy3d: "Hunyuan (uses all four sides; good for long animals)" |
| `input_view` | concept: "The first picture"; front: "Front view"; quarter: "Three-quarter view" |
| `texture_size` | 512: "512 px (smallest)"; 1024: "1024 px"; 2048: "2048 px"; 4096: "4096 px (largest)" |
| `upsample` | 1024: "Normal"; 1536: "Finer (about 2 to 3 times slower)" |
| `pivot` | base: "At the bottom (stands on the ground)"; center: "In the middle (floats or spins)" |
| `template` | auto: "Automatic (from the category)"; mixamo: "Standard human skeleton"; generic: "Fitted to the shape"; none: "No skeleton" |
| `formats` | fbx: "FBX (Unity, Unreal, Godot)"; glb: "GLB (web, Godot, Blender)" |
| `clips_library` | ual1: "Standard human moves (46 animations)"; ual2: "Extra human moves (second library)"; none: "Only its own walk and idle" |
| `views` | front: "Front"; left: "Left side"; back: "Back"; right: "Right side"; quarter: "Three-quarter"; top: "Top" |
| `collider` | see G16 (the option list changes) |

### 2.3 Knobs that apply to some categories only

A schema entry may carry `categories: [ids]`. `FineTune` hides an entry whose list does not include `asset.category`; `validate_params` accepts and ignores such a knob for other categories. Needed by G12, G14, G15, G21, G30, G34.

### 2.4 Finish card: "What you get" and "Using it in Unity"

1. The export stage writes `metrics.finish_lines`: a list of at most six `{id, text, state: "ok" | "warn", term}` built by a new `guidance.finish_lines(category, metrics)`. Order: size, surface, movement, collision, category special (moving parts, grid), engine files.
2. `FinishCard` shows them under the title as a short list headed "What you get", each with the existing traffic dot and an Info icon for `term`. A `warn` line with a matching failed check shows that check's Fix button.
3. `FinishCard` gets a closed disclosure "Using it in Unity" with three numbered lines written by the export stage into `metrics.unity_howto` (text per category; given in G33 and section 4.1).

### 2.5 A Fix that opens a tool

When a check's fix is `{"ui": ...}`, `StagePanel.fix` navigates to the check's stage, opens "Change something" and focuses the named editor (the parts editor, or the front picker in prep). Nothing runs until the user confirms there.

### 2.6 Keep the Unity importer current

Add `public const string Version = "N";` to `VgacImport.cs` (start at 2, raise it with every change). `export.py` reads the version from both copies and overwrites the project copy when it is older or has no version. The report gains `importer_version`.

### 2.7 Reduce Motion in the viewer

`Viewer.tsx` reads `matchMedia('(prefers-reduced-motion: reduce)')`. When set: the turntable starts off (the finish card passes `turntableDefault`, which is then ignored), clips load paused on frame 0 with the Play button visible, and the parts and hit-box previews (section 4) show still poses with a "Show open / Show closed" toggle instead of looping.

---

## 3. Gap designs

### 3.1 G01 and G02: metal and shine reach the engine

**A. Default.** Always on, no control.

1. The export stage packs two extra maps from the kept prep version, same size as the base colour, linear colour space:
   - `T_<Name>_MetallicSmoothness.png`: metallic in RGB, smoothness (1 minus roughness) in alpha.
   - `T_<Name>_ORM.png`: R occlusion, G roughness, B metallic.
2. The GLB material uses the ORM image as both `metallicRoughnessTexture` and `occlusionTexture`.
3. `VgacImport.cs` assigns the MetallicSmoothness map to the URP Lit material, so metal reads as metal.
4. The separate `metallic.png` and `roughness.png` are still written but leave the main download row (they stay in Details, Files).
5. The tileable material category writes both packs too.

**B. Where it surfaces.**

Download labels (`ExportView.tsx` `FILE_NAMES`, new keys `metallic_smoothness`, `orm`):

| Key | Label |
|---|---|
| `metallic_smoothness` | metal and shine texture (Unity) |
| `orm` | surface texture (Unreal, Godot, web) |

Check (export stage, only when the Unity import ran):

| Id | Severity | ok | fail | Fix label | Fix |
|---|---|---|---|---|---|
| `materials_connected` | warn | Unity uses all the surface textures: colour, bumps, shadows, metal and shine. | Unity is missing the {missing} texture, so the surface will look {effect}. | Rebuild for Unity | export, `{}` |

`vars`: `missing` is the plain name of the first missing map (colour, bump, shadow, metal and shine); `effect` is "like plastic" for metal and shine, "flat" for bump, "a little flat" for shadow, "grey" for colour.

Finish line: "Looks right in Unity: colour, bumps, shadows, metal and shine are connected."

Help entries (`help.ts` `TERM_HELP` and `guidance.PARAM_HELP` style text):

| Key | Plain | Meaning | Why | Technical |
|---|---|---|---|---|
| `metal_shine` | Metal and shine | A texture that tells the engine which parts are metal and how glossy each part is. | Without it armour looks like plastic and wet stone looks dry. | MetallicSmoothness map (URP Lit: metallic in RGB, smoothness in alpha) |
| `orm` | Surface texture | One texture that holds shadowing, roughness and metal in its three colour channels. | Unreal, Godot and web viewers read all three from one file, which saves memory. | ORM pack (R occlusion, G roughness, B metallic); glTF metallicRoughness |

**C. What the user can change.** Nothing. There is no reason a layperson would want a different layout.

**D. Engine notes.** URP Lit: set `_MetallicGlossMap`, enable keyword `_METALLICSPECGLOSSMAP`, `_SmoothnessTextureChannel = 0` (metallic alpha), `_Smoothness = 1`; import the pack with sRGB off and alpha from input [S11]. glTF: roughness in G, metal in B [S26]. Unreal ORM is common practice, not a spec. HDRP mask map is not built (section 7).

**E. Done when.** A pytest round-trips the packer (roughness 0.3 becomes alpha 178 or 179); `unity_report.texturesAssigned` lists `metallic_smoothness` for the knight, wolf and chest; a Unity snapshot of the knight shows a visible highlight on the armour (manual look).

### 3.2 G12, with G14 and a light G13: the right size, facing the right way

**A. Default.**

1. Each category yaml gains `size_axis`, `size_label`, `size_hint`, and a corrected `size_m`:

| Category | `size_axis` | `size_m` | `size_label` | `size_hint` |
|---|---|---|---|---|
| humanoid | height | 1.8 | How tall is it? | In metres, from the ground to the top of the head. A person is about 1.8 m. |
| quadruped | horizontal | 1.5 | How long is it? | In metres, nose to rump, not counting the tail. A wolf is about 1.4 m; a horse about 2.4 m. |
| winged | horizontal | 1.5 | How wide are the wings? | In metres, wing tip to wing tip. A hawk is about 1.2 m; a horse-sized dragon about 8 m. |
| multileg | horizontal | 1.0 | How wide is it? | In metres, across the legs. A large spider is about 0.2 m; a giant cave spider about 2 m. |
| serpent | longest | 2.0 | How long is it? | In metres, head to tail as it lies. A python is about 4 m. |
| vehicle | horizontal | 4.5 | How long is it? | In metres, front to back. A car is about 4.5 m; a tank about 7 m. |
| prop_rigid | longest | 0.6 | How big is it? | In metres, along its longest side. A barrel is about 0.9 m tall. |
| prop_articulated | longest | 0.8 | How big is it? | In metres, along its longest side. A chest is about 0.9 m wide; a door about 2.1 m tall. |
| environment | grid (see G15) | 3.0 | Size on the grid | Set by the grid; see G15. |
| foliage | height | 2.5 | How tall is it? | In metres, from the ground to the top. A bush is about 1 m; an oak about 15 m. |
| material | (not used) | 1.0 | (not used) | (not used) |

   `height` is the Y extent; `horizontal` the larger of the X and Z extents; `longest` the largest of all three. A serpent in an S curve is measured on its longest box side; the true arc length is not attempted.

2. Prep turns the model so the concept's front faces +Z before it scales. It uses the mesh stage's `silhouette_offset` and snaps to the nearest quarter turn when the offset is within 20 degrees of one; otherwise it leaves the model as built.
3. Prep scales uniformly so the chosen axis equals `size_m`. The gate measures the same axis with a 3 percent tolerance.
4. The prep knob `height_m` is renamed `size_m`. `height_m` is still read from older versions as an alias.
5. Light G13: when the description contains a size ("two metres wide", "6 ft tall", "4.5 m long"), the asset form shows it and uses it as the prep default. The parser accepts digits and the words one to twenty, units m, metre(s), meter(s), cm, ft, foot, feet, and axis words tall or high (height), long (horizontal), wide or across (horizontal), and nothing else. An explicit axis word overrides the category axis for that asset.

**B. Where it surfaces.**

Asset form (`ProjectPage.tsx`, under the description, only when a size was found): "Size: {value} m {axis_word}, from your description. You can change it later in Clean up for the game."

Prep Fine-tune, simple layer:

| Knob | Label | Control | Options or range | Default | Categories |
|---|---|---|---|---|---|
| `size_m` | `size_label` from the category yaml | number in metres, hint `size_hint` | 0.01 to 100 | category `size_m`, or the size from the description | all except environment and material |
| `face_yaw` | Which way is the front? | four picture buttons plus Automatic | auto: "Automatic"; 0: "As built"; 90: "Turned a quarter to the left"; 180: "Turned around"; 270: "Turned a quarter to the right" | auto | all except material |

The `face_yaw` hint: "Pick the picture where the front faces you. The game treats that side as forward." Each picture is a `glbSnapshot` still of the mesh version's GLB from the front camera with the model turned by that angle (extend `useGlbSnapshot` with a `yawDeg` option). When auto is chosen, the button for the angle prep actually used shows a small "chosen" mark.

Checks (prep stage):

| Id | Severity | ok | fail | Fix label | Fix |
|---|---|---|---|---|---|
| `size_m` (replaces `height_m`) | warn | The model is {value} m {axis_word}, as intended. | The model is {value} m {axis_word}; it should be {limit} m. | Set the size | prep, `{size_m: limit}` |
| `facing` | warn | The front of the model faces forward in the game. | The model may face the wrong way: it matches the front picture best when turned {value} degrees. | Turn it to face forward | prep, `{face_yaw: value}` |

`axis_word` is "tall", "long" or "across". `facing` fails when the best offset is more than 20 degrees from the applied turn.

Finish line: "Real size: {value} m {axis_word}, front facing forward."

Help entries:

| Key | Plain | Meaning | Why | Technical |
|---|---|---|---|---|
| `size_m` | Size | How big the asset is in the game, in metres, measured the way that makes sense for it: height for people, length for cars and snakes. | Keeps everything in proportion, so a car is car-sized next to your character. | Uniform scale so the extent on `size_axis` equals `size_m` |
| `face_yaw` | Front | Which side of the model the game treats as its front. | Characters walk and cars drive toward their front; if it is wrong, they move sideways. | Yaw about Y so the concept front faces +Z |

**C. What the user can change.** The size number and the front picture. Advanced: nothing new; `pivot` and `smooth_angle` stay where they are.

**D. Engine notes.** Unity and glTF: Y up, front faces +Z [S1 2.4.d][S25]; Unreal converts on FBX import [S17]. Confirm the sign of `silhouette_offset` on the wolf before trusting it (one test asset turned 90 degrees by hand).

**E. Done when.** A pytest checks every category yaml has `size_axis`; prep on a synthetic 1 x 1 x 4 box with category vehicle returns a 4.5 m horizontal extent within 3 percent; the wolf e2e reports length, not height; the size parser has unit tests for the examples above.

### 3.3 G32 and G33: animations loop, and a controller that works on day one

**A. Default.** Always on.

1. `VgacImport.cs` sets clip import settings through `ModelImporter.clipAnimations` (start from `defaultClipAnimations`, then `SaveAndReimport`): `loopTime` and `loopPose` on for cyclic clips, and Bake Into Pose for root rotation, height and XZ position on cyclic in-place clips. A clip is cyclic when its name matches, case-insensitive, `idle`, `walk`, `run`, `trot`, `jog`, `sprint`, `swim`, `slither`, `crawl`, `fly`, `flap`, `glide`, `hover`, or contains `loop`. The implementer reads the real Quaternius library names and extends the list.
2. The prefab's Animator has `applyRootMotion = false`.
3. The controller is rebuilt with a fixed shape instead of one state per clip:
   - Float `Speed`. State "Locomotion", a 1D blend tree: idle at 0, walk at 1.4, run at 3.5 (metres per second, scaled by `size_m / 1.8` for creatures).
   - Triggers `Attack`, `Hit`, `Die`, and `Jump` for humanoids. Attack, Hit and Jump return to Locomotion at exit time; Die goes to a "Dead" state with no exit.
   - Every other library clip sits in a sub-state machine "More moves", not connected, so it is available but out of the way.
   - A state is omitted when its clip does not exist, and so is its parameter. The manifest lists what was built.
   - Generic rigs get the same shape from their own clips (G30 supplies run, attack, hit and death).

**B. Where it surfaces.**

Checks (export stage, when Unity ran):

| Id | Severity | ok | fail | Fix label | Fix |
|---|---|---|---|---|---|
| `clips_loop` | block | Idle and walk loop smoothly. | The {value} animation does not loop, so it would stop after one cycle. | Rebuild for Unity | export, `{}` |
| `controller_ready` | warn | An animation controller switches between idle, walk and run by speed. | No animation controller was built, so you would have to set one up in Unity. | Rebuild for Unity | export, `{}` |

`clips_loop` is block because a frozen walk is broken in a game, and the Fix is automatic, so the autopilot's "Fix and continue" handles it.

Finish line: "Ready to move: set Speed and it idles, walks and runs; Attack, Hit and Die are one line each." (Only the moves that exist are named.)

"Using it in Unity" (humanoids and creatures):

1. Drag the prefab from `{prefab path}` into your scene.
2. In your movement script, set the speed every frame: `animator.SetFloat("Speed", speed);`
3. For one-off moves, call `animator.SetTrigger("Attack");` (or `"Hit"`, `"Die"`).

Help entries:

| Key | Plain | Meaning | Why | Technical |
|---|---|---|---|---|
| `animator_controller` | Animation controller | The part of the game object that picks which animation plays: standing, walking or running, depending on speed. | Your game sets one number, Speed, and the character switches on its own. | AnimatorController: 1D blend tree on Speed; triggers Attack, Hit, Die, Jump; root motion off |
| `loop` | Loops | The animation runs again from the start as soon as it ends, with no jump. | Idle and walk must loop, or the character freezes after one step. | AnimationClip loopTime and loopPose |

**C. What the user can change.** Nothing in the app. Experts edit the controller in Unity.

**D. Engine notes.** GREAT-ASSETS marks Loop Time default off as "verify": confirm it in the sandbox before and after. Root motion policy: in-place cycles, baked into pose [S13]. Raise `VgacImport.Version` (2.6).

**E. Done when.** `unity_report.json` gains `clips: [{name, loop}]` and `controller: {states, parameters}`; the knight and wolf reports show `loop: true` for idle and walk; a snapshot at 1.5 times the walk length shows a different pose from frame 0 (the clip kept playing).

### 3.4 G34 and G16, with G35: collision that fits

Hit boxes for characters and creatures are the designed flow in section 4.2. This entry covers the body collider for every category and collision for props and scenery.

**A. Default.** Chosen by category, with `collider: auto` as the new export default.

| Category | Body collider (always) | Hit boxes (section 4.2) |
|---|---|---|
| humanoid | Standing capsule: height = model height, radius from the median torso width between 30 and 70 percent of height | One per body part |
| winged | Standing capsule around the body, wings left out | Body parts plus one box per wing |
| quadruped, multileg | Lying capsule along the body (direction Z), radius from the median cross-section of the middle 60 percent of the length | One per body part |
| serpent | Lying capsule along the body | A chain of capsules along the spine |
| prop_rigid | Best fit (below) | None; the collider is the hit shape |
| prop_articulated | Best fit per piece once pieces exist (G21) | None |
| vehicle | Chassis as several shapes (at most 6); wheels get no collider | None |
| environment | Exact shape (static mesh collider) | None |
| foliage | Trunk capsule only: bottom 40 percent of the height | None |
| material | None | None |

Best fit for props, in order:

1. Try a box, an upright cylinder, a capsule and a sphere. Accept the first whose volume overlap with the mesh's convex hull is at least 0.85 and whose own volume is at most 1.3 times the hull (starting values; implementer may tune on the test set).
2. Otherwise, when the mesh volume is under 0.7 of its convex hull's volume (the object is hollow or has openings), split into several convex shapes with CoACD: at most 8 for props, 16 for scenery, each at most 250 triangles.
3. Otherwise one convex hull, decimated to at most 250 triangles (G35).

Environment pieces also stop filling their openings at the mesh stage: the environment yaml gets `remesh_default: false`. Verify this on three test pieces (a wall with a door, an arch, a catwalk). If more than 1 percent of edges come out open, keep remesh on for that piece and rely on the collider; record the result in STATUS.md.

**B. Where it surfaces.**

Export Fine-tune, simple layer, `collider` with new options:

| Value | Label |
|---|---|
| auto | Best fit (recommended) |
| hull | One wrapped shape |
| decompose | Several shapes, for hollow things |
| mesh | Exact shape (only for scenery that never moves) |
| none | None |

Hint: "The invisible shapes the game uses to know where this is solid." Advanced: `collider_max_parts`, label "Most shapes", integer 1 to 32, default automatic. For rigged categories the knob is replaced by `movement_shape` (section 4.2).

Checks (export stage):

| Id | Severity | ok | fail | Fix label | Fix |
|---|---|---|---|---|---|
| `collider_triangles` (replaces `collider_vertices`) | warn | The collision shape has {value} triangles, inside Unity's limit of 255. | The collision shape has {value} triangles; Unity would simplify it on its own and it may stop fitting. | Simplify the collision shape | export, `{}` |
| `openings_clear` | warn | Openings stay open: things can pass through doorways and gaps. | The collision shape fills {value} opening(s), so players cannot pass through. | Use several shapes | export, `{collider: "decompose"}` (environment: `"mesh"`) |

`openings_clear` applies to prop_rigid, environment and vehicle. Method: cast a grid of horizontal rays front to back and side to side through the bounding box; a ray that misses the visible mesh marks an opening; the check fails when more than 10 percent of those rays hit the collider. Implementer may refine the sampling.

Finish lines:
- Props and scenery: "Bumps into things: {count} collision shape(s){, and openings stay open}."
- Rigged: see section 4.2.

Help entry (replaces `collider`, `collider_vertices`, `collider_verts`):

| Key | Plain | Meaning | Why | Technical |
|---|---|---|---|---|
| `collider` | Collision shape | Invisible simple shapes the game uses to know where the asset is solid. | Without them things pass straight through it; the right shapes keep doorways open and seats usable. | Primitive, convex hull (255-triangle cap), CoACD convex decomposition, or mesh collider (static only) |
| `collider_triangles` | Collision shape size | How many triangles the collision shape has. | Unity allows at most 255 in a simple shape and reshapes anything bigger. | MeshCollider convex triangle limit [S2] |

**C. What the user can change.** The `collider` choice and, in Advanced, the most shapes. Editing shapes by hand is not offered (section 7).

**D. Engine notes.** CoACD is MIT and pip-installable [S38]; run it in the backend venv, not Blender's Python: `pack.py` writes the hull input, the backend runs CoACD and hands the parts back as `<name>_collider_00` to `_nn`. Unity: several shapes are convex `MeshCollider` children; scenery uses one non-convex `MeshCollider` [S3]. Unreal `UCX_` names are G11; Godot `-colonly` is G10.

**E. Done when.** The chest gets a box or a single hull; a test cart or chair gets at least two shapes and a ray down through the seat passes; the arch test piece passes `openings_clear`; every convex part has at most 255 triangles (pytest on the export metrics plus the Unity report).

### 3.5 G05 and G03: textures painted on the final model, with room at the seams

**A. Default.** Always on, no control in the simple layer.

1. G03: `UnwrapMesh` padding becomes `max(4, texture_size / 256)` pixels. After every bake, each map is dilated into empty texture space by `max(8, texture_size / 128)` pixels (numpy nearest-colour fill).
2. G05: the maps are baked on the exact triangles that ship. Two ways are acceptable; implementer decides, preferring whichever changes the proven ComfyUI chain least:
   - (a) move symmetrize and the final budget decimation into the mesh stage, before `UnwrapMesh` and the bakes, so prep only scales, turns and sets the pivot; or
   - (b) prep re-bakes in Blender Cycles from the dense mesh onto the final mesh (selected to active).
   Either way, record `bake_mesh_hash` and `export_mesh_hash` in the metrics.

**B. Where it surfaces.**

Checks:

| Id | Stage | Severity | ok | fail | Fix label | Fix |
|---|---|---|---|---|---|---|
| `bake_matches` | export | warn | The surface detail was painted onto the final model. | The surface detail was painted for an earlier copy of the model, so shading may look wavy. | Repaint the surface | prep, `{rebake: true}` (option b) or mesh, `{}` (option a) |
| `texture_padding` | prep | warn | Texture pieces have {value} px of space around them, enough for distant views. | Texture pieces have only {value} px of space, so thin dark seams may show at a distance. | Add more space | mesh, `{padding: limit}` |
| `atlas_used` | prep | warn | The model uses {value} of its texture. | The model uses only {value} of its texture, so it looks less sharp than it could. | Repaint the surface | as `bake_matches` |

`atlas_used` fails under 50 percent; it catches the symmetrize problem (half the atlas never sampled).

Mesh Fine-tune, Advanced: `padding`, label "Space around texture pieces (pixels)", integer 1 to 64, default automatic.

Help entries:

| Key | Plain | Meaning | Why | Technical |
|---|---|---|---|---|
| `padding` | Seam space | Extra painted space around each piece of the texture. | Stops thin dark lines appearing at seams when the model is far away. | UV island padding and edge dilation, in texels |
| `bake` | Surface painting | Copying the fine detail and colour from the raw model onto the clean game model. | If it is painted onto a different shape, the shading looks wavy or faceted. | Texture bake (normal, AO, colour) from source to target mesh |

**C. What the user can change.** Only the Advanced padding number.

**D. Engine notes.** Triangulate before baking and use MikkTSpace in baker and engine [S30]; `VgacImport.cs` already imports tangents as `CalculateMikk`. Padding guidance is community practice [S29].

**E. Done when.** The knight, wolf and chest e2e runs show `bake_mesh_hash == export_mesh_hash`, silhouette IoU no worse than before, `atlas_used` above 50 percent on the symmetric knight, and a 1/8-size render with no visible seams (manual look).

### 3.6 G26: every humanoid gets the shared animations

**A. Default.** `VgacImport.cs` builds the `HumanDescription` explicitly instead of trusting Unity's auto-mapping. If that fails, it tries auto-mapping; if that fails, it falls back to Generic with the character's own clips, as today. The report records `avatar_mapping: explicit | auto | generic`.

Mapping (match the bone name after stripping a `mixamorig:`, `mixamorig_` or `mixamorig` prefix; skip names starting `Extra_`; use `HumanTrait.BoneName` for Unity's strings rather than typing them):

| Model bone | Unity human bone |
|---|---|
| Hips | Hips |
| Spine | Spine |
| Spine1 | Chest |
| Spine2 | UpperChest (optional; skip when absent) |
| Neck | Neck |
| Head | Head |
| LeftShoulder, RightShoulder | LeftShoulder, RightShoulder |
| LeftArm, RightArm | LeftUpperArm, RightUpperArm |
| LeftForeArm, RightForeArm | LeftLowerArm, RightLowerArm |
| LeftHand, RightHand | LeftHand, RightHand |
| LeftUpLeg, RightUpLeg | LeftUpperLeg, RightUpperLeg |
| LeftLeg, RightLeg | LeftLowerLeg, RightLowerLeg |
| LeftFoot, RightFoot | LeftFoot, RightFoot |
| LeftToeBase, RightToeBase | LeftToes, RightToes |
| LeftHandThumb1 to 3, Index, Middle, Ring, Pinky (when present) | Left Thumb Proximal, Intermediate, Distal, and so on |

**B. Where it surfaces.**

Check (existing `unity_humanoid_avatar`, ported to ok and fail, humanoid only):

| Severity | ok | fail | Fix label | Fix |
|---|---|---|---|---|
| warn | Unity recognised the skeleton as a person, so all {value} shared animations work. | Unity could not read the skeleton as a person, so the shared animations are missing; it still has its own walk and idle. | Fit the standard skeleton again | rig, `{template: "mixamo"}` |

Finish line: "Shared animations: {value} human moves work on this character." (warn form: "Shared animations: not available for this character; it still walks and idles.")

**C. What the user can change.** Nothing new. The `template` knob already exists (now with plain option labels).

**D. Engine notes.** Assign `importer.humanDescription` with `human` from the table and `skeleton` from the model's rest transforms (read them from a first Generic import). The SkinToken rest pose is close to an A-pose; whether to rotate the arms to a T-pose in the skeleton array is the implementer's call, tested on the knight and the goblin [S4][S40].

**E. Done when.** The goblin that fell back to Generic (STATUS.md) reports `avatar_mapping: explicit` and `humanoidValid: true`; the knight is unchanged; a snapshot of the goblin playing a library clip shows the arms moving.

### 3.7 G30 and G31: creatures that run, fight, fly and slither

**A. Default.** Each category yaml gains `motion` and `clips`; `blender/procanim.py` builds those clips.

| Category | `motion` | `clips` |
|---|---|---|
| humanoid | library | idle, walk (own fallback); the library supplies the rest |
| quadruped | legs | idle, walk, run, turn_left, turn_right, attack, hit, death |
| multileg | legs | idle, walk, run, turn_left, turn_right, attack, hit, death |
| winged | wings | idle, walk, flap, glide, takeoff, land, attack, hit, death |
| serpent | wave | idle, slither, turn_left, turn_right, attack, hit, death |
| others | none | none |

Clip recipes (starting values; implementer tunes by eye on the wolf, a spider, a dragon and a snake):

1. run: the walk at 1.8 times the frequency and 1.4 times the stride.
2. turn_left and turn_right: in place, a 90-degree root yaw over 1 second with stepping legs.
3. attack: spine forward and head pitch down over 0.8 seconds (a lunge), forelimbs raised for quadrupeds.
4. hit: spine recoil backward over 0.4 seconds.
5. death: root rolls 90 degrees about the forward axis and drops to the ground over 1.2 seconds; holds the last frame; does not loop.
6. Multileg: every leg chain, not the first four (remove `clusters[:4]`). Six legs use an alternating tripod (legs split by side and index parity); eight or more use a wave gait (phase = leg index / leg count).
7. Wings: chains that leave the torso sideways above the shoulder line and are longer than the legs count as wings (implementer may refine detection). flap: downstroke 40 percent of the cycle, upstroke 60 percent. glide: wings held level with a slow tilt. takeoff and land: one-shot.
8. Wave: each spine bone rotates by A(i) sin(wt - k i), with amplitude rising from 0.2 to 1.0 of the maximum toward the tail and one to one and a half waves along the body.

**B. Where it surfaces.**

Rig Fine-tune:

| Knob | Layer | Label | Options | Default | Categories |
|---|---|---|---|---|---|
| `motion` | simple | How does it move? | legs: "Walks on its legs"; wings: "Flies, and walks when it lands"; wave: "Slithers or swims"; none: "Does not move by itself" | from the category | quadruped, multileg, winged, serpent |
| `moves` | simple | Which moves | idle: "Standing still"; walk: "Walk"; run: "Run"; turn_left and turn_right shown as one option "Turn on the spot"; attack: "Attack"; hit: "Get hit"; death: "Die"; flap: "Fly"; glide: "Glide"; takeoff: "Take off"; land: "Land"; slither: "Slither or swim" | every clip of the category | same |
| `move_size` | Advanced | How big the moves are | small: "Small"; normal: "Normal"; big: "Big" (amplitude 0.7, 1.0, 1.3) | normal | same |
| `move_speed` | Advanced | How fast | slow: "Slow"; normal: "Normal"; fast: "Fast" (0.8, 1.0, 1.25) | normal | same |

Humanoids keep the export knob `clips_library`, relabelled "Animation set" with the options from 2.2.

Checks (rig stage):

| Id | Severity | ok | fail | Fix label | Fix |
|---|---|---|---|---|---|
| `clips` (ported) | warn | Animations: {value}. | Some animations are missing: {missing}. | Make the moves again | rig, `{}` |
| `legs_moved` | warn | All {value} legs move in the walk. | Only {value} of {limit} legs move in the walk. | Make the moves again | rig, `{}` |
| `wings_found` | warn | Both wings were found, so it can fly and glide. | We could not find the wings in the skeleton, so it walks but cannot fly. | (none; "Try again" re-rigs) | none |

`legs_moved` applies to quadruped and multileg; `wings_found` to winged.

Finish line: "Moves: {list}." For example "Moves: idle, walk, run, turn, attack, get hit and die."

Help entries:

| Key | Plain | Meaning | Why | Technical |
|---|---|---|---|---|
| `motion` | How it moves | Whether the creature walks, flies or slithers. | Decides which animations it gets; change it for a bird that cannot fly or a fish shaped like a snake. | Procedural locomotion mode: legs, wings, wave |
| `moves` | Moves | The animations made for this creature. | Leave out moves your game will never use to keep the file smaller. | Procedural clip set (procanim.py) |

**C. What the user can change.** How it moves, which moves, and in Advanced how big and how fast.

**D. Engine notes.** All clips are in place (root motion off, G33). Death does not loop; every cyclic clip carries the loop flag (G32). Foot contact marking is not part of v1.

**E. Done when.** The wolf exports 8 clips; a spider reports `legs_moved` 8 of 8; a dragon's flap moves wing bones (bone delta above zero); a snake shows a phase lag along the chain; each finish card names the moves.

### 3.8 G15 and G17: scenery that snaps together and takes baked light

**A. Default.** Environment pieces only.

1. Grid: 1 m by default (`grid_m` prep knob, Advanced; no project setting in v1, so every piece uses 1 m unless changed).
2. After turning to face forward (G14), prep rounds:
   - width and depth to the nearest whole number of grid steps, when that side is at least 0.75 of a step (thinner sides, such as a wall's thickness, are left alone);
   - height to the nearest half step.
   Each axis may stretch or shrink by at most 15 percent; a side that needs more keeps its size and the `grid_fit` check fails.
3. The pivot goes to the minimum corner (left, back, ground: lowest X, lowest Z, Y = 0), the same corner on every piece.
4. Prep renders a "How it tiles" picture in Blender Workbench, three-quarter view from above: three copies in a row for walls, a 3 by 3 block for floors (a piece is a floor when its height is under a quarter of its smaller footprint side).
5. G17: `VgacImport.cs` sets `generateSecondaryUV = true` for environment and prop_rigid. Environment prefabs get the static flags Contribute GI and Batching Static.

**B. Where it surfaces.**

Prep Fine-tune (environment only):

| Knob | Layer | Label | Options | Default |
|---|---|---|---|---|
| `grid_footprint` | simple | Size on the grid | auto: "Automatic (nearest fit)"; 1x1: "1 x 1 squares"; 2x1: "2 x 1 squares"; 2x2: "2 x 2 squares"; 4x1: "4 x 1 squares"; 4x4: "4 x 4 squares" | auto |
| `grid_height` | simple | How tall is it? | auto: "Automatic"; 0.5: "Floor (0.5 m)"; 1: "Low (1 m)"; 3: "Wall (3 m)"; 4: "Tall (4 m)" | auto |
| `grid_m` | Advanced | Grid size | 0.5: "0.5 m"; 1: "1 m (standard)"; 2: "2 m"; 4: "4 m" | 1 |

The prep view (`MeshView`) shows the tiling picture below the model under the label "How it tiles".

Checks (prep stage, environment):

| Id | Severity | ok | fail | Fix label | Fix |
|---|---|---|---|---|---|
| `grid_fit` | warn | The piece is {w} x {d} x {h} m and lines up with the {grid} m grid. | The piece is {w} m wide, between grid steps, so it leaves a gap next to other pieces. | Snap to the grid | prep, `{grid_footprint: "auto"}` |
| `tiles_cleanly` | warn | Copies placed side by side meet without gaps. | Copies placed side by side leave a gap or overlap of {value} cm. | (none; "Change something" applies) | none |

Finish line: "Snaps to a {grid} m grid, pivot on the corner, ready for baked lighting."

Help entries:

| Key | Plain | Meaning | Why | Technical |
|---|---|---|---|---|
| `grid` | Grid | An invisible floor of squares that scenery pieces line up on. | Pieces made to the grid fit together with no gaps or overlaps. | Modular grid, `grid_m`; dimensions rounded to grid steps |
| `corner_pivot` | Corner anchor | The point the piece is placed by: its back-left bottom corner. | Every piece uses the same corner, so they snap edge to edge. | Origin at the bounding-box minimum corner |
| `lightmap_uv` | Baked lighting ready | A second texture layout the engine uses to store pre-computed light and shadow. | Walls and floors can carry soft, realistic shadows without slowing the game. | Lightmap UV (UV1), Unity `generateSecondaryUV` |

**C. What the user can change.** Footprint and height on the grid; grid size in Advanced.

**D. Engine notes.** Grid snap of half the footprint is common practice [S33]. Unreal generates lightmap UVs at import, so v1 writes none for it. Non-uniform scale is applied in Blender and baked into the mesh; the prefab transform stays at identity.

**E. Done when.** A wall test piece built at 2.13 m comes out at 2.00 m with its origin at the bounding-box minimum within 1 mm; the tiling picture is written; `unity_report` shows `secondaryUV: true` for environment and prop_rigid.

### 3.9 G07 and G08: light and colour that react to the game

**A. Default.**

1. `styles.yaml` gains `delight_default` per style: `remove` for realistic_pbr and clay; `keep` for painted, cel, pixel, retro64 and lowpoly_flat. Painted-on shading is part of a stylised look, so the app does not strip it by default.
2. Prep scores baked light (correlation of base-colour luminance with the baked AO and with the world-up normal). When the style says remove and the score is above the limit, prep runs a delight correction with a strength that grows with the score. Algorithm: implementer decides (AO-guided division, or a high-pass against the up-normal correlation, or both).
3. Always: roughness is clamped to at least 0.04, silently.
4. Realistic styles also: non-metal albedo is soft-clamped into 30 to 240 sRGB, and metallic values between 0.2 and 0.8 are pushed toward 0 or 1. Stylised styles are only measured and reported.

**B. Where it surfaces.**

Prep Fine-tune:

| Knob | Layer | Label | Options | Default |
|---|---|---|---|---|
| `delight` | simple | Painted-on shadows | auto: "Automatic (by look)"; remove: "Remove them"; keep: "Keep them (part of the look)" | auto |
| `pbr_fix` | Advanced | Keep colours in a realistic range | on or off | on for realistic styles, off for others |

When delight ran, the prep view shows two small base-colour thumbnails labelled "Before" and "After".

Checks (prep stage):

| Id | Severity | ok | fail | Fix label | Fix |
|---|---|---|---|---|---|
| `baked_light` | warn | No painted-on shadows found in the colours. (Keep styles: "Painted shading is kept on purpose for this look.") | The colours still carry shadows from the picture, so they will fight your game's lights. | Remove painted-on shadows | prep, `{delight: "remove"}` |
| `albedo_range` | warn | Colours stay inside the range real materials have. | {value} of the colours are darker or brighter than real materials get, so they may look flat or glow under game lights. | Bring colours into range | prep, `{pbr_fix: true}` |
| `metal_mixed` | warn | Metal parts are clearly metal and the rest clearly not. | {value} of the surface is half metal, which looks dull and grey in game engines. | Make metal clearly metal | prep, `{pbr_fix: true}` |

For keep styles `baked_light` always passes with its keep sentence. `albedo_range` fails above 1 percent of non-metal pixels outside 30 to 240; `metal_mixed` above 5 percent [S28].

Finish line (only when a correction ran or a check failed): "Lighting: painted-on shadows removed, so it reacts to your game's lights."

Help entries:

| Key | Plain | Meaning | Why | Technical |
|---|---|---|---|---|
| `baked_light` | Painted-on shadows | Shadows and highlights from the original picture that ended up in the model's colours. | They stay put when your game's sun moves, and crevices look twice as dark. | Baked lighting in albedo; delight pass |
| `pbr_range` | Realistic colour range | The brightness range that real materials fall inside. | Colours outside it look flat, chalky or glowing under modern game lighting. | PBR validation: albedo 30 to 240 sRGB, metallic near 0 or 1, roughness above 0 |

**C. What the user can change.** Whether painted-on shadows are removed; in Advanced, the realistic-range correction.

**D. Engine notes.** Range figures from the Substance PBR guidance [S28]; Unity's old De-Lighting tool [S16] is background only. The AO used for scoring must be the one baked on the final mesh (G05 first).

**E. Done when.** On a realistic-style test prop, `baked_light` passes after the correction and fails before; `albedo_range` is under 1 percent after the fix; a stylised knight is unchanged by default.

### 3.10 G21 and G23: pieces that move

Designed in full in section 4.1. In short: the "Make it move" step for articulated props and vehicles finds the moving pieces itself, cuts them free and sets their hinge or axle; the user checks the open animation and, if it is wrong, moves a cut line and picks an edge in the viewer. If nothing is found and the user does nothing, the asset ships as one solid piece with a warning line.

### 3.11 G10 and G11: clean files for Godot, the web and Unreal

**G10, a clean GLB (S).**

1. Default: `<Name>.glb` holds LOD0 only and no collider. `<Name>_godot.glb` holds LOD0 plus the collider named `<Name>-colonly`, which Godot turns into a collision shape on import. Per-LOD GLBs go to `lods/`.
2. Download labels: `glb` "GLB (web, Blender)"; `glb_godot` "GLB for Godot (with collision)".
3. No knob and no check. Restore the GLB help copy in section 6 when it lands.
4. Done when: the knight GLB opens in the app viewer with one mesh and no grey hull; Godot import of the `_godot` file shows a collision shape (manual check, or skip and record it as not verified).

**G11, an Unreal folder (M).**

1. Default: export also writes `unreal/` with `SM_<Name>.fbx` (or `SK_<Name>.fbx` when skinned), LODs in one FBX as a LOD group, collision as `UCX_SM_<Name>_00` and up, textures `T_<Name>_BaseColor`, `T_<Name>_Normal` (green channel flipped for DirectX), `T_<Name>_ORM` [S18][S19][S20].
2. Surfaces: a finish-card line "For Unreal: open the unreal folder and import the FBX; collision and detail levels come in with it." and a download button "Unreal folder (zip)".
3. Engine notes: Unreal works in centimetres and Z up; the Blender FBX exporter's unit scale handles it, to be confirmed by one import. Unreal builds its own physics asset for skinned meshes, so hit boxes (section 4.2) are not exported for it. Vehicles for Chaos need one bone per wheel [S22]; that waits for G23.
4. Done when: an Unreal 5 import of the chest shows one static mesh with three LODs and collision. If no Unreal install is available, record it as not verified.

### 3.12 G06: quad layout for characters (not cheap)

G06 needs the G05 re-bake first, so it is not cheap and goes in Phase 9.

1. Default once built: skinned categories on PC and Showcase get a QuadriFlow remesh to the budget before the bake. Phone targets and props keep triangles.
2. Surfaces: no simple knob. Mesh Advanced `topology`, label "Surface layout": auto "Automatic"; quad "Even squares (bends better)"; tri "Triangles (lightest)". The category yaml's existing `topology` key becomes the default the code finally reads.
3. Check: `quad_ratio`, info-level warn, only when quad was chosen: ok "{value} of the surface is even squares, so joints bend cleanly." fail "Only {value} of the surface is even squares; elbows and knees may crease when they bend." Fix label "Rebuild with even squares", fix mesh `{topology: "quad"}`.
4. Help `topology`: Surface layout / "How the model's surface is divided: into even four-sided squares or into triangles." / "Even squares bend more cleanly at elbows and knees; triangles are lighter." / "Quad-dominant remesh (QuadriFlow) versus triangle decimation".
5. Done when: the knight on PC shows a quad ratio above 0.8 and the 90-degree elbow bend render shows less collapse than before (manual comparison).

### 3.13 G18 to G20: foliage

1. G20, part one (S, do it with Phase 7): set the foliage LODGroup `fadeMode = CrossFade` in `VgacImport.cs` [S8]. No UI.
2. G20, part two (M, Phase 9): render an 8-view billboard as the last LOD. Finish line "Far away it turns into a flat picture, so a forest stays fast."
3. G19 (M, Phase 9, only together with a shader): bake wind vertex colours (the Crysis channel scheme [S32]) and ship a small URP Shader Graph "VGAC Foliage Wind" that reads them, assigned by `VgacImport.cs`. Without a shader that reads the colours the feature does nothing for this audience, so do not build the colours alone. Rig Fine-tune for foliage: `wind`, label "Sways in the wind", on by default.
4. G18 (L): leaf cards cut from the concept. Start with a research spike (one tree, one bush) before committing; listed in Phase 10.

---

## 4. Designed flows

### 4.1 Pieces that move (G21, G23)

**User goal.** "The lid of my chest should open in the game, and my car's wheels should turn."

**Who and when.** Someone making an articulated prop or a vehicle, usually on autopilot, looking at the result in step 5 ("Make it move"). They will not know the words hinge, pivot or axle and should not need them. They will look at it once per asset.

**Where it lives.** Step 5, the rig stage. Its plain name is already "Make it move". For categories whose yaml says `parts_engine: split` (new key: prop_articulated and vehicle; `rig_engine` stays `none` so budgets and estimates do not change), the rig stage runs the split job instead of skipping. Guide panel copy for these categories:

| Guide state | Copy |
|---|---|
| empty, "now" | Next we find the pieces that move ({parts}), cut them free and set where they turn. |
| empty, button | Find the moving pieces (about 30 seconds) |
| running, gerund | Cutting the moving pieces free |

The rig stage's help for these categories (frontend override keyed on `parts_engine`): meaning "Finds the pieces that move, such as a lid or wheels, cuts them free and sets where each one turns." why "Without this the lid cannot open and the wheels cannot turn." technical "Plane split, cap, pivot on hinge or axle, open and close clips".

**Kinds of piece.** The words in the asset's parts list (asset form, Advanced, "Parts"; defaults from the category yaml) decide the kind by keyword:

| Words | Kind | How it moves | Cut | Default hinge or axis | Default range |
|---|---|---|---|---|---|
| lid, cover, top, flap | hinge | Swings open | Across (horizontal) | Back edge | 110 degrees |
| hatch | hinge | Swings open | Across | Back edge | 90 degrees |
| door, gate, shutter, on prop_articulated, when the whole object is a slab (depth under a quarter of width) | whole | The whole object swings | No cut | Left edge | 90 degrees |
| wheel, wheels, tyre, tire | axle | Spins | Circle around the wheel | Left-right through the wheel centre | Free spin |
| turret, tower, mount | turn | Turns round | Across | Upright through the middle of the cut | All the way round |
| door or doors on a vehicle | (v1: not supported) | Stays shut | none | none | none |
| anything else | unknown | Asked (see below) | | | |

**Default behaviour, when the user does nothing.**

1. The split job proposes a cut for each piece. For a lid: scan horizontal slices between 50 and 90 percent of the height and choose the height with the sharpest change in outline (a ledge or seam); the implementer decides the measure. For wheels: find round side profiles near the bottom corners; implementer decides the detector.
2. Each proposal carries a confidence. At or above the threshold, it is applied: the piece is cut free, the cut face is capped, the pivot is placed on the hinge or axle, and the clips are written.
3. Below the threshold, the piece stays joined. Its proposal (or a fallback placement: lid line at 70 percent of the height; four wheels at plus or minus 0.42 of the width, plus or minus 0.32 of the length, radius 0.17 of the height) is saved only as the editor's starting position, because a wrong cut is worse than none.
4. The asset always exports. With no pieces found it is one solid piece with a correct base pivot, the `parts_found` check fails as a warning (the autopilot does not stop), and the finish card says what to do.

**Review (after the job).** The rig view leads with the viewer playing open and close in a loop (Reduce Motion: a still of the open pose with a "Show open / Show closed" toggle). The moving piece is drawn with an outline in `--accent`. Under the viewer, a list headed "Pieces that move":

| Row state | Copy | Actions |
|---|---|---|
| hinge found | Lid: opens {range} degrees on the {edge} edge. | Show, Change |
| whole found | Door: swings {range} degrees on the {edge} edge. | Show, Change |
| axle found | Wheels: {n} found; the front pair steers. | Show, Change |
| turn found | Turret: turns all the way round. (or "turns {range} degrees each way") | Show, Change |
| not found | {Name}: not found yet, so it stays part of the {asset}. | Mark it, It does not move |
| vehicle doors | Doors: stay shut (opening car doors is not supported yet). | none |
| unknown | We do not know how a {name} moves, so it stays fixed. | How does it move? (select) |

"Show" plays that piece's motion alone. "Change" and "Mark it" open the editor at that piece. Then the usual question: "Happy with this?" with "Yes, keep it", "Try again", "Change something". "Change something" opens the parts editor first, with "Describe the change" below it (text still works, for example "the lid is a bit lower").

**The editor.** It sits in the Change panel, next to the main viewer, which it drives. Everything is previewed live in the browser without a server round trip: the model is drawn twice with opposite clipping planes, and the piece's copy is rotated about the chosen hinge. Only "Cut the pieces free" runs a job.

States:

| State | What the user sees | Copy | Next |
|---|---|---|---|
| E0 loading | Viewer spinner | Loading the model... | E1 |
| E1 overview | The "Pieces that move" list, editable | Pick a piece to set up. | E2, W1, T1 or K |
| E2 hinge cut | A translucent slab across the model at the cut height with a drag handle on its right edge; the piece above it is outlined | Move the line to where the lid meets the box. | E3 |
| E3 hinge edge | Four picture buttons (a small top-down box drawing with one edge thick); the hovered or focused edge also lights up on the model | Which edge does it swing on? | E4 |
| E4 range | Three choices | How far does it open? | E5 |
| E5 preview | The piece opens and closes (Reduce Motion: still, with the toggle) | Does it open the right way? | E1 (Yes) or flipped preview |
| W1 wheels | Click on the model; a ring appears at the click with its size guessed (the click height above the ground is the radius) | Click the middle of each wheel. | W1 (more wheels) or E1 |
| T1 turret | Cut slab as in E2, then two choices for range | Move the line to where the turret sits on the body. Then: How far can it turn? | E1 |
| K unknown kind | One select | How does the {name} move? | E2, W1, T1 or E1 |
| V1 line off the model | "Cut the pieces free" disabled | Move the line onto the model; right now nothing is cut off. | E2 |
| V2 suspicious size | Inline note, not blocking | That cuts off almost the whole {asset}. Is the line too low? (or "That piece is very small. Is the line too high?") | continue |
| V3 click missed | Inline note | Click on the model itself. | W1 |
| R running | Guide panel running state | Cutting the moving pieces free. About 30 seconds left. | D or X |
| X error | Error box with Try again | We could not cut the {name} free there. Try moving the line a little. | E1 |
| D done | New rig version in review | (the review list above) | review |

V2 fires when the cut-off piece is under 1 percent or over 80 percent of the model's volume.

Controls:

| Control | Type | Default | Keyboard | Accessible name |
|---|---|---|---|---|
| Piece row "Change" or "Mark it" | button | none | Tab, Enter | "Change the lid" / "Mark the lid" |
| Cut height | range input 0 to 100 percent of height, step 0.5, with a readout "52% up" | proposal | Arrow keys 0.5, Page Up and Down 5 | "Cut height for the lid, percent of the model's height" |
| Cut drag handle | pointer drag on the slab edge in the viewer | mirrors the range input | none (the range input is the keyboard path) | not focusable (canvas) |
| Cut direction (under "More options") | two-button group: "Across" / "Upright" | from the kind | Arrow keys | "Cut direction" |
| Hinge edge | radio group of picture buttons: Back, Front, Left, Right (whole doors: Left, Right) | from the kind | Arrow keys | "Which edge does it swing on?" |
| Range (hinge) | radio group: "A little (45 degrees)", "Halfway (90 degrees)", "Wide (110 degrees)" | from the kind | Arrow keys | "How far does it open?" |
| Range (turret) | radio group: "All the way round", "A quarter each way (90 degrees)" | all the way | Arrow keys | "How far can it turn?" |
| Preview answers | "Yes, that is right" (primary in the editor), "Open the other way" | none | Tab, Enter | as labelled |
| Wheel size | "Smaller" and "Bigger" buttons (5 percent steps) plus a range input | guessed | Arrow keys on the range | "Wheel size" |
| Same on the other side | checkbox | on for symmetric categories | Space | "Add the matching wheel on the other side" |
| These wheels steer | checkbox | on for wheels in the front half | Space | "These wheels steer" |
| Remove this wheel | link button | none | Enter | "Remove this wheel" |
| How does it move? (unknown kind) | select: "Swings open (like a lid or door)", "Spins (like a wheel)", "Turns round (like a turret)", "Does not move" | Does not move | standard | "How does the {name} move?" |
| Footer | "Cut the pieces free" (submits all pieces; this is the Change panel's action), "Cancel" | none | Tab, Enter | as labelled |

The editor keeps its draft while the panel is open; Cancel or leaving the page discards it.

Screen reader: the canvas is not readable, so the overview list is the source of truth, and each change is announced in a polite live region ("Lid cut at 52 percent of the height, swings on the back edge, opens 110 degrees").

**What is submitted.** One hidden rig knob `parts_spec` (string JSON, not in either Fine-tune layer; the editor writes it), coordinates in metres in prep space (Y up, +Z front after G14):

```json
{"parts": [
  {"name": "lid", "kind": "hinge", "cut": {"axis": "y", "at": 0.52}, "edge": "back", "open_deg": 110, "flip": false},
  {"name": "wheel_fl", "kind": "axle", "center": [0.71, 0.34, 1.32], "radius": 0.34, "axis": "x", "steer": true},
  {"name": "turret", "kind": "turn", "cut": {"axis": "y", "at": 0.71}, "range_deg": 360}
]}
```

**What the job produces.**

1. A new `blender/split_parts.py` bisects along each plane (or cuts a cylinder for wheels), fills and triangulates the cut faces, and maps the cap to a dark interior colour sampled from the darkest 10 percent of the base colour (implementer may choose another sample). Interior modelling (G22) is later.
2. Each piece becomes its own object named `<Name>_<Piece>`, parented to `<Name>_Body`, with its origin on the hinge line (for a lid: the cut face's back edge), the wheel centre or the turret's vertical axis.
3. Hinge pieces get clips `open` (0 to range, 0.8 seconds, ease out), `close`, and `closed_idle`.
4. The manifest gets `parts: [{name, kind, pivot, axis, range_deg, radius, steer}]`.
5. Each piece gets its own LOD chain and its own collider by the best-fit rule (G16), parented to the piece so it moves with it. Wheels get no collider.

**In Unity.**

1. Hinge objects: the Animator gets a bool `IsOpen` with states Closed, Open and the two transitions.
2. Vehicles: a `Wheels` child holds one `WheelCollider` per wheel at the wheel centre with its radius; the visual wheels stay separate [S14]; a `Rigidbody` goes on the root with mass 1000 kg times (length / 4.5) cubed (implementer may refine).

"Using it in Unity" text:
- Hinge: 1. Drag the prefab into your scene. 2. To open it: `animator.SetBool("IsOpen", true);` 3. To close it, set it back to false.
- Vehicle: 1. Drag the prefab into your scene. 2. The wheel colliders are under Wheels; drive them from your car script. 3. Spin and steer the visual wheels (`Wheel_FL` and the rest) to match.

**Checks.**

| Id | Severity | ok | fail | Fix label | Fix |
|---|---|---|---|---|---|
| `parts_found` | warn | Pieces that move: {list}. | The {name} is still part of the {asset}, so it will not {verb}. | Mark the {name} | `{ui: "parts_editor"}` |
| `part_pivot` | warn | The {name} turns on its {hinge or axle}, not its middle. | The {name} turns about the wrong point. | Cut it again | rig, same `parts_spec` |

`verb` is open, spin or turn. `part_pivot` fails when the origin is more than 1 cm from the hinge line or axle.

**Finish lines.**
- "Moving parts: the lid opens 110 degrees on its back edge."
- "Moving parts: 4 wheels (the front pair steers) and a turret that turns all the way round."
- Warn: "Moving parts: the lid is not separate yet. Mark it in Make it move to make it open."

**Help entries.**

| Key | Plain | Meaning | Why | Technical |
|---|---|---|---|---|
| `moving_pieces` | Pieces that move | Parts of the model cut free so they can open, spin or turn on their own. | A chest with a separate lid can open in the game; one solid block cannot. | Mesh split into child objects with pivots on hinge or axle |
| `hinge_edge` | Hinge edge | The edge a lid or door swings on. | Pick the wrong one and the lid opens toward you instead of away. | Pivot line and rotation axis of a hinged part |
| `wheel` | Wheel | A round piece that spins on its centre. | Separate wheels can turn and steer while the car drives. | Axle pivot, radius and position; Unity WheelCollider |

**Quick desk check (cognitive walkthrough).**

1. Will the user know pieces can move? Yes: the step is called "Make it move", the viewer plays the lid opening, and the finish card names the moving parts.
2. Will they see when it is wrong? The open animation makes a wrong cut or a wrong edge obvious in a second.
3. Will they know what to do? "Change" sits next to the row that is wrong; the editor asks one question per screen, in their words.
4. Can they get stuck? No: every state has a way back to the overview, and Cancel leaves the kept version as it was.
5. If they ignore it all? The asset ships as a working solid prop with a correct pivot and a single warning line.

### 4.2 Hit boxes (G34)

**User goal.** "When something hits my character, it should count where it looks like it landed." Most users have never heard "hit box". The app makes them anyway and shows them once, so the word is learned by seeing it.

**Where it lives.** Fitted in the rig stage, right after the skeleton (it needs the bones), so it can be previewed with the animation. Built into the prefab at export.

**Default behaviour, when the user does nothing.**

1. Regions come from bone names (Mixamo template) or, for other skeletons, from `procanim.py`'s chain classes (head, neck, spine, tail, leg n, wing left and right):

| Category | Detailed (default on PC and Showcase) | Simple (default on Phone) |
|---|---|---|
| humanoid | 11: head, chest, hips, upper arm and forearm each side, thigh and shin each side | 2: head, body |
| quadruped | 8: head, neck, body, tail, one per leg | 2: head, body |
| multileg | head, body, one per leg | 2: head, body |
| winged | head, body, one box per wing, one per leg | 3: head, body, wings as one box |
| serpent | head plus a chain of capsules along the spine (bones / 3, between 4 and 10) | head plus 3 along the spine |

   Phone defaults to simple because each shape costs physics time on small devices; this is a judgement call, listed for the owner in section 8.

2. Fit: for each region, take the vertices whose strongest bone weight is in that region. The capsule axis runs from the region's first bone head to its last bone tail. The radius is the 80th-percentile distance of those vertices from the axis, times the snugness factor (snug 0.9, normal 1.0, loose 1.15). The ends are extended by the radius and clipped to the vertices' extent. Wings use an oriented box at least 5 cm thick. The implementer may refine the method.
3. Fallback when regions cannot be told apart (for example, a generic skeleton with no chains found): one capsule around the whole body and one sphere at the head end (the top of a standing creature, the front of a lying one).
4. Output: `hitboxes.json` with `[{name: "Hit_Head", bone, kind: "capsule" | "box", center, axis, radius, height}]` in bone-local space.
5. A body collider is always built too (G16 table). Hit boxes never replace it.
6. Re-fitting must not re-run SkinToken: when only hit-box knobs differ from the parent rig version, the rig stage reuses the parent's rigged files and re-fits only (about 10 seconds).

**Where it surfaces.**

1. Rig view: one line under the intro: "Hit boxes: {n}, fitted to the body. They move with the skeleton so hits land where they look."
2. A viewer toolbar toggle "Hit boxes" beside "Bones", `aria-pressed`, off by default (bones are on, and both together are cluttered).
3. When on, translucent capsules (fill `--warn` at 30 percent, outline `--warn`) are attached to their bones in the three.js scene, so they follow the playing clip. Hovering one shows its region in the viewer status line ("Left forearm").
4. A Details disclosure "Hit box list" names every region as text, for screen readers and for checking.

States:

| State | What shows | Copy |
|---|---|---|
| no skeleton yet | nothing | |
| fitted (default) | line plus toggle | Hit boxes: {n}, fitted to the body. They move with the skeleton so hits land where they look. |
| fitted, fallback | line plus toggle | Hit boxes: one for the body and one for the head. We could not tell the limbs apart on this skeleton. |
| none chosen | line, no toggle | No hit boxes. The body shape still keeps it out of walls. |
| re-fitting | guide running state | Fitting the hit boxes. About 10 seconds left. |
| fit failed | `hitbox_fit` check fails; rig version still usable | (check sentence below) |
| check warns | check rows with Fix | (check sentences below) |
| Reduce Motion | overlay on the paused rest pose; plays only when the user presses Play | |
| category without skeleton | nothing in the rig view; finish line says the collision shape is the hit shape | |

Rig Fine-tune (categories humanoid, quadruped, multileg, winged, serpent):

| Knob | Layer | Label | Options | Default |
|---|---|---|---|---|
| `hitboxes` | simple | Hit boxes | detailed: "One per body part"; simple: "Just head and body"; none: "None" | detailed on PC and Showcase, simple on Phone |
| `hitbox_fit` | simple | How snug | snug: "Snug"; normal: "Normal"; loose: "Loose" | normal |
| `hitbox_regions` | Advanced | Body parts with hit boxes | one checkbox per region found, labelled in plain words ("Tail", "Left wing") | all |

Export Fine-tune (same categories), replacing `collider`:

| Knob | Layer | Label | Options | Default |
|---|---|---|---|---|
| `movement_shape` | Advanced | Body shape for moving | auto: "Automatic (by category)"; upright: "Standing capsule (works with Unity's Character Controller)"; along: "Lying capsule along the body"; box: "Box"; none: "None" | auto |

Checks:

| Id | Stage | Severity | ok | fail | Fix label | Fix |
|---|---|---|---|---|---|---|
| `hitbox_cover` | rig | warn | {n} hit boxes cover {value} of the body and follow the skeleton. | The hit boxes cover only {value} of the body, so some hits would pass through. | Fit them looser | rig, `{hitbox_fit: "loose"}` |
| `hitbox_overreach` | rig | warn | The hit boxes stay close to the body. | The hit boxes stick out well past the body ({value} times its size), so hits would land in thin air. | Fit them snugger | rig, `{hitbox_fit: "snug"}` |
| `hitbox_fit` | rig | warn | (passes silently) | We could not fit hit boxes on this skeleton; it still has its body shape for moving. | Try again | rig, `{}` |
| `movement_shape` | export | warn | A {shape} {size} m {axis_word} keeps it from walking through walls. | No body shape was built, so it would fall through floors. | Add a body shape | export, `{movement_shape: "auto"}` |

`hitbox_cover` fails when under 85 percent of the surface vertices lie inside at least one shape; `hitbox_overreach` when the shapes' total volume is more than 1.6 times the mesh volume. `shape` is "standing capsule", "lying capsule" or "box".

Finish line: "Bumps into things: a {shape} for moving and {n} hit boxes that follow the animation." (Simple: "...and hit boxes for the head and body.")

"Using it in Unity" (one extra line when hit boxes exist): "Hit boxes are trigger colliders named Hit_Head, Hit_Chest and so on under the bones; check the name in OnTriggerEnter to know where a hit landed."

Help entries:

| Key | Plain | Meaning | Why | Technical |
|---|---|---|---|---|
| `hitboxes` | Hit boxes | Invisible shapes on each body part that tell the game where an attack landed. | Hits count where they look like they land, and you can tell a head hit from an arm hit. | Per-bone trigger capsules and boxes, fitted from skin weights |
| `hitbox_fit` | How snug | How tightly the hit boxes wrap the body. | Snug is fairer for the player being hit; loose is kinder to the player aiming. | Radius factor 0.9, 1.0 or 1.15 on the 80th-percentile vertex distance |
| `movement_shape` | Body shape for moving | One simple shape around the whole body that the game uses for walking into walls and standing on floors. | It stays still while the hit boxes move, so movement is smooth and predictable. | Root CapsuleCollider (direction Y or Z) or BoxCollider |

**What stays expert-only.** Moving or resizing single hit boxes by hand is not offered (section 7). Experts adjust them in the engine; every one is a named child object.

**Engine notes.**
1. Unity: one child per hit box under its bone, named `Hit_<Region>`, rotated so its Y axis lies along the capsule axis, with a `CapsuleCollider` (direction 1) or `BoxCollider`, `isTrigger = true`. No layer or tag is set, because neither exists in a user's project by default; the names are the contract.
2. Trigger events need a Rigidbody on one side; the game's attack or the character's own controller usually provides it. Say so in the manifest notes.
3. The body collider sits on the root: an upright `CapsuleCollider` for standing categories, `direction = 2` (Z) for long bodies.
4. Unreal builds its own physics asset from the skeleton; nothing is exported for it. Godot: hit boxes are listed in the manifest only.

**Quick desk check.**
1. Will the user know they exist? Yes: the rig view line, the toggle beside Bones, and the finish card.
2. Will they understand? The overlay shows them moving with the walk; the Info entry explains in one sentence.
3. Can they fix a bad fit? Yes: two Fix buttons and one "How snug" choice, each re-fitting in about 10 seconds.
4. If they ignore it? They get sensible hit boxes and a body shape that works with Unity's Character Controller (for standing categories).

---

## 5. Implementation order

Ordered by user value divided by effort. Value counts how many categories benefit and how visible the difference is in a game; effort uses the S, M, L scale from the top of this document.

### Phases

| Phase | Gaps (effort) | What the user notices | Lanes | Can start when |
|---|---|---|---|---|
| 0 | Plumbing 2.1 to 2.7 (S), G51 copy (S) | Honest help text; plain option names; passing checks read as passes; Fix buttons say what they do | B and C in parallel | now |
| 1 | G01 and G02 (S), G32 and G33 (S), G26 (S) | In Unity: metal looks like metal, the walk loops, a controller works, stylised humanoids get the 46 animations | A (one agent, one file), small B for the packer | after Phase 0's 2.6 |
| 2 | G12 with G14 and light G13 (S; front picker M), G10 (S) | Right size on the right axis, facing forward; a clean GLB | B, plus C for the size label and front picker | with Phase 1 (no shared files except the append-only registries) |
| 3 | G34 (M), G16 with G35 (M) | Characters collide and take hits properly; doorways and carts stay open | A, B and C | after Phase 1 (importer) and Phase 2 (`pack.py`) |
| 4 | G03 (S), G05 (M) | No wavy shading, no distant seams, sharper textures | B only | with Phase 3's A and C lanes; its B lane touches different files |
| 5 | G08 (S), G07 (M) | Realistic assets react to game light; colours in range | B, plus C for the before and after thumbnails | after Phase 4 (`stages/prep.py` and final textures) |
| 6 | G30 (M), G31 (L) | Creatures run, turn, fight and die; dragons fly, snakes slither, spiders use every leg | B (`procanim.py`), A (controller states), C (knobs) | after Phase 3 (`stages/rig.py`); `procanim.py` work may start earlier |
| 7 | G15 (M), G17 (S), G20 cross-fade (S) | Scenery snaps on a grid and takes baked light; trees fade between detail levels | B, A, C | after Phase 5 (`stages/prep.py`, `blender/prep.py`) |
| 8 | G21 (L), G23 (L, M once G21 is done) | Lids open, doors swing, wheels turn | A, B and C (new files mostly) | after Phase 2 (facing) and Phase 3 (per-piece colliders) |
| 9 | G11 (M), G06 (M), G20 billboard (M), G19 with shader (M), G22 interior (M) | Unreal folder; better bending on PC; faster forests; swaying trees; chests hollow inside | mixed | after Phase 8, or earlier per file table |
| 10 | G18 research spike, then build (L) | Trees with real leaves | B | when the owner asks |

Why this order: Phases 0 to 2 are almost all S and touch every asset the app makes. Phase 3 is the biggest change in how assets behave in play for M effort. Phase 4 must come before Phase 5 (the light score needs the final AO) and before G06. Creature animation (Phase 6) is high value but L. Scenery (Phase 7) helps one category. The parts flow (Phase 8) is high value for two categories but the most new work.

### Lanes and files

Lane A is the Unity importer (one agent at a time: it is one file). Lane B is the Python pipeline and Blender scripts. Lane C is the frontend.

| File | Phases | Rule |
|---|---|---|
| `unity-sandbox/Assets/Editor/VgacImport.cs` | 0 (version), 1, 3, 6, 7, 8, 9 | One owner per phase; raise `Version` every time |
| `backend/vgac/guidance.py` | every phase | Backend lane owns it; each phase appends its own block of `CHECK_GUIDE`, `PARAM_HELP` and finish-line entries, so merges are append-only |
| `frontend/src/help.ts` | every phase | Frontend lane owns it; same append-only rule (`TERM_HELP`, `OPTION_LABELS`, `SIMPLE_PARAMS`, mock `CHECK_TEXT`) |
| `backend/vgac/reference.py` (`STAGE_SCHEMAS`) | 0, 2, 3, 4, 5, 6, 7, 8 | Backend lane; one knob block per phase |
| `backend/vgac/data/categories/*.yaml` | 2, 3, 6, 7, 8 | Backend lane; one key family per phase (`size_*`, `remesh_default`, `motion` and `clips`, grid, `parts_engine`) |
| `backend/vgac/data/styles.yaml` | 5 | |
| `backend/vgac/quality.py`, `backend/vgac/imaging.py` | 2, 3, 4, 5, 7 | Sequential within lane B |
| `backend/vgac/stages/prep.py`, `backend/vgac/blender/prep.py` | 2, 4, 5, 7 | Sequential within lane B; the reason Phases 4, 5 and 7 are in that order |
| `backend/vgac/stages/mesh.py`, `backend/vgac/graphs/trellis.py` | 4 (and 9 for G06) | |
| `backend/vgac/stages/rig.py`, `backend/vgac/blender/rig_info.py` | 3 (hit boxes), 6, 8 | Sequential |
| `backend/vgac/blender/procanim.py` | 6 | Can start in parallel with Phases 3 to 5 |
| `backend/vgac/blender/pack.py`, `backend/vgac/stages/export.py` | 0 (importer copy), 1 (packer), 2 (GLB), 3 (CoACD), 8, 9 | Sequential within lane B |
| new `backend/vgac/blender/split_parts.py` | 8 | |
| `frontend/src/components/ui.tsx` (`PlainChecks`) | 0 | |
| `frontend/src/pipeline/FineTune.tsx` | 0, 2 (size label, front picker), 7 | |
| `frontend/src/pipeline/AutopilotPanel.tsx` (`FinishCard`) | 0 | Later phases only add data, not code |
| `frontend/src/Viewer.tsx` | 0 (Reduce Motion), 3 (hit box overlay), 8 (clipping preview) | Sequential within lane C |
| `frontend/src/pipeline/views/RigView.tsx` | 3, 6, 8 | |
| `frontend/src/pipeline/views/MeshView.tsx` | 5, 7 | |
| `frontend/src/pipeline/views/ExportView.tsx` | 0 (copy), 1, 2 | |
| `frontend/src/glbSnapshot.ts` | 2 | |
| new `frontend/src/pipeline/PartsEditor.tsx` | 8 | |
| `frontend/src/api.ts`, `frontend/src/mock/data.ts` | 0, then any phase that adds a type or mock data | Keep the mock in step so `npm run dev:mock` still shows each new surface |

### Verifying each phase

1. Backend: `.venv\Scripts\python -m pytest -q tests` from `backend/`, with the new tests named in each "Done when".
2. Frontend: `npm run build` and `npm run lint`; open the mock and the real backend and look at each new surface (manual).
3. Unity: batch import of the knight, the wolf and the chest, plus the phase's own test asset (goblin, spider, dragon, snake, cart, arch, wall, car); read `unity_report.json`.
4. End to end: `tools/e2e.py` on one asset per category the phase touches.
5. Record in STATUS.md what was verified and what was not; a green test run alone is not "done" for anything the user sees.

---

## 6. Copy to change now (G51)

Each line promises something the app does not do yet. Change it now; restore the "later" wording when the named gap lands.

| File, key | Now | Change to now | Restore when (wording) |
|---|---|---|---|
| `help.ts` `CATEGORY_HELP.vehicle` why | No skeleton; wheels, turret and doors are separate parts you can move. | No skeleton; for now it comes out as one solid piece with a collision shape. | G23: No skeleton; wheels and the turret become separate pieces that spin or turn. |
| `help.ts` `CATEGORY_HELP.prop_articulated` why | The moving part becomes a separate piece on a hinge. | For now it comes out as one solid piece; the lid or door does not open yet. | G21: The moving part becomes a separate piece on a hinge, with open and close animations. |
| `help.ts` `CATEGORY_HELP.environment` meaning | A piece of scenery that snaps to a grid, such as a wall or floor tile. | A piece of scenery, such as a wall or floor tile. | G15: A piece of scenery that snaps to a grid, such as a wall or floor tile. |
| `help.ts` `CATEGORY_HELP.environment` why | No skeleton; built to line up with other pieces. | No skeleton; sized in metres, but not yet rounded to a grid, so check that pieces line up. | G15: No skeleton; sized to the grid with its anchor on a corner, so pieces line up. |
| `help.ts` `CATEGORY_HELP.winged` why | Gets a skeleton with wing bones. | Gets a skeleton fitted to its shape; for now it walks and idles but does not flap. | G31: Gets a skeleton with wing bones, so it can fly, glide and land. |
| `help.ts` `CATEGORY_HELP.multileg` why | Gets a many-legged skeleton predicted from the shape. | Gets a skeleton fitted to its shape; for now the walk moves up to four legs. | G31: Gets a many-legged skeleton, and every leg moves in the walk. |
| `help.ts` `CATEGORY_HELP.serpent` why | Gets a spine-chain skeleton that bends along its length. | Gets a spine-chain skeleton; for now it sways rather than slithers. | G31: Gets a spine-chain skeleton that slithers or swims in a wave. |
| `help.ts` `TERM_HELP.parts` why | Each part becomes its own piece in the game. | We ask the painter to draw these as clearly separate pieces; for now they stay joined to the model. | G21: Each part becomes its own piece in the game that can open, spin or turn. |
| `help.ts` `TERM_HELP.collider_vertices` and `collider_verts` why | Engines cap this at 255. | Unity allows at most 255 triangles in a simple collision shape; we keep the shape small to stay under it. | G35: replaced by `collider_triangles` (section 3.4). |
| `help.ts` `TERM_HELP.lod_count` why | Four is standard. | Characters and creatures get four; props and scenery get three. | (keep) |
| `ExportView.tsx` ready-box line | Download the files for your game engine. FBX works in Unity, Unreal and Godot; GLB works on the web. | Download the files for your game engine. FBX works in Unity, Unreal and Godot. The GLB holds every detail level and the collision shape, so on the web or in Godot show only the first model. | G10: Download the files for your game engine. FBX works in Unity, Unreal and Godot; GLB works on the web and in Godot. |
| `ExportView.tsx` `FILE_NAMES.roughness` | shine texture | roughness texture | (keep; "shine" is the opposite of roughness and misleads) |
| `PlainChecks` Fix tooltip | Re-runs {stage} with {JSON} | Makes a new version of "{stage plain name}" with this change. | (keep) |

Check also `frontend/src/mock/data.ts` for the same strings, and `GlossaryPage.tsx` if it holds its own copies (it should read `TERM_HELP`).

---

## 7. Do not build

| Item | Why not, for this audience |
|---|---|
| A UV editor, texel-density slider or atlas view (G04 exposure) | Nobody without 3D training can act on it; the padding and atlas checks catch the problems, and experts use Blender. |
| A choice of texture packing or normal-map convention | The app writes every engine's version; a choice only adds a way to get it wrong. |
| Dragging or resizing single hit boxes or collision shapes | The fit is automatic with three snugness levels; anyone who needs per-shape control is already in the engine, where every shape is a named object. |
| Freeform or lasso cutting, and AI part segmentation models | Plane cuts and wheel clicks cover lids, doors, hatches, wheels and turrets; free segmentation models have unclear licences, and free-form cuts need 3D skill. |
| Opening car doors in v1 | A side door needs a region cut, not a plane; it doubles the editor's design for a part few small games need. Revisit if asked. |
| Drawers, sliding parts and multi-hinge chains | Rare in this audience's assets; the four kinds above cover the shortlist examples. |
| Twist bones and Unreal IK helper bones (G29) | Low impact at gameplay distance; Unreal users who need IK bones are technical. |
| Procedural finger bones (G25) | No free rigger does fingers; mitten hands read fine at gameplay distance for small games. |
| Trim sheets (G46) | A level-artist technique for large modular kits; this audience makes a handful of pieces. |
| HDRP mask map | Small teams use URP; HDRP users are technical and can repack. |
| A Nanite option | Unreal enables Nanite with one checkbox at import. |
| An in-app animation graph editor | The fixed controller plus three lines of "Using it in Unity" covers it; Unity's own Animator window is the right tool beyond that. |
| A per-level detail-switch editor (G36) | Derive the switch heights from size automatically when G36 is done; no user needs to see them. |
| A quad or triangle choice in the simple layer | The app picks by category and platform; the choice stays in Advanced (G06). |
| Wind vertex colours without a shader that reads them (G19 alone) | Invisible in every engine by default, so it would be a feature that does nothing. |

---

## 8. Hand-offs and open questions

| Item | Owner | Note |
|---|---|---|
| Copy polish for every string in this plan | narrative-designer | Keep meaning and length; the strings are shippable as written |
| Overlay colours (cut plane in `--accent`, hit boxes in `--warn` at 30 percent) against the viewer background, for contrast and colour-vision safety | art-director | Uses existing tokens; no new colours proposed |
| Stylised styles keep painted shading by default (G07) | owner | Recommended: yes; it is part of the look for painted, cel, pixel, retro and flat styles |
| Phone defaults to simple hit boxes (G34) | owner | Recommended: yes; easy to change per asset |
| Opening car doors left out of v1 (G23) | owner | Recommended: yes; doors stay shut with an honest row in the list |
| One grid per project instead of per asset (G15) | owner, then senior-fullstack-engineer | v1 is a per-asset knob with a 1 m default to avoid a database change; a project setting would need a column |
| Unreal and Godot verification (G10, G11) | owner | Neither engine is installed in the stack; without them those rows are recorded as not verified |
| Unity facts marked "verify" (clip Loop Time default, `silhouette_offset` sign, A-pose acceptance in the human description) | unity-engineer | Check in the sandbox at the start of Phase 1 and Phase 2 |
