// Sample data for the mock backend: reference tables, one project with a fully
// staged asset, and the fabricators that fill in files and metrics for new versions.
import type { Asset, Category, Job, LookPreset, Platform, Project, StageId, StageInfo, StylePreset, Version } from '../api'
import { makeBendStrip, makeImage, makeSpriteSheet, makeTexture, type Figure, type ViewName } from './images'

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
    parts_default: ['weapon', 'shield'],
    budgets: { mobile_low: { triangles: 8000, texture: 1024 }, pc_mid: { triangles: 30000, texture: 2048 }, high: { triangles: 80000, texture: 4096 } },
  },
  {
    id: 'quadruped',
    label: 'Quadruped',
    rig_engine: 'skintoken',
    symmetric: true,
    pose_block: 'full body, standing neutral, side three-quarter view, all four legs visible',
    size_m: 1.2,
    parts_default: [],
    budgets: { mobile_low: { triangles: 6000, texture: 1024 }, pc_mid: { triangles: 24000, texture: 2048 }, high: { triangles: 60000, texture: 4096 } },
  },
  {
    id: 'winged',
    label: 'Winged',
    rig_engine: 'skintoken',
    symmetric: true,
    pose_block: 'full body, wings spread, standing, side three-quarter view',
    size_m: 1.0,
    parts_default: [],
    budgets: { mobile_low: { triangles: 6000, texture: 1024 }, pc_mid: { triangles: 24000, texture: 2048 }, high: { triangles: 60000, texture: 4096 } },
  },
  {
    id: 'vehicle',
    label: 'Vehicle / mech',
    rig_engine: 'hinge',
    symmetric: true,
    pose_block: 'full vehicle, three-quarter view, wheels on the ground, no background',
    size_m: 4.0,
    parts_default: ['wheel_fl', 'wheel_fr', 'wheel_rl', 'wheel_rr', 'turret'],
    budgets: { mobile_low: { triangles: 5000, texture: 1024 }, pc_mid: { triangles: 20000, texture: 2048 }, high: { triangles: 50000, texture: 4096 } },
  },
  {
    id: 'prop_rigid',
    label: 'Prop (rigid)',
    rig_engine: 'none',
    symmetric: false,
    pose_block: 'single object, three-quarter view, resting on a flat surface',
    size_m: 0.5,
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
    parts_default: ['lid'],
    budgets: { mobile_low: { triangles: 1200, texture: 512 }, pc_mid: { triangles: 5000, texture: 1024 }, high: { triangles: 15000, texture: 2048 } },
  },
  {
    id: 'environment',
    label: 'Environment piece',
    rig_engine: 'none',
    symmetric: false,
    pose_block: 'modular piece, orthographic three-quarter view, grid aligned',
    size_m: 2.0,
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
    ],
  },
  {
    id: 'prep',
    label: 'Prep',
    adjust_schema: [
      { name: 'height_m', label: 'Height (m)', type: 'number', min: 0.05, max: 20, step: 0.05, default: 1.8 },
      { name: 'symmetric', label: 'Symmetrize', type: 'boolean', default: true },
      { name: 'pivot', label: 'Pivot', type: 'select', options: ['base', 'center'], default: 'base' },
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
    ],
  },
  {
    id: 'export',
    label: 'Export',
    adjust_schema: [
      { name: 'lods', label: 'LOD ratios', type: 'string', default: '1.0, 0.5, 0.25, 0.1' },
      { name: 'collider', label: 'Collider', type: 'select', options: ['hull', 'none'], default: 'hull' },
      { name: 'formats', label: 'Formats', type: 'multiselect', options: ['fbx', 'glb'], default: ['fbx', 'glb'] },
      { name: 'unity', label: 'Build Unity prefab', type: 'boolean', default: true },
      { name: 'clips_library', label: 'Clip library', type: 'select', options: ['ual1', 'ual2', 'none'], default: 'ual1' },
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

type MockCheck = { id: string; name: string; passed: boolean; ok: boolean; value?: unknown; limit?: unknown; message?: string; help?: string; fix?: { stage: string; params: Record<string, unknown> }; severity?: 'block' | 'warn' }

const chk = (id: string, ok: boolean, value: unknown, limit: unknown, extra: Partial<MockCheck> = {}): MockCheck => ({
  id,
  name: id,
  passed: ok,
  ok,
  value,
  limit,
  message: `${Array.isArray(value) ? JSON.stringify(value) : String(value)} (limit ${Array.isArray(limit) ? JSON.stringify(limit) : String(limit)})`,
  ...extra,
})

// Same shape as the real backend; some entries carry help/fix/severity to exercise
// the plain-language checks and the "Fix automatically" button.
const checksFor = (stage: StageId, seed: number, budget = 20000): MockCheck[] => {
  const fail = seed % 3 === 0
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
        chk('triangle_budget', !fail, fail ? budget + 210 : budget - 1580, budget, fail ? { fix: { stage: 'mesh', params: { target_faces: budget } }, help: `The model has 210 more triangles than this platform can handle (${(budget + 210).toLocaleString('en-US')} of ${budget.toLocaleString('en-US')}).` } : {}),
        chk('manifold', !fail, fail ? 8 : 0, 0, fail ? { fix: { stage: 'mesh', params: { remesh: true } } } : {}),
        chk('watertight', true, 0, 0),
        chk('degenerate_faces', true, 0, 0),
        chk('height_m', true, 1.8, 1.8),
        chk('uv_set', true, 1, 1),
      ]
    case 'rig':
      return [
        chk('bones', true, 20, '>0'),
        chk('single_root', true, 1, 1),
        chk('weight_coverage', !fail, fail ? 0.9889 : 0.998, 0.99, fail ? { severity: 'warn', fix: { stage: 'rig', params: { group_per_vertex: 6 } } } : {}),
        chk('max_influences', true, 4, 4),
        chk('weights_normalised', true, 0, 0),
      ]
    case 'export':
      return [
        chk('lod_count', true, 4, 4),
        chk('lod1_ratio', true, 0.5, 0.5),
        chk('collider_present', true, 212, '>0'),
        chk('collider_vertices', true, 212, 255),
        chk('unity_import', true, true, true),
        chk('unity_humanoid_avatar', true, true, true),
      ]
    default:
      return []
  }
}

