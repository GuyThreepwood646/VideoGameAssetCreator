# Hair, fur, beards, manes and feathers: research

Research date: 2026-11-05. Written in answer to the owner's question: "I'm curious how the industry handles hair. It feels like they have a separate asset for it? What can we do to lend realism to the assets hair? I don't know that this is applicable to all our asset types, but certainly some could benefit."

Hard rules applied: free, runs locally on the RTX 3060 12 GB and RTX 3080 10 GB, output usable in a commercial game, no paid services. Nothing was installed, no model or tool was downloaded, no code was changed and no graphics card was used. One read-only CPU script measured the knight's hair on the saved clean-up model (numbers in section 0). The web reader saved six source PDFs (talk slides and papers) in its own session folder outside the project; nothing else was fetched to disk.

Markers: "(secondary)" means confirmed only from a third-party source or a search extract; "(not verified)" means not confirmed at all; "(estimate)" means my own calculation, not a measurement. Everything else was read from a primary source (engine documentation, the talk's own slides, the repository's licence file or the model card) on the date above. Source numbers in square brackets point to the list at the end. Two limits on the reading: Blender's manual site refused automated readers on the day, so Blender facts come from blender.org, Blender's source code and the glTF add-on's own documentation; and a few Epic pages returned empty bodies, which the tables mark.

---

## Summary

1. **Yes, the industry makes hair as a separate asset, and it has good reasons.** Hair needs its own material (hair shines in a stretched band, not a round spot), its own texture, its own levels of detail and its own movement, and games want to swap hairstyles and hide hair under helmets. In Unreal a hairstyle is its own asset tied to the head by a binding; Dragon Age: The Veilguard ships over 100 hairstyles that way [E1], [I8]. What is inside that asset depends on the game: a **sculpted solid shape** for stylised and phone games, **hair cards** (flat strips with a see-through hair picture on them) for most realistic games of the last fifteen years, and **strand hair** (tens of thousands of real curves) only on PlayStation 5, Xbox Series and strong PCs.
2. **What VGAC makes today is the stylised-game answer done roughly.** On the knight the hair is about 1,000 of 19,600 triangles and 2.4 percent of the texture (about 7 texels per centimetre). It is a solid cap, one flat colour, no strand lines, no highlight, a saw-tooth fringe and a few loose flakes. The shape is not the main problem. The texture and the shading are.
3. **Most of what reads as "real hair" at game distance does not need new geometry.** In rough order: the outline, depth and shadow between clumps, the highlight band, strand lines, soft edges, movement. Depth, strand lines and most of the highlight can be painted or baked onto the shell we already have. Only outline break-up and see-through edges need extra geometry.
4. **Recommended first stage: a "hair pass" in the clean-up step, plus hair-friendly pictures.** Ask the painter for hair that converts well (clear clumps, off the face, clear of the collar), find the hair on the model, give it more texture space, paint clump and strand detail along the direction the hair flows, darken roots and under-layers, write a strand bump map, and for painted looks paint the highlight in. No download, no new triangles, the same result in Unity, Unreal and Godot.
5. **Second stage: make the hair its own part of the file.** The same mesh, but the hair triangles get their own material and their own texture (about six times the texels at 1024), a hair highlight in the Unity import, and spring bones for hair, capes and tails that hang free. This is the cheap version of "hair is a separate asset": own material, own texture, own movement, but not yet swappable.
6. **Third stage, as trials only: hair as a truly separate asset.** Two routes look possible and neither is proven: making the character bald with the image editor and building the hair as its own item (the way swords are made now), and HairDeck, a week-old open model (Apache-2.0) that turns one character picture into about 70 stylised hair cards with spring bones. A third, smaller trial adds a few see-through cards at the fringe and tips over the existing shell.
7. **What a one-mesh builder cannot reach.** Photoreal hair as seen in current console games is cards or strands made by hand over days, or grown in a hair tool. Every open image-to-strand model with released weights is non-commercial. For Photoreal the honest target is "good at three metres, a solid hairstyle up close", and the app should say so.
8. **It does not apply to every asset.** Pixel, Retro 64 and Low-poly flat looks want a plain faceted shape and need only the cleaner pictures. Painted and stylised humanoids gain the most. Creature fur, manes and feathers gain from the same texture pass; shell fur (the classic stacked-layer trick) is not worth building as a default because it does not travel in an FBX or GLB.

---

## 0. What the app makes today (the evidence)

Read from `runtime/tmp/face/hl_brow.png`, `hl_brow_wire.png` and `shipped_head.png` (the knight, pc_mid), from the code, and from a measurement of her saved clean-up model.

| Observation | Detail |
|---|---|
| One fused shell | The 3D step runs TRELLIS.2 (MIT [M14]; it has no part separation) and then a remesh in "solid" mode and a decimation to the triangle budget (`backend/vgac/graphs/trellis.py`, `RemeshMesh` with `sign_mode: solid`, then `DecimateMesh`). Hair, head, collar and body are one watertight surface. There is no scalp under the hair: the hair is the outside of the head. |
| Triangle share | Hair: about 1,015 of 19,594 triangles (5.2 percent). The whole head (top 13 percent of her height) has 1,241. Typical hair edge length 1.8 cm, longest 5.4 cm. The bob's outline is visibly made of straight segments in the wire picture. |
| Texture share | Hair covers 2.35 percent of the 2048 atlas, about 99,000 texels, at 6.8 texels per centimetre (the same density as the rest of the body). A lock of hair 2 mm wide is about one texel. The atlas as a whole is 39.5 percent used. |
| Colour | One saturated red with faint vertical streaks. No darker roots, no shadow between layers, no lighter tips. |
| Fringe | The painted fringe (thin locks over the forehead) became a saw-tooth texture edge on the forehead plus a few flake triangles standing off the brow. Thin, see-through things cannot survive a solid remesh at this triangle size. |
| Edges | The hair to skin border is a hard texel edge. Hair colour has also bled onto the collar where the hair nearly touches it. |
| Material | One material for the whole character. The FBX carries no material at all; the Unity importer builds one from the texture files (`VgacImport.cs`, `BuildMaterial`: URP Lit, HDRP Lit or Standard). The GLB carries one standard material (`blender/pack.py`, `build_material`). The Unity sandbox uses the built-in pipeline. |
| Bones and movement | No hair bones. The rigger (SkinToken) sometimes adds `Extra_` bones; nothing uses them for hair. Tails already get animated by the procedural clips. |
| What the app already says | The Photoreal recipe warns per category that hair "becomes a solid shape" and fur and feathers are "painted onto a smooth surface" (`data/styles.yaml`, `by_category`). |
| Work in flight | The face detail pass (`backend/vgac/face.py`, `stages/face_detail.py`) already tells hair from skin by colour on renders (`hair_and_skin`, `side_skin`) so the fringe is not painted onto skin. Another agent is extending it to repaint hair colour from the pictures and remove loose flakes. Stage 1 below builds on that, it does not replace it. |

---

## 1. How the industry makes hair

### 1.1 In plain words

There are five ways, and a game picks by its look and its hardware.

1. **Sculpt it.** The hair is a solid shape, modelled in big clumps, with strand lines and highlights painted on. No see-through parts. Used by stylised and anime-style games and by most phone games. It can be part of the head or a separate solid piece.
2. **Hair cards.** Hundreds of flat strips, each showing a picture of a lock of hair with see-through gaps, layered from the scalp outward. Used by most realistic games since about 2005, and still the fallback on weaker hardware.
3. **Shell fur.** The animal's skin is drawn several times, each copy slightly bigger and dotted with holes, so the stack looks like short fur. An old and cheap trick for short, even fur.
4. **Strand hair.** Tens of thousands of real hair curves, simulated and drawn every frame. Used for main characters on current consoles and strong PCs.
5. **Strands baked down to cards.** Artists grow strand hair in a hair tool and a baker turns it into cards for the game.

### 1.2 Methods compared

