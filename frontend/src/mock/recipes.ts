// Recipes and LoRAs (add-ons) for the mock backend: the same shapes as backend reference.recipe_public and data/addons.yaml.
import type { AddonEntry, Recipe, StrengthName } from '../api'

export const ADDONS: AddonEntry[] = [
  {
    id: 'qwen_lightning',
    label: 'Lightning LoRA (fast turnaround)',
    help: {
      meaning: 'Lets the side-view model (Qwen-Image-Edit) finish each side view in 4 quick passes instead of 40.',
      why: 'The turnaround takes seconds per view instead of minutes. It is part of the turnaround and cannot be switched off.',
      technical: 'Qwen-Image-Edit-2511 Lightning 4-step LoRA at strength 1.0',
    },
    stage: 'turnaround',
    base_models: ['qwen_edit'],
    strengths: { subtle: 1.0, normal: 1.0, strong: 1.0 },
    default_strength: 'normal',
    required: true,
    trigger: '',
    licence: 'Apache-2.0',
    source: 'models.yaml entry "qwen_lightning_lora"',
  },
  {
    id: 'qwen_angles',
    label: 'Multiple-angles LoRA (camera angles)',
    help: {
      meaning: 'Teaches the side-view model (Qwen-Image-Edit) to turn the camera around your asset for the side and back views.',
      why: 'Without it the views do not turn. Subtle keeps more of the original look; strong turns more reliably. The standard way of turning the views always loads it.',
      technical: 'Qwen-Image-Edit-2511 multiple-angles LoRA (lora_strength)',
    },
    stage: 'turnaround',
    base_models: ['qwen_edit'],
    strengths: { subtle: 0.75, normal: 0.9, strong: 1.05 },
    default_strength: 'normal',
    required: true,
    trigger: '',
    licence: 'Apache-2.0',
    source: 'models.yaml entry "qwen_angles_lora"',
    turn_method: 'standard',
  },
  {
    id: 'turnaround_sheet',
    label: 'Character turnaround sheet LoRA (all sides in one picture)',
    help: {
      meaning: 'Draws all five sides of a character in one picture, so the clothes and details match from side to side.',
      why: 'The closest match between the views for people, in a third of the time. Characters only, and it makes the look more photographic, so it suits the Photoreal look best.',
      technical: 'Qwen-Image-Edit-2511 character turnaround sheet LoRA (tarn59) at 1.0 instead of the angles LoRA. Bake-off 5.1.',
    },
    stage: 'turnaround',
    base_models: ['qwen_edit'],
    strengths: { subtle: 1.0, normal: 1.0, strong: 1.0 },
    default_strength: 'normal',
    required: false,
    trigger: '',
    licence: 'Apache-2.0',
    source: 'https://huggingface.co/tarn59/character_turnaround_sheet_qwen_edit_2511',
    turn_method: 'sheet',
  },
  {
    id: 'turnaround_white',
    label: 'Camera-angle LoRA (shadow-free prop views)',
    help: {
      meaning: 'Turns the camera around a prop on a clean white background, with no shadows.',
      why: 'Shadow-free views give the 3D model cleaner colours. The colours match the concept a little less well than the standard way. Rigid props only.',
      technical: 'Qwen-Image-Edit-2511 camera-angle LoRA (berkerdooo) at 1.0 instead of the angles LoRA. Bake-off 5.2.',
    },
    stage: 'turnaround',
    base_models: ['qwen_edit'],
    strengths: { subtle: 1.0, normal: 1.0, strong: 1.0 },
    default_strength: 'normal',
    required: false,
    trigger: '',
    licence: 'Apache-2.0',
    source: 'https://huggingface.co/berkerdooo/qwen-image-edit-2511-camera-angle-lora',
    turn_method: 'shadow_free',
  },
]

// The ways of turning the views per category (backend reference.turn_methods_for).
export const TURN_METHODS_BY_CATEGORY: Record<string, string[]> = { humanoid: ['standard', 'sheet'], prop_rigid: ['standard', 'shadow_free'] }

export const CONCEPT_CATALOG = ADDONS.filter((a) => a.stage === 'concept')

type StyleRecipe = {
  look: string
  model: string
  plain: string
  mesh?: { triangle_scale?: number; triangle_floor?: number; texture_cap?: number; normal_map?: boolean }
  surface?: { shading?: 'smooth' | 'flat' }
  addons?: { id: string; strength: StrengthName }[]
  delight?: 'remove' | 'keep'
}

const STYLE_RECIPES: Record<string, StyleRecipe> = {
  retro64: {
    look: 'Retro 64',
    model: 'zturbo',
    plain: 'chunky shapes with fewer triangles, small soft textures, no surface bumps.',
    mesh: { triangle_scale: 0.4, triangle_floor: 300, texture_cap: 256, normal_map: false },
  },
  lowpoly_flat: {
    look: 'Low-poly',
    model: 'zturbo',
    plain: 'faceted shapes with one flat colour per face, a quarter of the usual triangles, simple textures.',
    mesh: { triangle_scale: 0.25, triangle_floor: 300, texture_cap: 512, normal_map: false },
    surface: { shading: 'flat' },
  },
  realistic_pbr: { look: 'Realistic', model: 'zbase', plain: 'full detail, painted-in shadows removed, colours and shine kept in real-world ranges.', delight: 'remove' },
  painted: { look: 'Painted', model: 'zturbo', plain: 'soft painted colours and full detail for your platform.' },
  toon: { look: 'Toon', model: 'klein', plain: 'bold outlines and flat colours, full detail for your platform.' },
  cel: { look: 'Cel-shaded', model: 'anima', plain: 'flat colour regions and two-tone shading, full detail for your platform.' },
}

