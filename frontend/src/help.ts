// Help registry: the plain-language layer. Every stage, parameter, metric, check,
// model, platform, category and style has an entry {plain, meaning, why, technical}.
// The backend may send its own help (stage.help, param.help, check.help); the
// functions below prefer that and fall back to this registry.
import type { Category, Check, ModelInfo, Platform, StageId, StageInfo, StylePreset } from './api'
import { api, STAGES, stageEstimateSeconds } from './api'

export type HelpEntry = {
  plain: string // the plain name shown as a label
  meaning: string // one sentence: what it is
  why: string // one sentence: why it matters
  technical: string // the technical name, for people who want to search for it
}

const H = (plain: string, meaning: string, why: string, technical: string): HelpEntry => ({ plain, meaning, why, technical })

// ---------------------------------------------------------------------------
// Stages
// ---------------------------------------------------------------------------

// placeholder: the example shown in the empty "Describe the change" box of this step.
// Time estimates are not kept here: they come from GET /api/stages (see stageEstimate).
export type StageText = { plain: string; verb: string; gerund: string; help: HelpEntry; placeholder: string }

export const STAGE_TEXT: Record<StageId, StageText> = {
  concept: {
    plain: 'Describe',
    verb: 'Make concept art',
    gerund: 'Painting the concept art',
    placeholder: 'e.g. make the helmet open-faced and the shield round',
    help: H('Describe', 'Concept art: the first picture of your asset, painted from your description.', 'Everything that follows is built from this picture, so get the look right here.', 'concept'),
  },
  turnaround: {
    plain: 'Check all sides',
    verb: 'Check all sides',
    gerund: 'Drawing the other sides',
    placeholder: 'e.g. the back view should show the cloak',
    help: H('Check all sides', 'We look at your character from the front, sides and back so the 3D model gets the back right.', 'If a view looks wrong, regenerate just that view before building the model.', 'turnaround'),
  },
  mesh: {
    plain: 'Build the 3D model',
    verb: 'Build the 3D model',
    gerund: 'Building the 3D model',
    placeholder: 'e.g. thicker sword, less bulky shoulders',
    help: H('Build the 3D model', 'Turns the pictures into a 3D shape with painted surfaces.', 'This is the raw model; the next step tidies it for the game.', 'mesh'),
  },
  prep: {
    plain: 'Clean up for the game',
    verb: 'Clean up for the game',
    gerund: 'Cleaning up for the game',
    placeholder: 'e.g. more wear on the armour',
    help: H('Clean up for the game', 'Shrinks the model to the platform budget, fixes holes, sets its size and makes a tidy texture.', 'Game engines need a clean, light model; this step makes it one.', 'prep'),
  },
  rig: {
    plain: 'Make it move',
    verb: 'Make it move',
    gerund: 'Adding a skeleton',
    placeholder: 'e.g. smoother bending at the left elbow',
    help: H('Make it move', 'Adds a skeleton and attaches the surface to it so the model can be animated.', 'Without this the model is a statue; with it, walk and idle animations work.', 'rig'),
  },
  export: {
    plain: 'Hand off to the game',
    verb: 'Hand off to the game',
    gerund: 'Packing the files for the game',
    placeholder: 'e.g. drop the sprite sheet, add another detail level',
    help: H('Hand off to the game', 'Packs the model, textures, skeleton and animations into files a game engine can load.', 'This is the finished asset you drop into your game.', 'export'),
  },
}

export function stagePlain(stage: StageId, info?: StageInfo | null): string {
  return info?.plain_name || STAGE_TEXT[stage].plain
}

export function stageVerb(stage: StageId, info?: StageInfo | null): string {
  return info?.plain_verb || STAGE_TEXT[stage].verb
}

export function stagePlaceholder(stage: StageId): string {
  return STAGE_TEXT[stage].placeholder
}

export function stageGerund(stage: StageId): string {
  return STAGE_TEXT[stage].gerund
}

export function stageHelp(stage: StageId, info?: StageInfo | null): HelpEntry {
  const base = STAGE_TEXT[stage].help
  const h = info?.help
  if (!h) return { ...base, plain: stagePlain(stage, info) }
  return { plain: stagePlain(stage, info), meaning: h.meaning || base.meaning, why: h.why || base.why, technical: h.technical || base.technical }
}

// Seconds a stage usually takes: the backend estimate, else the /api/suggest estimate, else the fallback in api.ts.
export function stageEstimate(stage: StageId, info?: StageInfo | null, estimates?: Partial<Record<string, number>> | null): number {
  if (info?.estimate_seconds && info.estimate_seconds > 0) return info.estimate_seconds
  const s = estimates?.[stage]
  if (typeof s === 'number' && s > 0) return s
  return stageEstimateSeconds(stage)
}

export function fmtDuration(seconds: number, prefix = 'about '): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return 'a moment'
  if (seconds < 45) return `${prefix}${Math.max(5, Math.round(seconds / 5) * 5)} seconds`
  if (seconds < 90) return `${prefix}a minute`
  const m = Math.round(seconds / 60)
  if (m < 60) return `${prefix}${m} minutes`
  const h = Math.floor(m / 60)
  const rem = m % 60
  return `${prefix}${h} hour${h > 1 ? 's' : ''}${rem ? ` ${rem} minutes` : ''}`
}

// ---------------------------------------------------------------------------
// Models, platforms, categories, styles
// ---------------------------------------------------------------------------

// Model labels come from GET /api/models (the backend's data/models.yaml). These are used until it has loaded, or
// when it cannot be reached (the mock).
const MODEL_LABEL_FALLBACK: Record<string, string> = {
  zturbo: 'Z-Image Turbo',
  zbase: 'Z-Image Base',
  klein: 'FLUX.2 Klein 4B',
  anima: 'Anima Turbo',
  pixel: 'Z-Image Pixel Art',
  chroma: 'Chroma1-HD',
}

let apiModels: Record<string, ModelInfo> | null = null
let modelsRequest: Promise<void> | null = null

// Fetch the model list once; modelLabel starts it on first use. Call it early to have labels for the first render.
export function loadModels(): Promise<void> {
  if (!modelsRequest) {
    modelsRequest = api.models().then(
      (list) => {
        apiModels = Object.fromEntries(list.map((m) => [m.id, m]))
      },
      () => {
        modelsRequest = null // keep the fallback labels; try again on a later call
      },
    )
  }
  return modelsRequest
}

// Plain-language help per model; the label (plain) is filled from the API by modelHelp().
export const MODEL_HELP: Record<string, Omit<HelpEntry, 'plain'>> = {
  zturbo: { meaning: 'Fast painted look.', why: 'Good default: a clean painted concept in under half a minute.', technical: 'zturbo (Z-Image Turbo)' },
  zbase: { meaning: 'Higher quality, slower.', why: 'Use it when the Turbo result is close but not detailed enough.', technical: 'zbase (Z-Image Base)' },
  klein: { meaning: 'Quick drafts.', why: 'Fastest way to try several ideas before committing.', technical: 'klein (FLUX.2 Klein 4B)' },
  anima: { meaning: 'Anime line art.', why: 'Pick it for cel-shaded, anime-style characters.', technical: 'anima (Anima Turbo)' },
  pixel: { meaning: 'Pixel art sprites.', why: 'Pick it for retro, pixel-art games.', technical: 'pixel (Z-Image Pixel Art)' },
  chroma: { meaning: 'Loose painterly.', why: 'Pick it for a rough, expressive painted look.', technical: 'chroma (Chroma1-HD)' },
}

export function modelLabel(id: string | null | undefined): string {
  if (!id) return ''
  if (!apiModels) void loadModels()
  return apiModels?.[id]?.label || MODEL_LABEL_FALLBACK[id] || id
}

export function modelHelp(id: string): HelpEntry | null {
  const h = MODEL_HELP[id]
  const m = apiModels?.[id]
  if (h) return { plain: modelLabel(id), ...h }
  if (m) return H(m.label, m.description ? m.description.charAt(0).toUpperCase() + m.description.slice(1) + '.' : '', '', id)
  return null
}

export function modelReason(id: string | null | undefined): string {
  if (!id) return ''
  return (MODEL_HELP[id]?.meaning ?? '').replace(/\.$/, '').toLowerCase()
}

export const PLATFORM_HELP: Record<string, HelpEntry & { consequence: string }> = {
  mobile_low: { ...H('Phone: small and fast', 'For phones and low-end devices.', 'Keeps the model light so it runs smoothly on a phone.', 'mobile_low'), consequence: 'Few triangles and a 1024 px texture: lightest files, least surface detail.' },
  pc_mid: { ...H('PC and console', 'For desktop and current consoles.', 'A good balance of detail and performance.', 'pc_mid'), consequence: 'Medium triangle count and a 2048 px texture: the usual choice.' },
  high: { ...H('Showcase: large', 'For cinematics, trailers and high-end PCs.', 'Maximum detail; heavier files and slower to build.', 'high'), consequence: 'Many triangles and a 4096 px texture: biggest files, most detail.' },
}

export function platformPlain(id: string | null | undefined): string {
  if (!id) return ''
  return PLATFORM_HELP[id]?.plain ?? id
}

export function platformConsequence(id: string): string {
  return PLATFORM_HELP[id]?.consequence ?? ''
}

export const CATEGORY_HELP: Record<string, HelpEntry> = {
  humanoid: H('Humanoid', 'A person-shaped character: two arms, two legs, standing.', 'Gets a human skeleton with walk, idle and other shared clips.', 'humanoid'),
  quadruped: H('Quadruped', 'A four-legged animal or creature.', 'Gets a four-legged skeleton; animations are predicted from the shape.', 'quadruped'),
  winged: H('Winged creature', 'A creature with wings, such as a bird or dragon.', 'Gets a skeleton fitted to its shape; for now it walks and idles but does not flap.', 'winged'),
  multileg: H('Multi-leg creature', 'A creature with more than four legs, such as a spider.', 'Gets a skeleton fitted to its shape; for now the walk moves up to four legs.', 'multileg'),
  serpent: H('Serpent or fish', 'A long creature without legs.', 'Gets a spine-chain skeleton; for now it sways rather than slithers.', 'serpent'),
  vehicle: H('Vehicle or mech', 'A car, tank, ship or walking machine.', 'No skeleton; for now it comes out as one solid piece with a collision shape.', 'vehicle'),
  prop_rigid: H('Rigid prop', 'A solid object that does not bend or open, such as a barrel.', 'No skeleton; just a model with a collision shape.', 'prop_rigid'),
  prop_articulated: H('Articulated prop', 'An object with a moving part, such as a chest with a lid.', 'For now it comes out as one solid piece; the lid or door does not open yet.', 'prop_articulated'),
  environment: H('Environment piece', 'A piece of scenery, such as a wall or floor tile.', 'No skeleton; sized in metres, but not yet rounded to a grid, so check that pieces line up.', 'environment'),
  foliage: H('Foliage', 'A plant, tree or bush.', 'No skeleton; made to be scattered around a scene.', 'foliage'),
  material: H('Tileable material', 'A repeating surface texture, such as cobblestones.', 'Produces a texture only, no 3D model.', 'material'),
}

