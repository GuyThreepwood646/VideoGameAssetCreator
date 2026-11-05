# Character parts, accessories and animation quality: research

Written 2026-11-04 in answer to the owner's notes on the finished ranger (sword and quiver on his back): the death left his hands in the air, the attack did not use his sword, and the run looked clunky. Questions answered: how animations can look more natural; whether the mesh can tell the character apart from his accessories; whether to generate a bare base body first and add clothes, weapons and accessories afterwards; and how the industry does it.

Licences were read from the project's own LICENSE files or model cards where possible; where only a secondary source was found, the table says so. Nothing was downloaded or run on a graphics card. One small CPU-only script read the clip names out of the two animation libraries already in `data/animations`.

---

## Summary

**What the industry does.** Game studios do not make a character, his clothes and his weapon as one piece. A character is a body plus clothing, each a separate skinned mesh that follows one shared skeleton. Unreal does this with Leader Pose or Copy Pose, Unity by pointing each SkinnedMeshRenderer at the same bones. Body areas a garment covers are hidden or deleted so they cannot poke through. Weapons, shields, hats and backpacks are separate rigid meshes fixed to a named attachment point (a "socket") on a bone: right hand, left hand, back, hip. Drawing and sheathing a sword means switching it from the back or hip socket to the hand socket at one frame of the draw animation (an animation event). Hair is usually cards: flat textured strips. Cloaks, hair, tails and quivers get secondary motion from spring bones or cloth simulation. Animation comes from hand-made or motion-capture libraries retargeted onto the skeleton, with foot IK so feet stay planted.

**What VGAC does today, and why the ranger looks wrong.** Image-to-3D (TRELLIS.2) returns one fused, watertight shell, so the sword and quiver are part of the body surface and SkinToken skins them to the spine and shoulders like skin. Read from the code:

1. The app viewer and the GLB, Godot and Unreal files carry only the procedural clips from `backend/vgac/blender/procanim.py`. The hand-made CC0 library (Quaternius UAL1) reaches only the Unity prefab, through Humanoid retargeting (`export.py`, `clips_library`). What the owner most likely watched was the procedural set.
2. Procedural death (`_death_pose`): the body tips 90 degrees onto its back while each upper arm turns 30 degrees forward and 35 degrees outward. On a body lying face up, "forward" points at the sky, so the hands rise and stay raised.
3. Procedural attack (`clip_attack`): the right upper arm swings up to 150 degrees and comes down. It swings an empty hand, because the sword is a lump of the back surface skinned to the spine; nothing can move it into the hand.
4. Procedural run (`_walk_like` with `run=True`): fixed-angle leg swings with a vertical bob and no foot planting, no flight phase, and arm swing tied to the same sine. Feet slide and the motion reads as mechanical.

**What is feasible now, on 10 to 12 GB cards with commercially safe licences.**

1. Use the hand-made CC0 clips (UAL1 and UAL2, already on disk, 89 clips including `Sword_Regular_A/B/C`, `Sword_Block`, `Idle_Shield_Loop`, `Death01`, `Jog_Fwd_Loop`) in every output, not only Unity. Bake them onto the rig in Blender with rest-pose compensation and a foot-lock pass. This is the biggest visible gain for the least risk.
2. Generate characters empty-handed with nothing on the back. The prompt does most of it; Qwen-Image-Edit-2511 (Apache-2.0, already installed) removes items the painter still adds.
3. Generate each weapon or carried item as its own asset through the existing rigid-prop path, with the pivot at a detected grip point, and attach it to sockets added under the hand, back, hip and head bones.
4. Choose the clip set from what is attached (sword set when a sword is in the right hand, shield idle when a shield is on the left forearm).
5. Spring bones for cloaks, hair and tails, using free MIT implementations in Unity and Godot's built-in SpringBoneSimulator3D. This needs chain bones the rigger does not always produce, so it comes later.

**What is not feasible yet.** Cutting a finished fused character into clean parts: the best open models are either non-commercial (PartField, PartPacker, OmniPart through its PartField code), restricted by territory (Hunyuan3D-Part excludes the EU, UK and South Korea), or too big (SegviGen needs 24 GB). Even a perfect cut leaves a hole in the back and a sword with no hidden faces. Garment generation and fitting onto a generated base body: research code, often non-commercial (Garment3DGen is CC BY-NC), built on SMPL bodies and sewing patterns, and aimed at realistic clothes, not stylised game armour. Local text-to-motion: everything trained on AMASS or HumanML3D is unusable for commercial output. The one commercially licensed option, NVIDIA Kimodo (SOMA checkpoints), is worth a later trial but is not ready to be a default.

---

## 1. How the industry builds modular characters

### 1.1 Findings

| Practice | How it is done | Source |
|---|---|---|
| Body plus clothing on one skeleton | Each garment is its own skinned mesh weighted to the same skeleton. Unreal offers three ways: Leader Pose (children follow the leader's bones, cheapest on the game thread, but the child's joint hierarchy must match exactly), Copy Pose From Mesh (copies matching bones only), and Skeletal Mesh Merge (one mesh at runtime, highest setup cost, lowest render cost). Unity: rebuild each piece's `SkinnedMeshRenderer.bones` array by name against the target skeleton; Modular Avatar's Merge Armature automates this for outfits. | [U1], [U2], [U3] |
| Slots and armour sets | Bungie's Destiny split the body into four gear slots, built a reusable base armour so new pieces did not start from scratch, and wrote a preview tool to catch pieces clipping through each other. | [G1], [G2] |
| Hiding the body under clothing | Covered body areas are hidden or deleted, by per-region toggles (Cyberpunk 2077 hides a body region only when clothing fully covers it, or holes show) or by "shrink" blend shapes that collapse the covered region (Modular Avatar), which tools may turn into deleted polygons for speed. | [G3], [G4], [G5] |
| Weapons and props on sockets | Rigid meshes are attached to named sockets on bones, with an offset. Unreal: sockets are made in the Skeleton Tree and can be moved, rotated and scaled; Mesh Sockets live on one mesh when skeletons are shared. The Unreal mannequin carries a dedicated weapon bone (`ik_hand_gun`) so a weapon's motion can be keyed apart from the hand. Godot: a `BoneAttachment3D` node follows a named bone. Unity: parent the weapon under the hand transform (or a child empty), or use Animation Rigging's Multi-Parent Constraint to blend between holster and hand anchors. | [E1], [E2], [E3], [E4] |
| Draw and sheathe | The weapon is reattached from the back or hip socket to the hand socket by an animation notify placed at the frame the hand reaches it; unequip is the reverse. Montage notifies are the reliable kind in Unreal. | [E5] |
| Hair | Real-time hair is mostly cards: textured strips. Unreal can generate cards from strand grooms; many teams author strands only for cinematics and cards for gameplay. | [H1], [H2] |
| Secondary motion | Spring bones for hair, cloth and tails: VRM's SpringBone extension (deliberately simple, aimed at stylised hair and clothes), Godot 4's `SpringBoneSimulator3D` (per-joint stiffness, drag, gravity, its own colliders, external force for wind), UnityChan SpringBone (MIT). Cloth simulation: Unity's Cloth component (skinned meshes only, per-vertex max distance, collides only with listed sphere and capsule colliders). | [S1], [S2], [S3], [S4], [S5] |
| Naming conventions | Mixamo and VRM both name a fixed humanoid bone set (hips, spine, chest, neck, head, shoulders, arms, hands, legs, feet, optional fingers). Neither defines weapon bones; sockets are added per project. | [A1], [S1] |

