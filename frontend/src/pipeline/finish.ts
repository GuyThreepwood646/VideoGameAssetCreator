// The finish card's "What you get" list and "Using it in Unity" lines (plan 2.4).
//
// The export stage writes both: metrics.finish_lines (at most six {id, text, state, term}) and metrics.unity_howto
// (three lines). Older exports have neither, so the same lines are built here from what the export, prep and rig
// versions do report: the size check, the Unity report's assigned textures, clips and controller, the collider and
// the files. A line whose facts are missing is left out rather than guessed.
import { selectedVersion, type Asset, type Check, type FinishLine, type StageId, type Version } from '../api'
import { checkFix, checkPassed, checkSentence, joinAnd, measuredSize, metres, movesPlain, sizeAxisOf, type FixAction } from '../help'

export type UnityClip = { name: string; loop?: boolean }
export type UnityParam = string | { name: string; type?: string }
export type UnityReport = {
  ok?: boolean
  prefab?: string
  avatar?: string
  // 3.6: how the humanoid skeleton was mapped.
  avatar_mapping?: 'explicit' | 'auto' | 'generic' | string
  humanoidValid?: boolean
  // 3.1: the maps the importer put on the material (metallic_smoothness among them once it is connected).
  texturesAssigned?: string[]
  // 3.3: each clip and whether it loops, and the controller that was built.
  clips?: (UnityClip | string)[]
  // The controller: a path string from older importers, an object from newer ones.
  controller?: string | { path?: string; states?: string[]; parameters?: UnityParam[]; locomotion?: unknown; roles?: unknown; moreMoves?: unknown; applyRootMotion?: boolean } | null
  avatar_mapping_by?: 'names' | 'shape' | string
  hitboxCount?: number
  movementShape?: string
  importer_version?: string | number
  warnings?: string[]
  errors?: string[]
}

export const RIGGED_CATEGORIES = /^(humanoid|quadruped|winged|multileg|serpent)$/
// Clips that should loop (3.3): the plan's cyclic names, matched anywhere in the clip name.
export const CYCLIC_CLIP = /(idle|walk|run|trot|jog|sprint|swim|slither|crawl|fly|flap|glide|hover|loop)/i

export function unityReport(v: Version | undefined): UnityReport | null {
  const r = v?.metrics.unity_report
  return r && typeof r === 'object' ? (r as UnityReport) : null
}

export function checksOf(v: Version | undefined): Check[] {
  return v && Array.isArray(v.metrics.checks) ? (v.metrics.checks as Check[]) : []
}

const STAGES_WITH_FINISH_CHECKS: StageId[] = ['export', 'prep', 'rig']

// A check by id among the kept export, prep and rig versions.
export function findCheck(asset: Asset, id: string): Check | null {
  for (const s of STAGES_WITH_FINISH_CHECKS) {
    const c = checksOf(selectedVersion(asset, s)).find((x) => (x.id ?? x.name) === id)
    if (c) return c
  }
  return null
}

export function unityClips(rep: UnityReport | null): UnityClip[] {
  if (!rep || !Array.isArray(rep.clips)) return []
  return rep.clips.map((c) => (typeof c === 'string' ? { name: c } : c)).filter((c) => c && typeof c.name === 'string')
}

export function controllerParams(rep: UnityReport | null): string[] {
  const c = rep?.controller
  // An older importer wrote only the controller's path: a controller exists but its parameters are unknown.
  const ps = c && typeof c === 'object' ? c.parameters : undefined
  if (!Array.isArray(ps)) return []
  // Newer importers write "Attack:Trigger"; the name is before the colon.
  return ps
    .map((p) => (typeof p === 'string' ? p : p?.name))
    .filter((p): p is string => typeof p === 'string')
    .map((p) => p.split(':')[0])
}

const TRIGGER_ORDER = ['Attack', 'Hit', 'Die', 'Jump']
const LOCO_VERBS: [RegExp, string][] = [
  [/idle/i, 'idles'],
  [/walk/i, 'walks'],
  [/run|trot|jog|sprint/i, 'runs'],
]

// What the controller does: the locomotion moves that exist and the one-off triggers. Null without Speed.
export function controllerMoves(rep: UnityReport | null, clipNames: string[]): { loco: string[]; triggers: string[] } | null {
  const params = controllerParams(rep)
  if (!params.includes('Speed')) return null
  const names = clipNames.length ? clipNames : unityClips(rep).map((c) => c.name)
  const loco = LOCO_VERBS.filter(([re]) => names.some((n) => re.test(n))).map(([, verb]) => verb)
  const triggers = TRIGGER_ORDER.filter((t) => params.includes(t))
  return { loco: loco.length ? loco : ['idles', 'walks', 'runs'], triggers }
}

const num = (x: unknown): number | null => (typeof x === 'number' && Number.isFinite(x) ? x : null)

