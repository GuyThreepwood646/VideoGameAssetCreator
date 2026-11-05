// Sample data for the mock backend: reference tables, one project with a fully
// staged asset, and the fabricators that fill in files and metrics for new versions.
import type { Asset, Category, Job, LookPreset, Platform, Project, SetupItem, SetupReport, StageId, StageInfo, StylePreset, Version } from '../api'
import { CONCEPT_CATALOG, LOOK_OF, lookSummary, mockRecipe, recipeRecord } from './recipes'
import { makeBendStrip, makeImage, makeSpriteSheet, makeTexture, makeTiling, type Figure, type ViewName } from './images'

const RIGGED_IDS = ['humanoid', 'quadruped', 'multileg', 'winged', 'serpent'] as const
export const files = new Map<string, string>() // library-relative path -> data URL

let counter = 1000
export function uid(prefix = ''): string {
  counter += 1
  const hex = counter.toString(16).padStart(4, '0')
  return `${prefix}${hex}-mock-4000-8000-${Date.now().toString(16).slice(-12).padStart(12, '0')}`
}

export function nowIso(offsetMs = 0): string {
  return new Date(Date.now() + offsetMs).toISOString()
}

function putDataFile(path: string, dataUrl: string): string {
  files.set(path, dataUrl)
  return path
}

function putText(path: string, text: string, mime = 'application/json'): string {
  files.set(path, `data:${mime};charset=utf-8,` + encodeURIComponent(text))
  return path
}

export const categories: Category[] = [
  {
    id: 'humanoid',
    label: 'Humanoid',
    rig_engine: 'make_it_animatable',
    symmetric: true,
    pose_block: 'full body, A-pose, arms slightly away from the body, neutral expression, facing camera',
    size_m: 1.8,
    size_axis: 'height',
    size_label: 'How tall is it?',
    size_hint: 'In metres, from the ground to the top of the head. A person is about 1.8 m.',
    parts_default: ['weapon', 'shield'],
    budgets: { mobile_low: { triangles: 8000, texture: 1024 }, pc_mid: { triangles: 30000, texture: 2048 }, high: { triangles: 80000, texture: 4096 } },
  },
  {
    id: 'quadruped',
    label: 'Quadruped',
    rig_engine: 'skintoken',
    symmetric: true,
    pose_block: 'full body, standing neutral, side three-quarter view, all four legs visible',
    size_m: 1.5,
    size_axis: 'horizontal',
    size_label: 'How long is it?',
    size_hint: 'In metres, from the nose to the tip of the tail. A wolf is about 1.8 m; a horse about 3 m.',
    size_word: 'long',
    parts_default: [],
    budgets: { mobile_low: { triangles: 6000, texture: 1024 }, pc_mid: { triangles: 24000, texture: 2048 }, high: { triangles: 60000, texture: 4096 } },
  },
  {
    id: 'winged',
    label: 'Winged',
    rig_engine: 'skintoken',
    symmetric: true,
    pose_block: 'full body, wings spread, standing, side three-quarter view',
    size_m: 1.5,
    size_axis: 'horizontal',
    size_label: 'How wide are the wings?',
    size_hint: 'In metres, wing tip to wing tip. A hawk is about 1.2 m; a horse-sized dragon about 8 m.',
    size_word: 'across',
    parts_default: [],
    budgets: { mobile_low: { triangles: 6000, texture: 1024 }, pc_mid: { triangles: 24000, texture: 2048 }, high: { triangles: 60000, texture: 4096 } },
  },
  {
    id: 'vehicle',
    label: 'Vehicle / mech',
    rig_engine: 'hinge',
    symmetric: true,
    pose_block: 'full vehicle, three-quarter view, wheels on the ground, no background',
    size_m: 4.5,
    size_axis: 'horizontal',
    size_label: 'How long is it?',
    size_hint: 'In metres, front to back. A car is about 4.5 m; a tank about 7 m.',
    size_word: 'long',
    parts_default: ['wheel_fl', 'wheel_fr', 'wheel_rl', 'wheel_rr', 'turret'],
    budgets: { mobile_low: { triangles: 5000, texture: 1024 }, pc_mid: { triangles: 20000, texture: 2048 }, high: { triangles: 50000, texture: 4096 } },
  },
  {
    id: 'prop_rigid',
    label: 'Prop (rigid)',
    rig_engine: 'none',
    symmetric: false,
    pose_block: 'single object, three-quarter view, resting on a flat surface',
    size_m: 0.6,
    size_axis: 'longest',
    size_label: 'How big is it?',
    size_hint: 'In metres, along its longest side. A barrel is about 0.9 m tall.',
    size_word: 'across',
    parts_default: [],
    budgets: { mobile_low: { triangles: 800, texture: 512 }, pc_mid: { triangles: 3000, texture: 1024 }, high: { triangles: 10000, texture: 2048 } },
  },
  {
    id: 'prop_articulated',
    label: 'Prop (articulated)',
    rig_engine: 'hinge',
    symmetric: true,
    pose_block: 'single object, three-quarter view, closed state, resting on a flat surface',
    size_m: 0.8,
    size_axis: 'longest',
    size_label: 'How big is it?',
    size_hint: 'In metres, along its longest side. A chest is about 0.9 m wide; a door about 2.1 m tall.',
    size_word: 'across',
    parts_default: ['lid'],
    budgets: { mobile_low: { triangles: 1200, texture: 512 }, pc_mid: { triangles: 5000, texture: 1024 }, high: { triangles: 15000, texture: 2048 } },
  },
  {
    id: 'environment',
    label: 'Environment piece',
    rig_engine: 'none',
    symmetric: false,
    pose_block: 'modular piece, orthographic three-quarter view, grid aligned',
    size_m: 3.0,
    size_axis: 'grid',
    size_word: 'across',
    size_label: 'Size on the grid',
    size_hint: 'Rounded to whole grid squares; pick a footprint and a height below.',
    parts_default: [],
    budgets: { mobile_low: { triangles: 1500, texture: 1024 }, pc_mid: { triangles: 6000, texture: 2048 }, high: { triangles: 20000, texture: 4096 } },
  },
]

export const styles: StylePreset[] = [
  { id: 'painterly', label: 'Hand-painted stylised', model: 'zturbo', prompt_block: 'hand-painted stylised fantasy, chunky proportions, soft rim light, clean silhouette' },
  { id: 'realistic', label: 'Realistic PBR', model: 'zbase', prompt_block: 'photoreal game asset, neutral studio lighting, physically based materials' },
  { id: 'pixel', label: 'Pixel art', model: 'pixel', prompt_block: 'pixel art sprite, 32 colour palette, crisp edges' },
  { id: 'toon', label: 'Toon shaded', model: 'klein', prompt_block: 'cel shaded, bold outlines, flat colour regions, saturated palette' },
  { id: 'cel', label: 'Cel shaded (anime)', model: 'anima', prompt_block: 'anime cel shading, clean line art, flat colour regions, soft gradients' },
  { id: 'retro64', label: 'Retro 64', model: 'zturbo', prompt_block: 'late 1990s low-polygon console style, chunky simple shapes, small blurry textures' },
  { id: 'lowpoly_flat', label: 'Low-poly flat', model: 'zturbo', prompt_block: 'low-poly flat-shaded style, faceted geometric surfaces, solid flat colour per face' },
]

// Ready-made looks: GET /api/style-presets. Applying one means PUT /api/projects/{id}
// with the style_lock (plus "preset": id) and defaulting new assets to style_preset.
export const lookPresets: LookPreset[] = [
  {
    id: 'painted_fantasy',
    label: 'Painted fantasy',
    description: 'Hand-painted, chunky shapes, warm light. The safe choice for fantasy and casual games.',
    style_preset: 'painterly',
    style_lock: {
      prompt_block: 'hand-painted stylised fantasy, chunky proportions, soft rim light, clean silhouette, muted earth tones with one accent colour',
      model: 'zturbo',
      loras: [],
      palette: ['#3b2f2f', '#c9a24a', '#6b8e7b', '#e8dcc2', '#8c2f2f'],
      negative: 'photorealistic, text, watermark, blurry, extra limbs, cropped',
    },
    example_prompt: 'A sturdy young knight in worn plate armour, short dark hair, no helmet',
  },
  {
    id: 'clean_cartoon',
    label: 'Clean cartoon',
    description: 'Bold outlines, flat bright colours, simple shapes. Reads well on small screens.',
    style_preset: 'toon',
    style_lock: {
      prompt_block: 'clean cartoon style, bold outlines, flat colour regions, saturated palette, simple readable shapes',
      model: 'klein',
      loras: [],
      palette: ['#ff6b35', '#ffd166', '#06d6a0', '#118ab2', '#073b4c'],
      negative: 'photorealistic, gradients, noise, text, watermark, blurry',
    },
    example_prompt: 'A round cheerful robot vendor with a tray of snacks and one wobbly antenna',
  },
  {
    id: 'anime_cel',
    label: 'Anime cel',
    description: 'Clean line art with cel shading, as in Japanese animation.',
    style_preset: 'cel',
    style_lock: {
      prompt_block: 'anime cel shading, clean line art, flat colour regions, soft gradients, expressive eyes',
      model: 'anima',
      loras: [],
      palette: ['#2b2d42', '#8d99ae', '#edf2f4', '#ef233c', '#d90429'],
      negative: 'photorealistic, 3d render, text, watermark, blurry, extra limbs',
    },
    example_prompt: 'A lanky swordswoman in a school uniform with a long red scarf, calm expression',
  },
  {
    id: 'pixel_retro',
    label: 'Pixel retro',
    description: 'Crisp pixels and a small palette for 2D retro games; sprites come from the finished model.',
    style_preset: 'pixel',
    style_lock: {
      prompt_block: 'pixel art sprite, 32 colour palette, crisp edges, no anti-aliasing, 16-bit console look',
      model: 'pixel',
      loras: [],
      palette: ['#1a1c2c', '#5d275d', '#b13e53', '#ef7d57', '#ffcd75', '#38b764', '#3b5dc9', '#f4f4f4'],
      negative: 'photorealistic, smooth shading, blur, text, watermark',
    },
    example_prompt: 'A small green slime with two button eyes and a crown, bouncing',
  },
  {
    id: 'realistic',
    label: 'Realistic',
    description: 'Photoreal materials and proportions. Slower to make; best for PC and showcase.',
    style_preset: 'realistic',
    style_lock: {
      prompt_block: 'photoreal game asset, neutral studio lighting, physically based materials, fine surface detail',
      model: 'zbase',
      loras: [],
      palette: ['#2e2e2e', '#7a6f5d', '#b8b2a7', '#4a5a6a', '#8c6d3f'],
      negative: 'cartoon, painterly, text, watermark, blurry, extra limbs, cropped',
    },
    example_prompt: 'A weathered oak barrel with rusted iron bands and a cracked stave',
  },
]