export const STYLE_HELP: Record<string, HelpEntry> = {
  painted: H('Painted', 'Smooth painterly shading with clean readable shapes.', 'The safest style for most games.', 'painted'),
  painterly: H('Hand-painted stylised', 'Chunky proportions, soft light, clean silhouette.', 'Works for fantasy and casual games.', 'painterly'),
  cel: H('Cel shaded', 'Flat colour regions and thin outlines, like anime.', 'Pairs with the Anima model.', 'cel'),
  toon: H('Toon shaded', 'Bold outlines and flat colours.', 'Reads well on small screens.', 'toon'),
  pixel: H('Pixel art', 'Crisp pixels and a limited palette.', 'For retro 2D games; sprites come from the finished model.', 'pixel'),
  retro64: H('Retro 64', 'Late 1990s console look: chunky shapes, small blurry textures.', 'Low detail on purpose; fast on every platform.', 'retro64'),
  lowpoly_flat: H('Low-poly flat', 'Faceted shapes with one colour per face.', 'Very light models; no texture detail.', 'lowpoly_flat'),
  realistic_pbr: H('Realistic', 'Photoreal materials and proportions.', 'Slower to make; pairs with the Z-Image base model.', 'realistic_pbr'),
  realistic: H('Realistic', 'Photoreal materials and proportions.', 'Slower to make; pairs with the Z-Image base model.', 'realistic'),
  clay: H('Clay', 'Stop-motion clay look with soft sculpted surfaces.', 'Pairs with the Klein model.', 'clay'),
}

// ---------------------------------------------------------------------------
// Terms (parameters and metrics share these; look up by the bare name)
// ---------------------------------------------------------------------------