| Method (plain word) | Where it is used | Typical size | What the engine must provide | What moves it | Sources |
|---|---|---|---|---|---|
| Sculpted or modelled hair (a solid shape; "chunky clumps", anime hair, low-poly hair) | Stylised and anime-style games on every platform; the default on phones because it needs no transparency. Godot's manual notes stylised hair suits a hard cut-out better than dithering. | No official hair-only budgets found for Overwatch, Fortnite, Genshin Impact or Zelda (not verified). Guilty Gear Xrd characters are about 40,000 triangles in all, hair included (secondary). Reallusion caps a hair mesh at 40,000 faces. | Nothing special: an ordinary lit material and skinning. Highlights are usually painted in, or done with a toon shader. | Spring bones (jiggle bones): chains of a few bones per lock. A phone game (My Talking Angela 2) planned up to 30 bones per hairstyle (secondary). | [E5], [I1], [I2], [I3] |
| Hair cards (flat strips with an alpha texture, a see-through hair picture) | Most realistic games; the lower-detail fallback under strand hair on Xbox Series S, low PC settings, Switch and phones. | Final Fantasy XV: 20,000 to 30,000 polygons of hair per character (secondary). A hero haircut by one artist: about 40,000, which he calls heavy. Epic's card generator example targets under 50,000. Marketplace haircuts: 12,000 to 28,000. One hair texture sheet, commonly 2048 (secondary; no primary source for sheet sizes). | A two-sided material with a cut-out; a way to make the cut-out edge soft (see 1.3); a hair highlight (see 1.4); shadow inside the hair (baked into the vertices or the texture). | Bone chains, cloth on the strips, or simulated guide curves. Physics on cards can make them cut through each other. | [I4], [I5], [I6], [I7], [I9], [E2] |
| Shell fur and fin fur (stacked see-through skins, plus thin upright strips at the outline) | Short, even animal fur. Shadow of the Colossus on PlayStation 2 (six to eight layers, by a third-party breakdown), Star Fox Adventures, and some Nintendo Switch games (secondary). | The original paper: 16 layers with 128 by 128 textures, one small fin texture. Each layer redraws the whole furry surface. | Draw the mesh many times with a small push outward per layer (a shader feature, not something a file carries); cut-out or blending. Geometry shaders in the common free Unity version, which phones and Macs lack. | Lean the layers in the direction of motion or wind. | [F1], [F2], [F3], [F4], [F5] |
| Strand hair (real curves; Unreal Groom, Frostbite, id Tech, Unity's hair package) | Main characters on PlayStation 5, Xbox Series X and capable PCs. Unreal supports strands only on Windows, macOS, Linux, PS5 and Xbox Series; not on Switch or phones. The Veilguard uses cards on Series S and low PC settings. Indiana Jones and the Great Circle is the exception that runs strands everywhere it ships, at half the count on weaker machines. | The Veilguard: about 50,000 strands per character, 3 ms of a 60 fps frame for eight heads of hair, about 400 MB of graphics memory. Indiana Jones: up to 65,000 strands per head, about 2 ms. The Witcher 3 with HairWorks: 30,000 strands on Geralt, costing 10 to 20 frames per second in NVIDIA's own tests. | A dedicated renderer and simulator, compute shaders, a strand shading model, deep shadows. Unreal brings hair in as an Alembic groom file, not FBX or GLB. | Full simulation of every strand or of guide strands. | [E3], [E4], [I8], [I10], [I11], [I12], [I13] |
| Strands baked to cards | The usual way realistic card hair is made: grow strands (XGen, Houdini, Blender), bake lock pictures (colour, alpha, depth, root-to-tip, an ID per strand), place cards. Unreal's Hair Card Generator does it inside the Unreal editor and is experimental. | Same as cards. | Same as cards. | Same as cards. | [E2], [E6], [I6] |

### 1.3 Making card edges soft, and why sorting is the hard part

A see-through surface has to be drawn back to front, and hundreds of overlapping strips cannot be sorted cheaply. The ways around it:

| Technique (plain word) | What it does | Cost and catch | Sources |
|---|---|---|---|
| Alpha test (cut-out) | Every pixel is fully there or fully gone. No sorting needed, casts shadows. | Hard, crawling edges. This is what a glTF "MASK" material means. | [E5], [E7] |
| Alpha to coverage | Uses the anti-aliasing samples to fade the cut-out edge. | Needs MSAA (multi-sample anti-aliasing) switched on. | [E5] |
| Dithered or hashed alpha | A fine dot pattern stands in for transparency and the engine's temporal anti-aliasing blurs it smooth. Godot calls it Alpha Hash and says it suits realistic hair; Unreal has "Dither Opacity Mask". | Needs temporal anti-aliasing; shimmers without it. | [E5], [E8] |
| Opaque core, blended edges | Draw the solid middle of every strip first, then blend only the wispy edges. Scheuermann's 2004 scheme, with the strips pre-sorted inside to outside once, offline. Godot's "Depth Pre-Pass" is the same idea. | Two or more passes; the fixed order breaks when hair swings a lot. | [I5], [E5] |
| Order-independent transparency | The graphics card sorts per pixel. Tomb Raider (2013) did this for strand hair. | Heavy: about 200 MB at 1080p in that game. | [I11] |

For VGAC the first two rows are the only ones that can be set from a file. A GLB can say "cut-out, two-sided"; it cannot say "dither this".

### 1.4 How hair is shaded

| Model | In plain words | Who ships it | Sources |
|---|---|---|---|
| Kajiya-Kay (1989) | Lights each hair as a thin thread, so the highlight stretches into a band across the strands. | The base of most game hair until about 2016. | [P1] |
| Scheuermann (2004) | Kajiya-Kay with two bands: a white one shifted toward the tips and a coloured one shifted toward the roots, broken up by a noise texture. Adds baked shadow because threads lit this way look too bright. | The standard card recipe. | [I5] |
| Marschner (2003), d'Eon (2011), Karis (2016) | The physical version: light bounces off, passes through and bounces inside each hair. Karis made it cheap enough for games and says shadowing inside the hair is "absolutely vital to the look". | Unreal's Hair shading model; HDRP's "Physical" hair type; Frostbite. | [P2], [P3], [P4] |

What each engine gives for free:

| Engine | Hair shading | Soft edges | Sources |
|---|---|---|---|
| Unreal 5.8 | A Hair shading model on any material (Scatter, Backlit, Tangent inputs). | Masked blend mode; Dither Opacity Mask with temporal anti-aliasing. | [E9], [E8], [E10] |
| Unity HDRP 17 | A Hair shader with "Approximate" (Kajiya-Kay, for cards) and "Physical" (Marschner) types. Unity's own advice: cards for secondary characters, strands for main ones. | Alpha clipping, dithering through Shader Graph. | [E11] |
| Unity URP and built-in (Unity 6) | No hair shader. The URP list is Lit, Simple Lit, Baked Lit and Complex Lit; none has a stretched highlight. Free add-ons exist (lilToon, MIT, a toon shader with hair and fur features). A hair highlight has to be written by hand. | Alpha clipping on URP Lit; Cutout on Standard. | [E12], [T1] |
| Godot 4.7 | No hair model. The standard material has anisotropy (the stretched highlight) with a flow map, described as "commonly used for hair". | Alpha Scissor, Alpha Hash, Depth Pre-Pass, alpha anti-aliasing with MSAA. | [E5] |
| three.js (the app's own viewer, version 0.187) | `MeshPhysicalMaterial` anisotropy since r153. | `alphaTest`, `alphaToCoverage`, `alphaHash` (since r154). | [E13] |

### 1.5 Is hair a separate asset? Yes, and why

| Reason | Evidence |
|---|---|
| It is swapped | The Veilguard ships over 100 hairstyles as separate assets, each with a card version for weaker machines. Epic documents hair with its character-customisation system (Mutable). [I8], [E14] |
| It has its own material and shader | Unreal and HDRP both give hair its own material type. [E9], [E11] |
| It has its own levels of detail | An Unreal groom can be strands, cards or a plain mesh per level; MetaHuman hair has up to eight levels against four for the body (secondary). [E2], [E15] |
| It has its own movement | Groom simulation, or bone chains set up per hairstyle. [E3], [S1] |
| It has to work with hats | Indiana Jones needed special "fedora culling" volumes so hair does not poke through the hat. [I10] |
| It is tied to the head, not part of it | In Unreal a groom is its own asset, attached by a Groom Binding asset. [E1] |

No single source lists all of these in one sentence; the table collects the pieces. Stylised games are the partial exception: a fixed character's sculpted hair is often simply part of the head mesh, as it is in VGAC.

---

## 2. Where "realism" in hair comes from

No source was found that ranks these. The order below is my reading of the sources, weighed against what the evidence pictures lack. "Game distance" means a character a few metres from the camera, about a third of the screen tall.

| Rank | What | Why it matters | Can it be had without changing geometry? | Sources |
|---|---|---|---|---|
| 1 | Outline and volume (silhouette break-up) | The outline is read first. The fur paper says cues at the outline are critical, which is why fins exist; card artists add flyaway strands last, just to finish the outline. | **Partly.** A smoother outline needs more triangles on the head (same mesh, different triangle spending). Wisps and gaps need cards. Cleaner source pictures help for free. | [F1], [I6] |
| 2 | Depth: shadow between clumps, darker roots, darker under-layers | Without it hair is a flat cap. Karis: shadowing is vital. Scheuermann bakes it into the vertices because the maths alone is too bright. | **Yes.** It can be painted into the colour and the ambient occlusion map. | [P4], [I5] |
| 3 | The highlight band (anisotropic specular) | The stretched band across the strands is what says "hair" and not "clay" or "plastic". | **Yes**, in three ways: painted into the colour (right for painted and stylised looks, wrong for Photoreal because it does not move with the light); a strand bump map under an ordinary shiny material (works in every engine, to be proven); a hair shader (needs the hair to have its own material). | [I5], [P2] |
| 4 | Strand detail in the texture | Lines of slightly lighter and darker hair along the flow. Needs texels: at 7 per centimetre today, a lock is one texel wide. | **Yes.** More texture space for the hair, and detail painted along the flow direction. | section 0 |
| 5 | Soft edges through transparency | Hairlines, tips and fringes that fade out. | **Mostly no.** Where hair lies on skin (the hairline on the forehead, sideburns, stubble, eyebrows) the edge is only a texture border and can be painted soft, with fine hairs drawn onto the skin. Edges against the background need cards with alpha. | [I5] |
| 6 | Movement | Frostbite: believable motion is very important for hair to look right. At game distance a little sway is enough. | **Yes for parts that hang free** (ponytails, braids, capes, tails): bones and weights only. Not for hair resting on the shoulders, which a one-mesh model welds to the body. | [I13] |

Four of the six can be had on the shell we already make. That is the basis of stage 1.

---

## 3. What VGAC can do

Options from cheapest to most ambitious. Times are per asset on this machine; triangles are for a pc_mid humanoid (20,000 budget). All times and triangle counts for things not yet built are estimates.

### 3.1 The options at a glance

| # | Option | What the person sees | Suits | Where it is built | Extra time | Extra triangles | New download |
|---|---|---|---|---|---|---|---|
| h | Hair-friendly pictures | Cleaner hair shapes, fewer flakes, hair that no longer hides the eyes | Every look and category | Concept and side views | 0 s | 0 | None |
| i | More triangles on the head | A rounder hair outline, fewer straight edges | All but low-poly and pixel looks | Clean-up | about 30 s (a re-bake) | 0 (moved, not added) | None |
| a | Texture-only hair pass | Clumps, strand lines, darker roots, a highlight; fur and feathers with direction | Painted, stylised, cel, clay, Photoreal; humanoid hair and beards, creature fur and manes, feathers | Clean-up, after the face pass | 30 to 60 s | 0 | None |
| b | Hair as its own material | Sharper hair still; a real hair highlight in Unity; "Hair" as a separate entry in the files | Humanoid hair and beards, manes; smooth-shaded looks | Clean-up and hand-off | 20 to 40 s | 0 to 200 | None |
| f | Spring bones | Ponytails, braids, capes and tails sway | Anything with a part that hangs free | Make it move and hand-off | 5 to 15 s | 0 | None, or one small MIT package |
| d | Fringe and tip cards over the shell | Wispy fringe, broken-up tips, a softer outline | Photoreal and painted humanoids, manes and tails; PC and Showcase only | Clean-up and hand-off | 30 to 60 s | 500 to 1,500 | One CC0 texture pack (138.5 MB) or textures we render ourselves |
| c | Bald character plus a separate hair item | Hairstyles that can be swapped between characters; hair with a full texture of its own; a real scalp under a hat | Humanoids; hair that is short or tied | Concept, a second 3D build, the items path | 4 to 6 minutes | The hair's own 1,500 to 4,000 | None |
| g | A model that makes hair cards from the picture (HairDeck) | Anime-style hair as separate locks that move | Stylised and anime humanoids only | A new engine on a card, then clean-up | Not measured here | About 70 cards (count not stated in triangles) | About 7.7 GB including gated Meta models |
| e | Shell fur | Fuzzy short fur on creatures in Unity | Creatures, PC | A Unity material only | 0 s | 0 in the file; the mesh is drawn 8 to 16 times | One MIT shader (39 MB) |

### 3.2 Each option in detail

#### (h) Better source pictures

**What.** The 3D builder turns whatever outline the picture shows into a solid. Wisps become flakes, a see-through fringe becomes saw-teeth, and hair lying on a collar gets fused and bleeds colour. Hair painted as a few large clumps with a clean outline converts well. So ask for that, per look.

**Build.** `stages/concept.py` `build_prompt` already joins the description, the style block and the category's pose block. Add a hair sentence per look to the recipes in `data/styles.yaml` and a matching negative for painters that take one. Suggested wording (to be tested like the other prompt blocks in `docs/RECIPES-BAKEOFF.md`):

| Look | Hair sentence |
|---|---|
| Painted, stylised, cel, clay | "Hair drawn as a few large, clearly separated clumps with a clean outline, no loose strands, the forehead and both eyes clear of hair, hair ending above the collar or tied back." |
| Photoreal | "Hair neatly tied back, braided or cut short, combed in clear locks, no flyaway hairs, nothing over the face." |
| Low-poly flat, Retro 64, pixel | "Hair as one simple solid shape." |
| Creatures | "Short dense fur lying flat in clear clumps; the mane and tail as a few thick locks." |
| Negative, where supported | "wispy hair, flyaway strands, hair over the eyes, messy loose hair, transparent hair, hair covering the collar" |

When the description asks for long loose hair, keep the user's wish and show a note instead (wording in section 5.4). A readiness check on the concept, using the hair mask from (a), can say "Hair covers one eye" with a Fix that asks the image editor (Qwen-Image-Edit, already installed) to move the hair off the face.

**Engines.** No effect on files. **Risk.** The painter ignores the sentence on some seeds; a description that says "long flowing hair" and a block that says "tied back" pull against each other, so the block must give way to the description.

#### (i) More triangles on the head

**What.** The head has 1,241 of 19,594 triangles (6 percent) although it is where players look. Decimation spreads triangles by shape error, not by importance.

**Build.** The clean-up step already decimates back to the budget in Blender and re-bakes when it does (`stages/prep.py`, `blender/prep.py`, `blender/rebake.py`). Ask the 3D step for about 1.3 times the budget, then decimate in Blender with a vertex group that protects the head (the Decimate modifier's collapse mode takes a vertex group), so the head keeps about twice its share. `face.find_head` already finds the head. **Cost.** One re-bake, about 30 s. **Risk.** The body loses about 6 percent of its triangles; the triangle-budget and silhouette checks need the same tolerance as today. Not for Low-poly flat, where even triangle size is the look.

#### (a) Texture-only hair pass

**What the person sees.** The red cap becomes hair: darker at the roots and under the top layer, lighter toward the tips, with clump and strand lines following the direction the hair falls, a bump map that catches the light along the strands, and for painted looks a soft highlight band. On a wolf: fur with direction, darker in the hollows. On a bird: feather rows with a darker edge to each row.

**How it would work**, as a sibling of the face pass, run after it in `stages/prep.py`:

1. **Find the hair.** Extend what `face.hair_and_skin` and `side_skin` do for one view into a vote over the five turnaround directions, and write the result as a hair mask in texture space. For characters whose hair and skin are close in colour, add a 2D hair mask of the concept picture from a commercially clean model: MediaPipe's hair segmenter or selfie-multiclass model (Apache-2.0, small, CPU) or SAM 2 (Apache-2.0); see section 4.4. For creatures the "hair" is the whole body minus eyes, nose, mouth, claws and hooves, so the mask is the inverse of those.
2. **Find the flow direction.** Two sources, blended. From the pictures: a bank of oriented line filters over the painted hair gives a direction per pixel with a confidence (the classic method from hair capture research, no model needed) [P5], [P6]; it is projected onto the mesh the same way the face pass projects the face (`face.project_back`). From the shape: where the picture gives no clear direction, hair falls from the crown downward along the surface; fur runs from nose to tail and down the legs.
3. **Give the hair texels.** As `blender/faceuv.py` does for the face: enlarge the hair's texture pieces. Doubling their size takes hair from 2.4 to about 9.4 percent of the atlas, which the atlas has room for at 39.5 percent used (estimate; the face pass found that halving the packing margin freed the space it needed).
4. **Paint.** Colour from the sharpened picture where a picture sees the hair (the work now in flight). Then, everywhere on the mask: streaks of lighter and darker hair drawn along the flow (line integral convolution, a standard way to smear noise along a direction field); root and under-layer darkening from the existing ambient occlusion map and from distance to the hairline; a matching height pattern turned into the normal map; roughness set to a hair-like range. For painted, stylised, cel and clay looks, a highlight band painted across the flow where the surface faces up and outward. For Photoreal no painted highlight (the style removes painted light on purpose, `delight_default`).
5. **Soften the hairline.** Where hair meets skin on the surface, fade the border over a few texels and draw fine hairs onto the skin side. The face pass must keep ownership of the face: the hair pass only writes on the hair side and in a narrow band.

**Suits.** Painted, hand-painted stylised, cel, clay and Photoreal. Skipped for crisp and flat looks, exactly as the face pass is (`skip_reason`). Humanoid hair, beards, brows; creature fur and manes; feathers (flow from the wing root to the tip).

**Cost.** 30 to 60 s on the CPU after the face pass (estimate; the face pass itself takes about 143 s, most of it the image editor). No triangles.

**Engines.** Base colour, normal, roughness and occlusion maps travel in every file and every engine reads them. The highlight from a strand bump map under the ordinary material is expected to look stretched across the strands up close and to flatten at distance as the texture is shrunk for far views; this is to be proven in stage 1.

**Risks.** A wrong flow direction makes hair look combed sideways; the confidence must gate it and the fallback must be the plain downward direction. Strand lines on a 1.8 cm triangle shell cannot hide the shell's flat facets at the outline. Dark hair on dark skin and helmets or hoods defeat the colour split; the pass must skip and say why, as the face pass does.

#### (b) Hair as its own material (and optionally its own object)

**What.** The hair triangles stay where they are but get a second material with its own texture. That is enough for the three things a separate hair asset buys day to day: its own shader, its own texture space and its own treatment at each level of detail. A hair texture of 1024 holds about 630,000 texels at 60 percent fill against 99,000 today: about six times the texels, 17 per centimetre (estimate). At 2048, about 34 per centimetre.

**Build.**

1. The hair mask from (a) picks triangles. Triangles that straddle the hairline stay with the body material, so the hairline stays in the face's texture, where the face pass painted it.
2. `blender/faceuv.py` style repack, but into a second UV layout and a second image; `blender/rebake.py` bakes the hair maps.
3. `blender/pack.py` `build_material` builds two materials for the GLB. The FBX keeps two material slots (names only). `stages/export.py` adds the hair texture files to `TEXTURE_KEYS`-style lists and to the manifest.
4. `VgacImport.cs` `BuildMaterial` builds a second material. First version: the same Lit or Standard shader with the hair textures. Second version: a small "VGAC Hair" shader with a two-band Kajiya-Kay highlight written for the built-in pipeline and for URP, and HDRP's own Hair shader in "Approximate" mode [E11]. The importer Version constant goes up.
5. The app viewer: a second three.js material with anisotropy [E13].
6. Optional: split the hair into its own object sharing the skeleton, so the equipped prefab can hide it under a full helmet. There is no scalp underneath, so it cannot be hidden under a hat or swapped for another hairstyle. That needs (c).

**Suits.** Smooth-shaded looks on PC and Showcase budgets. On Phone, one material and one draw per character is worth more than sharper hair, so stay with (a).

**Cost.** 20 to 40 s (a second bake). One more material, which is one more draw per character. Up to about 200 triangles if the hairline is cut cleanly instead of following triangle edges.

**How it behaves in each engine.**

| Engine | From the FBX | From the GLB |
|---|---|---|
| Unity | The importer builds both materials itself, so it gets the hair shader automatically. | glTFast supports two materials, cut-out and two-sided on every pipeline; it does not read the anisotropy extension. [E16] |
| Unreal | FBX carries only basic materials and no settings [E17]. The person switches the hair material to the Hair shading model by hand; the README says how. | Import mapping for cut-out and extensions could not be read (not verified). Same manual step. |
| Godot | Not documented for alpha or culling (not verified). | Two materials arrive as standard materials. The person ticks Anisotropy and assigns the flow map we export; one README line. Godot's importer does not read the anisotropy extension. [E18] |

`KHR_materials_anisotropy` is ratified and Blender exports it since 4.1 [E19], [B4], but it describes brushed metal, not hair, and of the engines above only three.js reads it. Writing it is harmless and helps the app's own viewer; it is not a cross-engine answer.

**Risks.** The split is by colour and position, so a red-haired knight in a red cloak, a fur collar, a hood or a beard that merges into a scarf can be assigned wrongly; the pass needs a confidence and a "Show me what was taken as hair" picture, and must fall back to one material. Three shader variants to keep working in Unity.

#### (f) Spring bones for hair, capes and tails

**What.** Parts that hang free sway when the character moves.

**What a one-mesh model allows.** Only parts that hang clear of the body can move: a ponytail or braid away from the neck, a cape hem, a tail, long ears, a mane's tips. Hair that rests on the shoulders is welded to them by the solid remesh, and a bob like the knight's has nothing that hangs. Option (h) decides this at the picture: "tied back in a ponytail that hangs clear of the neck" gives something that can move.

**Build, at the rig step.** A new Blender script beside `blender/rig_fix.py`:

1. From the hair mask (b) or the cape and tail found by shape (`procanim.py` already classifies tail chains), find regions that hang free: thin, attached at one end, clear of the body by a few centimetres.
2. Add a chain of 2 to 4 bones along each, parented to Head, Spine2 or Hips, named `Spring_Hair_01` and so on. Weights fade from the parent bone at the root to the chain toward the tip.
3. Write `springs.json`: the chains, a stiffness, a drag, gravity, and the existing hit boxes (`hitboxes.json`) as the shapes the chains must not pass through.

**Movement, two ways.**

| Way | What it gives | Limits |
|---|---|---|
| Baked into the clips in Blender | A small spring simulation runs over each clip and is keyed onto the chain bones. Every output sways, including the app viewer, GLB, Godot and Unreal files, with nothing to install. | It replays; it does not react to the game (wind, sudden stops, blends between clips). Unity Humanoid clips shared from the library animate only the humanoid bones, so the chains would not move in the Unity prefab's 46 shared clips; only in the character's own clips. |
| A spring component in the engine | Reacts to everything. | Not carried by FBX at all. In a GLB only as the VRM spring-bone extension, which only VRM importers read [S2]. So it is set up per engine: |

| Engine | Free spring component | Licence | How VGAC would set it up |
|---|---|---|---|
| Unity | None built in (not verified beyond the Animation Rigging constraint list, which has none) [S3]. Free packages: UnityChanSpringBone (MIT, dormant since 2023), JigglePhysics by naelstrof (MIT, active), EZSoftBone (MIT, active), UniVRM's spring bone (MIT, active). Dynamic Bone and MagicaCloth 2 are paid (20 and 35 US dollars a seat). | [S4], [S5], [S6], [S7], [S8], [S9] | The importer reads `springs.json` and adds the component. Simplest and dependency-free: a short spring script of our own, written into the project beside `VgacImport.cs`. |
| Godot | `SpringBoneSimulator3D`, built in since 4.4, made for "hair, cloth, and tails", with its own collision shapes. | MIT (engine) [S1] | README steps, or a small import script. |
| Unreal | AnimDynamics, Spring Controller and the Rigid Body node are built in; Kawaii Physics is a free MIT plug-in (Unreal 5.3 to 5.8). | [S10], [S11] | README steps only; an Animation Blueprint cannot be built from our files. |

**Cost.** 5 to 15 s; 2 to 4 bones per chain, 2 to 12 bones a character. **Risks.** Weights at the root of a fused part stretch the body surface; chains on the wrong region (a scabbard read as a ponytail); spring settings that look right at one size and wrong at another.

#### (d) Hair cards over the shell (fringe and tips only)

**What.** Keep the shell as the solid core, which is what card artists build first anyway (a dense base layer that hides the scalp [I6], [I7]). Add a small number of see-through strips where the shell fails: the fringe, the tips, the nape, a few flyaways at the outline; on creatures, the mane ridge, tail tip, cheek and ear tufts.

**Why not a full card hairstyle.** A real one is 12,000 to 40,000 triangles placed by hand in three or more layers; that is the whole pc_mid budget, and hand-placed card hair is a craft that a procedure will not match. A fringe-and-tips set is 40 to 120 strips, 500 to 1,500 triangles.

**Build.**

1. From the flow field of (a), trace paths over the shell from the hair's boundary inward (for the fringe: from the saw-tooth edge up toward the crown) and along the outline-facing ridges.
2. Sweep a strip along each path, 4 to 8 segments, lifted 2 to 6 mm off the shell, extending 1 to 3 cm past the hair's edge. Built directly in a Blender script; Blender's own hair nodes are no shortcut here because they grow hair outward from a scalp, and our shell is the outside of the hair, not the scalp.
3. Texture: a small sheet of lock pictures with alpha. Either the CC0 pack "Hair Alphas For Days" (85 textures at 2048, 138.5 MB) [T2], or locks we render ourselves in Blender from its bundled hair nodes with an orthographic camera (no documented Blender workflow for this exists; our own script). Each strip is tinted with the shell's colour under it.
4. Cards take their skin weights from the nearest shell triangle. They are dropped at LOD1 and beyond.
5. Material: cut-out, two-sided. The GLB can say both (`alphaMode` MASK and `doubleSided`), and Blender exports both [B4], [E7]. The Unity importer sets them on the hair material; the built-in Standard shader has no two-sided option, so the strips get doubled back faces or the hair shader from (b).

**Suits.** Photoreal and painted humanoids on PC and Showcase; manes and tails. Not Phone (see-through pixels are costly there, secondary [I14]), not low-poly, pixel or Retro 64.

**Engines.** Unity: set by the importer. Godot: a GLB MASK material becomes Alpha Scissor with the same cut-off, and two-sided is kept [E18]. Unreal: FBX carries no such settings [E17]; the person sets Masked and Two Sided, or the dithered mask for soft edges [E8]. three.js: `alphaTest` from the GLB [E13].

**Risks.** The most visible failure mode of all the options: strips at a wrong angle look like straw or paper. Hard cut-out edges crawl unless the game has MSAA or temporal anti-aliasing. Needs (a) and (b) first.

#### (c) Bald character plus a separate hair item

**What.** The industry structure, built the way VGAC already builds swords: the character without hair, and the hair as its own asset placed on the head. The hair gets a whole texture to itself, there is a real scalp underneath, a hat can replace it, and one hairstyle can be given to another character.

**Build, reusing `items.py`.**

1. The approved concept stays as painted. The image editor (Qwen-Image-Edit-2511, Apache-2.0, installed) makes "the same character with a smooth bald head, everything else unchanged". The bald picture goes through the normal 3D build. The face pass still paints the face from the original picture.
2. The image editor makes a second picture: "only the hair, as a wig on a plain grey head shape". That goes through the rigid-prop path as a linked item, as `items.py` does for a hat (`Socket_Head` exists, with hat and helmet rules in `data/items.yaml`).
3. Fit: scale and place the wig over the skull by matching outlines from the front and side, the way `stages/face_detail.py` lines up side views.
4. Short hair is a rigid item on the head bone. Hair that reaches the neck or below must bend with the body, so it becomes a second skinned mesh on the same skeleton with weights copied from the body (Blender's Data Transfer), plus spring chains (f).

**Cost.** One more 3D build (110 to 160 s), two image edits, a second clean-up: 4 to 6 minutes (estimate). The hair has its own 1,500 to 4,000 triangles.

**Risks, and why this is a trial and not a plan.**

1. No evidence was found, either way, that the image editor reliably removes hair or draws a hair-only picture. Research projects train dedicated "bald converter" models for this, which suggests general editors struggle; the ones found are non-commercial or unlicensed [M12].
2. No report was found of how TRELLIS.2 treats a wig picture. It will most likely fill the inside solid.
3. Two separate 3D builds do not line up exactly (the finding of `CHARACTER-PARTS-RESEARCH.md` for clothing). Hair is more forgiving than a jerkin, because it only has to cover the scalp, but gaps at the temples and nape are likely.
4. The wig's edge on the forehead is a hard mesh edge, harder than today's painted hairline, unless it also gets fringe cards (d).
5. The bald edit can shift the face or the ears.

#### (g) Models that make hair from a picture

| Model | Gives | Licence | Fits our cards | Verdict |
|---|---|---|---|---|
| HairDeck (Tsinghua, CMU, CUHK; released 2026-10-30) [M1], [M2], [M3] | One character picture in; about 70 hair cards as a mesh with per-card UVs and 16 colours per card, optional spring-bone chains; GLB, rigged GLB or VRM. | Code and weights Apache-2.0, read in the licence file and on the model card. Depends on Meta's SAM 3 and DINOv3 (custom licences without a non-commercial clause; they bar military uses, must be passed on, and are gated downloads). Trained on VRoid characters the authors cannot redistribute. | 2.45 GB, about 7.7 GB with dependencies. About 6.7 GB of graphics memory with offloading (a third-party figure from a 16 GB card; no official number). | **The only usable one. Trial for stylised looks only.** The paper says very short hair and realistic alpha-textured cards are outside its training, and it makes no textures. The cards sit on its own template head, so fitting them to our head is our work. One week old. |
| CHARM (2025) [M4] | Anime hair cards from a point cloud. | GPL-3.0 | Not stated | Superseded by HairDeck; GPL code could not be bundled. |
| HairStep, DiffLocks, HAAR, Im2Haircut (picture or text to strands) [M5], [M6], [M7], [M8] | Strand hair. | CC BY-NC, or "non-commercial scientific research use only". | n/a | **Unusable (non-commercial).** |
| Neural Haircut, GaussianHaircut, MonoHair (video to strands) [M9] | Strand hair from a video of a real head. | CC BY-NC variants. | n/a | **Unusable (non-commercial)**, and they need video of a real person. |
| Perm (Adobe) [M10] | A parametric strand model. | Code MIT; the public weights are trained on data limited to non-commercial research. | n/a | **Unusable (weights).** |
| StdGEN [M11] | One picture to a character with body, clothes and hair as separate meshes. | Apache-2.0 on the repository and model card, but the README says the checkpoints are "for research purposes only". 22.1 GB; mesh stage needs at least 10 GB. | Borderline | **Not clean until the authors say which statement holds.** Worth watching: it is the closest thing to (c) in one model. |
| Strands2Cards (2025) [M13] | Strand hair to cards with textures, fully automatic. | No licence in the repository, which means no permission. | n/a | **Unusable (no licence).** |
| Part-aware builders (OmniPart, PartCrafter, HoloPart, Hunyuan3D-Part, PartPacker) | Parts of an object as separate meshes. | As found in `CHARACTER-PARTS-RESEARCH.md` section 2: non-commercial, territory-restricted, derived from non-commercial code, or without textures. | Mixed | No change: not a route to hair. PartCrafter cannot be asked for "the hair". |

Even with a strand model, the app would have nowhere to send the result: strands reach Unreal as an Alembic groom and Unity through a separate hair package under Unity's own licence [E3], [T3], and neither travels in an FBX or GLB. Blender's glTF exporter does not export hair curves at all [B4].

#### (e) Shell fur for creatures

| Way to do it | Works from the files alone? | Cost | Verdict |
|---|---|---|---|
| A shell-fur shader in the engine | No. A shader is per engine. Unity: jiaozi158/ShellFurURP (MIT; URP 12.1 and later; its multi-pass variant avoids geometry shaders; one folder of it is under Unity's own licence; Unity 6 not confirmed) and hecomi/UnityFurURP (MIT, needs geometry shaders, dormant since 2023). Godot: the known add-on is for Godot 3.4; the Godot 4 one is archived. Unreal: none built in. [T4], [T5], [T6], [T7] | The furry mesh is drawn 8 to 16 times. Heavy on phones (secondary). | **Not a default.** At most a later, optional "fuzzy fur" material for Unity URP on PC. |
| Shells baked into the file as real layers | Yes, with a cut-out material. | Triangles multiply: a 20,000-triangle wolf with 8 layers is 160,000, all skinned. | **No.** |
| Texture fur plus tuft cards | Yes. | The texture pass (a) plus 300 to 1,000 triangles of tufts (d). | **This is the creature answer:** direction and depth in the texture, tufts on the mane, tail, cheeks and ears. |

---

## 4. Free tools and data, with licences

### 4.1 Blender

| Item | Finding | Licence, where read | Use in VGAC |
|---|---|---|---|
| Output ownership | What you make with Blender is your own, including for commercial games. | GPL for the program; blender.org licence page [B1] | Already relied on. |
| Hair Curves and the bundled hair node groups | 26 node groups since 3.5: Generate, Interpolate and Duplicate Hair Curves; Clump, Curl, Braid; Frizz, Smooth, Trim, Noise and others; Set Hair Curve Profile. | Blender's source is GPL-2.0-or-later. The bundled assets are stated to be CC0 in the blender-assets README (secondary: seen as a search extract, the page could not be opened). [B2], [B3] | For rendering our own lock textures (d). Not for placing hair on our shell. |
| Driving them from a script with no window | Blender's own Quick Fur operator does it: find the bundled hair file under the data files folder, load the named groups with `bpy.data.libraries.load`, add a Nodes modifier, set the inputs. The file's path and the way inputs are set changed between versions, so both must be checked on 5.2.3. | Blender source [B3] | (d), lock textures. |
| Curves to strips | No official hair-card workflow. Curve to Mesh with a line profile makes a ribbon; UVs along it have to be written by the node setup (not verified at the manual). | n/a | We build strips directly instead. |
| Baking strands to lock textures | No free documented workflow found; every hit was a paid add-on. Doable with built-in features and our own script. | n/a | (d) |
| glTF export | Cut-out and blended alpha are taken from the material's nodes; two-sided is the inverse of Backface Culling; the anisotropy extension since 4.1; curves and hair are not exported. | Apache-2.0 add-on, its own docs [B4] | (b), (d) |

### 4.2 Hair-card tools and textures

| Name | What it is | Licence and price | Verdict |
|---|---|---|---|
| "Hair Alphas For Days" (OwlishMedia, OpenGameArt) [T2] | 85 lock textures at 2048 with alpha. | CC0, read on the page. 138.5 MB. | **Usable and can be bundled.** |
| GBH Tool (GixoXYZ) [T8] | A procedural hair add-on for Blender's new hair system that can output cards. | GPL-2.0-or-later (README). Free. Last updated November 2024 for Blender 4.2; not tested on 5.2. | Usable as a separate tool the app calls; a dependency on an unmaintained add-on is a risk. Our own strip builder is smaller. |
| "Hair cards from curves" (Daniel Bystedt) [T9] | A geometry-nodes file that lays premade cards along curves. | Free, but no licence is stated. | Cannot be bundled. A person may use it by hand. |
| Unreal Hair Card Generator [E6] | Cards and a texture sheet from a groom, inside the Unreal editor. | Unreal EULA: free; what you make with it may be used in other engines; the tool cannot be redistributed and needs a full Unreal install. [T10] | Not for an automatic pipeline. |
| Hair Tool, Hair Brick, Simple Hair Cards, FiberShop, Hair Strand Designer, GroomBear | Card and lock-texture tools. | Paid (5 to 59 US dollars) or under store licences (secondary for most). | **Out: not free.** |
| Strands2Cards [M13] | Research code. | No licence. | **Out.** |
| xNormal | A free baker. | Freeware; terms could not be read (not verified). | Not needed; Blender bakes. |

No open tool was found that fits cards onto an existing hair shell. That part would be ours.

### 4.3 Spring bones and shaders

Covered in the tables of 3.2 (f), 1.4 and 3.2 (e). The licences that matter: UnityChanSpringBone, JigglePhysics, EZSoftBone, UniVRM, Kawaii Physics, lilToon and both shell-fur repositories are MIT; Godot is MIT; three.js is MIT; Unity's HDRP hair shader, Unity's hair package and Unity Toon Shader are under the Unity Companion Licence (free, usable only inside Unity); Unreal's hair shading and physics nodes come with the engine. **MagicaCloth 2 is not free** (35 US dollars a seat; the idea that it became MIT is wrong) and Dynamic Bone is 20 US dollars a seat [S8], [S9]. The VRM spring-bone specification has no licence file in its repository (not verified); the implementations are MIT.

### 4.4 Telling hair from skin in a picture

| Model | What it gives | Licence, where read | Clean for commercial use? |
|---|---|---|---|
| MediaPipe Hair Segmenter [G1] | Hair or not, 512 by 512, tiny. | Apache-2.0, in the model card. | **Yes.** Made for selfies; braids and thin long pieces are unreliable; stylised art untested. |
| MediaPipe Selfie Multiclass [G2] | Background, hair, body skin, face skin, clothes. | Apache-2.0, in the model card. | **Yes.** Same caveats. |
| SAM 2 [G3] | Any region from a click or a box. | Apache-2.0. | **Yes.** Needs a seed point, which the colour split can supply. |
| SAM 3 [G4] | Regions from a word ("hair"). | Meta's SAM Licence: commercial use allowed, military uses barred, must be passed on; a gated download. | Conditionally; cannot be fetched silently. |
| face-parsing.PyTorch (BiSeNet) and jonathandinu/face-parsing [G5], [G6] | Face parts including hair. | Code MIT, but the weights are trained on CelebAMask-HQ, which is limited to non-commercial research; the second card says so itself. | **No.** |
| Sapiens v1 (Meta) [G7] | 28 body parts. | CC BY-NC 4.0. | **No.** |

The colour split in `face.py` plus one of the first three is enough. The popular face-parsing nodes for ComfyUI wrap the tainted models and must not be used.

---

## 5. Recommendation

### 5.1 The staged plan

| Stage | What is built | What the person sees | What it proves | Cost |
|---|---|---|---|---|
| **1. Hair that looks like hair, on the shell we have** | (h) hair sentences per look and a "hair covers the face" check; the hair mask over all views, saved as a version file; (a) the texture pass: more texels, colour from the pictures (in flight), strands along the flow, root and under-layer shadow, strand bump map, painted highlight for painted looks, soft hairline; (i) more triangles on the head. The same pass, with a different mask and flow, for creature fur and feathers. | "Her hair has strands, depth and a highlight, and the fringe no longer looks torn." "The wolf's fur has direction." | That the hair can be found reliably on the model (the mask against the picture's hair, measured); how far a solid shell can go; whether a strand bump map gives a believable highlight in Unity, Godot and the app viewer with no special shader. A new check, `hair_matches_picture`, and a "Hair: before and after" picture like the face's. | No download. 30 to 60 s, plus about 30 s when the head triangles are rebalanced. No new triangles. |
| **2. Hair as its own part of the file, and hair that moves** | (b) a second material and texture for hair on PC and Showcase budgets; the hair highlight in the Unity import (built-in and URP shader of our own, HDRP's Hair shader), anisotropy in the app viewer, README lines for Unreal and Godot with an exported flow map; (f) spring chains for ponytails, braids, capes and tails, baked into the character's own clips, `springs.json`, a small spring script in the Unity import. | "Hair shines like hair in Unity." "His ponytail and cape sway." | That the split holds across hair colours and outfits; the cost of a second material; that baked sway looks right in every output and the runtime spring in Unity. | No download (or one small MIT package if we do not write the spring script). 20 to 40 s, 5 to 15 s. One more draw; 2 to 12 bones. |
| **3. Trials toward hair as a separate asset** | Three small experiments, each judged before anything is built for users: (3a) bald edit plus a wig item through the items path, on five characters; (3b) HairDeck on the same five in stylised looks; (3c) fringe and tip cards over the shell on a Photoreal and a Painted character. | Nothing in the app until one passes. | 3a: does the editor make a clean bald version and a usable wig picture four times out of five, and does the wig fit within a centimetre? 3b: does it run in under 10 GB here, and do its cards suit painted (not anime) characters once coloured from our picture? 3c: do cards placed by procedure look better than the bare shell to a person, in Unity with anti-aliasing on? | 3b needs about 7.7 GB of downloads with two gated Meta models, and the owner's yes. |

Stage 1 comes first because it gives the largest visible change for the least risk and because stages 2 and 3 all need its hair mask and flow direction.

### 5.2 Where the look changes the answer

| Look | Industry answer | VGAC answer |
|---|---|---|
| Pixel, Retro 64, Low-poly flat | A simple solid shape. | Stage 1 pictures only. No hair pass (skipped like the face pass). Already close to right. |
| Painted, hand-painted stylised, cel, clay | Sculpted clumps with painted strands and a painted highlight; no transparency. | **The best fit.** Stage 1 reaches the industry look for this style; stage 2 adds movement. HairDeck (3b) is the route to separate locks. |
| Photoreal | Cards, or strands on current consoles. | Stage 1 and 2 give a believable solid hairstyle at a few metres. Fringe cards (3c) help the edges. It will not match a current console game up close, and the app should say so and steer toward short, tied or covered hair. |

| Asset | What applies |
|---|---|
| Humanoid hair | Everything above. |
| Beards, brows, stubble | Stage 1: these lie on skin, so painted soft edges and fine hairs work well. A long beard is a hanging part for stage 2 springs. |
| Creature fur | Stage 1 with flow from nose to tail. No shell fur. |
| Manes and tails | Stage 1, stage 2 springs (tails already have bones), tuft cards in 3c. |
| Feathers | Stage 1 with flow from the wing root outward and a darker edge per row. Separate flight feathers as cards are a later, creature-specific version of 3c. Wings will still look thicker than real ones. |
| Props, scenery, materials | Nothing, except fur and thatch as a material, which the material category already handles as a texture. |

### 5.3 What not to do

1. **Strand hair.** No commercially usable model makes it, FBX and GLB cannot carry it, and only PlayStation 5 class hardware runs it.
2. **Full card hairstyles by procedure.** They cost the whole triangle budget and are a hand craft; a bad card hairstyle looks worse than a good solid one.
3. **Cutting the hair off the fused mesh to make it swappable.** There is no scalp underneath. A material split (stage 2) gets the benefits that do not need one.
4. **Shell fur as a default, or baked into the file.** It is a per-engine shader, costly on phones, or a triangle explosion.
5. **Alpha-blended hair.** It needs sorting we cannot set up from a file. Cut-out only, softened by the game's anti-aliasing.
6. **A painted highlight on Photoreal.** It contradicts the style's "no painted light" rule and does not move with the light.
7. **Face-parsing models trained on CelebAMask-HQ, Sapiens v1, any CC BY-NC strand model, Strands2Cards, StdGEN until its licence is settled.**
8. **Paid tools as dependencies:** Hair Tool, MagicaCloth 2, Dynamic Bone, FiberShop.
9. **Cloth simulation on hair.** As found for capes: it needs separate meshes and capsule colliders; spring bones cover the need.
10. **Promising movement for hair that rests on the shoulders.** A one-mesh model welds it there.

### 5.4 What to tell a user, in plain words

For the asset form and the glossary (the standard term first, the plain word once in parentheses):

1. Under the description, when hair words are found and the look is Photoreal: "Hair is built as one solid shape with the strands painted on. It looks right from a few steps away and simpler up close. Short, tied-back or braided hair comes out best; long loose hair and wispy fringes come out worst."
2. When the description asks for long loose hair: "Long loose hair will be joined to the shoulders and will not swing. A ponytail or braid that hangs clear of the neck can move."
3. For creatures: "Fur is painted onto the surface with its direction and shading. It reads as fur at game distance; it is not made of separate hairs."
4. On the clean-up step, as the pass's line: "Hair: strands and shading painted from your picture." With the before and after picture.
5. Glossary: "Hair cards (flat strips with a see-through picture of hair on them): how most realistic games make hair. This app does not make them yet." "Spring bones (small extra bones that sway on their own): what makes a ponytail, cape or tail swing." "Anisotropic highlight (a shine stretched into a band): the bright band across hair."

---

## Sources

Internal: `STATUS.md`; `docs/CHARACTER-PARTS-RESEARCH.md` (sections 1, 2 and 5.4); `backend/vgac/face.py` (`hair_and_skin`, `side_skin`, `project_back`, `find_head`); `backend/vgac/stages/face_detail.py`, `prep.py`, `mesh.py`, `concept.py`, `rig.py`, `export.py`; `backend/vgac/graphs/trellis.py`; `backend/vgac/blender/pack.py`, `faceuv.py`, `rebake.py`, `rig_fix.py`, `procanim.py`; `backend/vgac/items.py`, `data/items.yaml`, `data/styles.yaml`, `data/categories/humanoid.yaml`; `unity-sandbox/Assets/Editor/VgacImport.cs` (`PickShader`, `BuildMaterial`); `frontend/package.json` (three.js 0.187); evidence pictures in `runtime/tmp/face`; the knight's clean-up model measured with a read-only script (hair chosen by colour in the top fifth of her height).

Industry practice and talks
- [I1] Guilty Gear Xrd art style, GDC 2015 (Junya Motomura): https://gdcvault.com/play/1022031/ ; handout: https://www.ggxrd.com/Motomura_Junya_GuiltyGearXrd.pdf (secondary: read as an extract)
- [I2] Outfit7, the My Talking Angela 2 hair system: https://medium.com/outfit7/the-my-talking-angela-2-hair-system-46cf06181893 (secondary: read as an extract)
- [I3] Reallusion, hair mesh production (40,000 face cap, three density tiers): https://wiki.reallusion.com/Content_Dev:CC_3.4_Hair_Mesh_Production
- [I4] Final Fantasy XV hair polygon counts (press report of Square Enix statements): https://gameranx.com/updates/id/26600/article/final-fantasy-xv-s-realistic-eyes-and-hair-graphics-look-incredible/
- [I5] Scheuermann, Practical Real-Time Hair Rendering and Shading (ATI, SIGGRAPH 2004 sketch): https://web.engr.oregonstate.edu/~mjb/cs557/Projects/Papers/HairRendering.pdf
- [I6] Calvin Verhoolen, Techniques for Game Character Hair Creation (80.lv): https://80.lv/articles/techniques-for-game-character-hair-creation
- [I7] Marketplace card haircuts (triangle counts): https://www.cgtrader.com/3d-models/character/woman/female-hair-style-hair-card ; https://flippednormals.com/product/realtime-hair-asset-ellie-4k-textures-maya-editable-file-7632
- [I8] EA, Strand hair in Dragon Age: The Veilguard (12 Nov 2024): https://www.ea.com/technology/news/strand-hair-dragon-age-the-veilguard
- [I9] Polycount, alpha test and alpha to coverage for hair (forum): https://polycount.com/discussion/comment/1206839
- [I10] MachineGames, Strand Hair in Indiana Jones and the Great Circle (SIGGRAPH 2025 Advances): https://www.advances.realtimerendering.com/s2025/content/Strand%20Hair%20in%20IJGC%20-%20Final%20Slides%20(Post-Conference).pdf
- [I11] TressFX hair in Tomb Raider (AMD, Confetti, Crystal Dynamics slides): https://www.slideshare.net/slideshow/hair-intombraider-final/20208628
- [I12] NVIDIA, The Witcher 3 graphics guide (HairWorks strand counts and cost): https://www.nvidia.com/en-us/geforce/news/the-witcher-3-wild-hunt-graphics-performance-and-tweaking-guide/
- [I13] Frostbite, Strand-based Hair Rendering (SIGGRAPH 2019 Advances): https://www.advances.realtimerendering.com/s2019/hair_presentation_final.pdf
- [I14] Unity and Arm, artists' best practices for mobile (transparency and overdraw; secondary): https://blog.unity.com/pt/technology/artists-best-practices-for-mobile-game-development

Papers
- [P1] Kajiya and Kay, Rendering fur with three dimensional textures (1989): https://history.siggraph.org/?p=119378
- [P2] Marschner et al., Light Scattering from Human Hair Fibers (2003): https://www.cs.cornell.edu/~srm/publications/SG03-hair-abstract.html
- [P3] d'Eon et al., An Energy-Conserving Hair Reflectance Model (2011): https://diglib.eg.org/items/a62ba8bb-e415-4395-9be6-9c9186ededfa
- [P4] Karis, Physically Based Hair Shading in Unreal (SIGGRAPH 2016): https://blog.selfshadow.com/publications/s2016-shading-course/karis/s2016_pbs_epic_hair.pdf
- [P5] Paris et al., Capture of Hair Geometry from Multiple Images (2004; orientation from oriented filters): https://artis.inrialpes.fr/Publications/2004/PBS04/
- [P6] Luo et al., Multi-View Hair Capture Using Orientation Fields (CVPR 2012): https://gfx.cs.princeton.edu/pubs/Luo_2012_MHC/cvpr2012MVHC.pdf

Engines
- [E1] Epic, Setting up bindings for grooms: https://dev.epicgames.com/documentation/en-us/unreal-engine/setting-up-bindings-for-grooms-in-unreal-engine
- [E2] Epic, Setting up cards and meshes for grooms: https://dev.epicgames.com/documentation/en-us/unreal-engine/setting-up-cards-and-meshes-for-grooms-in-unreal-engine
- [E3] Epic, Groom platform support: https://dev.epicgames.com/documentation/en-us/unreal-engine/groom-platform-support-in-unreal-engine
- [E4] Epic, Groom scalability and performance: https://dev.epicgames.com/documentation/en-us/unreal-engine/groom-scalability-and-performance-with-unreal-engine
- [E5] Godot, Standard Material 3D (transparency modes, alpha anti-aliasing, anisotropy): https://docs.godotengine.org/en/stable/tutorials/3d/standard_material_3d.html
- [E6] Epic, Hair Card Generator (Dataflow): https://dev.epicgames.com/documentation/metahuman/hair-card-generator-in-dataflow-in-unreal-engine ; https://dev.epicgames.com/documentation/unreal-engine/creating-hair-cards-and-lods-using-hair-card-generator
- [E7] glTF 2.0 material schema (alphaMode, alphaCutoff, doubleSided): https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html
- [E8] Epic, Dither Opacity Mask: https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Engine/Materials/UMaterial/DitherOpacityMask?application_version=5.4
- [E9] Epic, Shading models (Hair): https://dev.epicgames.com/documentation/en-us/unreal-engine/shading-models-in-unreal-engine
- [E10] Epic, Material blend modes: https://dev.epicgames.com/documentation/en-us/unreal-engine/material-blend-modes-in-unreal-engine
- [E11] Unity HDRP, Hair master stack: https://docs.unity3d.com/Packages/com.unity.render-pipelines.high-definition@17.0/manual/hair-master-stack-reference.html
- [E12] Unity 6, shaders in URP: https://docs.unity.com/en-us/engine/6000.3/manual/materials-and-shaders/built-in/shaders-in-universalrp
- [E13] three.js, MeshPhysicalMaterial and Material (anisotropy, alphaTest, alphaToCoverage, alphaHash): https://threejs.org/docs/pages/MeshPhysicalMaterial.html ; https://threejs.org/docs/pages/Material.html ; loader: https://raw.githubusercontent.com/mrdoob/three.js/dev/examples/jsm/loaders/GLTFLoader.js
- [E14] Epic, Using grooms with Mutable: https://dev.epicgames.com/documentation/en-us/unreal-engine/using-grooms-with-mutable-in-unreal-engine
- [E15] Epic, MetaHuman levels of detail (secondary: read as an extract): https://dev.epicgames.com/documentation/en-us/metahuman/controlling-metahuman-levels-of-detail-lods
- [E16] Unity glTFast 6.14, features: https://docs.unity3d.com/Packages/com.unity.cloud.gltfast@6.14/manual/features.html
- [E17] Epic, FBX material pipeline: https://dev.epicgames.com/documentation/en-us/unreal-engine/fbx-material-pipeline-in-unreal-engine
- [E18] Godot glTF importer source (alpha modes, culling, extensions read): https://raw.githubusercontent.com/godotengine/godot/master/modules/gltf/gltf_document.cpp
- [E19] KHR_materials_anisotropy: https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_materials_anisotropy/README.md

Fur
- [F1] Lengyel, Praun, Finkelstein, Hoppe, Real-Time Fur over Arbitrary Surfaces (2001): https://hhoppe.com/fur.pdf
- [F2] NVIDIA, Fur using shells and fins (2007): https://developer.download.nvidia.com/SDK/10.5/direct3d/Source/Fur/doc/FurShellsAndFins.pdf
- [F3] Froyok, breakdown of Shadow of the Colossus (third-party analysis, mirror): https://hn.jakechampion.name/reader/http://www.froyok.fr/blog/2012-10-breakdown-shadow-of-the-colossus-pal-ps2
- [F4] 80.lv, classic trick for rendering grass and fur: https://80.lv/articles/classic-video-games-trick-for-rendering-grass-fur/
- [F5] Acerola, How Do Games Render Fur? (video; content not read): https://www.youtube.com/watch?v=9dr-tRQzij4

Blender
- [B1] Blender licence and ownership of output: https://www.blender.org/about/license/
- [B2] Blender 3.5 release (hair node assets): https://www.blender.org/download/releases/3-5/
- [B3] Blender source, Quick Fur operator and Essentials loading: https://github.com/blender/blender (`scripts/startup/bl_operators/object_quick_effects.py`, `scripts/startup/bl_ui/properties_data_modifier.py`)
- [B4] glTF add-on documentation and anisotropy exporter: https://github.com/KhronosGroup/glTF-Blender-IO/blob/main/docs/blender_docs/scene_gltf2.rst ; https://github.com/blender/blender/blob/main/scripts/addons_core/io_scene_gltf2/blender/exp/material/extensions/anisotropy.py

Spring bones
- [S1] Godot, SpringBoneSimulator3D and the 4.4 release: https://docs.godotengine.org/en/stable/classes/class_springbonesimulator3d.html ; https://godotengine.org/releases/4.4/
- [S2] VRMC_springBone 1.0: https://github.com/vrm-c/vrm-specification/blob/master/specification/VRMC_springBone-1.0/README.md ; readers: https://github.com/V-Sekai/godot-vrm , https://github.com/pixiv/three-vrm , https://github.com/ruyo/VRM4U
- [S3] Unity Animation Rigging constraints: https://docs.unity3d.com/Packages/com.unity.animation.rigging@1.3/manual/ConstraintComponents.html
- [S4] UnityChanSpringBone (MIT): https://github.com/unity3d-jp/UnityChanSpringBone
- [S5] JigglePhysics (MIT): https://github.com/naelstrof/JigglePhysics
- [S6] EZSoftBone (MIT): https://github.com/EZhex1991/EZSoftBone
- [S7] UniVRM (MIT): https://github.com/vrm-c/UniVRM
- [S8] MagicaCloth 2 (paid): https://assetstore.unity.com/packages/tools/physics/magica-cloth-2-242307 ; https://magicasoft.jp/en/mc2_install/
- [S9] Dynamic Bone (paid): https://assetstore.unity.com/packages/tools/animation/dynamic-bone-16743
- [S10] Epic, AnimDynamics, Spring Controller, Rigid Body: https://dev.epicgames.com/documentation/en-us/unreal-engine/animation-blueprint-animdynamics-in-unreal-engine ; https://dev.epicgames.com/documentation/en-us/unreal-engine/animation-blueprint-spring-controller-in-unreal-engine ; https://dev.epicgames.com/documentation/en-us/unreal-engine/animation-blueprint-rigid-body-in-unreal-engine
- [S11] Kawaii Physics (MIT): https://github.com/pafuhana1213/KawaiiPhysics

Tools, textures and shaders
- [T1] lilToon (MIT): https://github.com/lilxyzw/lilToon
- [T2] Hair Alphas For Days (CC0): https://opengameart.org/node/85661
- [T3] Unity hair package (Unity Companion Licence): https://github.com/Unity-Technologies/com.unity.demoteam.hair
- [T4] jiaozi158/ShellFurURP (MIT, one folder Unity Companion Licence): https://github.com/jiaozi158/ShellFurURP
- [T5] hecomi/UnityFurURP (MIT): https://github.com/hecomi/UnityFurURP
- [T6] Arnklit/ShellFurGodot (MIT, Godot 3.4): https://github.com/Arnklit/ShellFurGodot
- [T7] Squiggles Fur (MIT, archived): https://github.com/QueenOfSquiggles/squiggles-fur
- [T8] GBH Tool (GPL-2.0-or-later): https://github.com/GixoXYZ/BlenderGBHTool
- [T9] Hair cards from curves (no licence stated): https://3dbystedt.gumroad.com/l/hairCardsFromCurves
- [T10] Unreal Engine EULA: https://www.unrealengine.com/en-US/eula/unreal

Models
- [M1] HairDeck code and licence: https://github.com/hyz317/HairDeck ; https://raw.githubusercontent.com/hyz317/HairDeck/main/LICENSE
- [M2] HairDeck weights: https://huggingface.co/hyz317/HairDeck ; paper: https://arxiv.org/html/2610.40599v1
- [M3] HairDeck community ComfyUI node (memory figure): https://github.com/visualbruno/ComfyUI-HairDeck ; news: https://comfyui-wiki.com/en/news/2026-11-02-hairdeck ; DINOv3 licence: https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md
- [M4] CHARM (GPL-3.0): https://huggingface.co/hyz317/CHARM
- [M5] HairStep licence (CC BY-NC 4.0): https://raw.githubusercontent.com/GAP-LAB-CUHK-SZ/HairStep/main/LICENSE
- [M6] DiffLocks licence (non-commercial research): https://raw.githubusercontent.com/Meshcapade/difflocks/main/LICENSE
- [M7] HAAR licence (CC BY-NC 4.0): https://raw.githubusercontent.com/Vanessik/HAAR/master/LICENSE.txt
- [M8] Im2Haircut (CC BY-NC-SA 4.0 per its README): https://github.com/Vanessik/Im2Haircut
- [M9] Neural Haircut: https://raw.githubusercontent.com/Vanessik/NeuralHaircut/main/LICENSE.txt ; GaussianHaircut: https://raw.githubusercontent.com/eth-ait/GaussianHaircut/main/LICENSE.md ; MonoHair: https://raw.githubusercontent.com/KeyuWu-CS/MonoHair/master/LICENSE.txt
- [M10] Perm: https://raw.githubusercontent.com/c-he/perm/main/LICENSE ; Hair20k terms: https://zhouyisjtu.github.io/project_hair/hair20k.html
- [M11] StdGEN: https://github.com/hyz317/StdGEN ; https://huggingface.co/hyz317/StdGEN ; paper: https://arxiv.org/abs/2411.05738
- [M12] Bald converters: HairPort (CC BY-NC-ND): https://deepmancer.github.io/HairPort/ ; Stable-Hair (weights licence not verified): https://github.com/Xiaojiu-z/Stable-Hair ; Qwen-Image-Edit-2511 (Apache-2.0): https://huggingface.co/Qwen/Qwen-Image-Edit-2511
- [M13] Strands2Cards (no licence): https://github.com/kenji-tojo/strns2cards
- [M14] TRELLIS.2 licence (MIT): https://raw.githubusercontent.com/microsoft/TRELLIS.2/main/LICENSE

Segmentation
- [G1] MediaPipe Hair Segmenter model card: https://storage.googleapis.com/mediapipe-assets/Model%20Card%20-%20Hair%20Segmentation.pdf
- [G2] MediaPipe Selfie Multiclass model card: https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Multiclass%20Segmentation.pdf
- [G3] SAM 2: https://github.com/facebookresearch/sam2
- [G4] SAM 3 licence: https://raw.githubusercontent.com/facebookresearch/sam3/main/LICENSE
- [G5] face-parsing.PyTorch: https://github.com/zllrunning/face-parsing.PyTorch ; CelebAMask-HQ terms: https://github.com/switchablenorms/CelebAMask-HQ
- [G6] jonathandinu/face-parsing: https://huggingface.co/jonathandinu/face-parsing
- [G7] Sapiens licence (CC BY-NC 4.0): https://raw.githubusercontent.com/facebookresearch/sapiens/main/LICENSE

Not verified, in one place
1. Official hair triangle budgets for Overwatch, Fortnite, Genshin Impact, Zelda, Uncharted 4, The Last of Us Part II, Horizon, God of War and The Witcher 3 without HairWorks. None was found; forum numbers were left out.
2. Typical card texture sheet sizes and bones per hair chain: only anecdotes.
3. Strand hair details for Insomniac, Guerrilla, Remedy and Ubisoft games.
4. How Unreal 5.8 maps a glTF cut-out material and which glTF extensions it reads; whether alpha cut-off or two-sided survives an FBX import in Unity, Unreal or Godot.
5. The CC0 statement for Blender's bundled hair assets (seen as an extract only); the hair asset file's path and the modifier input interface on Blender 5.2.3.
6. HairDeck's memory use and speed on a 3060 or 3080, and its results on painted (not anime) characters. Whether Apache-2.0 on weights trained from VRoid characters the authors cannot redistribute would hold if challenged.
7. Whether Qwen-Image-Edit makes a reliable bald version or a hair-only picture, and how TRELLIS.2 builds a wig picture.
8. Whether a strand bump map alone gives a convincing stretched highlight under a standard material (stage 1 tests it).
9. Unity 6 compatibility of the two shell-fur repositories; the licence text of the VRM spring-bone specification; xNormal's terms; Hair Tool's licence text.
10. Every time and triangle figure marked "(estimate)".