export const platforms: Platform[] = [
  { id: 'mobile_low', label: 'Mobile (low)', triangles_character: 8000, triangles_prop: 800, texture: 1024 },
  { id: 'pc_mid', label: 'PC (mid)', triangles_character: 30000, triangles_prop: 3000, texture: 2048 },
  { id: 'high', label: 'High end', triangles_character: 80000, triangles_prop: 10000, texture: 4096 },
]

export const stages: StageInfo[] = [
  {
    id: 'concept',
    label: 'Concept',
    estimate_seconds: 25,
    adjust_schema: [
      { name: 'model', label: 'Model', type: 'select', options: ['zturbo', 'zbase', 'klein', 'anima', 'pixel', 'chroma'], default: 'zturbo' },
      { name: 'seed', label: 'Seed', type: 'integer', min: 0, max: 2147483647, step: 1, default: 1234 },
      { name: 'width', label: 'Width', type: 'integer', min: 512, max: 2048, step: 64, default: 1024 },
      { name: 'height', label: 'Height', type: 'integer', min: 512, max: 2048, step: 64, default: 1024 },
      { name: 'steps', label: 'Steps', type: 'integer', min: 4, max: 50, step: 1, default: 8 },
      { name: 'readiness_block', label: 'Pipeline-readiness prompt block', type: 'boolean', default: true },
      { name: 'prompt_override', label: 'Prompt override', type: 'string', default: '' },
      // As the backend serves it: the concept add-ons in `catalog`; the value is {id: off | subtle | normal | strong}.
      { name: 'addons', label: 'Add-ons', type: 'addons', default: null, options: ['off', 'subtle', 'normal', 'strong'], description: 'The recipe and the style lock decide when empty', catalog: CONCEPT_CATALOG },
    ],
  },
  {
    id: 'turnaround',
    label: 'Turnaround',
    estimate_seconds: 110,
    adjust_schema: [
      { name: 'views', label: 'Views', type: 'multiselect', options: ['front', 'left', 'back', 'right', 'quarter', 'top'], default: ['front', 'left', 'back', 'right', 'quarter'] },
      { name: 'lora_strength', label: 'Multi-angle LoRA strength', type: 'number', min: 0, max: 1.5, step: 0.05, default: 0.9 },
      { name: 'seed', label: 'Seed', type: 'integer', min: 0, max: 2147483647, step: 1, default: 77 },
      { name: 'auto_reroll', label: 'Re-roll failing views (twice)', type: 'boolean', default: true },
    ],
  },
  {
    id: 'mesh',
    label: 'Mesh',
    estimate_seconds: 200,
    adjust_schema: [
      { name: 'engine', label: 'Engine', type: 'select', options: ['trellis', 'hy3d'], default: 'trellis' },
      { name: 'input_view', label: 'Input view', type: 'select', options: ['concept', 'front', 'quarter'], default: 'quarter' },
      { name: 'target_faces', label: 'Target faces (dense)', type: 'integer', min: 50000, max: 2000000, step: 10000, default: 500000 },
      { name: 'texture_size', label: 'Texture size', type: 'select', options: [1024, 2048, 4096], default: 2048 },
      { name: 'upsample', label: 'Upsample', type: 'select', options: [1024, 1536], default: 1024 },
      { name: 'seed', label: 'Seed', type: 'integer', min: 0, max: 2147483647, step: 1, default: 5 },
      { name: 'remesh', label: 'Remesh to watertight', type: 'boolean', default: true },
      // 3.9: seam space around texture pieces; Advanced (not in SIMPLE_PARAMS), empty means automatic.
      { name: 'padding', label: 'Space around texture pieces (pixels)', type: 'integer', min: 1, max: 16, step: 1, default: null },
    ],
  },
  {
    id: 'prep',
    label: 'Prep',
    adjust_schema: [
      // 3.2: size_m replaces height_m (read as an alias); null means the category's size. Not for scenery or materials.
      {
        name: 'size_m',
        label: 'Size (m)',
        type: 'number',
        min: 0.01,
        max: 100,
        step: 0.05,
        default: 1.8,
        // Scenery snaps to the grid instead (grid_footprint, grid_height, grid_m).
        categories: ['humanoid', 'quadruped', 'winged', 'multileg', 'serpent', 'vehicle', 'prop_rigid', 'prop_articulated', 'foliage'],
        // As the backend serves it (3.2): axis, default, word, question and hint per category.
        by_category: Object.fromEntries(
          categories
            .filter((c) => c.size_axis && c.size_axis !== 'grid')
            .map((c) => [c.id, { axis: c.size_axis!, default: c.size_m, word: wordOf(c.id), label: c.size_label ?? 'How big is it?', hint: c.size_hint ?? 'In metres.' }]),
        ),
      },
      {
        name: 'face_yaw',
        label: 'Which way is the front?',
        type: 'select',
        options: ['auto', 0, 90, 180, 270],
        default: 'auto',
        option_labels: { auto: 'Automatic', '0': 'As built', '90': 'Turned a quarter to the left', '180': 'Turned around', '270': 'Turned a quarter to the right' },
        hint: 'Pick the picture where the front faces you. The game treats that side as forward.',
        categories: ['humanoid', 'quadruped', 'winged', 'multileg', 'serpent', 'vehicle', 'prop_rigid', 'prop_articulated', 'environment', 'foliage'],
      },
      { name: 'symmetric', label: 'Mirror the halves', type: 'boolean', default: false, categories: ['humanoid', 'quadruped', 'winged', 'multileg', 'serpent', 'vehicle', 'prop_rigid', 'prop_articulated', 'environment', 'foliage'] },
      // 3.9: painted-on shadows, colour range and repainting. No option_labels, so help.ts OPTION_LABELS shows.
      { name: 'delight', label: 'Painted-on shadows', type: 'select', options: ['auto', 'remove', 'keep'], default: 'auto', categories: ['humanoid', 'quadruped', 'winged', 'multileg', 'serpent', 'vehicle', 'prop_rigid', 'prop_articulated', 'environment', 'foliage'] },
      { name: 'pbr_fix', label: 'Keep colours in a realistic range', type: 'boolean', default: null, categories: ['humanoid', 'quadruped', 'winged', 'multileg', 'serpent', 'vehicle', 'prop_rigid', 'prop_articulated', 'environment', 'foliage'] },
      // Recipes: auto follows the look (flat faces for low-poly). No option_labels, so help.ts OPTION_LABELS shows.
      { name: 'shading', label: 'Shading', type: 'select', options: ['auto', 'smooth', 'flat'], default: 'auto', categories: ['humanoid', 'quadruped', 'winged', 'multileg', 'serpent', 'vehicle', 'prop_rigid', 'prop_articulated', 'environment', 'foliage'] },
      { name: 'rebake', label: 'Paint the surface again', type: 'boolean', default: false, categories: ['humanoid', 'quadruped', 'winged', 'multileg', 'serpent', 'vehicle', 'prop_rigid', 'prop_articulated', 'environment', 'foliage'] },
      // This one carries the backend's option_labels (2.2); the others fall back to help.ts OPTION_LABELS.
      // Scenery always gets the corner anchor, so the pivot knob is not offered for it.
      {
        name: 'pivot',
        label: 'Pivot',
        type: 'select',
        options: ['base', 'center'],
        default: 'base',
        option_labels: { base: 'At the bottom (stands on the ground)', center: 'In the middle (floats or spins)' },
        categories: ['humanoid', 'quadruped', 'winged', 'multileg', 'serpent', 'vehicle', 'prop_rigid', 'prop_articulated', 'foliage', 'material'],
      },
      // Scenery on a grid: footprint and height in grid steps, and the grid itself (Advanced).
      {
        name: 'grid_footprint',
        label: 'Size on the grid',
        type: 'select',
        options: ['auto', '1x1', '2x1', '2x2', '4x1', '4x4'],
        default: 'auto',
        option_labels: { auto: 'Automatic (nearest fit)', '1x1': '1 x 1 squares', '2x1': '2 x 1 squares', '2x2': '2 x 2 squares', '4x1': '4 x 1 squares', '4x4': '4 x 4 squares' },
        categories: ['environment'],
      },
      {
        name: 'grid_height',
        label: 'Height on the grid',
        type: 'select',
        options: ['auto', 0.5, 1, 3, 4],
        default: 'auto',
        option_labels: { auto: 'Automatic', '0.5': 'Floor (0.5 m)', '1': 'Low (1 m)', '3': 'Wall (3 m)', '4': 'Tall (4 m)' },
        categories: ['environment'],
      },
      {
        name: 'grid_m',
        label: 'Grid size',
        type: 'select',
        options: [0.5, 1, 2, 4],
        default: 1,
        option_labels: { '0.5': '0.5 m', '1': '1 m (standard)', '2': '2 m', '4': '4 m' },
        categories: ['environment'],
      },
      { name: 'smooth_angle', label: 'Smooth angle (deg)', type: 'number', min: 10, max: 90, step: 1, default: 60 },
    ],
  },
  {
    id: 'rig',
    label: 'Rig',
    adjust_schema: [
      { name: 'template', label: 'Skeleton template', type: 'select', options: ['auto', 'mixamo', 'generic', 'none'], default: 'auto' },
      { name: 'target_faces', label: 'Target faces for the rigger', type: 'integer', min: 2000, max: 100000, step: 1000, default: 20000 },
      { name: 'group_per_vertex', label: 'Influences per vertex', type: 'integer', min: 1, max: 8, step: 1, default: 4 },
      { name: 'bottom_center_origin', label: 'Bottom-centre origin', type: 'boolean', default: true },
      { name: 'bend_test', label: 'Run bend test', type: 'boolean', default: true },
      // Creature moves (3.7) and hit boxes (4.2). No option_labels here, so help.ts OPTION_LABELS shows.
      { name: 'motion', label: 'Motion', type: 'select', options: ['legs', 'wings', 'wave', 'none'], default: null, categories: ['quadruped', 'multileg', 'winged', 'serpent'] },
      { name: 'moves', label: 'Moves', type: 'multiselect', options: ['idle', 'walk', 'run', 'turn', 'attack', 'hit', 'death', 'flap', 'glide', 'takeoff', 'land', 'slither'], default: null, categories: [...RIGGED_IDS] },
      { name: 'move_size', label: 'Move size', type: 'select', options: ['small', 'normal', 'big'], default: 'normal', categories: [...RIGGED_IDS] },
      { name: 'move_speed', label: 'Move speed', type: 'select', options: ['slow', 'normal', 'fast'], default: 'normal', categories: [...RIGGED_IDS] },
      { name: 'hitboxes', label: 'Hit boxes', type: 'select', options: ['detailed', 'simple', 'none'], default: null, categories: [...RIGGED_IDS] },
      { name: 'hitbox_fit', label: 'Hit box fit', type: 'select', options: ['snug', 'normal', 'loose'], default: 'normal', categories: [...RIGGED_IDS] },
      {
        name: 'hitbox_regions',
        label: 'Hit box regions',
        type: 'multiselect',
        options: ['head', 'neck', 'chest', 'hips', 'body', 'tail', 'upper_arm_l', 'forearm_l', 'upper_arm_r', 'forearm_r', 'thigh_l', 'shin_l', 'thigh_r', 'shin_r', 'leg_fl', 'leg_fr', 'leg_bl', 'leg_br', 'leg_l', 'leg_r', 'wing_l', 'wing_r', 'wings'],
        default: null,
        categories: [...RIGGED_IDS],
      },
    ],
  },
  {
    id: 'export',
    label: 'Export',
    adjust_schema: [
      { name: 'lods', label: 'LOD ratios', type: 'string', default: '1.0, 0.5, 0.25, 0.1' },
      // 3.4: collision by category, with the backend's option_labels.
      {
        name: 'collider',
        label: 'Collision shape',
        type: 'select',
        options: ['auto', 'hull', 'decompose', 'mesh', 'none'],
        default: 'auto',
        option_labels: { auto: 'Best fit (recommended)', hull: 'One wrapped shape', decompose: 'Several shapes, for hollow things', mesh: 'Exact shape (only for scenery that never moves)', none: 'None' },
        categories: ['prop_rigid', 'prop_articulated', 'vehicle', 'environment', 'foliage'],
      },
      { name: 'collider_max_parts', label: 'Most shapes', type: 'integer', min: 1, max: 32, step: 1, default: null, categories: ['prop_rigid', 'prop_articulated', 'vehicle', 'environment'] },
      { name: 'movement_shape', label: 'Movement shape', type: 'select', options: ['auto', 'upright', 'along', 'box', 'none'], default: 'auto', categories: [...RIGGED_IDS] },
      { name: 'formats', label: 'Formats', type: 'multiselect', options: ['fbx', 'glb'], default: ['fbx', 'glb'] },
      { name: 'unity', label: 'Build Unity prefab', type: 'boolean', default: true },
      // Humanoids only (2.3): the shared human animation libraries.
      { name: 'clips_library', label: 'Clip library', type: 'select', options: ['ual1', 'ual2', 'none'], default: 'ual1', categories: ['humanoid'] },
      { name: 'sprites', label: 'Render sprite sheet', type: 'boolean', default: false },
      { name: 'sprite_directions', label: 'Sprite directions', type: 'integer', min: 4, max: 16, step: 4, default: 8 },
      { name: 'sprite_size', label: 'Sprite size (px)', type: 'integer', min: 32, max: 256, step: 32, default: 64 },
    ],
  },
]

