# Credits

Video Game Asset Creator is built on the work below. This page is information, not legal advice. Licences are the authors' own terms; read the linked ones if a lot depends on it.

## If you share the app with other people, what you must do

- Keep THIRD-PARTY-NOTICES.md (every package that ships with the app, with its licence) and CREDITS.md with the app. The licences of the libraries (MIT, BSD and similar) require their notices to travel with it.
- The AI models are not shipped with the app. Each person downloads them, so each model's own licence applies to that person, including Hunyuan3D, Anima and Gemma (Google's Gemma Terms of Use, with a use policy).
- Blender and ComfyUI are GPL (a licence that requires sharing changes to the program itself) tools. The app calls them but does not include them. If you ever bundle either one, its licence text and source offer must come with it.

## Before you share the app (decisions that are yours, not settled here)

- The app has no licence of its own yet (desktop/package.json says UNLICENSED). Choose one before sharing; until then other people have no stated right to use or copy it.
- Blender is GPL. The app's own Blender scripts (backend/vgac/blender/) use Blender's Python interface. Blender's licence page says of such scripts, "(if published) are being shared under a GPL compliant license" (https://www.blender.org/about/license/). Whether that applies to these scripts, and what licence to pick, is yours to decide, ideally with advice.
- ComfyUI is GPL-3.0 and the app's own node pack (vgac-nodes) runs inside it. The same question applies and is open.
- Models are not shipped with the app: each person downloads them and each model's own licence applies to that person. Hunyuan3D (revenue clause, regional limits) and Anima (model non-commercial) deserve a read before anyone relies on them.

## What you must credit in a game made with these assets

- As recorded, nothing that ends up inside an exported asset needs a credit. The animations and hairstyles are CC0 (public domain dedication: free to use with no conditions), and the AI models' licences cover the model files, not the pictures and 3D shapes they make for you.
- Crediting is still polite, and this page lists everyone so you can copy it.
- Two exceptions are recorded. Hunyuan3D (the optional second 3D model) is under the Tencent Hunyuan Community Licence, which has a revenue clause and regional limits (it does not cover every country); read it before shipping anything made with it. Anima (the anime-style picture model) is a non-commercial model, but what it makes is usable commercially.
- Unity is its own licence (Personal or Pro): the game you build in it falls under Unity's terms, not this app's.

## AI models (the trained files that make pictures and 3D shapes)

These run on your computer. Their files are not part of your game; what they make is.

