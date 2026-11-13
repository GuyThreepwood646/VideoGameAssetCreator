# Your prompt in view, extra LoRAs on step 2, bringing your own files in at any step, and the delete word

UX-PROMPT-IMPORT. Owner's list of 2026-11-12. Status: items 2 and 4 built (frontend only, built into frontend/dist, checked in the mock preview). Item 3 phase A built 2026-11-12 (see "Phase A as built" below); phases B and C and item 1 are designed, not built.

## Inherited decisions

- Tokens: CSS variables in frontend/src/styles.css (--text, --muted, --input, --border, --surface-raised, --s1 to --s4, --fs-xs/--fs-sm, --radius-sm/--radius-md). No colour literals were added.
- Desktop app (Electron, mouse first), app-wide floor `body { min-width: 1280px }` (docs/PROJECTS-PAGE-SPEC.md). Narrower windows scroll sideways by design.
- `.field-label` is upper-case with letter spacing everywhere; text the person must read exactly has to opt out of that.
- One editing behaviour for the description: a changed description asks "Make a new step 1 picture from this description?" (UI-04, UI-26). The new step 1 card reuses that same component as the left rail, so they cannot drift.
- The prompt the painter got is already recorded per version: `metrics.prompt_final` (stages/concept.py, description plus style block, hair, palette, pose for 3D, parts and face lines, with LoRA trigger words). No backend change was needed.
- Ways to bring your own files in already exist: step 1 "Use my own picture", step 0.5 "From your pictures", step 2 "I have my own side or back views", step 4 "your own face close-up", your own LoRAs (step 1 painters only), hairstyles and moves.
- No analytics port exists in the frontend; none is added.

---

## Item 4: the delete word (built)

Problem: the delete dialog said "Type the-project-name to confirm" inside a `.field-label`, which upper-cases everything, while the check is case-sensitive. "Scrapyard Racers" was shown as SCRAPYARD RACERS and typing that did not work.

Change (components/Confirm.tsx, styles.css `.confirm-word`): the word is in its own span with `text-transform: none`, in a monospace chip, so it shows exactly as it must be typed. The label now reads "Type `Scrapyard Racers` to confirm, exactly as shown". The comparison stays case-sensitive on purpose (it is a guard against deleting the wrong project); only leading and trailing spaces are ignored, as before.

Verified in the mock preview at 1440 x 900: the name shows in its real case; typing it in lower case leaves "Delete project" disabled.

---

## Item 2: see and change what you asked for (built)

### Problem

The description lived only at the bottom of the left rail, in small muted text under the six steps; nothing on the step 1 page itself showed what you had asked for. When you started from a picture, the description was usually empty and the rail showed an empty line with "Edit", with nothing saying why. And the prompt the app actually sent (your words plus the look, the pose for 3D and other lines) was recorded but never shown.

### Design

1. **"What you asked for" card** at the top of step 1, under the step heading (pipeline/Brief.tsx `BriefCard`):
   - Your description in full, wrapping, at body size, with Edit (or Add when empty). Editing is the same component as the rail (`DescriptionEdit`): Save, then "Make a new step 1 picture from this description?" with "Make it" or "Just save the text". Ctrl+Enter saves, Esc cancels. A description can now be cleared (a picture-started asset may have none); a name still cannot.
   - When the asset was started from your own picture: a 96 px thumbnail of that picture (the see-through original when there is one), labelled "Your starting picture"; double-click or its corner button opens it full size.
   - Empty description on a picture-started asset: "No description. Adding one helps later steps name the items, hair and moving parts." (the description feeds item detection, the hair sentence and the moving-parts words in later steps).
2. **"Prompt sent to the painter"** under every painted step 1 picture (`PromptSent`, closed by default): the full prompt for that version in a selectable box, what the painter was told to avoid when it takes a negative prompt, the change you asked for on "Change something" versions, a Copy button, and one line saying your description comes first and where to change it. A picture you added shows your note with it instead, when there is one.
3. **The rail** keeps the description too, through the same component, and says "None (started from a picture)" when empty.

### State matrix

