# Recipes bake-off: add-ons and per-style settings for concept art and turnaround

Date: 2026-11-04. Scope: free, commercially usable add-ons (mostly LoRAs) for the concept models (Z-Image Turbo, Z-Image base, FLUX.2 Klein 4B, Anima, Chroma1-HD) and the turnaround model (Qwen-Image-Edit-2511 + Lightning 4 step + fal multiple-angles LoRA). It also checks, with no add-on, whether each style preset in `backend/vgac/data/styles.yaml` delivers its look.

Scripts: `bakeoff/recipes/` (`rlib.py`, `concepts.py`, `styles_check.py`, `posefix.py`, `turn_ab.py`, `photoreal.py`, `summarise.py`, `queue_a.sh`, `queue_b.sh`, `queue_c.sh`). Set `RECIPE_INSTANCE=a` to pin jobs to the RTX 3060. Images: `bakeoff/out/recipes/` (gitignored). No file under backend/, frontend/, desktop/ or unity-sandbox/ was changed.

## 1. Summary

Verdicts (section 4 has the numbers):

| Add-on (file in `models/loras/VGAC/`) | Verdict | Strength |
|---|---|---|
| `zit_3drender_redmond.safetensors` | Optional (new "stylised 3D" look), not default | 0.7 |
| `zit_clay_redmond.safetensors` | Optional for clay; the prompt alone does most of it | 0.6 to 0.7 |
| `zit_ps1_redmond.safetensors` | Optional for retro64 (best retro look found) | 0.6 |
| `zit_pixelart_redmond.safetensors` | Optional "chunky sprite" variant for creatures and props; keep the current pixel LoRA as default | 1.0 |
| `klein4b_pixelart_limbicnation.safetensors` | Reject | - |
| `qie2511_turnaround_sheet_tarn59.safetensors` (turnaround) | Optional, humanoids only; recommended for Photoreal humanoids | 1.0 |
| `qie2511_camera_angle_berkerdooo.safetensors` (turnaround) | Optional, rigid props only (clean white, shadowless views; worse colour identity) | 1.0 |
| fal multiple-angles (in use) | Keep as the default | 0.9 |
| `qie2511_anypose_base` + `_helper` | Not tested (section 9) | card: 0.7 + 0.7 |

Top three recipe changes:
1. **Clay style: switch the model from Klein to Z-Image Turbo, and put "Clay animation, Clay." (the tested words) as the first words of the block.** Klein ignored the A-pose in all 6 clay knight renders (three prompt variants): the arm-gap measure was 0.00 to 0.02 against 0.60 to 0.85 on Z-Image Turbo. Z-Image Turbo clay looked as clay-like with no LoRA. Never write "puppet" or "figure" in the block: it added a second clay figure next to the chest in 2 of 2 seeds.
2. **Humanoid pose block: say the arm angle and the headroom explicitly** (text in section 6.2). On Klein it turned arms-at-the-sides knights into a clear A-pose in 4 of 4 seeds and removed most of the floor shadow. On Z-Image Turbo it was neutral (no regressions).
3. **Retro64 and lowpoly_flat: rewrite the blocks so the medium is a physical thing, and offer the PS1 LoRA at 0.6 for retro64.** The current retro64 block gives cube-headed voxel figures, the "Minecraft" drift the skill warns about. The current lowpoly_flat block gives smooth surfaces on everything except the head.

**Photoreal** (section 7): keep Z-Image base with the current realistic_pbr block and recipe negative. It gave clean, crisp, evenly lit photo concepts with no depth-of-field blur. Changes:
- add the new humanoid pose block, because Z-Image base people otherwise keep their arms down;
- add light-equipment words to the negative: wording that names "light boxes" draws softboxes into the frame;
- no concept add-on for this look;
- the tarn59 sheet is a recommended option for Photoreal humanoid turnarounds;
- Klein 4B is the faster, most evenly lit alternative, with less surface detail.

The human caveat text is in section 7.

## 2. Method

- **Subjects** (asset prompts written as a user would, `rlib.SUBJECTS`):
  - knight (humanoid): "A sturdy young woman knight with short dark hair and no helmet, worn steel plate armour with brass trim over a deep blue padded tunic..."
  - wolf (quadruped): "A lean grey wolf with a thick cream chest ruff, dark stripes along the back..."
  - chest (prop_rigid): "A small sturdy wooden treasure chest with a curved lid, dark oak planks, chunky iron bands..."
- **Prompt:** the app's order from `stages/concept.build_prompt`: asset prompt, then the style `prompt_block`, then the category `pose_block`, read live from the YAML. A LoRA trigger is prepended.
- **Graphs:** the app's own builders (`vgac/graphs/concept.py`, `qwen_edit.py`). The LoRA is a `LoraLoaderModelOnly` inserted after the model loader, wired the way `pixel_graph` wires the pixel LoRA. The app's BiRefNet cutout is appended to the same graph.
- **Matched pairs:** "without" is the same graph and the same prompt (trigger included) with the LoRA at strength 0, so each pair differs only in the LoRA weight. Seeds 1101 and 2202. "now" references use the app's current model for that style, with no trigger.
- **Measures** (`rlib.concept_metrics`, on the BiRefNet cutout):
  - margin: the smallest gap from the alpha box to an image edge, as a fraction of the side. 0 means cropped.
  - single subject: connected components at least 1 percent of the foreground. Blind to an extra object that touches the subject (see clay v2).
  - bg std: RGB standard deviation outside the dilated cutout. Low means a plain backdrop; a floor shadow or gradient raises it.
  - arm gap: for humanoids, the fraction of rows from 30 to 55 percent of the figure height that show 3 or more separate runs (arm, torso, arm). It undercounts arms raised above that band.
  - hip/shoulder: the left-to-right extent at hip height over the shoulder width.
  - block err: pixel-art grid fit, the mean absolute error of a block-mean rebuild at the best block size from 4 to 32 px. Lower means chunkier, truer pixel art.
  - colours: distinct 5-bit colours inside the subject.
- **Contact sheets** were drawn for every experiment and every one was read by eye: `bakeoff/out/recipes/sheets/`.
- **Hardware:**
  - Everything up to the pixel tests ran on instance B (RTX 3080).
  - Midway the owner needed the 3080. Card b was switched off and the rest ran on instance A (RTX 3060) as one sequential queue.
  - Trimmed for that:
    - the rest of the pixel-art pairs and the Klein pixel test used seed 1101 only;
    - the Klein pixel LoRA ran at 0 and 1.0 only (planned: 0, 0.9, 1.2);
    - style variants v3 (lowpoly_flat) and v4 (clay) used seed 1101 only;
    - the turnaround LoRAs ran at strength 1.0 only (planned: 0.8 and 1.0);
    - AnyPose was dropped (section 9).

## 3. Candidate table

The research covered the Hugging Face adapter listings for each base model (Z-Image Turbo, Z-Image, Klein 4B and base 4B, Qwen-Image-Edit-2511 and 2509, Chroma1-HD), plus Civitai and Reddit for settings. Licence, trigger and file names come from the HF repo API on 2026-11-04. Every direct URL answered without a login. Strength is the card's value where stated, else "-" (community guidance for Z-Image Turbo style LoRAs is 0.7 to 0.9, and a combined total under about 1.2 to 1.4).

