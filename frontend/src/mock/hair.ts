// Mock of "Hair from the library" (docs/HAIR-LIBRARY-PLAN.md): a small catalogue, the bald pictures at Check all
// sides, the ranked candidates at the 3D step, the fitted hair at Clean up, the hair on the skeleton at Make it move,
// and GET /api/assets/{aid}/hair. A description with "waist" gets candidates that do not fit well (nothing_fits).
import type { Asset, HairCandidate, HairPanel, HairStyleEntry, Version } from '../api'
import { hairLibraryOffer } from '../pipeline/HairSetting'
import { fitWords } from '../pipeline/HairPicker'
import { makeHairThumb, makeImage, type HairShape, type ViewName } from './images'

type Put = (path: string, dataUrl: string) => string

const S = (id: string, name: string, family: string, kind: 'shell' | 'cards', triangles: number, author: string, pack: string): HairStyleEntry => ({
  id,
  name,
  family,
  kind,
  triangles,
  author,
  pack,
  licence: 'CC0-1.0',
  source_url: pack.startsWith('Quaternius') ? 'https://quaternius.com' : 'https://static.makehumancommunity.org/assets/assetpacks/hair01.html',
  phone_ok: kind === 'shell' && triangles <= 750,
})

export const HAIR_CATALOGUE: HairStyleEntry[] = [
  S('quaternius_simpleparted', 'Simple parted', 'short', 'shell', 830, 'Quaternius', 'Quaternius Universal Base Characters'),
  S('quaternius_buzzed', 'Buzzed', 'short', 'shell', 700, 'Quaternius', 'Quaternius Universal Base Characters'),
  S('quaternius_long', 'Long', 'long', 'shell', 3284, 'Quaternius', 'Quaternius Universal Base Characters'),
  S('quaternius_buns', 'Buns', 'bun', 'shell', 1960, 'Quaternius', 'Quaternius Universal Base Characters'),
  S('littleright_bobcut', 'Bob cut', 'bob', 'cards', 2410, 'littleright', 'MakeHuman Hair 01'),
  S('toigo_bob_short', 'Short bob', 'bob', 'cards', 1880, 'Toigo', 'MakeHuman Hair 01'),
  S('cortu_straight_bangs', 'Straight with bangs', 'long', 'cards', 2900, 'Cortu', 'MakeHuman Hair 01'),
  S('cortu_short_messy', 'Short and messy', 'short', 'cards', 2240, 'Cortu', 'MakeHuman Hair 01'),
  S('ponytail01', 'Low ponytail', 'ponytail', 'cards', 2650, 'MakeHuman team', 'MakeHuman system assets'),
  S('vgac_high_ponytail', 'High ponytail', 'ponytail', 'shell', 2180, 'VGAC (made by rule)', 'VGAC generated styles'),
  S('rehmanpolanski_bun', 'Neat bun', 'bun', 'cards', 2050, 'Rehman Polanski', 'MakeHuman Hair 01'),
  S('braid01', 'Single braid', 'braid', 'cards', 3000, 'MakeHuman team', 'MakeHuman system assets'),
  S('afro01', 'Afro', 'afro', 'cards', 3400, 'MakeHuman team', 'MakeHuman system assets'),
  S('vgac_waist_long', 'Waist-length', 'long', 'shell', 3500, 'VGAC (made by rule)', 'VGAC generated styles'),
]

const HAIR_HUE = 28

let thumbsMade = false
export function hairCatalogue(): HairStyleEntry[] {
  if (!thumbsMade) {
    for (const s of HAIR_CATALOGUE) s.thumbs = { front: makeHairThumb({ family: s.family, hue: 220, light: 72 }) }
    thumbsMade = true
  }
  return HAIR_CATALOGUE
}

const style = (id: string | null | undefined) => HAIR_CATALOGUE.find((s) => s.id === id) ?? null
export const credit = (s: HairStyleEntry) => `${s.author}, ${s.pack}, CC0`

