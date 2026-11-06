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

Sizes below are approximate. Model sizes are from memory of the publishers' pages and must be confirmed on the
download page before a large download. "Helper" means `Check-Setup.ps1 -Install` does it; "By hand" means the person
does it, and the helper only reports on it.

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

## What is still needed before other people can install this

Not built in this round. Ranked by how much each blocks a stranger; size is the rough effort (small, medium, large).
"Owner decision" means the owner has to choose before it can be built.

| Rank | What | Size | Owner decision |
|------|------|------|----------------|
| 1 | How the app is handed over at all: a zip of the project, a Git repository, or an installer. Everything below depends on it (where files live, how updates arrive, what the licence notices say) | Small to decide, medium to build (large for a true installer) | Yes: which of the three, and whether the project is public |
| 2 | Settings files written from what the helper finds, instead of this PC's paths: `backend/config.yaml`, `desktop/config.json` and the two `runtime/start-comfy-*.ps1` scripts (ComfyUI install folder, Windows user name, input and output folders, Blender path). The helper already finds Blender, Unity and the project folder; it needs to write proposed copies for the rest | Medium | No (rule already set: proposed copies, never overwrite) |
| 3 | A tested one-card setup: run every step on one card, record the real memory each step needs, fix the stage plan and `instance_capabilities` defaults, and give one-card PCs their own start script | Large (needs real hardware: a 10 GB and ideally a smaller card) | Yes: the smallest card the app will claim to support |
| 4 | Fetching ComfyUI's node packs and the models from their official sources, showing size and licence and asking yes or no for each item. Models are never bundled: each person downloads them under the model's own licence (some are not plain Apache-2.0: Anima is non-commercial, Hunyuan3D has its own community licence) | Large (many sources, big downloads, resume and checksum handling) | Yes: whether to automate at all, and which optional models are offered |
| 5 | Blender portable fetched into the project's own `tools` folder, so no other Blender is touched and the path is known | Small | Yes: pin to 5.2.3 or allow any 5.x |
| 6 | A one-click path for the animation packs (CC0 downloads into `data/animations`) and a check that the Unity sandbox project is complete | Small | No |
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
