# Great assets: what separates good from great, and where VGAC stands

Date: 2026-11-04. Scope: every category in `backend/vgac/data/categories/*.yaml`, the three platform targets in `platforms.yaml`, and the deliverables listed in `ARCHITECTURE.md` section 2 and `docs/BACKEND.md`.

This document has two parts. Part 1 sets out, for each category, what a merely usable game asset looks like next to one a technical artist would call great, and how to measure each property. Part 2 compares what VGAC produces and checks today against those criteria, lists the gaps, and picks the ten most worth closing.

How to read it:

1. "Good" means an engine imports it without complaint and it looks right in a screenshot. "Great" means it also behaves right in a shipped game: it reads at gameplay distance, deforms cleanly, survives mip-mapping and LOD switches, collides sensibly, and drops into Unity or Unreal with no hand fixing.
2. Sources are numbered [S1] to [S40] and listed with URLs at the end. Where a figure is a community rule of thumb rather than an engine or store requirement, the text says so.
3. The Part 2 findings come from reading the code. A few behaviours (marked "verify") are inferred from engine defaults and should be confirmed in the Unity sandbox before acting on them.

---

## Part 1: criteria

### 1.0 Shared rules (all categories)

#### Engine conventions at a glance

| Convention | Unity | Unreal | glTF / Godot / web |
|---|---|---|---|
| Unit | 1 unit = 1 m [S1 2.4.e] | 1 unit = 1 cm; the FBX importer converts [S17] | 1 unit = 1 m [S25][S26] |
| Up, forward | Y up; asset front faces +Z [S1 2.4.d] | Z up, X forward, left-handed [S17] | Y up, front faces +Z, right-handed [S25] |
| Pivot | Bottom centre; hinge or shared corner for animated and modular pieces [S1 2.4.f] | Same practice; pivot based on the mesh, not the scene | Centre bottom at the origin [S25] |
| Normal map | OpenGL (green up); imported as Normal Map type [S1 2.4.3.f] | DirectX (green down) | OpenGL, MikkTSpace [S25] |
| Packed map | URP: metallic in RGB, smoothness (1 minus roughness) in alpha [S11]; HDRP mask map: R metal, G AO, B detail mask, A smoothness [S12] | ORM: R occlusion, G roughness, B metallic (common practice) | metallicRoughness: G roughness, B metal; ORM adds R occlusion [S25][S26] |
| LODs | Meshes named `_LOD0` to `_LODn` build a LODGroup; prefab must carry it [S1 2.4.j][S7] | All LODs in one FBX, or auto LOD; Nanite removes geometry LODs for static and skeletal meshes on high targets [S18][S23][S24] | No core LOD; one mesh per file is safest |
| Collision | Primitives, compound, convex (255 triangle cap), concave mesh for static only [S2][S3] | `UCX_<mesh>` convex, `UBX_` box, `USP_` sphere in the same FBX [S19][S18] | Engine-specific name hints (Godot `-col`, `-colonly`) |
| Skin influences | Project setting 1, 2, 4 or unlimited per vertex [S6] | 4 or 8 by default, up to 12 [S21] | 4 per JOINTS_n set |
| Naming | Unique clip names, sliced clips [S1 2.4.1.d, 2.4.1.e] | `SM_`, `SK_`, `T_`, `M_`, `MI_` prefixes [S20] | Descriptive names |

#### Good vs great, shared

| Property | Good | Great | How to measure |
|---|---|---|---|
| Real-world scale | Scale 1, metres, size roughly right | Size within 2 to 3 percent of a stated real size; the metre-cube test passes in each engine [S15]; the size is set on the axis that defines the object (height for people, length for snakes and cars) | Bounding box per axis against the category size table; import scale factor 1 |
| Orientation | Y up | Front verified, not assumed; root and every node have identity rotation after import [S1 2.4.d][S25] | Render the front view and compare with the concept; read node transforms |
| Pivot | Base centre | Base centre for standing things, hinge axis for moving parts, grip point for held items, shared corner for modular pieces [S1 2.4.f] | Origin against bounding-box minimum; distance from pivot to hinge |
| Geometry hygiene | Manifold, no holes, no zero-area faces [S25] | Also no floating islands, no inverted shells, no vertices with 10 or more edges [S25], no long thin triangles [S27] | bmesh counts, island volumes, signed volume above 0, valence histogram, triangle aspect ratio |
| Shading | Smooth with hard edges above a crease angle | Hard edges only on UV seams; mesh triangulated before baking; MikkTSpace in baker and engine so normal maps match [S30][S25] | Tangent basis recorded; the bake mesh and the exported mesh are the same triangles |
| Triangle budget | LOD0 inside the platform budget | Budget spent on silhouette and face; vertex count after UV and hard-edge splits counted, since vertices are the mobile cost [S27]; no micro-triangles at the LOD switch distance [S27] | Triangles, split-vertex count, median on-screen triangle size at each switch |
| LOD chain | 3 to 4 levels at about 50 percent steps | Switch heights derived from object size so pops are invisible [S7][S23]; last LOD keeps the silhouette; Nanite option for Unreal high [S24] | Silhouette IoU of each LOD against LOD0 rendered at its switch size |
| UV layout | One UV set, 0 to 1, no overlap | Even texel density across the asset and the project (about 512 px/m third person, 1024 px/m first person; rules of thumb) [S31]; seams hidden; padding scaled to resolution (at least 4 px, 8 to 16 px at 2K for a full mip chain) [S29]; atlas fill above about 70 percent | Per-island density spread, overlap count, fill ratio, padding in texels |
| Texture set | PBR metal-rough, power of two, base colour sRGB, data maps linear [S1 2.4.3][S25] | Packed per engine (see the table above); normal convention recorded; texture sizes per platform; 2K normals even when colour is 1K [S25] | Channel layout and colour-space check; manifest fields |
| PBR values | Nothing obviously wrong | No baked light or highlights in base colour [S25][S16]; non-metal albedo inside about 30 to 240 sRGB (strict 50 to 240) [S28]; metallic close to 0 or 1; roughness never exactly 0 [S28] | Share of out-of-range pixels under 1 percent; share of mid-grey metallic under 5 percent; baked-light score (see failure modes) |
| Materials | One material | One per asset, a second only for transparent or emissive parts [S25]; characters at most two or three [S5] | Material count per LOD |
| Collision | One convex hull | Cheapest shape that fits: primitives, then compound, then convex, mesh only for static [S3]; concave objects decomposed into several hulls [S38]; under the 255 triangle convex cap [S2] | Collider type, count, triangle count; walk-through and doorway tests |
| Import hygiene | Imports without errors | Names carry asset and type (`SM_Chest`, `T_Chest_BaseColor`) [S20]; Read/Write off and mesh compression set [S9]; per-platform texture size and ASTC on mobile [S10]; normal maps typed [S1 2.4.3.f]; one prefab at zero transform [S1 2.4.d] | Import log, importer settings dump, name lint |

