# Plan: "Hair from the library" (opt-in, per character)

Decided by the owner on 2026-11-05 after two trials (runtime/tmp/wigtrial, runtime/tmp/hairlib; see STATUS.md and docs/HAIR-LIBRARY-RESEARCH.md). Today's hair stays the default and must be completely unchanged when the option is off. When on, the 3D model is built bald, the owner sees the best-fitting ready-made hairstyles on their own character and picks one, and the app fits and recolours it. Autopilot still runs end to end: it takes the best-scoring style and says so; under 0.6 it warns and offers a way back to today's hair.

Module names are short for `backend/vgac/...`. This file fixes the interfaces between four lanes; a lane that needs to change an interface says so to the coordinator, it does not change it alone.

## Key decisions

| Decision | Choice | Reason |
|---|---|---|
| Where the choice lives | Asset setting (new column `Asset.hair_json`), copied into stage params when a step runs | The choice spans steps 2 to 6 and the owner changes it in one place |
| Who follows what | Upstream steps (turnaround, mesh) read the asset setting. Downstream steps (prep, rig, export) follow the lineage: the mesh's `hair_source` | A mixed chain cannot happen |
| Where the bald pictures are made | Inside the Check all sides step, on engine B, after the normal views | The image card is already in use there; same edit graph as the trial; Stop already works there |
| Where candidates are ranked | End of the Build the 3D model step (Blender on the host, inside the 3D job) | The pick has to exist before Clean up, which splits the triangle budget and attaches the hair |
| Where the hair is attached | End of Clean up, after face pass and skin metal, on the final body | The body's size and facing are final there |
| Prep `glb` | Stays the bald body; the hair is separate files | SkinToken, rig_fix, hit boxes, face pass and every other reader keep their one-mesh contract |
| When the hair joins the skeleton | Rig step, after the moves and before spring bones (new `blender/hairbind.py`) | Hit boxes, procanim and retarget never see the hair |
| How the hair travels to export | `blender/pack.py` joins it as material slot 1 `<name>_Hair` | Same slot layout as today's hair material, so FBX, GLB, Unity and LODs keep one mesh per LOD |
| Items system as carrier | Rejected | Items are rigid on a socket (no skin weights, no spring chains), exist only in the equipped prefab, and each is its own asset |

## 1. The choice: name, timing, storage

