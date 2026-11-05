# Recipes bake-off: add-ons and per-style settings for concept art and turnaround

Date: 2026-11-04. Scope: free, commercially usable add-ons (mostly LoRAs) for the concept models (Z-Image Turbo, Z-Image base, FLUX.2 Klein 4B, Anima, Chroma1-HD) and the turnaround model (Qwen-Image-Edit-2511 + Lightning 4 step + fal multiple-angles LoRA). It also checks, with no add-on, whether each style preset in `backend/vgac/data/styles.yaml` delivers its look.

Scripts: `bakeoff/recipes/` (`rlib.py`, `concepts.py`, `styles_check.py`, `posefix.py`, `turn_ab.py`, `summarise.py`, `queue_a.sh`). Images: `bakeoff/out/recipes/` (gitignored). No file under backend/, frontend/, desktop/ or unity-sandbox/ was changed.

## 1. Summary

Verdicts (section 4 has the numbers):

| Add-on (file in `models/loras/VGAC/`) | Verdict | Strength |
|---|---|---|
| `zit_3drender_redmond.safetensors` | Optional (new "stylised 3D" look), not default | 0.7 |
| `zit_clay_redmond.safetensors` | Optional for clay; the prompt alone does most of it | 0.6 to 0.7 |
| `zit_ps1_redmond.safetensors` | Optional for retro64 (best retro look found) | 0.6 |
| `zit_pixelart_redmond.safetensors` | Optional "chunky sprite" variant for creatures and props; keep the current pixel LoRA as default | 1.0 |
| `klein4b_pixelart_limbicnation.safetensors` | Reject | - |
| TURN_VERDICTS |

Top three recipe changes:
1. **Clay style: switch the model from Klein to Z-Image Turbo, and put "Clay animation." as the first words of the block.** Klein ignored the A-pose in 10 of 10 clay knights: the arm-gap measure was 0.00 to 0.02 against 0.60 to 0.85 on Z-Image Turbo. Z-Image Turbo clay looked as clay-like with no LoRA. Never write "puppet" or "figure" in the block: it added a second clay figure next to the chest in 2 of 2 seeds.
2. **Humanoid pose block: say the arm angle and the headroom explicitly** (text in section 6.2). On Klein it turned arms-at-the-sides knights into a clear A-pose in 4 of 4 seeds and removed most of the floor shadow. On Z-Image Turbo it was neutral (no regressions).
3. **Retro64 and lowpoly_flat: rewrite the blocks so the medium is a physical thing, and offer the PS1 LoRA at 0.6 for retro64.** The current retro64 block gives cube-headed voxel figures, the "Minecraft" drift the skill warns about. The current lowpoly_flat block gives smooth surfaces on everything except the head.

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
    - AnyPose was dropped (section 8).

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

TURN_SECTION

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
- **clay** (switch the model to **zturbo**; put "Clay animation." first and keep the current text):
  > Clay animation. Stop-motion clay style, soft matte sculpted surfaces with fingerprint texture, rounded simplified shapes, warm muted colours, as if photographed on a tabletop set.

  Never use "puppet" or "figure" (an extra figure appeared). Optional: the clay LoRA at 0.6 to 0.7 for a more hand-made finish.
- **cel, pixel, realistic_pbr**: keep as they are. For realistic_pbr, ending the block with "seamless plain light grey studio backdrop, no floor shadow" might lower bg std 18 (untested).

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

## 7. Proposed recipe table

"Pose block" means the category pose block, with the humanoid text of section 6.2. Turnaround: TURN_RECIPE.