function clipNamesOf(exp: Version): string[] {
  const m = exp.metrics.clips
  if (Array.isArray(m)) return m.map((c) => (typeof c === 'string' ? c : (c as { name?: string })?.name)).filter((c): c is string => typeof c === 'string')
  return unityClips(unityReport(exp)).map((c) => c.name)
}

function sanitize(raw: unknown): FinishLine[] {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((l): l is FinishLine => !!l && typeof l === 'object' && typeof (l as FinishLine).text === 'string')
    .map((l, i): FinishLine => ({ id: String(l.id ?? i), text: l.text, state: l.state === 'warn' ? 'warn' : 'ok', term: typeof l.term === 'string' ? l.term : undefined, check: typeof l.check === 'string' ? l.check : undefined }))
    .slice(0, 6)
}

export function finishLines(asset: Asset): FinishLine[] {
  const exp = selectedVersion(asset, 'export')
  if (!exp) return []
  if (Array.isArray(exp.metrics.finish_lines)) return sanitize(exp.metrics.finish_lines)

  const lines: FinishLine[] = []
  const rep = unityReport(exp)
  const ctx = { platform: asset.platform_target, category: asset.category }
  const failed = (c: Check | null) => !!c && !checkPassed(c)
  const warnFrom = (id: string, c: Check, term?: string): FinishLine => ({ id, text: checkSentence(c, ctx), state: 'warn', term, check: c.id ?? c.name })

  // 1. Size, and the front when prep checked it (3.2).
  const axis = sizeAxisOf(asset.category)
  const size = axis && axis !== 'grid' ? measuredSize(selectedVersion(asset, 'prep')?.metrics, asset.category) : null
  if (size) {
    const sc = findCheck(asset, 'size_m') ?? findCheck(asset, 'height_m')
    const facing = findCheck(asset, 'facing')
    const bad = failed(sc) ? sc : failed(facing) ? facing : null
    lines.push({
      id: 'size',
      text: `Real size: ${metres(size.value)} m ${size.word}${facing && checkPassed(facing) ? ', front facing forward' : ''}.`,
      state: bad ? 'warn' : 'ok',
      term: 'size_m',
      check: bad ? (bad.id ?? bad.name) : undefined,
    })
  }

  // 2. Surface: metal and shine reach Unity (3.1).
  const mc = findCheck(asset, 'materials_connected')
  if (mc && !checkPassed(mc)) lines.push(warnFrom('surface', mc, 'metal_shine'))
  else if (mc || rep?.texturesAssigned?.includes('metallic_smoothness')) lines.push({ id: 'surface', text: 'Looks right in Unity: colour, bumps, shadows, metal and shine are connected.', state: 'ok', term: 'metal_shine' })

  // 3. Movement: loops and the controller (3.3).
  if (RIGGED_CATEGORIES.test(asset.category)) {
    const loops = findCheck(asset, 'clips_loop')
    const ctrl = findCheck(asset, 'controller_ready')
    const moves = controllerMoves(rep, clipNamesOf(exp))
    if (failed(loops)) lines.push(warnFrom('movement', loops!, 'loop'))
    else if (failed(ctrl)) lines.push(warnFrom('movement', ctrl!, 'animator_controller'))
    else if (moves) {
      const t = moves.triggers
      const oneOff = t.length ? `; ${joinAnd(t)} ${t.length > 1 ? 'are one line each' : 'is one line'}` : ''
      lines.push({ id: 'movement', text: `Ready to move: set Speed and it ${joinAnd(moves.loco)}${oneOff}.`, state: 'ok', term: 'animator_controller' })
    }
  }

  // 3b. Creature moves (3.7). Humanoids get the shared animations line instead.
  const rig = selectedVersion(asset, 'rig')
  if (rig && RIGGED_CATEGORIES.test(asset.category) && asset.category !== 'humanoid') {
    const moves = movesPlain(rig.metrics.clips)
    const missing = movesPlain(rig.metrics.missing_clips)
    const cc = findCheck(asset, 'clips')
    if (missing.length || failed(cc)) lines.push({ id: 'moves', text: cc && failed(cc) ? checkSentence(cc, ctx) : `Moves: ${moves.length ? joinAnd(moves) + '; ' : ''}could not make ${joinAnd(missing)}.`, state: 'warn', term: 'moves', check: 'clips' })
    else if (moves.length) lines.push({ id: 'moves', text: `Moves: ${joinAnd(moves)}.`, state: 'ok', term: 'moves' })
  }

  // 4. Collision: the body shape for moving and the hit boxes (4.2) when the rig fitted them.
  const hb = hitboxSummary(asset)
  if (hb) {
    const shape = movementShapeWord(exp.params.movement_shape, hb.movement_default)
    const boxes = hb.mode === 'simple' ? 'hit boxes for the head and body' : `${hb.count} hit boxes that follow the animation`
    const cover = findCheck(asset, 'hitbox_cover')
    const over = findCheck(asset, 'hitbox_overreach')
    const bad = failed(cover) ? cover : failed(over) ? over : null
    if (bad) lines.push(warnFrom('collision', bad, 'hitboxes'))
    else lines.push({ id: 'collision', text: shape ? `Bumps into things: a ${shape} for moving and ${boxes}.` : `Bumps into things: ${boxes}.`, state: 'ok', term: 'hitboxes' })
  }
  const cp = findCheck(asset, 'collider_present')
  if (!hb && exp.params.collider !== 'none') {
    if (failed(cp)) lines.push(warnFrom('collision', cp!, 'collider'))
    else {
      const m = exp.metrics
      const count = num(m.collider_parts) ?? num(m.collider_count) ?? (Array.isArray(m.colliders) ? m.colliders.length : null) ?? (cp ? 1 : null)
      if (count !== null && count > 0) {
        const open = findCheck(asset, 'openings_clear')
        lines.push({ id: 'collision', text: `Bumps into things: ${count} collision shape${count > 1 ? 's' : ''}${open && checkPassed(open) ? ', and openings stay open' : ''}.`, state: 'ok', term: 'collider' })
      }
    }
  }

  // 5. Category special: shared human animations (3.6).
  if (asset.category === 'humanoid') {
    const av = findCheck(asset, 'unity_humanoid_avatar')
    const isHuman = av ? checkPassed(av) : rep?.avatar ? /human/i.test(rep.avatar) : null
    if (isHuman === true) {
      const n = num(av?.vars?.clips) ?? num(av?.value)
      lines.push({ id: 'shared_animations', text: `Shared animations: ${n !== null ? n.toLocaleString('en-US') + ' ' : 'the '}human moves work on this character.`, state: 'ok', term: 'avatar', check: undefined })
    } else if (isHuman === false) {
      lines.push({ id: 'shared_animations', text: 'Shared animations: not available for this character; it still walks and idles.', state: 'warn', term: 'avatar', check: av ? (av.id ?? av.name) : undefined })
    }
  }

  // 6. Engine files.
  const files: string[] = []
  if (exp.files.fbx) files.push('FBX (Unity, Unreal, Godot)')
  if (exp.files.glb) files.push('GLB (web)')
  if (exp.files.prefab || exp.files.unitypackage || rep?.prefab) files.push('a ready Unity prefab')
  if (files.length) lines.push({ id: 'files', text: `Files: ${joinAnd(files)}.`, state: 'ok', term: 'formats' })

  return lines.slice(0, 6)
}

