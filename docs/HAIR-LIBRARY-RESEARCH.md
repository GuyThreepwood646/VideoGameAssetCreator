# A hairstyle library fitted to the bald head: research

Research date: 2026-11-05. Follows `docs/HAIR-RESEARCH.md` (read in full first; its basics are not repeated). The question: instead of asking the 3D builder for hair, keep a LIBRARY of ready-made hairstyle meshes, pick the closest one to the character's picture, fit it to the clean bald head the builder already makes, and adjust it to match.

Rules applied: free, usable in commercial games, local, no paid tools. Nothing was downloaded and nothing in the project was changed. Three short read-only Blender 5.2.3 runs (no files written) checked what Blender can do headless; their results are marked "(tested here)". "(secondary)" means read only in a search extract or a third-party page, not on the source's own page. "(not confirmed)" means not established at all. Several sites refused automated readers on the day (OpenGameArt, VRoid, pixiv help, the Blender manual), so some facts about them are secondary.

---

## Summary

1. **The studio method works without paid tools, and Blender does every step headless.** Studios never fit hair to an unknown head: every head shares one reference skull, and hair is authored once on it. We can copy that: keep every library hairstyle on one reference head, bend that reference head onto each bald head, and let the hair follow with Surface Deform (sticks one mesh to another surface). Surface Deform binding, Shrinkwrap, hair curves, the bundled hair node groups and curve-to-mesh conversion all ran headless here.
2. **Free hairstyles for commercial use exist, but the pool is small.** Best first: Quaternius Universal Base Characters (CC0, 5 hairstyles free, 20 for 19.99 US dollars; 122 MB; solid stylised shells), the MakeHuman Hair 01 pack (CC0, 25 styles, 217 MB) and the MakeHuman system hair (CC0, 10 styles, 267 MB). VRoid presets, Mixamo, Ready Player Me and MetaHuman are out.
3. **The library should grow by rule, not by download.** Blender's bundled hair node groups (CC0, 32 groups in 5.2.3) plus our own clump builder can make solid stylised hairstyles from a few guide curves, with sliders as parameters.
4. **Pick the style in two steps:** the local vision model names attributes (length, fringe, parting, tied, curl), which filters the library; then outline overlap with the picture's hair mask from front and side ranks what is left and tunes length and volume.
5. **Most likely to go wrong:** bending the reference head onto a lumpy builder head (ears, leftover hair bumps); skin poking through at temples and nape; fringe stretched on a high forehead; and a picture whose hair is simply not in the library.

---

## 1. Sources of ready-made hair meshes

### 1.1 Every source checked

"Shell" = a solid sculpted shape. "Cards" = flat strips with see-through hair textures. Triangle counts are given only where a page states them.