#### Failure modes that make an asset unusable

| Failure | What the user sees | How to detect automatically |
|---|---|---|
| Non-manifold edges, holes, double shells | Shading streaks, broken shadows, rig and decimation failures | bmesh non-manifold and boundary counts; shell count |
| Inverted or mixed normals | Faces vanish or look inside out; wrong lighting | Signed volume negative; share of normals pointing toward the centroid |
| Wrong scale | A character the size of a house; physics feels floaty | Bounding box against the category size on the right axis |
| Pivot at world origin instead of base or hinge | Object floats or sinks when placed; a door swings around its centre | Origin against bbox minimum; hinge distance |
| Baked lighting in albedo | Painted shadows that do not move with the sun; double-dark crevices once AO is applied | Correlation between base-colour luminance and AO or the world-up normal |
| Specular highlights in albedo | White blotches that stay put as the camera moves | Clipped bright pixels in non-metal areas; luminance above 240 sRGB |
| Broken or false symmetry | Mismatched arms, a seam down the middle, half the texture wasted | Mirror distance before and after symmetrize; UV islands no face uses |
| Finger and limb weighting | Fingers move as a mitten; a thigh stretches when the other leg moves | Limb separation test; per-finger bend renders |
| Mip bleeding at UV borders | Dark or coloured seams that appear at a distance | Padding in texels against resolution; render at 1/8 size |
| Normal map baked on a different mesh | Faceted or wavy shading, seams | Hash of the bake mesh equals hash of the exported mesh |
| Collider fills openings | Cannot walk through a doorway or sit in a cart | Ray through the opening against the collider |
| Clips do not loop | Character freezes after one walk cycle | Loop flag on every cyclic clip; pose difference between first and last frame |

### 1.1 Humanoid