### 1.2 Verdict

The industry answer to "can the mesh tell the character from his accessories" is "they are never one mesh in the first place." Weapons and carried props are separate rigid meshes on sockets; clothing is separate skinned meshes on a shared skeleton. For VGAC the weapon half of this is cheap to copy (rigid props already exist as a category, sockets are a few empties). The clothing half depends on fitting generated garments to a generated body, which section 3 finds is not yet practical.

---

## 2. Separating a fused generated mesh into parts

### 2.1 Methods

Hardware column: "fits" means it is stated or reasonably expected to run on the 12 GB 3060 or the 10 GB 3080. "Unknown" means no figure was found.

| Method | What it gives | Characters with accessories | Hardware | ComfyUI node or script | Licence (code; weights) | Verdict |
|---|---|---|---|---|---|---|
| Hunyuan3D-Part: P3-SAM + X-Part [P1], [P2] | P3-SAM: per-face part labels and part boxes on any mesh. X-Part: regenerates each part as a complete shape. The public X-Part is a "light version"; the full one is in Tencent's paid studio. | Built for exactly this; no published numbers on characters. | No figure published. | Python scripts in the repo; community wrappers (Modly). | Tencent Hunyuan 3D-Part Community Licence: territory excludes the EU, UK and South Korea, and outputs used outside the territory are unlicensed; a licence request is needed above 1 million monthly users; outputs may not be used to improve other AI models. [P2] | **Restricted. Not a default.** At most an opt-in with a territory warning, like the existing Hunyuan3D-2mv entry. A free app cannot control where its users live. |
| PartField (NVIDIA) [P3], [P4] | A feature field; clustering gives a hierarchy of parts. | Good general part splits in the paper. A ComfyUI wrapper exists. | Fits with `n_point_per_face` lowered. | `partfield_inference.py`; community ComfyUI wrapper [P5]. | NVIDIA Source Code Licence: "only may be used ... non-commercially". | **Unusable (non-commercial).** |
| PartPacker (NVIDIA) [P6], [P7] | Image to a part-separated mesh (dual volume packing). | Object-oriented; untextured. | About 10 GB in fp16. | Scripts and Gradio app. | NVIDIA Source Code Licence, non-commercial. | **Unusable (non-commercial).** |
| OmniPart [P8], [P9], [P10] | Image plus a 2D part mask to textured parts, built on TRELLIS (v1). | Promising for props; needs a hand-made or SAM-made part mask. | Unknown. | `scripts.inference_omnipart`, Gradio app. | Code and weights listed MIT, but the project NOTICE says the software "contains code derived from PartField", which is under the NVIDIA non-commercial licence. | **Unusable as shipped** until that PartField-derived code is removed or relicensed. |
| PartCrafter [P11], [P12] | Image to several part meshes at once (set the number of parts). | Untextured; trained on object parts, not clothed characters. | At least 8 GB, fits. | Python script; no ComfyUI node found. | MIT code and MIT weights. Built on TripoSG (MIT). Its default background remover is BRIA RMBG-1.4, which carries its own non-commercial terms; swap in BiRefNet (MIT, already used). | **Usable licence; weak fit.** Gives no textures and was not shown on characters. Keep on a watch list for props. |
| HoloPart (VAST) [P13], [P14] | Completes parts that are hidden or cut ("amodal"): given a mesh and a per-face part mask, it fills in each part's missing surfaces. | Exactly the "hole in his back after removing the quiver" problem; needs a segmentation first. | 2B parameters in fp16 (about 4 GB of weights); expected to fit, not verified. | `scripts.inference_holopart`. | MIT code; weights card says MIT. | **Usable; later experiment** as the second half of a cut-and-complete tool. Re-texturing the completed surfaces is still unsolved. |
| SAMPart3D [P15] | Per-object part segmentation at several detail levels, by distilling 2D features into a 3D network. | Good quality in the paper, but it trains per object. | Unknown; needs Blender renders and a PTv3 backbone. | Scripts; no node. | MIT. | **Usable licence, impractical.** Per-object training is too slow for an autopilot step. |
| BANG (Deemos and ShanghaiTech) [P16], [P17] | Exploded-view generation that splits an asset into parts. | Shown on many objects. | n/a | No code or weights; the demo is "coming soon" on Hyper3D. | None published. | **Unavailable.** |
| SegviGen [P18], [P19] | Part colours predicted on TRELLIS.2 voxels and carried back to the mesh; interactive (click points), full, or 2D-guided. | Native to our generator. | "At least 24GB". | Scripts. | MIT (TRELLIS.2 code under its own MIT terms). | **Does not fit our cards.** Watch for a smaller version. |
| Trellis_Seg [P20] | TRELLIS.2-based segmentation latents. | Research only. | Unknown. | No turnkey inference on arbitrary meshes. | MIT. | **Not ready.** |
| Seg3DParts, FullPart [P21], [P22] | Part-level generation on TRELLIS latents. | Papers. | Unknown. | No code found. | Unknown. | **Watch list.** |
| Connected components before the solid remesh | Free: pieces that do not touch come out separate. | Only works for items floating clear of the body (a sword held out, a staff). Straps, quivers and scabbards touch the body and fuse. | CPU. | Trimesh or Blender, a few lines. | n/a | **Cheap check, rarely useful.** Run it, but expect one component. |
| Colour or material clustering in texture space | Clusters the baked albedo. | Unreliable: a steel sword and steel armour look alike; leather quiver and leather jerkin alike. | CPU. | Own code. | n/a | **Not on its own.** Only as a hint to another method. |
| SAM on multi-view renders projected back to faces (SAMesh approach) [P23] | Render 6 to 12 views in Blender, segment each with SAM 2 (Apache-2.0) or SAM 3 (text prompts such as "sword", "quiver"), vote per face. | The most direct usable option for "which faces are the sword". Thin straps and parts touching the body still bleed. | SAM 2 or 3 fit easily. | Own script; Blender renders already exist in the app. | SAM 2: Apache-2.0. SAM 3: Meta SAM Licence, royalty-free and commercial, but bars military and weapons-development use and must be passed on with the model [P24]. Game swords are not weapons development, but the clause should be noted in the manifest. SAMesh's own code licence was not found; write our own. | **Best usable segmentation.** Medium effort. |