export const store = {
  projects: [] as Project[],
  assets: [] as Asset[],
  jobs: [] as Job[],
}

const VIEWS: ViewName[] = ['front', 'left', 'back', 'right', 'quarter']

function hueFor(asset: Asset, seed: number): number {
  let h = 0
  for (const ch of asset.name) h = (h * 31 + ch.charCodeAt(0)) % 360
  return (h + seed * 37) % 360
}

function figureFor(asset: Asset): Figure {
  return asset.category.startsWith('prop') || asset.category === 'environment' || asset.category === 'vehicle' ? 'box' : 'biped'
}

type MockCheck = {
  id: string
  name: string
  passed: boolean
  ok: boolean
  value?: unknown
  limit?: unknown
  message?: string
  help?: string
  fix?: { stage?: string; params?: Record<string, unknown>; label?: string; ui?: string; op?: 'refine'; instruction?: string }
  severity?: 'block' | 'warn'
  vars?: Record<string, unknown>
}

const chk = (id: string, ok: boolean, value: unknown, limit: unknown, extra: Partial<MockCheck> = {}): MockCheck => ({
  id,
  name: id,
  passed: ok,
  ok,
  value,
  limit,
  message: `${Array.isArray(value) ? value.join(' x ') : String(value)} (limit ${Array.isArray(limit) ? limit.join(' to ') : String(limit)})`,
  ...extra,
})

const RIGGED = /^(humanoid|quadruped|winged|multileg|serpent)$/
// Measured box per category as prep reports it: Blender [x, y, z] metres, Z up.
const MOCK_DIMS: Record<string, [number, number, number]> = {
  humanoid: [0.62, 0.35, 1.8],
  quadruped: [0.42, 1.5, 0.92],
  winged: [1.5, 0.9, 0.7],
  vehicle: [1.9, 4.5, 1.5],
  prop_rigid: [0.45, 0.45, 0.6],
  prop_articulated: [0.8, 0.5, 0.55],
  environment: [3.0, 0.4, 2.4],
}
export function clipsFor(category: string): string[] {
  if (category === 'humanoid') return ['idle', 'walk', 'run', 'jump', 'attack', 'hit', 'death']
  if (RIGGED.test(category)) return ['idle', 'walk', 'run', 'attack', 'hit', 'death']
  return []
}

// Same shape as the real backend under the 2.1 contract: help is the ok or fail sentence matching the outcome,
// a failing fix carries its label, and some checks carry vars. A few leave help out so the frontend fallback runs.
const checksFor = (stage: StageId, seed: number, budget = 20000, category = 'humanoid'): MockCheck[] => {
  const fail = seed % 3 === 0
  const collider = colliderFor(category)
  switch (stage) {
    case 'mesh':
      return [
        chk('silhouette_iou', true, 0.91, 0.6),
        chk('triangle_budget', true, 412_000, 2_000_000),
        chk('texture_base_color_pow2', true, [2048, 2048], 'power of two'),
        chk('texture_base_color_square', true, [2048, 2048], 'square'),
      ]
    case 'prep':
      return [
        chk(
          'triangle_budget',
          !fail,
          fail ? budget + 210 : budget - 1580,
          budget,
          fail
            ? { fix: { stage: 'mesh', params: { target_faces: budget }, label: 'Shrink to the budget' }, help: `The model has 210 more triangles than this platform can handle (${(budget + 210).toLocaleString('en-US')} of ${budget.toLocaleString('en-US')}).` }
            : { help: `The model has ${(budget - 1580).toLocaleString('en-US')} triangles, within the ${budget.toLocaleString('en-US')} this platform allows.` },
        ),
        // No help and no label: the frontend sentence and fallback label "Close the holes" show.
        chk('manifold', !fail, fail ? 8 : 0, 0, fail ? { fix: { stage: 'mesh', params: { remesh: true } } } : {}),
        chk('watertight', true, 0, 0),
        chk('degenerate_faces', true, 0, 0),
        ...(category === 'environment' || category === 'material'
          ? []
          : [
              chk('size_m', true, sizeOn(category), sizeOn(category), { severity: 'warn', vars: { axis_word: wordOf(category) }, help: `The model is ${sizeOn(category)} m ${wordOf(category)}, as intended.` }),
            ]),
        // The front check. On the failing seed the best turn is not a quarter turn, so its fix opens the front
        // picker instead of re-running (2.5).
        chk('facing', !fail, fail ? 45 : 0, 20, {
          severity: 'warn',
          ...(fail
            ? { fix: { ui: 'front_picker', label: 'Pick the front' }, help: 'The model may face the wrong way: it matches the front picture best when turned 45 degrees.' }
            : { help: 'The front of the model faces forward in the game.' }),
        }),
        chk('uv_set', true, 1, 1),
        // Textures and light (3.9). Some leave help out so the frontend fallback sentences show.
        chk('bake_matches', true, true, true, { severity: 'warn' }),
        chk('texture_padding', true, 8, 8, { severity: 'warn', help: 'Texture pieces have 8 px of space around them, enough for distant views.' }),
        chk('atlas_used', true, 0.71, 0.55, { severity: 'warn' }),
        chk('baked_light', true, 0.12, 0.15, { severity: 'warn', vars: { kept: true, removed: false } }),
        chk('albedo_range', false, 0.064, 0.02, {
          severity: 'warn',
          help: '6.4% of the colours are darker or brighter than real materials get, so they may look flat or glow under game lights.',
          fix: { stage: 'prep', params: { pbr_fix: true }, label: 'Bring colours into range' },
          vars: { pbr_fix_on: false },
        }),
        chk('metal_mixed', true, 0.01, 0.05, { severity: 'warn' }),
      ]
    case 'rig':
      return [
        chk('bones', true, 20, '>0'),
        chk('single_root', true, 1, 1),
        chk('weight_coverage', !fail, fail ? 0.9889 : 0.998, 0.99, fail ? { severity: 'warn', fix: { stage: 'rig', params: { group_per_vertex: 6 } } } : {}),
        chk('max_influences', true, 4, 4),
        chk('weights_normalised', true, 0, 0),
        ...rigMoveChecks(category, fail),
      ]
    case 'export':
      return [
        chk('lod_count', true, 4, 4),
        chk('lod1_ratio', true, 0.5, 0.5),
        chk('collider_present', true, collider.count, '>0'),
        ...(collider.count > 0 && collider.shape !== 'mesh'
          ? [chk('collider_triangles', true, collider.shape === 'decompose' ? 188 : 12, 255, { severity: 'warn', vars: { parts: collider.count } }), chk('collider_count', true, collider.count, 8, { severity: 'warn', vars: { vertices: 212 } })]
          : []),
        ...(/^(prop_rigid|environment|vehicle)$/.test(category)
          ? [chk('openings_clear', true, 0, 0, { severity: 'warn', vars: { openings: category === 'prop_rigid' ? 0 : 2, blocked: '0%', category } })]
          : []),
        chk('unity_import', true, true, true),
        chk('materials_connected', true, 'metal and shine', null, { severity: 'warn', help: 'Unity uses all the surface textures: colour, bumps, shadows, metal and shine.' }),
        ...(RIGGED.test(category)
          ? [
              chk('clips_loop', true, null, null, { severity: 'block', help: 'Idle and walk loop smoothly.' }),
              chk('controller_ready', true, true, true, { severity: 'warn', help: 'An animation controller switches between idle, walk and run by speed.' }),
            ]
          : []),
        ...(category === 'humanoid' ? [chk('unity_humanoid_avatar', true, 46, true, { severity: 'warn', vars: { clips: 46 } })] : []),
      ]
    default:
      return []
  }
}

