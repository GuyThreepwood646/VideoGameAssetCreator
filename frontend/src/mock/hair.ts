// Mock of "Hair from the library" (docs/HAIR-LIBRARY-PLAN.md), in the shapes the real backend sends (checked against
// the live app on 2026-11-06): a small catalogue (GET /api/hair-library {styles, families, installed}, credit as an
// object), the bald pictures at Check all sides (metrics.bald), the ranked candidates at the 3D step
// (metrics.hair_library, pictures as files hair_cand_N), the fitted hair at Clean up (skin_through {all, crown, sides}),
// the hair on the skeleton at Make it move (metrics.hair_bind), and GET /api/assets/{aid}/hair.
// Prompt words for trying the rare paths: "waist" gives candidates that do not fit well (nothing_fits), "heavy" fails
// hair_triangles once, "changed face" makes the bald picture fail on the face.
import type { Asset, HairCandidate, HairCreditInfo, HairLibrary, HairPanel, HairStyleEntry, LibraryHairMetrics, MyHairListing, MyHairStyle, Version } from '../api'
import { hairLibraryOffer } from '../pipeline/HairSetting'
import { makeHairCandidate, makeHairThumb, makeImage, type HairShape, type ViewName } from './images'

type Put = (path: string, dataUrl: string) => string
type MockStyle = HairStyleEntry & { author: string; source_url: string }

const S = (id: string, name: string, family: string, kind: 'shell' | 'cards', triangles: number, author: string, site: string): MockStyle => ({
  id,
  name,
  family,
  kind,
  alpha: kind === 'cards' ? 'cutout' : 'opaque',
  triangles,
  phone_ok: kind === 'shell',
  fringe: false,
  parting: 'centre',
  author,
  source_url: site,
})
const Q = 'https://quaternius.com'
const MH = 'http://www.makehumancommunity.org'

const HAIR_STYLES: MockStyle[] = [
  S('q_simple_parted', 'Simple parted', 'short', 'shell', 1014, 'Quaternius', Q),
  S('q_buzzed', 'Buzzed', 'short', 'shell', 1000, 'Quaternius', Q),
  S('q_long', 'Long', 'long', 'shell', 3168, 'Quaternius', Q),
  S('q_buns', 'Buns', 'bun', 'shell', 3284, 'Quaternius', Q),
  S('mh_toigo_blunt_bob', 'Bob, blunt, chin length', 'bob', 'cards', 3116, 'MargaretToigo', MH),
  S('mh_toigo_curled_bob', 'Bob, curled ends', 'bob', 'cards', 3112, 'MargaretToigo', MH),
  S('mh_cortu_straight_bangs', 'Long, straight with bangs', 'long', 'cards', 1128, 'Cortu', MH),
  S('mh_cortu_shaggy', 'Long, shaggy layers', 'long', 'cards', 1548, 'Cortu', MH),
  S('mh_ponytail01', 'Mid ponytail, swept back', 'ponytail', 'cards', 4764, 'MakeHuman team (Data Collection AB)', MH),
  S('rule_high_ponytail', 'High ponytail', 'ponytail', 'shell', 2988, 'VGAC (rule-built)', 'https://github.com'),
  S('mh_rehmanpolanski_bun', 'Neat low bun', 'bun', 'cards', 2050, 'Rehman Polanski', MH),
  S('mh_braid01', 'Single braid', 'braid', 'cards', 3000, 'MakeHuman team (Data Collection AB)', MH),
  S('mh_afro01', 'Afro', 'afro', 'cards', 3400, 'MakeHuman team (Data Collection AB)', MH),
  S('rule_long_centre', 'Long, straight, centre parting', 'long', 'shell', 3500, 'VGAC (rule-built)', 'https://github.com'),
]
const FAMILIES = ['afro', 'bob', 'braid', 'bun', 'long', 'ponytail', 'short']
const HAIR_HUE = 28

const style = (id: string | null | undefined) => HAIR_STYLES.find((s) => s.id === id) ?? null
// The credit object the backend sends everywhere a style is named.
export const credit = (s: MockStyle): HairCreditInfo =>
  s.own
    ? { id: s.id, name: s.name, author: s.author, licence: s.licence ?? 'Not stated', source_url: s.source_url }
    : { id: s.id, name: s.id.replace(/^(q|mh|rule)_/, ''), author: s.author, licence: 'CC0-1.0', source_url: s.source_url }