| Source | URL | Licence, as stated, and where | Format | Styles | Triangles | Shell or cards | Download | Account | Suits |
|---|---|---|---|---|---|---|---|---|---|
| **Quaternius, Universal Base Characters** | https://quaternius.itch.io/universal-base-characters ; https://quaternius.com/packs/universalbasecharacters.html | CC0. itch page: "Creative Commons Zero v1.0 Universal". Site: "Free to use in personal, educational and commercial projects." | FBX, glTF, OBJ; .blend only in the paid Source | Standard (free): 5 hairstyles and 2 bodies. Source (19.99 US dollars, also CC0): 20 hairstyles, 8 bodies | "Average 13k tris" for whole characters; hair alone not stated | Solid stylised shells (from the preview pictures; not measured) | Standard 122 MB; Source 600 MB | No (itch "name your own price") | Stylised and painted; flat cel. Phone-friendly |
| **Quaternius, Background Posed Humans** | https://quaternius.com/packs/backgroundposedhumans.html | CC0; "free to use in personal and commercial projects." | FBX, OBJ, Blend | 8 hairstyles on 20 posed people | Not stated | Solid low-poly shells (not measured) | Not stated | No | Low-poly flat, stylised |
| Quaternius, Ultimate Modular Men / Women, Modular Character Outfits Fantasy | https://quaternius.com/packs/ultimatemodularwomen.html ; https://quaternius.com/packs/modularcharacteroutfitsfantasy.html | CC0, same wording | FBX, OBJ, Blend, glTF | Each character is 4 swappable parts; whether hair is one of them is not stated (not confirmed). Outfits fit the Universal Base heads | Not stated | Not confirmed | Not stated | No | Low-poly stylised |
| **MakeHuman Community, Hair 01** | https://static.makehumancommunity.org/assets/assetpacks/hair01.html ; licence heading on https://static.makehumancommunity.org/assets/assetpacks.html | Every asset listed as "CC0". Packs page heading: "Mesh assets shared under CC0:" | MakeHuman proxy files (.mhclo plus .obj plus textures) in a zip | 25 (bobs, braids, buns, an anime style, messy, bangs) by 9 authors | Not stated. Described as "mostly low-poly and stylized" | Mixed; not confirmed per asset | 217 MB | No | Stylised to semi-real |
| **MakeHuman system assets (hair part)** | https://static.makehumancommunity.org/assets/assetpacks/makehuman_system_assets.html | Each item "CC0"; heading "System assets, shared under CC0:" | Same | 10: afro01, bob01, bob02, braid01, long01, ponytail01, short01 to 04 | Not stated | MakeHuman's default hair uses alpha-textured layers (not confirmed per asset) | 267 MB (whole system pack) | No | Semi-real |
| MakeHuman Hair 02 and Hair 03 | https://static.makehumancommunity.org/assets/assetpacks/hair02.html ; .../hair03.html | "Mesh assets shared under CC-BY:" (credit required) | Same | 21 high-poly (Elvaerwyn); 14 mixed | Hair 02 "mostly high-poly" | Mixed | 68 MB; 38 MB | No | Realistic. Usable with credit; not a first choice |
| MakeHuman hair editor pack | https://static.makehumancommunity.org/assets/assetpacks/haireditor.html | Packs page says the functional assets "are shared under CC0" (the subpage itself states none) | Two .blend files of geometry-nodes hair and fur | Templates | n/a | Strand curves, not meshes | 18 MB | No | Realistic strands; a reference for building by rule |
| MPFB2 (MakeHuman for Blender) | https://github.com/makehumancommunity/mpfb2 | Code GPL-3.0 (`LICENSE.CODE.md`); assets CC0 (`LICENSE.ASSETS.md` is the CC0 text) | Blender add-on | Loads the packs above | n/a | n/a | n/a | No | Supports Blender 4.2 and newer; a fix for 5.1 is logged; 5.2 not confirmed (secondary) |
| **Blender bundled hair node groups (Essentials)** | Inside Blender: `5.2/datafiles/assets/nodes/procedural_hair_node_assets.blend` | CC0: the folder's `LICENSE` file reads "CC0 1.0 Universal" (read locally in 5.2.3) | Node groups, not hairstyles | 32 groups (list in section 3) | n/a | Strand curves; can be turned into a mesh | Already installed | No | Builds any look; see section 3 |
| Blender Studio, Human Base Meshes 1.4.1 | https://www.blender.org/download/demo-files/ | "49 MB – CC0" on that page | .blend | No hair found (not confirmed by opening) | Not stated | n/a | 49 MB | No | Useful as a clean reference skull, not as hair |
| OpenGameArt single CC0 pieces | https://opengameart.org/content/short-punk-hair-style-for-female-model ; the "CC0 Character Customization" collection https://opengameart.org/node/132423 ; https://opengameart.org/content/short-dreads | CC0 per search extracts (secondary; the site refused the reader) | .blend, zip | About 5 to 8 single styles (punk, ponytail, upcomb, side parting, long, dreads) | Punk 374, a male style 253 (secondary) | Solid low-poly | Small | No | Low-poly flat |
| Toon / Low Poly Dread Ponytail set (tiko) | https://endthegame.itch.io/toon-ponytail-dreadlock-set | "CC0 free to use in any project!" | FBX | Not stated (a set) | 88,000 (too heavy as is) | Solid toon | 19 MB | No | Toon; would need decimating |
| Kenney (Mini Characters, Blocky Characters) | https://kenney.nl/assets/mini-characters-1 | CC0 (secondary) | GLB, FBX | Hair appears fused into each character (not confirmed) | Very low | Shell | Small | No | Not a hair source |
| Polygonal Mind 100Avatars | https://github.com/PolygonalMind/initiative-opensource-release | Page: "free to use, modify, and distribute our work without any restrictions". An older README asks that the avatars not be sold as they are (secondary) | FBX, VRM | About 200 avatars | Low | Hair as a separate mesh not confirmed | Not stated | No | Low-poly stylised; licence wording conflicts, so not first |
| Poly Pizza | https://poly.pizza | Per model; no CC0 hair confirmed | GLB | Not found | | | | No | Not confirmed |
| Sketchfab | https://sketchfab.com/search?features=downloadable&type=models | Search has a licence filter that includes CC0 (secondary); each model must be checked | glTF and others | No good CC0 hair pack found | | | | **Yes, a login to download** | Not confirmed |
| itch.io, GitHub | | Beyond the two above, no CC0 hair library was found; most hits are paid packs (CGTrader, Superhive, Fab) | | | | | | | |
| VRoid Studio presets | https://vroid.com/en/studio/guidelines | Exported models and preset items may be used and sold commercially unless an item adds special terms (secondary). **But** pixiv's help says VRoid Studio may not be used to build an application that deforms or combines its meshes to generate avatars or items, except for your own use (secondary) | VRM | Dozens of presets | | Cards-like strips | | Account for VRoid Hub | **Out.** VGAC would be exactly that kind of application |
| Ready Player Me | https://en.wikipedia.org/wiki/Ready_Player_Me | Service shut down on 31 January 2026 after Netflix bought the company (secondary) | | | | | | | **Out** |
| Mixamo characters | https://helpx.adobe.com/be_en/creative-cloud/faq/mixamo-faq.html | Royalty-free in games, but raw character files may not be redistributed or sold as assets (secondary, from Adobe's FAQ and community FAQ) | FBX | Hair fused into each character | | | | Adobe account | **Out.** The app would be handing out the raw files |
| MetaHuman grooms | Epic | Strand or card hair for Unreal; realistic; tied to the MetaHuman head (secondary) | Alembic, uasset | | | | | Epic account | **Out** for this app |
| CharMorph | https://github.com/Upliner/CharMorph ; https://charmorph-docs.readthedocs.io/en/main/ | Code GPL; content keeps each author's licence: Vitruvian CC0, Antonia CC-BY, MB-Lab AGPL (secondary) | Blender add-on | Hair is particle strands | n/a | Strands | | No | Realistic strands only; out |