export const TERM_HELP: Record<string, HelpEntry> = {
  // Nouns of the app
  project: H('Project', 'One game, or one set of assets that share a look.', 'Everything made inside a project matches in style and platform.', 'project'),
  asset: H('Asset', 'One distinct thing: a character, creature, prop or material.', 'Each asset carries its own description, category and platform.', 'asset'),
  version: H('Version', 'One attempt at one step of one asset.', 'Nothing is lost by trying again; every attempt is kept and you choose which one to continue from.', 'version'),
  prompt: H('Description', 'What you want, written in plain words.', 'The better the description, the closer the first picture.', 'prompt'),
  category: H('Category', 'What kind of thing this is: a person, an animal, a prop.', 'It decides the pose, the skeleton and the size budget.', 'category'),
  style_preset: H('Style', 'The look: painted, cel shaded, pixel art and so on.', 'Picks the model and the wording added to every description.', 'style_preset'),
  platform: H('Platform', 'Where the game will run: phone, PC or showcase.', 'Sets how many triangles and how big a texture the model may have.', 'platform_target'),
  parts: H('Parts', 'Pieces that must move or swap on their own, such as a weapon or a lid.', 'We ask the painter to draw these as clearly separate pieces; for now they stay joined to the model.', 'parts'),
  style_lock: H('Style lock', 'The look every asset in this project shares.', 'Keeps a whole game looking like one game.', 'style_lock'),
  prompt_block: H('Style words', 'Words added in front of every description in this project.', 'This is how the style stays the same from asset to asset.', 'prompt_block'),
  palette: H('Palette', 'The handful of colours the project uses.', 'Shared colours make assets look like they belong together.', 'palette'),
  negative: H('Avoid', 'Things the painter should leave out, such as text or blur.', 'Removes common mistakes before they happen.', 'negative prompt'),
  loras: H('Style add-ons', 'Small trained add-ons that push the painter toward a particular look.', 'Optional; most projects need none.', 'LoRA'),
  lora: H('Style add-on', 'A small trained add-on that pushes the painter toward a particular look.', 'Optional; most projects need none.', 'LoRA'),
  model: H('Painter', 'The AI model that paints the picture.', 'Different painters are faster, slower or suit different styles.', 'model (checkpoint)'),
  concept_model: H('Painter', 'The AI model that paints the first picture of this asset.', 'We suggest one that suits the look and platform; pick another if you prefer its style. You can change it later under Fine-tune.', 'concept_model'),
  stage: H('Step', 'One of the six steps every asset goes through: describe, check all sides, build the 3D model, clean up, make it move, hand off.', 'Each step builds on the version you kept in the step before it.', 'stage'),
  // Image generation
  seed: H('Seed', 'A number that picks which of many possible pictures you get.', 'Same seed, same picture; change it to get a different one.', 'seed'),
  steps: H('Steps', 'How many passes the painter takes.', 'More passes can add detail but take longer; the default is usually right.', 'steps'),
  cfg: H('Prompt strength', 'How strictly the painter follows the description.', 'Too high looks forced, too low wanders off.', 'CFG (classifier-free guidance)'),
  width: H('Width', 'Picture width in pixels.', 'Bigger pictures hold more detail and take longer.', 'width'),
  height: H('Height', 'Picture height in pixels.', 'Bigger pictures hold more detail and take longer.', 'height'),
  size: H('Size', 'Picture size in pixels.', '1024 is the sweet spot; bigger takes longer.', 'width and height'),
  readiness_block: H('Pose for 3D', 'Adds the standard pose and plain background to the description.', 'Keep this on: the 3D step needs a clear, whole figure on a plain background.', 'readiness_block'),
  prompt_override: H('Replace description', 'Use this text instead of the asset description for one attempt.', 'Handy for a one-off experiment without changing the asset.', 'prompt_override'),
  cutout: H('Cutout', 'The picture with the background removed.', 'The 3D step works from the cutout, so a clean edge matters.', 'cutout (alpha matte)'),
  finetune: H('Fine-tune', 'Re-run this step with different settings.', 'Use it when the result is close but a setting is off; the description stays the same.', 'adjust'),
  instruction: H('Change request', 'What you asked to change in this attempt.', 'Recorded so you can see how this version came about.', 'instruction'),
  // Turnaround
  views: H('Views', 'Which sides to draw: front, left, back, right, a three-quarter view and top.', 'The 3D step uses them to get the back and sides right.', 'views'),
  view: H('View', 'One side of the character.', 'Regenerate a single view when only that one looks wrong.', 'view'),
  turnaround: H('All sides', 'The same character drawn from several sides.', 'Without it the 3D model has to guess what the back looks like.', 'turnaround'),
  lora_strength: H('Side-view strength', 'How hard we push the painter to keep the character consistent across views.', 'Raise it if the sides drift from the front; lower it if they look stiff.', 'lora_strength (multi-angle LoRA)'),
  auto_reroll: H('Retry bad views', 'Automatically redraw a view that fails the consistency check.', 'Saves you a click on most assets.', 'auto_reroll'),
  consistency_ok: H('Sides match', 'Whether every view agrees with the front view in size and colour.', 'Mismatched views make a lopsided model.', 'consistency_ok'),
  views_passed: H('Views that match', 'How many views passed the consistency check.', 'All views should pass before building the model.', 'views_passed'),
  coverage: H('Fills the frame', 'How much of the picture the character covers.', 'Too small and the model loses detail.', 'coverage'),
  height_ratio: H('Height match', 'Height of this view compared with the front view.', 'Views of different heights make a distorted model.', 'height_ratio'),
  hist_dist: H('Colour match', 'How different the colours are from the front view.', 'Colour drift between views causes patchy textures.', 'hist_dist (histogram distance)'),
  mirror_iou: H('Left-right match', 'How well the left and right views mirror each other.', 'Catches a side view that drew a different character.', 'mirror_iou'),
  // Mesh
  engine: H('Builder', 'The AI model that turns pictures into a 3D shape.', 'TRELLIS is the default; Hunyuan is the alternative when a shape comes out wrong.', 'engine (trellis | hy3d)'),
  input_view: H('Build from', 'Which picture the builder starts from.', 'The three-quarter view usually gives the best shape; the concept keeps the most detail.', 'input_view'),
  target_faces: H('Detail level', 'How many triangles the model is allowed.', 'More triangles mean more detail and a heavier model.', 'target_faces'),
  triangles: H('Triangles', 'The small flat pieces a 3D model is made of.', 'More triangles mean more detail but a slower game; each platform has a budget.', 'triangles (faces)'),
  budget: H('Budget', 'The most triangles or the biggest texture the platform allows.', 'Staying inside the budget keeps the game fast.', 'budget'),
  vertices: H('Points', 'The corner points of the triangles.', 'Roughly half the triangle count; another measure of weight.', 'vertices'),
  texture_size: H('Texture size', 'The size of the painted surface image in pixels.', 'Bigger textures look sharper up close and use more memory.', 'texture_size'),
  texture: H('Texture', 'The painted image wrapped around the model.', 'It is what gives the model its colours and surface detail.', 'texture'),
  normal_map: H('Normal map', 'A special texture that fakes small bumps and dents, such as rivets or wood grain, without adding triangles.', 'The model looks detailed up close while staying light enough for the game.', 'normal map'),
  triangle_budget: H('Triangle budget', 'The most triangles a model may have on the chosen platform.', 'Staying inside it keeps the game running smoothly; the clean-up step shrinks the model to fit.', 'triangle budget (triangles per asset)'),
  upsample: H('Texture sharpening', 'Sharpens the painted surface at a larger size.', 'Use the larger size for showcase assets; it is slower.', 'upsample'),
  remesh: H('Close holes', 'Rebuild the surface so it is one closed shell.', 'Closed shells clean up and rig reliably.', 'remesh (watertight)'),
  silhouette_iou: H('Outline match', 'How closely the model outline matches the drawing from each side.', 'Low match means the model does not look like the picture.', 'silhouette IoU (intersection over union)'),
  iou: H('Outline match', 'How closely two outlines overlap, from 0 to 1.', 'Low match means the shapes disagree.', 'IoU (intersection over union)'),
  silhouette: H('Outline', 'The shape of the model seen as a flat shadow.', 'A good outline is what makes a character readable in a game.', 'silhouette'),
  // Prep
  height_m: H('Size', 'How big the asset is in the game, in metres, measured the way that makes sense for it: height for people, length for cars and snakes.', 'Keeps everything in proportion, so a car is car-sized next to your character.', 'height_m (older name of size_m)'),
  size_m: H('Size', 'How big the asset is in the game, in metres, measured the way that makes sense for it: height for people, length for cars and snakes.', 'Keeps everything in proportion, so a car is car-sized next to your character.', 'Uniform scale so the extent on size_axis equals size_m'),
  face_yaw: H('Front', 'Which side of the model the game treats as its front.', 'Characters walk and cars drive toward their front; if it is wrong, they move sideways.', 'Yaw about Y so the concept front faces +Z'),
  symmetric: H('Mirror left and right', 'Make the left and right halves identical.', 'Most characters are symmetric; turn it off for lopsided things.', 'symmetric'),
  pivot: H('Anchor point', 'Where the model sits when placed: at its feet or its centre.', 'Characters stand on their feet; floating things use the centre.', 'pivot (base | center)'),
  smooth_angle: H('Smoothing', 'The angle above which an edge looks sharp instead of smooth.', 'Lower keeps hard edges; higher rounds things off.', 'smooth_angle (auto smooth)'),
  manifold: H('Clean surface', 'No holes or overlapping edges.', 'Game engines and the skeleton step need a clean surface.', 'manifold'),
  non_manifold_edges: H('Broken edges', 'Edges shared by too many faces or by none.', 'They cause shading errors and rigging problems.', 'non_manifold_edges'),
  watertight: H('Closed shell', 'The surface has no openings.', 'A closed shell cleans up and casts shadows correctly.', 'watertight'),
  boundary_edges: H('Open edges', 'Edges with nothing on one side: the rim of a hole.', 'Zero means the model is closed.', 'boundary_edges'),
  degenerate_faces: H('Zero-area faces', 'Triangles squashed to a line or a point.', 'They cause rendering glitches.', 'degenerate_faces'),
  uv_set: H('Texture layout', 'The map that says which part of the texture goes where.', 'There should be exactly one.', 'uv_set'),
  uv_layers: H('Texture layouts', 'How many texture maps the model carries.', 'Exactly one is what engines expect.', 'uv_layers'),
  uv_islands: H('Texture pieces', 'Separate patches in the texture layout.', 'Fewer, bigger pieces waste less texture.', 'uv_islands'),
  texel_density: H('Texture sharpness', 'How many texture pixels cover one metre of surface.', 'Even sharpness across the model avoids blurry patches.', 'texel_density'),
  gate_ok: H('Looks good', 'Whether every quality check passed.', 'A failing check means the asset may misbehave in the game.', 'gate_ok'),
  checks_passed: H('Checks passed', 'How many quality checks passed.', 'All of them should.', 'checks_passed'),
  dims: H('Size', 'Width, depth and height of the model in metres.', 'Sanity check for scale.', 'dims'),
  // Rig
  rig: H('Rig', 'The skeleton inside the model plus the skin weights that tie the surface to it.', 'A rigged model can be animated: it walks, idles and bends at the joints.', 'rig (armature and skin weights)'),
  template: H('Skeleton type', 'Which skeleton to fit: a standard human one, a generic one, or none.', 'The standard human skeleton gets shared animations for free.', 'template (auto | mixamo | generic | none)'),
  skeleton: H('Skeleton', 'The set of bones inside the model that animations move.', 'No skeleton, no movement.', 'skeleton (armature)'),
  bones: H('Bones', 'The joints of the skeleton.', 'More bones mean finer movement and a slightly heavier asset.', 'bones'),
  skin_weights: H('Skin weights', 'How much each point of the surface follows each bone.', 'Bad weights make the surface tear or bend wrongly.', 'skin weights'),
  weight_coverage: H('Surface attached', 'Share of the surface that follows at least one bone.', 'Unattached points stay behind when the character moves.', 'weight_coverage'),
  group_per_vertex: H('Bones per point', 'How many bones one surface point may follow.', '4 is standard; lower is faster, higher bends more smoothly.', 'group_per_vertex (max influences)'),
  max_influences: H('Bones per point', 'The most bones any surface point follows.', 'Game engines usually allow 4.', 'max_influences'),
  weights_normalised: H('Weights add up', 'Each point\'s weights add up to exactly 1.', 'Otherwise the surface drifts when it bends.', 'weights_normalised'),
  unnormalised_verts: H('Points with bad weights', 'Surface points whose weights do not add up to 1.', 'Should be zero.', 'unnormalised_verts'),
  single_root: H('One root bone', 'The skeleton starts from one bone.', 'Engines expect one root.', 'single_root'),
  bottom_center_origin: H('Stand on the ground', 'Put the anchor at the feet, centred.', 'The character then stands on the floor when placed.', 'bottom_center_origin'),
  bend_test: H('Bend test', 'Bend each limb and take pictures to check the skin follows.', 'Catches bad weights before export.', 'bend_test'),
  bent: H('Bent joints', 'The joints the bend test moved.', 'A joint missing from the list was not tested.', 'bent'),
  anim_kind: H('Animation set', 'Which family of animations fits: biped, quadruped and so on.', 'Decides which clips can be attached.', 'anim_kind'),
  faces_rigged: H('Triangles rigged', 'How many triangles the skeleton was fitted on.', 'A reduced copy is used for speed.', 'faces_rigged'),
  rigid: H('Rigid', 'Whether the model was treated as a solid object without a skeleton.', 'Props are rigid; characters are not.', 'rigid'),
  clip: H('Clip', 'One animation, such as walk or idle.', 'Clips are what the game plays.', 'clip'),
  clips: H('Animations', 'The animations attached: walk, idle, run and so on.', 'They come from a shared library and are fitted to the skeleton.', 'clips'),
  clips_library: H('Animation library', 'Which shared set of animations to attach.', 'None skips animations.', 'clips_library'),
  // Creature moves (3.7)
  motion: H('How it moves', 'Whether the creature walks, flies or slithers.', 'Decides which animations it gets; change it for a bird that cannot fly or a fish shaped like a snake.', 'Procedural locomotion mode: legs, wings, wave'),
  moves: H('Moves', 'The animations made for this creature: standing still, walking, running, turning, attacking, getting hit and so on.', 'Leave out moves your game will never use to keep the file smaller.', 'Procedural clip set (procanim.py)'),
  move_size: H('How big the moves are', 'How far the legs, body and wings swing in each move.', 'Small suits heavy or calm creatures; big suits lively ones.', 'Amplitude 0.7, 1.0 or 1.3'),
  move_speed: H('How fast', 'How quickly the moves play.', 'Match the creature\'s size and mood: big animals move slower.', 'Clip speed 0.8, 1.0 or 1.25'),
  // Hit boxes and the body shape for moving (4.2)
  hitboxes: H('Hit boxes', 'Invisible shapes on each body part that tell the game where an attack landed.', 'Hits count where they look like they land, and you can tell a head hit from an arm hit.', 'Per-bone trigger capsules and boxes, fitted from skin weights'),
  hitbox_fit: H('How snug', 'How tightly the hit boxes wrap the body.', 'Snug is fairer for the player being hit; loose is kinder to the player aiming.', 'Radius factor 0.9, 1.0 or 1.15 on the 80th-percentile vertex distance'),
  hitbox_regions: H('Body parts with hit boxes', 'Which body parts get their own hit box.', 'Leave out parts your game never needs to tell apart.', 'Region ids from hitboxes.json regions_found'),
  movement_shape: H('Body shape for moving', 'One simple shape around the whole body that the game uses for walking into walls and standing on floors.', 'It stays still while the hit boxes move, so movement is smooth and predictable.', 'Root CapsuleCollider (direction Y or Z) or BoxCollider'),
  // Export
  lod: H('Detail levels', 'Lighter copies of the model used when it is far away.', 'Keeps the game fast with many assets on screen.', 'LOD (level of detail)'),
  lods: H('Detail levels', 'Lighter copies of the model used when it is far away, as fractions of the full model.', 'Keeps the game fast with many assets on screen.', 'lods (LOD ratios)'),
  lod_count: H('Number of detail levels', 'How many lighter copies were made.', 'Characters and creatures get four; props and scenery get three.', 'lod_count'),
  collider: H('Collision shape', 'Invisible simple shapes the game uses to know where the asset is solid.', 'Without them things pass straight through it; the right shapes keep doorways open and seats usable.', 'Primitive, convex hull (255-triangle cap), CoACD convex decomposition, or mesh collider (static only)'),
  collider_triangles: H('Collision shape size', 'How many triangles the collision shape has.', 'Unity allows at most 255 in a simple shape and reshapes anything bigger.', 'MeshCollider convex triangle limit'),
  collider_vertices: H('Collision shape points', 'How many points the collision shape has.', 'Unity allows at most 255 triangles in a simple collision shape; we keep the shape small to stay under it.', 'collider_vertices'),
  collider_verts: H('Collision shape points', 'How many points the collision shape has.', 'Unity allows at most 255 triangles in a simple collision shape; we keep the shape small to stay under it.', 'collider_verts'),
  collider_present: H('Collision shape present', 'Whether a collision shape was made.', 'Needed for physics.', 'collider_present'),
  metal_shine: H('Metal and shine', 'A texture that tells the engine which parts are metal and how glossy each part is.', 'Without it armour looks like plastic and wet stone looks dry.', 'MetallicSmoothness map (URP Lit: metallic in RGB, smoothness in alpha)'),
  metallic_smoothness: H('Metal and shine', 'A texture that tells the engine which parts are metal and how glossy each part is.', 'Without it armour looks like plastic and wet stone looks dry.', 'MetallicSmoothness map (URP Lit: metallic in RGB, smoothness in alpha)'),
  orm: H('Surface texture', 'One texture that holds shadowing, roughness and metal in its three colour channels.', 'Unreal, Godot and web viewers read all three from one file, which saves memory.', 'ORM pack (R occlusion, G roughness, B metallic); glTF metallicRoughness'),
  animator_controller: H('Animation controller', 'The part of the game object that picks which animation plays: standing, walking or running, depending on speed.', 'Your game sets one number, Speed, and the character switches on its own.', 'AnimatorController: 1D blend tree on Speed; triggers Attack, Hit, Die, Jump; root motion off'),
  loop: H('Loops', 'The animation runs again from the start as soon as it ends, with no jump.', 'Idle and walk must loop, or the character freezes after one step.', 'AnimationClip loopTime and loopPose'),
  avatar: H('Skeleton in Unity', 'Whether Unity reads the skeleton as a person (Humanoid) or as its own shape (Generic).', 'Humanoid skeletons can play every shared human animation; Generic ones play only their own.', 'Unity Avatar: Humanoid (HumanDescription) or Generic'),
  what_you_get: H('What you get', 'A short list of what the finished asset does in a game: its size, its surface, how it moves and what it bumps into.', 'Each line is checked; a line with a yellow dot has a problem, and the button next to it fixes it.', 'metrics.finish_lines (export stage)'),
  unity_howto: H('Using it in Unity', 'The three things to do in Unity to put this asset in your game.', 'The prefab is already set up; these lines say how to drive it from your own scripts.', 'metrics.unity_howto (export stage)'),
  formats: H('File formats', 'Which file types to write: FBX for most engines, GLB for the web.', 'Pick what your engine imports.', 'formats (fbx | glb)'),
  unity: H('Unity check', 'Import the asset into Unity and build a ready-to-use prefab.', 'Proves the asset works in a real engine.', 'unity'),
  unity_import: H('Unity imported it', 'Whether Unity loaded the files without errors.', 'If this fails the asset needs fixing before use.', 'unity_import'),
  unity_humanoid_avatar: H('Unity recognised the skeleton', 'Whether Unity mapped the skeleton as a humanoid.', 'Needed for shared humanoid animations in Unity.', 'unity_humanoid_avatar'),
  unity_report: H('Unity report', 'What Unity said when it imported the asset.', 'Warnings are fine; errors are not.', 'unity_report'),
  sprites: H('Sprite sheet', 'Also render flat 2D pictures of the animations from several directions.', 'For 2D games that want a 3D-made character.', 'sprites'),
  sprite_directions: H('Sprite directions', 'How many viewing directions to render.', '8 is common for top-down games.', 'sprite_directions'),
  sprite_size: H('Sprite size', 'Pixel size of each sprite frame.', 'Bigger is sharper and heavier.', 'sprite_size'),
  manifest: H('Manifest', 'A file listing everything in the export and where it came from.', 'Useful for tracking what was used.', 'manifest'),
  elapsed_seconds: H('Time taken', 'How long this step took.', 'Helps you plan.', 'elapsed_seconds'),
  models: H('Models used', 'The AI models that made this version.', 'Recorded for licensing.', 'models'),
  readiness: H('Ready for 3D', 'Score for how well the picture suits the 3D step.', 'Higher is better.', 'readiness'),
  pose_score: H('Pose score', 'How close the pose is to the standard one.', 'A standard pose rigs better.', 'pose_score'),
  judge: H('Judge', 'Recompute the quality checks for this version.', 'Use it after changing the budget.', 'judge'),
  variant: H('Variant', "A copy of an asset with one change (outfit, colour, damage) that keeps the original's identity.", 'Make one for the same character in winter clothes or battle-damaged; it starts from the original picture, so it stays recognisable.', 'variant (base_asset_id)'),
  variant_change: H('What changes', 'The one change that makes this variant different from the original.', 'Keep it short: an outfit, a colour scheme, a damage state.', 'variant_change'),
  // Feedback and the gallery
  feedback: H('Liked it?', 'Thumbs up or down on a result, with an optional note on what was wrong.', 'It only teaches this app which results you liked so it can suggest better settings later. Nothing is sent anywhere; it stays in your own library.', 'feedback (metrics.feedback)'),
  feedback_note: H('What was wrong?', 'A few words on why you did not like this result, such as "face too dark" or "sword bent".', 'Optional. It is kept with the version so you, and the app, can see what to avoid next time.', 'feedback.note'),
  gallery: H('Gallery', 'Every finished asset from every project in one place.', 'Find a finished asset, open it, or copy the folder its files were saved to.', 'gallery (GET /api/gallery)'),
  delivered_to: H('Saved to', 'The folder on this computer where the finished files were copied.', 'Open this folder, or point your game engine at it, to use the asset.', 'delivered_to'),
  // Autopilot
  autopilot: H('Autopilot', 'Runs every step for you, keeps each result that passes its checks, and stops only when something needs you.', 'Press "Make it" once and come back to a finished asset; you can still change any step afterwards.', 'autopilot (POST /api/assets/{id}/autopilot)'),
  stop_on: H('When to stop', 'Which check failures make the autopilot pause: serious problems only, any warning, or never.', 'Serious problems would break the asset in the game, so the default pauses on those and continues past warnings.', 'stop_on (block | warn | never)'),
  until_stage: H('Run until', 'The last step the autopilot runs before it stops.', 'Usually the hand-off; stop earlier if you only want the picture or the model.', 'until_stage'),
  // Style presets
  look: H('Look', 'A ready-made style: the painter, the style words, a palette and what to avoid, chosen together so they work.', 'Pick one and every asset in the project matches it; edit the fields by hand only if you want something unusual.', 'style preset (GET /api/style-presets, stored as style_lock.preset)'),
  look_preset: H('Look', 'A ready-made style: the painter, the style words, a palette and what to avoid, chosen together so they work.', 'Pick one and every asset in the project matches it; edit the fields by hand only if you want something unusual.', 'style preset (style_lock.preset)'),
  // Graphics cards (Health page)
  gpus: H('Graphics cards', 'The cards in this computer that paint the pictures and build the 3D models. Only those generation steps use a card at full load; cleaning up, handing off and the Unity import run on the processor.', 'If you need a card for a game or another app, turn it off here and the app carries on with the other one.', 'ComfyUI instances (GET /api/gpus)'),
  gpu_switch: H('On or off', 'Turning a card off hands its work to the other card so you can use it for games or other apps. A step only this card can do waits until it is back on. Nothing is lost.', 'You can switch it back on at any time; the app remembers your choice after a restart.', 'PUT /api/gpus/{instance} enabled (prefs.json disabled_instances)'),
  gpu_memory: H('Memory used', 'How much of the card\'s own memory is in use right now, by this app and anything else.', 'A card keeps its models loaded between steps so the next step starts faster; that memory is released when you turn the card off.', 'VRAM (ComfyUI /system_stats)'),
  gpu_advanced: H('Advanced', 'Finer control over one card: which kinds of work it may do, and whether it empties its memory after every step.', 'Most people never need these; turning the card off covers the usual case.', 'allowed_kinds, free_after_job'),
  gpu_kinds: H('What this card may do', 'Untick a kind of work to keep it off this card. Work of that kind moves to another card that may do it, or waits until one may.', 'Useful when a card is busy with something else but can still help with lighter work. Greyed out boxes are work this card cannot do at all.', 'allowed_kinds (prefs.json)'),
  gpu_kind_pictures: H('Pictures', 'Painting the first picture, drawing all sides, and making tileable materials.', 'These are the quickest steps and the ones you make the most of.', 'concept, turnaround and material stages'),
  gpu_kind_model: H('3D model', 'Turning the pictures into a 3D shape with painted surfaces.', 'The heaviest step: it needs a lot of the card\'s memory for a few minutes.', 'mesh stage'),
  gpu_kind_rigging: H('Rigging', 'Adding a skeleton so the model can move.', 'This step only works on the card set up for it, because it needs Blender wired in.', 'rig stage (SkinToken)'),
  gpu_kind_review: H('Review', 'The app looking at a result and saying what it thinks of it.', 'Optional; a review waits if no card may do it, nothing else stops.', 'local judge (judge job)'),
  gpu_free_after: H('Free memory after each step', 'After every step on this card, the app tells it to unload its models and release its memory.', 'Leaves the card\'s memory free for your other apps between steps, at the cost of a slower next step, because the models have to load again.', 'free_after_job (ComfyUI POST /free)'),
}

