// "Hair from the library (ready-made hairstyles)" (docs/HAIR-LIBRARY-PLAN.md section 1): the choice on the asset form,
// the "Hair" line with Change on the asset page, and what happens when it is changed later (the bald pictures are drawn,
// then the page offers "Rebuild from the 3D model"). Off is the default and leaves everything as it was.
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { ApiError, hairApi, STAGES, stageEstimateSeconds, type Asset, type HairPanel, type HairSource, type Job, type StageId } from '../api'
import { Info } from '../components/Info'
import { ResumeButton } from '../components/ui'
import { BALD_WORDS, COVER_AFTER, COVER_NEGATORS, COVER_WORDS, HAIR_WORDS, hairKind, NO_HAIR_BODIES } from '../hairNotes'
import { fmtDuration, stagePlain } from '../help'

export const HAIR_SETTING = 'Hair from the library (ready-made hairstyles)'
export const HAIR_OPTION: Record<HairSource, string> = { model: 'Built with the 3D model', library: 'From the library' }
export const HAIR_OPTION_HINT: Record<HairSource, string> = {
  model: 'As before: the hair is part of the 3D model, with strands and shading painted on.',
  library:
    'The 3D model is built bald, and you pick a ready-made hairstyle that the app fits to the head and recolours to your picture. Long hair and ponytails can hang free and swing. It adds about 5 minutes: the bald pictures, then trying the hairstyles on.',
}
export const HAIR_PHONE_NOTE = 'On Phone only hairstyles made as one solid shape are offered, in a lighter version where the style has one.'

// Looks whose hair is one simple solid shape on purpose (the backend's hairlib.available says the same).
const PLAIN_LOOKS: Record<string, string> = { pixel: 'Pixel art', retro64: 'Retro 64', lowpoly_flat: 'Low-poly flat' }

const norm = (text: string) => ' ' + (text || '').toLowerCase().replace(/[^a-z]+/g, ' ').trim() + ' '
const has = (t: string, p: string) => t.includes(' ' + p + ' ')

function coveredHead(t: string): boolean {
  const words = t.trim().split(' ')
  for (const c of COVER_WORDS) {
    const cw = c.split(' ')
    for (let i = 0; i + cw.length <= words.length; i++) {
      if (!cw.every((w, k) => words[i + k] === w)) continue
      const before = words.slice(Math.max(0, i - 3), i)
      const after = words.slice(i + cw.length, i + cw.length + 1)
      if (before.some((w) => COVER_NEGATORS.includes(w)) || after.some((w) => COVER_AFTER.includes(w))) continue
      return true
    }
  }
  return false
}

// Whether the asset form may offer the library, and if not, why (the same rules as the backend's hairlib.available;
// the asset page asks the backend instead).
export function hairLibraryOffer(prompt: string, style: string, category: string): { ok: boolean; reason: string } {
  if (category !== 'humanoid') return { ok: false, reason: 'Hair from the library is for human-shaped characters only.' }
  if (PLAIN_LOOKS[style]) return { ok: false, reason: `The ${PLAIN_LOOKS[style]} look keeps hair as one simple solid shape on purpose.` }
  if (hairKind(prompt, category) === null) {
    const t = norm(prompt)
    if (BALD_WORDS.some((w) => has(t, w))) return { ok: false, reason: 'The description says the character is bald.' }
    if (NO_HAIR_BODIES.some((w) => has(t, w)) && !HAIR_WORDS.some((w) => has(t, w))) return { ok: false, reason: 'This character has no hair to replace.' }
    if (coveredHead(t)) return { ok: false, reason: 'The description covers the head (a helmet, a hood or similar), so there is no hair to replace.' }
    return { ok: false, reason: 'This character has no hair to replace.' }
  }
  return { ok: true, reason: '' }
}

// The choice on the asset form, under the hair notes.
export function HairFormLine({ value, onChange, platform }: { value: HairSource; onChange: (v: HairSource) => void; platform: string }) {
  return (
    <div className="items-line" role="group" aria-label={HAIR_SETTING}>
      <p className="small">
        {HAIR_SETTING}
        <Info term="term.hair_library" />
      </p>
      <HairOptions name="hair-form" value={value} onChange={onChange} />
      <p className="muted small">
        {HAIR_OPTION_HINT[value]}
        {value === 'library' && platform === 'mobile_low' ? ' ' + HAIR_PHONE_NOTE : ''}
      </p>
    </div>
  )
}

