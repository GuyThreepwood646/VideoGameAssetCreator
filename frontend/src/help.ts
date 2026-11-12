// Help registry: the plain-language layer. Every stage, parameter, metric, check,
// model, platform, category and style has an entry {plain, meaning, why, technical}.
// The backend may send its own help (stage.help, param.help, check.help); the
// functions below prefer that and fall back to this registry.
import type { Category, Check, ModelInfo, Platform, SetupItem, StageId, StageInfo, StylePreset } from './api'
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
    placeholder: 'e.g. hands apart from the backpack arms, thicker sword',
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
  environment: H('Environment piece', 'A piece of scenery that snaps to a grid, such as a wall or floor tile.', 'No skeleton; sized to the grid with its anchor on a corner, so pieces line up.', 'environment'),
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
  realistic_pbr: H('Photoreal', 'Looks like a photograph of a real thing: true materials, fine detail, real proportions.', 'Slower to make; pairs with the Z-Image base model.', 'realistic_pbr'),
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
  prompt: H('Prompt (description)', 'What you want, written in plain words.', 'The better the description, the closer the first picture.', 'prompt'),
  category: H('Category', 'What kind of thing this is: a person, an animal, a prop.', 'It decides the pose, the skeleton and the size budget.', 'category'),
  style_preset: H('Style', 'The look: painted, cel shaded, pixel art and so on.', 'Picks the model and the wording added to every description.', 'style_preset'),
  platform: H('Platform', 'Where the game will run: phone, PC or showcase.', 'Sets how many triangles and how big a texture the model may have.', 'platform_target'),
  parts: H('Parts', 'Pieces that must move or swap on their own, such as a weapon or a lid.', 'We ask the image model to draw these as clearly separate pieces; for now they stay joined to the rest of the 3D model.', 'parts'),
  style_lock: H('Style lock', 'The look every asset in this project shares.', 'Keeps a whole game looking like one game.', 'style_lock'),
  prompt_block: H('Style prompt (style words)', 'Words added in front of every description in this project.', 'This is how the style stays the same from asset to asset.', 'prompt_block'),
  palette: H('Palette', 'The handful of colours the project uses.', 'Shared colours make assets look like they belong together.', 'palette'),
  negative: H('Negative prompt (things to avoid)', 'Things the model should leave out, such as text or blur.', 'Removes common mistakes before they happen.', 'negative (style_lock.negative)'),
  loras: H('LoRAs (style add-ons)', 'Small trained files that push the model toward a particular look.', 'Optional; most projects need none.', 'LoRA (low-rank adaptation), style_lock.loras'),
  lora: H('LoRA (style add-on)', 'A small trained file that pushes the model toward a particular look.', 'Optional; most projects need none.', 'LoRA (low-rank adaptation)'),
  model: H('Model (the painter)', 'The AI image model that paints the picture.', 'Different models are faster, slower or suit different styles.', 'checkpoint (data/models.yaml)'),
  concept_model: H('Model (the painter)', 'The AI image model that paints the first picture of this asset.', 'We suggest one that suits the look and platform; pick another if you prefer its style. You can change it later under Fine-tune.', 'concept_model'),
  stage: H('Step', 'One of the six steps every asset goes through: describe, check all sides, build the 3D model, clean up, make it move, hand off.', 'Each step builds on the version you kept in the step before it.', 'stage'),
  // Image generation
  seed: H('Seed', 'A number that picks which of many possible pictures you get.', 'Same seed, same picture; change it to get a different one.', 'seed'),
  steps: H('Steps', 'How many passes the model takes.', 'More passes can add detail but take longer; the default is usually right.', 'steps'),
  cfg: H('CFG (how strictly the model follows the description)', 'A number for how closely the model sticks to your description.', 'Too high looks forced, too low wanders off.', 'classifier-free guidance scale'),
  width: H('Width', 'Picture width in pixels.', 'Bigger pictures hold more detail and take longer.', 'width'),
  height: H('Height', 'Picture height in pixels.', 'Bigger pictures hold more detail and take longer.', 'height'),
  size: H('Size', 'Picture size in pixels.', '1024 is the sweet spot; bigger takes longer.', 'width and height'),
  readiness_block: H('Pose prompt (pose for 3D)', 'Adds the standard pose and plain background to the description.', 'Keep this on: the 3D step needs a clear, whole figure on a plain background.', 'readiness_block'),
  prompt_override: H('Prompt override (replace description)', 'Use this text instead of the asset description for one attempt.', 'Handy for a one-off experiment without changing the asset.', 'prompt_override'),
  cutout: H('Cutout', 'The picture with the background removed.', 'The 3D step works from the cutout, so a clean edge matters.', 'cutout (alpha matte)'),
  finetune: H('Fine-tune', 'Re-run this step with different settings.', 'Use it when the result is close but a setting is off; the description stays the same.', 'adjust'),
  instruction: H('Change request', 'What you asked to change in this attempt.', 'Recorded so you can see how this version came about.', 'instruction'),
  // Turnaround
  views: H('Views', 'Which sides to draw: front, left, back, right, a three-quarter view and top.', 'The 3D step uses them to get the back and sides right, and the clean-up step paints the texture from them. For a character the three-quarter view from the other side is drawn too, so both cheeks and both flanks have a picture behind them.', 'views'),
  view: H('View', 'One side of the character.', 'Regenerate a single view when only that one looks wrong.', 'view'),
  turnaround: H('Turnaround (all sides)', 'The same character drawn from several sides.', 'Without it the 3D model has to guess what the back looks like.', 'turnaround'),
  turn_method: H('How to turn the views', 'How the side, back and three-quarter views are drawn from your picture.', 'Standard works for everything and keeps your look. For a character, all sides in one picture makes the clothes and details match best but looks more photographic. For a rigid prop, shadow-free gives clean white views for the 3D model, with colours a little further from the picture.', 'turn_method: standard (multiple-angles LoRA), sheet (turnaround sheet LoRA), shadow_free (camera-angle LoRA)'),
  lora_strength: H('Multiple-angles LoRA strength (side-view strength)', 'How hard the multiple-angles LoRA pushes the side-view model (Qwen-Image-Edit) to keep the character consistent across views. Used by the standard way of turning the views only.', 'Raise it if the sides drift from the front; lower it if they look stiff.', 'lora_strength (multi-angle LoRA)'),
  auto_reroll: H('Auto re-roll (retry bad views)', 'Automatically redraw a view that fails the consistency check.', 'Saves you a click on most assets.', 'auto_reroll'),
  consistency_ok: H('Sides match', 'Whether every view agrees with the front view in size and colour.', 'Mismatched views make a lopsided model.', 'consistency_ok'),
  views_passed: H('Views that match', 'How many views passed the consistency check.', 'All views should pass before building the model.', 'views_passed'),
  coverage: H('Coverage (fills the frame)', 'How much of the picture the character covers.', 'Too small and the model loses detail.', 'coverage'),
  height_ratio: H('Height match', 'Height of this view compared with the front view.', 'Views of different heights make a distorted model.', 'height_ratio'),
  hist_dist: H('Colour match', 'How different the colours are from the front view.', 'Colour drift between views causes patchy textures.', 'hist_dist (histogram distance)'),
  mirror_iou: H('Left-right match', 'How well the left and right views mirror each other.', 'Catches a side view that drew a different character.', 'mirror_iou'),
  // Mesh
  engine: H('Image-to-3D model (builder)', 'The AI model that turns pictures into a 3D shape.', 'TRELLIS is the default; Hunyuan is the alternative when a shape comes out wrong.', 'engine (trellis | hy3d)'),
  input_view: H('Input view (build from)', 'Which picture the builder starts from.', 'The three-quarter view usually gives the best shape; the concept keeps the most detail.', 'input_view'),
  target_faces: H('Triangle count (detail level)', 'How many triangles the model is allowed.', 'More triangles mean more detail and a heavier model.', 'target_faces'),
  triangles: H('Triangles', 'The small flat pieces a 3D model is made of.', 'More triangles mean more detail but a slower game; each platform has a budget.', 'triangles (faces)'),
  budget: H('Budget', 'The most triangles or the biggest texture the platform allows.', 'Staying inside the budget keeps the game fast.', 'budget'),
  vertices: H('Vertices (points)', 'The corner points of the triangles.', 'Roughly half the triangle count; another measure of weight.', 'vertices'),
  texture_size: H('Texture size', 'The size of the painted surface image in pixels.', 'Bigger textures look sharper up close and use more memory.', 'texture_size'),
  texture: H('Texture', 'The painted image wrapped around the model.', 'It is what gives the model its colours and surface detail.', 'texture'),
  normal_map: H('Normal map', 'A special texture that fakes small bumps and dents, such as rivets or wood grain, without adding triangles.', 'The model looks detailed up close while staying light enough for the game.', 'normal map'),
  triangle_budget: H('Triangle budget', 'The most triangles a model may have on the chosen platform.', 'Staying inside it keeps the game running smoothly; the clean-up step shrinks the model to fit.', 'triangle budget (triangles per asset)'),
  head_build: H('Build the head on its own', 'With hair from the library, the head is built a second time from a close crop of the bald picture and joined to the body at the neck.', 'Built with the whole body, the face gets only a few pixels of the builder\'s attention and comes out smudged; built on its own it gets real brows, eyes, nose and lips. It adds about 5 minutes. If the head does not fit, the body\'s own head is kept.', 'Second TRELLIS.2 build from the top 26 percent of the bald figure; joined by colour at the neck; both textures baked into one atlas (head at most 30 percent of it)'),
  head_crop: H('Head crop (share of the height)', 'How much of the figure, from the top, the head build sees.', 'Smaller crops give the face more detail; a crop that is too tight can come out flat, so it is built again once and otherwise the body\'s own head is kept.', 'Share of the figure\'s height, 0.18 to 0.4 (default 0.26)'),
  tex_steps: H('Colour detail', 'How carefully the colours are painted: more steps give a richer, more detailed texture.', '24 (the default) gives clearly more detail than 12 for a few seconds more; the overall tone is then shifted back towards your picture. Use 12 if a model comes out too busy.', 'TRELLIS.2 texture sampler steps'),
  upsample: H('Finer colour and shape (slower)', 'Builds the model on a finer grid, so colour detail such as faces, eyes and markings, and small shapes, can come out sharper.', 'It redoes the whole colour pass, so colours can also shift and results vary: try it as a second version and keep the better one. About 2 to 3 times slower, and very slow for large, bulky objects.', 'TRELLIS.2 upsample resolution'),
  remesh: H('Remesh (close holes)', 'Rebuild the surface so it is one closed shell.', 'Closed shells clean up and rig reliably.', 'remesh (watertight)'),
  silhouette_iou: H('Silhouette IoU (outline match)', 'How closely the model outline matches the drawing from each side.', 'Low match means the model does not look like the picture.', 'silhouette IoU (intersection over union)'),
  iou: H('IoU (outline match)', 'How closely two outlines overlap, from 0 to 1.', 'Low match means the shapes disagree.', 'IoU (intersection over union)'),
  silhouette: H('Silhouette (outline)', 'The shape of the model seen as a flat shadow.', 'A good outline is what makes a character readable in a game.', 'silhouette'),
  // Prep
  height_m: H('Size', 'How big the asset is in the game, in metres, measured the way that makes sense for it: height for people, length for cars and snakes.', 'Keeps everything in proportion, so a car is car-sized next to your character.', 'height_m (older name of size_m)'),
  size_m: H('Size', 'How big the asset is in the game, in metres, measured the way that makes sense for it: height for people, length for cars and snakes.', 'Keeps everything in proportion, so a car is car-sized next to your character.', 'Uniform scale so the extent on size_axis equals size_m'),
  face_yaw: H('Forward direction (front)', 'Which side of the model the game treats as its front.', 'Characters walk and cars drive toward their front; if it is wrong, they move sideways.', 'Yaw about Y so the concept front faces +Z'),
  symmetric: H('Symmetrize (mirror the halves)', 'Mirror one half of the model onto the other.', 'Fixes mismatched arms or legs, but the two halves then share one painted side, so it is off unless you turn it on.', 'Symmetrize across X, then re-bake the maps onto the mirrored mesh'),
  // Textures and light (3.9)
  padding: H('UV padding (seam space)', 'Extra painted space around each piece of the texture.', 'Stops thin dark lines appearing at seams when the model is far away.', 'UV island padding and edge dilation, in texels'),
  bake: H('Texture bake (surface painting)', 'Copying the fine detail and colour from the raw model onto the clean game model.', 'If it is painted onto a different shape, the shading looks wavy or faceted.', 'Texture bake (normal, AO, colour) from source to target mesh'),
  rebake: H('Rebake (paint the surface again)', 'Paint the colours and surface detail onto the cleaned-up model again, even when its shape did not change.', 'If it is painted onto a different shape, the shading looks wavy or faceted.', 'Cycles selected-to-active bake from the mesh stage model'),
  delight: H('Delight (remove painted-on shadows)', "Shadows and highlights from the original picture that ended up in the model's colours.", "They stay put when your game's sun moves, and crevices look twice as dark. Stylised looks keep them on purpose.", 'Baked lighting in albedo; AO and up-normal guided delight pass'),
  baked_light: H('Baked lighting (painted-on shadows)', "Shadows and highlights from the original picture that ended up in the model's colours.", "They stay put when your game's sun moves, and crevices look twice as dark. Stylised looks keep them on purpose.", 'Baked lighting in albedo; AO and up-normal guided delight pass'),
  face_detail: H('Match the face to the picture', 'Paint the face from the picture onto the model, so the eyes, brows and mouth are sharp and look like the picture.', 'The 3D builder makes up the face from one picture and blurs it; this puts the painted face back.', 'Front render, painted head sharpened by Qwen-Image-Edit, fitted to the render, projected through the UVs'),
  source_project: H('Paint the picture onto the model', 'Puts the picture\'s own colours and detail onto the side of the model the picture shows, so that side looks like the picture.', 'The 3D builder makes up its colours from a description of the picture and they come out soft. Your own picture is used first, then the views drawn from it for the other sides. When left empty this runs by itself for characters; turn it on to try it on other models, or off to keep the builder\'s own colours.', 'Picture fitted to a render by outline and optical flow, projected through the UVs with depth, edge and stretch limits'),
  face_strength: H('Face strength', 'How much of the painted face goes onto the model.', 'Lower it if the painted face looks pasted on; 1 uses all of it.', 'Projection weight multiplier, 0 to 1'),
  face_closeup_guided: H('Guide the close-up by your face picture (optional)', 'With a picture of the real face (your own close-up in this step, or the Face picture in step 0.5), the close-up is drawn with that face\'s features.', 'On by default. The close-up keeps the painting\'s pose, expression and light and takes the eyes, nose, mouth, skin and hair from your picture, so the model looks more like the person. Without a face picture this changes nothing.', 'Qwen-Image-Edit with the painted crop as Picture 1 and the face picture as Picture 2; two tries, each checked like any close-up'),
  face_sides: H('Paint the cheeks from the side views (optional)', 'Paints the sides of the face from the side views of step 2, where the front picture cannot reach.', 'On by default: it hides the seam where the front painting ends on the cheek. Turn it off if it leaves smudges by the ears or the neck (it did on a bearded face whose side views placed the beard differently); the front of the face is unchanged either way.', 'Second projections from the quarter and side cut-outs, only where the front painted little'),
  face_density: H('Face texel density (face sharpness)', 'How many texture pixels the face gets. Standard is an eighth of the texture\'s width; High is about 512 across the face, or as many as the picture has detail for.', 'More texels make the eyes and mouth crisp up close. Where the texture budget is 4096, High ships the colour texture at 4096; at 2048 and 1024 the face takes a larger share and the rest of the body gives up at most 12 percent of its sharpness.', 'Face UV island scale and repack (max loss 12 percent), base colour at 4096 on 4096 budgets'),
  face_closeup: H('Close-up source (a larger picture of the face)', 'When the face in the picture is small, the image model draws a close-up of the same head and the face is painted from it.', 'A small painted face has too few pixels to fill the texture. The close-up is used only if it is still the same face; otherwise the painting is used.', 'Qwen-Image-Edit close-up at 1024 (drawn once more in the look\'s own words if the first drifts from the painting); each eye and the mouth found again by block matching at the painting\'s resolution and moved back if a little off; checked by likeness, eye, skin and hair colour, and skin texture (a photograph-like close-up is not used)'),
  hair_detail: H('Hair detail (strands and shading painted on)', 'Strands and clumps along the way the hair falls, darker roots and under-layers, lighter tips and, for painted looks, a soft highlight band.', 'The 3D builder makes hair as one flat-coloured shape; this makes it read as hair without adding to the shape. It stands back when hair cannot be told from skin or clothes.', 'Colour vote over the model and turnaround pictures; flow from oriented line filters or down from the crown; line integral convolution in 3D; strand height in the normal map; more texels in the face relayout'),
  head_triangles: H('Weighted decimation (more triangles on the head)', 'When the model is shrunk to your platform\'s triangle budget, the head keeps about twice the triangles an even shrink would leave it, and the body gives them up.', 'Players look at the head; with more triangles the outline of the hair and the face is rounder. The total stays inside the budget. Characters in the Low-poly, Pixel-art and Retro 64 looks never get it.', 'The 3D step builds twice the target; Blender Decimate (collapse) with an inverted vertex group on the top 13 percent of the height, then one rebake'),
  hair_strength: H('Hair strength', 'How strongly the hair detail is painted: 1 is full, 0 changes nothing.', 'Lower it if the strands look too busy for your look.', 'Blend weight of the colour, normal, roughness and metallic changes on the hair, 0 to 1'),
  hair_material: H('Hair material (its own texture and shader)', 'The hair gets a second material with its own texture, so it holds about three times the texels and, in Unity, a highlight that stretches across the strands.', 'Hair is what people look at up close. It costs one more draw call per character, so Automatic turns it on for PC and Showcase and leaves Phone with one material.', 'Second material slot on the hair triangles (straddling triangles stay with the body), own UV layout and 1024 px maps (2048 on Showcase), flow map, KHR_materials_anisotropy in the GLB, VGAC/Hair Kajiya-Kay shader on the Unity built-in pipeline'),
  textures_full_size: H('Textures at full size', 'Whether Unity imports each texture at the size of its file.', 'Unity shrinks textures over 2048 px unless told otherwise, which softens a 4096 texture.', 'TextureImporter.maxTextureSize against the source size'),
  face_matches_picture: H('Face matches the picture', 'Whether the face was painted from the picture and came out sharp.', 'A blurred face is the first thing people notice on a character.', 'Sharpness of the front render and feature match to the fitted picture'),
  hair_matches_picture: H('Hair detail (strands and shading painted on)', 'Whether the hair got strands and shading, or why the pass stood back.', 'Flat-coloured hair reads as a helmet; painted strands read as hair.', 'Hair mask, flow, texels per cm before and after, the face measures before and after'),
  hair_material_check: H('Hair material (its own texture and shader)', 'Whether the hair got its own material and texture, its texels per cm, or why it stayed in the body material.', 'A second material makes the hair sharper and lets Unity give it a hair highlight, at one more draw call.', 'Triangle split confidence, hair texture texels per cm, body texture texels per cm'),
  hair_material_shipped: H('Hair material in the files', 'Whether the files that ship carry the hair as its own material.', 'Without it the game would show the hair with the body texture only.', 'GLB material count, FBX material slot names, Unity hair material and shader'),
  pbr_fix: H('PBR range check (realistic colour range)', 'Keep colours, metal and shine inside the range real materials have.', 'Colours outside it look flat, chalky or glowing under modern game lighting.', 'PBR validation: albedo 30 to 240 sRGB, metallic near 0 or 1, roughness above 0'),
  pbr_range: H('PBR range check (realistic colour range)', 'Keep colours, metal and shine inside the range real materials have.', 'Colours outside it look flat, chalky or glowing under modern game lighting.', 'PBR validation: albedo 30 to 240 sRGB, metallic near 0 or 1, roughness above 0'),
  pivot: H('Pivot (anchor point)', 'Where the model sits when placed: at its feet or its centre.', 'Characters stand on their feet; floating things use the centre.', 'pivot (base | center)'),
  // Scenery on a grid
  grid: H('Grid', 'An invisible floor of squares that scenery pieces line up on.', 'Pieces made to the grid fit together with no gaps or overlaps.', 'Modular grid, grid_m; dimensions rounded to grid steps'),
  grid_m: H('Grid size', 'An invisible floor of squares that scenery pieces line up on.', 'Pieces made to the grid fit together with no gaps or overlaps.', 'Modular grid, grid_m; dimensions rounded to grid steps'),
  corner_pivot: H('Corner pivot (corner anchor)', 'The point the piece is placed by: its back-left bottom corner.', 'Every piece uses the same corner, so they snap edge to edge.', 'Origin at the bounding-box minimum corner'),
  grid_footprint: H('Size on the grid', 'How many grid squares the piece covers on the ground, wide by deep.', 'Pieces made to whole squares fit together with no gaps or overlaps.', 'Footprint in grid cells; automatic rounds width and depth to the nearest whole step (at most 15 percent per side)'),
  grid_height: H('Height on the grid', 'How tall the piece is, rounded to the grid.', 'Walls of the same height line up at the top, and floors sit flush.', 'Height snapped to half grid steps, or set to the chosen value'),
  smooth_angle: H('Auto-smooth angle (smoothing)', 'The angle above which an edge looks sharp instead of smooth.', 'Lower keeps hard edges; higher rounds things off.', 'smooth_angle (auto smooth)'),
  manifold: H('Clean surface', 'No holes or overlapping edges.', 'Game engines and the skeleton step need a clean surface.', 'manifold'),
  non_manifold_edges: H('Broken edges', 'Edges shared by too many faces or by none.', 'They cause shading errors and rigging problems.', 'non_manifold_edges'),
  watertight: H('Closed shell', 'The surface has no openings.', 'A closed shell cleans up and casts shadows correctly.', 'watertight'),
  boundary_edges: H('Open edges', 'Edges with nothing on one side: the rim of a hole.', 'Zero means the model is closed.', 'boundary_edges'),
  degenerate_faces: H('Zero-area faces', 'Triangles squashed to a line or a point.', 'They cause rendering glitches.', 'degenerate_faces'),
  uv_set: H('UV map (texture layout)', 'The map that says which part of the texture goes where.', 'There should be exactly one.', 'uv_set'),
  uv_layers: H('UV maps (texture layouts)', 'How many texture maps the model carries.', 'Exactly one is what engines expect.', 'uv_layers'),
  uv_islands: H('UV islands (texture pieces)', 'Separate patches in the texture layout.', 'Fewer, bigger pieces waste less texture.', 'uv_islands'),
  texel_density: H('Texel density (texture sharpness)', 'How many texture pixels cover one metre of surface.', 'Even sharpness across the model avoids blurry patches.', 'texel_density'),
  gate_ok: H('Looks good', 'Whether every quality check passed.', 'A failing check means the asset may misbehave in the game.', 'gate_ok'),
  checks_passed: H('Checks passed', 'How many quality checks passed.', 'All of them should.', 'checks_passed'),
  dims: H('Size', 'Width, depth and height of the model in metres.', 'Sanity check for scale.', 'dims'),
  // Rig
  rig: H('Rig', 'The skeleton inside the model plus the skin weights that tie the surface to it.', 'A rigged model can be animated: it walks, idles and bends at the joints.', 'rig (armature and skin weights)'),
  template: H('Rig template (skeleton type)', 'Which skeleton to fit: a standard human one, a generic one, or none.', 'The standard human skeleton gets shared animations for free.', 'template (auto | mixamo | generic | none)'),
  skeleton: H('Skeleton', 'The set of bones inside the model that animations move.', 'No skeleton, no movement.', 'skeleton (armature)'),
  bones: H('Bones', 'The joints of the skeleton.', 'More bones mean finer movement and a slightly heavier asset.', 'bones'),
  skin_weights: H('Skin weights', 'How much each point of the surface follows each bone.', 'Bad weights make the surface tear or bend wrongly.', 'skin weights'),
  weight_coverage: H('Weight coverage (surface attached)', 'Share of the surface that follows at least one bone.', 'Unattached points stay behind when the character moves.', 'weight_coverage'),
  group_per_vertex: H('Max bone influences (bones per point)', 'How many bones one surface point may follow.', '4 is standard; lower is faster, higher bends more smoothly.', 'group_per_vertex (max influences)'),
  max_influences: H('Max bone influences (bones per point)', 'The most bones any surface point follows.', 'Game engines usually allow 4.', 'max_influences'),
  weights_normalised: H('Weights add up', 'Each point\'s weights add up to exactly 1.', 'Otherwise the surface drifts when it bends.', 'weights_normalised'),
  unnormalised_verts: H('Unnormalised vertices (points with bad weights)', 'Surface points whose weights do not add up to 1.', 'Should be zero.', 'unnormalised_verts'),
  single_root: H('One root bone', 'The skeleton starts from one bone.', 'Engines expect one root.', 'single_root'),
  bottom_center_origin: H('Stand on the ground', 'Put the anchor at the feet, centred.', 'The character then stands on the floor when placed.', 'bottom_center_origin'),
  bone_names: H('Bone names', 'The label on every joint of the skeleton.', 'Engines and animation tools match animations to joints by name, so wrong names mean wrong limbs move.', 'Mixamo naming convention (mixamorig:Hips, LeftArm and so on), assigned by geometry'),
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
  moves: H('Animation clips (moves)', 'The animations made for this creature: standing still, walking, running, turning, attacking, getting hit and so on.', 'Leave out moves your game will never use to keep the file smaller.', 'Procedural clip set (procanim.py)'),
  clips_source: H('Where the moves come from', 'Human-shaped characters can use a library of hand-made moves; every other skeleton gets moves built for it.', 'The hand-made moves look natural. The built-in ones work on any skeleton and need nothing installed.', 'CC0 Quaternius Universal Animation Library retargeted in Blender, else procedural clips'),
  move_size: H('How big the moves are', 'How far the legs, body and wings swing in each move.', 'Small suits heavy or calm creatures; big suits lively ones.', 'Amplitude 0.7, 1.0 or 1.3'),
  move_speed: H('How fast', 'How quickly the moves play.', 'Match the creature\'s size and mood: big animals move slower.', 'Clip speed 0.8, 1.0 or 1.25'),
  // Add a move (rig step)
  add_move: H('Add a move', 'Pick more hand-made moves for a human-shaped character, such as sitting, blocking with a shield or casting a spell, or take some off.', 'Only the moves are made again; the skeleton and hit boxes stay as they are. Once you hand it off to the game again (step 6), each added move is in the files and in Unity, ready to use.', 'Quaternius Universal Animation Library packs 1 and 2 (CC0), retargeted in Blender; rig params added_moves, removed_moves'),
  my_moves: H('Your own animation file', 'An FBX, GLB or BVH file of a human-shaped skeleton that you bring, for example a Mixamo download without skin or a motion capture file.', 'The app recognises the bones by their names and copies the moves onto your character. You are responsible for being allowed to use the file in your game.', 'Bone names: Mixamo, Unreal, Rigify, Unity Humanoid and common BVH names; library/my_moves'),
  moves_from: H('Where moves come from', 'Human-shaped characters get hand-made moves from a free library (CC0, fine in commercial games), copied onto their own skeleton, and you can add more from that library or bring your own animation file. Other creatures, such as animals, spiders, birds and snakes, get moves the app builds for their own skeleton.', 'Hand-made moves look natural; the built ones fit any skeleton and need nothing extra.', 'Library retarget (retarget.py) for humanoids, procedural clips (procanim.py) for every other skeleton'),
  // Preview in realistic light (PHOTOREAL-PLAN phase 1)
  preview_light: H('Preview light', 'The light the 3D preview is shown in: Studio, Outdoors, Overcast, Night, or Flat for plain lights that make the shape easy to check.', 'Metal and shiny parts only look right when there is something around them to reflect. Only the preview changes; your game uses its own lights.', 'Image-based lighting from a CC0 HDRI (three.js RoomEnvironment for Studio), PMREM, Khronos PBR Neutral tone mapping'),
  beauty_render: H('Beauty render (beauty picture)', 'A finished picture of your asset in the same outdoor light as the preview, made when the files are packed.', 'Good for showing people what you made.', 'Blender Eevee render of the final model under a CC0 HDRI, Filmic or AgX view transform'),
  // Hair and fur (docs/HAIR-RESEARCH.md 5.4)
  hair_pictures: H('Hair-friendly pictures', 'For characters with hair the app adds a short hair sentence to the picture request: in the Painted look, hair as one smooth, simple shape with both eyes clearly visible; in Photoreal, tidy hair with nothing over the face; in Low-poly, Pixel and Retro 64, one simple solid shape. Cel and Clay characters get none, because every wording tried changed the character. Creatures get fur lying flat in clear clumps.', 'The 3D builder turns the outline of the picture into one solid shape, so wispy hair becomes loose flakes and separated locks become jagged edges. Your description always wins: long loose hair, a fringe over one eye, dreadlocks or curls are kept, and long loose hair gets no hair sentence at all.', 'Hair sentence per look in the concept prompt (data/styles.yaml hair, vgac/hairpic.py)'),
  hair_cards: H('Hair cards (flat strips with a see-through picture of hair on them)', 'How most realistic games make hair: hundreds of flat strips layered from the scalp outward, each showing a lock of hair with see-through gaps.', 'Some styles in the hair library are made of them. The hair built with the 3D model is one solid shape with the colour and strands painted on, which suits painted and stylised looks best.', 'Alpha-tested hair cards'),
  spring_bones: H('Spring bones (small extra bones that sway on their own)', 'What makes a ponytail, cape or tail swing in a game: short chains of bones that lag behind the body and settle again.', 'The app adds them at the Make it move step to parts that hang clear of the body; hair resting on the shoulders is joined to them and moves only with the body.', 'Spring bone (jiggle bone) chains'),
  anisotropic_highlight: H('Anisotropic highlight (a shine stretched into a band)', 'The bright band across hair, running across the strands instead of sitting as a round spot.', 'It is much of what makes hair look like hair rather than plastic. The app adds one when the hair has a hair material (its own texture and shader), whether the hair was built with the 3D model or came from the hair library: Unity draws the band with the hair shader the app installs, and the GLB file carries it for engines that read it. Hair without its own material, as on Phone by default, gets no band; in painted looks the picture may already have one painted in.', 'Anisotropic specular (Kajiya-Kay two-band shading in the VGAC/Hair and VGAC/HairCutout shaders in Unity; KHR_materials_anisotropy in the GLB)'),
  // Hair from the library (docs/HAIR-LIBRARY-PLAN.md section 7)
  hair_library: H('Hair library (ready-made hairstyles fitted to the head)', 'A choice per character, off unless you turn it on: the 3D model is built bald, and you pick one of the ready-made hairstyles the app tried on your character. The app fits it to the head, recolours it to the hair in your picture and gives it its own material.', 'Ready-made hairstyles are made by artists and can hang free, so long hair and ponytails swing with spring bones (small extra bones that sway on their own). The hair built with the 3D model often looks more like the picture where the hair rests on the head. Every style is CC0 (free to use, also in games you sell), and the hand-off credits its author. Turn it on when you create the character, or later with Change next to Hair on the asset page.', 'Asset.hair_json; bald turnaround pictures; hairfit rank at the 3D step and attach at Clean up; material slot <name>_Hair; backend/vgac/data/hairlib'),
  bald_picture: H('Bald picture (the character without hair, used to build the 3D model)', 'A copy of your picture with only the hair taken away, drawn by the picture editor from the front, the three-quarter view and both sides.', 'The 3D model is built from it, so the library hairstyle gets a clean head to sit on. The face is still painted from your own picture. Check that the face and the clothes look the same in both pictures.', 'Qwen-Image-Edit bald edit: bald_front, bald_quarter, bald_left, bald_right; bald_picture check (share of the picture outside the hair that stayed the same)'),
  alpha_cutout: H('Alpha cutout (see-through parts of a texture)', 'A texture whose pixels are either shown or left out, so a flat strip can look like a lock of hair with gaps between the strands.', 'Hair cards (see-through strips) need it. Each pixel is either fully there or fully gone, which is cheap and draws in the right order in game engines; the edges look a little harder than real see-through.', 'Alpha test (glTF alphaMode MASK, cutoff 0.5), VGAC/HairCutout shader in Unity, mipmaps that keep their coverage'),
  scalp_cap: H('Scalp cap (a hair-coloured layer under the hair)', 'A thin layer in the hair colour, fitted just over the head under a library hairstyle.', 'Where the hairstyle leaves a small gap, you see hair colour instead of skin.', '2.5 mm off the head surface; role 1 in hair_mesh.npz'),
  // Hit boxes and the body shape for moving (4.2)
  socket: H('Socket (attachment point)', 'A named spot on the skeleton where your game can attach a weapon, hat, backpack, or a shield strapped to the forearm.', 'The item then follows the hand, forearm, back or head through every animation.', 'Empty transform named Socket_* parented to a bone; listed in sockets.json'),
  items: H('Items', 'A sword, shield, bow, quiver, hat or backpack made as its own asset and put on a socket (attachment point) of a character, instead of being part of the body.', 'The character can then really hold the sword when he attacks, and you can swap or take items off in your game. Clothes that bend with the body, such as a cloak or armour, stay part of the body. A shield is strapped to the outside of the forearm. Items you have not moved yourself are fitted to the moves automatically so they stay clear of the body; Fit it for me does the same for one you placed. The hand does not close around the handle (no finger bones).', 'Item asset (prop_rigid, Asset.held_item) with its pivot at the detected grip; attachments.json, <name>_equipped.glb and the Unity prefab "<name> (equipped)"'),
  hitboxes: H('Hit boxes', 'Invisible shapes on each body part that tell the game where an attack landed.', 'Hits count where they look like they land, and you can tell a head hit from an arm hit.', 'Per-bone trigger capsules and boxes, fitted from skin weights'),
  hitbox_fit: H('Hit box fit (how snug)', 'How tightly the hit boxes wrap the body.', 'Snug is fairer for the player being hit; loose is kinder to the player aiming.', 'Radius factor 0.9, 1.0 or 1.15 on the 80th-percentile vertex distance'),
  hitbox_regions: H('Body parts with hit boxes', 'Which body parts get their own hit box.', 'Leave out parts your game never needs to tell apart.', 'Region ids from hitboxes.json regions_found'),
  head_sway: H('What may sway on the head', 'Which parts hanging from the head get spring bones.', 'Automatic: hair sways; a hat\'s brim or back, a hood or a helmet moves with the head, because the app cannot tell a hat from cloth by shape alone. A headscarf, ribbon, veil, tassel or feather named in the description may sway. When the description names a hat, helmet or hood, a part must be clearly hair-coloured to sway as hair. Choose Everything that hangs for the old behaviour.', 'blender/springs.py --head-cloth stiff|sway, --headwear; HAIR_SHARE_HEADWEAR 0.8'),
  springs: H('Spring bones (small extra bones that sway on their own)', 'Parts that hang clear of the body, such as a ponytail, a braid, a cape or a tail, get a short chain of bones that lags behind the body and settles again. The sway is baked into every move, and in Unity a small script makes it react to the game.', 'Only parts that hang clear of the body can move: hair resting on the shoulders is joined to them in the 3D model, and a short haircut has nothing that hangs. Rigid things such as a sword, a scabbard or a quiver are left alone.', 'Voxel slices find parts joined to the body at one end only; 2 to 4 bones per chain, weights faded from the root; a 120 Hz spring simulation with the hit boxes as colliders baked into every clip; springs.json; VgacSpringBones in Unity'),
  movement_shape: H('Character collider (body shape for moving)', 'One simple shape around the whole body that the game uses for walking into walls and standing on floors.', 'It stays still while the hit boxes move, so movement is smooth and predictable.', 'Root CapsuleCollider (direction Y or Z) or BoxCollider'),
  // Export
  lod: H('LOD (detail levels)', 'Lighter copies of the model used when it is far away.', 'Keeps the game fast with many assets on screen.', 'LOD (level of detail)'),
  lods: H('LOD (detail levels)', 'Lighter copies of the model used when it is far away, as fractions of the full model.', 'Keeps the game fast with many assets on screen.', 'lods (LOD ratios)'),
  lod_count: H('LOD count (number of detail levels)', 'How many lighter copies were made.', 'Characters and creatures get four; props and scenery get three.', 'lod_count'),
  collider: H('Collider (collision shape)', 'Invisible simple shapes the game uses to know where the asset is solid.', 'Without them things pass straight through it; the right shapes keep doorways open and seats usable.', 'Primitive, convex hull (255-triangle cap), CoACD convex decomposition, or mesh collider (static only)'),
  collider_triangles: H('Collider triangles', 'How many triangles the collider (collision shape) has.', 'Unity allows at most 255 in a simple shape and reshapes anything bigger.', 'MeshCollider convex triangle limit'),
  collider_vertices: H('Collider vertices', 'How many vertices (points) the collider has.', 'Unity allows at most 255 triangles in a convex collider; we keep the shape small to stay under it.', 'collider_vertices'),
  collider_verts: H('Collider vertices', 'How many vertices (points) the collider has.', 'Unity allows at most 255 triangles in a convex collider; we keep the shape small to stay under it.', 'collider_verts'),
  collider_present: H('Collider present', 'Whether a collider (collision shape) was made.', 'Needed for physics.', 'collider_present'),
  collider_max_parts: H('Max collider parts (most shapes)', 'The most separate collider shapes the asset may get.', 'More shapes fit hollow things better; fewer keep physics cheap, which matters most on phones.', 'CoACD max_convex_hull; automatic is 8 for props, 16 for scenery, 6 for vehicles, half on Phone'),
  collider_count: H('Collider count', 'How many separate simple shapes make up the collider.', 'Each shape costs a little physics time every frame, so phones get fewer.', 'Collider components under the Colliders child of the prefab'),
  openings_clear: H('Openings stay open', 'Whether doorways, arches and gaps are left open by the collision shape.', 'A shape that fills a doorway stops players walking through it.', 'Horizontal ray grid; fails when more than 10 percent of opening rays hit the collider'),
  lightmap_uv: H('Baked lighting ready', 'A second texture layout the engine uses to store pre-computed light and shadow.', 'Walls and floors can carry soft, realistic shadows without slowing the game.', 'Lightmap UV (UV1), Unity generateSecondaryUV'),
  lod_fade: H('LOD cross-fade (smooth detail changes)', 'Far-away copies blend into each other instead of switching at once.', 'Across a forest, sudden switches show as popping; a blend hides it.', 'LODGroup fadeMode CrossFade; URP needs LOD Cross Fade on'),
  metal_shine: H('Metal and shine', 'A texture that tells the engine which parts are metal and how glossy each part is.', 'Without it armour looks like plastic and wet stone looks dry.', 'MetallicSmoothness map (URP Lit: metallic in RGB, smoothness in alpha)'),
  metallic_smoothness: H('Metal and shine', 'A texture that tells the engine which parts are metal and how glossy each part is.', 'Without it armour looks like plastic and wet stone looks dry.', 'MetallicSmoothness map (URP Lit: metallic in RGB, smoothness in alpha)'),
  orm: H('Surface texture', 'One texture that holds shadowing, roughness and metal in its three colour channels.', 'Unreal, Godot and web viewers read all three from one file, which saves memory.', 'ORM pack (R occlusion, G roughness, B metallic); glTF metallicRoughness'),
  animator_controller: H('Animation controller', 'The part of the game object that picks which animation plays: standing, walking or running, depending on speed.', 'Your game sets one number, Speed, and the character switches on its own.', 'AnimatorController: 1D blend tree on Speed; triggers Attack, Hit, Die, Jump; root motion off'),
  loop: H('Loops', 'The animation runs again from the start as soon as it ends, with no jump.', 'Idle and walk must loop, or the character freezes after one step.', 'AnimationClip loopTime and loopPose'),
  avatar: H('Skeleton in Unity', 'Whether Unity reads the skeleton as a person (Humanoid) or as its own shape (Generic).', 'Humanoid skeletons can play every shared human animation; Generic ones play only their own.', 'Unity Avatar: Humanoid (HumanDescription) or Generic'),
  what_you_get: H('What you get', 'A short list of what the finished asset does in a game: its size, its surface, how it moves and what it bumps into.', 'Each line is checked; a line with a yellow dot has a problem, and the button next to it fixes it.', 'metrics.finish_lines (export stage)'),
  unity_howto: H('Using it in Unity', 'The few things to do in Unity to put this asset in your game.', 'The prefab is already set up; these lines say how to drive it from your own scripts.', 'metrics.unity_howto (export stage)'),
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
  look: H('Style preset (look)', 'A ready-made style: the model, the style words, a palette and what to avoid, chosen together so they work.', 'Pick one and every asset in the project matches it; edit the fields by hand only if you want something unusual.', 'style preset (GET /api/style-presets, stored as style_lock.preset)'),
  look_preset: H('Style preset (look)', 'A ready-made style: the model, the style words, a palette and what to avoid, chosen together so they work.', 'Pick one and every asset in the project matches it; edit the fields by hand only if you want something unusual.', 'style preset (style_lock.preset)'),
  // Graphics cards (Health page)
  gpu_default: H('Default card', 'The card new work goes to first. Every step it can do starts there; rigging stays on the card that can rig.', 'Pick the faster card to finish single assets sooner, or leave each step on its usual card so both cards stay loaded with the models they use most.', 'prefs.json default_instance (PUT /api/gpus)'),
  gpu_share: H('Use the other card when one is busy', 'When one card is busy and the other has nothing to do, a waiting step moves to the free card, if that card can do it and is allowed to.', 'Turn it on when you make several assets at once. A card loads the models for a new kind of step the first time, which adds a minute or two.', 'prefs.json share_when_busy; JobQueue.take_shared'),
  gpus: H('Graphics cards', 'The cards in this computer that paint the pictures and build the 3D models. Only those generation steps use a card at full load; cleaning up, handing off and the Unity import run on the processor.', 'If you need a card for a game or another app, turn it off here and the app carries on with the other one.', 'ComfyUI instances (GET /api/gpus)'),
  gpu_switch: H('On or off', 'Turning a card off hands its work to the other card so you can use it for games or other apps. A step only this card can do waits until it is back on. Nothing is lost.', 'You can switch it back on at any time; the app remembers your choice after a restart.', 'PUT /api/gpus/{instance} enabled (prefs.json disabled_instances)'),
  gpu_memory: H('Memory used', 'How much of the card\'s own memory is in use right now, by this app and anything else.', 'A card keeps its models loaded between steps so the next step starts faster; that memory is released when you turn the card off.', 'VRAM (ComfyUI /system_stats)'),
  gpu_advanced: H('Advanced', 'Finer control over one card: which kinds of work it may do, and whether it empties its memory after every step.', 'Most people never need these; turning the card off covers the usual case.', 'allowed_kinds, free_after_job'),
  gpu_kinds: H('What this card may do', 'Untick a kind of work to keep it off this card. Work of that kind moves to another card that may do it, or waits until one may.', 'Useful when a card is busy with something else but can still help with lighter work. Greyed out boxes are work this card cannot do at all.', 'allowed_kinds (prefs.json)'),
  gpu_kind_pictures: H('Pictures', 'Painting the first picture, drawing all sides, and making tileable materials.', 'These are the quickest steps and the ones you make the most of.', 'concept, turnaround and material stages'),
  gpu_kind_model: H('3D model', 'Turning the pictures into a 3D shape with painted surfaces.', 'The heaviest step: it needs a lot of the card\'s memory for a few minutes.', 'mesh stage'),
  gpu_kind_rigging: H('Rigging', 'Adding a skeleton so the model can move.', 'This step only works on the card set up for it, because it needs Blender wired in.', 'rig stage (SkinToken)'),
  gpu_kind_review: H('Review', 'The app looking at a result and saying what it thinks of it.', 'Optional; a review waits if no card may do it, nothing else stops.', 'local judge (judge job)'),
  gpu_engine: H('ComfyUI instance (graphics engine)', 'The program on this card that paints the pictures and builds the 3D models (ComfyUI). The app starts it by itself when a step needs it, which takes about a minute.', 'The app only ever shuts down an engine it started itself. One started by ComfyUI Desktop or the launcher is left running.', 'ComfyUI instance (POST /api/gpus/{instance}/start and /stop)'),
  gpu_idle_stop: H('Shut down when idle', 'After this long with nothing to do, the app shuts down the card\'s graphics engine. That frees the whole card between bursts of work.', 'The next step then waits about a minute while the engine starts again. Only applies to an engine the app started itself; one started by ComfyUI Desktop or the launcher is left running.', 'stop_when_idle_minutes (prefs.json)'),
  gpu_address: H('Address', 'Where the app talks to this card\'s graphics engine. "Found by its card" means the engine was not at the usual address, so the app looked for the one running on this card and uses that.', 'ComfyUI Desktop picks its own address; the app follows it instead of failing. It never takes an engine running on the other card.', 'ComfyUI url, instances.<name>.discover in backend/config.yaml'),
  gpu_setup_problem: H('Setup problem', 'Something on this computer stops the card\'s graphics engine from being used, for example another program holding its address.', 'The app does not send this card\'s work anywhere else by mistake; fix the problem and press Try again.', 'Supervisor.conflict (instances.py)'),
  setup_check: H('Is everything ready?', 'A check of everything the app needs: Blender, the Unity editor, the graphics engines and their parts, the model files, disk space and memory.', 'A missing piece shows up here, with what to do, before a step fails halfway.', 'GET /api/setup (backend/vgac/preflight.py)'),
  setup_check_again: H('Check again', 'Runs every check now instead of using the last result (results are kept for a minute).', 'Use it after you have fixed something.', 'GET /api/setup?refresh=1'),
  setup_show_all: H('Show all checks', 'Every check, including the ones that passed, grouped by tools, graphics engines, models and storage.', 'Useful to confirm a fix, or to see exactly what the app looked for.', 'preflight items'),
  setup_not_checked: H('Not checked', 'The app could not check this right now, usually because the card is off or its graphics engine is not running yet.', 'This is not a problem by itself; the check runs again once the engine is up.', 'state unknown'),
  setup_group_tools: H('Tools', 'The programs the app uses on this computer: Blender for cleaning up, rigging and animation, the Unity editor for the test import, and the shared animation library.', 'Without Blender the last steps cannot run; without Unity only the test import is skipped.', 'blender_exe, unity_exe, unity_project, clips_libraries'),
  setup_group_engines: H('ComfyUI instances (graphics engines)', 'The programs on the graphics cards that paint the pictures and build the 3D models (ComfyUI), and the parts (nodes) each step needs in them.', 'A missing part stops only the steps that need it.', 'ComfyUI instances, /object_info'),
  setup_group_models: H('Models', 'The large files the graphics engines learn from: one per image model (the painter), the side-view model (Qwen-Image-Edit), the 3D model builder (TRELLIS.2) and the rigging model (SkinToken).', 'A missing model file stops the step that uses it; you download it once.', 'data/models.yaml, loader node file choices'),
  setup_group_storage: H('Storage', 'Free disk space where your assets are kept, and free memory on this computer.', 'Steps wait when memory is short, and fail to save when the disk is full.', 'library_path, export_path, min_free_ram_gb'),
  setup_needs_attention: H('Setup needs attention', 'At least one thing the app needs is missing or broken.', 'Open the Health page to see what it is and how to fix it.', 'GET /api/setup summary.fails'),
  gpu_free_after: H('Free memory after each step', 'After every step on this card, the app tells it to unload its models and release its memory.', 'Leaves the card\'s memory free for your other apps between steps, at the cost of a slower next step, because the models have to load again.', 'free_after_job (ComfyUI POST /free)'),
  // Recipes: the look carried through every step
  recipe: H('Recipe', 'How your chosen look is carried through every step: detail level, texture size, shading and how the game engine shows the textures.', 'So a pixel-art or low-poly asset stays that way all the way into your game, not just in the first picture.', 'reference.recipe'),
  addons: H(
    'LoRAs (style add-ons)',
    'Small trained files that load on top of the image model and push the picture toward a look. Each one is made for one model family (its base model), so the list changes when you change the model.',
    'You never type a file path or a name: switch one on from the list, or press Add your own LoRA and choose the .safetensors file. The app keeps the file in its own folder and adds it to the picture recipe for you; there are no slots to fill.',
    'One LoraLoaderModelOnly per LoRA after the model loader. Tested: data/addons.yaml, files in loras/VGAC/. Yours: library/my_addons.json, files in loras/VGAC/mine/',
  ),
  addon_mine: H('Your own LoRA', 'A LoRA file you brought in yourself, listed under Yours for the models you said it was made for.', 'The app has not tested it. If the picture looks wrong, try Subtle or switch it off. Remove takes it off the list and leaves the file in the LoRA folder.', 'library/my_addons.json, loras/VGAC/mine/'),
  addon_painter: H('Base model (which model it was made for)', 'Every LoRA is trained for one model family and only works with that one.', 'Its download page names it, usually as the base model (for example Z-Image Turbo). The app reads the file when it can and leaves this empty when it cannot tell.', 'modelspec.architecture, ss_base_model_version in the file header'),
  addon_trigger: H('Trigger words (words the LoRA needs in the description)', 'Some LoRAs only work when certain words are in the description.', 'Its download page lists them. The app adds them to your description for you whenever the LoRA is on.', 'trigger (data/addons.yaml, my_addons.json)'),
  addon: H('LoRA (style add-on)', 'One small trained file that loads on top of the image model.', 'Switch it off to drop its effect, or pick how strongly it pushes the picture.', 'data/addons.yaml'),
  strength_subtle: H('Subtle', 'The LoRA pushes the picture a little.', 'Keeps more of the model\'s own look.', 'lower LoRA strength (strengths.subtle)'),
  strength_normal: H('Normal', 'The LoRA at the strength it was made for.', 'The safe choice; start here.', 'LoRA strength (strengths.normal)'),
  strength_strong: H('Strong', 'The LoRA pushes the picture harder.', 'Use it when the look does not come through enough; too strong can distort the picture.', 'higher LoRA strength (strengths.strong)'),
  texture_crisp: H('Crisp textures', 'Every texture pixel stays a sharp square, even up close.', 'The pixel-art look; smoothing would blur it.', 'Point filtering, no mipmaps, no compression'),
  texture_smooth: H('Smooth textures', 'Texture pixels blend into each other up close and at a distance.', 'The usual look for painted and realistic assets; it hides the pixel grid.', 'Bilinear filtering, mipmaps, compression'),
  shading: H('Shading', 'Whether the surface looks smooth and rounded or cut into flat faces.', 'Automatic follows your style: flat faces for low-poly, smooth for the rest.', 'smooth normals / flat per-face normals'),
  look_applied: H('Look applied', 'Whether the finished model matches your chosen look: its detail level, texture size, shading and surface bumps.', 'A pixel-art or low-poly asset should stay that way in your game, not come out as a detailed model.', 'look_applied check (prep), metrics.recipe'),
  // Start from your own images
  compose: H('Step 0.5: a character from your pictures', 'Give a few pictures, such as a face, a body and an outfit, and the app draws one new full-body character from them, standing ready for 3D and in the look of this project.', 'Each result arrives in step 1 as a normal first picture: keep the one you like and the other steps carry on as usual. The drawing model reads three pictures at most; with more, the extra ones are put side by side on one sheet.', 'POST /api/assets/{aid}/stages/concept/compose (Qwen-Image-Edit 2511 with up to three pictures, backend/vgac/compose.py)'),
  image_drop: H('Your picture', 'A picture from this computer: drag it in, paste it, or choose the file. PNG, JPEG or WebP, up to 25 MB.', 'It is turned the right way up, shrunk to at most 2048 pixels and stripped of hidden details such as where a photo was taken. It never leaves this computer.', 'base64 data URL in the JSON body (backend/vgac/uploads.py)'),
  start_from: H('Start from', 'Whether the first picture is painted from your words or is a picture you already have.', 'With your own picture the app skips painting and goes straight to cutting it out and checking it is ready for 3D.', 'POST /api/projects/{id}/assets with image'),
  what_is_it_note: H('What is it?', 'A few words about your picture, such as "a knight in red armour".', 'Optional. Later steps, such as changing something, read these words, so a short description helps them.', 'asset prompt (note)'),
  own_picture: H('Use my own picture', 'Start this step from a picture you already have instead of painting one.', 'Your picture is cut out and checked like a painted one; then every later step builds on it.', 'POST /api/assets/{id}/stages/concept/upload'),
  your_picture: H('Your picture', 'A version made from a picture you added, not painted by the app.', 'It goes through the same cutout and checks as a painted picture.', 'params.source = upload'),
  ready_for_3d: H('Ready for 3D', 'Checks that the picture suits the 3D step: one subject, all of it in the picture, big enough, and for people arms away from the body.', 'They only warn; you can keep a picture that fails one. "Redraw it ready for 3D" asks the image edit model to redraw it in the standard pose.', 'readiness checks (one_subject, in_frame, big_enough, arms_away)'),
  one_subject: H('One subject', 'Whether the picture shows one thing on its own.', 'Two characters or loose pieces in one picture become one strange 3D model.', 'connected areas of the cutout'),
  in_frame: H('Whole subject in the picture', 'Whether there is space around the subject on every side.', 'A part cut off at the edge is missing from the 3D model.', 'cutout margin on every side, at least 1 percent'),
  big_enough: H('Big enough', 'How many pixels tall (or across) the subject is.', 'The 3D step reads about 500 pixels; a small subject loses its detail.', 'subject height in pixels, at least 400'),
  arms_away: H('Arms away from the body', 'Whether the arms stand clear of the sides, as in an A-pose or a T-pose.', 'Arms pressed to the body get joined to it in the 3D model, and the skeleton step cannot tell them apart.', 'silhouette gap between arm and torso, or arm span against waist width'),
  hair_off_eyes: H('Eyes clear of hair', 'Whether hair hangs over the eyes in the picture, found by telling the hair colour from the skin colour.', 'The 3D model turns hair over the face into a solid flap, and the face cannot be painted cleanly. "Move the hair off the face" asks the picture editor to sweep it aside; it makes a new version and your picture stays. When your description asks for hair over the eyes, the app does not check this.', 'hair_off_eyes check (vgac/hairpic.py): hair against skin in each eye area'),
  face_drawn: H('Face drawn', 'Whether the character in the picture has a face with eyes, and is not painted from behind. The local vision model (the review engine) looks at a close-up of the head.', 'The 3D model is built from the picture, so an empty face or the back of the head gives a model with no face. "Draw the face" asks the picture editor to draw it; it makes a new version and your picture stays. Only the Cel look is checked, because its painter sometimes leaves the face out. When your description hides the face (a closed helmet, a mask, a figure seen from behind), the app does not check this.', 'face_drawn check (vgac/hairpic.face_check): Gemma 3 on a head crop answers eyes, mouth and back'),
  redraw_ready: H('Redraw it ready for 3D', 'Asks the image edit model (picture editor) to redraw the same subject, same design and colours, in the standard pose on a plain light grey background.', 'It makes a new version; your picture stays as it was.', 'concept refine with the category pose block'),
  even_lighting: H('Even light', 'Whether the photo is about as bright on one side as on the other.', 'A tiling texture repeats the photo side by side, so a bright side and a dark side show as stripes.', 'brightness plane fitted across the photo'),
  straight_on: H('Taken straight on', 'Whether the photo looks at the surface square on, not at an angle.', 'A photo taken at an angle has finer detail at the far side, and the texture looks stretched.', 'local detail in opposite thirds of the photo'),
  face_matters: H('The face matters', 'Whether this character\'s face should be sharpened and matched to the picture on the 3D model.', 'On (the default), each clean-up paints the face from the picture and, when the face is small, draws a close-up for more detail: a few minutes per run. Turn it off for a character whose face is hidden or too simple to gain from it (a hood with glowing eyes, a mask, a helmet, a robot); the face then stays as the 3D step made it. A run can still turn the face pass on in its own settings.', 'asset.face_matters; prep param face_detail when not set on the run'),
  own_face_closeup: H('Your own face close-up', 'A close-up picture of the face of this character that you already have, used instead of the one the app draws.', 'The clean-up step draws a close-up of the painted face with your picture\'s features (the painting\'s pose, expression and light, your picture\'s eyes, nose, mouth and skin); the colours and light stay those of the painting, so the face matches the body. If that close-up does not pass the checks, your picture is lined up with the painted face directly; if that fails too it is not used, and the quality checks say why. It is kept with the asset, so every later clean-up uses it until you remove it.', 'POST /api/assets/{id}/face-closeup, prep param face_closeup_own'),
  own_views: H('Your own side or back views', 'Pictures you already have of the other sides of the same subject.', 'The app uses yours as they are and draws only the sides you leave out. A picture that does not match the front gets a warning but is kept.', 'turnaround provided_views, POST /api/versions/{id}/views/{view}/upload'),
  supplied_view: H('Your view', 'A side you supplied yourself, not drawn by the app.', 'It is cut out and checked against the front like a drawn one, but never redrawn on its own.', 'metrics.views.<view>.supplied'),
  match_picture: H('Match a picture', 'Take the colours or the look of a picture you like and use them for this project.', 'Nothing changes until you press Apply, and you can edit the result first.', 'POST /api/palette-from-image, POST /api/projects/{id}/style-from-image'),
  take_colours: H('Take the colours', 'Finds the main colours of the picture, most used first, and leaves out a plain background.', 'Remove any you do not want, then apply them as the project palette.', 'median cut colour quantisation (palette-from-image)'),
  describe_style: H('Describe the style', 'The app looks at the picture and writes a short paragraph about how it is painted: the medium, shading, lines, shapes and colour mood.', 'The paragraph guides the look in words: the image model reads it before every description. Edit it freely before you apply it.', 'local vision model (Gemma) through the review engine'),
  review_engine: H('Vision model (review engine)', 'The part of the app that looks at pictures and writes about them, running on a graphics card here.', 'It is needed for "Describe the style"; the colours work without it.', 'judge_instance (graphs/judge.py)'),
  reference_picture: H('Reference picture (experimental)', 'A second picture the image edit model may look at while making your change, such as a helmet you want copied.', 'Experimental: the image edit model is given the picture, but how closely it follows one has not been measured yet. Your words still matter most.', 'Qwen-Image-Edit image2 (TextEncodeQwenImageEditPlus)'),
  unity_handoff: H('Sent to your open Unity project', 'Unity cannot be handed files while your project is open, so the app leaves them in the project for Unity to pick up.', 'You do not have to close your project; it imports itself when you switch to Unity.', 'Assets/VGAC/<Name>/_incoming/vgac_import.json, VgacImportWatcher'),
}

