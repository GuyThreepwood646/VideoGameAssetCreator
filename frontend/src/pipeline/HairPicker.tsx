// The hairstyle picker in the Build the 3D model view (docs/HAIR-LIBRARY-PLAN.md section 5): the best-fitting library
// hairstyles shown on the person's own character with plain fit words, triangles, solid shape or strips and the credit,
// "Use this hairstyle", what changing the pick costs before it is made, and "Show more hairstyles" from the catalogue.
import { useEffect, useRef, useState } from 'react'
import { creditText, hairApi, hairLibraryUrl, libraryUrl, stageEstimateSeconds, type Asset, type HairCandidate, type HairPanel, type HairStyleEntry, type Version } from '../api'
import { Info } from '../components/Info'
import { ErrorText } from '../components/ui'
import { fmtDuration, joinAnd, stagePlain, type FixAction } from '../help'
import { useHairPanel } from './HairSetting'

export const FAMILY_WORDS: Record<string, string> = { bob: 'Bob', short: 'Short', long: 'Long', ponytail: 'Ponytail', bun: 'Bun', braid: 'Braid', afro: 'Afro' }
const FAMILY_ORDER = ['bob', 'short', 'long', 'ponytail', 'bun', 'braid', 'afro']

// How well a style follows the hair outline in the picture, in words. Kept here with the other wording; the backend's
// shorter fit_words are not shown.
export function fitWords(score: number | null | undefined): string {
  if (typeof score !== 'number') return 'Not tried on your character yet.'
  if (score >= 0.8) return 'A close match to the hair outline in your picture.'
  if (score >= 0.7) return 'A good match to the hair outline in your picture.'
  if (score >= 0.6) return 'A fair match: the outline is roughly like the hair in your picture.'
  return 'A poor match: the outline is clearly different from the hair in your picture.'
}

export function kindWords(kind: string): string {
  return kind === 'cards' ? 'Hair cards (see-through strips)' : 'One solid shape'
}

function creditOf(s: HairStyleEntry): string {
  if (creditText(s.credit)) return creditText(s.credit)
  const lic = !s.licence || /^CC0/i.test(s.licence) ? 'CC0 (free to use)' : s.licence
  return [s.author ? `By ${s.author}` : null, s.pack, lic].filter(Boolean).join(', ')
}

function thumbOf(s: HairStyleEntry): string | undefined {
  const t = s.thumbs
  if (!t) return undefined
  if (Array.isArray(t)) return hairLibraryUrl(t[0])
  return hairLibraryUrl(t.quarter ?? t.front ?? Object.values(t)[0])
}

// Plan section 5: hair-only Clean up 20 to 40 s, Make it move without the rigger 60 to 90 s, Hand off about 70 s.
const HAIR_ONLY_SECONDS = { prep: 40, rig: 80, export: 70 }

// The room kept for hair on the kept body: the panel's allowance when the backend sends it, else the largest candidate (or the style in use), rounded up to 250.
function allowanceOf(panel: HairPanel, appliedTriangles: number | null): number {
  if (typeof panel.allowance === 'number' && panel.allowance > 0) return panel.allowance
  const most = Math.max(0, appliedTriangles ?? 0, ...panel.candidates.map((c) => c.triangles || 0))
  return Math.ceil(most / 250) * 250
}

// What using another style costs from here, in words; null when nothing is rebuilt.
function changeCost(panel: HairPanel, triangles: number, appliedTriangles: number | null): { text: string; seconds: number } | null {
  if (!panel.applied) return null
  const allowance = allowanceOf(panel, appliedTriangles)
  if (allowance > 0 && triangles <= allowance) {
    const s = HAIR_ONLY_SECONDS.prep + HAIR_ONLY_SECONDS.rig + HAIR_ONLY_SECONDS.export
    return {
      seconds: s,
      text: `The new hair is fitted on the same body, and the later steps are made again: ${fmtDuration(s)} in all (${stagePlain('prep')} ${fmtDuration(HAIR_ONLY_SECONDS.prep)}, ${stagePlain('rig')} ${fmtDuration(HAIR_ONLY_SECONDS.rig)}, ${stagePlain('export')} ${fmtDuration(HAIR_ONLY_SECONDS.export)}). The body and its skeleton are kept.`,
    }
  }
  const s = stageEstimateSeconds('prep') + stageEstimateSeconds('rig') + stageEstimateSeconds('export')
  return {
    seconds: s,
    text: `This hairstyle has more triangles than the room kept for hair (${allowance.toLocaleString('en-US')}), so ${stagePlain('prep')} runs in full and the later steps are made again: ${fmtDuration(s)} in all.`,
  }
}