| Name | What it does here | Author | Licence | Credit required | In your assets |
|---|---|---|---|---|---|
| [Z-Image Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo) | Paints the first concept picture quickly. | Tongyi-MAI (Alibaba) | Apache-2.0 | no | no |
| [Z-Image base](https://huggingface.co/Tongyi-MAI/Z-Image) | Paints the concept picture at higher quality, more slowly. | Tongyi-MAI (Alibaba) | Apache-2.0 | no | no |
| [FLUX.2 Klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) | Paints quick draft concept pictures. | Black Forest Labs | Apache-2.0 | no | no |
| [Anima turbo](https://huggingface.co/circlestone-labs/Anima) | Paints anime line-art concept pictures. | circlestone-labs | Non-commercial model, outputs usable commercially (The model is non-commercial; what it makes may be used commercially. Do not sell or host the model itself.) | no | no |
| [Z-Image Turbo + pixel art LoRA](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo) | Z-Image Turbo with a pixel-art add-on built in, for blocky pixel concept pictures. | Tongyi-MAI (Alibaba) with a community pixel-art add-on | Apache-2.0 | no | no |
| [Chroma1-HD](https://huggingface.co/lodestones/Chroma1-HD) | Paints loose, painterly concept pictures. | lodestones | Apache-2.0 | no | no |
| [Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511) | Edits a picture and draws the side, back and other views of your concept (the turnaround). | Qwen team (Alibaba) | Apache-2.0 | no | no |
| [Qwen-Image-Edit-2511 multiple-angles LoRA](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA) | Helps the side-view model turn the character to exact camera angles. | fal | Apache-2.0 | no | no |
| [Qwen-Image-Edit-2511 Lightning 4 steps](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) | Lets the side-view model finish each view in 4 passes instead of many. | lightx2v | Apache-2.0 | no | no |
| [BiRefNet](https://github.com/ZhengPeng7/BiRefNet) | Cuts the subject out of its background. | Peng Zheng and co-authors (BiRefNet) | MIT | no | no |
| [TRELLIS.2](https://github.com/microsoft/TRELLIS.2) | Builds the 3D model with its colours from the pictures. The default 3D model. | Microsoft | MIT | no | no |
| [Hunyuan3D-2mv](https://huggingface.co/tencent/Hunyuan3D-2mv) | Optional second 3D model that builds the shape from several views. | Tencent | Tencent Hunyuan Community Licence (Opt-in. Community licence with a revenue clause and regional limits: read it before shipping anything made with it.) | no | no |
| [SkinToken](https://github.com/VAST-AI-Research/SkinTokens) | Rigs the 3D model: builds its skeleton and works out how the surface follows each bone. | VAST-AI-Research | MIT | no | no |

## Add-ons (LoRAs: small files that steer a model toward a look)

Each one is loaded on top of a model above.

| Name | What it does here | Author | Licence | Credit required | In your assets |
|---|---|---|---|---|---|
| [Pixel-art LoRA](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo) | Pushes the image model toward crisp 16-bit pixel art with a small palette. | community add-on for Z-Image Turbo | Apache-2.0 | no | no |
| [Lightning LoRA (fast turnaround)](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) | Lets the side-view model (Qwen-Image-Edit) finish each side view in 4 quick passes instead of 40. | lightx2v | Apache-2.0 | no | no |
| [Multiple-angles LoRA (camera angles)](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA) | Teaches the side-view model (Qwen-Image-Edit) to turn the camera around your asset for the side and back views. | fal | Apache-2.0 | no | no |
| [Stylised 3D look](https://huggingface.co/artificialguybr/3DRenderStyle-REDMOND-ZIMAGE) | Makes the picture look like a rendered stylised 3D game character or prop. | artificialguybr | Apache-2.0 | no | no |
| [Hand-made clay finish](https://huggingface.co/artificialguybr/ClayAnimation-Redmond-ZIMAGE) | Makes clay pictures look more matte and hand-sculpted. | artificialguybr | Apache-2.0 | no | no |
| [Late-90s low-poly look](https://huggingface.co/artificialguybr/PS1Graphics-Redmond-ZimageTURBO) | Pushes the picture toward a late-1990s low-polygon game model with small blurry textures. | artificialguybr | Apache-2.0 | no | no |
| [Chunky pixel sprites](https://huggingface.co/artificialguybr/PIXELART-REDMOND-ZIMAGETURBO) | Bigger, blockier pixels with a smaller palette than the standard pixel-art look. | artificialguybr | Apache-2.0 | no | no |

## Ready-made data (moves, light pictures)

Files that come with the app. Moves can end up inside your characters.

| Name | What it does here | Author | Licence | Credit required | In your assets |
|---|---|---|---|---|---|
| [Universal Animation Library (Standard)](https://quaternius.com) | The ready-made moves (walk, run, idle and more) your rigged characters can use. | Quaternius | CC0 1.0 | no | yes |
| [Universal Animation Library 2 (Standard)](https://quaternius.com) | More ready-made moves for rigged characters. | Quaternius | CC0 1.0 | no | yes |
| [Light picture: Outdoors (kloofendal_48d_partly_cloudy)](https://polyhaven.com/a/kloofendal_48d_partly_cloudy) | The Outdoors lighting choice in the 3D viewer, the preview picture and the Unity showcase. | Greg Zaal (Poly Haven) | CC0 1.0 | no | no |
| [Light picture: Overcast (overcast_soil)](https://polyhaven.com/a/overcast_soil) | The Overcast lighting choice in the viewer and previews. | Sergej Majboroda (Poly Haven) | CC0 1.0 | no | no |
| [Light picture: Night (dikhololo_night)](https://polyhaven.com/a/dikhololo_night) | The Night lighting choice in the viewer and previews. | Greg Zaal (Poly Haven) | CC0 1.0 | no | no |

## Hairstyles (the hair library)

Every style is CC0 (public domain dedication) and is fitted onto characters you export.

| Name | What it does here | Author | Licence | Credit required | In your assets |
|---|---|---|---|---|---|
| [Hairstyle: Two buns](https://quaternius.com) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | Quaternius | CC0-1.0 | no | yes |
| [Hairstyle: Buzz cut](https://quaternius.com) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | Quaternius | CC0-1.0 | no | yes |
| [Hairstyle: Buzz cut, rounded hairline](https://quaternius.com) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | Quaternius | CC0-1.0 | no | yes |
| [Hairstyle: Shoulder-length, layered](https://quaternius.com) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | Quaternius | CC0-1.0 | no | yes |
| [Hairstyle: Short, side-parted with volume](https://quaternius.com) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | Quaternius | CC0-1.0 | no | yes |
| [Hairstyle: Short and messy](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | Cortu | CC0-1.0 | no | yes |
| [Hairstyle: Straight with a full fringe](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | Cortu | CC0-1.0 | no | yes |
| [Hairstyle: Shaggy, shoulder-length](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | Cortu | CC0-1.0 | no | yes |
| [Hairstyle: Curly, shoulder-length](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | Cortu | CC0-1.0 | no | yes |
| [Hairstyle: Short and spiky](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | Faydaen | CC0-1.0 | no | yes |
| [Hairstyle: Chin-length bob, side parting](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | MargaretToigo | CC0-1.0 | no | yes |
| [Hairstyle: Chin-length bob with a fringe](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | MargaretToigo | CC0-1.0 | no | yes |
| [Hairstyle: Bob, curled under](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | MargaretToigo | CC0-1.0 | no | yes |
| [Hairstyle: Bob, curled under, with a fringe](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | MargaretToigo | CC0-1.0 | no | yes |
| [Hairstyle: Inverted bob, longer at the front](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | MargaretToigo | CC0-1.0 | no | yes |
| [Hairstyle: Inverted bob with a fringe](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | MargaretToigo | CC0-1.0 | no | yes |
| [Hairstyle: Afro](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | MakeHuman team (Data Collection AB) | CC0-1.0 | no | yes |
| [Hairstyle: Bob with a side-swept fringe](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | MakeHuman team (Data Collection AB) | CC0-1.0 | no | yes |
| [Hairstyle: Long, straight, waist-length](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | MakeHuman team (Data Collection AB) | CC0-1.0 | no | yes |
| [Hairstyle: Mid ponytail, swept back](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | MakeHuman team (Data Collection AB) | CC0-1.0 | no | yes |
| [Hairstyle: Short, textured](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | MakeHuman team (Data Collection AB) | CC0-1.0 | no | yes |
| [Hairstyle: Short with a side-swept fringe](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | MakeHuman team (Data Collection AB) | CC0-1.0 | no | yes |
| [Hairstyle: Single braid with a side fringe](http://www.makehumancommunity.org) | A hairstyle you can give a character. It is fitted to the head and exported with the character. | MakeHuman team (Data Collection AB) | CC0-1.0 | no | yes |
| Hairstyle: Long, straight, centre parting | A hairstyle you can give a character. It is fitted to the head and exported with the character. | VGAC | CC0-1.0 | no | yes |
| Hairstyle: High ponytail | A hairstyle you can give a character. It is fitted to the head and exported with the character. | VGAC | CC0-1.0 | no | yes |
| Hairstyle: Top bun | A hairstyle you can give a character. It is fitted to the head and exported with the character. | VGAC | CC0-1.0 | no | yes |

## Tools (outside programs the app calls)

None of these is included in the app.

| Name | What it does here | Author | Licence | Credit required | In your assets |
|---|---|---|---|---|---|
| [Blender](https://www.blender.org/about/license/) | Does the heavy 3D work: cleaning the mesh, fitting hair, building detail levels and collision shapes, and drawing preview renders. | Blender Foundation | GPL-3.0-or-later (a GPL tool; what you make with it is yours) (Called as a separate program; not included in this app.) | no | no |
| [Unity Editor](https://unity.com/legal) | Imports the finished asset in a sandbox project to prove it works, and makes the Unity showcase. | Unity Technologies | Unity Personal or Pro terms (Your game falls under Unity's terms for the plan you use.) | no | no |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | The engine that runs every AI model on your graphics card. The app sends it a recipe and gets the picture or 3D model back. Its built-in parts also supply the nodes used for the 3D steps and the picture check. | Comfy Org and contributors | GPL-3.0 (Called over its local web address; not included in this app.) | no | no |
| [ComfyUI-SkinToken](https://github.com/Rizzlord/ComfyUI-SkinToken) | The ComfyUI add-on (custom node pack) that lets ComfyUI run the SkinToken rigging model. | Rizzlord | not confirmed (The pack has no licence file and says to read the SkinTokens project's terms, which are MIT. Not confirmed as its own licence.) | no | no |
| vgac-nodes (VGACLoadTrimesh, VGACSaveTrimesh) | Two small ComfyUI nodes written for this app to load and save 3D files. | This project | not confirmed (Your own code; no licence is recorded for it yet. Choose one before passing it on.) | no | no |
| [Gemma 3 12B (instruction tuned)](https://ai.google.dev/gemma/terms) | The local vision model that looks at pictures and judges them for the review checks and hairstyle choice. Runs inside ComfyUI. | Google DeepMind | Gemma Terms of Use (Google's own terms, with a Prohibited Use Policy) (Google states it claims no rights in what you generate with it. The terms must be accepted by whoever downloads the model.) | no | no |

## Software libraries (code the app is built from)

Their licence notices must travel with the app if you pass it on.

| Name | What it does here | Author | Licence | Credit required | In your assets |
|---|---|---|---|---|---|
| [FastAPI](https://fastapi.tiangolo.com) | The web server framework behind the app's backend. | Sebastian Ramirez | MIT | no | no |
| [Uvicorn](https://www.uvicorn.org) | Runs the backend and serves it to the browser. | Encode | BSD-3-Clause | no | no |
| [SQLAlchemy](https://www.sqlalchemy.org) | Keeps your projects, assets and versions in the local database. | Mike Bayer and contributors | MIT | no | no |
| [Pydantic](https://docs.pydantic.dev) | Checks that the data the app handles has the right shape. | Samuel Colvin and contributors | MIT | no | no |
| [HTTPX](https://www.python-httpx.org) | Lets the backend talk to ComfyUI over its local address. | Encode | BSD-3-Clause | no | no |
| [websockets](https://websockets.readthedocs.io) | The live-updates channel between ComfyUI and the app. | Aymeric Augustin and contributors | BSD-3-Clause | no | no |
| [PyYAML](https://pyyaml.org) | Reads the app's recipe and settings files. | Kirill Simonov and contributors | MIT | no | no |
| [Pillow](https://python-pillow.org) | Opens, resizes and saves pictures. | Jeffrey A. Clark and contributors | MIT-CMU | no | no |
| [psutil](https://github.com/giampaolo/psutil) | Reads how much memory and processor the computer has free. | Giampaolo Rodola | BSD-3-Clause | no | no |
| [trimesh](https://trimesh.org) | Reads, checks and repairs 3D mesh files. | Michael Dawson-Haggerty | MIT | no | no |
| [NumPy](https://numpy.org) | The number-crunching behind mesh and picture measurements. | NumPy developers | BSD-3-Clause | no | no |
| [CoACD](https://github.com/SarahWeiii/CoACD) | Builds the simple collision shapes (colliders) around a 3D model. | Xinyue Wei and co-authors | MIT | no | no |
| [pytest](https://pytest.org) | Runs the app's own automatic tests (development only). | pytest developers | MIT | no | no |
| [React](https://react.dev) | Draws every screen of the app. | Meta and contributors | MIT | no | no |
| [React DOM](https://react.dev) | Puts the React screens in the browser window. | Meta and contributors | MIT | no | no |
| [React Router](https://reactrouter.com) | Moves between pages (Projects, Gallery, Credits and so on). | Remix Software | MIT | no | no |
| [three.js](https://threejs.org) | Shows your 3D model in the viewer. | mrdoob and contributors | MIT | no | no |
| [Vite](https://vite.dev) | Builds and serves the app's screens (development and build only). | Evan You and contributors | MIT | no | no |
| [Vite React plugin (@vitejs/plugin-react)](https://github.com/vitejs/vite-plugin-react) | Lets Vite build the React screens. | Vite team | MIT | no | no |
| [oxlint](https://oxc.rs) | Checks the screen code for mistakes (development only). | Oxc project | MIT | no | no |
| [TypeScript](https://www.typescriptlang.org) | Type-checks the screen code (development only). | Microsoft | Apache-2.0 | no | no |
| [Electron](https://www.electronjs.org) | The desktop window the app opens in. | OpenJS Foundation and contributors | MIT | no | no |
| [electron-builder](https://www.electron.build) | Packs the desktop app into an installer. | Vladimir Krivosheev and contributors | MIT | no | no |

Generated by tools/make_credits.py from backend/vgac/data/credits.yaml, models.yaml, addons.yaml and the hair library catalogue. Do not edit by hand.