// The add-on strength steps in words (Fine-tune add-ons).
export const STRENGTH_LABELS: Record<'off' | 'subtle' | 'normal' | 'strong', string> = { off: 'Off', subtle: 'Subtle', normal: 'Normal', strong: 'Strong' }

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

// The clean-up step's line on the head after weighted decimation (vars head_weighted, head_triangles, head_share,
// head_even; backend headtris.head_vars), or nothing.
const headLine = (vars: Record<string, unknown>): string => {
  if (!vars.head_weighted || num(vars.head_triangles) === null) return ''
  let line = ` The head has ${n0(vars.head_triangles)} of them`
  if (num(vars.head_share) !== null) line += ` (${pct(vars.head_share)})`
  if (num(vars.head_even) !== null) line += `; an even spread would have left it ${n0(vars.head_even)}`
  return line + '.'
}

const CHECK_TEXT: Record<string, CheckText> = {
  triangle_budget: (v, l, ok, ctx, vars) => {
    const over = num(v) !== null && num(l) !== null ? num(v)! - num(l)! : null
    const who = ctx.platform ? platformPlain(ctx.platform).split(':')[0].toLowerCase() : 'this platform'
    // Library hair: the body is measured against what is left after the hair's share (vars hair_allowance, platform_triangles).
    if (num(vars.hair_allowance) !== null && num(vars.platform_triangles) !== null)
      return ok
        ? `The body's ${n0(v)} triangles fit the ${n0(l)} left for it after ${n0(vars.hair_allowance)} of your platform's ${n0(vars.platform_triangles)} are kept for the library hairstyle.${headLine(vars)}`
        : `The body has ${over !== null ? n0(over) + ' more' : 'more'} triangles than the ${n0(l)} left for it (${n0(v)}) after ${n0(vars.hair_allowance)} of your platform's ${n0(vars.platform_triangles)} are kept for the library hairstyle.`
    // The 3D step's model built over the budget on purpose, so the clean-up step can give the head more (vars head_room).
    if (vars.head_room && ok)
      return `The model has ${n0(v)} triangles, more than the ${n0(vars.platform_triangles)} ${who} allows on purpose: Clean up for the game brings it back to the budget with weighted decimation (more triangles on the head).`
    if (vars.head_room) return `The model has ${over !== null ? n0(over) + ' more' : 'more'} triangles than the 3D step asked for (${n0(v)} of ${n0(l)}, which already leaves room for the head).`
    return ok ? `The model has ${n0(v)} triangles, within the ${n0(l)} ${who} allows.${headLine(vars)}` : `The model has ${over !== null ? n0(over) + ' more' : 'more'} triangles than ${who === 'phone' ? 'a phone' : who} can handle (${n0(v)} of ${n0(l)}).`
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
  collider_triangles: (v, l, ok) =>
    ok ? `Each collision shape has at most ${n0(v)} triangles, inside Unity's limit of ${n0(l)}.` : `The collision shape has ${n0(v)} triangles; Unity would simplify it on its own and it may stop fitting.`,
  collider_count: (v, l, ok) =>
    ok
      ? `${n0(v)} collision shape${num(v) === 1 ? '' : 's'}, inside the ${n0(l)} your platform allows.`
      : `The collision is ${n0(v)} shapes, more than the ${n0(l)} we aim for on your platform; physics costs a little more each frame.`,
  openings_clear: (v, _l, ok, _ctx, vars) => {
    if (ok) return num(vars.openings) === 0 ? 'There are no doorways or gaps for the collision shape to fill.' : 'Openings stay open: things can pass through doorways and gaps.'
    return `The collision shape fills ${n0(v)} opening${num(v) === 1 ? '' : 's'}, so players cannot pass through.`
  },
  // Textures and light (3.9)
  bake_matches: (_v, _l, ok) => (ok ? 'The surface detail was painted onto the final model.' : 'The surface detail was painted for an earlier copy of the model, so shading may look wavy.'),
  normal_map_upright: (_v, _l, ok, _ctx, vars) => {
    if (ok) return 'The normal map (bump texture) is the right way up, so the surface shades smoothly.'
    return vars.stage === 'prep'
      ? 'The normal map (bump texture) is upside down, so the surface shows triangle-shaped facets and dark patches under light. Cleaning up again bakes it the right way up.'
      : 'The normal map (bump texture) is upside down, so the surface shows triangle-shaped facets and dark patches under light. Building the 3D model again writes it the right way up.'
  },
  texture_matches_picture: (v, l, ok, _ctx, vars) => {
    if (ok)
      return vars.rebuilt
        ? `The first build's base colour (the colour texture) came out too dark, so the 3D model was built again; build ${n0(vars.kept_try)} of ${n0(vars.tries)} keeps ${pct(v)} of the picture's brightness.`
        : `The base colour (the colour texture) keeps ${pct(v)} of the picture's brightness.`
    return `The base colour (the colour texture) came out much darker than the picture: ${pct(v)} of its brightness, it needs ${pct(l)}. ${vars.rebuilt ? `It was built ${n0(vars.tries)} times and this is the closest. ` : ''}Build it again, or use a picture with some soft light and shadow on the character.`
  },
  texture_padding: (v, _l, ok) => (ok ? `Texture pieces have ${n0(v)} px of space around them, enough for distant views.` : `Texture pieces have only ${n0(v)} px of space, so thin dark seams may show at a distance.`),
  atlas_used: (v, _l, ok) => (ok ? `The model uses ${pct(v)} of its texture.` : `The model uses only ${pct(v)} of its texture, so it looks less sharp than it could.`),
  face_matches_picture: (_v, _l, ok, _ctx, vars) => {
    if (ok) return 'The face was painted from the picture and is sharp.'
    const eye = str(vars.eye_words)
    const verb = str(vars.eye_verb) || 'was'
    if (vars.reason === 'eye_missing' && eye) return `The face was painted, but ${eye} ${verb} not: the picture shows the eye, the model still has the blurred smudge from the 3D step.`
    if (vars.reason === 'eye_under_hair' && eye)
      return `The face was painted, but ${eye} ${verb} not: the model's hair covers that eye, although the picture shows it. Painting the face again cannot change that; building the 3D model again can give hair that leaves the eye free.`
    if (vars.reason === 'eye_blurry' && eye) return `The face was painted, but ${eye} came out blurry, much softer than the picture's.`
    return 'The face does not match the picture yet.'
  },
  hair_matches_picture: (_v, _l, ok) => (ok ? 'Strands and shading were painted on the hair, or the pass stood back where hair could not be told apart.' : 'The hair detail did not come out as it should.'),
  hair_material: (v, l, ok, _ctx, vars) => {
    if (!ok) return 'The hair material was not made: something went wrong, so the hair stays in the body material.'
    if (vars.used === false) return 'The hair stays in the body material (one material), as the split was doubtful or not worth it.'
    return `The hair has its own material: ${n0(v)} texels per cm against ${n0(l)} in the body texture, at one more draw call.`
  },
  hair_material_shipped: (v, _l, ok) => (ok ? 'The files carry the hair as its own material.' : `The files that ship have ${n0(v)} material(s), not the hair's own.`),
  // Hair from the library (the backend writes these sentences; these are the fallback, and the passing ones show).
  bald_picture: (v, l, ok, _ctx, vars) =>
    ok
      ? `Bald picture (the character without hair, used to build the 3D model): only the hair was taken away, and ${pct(v)} of the rest of the picture is unchanged.${vars.rerolled ? ' The first try changed too much, so it was drawn a second time.' : ''}`
      : `Bald picture (the character without hair, used to build the 3D model): more than the hair changed (${pct(v)} of the rest of the picture is unchanged, it needs ${pct(l)}).`,
  head_build: (_v, _l, ok, _ctx, vars) =>
    ok
      ? 'The head was built on its own and joined to the body at the neck, so the face is much sharper.'
      : `The head built on its own did not fit, so the body's own head is used: ${str(vars.reason_words) || 'building it failed'}.`,
  hair_fit: (v, l, ok, _ctx, vars) => {
    const score = num(v) !== null ? num(v)!.toFixed(2) : '?'
    const good = num(l) !== null ? num(l)!.toFixed(2) : '0.60'
    const name = str(vars.style_name)
    const best = str(vars.best_name)
    if (!ok)
      return vars.step === 'prep'
        ? `Hair from the library (ready-made hairstyles): ${name ? `"${name}"` : 'the hairstyle'} follows the hair outline in the picture poorly (fit score ${score}, good from ${good}). Choose another style, or use the hair built with the 3D model.`
        : `Hair from the library (ready-made hairstyles): nothing in the library fits this picture well. ${best ? `The closest, "${best}", has` : 'The closest style has'} a fit score of ${score} (good from ${good}). Pick one by eye, or use the hair built with the 3D model.`
    const head =
      vars.step === 'prep' && name
        ? `Hair from the library (ready-made hairstyles): "${name}" was fitted to the head, ${vars.picked ? 'the style you picked' : 'the best match'}. `
        : num(vars.candidates) !== null
          ? `Hair from the library (ready-made hairstyles): ${n0(vars.candidates)} styles were tried on the bald model, and ${best ? `"${best}"` : 'the best one'} matches best. `
          : 'Hair from the library (ready-made hairstyles): '
    const none = vars.family_source === 'none' ? ' The description does not name a hairstyle, so the styles were ranked by outline alone; check them by eye.' : ''
    return `${head}It follows the hair outline in the picture well (fit score ${score}, good from ${good}).${none}`
  },
  hair_skin_through: (v, l, ok) => {
    if (!ok) return `Skin shows through the library hairstyle on ${pct(v)} of the scalp, more than ${pct(l)}. Fitting it again with more cover pushes the hair out a little and thickens the scalp cap (a hair-coloured layer under the hair).`
    if (num(v) === 0) return 'No skin shows through the library hairstyle. A scalp cap (a hair-coloured layer under the hair) sits under it.'
    return `The library hairstyle covers the head: ${pct(v)} of the scalp shows through, under the ${pct(l)} limit. A scalp cap (a hair-coloured layer under the hair) hides small gaps.`
  },
  hair_triangles: (v, l, ok, _ctx, vars) => {
    if (!ok) return `With the library hairstyle the character has ${n0(v)} triangles, more than your platform's ${n0(l)}, so it would run slower than it should. Choose a lighter hairstyle.`
    const split = num(vars.body) !== null && num(vars.hair) !== null ? `: ${n0(vars.body)} for the body and ${n0(vars.hair)} for the hair` : ''
    return `With the library hairstyle the character has ${n0(v)} triangles, within your platform's ${n0(l)}${split}.`
  },
  hair_bound: (v, _l, ok, _ctx, vars) => {
    if (!ok) return `Only ${pct(v)} of the library hairstyle follows the skeleton, so the rest would stay behind when the character moves. Making the moves again joins all of it to the skeleton.`
    const n = num(vars.groups_swaying) ?? 0
    if (n > 0) return `The library hairstyle follows the skeleton: it moves with the head, and ${n === 1 ? '1 hanging part sways' : `${n0(n)} hanging parts sway`} with spring bones (small extra bones that sway on their own).`
    return 'The library hairstyle follows the skeleton: it rests on the head, so it moves with the head.'
  },
  springs: (v, _l, ok, _ctx, vars) => {
    if (!ok) return 'The spring bones did not come out as they should.'
    if (vars.skipped) return `No spring bones were added: ${String(vars.why || 'nothing hangs clear of the body')}.`
    const what = String(vars.what || 'A hanging part')
    return `${what.charAt(0).toUpperCase() + what.slice(1)} sways when the character moves, on ${n0(v)} added bones.`
  },
  springs_shipped: (v, _l, ok) => (ok ? `The files carry ${n0(v)} spring chain(s), with the sway baked into the moves.` : 'The files that ship do not carry the spring bones.'),
  baked_light: (_v, _l, ok, _ctx, vars) => {
    if (!ok) return "The colours still carry shadows from the picture, so they will fight your game's lights."
    if (vars.kept) return 'Painted shading is kept on purpose for this look.'
    if (vars.removed) return "Painted-on shadows removed, so it reacts to your game's lights."
    return 'No painted-on shadows found in the colours.'
  },
  albedo_range: (v, _l, ok) => (ok ? 'Colours stay inside the range real materials have.' : `${pct(v)} of the colours are darker or brighter than real materials get, so they may look flat or glow under game lights.`),
  metal_mixed: (v, _l, ok) => (ok ? 'Metal parts are clearly metal and the rest clearly not.' : `${pct(v)} of the surface is half metal, which looks dull and grey in game engines.`),
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
  textures_full_size: (v, l, ok, _ctx, vars) => (ok ? `Unity imports every texture at its full size (the largest is ${String(vars.file_px ?? l)} px).` : `Unity imports the ${String(vars.texture || 'colour')} texture at ${String(vars.import_px ?? v)} px, smaller than the file's ${String(vars.file_px ?? l)} px, so it looks softer in the game.`),
  clips_loop: (v, _l, ok, _ctx, vars) => (ok ? 'Idle and walk loop smoothly.' : `The ${listText(vars.clip) || listText(v) || 'walk'} animation does not loop, so it would stop after one cycle.`),
  clips_face_forward: (v, _l, ok, _ctx, vars) => {
    const deg = n0(num(v) ?? 0)
    const clip = str(vars.clip)
    if (ok) return `Every move faces the character's forward in Unity; the furthest any turns is ${deg} degrees${clip ? ` (${clip})` : ''}.`
    const role = str(vars.role)
    const who = role ? `The ${role} move (${clip})` : clip ? `The ${clip} move` : 'A move'
    const side = str(vars.side)
    const others = num(vars.others) ?? 0
    return `${who} plays with the body turned ${deg} degrees away from the character's forward${side ? ` (to its ${side})` : ''}${others > 0 ? `, and ${n0(others)} more moves are turned too` : ''}, so in a game the character would face the wrong way while it plays.`
  },
  controller_ready: (_v, _l, ok) => (ok ? 'An animation controller switches between idle, walk and run by speed.' : 'No animation controller was built, so you would have to set one up in Unity.'),
  silhouette_iou: (v, l, ok) => (ok ? `The model outline matches the drawing (${pct(v)}).` : `The model outline only matches the drawing ${pct(v)}; it needs ${pct(l)}.`),
  coverage: (v, l, ok) => (ok ? `The character fills ${pct(v)} of the picture.` : `The character fills only ${pct(v)} of the picture; it needs at least ${pct(l)}.`),
  height_ratio: (v, _l, ok) => (ok ? `This view is the same height as the front view (${num(v)?.toFixed(2) ?? v}x).` : `This view is ${num(v)?.toFixed(2) ?? v}x the height of the front view; it should be about the same.`),
  hist_dist: (v, _l, ok) => (ok ? 'The colours match the front view.' : `The colours differ from the front view (${num(v)?.toFixed(2) ?? v}).`),
  mirror_iou: (v, l, ok) => (ok ? `The left and right views mirror each other (${pct(v)}).` : `The left and right views do not mirror each other well (${pct(v)}, needs ${pct(l)}).`),
  // Scenery on a grid
  grid_fit: (v, _l, ok, _ctx, vars) => {
    const [w, d, h] = gridDims(v, vars)
    const g = metres(vars.grid ?? vars.grid_m)
    if (ok) {
      const size = `${metres(w)} x ${metres(d)} x ${metres(h)} m`
      return vars.small ? `The piece is ${size}, smaller than one ${g} m square, so it sits inside one square.` : `The piece is ${size} and lines up with the ${g} m grid.`
    }
    const i = sideIndex(vars.side)
    const word = str(vars.side_word) || ['wide', 'deep', 'tall'][i]
    return `The piece is ${metres([w, d, h][i])} m ${word}, between grid steps, so it leaves a gap next to other pieces.`
  },
  tiles_cleanly: (v, _l, ok) => (ok ? 'Copies placed side by side meet without gaps.' : `Copies placed side by side leave a gap or overlap of ${n0(num(v) !== null ? Math.round(num(v)! * 10) / 10 : v)} cm.`),
  // Recipes: what the look did to the model (prep).
  look_applied: (v, _l, ok, _ctx, vars) => {
    const look = str(vars.look)
    const summary = str(vars.summary) || str(v)
    if (ok) return summary ? (look ? `${look} look applied: ${summary}.` : `Look applied: ${summary}.`) : 'Look applied.'
    const problem = str(vars.problem)
    return look && problem ? `The ${look} look is not fully applied: ${problem}.` : 'The look is not fully applied to the model yet.'
  },
  // Start from your own images (the backend writes these sentences; these are the fallback).
  one_subject: (v, _l, ok, _ctx, vars) =>
    ok ? 'The picture shows one subject.' : vars.empty ? 'We could not find a subject in the picture; the background may be too busy to cut out.' : `The picture shows ${n0(v)} separate things; the 3D step works best with one subject on its own.`,
  in_frame: (_v, _l, ok, _ctx, vars) =>
    ok ? 'The whole subject is in the picture, with space around it.' : `The subject touches the ${str(vars.sides_text) || listText(vars.sides) || 'edge'} edge of the picture, so part of it may be cut off and the 3D model would miss it.`,
  big_enough: (v, l, ok, _ctx, vars) =>
    ok ? `The subject is ${n0(v)} pixels ${str(vars.measure) || 'tall'}, big enough for the 3D step.` : `The subject is only ${n0(v)} pixels ${str(vars.measure) || 'tall'}; at least ${n0(l)} keeps the detail in the 3D model.`,
  arms_away: (_v, _l, ok) =>
    ok ? 'The arms stand away from the body, so the skeleton step can tell them apart.' : 'The arms are close to the body; the 3D model may join them to the sides and the skeleton step may not tell them apart.',
  even_lighting: (v, _l, ok) =>
    ok ? 'The light is even across the photo.' : `One side of the photo is much brighter than the other (${pct(v)} difference), so the repeating texture will show stripes of light and dark. Take it in even, cloudy-day light.`,
  straight_on: (_v, _l, ok) =>
    ok ? 'The photo looks taken straight on.' : 'The detail is much finer on one side of the photo than the other, which usually means it was taken at an angle; the texture would look stretched. Take it straight on.',
  // Mock-era names
  'single subject': (_v, _l, ok) => (ok ? 'The picture shows one subject.' : 'The picture shows more than one subject.'),
  'rig-ready pose': (_v, _l, ok) => (ok ? 'The pose is ready for a skeleton.' : 'The pose will not rig well.'),
  'plain background': (_v, _l, ok) => (ok ? 'The background is plain.' : 'The background is busy; the cutout may be rough.'),
}

// grid_fit's measured size: value [w, d, h], else vars.w, vars.d, vars.h.
function gridDims(v: unknown, vars: Record<string, unknown>): [unknown, unknown, unknown] {
  if (Array.isArray(v) && v.length >= 3) return [v[0], v[1], v[2]]
  return [vars.w, vars.d, vars.h]
}
// Which side failed: 0 width, 1 depth, 2 height (an index, or a name such as "w", "depth" or "height").
function sideIndex(side: unknown): number {
  if (typeof side === 'number' && side >= 0 && side <= 2) return Math.floor(side)
  const s = str(side).toLowerCase()
  if (s.startsWith('d') || s === 'y') return 1
  if (s.startsWith('h') || s.startsWith('t') || s === 'z') return 2
  return 0
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
  library_clips: 'clips_source',
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
  collider_triangles: 'collider_triangles',
  collider_count: 'collider_count',
  openings_clear: 'openings_clear',
  bake_matches: 'bake',
  normal_map_upright: 'normal_map',
  texture_padding: 'padding',
  atlas_used: 'bake',
  baked_light: 'baked_light',
  albedo_range: 'pbr_range',
  metal_mixed: 'pbr_range',
  unity_import: 'unity_import',
  unity_humanoid_avatar: 'unity_humanoid_avatar',
  silhouette_iou: 'silhouette_iou',
  coverage: 'coverage',
  height_ratio: 'height_ratio',
  hist_dist: 'hist_dist',
  mirror_iou: 'mirror_iou',
  grid_fit: 'grid',
  tiles_cleanly: 'grid',
  look_applied: 'look_applied',
  one_subject: 'one_subject',
  in_frame: 'in_frame',
  big_enough: 'big_enough',
  arms_away: 'arms_away',
  hair_off_eyes: 'hair_off_eyes',
  face_drawn: 'face_drawn',
  bald_picture: 'bald_picture',
  hair_fit: 'hair_library',
  hair_skin_through: 'scalp_cap',
  hair_triangles: 'hair_library',
  hair_bound: 'spring_bones',
  springs: 'springs',
  springs_shipped: 'springs',
  even_lighting: 'even_lighting',
  straight_on: 'straight_on',
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
  if (/^(texture_.+_budget|lod\d+_ratio|height_ratio|hist_dist|coverage|mirror_iou|clips|legs_moved|wings_found|hitbox_.+|movement_shape|collider_triangles|collider_count|openings_clear|bake_matches|normal_map_upright|texture_padding|atlas_used|face_matches_picture|hair_matches_picture|hair_material|hair_material_shipped|baked_light|albedo_range|metal_mixed|look_applied|one_subject|in_frame|big_enough|arms_away|hair_off_eyes|face_drawn|even_lighting|straight_on|clips_face_forward|bald_picture|hair_fit|hair_skin_through|hair_triangles|hair_bound)$/.test(id)) return 'warn'
  return 'block'
}

// A Fix button's action. Without `ui` it re-runs `stage` with `params`. With `ui` it opens a tool instead (2.5):
// the page goes to `stage`, opens "Change something" and focuses the tool or the Fine-tune `knob`, with `params`
// filled in but nothing run until the person applies it.
// With op "refine" it runs the refine on the version the check is on with `instruction` ("Redraw it ready for 3D").
export type FixAction = { stage: StageId; params: Record<string, unknown>; label: string; ui?: string; knob?: string; op?: 'refine' | 'upscale'; instruction?: string }

export const REDRAW_LABEL = 'Redraw it ready for 3D'

// The step and the Fine-tune setting each tool lives in.
export const FIX_TOOLS: Record<string, { stage: StageId; knob?: string; plain: string }> = {
  front_picker: { stage: 'prep', knob: 'face_yaw', plain: 'the front picker' },
  parts_editor: { stage: 'rig', plain: 'the parts editor' },
  hair_picker: { stage: 'mesh', plain: 'the hairstyle picker' },
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
  textures_full_size: 'Rebuild for Unity',
  clips_loop: 'Rebuild for Unity',
  clips_face_forward: 'Rebuild for Unity',
  controller_ready: 'Rebuild for Unity',
  unity_humanoid_avatar: 'Fit the standard skeleton again',
  clips: 'Make the moves again',
  legs_moved: 'Make the moves again',
  hitbox_cover: 'Fit them looser',
  hitbox_overreach: 'Fit them snugger',
  hitbox_fit: 'Try again',
  movement_shape: 'Add a body shape',
  collider_triangles: 'Simplify the collision shape',
  collider_count: 'Use fewer shapes',
  openings_clear: 'Use several shapes',
  bake_matches: 'Repaint the surface',
  normal_map_upright: 'Build the 3D model again',
  texture_matches_picture: 'Build it again',
  texture_padding: 'Add more space',
  atlas_used: 'Repaint the surface',
  face_matches_picture: 'Paint the face again',
  hair_matches_picture: 'Paint the hair again',
  hair_material: 'Give the hair its own material again',
  hair_material_shipped: 'Make the files again',
  springs: 'Make the spring bones again',
  springs_shipped: 'Make the files again',
  baked_light: 'Remove painted-on shadows',
  albedo_range: 'Bring colours into range',
  metal_mixed: 'Make metal clearly metal',
  grid_fit: 'Snap to the grid',
  look_applied: 'Apply the look again',
  bald_picture: 'Make the bald picture again',
  hair_fit: 'Use the hair built with the 3D model',
  hair_skin_through: 'Fit the hair again with more cover',
  hair_triangles: 'Choose a lighter hairstyle',
  hair_bound: 'Make the moves again',
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
  // "Upscale it" (detail_size): a bigger copy of the step 1 picture (POST /api/versions/{vid}/upscale).
  if (f && f.op === 'upscale') return { stage: 'concept', params: {}, label, op: 'upscale' }
  if (f && f.op === 'refine' && f.instruction && f.stage && STAGES.includes(f.stage as StageId))
    return { stage: f.stage as StageId, params: f.params ?? {}, label, op: 'refine', instruction: f.instruction }
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
  // Collision and surface checks (3.4, 3.9): the same fixes the backend sends, for payloads without one.
  const vars = check.vars ?? {}
  if (id === 'collider_triangles') return { stage: 'export', params: {}, label }
  if (id === 'collider_count' && limit) return { stage: 'export', params: { collider_max_parts: limit }, label }
  if (id === 'openings_clear') return { stage: 'export', params: { collider: vars.category === 'environment' ? 'mesh' : 'decompose' }, label }
  if (id === 'bake_matches' || id === 'atlas_used') return { stage: 'prep', params: { rebake: true }, label }
  if (id === 'normal_map_upright') return vars.stage === 'prep' ? { stage: 'prep', params: {}, label: check.fix?.label || 'Clean up again' } : { stage: 'mesh', params: {}, label }
  if (id === 'texture_matches_picture') return { stage: 'mesh', params: { seed: null }, label }
  if (id === 'baked_light' && !vars.removed) return { stage: 'prep', params: { delight: 'remove' }, label }
  if ((id === 'albedo_range' || id === 'metal_mixed') && !vars.pbr_fix_on) return { stage: 'prep', params: { pbr_fix: true }, label }
  if (id === 'look_applied') return { stage: 'prep', params: {}, label }
  if (id === 'face_matches_picture' && vars.reason === 'eye_under_hair') return { stage: 'mesh', params: {}, label: check.fix?.label || 'Build the 3D model again' }
  if (id === 'face_matches_picture') return { stage: 'prep', params: { face_detail: true, face_seed: null, face_closeup_seed: null, face_closeup_seed_2: null }, label }
  if (id === 'hair_matches_picture') return { stage: 'prep', params: { hair_detail: true }, label }
  if (id === 'hair_material') return { stage: 'prep', params: { hair_material: 'on' }, label }
  if (id === 'hair_material_shipped') return { stage: 'export', params: {}, label }
  if (id === 'bald_picture') return { stage: 'turnaround', params: { bald_only: true, bald_seed: null }, label }
  if (id === 'hair_fit') return { stage: 'mesh', params: { hair_source: 'model' }, label }
  if (id === 'hair_skin_through') return { stage: 'prep', params: { hair_cover: 'more' }, label }
  if (id === 'hair_triangles') return { stage: 'mesh', params: {}, label, ui: 'hair_picker' }
  if (id === 'hair_bound') return { stage: 'rig', params: {}, label }
  if (id === 'springs') return { stage: 'rig', params: { springs: true }, label }
  if (id === 'springs_shipped') return { stage: 'export', params: {}, label }
  return null
}

// The Fix button's tooltip: what pressing it does, in words (never the raw params).
export function fixTooltip(fix: FixAction, stages?: StageInfo[] | null): string {
  const name = stagePlain(fix.stage, stages?.find((s) => s.id === fix.stage) ?? null)
  if (fix.ui === 'hair_picker') return `Opens the hairstyle picker in "${name}". Nothing changes until you pick a style.`
  if (fix.ui) return `Opens ${FIX_TOOLS[fix.ui]?.plain ?? 'the tool'} in "${name}". Nothing changes until you apply it.`
  if (fix.stage === 'mesh' && fix.params.hair_source === 'model') return 'Builds the 3D model again with the hair built in, as without the library. The bald build stays in the history.'
  if (fix.stage === 'turnaround' && fix.params.bald_only) return 'Draws the bald pictures again with a new seed and keeps every other view. It makes a new version; this one stays.'
  if (fix.op === 'refine' && fix.label === 'Move the hair off the face') return 'Asks the picture editor to move the hair away from the eyes and keep everything else the same. It makes a new version; this one stays.'
  if (fix.op === 'refine') return 'Asks the picture editor to redraw the same subject in the standard pose on a plain background. It makes a new version; this one stays.'
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

// Setup check (GET /api/setup): plain names of the steps a problem holds up, and the Info entry of one check.
const SETUP_STAGE_EXTRA: Record<string, string> = { judge: 'Review', material: 'Materials' }

export function setupStagePlain(stage: string): string {
  return STAGES.includes(stage as StageId) ? STAGE_TEXT[stage as StageId].plain : SETUP_STAGE_EXTRA[stage] ?? stage
}

export function setupItemHelp(item: SetupItem): HelpEntry {
  const group = TERM_HELP[`setup_group_${item.group}`]
  const steps = item.needed_for.map(setupStagePlain)
  const why = steps.length ? `Needed for: ${steps.join(', ')}.` : group?.why ?? ''
  return H(item.label, group?.meaning ?? item.detail, why, item.technical || group?.technical || item.id)
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
  turnaround: ['views', 'turn_method'],
  mesh: ['engine', 'target_faces', 'head_build'],
  prep: ['size_m', 'height_m', 'grid_footprint', 'grid_height', 'face_yaw', 'symmetric', 'delight', 'face_detail', 'hair_detail'],
  rig: ['template', 'motion', 'moves', 'clips_source', 'hitboxes', 'hitbox_fit'],
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
  upsample: { '1024': 'Normal', '1536': 'Finer (about 2 to 3 times slower)' },
  tex_steps: { '12': 'Quicker, flatter colours', '24': 'Richer detail (recommended)' },
  pivot: { base: 'At the bottom (stands on the ground)', center: 'In the middle (floats or spins)' },
  template: { auto: 'Automatic (from the category)', mixamo: 'Standard human skeleton', generic: 'Fitted to the shape', none: 'No skeleton' },
  formats: { fbx: 'FBX (Unity, Unreal, Godot)', glb: 'GLB (web, Godot, Blender)' },
  clips_library: { ual1: 'Standard human moves (46 animations)', ual2: 'Extra human moves (second library)', none: 'Only its own walk and idle' },
  views: { front: 'Front', left: 'Left side', back: 'Back', right: 'Right side', quarter: 'Three-quarter', quarter2: 'Three-quarter (other side)', top: 'Top' },
  turn_method: { standard: 'Standard', sheet: 'All sides in one picture', shadow_free: 'Shadow-free on white' },
  collider: { auto: 'Best fit (recommended)', hull: 'One wrapped shape', decompose: 'Several shapes, for hollow things', mesh: 'Exact shape (only for scenery that never moves)', none: 'None' },
  delight: { auto: 'Automatic (by look)', remove: 'Remove them', keep: 'Keep them (part of the look)' },
  face_density: { auto: 'Automatic (by platform and picture)', standard: 'Standard', high: 'High' },
  head_sway: { auto: 'Automatic (hair; a headscarf or ribbon if described)', hair: 'Hair only', all: 'Everything that hangs (as before)' },
  shading: { auto: 'Automatic (by look)', smooth: 'Smooth', flat: 'Flat faces' },
  face_yaw: { auto: 'Automatic', '0': 'As built', '90': 'Turned a quarter to the left', '180': 'Turned around', '270': 'Turned a quarter to the right' },
  motion: { legs: 'Walks on its legs', wings: 'Flies, and walks when it lands', wave: 'Slithers or swims', none: 'Does not move by itself' },
  clips_source: { auto: 'Automatic', library: 'Hand-made library', procedural: 'Built-in moves' },
  moves: { idle: 'Standing still', walk: 'Walk', run: 'Run', turn: 'Turn on the spot', attack: 'Attack', hit: 'Get hit', death: 'Die', jump: 'Jump', crouch: 'Crouch', roll: 'Roll', interact: 'Use or pick up', flap: 'Fly', glide: 'Glide', takeoff: 'Take off', land: 'Land', slither: 'Slither or swim' },
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
  grid_footprint: { auto: 'Automatic (nearest fit)', '1x1': '1 x 1 squares', '2x1': '2 x 1 squares', '2x2': '2 x 2 squares', '4x1': '4 x 1 squares', '4x4': '4 x 4 squares' },
  grid_height: { auto: 'Automatic', '0.5': 'Floor (0.5 m)', '1': 'Low (1 m)', '3': 'Wall (3 m)', '4': 'Tall (4 m)' },
  grid_m: { '0.5': '0.5 m', '1': '1 m (standard)', '2': '2 m', '4': '4 m' },
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
  sprint: 'sprint',
  punch: 'punch',
  jump: 'jump',
  jump_start: 'jump',
  jump_loop: 'jump',
  jump_land: 'jump',
  crouch: 'crouch',
  crouch_idle: 'crouch',
  crouch_walk: 'crouch',
  roll: 'roll',
  interact: 'use or pick up',
  flap: 'fly',
  glide: 'glide',
  takeoff: 'take off',
  land: 'land',
  slither: 'slither',
}

// The moves a creature can make, by how it moves (the category yaml's clips, with turn as one move).
export const MOVES_BY_MOTION: Record<string, string[]> = {
  legs: ['idle', 'walk', 'run', 'turn', 'attack', 'hit', 'death'],
  // Human-shaped characters also get the hand-made library's extra moves.
  human: ['idle', 'walk', 'run', 'turn', 'attack', 'hit', 'death', 'jump', 'crouch', 'roll', 'interact'],
  wings: ['idle', 'walk', 'flap', 'glide', 'takeoff', 'land', 'attack', 'hit', 'death'],
  wave: ['idle', 'slither', 'turn', 'attack', 'hit', 'death'],
  none: [],
}
// How each category moves when the motion knob is left empty (the category yaml's motion).
export const MOTION_BY_CATEGORY: Record<string, string> = { humanoid: 'legs', quadruped: 'legs', multileg: 'legs', winged: 'wings', serpent: 'wave' }

// Clips that loop when the sidecar does not say (gait.py LOOPING); every other procedural clip plays once.
const LOOPING_CLIPS = /^(idle|walk|run|sprint|jump_loop|crouch_idle|crouch_walk|slither|flap|glide)$/i
const ONE_SHOT_CLIPS = /^(turn_left|turn_right|attack|punch|hit|death|jump_start|jump_land|roll|interact|takeoff|land)$/i
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
const CLIP_LABELS: Record<string, string> = {
  idle: 'Standing still',
  attack: 'Sword attack',
  jump_start: 'Jump (take off)',
  jump_loop: 'Jump (in the air)',
  jump_land: 'Jump (land)',
  crouch_idle: 'Crouch',
  crouch_walk: 'Crouch walk',
  interact: 'Use or pick up',
}

// "Moves: 15 hand-made (free library) and 2 built in." from the rig version's metrics; '' when it does not say.
export function movesSourceLine(metrics: Record<string, unknown>): string {
  // Moves from the person's own files ("Add a move", metrics.moves source "mine") are counted on their own.
  const mine = Array.isArray(metrics.moves) ? metrics.moves.filter((m) => (m as { source?: string; made?: boolean })?.source === 'mine' && (m as { made?: boolean }).made !== false).length : 0
  const lib = (Array.isArray(metrics.library_clips) ? metrics.library_clips.length : 0) - mine
  const proc = Array.isArray(metrics.procedural_clips) ? metrics.procedural_clips.length : 0
  const yours = mine ? `, ${mine} from your own file${mine > 1 ? 's' : ''}` : ''
  if (metrics.clips_source === 'library' && lib + mine > 0) return `Where they come from: ${lib} hand-made (a free library, fine for commercial games)${yours}${proc ? ` and ${proc} built in` : ''}.`
  if (metrics.clips_source === 'procedural') return 'Where they come from: built in, made for this skeleton.'
  return ''
}

export function clipLabel(name: string, humanLibrary = false): string {
  const k = name.toLowerCase()
  if (CLIP_LABELS[k] && (humanLibrary || k !== 'attack')) return CLIP_LABELS[k]
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
  environment: { axis: 'grid', size_m: 3.0, label: 'Size on the grid', hint: 'Rounded to whole grid squares; pick a footprint and a height below.', word: 'across' },
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
  // Scenery on a grid is read with gridReadout instead.
  if (axis === 'grid') return null
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

// Scenery on a grid (prep metrics grid_cells, grid_size_m, grid_m): "On the grid: 3 x 1 squares, 3 m tall".
// Cells come from grid_cells, else grid_footprint_cells ("3x1"), else the size divided by the grid. Null when
// the version says nothing about the grid.
export function gridReadout(metrics: Record<string, unknown> | null | undefined): string | null {
  if (!metrics) return null
  const g = num(metrics.grid_m)
  const sz = Array.isArray(metrics.grid_size_m) ? metrics.grid_size_m.map(num) : []
  let cells: number[] | null = null
  if (Array.isArray(metrics.grid_cells) && metrics.grid_cells.length >= 2 && metrics.grid_cells.every((c) => num(c) !== null)) cells = (metrics.grid_cells as number[]).slice(0, 2)
  else if (typeof metrics.grid_footprint_cells === 'string' && /^\d+x\d+$/.test(metrics.grid_footprint_cells)) cells = metrics.grid_footprint_cells.split('x').map(Number)
  else if (g && sz[0] !== null && sz[0] !== undefined && sz[1] !== null && sz[1] !== undefined) cells = [Math.max(1, Math.round(sz[0] / g)), Math.max(1, Math.round(sz[1] / g))]
  const h = num(metrics.grid_height_m) ?? sz[2] ?? null
  if (!cells && h === null) return null
  const parts: string[] = []
  if (cells) parts.push(`${cells[0]} x ${cells[1]} squares`)
  if (h !== null) parts.push(`${metres(h)} m tall`)
  return `On the grid: ${parts.join(', ')}`
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