const FAMILY_WORDS: [string, string[]][] = [
  ['ponytail', ['ponytail', 'pony tail']],
  ['bun', ['bun', 'topknot']],
  ['braid', ['braid', 'braids', 'plait']],
  ['bob', ['bob']],
  ['afro', ['afro']],
  ['long', ['long']],
  ['short', ['short', 'cropped', 'buzz cut']],
]
export function familyOf(prompt: string): { family: string | null; words: string[] } {
  const t = ' ' + prompt.toLowerCase().replace(/[^a-z]+/g, ' ') + ' '
  for (const [family, words] of FAMILY_WORDS) {
    const hit = words.filter((w) => t.includes(' ' + w + ' '))
    if (hit.length) return { family, words: hit.map((w) => `${w}`) }
  }
  return { family: null, words: [] }
}

export const isLibrary = (a: Asset) => a.hair?.source === 'library'
const hairShape = (family: string): HairShape => ({ family, hue: HAIR_HUE })

function keptOf(a: Asset, stage: 'turnaround' | 'mesh' | 'prep' | 'rig'): Version | undefined {
  const list = a.stages[stage] ?? []
  return list.find((v) => v.id === a.selected[stage]) ?? list.find((v) => v.selected)
}

// The ranked candidates for an asset's description: the family's styles first, best first.
function rank(a: Asset): HairCandidate[] {
  const { family } = familyOf(a.prompt)
  const phone = a.platform_target === 'mobile_low'
  const offered = HAIR_CATALOGUE.filter((s) => !phone || s.phone_ok)
  const pool = family ? [...offered.filter((s) => s.family === family), ...offered.filter((s) => s.family !== family)] : offered
  const poor = /\bwaist\b/i.test(a.prompt)
  const scores = poor ? [0.55, 0.51, 0.47, 0.42] : family ? [0.78, 0.71, 0.64, 0.57] : [0.66, 0.63, 0.6, 0.58]
  return pool.slice(0, 4).map((s, i) => ({ style: s.id, name: s.name, score: scores[i], fit_words: fitWords(scores[i]), picture_url: null, triangles: s.triangles, kind: s.kind, credit: credit(s) }))
}