let thumbsMade = false
// On Phone a solid style uses its lite version (at most 1,000 triangles); cards are not offered there.
const liteOf = (s: MockStyle) => Math.min(s.triangles, 1000)
// GET /api/hair-library, with ?platform= or ?asset_id= the styles offered there and the triangles used there.
export function hairCatalogue(platform: string | null = null): HairLibrary {
  if (!thumbsMade) {
    for (const s of HAIR_STYLES) {
      const shape = { family: s.family, hue: 220, light: 72 }
      s.thumbs = { front: makeHairThumb(shape), quarter: makeHairThumb(shape) }
      s.credit = credit(s)
    }
    thumbsMade = true
  }
  const phone = platform === 'mobile_low'
  const offered = HAIR_STYLES.filter((s) => !phone || s.phone_ok)
  const styles = offered.map(({ author: _a, source_url: _s, ...entry }) =>
    platform ? { ...entry, triangles_used: phone ? liteOf(entry as MockStyle) : entry.triangles, lite_used: phone && liteOf(entry as MockStyle) < entry.triangles } : entry,
  )
  return { styles, families: FAMILIES, installed: true, platform }
}

const FAMILY_WORDS: [string, string[]][] = [
  ['ponytail', ['ponytail', 'pony tail']],
  ['bun', ['bun', 'topknot']],
  ['braid', ['braid', 'braids', 'plait']],
  ['bob', ['bob']],
  ['afro', ['afro']],
  ['long', ['long loose hair', 'long hair', 'long']],
  ['short', ['short hair', 'short', 'cropped', 'buzz cut']],
]
export function familyOf(prompt: string): { family: string | null; words: string[] } {
  const t = ' ' + prompt.toLowerCase().replace(/[^a-z]+/g, ' ') + ' '
  for (const [family, words] of FAMILY_WORDS) {
    const hit = words.find((w) => t.includes(' ' + w + ' '))
    if (hit) return { family, words: [hit] }
  }
  return { family: null, words: [] }
}

export const isLibrary = (a: Asset) => a.hair?.source === 'library'
const hairShape = (family: string): HairShape => ({ family, hue: HAIR_HUE })

function keptOf(a: Asset, stage: 'turnaround' | 'mesh' | 'prep' | 'rig'): Version | undefined {
  const list = a.stages[stage] ?? []
  return list.find((v) => v.id === a.selected[stage]) ?? list.find((v) => v.selected)
}

// The backend's own short fit words (hairfit.fit_words); the picker shows its own sentences instead.
const backendFitWords = (score: number) => (score >= 0.75 ? 'close to the picture' : score >= 0.6 ? 'a fair match' : 'not like the picture')

type MeshCandidate = { style: string; name: string; score: number; iou: number; eye_penalty: number; params: Record<string, unknown>; triangles: number; kind: string }