| State | What shows |
|---|---|
| First visit, started from a description | Card with the description and Edit; each picture has "Prompt sent to the painter" |
| Started from a picture, no description | Card with the thumbnail, the empty-description line and Add |
| Started from a picture with a note | Card with the thumbnail and the note as the description; that version shows "Your note with this picture" |
| Returning | Same; the card reflects the saved text after every save (both card and rail reload) |
| Editing | Text box (5 rows) with Save and Cancel; Save with no change closes it |
| Saved, asking | "Make a new step 1 picture from this description?" Make it / Just save the text; both disabled while saving |
| Loading (a picture being painted) | Card unchanged; the running version has no prompt yet, so no panel |
| Error saving | The asset page's error line (onError), editor stays open |
| Older versions made before prompt_final existed | Falls back to params.prompt; if neither exists the panel is hidden |
| "Change something" version | Panel shows "Change you asked for" and the prompt it inherited |
| Very long description | Wraps; the sent-prompt box scrolls after 14 lines |
| Narrow window | Not applicable below 1280 px (app floor); under 560 px the card stacks |
| Reduce Motion | Nothing animates |
| Screen reader | Card is a labelled section ("What you asked for"); buttons say "Add a description" / "Edit the description"; the prompt box is labelled |
| Material category | Same card; no pose line in the prompt |
| Other steps | Card shows on step 1 only; the rail shows the description on every step |

### Acceptance criteria and how each was checked

| # | Criterion | Seam | Result |
|---|---|---|---|
| P1 | Step 1 shows the full description above the versions, editable | Mock preview, Archer | Pass (screenshot in session) |
| P2 | Saving from the card updates the card and the rail; "Just save the text" makes no new picture | Mock preview | Pass |
| P3 | Each painted picture has a closed "Prompt sent to the painter" panel with the full prompt and Copy | Mock preview (mock now fabricates prompt_final like build_prompt) | Pass |
| P4 | A picture-started asset shows its starting picture and an Add prompt | Code path only | Not checked in the preview: the mock's own-picture flow needs the file picker. Check on the next real picture-started asset |
| P5 | Type check and build clean | `npm run build` | Pass |

---

## Item 1: extra LoRAs on step 2 (assessment, not built)

### How step 2 works today

Step 2 ("Check all sides") redraws your kept step 1 picture from other angles with Qwen-Image-Edit 2511 (fp8) on the 3080. Every view loads exactly two LoRAs in a chain: the Lightning 4-step LoRA (required, it is what makes a view take seconds) and one view LoRA chosen by "How to turn the views" (multiple-angles, the turnaround sheet, or the shadow-free camera LoRA). The catalog says plainly that two view LoRAs are never stacked. Your own LoRAs can only be added for the step 1 painters (Z-Image and friends): `user_addons.py` registers every own LoRA with `stage: concept`, and LoRAs made for those painters cannot load on Qwen-Image-Edit at all.

### Would it be a hard ask?

The plumbing is not hard; about a day of work:

- Let an own LoRA be registered for the Qwen-Image-Edit 2511 family (`stage: turnaround`, base model `qwen_edit`), with the same file checks as today.
- Chain each chosen LoRA after the view LoRA in graphs/qwen_edit.py (one `LoraLoaderModelOnly` per LoRA), with a strength, in the standard, sheet and shadow-free graphs and the redraw path.
- A step 2 "Add-ons" picker in Fine-tune (first build and Change something), limited to one or two extra LoRAs, plus the record in metrics.addons and the export credits, which already list every LoRA behind a view.

The hard part is the result, not the code, and it changes how step 2 behaves in the workflow:

1. **Step 2's job is to copy, not to restyle.** The 3D build reads several views together and the texture is painted from them. A look LoRA at step 2 pulls each view a little differently from the step 1 picture and from each other, so the model gets seams, mismatched colours or a different face from the front to the back. Today's "Check all sides" consistency checks would catch some of this, and would fail more often.
2. **The look belongs on step 1.** Whatever the views show is copied from the step 1 picture. If you want a look, adding the LoRA on step 1 (already supported) gives the views that look for free, with nothing fighting the angle LoRA.
3. **Stacking on a 4-step distilled model is fragile.** The Lightning LoRA gets its speed by being finely balanced; extra LoRAs on top often blur or burn detail, and the angle LoRA turns less reliably when another LoRA pulls the same weights. Expect to need lower strengths (around 0.3 to 0.6) and per-LoRA testing.
4. **Few LoRAs exist for Qwen-Image-Edit 2511.** Most style LoRAs online are for SDXL, Flux or Qwen-Image (the text-to-image model); those will not load or will do nothing on the edit model. The form would have to say which family a file is for (the header prefill already guesses this for painters).
5. **Time and memory.** Each LoRA adds a little load time per view and some memory on the 3080, which already holds a 20B model in fp8.