// Files and metrics for library hair, added to what fabricate made. f and m are the version's new files and metrics.
export function fabricateHair(a: Asset, v: Version, f: Record<string, string>, m: Record<string, unknown>, base: string, put: Put, hue: number) {
  const checks = (m.checks ??= []) as Record<string, unknown>[]
  const chk = (id: string, ok: boolean, value: unknown, limit: unknown, vars: Record<string, unknown>) =>
    checks.push({ id, name: id, passed: ok, ok, value, limit, severity: 'warn', vars, message: `${String(value)} (limit ${String(limit)})` })
  const seed = Number(v.params.seed ?? 1) || 1
  switch (v.stage) {
    case 'turnaround': {
      if (!isLibrary(a) && !v.params.bald_views && !v.params.bald_only) return
      v.params.bald_views = true
      if (v.params.bald_seed === null || v.params.bald_seed === undefined) v.params.bald_seed = 1000 + (seed % 900)
      const { family } = familyOf(a.prompt)
      for (const view of ['front', 'quarter', 'left', 'right'] as ViewName[]) {
        // The haired views of this asset (re-drawn with hair for the mock) and their bald copies.
        f[view] = put(`${base}/${view}.png`, makeImage({ w: 384, h: 512, hue, seed, view, label: view, head: hairShape(family ?? 'bob') }))
        f[`${view}_cutout`] = put(`${base}/${view}_cutout.png`, makeImage({ w: 384, h: 512, hue, seed, view, transparent: true, head: hairShape(family ?? 'bob') }))
        f[`bald_${view}`] = put(`${base}/bald_${view}.png`, makeImage({ w: 384, h: 512, hue, seed, view, label: `${view}, bald`, head: 'bald' }))
        f[`bald_${view}_cutout`] = put(`${base}/bald_${view}_cutout.png`, makeImage({ w: 384, h: 512, hue, seed, view, transparent: true, head: 'bald' }))
      }
      const rerolled = Number(v.params.bald_seed) % 4 === 1
      const kept = 0.96
      m.bald = { kept_share: kept, face_delta: 2.1, rerolled, seed: v.params.bald_seed }
      chk('bald_picture', true, kept, 0.9, { face_delta: 2.1, rerolled })
      return
    }
    case 'mesh': {
      if (!isLibrary(a) || v.params.hair_source === 'model' || v.params.engine === 'hy3d') return
      v.params.hair_source = 'library'
      const { family } = familyOf(a.prompt)
      const cands = rank(a)
      cands.forEach((c, i) => {
        const s = style(c.style)!
        c.picture_url = f[`hair_cand_${i + 1}`] = put(`${base}/hair_cand_${i + 1}.png`, makeImage({ w: 320, h: 420, hue, seed, view: 'quarter', head: hairShape(s.family) }))
      })
      const best = cands[0]
      const nothing = best.score < 0.6
      m.hair_library = { family, family_source: family ? 'description' : 'none', candidates: cands.map((c) => ({ style: c.style, name: c.name, score: c.score, triangles: c.triangles, kind: c.kind, picture: c.picture_url, credit: c.credit })), best: best.style, best_score: best.score, nothing_fits: nothing, seconds: 41, error: null }
      chk('hair_fit', !nothing, best.score, 0.6, { step: 'mesh', best_name: best.name, family, family_source: family ? 'description' : 'none', candidates: cands.length })
      if (nothing) checks[checks.length - 1].fix = { stage: 'mesh', params: { hair_source: 'model' }, label: 'Use the hair built with the 3D model' }
      return
    }
    case 'prep': {
      const mesh = keptOf(a, 'mesh')
      const lm = mesh?.metrics.hair_library as { candidates?: { style: string; score: number }[]; best?: string } | undefined
      if (!mesh || mesh.params.hair_source !== 'library' || !lm) return
      const explicit = typeof v.params.hair_style === 'string' && v.params.hair_style_source !== 'best' && v.params.hair_style_source !== 'picked'
      const picked = explicit ? (v.params.hair_style as string) : (a.hair?.pick ?? null)
      const source = explicit ? 'chosen' : a.hair?.pick ? 'picked' : 'best'
      const reused = explicit && v.parent_version_id && (a.stages.prep ?? []).some((x) => x.id === v.parent_version_id) ? v.parent_version_id : null
      const id = picked ?? lm.best ?? HAIR_CATALOGUE[0].id
      const s = style(id) ?? HAIR_CATALOGUE[0]
      const score = lm.candidates?.find((c) => c.style === s.id)?.score ?? 0.62
      const allowance = Math.ceil(Math.max(s.triangles, ...(lm.candidates ?? []).map((c) => style(c.style)?.triangles ?? 0)) / 250) * 250
      const tb = checks.find((c) => c.id === 'triangle_budget')
      const budget = Number(tb?.limit) || (a.platform_target === 'mobile_low' ? 5000 : 20000)
      const body = Math.min(Number(tb?.value) || budget, budget) - allowance - 120
      const more = v.params.hair_cover === 'more'
      const skin = more ? 0.004 : seed % 2 === 0 ? 0.035 : 0.0
      v.params.hair_source = 'library'
      v.params.hair_style = s.id
      v.params.hair_style_source = source
      v.params.hair_allowance = allowance
      f.with_hair_glb = 'samples/sample.glb'
      f.hair_fit_sheet = put(`${base}/hair_fit_sheet.png`, makeImage({ w: 640, h: 320, hue, seed, view: 'front', label: 'fit sheet', head: hairShape(s.family) }))
      m.hair_library = { attached: true, style: s.id, name: s.name, family: s.family, score, triangles: s.triangles, cap_triangles: 180, allowance, skin_through: skin, kind: s.kind, alpha: s.kind === 'cards' ? 'cutout' : 'opaque', two_sided: s.kind === 'cards', credit: credit(s), extra_height_m: s.family === 'bun' ? 0.09 : 0.03, seconds: 28, reused_body_of: reused }
      m.triangles = body + s.triangles + 180
      chk('hair_fit', score >= 0.6, score, 0.6, { step: 'prep', style_name: s.name, picked: !!picked, family: s.family })
      chk('hair_skin_through', skin <= 0.02, skin, 0.02, { crown: skin * 1.4, sides: skin * 0.6 })
      // A description with "heavy" goes over the budget, so "Choose a lighter hairstyle" can be tried.
      const heavy = /\bheavy\b/i.test(a.prompt) && !v.params.hair_style_source?.toString().startsWith('chosen')
      chk('hair_triangles', !heavy, body + s.triangles + 180 + (heavy ? 1500 : 0), budget, { hair: s.triangles + 180, body, allowance })
      if (heavy) checks[checks.length - 1].fix = { stage: 'mesh', ui: 'hair_picker', params: {}, label: 'Choose a lighter hairstyle' }
      return
    }
    case 'rig': {
      const prep = keptOf(a, 'prep')
      const lh = prep?.metrics.hair_library as { attached?: boolean; family?: string } | undefined
      if (!lh?.attached) return
      const groups = lh.family === 'ponytail' || lh.family === 'long' || lh.family === 'braid' ? 1 : 0
      m.hair_bind = { ok: true, verts: 1400, rest_share: groups ? 0.7 : 1, nape_blend_verts: 210, groups, seconds: 3.1 }
      chk('hair_bound', true, 1.0, 1.0, { groups_swaying: groups, rest_on_head: !groups })
      return
    }
    case 'export': {
      const prep = keptOf(a, 'prep')
      if (!(prep?.metrics.hair_library as { attached?: boolean } | undefined)?.attached) return
      m.hair_material = { ok: true, slots: [`${a.name}_Body`, `${a.name}_Hair`], source: 'library' }
      checks.push({ id: 'hair_material_shipped', name: 'hair_material_shipped', passed: true, ok: true, value: 2, limit: 2, severity: 'warn', vars: { source: 'library', slots: `${a.name}_Body and ${a.name}_Hair` } })
      return
    }
  }
}