function HairOptions({ name, value, onChange, disabled }: { name: string; value: HairSource; onChange: (v: HairSource) => void; disabled?: boolean }) {
  return (
    // One option per row, each row clickable across its whole width (the words select it, not only the round button).
    <div className="choice-row" role="radiogroup" aria-label={HAIR_SETTING} style={{ flexDirection: 'column', gap: 'var(--s1)' }}>
      {(['model', 'library'] as const).map((s) => (
        <label className="check-item" key={s} style={{ display: 'flex', width: '100%', cursor: disabled ? 'default' : 'pointer', minHeight: 28 }}>
          <input type="radio" name={name} checked={value === s} disabled={disabled} onChange={() => onChange(s)} />
          <span>
            {HAIR_OPTION[s]}
            {s === 'model' ? ' (as before)' : ''}
          </span>
        </label>
      ))}
    </div>
  )
}

// GET /api/assets/{aid}/hair for a character, reloaded when its steps change. null until known, and for a backend
// without the feature (404) or a category that never has hair.
export function useHairPanel(asset: Asset | null): { panel: HairPanel | null; reload: () => void; setPanel: (p: HairPanel) => void } {
  const [panel, setPanel] = useState<HairPanel | null>(null)
  const [tick, setTick] = useState(0)
  const reload = useCallback(() => setTick((t) => t + 1), [])
  const id = asset?.id ?? ''
  const humanoid = asset?.category === 'humanoid'
  // Reload when a version or a choice changes.
  const key = asset ? JSON.stringify([asset.selected, asset.hair ?? null, STAGES.map((s) => (asset.stages[s] ?? []).map((v) => v.status).join(''))]) : ''
  useEffect(() => {
    if (!id || !humanoid) {
      setPanel(null)
      return
    }
    let alive = true
    hairApi.panel(id).then(
      (p) => alive && setPanel(p),
      (e) => {
        if (alive && e instanceof ApiError && e.status === 404) setPanel(null)
      },
    )
    return () => {
      alive = false
    }
  }, [id, humanoid, key, tick])
  return { panel, reload, setPanel }
}

// The "Hair" line in the asset page's side column.
export function HairRailLine({ panel, open, onToggle }: { panel: HairPanel; open: boolean; onToggle: () => void }) {
  const chosen = panel.source === 'library' ? (panel.applied?.style ?? panel.pick ?? null) : null
  const fromCandidates = chosen ? (panel.candidates.find((c) => c.style === chosen)?.name ?? null) : null
  // A style chosen from "Show more hairstyles" is not among the candidates: its name comes from the catalogue.
  const [fromCatalogue, setFromCatalogue] = useState<string | null>(null)
  useEffect(() => {
    if (!chosen || fromCandidates) return
    let alive = true
    hairApi.library().then(
      (list) => alive && setFromCatalogue(list.find((s) => s.id === chosen)?.name ?? null),
      () => undefined,
    )
    return () => {
      alive = false
    }
  }, [chosen, fromCandidates])
  const pickName = fromCandidates ?? (chosen ? fromCatalogue : null)
  return (
    <div className="muted small">
      Hair: {HAIR_OPTION[panel.source]}
      {pickName ? `, "${pickName}"` : ''}
      <Info term="term.hair_library" />{' '}
      <button type="button" className="link small" aria-pressed={open} onClick={onToggle} title={`Change ${HAIR_SETTING}`}>
        Change
      </button>
    </div>
  )
}

// Minutes for a rebuild from a step: the backend's figure, else the usual step times added up.
export function rebuildSeconds(from: StageId, minutes?: number | null): number {
  if (typeof minutes === 'number' && minutes > 0) return minutes * 60
  return STAGES.slice(STAGES.indexOf(from)).reduce((t, s) => t + stageEstimateSeconds(s), 0)
}

// What a change of the setting does, said before it is made.
function changeConsequence(asset: Asset, to: HairSource): string {
  const turnaround = !!asset.selected?.turnaround
  const mesh = !!asset.selected?.mesh
  if (to === 'library') {
    if (!turnaround) return 'The next steps use it: Check all sides also draws the bald pictures (the character without hair, used to build the 3D model).'
    return `The bald pictures (the character without hair, used to build the 3D model) are drawn now, ${fmtDuration(120)}; your other views are kept. ${
      mesh ? `Then "Rebuild from the 3D model" builds the bald model and tries the hairstyles on it, ${fmtDuration(rebuildSeconds('mesh'))} for all the later steps.` : 'Then build the 3D model as usual.'
    } Nothing you made is thrown away.`
  }
  if (!mesh) return 'The next steps build the hair as part of the 3D model, as before.'
  return `"Rebuild from the 3D model" then builds the 3D model with its own hair again, ${fmtDuration(rebuildSeconds('mesh'))} for all the later steps. Nothing you made is thrown away.`
}