// Plain words for the autopilot's stop_on setting.
export const STOP_ON_OPTIONS: { id: 'block' | 'warn' | 'never'; label: string; hint: string }[] = [
  { id: 'block', label: 'Stop for serious problems', hint: 'Pauses when a check fails that would break the asset; warnings do not stop it.' },
  { id: 'warn', label: 'Stop for any warning', hint: 'Pauses on every failed check, even small ones.' },
  { id: 'never', label: 'Never stop for checks', hint: 'Keeps going whatever the checks say; only a failed step pauses it.' },
]

// "Autopilot: Building the 3D model, about 7 minutes left, step 3 of 6".
export function autopilotLine(stage: StageId | null, secondsLeft: number | null, extra?: { queued?: boolean }): string {
  if (!stage) return 'Autopilot: starting.'
  const k = STAGES.indexOf(stage) + 1
  const time = extra?.queued ? 'waiting for a free machine' : secondsLeft !== null ? `${fmtDuration(secondsLeft)} left` : ''
  const g = stageGerund(stage)
  return `Autopilot: ${g.charAt(0).toLowerCase() + g.slice(1)}${time ? `, ${time}` : ''}, step ${k} of ${STAGES.length}`
}

// Seconds left for the autopilot: the rest of the current stage plus every stage after it, up to until_stage.
export function autopilotSecondsLeft(stage: StageId, progress: number, untilStage: StageId, stages: StageInfo[] | null | undefined, estimates: Record<string, number> | null | undefined): number {
  const from = STAGES.indexOf(stage)
  const to = Math.max(from, STAGES.indexOf(untilStage) < 0 ? STAGES.length - 1 : STAGES.indexOf(untilStage))
  let total = 0
  for (let i = from; i <= to; i++) {
    const s = STAGES[i]
    const est = stageEstimate(s, stages?.find((x) => x.id === s), estimates)
    total += i === from ? est * (1 - Math.min(1, Math.max(0, progress))) : est
  }
  return total
}

export const VARIANT_EXAMPLES = ['winter cloak and fur boots', 'rusted and battle-damaged', 'red and gold colour scheme']

// ---------------------------------------------------------------------------
// Checks: plain sentence per check id, with the value and limit filled in
// ---------------------------------------------------------------------------

type CheckText = (value: unknown, limit: unknown, passed: boolean, ctx: CheckContext, vars: Record<string, unknown>) => string
// platform: for the triangle budget sentence; category: for the size sentence's axis word when the check has none.
export type CheckContext = { platform?: string; category?: string }

const num = (x: unknown): number | null => (typeof x === 'number' && Number.isFinite(x) ? x : null)
// Percent with one decimal when a whole number would hide the difference (98.9% vs 99%).
const pct = (x: unknown): string => {
  const n = num(x)
  if (n === null) return '?'
  const p = n * 100
  return Math.abs(p - Math.round(p)) < 0.05 ? `${Math.round(p)}%` : `${p.toFixed(1)}%`
}
const n0 = (x: unknown): string => {
  const n = num(x)
  return n === null ? String(x ?? '?') : n.toLocaleString('en-US')
}
// Metres with at most two decimals: 1.8, 0.45, 4.25.
export const metres = (x: unknown): string => {
  const n = num(x)
  if (n === null) return String(x ?? '?')
  return String(Math.round(n * 100) / 100)
}
const str = (x: unknown): string => (typeof x === 'string' ? x : typeof x === 'number' ? String(x) : '')
const listText = (x: unknown): string => (Array.isArray(x) ? joinAnd(x.map(String)) : str(x))
// "a, b and c"
export function joinAnd(items: string[]): string {
  if (items.length <= 1) return items[0] ?? ''
  return items.slice(0, -1).join(', ') + ' and ' + items[items.length - 1]
}
// The plain name of a missing surface map (materials_connected) and what the surface looks like without it.
const MAP_EFFECT: Record<string, string> = { 'metal and shine': 'like plastic', bump: 'flat', shadow: 'a little flat', colour: 'grey' }
const mapName = (id: string): string => {
  const m = id.match(/^texture_(.+)_(pow2|square|budget)$/)
  if (!m) return 'texture'
  return m[1].replace(/_/g, ' ') + ' texture'
}