// The collision the export chooses on auto (3.4): one box for a barrel-like prop, fitted shapes for the chest and the
// vehicle, the exact shape for scenery, one hull for creatures (their hit boxes are separate).
function colliderFor(category: string): { shape: string; count: number; kinds: string[] } {
  if (category === 'prop_rigid') return { shape: 'primitive', count: 1, kinds: ['box'] }
  if (category === 'environment') return { shape: 'mesh', count: 1, kinds: ['mesh'] }
  if (category === 'prop_articulated' || category === 'vehicle') return { shape: 'decompose', count: 3, kinds: ['hull'] }
  return { shape: 'hull', count: 1, kinds: ['hull'] }
}

// Scenery on a grid, as prep reports it: a 3 m wall on the 1 m grid. On a failing seed the wall comes out 3.4 m
// wide, between grid steps, until the grid is changed (the fix sets the 0.5 m grid). Some checks leave help out so
// the frontend fallback sentences show.
function gridFor(seed: number, params: Record<string, unknown>): { metrics: Record<string, unknown> & { grid_size_m: number[]; grid_fit: boolean }; checks: MockCheck[] } {
  const chosen = Number(params.grid_m)
  const grid = Number.isFinite(chosen) && chosen > 0 ? chosen : 1
  const off = seed % 3 === 0 && params.grid_m === undefined
  if (off) {
    const size = [3.4, 0.3, 3]
    return {
      metrics: { grid_m: grid, grid_footprint_cells: '3x1', grid_cells: [3, 1], grid_size_m: size, grid_height_m: 3, grid_kind: 'wall', grid_fit: false, tile_gap_cm: 40, pivot_corner: true },
      checks: [
        chk('grid_fit', false, size, grid, { severity: 'warn', vars: { grid, side: 'w', side_word: 'wide' }, fix: { stage: 'prep', params: { grid_m: 0.5 }, label: 'Snap to the grid' } }),
        chk('tiles_cleanly', false, 40, 0, { severity: 'warn' }),
      ],
    }
  }
  const width = grid <= 0.5 ? 3.5 : 3
  const cells = [Math.max(1, Math.round(width / grid)), 1]
  const size = [width, grid <= 0.5 ? 0.5 : 0.3, 3]
  return {
    metrics: { grid_m: grid, grid_footprint_cells: `${cells[0]}x${cells[1]}`, grid_cells: cells, grid_size_m: size, grid_height_m: 3, grid_kind: 'wall', grid_fit: true, tile_gap_cm: 0, pivot_corner: true },
    checks: [
      chk('grid_fit', true, size, grid, { severity: 'warn', vars: { grid } }),
      chk('tiles_cleanly', true, 0, 0, { severity: 'warn', help: 'Copies placed side by side meet without gaps.' }),
    ],
  }
}

function sizeOn(category: string): number {
  const d = MOCK_DIMS[category] ?? MOCK_DIMS.prop_rigid
  const axis = categories.find((c) => c.id === category)?.size_axis
  return axis === 'horizontal' ? Math.max(d[0], d[1]) : axis === 'longest' ? Math.max(...d) : d[2]
}

// The export stage's finish card lines and "Using it in Unity" text (2.4), as the new backend writes them.
// The seeded knight gets these; other exports leave them out so the frontend builds the lines itself.
export function addBackendFinish(asset: Asset, version: Version) {
  const prefab = `Assets/VGAC/${asset.name}/${asset.name}.prefab`
  version.metrics.finish_lines = [
    { id: 'size', text: 'Real size: 1.8 m tall, but the front may face the wrong way.', state: 'warn', term: 'face_yaw', check: 'facing' },
    { id: 'surface', text: 'Looks right in Unity: colour, bumps, shadows, metal and shine are connected.', state: 'ok', term: 'metal_shine' },
    { id: 'movement', text: 'Ready to move: set Speed and it idles, walks and runs; Attack, Hit and Die are one line each.', state: 'ok', term: 'animator_controller' },
    { id: 'collision', text: 'Bumps into things: a standing capsule for moving and 11 hit boxes that follow the animation.', state: 'ok', term: 'hitboxes' },
    { id: 'shared_animations', text: 'Shared animations: 46 human moves work on this character.', state: 'ok', term: 'avatar' },
    { id: 'files', text: 'Files: FBX (Unity, Unreal, Godot), GLB (web) and a ready Unity prefab.', state: 'ok', term: 'formats' },
  ]
  version.metrics.unity_howto = [
    `Drag the prefab from \`${prefab}\` into your scene.`,
    'In your movement script, set the speed every frame: `animator.SetFloat("Speed", speed);`',
    'For one-off moves, call `animator.SetTrigger("Attack");` (or `"Hit"`, `"Die"`).',
    'Hit boxes are trigger colliders named Hit_Head, Hit_Chest and so on under the bones; check the name in OnTriggerEnter to know where a hit landed.',
  ]
}

// The backend's readiness checks for your own picture, as the mock fakes them: a person's arms are always "close to
// the body" on the first upload so the warning and its "Redraw it ready for 3D" fix show.
const REDRAW_HUMANOID = 'Redraw this same character with the same design, colours and outfit, standing in a relaxed A-pose, whole body in frame, plain light grey background, even lighting'
function uploadReadiness(asset: Asset): MockCheck[] {
  const redraw = asset.category === 'humanoid' ? REDRAW_HUMANOID : `Redraw this same subject with the same design, colours and materials. ${categories.find((c) => c.id === asset.category)?.pose_block ?? ''}`.trim()
  const fix = { stage: 'concept', op: 'refine' as const, instruction: redraw, params: {}, label: 'Redraw it ready for 3D' }
  const out = [
    chk('one_subject', true, 1, 1, { severity: 'warn', help: 'The picture shows one subject.' }),
    chk('in_frame', true, 0.04, 0.01, { severity: 'warn', help: 'The whole subject is in the picture, with space around it.' }),
    chk('big_enough', true, 820, 400, { severity: 'warn', vars: { measure: asset.category === 'humanoid' ? 'tall' : 'across' } }),
  ]
  if (asset.category === 'humanoid')
    out.push(
      chk('arms_away', false, 0.05, 0.2, {
        severity: 'warn',
        help: 'The arms are close to the body; the 3D model may join them to the sides and the skeleton step may not tell them apart.',
        fix,
        vars: { redraw, span_ratio: 1.3 },
      }),
    )
  return out
}