### Recommendation

Do it as an opt-in for the cases where it helps: a LoRA made for Qwen-Image-Edit 2511 that keeps a likeness or a costume detail steady across angles, rather than one that changes the look. Shape:

- Own LoRAs get a "Made for" choice that includes "Qwen-Image-Edit 2511 (step 2)".
- Step 2 Fine-tune shows "Extra add-ons (advanced)" with at most two, default strength subtle, and a note: "Add a look on step 1. Use this only for a LoRA made for turning views or keeping details the same."
- Try it on one view first ("Redraw this view"), then on all; the consistency checks stay as they are.

Open question for the owner: do you have a particular LoRA in mind, and is it made for Qwen-Image-Edit 2511? That decides whether this is worth a day now.

---

## Item 3: bringing your own files in at any step (design, not built)

### What exists already

| Step | You can bring in today |
|---|---|
| 0.5 From your pictures | Face, body and outfit pictures |
| 1 Describe | Your own picture ("Use my own picture"), your own painter LoRAs |
| 2 Check all sides | Your own side, back or three-quarter views ("I have my own side or back views") |
| 3 Build the 3D model | Nothing |
| 4 Clean up for the game | Your own face close-up; your own hairstyles (from the library) |
| 5 Make it move | Your own moves (animation files) |
| 6 Hand off | Nothing (it is the output) |

So the gap is a 3D model: there is no way to start from a mesh, textured or not, or from a rigged model. The pipeline is ready for it in one important way: every step reads the version you kept at the step before it, by file (`files.glb` for the mesh), and the backend already reads GLB and FBX through Blender for own hairstyles and moves with the same streamed-upload checks.

### Design

**One entry point per step, same wording everywhere: "Use my own ..."**, next to the existing "Use my own picture". Each makes an ordinary version at that step marked "Yours" (`params.source = "upload"`), which you keep or not like any other version, and the steps after it run from it as usual.

| Where | Button | Accepts | What the app does |
|---|---|---|---|
| Step 3 | Use my own 3D model | GLB, glTF, FBX, OBJ (+ textures in a zip) | Blender reads it, joins parts, applies transforms, faces it forward, bakes textures to one material if it has several, writes `model.glb`. Checks: triangle count, scale, open edges, textures found |
| Step 3 | (same file, textured or not) | | An untextured model gets the step 1 picture painted on (the existing picture painting), or stays grey if there is no picture |
| Step 5 | Use my own rigged model | FBX or GLB with an armature | Keeps the skeleton when it maps to Humanoid (Mixamo names or the Unity Humanoid mapping); otherwise offers "Rig it again" |
| Projects page | New asset from a 3D model | Same as step 3 | Makes the asset and lands on step 3 with the model in |

**The steps before an imported model.** Later steps use pictures: the face pass, the texture painting and the asset's thumbnail all read step 1 and 2. For an imported model the app renders it (front, sides, back, three-quarters) in Blender and fills steps 1 and 2 with those renders, marked "From your model". This also gives the "improve on it" path you asked about: change the step 1 render with "Change something" (for example "sharper armour, cleaner face"), and step 3 offers "Repaint the texture from the new pictures" on the same mesh instead of building a new one.

**What "improve" can mean, and which steps do it:**

- Cleaner for games: step 4 as today (triangle budget, LODs, collider, symmetry, normals).
- Better texture: repaint from improved pictures (above), or upscale the existing texture.
- A face that holds up: step 4's face pass with your face close-up.
- Moving: step 5 rig and moves, unless you brought a rig.

### State matrix (for the step 3 import)