| # | Repo (huggingface.co/...) | Base | Licence | Trigger / prompt | Card strength | Why it might help / status |
|---|---|---|---|---|---|---|
| A1 | artificialguybr/PIXELART-REDMOND-ZIMAGETURBO | Z-Image Turbo | apache-2.0 | `Pixel Art, PixArFK` | - | Alternative pixel look. **Tested** |
| A2 | tarn59/pixel_art_style_lora_z_image_turbo | Z-Image Turbo | apache-2.0 | `Pixel art style.` | - | In use (style pixel). Reference |
| A3 | SkyAsl/Pixel-artist-Z | Z-Image Turbo | apache-2.0 | "a pixel art character ..." | 0.85 | Rejected: one narrow look (Nouns dataset); PEFT keys |
| A4 | artificialguybr/ICONS-REDMOND-ZIMAGETURBO | Z-Image Turbo | apache-2.0 | `ICONS. ICREDM.` | - | Item icons; may add an app tile. Not tested |
| A5 | artificialguybr/3DRenderStyle-REDMOND-ZIMAGE | Z-Image Turbo | apache-2.0 | `3D Render Style. 3DRenderAF.` | - | Stylised 3D look. **Tested** |
| A6 | artificialguybr/ISOMETRIC-REDMOND-ZIMAGE | Z-Image Turbo | apache-2.0 | `Isometric. IsometricRedm.` | - | Isometric props; may make dioramas. Not tested |
| A7 | artificialguybr/ClayAnimation-Redmond-ZIMAGE | Z-Image Turbo | apache-2.0 | `Clay animation, Clay.` | - | Clay style. **Tested** |
| A8 | artificialguybr/PS1Graphics-Redmond-ZimageTURBO | Z-Image Turbo | apache-2.0 | `Playstation 1 Graphics. PS1 Game.` | - | Retro low-poly. **Tested** |
| A9 | artificialguybr/DIGITALASSET-REDMOND-ZIMAGE | Z-Image Turbo | apache-2.0 | `Digital Asset.` | - | Unclear style. Not tested |
| A10 | artificialguybr/STICKERS-REDMOND-ZIMAGETURBO | Z-Image Turbo | apache-2.0 | `Sticker. Stickers` | - | White background, but adds a sticker border. Not tested |
| A11 | artificialguybr/CuteCartoon-Redmond-ZIMAGE | Z-Image Turbo | apache-2.0 | `CuteCartoonAF, Cute Cartoon.` | - | Optional cartoon style. Not tested |
| A12 | artificialguybr/360VIEW-REDMOND-ZIMAGETURBO | Z-Image Turbo | apache-2.0 | `360, 360 VIEW.` | - | Rejected: panoramas, not object turns |
| A13 | renderartist/Saturday-Morning-Z-Image-Turbo | Z-Image Turbo | apache-2.0 | `saturd4ym0rning` | - | 2D TV cartoon, early version. Not tested |
| A14 | DK9/3D_MMORPG_style_z-image-turbo_lora | Z-Image Turbo | apache-2.0 | - | - | Rejected: trained on a commercial game (Lost Ark) |
| A15 | NoahtheGameplayer/flat-color-style | Z-Image Turbo | openrail | - | - | Rejected: no description or data |
| A16 | Muapi character-design-sheet-helper (Civitai mirror) | SDXL (Illustrious) | openrail++ | `reference sheet, simple white background, ...` | - | Rejected: the HF file is the SDXL version |
| A17 | windflakes85/Toys | Z-Image Turbo | creativeml-openrail-m | - | - | Rejected: explicit content |
| A18 | "D&D Character Sheet - ZIT" (Civitai only) | Z-Image Turbo | Apache-2.0 (unverified) | `D&D Character Sheet` | 0.8 | Not tested: Civitai login, licence unverified |
| A19 | alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1 | Z-Image Turbo | apache-2.0 | ControlNet (pose, depth, canny) | - | Would force an A-pose; 2 GB lite file. Not tested |
| B1 | furaidosu/flat-cartoon-zimage | Z-Image base | apache-2.0 | - | - | Only game-relevant base adapter; no data info. Not tested |
| B2 | Davidyich/otp3d-style-loras | Z-Image base | none | - | - | Rejected: no licence |
| C1 | Limbicnation/pixel-art-lora | Klein 4B (distilled) | apache-2.0 | `pixel art sprite, [desc], game asset` | 0.85 to 1.4 | CC0 training data. **Tested** |
| C2 | adirik/pixel-art-lora-flux.2-klein-4B | Klein base 4B | apache-2.0 | `PXART4` | - | Base 4B only (the app runs distilled). Not tested |
| C3 | svntax-dev/pixel_spritesheet_4walk_small_lora_v1 | Klein base 4B | apache-2.0 | - | - | Walk sheets; author says distilled 4B ruins pixels. Not tested |
| C4 | fal/flux-2-klein-4b-spritesheet-lora | Klein 4B | apache-2.0 | `2x2 sprite sheet` | 1.1 | Four views of a prop on red. Not tested |
| C5 | fal/flux-2-klein-4B-background-remove-lora | Klein 4B | apache-2.0 | `Remove the background from the image` | - | Cleanup step. Not tested |
| C6 | xocialize/refcontrol-FLUX.2-klein-4B-pose-lora | Klein base 4B | apache-2.0 | `refcontrol` | - | Re-pose to a skeleton; ComfyUI use unverified. Not tested |
| C7 | lrzjason/Consistance_Edit_Lora (f2k 4B file) | Klein 4B | apache-2.0 | - | - | Less drift in Klein edits. Not tested |
| C8 | busetolunay/assets-bld5g-klein-4b-rich | Klein base 4B | creativeml-openrail-m | `bld5g style` | - | Unclear style. Not tested |
| C9 | Mike0021/flux2-klein-apple-emoji-lora | Klein base 4B | other | `emj icon` | - | Rejected: trained on a company's emoji art |
| C10 | ostris/sketch_to_image_klein_4b | Klein base 4B | apache-2.0 | - | - | Sketch control. Not tested |
| - | Klein 9B LoRAs (crafiq game-asset-tiles, dx8152, linoyts Delight, ...) | Klein 9B | various | - | - | Rejected: 9B LoRAs do not load on 4B, and Klein 9B is non-commercial |
| D1 | tarn59/character_turnaround_sheet_qwen_edit_2511 | QIE 2511 | apache-2.0 | `Character turnaround sheet` | - | One-pass turnaround sheet. **Tested** |
| D2 | berkerdooo/qwen-image-edit-2511-camera-angle-lora | QIE 2511 | apache-2.0 | "Move the camera N degrees to the right around the object. Target camera elevation: E degrees. Keep the object, its colors, and the white background the same." | - (card: 25 steps, true CFG 3) | Object turns on white, CC-BY and CC0 Objaverse data. **Tested** |
| D3 | zealman/front-right-isometric-view_lora_v1 | QIE 2511 | apache-2.0 | `front-right isometric view` | - | Isometric prop views. Not tested |
| D4 | lilylilith/AnyPose (base + helper) | QIE 2511 | apache-2.0 | long fixed prompt from the card | 0.7 + 0.7 | Re-pose to an A-pose. Downloaded, not tested (section 8) |
| D5 | lilylilith/QIE-2511-To_Material_Preview_Render | QIE 2511 | apache-2.0 | `Turn the image into a preview render.` | 1.0 | Delight before 3D. Not tested |
| D6 | prithivMLmods/QIE-2511-Studio-DeLight | QIE 2509/2511 | apache-2.0 | unverified | - | Studio relight. Not tested |
| D7 | Hyperccino/Qwen-Edit-2511-PS1-v1.0 | QIE 2511 | apache-2.0 | `Turn this into retro low poly 3D model style, gray background` | - | Retro restyle as an edit. Not tested |
| D8 | Hyperccino/Qwen-Edit-2511-PS1-to-Untextured-v1.0 | QIE 2511 | apache-2.0 | `Turn this into a colorless untextured matcap render, dark gray background` | - | Matcap for shape checks. Not tested |
| D9 | svntax-dev/pixel_spritesheet_4walk_combat_32x48_v1 | QIE 2511 | apache-2.0 | - | - | Pixel walk and attack frames. Not tested |
| D10 | lrzjason/Consistance_Edit_Lora (qe2511 alpha) | QIE 2511 | apache-2.0 | - | - | Less drift; needs the author's workflow. Not tested |
| D11 | maxbsoft/cbai-adapter3-portrait2sheet-qwen2511 | QIE 2511 | apache-2.0 | - | - | Head and bust sheets only. Not tested |
| D12 | tarn59/extract_texture_qwen_image_edit_2511 | QIE 2511 | apache-2.0 | `Extract texture from {object} into flattened texture.` | - | Material work. Not tested |
| D13 | prithivMLmods/QIE-2511-Extract-Outfit | QIE 2511 | apache-2.0 | - | - | Not needed |
| D14 | dx8152/Qwen-Image-Edit-2511-Style-Transfer | QIE 2511 | apache-2.0 | "Change the style of Figure 1 to the style of Figure 2." | - | House-style transfer; unproven (0 downloads). Not tested |
| D15 | prithivMLmods/Qwen-Image-Edit-2511-Pixar-Inspired-3D | QIE 2511 | apache-2.0 | - | - | Rejected: named after a commercial studio's style |
| D16 | WarmBloodAban/Anyto3DDonghuaStyle | QIE 2511 | apache-2.0 | `transform into 3d style` | 0.8 to 1.0 | Rejected as a precaution: training source not stated |
| D17 | Zaytron40k Scene2CharRef / CharSheet2Art | QIE 2511 | none | - | - | Rejected: no licence, partly explicit data |
| D18 | Notid/qwen-edit-2511-mask-extraction | QIE 2511 | none | - | - | Rejected: no licence |
| D19 | fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA | QIE 2511 | apache-2.0 | `<sks> [azimuth] [elevation] [distance]` | 0.8 to 1.0 | In use at 0.9 (inside the card range). The many same-name repos are re-uploads |
| D20 | lightx2v/Qwen-Image-Edit-2511-Lightning | QIE 2511 | apache-2.0 | - | 1.0 | In use (4 step). An 8-step file exists (850 MB) |
| E1 | dx8152/Qwen-Edit-2509-Multiple-angles | QIE 2509 | apache-2.0 | camera commands | - | Predecessor of D19. Not tested |
| E2 | tarn59/character_turnaround_sheet_qwen_image_edit_2509 | QIE 2509 | apache-2.0 | `Character turnaround sheet` | - | Use D1 on 2511 |
| E3 | lovis93/next-scene-qwen-image-lora-2509 | QIE 2509 | MIT | "Next Scene:" | - | Story frames, not sheets |
| E4 | rsshekhawat/Qwen-Edit-3DChibi-LoRA | QIE 2509 | apache-2.0 | `3D Chibi Style` | - (25 steps, CFG 5) | Toy look. Not tested |
| E5 to E8 | dx8152 White_to_Scene, Multi-Angle-Lighting; flymy-ai inscene; tarn59 apply_texture 2509 | QIE 2509 | apache-2.0 | - | - | Not needed (White_to_Scene works the wrong way) |
| F1 | codeShare/Drills3D_Chroma_LoRa | Chroma1-HD | apache-2.0 | - | - | Rejected: styled after named artists. No usable Chroma or Anima game-asset adapters exist on HF |