### 2.2 Verdict

The mesh can be made to tell the sword from the ranger, with multi-view SAM today, or with Hunyuan3D-Part if territory limits were acceptable. It does not solve the problem well, though. After the cut, the back has a hole where the quiver sat. The sword has no faces where it touched the back. Its texture is half of a shared atlas. Its skin weights belong to the spine. HoloPart could fill both holes geometrically, but the new surfaces would have no texture. Generating the character and each item separately avoids all of this and costs one more TRELLIS.2 run per item (about 2 minutes on the mobile budget, per STATUS.md). Recommendation: do not build "cut parts off a finished character" as the main path. Keep multi-view SAM as a later rescue tool for characters made before the change.

---

## 3. Generating in layers: base body first, then garments and accessories

### 3.1 Methods

| Method | What it gives | Licence | Fits our hardware | Verdict |
|---|---|---|---|---|
| Garment3DGen (Meta) [L1], [L2] | Stylises a template garment mesh from an image and fits it to a parametric body, ready to simulate. | CC BY-NC 4.0 | Unknown | **Unusable (non-commercial).** |
| ChatGarment [L3] | A vision-language model writes GarmentCode sewing patterns; patterns are stitched and draped in simulation. | Apache-2.0 code; built on LLaVA and LISA, whose base models carry their own terms; body model terms not stated. | Trained on H100s; inference size unknown, likely over 12 GB. | **Not now.** Realistic clothes, not stylised armour; heavy stack. |
| GarmentCode [L4] | Parametric sewing patterns in code plus a simulation pipeline. | MIT | CPU and GPU simulation | **Interesting library; not a generator.** Would need our own body and draping. |
| DressCode [L5], [L6] | Text to sewing pattern (SewingGPT) plus PBR textures. | No licence file found in the repo, so all rights reserved by default. | Unknown | **Unusable (no licence).** |
| GarmentDreamer [L7] | Gaussian-splat-guided garment meshes from text. | Licence name not shown; key parts and weights unreleased. | One GPU, size unknown | **Unusable for now.** |
| Image edit to make a clothed and an unclothed version, then two 3D runs | Qwen-Image-Edit-2511 turns the clothed concept into "the same character in a plain grey bodysuit" (or the reverse). Each image goes through TRELLIS.2 separately. | Apache-2.0 [L8]; already installed. | Fits (already runs here). | **Works for rigid add-ons, not for clothes.** The two meshes do not line up: pose, proportions and surface shift between runs, so a jerkin from run A will not sit on the body from run B without shrink-wrap and cleanup. |