const CHECK_TEXT: Record<string, CheckText> = {
  triangle_budget: (v, l, ok, ctx) => {
    const over = num(v) !== null && num(l) !== null ? num(v)! - num(l)! : null
    const who = ctx.platform ? platformPlain(ctx.platform).split(':')[0].toLowerCase() : 'this platform'
    return ok ? `The model has ${n0(v)} triangles, within the ${n0(l)} ${who} allows.` : `The model has ${over !== null ? n0(over) + ' more' : 'more'} triangles than ${who === 'phone' ? 'a phone' : who} can handle (${n0(v)} of ${n0(l)}).`
  },
  manifold: (v, _l, ok) => (ok ? 'The surface has no overlapping or broken edges.' : `The surface has ${n0(v)} broken or overlapping edges.`),
  watertight: (v, _l, ok) => (ok ? 'The surface is closed with no holes.' : `The surface has ${n0(v)} open holes.`),
  degenerate_faces: (v, _l, ok) => (ok ? 'No zero-area triangles.' : `${n0(v)} triangles are squashed flat.`),
  height_m: (v, l, ok, _ctx, vars) => sizeSentence(v, l, ok, str(vars.axis_word) || 'tall'),
  size_m: (v, l, ok, ctx, vars) => sizeSentence(v, l, ok, str(vars.axis_word) || axisWord(sizeAxisOf(ctx.category), ctx.category)),
  facing: (v, _l, ok) => (ok ? 'The front of the model faces forward in the game.' : `The model may face the wrong way: it matches the front picture best when turned ${n0(v)} degrees.`),
  uv_set: (v, _l, ok) => (ok ? 'The model has one texture layout.' : `The model has ${n0(v)} texture layouts; it needs exactly one.`),
  bones: (v, _l, ok) => (ok ? `The skeleton has ${n0(v)} bones.` : 'No skeleton was found.'),
  single_root: (v, _l, ok) => (ok ? 'The skeleton starts from one root bone.' : `The skeleton has ${n0(v)} root bones; it needs one.`),
  weight_coverage: (v, l, ok) => (ok ? `${pct(v)} of the surface follows the bones.` : `Only ${pct(v)} of the surface follows the bones; it needs ${pct(l)}.`),
  max_influences: (v, l, ok) => (ok ? `Each surface point follows at most ${n0(v)} bones.` : `Some surface points follow ${n0(v)} bones; the limit is ${n0(l)}.`),
  weights_normalised: (v, _l, ok) => (ok ? 'All skin weights add up correctly.' : `${n0(v)} surface points have weights that do not add up.`),
  clips: (v, l, ok, _ctx, vars) => {
    if (ok) return movesLine(v) || `Animations attached: ${listText(v)}.`
    const missing = movesPlain(vars.missing)
    return missing.length ? `Some moves could not be made: ${joinAnd(missing)}.` : `Some animations are missing (expected ${listText(l)}).`
  },
  legs_moved: (v, l, ok) => (ok ? `All ${n0(v)} legs move in the walk.` : `Only ${n0(v)} of ${n0(l)} legs move in the walk.`),
  wings_found: (_v, _l, ok) => (ok ? 'Both wings were found, so it can fly and glide.' : 'We could not find the wings in the skeleton, so it walks but cannot fly.'),
  hitbox_cover: (v, _l, ok, _ctx, vars) => {
    const cover = str(vars.cover) || pct(v)
    return ok ? `${str(vars.n) || 'The'} hit boxes cover ${cover} of the body and follow the skeleton.` : `The hit boxes cover only ${cover} of the body, so some hits would pass through.`
  },
  hitbox_overreach: (v, _l, ok, _ctx, vars) => (ok ? 'The hit boxes stay close to the body.' : `The hit boxes stick out well past the body (${str(vars.times) || num(v)?.toFixed(1) || v} times its size), so hits would land in thin air.`),
  rig_restore: (_v, _l, ok) => (ok ? 'The rigged model kept its textures and its size.' : 'The rigged model lost its textures or its size while the skeleton was fitted.'),
  hitbox_fit: (_v, _l, ok) => (ok ? 'Hit boxes were fitted to the skeleton.' : 'We could not fit hit boxes on this skeleton; it still has its body shape for moving.'),
  lod_count: (v, l, ok) => (ok ? `${n0(v)} detail levels were made.` : `${n0(v)} detail levels were made; ${n0(l)} expected.`),
  collider_present: (_v, _l, ok) => (ok ? 'A collision shape is present.' : 'No collision shape was made.'),
  collider_vertices: (v, l, ok) => (ok ? `The collision shape has ${n0(v)} points (limit ${n0(l)}).` : `The collision shape has ${n0(v)} points; engines allow ${n0(l)}.`),
  unity_import: (_v, _l, ok) => (ok ? 'Unity imported the asset without errors.' : 'Unity could not import the asset.'),
  unity_humanoid_avatar: (v, _l, ok, _ctx, vars) => {
    const n = num(vars.clips) ?? num(v)
    return ok
      ? `Unity recognised the skeleton as a person, so all ${n !== null ? n0(n) + ' ' : 'the '}shared animations work.`
      : 'Unity could not read the skeleton as a person, so the shared animations are missing; it still has its own walk and idle.'
  },
  materials_connected: (v, _l, ok, _ctx, vars) => {
    if (ok) return 'Unity uses all the surface textures: colour, bumps, shadows, metal and shine.'
    const missing = str(vars.missing) || str(v) || 'metal and shine'
    const effect = str(vars.effect) || MAP_EFFECT[missing] || 'flat'
    return `Unity is missing the ${missing} texture, so the surface will look ${effect}.`
  },
  clips_loop: (v, _l, ok, _ctx, vars) => (ok ? 'Idle and walk loop smoothly.' : `The ${listText(vars.clip) || listText(v) || 'walk'} animation does not loop, so it would stop after one cycle.`),
  controller_ready: (_v, _l, ok) => (ok ? 'An animation controller switches between idle, walk and run by speed.' : 'No animation controller was built, so you would have to set one up in Unity.'),
  silhouette_iou: (v, l, ok) => (ok ? `The model outline matches the drawing (${pct(v)}).` : `The model outline only matches the drawing ${pct(v)}; it needs ${pct(l)}.`),
  coverage: (v, l, ok) => (ok ? `The character fills ${pct(v)} of the picture.` : `The character fills only ${pct(v)} of the picture; it needs at least ${pct(l)}.`),
  height_ratio: (v, _l, ok) => (ok ? `This view is the same height as the front view (${num(v)?.toFixed(2) ?? v}x).` : `This view is ${num(v)?.toFixed(2) ?? v}x the height of the front view; it should be about the same.`),
  hist_dist: (v, _l, ok) => (ok ? 'The colours match the front view.' : `The colours differ from the front view (${num(v)?.toFixed(2) ?? v}).`),
  mirror_iou: (v, l, ok) => (ok ? `The left and right views mirror each other (${pct(v)}).` : `The left and right views do not mirror each other well (${pct(v)}, needs ${pct(l)}).`),
  // Mock-era names
  'single subject': (_v, _l, ok) => (ok ? 'The picture shows one subject.' : 'The picture shows more than one subject.'),
  'rig-ready pose': (_v, _l, ok) => (ok ? 'The pose is ready for a skeleton.' : 'The pose will not rig well.'),
  'plain background': (_v, _l, ok) => (ok ? 'The background is plain.' : 'The background is busy; the cutout may be rough.'),
}

function sizeSentence(v: unknown, l: unknown, ok: boolean, axis: string): string {
  return ok ? `The model is ${metres(v)} m ${axis}, as intended.` : `The model is ${metres(v)} m ${axis}; it should be ${metres(l)} m.`
}

function checkTextFor(id: string): CheckText | null {
  if (CHECK_TEXT[id]) return CHECK_TEXT[id]
  const lod = id.match(/^lod(\d+)_ratio$/)
  if (lod) return (v, l, ok) => (ok ? `Detail level ${lod[1]} keeps ${pct(v)} of the triangles.` : `Detail level ${lod[1]} keeps ${pct(v)} of the triangles; ${pct(l)} expected.`)
  if (/^texture_.+_pow2$/.test(id)) return (v, _l, ok) => (ok ? `The ${mapName(id)} is a power-of-two size.` : `The ${mapName(id)} is ${Array.isArray(v) ? v.join(' x ') : String(v)} px; it must be a power of two.`)
  if (/^texture_.+_square$/.test(id)) return (v, _l, ok) => (ok ? `The ${mapName(id)} is square.` : `The ${mapName(id)} is ${Array.isArray(v) ? v.join(' x ') : String(v)} px; it must be square.`)
  if (/^texture_.+_budget$/.test(id)) return (v, l, ok) => (ok ? `The ${mapName(id)} is ${n0(v)} px, within the ${n0(l)} px budget.` : `The ${mapName(id)} is ${n0(v)} px; the platform allows ${n0(l)} px.`)
  return null
}

const CHECK_HELP: Record<string, string> = {
  triangle_budget: 'triangles',
  manifold: 'manifold',
  watertight: 'watertight',
  degenerate_faces: 'degenerate_faces',
  height_m: 'size_m',
  size_m: 'size_m',
  facing: 'face_yaw',
  materials_connected: 'metal_shine',
  clips_loop: 'loop',
  controller_ready: 'animator_controller',
  uv_set: 'uv_set',
  bones: 'bones',
  single_root: 'single_root',
  weight_coverage: 'weight_coverage',
  max_influences: 'max_influences',
  weights_normalised: 'weights_normalised',
  clips: 'moves',
  legs_moved: 'moves',
  wings_found: 'motion',
  hitbox_cover: 'hitboxes',
  hitbox_overreach: 'hitbox_fit',
  hitbox_fit: 'hitboxes',
  movement_shape: 'movement_shape',
  rig_restore: 'rig',
  lod_count: 'lod_count',
  collider_present: 'collider_present',
  collider_vertices: 'collider_vertices',
  unity_import: 'unity_import',
  unity_humanoid_avatar: 'unity_humanoid_avatar',
  silhouette_iou: 'silhouette_iou',
  coverage: 'coverage',
  height_ratio: 'height_ratio',
  hist_dist: 'hist_dist',
  mirror_iou: 'mirror_iou',
}