// The ranked candidates for an asset's description: the family's styles first, best first (metrics.hair_library shape).
function rank(a: Asset): MeshCandidate[] {
  const { family } = familyOf(a.prompt)
  const phone = a.platform_target === 'mobile_low'
  const offered = HAIR_STYLES.filter((s) => !phone || s.phone_ok)
  const pool = family ? [...offered.filter((s) => s.family === family), ...offered.filter((s) => s.family !== family)] : offered
  const poor = /\bwaist\b/i.test(a.prompt)
  const scores = poor ? [0.55, 0.51, 0.47, 0.42] : family ? [0.78, 0.71, 0.64, 0.57] : [0.66, 0.63, 0.6, 0.58]
  return pool.slice(0, 4).map((s, i) => ({ style: s.id, name: s.name, score: scores[i], iou: scores[i], eye_penalty: 0, params: { dz_head: 0, length: 1, mirror: false, volume: 0.85 }, triangles: phone ? liteOf(s) : s.triangles, kind: s.kind as string }))
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
        f[`bald_${view}_white`] = f[`bald_${view}`]
      }
      const bseed = Number(v.params.bald_seed)
      const face = /changed face/i.test(a.prompt)
      const rerolled = face || bseed % 4 === 1
      const kept = 0.976
      const reason = face ? 'face_changed' : null
      const bearded = /\b(beard|bearded|moustache|mustache)\b/i.test(a.prompt)
      const beard = bearded ? { bearded: true, beard_change: 0.04, face_dark: null } : { face_dark: 0.01 }
      m.bald = { kept_share: kept, face_delta: face ? 15.3 : 2.1, face_change: face ? 11.3 : 1.2, ...beard, reason, rerolled, seed: bseed, kept_seed: bseed, seconds: 156, tries: [{ seed: bseed, kept_share: kept, reason }] }
      chk('bald_picture', !face, kept, 0.9, { face_delta: face ? 15.3 : 2.1, ...beard, rerolled, ...(reason ? { reason } : {}) })
      if (face) checks[checks.length - 1].fix = { stage: 'turnaround', params: { bald_only: true, bald_seed: null }, label: 'Make the bald picture again' }
      return
    }
    case 'mesh': {
      if (!isLibrary(a) || v.params.hair_source === 'model' || v.params.engine === 'hy3d') return
      v.params.hair_source = 'library'
      const { family, words } = familyOf(a.prompt)
      const cands = rank(a)
      // Each picture: the character's own head in plain grey with the recoloured hair, front and three-quarter.
      cands.forEach((c, i) => {
        f[`hair_cand_${i + 1}`] = put(`${base}/hair_rank/hair_cand_${i + 1}.png`, makeHairCandidate(hairShape(style(c.style)!.family)))
      })
      const best = cands[0]
      const nothing = best.score < 0.6
      m.hair_library = { best: best.style, best_score: best.score, candidates: cands, error: null, family, family_source: family ? 'description' : 'none', family_words: words, nothing_fits: nothing, offered: cands.length + 1, searched: cands.length + 1, seconds: 33, timed_out: false, yaw: 0 }
      chk('hair_fit', !nothing, best.score, 0.6, { step: 'mesh', best_name: best.name, family, family_source: family ? 'description' : 'none', candidates: cands.length, picked: false, colour_delta: null, eye_cover: null })
      if (nothing) checks[checks.length - 1].fix = { stage: 'mesh', params: { hair_source: 'model' }, label: 'Use the hair built with the 3D model' }
      return
    }
    case 'prep': {
      const mesh = keptOf(a, 'mesh')
      const lm = mesh?.metrics.hair_library as { candidates?: MeshCandidate[]; best?: string; family_source?: string } | undefined
      if (!mesh || mesh.params.hair_source !== 'library' || !lm) return
      const explicit = typeof v.params.hair_style === 'string' && v.params.hair_style_source !== 'best' && v.params.hair_style_source !== 'picked'
      const picked = explicit ? (v.params.hair_style as string) : (a.hair?.pick ?? null)
      const source = explicit ? 'chosen' : a.hair?.pick ? 'picked' : 'best'
      const reused = explicit && v.parent_version_id && (a.stages.prep ?? []).some((x) => x.id === v.parent_version_id) ? v.parent_version_id : null
      const s = style(picked ?? lm.best) ?? HAIR_STYLES[0]
      const score = lm.candidates?.find((c) => c.style === s.id)?.score ?? 0.62
      const allowance = Math.ceil(Math.max(s.triangles, ...(lm.candidates ?? []).map((c) => c.triangles)) / 250) * 250
      const tb = checks.find((c) => c.id === 'triangle_budget')
      const budget = Number(tb?.limit) || (a.platform_target === 'mobile_low' ? 5000 : 20000)
      const body = Math.min(Number(tb?.value) || budget, budget) - allowance - 120
      const cap = 262
      const skinAll = v.params.hair_cover === 'more' ? 0.004 : seed % 2 === 0 ? 0.035 : 0.0
      const skin = { all: skinAll, crown: skinAll * 1.4, sides: skinAll * 0.6 }
      v.params.hair_source = 'library'
      v.params.hair_style = s.id
      v.params.hair_style_source = source
      v.params.hair_allowance = allowance
      v.params.hair_cover = v.params.hair_cover ?? 'normal'
      f.with_hair_glb = 'samples/sample.glb'
      f.hair_fit_sheet = put(`${base}/hair_fit_sheet.png`, makeHairCandidate(hairShape(s.family)))
      const lib: LibraryHairMetrics & Record<string, unknown> = {
        attached: true, style: s.id, name: s.name, family: s.family, score, iou: score, triangles: s.triangles, cap_triangles: cap, allowance, budget,
        skin_through: skin, kind: s.kind, alpha: s.alpha, two_sided: s.kind === 'cards', credit: credit(s), extra_height_m: s.family === 'bun' ? 0.09 : 0.017,
        seconds: 9.8, reused_body_of: reused, style_source: source, cover: v.params.hair_cover, groups: s.family === 'short' || s.family === 'bob' ? 0 : 1, tex: 1024,
      }
      m.hair_library = lib
      m.triangles = body
      chk('hair_fit', score >= 0.6, score, 0.6, { step: 'prep', style_name: s.name, picked: !!picked, family: s.family, best_name: style(lm.best)?.name, family_source: lm.family_source })
      chk('hair_skin_through', skinAll <= 0.02, skinAll, 0.02, { crown: skin.crown, sides: skin.sides })
      // A description with "heavy" goes over the budget, so "Choose a lighter hairstyle" can be tried.
      const heavy = /\bheavy\b/i.test(a.prompt) && source !== 'chosen'
      chk('hair_triangles', !heavy, body + s.triangles + (heavy ? 1500 : 0), budget, { hair: s.triangles, body, allowance })
      if (heavy) checks[checks.length - 1].fix = { stage: 'mesh', ui: 'hair_picker', params: {}, label: 'Choose a lighter hairstyle' }
      return
    }
    case 'rig': {
      const prep = keptOf(a, 'prep')
      const lh = prep?.metrics.hair_library as (LibraryHairMetrics & { groups?: number }) | undefined
      if (!lh?.attached) return
      const groups = lh.groups ?? 0
      m.hair_bind = { ok: true, error: null, verts: 3225, bound_share: 1.0, follow_error_m: 0, rest_share: groups ? 0.5 : 1, nape_blend_verts: 1628, groups, seconds: 2.4 }
      chk('hair_bound', true, 1.0, 1.0, { groups_swaying: groups, rest_on_head: !groups })
      return
    }
    case 'export': {
      const prep = keptOf(a, 'prep')
      const lh = prep?.metrics.hair_library as LibraryHairMetrics | undefined
      if (!lh?.attached) return
      m.hair_material = { ok: true, planned: true, source: 'library', kind: lh.kind, alpha: lh.alpha, two_sided: lh.two_sided, faces: lh.triangles, cap_faces: lh.cap_triangles, draws_added: 1, slots: [`${a.name}_Body`, `${a.name}_Hair`] }
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
  const meshIsLibrary = mesh?.params.hair_source === 'library'
  const lm = meshIsLibrary ? (mesh!.metrics.hair_library as { candidates?: MeshCandidate[]; best?: string; nothing_fits?: boolean } | undefined) : undefined
  const lp = prep?.metrics.hair_library as LibraryHairMetrics | undefined
  const { family, words } = familyOf(a.prompt)
  // Where the bald pictures of the kept (or newest) Check all sides version stand, as the backend's bald_state.
  const newest = (a.stages.turnaround ?? []).filter((v) => v.params.bald_only || v.params.bald_views).slice(-1)[0]
  const baldState: HairPanel['bald_state'] =
    source !== 'library' || !turn
      ? 'none'
      : newest && newest.stopped
        ? 'stopped'
        : newest && (newest.status === 'queued' || newest.status === 'running')
          ? 'drawing'
          : newest && newest.status === 'error'
            ? 'failed'
            : 'bald_front' in turn.files && 'bald_quarter' in turn.files
              ? 'ready'
              : 'missing'
  let rebuild: HairPanel['rebuild'] = null
  if (mesh && source === 'library' && !meshIsLibrary && baldState === 'ready') rebuild = { from_stage: 'mesh', minutes: 12 }
  if (mesh && source === 'model' && meshIsLibrary) rebuild = { from_stage: 'mesh', minutes: 10 }
  const candidates: HairCandidate[] = (lm?.candidates ?? []).map((c, i) => ({
    style: c.style, name: c.name, score: c.score, fit_words: backendFitWords(c.score), picture_url: mesh!.files[`hair_cand_${i + 1}`] ?? null, triangles: c.triangles, kind: c.kind, credit: style(c.style) ? credit(style(c.style)!) : null,
  }))
  const applied = lp?.attached && lp.style ? { style: lp.style, score: lp.score ?? null, prep_version_id: prep!.id } : null
  const baldSentence: Partial<Record<NonNullable<HairPanel['bald_state']>, string>> = {
    missing: 'Hair from the library is on, but the kept side views have no bald pictures yet.',
    drawing: 'The bald pictures are being drawn.',
    stopped: 'The bald pictures were stopped before they were finished; press Resume on Check all sides.',
    failed: 'The bald pictures could not be made.',
  }
  const sentence = baldSentence[baldState ?? 'none'] ?? (!offer.ok
    ? `Hair from the library is switched on, but it cannot be used: ${offer.reason} The hair is built with the 3D model.`
    : source === 'library' && applied
      ? `The character wears "${style(applied.style)?.name ?? applied.style}" from the hair library (ready-made hairstyles).`
      : source === 'library' && candidates.length
        ? 'Pick the hairstyle you like best; the best match is used when you do not pick one.'
        : source === 'library'
          ? 'The hairstyles are tried on the bald 3D model when it is built.'
          : 'The hair is built with the 3D model.')
  return {
    source,
    available: offer.ok,
    unavailable_reason: offer.ok ? null : offer.reason,
    family,
    family_words: words,
    pick: a.hair?.pick ?? null,
    candidates,
    mesh_version_id: mesh ? mesh.id : null,
    best: lm?.best ?? null,
    nothing_fits: !!lm?.nothing_fits,
    applied,
    allowance: typeof lp?.allowance === 'number' ? lp.allowance : null,
    rebuild,
    bald_state: baldState,
    sentence,
  }
}