// Fill in files and metrics for a finished version.
export function fabricate(asset: Asset, version: Version) {
  const seed = Number(version.params.seed ?? 1) || 1
  const hue = hueFor(asset, seed)
  const figure = figureFor(asset)
  const base = `projects/${asset.project_id}/assets/${asset.id}/${version.stage}/${version.id}`
  const f: Record<string, string> = {}
  const m: Record<string, unknown> = {}
  const recipe = recipeFor(asset)
  switch (version.stage) {
    case 'concept': {
      // Your own picture (the mock upload handler stored it as files.image): kept, "cut out", and checked.
      if ((version.params.source === 'upload' || version.params.source === 'upload_edit') && version.files.image && !version.params.instruction) {
        f.image = version.files.image
        if (asset.category === 'material') {
          f.raw = version.files.image
          m.source = version.params.source
          m.checks = [
            chk('even_lighting', true, 0.08, 0.25, { severity: 'warn', help: 'The light is even across the photo.' }),
            chk('straight_on', true, 1.2, 2, { severity: 'warn', help: 'The photo looks taken straight on.' }),
          ]
          m.gate_ok = true
          break
        }
        f.cutout = version.files.image
        f.cutout_white = version.files.image
        m.source = version.params.source
        m.cutout = { coverage: 0.2, empty: false, aspect: 0.44 }
        m.checks = uploadReadiness(asset)
        m.gate_ok = (m.checks as MockCheck[]).every((c) => c.passed)
        break
      }
      f.image = putDataFile(`${base}/concept.png`, makeImage({ w: 512, h: 512, hue, seed, figure, label: `seed ${seed}` }))
      f.cutout = putDataFile(`${base}/cutout.png`, makeImage({ w: 512, h: 512, hue, seed, figure, transparent: true }))
      m.cutout = { coverage: Number((0.15 + (seed % 7) / 100).toFixed(3)), empty: false, aspect: 0.44 }
      m.elapsed_seconds = 16 + (seed % 5)
      m.model = version.params.model ?? 'zturbo'
      m.seed = seed
      m.recipe = recipeRecord(recipe, 'concept', 'addons')
      // Like the backend: style-lock add-ons the registry does not know are reported, never a failure.
      {
        const lock = store.projects.find((p) => p.id === asset.project_id)?.style_lock.loras ?? []
        const known = new Set(CONCEPT_CATALOG.map((a) => a.id))
        m.addons_unknown = lock.map((l) => l.split(':')[0]).filter((id) => !known.has(id))
        m.addons_ignored = []
      }
      break
    }
    case 'turnaround': {
      const views = (version.params.views as ViewName[] | undefined) ?? VIEWS
      const perView: Record<string, unknown> = {}
      views.forEach((view, i) => {
        const own = version.files[`supplied_${view}`]
        if (own) {
          // Your own view: kept as it is; the mock warns on the back view so the "warns but kept" state shows.
          f[`supplied_${view}`] = own
          f[view] = own
          f[`${view}_cutout`] = own
          const warn = view === 'back'
          perView[view] = {
            supplied: true,
            seed: null,
            rerolls: 0,
            coverage: 0.16,
            height_ratio: warn ? 0.7 : 1.0,
            passed: !warn,
            ok: !warn,
            flagged: warn,
            checks: [chk('coverage', true, 0.16, 0.02), chk('height_ratio', !warn, warn ? 0.7 : 1.0, [0.75, 1.3])],
          }
          return
        }
        f[view] = putDataFile(`${base}/${view}.png`, makeImage({ w: 384, h: 512, hue, seed: seed + i, figure, view, label: view }))
        f[`${view}_cutout`] = putDataFile(`${base}/${view}_cutout.png`, makeImage({ w: 384, h: 512, hue, seed: seed + i, figure, view, transparent: true }))
        const bad = (seed + i) % 5 === 1
        const hist = Number((bad ? 0.41 : 0.04 + ((seed + i) % 4) / 100).toFixed(2))
        perView[view] = {
          coverage: Number((0.12 + ((seed + i) % 6) / 100).toFixed(3)),
          height_ratio: Number((bad ? 0.71 : 0.99).toFixed(2)),
          hist_dist: hist,
          rerolls: bad ? 2 : 0,
          passed: !bad,
          ok: !bad,
          checks: [chk('coverage', true, 0.18, 0.02), chk('height_ratio', !bad, bad ? 0.71 : 0.99, [0.75, 1.3]), chk('hist_dist', !bad, hist, 0.35)],
        }
      })
      m.views = perView
      m.views_passed = Object.values(perView).filter((v) => (v as { passed: boolean }).passed).length
      m.views_total = views.length
      m.consistency_ok = m.views_passed === views.length
      m.flagged_views = views.filter((v) => !(perView[v] as { passed: boolean }).passed)
      m.supplied_views = views.filter((v) => (perView[v] as { supplied?: boolean }).supplied)
      break
    }
    case 'mesh': {
      f.glb = 'samples/sample.glb'
      f.base_color = putDataFile(`${base}/base_color.png`, makeTexture('base_color', hue))
      f.metallic_roughness = putDataFile(`${base}/orm.png`, makeTexture('orm', hue))
      m.engine = version.params.engine ?? 'trellis'
      m.triangles = 412_000 + (seed % 7) * 10_000
      m.vertices = Math.round((412_000 + (seed % 7) * 10_000) / 2)
      m.silhouette_iou = { front: 0.93, left: 0.88, back: 0.91, right: 0.9, quarter: 0.92 }
      m.texture_size = version.params.texture_size ?? 2048
      m.checks = checksFor('mesh', seed)
      m.recipe = recipeRecord(recipe, 'mesh')
      m.gate_ok = true
      m.elapsed_seconds = 180 + (seed % 40)
      break
    }
    case 'prep': {
      f.glb = 'samples/sample.glb'
      f.base_color = putDataFile(`${base}/base_color.png`, makeTexture('base_color', hue, 512))
      f.normal = putDataFile(`${base}/normal.png`, makeTexture('normal', hue, 512))
      f.orm = putDataFile(`${base}/orm.png`, makeTexture('orm', hue, 512))
      m.triangles = 18_420 + (seed % 5) * 300
      m.vertices = 9_600
      m.parts = asset.parts.length + 1
      m.uv_islands = 23
      m.texel_density = 10.2
      m.collider_vertices = 212
      m.dims = MOCK_DIMS[asset.category] ?? MOCK_DIMS.prop_rigid
      // The turn prep applied when face_yaw was automatic (3.2); the front picker marks it "chosen".
      m.face_yaw_applied = 0
      m.face_best_turn = 0
      m.facing_source = 'audit'
      m.size_source = 'category'
      // Textures and light (3.9).
      m.rebaked = false
      m.light_score = 0.12
      m.delight_applied = false
      m.delight_mode = 'keep'
      m.texture_padding_px = 8
      m.atlas_used = 0.71
      m.checks = checksFor('prep', seed, categories.find((c) => c.id === asset.category)?.budgets[asset.platform_target]?.triangles ?? 20000, asset.category)
      // Recipes: the shading (the knob, else the look), and for a look that changes the result, the look_applied check.
      {
        const choice = version.params.shading
        const shading = choice === 'smooth' || choice === 'flat' ? choice : recipe.surface.shading
        m.shading_applied = shading
        m.shading_source = choice === 'smooth' || choice === 'flat' ? 'chosen' : 'recipe'
        m.recipe = recipeRecord(recipe, 'mesh', 'surface')
        const colours = recipe.surface.palette_colors
        if (colours) {
          m.palette_limit = colours
          m.texture_colours = colours
        }
        const summary = lookSummary(recipe, shading, colours)
        if (summary) {
          if (recipe.mesh.triangles && recipe.mesh.triangle_scale < 1) m.triangles = recipe.mesh.triangles - 40 + (seed % 5) * 10
          if (!recipe.mesh.normal_map) delete f.normal
          const look = LOOK_OF[asset.style_preset] ?? asset.style_preset
          ;(m.checks as MockCheck[]).push(
            chk('look_applied', true, summary, recipe.id, { severity: 'warn', help: `${look} look applied: ${summary}.`, vars: { look, summary, problem: '' } }),
          )
        }
      }
      if (asset.category === 'environment') {
        const g = gridFor(seed, version.params)
        Object.assign(m, g.metrics)
        m.dims = g.metrics.grid_size_m
        f.tiling = putDataFile(`${base}/tiling.png`, makeTiling(hue, g.metrics.grid_fit ? 0 : 14))
        ;(m.checks as MockCheck[]).push(...g.checks)
      }
      m.gate_ok = (m.checks as MockCheck[]).every((c) => c.ok)
      m.checks_failed = (m.checks as MockCheck[]).filter((c) => !c.ok).map((c) => c.id)
      break
    }
    case 'rig': {
      f.bend_strip = putDataFile(`${base}/bend_strip.png`, makeBendStrip(hue, ['LeftArm', 'LeftForeArm', 'LeftUpLeg', 'LeftLeg']))
      const sample = RIG_SAMPLES[asset.category]
      if (sample) {
        // Real rigged files from the library's samples folder (a knight and a wolf), with their fitted hit boxes
        // and clips, so the overlay can be checked against a real skeleton.
        const dir = `samples/${sample.dir}`
        f.glb = `${dir}/rig.glb`
        f.anim_glb = `${dir}/anim.glb`
        f.anim_clips = `${dir}/anim_clips.json`
        f.rig_info = `${dir}/rig_info.json`
        if (version.params.hitboxes !== 'none') f.hitboxes = `${dir}/hitboxes.json`
        m.bones = sample.bones
        Object.assign(m, rigMoveMetrics(asset.category, version.params, seed % 3 === 0))
        // The rig stage restores textures and size, and renames humanoid bones by geometry.
        const human = asset.category === 'humanoid'
        m.rig_restore = { ok: true, uvs: true, scale_factor: 1, size_m: 1.8, reason: null, bone_names: { count: human ? 22 : 0, corrected: human, left_is: human ? '+x' : null, reason: human ? null : 'not a humanoid skeleton' } }
      } else {
        f.glb = 'samples/sample.glb'
        const bones = bipedBones()
        f.rig_info = putText(`${base}/rig_info.json`, JSON.stringify({ template: 'mixamo', bone_segments: bones }, null, 2))
        m.bones = bones.length
      }
      m.template = version.params.template === 'auto' || !version.params.template ? (asset.category === 'humanoid' ? 'mixamo' : 'generic') : version.params.template
      m.max_influences = version.params.group_per_vertex ?? 4
      m.unweighted_vertices = 0
      m.checks = checksFor('rig', seed, 20000, asset.category).filter((c) => sample || !/^(hitbox_|clips|legs_moved|wings_found)/.test(c.id))
      m.gate_ok = (m.checks as MockCheck[]).every((c) => c.ok)
      m.elapsed_seconds = 95
      break
    }
    case 'export': {
      f.glb = 'samples/sample.glb'
      f.fbx = putText(`${base}/${asset.name}.fbx`, 'mock fbx', 'application/octet-stream')
      const lods = [
        { level: 0, triangles: 18_420, screen_height: 0.6 },
        { level: 1, triangles: 9_210, screen_height: 0.3 },
        { level: 2, triangles: 4_600, screen_height: 0.12 },
        { level: 3, triangles: 1_840, screen_height: 0.03 },
      ]
      const clips = clipsFor(asset.category)
      // Packed maps (3.1); the separate metal and roughness maps are still written.
      f.base_color = putDataFile(`${base}/T_${asset.name}_BaseColor.png`, makeTexture('base_color', hue, 256))
      f.normal = putDataFile(`${base}/T_${asset.name}_Normal.png`, makeTexture('normal', hue, 256))
      f.metallic_smoothness = putDataFile(`${base}/T_${asset.name}_MetallicSmoothness.png`, makeTexture('orm', hue, 256))
      f.orm = putDataFile(`${base}/T_${asset.name}_ORM.png`, makeTexture('orm', hue + 40, 256))
      f.metallic = putDataFile(`${base}/T_${asset.name}_Metallic.png`, makeTexture('orm', hue + 80, 256))
      f.roughness = putDataFile(`${base}/T_${asset.name}_Roughness.png`, makeTexture('orm', hue + 120, 256))
      const manifest = {
        asset: asset.name,
        category: asset.category,
        style_preset: asset.style_preset,
        platform_target: asset.platform_target,
        lods,
        textures: { base_color: 2048, normal: 2048, orm: 2048 },
        bones: 65,
        parts: asset.parts,
        models: [
          { name: 'Z-Image Turbo', version: '1.0', licence: 'Apache-2.0' },
          { name: 'Qwen-Image-Edit-2511', version: '2511', licence: 'Apache-2.0' },
          { name: 'TRELLIS.2', version: '2.0', licence: 'MIT' },
          { name: 'Make-It-Animatable', version: '1.0', licence: 'MIT' },
        ],
        seeds: { concept: seed, turnaround: 77, mesh: 5 },
        lineage: Object.values(asset.selected),
      }
      f.manifest = putText(`${base}/manifest.json`, JSON.stringify(manifest, null, 2))
      if (version.params.sprites) {
        f.sprite_sheet = putDataFile(`${base}/sprites.png`, makeSpriteSheet(hue, clips, Number(version.params.sprite_directions ?? 8), Number(version.params.sprite_size ?? 64)))
        f.sprite_atlas = putText(`${base}/sprites.json`, JSON.stringify({ clips, directions: version.params.sprite_directions ?? 8, size: version.params.sprite_size ?? 64 }))
      }
      m.lods = lods
      m.clips = clips
      m.formats = version.params.formats ?? ['fbx', 'glb']
      const rigged = RIGGED.test(asset.category)
      const col = version.params.collider === 'none' ? { shape: 'none', count: 0, kinds: [] as string[] } : colliderFor(asset.category)
      m.collider_count = col.count
      m.collider_shape = col.shape
      m.collider_kinds = col.kinds
      m.collider_verts = 212
      const scenery = /^(prop_rigid|environment)$/.test(asset.category)
      if (!RIGGED.test(asset.category)) f.fbx_unreal = putText(`${base}/${asset.name}_unreal.fbx`, 'mock fbx', 'application/octet-stream')
      m.unity_report = {
        ok: true,
        prefab: `Assets/VGAC/${asset.name}/${asset.name}.prefab`,
        ...(rigged ? (asset.category === 'humanoid' ? { avatar: 'Humanoid', avatar_mapping: 'explicit', humanoidValid: true } : { avatar: 'Generic' }) : {}),
        importer_version: 4,
        colliderCount: col.count,
        colliderKinds: col.kinds,
        ...(scenery ? { secondaryUV: true } : {}),
        lodFadeMode: asset.category === 'environment' ? 'CrossFade' : 'None',
        texturesAssigned: ['base_color', 'normal', 'occlusion', 'metallic_smoothness'],
        ...(rigged
          ? {
              clips: clips.map((c) => ({ name: c, loop: /idle|walk|run/.test(c) })),
              controller: { states: ['Locomotion', 'Attack', 'Hit', 'Dead'], parameters: ['Speed', 'Attack', 'Hit', 'Die', ...(asset.category === 'humanoid' ? ['Jump'] : [])] },
            }
          : {}),
        warnings: ['LOD3 is under 10 percent of LOD0; Unity will cull it at 3 percent screen height'],
        errors: [],
      }
      m.checks = checksFor('export', seed, 20000, asset.category)
      m.recipe = recipeRecord(recipe, 'surface', 'engine')
      m.gate_ok = true
      break
    }
  }
  version.files = f
  version.metrics = m
}