| Property | Good | Great | How to measure |
|---|---|---|---|
| Silhouette and proportion | Reads as a person at full size | Role readable from the silhouette alone at gameplay size; distinct from the project's other characters [S35] | Silhouette at 64 px height; IoU distinctness against other assets |
| Rest pose | A-pose or T-pose, hands empty | A-pose with arms about 45 degrees, slight elbow and knee bend, open hands with fingers apart so fingers can be rigged | Joint angles from the skeleton |
| Topology | Triangle decimation (fine for mobile low) | Quad-dominant, about three loops across elbows and knees, extra shoulder loops, poles moved off hinge lines [S39]; the Unity Asset Store requires AI-generated meshes to be retopologised [S1 2.4.g] | Quad ratio, loop count near joints, valence |
| Budget | Mobile 5k, PC 20k, high 60k triangles (VGAC values) | Same totals, with head and hands getting a larger share | Triangle share per body region |
| Skeleton | Unity Humanoid required bones present and mapped: hips, spine, head, upper and lower legs, feet, upper and lower arms, hands [S4] | Plus chest, upper chest, neck, shoulders, toes, three bones per finger; optional twist bones on upper arm and forearm for PC and high; a weapon socket per hand; Mixamo names [S40] | Unity avatar `isHuman` and `isValid`; bone list against the template |
| Bone count | Under about 60 | Around 30 or fewer on mobile, 15 to 60 on desktop [S5]; unweighted helper bones removed | Bone count; unweighted bones |
| Skin weights | At most 4 influences, normalised [S6][S21] | No bleed between the legs or from arm into torso; separate finger weights; no creases at 90 degrees [S1 2.4.1.b, 2.4.k] | Bend test with a numeric volume-loss and self-intersection score; limb separation test |
| Animation set | Idle, walk | Idle, walk, run, jump, attack, hit, death (VGAC's own definition of done), plus turn in place and start or stop; every cyclic clip loop-flagged; unique names [S1 2.4.1.e] | Clip list; loop seam error |
| Root motion | Some policy | One documented policy: in-place cycles, idles baked into pose [S13] | Root delta per clip |
| Collision | One hull | Upright capsule for movement plus per-bone capsules as hit boxes | Collider types; capsule fit to bounds |
| Face | None (out of scope in v1) | Jaw and eye bones, which Unity Humanoid treats as optional [S4] | Bone list |

### 1.2 Quadruped

| Property | Good | Great | How to measure |
|---|---|---|---|
| Silhouette | Four legs visible | Clear gaps between all four legs in side view; distinct head-neck line and tail | Side-view silhouette leg gaps |
| Size | Plausible | Set from shoulder height or body length in the prompt (a mouse and a horse are not both 1.2 m) | Dims against the stated size |
| Skeleton | Spine, four legs, neck, head, tail | A named template (spine chain, three or more segments per leg, tail chain) so clips transfer between creatures; foot IK targets | Template conformance |
| Weights | Legs bend | No belly-to-leg bleed; the shoulder area slides | Limb separation test |
| Animation set | Idle, walk | Idle, walk with a diagonal or lateral gait, trot or run, turn, bite or attack, hit, death; foot contacts marked | Foot slide during contact |
| Collision | Hull | Capsule along the body (an upright character capsule does not fit a long body) plus a head hit box | Collider orientation against the long axis |

### 1.3 Multi-leg creature

| Property | Good | Great | How to measure |
|---|---|---|---|
| Silhouette | Body and legs visible | Every leg separated in top and side views; legs survive at the last LOD | Leg count from the silhouette per LOD |
| Size | Plausible | Set by body length or leg span, not height | Dims on the right axis |
| Skeleton | One chain per leg | Every leg rigged with three or more segments; consistent naming per leg index and side | Chains counted equals legs counted |
| Animation | Legs move | Every leg animated in an alternating tripod or wave gait; idle twitch; attack; hit; death | Legs moved per clip equals leg count |
| LOD | Decimated | Thin legs kept as minimum-width geometry at low LODs | Leg presence per LOD |

### 1.4 Winged creature

| Property | Good | Great | How to measure |
|---|---|---|---|
| Rest pose | Wings spread | Wings spread flat, membranes unobstructed, legs planted | Wing span against body length |
| Geometry | Closed mesh | Thin wing membranes kept thin (not inflated into slabs), two-sided where open | Membrane thickness |
| Skeleton | Body chain | Wing chains (upper arm, forearm, hand or finger bones) on both sides, tail chain | Wing chains present |
| Animation | Idle | Idle, flap, glide, take-off, land, plus walk if it lands; fold and unfold | Wing bones moved per clip |
| Collision | Hull including the wings | Body capsule for movement, separate wing boxes for hits | Collider types |

### 1.5 Serpent or fish

| Property | Good | Great | How to measure |
|---|---|---|---|
| Size | Plausible | Set by body length (a 2 m snake is 2 m long, not 2 m tall) | Longest axis against the stated length |
| Skeleton | A spine chain | 12 to 30 evenly spaced spine bones, head and jaw; fin bones for fish | Bone spacing variance |
| Weights | Bends | Smooth along the chain, no pinching at each joint | Bend test along the chain |
| Animation | Some sway | Travelling-wave slither or swim (amplitude rising toward the tail), turn, strike, hit, death | Phase lag along the chain |
| Collision | Hull | A chain of capsules following the spine | Collider count and alignment |

### 1.6 Rigid prop

| Property | Good | Great | How to measure |
|---|---|---|---|
| Pivot | Base centre | Base centre, or grip point for items the player picks up, with a socket for the hand [S1 2.4.f] | Pivot against bbox and grip |
| Texel density | Sharp enough | Matches the characters it sits next to [S31] | px/m against the project target |
| Collision | Convex hull | Box, cylinder or capsule when it fits; compound or decomposed hulls for concave shapes such as chairs and carts [S3][S38] | Collider type; doorway or seat test |
| LODs | 2 to 3 | 2 to 3 with switch heights from object size; LOD1 visually identical at its switch [S7] | LOD silhouette IoU |
| Static lighting | Nothing | Non-overlapping lightmap UV (UV1) for static props [S25] | UV1 present and overlap-free |

### 1.7 Articulated prop

| Property | Good | Great | How to measure |
|---|---|---|---|
| Parts | One mesh | Each moving part is its own mesh with its pivot on the hinge axis [S1 2.4.f][S25] | Part count; pivot-to-hinge distance |
| Limits | None | Axis and open angle recorded (for example, the lid opens 110 degrees about local X) | Manifest fields |
| Interior | Closed shell | Interior modelled and textured, so the chest is not a solid block when opened | Render with the lid open |
| Animation | None | Open and close clips, plus a closed idle | Clip list |
| Collision | One hull | One collider per part, moving with the part | Collider per part |

### 1.8 Vehicle or mech

| Property | Good | Great | How to measure |
|---|---|---|---|
| Size | Plausible | A car is about 4.5 m long and 1.5 m tall; size set on length | Dims per axis |
| Parts | One mesh | Wheels, turret, doors and hatches separate, each pivot at its axle or hinge | Part count; pivot positions |
| Wheels | Modelled | Wheel radius and axle positions recorded; visual wheels separate from Unity WheelColliders [S14]; one bone per wheel for Unreal Chaos vehicles [S22] | Radius against the mesh; bone per wheel |
| Collision | Hull | Chassis compound or box set; wheels without collision (the wheel system handles them) [S22][S14] | Collider list |
| Animation | None | Wheel spin and steer driven by code, suspension travel, turret yaw and pitch limits | Bones and limits present |
| Sockets | None | Seats, muzzle, exhaust, lights | Named empties |

### 1.9 Environment and modular kits

| Property | Good | Great | How to measure |
|---|---|---|---|
| Module size | Arbitrary | Dimensions exact to a project grid (for example 1, 2 or 4 m footprints and a 3 m wall height); a grid snap of half the footprint is common practice [S33] | Dims modulo grid, under 1 mm error |
| Pivot | Base centre | Same corner on every piece of the kit [S1 2.4.f] | Origin at the grid corner |
| Edges | Close | Edge profiles match, so pieces meet without gaps or z-fighting | Tiling test: place 3 x 3 and check for gaps |
| Openings | Filled | Doors and windows open; only visible faces modelled | Ray test through openings |
| Collision | Hull | Concave mesh collider allowed because the piece is static [S3], or decomposed hulls | Doorway walk-through |
| Textures | Unique 0 to 1 map | Tileable materials or a trim sheet shared across the kit, so pieces swap materials without new UVs [S34][S33] | Material reuse count |
| Lighting | Nothing | Non-overlapping UV1 for lightmaps | UV1 present |
| LOD | 2 levels | LODs, or Nanite on high [S24] | LOD count |

### 1.10 Foliage

| Property | Good | Great | How to measure |
|---|---|---|---|
| Leaves | Leaf mass as solid geometry | Alpha-tested cards or leaf clusters, with a two-sided material whose shader reads alpha [S1 2.4.3.e] | Card count; alpha channel present |
| Wind | None | Vertex colours carrying stiffness, phase and branch attenuation for a wind shader (the Crysis scheme) [S32]; optional wind clip | Vertex colour layer present and graded from trunk to tips |
| LOD | Decimated | Cross-fade or SpeedTree-style fade between LODs and a camera-facing billboard as the last LOD [S8] | LODGroup fade mode; billboard present |
| Pivot | Base centre | Base of the trunk at the ground line | Pivot height |
| Collision | Hull over the canopy | Trunk capsule only; leaves without collision | Collider shape |
| Mobile cost | Ignored | Alpha overdraw kept low by tight cards | Alpha coverage ratio per card |

### 1.11 Tileable material

| Property | Good | Great | How to measure |
|---|---|---|---|
| Seams | Borders match | Tiles without seams or obvious edges [S1 2.4.3.c] and with no visible repetition at 3 x 3 | Border difference; low-frequency luminance variation across the tile |
| Lighting | Even | Delit: no light direction, no cast shadows | Luminance gradient across the tile; shadow detection |
| Maps | Base colour plus derived maps | Height and normal consistent with each other and with the base colour (dark grout is low); roughness plausible for the material; metallic near 0 or 1 and matching the subject (rusted steel is not 0 everywhere); AO for crevices | Normal recomputed from height against the shipped normal; PBR range checks [S28] |
| Scale | Unknown | Real-world tile size recorded (for example 2 m x 2 m) so texel density matches the project [S25][S31] | Manifest field |
| Variation | One map | A macro variation or detail map, and a height map suitable for height-blending with other materials | Extra maps present |
| Delivery | Zip of PNGs | A ready Unity material or terrain layer, and Unreal-ready packed maps | Engine assets present |
| Environment use | Single swatch | Trim sheets for modular kits [S34] | Trim layout present |

### 1.12 Sprite sheets (optional output for any rigged or static asset)

| Property | Good | Great | How to measure |
|---|---|---|---|
| Directions | 8 | 4, 8 or 16 by game type; a symmetric subject renders 5 and mirrors 3, saving close to half the memory [S37] | Direction count; mirror flag |
| Frames | Fixed count | Count per clip chosen for the motion (key poses first, inbetweens after, as Dead Cells did) [S36] | Frames per clip |
| Pivot | Frame centre | Same pivot at the feet in every frame and direction, written into the atlas | Pivot field in the atlas; foot pixel drift |
| Consistency | Per asset | Same camera angle, scale and light across the whole project | Camera parameters shared per project |
| Packing | Grid | Power-of-two sheet with padding between frames and trimmed rects | Padding in px |
| Pixel art | No anti-aliasing | Palette limited to the project palette; optional one-pixel outline | Unique colours against the palette |

---

## Part 2: gap review

Impact is the effect on the finished asset in a game. Feasibility is how hard the gap is to close with the free local tools already in the stack (ComfyUI, Blender 5.2, Unity 6000.4, Python): High is a few days with existing tools; Medium needs a new free tool or about a week of work; Low needs research or a model that may not exist under a permissive licence.

### Gap table

| ID | Applies to | Criterion | What the app does today | Gap | Impact | Feasibility | Idea to close |
|---|---|---|---|---|---|---|---|
| G01 | All meshes | Engine material uses every PBR map | `unity-sandbox/Assets/Editor/VgacImport.cs` assigns base colour, normal and AO only; metallic and roughness PNGs are imported as linear but never assigned | In Unity every asset gets the URP default smoothness and no metal, so armour looks like plastic and stone looks wet | High | High (Python packing plus C#) | Export a `MetallicSmoothness` PNG (metal in RGB, 1 minus roughness in alpha) and assign it as `_MetallicGlossMap` with its keyword on |
| G02 | All meshes | Channel packing per engine | `backend/vgac/stages/export.py` copies separate `base_color`, `normal`, `metallic`, `roughness` and `ao` maps | No glTF or Unreal ORM, no HDRP mask map; five textures where two would do | Medium | High (numpy) | Write ORM and Unity packs at export; record the layout in the manifest |
| G03 | All meshes | Padding at least 4 texels, scaled to resolution | `backend/vgac/graphs/trellis.py` calls `UnwrapMesh` with `padding: 1`; no dilation pass | Colour bleeds across island borders at lower mips and on LOD2 and LOD3, showing as seams at a distance | High | High (a parameter plus a numpy dilation) | Padding of `max(4, tex/256)`, then dilate every baked map 16 px into the empty space |
| G04 | All meshes | Texel density, overlap, atlas fill | `backend/vgac/quality.py` only checks that a UV set exists; `frontend/src/help.ts` explains texel density but only the mock data produces a value | Fragmented atlases (noted in STATUS.md) and uneven sharpness go unflagged | Medium | High (Blender bmesh UV areas) | Add a UV report to `blender/prep.py` (px/m spread, overlap count, fill ratio, island count) and gate on fill and overlap |
| G05 | All meshes | Normal and AO maps baked on the final mesh | Baking happens in the mesh stage (`graphs/trellis.py`); `blender/prep.py` then symmetrizes and decimates to the budget | The maps no longer match the triangles they shade (wavy or faceted shading), and after symmetrize half the atlas is never sampled | High | Medium (Blender Cycles bake from the dense mesh, or reorder the ComfyUI chain) | Run symmetry and the final budget before `UnwrapMesh` and the bakes, or re-bake in Blender after prep |
| G06 | Humanoid, quadruped, winged, multileg, serpent | Quad-dominant topology with joint loops | The category yaml sets `topology: quad`, but nothing reads it; every category goes through `DecimateMesh` triangles | Elbows, knees and shoulders crease and collapse when bent; the Unity Asset Store would reject unretopologised AI meshes [S1 2.4.g] | High | Medium (Blender QuadriFlow is built in; needs the G05 re-bake) | QuadriFlow to the budget for skinned categories, then bake; keep triangles for props and mobile low |
| G07 | All meshes | No baked lighting or highlights in base colour | `quality.py` states that delit albedo is not checked; the delighting pass in ARCHITECTURE.md stage 4 step 6 is not implemented | Shadows from the concept image stay painted on and fight the game's lights | High | Medium (numpy plus the AO already baked) | Baked-light score: correlation of base-colour luminance with AO and the world-up normal; optional AO-guided correction |
| G08 | All meshes and materials | PBR value ranges | No range checks in `quality.py` | Too-dark or too-bright albedo and mid-grey metallic go unnoticed; assets look wrong under image-based lighting | Medium | High (numpy) | Checks for albedo 30 to 240 sRGB, metallic bimodality and roughness above 0 [S28] |
| G09 | All meshes | Normal convention recorded; Unreal variant | `stages/material.py` records `normal_convention: OpenGL` for materials only; mesh manifests say nothing | Unreal users get inverted bumps without knowing why | Medium | High | Record the convention in every manifest; write a green-flipped `_DX` normal on request |
| G10 | All meshes | One clean mesh per GLB | `blender/pack.py` selects everything and exports LOD0 to LODn plus `_collider` into one GLB | Godot, web and Blender users see four overlapping copies and a grey hull | Medium | High | GLB of LOD0 only (plus optional per-LOD GLBs); collider left out or named with Godot hints |
| G11 | All meshes (Unreal users) | Unreal import naming | Collider named `<asset>_collider`; no `UCX_`, `SM_`, `SK_` or `T_` names; LODs as separate `_LODn` objects | Unreal imports the collider as visible geometry and the LODs as separate meshes | Medium | High | An Unreal export profile: `UCX_<mesh>` collision, `SM_` or `SK_` names, a LOD group, DX normals [S19][S20][S18] |
| G12 | Vehicle, serpent, multileg, winged, quadruped | Size on the defining axis | `blender/prep.py` scales so the Z height equals `size_m`; the gate in `quality.py` then checks the same Z height | Vehicles come out 4 m tall, snakes 2 m tall and spiders 1 m tall, and the gate passes them | High | High | Add `size_axis` (height, length or longest) to each category yaml and scale on that axis |
| G13 | All | Size from the description | A fixed `size_m` per category; the user can set `height_m` in Fine-tune | A mouse and a horse in the quadruped category get the same size | Medium | High | A "real size" field on the asset form, prefilled from the category and from any size written in the prompt |
| G14 | All meshes | Front faces +Z after export | Nothing checks facing; `blender/procanim.py` guesses forward from geometry; the mesh audit already computes `silhouette_offset` | A model built from a three-quarter view can face sideways in the game | Medium | High | Rotate by the audit's best yaw offset in prep so the concept's front faces forward, and add a facing check |
| G15 | Environment | Grid-exact size and corner pivot | Pivot at base centre (`blender/prep.py`); no grid rounding; `help.ts` promises the piece "snaps to a grid" | Kit pieces leave gaps or overlap, and the help text promises something the app does not do | High | High (Blender) | A project grid setting; round dims to the grid, put the pivot at the minimum corner, render a tiling test |
| G16 | Environment, prop_rigid, vehicle | Openings stay open; concave collision | `RemeshMesh` solid mode (`graphs/trellis.py`) fills cavities; `blender/pack.py` makes one convex hull | Doorways and arches are solid to physics; chairs and carts cannot be sat in | High | High (CoACD is MIT-licensed and pip-installable [S38]) | Convex decomposition with CoACD for dynamic props; a concave mesh collider for static environment pieces; solid remesh off for environment |
| G17 | Environment, static props | Lightmap UV1 | One UV set only | Baked lighting in Unity or Unreal needs a hand-made second UV | Medium | High | Set `generateSecondaryUV` in `VgacImport.cs` for static categories, or pack a Blender lightmap UV into UV1 |
| G18 | Foliage | Alpha cards, two-sided leaves | The same solid TRELLIS shell and remesh as every other category | Trees come out as blobs with no light through the canopy | High | Low to Medium (card generation from the concept cutout is new work) | Trunk from TRELLIS; leaves as alpha cards cut from the concept with BiRefNet; a two-sided alpha-test material |
| G19 | Foliage | Wind vertex colours | No vertex colours | No wind sway in any engine shader | Medium | High (Blender) | Bake a vertex colour from height and distance to the trunk (the Crysis channel scheme [S32]) |
| G20 | Foliage | Billboard last LOD and cross-fade | `VgacImport.cs` builds a plain LODGroup; no fade mode, no billboard | Visible popping across a forest; full geometry drawn far away on mobile | Medium | Medium | Render an 8-view billboard in Blender, add it as the last LOD, set `fadeMode` to CrossFade [S8] |
| G21 | Prop_articulated | Moving part separate, hinge pivot, open and close | Parts are only mentioned in the concept prompt (`stages/concept.py`); nothing segments them; the rig stage skips; `help.ts` says the part "becomes a separate piece on a hinge" | The chest lid cannot open, and the promise is not kept | High | Medium (a split-plane tool in Blender; free segmentation models need licence checks) | Adjust tool: the user drags a split plane and picks the hinge edge; Blender bisects, fills, sets the pivot and writes open and close clips |
| G22 | Prop_articulated | Interior surfaces | Solid remesh closes the inside | Opening the lid would reveal a solid block | Medium | Low to Medium | After the split, inset the cut face into a shallow cavity and texture it from the outer material |
| G23 | Vehicle | Separate wheels, turret and doors with pivots; wheel data | Nothing segments parts; `help.ts` says "wheels, turret and doors are separate parts you can move" | Vehicles cannot drive or turn turrets, and the promise is not kept | High | Medium (the split tool from G21; wheel radius from the cut) | Mark wheels with boxes, cut them and pivot at the axle, write wheel radius and positions to the manifest, place WheelColliders in the prefab [S14] |
| G24 | Humanoid, props, vehicles | Sockets and attachment points | None | Weapons, riders and pickups must be placed by hand in every engine | Medium | High for characters, Medium for props | Add `socket_hand_R` and `socket_hand_L` empties under the hand bones; grip and seat empties from user clicks |
| G25 | Humanoid | Finger bones | The SkinToken Mixamo template gives 31 to 52 bones without fingers (STATUS.md D2) | Hands move as mittens; the finger motion in the CC0 clips is dropped; grips look wrong | Medium | Low (needs a free rigger that does fingers) | Procedural finger chains placed from hand geometry, weighted by nearest bone, as a later option |
| G26 | Humanoid | Reliable Humanoid mapping | `VgacImport.cs` uses `CreateFromThisModel` and Unity's auto-mapping; on the goblin it failed and fell back to Generic (STATUS.md) | Stocky or stylised humanoids lose all 46 CC0 clips | High | High (C#) | Build the `HumanDescription` explicitly from the mixamorig names, skip `Extra_` bones, enforce the rest pose [S4] |
| G27 | Rigged categories | Bone budget per platform | The gate only requires more than 0 bones (`quality.py`) | The goblin shipped 52 bones on mobile low against about 30 recommended [S5] | Medium | High | A per-platform bone budget check; prune unweighted leaf and `Extra_` bones in `blender/rig_fix.py` |
| G28 | Rigged categories | Measured deformation and limb separation | `blender/rig_info.py` renders bend poses for a person to look at; the limb separation test in ARCHITECTURE.md item 15 is not implemented; `rig_fix.py` copies the nearest weighted vertex's weights to unweighted vertices, which can cross limbs | Bad weights pass the gate; a crotch vertex can follow the wrong leg | Medium | High (Blender) | A numeric bend score (local volume loss and self-intersections at 90 degrees) and a limb separation count per side; restrict the nearest-vertex copy to the same side |
| G29 | Rigged categories | Twist bones, IK targets, Unreal mannequin names | None | Forearms candy-wrap on PC and high; Unreal retargeting needs manual IK setup | Low | Medium | Optional twist bones for PC and high; an Unreal profile that adds `ik_foot_root` style bones |
| G30 | Non-humanoid rigged | Animation set per category | `blender/procanim.py` makes `idle` and `walk` only; ARCHITECTURE.md item 19 promises run and turn too; no attack, hit or death for any category | Creatures cannot fight or die; prefabs feel unfinished | High | Medium (extend procanim) | Add run (faster, larger amplitude), turn, hit (spine recoil), death (root roll and collapse) and attack (head or forelimb lunge) |
| G31 | Winged, serpent, multileg | Category-specific locomotion | `procanim.py` classifies legs only and keeps `clusters[:4]`; wings are not recognised; with no legs the walk is a small torso sway | Spiders move four of eight legs, dragons do not flap, snakes do not slither | High | Medium | Wing chains get flap and glide; serpents get a travelling wave; multileg gets an alternating tripod over all legs |
| G32 | All rigged | Clips loop | `VgacImport.cs` never sets clip import settings, so Loop Time stays at the importer default (off; verify) | Idle and walk can play once and freeze in the game | High | High (C#) | Set `loopTime` and `loopPose` for idle, walk, run and other cyclic clips through `clipAnimations` |
| G33 | All rigged | A usable Animator controller and root motion policy | `VgacImport.cs` adds one state per clip (up to 200 for the CC0 library) with no parameters or transitions; root motion is not set | The user must build a locomotion graph before the character can walk | Medium | High (C#) | A small controller: a Speed-driven blend tree for idle, walk and run, triggers for attack, hit and death, root motion off with in-place cycles [S13] |
| G34 | Characters and creatures | Capsule and hit boxes | One convex `MeshCollider` on the prefab root (`VgacImport.cs`); ARCHITECTURE.md item 7 promises a capsule proxy | The collider does not follow the animation and cannot be used with a character controller | High | High (C#) | A CapsuleCollider from the bounds on the root; per-bone capsules from the `rig_info` segments as hit boxes |
| G35 | All meshes | Convex collider within Unity's limit | `quality.py` caps the hull at 255 vertices; Unity's limit is 255 triangles [S2] | A hull can pass the gate and still be simplified again by Unity | Low | High | Check triangles; decimate the hull to 250 triangles |
| G36 | All meshes | LOD switch heights by object size | Fixed `0.6, 0.3, 0.12, 0.03` in `stages/export.py` and `VgacImport.cs`, used only when Unity does not build the LODGroup itself | Small props pop early, large vehicles late | Low | High | Derive heights from size and triangle density; always write them into the LODGroup |
| G37 | All meshes | LOD quality | LODs are plain collapse decimation (`blender/pack.py`) with no per-LOD check | Thin parts (legs, antennae, sword blades) vanish at LOD2 | Medium | High (the existing silhouette renderer) | Render each LOD at its switch size and compare with LOD0; protect boundaries and UV seams during decimation |
| G38 | All meshes | Unity texture import settings | `VgacImport.cs` sets colour space and normal-map type only | Every texture ships at full size with default compression on phones | Medium | High (C#) | Per-platform max size from the budget, ASTC 6x6 on Android and iOS, mipmaps on [S10] |
| G39 | All meshes | Unity mesh import settings | Read/Write and mesh compression left at their defaults | Minor memory waste; no control | Low | High | Set `isReadable = false` and `meshCompression` per platform [S9] |
| G40 | All | Asset-specific texture names | Textures are `base_color.png`, `normal.png` and so on in every export | Easy to mix up textures between assets once they share a project | Low | High | `T_<asset>_BaseColor.png` style names [S20] |
| G41 | All meshes | Floating islands and inverted shells | `blender/prep.py` deletes loose vertices and edges and recalculates normals, but small islands and inside-out shells are not counted (ARCHITECTURE.md item 1) | Stray specks and black patches can ship | Medium | High | Count and remove islands under 0.1 percent of the volume; signed-volume check per shell |
| G42 | Symmetric categories | Measured symmetry | `prep.py` symmetrizes across X through the bbox centre whenever the category says so; asymmetry is never measured | A head turned in the concept, or an off-centre model, gets mangled by forced symmetry | Medium | High | Measure the mirror distance first; symmetrize only under a threshold, about the measured plane |
| G43 | Material | Physically plausible maps | `graphs/material.py` derives height from blurred luminance, normal from height and roughness from high-pass detail; metallic is always black; no AO | Metal materials have no metal; painted patterns become bumps | Medium | Medium (the local vision judge or prompt keywords for metal) | A metal mask from the prompt or a judge question; AO from height; a height direction check against the image |
| G44 | Material | Repetition and delighting | `seam_error` compares opposite borders only | A tile can be seamless and still show an obvious grid at 3 x 3, or a light gradient | Medium | High (numpy) | A low-frequency luminance flatness check and correction; a 3 x 3 preview in the UI |
| G45 | Material | Real-world size and engine-ready delivery | A zip of PNGs plus the manifest (`stages/material.py`) | No tile size, no Unity material or terrain layer, no Unreal packing | Medium | High | Record the tile size in metres; build a URP material and TerrainLayer in the sandbox; pack ORM |
| G46 | Material, environment | Trim sheets and variation | Not offered | Modular kits need many unique textures instead of one shared sheet | Low | Medium | A trim-sheet layout option that stacks several tileable strips with bevel normals [S34] |
| G47 | Sprites | Pivot, mirroring, consistent framing | `blender/sprites.py` frames each asset on its own bounding box, renders all 8 directions and 8 frames for every clip under Workbench studio light; the atlas holds rects only | Feet drift between clips and assets; sprites of different assets do not share a scale; no pivot for Unity | Medium | High | A project-level sprite camera; a pivot at the feet in the atlas; a mirror option; frame count per clip; padding |
| G48 | Sprites (pixel style) | Palette and outline | `--pixel` turns off anti-aliasing only | Sprites drift off the project palette | Low | High | Quantise to the style-lock palette; optional one-pixel outline |
| G49 | All | Silhouette readability | Turnaround consistency and mesh IoU checks exist; nothing checks readability at gameplay size | Assets can be accurate but unreadable in play | Low | High | A 64 px silhouette thumbnail in the review; distinctness against the project's other assets |
| G50 | All | Vertex count after splits | Triangles only | On mobile a fragmented atlas can double the real vertex cost unnoticed [S27] | Low | High | Count split vertices in `prep.py` and show them next to triangles |
| G51 | Several | Help text matches behaviour | `frontend/src/help.ts` promises grid snapping (environment), movable parts (vehicle), hinges (articulated prop) and wing bones (winged) | Users trust a promise the asset does not keep | Medium | High | Reword now; restore each promise as G15, G21, G23 and G31 land |

### Shortlist: the ten gaps most worth closing

Ranked by impact on the finished asset, how many categories each touches, and how cheaply each closes with tools already installed. Each item ends with what a game maker would notice.

1. **G01 and G02: materials lose metal and shine in Unity.** Pack and assign the metallic and smoothness map, and write engine-specific packs. Benefit: metal looks like metal and leather looks like leather when you drop the asset into Unity, instead of everything looking like the same plastic.
2. **G12: size set on the wrong axis.** Scale vehicles, snakes, spiders and other long creatures by length, not height. Benefit: a car comes out car-sized and a snake snake-sized, so you do not have to rescale every creature by hand.
3. **G32 and G33: clips do not loop, and the controller is a pile of states.** Set loop flags and build a small idle, walk and run controller with attack, hit and death triggers. Benefit: drag the character into a scene and it idles and walks smoothly instead of freezing after one step.
4. **G34 and G16: collision that fits.** A capsule and hit boxes for characters; decomposed hulls or a mesh collider for props and scenery. Benefit: characters bump into walls properly, you can walk through doorways and sit in carts, and hits land where they look like they land.
5. **G05 and G03: maps baked on the wrong mesh, with thin padding.** Bake after the final clean-up and pad every map. Benefit: no wavy shading, no half-wasted texture, and no dark seams that appear when the asset is further away.
6. **G26: Humanoid mapping fails on stylised characters.** Map the skeleton explicitly instead of trusting Unity's auto-mapping. Benefit: every humanoid, including stocky goblins and chibi heroes, gets the 46 free animations, not just the well-proportioned ones.
7. **G30 and G31: too few, and wrong, animations for creatures.** Add run, turn, attack, hit and death, plus wing flaps, slithering, and gaits that move every leg. Benefit: dragons fly, snakes slither, spiders walk on all eight legs, and every creature can fight and die straight away.
8. **G15 and G17: environment pieces that do not snap.** Round sizes to a project grid, put the pivot on a corner, keep openings open and add a lightmap UV. Benefit: walls and floors click together without gaps, doorways are walkable, and baked lighting works without extra steps.
9. **G07 and G08: painted-on lighting and out-of-range colours.** Score and correct baked light in the base colour, and check PBR value ranges. Benefit: assets react to your game's sun and torches instead of carrying the shadows of the original picture.
10. **G21 and G23: moving parts that do not move.** A split-plane tool with a hinge or axle pick for articulated props and vehicles, with open and close clips and wheel data. Benefit: the chest lid opens, the door swings and the car's wheels turn, as the app already promises.

Next in line: G06 (quad remesh for characters; high impact, but it needs the G05 re-bake first), G18 to G20 (foliage cards, wind and billboards), G10 and G11 (a clean GLB and an Unreal export profile), and G51 (reword the help text until the promised features land; about an hour's work, worth doing straight away).

---

## Sources

- [S1] Unity Asset Store Submission Guidelines, sections 2.4 (meshes, prefabs, pivots, scale, LODs), 2.4.1 (characters and animation), 2.4.3 (textures). https://assetstore.unity.com/publishing/submission-guidelines
- [S2] Unity Manual, Mesh Collider ("Convex mesh colliders are limited to 255 triangles"). https://docs.unity.com/en-us/engine/current/manual/physics-section/physics-overview/collision-section/collider-shapes/mesh-colliders/class-mesh-collider
- [S3] Unity Manual, collider types and their cost (primitive, compound, convex, concave). https://docs.unity3d.com/kr/current/Manual/physics-optimization-cpu-collider-types.html
- [S4] Unity Manual, Configuring the Avatar (required bones, T-pose, Enforce T-Pose). https://docs.unity3d.com/2020.2/Documentation/Manual/ConfiguringtheAvatar.html
- [S5] Unity Manual, Modeling characters for optimal performance (bone counts, polygons, materials; older guidance). https://docs.unity3d.com/560/Documentation/Manual/ModelingOptimizedCharacters.html
- [S6] Unity Scripting API, QualitySettings.skinWeights. https://docs.unity3d.com/es/ScriptReference/QualitySettings-skinWeights.html
- [S7] Unity Manual, LOD Group (screen-relative transition heights, object size). https://docs.unity3d.com/6000.4/Documentation/Manual/class-LODGroup.html
- [S8] Unity Scripting API, LODFadeMode (cross-fade, SpeedTree, billboard). https://docs.unity3d.com/520/Documentation/ScriptReference/LODFadeMode.html
- [S9] Unity Manual, Model import settings (mesh compression, Read/Write, optimise mesh). https://docs.unity3d.com/Manual/FBXImporter-Model.html
- [S10] Unity Manual, texture import platform overrides and ASTC. https://docs.unity.cn/Manual/class-TextureImporterOverride.html
- [S11] Unity Manual, URP Lit shader (metallic map, smoothness source). https://docs.unity3d.com/Manual/urp/lit-shader
- [S12] Unity HDRP, Mask Map and Detail Map. https://docs.unity3d.com/Packages/com.unity.render-pipelines.high-definition@12.1/manual/Mask-Map-and-Detail-Map.html
- [S13] Unity Manual, Root Motion (Bake Into Pose, Loop Pose). https://docs.unity3d.com/Manual/RootMotion
- [S14] Unity Manual, Wheel Collider. https://docs.unity3d.com/Manual/class-WheelCollider.html
- [S15] Unity Manual, Art Asset Best Practice Guide (units, metre-cube test). https://docs.unity3d.com/2018.3/Documentation/Manual/HOWTO-ArtAssetBestPracticeGuide.html
- [S16] Unity blog, the experimental De-Lighting tool. https://blog.unity.com/en/technology/experimental-feature-de-lighting-tool
- [S17] Unreal Engine, Coordinate System and Spaces. https://dev.epicgames.com/documentation/en-us/unreal-engine/coordinate-system-and-spaces-in-unreal-engine
- [S18] Unreal Engine, FBX Import Options Reference. https://dev.epicgames.com/documentation/en-us/unreal-engine/fbx-import-options-reference-in-unreal-engine
- [S19] Unreal Engine 3 FBX Importer User Guide (UCX_, UBX_ and USP_ collision names; LODs in one FBX). https://docs.unrealengine.com/udk/Three/FBXImporterUserGuide.html
- [S20] Unreal Engine, Recommended Asset Naming Conventions. https://dev.epicgames.com/documentation/unreal-engine/recommended-asset-naming-conventions-in-unreal-engine-projects
- [S21] Unreal Engine, Skeletal Mesh Rendering Paths (bone influences 4, 8, up to 12). https://dev.epicgames.com/documentation/en-us/unreal-engine/skeletal-mesh-rendering-paths-in-unreal-engine
- [S22] Unreal Engine, How to Set Up Vehicles (wheel bones, chassis collision). https://dev.epicgames.com/documentation/unreal-engine/how-to-set-up-vehicles-in-unreal-engine
- [S23] Unreal Engine, Static Mesh Automatic LOD Generation. https://dev.epicgames.com/documentation/en-us/unreal-engine/static-mesh-automatic-lod-generation-in-unreal-engine
- [S24] Unreal Engine, Nanite Virtualized Geometry. https://dev.epicgames.com/documentation/en-us/unreal-engine/nanite-virtualized-geometry-in-unreal-engine
- [S25] Khronos 3D Commerce, Real-time Asset Creation Guidelines (units, +Y up and +Z front, centre-bottom origin, ORM layout, OpenGL normals, no non-manifold geometry, high-valence vertices). https://github.com/mlimper/3DC-Asset-Creation/blob/main/asset-creation-guidelines/RealtimeAssetCreationGuidelines.md and https://registry.khronos.org/3DCommerce/AssetCreation/specs/1.0/AssetCreationGuidelines.pdf
- [S26] Khronos glTF 2.0 specification (metallicRoughness texture channels). https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html
- [S27] Android Developers, Optimize geometry (Arm-contributed: vertex cost, micro-triangles, thin triangles, LOD). https://developer.android.com/games/optimize/geometry
- [S28] Adobe Substance 3D Sampler, PBR Validate filter, https://experienceleague.adobe.com/en/docs/substance-3d-sampler/using/filters/tools/pbr-validate ; albedo range figures from the Allegorithmic PBR Guide as discussed at https://polycount.com/discussion/comment/2636450
- [S29] Edge padding and mip bleeding: https://polycount.com/discussion/191707/padding and https://gamedev.net/forums/topic/699809-uv-maps-with-linear-filtering-problem (community guidance)
- [S30] Triangulate before baking, synced MikkTSpace tangents: https://polycount.com/discussion/comment/2452683 (community guidance)
- [S31] Texel density presets (10.24 px/cm first person, 5.12 px/cm third person): https://help.maxon.net/c4d/en-us/Content/html/Texel_Density.html.md and https://polycount.com/discussion/comment/2258684 (rules of thumb)
- [S32] Tiago Sousa, GPU Gems 3 chapter 16, Vegetation Procedural Animation and Shading in Crysis. https://developer.nvidia.com/gpugems/GPUGems3/gpugems3_ch16.html
- [S33] Joel Burgess and Nathan Purkeypile, Skyrim's Modular Approach to Level Design, GDC 2013. https://www.slideshare.net/JoelBurgess/gdc2013-kit-buildingfinal and https://www.gamedeveloper.com/design/skyrim-s-modular-approach-to-level-design
- [S34] Morten Olsen, The Ultimate Trim: Texturing Techniques of Sunset Overdrive, GDC 2015. https://gdcvault.com/play/1022323/The-Ultimate-Trim-Texturing-Techniques
- [S35] Valve, Stylization with a Purpose: the Illustrative World of Team Fortress 2 (GDC 2008), https://cdn.fastly.steamstatic.com/apps/valve/2008/GDC2008_StylizationWithAPurpose_TF2.pdf ; Mitchell, Francke and Eng, Illustrative Rendering in Team Fortress 2 (NPAR 2007), https://www.cs.princeton.edu/courses/archive/fall07/cos597B/papers/mitchell-team-fortress.pdf
- [S36] Dead Cells 3D-to-2D animation pipeline (Thomas Vasseur, Game Developer, 2018), summarised at https://80.lv/articles/case-study-dead-cells-character-art-pipeline/
- [S37] Sprite directions and mirroring: https://turbo-wiki.pages.dev/tim-cain/art/tiles-walking-and-projection , https://www.zdoom.org/wiki/Creating_new_sprite_graphics and https://gamedev.net/forums/topic/632342-direction-in-isometric-games/4987130/
- [S38] Wei et al., CoACD: Approximate Convex Decomposition for 3D Meshes with Collision-Aware Concavity and Tree Search (SIGGRAPH 2022, MIT licence). https://ar5iv.labs.arxiv.org/html/2205.02961 and https://github.com/SarahWeiii/CoACD
- [S39] Joint topology guides (edge loops at joints, pole placement): https://www.shootfirst.art/toplogy and https://michael-vl.github.io/learning-wiki/blender/topology/ (tutorial-level sources)
- [S40] Mixamo bone naming, as mapped by Cascadeur. https://cascadeur.com/help/getting_started/import_fbxdae/using_mixamo_rig_in_cascadeur