// --- Your own hairstyles (backend vgac/my_hair.py, /api/my-hair) -------------------------------------------------------
// The demo "reads" a file by its name: "nomesh", "character" and "huge" in the name give the backend's plain refusals.
const OWN_FAMILIES = [
  { id: 'short', label: 'Short' },
  { id: 'bob', label: 'Bob' },
  { id: 'long', label: 'Long' },
  { id: 'ponytail', label: 'Ponytail' },
  { id: 'bun', label: 'Bun' },
  { id: 'braid', label: 'Braid' },
  { id: 'afro', label: 'Afro' },
]
const OWN_LICENCES = ['CC0', 'CC-BY', 'My own work', 'Paid or licensed asset', 'Not sure']
const myHair: MyHairStyle[] = []

export class OwnHairError extends Error {
  status: number
  constructor(message: string, status = 400) {
    super(message)
    this.status = status
  }
}

const ownEntry = (h: MyHairStyle): MockStyle => ({
  id: h.id,
  name: h.name,
  family: h.family,
  kind: 'shell',
  alpha: 'opaque',
  triangles: h.triangles,
  phone_ok: h.phone_ok,
  fringe: h.fringe,
  curly: h.curly,
  parting: 'centre',
  author: h.licence === 'My own work' ? 'You' : h.source && !/^https?:/i.test(h.source) ? h.source : 'Not stated',
  source_url: /^https?:/i.test(h.source) ? h.source : '',
  licence: h.licence || 'Not stated',
  own: true,
  thumbs: h.thumbs,
})