type Props = { asset: Asset; version: Version; focus?: number | null; onFix?: (fix: FixAction) => void; onChanged?: () => void }

export function HairPicker({ asset, version, focus, onFix, onChanged }: Props) {
  const { panel, setPanel } = useHairPanel(asset)
  const [confirm, setConfirm] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [more, setMore] = useState(false)
  const [lighter, setLighter] = useState(false)
  const [catalogue, setCatalogue] = useState<HairStyleEntry[] | null>(null)
  const boxRef = useRef<HTMLDivElement>(null)

  // "Choose a lighter hairstyle" (the hair_triangles Fix) lands here: show more, lightest first.
  const loaded = !!panel
  useEffect(() => {
    if (!focus) return
    setMore(true)
    setLighter(true)
  }, [focus])
  useEffect(() => {
    if (!focus || !loaded) return
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    boxRef.current?.scrollIntoView({ block: 'start', behavior: reduce ? 'auto' : 'smooth' })
  }, [focus, loaded])
  // The catalogue gives names, credits and thumbnails for styles that were not among the four tried (the one in use
  // may be one of them).
  const libraryOn = panel?.source === 'library'
  useEffect(() => {
    if (!libraryOn || catalogue) return
    hairApi.library(asset.id).then(setCatalogue, (e) => setError(e))
  }, [libraryOn, catalogue, asset.id])

  if (!panel || panel.source !== 'library') return null
  const metrics = (version.metrics.hair_library ?? null) as { error?: string | null; seconds?: number } | null
  const current = panel.applied?.style ?? panel.pick ?? panel.best ?? null
  const byId = new Map<string, HairStyleEntry>((catalogue ?? []).map((s) => [s.id, s]))
  const nameOf = (id: string | null | undefined) => (id ? (panel.candidates.find((c) => c.style === id)?.name ?? byId.get(id)?.name ?? id) : '')
  const appliedTriangles = panel.applied ? (panel.candidates.find((c) => c.style === panel.applied!.style)?.triangles ?? (byId.get(panel.applied.style)?.triangles_used ?? byId.get(panel.applied.style)?.triangles) ?? null) : null

  const pick = async (style: string, rebuild: boolean) => {
    setBusy(true)
    setError(null)
    try {
      const r = await hairApi.pick(asset.id, style, rebuild)
      setPanel(r.panel)
      setConfirm(null)
      onChanged?.()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  const choose = (style: string, triangles: number) => {
    if (changeCost(panel, triangles, appliedTriangles)) setConfirm(style)
    else void pick(style, false)
  }

  const words = (panel.family_words ?? []).filter(Boolean)
  // The panel's own sentence (panel.sentence) is not shown here: this intro says the same with the family words.
  const intro =
    (panel.candidates.length === 0
      ? metrics?.error
        ? 'The hairstyles could not be tried on this model: something went wrong on the way. You can still choose one from the library below; it is fitted at Clean up for the game.'
        : 'No hairstyles have been tried on this model yet. They are tried at the end of this step.'
      : panel.family
        ? `Your description says ${words.length ? joinAnd(words.map((w) => `"${w}"`)) : `${FAMILY_WORDS[panel.family]?.toLowerCase() ?? panel.family} hair`}, so these ${(FAMILY_WORDS[panel.family] ?? panel.family).toLowerCase()} styles were tried on your character and put in order of how well they follow the hair outline in your picture.`
        : 'Your description does not name a hairstyle, so every style was tried quickly on your character. The order is a rough guide; pick by eye.')
  const notFromThisVersion = panel.mesh_version_id && panel.mesh_version_id !== version.id

  const card = (c: { style: string; name: string; triangles: number; kind: string; credit?: string | null; picture?: string; fit: string; score?: number | null; lite?: boolean }, tried: boolean) => {
    // Tried styles: the backend renders your character's own head in plain grey with the recoloured hair, front and
    // three-quarter. Catalogue thumbnails: the style itself in grey on a grey reference figure.
    const inUse = panel.applied?.style === c.style
    const isPick = !inUse && panel.pick === c.style
    const isBest = !inUse && !isPick && !panel.pick && !panel.applied && panel.best === c.style
    const cost = confirm === c.style ? changeCost(panel, c.triangles, appliedTriangles) : null
    return (
      <div key={c.style} className={'move-card' + (current === c.style ? ' move-card-on' : '')} style={{ cursor: 'default', alignItems: 'stretch' }}>
        <div className="view-image" style={tried ? { height: 'auto', minHeight: 90, background: 'var(--panel-2)' } : { height: 150, background: 'var(--panel-2)' }}>
          {c.picture ? (
            <img src={c.picture} alt={tried ? `${c.name} on your character's head, shown in plain grey` : `${c.name} on a grey reference figure`} style={tried ? { width: '100%', height: 'auto' } : undefined} loading="lazy" />
          ) : (
            <span className="muted small">No picture</span>
          )}
        </div>
        <div className="move-card-text">
          <span className="move-card-label">
            {c.name}{' '}
            {inUse && <span className="chip chip-ok">In use</span>}
            {isPick && <span className="chip chip-accent">Your pick</span>}
            {isBest && <span className="chip chip-accent">Best match</span>}
          </span>
          <span className={'small' + (typeof c.score === 'number' && c.score < 0.6 ? ' warn-text' : '')}>{c.fit}</span>
          <span className="small muted">
            {c.triangles.toLocaleString('en-US')} triangles{c.lite ? ' (lite version)' : ''}, {kindWords(c.kind).charAt(0).toLowerCase() + kindWords(c.kind).slice(1)}
            <Info term={c.kind === 'cards' ? 'term.hair_cards' : 'term.triangles'} />
          </span>
          {c.credit && <span className="small muted">Credit: {c.credit}</span>}
        </div>
        {cost ? (
          <div className="tool-note small" style={{ marginBottom: 0 }}>
            {cost.text}
            <div className="row-gap" style={{ marginTop: 'var(--s2)' }}>
              <button type="button" className="primary small-btn" disabled={busy} onClick={() => pick(c.style, true)}>
                Change the hairstyle
              </button>
              <button type="button" className="small-btn" disabled={busy} onClick={() => setConfirm(null)}>
                Keep "{nameOf(panel.applied?.style)}"
              </button>
            </div>
          </div>
        ) : (
          <button type="button" className="small-btn" disabled={busy || inUse || isPick} onClick={() => choose(c.style, c.triangles)}>
            {inUse ? 'In use' : isPick ? 'Picked' : 'Use this hairstyle'}
          </button>
        )}
      </div>
    )
  }

  const tried = panel.candidates.map((c: HairCandidate) =>
    card({ style: c.style, name: c.name, triangles: c.triangles, kind: c.kind, credit: creditText(c.credit), picture: libraryUrl(c.picture_url), fit: fitWords(c.score), score: c.score }, true),
  )
  // The style in use came from "Show more hairstyles": it leads the row, from the catalogue, with its fit from Clean up.
  const appliedEntry = panel.applied && !panel.candidates.some((c) => c.style === panel.applied!.style) ? byId.get(panel.applied.style) : undefined
  if (appliedEntry)
    tried.unshift(
      card({ style: appliedEntry.id, name: appliedEntry.name, triangles: typeof appliedEntry.triangles_used === 'number' ? appliedEntry.triangles_used : appliedEntry.triangles, lite: appliedEntry.lite_used, kind: appliedEntry.kind, credit: creditOf(appliedEntry), picture: thumbOf(appliedEntry), fit: fitWords(panel.applied?.score ?? null), score: panel.applied?.score ?? null }, false),
    )
  const triedIds = new Set([...panel.candidates.map((c) => c.style), ...(appliedEntry ? [appliedEntry.id] : [])])
  const phone = asset.platform_target === 'mobile_low'
  // A backend that answers per platform has already left out what is not offered here (it sends triangles_used).
  const perPlatform = (catalogue ?? []).some((s) => typeof s.triangles_used === 'number')
  const used = (s: HairStyleEntry) => (typeof s.triangles_used === 'number' ? s.triangles_used : s.triangles)
  const others = (catalogue ?? []).filter((s) => !triedIds.has(s.id) && (perPlatform || !phone || s.phone_ok || (s.kind !== 'cards' && s.triangles <= 750)))
  const anyLite = others.some((s) => s.lite_used)
  const families = [...new Set([panel.family ?? '', ...FAMILY_ORDER, ...others.map((s) => s.family)])].filter((f) => others.some((s) => s.family === f))
  const allowance = allowanceOf(panel, appliedTriangles)

  return (
    <div className="hair-picker" ref={boxRef} style={{ gridColumn: '1 / -1', display: 'flex', flexDirection: 'column', gap: 'var(--s2)' }}>
      <div className="field-label">
        Hairstyle (Hair from the library)
        <Info term="term.hair_library" />
      </div>
      <div className="small muted">{intro}</div>
      {panel.candidates.length > 0 && <div className="small muted">Each picture shows your character's own head in plain grey, from the front and from three-quarters, with the hairstyle in the hair colour of your picture.</div>}
      {notFromThisVersion && <div className="small muted">These were tried on the 3D model you kept.</div>}
      {panel.nothing_fits && (
        <div className="tool-note small">
          Nothing in the library fits this picture well. You can still use the closest style, or go back to the hair built with the 3D model.
          {onFix && (
            <div className="row-gap" style={{ marginTop: 'var(--s2)' }}>
              <button
                type="button"
                className="small-btn"
                disabled={busy}
                onClick={() => onFix({ stage: 'mesh', params: { hair_source: 'model' }, label: 'Use the hair built with the 3D model' })}
                title="Builds the 3D model again with the hair built in, as without the library. The bald build stays in the history."
              >
                Use the hair built with the 3D model
              </button>
              <span className="muted small">{fmtDuration(stageEstimateSeconds('mesh'))} to build it again</span>
            </div>
          )}
        </div>
      )}
      {tried.length > 0 && (
        <div className="move-cards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))' }}>
          {tried}
        </div>
      )}
      {!panel.applied && current && (
        <div className="small muted">
          {panel.pick ? `Clean up for the game uses "${nameOf(panel.pick)}".` : `If you pick nothing, Clean up for the game uses the best match, "${nameOf(current)}".`} Picking costs no time now.
        </div>
      )}
      <ErrorText error={error} />
      <div>
        <button type="button" className="link small" aria-expanded={more} onClick={() => setMore((m) => !m)}>
          {more ? 'Show fewer hairstyles' : 'Show more hairstyles'}
        </button>
      </div>
      {more && (
        <div className="col" style={{ gap: 'var(--s2)' }}>
          <div className="small muted">
            These have not been tried on your character; the one you choose is fitted at {stagePlain('prep')}. The pictures show each style in grey on a stand-in figure.
            {phone ? ' On Phone only hairstyles made as one solid shape are shown.' : ''}
            {anyLite ? ' Styles marked "lite version" use their lite version (a lighter copy of the style with fewer triangles) here; the count shown is the one used.' : ''}
          </div>
          {lighter && allowance > 0 && <div className="small">Lightest first. The room kept for hair on this body is about {allowance.toLocaleString('en-US')} triangles; heavier styles make {stagePlain('prep')} run in full.</div>}
          {!catalogue && !error && <div className="muted small">Loading the hair library...</div>}
          {catalogue && others.length === 0 && <div className="muted small">Every hairstyle in the library is shown above.</div>}
          {lighter ? (
            <div className="move-cards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))' }}>
              {[...others].sort((a, b) => used(a) - used(b)).map((s) => card({ style: s.id, name: s.name, triangles: used(s), lite: s.lite_used, kind: s.kind, credit: creditOf(s), picture: thumbOf(s), fit: fitWords(null) }, false))}
            </div>
          ) : (
            families.map((f) => (
              <div key={f} className="move-group">
                <div className="field-label">{FAMILY_WORDS[f] ?? f}</div>
                <div className="move-cards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))' }}>
                  {others.filter((s) => s.family === f).map((s) => card({ style: s.id, name: s.name, triangles: used(s), lite: s.lite_used, kind: s.kind, credit: creditOf(s), picture: thumbOf(s), fit: fitWords(null) }, false))}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