// The asset's recipe, as GET /api/recipe would give it.
export function recipeFor(asset: Pick<Asset, 'style_preset' | 'category' | 'platform_target'>) {
  const budget = categories.find((c) => c.id === asset.category)?.budgets[asset.platform_target] ?? { triangles: 20000, texture: 2048 }
  return mockRecipe(asset.style_preset, asset.category, asset.platform_target, budget)
}

// Real rig samples in library/samples (copied from library rig versions, with procanim clips and fitted hit boxes).
type RigSample = { dir: string; bones: number; hitboxes: number; regions: [string, string][]; cover: number; overreach: number; movement: string }
const RIG_SAMPLES: Record<string, RigSample> = {
  humanoid: {
    dir: 'knight',
    bones: 34,
    hitboxes: 11,
    regions: [
      ['head', 'Head'],
      ['hips', 'Hips'],
      ['chest', 'Chest'],
      ['upper_arm_l', 'Left upper arm'],
      ['forearm_l', 'Left forearm'],
      ['upper_arm_r', 'Right upper arm'],
      ['forearm_r', 'Right forearm'],
      ['thigh_l', 'Left thigh'],
      ['shin_l', 'Left shin'],
      ['thigh_r', 'Right thigh'],
      ['shin_r', 'Right shin'],
    ],
    cover: 0.8653,
    overreach: 1.79,
    movement: 'upright',
  },
  quadruped: {
    dir: 'wolf',
    bones: 30,
    hitboxes: 8,
    regions: [
      ['head', 'Head'],
      ['neck', 'Neck'],
      ['body', 'Body'],
      ['tail', 'Tail'],
      ['leg_fr', 'Right front leg'],
      ['leg_fl', 'Left front leg'],
      ['leg_br', 'Right back leg'],
      ['leg_bl', 'Left back leg'],
    ],
    cover: 0.8853,
    overreach: 1.27,
    movement: 'along',
  },
}
const SAMPLE_CLIPS = ['idle', 'walk', 'run', 'turn_left', 'turn_right', 'attack', 'hit', 'death']

// Rig metrics for moves (3.7) and hit boxes (4.2), as the rig stage writes them.
function rigMoveMetrics(category: string, params: Record<string, unknown>, fail: boolean): Record<string, unknown> {
  const s = RIG_SAMPLES[category]
  if (!s) return {}
  const mode = typeof params.hitboxes === 'string' ? params.hitboxes : 'detailed'
  const fit = params.hitbox_fit ?? 'normal'
  const hitboxes =
    mode === 'none'
      ? { count: 0, mode: 'none', fit, fallback: false, regions_found: [], cover: null, overreach: null, movement_default: s.movement }
      : { count: s.hitboxes, mode, fit, fallback: false, regions_found: s.regions.map(([id, label]) => ({ id, label })), cover: fail ? 0.78 : s.cover, overreach: s.overreach, movement_default: s.movement }
  const out: Record<string, unknown> = { motion: 'legs', anim_kind: category === 'humanoid' ? 'biped' : 'quadruped', clips: SAMPLE_CLIPS, missing_clips: [], hitboxes }
  if (category === 'quadruped') Object.assign(out, { legs_moved: 4, legs_total: 4 })
  return out
}

// The rig checks for moves and hit boxes (sentences as the backend writes them).
function rigMoveChecks(category: string, fail: boolean): MockCheck[] {
  const s = RIG_SAMPLES[category]
  if (!s) return []
  const cover = fail ? 0.78 : s.cover
  const coverPct = `${Math.round(cover * 100)}%`
  const out: MockCheck[] = [
    chk('clips', true, SAMPLE_CLIPS, SAMPLE_CLIPS, { severity: 'warn', vars: { missing: [] }, help: `Animations: ${SAMPLE_CLIPS.join(', ')}.` }),
    chk('hitbox_cover', !fail, cover, 0.85, {
      severity: 'warn',
      vars: { n: s.hitboxes, cover: coverPct },
      ...(fail
        ? { help: `The hit boxes cover only ${coverPct} of the body, so some hits would pass through.`, fix: { stage: 'rig', params: { hitbox_fit: 'loose' }, label: 'Fit them looser' } }
        : { help: `${s.hitboxes} hit boxes cover ${coverPct} of the body and follow the skeleton.` }),
    }),
    chk('hitbox_overreach', true, s.overreach, 2.75, { severity: 'warn', vars: { n: s.hitboxes, times: s.overreach.toFixed(1) }, help: 'The hit boxes stay close to the body.' }),
  ]
  if (category === 'quadruped') out.push(chk('legs_moved', true, 4, 4, { severity: 'warn', help: 'All 4 legs move in the walk.' }))
  return out
}

function bipedBones() {
  const b = (name: string, head: number[], tail: number[]) => ({ name, head, tail })
  return [
    b('Hips', [0, 0.95, 0], [0, 1.1, 0]),
    b('Spine', [0, 1.1, 0], [0, 1.25, 0]),
    b('Spine1', [0, 1.25, 0], [0, 1.4, 0]),
    b('Spine2', [0, 1.4, 0], [0, 1.52, 0]),
    b('Neck', [0, 1.52, 0], [0, 1.62, 0]),
    b('Head', [0, 1.62, 0], [0, 1.95, 0]),
    b('LeftShoulder', [0, 1.5, 0], [0.18, 1.48, 0]),
    b('LeftArm', [0.18, 1.48, 0], [0.42, 1.2, 0]),
    b('LeftForeArm', [0.42, 1.2, 0], [0.62, 0.95, 0]),
    b('LeftHand', [0.62, 0.95, 0], [0.7, 0.85, 0]),
    b('RightShoulder', [0, 1.5, 0], [-0.18, 1.48, 0]),
    b('RightArm', [-0.18, 1.48, 0], [-0.42, 1.2, 0]),
    b('RightForeArm', [-0.42, 1.2, 0], [-0.62, 0.95, 0]),
    b('RightHand', [-0.62, 0.95, 0], [-0.7, 0.85, 0]),
    b('LeftUpLeg', [0.15, 0.95, 0], [0.17, 0.5, 0]),
    b('LeftLeg', [0.17, 0.5, 0], [0.18, 0.08, 0]),
    b('LeftFoot', [0.18, 0.08, 0], [0.18, 0, 0.15]),
    b('RightUpLeg', [-0.15, 0.95, 0], [-0.17, 0.5, 0]),
    b('RightLeg', [-0.17, 0.5, 0], [-0.18, 0.08, 0]),
    b('RightFoot', [-0.18, 0.08, 0], [-0.18, 0, 0.15]),
  ]
}

export function newVersion(asset: Asset, stage: StageId, parent: string | null, params: Record<string, unknown>): Version {
  const v: Version = {
    id: uid('v'),
    asset_id: asset.id,
    stage,
    parent_version_id: parent,
    status: 'queued',
    params,
    files: {},
    metrics: {},
    job_id: null,
    error: null,
    created_at: nowIso(),
    selected: false,
  }
  ;(asset.stages[stage] ??= []).push(v)
  return v
}

export function setSelected(asset: Asset, version: Version) {
  for (const v of asset.stages[version.stage] ?? []) v.selected = v.id === version.id
  asset.selected[version.stage] = version.id
}

function doneJob(asset: Asset, version: Version, kind: string, instance: Job['instance'], minutesAgo: number): Job {
  const job: Job = {
    id: uid('j'),
    instance,
    kind,
    asset_id: asset.id,
    version_id: version.id,
    status: 'done',
    progress: 1,
    message: 'finished',
    created_at: nowIso(-minutesAgo * 60_000),
    started_at: nowIso(-minutesAgo * 60_000 + 2_000),
    finished_at: nowIso(-minutesAgo * 60_000 + 90_000),
    error: null,
  }
  version.job_id = job.id
  store.jobs.push(job)
  return job
}

function seedVersion(asset: Asset, stage: StageId, parent: Version | null, params: Record<string, unknown>, minutesAgo: number, instance: Job['instance']) {
  const v = newVersion(asset, stage, parent?.id ?? null, params)
  v.status = 'done'
  v.created_at = nowIso(-minutesAgo * 60_000)
  fabricate(asset, v)
  doneJob(asset, v, `${stage}.run`, instance, minutesAgo)
  return v
}