### 1.2 What stands out

1. **CC0 game hair is scarce.** Almost every polished stylised hairstyle pack online is paid. The free pool is about 5 to 20 Quaternius shells, 35 MakeHuman CC0 styles and a handful of OpenGameArt pieces. A library big enough to cover what people describe will have to be grown by rule (section 3) with the downloads as seeds and as a quality bar.
2. **MakeHuman brings its own fitting data.** A MakeHuman hair (.mhclo) stores every hair vertex as a weighted mix of three vertices of the MakeHuman base mesh plus an offset (secondary, from the MakeHuman file-format notes [S3]). That is exactly the "authored once on a reference skull" idea. All 35 CC0 styles already sit on one shared head, which can serve as our reference skull.
3. **Style match matters as much as licence.** Quaternius shells are the closest to VGAC's painted, cel and low-poly looks and to phone budgets. MakeHuman hair leans semi-real and often relies on see-through textures, which the earlier research ruled out as a default.

### 1.3 Ranked shortlist: try these first

| Rank | Name | URL | Licence | Size | Account | Why |
|---|---|---|---|---|---|---|
| 1 | Quaternius Universal Base Characters, Standard | https://quaternius.itch.io/universal-base-characters | CC0 | 122 MB | No | Solid stylised shells: the right kind of hair for our looks and phone budgets. Comes with its own base head, which can be the reference skull. Only 5 free styles; the 20-style Source costs 19.99 US dollars (CC0 too, so allowed if the owner chooses to pay). |
| 2 | MakeHuman Hair 01 | https://static.makehumancommunity.org/assets/assetpacks/hair01.html | CC0 | 217 MB | No | The most free styles in one place (25), all on one shared base head with fitting data built in. Mesh kind and triangle counts must be checked after download. |
| 3 | MakeHuman system assets (10 hairs plus the base mesh) | https://static.makehumancommunity.org/assets/assetpacks/makehuman_system_assets.html | CC0 | 267 MB | No | The base mesh and 10 everyday styles. Needed anyway if rank 2 is used, since its hairs are fitted to this head. |

Not a download but worth listing beside them: Blender's own hair node groups (CC0, already on disk) as the parts kit for building styles by rule.

---

## 2. How one hairstyle is fitted to many heads

### 2.1 What the tools and games do

The common thread: **nobody fits hair to an arbitrary head at run time.** Every product keeps one reference skull; heads differ only by morphs (shape changes stored on the same mesh) or bones, and the hair carries matching morphs or follows the same bones.

| Product | How hair follows the head | Source |
|---|---|---|
| Unreal groom binding | A groom is tied to a skeletal mesh by a binding asset. Transfer to a different head assumes the two heads share the same UV layout; the hair roots are carried across by UV. An optional source mesh lets the roots be wrapped when they do not line up. An optional RBF (radial basis function: a smooth warp driven by a few sample points) "preserves the original groom shape under large skeletal mesh deformations", usually with about 100 samples or fewer. Users report hair landing under the chin when the head is not MetaHuman topology. | [F1], [F2] |
| Unreal Mutable, Mesh Reshape | Clothing authored on one body is reused on other body shapes: each garment vertex is bound to the nearest face of a "base shape", then moved with the matching face of the "target shape"; optional smoothing; vertex colours can weight the effect. This is the same idea as Blender's Surface Deform driven by a shape key. | [F3] |
| MetaHuman | All heads share one topology, so grooms bind directly. Custom heads must first be converted to MetaHuman topology. | [F1], [F2] |
| Daz Studio autofit | Clothing and hair made for one figure are projected onto another through "clone" shapes (the old figure's shape built on the new figure's mesh); a projection template supplies bones and weight maps, and a smoothing modifier with collision iterations removes poke-through. | [F4], [F5] |
| Character Creator (Reallusion) | All heads share the CC base topology. Head hair has no skin weights by default, so the hairline can poke into the head when the face morphs; the fix is to mark hairline vertices so they follow the head's morphs. Imported hair can distort when moved to another character. | [F6], [F7] |
| The Sims | Hair is weighted to head bones and follows head-shape sliders; mods break when hair is weighted to the wrong bone (secondary, modding forums). | [F8] |
| VRoid Studio | One template head for everyone; hair is drawn as guide strokes on it and regenerated as strip meshes; face sliders mostly change the face, not the skull (secondary). | [F9] |
| Games with character creators | Hairstyles are swapped as whole objects, head and body sliders are blend shapes (shape keys) or bone scales, and hats trigger a hidden "hat" shape on the hair (one devlog). | [F10] |