// The Change panel in the main column of the asset page.
export function HairSettingPanel({ asset, panel, onClose, onSaved, onError }: { asset: Asset; panel: HairPanel; onClose: () => void; onSaved: (p: HairPanel) => void; onError: (e: unknown) => void }) {
  const [choice, setChoice] = useState<HairSource>(panel.source)
  const [busy, setBusy] = useState(false)
  const changed = choice !== panel.source
  const save = async () => {
    setBusy(true)
    try {
      const r = await hairApi.setSource(asset.id, choice)
      onSaved(r.panel)
      onClose()
    } catch (e) {
      onError(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="variant-panel">
      <div className="row-between">
        <div className="field-label">
          {HAIR_SETTING}
          <Info term="term.hair_library" />
        </div>
        <button type="button" className="link small" onClick={onClose}>
          Close
        </button>
      </div>
      {!panel.available && panel.source !== 'library' ? (
        <div className="muted small">Not offered for this character. {panel.unavailable_reason || 'Its hair cannot be replaced.'} The hair stays built with the 3D model.</div>
      ) : (
        <>
          <HairOptions name="hair-page" value={choice} onChange={setChoice} disabled={busy} />
          <div className="muted small">
            {HAIR_OPTION_HINT[choice]}
            {choice === 'library' && asset.platform_target === 'mobile_low' ? ' ' + HAIR_PHONE_NOTE : ''}
          </div>
          {changed && <div className="small">{changeConsequence(asset, choice)}</div>}
          <div className="row-gap">
            <button type="button" className="primary" disabled={busy || !changed} onClick={save}>
              {busy ? 'Changing...' : 'Change it'}
            </button>
            <button type="button" disabled={busy} onClick={onClose}>
              Cancel
            </button>
          </div>
        </>
      )}
    </div>
  )
}

// After the setting changed: the bald pictures being drawn, then the offer to rebuild from the 3D model.
export function HairRebuildNote({ asset, panel, jobs, busy, onRebuild }: { asset: Asset; panel: HairPanel; jobs: Job[]; busy: boolean; onRebuild: (from: StageId) => void }) {
  const bs = panel.bald_state
  // Older backends send no bald_state: a running Check all sides job stands for "drawing".
  const drawing = panel.source === 'library' && (bs ? bs === 'drawing' : jobs.some((j) => (j.status === 'queued' || j.status === 'running') && (asset.stages.turnaround ?? []).some((v) => v.id === j.version_id)))
  const turnaround = stagePlain('turnaround')
  if (panel.source === 'library' && (bs === 'stopped' || bs === 'failed' || bs === 'missing'))
    return (
      <div className="stale-note">
        <span>
          {panel.sentence ||
            (bs === 'stopped'
              ? 'The bald pictures (the character without hair, used to build the 3D model) were stopped before they were finished.'
              : bs === 'failed'
                ? 'The bald pictures (the character without hair, used to build the 3D model) could not be made.'
                : `The kept "${turnaround}" result has no bald pictures (the character without hair, used to build the 3D model) yet.`)}{' '}
          {bs === 'stopped'
            ? `Press Resume on "${turnaround}" to finish them. "Rebuild from the 3D model" is offered once they are ready.`
            : bs === 'failed'
              ? `Open "${turnaround}" to see what went wrong and try again. "Rebuild from the 3D model" is offered once they are ready.`
              : '"Rebuild from the 3D model" is offered once they are ready.'}
        </span>
        <Link className="small" to={`/assets/${asset.id}/turnaround`}>
          Go to "{turnaround}"
        </Link>
      </div>
    )
  if (drawing)
    return (
      <div className="stale-note">
        <span>
          {HAIR_SETTING} is on. The bald pictures (the character without hair, used to build the 3D model) are being drawn, {fmtDuration(120)}.
        </span>
      </div>
    )
  const r = panel.rebuild
  if (!r || (panel.source === 'library' && bs && bs !== 'ready')) return null
  const from = (STAGES.includes(r.from_stage as StageId) ? r.from_stage : 'mesh') as StageId
  return (
    <div className="stale-note">
      <span>
        {panel.source === 'library'
          ? `${HAIR_SETTING} is on, but "${stagePlain(from)}" and the steps after it still have the hair built in.`
          : `${HAIR_SETTING} is off, but "${stagePlain(from)}" and the steps after it still have the library hairstyle.`}
      </span>
      <ResumeButton
        small
        label={from === 'mesh' ? 'Rebuild from the 3D model' : `Rebuild from "${stagePlain(from)}"`}
        est={fmtDuration(rebuildSeconds(from, r.minutes))}
        disabled={busy}
        onClick={() => onRebuild(from)}
        title="Makes that step again with the new hair setting and rebuilds every step after it. The results you have now stay in the history."
      />
    </div>
  )
}
