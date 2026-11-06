# First-time setup (a fresh Windows PC)

This is for someone setting the app up on their own Windows PC. It assumes nothing: not drive F:, not two graphics
cards, not anything already installed. The project folder can be anywhere; the helper works out where it is from its
own location. The app was built and tested on one PC (the author's: two NVIDIA cards, 96 GB of RAM), so where a figure
below comes from that PC and not from a test, the text says so.

This lists everything the app needs, in the order to install it. The helper script `setup/Check-Setup.ps1` looks at
the PC first, prints one table (Ready, Missing, or Needs you) and changes nothing. With `-Install` it can then set up
the parts that live entirely inside the project folder. It never touches ComfyUI, the models, Blender, Unity or any
existing settings file. It reads tool paths from `backend/config.yaml` when they exist, and otherwise looks on PATH
and in the usual places (Program Files, the Unity Hub folder, a `tools` folder inside the project).

## The smallest PC that can run it

There is no tested minimum. The repository records only what the author's PC did. Treat the figures as a starting
point, and the "this PC" ones as experience, not a promise.

| Part | Figure | Where it comes from |
|------|--------|---------------------|
| Operating system | Windows 10 (2004) or 11, 64-bit | The helper's own check; not tested on older versions |
| Graphics card | NVIDIA only (CUDA). 10 GB of graphics memory or more is the realistic floor | This PC's experience: the 10 GB RTX 3080 runs the picture and rigging steps and the 12 GB RTX 3060 runs the 3D model step. Nothing in the repository measures the peak memory of a step, and one card running everything has not been tested |
| Free RAM | The job queue holds every job while free RAM is below `min_free_ram_gb`, which is 24 in `backend/config.yaml` | A setting, not a measured need. On this PC (96 GB) it never bites. A PC with 32 GB should still lower the number to leave room for Windows and ComfyUI, and expect slow runs |
| Disk | 150 GB or more is comfortable: models are most of it, plus ComfyUI, the project packages and every asset you make. The helper warns under 20 GB free on the project's drive | An estimate from the sizes in the tables below, not a measurement |
| Internet | Needed for the downloads in sections 2 to 5, and once for the Unity sign-in | n/a |

Run the look-only check (safe to run any time, on any PC):

    powershell -ExecutionPolicy Bypass -File setup\Check-Setup.ps1

Install the project-folder parts (asks before each step; `-Yes` answers yes to all):

    powershell -ExecutionPolicy Bypass -File setup\Check-Setup.ps1 -Install

## Part 2: models, node packs, Blender, packs and settings (`setup/Setup-Extras.ps1`)

A second script does the rest of the list. Same rules as the first: the default mode only looks and changes nothing;
`-Install` acts and asks before every single item; `-Install -DryRun` prints what it would do and does nothing;
`-Yes` answers the plain questions (never a licence question). Name the parts you want, or leave them all on:
`-Models -Nodes -Blender -Packs -Settings`.

    powershell -ExecutionPolicy Bypass -File setup\Setup-Extras.ps1
    powershell -ExecutionPolicy Bypass -File setup\Setup-Extras.ps1 -Models -Nodes -ComfyRoot D:\ComfyUI -Install

`-ComfyRoot` is the ComfyUI folder that holds `models` and `custom_nodes`. The script never lists a models folder
(only exact file paths are looked at).

| Part | What it does | Sources and limits |
|------|--------------|--------------------|
| Models and add-ons | A select list of every model in `models.yaml` and add-on in `addons.yaml` (exact file names), with size, the licence line from the yaml, and the source. Yes or no per item; files already there are skipped; nothing is replaced. Downloads resume after an interruption and are checked against the published size and SHA-256. It shows "Built and tested on 10 GB and 12 GB NVIDIA cards. Less than 10 GB is untested and may fail." and a `[!] Warning` line when `nvidia-smi` shows under 10 GB. `-Only id,id` picks items. Your own additions go in `setup/downloads.local.json` | Links, sizes and checksums are in `setup/downloads.json`, read from the Hugging Face file lists on 6 Nov 2026. Edit a link there; no code change |
| Licence steps | Hunyuan3D-2mv (type "I have read this and I am not in the EU, UK or South Korea"), Anima ("I have read this"), Gemma 3 12B ("I accept the Gemma terms myself", with Google's terms link). You type the sentence; the helper never accepts for you, and `-Yes` skips these items | Hunyuan3D-2mv is off unless you type it |
| Node packs | `ComfyUI-SkinToken`, `ComfyUI-PixelArt-Detector` (GitHub zip) and the project's own `vgac-nodes` (copied from `comfy-nodes/`) into `custom_nodes`. Skips a pack that is there, never overwrites, never restarts ComfyUI, never runs `pip` (it prints the line when a pack has a `requirements.txt`) | Both GitHub repositories' default branch is `main` (checked) |
| Blender | Accepts an existing Blender 5.x (a note says only 5.2.3 is tested), refuses below 5.0, and otherwise fetches 5.2.3 portable into the project's `tools` folder after asking, checking the zip against Blender's own checksum file | Not read back: see "Could not be confirmed" |
| Animation packs | Shows the plain description of each Quaternius pack. You download the zip (itch.io needs a click-through) and give the helper its path (`-Ual1Zip`, `-Ual2Zip`, or it asks); it unpacks into `data/animations/ual1` or `ual2`, keeping the folder names inside the zip | Pages: quaternius.itch.io, linked from quaternius.com |
| Hair packs | A pick list with the pack descriptions; the choice is written to `library/hair-packs.json` | See "Hair packs: plan" |
| Settings | Writes proposed copies `backend/config.proposed.yaml`, `desktop/config.proposed.json`, `runtime/start-comfy-b.proposed.ps1` and `runtime/start-comfy-a.proposed.ps1` from what it finds (project folder, cards, `-ComfyRoot` and `-ComfyInstall`, Blender 5.x, Unity, input and output folders, animation pack paths). Never edits or overwrites an existing file, an earlier proposed one included. A key whose value already fits this PC is left out | One card: engine B only, `stage_instances`, `material_stage_instances` and `judge_instance` all on b, and no engine A start script |

Could not be confirmed (so left to you, or guarded):

1. Blender: blender.org's download server refused an automated request on 6 Nov 2026, so the 5.2.3 link follows
   Blender's standard release layout and was not read back. The script stops without downloading when the checksum
   file is not readable, and says to download from blender.org/download by hand.
2. `anima-turbo-v1.1.safetensors` was not found in the publisher's repository. It is listed as by hand, with the Anima
   page.
3. `z-image-turbo-fp8-aio.safetensors`, `z_image_int8_convrot.safetensors` and the pixel-art LoRA are community
   uploads, and the Gemma 3 file is a Comfy-Org repack. The picker marks them "community or repack" and shows the page.
4. Where ComfyUI looks: the folders used are `checkpoints`, `diffusion_models`, `loras`, `clip_vision`,
   `text_encoders`, `background_removal` (as the Comfy-Org repositories lay them out) and `skintoken` (from the node
   pack's README). `unet` and `clip` are checked as alternate names. A PC that remaps folders with an
   `extra_model_paths` file may need the files moved.
5. The proposed start scripts use `--base-directory` (or `--extra-model-paths-config` with `-ModelPathsConfig`). Not
   tried against a fresh ComfyUI. Read them before using.

Sizes below are approximate. Model sizes are from memory of the publishers' pages and must be confirmed on the
download page before a large download. "Helper" means `Check-Setup.ps1 -Install` does it; "Part 2" means `Setup-Extras.ps1` can do it after asking; "By hand"
means the person does it, and the helper only reports on it.

## 1. The PC itself

| # | Item | What it is for in the app | Size | Where from | Licence | Who installs |
|---|------|---------------------------|------|------------|---------|--------------|
| 1 | Windows 10 (2004) or 11, 64-bit | Everything runs on Windows | n/a | Microsoft | Windows licence | By hand |
| 2 | NVIDIA graphics driver (current Game Ready or Studio driver) | Lets ComfyUI use the graphics cards (CUDA). `nvidia-smi` must work | about 1 GB | nvidia.com/drivers | NVIDIA driver licence (free) | By hand |
| 3 | Free disk space | Models, ComfyUI, assets, exports | 150 GB or more is comfortable (models alone are most of it); the check warns under 20 GB on the project drive | n/a | n/a | By hand |
| 4 | RAM | The job queue holds work while free RAM is below `min_free_ram_gb` (24 in `backend/config.yaml`) | n/a | n/a | n/a | By hand (lower the number for a smaller PC) |

Also needed on a PC that is not the author's: the ComfyUI paths in `runtime/start-comfy-a.ps1` and
`runtime/start-comfy-b.ps1`, `desktop/config.json` (`desktopExe`, `projectRoot`) and several lines of
`backend/config.yaml` still hold the author's paths. Section "What is still needed before other people can install this"
says what a later round does about that. Until then, edit copies of those files by hand, and the helper's
`-Install` writes proposed copies for the project folder path, Blender and Unity.

## 2. Developer tools

| # | Item | What it is for | Size | Where from | Licence | Who installs |
|---|------|----------------|------|------------|---------|--------------|
| 5 | Python 3.13 | Runs the backend (the app's server). The project's own venv (private copy of Python packages) is built from it | about 100 MB | python.org (tick "Add to PATH") | PSF licence (free) | By hand |
| 6 | Node.js LTS with npm | Builds the screens (frontend) and runs the desktop launcher | about 100 MB | nodejs.org | MIT-style (free) | By hand |
| 7 | Git for Windows | Downloading and updating the project | about 60 MB | git-scm.com | GPL-2.0 | By hand |

## 3. The project folder (the helper does these)

| # | Item | What it is for | Size | Where from | Licence | Who installs |
|---|------|----------------|------|------------|---------|--------------|
| 8 | `backend/.venv` plus `backend/requirements.txt` | The Python packages the backend uses (FastAPI, SQLAlchemy, trimesh and so on) | about 1 GB | PyPI, pinned versions in `requirements.txt` | Each package's own open licence | Helper |
| 9 | `frontend/node_modules` and `frontend/dist` (`npm install`, `npm run build`) | The screens you click in; the backend serves `dist` | about 400 MB | npm registry | Each package's own open licence | Helper |
| 10 | `desktop/node_modules` (`npm install`) | The Electron launcher window | about 300 MB | npm registry | Each package's own open licence | Helper |
| 11 | `library/samples/sample.glb` (`backend/scripts/make_sample_glb.py`) | A small sample 3D file for the viewer | under 1 MB | Written locally | n/a | Helper |
| 12 | Hair library (`backend/vgac/data/hairlib`) | Hair styles for characters. It ships inside the project | see the folder | Quaternius Universal Base Characters and MakeHuman hair, plus rule-built styles | CC0 (see `CREDITS.txt` there) | Comes with the project; the helper only checks it |

## 4. Blender, Unity and the animation packs (by hand)

| # | Item | What it is for | Size | Where from | Licence | Who installs |
|---|------|----------------|------|------------|---------|--------------|
| 13 | Blender 5.2.3 (portable zip). Any 5.x works; path in `blender_exe` | Cleans up the mesh, builds LODs, exports FBX and GLB, runs the rig checks | about 400 MB unzipped | blender.org | GPL (a tool the app runs; it does not ship it) | By hand: unzip, then set `blender_exe` in `backend/config.yaml` and the `SKINTOKEN_BLENDER_BIN` line in `runtime/start-comfy-*.ps1` |
| 14 | Unity Hub, then Editor 6000.4.3f1 | Imports the finished asset into the sandbox project and makes the prefab | about 10 GB with Windows build support | unity.com/download | Unity Personal (free for small teams) or Pro. Needs a Unity account and an activated licence | By hand (account, sign-in and licence are yours to do; the helper cannot check the licence) |
| 15 | Animation packs `data/animations/ual1` and `ual2` (Universal Animation Library Standard and 2 Standard, by Quaternius) | Ready-made walk, run and other moves for humanoid characters | tens of MB each | Quaternius's pages (itch.io / quaternius.com); keep the folder names the config expects | CC0 | By hand: download, unzip into `data/animations`, keeping the names in `clips_libraries` |

## 5. ComfyUI, its node packs and the models (by hand)

| # | Item | What it is for | Size | Where from | Licence | Who installs |
|---|------|----------------|------|------------|---------|--------------|
| 16 | ComfyUI Desktop | The engine that runs the picture, 3D and rigging models. The app talks to it over HTTP at the ports in `backend/config.yaml` | about 5 GB with its Python | comfy.org (its own installer) | GPL-3.0 | By hand |
| 17 | Custom node packs: `ComfyUI-SkinToken` (rigging), `vgac-nodes` (the project's own mesh loader, in this repo), plus the packs that supply the TRELLIS.2, Hunyuan3D and background-removal nodes | Parts of the graphs the app sends | small each | ComfyUI Manager, or each pack's GitHub page | Each pack's own licence | By hand. The app's own "Is everything ready?" check lists which node classes are missing |
| 18 | Models, exact file names from `backend/vgac/data/models.yaml` | The picture, side-view, 3D and rigging models | About 60 to 100 GB in all (estimate) | Each model's official page (Hugging Face) | See the next table | By hand |
| 19 | Add-ons (small LoRA files), names from `backend/vgac/data/addons.yaml` | Extra looks (pixel art, clay, PS1 style) and the two the side views need | tens to hundreds of MB each | Hugging Face pages named in `addons.yaml` | Apache-2.0 | By hand |

Model files the check looks for (exact names, no folder is ever listed):

| File | Used for | Engine | Licence | Required |
|------|----------|--------|---------|----------|
| `z-image-turbo-fp8-aio.safetensors` | Z-Image Turbo, the default picture model | B | Apache-2.0 | Yes |
| `z_image_int8_convrot.safetensors` | Z-Image Base | B | Apache-2.0 | Optional |
| `flux-2-klein-4b-fp8.safetensors` | FLUX.2 Klein 4B | B | Apache-2.0 | Optional |
| `anima-turbo-v1.1.safetensors` | Anima Turbo | B | Non-commercial model; its outputs are usable commercially | Optional |
| `pixel_art_style_z_image_turbo.safetensors` | Z-Image Pixel Art | B | Apache-2.0 | Optional |
| `Chroma1-HD-fp8mixed.safetensors` | Chroma1-HD | B | Apache-2.0 | Optional |
| `qwen_image_edit_2511_fp8mixed.safetensors` | Side views and picture edits | B | Apache-2.0 | Yes |
| `qwen-image-edit-2511-multiple-angles-lora.safetensors` | Side views | B | Apache-2.0 | Yes |
| `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` | Fast side views | B | Apache-2.0 | Yes |
| `birefnet.safetensors` | Cutting the picture out | B | MIT | Yes |
| `trellis_2_int8_convrot.safetensors` | TRELLIS.2, builds the 3D model | A | MIT | Yes |
| `Hunyuan3D-2mv.fp16.safetensors` | Hunyuan3D, the other 3D route | A | Tencent Hunyuan Community Licence | Optional |
| `grpo_1400.ckpt` | SkinToken rigging weights | B | MIT | Yes |

Engine A is the one that builds 3D models; engine B does pictures and rigging.

## One graphics card

The app is set up for two NVIDIA cards: engine A (3D models) and engine B (pictures and rigging), each a ComfyUI on
its own card and port. A one-card PC is expected for most people, so the helper reports it as a normal case, not a
failure: with one card of 10 GB or more it shows the card as Ready, with a note; under 10 GB it shows "Needs you"
because that is untested. The model check asks whichever ComfyUI is running, so one engine is enough to be checked.

What the app does for you: it can turn a card off (Health page, "Graphics cards"), and `instance_capabilities` in
`backend/config.yaml` (see the comment block there) says what each card can take over. With one card, work that
preferred the other card runs on it, one graphics step at a time.

What a one-card PC has to get right, not built or tested in this round:

1. One ComfyUI only, and it should be the one the app calls B (port 8189, started by `runtime/start-comfy-b.ps1`).
   Rigging runs on B only: the SkinToken node finds Blender through `SKINTOKEN_BLENDER_BIN`, which that script sets.
   A rig job waits for B instead of moving to A.
2. In the app, switch off the card you do not have. Pictures and the 3D model then use the one card.
3. Memory: a 10 GB card ran the pictures and rigging on the author's PC; a 12 GB card ran the 3D model. Whether one
   10 GB card can run the 3D model is not recorded anywhere in the repository. Treat it as unknown.
4. Run one job at a time; two ComfyUIs sharing one card is likely to fail and the helper flags it.

## Licence rules for the later download step

From the credits work (`backend/vgac/data/credits.yaml`, `CREDITS.md` are the source of truth). The app itself is
GPL-3.0-or-later (`LICENSE`). Decisions on terms are the owner's.

1. Model files are never bundled. Each is downloaded from its original source and used under its own licence. Before
   a download, show the licence line from `models.yaml`.
2. Models the code really uses: Z-Image Turbo and Base, FLUX.2 Klein, Chroma1-HD, Anima, the Z-Image pixel LoRA,
   Qwen-Image-Edit-2511 with its angles and Lightning LoRAs, BiRefNet, TRELLIS.2, SkinToken, Hunyuan3D-2mv (opt-in
   3D engine) and Gemma 3 12B (the local picture judge; optional, and not yet in the helper's model check).
3. Hunyuan3D-2mv: off by default, an explicit opt-in. The Tencent licence is not granted in the EU, UK or South Korea
   (people there must not use it), and a request to Tencent is needed above 1 million monthly users of their products.
   Require "I have read this and I am not in those regions" before download.
4. Anima: the model is non-commercial; what it makes is usable commercially. Show that line and require an explicit
   "I have read this" before download.
5. Gemma 3: Google's Gemma Terms of Use and Prohibited Use Policy; Google claims no rights in what it makes. The
   person must accept the terms themselves; the helper never accepts for them.
6. Unity is not included: each person needs their own install. Checked against Unity's own pages (6 Nov 2026):
   Unity Personal is free for individuals and small organisations with under 200,000 US dollars of revenue and funds
   raised in the last 12 months, and all users in one organisation must be on the same plan (sources:
   unity.com/products/unity-personal, unity.com/pages/license-compliance). Unity's old page text still says 100,000
   in one place; the 200,000 figure is the current one. Say so plainly in setup, and link Unity's page rather than
   quoting the number as ours.
7. ComfyUI node packs beyond ComfyUI: `ComfyUI-SkinToken` (github.com/Rizzlord/ComfyUI-SkinToken; licence not
   confirmed, it defers to the MIT SkinTokens project), `ComfyUI-PixelArt-Detector` (MIT) and `vgac-nodes` (ours,
   GPL-3.0-or-later, source in `comfy-nodes/vgac-nodes/`). `Setup-Extras.ps1 -Nodes` installs these three
   (after asking).

## Optional content packs: hair and animation (the person picks)

Decision: setup should let each person choose which packs to download, with a short description of each, so nothing
is pushed on them and the credit notices follow what they actually took. Built in part: `Setup-Extras.ps1 -Packs` unpacks the two
animation packs (about 91 MB together) from a zip you download, and records a hair pick. Today the hair library (about
16 MB) ships inside the project, so the hair pick does not yet change what is installed or credited. The packs are all CC0 and, with the hair, are the only content that is not a model.

Animation packs (both by Quaternius, from quaternius.com):

| Pack | What it gives | Size here |
|------|---------------|-----------|
| Universal Animation Library (Standard) | The ready-made moves (walk, run, idle and more) for rigged humanoid characters | about 53 MB |
| Universal Animation Library 2 (Standard) | More ready-made moves; uses Unreal-style bone names, which the app maps itself | about 38 MB |

Hair packs (26 styles in the library now, all CC0; each style in the app has a plain name and a thumbnail):

| Pack | Styles | What it gives |
|------|--------|---------------|
| Quaternius Universal Base Characters (Standard), hair part | 5 | Simple game-ready styles: two buns, buzz cut, rounded buzz cut, shoulder-length layered, short side-parted |
| MakeHuman system assets (MakeHuman team) | 7 | Afro, bob with a fringe, single braid, long straight waist-length, mid ponytail, two short styles |
| MakeHuman community hair pack hair01: Cortu | 4 | Shaggy and curly shoulder-length, short and messy, straight with a full fringe |
| MakeHuman community hair pack hair01: Faydaen | 1 | Short and spiky |
| MakeHuman community hair pack hair01: MargaretToigo | 6 | Bob family: blunt, curled under, inverted, each with and without a fringe |
| VGAC rule-built styles (made by the app, no download) | 3 | Top bun, long straight with a centre parting, high ponytail |

Left out on purpose, so the list stays CC0-only: the hair01 styles under AGPL-3.0 or CC BY, beards, and styles too
heavy for a game character (see `backend/vgac/data/hairlib/CREDITS.txt`, "Left out").

### Hair packs: plan

Done: the credits follow the picks. Every catalogue style carries `pack` and `author`, and `setup/downloads.json` maps
each pack above to that pair. `Setup-Extras.ps1 -Packs -Install` writes the picked packs to `library/hair-packs.json`;
`hairstyles.py` (the one loader the hair picker, the fitting and the credits page all use) then hides styles from packs
that were not kept, after the backend restarts. No file, or an unreadable one, means every style, as before. Tests:
`backend/tests/test_hair_packs.py`.

Not done: shipping each pack as its own download, so unpicked packs are not on disk at all. The pack sources, read from
the official pages on 6 Nov 2026 and equal in size to the zips the library was built from (kept in `runtime/hairpacks`):

| Pack | Source | Size |
|------|--------|------|
| MakeHuman system assets | https://files.makehumancommunity.org/asset_packs/makehuman_system_assets/makehuman_system_assets_cc0.zip (page: static.makehumancommunity.org/assets/assetpacks/makehuman_system_assets.html) | 280,737,770 bytes |
| MakeHuman hair01 (Cortu, Faydaen, MargaretToigo are all in this one zip) | https://files.makehumancommunity.org/asset_packs/hair01/hair01_cc0.zip (page: .../assetpacks/hair01.html) | 227,775,853 bytes |
| Quaternius Universal Base Characters (Standard) | quaternius.itch.io/universal-base-characters: click-through, you download it yourself | 128,968,391 bytes |

Those zips are far bigger than the 16 MB of hair the app keeps from them, because the library holds baked, trimmed
copies (`tools/hairlib_build.py`), so a per-pack download would be a packaged form of the baked files, not these zips.
That is the part left to do.

Size: medium. No licence question: all are CC0.

## What is still needed before other people can install this

Ranked by how much each blocks a stranger; size is the rough effort (small, medium, large).
"Owner decision" means the owner has to choose before it can be built.

| Rank | What | Size | Owner decision |
|------|------|------|----------------|
| 1 | How the app is handed over: DECIDED, a public Git repository. Still to do inside that: the licence notices a public repository needs (the credits work already under way feeds this) | Small | No |
| 2 | DONE as proposed copies (`Setup-Extras.ps1 -Settings`). Settings files written from what the helper finds, instead of this PC's paths: `backend/config.yaml`, `desktop/config.json` and the two `runtime/start-comfy-*.ps1` scripts (ComfyUI install folder, Windows user name, input and output folders, Blender path). The helper already finds Blender, Unity and the project folder; it needs to write proposed copies for the rest | Medium | No (rule already set: proposed copies, never overwrite) |
| 3 | A tested one-card setup: run every step on one card, record the real graphics memory each model and step needs (a "needs about N GB" figure per model in `models.yaml`, measured, not guessed), fix the stage plan and `instance_capabilities` defaults, and give one-card PCs their own start script. The smallest card the app claims to support is then set by the hungriest required model, which is not necessarily the smallest file | Large (needs real hardware: a 10 GB and ideally a smaller card) | Decided: a plain "your mileage may vary" disclaimer, not a full hardware spec and no benchmarking of each model. Wording: "Built and tested on 10 GB and 12 GB NVIDIA cards. Less than 10 GB is untested and may fail." |
| 4 | DONE (`Setup-Extras.ps1 -Models`, `-Nodes`). Fetching ComfyUI's node packs and the models from their official sources as a select list: size, licence and a yes or no per item, with a warning mark on any model that is likely too big for this PC's card (using the figures from item 3). Models are never bundled: each person downloads them under the model's own licence (some are not plain Apache-2.0: Anima is non-commercial, Hunyuan3D has its own community licence). Depends on item 3 for the warnings | Large (many sources, big downloads, resume and checksum handling) | Decided: the app ships with no models at all; setup is the only way they arrive. It offers a select list of the models the app uses today (the table above) and stays open to the person's own additions, without naming further models or adding a licence clause for each. The warning marks are just the 10 GB disclaimer, no per-model figures |
| 5 | DONE, link not read back (`Setup-Extras.ps1 -Blender`). Blender portable fetched into the project's own `tools` folder, so no other Blender is touched and the path is known. Plan: fetch 5.2.3 (the version the app was built on) for new installs; accept an existing Blender 5.x with a disclaimer that only 5.2.3 is tested; refuse below 5.0 (the app's own check already requires 5.0 or newer). Other versions are not tested and not promised | Small | No: decided (disclaimer only, no version testing) |
| 6 | Animation picker DONE; hair pick recorded only (see "Hair packs: plan"). The pick-what-you-want hair and animation pack list described above (CC0 downloads, one description per pack, credits from the picks), and a check that the Unity sandbox project is complete | Medium | No: decided |
| 7 | Installing Python, Node and Git for people who lack them (offering `winget` commands, asking first) | Small | No |

Unity stays by hand whatever is built: it needs the person's own account and licence.

## Still by hand

What stays manual, what a later round of the helper could automate, and what it should never touch:

| Part | Why it stays by hand now | A later round could | It should never |
|------|--------------------------|---------------------|-----------------|
| ComfyUI and its node packs | Own installer; the install folder and the Windows user name appear in `runtime/start-comfy-*.ps1` | Write proposed copies of the start scripts with this PC's paths; list missing node packs from the app's own check and print the Manager steps | Install, update or restart ComfyUI; change its settings |
| Models and add-ons | Large downloads from many sources, some with their own terms | Download the missing named files from their official pages, after asking, with size and licence shown, into the exact folder the loader names | List, open, move or delete anything else in a models folder; replace a file that is already there |
| Blender | Portable zip; only a path | Download the official 5.2.3 zip into a chosen folder after asking, and propose the path in a settings copy | Touch another Blender install |
| Unity | Needs an account and a licence you activate | Detect the Editor in Unity Hub's folder and propose `unity_exe`; open the Hub download page | Sign in, activate a licence or accept terms for you |
| One-card setups | Needs a decision about the stage plan and a test on real hardware | Detect one card and write a proposed `instance_capabilities` and `stage_instances` copy | Edit `backend/config.yaml` directly |
| Python, Node, Git, the graphics driver | System-wide installs | Offer `winget` commands, after asking | Install without asking or upgrade an existing one |

Settings files: the helper never edits `backend/config.yaml` or `desktop/config.json`. When the project folder path
differs, `-Install` writes `backend/config.proposed.yaml` and `desktop/config.proposed.json` beside them. Paths it
does not propose and you must set by hand: the ComfyUI paths in the two start scripts, `desktopExe` in
`desktop/config.json`, `comfy_input_dir`, `loras_dir` and the Blender path.

After setup, start the app (desktop shortcut or `backend/run.ps1`) and open "Is everything ready?" in the app for the
deeper check of ComfyUI node packs and models.