// The sentence for a check. The backend is the one source of check wording (2.1): it writes `help` from the
// check's ok or fail sentence. Older backends wrote the fail sentence into `help` whether the check passed or not,
// so for a passing check the value-filled sentence here wins when there is one; a failing check takes the backend
// sentence first. Without either, the raw message.
export function checkSentence(check: Check, ctx: CheckContext = {}): string {
  const id = check.id ?? check.name
  const passed = check.passed ?? check.ok ?? false
  const fn = checkTextFor(id)
  const local = fn ? fn(check.value, check.limit, passed, ctx, check.vars ?? {}) : null
  if (passed && local) return local
  if (check.help) return check.help
  if (local) return local
  const label = id.replace(/_/g, ' ')
  if (check.message) return `${label}: ${check.message}`
  return passed ? `${label} passed.` : `${label} failed.`
}

export function checkPassed(check: Check): boolean {
  return check.passed ?? check.ok ?? false
}

export function checkSeverity(check: Check): 'block' | 'warn' {
  if (check.severity === 'warn' || check.severity === 'block') return check.severity
  const id = check.id ?? check.name
  if (/^(texture_.+_budget|lod\d+_ratio|height_ratio|hist_dist|coverage|mirror_iou|clips|legs_moved|wings_found|hitbox_.+|movement_shape)$/.test(id)) return 'warn'
  return 'block'
}

// A Fix button's action. Without `ui` it re-runs `stage` with `params`. With `ui` it opens a tool instead (2.5):
// the page goes to `stage`, opens "Change something" and focuses the tool or the Fine-tune `knob`, with `params`
// filled in but nothing run until the person applies it.
export type FixAction = { stage: StageId; params: Record<string, unknown>; label: string; ui?: string; knob?: string }

// The step and the Fine-tune setting each tool lives in.
export const FIX_TOOLS: Record<string, { stage: StageId; knob?: string; plain: string }> = {
  front_picker: { stage: 'prep', knob: 'face_yaw', plain: 'the front picker' },
  parts_editor: { stage: 'rig', plain: 'the parts editor' },
}

// Button text when the backend sends no fix_label (2.1 item 4).
const FIX_LABELS: Record<string, string> = {
  triangle_budget: 'Shrink to the budget',
  size_m: 'Set the size',
  height_m: 'Set the size',
  facing: 'Turn it to face forward',
  lod_count: 'Rebuild the detail levels',
  collider_present: 'Add a collision shape',
  weight_coverage: 'Attach the loose surface',
  weights_normalised: 'Balance the weights',
  manifold: 'Close the holes',
  watertight: 'Close the holes',
  materials_connected: 'Rebuild for Unity',
  clips_loop: 'Rebuild for Unity',
  controller_ready: 'Rebuild for Unity',
  unity_humanoid_avatar: 'Fit the standard skeleton again',
  clips: 'Make the moves again',
  legs_moved: 'Make the moves again',
  hitbox_cover: 'Fit them looser',
  hitbox_overreach: 'Fit them snugger',
  hitbox_fit: 'Try again',
  movement_shape: 'Add a body shape',
}
export const DEFAULT_FIX_LABEL = 'Fix automatically'

const quarterTurn = (v: number | null): number | null => {
  if (v === null) return null
  const d = ((Math.round(v) % 360) + 360) % 360
  return [0, 90, 180, 270].includes(d) ? d : null
}

// Where the app can fix a failing check (the backend's check.fix wins). Passing checks have no fix.
export function checkFix(check: Check): FixAction | null {
  if (checkPassed(check)) return null
  const id = check.id ?? check.name
  const label = check.fix?.label || check.fix_label || FIX_LABELS[id] || DEFAULT_FIX_LABEL
  const f = check.fix
  if (f && f.ui) {
    const tool = FIX_TOOLS[f.ui]
    const stage = f.stage && STAGES.includes(f.stage as StageId) ? (f.stage as StageId) : tool?.stage
    if (!stage) return null
    return { stage, params: f.params ?? {}, label, ui: f.ui, knob: f.knob ?? tool?.knob }
  }
  if (f && f.stage && STAGES.includes(f.stage as StageId)) return { stage: f.stage as StageId, params: f.params ?? {}, label }
  const limit = num(check.limit)
  if (id === 'triangle_budget' && limit) return { stage: 'mesh', params: { target_faces: limit }, label }
  if (id === 'manifold' || id === 'watertight') return { stage: 'mesh', params: { remesh: true }, label }
  if ((id === 'size_m' || id === 'height_m') && limit) return { stage: 'prep', params: { size_m: limit }, label }
  const turn = quarterTurn(num(check.value))
  if (id === 'facing' && turn !== null) return { stage: 'prep', params: { face_yaw: turn }, label }
  if (id === 'hitbox_cover') return { stage: 'rig', params: { hitbox_fit: 'loose' }, label }
  if (id === 'hitbox_overreach') return { stage: 'rig', params: { hitbox_fit: 'snug' }, label }
  if (id === 'hitbox_fit' || id === 'clips' || id === 'legs_moved') return { stage: 'rig', params: {}, label }
  if (id === 'movement_shape') return { stage: 'export', params: { movement_shape: 'auto' }, label }
  return null
}

// The Fix button's tooltip: what pressing it does, in words (never the raw params).
export function fixTooltip(fix: FixAction, stages?: StageInfo[] | null): string {
  const name = stagePlain(fix.stage, stages?.find((s) => s.id === fix.stage) ?? null)
  if (fix.ui) return `Opens ${FIX_TOOLS[fix.ui]?.plain ?? 'the tool'} in "${name}". Nothing changes until you apply it.`
  return `Makes a new version of "${name}" with this change.`
}

export function checkHelpKey(check: Check): string | null {
  const id = check.id ?? check.name
  if (CHECK_HELP[id]) return CHECK_HELP[id]
  if (/^texture_/.test(id)) return 'texture'
  if (/^lod\d+_ratio$/.test(id)) return 'lods'
  return null
}

// ---------------------------------------------------------------------------
// Generic lookup used by <Info term="..."/>
// ---------------------------------------------------------------------------

export function helpFor(term: string): HelpEntry | null {
  if (!term) return null
  const [kind, ...rest] = term.split('.')
  const name = rest.join('.')
  switch (kind) {
    case 'stage':
      return STAGES.includes(name as StageId) ? STAGE_TEXT[name as StageId].help : null
    case 'model':
      return modelHelp(name)
    case 'platform':
      return PLATFORM_HELP[name] ?? null
    case 'category':
      return CATEGORY_HELP[name] ?? null
    case 'style':
      return STYLE_HELP[name] ?? null
    case 'check': {
      const key = CHECK_HELP[name] ?? (/^texture_/.test(name) ? 'texture' : /^lod\d+_ratio$/.test(name) ? 'lods' : null)
      return key ? TERM_HELP[key] ?? null : null
    }
    case 'param':
    case 'metric':
    case 'term':
    case 'preset':
      return TERM_HELP[name] ?? null
    default:
      return TERM_HELP[term] ?? null
  }
}

// The plain label for a parameter, with the backend label as a fallback for unknown names.
export function paramPlain(name: string, fallback?: string): string {
  return TERM_HELP[name]?.plain ?? fallback ?? name.replace(/_/g, ' ')
}

export function metricPlain(name: string): string {
  return TERM_HELP[name]?.plain ?? name.replace(/_/g, ' ')
}

// ---------------------------------------------------------------------------
// Simple layer: which parameters show by default in Fine-tune
// ---------------------------------------------------------------------------

export const SIMPLE_PARAMS: Record<StageId, string[]> = {
  concept: ['model', 'width', 'height', 'readiness_block'],
  turnaround: ['views'],
  mesh: ['engine', 'target_faces'],
  prep: ['size_m', 'height_m', 'face_yaw', 'symmetric'],
  rig: ['template', 'motion', 'moves', 'hitboxes', 'hitbox_fit'],
  export: ['lods', 'collider', 'formats', 'sprites'],
}

// ---------------------------------------------------------------------------
// Plain labels for select options (2.2). The backend's option_labels win; this is the fallback for older
// backends and the mock.
// ---------------------------------------------------------------------------

export const OPTION_LABELS: Record<string, Record<string, string>> = {
  engine: { trellis: 'TRELLIS (most detail from one picture)', hy3d: 'Hunyuan (uses all four sides; good for long animals)' },
  input_view: { concept: 'The first picture', front: 'Front view', quarter: 'Three-quarter view' },
  texture_size: { '512': '512 px (smallest)', '1024': '1024 px', '2048': '2048 px', '4096': '4096 px (largest)' },
  upsample: { '1024': 'Normal', '1536': 'Fine (several times slower)' },
  pivot: { base: 'At the bottom (stands on the ground)', center: 'In the middle (floats or spins)' },
  template: { auto: 'Automatic (from the category)', mixamo: 'Standard human skeleton', generic: 'Fitted to the shape', none: 'No skeleton' },
  formats: { fbx: 'FBX (Unity, Unreal, Godot)', glb: 'GLB (web, Godot, Blender)' },
  clips_library: { ual1: 'Standard human moves (46 animations)', ual2: 'Extra human moves (second library)', none: 'Only its own walk and idle' },
  views: { front: 'Front', left: 'Left side', back: 'Back', right: 'Right side', quarter: 'Three-quarter', top: 'Top' },
  collider: { auto: 'Best fit (recommended)', hull: 'One wrapped shape', decompose: 'Several shapes, for hollow things', mesh: 'Exact shape (only for scenery that never moves)', none: 'None' },
  face_yaw: { auto: 'Automatic', '0': 'As built', '90': 'Turned a quarter to the left', '180': 'Turned around', '270': 'Turned a quarter to the right' },
  motion: { legs: 'Walks on its legs', wings: 'Flies, and walks when it lands', wave: 'Slithers or swims', none: 'Does not move by itself' },
  moves: { idle: 'Standing still', walk: 'Walk', run: 'Run', turn: 'Turn on the spot', attack: 'Attack', hit: 'Get hit', death: 'Die', flap: 'Fly', glide: 'Glide', takeoff: 'Take off', land: 'Land', slither: 'Slither or swim' },
  move_size: { small: 'Small', normal: 'Normal', big: 'Big' },
  move_speed: { slow: 'Slow', normal: 'Normal', fast: 'Fast' },
  hitboxes: { detailed: 'One per body part', simple: 'Just head and body', none: 'None' },
  hitbox_fit: { snug: 'Snug', normal: 'Normal', loose: 'Loose' },
  hitbox_regions: {
    head: 'Head',
    neck: 'Neck',
    chest: 'Chest',
    hips: 'Hips',
    body: 'Body',
    tail: 'Tail',
    upper_arm_l: 'Left upper arm',
    forearm_l: 'Left forearm',
    upper_arm_r: 'Right upper arm',
    forearm_r: 'Right forearm',
    thigh_l: 'Left thigh',
    shin_l: 'Left shin',
    thigh_r: 'Right thigh',
    shin_r: 'Right shin',
    leg_fl: 'Left front leg',
    leg_fr: 'Right front leg',
    leg_bl: 'Left back leg',
    leg_br: 'Right back leg',
    leg_l: 'Left leg',
    leg_r: 'Right leg',
    wing_l: 'Left wing',
    wing_r: 'Right wing',
    wings: 'Wings',
  },
  movement_shape: { auto: 'Automatic (by category)', upright: "Standing capsule (works with Unity's Character Controller)", along: 'Lying capsule along the body', box: 'Box', none: 'None' },
}