Licence notes:
- Every **tested** file is Apache-2.0 on its model card.
- Anima's own licence is non-commercial for the model, but its card says the restriction does not cover outputs. That matches models.yaml.
- **Chosen for testing (8):** A5, A7, A8, A1, C1, D1, D2, D4.
  - Why these: each has a check a script can make (frame, background, single subject, pose, pixel grid, view consistency).
  - Each targets a style the app already ships, or the turnaround.
  - Each is a direct HF download under 1 GB.

## 4. Concept add-ons: results

Means over 3 subjects x 2 seeds (pixel-art pairs: 4 or 3 images, as trimmed). "s0.0" means the LoRA was off but the trigger was still in the prompt.

| exp | cond | n | min margin | single subject | bg std | arm gap (knight) | hip/shoulder (knight) | block err | colours |
|---|---|---|---|---|---|---|---|---|---|
| render3d (painted style) | s0.0 | 6 | 0.024 | 6/6 | 13.9 | 0.958 | 1.395 | - | 1708 |
| render3d | s0.7 | 6 | 0.008 | 6/6 | 20.9 | 0.764 | 1.397 | - | 1531 |
| render3d | s1.0 | 6 | 0.011 | 6/6 | 25.4 | 0.577 | 1.398 | - | 1728 |
| clay (clay style) | klein_now (app today) | 6 | 0.037 | 6/6 | 7.8 | 0.004 | 1.899 | - | 1550 |
| clay | s0.0 (Z-Image Turbo, no LoRA) | 6 | 0.048 | 6/6 | 13.6 | 0.647 | 1.921 | - | 1364 |
| clay | s0.7 | 6 | 0.045 | 6/6 | 19.0 | 0.637 | 2.157 | - | 1313 |
| clay | s1.0 | 6 | 0.028 | 6/6 | 22.5 | 0.605 | 2.196 | - | 1312 |
| ps1 (retro64 style) | zt_now (app today) | 6 | 0.065 | 6/6 | 6.8 | 0.625 | 1.490 | - | 1660 |
| ps1 | s0.0 | 6 | 0.051 | 6/6 | 3.5 | 0.881 | 1.341 | - | 1954 |
| ps1 | s0.6 | 6 | 0.031 | 6/6 | 2.8 | 0.933 | 1.356 | - | 1507 |
| ps1 | s0.9 | 6 | 0.031 | 6/6 | 3.0 | 0.938 | 1.352 | - | 1237 |
| pixred (pixel style) | tarn_now (app today) | 4 | 0.039 | 4/4 | 5.2 | 0.839 | 1.329 | 3.11 | 23 |
| pixred | s0.0 (no LoRA) | 4 | 0.070 | 4/4 | 3.3 | 0.726 | 1.387 | 2.47 | 19 |
| pixred | s0.7 | 4 | 0.062 | 4/4 | 3.5 | 0.166 | 1.373 | 1.67 | 23 |
| pixred | s1.0 | 4 | 0.062 | 4/4 | 3.1 | 0.613 | 1.356 | 1.18 | 17 |
| kleinpix (pixel style on Klein) | tarn_now | 3 | 0.039 | 3/3 | 5.7 | 0.847 | 1.300 | 2.74 | 24 |
| kleinpix | s0.0 | 3 | 0.031 | 3/3 | 2.7 | 0.153 | 1.549 | 2.81 | 25 |
| kleinpix | s1.0 | 3 | 0.031 | 3/3 | 3.3 | 0.136 | 1.556 | 3.01 | 26 |

All 91 add-on images, 60 style-check images and 16 pose-block images were fully in frame (smallest margin 0.008) and single-subject by the component count.

### 4.1 3DRenderStyle Redmond (Z-Image Turbo): optional at 0.7
- File: `VGAC/zit_3drender_redmond.safetensors`
- Source: artificialguybr/3DRenderStyle-REDMOND-ZIMAGE
- Licence: Apache-2.0
- Trigger: `3D Render Style. 3DRenderAF.`

Seen:
- A consistent stylised 3D-character look: rounder faces, toy-like armour, a clean wolf and chest.
- The trigger text alone, at s0.0, already moves Turbo halfway there.

Damage:
- The backdrop turns into a darker studio gradient: bg std 13.9 rises to 20.9 at 0.7 and 25.4 at 1.0.
- The subject grows toward the edges: the knight's feet came within 0.8 percent of the bottom.
- The arms close in: arm gap 0.96 falls to 0.58 at 1.0, and knight 2202 at 1.0 has its arms against the body.
- BiRefNet still cut every image cleanly.

Use: worth offering as a "Stylised 3D" preset at 0.7 with the new pose block, not as a default on painted.

### 4.2 ClayAnimation Redmond (Z-Image Turbo): optional at 0.6 to 0.7
- File: `VGAC/zit_clay_redmond.safetensors`
- Source: artificialguybr/ClayAnimation-Redmond-ZIMAGE
- Licence: Apache-2.0
- Trigger: `Clay animation, Clay.`

The main finding is the "without" side. Z-Image Turbo with the trigger words and the current clay block, and no LoRA:
- gives a convincing plasticine knight, wolf and chest;
- keeps the A-pose (arm gap 0.65);
- the app today (Klein) gives 0.004: arms at the sides in every knight.

With the LoRA:
- At 0.7 the finish is more matte and hand-made.
- At 1.0 it loses prompt detail: knight 2202 lost most of her armour, and the wolf's ruff turned into a scarf-like lump.
- The backdrop darkens: bg std 13.6, 19.0 and 22.5 at 0, 0.7 and 1.0.

Use: the style can do without it. Offer it for a stronger clay finish.

### 4.3 PS1Graphics Redmond (Z-Image Turbo): optional for retro64 at 0.6
- File: `VGAC/zit_ps1_redmond.safetensors`
- Source: artificialguybr/PS1Graphics-Redmond-ZimageTURBO
- Licence: Apache-2.0
- Trigger: `Playstation 1 Graphics. PS1 Game.`

Text check: the trigger contains a console name, which the skill warns gets painted as lettering. No lettering appeared in 18 images.

