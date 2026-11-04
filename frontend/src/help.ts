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
  winged: H('Winged creature', 'A creature with wings, such as a bird or dragon.', 'Gets a skeleton with wing bones.', 'winged'),
  multileg: H('Multi-leg creature', 'A creature with more than four legs, such as a spider.', 'Gets a many-legged skeleton predicted from the shape.', 'multileg'),
  serpent: H('Serpent or fish', 'A long creature without legs.', 'Gets a spine-chain skeleton that bends along its length.', 'serpent'),
  vehicle: H('Vehicle or mech', 'A car, tank, ship or walking machine.', 'No skeleton; wheels, turret and doors are separate parts you can move.', 'vehicle'),
  prop_rigid: H('Rigid prop', 'A solid object that does not bend or open, such as a barrel.', 'No skeleton; just a model with a collision shape.', 'prop_rigid'),
  prop_articulated: H('Articulated prop', 'An object with a moving part, such as a chest with a lid.', 'The moving part becomes a separate piece on a hinge.', 'prop_articulated'),
  environment: H('Environment piece', 'A piece of scenery that snaps to a grid, such as a wall or floor tile.', 'No skeleton; built to line up with other pieces.', 'environment'),
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
  parts: H('Parts', 'Pieces that must move or swap on their own, such as a weapon or a lid.', 'Each part becomes its own piece in the game.', 'parts'),
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
  height_m: H('Height', 'How tall the asset is in the game, in metres.', 'Keeps characters and props in proportion to each other.', 'height_m'),
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
  // Export
  lod: H('Detail levels', 'Lighter copies of the model used when it is far away.', 'Keeps the game fast with many assets on screen.', 'LOD (level of detail)'),
  lods: H('Detail levels', 'Lighter copies of the model used when it is far away, as fractions of the full model.', 'Keeps the game fast with many assets on screen.', 'lods (LOD ratios)'),
  lod_count: H('Number of detail levels', 'How many lighter copies were made.', 'Four is standard.', 'lod_count'),
  collider: H('Collision shape', 'A simple invisible shape the game uses for bumping into things.', 'Without it the asset is walk-through.', 'collider (convex hull)'),
  collider_vertices: H('Collision shape points', 'How many points the collision shape has.', 'Engines cap this at 255.', 'collider_vertices'),
  collider_verts: H('Collision shape points', 'How many points the collision shape has.', 'Engines cap this at 255.', 'collider_verts'),
  collider_present: H('Collision shape present', 'Whether a collision shape was made.', 'Needed for physics.', 'collider_present'),
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

type CheckText = (value: unknown, limit: unknown, passed: boolean, ctx: CheckContext) => string
export type CheckContext = { platform?: string }

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
  height_m: (v, l, ok) => (ok ? `The model is ${v} m tall, as intended.` : `The model is ${v} m tall; it should be ${l} m.`),
  uv_set: (v, _l, ok) => (ok ? 'The model has one texture layout.' : `The model has ${n0(v)} texture layouts; it needs exactly one.`),
  bones: (v, _l, ok) => (ok ? `The skeleton has ${n0(v)} bones.` : 'No skeleton was found.'),
  single_root: (v, _l, ok) => (ok ? 'The skeleton starts from one root bone.' : `The skeleton has ${n0(v)} root bones; it needs one.`),
  weight_coverage: (v, l, ok) => (ok ? `${pct(v)} of the surface follows the bones.` : `Only ${pct(v)} of the surface follows the bones; it needs ${pct(l)}.`),
  max_influences: (v, l, ok) => (ok ? `Each surface point follows at most ${n0(v)} bones.` : `Some surface points follow ${n0(v)} bones; the limit is ${n0(l)}.`),
  weights_normalised: (v, _l, ok) => (ok ? 'All skin weights add up correctly.' : `${n0(v)} surface points have weights that do not add up.`),
  clips: (v, l, ok) => (ok ? `Animations attached: ${Array.isArray(v) ? v.join(', ') : String(v)}.` : `Some animations are missing (expected ${Array.isArray(l) ? l.join(', ') : String(l)}).`),
  lod_count: (v, l, ok) => (ok ? `${n0(v)} detail levels were made.` : `${n0(v)} detail levels were made; ${n0(l)} expected.`),
  collider_present: (_v, _l, ok) => (ok ? 'A collision shape is present.' : 'No collision shape was made.'),
  collider_vertices: (v, l, ok) => (ok ? `The collision shape has ${n0(v)} points (limit ${n0(l)}).` : `The collision shape has ${n0(v)} points; engines allow ${n0(l)}.`),
  unity_import: (_v, _l, ok) => (ok ? 'Unity imported the asset without errors.' : 'Unity could not import the asset.'),
  unity_humanoid_avatar: (_v, _l, ok) => (ok ? 'Unity recognised the humanoid skeleton.' : 'Unity did not recognise the skeleton as humanoid.'),
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
  height_m: 'height_m',
  uv_set: 'uv_set',
  bones: 'bones',
  single_root: 'single_root',
  weight_coverage: 'weight_coverage',
  max_influences: 'max_influences',
  weights_normalised: 'weights_normalised',
  clips: 'clips',
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

// The sentence for a check: backend help first, then the registry, then the raw message.
export function checkSentence(check: Check, ctx: CheckContext = {}): string {
  if (check.help) return check.help
  const id = check.id ?? check.name
  const passed = check.passed ?? check.ok ?? false
  const fn = checkTextFor(id)
  if (fn) return fn(check.value, check.limit, passed, ctx)
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
  if (/^(texture_.+_budget|lod\d+_ratio|height_ratio|hist_dist|coverage|mirror_iou)$/.test(id)) return 'warn'
  return 'block'
}

// Where the app can fix a failing check on its own (the backend's check.fix wins).
export function checkFix(check: Check): { stage: StageId; params: Record<string, unknown> } | null {
  if (check.fix && check.fix.stage && STAGES.includes(check.fix.stage as StageId)) return { stage: check.fix.stage as StageId, params: check.fix.params ?? {} }
  if (checkPassed(check)) return null
  const id = check.id ?? check.name
  const limit = num(check.limit)
  if (id === 'triangle_budget' && limit) return { stage: 'mesh', params: { target_faces: limit } }
  if (id === 'manifold' || id === 'watertight') return { stage: 'mesh', params: { remesh: true } }
  return null
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
  prep: ['height_m', 'symmetric'],
  rig: ['template'],
  export: ['lods', 'collider', 'formats', 'sprites'],
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