| State | Behaviour |
|---|---|
| Empty (no versions) | "Use my own 3D model" shown next to "Build it" |
| Uploading | Progress by bytes; the file streams to disk, never held in memory |
| Reading in Blender | A job like any other, with Stop |
| Wrong format or not a mesh | Plain error: what was found and what is accepted |
| Too many triangles | Kept, with a warning; step 4 brings it to the platform budget |
| Several materials or no UVs | Baked or unwrapped, with a note on the version |
| Missing textures (OBJ/FBX with external images) | Warns "textures not found: name.png"; offers to add a zip |
| Rigged file dropped on step 3 | Uses the mesh, says the rig is set aside, offers step 5's import |
| Success | Version marked "Yours", the 3D view, checks; Keep it like any version |
| Steps 1 and 2 filled from renders | Marked "From your model"; can be replaced by your own pictures |

### Phasing and effort (estimates, to be checked once started)

1. **Phase A, about 2 to 3 days:** import a model into step 3 (Blender normalise, checks, version), renders into steps 1 and 2, New asset from a 3D model. Tests with a GLB, an FBX, an OBJ with textures and an untextured mesh.
2. **Phase B, about 2 days:** "Repaint the texture from the new pictures" on a kept mesh (reuses the picture painting), so a textured model can be improved.
3. **Phase C, about 2 days:** import a rigged model into step 5, Humanoid mapping check, keep or re-rig.

Risks: imported files vary a lot (units, axes, many materials, packed or loose textures), so phase A needs a small set of real files to test against; the face pass on an imported face may not find the face if the renders differ from what it expects.

### Phase A as built (2026-11-12)

- Step 3 "Use my own 3D model" (pipeline/OwnModel.tsx) and "A 3D model I have" on the new-asset form (pages/ProjectPage.tsx). The form makes the asset, sends the file and opens step 3; Autopilot does not start, because it would paint a step 1 picture while the model is read.
- Backend vgac/model_import.py (streamed upload, zip checks, the job) and blender/modelimport.py (read, set a skeleton aside, join, turn, keep or bake the colours on the processor with Cycles, export). API in docs/API.md.
- Measured: a TRELLIS chest GLB (one material with a picture) imports "kept" in 2 s; the Quaternius mannequin FBX (2 materials, 53-bone skeleton) imports "baked" in 5 s with the skeleton set aside; a zipped two-material OBJ bakes red and blue as expected. A whole import with renders and checks took about 10 s in the tests.
- "Which side is the front": files from other programs face different ways (the Unity FBX faced backwards). Step 3 shows "Left side / Back / Right side is the front" under an imported model; each makes a new version from the same file, turned (checked on the mannequin with Back and the chest with Left side).
- Running text says "Reading your 3D model", about a minute, not a build's minutes.
- Fine-tune or a described change on an imported model builds a new model from the step 1 and 2 pictures (the import's own settings are not carried over).
- Tests: backend/tests/test_model_import.py (21: file checks, real Blender through the API and queue, step 4 on an imported model, turn, refusals, material).
- Not done in phase A: keeping a skeleton (phase C), repainting from changed pictures (phase B), a licence line for imported models (open question 3), reading a model on the step 3 page of the live app (the backend must be restarted first).

### Open questions (owner)

1. Which model do you want to improve first, and in what format? A real file makes phase A much safer.
2. When you import a textured model, should the app keep your texture untouched unless you ask, or repaint by default? (Proposed: keep it.)
3. Should licences be asked for on import, like own hairstyles and moves, so they travel into the export credits? (Proposed: yes, same "Where it came from" line.)

---

## Files changed

- frontend/src/components/Confirm.tsx, frontend/src/styles.css (`.confirm-word`): item 4.
- frontend/src/pipeline/Brief.tsx (new: DescriptionEdit, BriefCard, PromptSent), frontend/src/pipeline/StagePanel.tsx (card on step 1), frontend/src/pipeline/views/ConceptView.tsx (prompt panel), frontend/src/pages/AssetPage.tsx (rail uses DescriptionEdit; old RailEdit removed), frontend/src/styles.css (`.brief-*`, `.prompt-*`): item 2.
- frontend/src/mock/data.ts: mock concept versions carry prompt_final, so the panel shows in the demo.