// GET /api/assets/{aid}/hair.
export function hairPanel(a: Asset): HairPanel {
  const offer = hairLibraryOffer(a.prompt, a.style_preset, a.category)
  const source = a.hair?.source ?? 'model'
  const mesh = keptOf(a, 'mesh')
  const prep = keptOf(a, 'prep')
  const turn = keptOf(a, 'turnaround')
  const lm = mesh && mesh.params.hair_source === 'library' ? (mesh.metrics.hair_library as { candidates?: (HairCandidate & { picture?: string })[]; best?: string; nothing_fits?: boolean; family?: string | null } | undefined) : undefined
  const lp = prep?.metrics.hair_library as { attached?: boolean; style?: string; score?: number } | undefined
  const { family, words } = familyOf(a.prompt)
  const meshIsLibrary = mesh?.params.hair_source === 'library'
  const bald = !!turn && 'bald_front' in turn.files
  let rebuild: HairPanel['rebuild'] = null
  if (mesh && source === 'library' && !meshIsLibrary && bald) rebuild = { from_stage: 'mesh', minutes: 9 }
  if (mesh && source === 'model' && meshIsLibrary) rebuild = { from_stage: 'mesh', minutes: 8 }
  return {
    source,
    available: offer.ok,
    unavailable_reason: offer.ok ? null : offer.reason,
    family,
    family_words: words,
    pick: a.hair?.pick ?? null,
    candidates: (lm?.candidates ?? []).map((c) => ({ style: c.style, name: c.name, score: c.score, fit_words: fitWords(c.score), picture_url: c.picture ?? null, triangles: c.triangles, kind: c.kind, credit: c.credit })),
    mesh_version_id: meshIsLibrary ? mesh!.id : null,
    best: lm?.best ?? null,
    nothing_fits: !!lm?.nothing_fits,
    applied: lp?.attached && lp.style ? { style: lp.style, score: lp.score ?? null, prep_version_id: prep!.id } : null,
    rebuild,
    sentence: null,
  }
}