const NOUNS: Record<string, string> = {
  humanoid: 'character',
  quadruped: 'creature',
  winged: 'flying creature',
  multileg: 'creature',
  serpent: 'creature',
  vehicle: 'vehicle',
  prop_rigid: 'prop',
  prop_articulated: 'prop',
  environment: 'scenery piece',
  foliage: 'plant',
  material: 'material',
}

let counter = 0
const ids = new Map<string, string>()

export function mockRecipe(style: string, category: string, platform: string, budget: { triangles: number; texture: number }): Recipe {
  const s = STYLE_RECIPES[style] ?? { look: style ? style.charAt(0).toUpperCase() + style.slice(1) : 'Default', model: 'zturbo', plain: 'the full pipeline for your platform.' }
  const scale = s.mesh?.triangle_scale ?? 1
  const floor = s.mesh?.triangle_floor ?? 0
  const tris = budget.triangles ? Math.min(budget.triangles, Math.max(floor, Math.round(budget.triangles * scale))) : null
  const cap = s.mesh?.texture_cap ?? null
  const addons: { id: string; strength: StrengthName }[] = [...(s.addons ?? [])]
  for (const a of ADDONS) {
    const conceptLocked = a.stage === 'concept' && a.base_models.includes(s.model) && a.required
    if ((conceptLocked || (a.required && a.stage === 'turnaround')) && !addons.some((d) => d.id === a.id)) addons.push({ id: a.id, strength: a.default_strength })
  }
  const chosen = new Map(addons.map((d) => [d.id, d.strength]))
  const turns = TURN_METHODS_BY_CATEGORY[category] ?? ['standard']
  const entries: AddonEntry[] = ADDONS.filter((a) => (a.stage === 'concept' ? a.base_models.includes(s.model) : turns.includes(a.turn_method ?? 'standard'))).map((a) => ({
    ...a,
    on: chosen.has(a.id),
    strength: chosen.get(a.id) ?? a.default_strength,
  }))
  const key = `${style}|${category}|${platform}`
  if (!ids.has(key)) ids.set(key, (0x5a17c0de + ++counter * 7919).toString(16).slice(-12).padStart(12, '0'))
  return {
    id: ids.get(key)!,
    style,
    category,
    platform,
    plain: `${s.look} ${NOUNS[category] ?? category}: ${s.plain}`,
    concept: { model: s.model, steps: null, prompt_block: '', negative: '' },
    addons,
    turnaround: { lora_strength: 0.9, prompt_suffix: '' },
    mesh: {
      triangle_scale: scale,
      triangle_floor: floor,
      triangles: tris,
      budget_triangles: budget.triangles || null,
      texture_cap: cap,
      texture: cap ? Math.min(cap, budget.texture) : budget.texture,
      budget_texture: budget.texture,
      normal_map: s.mesh?.normal_map ?? true,
    },
    surface: {
      shading: s.surface?.shading ?? 'smooth',
      delight: s.delight ?? 'keep',
      pbr_fix: style === 'realistic_pbr',
    },
    addon_entries: entries,
  }
}

// metrics.recipe as a version records it: id, style, category, platform, plain and the named sections.
export function recipeRecord(r: Recipe, ...sections: ('concept' | 'addons' | 'turnaround' | 'mesh' | 'surface')[]): Record<string, unknown> {
  const out: Record<string, unknown> = { id: r.id, style: r.style, category: r.category, platform: r.platform, plain: r.plain }
  for (const s of sections) out[s] = r[s]
  return out
}

// What a look changes, as the prep look_applied check says it; null for looks that keep the full pipeline.
export function lookSummary(r: Recipe, shading: string): string | null {
  const special = r.mesh.triangle_scale < 1 || shading === 'flat' || !r.mesh.normal_map || (r.mesh.texture_cap !== null && r.mesh.texture_cap < (r.mesh.budget_texture ?? 0))
  if (!special) return null
  const parts: string[] = []
  if (r.mesh.triangle_scale < 1 && r.mesh.triangles) parts.push(`about ${r.mesh.triangles.toLocaleString('en-US')} triangles`)
  if (shading === 'flat') parts.push('flat faces')
  if (r.mesh.texture) parts.push(`${r.mesh.texture} px textures`)
  if (!r.mesh.normal_map) parts.push('no surface bumps')
  return parts.join(', ')
}

export const LOOK_OF: Record<string, string> = Object.fromEntries(Object.entries(STYLE_RECIPES).map(([k, v]) => [k, v.look]))