At 0.6:
- the cube-headed "voxel" figures of the current retro64 output become low-poly 3D models with low-resolution textures;
- the backdrop is the plainest of any test (bg std 2.8 vs 6.8 for the app today);
- the arms clear the body (arm gap 0.93 vs 0.63).

At 0.9 adherence slips: knight 2202 shows bare arms and legs and lighter armour, and the colour count drops.

Use: offer at 0.6. It is not the default only because the training data is not described on the card ("Screencap" in the file name) and 0.9 already drifts.

### 4.4 PixelArt Redmond (Z-Image Turbo): optional "chunky sprite" variant at 1.0; keep tarn59 as default
- File: `VGAC/zit_pixelart_redmond.safetensors`
- Source: artificialguybr/PIXELART-REDMOND-ZIMAGETURBO
- Licence: Apache-2.0
- Trigger: `Pixel Art, PixArFK`
- Run through the app's pixel graph, swapped in for the current LoRA, with PixelArtDetectorToImage at 32 colours.

Block error falls from 2.47 (no LoRA) and 3.11 (current tarn59) to 1.67 at 0.7 and 1.18 at 1.0. These are visibly chunkier, more authentic low-resolution sprites; the wolf and chest at 1.0 are the most game-like pixel art in the test.

On the knight it costs adherence:
- bare legs at both strengths;
- arms closed at 0.7 (arm gap 0.17);
- at 1.0 seed 2202 gained a cap, though the prompt says "no helmet", and a different skin tone.

The current tarn59 LoRA gives finer pixels and the most faithful humanoids (arm gap 0.84).

Use: Z-Image Turbo with no LoRA plus the detector is also a clean pixel look, a useful fallback.

### 4.5 Limbicnation pixel-art (FLUX.2 Klein 4B distilled): reject
- File: `VGAC/klein4b_pixelart_limbicnation.safetensors`
- Licence: Apache-2.0, CC0 training data
- Prompt prefix: `pixel art sprite, game asset.`

At 1.0 the images are nearly identical to strength 0 (block error 2.81 vs 3.01, the same composition). The LoRA loads, since the shadows and colours shift slightly, but adds no visible pixel character.

Klein also dropped the A-pose (arm gap 0.15). Not tested above 1.0 (the card allows up to 1.4) because of the trim.

## 5. Turnaround add-ons: results