**How clothing is fitted and skinned in production.** The garment is modelled over the base body in the same pose. Its weights are copied from the body: nearest-surface transfer (Blender's Data Transfer modifier) or the more robust inpainting method from SIGGRAPH Asia 2023, which copies weights only where the surfaces match and smoothly fills the rest. Its research code is public and a free Blender add-on exists; the licence of either was not confirmed [L9], [L10]. Shrink-wrap pulls loose areas onto the body, then a few frames of cloth simulation settle folds. Covered body faces are hidden or deleted, and loose parts (skirts, capes) get extra chain bones or engine cloth. Every step assumes the garment was made for that body, and that is the part generation does not yet give us.

**The base body must be a neutral, non-explicit form.** Studios use a mannequin-like body in a plain fitted bodysuit or base layer (tunic and trousers, or a leotard-style suit), smooth with no anatomical detail. Never nudity. If VGAC ever makes a base body, the prompt and negative should enforce it, for example:

- Positive: "full-body game character base mesh, neutral A-pose, arms 45 degrees from the body, hands open and empty, wearing a plain fitted grey bodysuit covering the torso, arms and legs, simple flat shoes, no accessories, no weapons, no hair ornaments, smooth stylised mannequin forms, front view, plain white background".
- Negative (for painters that take one): "nude, naked, underwear only, lingerie, exposed skin on torso, anatomical detail, cleavage, weapon, sword, bow, shield, backpack, quiver, cape, hat".
- Checks: run the existing local judge on the base-body concept with a "fully clothed in a bodysuit" yes or no question, and refuse to continue on a no.

### 3.2 Verdict

Base-body-then-clothes is how studios work, but it depends on garments made for the body, and generated garments are not there yet. Every serious garment model is research code. The usable ones produce realistic sewing-pattern clothes on SMPL bodies, and the closest commercial-safe trick (image edit plus two 3D runs) gives meshes that do not register. For VGAC the realistic version of "layers" today is: clothing stays fused with the body (it deforms correctly there, since it is skinned with it), and only rigid items are split out: weapons, shields, quivers, backpacks, hats and helmets. Revisit garment layering when a commercially licensed garment-on-body model that fits 12 GB appears.

---

## 4. Weapons and accessories as separate assets

### 4.1 Pipeline for VGAC

1. **Character concept, empty-handed.** When the description names carried items, the concept prompt adds "hands open and empty, nothing on the back, nothing at the belt" and the negative (where supported) lists the items. If the chosen concept still shows them, run Qwen-Image-Edit-2511 with "remove the sword and the quiver, keep everything else the same". The quiver strap is the usual leftover; the edit prompt names it.
2. **Each item as its own asset.** A new category, "held item" (a variant of `prop_rigid`), takes the item words from the description ("a short sword with a leather grip"). It makes a side-view concept on a plain background and runs the normal mesh, prep and export chain. It must be the same style recipe as the character so the look matches (the recipes work in `0c145b9` carries the style through every step).
3. **Grip point detection.**
   - Find the long axis by PCA of the vertices.
   - Cut 64 slices along it and measure each slice's width and roundness.
   - The handle is the longest run of thin, nearly constant, roughly round slices. A guard is a sudden width jump next to it; the blade is the long flat run beyond the guard; the pommel is the small bulge at the far end.
   - Grip point: the middle of the handle run. Grip axis: the long axis. Blade direction: from the handle toward the guard.
   - Bows: the grip is the thickest slice near the middle of the arc. Staffs and spears (no clear handle): 35 to 40 percent of the length from the bottom.
   - Each guess carries a confidence. Below the threshold, the item still exports with the pivot at the guessed point, and the "Add an item" screen asks "Click where the hand holds it". This is the same policy as the pieces-that-move plan: a wrong guess is shown, never silently used.
4. **Sockets on the character.** Empties under the bones, placed by geometry, since `rig_fix.py` already finds body parts that way:

| Socket | Parent bone | Placement |
|---|---|---|
| `socket_hand_R`, `socket_hand_L` | RightHand, LeftHand | Palm centre: about 40 percent from the wrist toward the fingertips, pushed into the palm by a quarter of the hand thickness. X along the fingers, Z out of the palm. |
| `socket_forearm_L` | LeftForeArm | Two thirds down the forearm, on the outer side (shields). |
| `socket_back` | Spine2 (chest) | Centre of the upper back, just off the surface, from the back silhouette depth. |
| `socket_hip_L`, `socket_hip_R` | Hips | At the side of the pelvis, at belt height. |
| `socket_head` | Head | Top of the skull, from the head bounds. |

5. **Default pose per item type** (offsets relative to the socket; the user nudges from here):

| Item | In use | Put away |
|---|---|---|
| One-handed sword, axe, mace | Right hand, grip on the socket, blade up and forward along the thumb side. | Left hip, blade down and back about 20 degrees; or back, diagonal, handle over the right shoulder. |
| Bow | Left hand, grip on the socket, string toward the body, bow upright. | Back, diagonal. |
| Shield | Left forearm, face outward, centre on the socket. | Back, flat. |
| Staff, spear | Right hand at 35 to 40 percent from the bottom, upright. | Back, near-vertical. |
| Quiver | Back, mouth up over the right shoulder (right-handed archer). | Same. |
| Backpack | Back, straps toward the shoulders. | Same. |
| Hat, helmet | Head, pivot at the inner rim centre, scaled to the head bounds. | Same. |

6. **What engines expect.** Unity: the item is a child of the socket transform under the hand bone. A prefab variant "Ranger (equipped)" has the items attached; the bare "Ranger" stays clean. Animation Rigging's Multi-Parent Constraint lets a game blend between the hip and hand anchors [E4]. Unreal: sockets on the skeleton, items as static meshes attached by socket name [E1]. FBX empties under bones come in as extra bones, not sockets. So the Unreal folder should list sockets in `attachments.json` plus a small editor Python script that creates them (to verify). Godot: a `BoneAttachment3D` per socket with the item as its child [E3]. Godot's glTF importer creates these for nodes parented to bones (to verify on one import).
7. **Clips that use the item.** With a one-handed sword in the right hand, the role picker uses the sword set: `Sword_Idle`, `Sword_Regular_A/B/C` and the combo, `Sword_Block`, `Sword_Dash_RM` (UAL1 and UAL2). With a shield on the left forearm it uses `Idle_Shield_Loop`, `Shield_OneShot`, `Shield_Dash_RM`. With a staff it uses the `Spell_Simple_*` set. With nothing in the hands it uses `Punch_*` and the plain idle.
8. **Known limit: fingers.** SkinToken gives no finger bones (decision D2), so the hand stays a mitten around the handle. On stylised characters this reads fine at game distance; on realistic ones the open hand is visible. Fingers would need another rigger (Make-It-Animatable was rejected for its environment and its paid add-on). A cheap improvement is a "hand closed" squeeze: scale the hand mesh's finger region toward the grip with a corrective shape, generated per character. Treat it as optional.

### 4.2 Verdict

Feasible now with no new model. It reuses the rigid-prop chain, the style recipes, the geometry-based bone finder and the Unity importer. It fixes the owner's second complaint directly: with the sword in `socket_hand_R`, `Sword_Regular_A` swings the sword.

---

## 5. Animation quality

### 5.1 Clip libraries

| Library | What it gives | Licence | Can the app ship it? | Verdict |
|---|---|---|---|---|
| Quaternius Universal Animation Library 1 and 2 [M1], [M2] | 46 + 43 hand-keyed clips on a universal humanoid rig, including sword, shield, spell, death, jog, sprint, roll, climb. Already in `data/animations`. | CC0 | Yes | **Use everywhere (already licensed and on disk).** |
| Mesh2Motion assets [M3] | Rigs and animations (some from Quaternius) with Blender sources. | CC0 assets, MIT code | Yes | **Usable; add if it fills gaps** (more deaths, two-handed moves). |
| Mixamo [M4] | Large motion-capture library on a standard rig. | Royalty-free in games, but redistributing the raw character or animation files is not allowed, and it needs an Adobe login. | No | **User-supplied only.** The app may import a Mixamo FBX the user downloaded; it must never bundle or fetch them. |
| CMU Graphics Lab motion capture [M5], [M6] | About 2,600 raw captures. | The original site is quoted as free for research and commercial projects; some later conversions are CC BY-NC (for example the 4TU FBX set). | Raw BVH only | **Usable for the original data, with cleanup.** Raw capture has foot slide and noise; low priority. |
| Rokoko free packs [M7] | Several free mocap collections (fight, walk and run, idle, dance, magic). | Free for commercial use in your project; redistribution terms not found. | Not as bundled files | **User-supplied only.** |
| AMASS [M8] | Unified mocap archive used by most research. | Non-commercial; explicitly forbids training networks for commercial use. | No | **Unusable.** |
| Bandai Namco Research Motion Dataset [M9] | 3,000+ stylised moves. | CC BY-NC 4.0 (one article says BY-NC-ND) | No | **Unusable (non-commercial).** |

### 5.2 Retargeting methods

| Method | What it does | Where | Verdict |
|---|---|---|---|
| Rest-pose compensation | Source and target rest poses differ (UAL is a T-pose; SkinToken output is close to an A-pose). Per bone, apply the source's rotation relative to its own rest pose on top of the target's rest. Without it, arms float or clip. | Our own Blender bake. Unity Humanoid does this internally. | **Required** for the Blender bake. |
| Humanoid muscle space | Unity converts both rigs to muscle values; works across proportions; foot IK option on the clip and `HumanPose` IK goals [R1], [R2]. Known to slide feet when leg lengths differ [R3]. | Unity, already used. | **Keep; turn Foot IK on for locomotion clips.** |
| IK retargeting | Unreal's IK Retargeter maps chains by name with fuzzy matching, and adds IK goals for feet and hands to keep contacts (speed planting, stride warping) [R4]. | Unreal side. | **Document for Unreal users;** our corrected Mixamo names make chains map automatically. |
| Hip-height scaling and foot locking | Scale root translation by leg-length ratio, then detect foot contact frames (foot speed near zero and foot near the ground) and pin the foot with two-bone IK over those frames. | Our Blender bake, after rest-pose compensation. | **Build.** This is what removes the clunky slide. |

### 5.3 Text-to-motion models that run locally

| Model | Output | Licence | Hardware | Retarget to our Mixamo-style rig | Verdict |
|---|---|---|---|---|---|
| MDM, MoMask, T2M-GPT, MotionGPT [T1], [T2] | SMPL-joint motion from text. | Code mostly MIT, but the weights are trained on HumanML3D, which is built from AMASS; AMASS forbids commercial use and training for commercial use. SMPL itself is non-commercial. | Fit | Possible via SMPL joint mapping | **Unusable (non-commercial training data).** |
| HY-Motion 1.0 and Lite (Tencent) [T3], [T4] | Skeleton motion from text, 1B and 0.46B parameters. | Tencent Hunyuan Community Licence, EU gated. | 26 GB and 24 GB minimum | Not documented | **Does not fit, and restricted.** |
| Kimodo (NVIDIA), SOMA and G1 checkpoints [T5], [T6], [T7], [T8] | 30-joint SOMA skeleton, root plus rotations at 30 fps, with keyframe and contact constraints. Trained on Bones Rigplay, a licensed 700-hour mocap set. | Code Apache-2.0. SOMA weights: NVIDIA Open Model Licence, "ready for commercial use". The SMPL-X checkpoint is research-only. The text encoder is Meta-Llama-3-8B-Instruct (gated download, Meta Llama 3 Community Licence [T9]). | Official figure not given; the Llama encoder is about 15 GB, with community 4-bit quantisation at 5 to 8 GB and a CPU option; a GGML port runs in under 2 GB but slowly. | SOMA names are close to Mixamo's, except the legs shift by one (SOMA `LeftLeg` is the thigh). A name map plus rest-pose offset works. | **Usable licence; trial later.** It is the only commercially clean local text-to-motion found. As an opt-in "Make a custom move" it could cover moves the libraries lack. Not a default: a large extra download, a gated Llama account step, and unknown quality on stylised proportions. |

### 5.4 Procedural polish

| Technique | Free implementation | Licence | Verdict |
|---|---|---|---|
| Foot IK in engine | Unity Humanoid Foot IK; Unity Animation Rigging Two Bone IK [R1], [E4]; Godot `SkeletonModifier3D` IK nodes | Part of the engines | **Turn on in the Unity importer for locomotion states.** |
| Spring bones for cloaks, hair, tails, quivers | UnityChan SpringBone [S3]; UniVRM SpringBone [S4]; Godot `SpringBoneSimulator3D` [S2] | MIT, MIT, engine built-in | **Build later**, once the rig has chain bones in those regions (SkinToken adds `Extra_` bones only sometimes). The rig step would add a 3 to 5 bone chain down the back of a cloak or along a tail where the mesh hangs free. |
| Cloth | Unity Cloth [S5] | Engine built-in | **Not by default.** Needs the cloak as its own skinned mesh and capsule colliders; fused cloaks cannot use it. |

### 5.5 Verdict

The quickest gain is to stop shipping procedural clips to humanoids where a hand-made CC0 clip exists, and to retarget those clips properly (rest pose, hip scaling, foot locks) into every output. The procedural set stays for creatures and as the fallback. Two small fixes are also worth making to it now:

1. Death: drive the arms in world space toward the ground, or blend them to the relaxed rest after the body lands, so they fall to the sides.
2. Run: add a short flight phase and planted feet.

---

## 6. Recommendation for VGAC

### 6.1 In priority order

| # | What | Benefit in plain words | Effort | Needs | Risk |
|---|---|---|---|---|---|
| 1 | Hand-made clips in every output. Retarget UAL1 and UAL2 onto the rig in Blender (rest-pose compensation, hip scaling, foot locking) and put them in the GLB, the viewer, Godot and Unreal files, as Unity already gets them. Use procedural clips only when no library clip fits. | "He runs, swings and falls like a real game character, everywhere you look at him." | M | Nothing new: CC0 files already on disk. | Rest-pose differences between characters; mitigated by the geometry bone finder. Foot lock tuning. |
| 2 | Fix the procedural death and run. Arms fall to the sides; the run gets a flight phase and planted feet. | "Creatures and odd-shaped characters die and run naturally too." | S | Nothing | Low |
| 3 | Empty-handed characters. Item words in the description are routed to separate items. The concept prompt asks for empty hands and a clear back; Qwen-Image-Edit removes leftovers. | "The weapon no longer bends with his back, and his back has no lump when the sword is in his hand." | S | Nothing new (Qwen-Image-Edit-2511 installed, Apache-2.0) | Painters sometimes still add straps; the edit pass and a judge question catch most. |
| 4 | Sockets. Add hand, forearm, back, hip and head empties to every humanoid rig by geometry, exported in all formats (gap G24). | "You can put any item in his hand or on his back in your game without lining it up yourself." | S to M | Nothing | Unreal socket import needs one manual check. |
| 5 | Items as their own assets with "Add an item". A held-item category with grip detection; default poses per item type; equipped prefab variant; `attachments.json`. | "Make a sword once, give it to any character, and he holds it properly." | M to L | Nothing new | Grip detection on odd weapons; the click fallback covers it. Hands stay mitten-like (no finger bones). |
| 6 | Clip set follows the items. Sword set when a sword is in hand, shield set for a shield, spell set for a staff. | "With a sword he fights with the sword; with a bow he does not punch." | S (after 1 and 5) | Nothing | Library lacks bow-draw clips; Mesh2Motion or a Kimodo trial may fill it. |
| 7 | Spring bones for cloaks, hair and tails. Add chain bones where the mesh hangs free; set up UnityChan SpringBone (MIT) or the VRM one in Unity and SpringBoneSimulator3D in Godot. | "His cloak and hair sway as he moves." | M | MIT package (small) | Fused cloaks pull on the body around the chain; needs weight painting rules. |
| 8 | Kimodo trial (opt-in, later). "Describe a move" for one-off moves, retargeted from SOMA. | "Ask for a move that is not in the list." | L | Kimodo SOMA weights (size not stated) plus Llama 3 8B (about 16 GB, gated, Meta licence); Apache-2.0 code, NVIDIA Open Model Licence | Large download, account step, quality unknown on stylised bodies, VRAM tight on 10 GB. |
| 9 | Rescue tool for old fused characters (later). Multi-view SAM 2 or SAM 3 face labels; optional HoloPart completion. | "Take the sword off a character you already made." | M to L | SAM 2 (about 0.9 GB, Apache-2.0) or SAM 3 (Meta SAM Licence); HoloPart (MIT, 2B parameters) | Holes and missing texture after the cut; regeneration is usually better. |

### 6.2 Design sketch: weapons and accessories as their own assets

**User goal.** "My ranger should carry a sword and a quiver, and fight with the sword."

**On the asset form.** As the user types, the form looks for carried-item words: sword, axe, mace, dagger, bow, crossbow, arrows, quiver, shield, staff, wand, spear, backpack, bag, satchel, hat, helmet, crown, lantern, torch. When it finds any, a quiet line appears under the description:

> Weapons work best as their own asset. We will make the ranger empty-handed, then make the **sword** and the **quiver** as separate items and put them on him. That way he can hold the sword when he fights.
>
> [Make them separately (recommended)]  [Keep them part of the body]

1. "Make them separately" is preselected; autopilot follows it with no further question.
2. Worn things that bend with the body (cloak, scarf, belt, armour, boots) are not listed. They stay on the body, and the help says so: "Clothes that bend with him stay part of his body."
3. The detected items become child assets of the character, named "Ranger - sword", "Ranger - quiver", using the same style recipe.

**On the character's page: "Add an item".**

1. **Pick or make an item.** Two tiles: "Make a new item" (one line of description, the style is copied from the character) and "Use one you made" (a grid of held items from this project).
2. **Where does it go?** Large plain buttons, with a small figure highlighting the spot: Right hand, Left hand, On the back, At the left hip, At the right hip, On the head. The item type preselects the usual one (a sword goes to the right hand, a quiver to the back, a shield to the left arm). An optional second choice, "When it is put away", appears for weapons: "At the left hip" or "On the back".
3. **Nudge.** Arrow buttons: up, down, left, right, forward, back; two turn buttons; "Smaller" and "Bigger"; "Start again". Each press moves 1 cm or 5 degrees, or 5 cm and 15 degrees while Shift is held. No numbers are shown unless Advanced is open.
4. **Check while it moves.** The viewer plays the character with the item attached, with clip buttons: Stand, Walk, Run, Attack, Put away (when a put-away place was chosen). A status line says what to look at: "Watch the sword during Attack. If it cuts through his arm, nudge it forward."
5. **If the grip guess was unsure.** "We were not sure where the hand holds this. Click the spot on the item where the hand should be." One click moves the pivot and re-plays the clip.
6. **Checks** in the existing pass and fail format:
   - `grip_found`: "The hand holds the sword by the handle." / "We guessed where the hand holds the sword; check it in the preview."
   - `item_clear_of_body`: "The sword stays clear of his body in every move." / "The sword passes through his body in Attack; nudge it forward."

**What the exported files contain** (`exports/<project>/<character>/`):

1. `Ranger.fbx`, `Ranger.glb`, `Ranger_godot.glb`, `unreal/`: the character as today, plus the socket empties (`socket_hand_R` and the rest).
2. `items/Ranger_sword/` and `items/Ranger_quiver/`: each item's FBX and GLB, LODs and collider, pivot at the grip, plus its own manifest.
3. `Ranger_equipped.glb`: the character with the items parented to their sockets. Opens equipped in Blender, the web viewer and Godot.
4. `attachments.json`: per item, the socket, the put-away socket, and position, rotation and scale offsets, plus the clip set chosen.
5. Unity: prefabs `Ranger` (bare) and `Ranger (equipped)` (items under the sockets), and an animator using the sword clip set.
6. `README.txt` in plain words: "To give the sword to another character, drop `Ranger_sword` under that character's `socket_hand_R`."

### 6.3 What should not be built yet, and why

1. **Clothing as separate layers on a generated base body.** No commercially usable garment model fits our cards (Garment3DGen is non-commercial; DressCode has no licence; ChatGarment is a heavy realistic-clothing stack), and two separate 3D runs do not line up. Clothes skinned as part of the body already deform correctly.
2. **Swappable armour sets.** These need many pieces made for one base body with matching topology, the base body above, and body hiding. That is a content pipeline, not a generation step.
3. **Cutting parts off fused characters as the main path.** The best tools are non-commercial or territory-restricted, the usable one (multi-view SAM) leaves holes and untextured hidden faces, and regeneration is cleaner.
4. **Part-aware generators as the mesh engine** (OmniPart, PartCrafter, PartPacker). They are blocked on licence, give no textures, and are untested on characters.
5. **Text-to-motion as a default.** Everything except Kimodo is unusable on licence or size, and Kimodo is a large, gated, untested addition.
6. **Cloth simulation.** It only works on separate cloak meshes, which we do not have, and it is costly on phones. Spring bones cover most of the visual need.
7. **Finger rigging.** Worth doing only if a rigger with finger bones and a clean licence and environment appears; the mitten hand is acceptable for stylised characters.

---

## Sources

Internal: `STATUS.md`; `docs/GREAT-ASSETS.md` (gap G24 sockets); `docs/GREAT-ASSETS-PLAN.md` section 4.1 (pieces that move); `backend/vgac/blender/procanim.py` (`_walk_like`, `clip_attack`, `_death_pose`); `backend/vgac/stages/export.py` (`clips_library`); `backend/vgac/data/models.yaml`; `unity-sandbox/Assets/Editor/VgacImport.cs` (role picker); clip names read from `data/animations/ual1` and `ual2`.

Industry practice
- [U1] Epic, Working with Modular Characters: https://dev.epicgames.com/documentation/en-us/unreal-engine/working-with-modular-characters-in-unreal-engine
- [U2] Modular Avatar, Merge Armature: https://modular-avatar.nadena.dev/docs/reference/merge-armature
- [U3] Reallusion forum, clothes and bones arrays in Unity: https://forum.reallusion.com/PrintTopic429523.aspx
- [G1] Building Customizable Characters for Destiny (GDC 2014), video at Game Developer: https://gamedeveloper.com/design/video-building-customizable-characters-for-i-destiny-i-
- [G2] mp1st summary of the Destiny talk: https://mp1st.com/news/bungie-creating-fully-customizable-characters-destiny
- [G3] Cyberpunk 2077 body toggles (ArchiveXL): https://www.nexusmods.com/cyberpunk2077/mods/26627
- [G4] Modular Avatar, advanced toggles and shrink blend shapes: https://modular-avatar.nadena.dev/docs/tutorials/advanced-toggles
- [G5] Blender Artists, preventing clothing clipping: https://blenderartists.org/t/right-way-to-prevent-clothing-clipping/1639772
- [E1] Epic, Skeletal Mesh Sockets: https://dev.epicgames.com/documentation/en-us/unreal-engine/skeletal-mesh-sockets-in-unreal-engine
- [E2] Unreal forum, mannequin IK bones (ik_hand_gun): https://forums.unrealengine.com/t/explanation-of-the-mannequin-ik-bones/45166
- [E3] Godot, BoneAttachment3D: https://docs.godotengine.org/en/latest/classes/class_boneattachment3d.html
- [E4] Unity blog, Animation Rigging character and props interaction: https://blog.unity.com/es/games/advanced-animation-rigging-character-and-props-interaction ; Unity Animation Rigging manual: https://docs.unity3d.com/Packages/com.unity.animation.rigging@0.1/manual
- [E5] Unreal forum, equip and unequip with notifies: https://forums.unrealengine.com/t/how-to-weapon-equip-unequip-on-animation-sequence/452015
- [H1] Epic, Hair Card Generator: https://dev.epicgames.com/documentation/unreal-engine/hair-card-generator-for-grooms-in-unreal-engine
- [H2] Epic, Setting up cards and meshes for grooms: https://dev.epicgames.com/documentation/en-us/unreal-engine/setting-up-cards-and-meshes-for-grooms-in-unreal-engine
- [S1] VRM 1.0 (SpringBone extension): https://vrm.dev/vrm1/
- [S2] Godot, SpringBoneSimulator3D: https://docs.godotengine.org/en/latest/classes/class_springbonesimulator3d.html
- [S3] UnityChan SpringBone (MIT, fork): https://github.com/Hengle/UnityChanSpringBone ; OpenUPM: https://openupm.com/packages/com.unity.springbone/
- [S4] UniVRM (MIT): https://github.com/vrm-c/UniVRM
- [S5] Unity Manual, Cloth: https://docs.unity3d.com/Manual/class-Cloth.html
- [A1] Mixamo FAQ thread (Adobe community): https://community.adobe.com/t5/mixamo-discussions/mixamo-faq-licensing-royalties-ownership-eula-and-tos/td-p/13234775

Part segmentation and part-aware generation
- [P1] Hunyuan3D-Part repository: https://github.com/Tencent-Hunyuan/Hunyuan3D-Part ; weights: https://huggingface.co/tencent/Hunyuan3D-Part
- [P2] Hunyuan3D-Part licence: https://raw.githubusercontent.com/Tencent-Hunyuan/Hunyuan3D-Part/main/LICENSE
- [P3] PartField repository: https://github.com/nv-tlabs/PartField
- [P4] PartField licence: https://raw.githubusercontent.com/nv-tlabs/PartField/main/LICENSE
- [P5] Reddit, ComfyUI PartField wrapper: https://reddit.birdcat.cafe/r/StableDiffusion/comments/1kbxub2/i_just_implemented_a_3d_model_segmentation_model
- [P6] PartPacker repository: https://github.com/NVlabs/PartPacker
- [P7] PartPacker licence: https://raw.githubusercontent.com/NVlabs/PartPacker/main/license.md
- [P8] OmniPart repository: https://github.com/HKU-MMLab/OmniPart
- [P9] OmniPart weights card: https://huggingface.co/omnipart/OmniPart
- [P10] OmniPart NOTICE (PartField-derived code): https://huggingface.co/spaces/omnipart/OmniPart/commit/8bdacdcc646be686aa701d4b59a1718868b4e25f
- [P11] PartCrafter repository: https://github.com/wgsxm/PartCrafter
- [P12] PartCrafter weights card: https://huggingface.co/wgsxm/PartCrafter
- [P13] HoloPart repository: https://github.com/VAST-AI-Research/HoloPart
- [P14] HoloPart weights card: https://huggingface.co/VAST-AI/HoloPart
- [P15] SAMPart3D repository: https://github.com/Pointcept/SAMPart3D
- [P16] BANG paper: https://arxiv.org/abs/2507.21493
- [P17] BANG project page: https://sites.google.com/view/bang7355608
- [P18] SegviGen repository: https://github.com/Nelipot-Lee/SegviGen
- [P19] SegviGen paper: https://arxiv.org/abs/2603.16869
- [P20] Trellis_Seg: https://github.com/YixinZhu042/Trellis_Seg
- [P21] Seg3DParts: https://arxiv.org/html/2609.36918
- [P22] FullPart: https://arxiv.org/pdf/2510.26140
- [P23] Segment Any Mesh (SAMesh): https://arxiv.org/html/2408.13679v1
- [P24] SAM 3 licence: https://huggingface.co/facebook/sam3/blob/main/LICENSE ; summary: https://roboflow.com/model-licenses/segment-anything-3

Garments and fitting
- [L1] Garment3DGen project: https://nsarafianos.github.io/garment3dgen
- [L2] Garment3DGen licence (CC BY-NC 4.0): https://github.com/nsarafianos/Garment3DGen/blob/main/LICENSE.md
- [L3] ChatGarment: https://github.com/biansy000/ChatGarment
- [L4] GarmentCode: https://github.com/maria-korosteleva/GarmentCode
- [L5] DressCode repository: https://github.com/IHe-KaiI/DressCode
- [L6] DressCode paper: https://arxiv.org/abs/2401.16465v4
- [L7] GarmentDreamer: https://github.com/boqian-li/GarmentDreamer
- [L8] Qwen-Image-Edit-2511 licence summary: https://www.datalearner.com/en/ai-models/pretrained-models/qwen-image-edit-2511
- [L9] Robust Skin Weights Transfer code: https://github.com/rin-23/RobustSkinWeightsTransferCode ; project: https://dgp.toronto.edu/~rinat/projects/RobustSkinWeightsTransfer/index.html
- [L10] Robust Weight Transfer Blender add-on article: https://digitalproduction.com/2025/04/04/weight-no-more-streamlining-blenders-weight-painting-with-robust-weight-transfer/

Animation libraries and retargeting
- [M1] Quaternius Universal Animation Library: https://quaternius.com/packs/universalanimationlibrary.html
- [M2] Quaternius Universal Animation Library 2: https://quaternius.com/packs/universalanimationlibrary2.html
- [M3] Mesh2Motion (MIT code, CC0 assets): https://gamefromscratch.com/mesh2motion-free-open-source-animation-app/
- [M4] Mixamo licensing FAQ thread: https://community.adobe.com/t5/mixamo-discussions/mixamo-faq-licensing-royalties-ownership-eula-and-tos/m-p/13234775
- [M5] CMU mocap terms as quoted: https://api.renderosity.com/forums/threads/2807853/print?single=Y ; https://cgpress.org/archives/carnegie-mellon-university-motion-capture-database.html
- [M6] CMU FBX conversion (CC BY-NC): https://data.4tu.nl/datasets/0448aab2-3332-449f-a8e2-d208cb58c7df
- [M7] Rokoko free mocap assets: https://www.rokoko.com/mocap/free-mocap-assets ; CG Channel: https://www.cgchannel.com/2020/03/get-150-free-mocap-moves-from-rokokos-motion-library
- [M8] AMASS licence: https://amass.is.tue.mpg.de/license.html
- [M9] Bandai Namco Research Motion Dataset: https://github.com/BandaiNamcoResearchInc/Bandai-Namco-Research-Motiondataset/blob/master/README.md ; https://automaton-media.com/en/news/20220430-12213/
- [R1] Unity, HumanPose (IK goals): https://docs.unity3d.com/6000.4/Documentation/ScriptReference/HumanPose.html
- [R2] Unity, applyFootIK: https://docs.unity3d.com/2018.4/Documentation/ScriptReference/Timeline.AnimationPlayableAsset-applyFootIK.html
- [R3] Unity forum, Humanoid retargeting with different leg lengths: https://forum.unity.com/threads/humanoid-retargeting-make-characters-with-different-leg-lengths-travel-the-same-distance.1393378/
- [R4] Epic, IK Rig animation retargeting: https://dev.epicgames.com/documentation/en-us/unreal-engine/ik-rig-animation-retargeting-in-unreal-engine

Text-to-motion
- [T1] HumanML3D (AMASS-derived): https://github.com/EricGuo5513/HumanML3D
- [T2] Example of AMASS restriction propagating to a model licence: https://huggingface.co/khania/motion-clip/blob/main/README.md
- [T3] HY-Motion 1.0 model card: https://huggingface.co/tencent/HY-Motion-1.0/blob/main/README.md
- [T4] HY-Motion 1.0 paper: https://arxiv.org/pdf/2512.23464v1
- [T5] Kimodo SOMA model card: https://huggingface.co/nvidia/Kimodo-SOMA-RP-v1 ; SMPL-X card (research only): https://huggingface.co/nvidia/Kimodo-SMPLX-RP-v1
- [T6] Kimodo project information and installation: https://research.nvidia.com/labs/sil/projects/kimodo/docs/project_info.html ; https://research.nvidia.com/labs/sil/projects/kimodo/docs/getting_started/installation.html
- [T7] Kimodo.cpp guide (memory, SOMA to Mixamo mapping): https://www.top3d.ai/learn/kimodo-cpp-local-ai-animation
- [T8] Kimodo quantised fork (VRAM reports): https://github.com/matbeedotcom/kimodo
- [T9] Meta Llama 3 Community Licence: https://www.llama.com/llama3/license/