// Fill in files and metrics for a finished version.
export function fabricate(asset: Asset, version: Version) {
  const seed = Number(version.params.seed ?? 1) || 1
  const hue = hueFor(asset, seed)
  const figure = figureFor(asset)
  const base = `projects/${asset.project_id}/assets/${asset.id}/${version.stage}/${version.id}`
  const f: Record<string, string> = {}
  const m: Record<string, unknown> = {}
  switch (version.stage) {
    case 'concept': {
      f.image = putDataFile(`${base}/concept.png`, makeImage({ w: 512, h: 512, hue, seed, figure, label: `seed ${seed}` }))
      f.cutout = putDataFile(`${base}/cutout.png`, makeImage({ w: 512, h: 512, hue, seed, figure, transparent: true }))
      m.cutout = { coverage: Number((0.15 + (seed % 7) / 100).toFixed(3)), empty: false, aspect: 0.44 }
      m.elapsed_seconds = 16 + (seed % 5)
      m.model = version.params.model ?? 'zturbo'
      m.seed = seed
      break
    }
    case 'turnaround': {
      const views = (version.params.views as ViewName[] | undefined) ?? VIEWS
      const perView: Record<string, unknown> = {}
      views.forEach((view, i) => {
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
      m.checks = checksFor('prep', seed, categories.find((c) => c.id === asset.category)?.budgets[asset.platform_target]?.triangles ?? 20000)
      m.gate_ok = (m.checks as MockCheck[]).every((c) => c.ok)
      m.checks_failed = (m.checks as MockCheck[]).filter((c) => !c.ok).map((c) => c.id)
      break
    }
    case 'rig': {
      f.glb = 'samples/sample.glb'
      const bones = bipedBones()
      f.rig_info = putText(`${base}/rig_info.json`, JSON.stringify({ template: 'mixamo', bone_segments: bones }, null, 2))
      f.bend_strip = putDataFile(`${base}/bend_strip.png`, makeBendStrip(hue, ['LeftArm', 'LeftForeArm', 'LeftUpLeg', 'LeftLeg']))
      m.bones = bones.length
      m.template = version.params.template === 'auto' || !version.params.template ? 'mixamo' : version.params.template
      m.max_influences = version.params.group_per_vertex ?? 4
      m.unweighted_vertices = 0
      m.checks = checksFor('rig', seed)
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
      const clips = ['idle', 'walk', 'run', 'jump', 'attack', 'hit', 'death']
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
      m.unity_report = {
        ok: true,
        prefab: `Assets/VGAC/${asset.name}/${asset.name}.prefab`,
        avatar: 'Humanoid',
        warnings: ['LOD3 is under 10 percent of LOD0; Unity will cull it at 3 percent screen height'],
        errors: [],
      }
      m.checks = checksFor('export', seed)
      m.gate_ok = true
      break
    }
  }
  version.files = f
  version.metrics = m
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
  const pr1 = seedVersion(knight, 'prep', m2, { height_m: 1.8, symmetric: true, pivot: 'base', smooth_angle: 60, seed: 3 }, 120, 'host')
  setSelected(knight, pr1)

  const r1 = seedVersion(knight, 'rig', pr1, { template: 'auto', target_faces: 20_000, group_per_vertex: 4, bottom_center_origin: true, bend_test: true }, 80, 'b')
  setSelected(knight, r1)

  const e1 = seedVersion(knight, 'export', r1, { lods: '1.0, 0.5, 0.25, 0.1', collider: 'hull', formats: ['fbx', 'glb'], unity: true, clips_library: 'ual1', sprites: true, sprite_directions: 8, sprite_size: 64 }, 30, 'host')
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