const syncOwn = () => {
  for (let i = HAIR_STYLES.length - 1; i >= 0; i--) if (HAIR_STYLES[i].own) HAIR_STYLES.splice(i, 1)
  for (const h of myHair) {
    const e = ownEntry(h)
    e.credit = credit(e)
    HAIR_STYLES.push(e)
  }
}

export function myHairListing(): MyHairListing {
  return {
    styles: myHair.map((h) => ({ ...h })),
    responsibility:
      'Only add hair you made or have the right to use. The file stays on this computer, and the name, source and licence you give here are written into the credits of every character that uses it.',
    max_mb: 80,
    kinds: ['.glb', '.gltf', '.fbx'],
    families: OWN_FAMILIES,
    licences: OWN_LICENCES,
  }
}

export function addMyHair(q: URLSearchParams): MyHairStyle {
  const name = String(q.get('name') ?? '')
  if (/[\\/:]|\.\./.test(name)) throw new OwnHairError('The file name must be a plain file name, without folders.')
  if (!/\.(glb|gltf|fbx)$/i.test(name)) throw new OwnHairError('Use a GLB, glTF or FBX hair mesh.')
  const label = (q.get('label') || name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ')).trim().slice(0, 60)
  const family = String(q.get('family') ?? 'short')
  if (!OWN_FAMILIES.some((f) => f.id === family)) throw new OwnHairError('Pick the kind of hairstyle (short, long, bob, ponytail, bun, braid or afro).')
  const licence = String(q.get('licence') ?? '')
  if (licence && !OWN_LICENCES.includes(licence)) throw new OwnHairError('Pick one of the licences in the list, or leave it unset.')
  if (Number(q.get('bytes') ?? 0) > 80 * 1024 * 1024) throw new OwnHairError('This file is larger than 80 MB. Hair meshes are much smaller.', 413)
  if (myHair.some((h) => h.name.toLowerCase() === label.toLowerCase())) throw new OwnHairError('You already have a hairstyle with that name. Choose another name.')
  const low = name.toLowerCase()
  if (low.includes('nomesh')) throw new OwnHairError('This file has no mesh we can use as hair.')
  if (low.includes('character')) throw new OwnHairError('This file looks like a whole character or a rigged model, not just hair. Export only the hair mesh and try again.')
  if (low.includes('huge')) throw new OwnHairError('This hair has far too many triangles (over 300,000). Reduce it in your 3D program and try again.')
  let id = 'mine_' + (label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 30) || 'hair')
  const base = id
  for (let i = 2; myHair.some((h) => h.id === id) || HAIR_STYLES.some((s) => s.id === id && !s.own); i++) id = `${base}_${i}`
  const shape = { family, hue: 28, light: 60 }
  const thumbs = { front: makeHairThumb(shape), quarter: makeHairThumb(shape) }
  const h: MyHairStyle = {
    id,
    name: label,
    family,
    family_label: OWN_FAMILIES.find((f) => f.id === family)?.label ?? family,
    fringe: q.get('fringe') === 'true',
    curly: q.get('curly') === 'true',
    kind: 'shell',
    triangles: 2400,
    phone_ok: true,
    source: String(q.get('source') ?? '').trim().slice(0, 300),
    licence: licence || 'Not stated',
    file_name: name,
    thumbs,
    added_at: new Date().toISOString(),
    has_texture: false,
    own: true,
  }
  myHair.push(h)
  syncOwn()
  return { ...h }
}