### 2.2 The methods, and what Blender 5.2 offers headless

| Method (plain words) | What it does | Blender 5.2, headless through Python | Pitfalls |
|---|---|---|---|
| **Reference skull plus surface binding** (wrap) | Author hair on head A. Bend head A until it matches head B (keeping A's mesh). Hair glued to A's surface comes along. | **Surface Deform** modifier on the hair, target = reference head; bind with `bpy.ops.object.surfacedeform_bind` inside `context.temp_override`. Then change the reference head (shape key or edited vertices); the hair follows. **(tested here:** a hair shell bound to a sphere followed a 1.3 times widening and 0.9 times squash.) | Binding fails on targets with concave faces, edges shared by more than two faces, or overlapping vertices (the modifier's own error messages); fewer target vertices after binding stops it. Hair far from the surface (long tails) swings with distant faces and can stretch. |
| **Bending the reference head onto the bald head** (registration) | Makes head A take head B's shape without changing A's mesh. | Three steps: (1) line up and scale from landmarks (crown, ear tops, brow, nape, chin) found on the bald head; (2) **RBF warp** from those landmarks: no Blender node for it and no SciPy in Blender's Python, but NumPy 2.3.4 is bundled **(tested here)** and a 20 to 60 point RBF solve is a few lines; (3) **Shrinkwrap** (pulls one surface onto another) with `NEAREST_SURFACEPOINT`, restricted to a "cranium" vertex group, then a **Corrective Smooth** pass. | Shrinkwrap snaps into ears, nose and leftover hair bumps; restrict it to the skull top and back. A builder head that still carries a hair bulge drags the reference skull up and the hair floats. |
| **Cage** (Mesh Deform) | A coarse closed box around the hair drives it. | **Mesh Deform** modifier, bind with `bpy.ops.object.meshdeform_bind` (operator present). | The cage must be closed; binding is slow at high precision; the cage itself still has to be fitted to the head, so it only moves the problem. |
| **Lattice** | A 3D grid of control points bends everything inside. | **Lattice** modifier; lattice points settable from Python. | Good for whole-style changes (taller, wider, longer at the back), poor at local fit. |
| **Shrinkwrap push-out** | Pushes any hair vertex that sits inside the head back outside it. | Shrinkwrap with `wrap_mode` `OUTSIDE` or `OUTSIDE_SURFACE` and a small offset, limited to the inner layer of the hair (vertex group). The enum values exist in 5.2's RNA. | Applied to the whole style it flattens volume into a helmet. Use it only as a final repair on the inner layer. |
| **Shape keys** (blend shapes) | Stored alternative shapes on the same mesh. | Fully scriptable; exported to FBX and glTF as morph targets. | Each slider is authored once per style; sliders that fight each other (length and volume) need test pairs. |
| **Geometry nodes** | Any of the above as a node graph. | Sample Nearest Surface, Raycast and the bundled **Shrinkwrap Hair Curves** and **Attach Hair Curves to Surface** groups; for curve hair, **Deform Curves on Surface** moves hair curves with their surface (node present in 5.2). | Node inputs change between versions; pin the version. |
| **Data Transfer** | Copies skin weights from the head to the hair. | Data Transfer modifier, nearest face interpolated. | Long hair that passes the shoulders picks up shoulder weights; clamp to head and neck bones. |

### 2.3 Known faults and their usual fixes

| Fault | Cause | Fix |
|---|---|---|
| Skin poking through | Head wider or taller than the reference in places | Push-out on the inner layer; a painted scalp cap under the hair so a small gap shows hair colour, not skin |
| Stretched fringe | High or sloping forehead pulls the fringe roots apart | Fit the fringe as its own part bound to the brow region; or keep a "short fringe" shape key |
| Hairline gap at temples and nape | Hair edge sits off the skin after warping | Tuck the hair edge 2 to 4 mm into the head; paint the hairline on the head texture |
| Floating hair | Leftover hair bump on the "bald" head | Measure the gap; reject the bald build or flatten the skull top before fitting |
| Long hair through shoulders | Hair below the neck bound to the head only | Split: head part bound to the skull; hanging part skinned and given spring chains |

---

## 3. Building hairstyles by rule

### 3.1 How artists do stylised and anime hair from curves

The common recipe (tutorials [B1], [B2], [B3]): each lock is one curve with a bevel profile (a flat rectangle, almond or crescent cross-section) and a taper curve that narrows it to a point; locks are laid from the crown outward in layers (back, sides, fringe); then each curve is turned into a mesh and UV-unwrapped as a straight strip. A known snag: the default bevel narrows evenly on all sides, which makes needle-like fringe tips; anime fringe needs a flattened profile that narrows only in width [B3]. A second school models flat strips and bends them onto the head with Curve, Simple Deform and Lattice modifiers [B4]. Both end with a solid shell made of separate locks, which is what VGAC wants.

### 3.2 How VRoid makes hair

Hair is grouped (fringe, back, sides). In each group the user draws guide strokes on a template head; each stroke becomes a strip mesh with settings for width, thickness, curl and tip shape, and "procedural" groups spread many locks round the head from a few settings (secondary [F9]). Hair textures are mapped root at the top, tip at the bottom of a straight strip (secondary [B5]). HairDeck (already covered) learned from such VRoid hair.

### 3.3 What Blender 5.2.3 offers, checked here

The bundled hair file holds 32 node groups **(tested here, listed headless)**: Attach Hair Curves to Surface, Blend, Braid, Clump, Curl, Displace, Duplicate, Frizz, Generate, Interpolate, Noise, Roll, Rotate, Shrinkwrap, Smooth, Straighten and Trim Hair Curves, Set Hair Curve Profile, Redistribute Curve Points, Restore Curve Segment Length, Create Guide Index Map, and helpers (Curve Info, Curve Root, Curve Tip, Curve Segment, Attachment Info, Get/Set Attachment Surface, Get Hair Surface Geometry, Get Rest Geometry, Rest Surface). Licence CC0 (local `LICENSE` file).

**Headless, from Python, into a normal mesh: works (tested here).** A script loaded "Curl Hair Curves" from the bundled file with `bpy.data.libraries.load`, made 40 strands, ran them through Curl and Curve to Mesh, and got a mesh two ways: (a) a mesh "carrier" object whose node graph pulls the curves in with Object Info, read with `bpy.data.meshes.new_from_object` (33,600 faces); (b) `bpy.ops.object.convert(target='MESH')` on the curves object itself. Reading the curves object directly with `new_from_object` fails ("Object does not have geometry data"), so use (a) or (b). Curl's inputs in 5.2.3: Factor, Subdivision, Curl Start, Radius, Factor Start, Factor End, Frequency, Random Offset, Seed, Guide Index, Guide Distance, Guide Mask, Existing Guide Map.

**The catch:** these groups make realistic strands (thousands of thin curves). For game shells we want a few dozen fat locks. The useful route is our own small builder: a few guide curves per lock (from a style recipe), Clump/Curl/Braid applied to the guides only, then each guide swept with a fat tapered profile (Curve to Mesh with a crescent profile and a radius that narrows to the tip), with UVs written along the strip. Locks of 6 to 10 segments and 6 to 8 profile points give about 70 to 160 triangles each; 40 locks is 3,000 to 6,000 triangles before decimation (estimate).

### 3.4 Sliders in character creators, and how they are built

| Slider | Usual implementation |
|---|---|
| Style | Swap the whole mesh (Unity tutorials, most games) [F10] |
| Length | A separate mesh per length, or a shape key; bone scaling on hair chains in some games (not confirmed for any named game) |
| Volume | Shape key pushing locks out along the normal |
| Fringe on/off, parting side | Separate parts, or a mirrored copy |
| Curl, braid | Usually a different style, not a slider |
| Hat | Hidden "hat hair" shape key or a separate short mesh [F10] |
| Colour | Shader tint, not geometry (section 5) |

For VGAC: **regenerate by rule** where we build the style (curl, lock count, length are parameters of the builder), and **shape keys** for downloaded shells (length, volume and fringe keys made once per style by the same builder logic or by hand). Shape keys also travel to Unity as blend shapes, so a game could expose them.

---

## 4. Choosing the style and settings from a picture

| Method | What it gives | Practical here? | Licence |
|---|---|---|---|
| **Vision-language model attributes** | Asks the local model for JSON: length (above ear / chin / shoulder / mid-back), fringe (none / side / full), parting, tied (ponytail / bun / braid), curl, volume, hair colour | **Yes, first filter.** The app already runs Gemma 3 12B locally as its judge (365 s cold on the 3080, `STATUS.md`); Qwen3-VL 8B is the other candidate in `docs/PHOTOREAL-PLAN.md` | Qwen3-VL Apache-2.0 (per that doc); Gemma under Google's Gemma terms (not re-checked here) |
| **Zero-shot image matching** (SigLIP or CLIP) | Similarity between the character picture and renders of each library style | Yes, as a tie-breaker; style of rendering must match the picture's look | SigLIP 2 base, Apache-2.0, 0.4 billion parameters [R5] |
| **Outline overlap** (silhouette IoU: shared area over combined area) | Renders each candidate's hair outline from front and side on our fitted head, compares with the hair mask from the concept and side view | **Yes, main ranker.** The masks already exist (`face.hair_and_skin`, the MediaPipe or SAM 2 masks of the earlier research). Needs no GPU: project vertices and fill triangles with NumPy and Pillow | Own code |
| **Chamfer distance on outlines** | Average distance between the two outline curves | Yes, better than IoU at telling a short fringe from none | Own code |
| **Fitting by search** | A small grid or Nelder-Mead search over scale, length and volume shape keys to maximise IoU | Yes, a few hundred cheap renders | Own code |
| **Differentiable rendering** (the renderer reports how to change the shape to match) | Gradient-based fitting | Possible but not needed for a few parameters. **nvdiffrast is non-commercial** ("research or evaluation purposes only") [R3]; PyTorch3D is BSD [R4] | |
| Trained hairstyle classifiers | | No commercially clean, ready model found | |
| Published retrieval work | Hu et al. 2015 retrieves from a database of 343 hairstyles by matching 2D strand directions plus user strokes [R1]; Digital Salon (2025) retrieves by CLIP over text captions of 1,320 styles and says it does not yet support image-guided retrieval [R2] | Method ideas only. The data behind Hu et al. (USC-HairSalon) is research-only through its derivative Hair20k [R6]; Digital Salon's code licence not confirmed | Not usable data |

Order: attributes filter the library (a ponytail never ranks against a bob), outline overlap from front and side ranks the rest, image matching breaks ties, a short search tunes the shape keys. If the best overlap is poor (say under 0.6), say so to the user and offer the "build by rule" route or the current fused hair, rather than forcing a wrong style.

---

## 5. Tricks for game hair as its own mesh

"Shell" = solid stylised hair; "Cards" = see-through strips.

| Trick | What it is | Shell | Cards |
|---|---|---|---|
| Painted scalp cap | Hair colour (and root shading) painted on the head under the hair, so any gap shows hair, not skin. Card artists use a dense base layer or bake scalp hair into the head texture for the same reason [T1] | **Yes**, and it hides fitting gaps | Yes |
| Straight-strip UV layout | Every lock unwrapped as a vertical strip, root at the top. Diagonal strips give jagged lines [T2]; VRoid maps root to top [B5]. Flow direction is then known everywhere, so strand lines and highlights are simple to paint or shade | **Yes**, the main gain of building by rule | Yes |
| Grey texture tinted in the shader | Texture holds value only; colour comes from a tint and a root-to-tip gradient. Reallusion's root map is black at roots, white at ends [T3]; an ID map in grey levels lets each strand group get its own shade [T4]; packed maps should use smooth gradients or edges show [T5] | **Yes**: one library texture serves every hair colour; tint from the picture's hair colour | Yes |
| Ambient occlusion in vertex colours | Darker under layers, inner faces and the underside, baked once per fitted style | **Yes**, cheap on phones | Yes |
| Highlight band ("angel ring") | Unity Toon Shader's angel ring is a screen-fixed highlight driven by a texture on a second UV set [T6]; Anime Shading Plus uses a mask channel the same way [T7]; otherwise the Kajiya-Kay shader we already have | **Yes** (needs the straight strips) | Yes |
| Back faces | Shells are closed or have their inner faces kept, so no two-sided material is needed | **Yes, none needed** | Cards need two-sided or doubled faces |
| Hat or helmet variant | A "hat" shape key or a cropped short variant per style [F10]; or hide hair entirely under full helmets (existing `Socket_Head` rules) | Yes | Yes |
| Levels of detail | LOD1: decimate and drop small locks; LOD2: merge to one cap. Unity LOD Group | Yes | Cards dropped first |
| Hairline on the forehead | Stylised games keep a hard mesh edge, tuck it slightly into the skin, paint a soft shadow under the fringe on the face texture and match the scalp colour to the root colour | **Yes** | Cards use alpha fade |
| Ponytails and braids that swing | A separate lock group from the tie outward with one edge ring per bone; a 3 to 5 bone chain parented to the head; capsules on neck and shoulders (springs already planned in `HAIR-RESEARCH.md`, option f) | Yes | Yes |
| Phone budgets (Unity Built-in) | One opaque hair material (no cut-out: see-through pixels are costly on phone GPUs [I14 in HAIR-RESEARCH.md], secondary), one draw call, shells around 1,500 to 3,000 triangles (estimate), vertex-colour shading instead of an extra texture | **Yes** | Cards are a poor fit for phones |

---

## 6. Recommendation

**Given:** a bald head mesh, a hair outline mask from the picture, Blender 5.2 headless, no paid tools.

**Most promising build, in order:**

1. **Pick one reference skull and put the library on it.** Use the Quaternius Universal Base head (if rank 1 is taken) or the MakeHuman base mesh (if rank 2 and 3 are taken; their hairs are already fitted to it). Every style is stored on that head with: hair mesh with straight-strip UVs and a grey texture, an inner-layer vertex group, a fringe group, shape keys (length, volume, fringe), front and side outline pictures, and attribute tags.
2. **Bend the reference skull onto the bald head.** Landmarks from the existing head finder, a NumPy RBF warp, Shrinkwrap on the cranium group, Corrective Smooth. Save as a shape key on the reference skull. Check it: the gap between warped skull and bald head under 3 mm over the hair area.
3. **Carry the hair across** with Surface Deform bound to the reference skull (tested here), then the push-out on the inner layer.
4. **Choose:** vision-model attributes filter; front and side outline overlap ranks; tune shape keys by a short search.
5. **Finish:** tint from the picture's hair colour, root-to-tip gradient, vertex-colour occlusion, Kajiya-Kay or painted highlight, painted scalp cap on the head, skin weights by Data Transfer, spring chains for anything that hangs.
6. **In parallel, start the rule-built library:** our own lock builder on the bundled CC0 node groups, so the library can grow past the 35 to 55 free styles and match VGAC's look exactly.

**Try first, small:** five bald characters, five Quaternius styles, steps 2 and 3 only, and look at the temples, nape and fringe in Unity.

**Most likely to go wrong:**

1. Registration on builder heads: ears, uneven skull tops and leftover hair bulges pull the reference skull out of shape. Needs a cranium-only mask and a gap check that rejects bad fits.
2. Poke-through and hairline gaps at temples and nape, worst on wide or narrow heads far from the reference.
3. Fringe stretching on high foreheads.
4. The picture's hairstyle is not in a 5 to 50 style library; the outline score must be honest and the app must offer a fall-back.
5. Look mismatch: semi-real MakeHuman hair on a painted cel character looks wrong; the rule-built library solves this, the downloads do not.
6. Hair masks from stylised art: MediaPipe is trained on photos; colour-split fall-backs are needed (already noted in `HAIR-RESEARCH.md`).

---

## Not confirmed

1. Triangle counts and shell-or-cards for each MakeHuman and Quaternius hairstyle (need opening after download).
2. Whether Quaternius Ultimate Modular packs include separate hair parts.
3. The VRoid application restriction and Mixamo redistribution terms were read only in search extracts of the official pages.
4. OpenGameArt licences and counts (site refused the reader).
5. MPFB2 on Blender 5.2.
6. Gemma's terms for this use; Digital Salon's code licence.
7. Every time and triangle figure marked "(estimate)".

---

## Sources

Licences and assets
- [A1] Quaternius Universal Base Characters: https://quaternius.itch.io/universal-base-characters ; https://quaternius.com/packs/universalbasecharacters.html
- [A2] Quaternius Background Posed Humans: https://quaternius.com/packs/backgroundposedhumans.html ; Ultimate Modular Women: https://quaternius.com/packs/ultimatemodularwomen.html ; Outfits Fantasy: https://quaternius.com/packs/modularcharacteroutfitsfantasy.html
- [A3] MakeHuman asset packs (licence headings): https://static.makehumancommunity.org/assets/assetpacks.html ; Hair 01: https://static.makehumancommunity.org/assets/assetpacks/hair01.html ; Hair 02: https://static.makehumancommunity.org/assets/assetpacks/hair02.html ; Hair 03: https://static.makehumancommunity.org/assets/assetpacks/hair03.html ; system assets: https://static.makehumancommunity.org/assets/assetpacks/makehuman_system_assets.html ; hair editor: https://static.makehumancommunity.org/assets/assetpacks/haireditor.html
- [A4] MPFB2: https://github.com/makehumancommunity/mpfb2 ; https://raw.githubusercontent.com/makehumancommunity/mpfb2/master/LICENSE.CODE.md ; https://raw.githubusercontent.com/makehumancommunity/mpfb2/master/LICENSE.ASSETS.md ; versions: https://extensions.blender.org/add-ons/mpfb/versions/
- [A5] Blender bundled assets licence: local file `F:\Tools\blender-5.2.3-windows-x64\5.2\datafiles\assets\LICENSE` ; Human Base Meshes: https://www.blender.org/download/demo-files/
- [A6] OpenGameArt: https://opengameart.org/content/short-punk-hair-style-for-female-model ; https://opengameart.org/node/132423 ; https://opengameart.org/content/short-dreads (secondary)
- [A7] Toon ponytail dreadlock set: https://endthegame.itch.io/toon-ponytail-dreadlock-set
- [A8] Kenney Mini Characters: https://kenney.nl/assets/mini-characters-1 (secondary)
- [A9] Polygonal Mind: https://github.com/PolygonalMind/initiative-opensource-release ; https://github.com/gfodor/100avatars (secondary)
- [A10] VRoid guidelines: https://vroid.com/en/studio/guidelines ; pixiv help: https://vroid.pixiv.help/hc/en-us/articles/4405813333657 (both secondary, refused the reader)
- [A11] Ready Player Me shutdown: https://en.wikipedia.org/wiki/Ready_Player_Me ; https://avatarsdk.com/blog/2026/07/07/ready-player-me-migration-guide/ (secondary)
- [A12] Mixamo FAQ: https://helpx.adobe.com/be_en/creative-cloud/faq/mixamo-faq.html ; https://community.adobe.com/t5/mixamo-discussions/mixamo-faq-licensing-royalties-ownership-eula-and-tos/td-p/13234775 (secondary)
- [A13] CharMorph: https://github.com/Upliner/CharMorph ; https://charmorph-docs.readthedocs.io/en/main/ (secondary)

Fitting
- [F1] Epic, groom bindings: https://dev.epicgames.com/documentation/en-us/unreal-engine/setting-up-bindings-for-grooms-in-unreal-engine
- [F2] Unreal forum, groom on a custom head: https://forums.unrealengine.com/t/groom-binding-attaches-hair-to-wrong-position-on-customed-metahuman/2640377 ; https://forums.unrealengine.com/t/using-metahuman-grooms-with-non-mhc-meshes/226299
- [F3] Mutable Mesh Reshape: https://github.com/anticto/Mutable-Documentation/wiki/Node-Mesh-Reshape-(v5.5) ; https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/MutableTools/MuT/NodeMeshReshape?application_version=5.5
- [F4] Daz Transfer Utility API: https://docs.daz3d.com/public/software/dazstudio/4/referenceguide/scripting/api_reference/object_index/transferutility_dz ; forum on smoothing: https://www.daz3d.com/forums/discussion/comment/288282/
- [F5] Daz autofit clones: https://www.daz3d.com/forums/discussion/comment/650280 (secondary)
- [F6] Character Creator, conforming hair on head: https://manual.reallusion.com/Character-Creator-4/Content/ENU/4.1/08_Hair/Conforming-Hair-on-Head.htm ; hair creation guide: https://wiki.reallusion.com/Content_Dev:CC_Hair_Creation_Guide
- [F7] Reallusion feedback, conform distortion: https://www.reallusion.com/FeedBackTracker/Issue/Hair-conforming-not-working/2
- [F8] The Sims head-shape and hair bones (modding): https://linna.modthesims.info/d/399866/ ; https://modthesims.info/d/613061 (secondary)
- [F9] VRoid hair tool (secondary): https://www.pixivision.net/ms/a/3681 ; https://booth.pm/en/items/6753845
- [F10] Character customiser practice: https://forums.unrealengine.com/t/universal-character-customization-system/119234/8 ; hat blend shape devlog: https://ayuo-dev.itch.io/charcrafter/devlog/977130/char-crafter-v13-coming-soon-
- Blender source: Surface Deform errors: https://raw.githubusercontent.com/blender/blender/main/source/blender/modifiers/intern/MOD_surfacedeform.cc ; Shrinkwrap modes: https://raw.githubusercontent.com/blender/blender/main/source/blender/makesrna/intern/rna_modifier.cc
- [S3] MakeHuman proxy format (three base-mesh vertices per vertex): https://static.makehumancommunity.org/oldsite/documentation/file_formats_and_extensions.html ; https://deepwiki.com/makehumancommunity/makehuman/4.1-proxy-system (secondary)

Building by rule
- [B1] Stylised hair from curves: https://www.blendernation.com/2018/11/05/tutorial-stylized-hair-from-curves/
- [B2] Cartoon hair with Bezier curves (CG Cookie): https://classcentral.com/course/youtube-how-to-model-cartoon-style-hair-in-blender-bezier-curves-tutorial-170794 ; curve to mesh then unwrap: https://devforum.roblox.com/t/how-to-make-hair-in-blender/480098
- [B3] Flat taper problem: https://redlib.hbubli.cc/r/blenderhelp/comments/1kfjk34/taper_along_axis_on_curves (secondary)
- [B4] Flat modelling anime hair: https://www.blendernation.com/2020/02/01/flat-modeling-anime-hair/
- [B5] VRoid hair texture orientation: https://booth.pm/en/items/3423676 (secondary)

Choosing from a picture
- [R1] Hu, Ma, Luo, Li, Single-view hair modeling using a hairstyle database (2015): https://linjieluo.github.io/assets/pdf/15-SIG-SingleViewHairModelingUsingAHairstyleDatabase.pdf
- [R2] Digital Salon (2025): https://arxiv.org/html/2507.07387v1
- [R3] nvdiffrast licence: https://raw.githubusercontent.com/NVlabs/nvdiffrast/main/LICENSE.txt
- [R4] PyTorch3D licence: https://raw.githubusercontent.com/facebookresearch/pytorch3d/main/LICENSE
- [R5] SigLIP 2: https://huggingface.co/google/siglip2-base-patch16-224
- [R6] Hair20k terms (USC-HairSalon derivative): https://zhouyisjtu.github.io/project_hair/hair20k.html

Game hair tricks
- [T1] Scalp coverage on game hair: https://modthesims.info/t/677170
- [T2] Straight UV shells: https://polycount.com/discussion/comment/2700725
- [T3] Reallusion root and ID maps: https://manual.reallusion.com/Character-Creator-4/Content/ENU/4.0/15_Digital_Human_Shader/Hair/Textures-3-4.htm
- [T4] Reallusion ID map grey ranges: https://wiki.reallusion.com/Content_Dev:CC_3.4_Hair_Creation_Process
- [T5] inZOI packed hair maps: https://mod-docs.playinzoi.com/ModKit/project/CAZ/Hair/05.TextureSetup.html
- [T6] Unity Toon Shader angel ring: https://docs.unity3d.com/Packages/com.unity.toonshader@0.13/manual/AngelRing.html
- [T7] Anime Shading Plus hair ring highlight: https://erichu33.github.io/ASPDocs/en/articles/shader-properties-references/hair-ring-highlight.html

Local checks (tested here, Blender 5.2.3 LTS, background mode, no files written): list of the 32 bundled hair node groups; `surfacedeform_bind`, `meshdeform_bind` and `convert(target='MESH')` present; Curl Hair Curves plus Curve to Mesh turned into a mesh by two routes; Surface Deform followed a reshaped target; Shrinkwrap `OUTSIDE` mode set; NumPy 2.3.4 present, SciPy absent; Deform Curves on Surface node present.