// ---------------------------------------------------------------------------
// Creature moves (3.7): plain names for clips, the moves each way of moving can make, and the moves line.
// ---------------------------------------------------------------------------

// A clip name in a sentence ("Moves: idle, walk, turn and get hit"). turn_left and turn_right are one move, "turn".
const CLIP_PLAIN: Record<string, string> = {
  idle: 'idle',
  walk: 'walk',
  run: 'run',
  turn: 'turn',
  turn_left: 'turn',
  turn_right: 'turn',
  attack: 'attack',
  hit: 'get hit',
  death: 'die',
  flap: 'fly',
  glide: 'glide',
  takeoff: 'take off',
  land: 'land',
  slither: 'slither',
}

// The moves a creature can make, by how it moves (the category yaml's clips, with turn as one move).
export const MOVES_BY_MOTION: Record<string, string[]> = {
  legs: ['idle', 'walk', 'run', 'turn', 'attack', 'hit', 'death'],
  wings: ['idle', 'walk', 'flap', 'glide', 'takeoff', 'land', 'attack', 'hit', 'death'],
  wave: ['idle', 'slither', 'turn', 'attack', 'hit', 'death'],
  none: [],
}
// How each category moves when the motion knob is left empty (the category yaml's motion).
export const MOTION_BY_CATEGORY: Record<string, string> = { humanoid: 'legs', quadruped: 'legs', multileg: 'legs', winged: 'wings', serpent: 'wave' }

// Clips that loop when the sidecar does not say (gait.py LOOPING); every other procedural clip plays once.
const LOOPING_CLIPS = /^(idle|walk|run|slither|flap|glide)$/i
const ONE_SHOT_CLIPS = /^(turn_left|turn_right|attack|hit|death|takeoff|land)$/i
export function clipLoopsByName(name: string): boolean {
  if (LOOPING_CLIPS.test(name)) return true
  return !ONE_SHOT_CLIPS.test(name)
}

// The plain move names for a clip list, in order, each once: ["idle", "walk", "turn", "get hit"].
export function movesPlain(clips: unknown): string[] {
  if (!Array.isArray(clips)) return []
  const out: string[] = []
  for (const c of clips) {
    const name = typeof c === 'string' ? c : (c as { name?: unknown })?.name
    if (typeof name !== 'string' || !name) continue
    const plain = CLIP_PLAIN[name.toLowerCase()] ?? name.replace(/_/g, ' ').toLowerCase()
    if (!out.includes(plain)) out.push(plain)
  }
  return out
}

// "Moves: idle, walk, run, turn, attack, get hit and die." Empty when there are no clips.
export function movesLine(clips: unknown): string {
  const list = movesPlain(clips)
  return list.length ? `Moves: ${joinAnd(list)}.` : ''
}

// A clip's name in the viewer's clip picker: "Walk", "Turn left", "Get hit", "Die".
export function clipLabel(name: string): string {
  const k = name.toLowerCase()
  const plain = k === 'turn_left' ? 'turn left' : k === 'turn_right' ? 'turn right' : (CLIP_PLAIN[k] ?? name.replace(/_/g, ' '))
  return plain.charAt(0).toUpperCase() + plain.slice(1)
}

export function optionLabel(p: { name: string; option_labels?: Record<string, string> }, value: unknown): string {
  const k = String(value)
  if (p.name === 'model') return modelLabel(k)
  return p.option_labels?.[k] ?? OPTION_LABELS[p.name]?.[k] ?? k
}

// Knobs that apply to some categories only (2.3): no list means every category. A knob with by_category (size_m)
// and no categories list applies to the categories it has an entry for.
export function knobApplies(p: { categories?: string[] | null; by_category?: Record<string, unknown> | null }, category: string): boolean {
  if (Array.isArray(p.categories) && p.categories.length > 0) return p.categories.includes(category)
  if (p.by_category && typeof p.by_category === 'object' && Object.keys(p.by_category).length > 0) return category in p.by_category
  return true
}

// ---------------------------------------------------------------------------
// Size (3.2): the axis each category is measured on, the question the size knob asks, and "About 1.8 m tall".
// The category yaml (GET /api/categories size_axis, size_label, size_hint) wins; this table is the fallback.
// ---------------------------------------------------------------------------

export type SizeAxis = 'height' | 'horizontal' | 'longest' | 'grid'
type SizeInfo = { axis: SizeAxis | null; size_m: number; label: string; hint: string; word?: 'tall' | 'long' | 'across' }

export const SIZE_INFO: Record<string, SizeInfo> = {
  humanoid: { axis: 'height', size_m: 1.8, label: 'How tall is it?', hint: 'In metres, from the ground to the top of the head. A person is about 1.8 m.' },
  quadruped: { axis: 'horizontal', size_m: 1.5, label: 'How long is it?', hint: 'In metres, from the nose to the tip of the tail. A wolf is about 1.8 m; a horse about 3 m.', word: 'long' },
  winged: { axis: 'horizontal', size_m: 1.5, label: 'How wide are the wings?', hint: 'In metres, wing tip to wing tip. A hawk is about 1.2 m; a horse-sized dragon about 8 m.', word: 'across' },
  multileg: { axis: 'horizontal', size_m: 1.0, label: 'How wide is it?', hint: 'In metres, across the legs. A large spider is about 0.2 m; a giant cave spider about 2 m.', word: 'across' },
  serpent: { axis: 'longest', size_m: 2.0, label: 'How long is it?', hint: 'In metres, head to tail as it lies. A python is about 4 m.', word: 'long' },
  vehicle: { axis: 'horizontal', size_m: 4.5, label: 'How long is it?', hint: 'In metres, front to back. A car is about 4.5 m; a tank about 7 m.', word: 'long' },
  prop_rigid: { axis: 'longest', size_m: 0.6, label: 'How big is it?', hint: 'In metres, along its longest side. A barrel is about 0.9 m tall.', word: 'across' },
  prop_articulated: { axis: 'longest', size_m: 0.8, label: 'How big is it?', hint: 'In metres, along its longest side. A chest is about 0.9 m wide; a door about 2.1 m tall.', word: 'across' },
  environment: { axis: 'longest', size_m: 3.0, label: 'How big is it?', hint: 'In metres, along its longest side. A wall section is about 3 m wide; a floor tile about 2 m. Pieces are not rounded to a grid yet.', word: 'across' },
  foliage: { axis: 'height', size_m: 2.5, label: 'How tall is it?', hint: 'In metres, from the ground to the top. A bush is about 1 m; an oak about 15 m.' },
  material: { axis: null, size_m: 1.0, label: '', hint: '' },
}

// The front picker's hint (3.2).
export const FACE_YAW_HINT = 'Pick the picture where the front faces you. The game treats that side as forward.'

let categoryCache: Record<string, Category> = {}
export function rememberCategories(list: Category[]): void {
  categoryCache = Object.fromEntries(list.map((c) => [c.id, c]))
}

function catOf(category: Category | string | null | undefined): Category | undefined {
  if (!category) return undefined
  return typeof category === 'string' ? categoryCache[category] : category
}
const idOf = (category: Category | string | null | undefined): string => (typeof category === 'string' ? category : (category?.id ?? ''))

// The prep size_m knob's per-category entry (backend by_category), when the schema has been loaded.
export type SizeByCategory = { axis?: string; default?: number; word?: string; label?: string; hint?: string }
let sizeKnob: Record<string, SizeByCategory> = {}
export function rememberSizeKnob(stages: StageInfo[]): void {
  const p = stages.find((s) => s.id === 'prep')?.adjust_schema.find((x) => x.name === 'size_m')
  sizeKnob = p?.by_category ?? {}
}

export function sizeAxisOf(category: Category | string | null | undefined): SizeAxis | null {
  const c = catOf(category)
  const a = sizeKnob[idOf(category)]?.axis ?? c?.size_axis ?? SIZE_INFO[idOf(category)]?.axis ?? null
  return a === 'height' || a === 'horizontal' || a === 'longest' || a === 'grid' ? a : null
}

export function sizeQuestion(category: Category | string | null | undefined): string {
  return sizeKnob[idOf(category)]?.label || catOf(category)?.size_label || SIZE_INFO[idOf(category)]?.label || 'How big is it?'
}

export function sizeHint(category: Category | string | null | undefined): string {
  return sizeKnob[idOf(category)]?.hint || catOf(category)?.size_hint || SIZE_INFO[idOf(category)]?.hint || 'In metres.'
}

// "tall", "long" or "across": the backend's word for the category (by_category, then size_word), else the table.
// Longest without a word of its own is "tall" when the longest side is the height, else "long".
export function axisWord(axis: SizeAxis | null, category?: string, longestIsUp?: boolean): string {
  const own = category ? sizeKnob[category]?.word || catOf(category)?.size_word || SIZE_INFO[category]?.word : undefined
  if (axis === 'height') return 'tall'
  if (own) return own
  if (axis === 'longest') return longestIsUp ? 'tall' : 'long'
  return axis === 'horizontal' ? 'long' : 'tall'
}

// Prep's metrics.dims are Blender [x, y, z] in metres with Z up ({x, y, z} is read the same way).
function dimsOf(metrics: Record<string, unknown>): [number, number, number] | null {
  const d = metrics.dims ?? metrics.dimensions
  if (Array.isArray(d) && d.length >= 3 && d.every((x) => typeof x === 'number')) return [d[0], d[1], d[2]] as [number, number, number]
  if (d && typeof d === 'object') {
    const o = d as Record<string, unknown>
    const x = num(o.x)
    const y = num(o.y)
    const z = num(o.z)
    if (x !== null && y !== null && z !== null) return [x, y, z]
  }
  return null
}