export function changeMyHair(id: string, body: Record<string, unknown>): MyHairStyle {
  const h = myHair.find((x) => x.id === id)
  if (!h) throw new OwnHairError('That hairstyle is not on your list.', 404)
  if (typeof body.label === 'string') {
    const label = body.label.trim().slice(0, 60)
    if (!label) throw new OwnHairError('Give the hairstyle a name.')
    if (myHair.some((x) => x.id !== id && x.name.toLowerCase() === label.toLowerCase())) throw new OwnHairError('You already have a hairstyle with that name. Choose another name.')
    h.name = label
  }
  if (typeof body.family === 'string') {
    if (!OWN_FAMILIES.some((f) => f.id === body.family)) throw new OwnHairError('Pick the kind of hairstyle (short, long, bob, ponytail, bun, braid or afro).')
    h.family = body.family
    h.family_label = OWN_FAMILIES.find((f) => f.id === body.family)?.label ?? h.family
  }
  if (typeof body.fringe === 'boolean') h.fringe = body.fringe
  if (typeof body.curly === 'boolean') h.curly = body.curly
  if (typeof body.source === 'string') h.source = body.source.trim().slice(0, 300)
  if (typeof body.licence === 'string') h.licence = body.licence.trim() || 'Not stated'
  syncOwn()
  return { ...h }
}

export function removeMyHair(id: string, inUse: string[] = []): { removed: string } {
  const i = myHair.findIndex((x) => x.id === id)
  if (i < 0) throw new OwnHairError('That hairstyle is not on your list.', 404)
  if (inUse.length) throw new OwnHairError('Used by ' + inUse.slice(0, 3).join(', ') + '. Pick another hairstyle for it first, then remove this one.', 409)
  myHair.splice(i, 1)
  syncOwn()
  return { removed: id }
}