**Setup:**
- Qwen-Image-Edit-2511 fp8 with Lightning 4 step at 1.0 (the app's `angle_graph`), seed 777.
- Each candidate replaces the fal LoRA in slot `l2` at strength 1.0 (trimmed: 0.8 was planned too).
- Sources:
  - knight and chest: painted Z-Image Turbo concepts, seed 1101;
  - photoreal pairing (section 7): human, lantern, mossy wall and wolf from Z-Image base with the first photo block (`zbase_new`).

**Methods:**
- **fal:** the app today. fal multiple-angles at 0.9, front, left, back, right and quarter, one job per view. For the photoreal subjects the quarter view was skipped (trim).
- **cam1.0:** the berkerdooo camera-angle LoRA with its card prompt "Move the camera N degrees to the right/left around the object ... Keep the object, its colors, and the white background the same". Relative turns of +90, 180, -90 and +45, from the source composited on white.
- **sheet1.0:** the tarn59 turnaround sheet LoRA, prompt `Character turnaround sheet`. The input is the source cut out and padded to 600 x 1080; the output is one 2592 x 1088 image, split into figures at empty columns.

**Measures** (the app's own turnaround checks from `stages/turnaround.py`; generated views only, the source excluded):
- mirror IoU of the two side views (the sheet: figures 2 and 4);
- spread of height ratio against the front;
- colour-histogram distance to the source, mean and worst (the app's limit is 0.35);
- single subject per view.

No meshes were built, so bakeoff/metrics.py's mesh IoU was not used.

| subject | method | views | side mirror IoU | height spread | hist vs source mean / worst | single subject |
|---|---|---|---|---|---|---|
| knight (painted) | fal (today) | 5 | **0.899** | 0.027 | **0.141** / 0.183 | 5/5 |
| knight | cam1.0 | 4 | 0.869 | 0.023 | 0.172 / 0.211 | 4/4 |
| knight | sheet1.0 | 5 | 0.870 | 0.021 | 0.148 / 0.236 | 5/5 |
| chest (painted) | fal (today) | 5 | 0.689 | 0.113 | 0.355 / 0.437 | 5/5 |
| chest | cam1.0 | 4 | 0.942 (source vs a side view, see note) | 0.155 | 0.411 / 0.473 | 4/4 |
| chest | sheet1.0 | 5 | 0.857 | 0.118 | 0.260 / 0.501 | 5/5 |
| human (photo) | fal | 4 | 0.649 | 0.021 | 0.187 / 0.255 | 4/4 |
| human | sheet1.0 | 5 | 0.637 | 0.014 | **0.137** / 0.187 | 5/5 |
| lantern (photo) | fal | 4 | 0.843 | 0.057 | 0.314 / 0.445 | 4/4 |
| lantern | sheet1.0 | 5 | 0.869 | 0.018 | 0.279 / 0.413 | 5/5 |
| wall (photo) | fal | 4 | 0.664 | 0.257 | 0.261 / 0.401 | 4/4 |
| wall | sheet1.0 | 5 | 0.851 (repeated views, see below) | 0.131 | 0.225 / 0.532 | 5/5 |
| wolf (photo) | fal | 4 | **0.604** | 0.094 | **0.140** / 0.220 | 4/4 |
| wolf | sheet1.0 | 5 | 0.492 | 0.035 | 0.259 / 0.359 | 5/5 |

Note on the chest's camera-LoRA mirror IoU: the source is a three-quarter view, so the mirror pair is the source against a turned view (the better of the two possible directions). It is not comparable with fal's left/right pair.

Time per subject on the RTX 3060:
- fal: five jobs of about 62 s, plus cutouts.
- Camera LoRA: four jobs of about 63 s.
- Sheet: one job of about 105 s.

### 5.1 tarn59 character turnaround sheet (`VGAC/qie2511_turnaround_sheet_tarn59.safetensors`, Apache-2.0, trigger `Character turnaround sheet`): optional, humanoids only, strength 1.0

What it does well:
- On people it is the most consistent set seen: the outfit details agree in all five figures (front, side, back, side, three-quarter).
- The background comes out clean white with even light, and the A-pose holds.
- Colour identity matches or beats fal: human 0.137 vs 0.187, knight 0.148 vs 0.141.
- It costs one job instead of five, at about a third of the time.

Damage:
- **Style drift:** a painted source comes back photoreal (polished metal, photographic faces). For Photoreal that is a feature; for painted, cel or clay it breaks the look.
- **Tight framing:** heads and feet nearly touch the image edges, and the figures come out narrow, as the author warns.
- **Fixed views:** the five views cannot be asked for one at a time, so refining a single view needs fal anyway.
- **Non-humanoids fail by eye even where numbers look fine:**
  - the chest got taller and narrower, the padlock moved to the side in one figure, and one corner broke;
  - the wolf had no true front view and the same side twice (mirror IoU 0.49);
  - the wall repeated one three-quarter view three times, which is why its mirror IoU of 0.85 is meaningless.

Use: offer as an option for humanoids, and recommend it for Photoreal humanoids. Do not use it for other categories.

### 5.2 berkerdooo camera angle (`VGAC/qie2511_camera_angle_berkerdooo.safetensors`, Apache-2.0): optional, rigid props only, strength 1.0

What it does well:
- Views come back on clean white with no cast shadow, which is good for image-to-3D.
- The views turn by the requested amounts.

Damage:
- Colour identity is worse than fal on both subjects: knight 0.172 vs 0.141, chest 0.411 vs 0.355; the chest is above the app's 0.35 limit.
- The padlock appears and disappears between views.
- The +45 knight view has twisted feet.
- The relative-angle prompts need the source's own angle to be known before they map to front, left and back.

Not tried: the card's own settings (25 steps, true CFG 3, no Lightning), which might do better at about 6 times the time.

### 5.3 Current fal multiple-angles at 0.9: stays the default

It is still the best on the painted knight (side mirror IoU 0.899, colour distance 0.141) and the only method whose views can be asked for one at a time. Two weaknesses:
- **It copies the source's backdrop and lighting.** With the photo sources it redrew the softboxes in every view, and it keeps cast shadows. So the concept must already be clean.
- **Proportions drift on scenery:** the wall's height spread was 0.257.

No change to its strength is suggested.

## 6. Style presets without LoRAs (Part 3)

Two subjects (knight, chest) by two seeds for "now". Proposed blocks were run on all three subjects; v3 and v4 used seed 1101 only. Sheets: `sheets/style_<id>.png`.

| style | variant | model | n | min margin | single | bg std | arm gap (knight) | block err | colours | look delivered? |
|---|---|---|---|---|---|---|---|---|---|---|
| retro64 | now | zturbo | 4 | 0.065 | 4/4 | 9.7 | 0.625 | 2.03 | 2059 | **No**: cube-headed voxel figures; the chest is a modern stylised prop |
| retro64 | v2 | zturbo | 6 | 0.041 | 6/6 | 16.7 | 0.861 | 2.02 | 1904 | Partly: real 3D models with gouraud shine, heads still blocky, studio gradient |
| retro64 | v3 (v2 text, medium first) | zturbo | 6 | 0.064 | 6/6 | 16.6 | 0.833 | 1.78 | 2166 | Worse than v2: cubes came back on head and wolf |
| lowpoly_flat | now | zturbo | 4 | 0.039 | 4/4 | 10.4 | 0.925 | 2.01 | 1630 | Partly: faceted head, smooth metal and wood |
| lowpoly_flat | v2 | zturbo | 6 | 0.038 | 6/6 | 10.5 | 0.910 | 1.82 | 1480 | Partly: the wolf is properly faceted; the knight's armour and the chest stay smooth |
| lowpoly_flat | v3 (v2 text, medium first) | zturbo | 3 | 0.038 | 3/3 | 9.3 | 0.935 | 1.51 | 1279 | Best of the three: chest and wolf flatter, fewer colours; knight armour still glossy |
| clay | now | klein | 4 | 0.037 | 4/4 | 7.6 | **0.004** | 2.10 | 1625 | Look yes (vinyl toy more than clay); **pose no**, arms at the sides |
| clay | v2 ("puppet" block) | zturbo | 6 | 0.049 | 6/6 | 12.9 | 0.694 | 2.72 | 1372 | Clay yes, but **a second clay figure appeared beside the chest in both seeds** |
| clay | v3 (v2 text) | klein | 6 | 0.039 | 6/6 | 8.5 | 0.011 | 2.18 | 1846 | Same as now; Klein still drops the A-pose |
| clay | v4 (no "puppet") | zturbo | 3 | 0.046 | 3/3 | 14.7 | 0.851 | 2.40 | 1206 | Clean; wolf and chest clay; knight's armour reads as metal |
| clay | Z-Image Turbo, current block, "Clay animation, Clay." first (section 4.2 s0.0) | zturbo | 6 | 0.048 | 6/6 | 13.6 | 0.647 | - | 1364 | **Best clay**: all three clay, A-pose kept |
| cel | now | anima | 4 | 0.019 | 4/4 | 7.2 | 0.691 | 3.09 | 989 | **Yes**: clean anime cel look, flat backdrop; gloves oversized on one knight |
| pixel | now | pixel | 4 | 0.039 | 4/4 | 7.3 | 0.839 | 3.57 | 18 | **Yes**: true pixel grid, 8 to 26 colours |
| realistic_pbr | now | zbase | 4 | 0.019 | 4/4 | 17.9 | 0.609 | 2.17 | 1365 | **Yes**: photoreal armour and wood; strongest floor shadow and gradient (bg std 18) |

### 6.1 Proposed style blocks

All blocks follow the skill: the medium first as a physical thing, no console or brand names, exclusions at the end.

- **retro64** (keep zturbo; use the v2 text in the app's current order; optionally add the PS1 LoRA at 0.6):
  > An in-game real-time 3D model from a late-1990s 64-bit home console game, a low-polygon mesh rendered in real time, not a drawing or illustration. Smooth gouraud vertex shading with soft glossy specular highlights, slightly angular silhouettes, chunky simplified hands and details, blurry low-resolution bilinear-filtered textures, bold saturated colours, bright ambient light with one warm key light from the upper left, a soft round blob shadow underneath, the whole image slightly soft like a low-resolution frame upscaled. Not built from cubes, no pixel art, no voxels.

  Caveat: the blob shadow raises bg std; BiRefNet cut it out cleanly in all 12 images.
- **lowpoly_flat** (keep zturbo; this text placed before the asset prompt did best):
  > A low-poly 3D model rendered in real time with flat shading, built from a few hundred large flat triangular facets, every facet one solid colour so each plane shows as a separate tone, sharp angular edges everywhere including the face, hair, cloth and wood, no textures and no smooth or rounded surfaces. Clean readable silhouette, soft pastel palette, simple soft ambient light. No pixel art, no voxels, no fine surface detail.

  Not fully solved: glossy metal stays smooth on Z-Image Turbo. Untested alternatives: Klein for this style, or the Isometric/CuteCartoon-type LoRAs.
- **clay** (switch the model to **zturbo**; put "Clay animation, Clay." first and keep the current text):
  > Clay animation, Clay. Stop-motion clay style, soft matte sculpted surfaces with fingerprint texture, rounded simplified shapes, warm muted colours, as if photographed on a tabletop set.

  Never use "puppet" or "figure" (an extra figure appeared). Optional: the clay LoRA at 0.6 to 0.7 for a more hand-made finish.
- **cel, pixel**: keep as they are.
- **realistic_pbr**: the "now" row above used the older block ("Photorealistic physically based rendering reference ..."), which gave the strongest floor shadow (bg std 18). The block was replaced on 2026-11-04 (commit cf09eaf, "light tent" wording plus a recipe negative). Section 7 tests the replacement: it works, so keep it.

### 6.2 Proposed humanoid pose block (posefix.py, 4 seeds x 2 models, `sheets/posefix.png`)

> The character stands in a relaxed A-pose, both arms straight and angled about thirty degrees out from the body with a clear gap between each arm and the torso, palms inward, feet shoulder-width apart, facing the camera directly. The whole figure from the top of the head to the soles of the feet fits inside the middle four fifths of the image height, with empty background above the head and below the feet, centred in frame. Plain flat light grey background. Soft even studio lighting from the front with no hard shadows. No text, no logos, no background scenery, no floor shadow, no motion blur, no cropping, nothing held in the hands.

| model | block | mean margin | arm gap | bg std | by eye |
|---|---|---|---|---|---|
| zturbo | now | 0.027 | 0.965 | 10.4 | A-pose, slight floor shadow |
| zturbo | new | 0.030 | 0.979 | 10.5 | Same images, nearly pixel for pixel |
| klein | now | 0.029 | 0.127 | 6.4 | Arms hanging at the sides in 4 of 4 |
| klein | new | 0.032 | 0.312 | 3.1 | Clear A-pose with arms well out in 4 of 4; floor shadow mostly gone |

The arm-gap measure understates the Klein gain: its arms are raised above the measured band, so the eye check carries this result.

The headroom wording barely moves Z-Image Turbo's framing: margins stay at 2 to 3 percent, about 25 px at 1024. So do not rely on the prompt for margin; the app's cutout-based framing check stays necessary.

Verdict: adopt. It is neutral on Z-Image Turbo and fixes Klein.

## 7. Photoreal recipe (owner request)

**Question:** which painter and settings give the best photoreal concept for image-to-3D? What matters:
- even, shadowless light, since cast shadows and one-sided light bake into the texture;
- a plain backdrop;
- the whole subject in frame;
- crisp surface detail;
- no depth-of-field blur.

Also checked: does any add-on that pushes toward photoreal help? Of the tested add-ons only the tarn59 turnaround sheet did (section 5.1); none of the concept LoRAs moved toward photoreal.

**Subjects** (seed 1101, `bakeoff/recipes/photoreal.py`):
- a weathered iron lantern (prop_rigid);
- a mossy stone wall section (environment);
- the wolf (quadruped);
- a man in a grey hooded jacket (humanoid, to document faces and hair).

**Painters and prompts.** The category pose block was used throughout; v2 humanoids used the new pose block from section 6.2.

| variant | model, settings | prompt | negative |
|---|---|---|---|
| zbase_app | Z-Image base, 30 steps, CFG 4, res_multistep/simple, shift 3 | the app's current realistic_pbr block ("A sharp studio product photograph taken for a 3D scanning reference ... from a large soft light tent ..."), asset first | the style's recipe negative from styles.yaml |
| zbase_now | same | same block | the graph's default painterly negative |
| zbase_new | same | block 1, medium first: "... lit by large soft light boxes from the front, both sides and above ..." | photo negative |
| zturbo_new | Z-Image Turbo, 8 steps, CFG 1 | block 1 | none possible |
| klein_new | Klein 4B, 4 steps | block 1 | none possible |
| zbase_v2 | Z-Image base, 30, CFG 4 | block 2, medium first (below) | photo negative plus equipment words |
| klein_v2 | Klein 4B, 4 steps | block 2 | none possible |

**Extra measures** (`photoreal.photo_metrics`):
- cast shadow: share of a 40 px ring around the subject that is more than 15 percent darker than the backdrop;
- light asymmetry (lr): left-half against right-half brightness of the subject, 0 means even;
- sharpness: mean Laplacian inside the subject;
- dof ratio: the softest third-of-subject cell against the median. It proved unreliable: glass, smooth cloth and soft fur read as "soft" though the eye sees no focus blur.

| subject | variant | margin | bg std | cast shadow | lr asym | sharpness | by eye |
|---|---|---|---|---|---|---|---|
| lantern | zbase_app | 0.146 | 9.8 | 0.004 | 0.039 | 20.8 | Real photo, crisp rust, even light |
| lantern | zbase_v2 | 0.166 | 8.6 | 0.029 | 0.117 | 23.8 | Same quality, smaller in frame |
| lantern | zbase_new | 0.143 | **53.1** | 0.020 | 0.056 | 21.5 | **Softboxes and stands drawn in frame** |
| lantern | zturbo_new | 0.082 | **44.4** | 0.001 | 0.113 | 11.8 | **Softboxes in frame** |
| lantern | klein_v2 | 0.105 | 4.1 | 0.000 | 0.026 | 11.5 | Cleanest light and backdrop; less fine detail, a little CG-clean |
| wall | zbase_app | 0.173 | 9.5 | 0.045 | 0.168 | 43.8 | Real stone and moss, crisp; soft contact shadow |
| wall | zbase_v2 | 0.206 | 6.0 | 0.051 | 0.126 | 43.6 | As app, plainer backdrop |
| wall | klein_v2 | 0.104 | 6.4 | 0.044 | 0.056 | 25.6 | Even light; stones more uniform, less detail |
| wolf | zbase_app | 0.125 | 15.1 | 0.015 | 0.234 | 18.3 | Real wolf, fur crisp; light slightly from one side |
| wolf | zbase_v2 | 0.122 | 11.1 | 0.033 | 0.304 | 16.3 | As app |
| wolf | zturbo_new | 0.033 | 46.1 | 0.002 | 0.041 | 14.2 | Softboxes in frame, crowded edge |
| wolf | klein_v2 | 0.048 | 6.5 | 0.028 | 0.049 | 19.9 | Most even light; large in frame |
| human | zbase_app | 0.045 | 12.6 | 0.008 | 0.149 | 9.8 | Real face and hair; **arms at the sides** (arm gap 0.00) |
| human | zbase_v2 (new pose block) | 0.083 | 7.8 | 0.003 | 0.236 | 11.2 | Arms raised almost to a T, figure small; rig-ready but odd |
| human | klein_v2 (new pose block) | 0.042 | 5.6 | 0.001 | 0.108 | 13.4 | **Clean A-pose** (arm gap 0.72), even light, believable face |

Findings:
1. **Never name light equipment in the prompt.** "Large soft light boxes" made Z-Image base and Turbo draw softboxes and light stands into 7 of 8 frames (bg std 41 to 61). The fal turnaround then copied them into every view. The app's current "light tent" wording drew nothing. The v2 wording ("light from every direction like a bright overcast sky ... no visible lights, lamps or studio equipment") was clean in 8 of 8.
2. **No depth-of-field blur appeared** in any image from any painter. All three blocks forbid it, and the app's negative lists it.
3. **Z-Image base keeps the best surface detail:** sharpness 18 to 44 against Klein's 11 to 26, for example rust pitting, moss and fur. That is what the baked texture needs most.
4. **Klein gives the most even light** (lr 0.03 to 0.11) and the plainest backdrop, about 5 times faster (16 s against 80 s on the 3060), but it simplifies surfaces.
5. **People:** Z-Image base with the current humanoid pose block leaves the arms down, which breaks rigging. With the new block it overshoots toward a T-pose. Klein with the new block gave the best rig-ready photoreal person.

**Recommended Photoreal recipe:**
- **Painter:** Z-Image base (`zbase`), 30 steps, CFG 4, res_multistep, simple scheduler, shift 3, as today.
- **Prompt block:** keep the current realistic_pbr block; it already produces clean photographic concepts. If the block is ever rewritten, use block 2 below and never name lights.
- **Negative:** keep the current recipe negative, and add "softbox, light stand, umbrella light, studio equipment, lamp, tripod" as a guard.
- **Humanoids:**
  - use the new humanoid pose block (section 6.2);
  - consider Klein 4B as the humanoid painter for Photoreal: the cleanest A-pose and even light seen, at the cost of fine detail;
  - test 2 to 4 more seeds before deciding, since this is one seed.
- **Add-ons:** no concept add-on for this look. Turnaround: the tarn59 sheet at 1.0 is recommended as an option for Photoreal humanoids (section 5.1). There one-pass consistency, clean white background and even light are all wanted, and its photoreal drift is no longer damage. Not a default on one photoreal sample.
- **Caveat for human characters** (shown with the recipe; extends the existing humanoid caveat):
  > Photoreal people are the hardest case. The concept picture can look like a real photograph, but the 3D model is one shell with one painted skin: eyes, teeth and hair are painted onto the surface rather than made separately, and hair becomes a solid shape. So faces look less real than clothes and gear, and the face may not stay identical from every side. Check the face in the turnaround before building the model.

Block 2 (for reference, medium first; clean in 8 of 8):
> A real high-resolution photograph of the subject alone on a seamless plain light grey backdrop, made as a photogrammetry reference: soft, even, diffuse light from every direction like a bright overcast sky, so there are no shadows and no highlights from any single side, the whole subject in crisp focus from front to back, true-to-life materials and colours, fine crisp surface detail. No visible lights, lamps or studio equipment, no depth of field blur, no bokeh, no directional lighting, no cast shadows, no vignette.

Sheets: `sheets/photoreal_final.png` (the app today against the proposals) and `sheets/photoreal.png` (all seven variants). Images: `photoreal/<subject>_<variant>_{img,cut}.png`. Numbers: `photoreal.json`.

## 8. Proposed recipe table

"Pose block" means the category pose block, with the humanoid text of section 6.2. Turnaround means fal multiple-angles at 0.9 unless the row says otherwise.

| Style | Category | Model, steps | Prompt block change | Default add-ons | Optional add-ons | Turnaround |
|---|---|---|---|---|---|---|
| painted | all | zturbo, 8 | none | none | - | see section 5 |
| painted (new "stylised 3D" preset) | all | zturbo, 8 | trigger `3D Render Style. 3DRenderAF.` first | `VGAC/zit_3drender_redmond` 0.7 | - | same |
| retro64 | all | zturbo, 8 | section 6.1 text (asset first) | none | `VGAC/zit_ps1_redmond` 0.6, trigger `Playstation 1 Graphics. PS1 Game.` | same |
| lowpoly_flat | all | zturbo, 8 | section 6.1 text, placed **before** the asset prompt | none | - | same |
| clay | all | **zturbo, 8** (was klein, 4) | "Clay animation, Clay." first; never "puppet" or "figure" | none | `VGAC/zit_clay_redmond` 0.6 to 0.7 | same |
| cel | all | anima turbo, 10 | none | none | - | same |
| pixel | humanoid | pixel (Z-Image Turbo + tarn59 1.0 + detector 32 colours), 8 | none | tarn59 1.0 (as today) | - | same |
| pixel | quadruped, props | same | none | tarn59 1.0 | swap to `VGAC/zit_pixelart_redmond` 1.0, trigger `Pixel Art, PixArFK` (chunky sprites) | same |
| realistic_pbr (Photoreal) | all but humanoid | zbase, 30, CFG 4 | keep the current block; add equipment words to the negative (section 7) | none | - | fal 0.9 |
| realistic_pbr (Photoreal) | humanoid | zbase, 30, CFG 4 (or klein, 4: cleaner A-pose, less detail) | new humanoid pose block | none | - | fal 0.9; optional `VGAC/qie2511_turnaround_sheet_tarn59` 1.0 |
| any style | rigid props | as the style | as the style | none | - | fal 0.9; optional `VGAC/qie2511_camera_angle_berkerdooo` 1.0 for shadowless white views |
| any style on klein | humanoid | klein, 4 | new humanoid pose block (section 6.2) | none | - | same |

Settings notes:
- Z-Image Turbo LoRAs stay at 8 steps, CFG 1, res_multistep/simple, shift 3 (no change needed).
- Keep the sum of Z-Image Turbo LoRA strengths at or below about 1.2. The pixel graph already carries one LoRA at 1.0, so never stack a second style LoRA on it.

## 9. Not tested (honest list)

- **AnyPose** (`VGAC/qie2511_anypose_base.safetensors` + `_helper`): downloaded but not run.
  - Z-Image Turbo already gives an A-pose in every painted knight (arm gap 0.96 to 0.98).
  - The new pose block fixes Klein.
  - The 3080 was taken back midway.
  - It is the remaining option for user-supplied art or Anima/zbase knights whose arms touch the body.
- **Trimmed after the switch to the 3060** (seeds and strengths as listed in section 2):
  - the Klein pixel LoRA at 0.9 and 1.2;
  - the turnaround LoRAs at 0.8;
  - the berkerdooo LoRA at its card settings (25 steps, true CFG 3, no Lightning); only the app's Lightning 4-step pipeline was tested;
  - the tarn59 author's suggested 20 percent horizontal stretch (it does not change height ratio or mirror IoU between figures of one sheet);
  - Photoreal (section 7): one seed per painter and subject, and fal without the quarter view on the photo subjects;
  - the turnaround for the Photoreal recipe ran on the `zbase_new` sources, which had softboxes in frame; it was not rerun on the clean `zbase_app` or `klein_v2` sources;
  - Chroma1-HD was not tried for Photoreal (about 190 s per image on the 3060, and the skill rates its adherence weakest).
- **Engines:** after the owner freed the RTX 3080 again, only the four `zbase_app` Photoreal images ran on instance B; everything else after the switch ran on the 3060.
- **Researched but not tested:**
  - Z-Image Turbo: ICONS, ISOMETRIC, Stickers, CuteCartoon Redmond.
  - Qwen-Image-Edit-2511: isometric view (zealman), Material Preview and Studio-DeLight (could help realistic_pbr delighting), Hyperccino PS1 edit, the lrzjason consistency LoRA.
  - Klein: fal spritesheet and background-remove.
  - The Z-Image Turbo ControlNet Union (2 GB).
- **No add-ons exist** for Z-Image base, Chroma1-HD or Anima, so none were tested. Chroma and Z-Image base styles were not re-checked here (only realistic_pbr on zbase).
- **Measures not built:**
  - single subject misses a second object that touches the first (clay v2 showed this);
  - arm gap misses raised arms;
  - there is no automatic measure of style adherence beyond block error and colour count, so style verdicts rest on reading the sheets;
  - no mesh was built from any turnaround, so bakeoff/metrics.py's mesh IoU was not run; the turnaround was scored with the app's own view checks instead (section 5).
- **Seeds:** one or two per cell. The verdicts above are about clear, repeated differences; small metric differences (under about 10 percent) should not be read as real.

## 10. Images

All under `F:/Projects/VideoGameAssetCreator/bakeoff/out/recipes/`:
- Contact sheets (`sheets/`):
  - concept add-ons: `concept_render3d.png`, `concept_clay.png`, `concept_ps1.png`, `concept_pixred.png` (seed 1101 rows), `concept_kleinpix.png`;
  - styles: `style_retro64.png`, `style_lowpoly_flat.png`, `style_clay.png`, `style_cel.png`, `style_pixel.png`, `style_realistic_pbr.png`;
  - pose block: `posefix.png`;
  - turnaround: `turnaround.png` (all methods and subjects), `turn_chest_views.png`, `tmp_knight.png` (knight fal and camera views);
  - Photoreal: `photoreal_final.png`, `photoreal.png`.
- Photoreal concepts: `photoreal/<subject>_<variant>_{img,cut}.png`. Metrics in `photoreal.json`.
- Concept pairs: `concepts/<exp>/<subject>_<seed>_<cond>_img.png`, the cutout `_cut.png`, and the pixel raw `_raw.png`. Metrics in `concepts.jsonl`.
- Style checks: `styles/<style>/<subject>_<seed>_<variant>_{img,cut}.png`. Metrics in `styles.jsonl`.
- Pose block: `posefix/<model>_<seed>_<now|new>_{img,cut}.png`, `posefix.json`.
- Turnaround: `turn/<subject>/source_*.png`, `turn/<subject>/<method>/<view>_{img,cut}.png`; sheets `sheet_img.png` plus split `fig<i>_cut.png`. Subjects: knight, chest, pr_human, pr_lantern, pr_wall, pr_wolf. Metrics in `turn.json`.
- Mean tables: `summary.md`.

Downloaded models (all Apache-2.0, all under `F:/Projects/ComfyUI/models/loras/VGAC/`):

| File | Source |
|---|---|
| zit_3drender_redmond.safetensors | https://huggingface.co/artificialguybr/3DRenderStyle-REDMOND-ZIMAGE |
| zit_clay_redmond.safetensors | https://huggingface.co/artificialguybr/ClayAnimation-Redmond-ZIMAGE |
| zit_ps1_redmond.safetensors | https://huggingface.co/artificialguybr/PS1Graphics-Redmond-ZimageTURBO |
| zit_pixelart_redmond.safetensors | https://huggingface.co/artificialguybr/PIXELART-REDMOND-ZIMAGETURBO |
| klein4b_pixelart_limbicnation.safetensors | https://huggingface.co/Limbicnation/pixel-art-lora |
| qie2511_turnaround_sheet_tarn59.safetensors | https://huggingface.co/tarn59/character_turnaround_sheet_qwen_edit_2511 |
| qie2511_camera_angle_berkerdooo.safetensors | https://huggingface.co/berkerdooo/qwen-image-edit-2511-camera-angle-lora |
| qie2511_anypose_base.safetensors, qie2511_anypose_helper.safetensors | https://huggingface.co/lilylilith/AnyPose |

## 11. Paste blocks (for the coordinator to apply; this bake-off did not edit them)

### 11.1 `backend/vgac/data/addons.yaml`: append under `addons:`

None of these goes into a recipe's default `addons` list: every verdict is optional. They become available through the add-on knob for the models in `base_models`.

```yaml
  - id: stylised_3d
    label: Stylised 3D look
    help:
      meaning: Makes the picture look like a rendered stylised 3D game character or prop.
      why: Gives one consistent toy-like 3D look. It darkens the backdrop a little and can pull arms closer to the body, so use normal or subtle.
      technical: Z-Image Turbo 3DRenderStyle Redmond LoRA, model only, trigger "3D Render Style. 3DRenderAF." Bake-off docs/RECIPES-BAKEOFF.md 4.1.
    file: VGAC/zit_3drender_redmond.safetensors
    base_models: [zturbo]
    stage: concept
    trigger: 3D Render Style. 3DRenderAF.
    strengths: {subtle: 0.5, normal: 0.7, strong: 1.0}
    default_strength: normal
    licence: Apache-2.0
    source: https://huggingface.co/artificialguybr/3DRenderStyle-REDMOND-ZIMAGE
  - id: clay_finish
    label: Hand-made clay finish
    help:
      meaning: Makes clay pictures look more matte and hand-sculpted.
      why: The Clay style already looks like clay; this adds a stronger plasticine finish. Strong loses small details.
      technical: Z-Image Turbo ClayAnimation Redmond LoRA, model only, trigger "Clay animation, Clay." Bake-off 4.2.
    file: VGAC/zit_clay_redmond.safetensors
    base_models: [zturbo]
    stage: concept
    trigger: Clay animation, Clay.
    strengths: {subtle: 0.4, normal: 0.6, strong: 0.8}
    default_strength: normal
    licence: Apache-2.0
    source: https://huggingface.co/artificialguybr/ClayAnimation-Redmond-ZIMAGE
  - id: retro_lowpoly
    label: Late-90s low-poly look
    help:
      meaning: Pushes the picture toward a late-1990s low-polygon game model with small blurry textures.
      why: The best retro look found, and it keeps the backdrop plain. Strong starts to change outfits.
      technical: Z-Image Turbo PS1Graphics Redmond LoRA, model only. Its trigger names a console but drew no lettering in 18 tests. Bake-off 4.3.
    file: VGAC/zit_ps1_redmond.safetensors
    base_models: [zturbo]
    stage: concept
    trigger: Playstation 1 Graphics. PS1 Game.
    strengths: {subtle: 0.4, normal: 0.6, strong: 0.8}
    default_strength: normal
    licence: Apache-2.0
    source: https://huggingface.co/artificialguybr/PS1Graphics-Redmond-ZimageTURBO
  - id: chunky_pixels
    label: Chunky pixel sprites
    help:
      meaning: Bigger, blockier pixels with a smaller palette than the standard pixel-art look.
      why: Looks most like an old sprite on creatures and props. On people it can change the outfit, so the standard pixel look stays the default for characters.
      technical: Z-Image Turbo PixelArt Redmond LoRA, trigger "Pixel Art, PixArFK". For the Pixel Art painter it must REPLACE the built-in pixel_art LoRA (needs code; never stack both). Bake-off 4.4.
    file: VGAC/zit_pixelart_redmond.safetensors
    base_models: [zturbo]
    stage: concept
    trigger: Pixel Art, PixArFK
    strengths: {subtle: 0.7, normal: 1.0, strong: 1.2}
    default_strength: normal
    licence: Apache-2.0
    source: https://huggingface.co/artificialguybr/PIXELART-REDMOND-ZIMAGETURBO
# Done 2026-11-06: both are listed (addons.yaml turnaround_sheet and turnaround_white) and picked with the
# turnaround's turn_method (stages/turnaround.py). The proposal as written:
# The two turnaround LoRAs below need code before they can be listed: each REPLACES qwen_angles (never stack two
# view LoRAs) and uses its own prompts. The sheet makes one 2592x1088 image from a 600x1080 padded cutout, split
# into five figures. The camera LoRA uses relative "Move the camera N degrees ..." prompts on a white composite.
# See bakeoff/recipes/turn_ab.py run_sheet and run_cam.
#  - id: turnaround_sheet
#    label: One-picture character turnaround
#    help:
#      meaning: Draws all five sides of a character in one picture so they match each other.
#      why: The most consistent clothes and details across views for people, in a third of the time. Characters only; it makes the look more photographic.
#      technical: Qwen-Image-Edit-2511 character turnaround sheet LoRA (tarn59) at 1.0 instead of the angles LoRA, prompt "Character turnaround sheet". Bake-off 5.1.
#    file: VGAC/qie2511_turnaround_sheet_tarn59.safetensors
#    base_models: [qwen_edit]
#    stage: turnaround
#    trigger: Character turnaround sheet
#    strengths: {subtle: 0.9, normal: 1.0, strong: 1.1}
#    default_strength: normal
#    licence: Apache-2.0
#    source: https://huggingface.co/tarn59/character_turnaround_sheet_qwen_edit_2511
#  - id: turnaround_white
#    label: Shadow-free prop turnaround
#    help:
#      meaning: Turns a prop on a clean white background with no shadows.
#      why: Shadow-free views help the 3D model's colours; colours match the concept a little less well than the standard turnaround.
#      technical: Qwen-Image-Edit-2511 camera-angle LoRA (berkerdooo) at 1.0 instead of the angles LoRA, relative camera prompts. Rigid props only. Bake-off 5.2.
#    file: VGAC/qie2511_camera_angle_berkerdooo.safetensors
#    base_models: [qwen_edit]
#    stage: turnaround
#    strengths: {subtle: 0.9, normal: 1.0, strong: 1.1}
#    default_strength: normal
#    licence: Apache-2.0
#    source: https://huggingface.co/berkerdooo/qwen-image-edit-2511-camera-angle-lora
```

Not listed: `klein4b_pixelart_limbicnation` (rejected) and AnyPose (untested).

### 11.2 `backend/vgac/data/styles.yaml`: changed fields only

```yaml
  - id: retro64
    # model stays zturbo; replace prompt_block:
    prompt_block: >-
      An in-game real-time 3D model from a late-1990s 64-bit home console game, a low-polygon mesh
      rendered in real time, not a drawing or illustration. Smooth gouraud vertex shading with soft
      glossy specular highlights, slightly angular silhouettes, chunky simplified hands and details,
      blurry low-resolution bilinear-filtered textures, bold saturated colours, bright ambient light
      with one warm key light from the upper left, a soft round blob shadow underneath, the whole image
      slightly soft like a low-resolution frame upscaled. Not built from cubes, no pixel art, no voxels.
  - id: lowpoly_flat
    # model stays zturbo; replace prompt_block. Best results came with this block BEFORE the asset prompt
    # (stages/concept.build_prompt puts the style block second today); a per-style "style_first: true" would need code.
    prompt_block: >-
      A low-poly 3D model rendered in real time with flat shading, built from a few hundred large flat
      triangular facets, every facet one solid colour so each plane shows as a separate tone, sharp
      angular edges everywhere including the face, hair, cloth and wood, no textures and no smooth or
      rounded surfaces. Clean readable silhouette, soft pastel palette, simple soft ambient light.
      No pixel art, no voxels, no fine surface detail.
  - id: clay
    model: zturbo            # was klein: Klein dropped the A-pose in every clay knight
    prompt_block: >-
      Clay animation, Clay. Stop-motion clay style, soft matte sculpted surfaces with fingerprint texture,
      rounded simplified shapes, warm muted colours, as if photographed on a tabletop set.
    # Never add "puppet" or "figure": Z-Image Turbo then adds a second clay figure beside the subject.
  - id: realistic_pbr
    # model, prompt_block and steps unchanged (Z-Image base, 30 steps, CFG 4). Append to recipe.concept.negative:
    #   , softbox, light stand, umbrella light, studio equipment, lamp, tripod
    by_category:
      humanoid:
        caveat: >-
          Photoreal people are the hardest case. The concept picture can look like a real photograph,
          but the 3D model is one shell with one painted skin: eyes, teeth and hair are painted onto the
          surface rather than made separately, and hair becomes a solid shape. So faces look less real
          than clothes and gear, and the face may not stay identical from every side. Check the face in
          the turnaround before building the model.
```

Optional new preset (section 4.1), if a stylised 3D look is wanted:

```yaml
  - id: stylised_3d
    delight_default: keep
    pbr_fix_default: false
    label: Stylised 3D
    model: zturbo
    look: Stylised 3D
    plain: a rendered toy-like 3D look with smooth rounded shapes, full detail for your platform.
    prompt_block: >-
      A stylised 3D game character render, smooth rounded shapes, clean readable forms, soft even
      studio light, bright friendly colours.
    recipe:
      addons: [{id: stylised_3d, strength: normal}]
```

This preset's prompt_block is a suggestion and was not itself tested; the tested prompt was the painted block with the trigger.

### 11.3 `backend/vgac/data/categories/humanoid.yaml`: replace pose_block (section 6.2)

```yaml
pose_block: >-
  The character stands in a relaxed A-pose, both arms straight and angled about thirty degrees out from
  the body with a clear gap between each arm and the torso, palms inward, feet shoulder-width apart,
  facing the camera directly. The whole figure from the top of the head to the soles of the feet fits
  inside the middle four fifths of the image height, with empty background above the head and below
  the feet, centred in frame. Plain flat light grey background. Soft even studio lighting from the
  front with no hard shadows. No text, no logos, no background scenery, no floor shadow, no motion
  blur, no cropping, nothing held in the hands.
```

Watch item: on Z-Image base Photoreal, this block raised one man's arms almost to a T-pose (section 7). That is still rig-ready, but check 2 to 4 more seeds.