type HitboxMetrics = { count: number; mode?: string; fallback?: boolean; movement_default?: string }

// metrics.hitboxes on the kept rig version when it fitted some (4.2).
export function hitboxSummary(asset: Asset): HitboxMetrics | null {
  const h = selectedVersion(asset, 'rig')?.metrics.hitboxes as Partial<HitboxMetrics> & { failed?: boolean } | undefined
  if (!h || typeof h !== 'object' || h.failed || h.mode === 'none' || !num(h.count)) return null
  return { count: num(h.count)!, mode: h.mode, fallback: h.fallback, movement_default: h.movement_default }
}

// "standing capsule", "lying capsule" or "box"; null for none.
function movementShapeWord(chosen: unknown, fallback: string | undefined): string | null {
  const k = typeof chosen === 'string' && chosen !== 'auto' ? chosen : (fallback ?? 'upright')
  return k === 'none' ? null : k === 'along' ? 'lying capsule' : k === 'box' ? 'box' : 'standing capsule'
}

// The Fix for a warn line: its named check, else the check with the line's id, when that check failed.
export function finishLineFix(asset: Asset, line: FinishLine): FixAction | null {
  if (line.state !== 'warn') return null
  const c = findCheck(asset, line.check ?? line.id)
  return c ? checkFix(c) : null
}

// "Using it in Unity": the export's own lines, else the controller lines (3.3) when a Speed controller was built.
export function unityHowto(asset: Asset): string[] | null {
  const exp = selectedVersion(asset, 'export')
  if (!exp) return null
  const own = exp.metrics.unity_howto
  if (Array.isArray(own)) {
    const lines = own.filter((l): l is string => typeof l === 'string' && l.trim().length > 0)
    return lines.length ? lines : null
  }
  const rep = unityReport(exp)
  const moves = controllerMoves(rep, clipNamesOf(exp))
  if (!moves) return null
  const prefab = rep?.prefab ?? exp.files.prefab
  const out = [prefab ? `Drag the prefab from \`${prefab}\` into your scene.` : 'Drag the prefab into your scene.', 'In your movement script, set the speed every frame: `animator.SetFloat("Speed", speed);`']
  const [first, ...rest] = moves.triggers.filter((t) => t !== 'Jump' || asset.category === 'humanoid')
  if (first) out.push(`For one-off moves, call \`animator.SetTrigger("${first}");\`${rest.length ? ` (or ${rest.map((t) => `\`"${t}"\``).join(', ')})` : ''}.`)
  if (hitboxSummary(asset)) out.push('Hit boxes are trigger colliders named Hit_Head, Hit_Chest and so on under the bones; check the name in OnTriggerEnter to know where a hit landed.')
  return out
}