// What a prep version measures on its category's axis: the size check first (it carries the axis word), then
// metrics.size_m, then the dims (height is Z, horizontal the larger of X and Y). Null when nothing says.
export function measuredSize(metrics: Record<string, unknown> | null | undefined, category: string): { value: number; word: string } | null {
  if (!metrics) return null
  const checks = Array.isArray(metrics.checks) ? (metrics.checks as Check[]) : []
  const axis = sizeAxisOf(category)
  const dims = dimsOf(metrics)
  const longestUp = dims ? dims[2] >= Math.max(dims[0], dims[1]) : undefined
  const sc = checks.find((c) => (c.id ?? c.name) === 'size_m') ?? checks.find((c) => (c.id ?? c.name) === 'height_m')
  const scv = sc ? num(sc.value) : null
  if (sc && scv !== null) {
    const isHeight = (sc.id ?? sc.name) === 'height_m'
    const word = str(sc.vars?.axis_word) || (isHeight ? 'tall' : axisWord(axis, category, longestUp))
    return { value: scv, word }
  }
  const sm = num(metrics.size_m)
  if (sm !== null) return { value: sm, word: axisWord(axis, category, longestUp) }
  if (dims) {
    const [x, y, z] = dims
    const v = axis === 'horizontal' ? Math.max(x, y) : axis === 'longest' ? Math.max(x, y, z) : z
    return { value: v, word: axisWord(axis ?? 'height', category, longestUp) }
  }
  return null
}

// The category's default size: by_category, then the category, then the table.
export function sizeDefault(category: string): number | null {
  return sizeKnob[category]?.default ?? catOf(category)?.size_m ?? SIZE_INFO[category]?.size_m ?? null
}

// "About 1.8 m tall", "About 4.2 m long", "About 0.45 m long".
export function aboutSize(value: number, word: string): string {
  const v = value >= 10 ? String(Math.round(value)) : value >= 1 ? String(Math.round(value * 10) / 10) : String(Math.round(value * 100) / 100)
  return `About ${v} m ${word}`
}

// Light G13: a size written in the description ("two metres wide", "6 ft tall", "4.5 m long"). A port of the
// backend's reference.parse_size, same rules: digits or the words one to twenty; units m, metre(s), meter(s), cm,
// ft, foot, feet; axis words tall or high (height), long, wide or across (horizontal). Sizes outside 0.01 to 100 m
// are ignored; a size with an axis word wins over one without; with defaultM (the category size) a size without an
// axis word is ignored unless it is within a factor of four of it ("2 cm iron bands" does not size the chest).
const NUMBER_WORDS = ['one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty']
const UNITS: Record<string, number> = { m: 1, metre: 1, metres: 1, meter: 1, meters: 1, cm: 0.01, ft: 0.3048, foot: 0.3048, feet: 0.3048 }
const AXIS_WORDS: Record<string, ['height' | 'horizontal', string]> = { tall: ['height', 'tall'], high: ['height', 'tall'], long: ['horizontal', 'long'], wide: ['horizontal', 'across'], across: ['horizontal', 'across'] }
// Longer number words first so "seventeen" is not read as "seven".
const NUM_ALT = [...NUMBER_WORDS].sort((a, b) => b.length - a.length).join('|')
const SIZE_RE = new RegExp(`(?<![\\w.])(\\d+(?:\\.\\d+)?|${NUM_ALT})\\s*-?\\s*(metres|metre|meters|meter|feet|foot|cm|ft|m)\\b(?:\\s*-?\\s*(tall|high|long|wide|across)\\b)?`, 'gi')

export function sizeFromDescription(text: string, defaultM?: number | null): { value: number; axis: 'height' | 'horizontal' | null; word: string | null } | null {
  const found: { value: number; axis: 'height' | 'horizontal' | null; word: string | null }[] = []
  for (const m of (text ?? '').matchAll(SIZE_RE)) {
    const raw = m[1].toLowerCase()
    const n = NUMBER_WORDS.includes(raw) ? NUMBER_WORDS.indexOf(raw) + 1 : Number(raw)
    const metresValue = Math.round(n * UNITS[m[2].toLowerCase()] * 1000) / 1000
    if (!(metresValue >= 0.01 && metresValue <= 100)) continue
    const aw = m[3] ? AXIS_WORDS[m[3].toLowerCase()] : undefined
    if (!aw && defaultM && !(metresValue >= defaultM / 4 && metresValue <= defaultM * 4)) continue
    found.push({ value: metresValue, axis: aw?.[0] ?? null, word: aw?.[1] ?? null })
  }
  return found.find((f) => f.axis) ?? found[0] ?? null
}

export function isSimpleParam(stage: StageId, p: { name: string; simple?: boolean }, schemaHasFlag: boolean): boolean {
  if (schemaHasFlag) return p.simple === true
  return SIMPLE_PARAMS[stage].includes(p.name)
}

// ---------------------------------------------------------------------------
// Suggestions (fallback for GET /api/suggest)
// ---------------------------------------------------------------------------

export type SuggestionLike = {
  model: string
  model_label: string
  reason: string
  budgets: { triangles: number; texture: number }
  estimates: Record<string, number>
}

export function suggestFallback(
  category: string,
  style: string,
  platform: string,
  ref: { categories: Category[]; styles: StylePreset[]; platforms: Platform[]; stages: StageInfo[] } | null,
): SuggestionLike {
  const preset = ref?.styles.find((s) => s.id === style)
  const model = preset?.model ?? 'zturbo'
  const cat = ref?.categories.find((c) => c.id === category)
  const plat = ref?.platforms.find((p) => p.id === platform)
  const isProp = !!category && !/^(humanoid|quadruped|winged|multileg|serpent)$/.test(category)
  const budgets = cat?.budgets?.[platform] ?? {
    triangles: plat ? (isProp ? plat.triangles_prop : plat.triangles_character) : 20000,
    texture: plat?.texture ?? 2048,
  }
  const estimates: Record<string, number> = {}
  for (const s of STAGES) estimates[s] = stageEstimate(s, ref?.stages.find((x) => x.id === s))
  if (model === 'zbase') estimates.concept = Math.max(estimates.concept, 90)
  if (platform === 'high') estimates.mesh = Math.round(estimates.mesh * 1.5)
  const styleName = preset?.label?.toLowerCase() ?? style
  const platName = platformPlain(platform).split(':')[0].toLowerCase()
  const reason = `${modelReason(model) || 'it fits this style'}, which suits a ${styleName} look for ${platName}`
  return { model, model_label: modelLabel(model), reason, budgets, estimates }
}

export function totalEstimate(estimates: Record<string, number> | null | undefined, ref?: { stages: StageInfo[] } | null): number {
  let total = 0
  for (const s of STAGES) total += stageEstimate(s, ref?.stages.find((x) => x.id === s), estimates)
  return total
}

// ---------------------------------------------------------------------------
// Example prompts
// ---------------------------------------------------------------------------

export const PROMPT_TEMPLATE = 'A [kind of thing], [two or three visible details], [material or colour], [mood or wear]'

export const EXAMPLE_PROMPTS: Record<string, [string, string]> = {
  humanoid: ['A sturdy young knight in worn plate armour, short dark hair, no helmet', 'A lanky desert trader in layered linen robes, brass goggles pushed up on the forehead, sandals'],
  quadruped: ['A large grey dire wolf with a scarred muzzle and matted fur, standing alert', 'A stocky farm ox with short curved horns and a leather yoke, mud on its legs'],
  winged: ['A small copper-feathered hawk with a cream chest and a hooked beak, wings spread', 'A squat green dragon whelp with bat-like wings and stubby horns, standing'],
  multileg: ['A black cave spider with a glossy abdomen and pale leg joints', 'A rusted six-legged mining crawler with a drill on the front, hazard stripes'],
  serpent: ['A thick jungle python with green and gold diamond markings, coiled loosely', 'A pale deep-sea eel with a long dorsal fin and small glowing spots'],
  vehicle: ['A boxy scrapyard buggy with mismatched wheels, a roll cage and a mounted turret', 'A rounded 1950s hover-van in cream and teal, chrome trim, no wheels'],
  prop_rigid: ['A weathered oak barrel with rusted iron bands and a cracked stave', 'A chunky stone lantern with moss in the cracks and a wooden shutter'],
  prop_articulated: ['A wooden treasure chest with iron bands and a brass lock, closed lid', 'A heavy iron-bound door with a grated window, closed'],
  environment: ['A mossy stone wall segment, two metres wide, with a crumbling top edge', 'A rusted metal catwalk piece with railings and a grated floor'],
  foliage: ['A gnarled dead oak with sparse leaves and exposed roots', 'A bushy fern cluster with broad fronds, slightly yellowed tips'],
  material: ['Worn cobblestones with moss in the gaps and a wet sheen', 'Weathered wooden planks with visible grain, nail heads and peeling paint'],
}

export function examplePrompts(category: string): [string, string] {
  return EXAMPLE_PROMPTS[category] ?? EXAMPLE_PROMPTS.prop_rigid
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export function humanError(raw: string | null | undefined): string {
  const r = (raw ?? '').toLowerCase()
  if (!r) return 'Something went wrong in this step. Try again.'
  if (r.includes('cancel')) return 'This run was cancelled.'
  if (r.includes('comfyerror') || r.includes('comfy') || r.includes('interrupted') || r.includes('out of vram') || r.includes('no images')) return 'The generator stopped; this usually fixes itself. Try again.'
  if (r.includes('blender') || r.includes('unity') || r.includes('bpy') || r.includes('remesh') || r.includes('decimat')) return 'The clean-up step failed on this model. Try again or change something.'
  if (r.includes('timeout') || r.includes('timed out')) return 'This step took too long and was stopped. Try again.'
  if (r.includes('select a') && r.includes('version first')) return 'Keep a version in the previous step first.'
  return 'Something went wrong in this step. Try again.'
}

// Candidate-card summary: the first failing check sentence, or null when all pass.
export function firstFailing(metrics: Record<string, unknown>, ctx: CheckContext = {}): string | null {
  const checks = metrics.checks
  if (Array.isArray(checks)) {
    const bad = (checks as Check[]).find((c) => c && typeof c === 'object' && !checkPassed(c))
    if (bad) return checkSentence(bad, ctx)
  }
  if (metrics.consistency_ok === false) {
    const flagged = Array.isArray(metrics.flagged_views) ? (metrics.flagged_views as string[]).join(', ') : ''
    return flagged ? `The ${flagged} view${flagged.includes(',') ? 's do' : ' does'} not match the front view.` : 'Some views do not match the front view.'
  }
  if (metrics.views && typeof metrics.views === 'object') {
    for (const [name, v] of Object.entries(metrics.views as Record<string, { passed?: boolean }>)) if (v && v.passed === false) return `The ${name} view does not match the front view.`
  }
  return null
}

export function looksGood(metrics: Record<string, unknown>): boolean | null {
  if (typeof metrics.gate_ok === 'boolean') return metrics.gate_ok
  if (typeof metrics.consistency_ok === 'boolean') return metrics.consistency_ok
  const checks = metrics.checks
  if (Array.isArray(checks) && checks.length) return (checks as Check[]).every((c) => checkPassed(c))
  if (metrics.views && typeof metrics.views === 'object') {
    const vs = Object.values(metrics.views as Record<string, { passed?: boolean }>)
    if (vs.length) return vs.every((v) => v?.passed !== false)
  }
  return null
}