| Style | Category | Model, steps | Prompt block change | Default add-ons | Optional add-ons | Turnaround |
|---|---|---|---|---|---|---|
| painted | all | zturbo, 8 | none | none | - | see section 5 |
| painted (new "stylised 3D" preset) | all | zturbo, 8 | trigger `3D Render Style. 3DRenderAF.` first | `VGAC/zit_3drender_redmond` 0.7 | - | same |
| retro64 | all | zturbo, 8 | section 6.1 text (asset first) | none | `VGAC/zit_ps1_redmond` 0.6, trigger `Playstation 1 Graphics. PS1 Game.` | same |
| lowpoly_flat | all | zturbo, 8 | section 6.1 text, placed **before** the asset prompt | none | - | same |
| clay | all | **zturbo, 8** (was klein, 4) | "Clay animation." first; never "puppet" or "figure" | none | `VGAC/zit_clay_redmond` 0.6 to 0.7 | same |
| cel | all | anima turbo, 10 | none | none | - | same |
| pixel | humanoid | pixel (Z-Image Turbo + tarn59 1.0 + detector 32 colours), 8 | none | tarn59 1.0 (as today) | - | same |
| pixel | quadruped, props | same | none | tarn59 1.0 | swap to `VGAC/zit_pixelart_redmond` 1.0, trigger `Pixel Art, PixArFK` (chunky sprites) | same |
| realistic_pbr | all | zbase, 30, CFG 4 | optional: "no floor shadow" (untested) | none | - | same |
| any style on klein | humanoid | klein, 4 | new humanoid pose block (section 6.2) | none | - | same |

Settings notes:
- Z-Image Turbo LoRAs stay at 8 steps, CFG 1, res_multistep/simple, shift 3 (no change needed).
- Keep the sum of Z-Image Turbo LoRA strengths at or below about 1.2. The pixel graph already carries one LoRA at 1.0, so never stack a second style LoRA on it.

## 8. Not tested (honest list)

- **AnyPose** (`VGAC/qie2511_anypose_base.safetensors` + `_helper`): downloaded but not run.
  - Z-Image Turbo already gives an A-pose in every painted knight (arm gap 0.96 to 0.98).
  - The new pose block fixes Klein.
  - The 3080 was taken back midway.
  - It is the remaining option for user-supplied art or Anima/zbase knights whose arms touch the body.
- **Trimmed after the switch to the 3060** (seeds and strengths as listed in section 2):
  - the Klein pixel LoRA at 0.9 and 1.2;
  - the turnaround LoRAs at 0.8;
  - the berkerdooo LoRA at its card settings (25 steps, true CFG 3, no Lightning); only the app's Lightning 4-step pipeline was tested;
  - the tarn59 author's suggested 20 percent horizontal stretch (it does not change height ratio or mirror IoU between figures of one sheet).
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

## 9. Images

All under `F:/Projects/VideoGameAssetCreator/bakeoff/out/recipes/`:
- Contact sheets (`sheets/`):
  - concept add-ons: `concept_render3d.png`, `concept_clay.png`, `concept_ps1.png`, `concept_pixred.png` (seed 1101 rows), `concept_kleinpix.png`;
  - styles: `style_retro64.png`, `style_lowpoly_flat.png`, `style_clay.png`, `style_cel.png`, `style_pixel.png`, `style_realistic_pbr.png`;
  - pose block: `posefix.png`;
  - turnaround: `turnaround.png`.
- Concept pairs: `concepts/<exp>/<subject>_<seed>_<cond>_img.png`, the cutout `_cut.png`, and the pixel raw `_raw.png`. Metrics in `concepts.jsonl`.
- Style checks: `styles/<style>/<subject>_<seed>_<variant>_{img,cut}.png`. Metrics in `styles.jsonl`.
- Pose block: `posefix/<model>_<seed>_<now|new>_{img,cut}.png`, `posefix.json`.
- Turnaround: `turn/<subject>/source_*.png`, `turn/<subject>/<method>/<view>_{img,img_cut}.png`; sheets `sheet_img.png` plus split `fig<i>_cut.png`. Metrics in `turn.json`.
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

If any is adopted, add it to `backend/vgac/data/models.yaml` with its licence.
