// Recipes: how the chosen look carries through every step (GET /api/recipe), and the plain "look" sentence the
// mesh and prep views and the finish card show when the look changed the result.
import { useEffect, useState } from 'react'
import { api, type AddonEntry, type Check, type Recipe, type StrengthName, type Version } from './api'
import { checkPassed, checkSentence } from './help'

const cache = new Map<string, Promise<Recipe | null>>()

export function fetchRecipe(style: string, category: string, platform: string): Promise<Recipe | null> {
  const key = `${style}|${category}|${platform}`
  let p = cache.get(key)
  if (!p) {
    // A backend without recipes (404) or an unknown style reads as no recipe; the screens then show nothing extra.
    p = api.recipe({ style, category, platform }).catch(() => null)
    cache.set(key, p)
    p.then((r) => {
      if (!r) cache.delete(key)
    })
  }
  return p
}

export function useRecipe(style: string | null | undefined, category: string | null | undefined, platform: string | null | undefined, enabled = true): Recipe | null {
  const [recipe, setRecipe] = useState<Recipe | null>(null)
  useEffect(() => {
    if (!enabled || !style || !category || !platform) {
      setRecipe(null)
      return
    }
    let alive = true
    fetchRecipe(style, category, platform).then((r) => alive && setRecipe(r))
    return () => {
      alive = false
    }
  }, [style, category, platform, enabled])
  return recipe
}

// Whether an add-on is part of this painter (or of the turnaround) and cannot be switched off.
export function addonLocked(a: AddonEntry, painter: string | null | undefined): boolean {
  return a.required || (!!painter && a.required_for.includes(painter))
}

export function strengthsEqual(a: AddonEntry): boolean {
  const s = a.strengths ?? ({} as Record<StrengthName, number>)
  return s.subtle === s.normal && s.normal === s.strong
}

// The look word per style, as the backend's styles.yaml `look` field says it.
const LOOK_WORD: Record<string, string> = {
  painted: 'Painted',
  cel: 'Cel-shaded',
  pixel: 'Pixel-art',
  retro64: 'Retro 64',
  lowpoly_flat: 'Low-poly',
  realistic_pbr: 'Photoreal',
  realistic: 'Realistic',
  clay: 'Clay',
}

type RecipeRecord = Partial<Pick<Recipe, 'style' | 'plain'>> & { mesh?: Partial<Recipe['mesh']>; surface?: Partial<Recipe['surface']> }

export type LookSummary = { look: string; summary: string; sentence: string; ok: boolean; crisp: boolean }

const num = (x: unknown): number | null => (typeof x === 'number' && Number.isFinite(x) ? x : null)

function lookWord(rec: RecipeRecord): string {
  if (rec.style && LOOK_WORD[rec.style]) return LOOK_WORD[rec.style]
  // The plain sentence starts with "<Look> <noun>:"; without a known style take the first word.
  const head = (rec.plain ?? '').split(':')[0].trim()
  return head ? head.split(/\s+/)[0] : 'Your'
}

// What metrics.recipe says the look did, in plain words, or null when the look keeps the full pipeline.
export function lookFromRecipe(raw: unknown, metrics: Record<string, unknown> = {}): LookSummary | null {
  if (!raw || typeof raw !== 'object') return null
  const rec = raw as RecipeRecord
  const mesh = rec.mesh ?? {}
  const srf = rec.surface ?? {}
  const scale = num(mesh.triangle_scale) ?? 1
  const shading = (typeof metrics.shading_applied === 'string' ? metrics.shading_applied : null) ?? srf.shading
  const crisp = srf.texture_filter === 'crisp'
  const noBumps = mesh.normal_map === false
  const cap = num(mesh.texture_cap)
  const budgetTex = num(mesh.budget_texture)
  const special = scale < 1 || shading === 'flat' || noBumps || crisp || (cap !== null && budgetTex !== null && cap < budgetTex)
  if (!special) return null
  const parts: string[] = []
  const tris = num(mesh.triangles)
  if (scale < 1 && tris) parts.push(`about ${tris.toLocaleString('en-US')} triangles`)
  if (shading === 'flat') parts.push('flat faces')
  const tex = num(mesh.texture) ?? cap
  const colours = num(srf.palette_colors) ?? num(metrics.texture_colours)
  if (tex) parts.push(`${tex} px ${crisp ? 'crisp ' : ''}textures${crisp && colours ? ` in ${colours} colours` : ''}`)
  else if (crisp) parts.push('crisp pixels')
  if (noBumps) parts.push('no surface bumps')
  if (!parts.length) return null
  const look = lookWord(rec)
  const summary = parts.join(', ')
  return { look, summary, sentence: `${look} look: ${summary}.`, ok: true, crisp }
}

// The look line for a mesh or prep version: the look_applied check's sentence when the backend sent one, else built
// from metrics.recipe.
export function versionLook(v: Version | undefined | null): LookSummary | null {
  if (!v) return null
  const checks = Array.isArray(v.metrics.checks) ? (v.metrics.checks as Check[]) : []
  const check = checks.find((c) => (c.id ?? c.name) === 'look_applied')
  const built = lookFromRecipe(v.metrics.recipe, v.metrics)
  if (check) {
    const look = typeof check.vars?.look === 'string' ? check.vars.look : (built?.look ?? '')
    const summary = typeof check.vars?.summary === 'string' ? check.vars.summary : typeof check.value === 'string' ? check.value : (built?.summary ?? '')
    return { look, summary, sentence: checkSentence(check), ok: checkPassed(check), crisp: built?.crisp ?? /crisp/.test(summary) }
  }
  return built
}