export function seedStore() {
  const p1: Project = {
    id: uid('p'),
    name: 'Hollow Keep',
    created_at: nowIso(-6 * 3600_000),
    style_lock: {
      prompt_block: 'hand-painted stylised fantasy, chunky proportions, soft rim light, clean silhouette, muted earth tones with one accent colour',
      model: 'zturbo',
      loras: ['vgac_painterly_v2', 'hollowkeep_palette'],
      palette: ['#3b2f2f', '#c9a24a', '#6b8e7b', '#e8dcc2', '#8c2f2f'],
      negative: 'photorealistic, text, watermark, blurry, extra limbs, cropped',
      preset: 'painted_fantasy',
    },
    platform_target: 'pc_mid',
  }
  const p2: Project = {
    id: uid('p'),
    name: 'Scrapyard Racers',
    created_at: nowIso(-2 * 3600_000),
    style_lock: { prompt_block: 'cel shaded, bold outlines, saturated palette, rusted metal', model: 'klein', loras: [], palette: ['#f2b134', '#2b2b2b', '#4ea1d3'], negative: 'photorealistic, blurry' },
    platform_target: 'mobile_low',
  }
  store.projects.push(p1, p2)

  const knight: Asset = {
    id: uid('a'),
    project_id: p1.id,
    name: 'Knight',
    category: 'humanoid',
    style_preset: 'painterly',
    platform_target: 'pc_mid',
    prompt: 'armoured knight with a round shield and a short sword, weathered steel plate, crimson tabard, closed great helm',
    parts: ['sword', 'shield'],
    created_at: nowIso(-5 * 3600_000),
    selected: {},
    stages: {},
  }
  store.assets.push(knight)

  const c1 = seedVersion(knight, 'concept', null, { model: 'zturbo', seed: 3, width: 1024, height: 1024, steps: 8, readiness_block: true }, 300, 'b')
  c1.status = 'rejected'
  const c2 = seedVersion(knight, 'concept', null, { model: 'zturbo', seed: 11, width: 1024, height: 1024, steps: 8, readiness_block: true }, 298, 'b')
  seedVersion(knight, 'concept', null, { model: 'zturbo', seed: 19, width: 1024, height: 1024, steps: 8, readiness_block: true }, 296, 'b')
  const c4 = newVersion(knight, 'concept', c2.id, { model: 'zturbo', seed: 11, instruction: 'make the helm open-faced', refine: true })
  c4.status = 'done'
  c4.created_at = nowIso(-280 * 60_000)
  fabricate(knight, c4)
  doneJob(knight, c4, 'concept.refine', 'b', 280)
  setSelected(knight, c2)

  const t1 = seedVersion(knight, 'turnaround', c2, { views: VIEWS, lora_strength: 0.9, seed: 77, auto_reroll: true }, 240, 'b')
  setSelected(knight, t1)

  seedVersion(knight, 'mesh', t1, { engine: 'trellis', input_view: 'quarter', target_faces: 500_000, texture_size: 2048, upsample: 1024, seed: 5, remesh: true }, 200, 'a')
  const m2 = seedVersion(knight, 'mesh', t1, { engine: 'trellis', input_view: 'quarter', target_faces: 500_000, texture_size: 2048, upsample: 1536, seed: 8, remesh: true }, 170, 'a')
  setSelected(knight, m2)

  // seed 3 makes the mock prep checks fail (triangle budget and manifold) so the fix buttons show.
  const pr1 = seedVersion(knight, 'prep', m2, { size_m: 1.8, size_axis: 'height', size_word: 'tall', face_yaw: 'auto', symmetric: true, pivot: 'base', smooth_angle: 60, seed: 3 }, 120, 'host')
  setSelected(knight, pr1)

  const r1 = seedVersion(knight, 'rig', pr1, { template: 'auto', target_faces: 20_000, group_per_vertex: 4, bottom_center_origin: true, bend_test: true, motion: 'legs', moves: SAMPLE_CLIPS, hitboxes: 'detailed', hitbox_fit: 'normal' }, 80, 'b')
  setSelected(knight, r1)

  const e1 = seedVersion(knight, 'export', r1, { lods: '1.0, 0.5, 0.25, 0.1', collider: 'hull', formats: ['fbx', 'glb'], unity: true, clips_library: 'ual1', sprites: true, sprite_directions: 8, sprite_size: 64 }, 30, 'host')
  addBackendFinish(knight, e1)
  setSelected(knight, e1)

  const chest: Asset = {
    id: uid('a'),
    project_id: p1.id,
    name: 'Treasure chest',
    category: 'prop_articulated',
    style_preset: 'painterly',
    platform_target: 'pc_mid',
    prompt: 'wooden treasure chest with iron bands and a brass lock, closed lid',
    parts: ['lid'],
    created_at: nowIso(-90 * 60_000),
    selected: {},
    stages: {},
  }
  store.assets.push(chest)
  const cc1 = seedVersion(chest, 'concept', null, { model: 'zturbo', seed: 42, width: 1024, height: 1024, steps: 8, readiness_block: true }, 60, 'b')
  seedVersion(chest, 'concept', null, { model: 'zturbo', seed: 43, width: 1024, height: 1024, steps: 8, readiness_block: true }, 58, 'b')
  setSelected(chest, cc1)
  const ct = newVersion(chest, 'turnaround', cc1.id, { views: VIEWS, lora_strength: 0.9, seed: 7, auto_reroll: true })
  ct.status = 'error'
  ct.error = "ComfyError: prompt 3bf72e12 failed: {'status_str': 'error', 'completed': False, 'node': 14, 'exception_message': 'Allocation on device: out of VRAM'}"
  const failedJob = doneJob(chest, ct, 'turnaround.run', 'b', 20)
  failedJob.status = 'error'
  failedJob.progress = 0.4
  failedJob.message = 'node 14 failed'
  failedJob.error = ct.error

  const wolf: Asset = {
    id: uid('a'),
    project_id: p1.id,
    name: 'Dire wolf',
    category: 'quadruped',
    style_preset: 'painterly',
    platform_target: 'pc_mid',
    prompt: 'large grey dire wolf with scarred muzzle, standing',
    parts: [],
    created_at: nowIso(-10 * 60_000),
    selected: {},
    stages: {},
  }
  store.assets.push(wolf)
  // The wolf runs through to export without backend finish lines, so the finish card builds its own (hit boxes, moves).
  const wc = seedVersion(wolf, 'concept', null, { model: 'zturbo', seed: 5, width: 1024, height: 1024, steps: 8, readiness_block: true }, 9, 'b')
  setSelected(wolf, wc)
  const wt = seedVersion(wolf, 'turnaround', wc, { views: VIEWS, lora_strength: 0.9, seed: 7, auto_reroll: true }, 8, 'b')
  setSelected(wolf, wt)
  const wm = seedVersion(wolf, 'mesh', wt, { engine: 'hy3d', input_view: 'concept', seed: 2, remesh: true }, 7, 'a')
  setSelected(wolf, wm)
  const wp = seedVersion(wolf, 'prep', wm, { size_m: 1.8, face_yaw: 'auto', symmetric: true, pivot: 'base', smooth_angle: 60, seed: 1 }, 6, 'host')
  setSelected(wolf, wp)
  const wr = seedVersion(wolf, 'rig', wp, { template: 'auto', group_per_vertex: 4, bottom_center_origin: true, bend_test: true, motion: 'legs', moves: SAMPLE_CLIPS, hitboxes: 'detailed', hitbox_fit: 'normal' }, 5, 'b')
  setSelected(wolf, wr)
  const we = seedVersion(wolf, 'export', wr, { lods: '1.0, 0.5, 0.25', collider: 'hull', movement_shape: 'auto', formats: ['fbx', 'glb'], unity: true }, 3, 'host')
  setSelected(wolf, we)

  // A barrel, through to export, with painted-on shadows removed in prep (3.9) and one box for collision (3.4).
  const barrel: Asset = {
    id: uid('a'),
    project_id: p1.id,
    name: 'Oak barrel',
    category: 'prop_rigid',
    style_preset: 'realistic',
    platform_target: 'pc_mid',
    prompt: 'weathered oak barrel with rusted iron bands and a cracked stave',
    parts: [],
    created_at: nowIso(-40 * 60_000),
    selected: {},
    stages: {},
  }
  store.assets.push(barrel)
  const bc = seedVersion(barrel, 'concept', null, { model: 'zbase', seed: 4, width: 1024, height: 1024, steps: 8, readiness_block: true }, 39, 'b')
  setSelected(barrel, bc)
  const bt = seedVersion(barrel, 'turnaround', bc, { views: VIEWS, lora_strength: 0.9, seed: 7, auto_reroll: true }, 38, 'b')
  setSelected(barrel, bt)
  const bm = seedVersion(barrel, 'mesh', bt, { engine: 'trellis', input_view: 'quarter', seed: 2, remesh: true }, 36, 'a')
  setSelected(barrel, bm)
  const bp = seedVersion(barrel, 'prep', bm, { size_m: 0.9, face_yaw: 'auto', symmetric: false, delight: 'auto', seed: 1 }, 34, 'host')
  removeShadows(barrel, bp)
  setSelected(barrel, bp)
  // Props have no skeleton: the rig step keeps the model as one rigid piece.
  const br = seedVersion(barrel, 'rig', bp, { template: 'none' }, 33, 'b')
  br.metrics = { rigid: true, bones: 0, template: 'none', checks: [], gate_ok: true }
  br.files = { glb: 'samples/sample.glb' }
  setSelected(barrel, br)
  const be = seedVersion(barrel, 'export', br, { lods: '1.0, 0.5, 0.25', collider: 'auto', formats: ['fbx', 'glb'], unity: true }, 32, 'host')
  setSelected(barrel, be)

  // A wall section snapped to the 1 m grid (scenery), through to export. An earlier prep attempt (seed 3) came out
  // between grid steps, so its grid check fails with a "Snap to the grid" fix.
  const wall: Asset = {
    id: uid('a'),
    project_id: p1.id,
    name: 'Stone wall section',
    category: 'environment',
    style_preset: 'painterly',
    platform_target: 'pc_mid',
    prompt: 'mossy stone wall section, three metres wide, with a crumbling top edge',
    parts: [],
    created_at: nowIso(-50 * 60_000),
    selected: {},
    stages: {},
  }
  store.assets.push(wall)
  const sc = seedVersion(wall, 'concept', null, { model: 'zturbo', seed: 6, width: 1024, height: 1024, steps: 8, readiness_block: true }, 49, 'b')
  setSelected(wall, sc)
  const st = seedVersion(wall, 'turnaround', sc, { views: VIEWS, lora_strength: 0.9, seed: 7, auto_reroll: true }, 48, 'b')
  setSelected(wall, st)
  const sm = seedVersion(wall, 'mesh', st, { engine: 'trellis', input_view: 'quarter', seed: 2, remesh: true }, 46, 'a')
  setSelected(wall, sm)
  seedVersion(wall, 'prep', sm, { face_yaw: 'auto', symmetric: false, delight: 'auto', grid_footprint: 'auto', grid_height: 'auto', seed: 3 }, 45, 'host')
  const sp = seedVersion(wall, 'prep', sm, { face_yaw: 'auto', symmetric: false, delight: 'auto', grid_footprint: 'auto', grid_height: 'auto', grid_m: 1, seed: 1 }, 44, 'host')
  setSelected(wall, sp)
  const sr = seedVersion(wall, 'rig', sp, { template: 'none' }, 43, 'b')
  sr.metrics = { rigid: true, bones: 0, template: 'none', checks: [], gate_ok: true }
  sr.files = { glb: 'samples/sample.glb' }
  setSelected(wall, sr)
  const se = seedVersion(wall, 'export', sr, { lods: '1.0, 0.5, 0.25', collider: 'auto', formats: ['fbx', 'glb'], unity: true }, 42, 'host')
  setSelected(wall, se)

  // A pixel-art hero for phones, through to export: the recipe shrinks it (look line in the mesh and prep views and on
  // the finish card), and the hand-off to the open Unity project waits as "sent" until "Check again".
  const hero: Asset = {
    id: uid('a'),
    project_id: p1.id,
    name: 'Pixel hero',
    category: 'humanoid',
    style_preset: 'pixel',
    platform_target: 'mobile_low',
    prompt: 'a small pixel-art adventurer with a green hood, a wooden sword and brown boots',
    parts: [],
    created_at: nowIso(-20 * 60_000),
    concept_model: 'pixel',
    selected: {},
    stages: {},
  }
  store.assets.push(hero)
  const hc = seedVersion(hero, 'concept', null, { model: 'pixel', seed: 14, width: 1024, height: 1024, steps: 8, readiness_block: true }, 19, 'b')
  setSelected(hero, hc)
  const ht = seedVersion(hero, 'turnaround', hc, { views: VIEWS, lora_strength: 0.9, seed: 7, auto_reroll: true }, 18, 'b')
  setSelected(hero, ht)
  const hm = seedVersion(hero, 'mesh', ht, { engine: 'trellis', input_view: 'quarter', seed: 2, remesh: true }, 16, 'a')
  setSelected(hero, hm)
  const hp = seedVersion(hero, 'prep', hm, { size_m: 1.2, face_yaw: 'auto', symmetric: true, pivot: 'base', shading: 'auto', seed: 1 }, 14, 'host')
  setSelected(hero, hp)
  const hr = seedVersion(hero, 'rig', hp, { template: 'auto', group_per_vertex: 4, bottom_center_origin: true, bend_test: true, motion: 'legs', moves: SAMPLE_CLIPS, hitboxes: 'simple', hitbox_fit: 'normal' }, 12, 'b')
  setSelected(hero, hr)
  const he = seedVersion(hero, 'export', hr, { lods: '1.0, 0.5, 0.25', collider: 'auto', formats: ['fbx', 'glb'], unity: true }, 10, 'host')
  he.metrics.unity_project_import = {
    ok: null,
    state: 'sent',
    project: 'D:/Games/HollowKeep',
    request_id: '4f1c0a9e2b7d4c55a1e0f3b6c9d8e712',
    folder: 'D:/Games/HollowKeep/Assets/VGAC/Pixel_hero/_incoming',
    prefab: '',
    error: '',
    note: 'Your Unity project was open, so the asset waits in Assets/VGAC/Pixel_hero/_incoming and imports itself the next time you switch to Unity (or use Tools > VGAC > Import pending assets).',
  }
  setSelected(hero, he)

  // A variant of the knight: same identity, winter outfit.
  const winter: Asset = {
    id: uid('a'),
    project_id: p1.id,
    name: 'Knight - winter cloak and fur boots',
    category: 'humanoid',
    style_preset: 'painterly',
    platform_target: 'pc_mid',
    prompt: `${knight.prompt}, winter cloak and fur boots`,
    parts: ['sword', 'shield'],
    created_at: nowIso(-25 * 60_000),
    base_asset_id: knight.id,
    variant_change: 'winter cloak and fur boots',
    selected: {},
    stages: {},
  }
  store.assets.push(winter)
  const wc0 = newVersion(winter, 'concept', null, { ...c2.params, copied_from: c2.id })
  wc0.status = 'done'
  wc0.created_at = nowIso(-25 * 60_000)
  wc0.files = { ...c2.files }
  wc0.metrics = { ...c2.metrics }
  const wc1 = newVersion(winter, 'concept', wc0.id, { ...c2.params, seed: 23, instruction: 'winter cloak and fur boots' })
  wc1.status = 'done'
  wc1.created_at = nowIso(-24 * 60_000)
  fabricate(winter, wc1)
  doneJob(winter, wc1, 'concept.refine', 'b', 24)
  setSelected(winter, wc1)
}