Wording (owner's rule: real term first, plain helper once in brackets):
- Setting: "Hair from the library (ready-made hairstyles)". Options: "Built with the 3D model" (default, today) and "From the library".
- Glossary term `term.hair_library`: "Hair library (ready-made hairstyles fitted to the head)".

When it must be made: before Check all sides, so the bald pictures are drawn with the views.
- Asset form: a radio line under the hair notes, shown only when hairstyles can be offered (availability below).
- Asset page: a "Hair" line with Change.
- Turning it on later: the API queues a turnaround adjust that keeps every view and draws only the bald pictures, then says "Rebuild from the 3D model".

Storage:
- `db.py`: `Asset.hair_json` TEXT, added to `_ADDED_COLUMNS["asset"]`, with a property `hair` and `"hair": a.hair` in `asset_to_dict`.
- Shape: `{"source": "library"|"model", "pick": "<style id>"|null, "picked_at": iso, "changed_by": "form"|"page"|"fix"}`. Absent means today's hair.
- `main.AssetIn.hair: Literal["model","library"] | None`. Variants copy `hair_json` from the base asset.

Recorded per version (internal params; none go in `reference.STAGE_SCHEMAS`):
- Turnaround: `bald_views` (bool), `bald_seed` (int), `bald_only` (adjust marker).
- Mesh: `hair_source` (`"model"|"library"`).
- Prep: `hair_source`, `hair_style`, `hair_style_source` (`picked|best|chosen`), `hair_allowance`, `hair_cover` (`normal|more`).
- `jobs.RESUME_SEEDS` gains `"bald_seed"`.

Resume, re-runs and Autopilot:
- Resume: same version and params, so the same seed, style and allowance. Adjust inherits the parent's params.
- Run again: upstream reads the asset setting; downstream follows the mesh lineage.
- Autopilot gets no new pause. The turnaround draws bald pictures when the setting is on, the mesh ranks, prep takes the pick or the best.
- An explicit param wins over the asset setting. When a Fix sends `hair_source: "model"`, the mesh step writes the asset setting back to "model" (`hairlib.set_choice(..., changed_by="fix")`).
- Not offered with Hunyuan3D: the mesh step forces `hair_source: "model"` and notes it.

Availability, `hairlib.available(asset, look)` returns a plain reason when the library cannot be used: humanoid only; not Pixel, Retro 64 or Low-poly looks; not when the description says bald, helmet or hood (reuse the word lists in `hairpic.py`); on `mobile_low` only shell styles at or under 750 triangles (cards are never offered on Phone).

## 2. The bald pictures

What gets edited: the concept image (the front picture the trial edited cleanly 5 of 5 times), with the trial's `PROMPTS["bald"]` (runtime/tmp/wigtrial/edit.py) plus "Keep any beard or moustache exactly as it is." Then bald quarter, left and right are drawn from the bald front with `angle_graph`, each with the seed its haired view used. Quarter is the 3D input; left and right are for the face pass. No bald back.

Where it runs: `stages/turnaround.py` on engine B. New `_run_bald(sc, source, params)` runs after `_run_views` when `bald_views` is true; it uses `edit_graph`, `sc.comfy`, `sc.cutout` and `_generate_view` with the uploaded bald front. `bald_seed` is written at once (the `picked_seed` pattern) so Resume draws the same picture. One automatic redraw with `bald_seed + 1` when the check fails; the better one is kept. Bald metrics live in `metrics.bald`, never in `metrics.views`. `refine` and `upload` copy every `bald_*` file forward. `adjust` with `bald_only` copies all views and runs only `_run_bald`.

Files in the turnaround version:
- `bald_front`, `bald_front_cutout`, `bald_front_white`; the same three for quarter, left and right.
- `hair_outline_front`: an L-mode PNG at concept size.
- `hair_picture`: JSON with `target` (the `fitprep.py` measures `H_px, top_px, neck_px, head_cx_px, head_w, head_h`), `eye_boxes`, `pic_eye_cover`, `colour {dark, median, light}` in sRGB, and `outline_px`.
- `bald_sheet`: haired and bald pictures side by side.

Picture maths: new `vgac/baldpic.py` (a port of the trial's `fitprep.py` and `prep_pic.py`, numpy and PIL only):
- `outline_mask(orig_rgba, bald_rgba) -> mask`
- `hair_colour(orig, mask) -> {dark, median, light}`
- `eye_boxes(bald, target)`
- `kept_measure(orig, bald, mask) -> {kept_share, face_delta}`

Checks that compare the model with the picture, when the model is bald on purpose:
- Silhouette: `mesh.audit` uses `bald_front_cutout`, `bald_left_cutout` and `bald_right_cutout` and drops back, with vars `{"bald": true}`.
- `texture_matches_picture`: `mesh._input_image` returns `bald_<view>_cutout` (concept maps to `bald_front_cutout`), so the check and `prep.picture_colour` compare against the picture the model was built from. A missing bald file raises a plain error.
- Face checks: unchanged; they still compare against the original concept.
- `hair_matches_picture` and `hair_material`: not emitted.
- New check `bald_picture` (section 7).

## 3. Existing passes when library hair is on

- Face pass (`stages/face_detail.py`): still paints from the original concept cut-out. One edit: `side_pictures(sc)` reads `bald_{quarter,left,right}_cutout` when the lineage is library; if they are missing it returns `{}` (no side painting), so no haired side picture ever reaches the skull.
- Skin metal: unchanged.
- Hair pass and fused hair material: one edit in `stages/hair_detail.py`: `skip_reason` returns `"library"` when `params["hair_source"] == "library"`, and `"library"` joins `NOT_APPLICABLE`. `hair_checks` returns `[]`; `blender/hairmat.py` is never called.
- Reused from `vgac/hairmat.py`: `hair_texture_size(budget)`, `flow_map`, `gl_flow`, `glb_add_anisotropy`, `readme_lines`, `ANISOTROPY_STRENGTH`. The VGAC/Hair shader stays unchanged and is used for opaque shells. New `unity-sandbox/Assets/VgacShaders/VgacHairCutout.shader` ("VGAC/HairCutout"): same two-band lighting, `alphatest:_Cutoff`, `Cull [_Cull]`, VFACE normal flip, `addshadow`, AlphaTest queue; used for cards and for shells marked two-sided. A new file, so today's hair renders identically.
- Head triangle weighting: unchanged rule.
- Triangle budget: the body is decimated to `budget - hair_allowance`. `hair_allowance` is the largest triangle count (hair plus cap) among the candidates on the lineage mesh and the chosen style, rounded up to 250, at most `HAIR_SHARE_MAX = 0.25` of the budget; styles over that are not offered. The `triangle_limit` metric becomes the body limit, so `triangle_budget` measures the body; the new `hair_triangles` check measures body plus hair against the platform budget.
- Texture budget: the hair gets its own map at `min(library texture size, hairmat.hair_texture_size(budget))`. File keys `hair_base_color` (RGBA for cards), `hair_normal`, `hair_roughness` (constant 0.55), `hair_flow`; no `hair_ao` in v1. These are the keys export already handles.

## 4. The library as app data

Location: `backend/vgac/data/hairlib/`, committed. Cap 40 MB, enforced by the build script and by a test.

| Path | What it holds |
|---|---|
| `catalogue.json` | One entry per style |
| `curation.yaml` | Hand-kept choices: which styles, family, fringe, parting, hang-group overrides, thinning target, two_sided, notes |
| `styles/<id>.npz` | The style's mesh in the reference skull frame |
| `tex/<sha8>_grey.png` | Grey texture: LA PNG at 1024, median hair luminance normalised to 0.5, alpha kept for cards; shared textures stored once |
| `tex/<sha8>_normal.png` | Normal map at 512; shells only |
| `thumbs/<id>_front.png`, `thumbs/<id>_quarter.png` | 256 px renders on a neutral grey reference head |
| `CREDITS.txt` | Source, author and CC0 statement per style |

Arrays in `styles/<id>.npz`:
- `q` (N,3) float32: vertices in the normalised reference skull frame (the trial's `to_local(S_r, V)`, front -y, Z up).
- `rho_r` (N,) float32: the reference scalp radius sampled under each vertex (all the trial's `Placer` needed from the reference head, so no reference head is needed at run time).
- `tris` (T,3) int32 and `uv` (T,3,2) float32 corner UVs.
- `role` (N,) int8: 0 rests on the head, 2+k belongs to hang group k. The cap is made at run time (role 1).
- `flow` (N,3) float32: strand direction in the skull frame.

Catalogue entry: `id`, `name`, `pack`, `source_url`, `author`, `licence: "CC0-1.0"`, `family` (bob, short, long, ponytail, bun, braid, afro), `fringe`, `parting` (`left|right|centre|none`), `length_rel_head`, `kind: shell|cards`, `alpha: opaque|cutout`, `two_sided`, `triangles`, `vertices`, `hang_groups` (up to 3 entries `{k, kind: "Hair", path_q: [[x,y,z],...], length_q}`), `textures: {grey, normal|null, size}`, `phone_ok`, `lite` (an alternative npz of at most 750 triangles, shells only), `default_params {volume, dz_head, length, mirror}`, `thumbs`, `sha256` per file.

Curated set, about 25 styles:
- Quaternius shells: Buns, Buzzed, BuzzedFemale, Long, SimpleParted.
- MakeHuman, the usable rows of runtime/tmp/hairlib/r2/families.json: cortu_short_messy, cortu_straight_bangs, cortu_shaggy, cortu_strawberry, culturalibre_01, faydaen_1, littleright_bobcut, the toigo bobs (their texture shared once at 1024), rehmanpolanski bun, afro01, bob02, long01, ponytail01, short02, short03.
- Thinned: braid01 to about 3,000 triangles, elvs_french_braid_variation to about 3,500.
- The round-one generated waist-length long hair and high ponytail.
- Beards, the misplaced styles and heavy card styles are excluded.
- Correction found while building (2026-11-05): several MakeHuman styles carry AGPL3 or CC-BY in their own .mhclo header although the pack page says CC0 (culturalibre_hair_01, littleright_bobcut_hair, rehmanpolanski_hair_bun_brown, the elvs braids, learning_anime_hair, both sonntag78 styles, o4saken_long01). The builder refuses all of them; the rule is the style's own file, not the pack page. To fill the gaps the library adds a rule-built bun and a rule-built long hair with a centre parting. Braid, like afro, may have a single style.

Offline builder (venv; drives Blender; reads runtime/hairpacks read-only; runs no pack scripts): `tools/hairlib_build.py`, `tools/hairlib_blender.py` (the round-two `lib_prep.py` logic plus baking `q` and `rho_r`, thinning, `lite` variants, clustering loose hanging pieces into up to 3 paths, thumbnails), `tools/hairlib_rulebuilt.py` (a port of `hairgen.py`'s long and ponytail styles, because runtime/ is not in git). The builder refuses any style whose `.mhclo` or pack page licence is not CC0.

Loader: new `vgac/hairstyles.py` (`lru_cache`; a data change needs a backend restart): `catalogue()`, `style(id)`, `styles_for(family, platform, look) -> list`, `family_of(prompt) -> (family|None, words)` (word lists from `hairpic.py`), `paths(id)`, `credit(id)`.

UI pictures: `GET /api/hair-library` and `GET /api/hair-library/files/{path}` (path contained to `data/hairlib`).

## 5. Candidates and the pick

When and where: `stages/mesh.py audit()` calls `hairfit.rank(sc, ...)` just before `judge(sc)` when `params.hair_source == "library"`. Blender runs on the host inside the 3D job. Time cap 150 s. With a family: the trial's full search, about 10 s per style. With no family words: a coarse 24-point grid over every offered style, about 3 s each, with the note "the description does not name the hairstyle". The model is turned by `quality.auto_turn` of the audit's best turn and normalised to 1.7 m. The picture alignment search uses `bald_front_cutout`. The 4 best are built, recoloured and rendered front and three-quarter. It never fails the job; errors go to `metrics.hair_library.error`.

Mesh step files and metrics:
- Files: `hair_candidates` (JSON) and `hair_cand_1` to `hair_cand_4` (PNG).
- `metrics.hair_library = {family, family_source: "description"|"none", candidates: [{style, name, score, iou, eye_penalty, params, triangles, kind}], best, best_score, nothing_fits, seconds, error}`.

Where the owner picks: a new `HairPicker` component in the Build the 3D model view (`pipeline/views/MeshView.tsx` via `StagePanel`). Cards show the character with each style, plain fit words, triangle count, kind and credit, and a "Use this hairstyle" button. "Show more hairstyles" lists catalogue thumbnails for the family, then all families (not fitted until Clean up).

API (new router in `vgac/hairlib.py`, included in `main.py` like `items.router`):
- `GET /api/assets/{aid}/hair` returns `{source, available, unavailable_reason, family, family_words, pick, candidates:[{style,name,score,fit_words,picture_url,triangles,kind,credit}], mesh_version_id, best, nothing_fits, applied:{style,score,prep_version_id}|null, rebuild:{from_stage,minutes}|null, sentence}`.
- `PUT /api/assets/{aid}/hair {source}` returns `{panel, started:[{job,version}], rebuild_from}`. Turning it on queues the `bald_only` turnaround adjust when the kept turnaround has no bald pictures. Neither direction auto-rejects anything; the UI offers "Rebuild from the 3D model" through the existing autopilot `from_stage`.
- `POST /api/assets/{aid}/hair/pick {style, rebuild: bool}` stores the pick. With `rebuild` and a kept library prep using another style, it calls `autopilot.start(aid, fix={"stage":"prep","params":{"hair_style":style}})`. Returns `{panel, autopilot}`.

How prep resolves the style: explicit `params.hair_style`, then `asset.hair.pick`, then the lineage mesh's `best`. Fit numbers come from the mesh candidates when the style is among them; otherwise prep runs a single-style search (about 10 s).

What a pick change re-runs:
- Prep adjust where only `hair_style` or `hair_cover` changed and the new style fits the parent's allowance (`hairlib.only_hair_change`): copy the parent's body files, attach only (20 to 40 s).
- Rig: the donor path in section 6 (60 to 90 s, no SkinToken).
- Export: in full (about 70 s).
- A heavier style than the allowance: full Clean up.

## 6. Attaching the hair and carrying it through

Prep (Clean up): `hairfit.attach(...)` runs after skin metal and before `judge`. It fits skull to skull, applies the radial correction and volume, dz, length and mirror, pushes out by 1.5 mm, adds a hair-coloured scalp cap at 2.5 mm, measures skin showing through (crown and sides), recolours (the round-two `texture2.py` method through the picture's dark, median and light tones), writes the flow texture via `hairmat.flow_map`, and scales back to prep model space. It calls `sc.use_model("hair_library")` (new `data/models.yaml` entry, CC0).
- Files: `hair_mesh` (the canonical npz: `verts` in glTF Y-up metres, `tris`, `uv`, `role` with 1 for the cap, `flow`), `hair_parts` (JSON `{style, groups:[{k, kind, path, length_m}], cap_first, extra_height_m}`), `hair_glb`, `with_hair_glb` (body plus hair, for the viewer), `hair_base_color`, `hair_normal`, `hair_roughness`, `hair_flow`, `hair_fit_sheet`. The npz is canonical because glTF export reorders vertices.
- Metrics: `hair_library = {attached, style, family, score, params, triangles, cap_triangles, allowance, skin_through, pushed_out, kind, alpha, two_sided, tex, colour_delta, eye_cover, seconds, credit, reused_body_of}`; `body_glb_hash` (sha1 of `prep.glb`, library mode only).
- Size: `size_m` refers to the bald body; the sentence adds "about N cm more with the hair".

Rig (Make it move): in `_animate`, between `_retarget` and `_springs`, insert `await hair_bind(sc, anim_glb)`. It first copies `anim.glb` to the `anim_body` version file, then `blender/hairbind.py` builds the `VGAC_Hair` object from `hair_mesh.npz`, with material `VGAC_Hair`, parented to the armature.
- Weights: above the neck joint Head = 1.0; below it a blend toward the body's nearest weights (Data Transfer limited to Neck, Spine2, Spine1 and the shoulders) over 10 cm; hang-group vertices Head (spring chains take over).
- `springs.py` and `blender/springs.py` gain `--hair-object VGAC_Hair --hair-mesh hair_mesh.npz --hair-parts hair_parts.json`. The hair object is left out of hanging-part detection; each group becomes a chain of 2 to 4 bones along its path, parented to Head; group vertices are found by matching positions to the npz. New reason `library_rests`: "the hairstyle rests on the head, so it moves with the head".
- Donor reuse in `_rig`: when a done rig of this asset was built on a prep with the same `body_glb_hash` and the same template and group-per-vertex settings, copy its `rig.glb`, rig_fix, rig_info and hit boxes, skip SkinToken, start from its `anim_body.glb`, then bind, springs and strip.
- Metrics: `hair_bind {ok, verts, rest_share, nape_blend_verts, groups, seconds}`.

Hand-off, `blender/pack.py`: new `--hair-object VGAC_Hair --hair-mesh <npz> [--hair-alpha cutout] [--hair-two-sided]`. Before the join, set the hair material to slot `<name>_Hair` and keep slot order body 0, hair 1. LODs: shells decimate with the body; cards are protected at LOD1 and from LOD2 every non-cap hair face is deleted, leaving the cap. Measured on body faces only: manifold, watertight, `dims_m`, LOD ratio counts (new `lods_body`) and socket placement. `report.hair_material` gains `{source, faces, lod_triangles, slots}`. GLB hair material: `alphaMode MASK`, `alphaCutoff 0.5`, `doubleSided` from the catalogue flag.

Hand-off, `stages/export.py`: `hair_inputs` takes the library branch when `prep.metrics.hair_library.attached` (no layout, same `HAIR_KEYS`). Unity args: `-vgacHair on`, plus `-vgacHairCutout on` and `-vgacHairTwoSided on`. `sync_shader` also copies `VgacHairCutout.shader`. `_hair_manifest` adds `{source: "library", style: {id, name, author, licence, source_url}, alpha, two_sided, hanging_groups}`; README gets credit lines. Items' `blender/equip.py` and `blender/itemfit.py` leave out faces in the `_Hair` slot from the body tree.

Unity, `VgacImport.cs` Version 17: the cutout and two-sided flags build the material on VGAC/HairCutout with `_Cutoff 0.5`, `_Cull 0`. Hair texture import: alpha from input, `alphaIsTransparency`, `mipMapsPreserveCoverage` with `alphaTestReferenceValue 0.5`. Report fields `hairCutout` and `hairTwoSided`. URP and HDRP fall back to Lit with alpha clip, not verified. `mobile_low`: shells only.

## 7. Checks and wording

All new checks are `warn`.

| Check | Step | value / limit | vars | Fix |
|---|---|---|---|---|
| `bald_picture` | turnaround | `kept_share` / 0.9 | `face_delta, rerolled, reason: face_changed\|clothes_changed\|no_hair_removed\|error` | `{"stage":"turnaround","params":{"bald_only":true,"bald_seed":null}}`, "Make the bald picture again" |
| `hair_fit` | mesh (best) and prep (used style) | score / 0.6; under 0.6 means "nothing fits well" | `style_name, family, family_source, picked, best_name, candidates, step, eye_cover, colour_delta` | `{"stage":"mesh","params":{"hair_source":"model"}}`, "Use the hair built with the 3D model" |
| `hair_skin_through` | prep | bare share / 0.02 | `crown, sides` | `{"stage":"prep","params":{"hair_cover":"more"}}`, "Fit the hair again with more cover" |
| `hair_triangles` | prep | body + hair / budget | `hair, body, allowance` | `{"stage":"mesh","ui":"hair_picker","params":{}}`, "Choose a lighter hairstyle" |
| `hair_bound` | rig | share of hair vertices following the skeleton / 1.0 | `groups_swaying, rest_on_head` | `{"stage":"rig","params":{}}`, "Make the moves again" |

- Export reuses `hair_material_shipped` (with vars `source`).
- Stages build checks only through `quality.check(name, ok, value, limit, vars=...)`.
- Every sentence lives in the shared wording files, which the UI lane alone edits, by exact insertion:
  - `guidance.py` `CHECK_GUIDE`: the five entries; sentences use "Hair from the library", "Bald picture (the character without hair, used to build the 3D model)", "Scalp cap (a hair-coloured layer under the hair)".
  - `backend/tests/test_great_assets_laneb.py` `SAMPLES`: `bald_picture ((0.97, 0.9), (0.7, 0.9))`, `hair_fit ((0.77, 0.6), (0.55, 0.6))`, `hair_skin_through ((0.0, 0.02), (0.06, 0.02))`, `hair_triangles ((19800, 20000), (21500, 20000))`, `hair_bound ((1.0, 1.0), (0.8, 1.0))`.
  - `frontend/src/help.ts`: terms `term.hair_library`, `term.bald_picture`, `term.alpha_cutout`; `CHECK_TEXT` fallbacks, the check-to-step map, fix labels, fix actions. `term.hair_cards`: "This app does not make them yet" becomes "Some styles in the hair library are made of them".
  - `GlossaryPage.tsx`: the new items in the "Hair and fur" group.
  - `hairNotes.ts` and `hairpic.py` (kept equal by `test_hair_pictures`): the long_loose note gains "Hair from the library (ready-made hairstyles) can hang free and swing."
  - `springs.py` `REASONS`: `library_rests` (owned by the rig lane).

## 8. Lanes

Nobody commits; the coordinator reviews, runs the whole suite, commits and restarts. No lane restarts the backend, edits the database directly, or starts builds in the app.

### Lane 1: Library and fit engine (offline, pure)

Owns: `tools/hairlib_build.py`, `tools/hairlib_blender.py`, `tools/hairlib_rulebuilt.py`, `tools/hairlib_check.py`, `backend/vgac/data/hairlib/**`, `backend/vgac/hairstyles.py`, `backend/vgac/baldpic.py`, `backend/vgac/hairfit.py`, `backend/vgac/blender/hairfit.py` (modes `rank` and `attach`), one `hair_library` line in `backend/vgac/data/models.yaml`, `backend/tests/test_hairstyles.py`, `backend/tests/test_hairfit.py`, `backend/tests/data/hairlib_fixture/`.

Provides:
- `async def rank(sc: StageContext, glb: Path, picture_json: Path, bald_cutout: Path, outline: Path, family: str|None, yaw: int, out_dir: Path) -> dict` returns the `metrics.hair_library` block and writes the files.
- `async def attach(sc, body_glb: Path, style_id: str, params: dict|None, picture_json: Path, bald_cutout: Path, outline: Path, cover: str, tex: int, out_dir: Path) -> {files: {key: Path}, metrics: {...}}`.
- `def check_values(m) -> dict` for `hair_fit`, `hair_skin_through` and `hair_triangles`.
- The `baldpic` functions.
- First of all: a fixture library of two styles (one shell, one card set) and a trial-derived `hair_mesh.npz` with `hair_parts.json` in `backend/tests/data/hairlib_fixture/`, so lanes 2 and 3 can code against real files.

Must prove offline: catalogue valid; every style CC0 with a credit; total at most 40 MB; every family except afro has at least 2 styles; `family_of` matches a word table; a style placed on its own reference skull reproduces its positions within 1 mm; recolour Lab hue within 8 of the picture's; `baldpic` on the five trial pictures (committed 256 px fixtures) gives a mask IoU of at least 0.8 against the trial masks; `tools/hairlib_check.py` (rank and attach on the five saved wig-trial bald builds) reproduces the round-two choices and scores within 0.02 with no skin through.

### Lane 2: Pipeline (choice, pictures, mesh, prep, API)

Owns: `backend/vgac/hairlib.py` (choice, availability, resolution, router, `only_hair_change`, `set_choice`); exact insertions in `db.py`, `main.py` (`AssetIn.hair`, router include, variant copy) and `jobs.py` (`RESUME_SEEDS`); `stages/turnaround.py`; `stages/mesh.py` (`_input_image`, audit refs, `rank` call, `hair_fit` in `judge`); `stages/prep.py` (allowance, attach call, reuse path, `body_glb_hash`, checks); one-function edits in `stages/face_detail.py` (`side_pictures`) and `stages/hair_detail.py` (skip reason); a `docs/API.md` section; `backend/tests/test_hair_library_pipeline.py`.

Must prove (stubbed engines, the existing mocked-autopilot pattern): the column migration works; OFF IS UNCHANGED (for an asset with no hair setting, the param, file, metric and check keys of all six steps equal today's); on: the bald files exist and the mesh uses `bald_quarter_cutout`; prep resolves pick, then best, then explicit; the hair-only adjust copies the body and leaves its hash identical; `bald_seed` survives Stop and Resume; Autopilot runs end to end with library on and a score under 0.6 (warn, no pause); the Fix flips the asset setting back to model.

### Lane 3: Rig, hand-off, Unity

Owns: `backend/vgac/blender/hairbind.py`; `stages/rig.py` (`hair_bind`, the donor path, `hair_bound`); `springs.py`; `blender/springs.py`; `blender/pack.py`; `stages/export.py`; `blender/equip.py` and `blender/itemfit.py` (leave out the hair slot); `unity-sandbox/Assets/Editor/VgacImport.cs`; `unity-sandbox/Assets/VgacShaders/VgacHairCutout.shader` and its `.meta`; `tools/hair_rig_check.py`; `backend/tests/test_hair_bind.py`.

Must prove: numpy parts (nape blend weights, npz position matching, chain path sampling, pack and importer args, the manifest block); an offline host run (`tools/hair_rig_check.py`) on a saved bald asset with the lane 1 attach: rig to export to sandbox batch import. The prefab must have the hair sub-mesh, VGAC/HairCutout for cards, a ponytail chain swaying in the strip, body-only manifold, size and sockets unchanged, and today's export check count reached.

### Lane 4: UI and wording

Owns: new `frontend/src/pipeline/HairPicker.tsx` and `frontend/src/pipeline/HairSetting.tsx`; insertions in `frontend/src/api.ts`, `pages/ProjectPage.tsx`, `pages/AssetPage.tsx`, `pipeline/views/MeshView.tsx` (picker; `with_hair_glb` before `glb`), `pipeline/views/TurnaroundView.tsx` (bald row), `pipeline/StagePanel.tsx`, `pipeline/finish.ts`, `pipeline/openTool.ts` (`ui: "hair_picker"`), `frontend/src/mock/*`; all shared wording files in section 7.

Must prove: `npm run build` is clean; the mock-mode walkthrough covers form, page, picker, change and checks; `SAMPLES` and the guide tests pass.

Needs a backend restart (`backend/restart.ps1`, coordinator only): every backend change, including the database migration and the library data cache. Unity Version 17 is picked up on the next import.

Live acceptance run (coordinator; project "Hair library check"; Painted unless stated; pc_mid):
1. Control knight with a bob, option off, autopilot: check and param lists identical to the last fresh-knight chain.
2. Ponytail woman (Cel, "dark brown hair in a long high ponytail"), option on. Autopilot until mesh, Stop during the bald edit, Resume: same `bald_seed`. Pick the high ponytail, then continue. Expect: `bald_picture` passes, `hair_fit` at least 0.6, `springs` at least 1 Hair chain, Unity hair sub-mesh present, ponytail trailing in the walk strip.
3. Long-haired mage, option on. Hair past the shoulders and not through the back in walk and run. If it scores under 0.6, press "Use the hair built with the 3D model" once and confirm today's chain comes back.
4. Knight with a bob, option on, autopilot with no pause, then change the pick: only prep-hair (40 s or less), rig without SkinToken (90 s or less) and export re-run.

## 9. Risks, most likely first

1. The bald edit changes the face or clothes. The face is painted from the original anyway. `bald_picture` measures `kept_share` and `face_delta` with one automatic redraw; if it still fails: warn, and offer the same picture again or today's hair.
2. Fused-hair assumptions hidden in other modules. Found so far: hair pass and hair material (handled by the skip); springs `none_found` wording; pack manifold, dims, LOD ratios and sockets reading every vertex; export `hair_inputs` requiring a layout; equip and itemfit body tree; size sentence; Hunyuan3D; procanim bounding box and hit boxes (avoided by binding after the moves). Mitigation: one constant `HAIR_OBJECT = "VGAC_Hair"`, and the lane 3 offline full chain. If something else bites, exclude by material slot `_Hair`.
3. Hair pixels from side views painted on the skull. Bald side pictures only, else no side painting. Lane 2 test: hair-coloured texel share above the hairline after the face pass, on a bald knight.
4. Card hair stringy, vanishing at distance or crawling in Unity. Cutout with preserve-coverage mips and two-sided. If bad: rank shells first on ties and mark card styles `look_ok` in curation.
5. Bald build keeps a hair bump or dark scalp, so the hair floats. Skull fit residual and a top-gap measure feed `hair_fit` vars; the cap and push-out cover small gaps.
6. Wrong family or no hair words. Coarse ranking over all styles and an honest "pick by eye" sentence.
7. Recolour reads grey (the trial's ponytail). `colour_delta` measured, dark-tone weighting raised; under 12 Lab is the aim.
8. Body triangles cut by the allowance. Capped at 25 percent; shells preferred on ties.
9. Card A held idle during ranking. Capped at 150 s.
10. Stop during ranking makes Resume rebuild TRELLIS. Accepted in v1, said in the job line.
11. Spring chains on many loose cards. Clustered into 3 groups at most offline; if one sways badly, set `hang_groups: []` in curation and rely on the nape blend.
12. Repo size. Enforced at 40 MB.

## 10. Left out of v1

More rule-built styles beyond the two generated ones; a vision-model attribute filter; side-view matching; shape-key sliders (length, volume, fringe); a user hair-colour picker; hats and helmets with the library; beards, brows and creature fur; card styles on Phone; URP and HDRP cutout, Unreal and Godot handling (README lines only); Autopilot pausing for the pick; a 3D preview per candidate; Hunyuan3D builds; bald versions of the person's own side views; item against hair clearance; the hair pass painting on library hair; HairDeck; the paid 20-style Quaternius Source.

## Not determined from the code when this was written

- Whether `edit_graph`'s 1024 output always matches the concept size (the plan resizes, as `fitprep.py` did).
- Each MakeHuman style's own licence line (pack pages say CC0; the builder checks per file).
- Whether the VGAC/Hair shader reads vertex colours (the npz route avoids needing them).
- Whether export's movement shape or colliders read the joined mesh's bounds (lane 3 checks it).
- How the queue's Stop treats a host-side Blender ranking inside the 3D job (assumed to cancel like the audit).
- Real in-app timings for ranking (trial figures only).