// A prep version whose painted-on shadows were removed (3.9): the corrected colour map and the one before it,
// the light score before and after, and the baked_light check saying so.
function removeShadows(asset: Asset, version: Version) {
  const base = `projects/${asset.project_id}/assets/${asset.id}/prep/${version.id}`
  const hue = hueFor(asset, 1)
  version.files.base_color_original = putDataFile(`${base}/base_color_original.png`, makeTexture('base_color', hue, 512))
  version.files.base_color = putDataFile(`${base}/base_color.png`, makeTexture('base_color', hue + 8, 512))
  Object.assign(version.metrics, { delight_applied: true, delight_mode: 'remove', delight_strength: 0.6, light_score_before: 0.31, light_score: 0.06 })
  const checks = version.metrics.checks as MockCheck[]
  const i = checks.findIndex((c) => c.id === 'baked_light')
  if (i >= 0) checks[i] = chk('baked_light', true, 0.06, 0.15, { severity: 'warn', vars: { kept: false, removed: true } })
}

// The axis word a category's size is said in: the category's size_word, else from its axis.
function wordOf(category: string): string {
  const c = categories.find((x) => x.id === category)
  if (c?.size_word) return c.size_word
  return c?.size_axis === 'height' ? 'tall' : c?.size_axis === 'horizontal' ? 'long' : 'across'
}

// GET /api/setup in the mock: everything in place except the optional Unity editor (one warning example).
export function setupReport(): SetupReport {
  const it = (id: string, group: SetupItem['group'], label: string, state: SetupItem['state'], detail: string, fix: string, needed_for: string[], technical: string): SetupItem =>
    ({ id, group, label, state, detail, fix, needed_for, technical })
  const items: SetupItem[] = [
    it('blender', 'tools', 'Blender', 'ok', 'Blender 5.2.3 is ready.', '', ['mesh', 'prep', 'rig', 'export'], 'blender_exe in backend/config.yaml: F:/Tools/blender-5.2.3-windows-x64/blender.exe (version 5.2.3)'),
    it('unity', 'tools', 'Unity editor', 'warn', 'The Unity editor was not found, so the app cannot try the asset in Unity for you. Everything else works; the hand-off still makes the files.', 'Install the Unity editor, or set unity_exe in backend/config.yaml to its Unity.exe.', ['export'], 'unity_exe in backend/config.yaml: C:/Program Files/Unity/Hub/Editor/6000.4.3f1/Editor/Unity.exe'),
    it('unity_project', 'tools', 'Unity test project', 'ok', 'The Unity test project is there.', '', ['export'], 'unity_project in backend/config.yaml'),
    it('clips.ual1', 'tools', 'Shared animation library', 'ok', 'The shared animations (walk, run, idle and more) are there.', '', ['export'], 'clips_libraries.ual1 in backend/config.yaml'),
    it('skintoken_blender', 'tools', 'Blender for rigging', 'ok', 'The rigging engine knows where Blender is.', '', ['rig'], 'SKINTOKEN_BLENDER_BIN in runtime/start-comfy-b.ps1'),
    it('engine.a', 'engines', 'Graphics engine on the RTX 3060', 'ok', 'Running.', '', ['mesh'], 'instance a at http://127.0.0.1:8188; device cuda:1 NVIDIA GeForce RTX 3060'),
    it('nodes.a.mesh.0', 'engines', 'Parts for building and baking the 3D model (TRELLIS.2)', 'ok', 'All parts are installed.', '', ['mesh'], 'instance a: node classes Trellis2ShapeStage, BakeTextureFromVoxel, ...'),
    it('engine.b', 'engines', 'Graphics engine on the RTX 3080', 'unknown', 'Not running now; it starts when needed.', '', ['concept', 'turnaround', 'rig', 'material', 'judge'], 'instance b at http://127.0.0.1:8189'),
    it('nodes.b.rig.0', 'engines', 'Parts for rigging (SkinToken)', 'unknown', 'Not checked: the engine is not running; it starts when needed.', '', ['rig'], 'instance b: SkinTokenRigTrimesh, VGACLoadTrimesh, PreviewAny'),
    it('model.a.trellis2', 'models', 'TRELLIS.2', 'ok', 'Installed.', '', ['mesh'], 'instance a: trellis_2_int8_convrot.safetensors (UNETLoader.unet_name; models.yaml trellis2)'),
    it('model.b.zturbo', 'models', 'Z-Image Turbo', 'unknown', 'Not checked: the engine is not running; it starts when needed.', '', ['concept'], 'instance b: z-image-turbo-fp8-aio.safetensors (CheckpointLoaderSimple.ckpt_name; models.yaml zturbo)'),
    it('disk.F:', 'storage', 'Free space on F:', 'ok', '412 GB is free on F:.', '', [], 'library_path on F:\: 412.0 GB free'),
    it('memory', 'storage', 'Free memory', 'ok', '28 GB of memory is free.', '', [], 'min_free_ram_gb in backend/config.yaml: 8; 28.0 GB free now'),
  ]
  const fails = items.filter((i) => i.state === 'fail').length
  const warns = items.filter((i) => i.state === 'warn').length
  const unknown = items.filter((i) => i.state === 'unknown').length
  return {
    summary: { ok: fails === 0, fails, warns, unknown, affected: ['export'] },
    items,
    groups: [
      { id: 'tools', label: 'Tools' },
      { id: 'engines', label: 'Graphics engines' },
      { id: 'models', label: 'Models' },
      { id: 'storage', label: 'Storage' },
    ],
    checked_at: new Date().toISOString().slice(0, 19),
  }
}
