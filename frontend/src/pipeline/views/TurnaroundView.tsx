import { useState } from 'react'
import { libraryUrl, TURNAROUND_VIEWS, type Check, type Version } from '../../api'
import { Info } from '../../components/Info'
import { PictureViewer, Zoomable, type PictureItem } from '../../components/PictureViewer'
import { Disclosure, fmtNum, isCheckList, PlainChecks, TrafficDot } from '../../components/ui'
import { metricPlain } from '../../help'

type ViewMetrics = Record<string, unknown> & { passed?: boolean; checks?: Check[] }

// Hair from the library: the bald pictures, each next to the picture it was drawn from (concept for the front).
const BALD_VIEWS = ['front', 'quarter', 'quarter2', 'left', 'right'] as const

function BaldRow({ version, cutout }: { version: Version; cutout: boolean }) {
  const [open, setOpen] = useState<number | null>(null)
  const shown = BALD_VIEWS.filter((v) => `bald_${v}` in version.files)
  if (!shown.length) return null
  const bald = (version.metrics.bald ?? null) as { kept_share?: number; rerolled?: boolean; reason?: string | null } | null
  const sheet = libraryUrl(version.files.bald_sheet)
  const pick = (key: string) => (cutout ? (version.files[`${key}_cutout`] ?? version.files[key]) : version.files[key])
  // Each pair as two pictures in the viewer: with hair, then bald.
  const items: PictureItem[] = shown.flatMap((view) => {
    const name = VIEW_NAMES[view] ?? view
    const haired = pick(view)
    return [...(haired ? [{ src: libraryUrl(haired)!, label: `${name}, with hair` }] : []), { src: libraryUrl(pick(`bald_${view}`))!, label: `${name}, bald` }]
  })
  const at = (label: string) => setOpen(Math.max(0, items.findIndex((i) => i.label === label)))
  return (
    <div className="bald-row">
      <div className="view-intro muted small" style={{ paddingBottom: 0 }}>
        <strong>Bald pictures (the character without hair, used to build the 3D model)</strong>
        <Info term="term.bald_picture" /> Check that only the hair is gone: the face and the clothes should look the same in both pictures of each pair.
        {typeof bald?.kept_share === 'number' ? ` ${Math.round(bald.kept_share * 100)}% of the picture outside the hair is unchanged.` : ''}
        {bald?.rerolled ? ' The first try changed too much, so it was drawn a second time.' : ''}
        {bald?.reason === 'face_changed' ? ' The face looks different in the bald picture: compare the faces closely in the side-by-side picture.' : ''}
        {bald?.reason === 'clothes_changed' ? ' The clothes look different in the bald picture: compare them closely.' : ''}
        {sheet && (
          <>
            {' '}
            <a href={sheet} target="_blank" rel="noreferrer">
              All of them side by side
            </a>
          </>
        )}
      </div>
      <div className="views-row">
        {shown.map((view) => {
          const haired = pick(view)
          return (
            <div key={view} className="view-cell" style={{ minWidth: 300 }}>
              <div className="row-gap" style={{ gap: 'var(--s1)' }}>
                {haired ? (
                  <Zoomable className={'view-image' + (cutout ? ' checker' : '')} style={{ flex: '1 1 0', height: 280 }} label={`the ${view} view with hair`} onOpen={() => at(`${VIEW_NAMES[view] ?? view}, with hair`)}>
                    <img src={libraryUrl(haired)} alt={`${view} with hair`} />
                  </Zoomable>
                ) : (
                  <div className={'view-image' + (cutout ? ' checker' : '')} style={{ flex: '1 1 0', height: 280 }}>
                    <span className="muted small">the first picture</span>
                  </div>
                )}
                <Zoomable className={'view-image' + (cutout ? ' checker' : '')} style={{ flex: '1 1 0', height: 280 }} label={`the ${view} view without hair`} onOpen={() => at(`${VIEW_NAMES[view] ?? view}, bald`)}>
                  <img src={libraryUrl(pick(`bald_${view}`))} alt={`${view} without hair`} />
                </Zoomable>
              </div>
              <div className="row-between small">
                <strong>{VIEW_NAMES[view] ?? view}</strong>
                <span className="muted">with hair, then bald</span>
              </div>
            </div>
          )
        })}
      </div>
      {open !== null && <PictureViewer items={items} start={open} checker={cutout} onClose={() => setOpen(null)} />}
    </div>
  )
}

// Pose for 3D first (pose_ready): the step 1 picture next to the redrawn one every view and the 3D model start from,
// so a removed arm or tool, new gloves or new colours are seen here, before the 3D step.
function PoseReadyRow({ version, originalFiles, cutout }: { version: Version; originalFiles?: Record<string, string> | null; cutout: boolean }) {
  const [open, setOpen] = useState<number | null>(null)
  const posed = cutout ? (version.files.pose_ready_cutout ?? version.files.pose_ready_picture) : version.files.pose_ready_picture
  if (!posed) return null
  const mouth = version.params?.neutral_mouth === true
  const original = originalFiles ? (cutout ? (originalFiles.cutout ?? originalFiles.image) : originalFiles.image) : null
  const items: PictureItem[] = [
    ...(original ? [{ src: libraryUrl(original)!, label: 'Your step 1 picture' }] : []),
    { src: libraryUrl(posed)!, label: 'Redrawn for 3D' },
  ]
  return (
    <div className="pose-ready-row">
      <div className="view-intro muted small" style={{ paddingBottom: 0 }}>
        <strong>Redrawn for 3D (Pose for 3D first was on)</strong>
        <Info term="param.pose_ready" /> Every view below and the 3D model start from the redrawn picture{mouth ? ', which also has a closed mouth' : ''}; your step 1 picture is unchanged. Check that nothing that belongs to the design is gone (extra arms, claws, tools) and that the colours still match. If it is wrong, turn Pose for 3D first off under Fine-tune and draw the views again.
      </div>
      <div className="views-row">
        {items.map((it, i) => (
          <div key={it.label} className="view-cell">
            <Zoomable className={'view-image' + (cutout ? ' checker' : '')} label={it.label.toLowerCase()} onOpen={() => setOpen(i)}>
              <img src={it.src} alt={it.label} />
            </Zoomable>
            <div className="row-between small">
              <strong>{it.label}</strong>
            </div>
          </div>
        ))}
      </div>
      {open !== null && <PictureViewer items={items} start={open} checker={cutout} onClose={() => setOpen(null)} />}
    </div>
  )
}

const VIEW_NAMES: Record<string, string> = { front: 'Front', left: 'Left side', back: 'Back', right: 'Right side', quarter: 'Three-quarter', quarter2: 'Three-quarter (other side)', top: 'Top' }

// Words for a list of views: "Back", "Back and right side", "Front, back and right side".
function viewList(views: string[]): string {
  const names = views.map((v, i) => (i === 0 ? (VIEW_NAMES[v] ?? v) : (VIEW_NAMES[v] ?? v).toLowerCase()))
  if (names.length <= 1) return names[0] ?? ''
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

type Props = {
  version: Version
  // Redraw the ticked views together: one new version, every other view carried over unchanged.
  onRedraw: (views: string[]) => void
  // A request is on its way: the Redraw button waits.
  busy?: boolean
  // Why this version cannot be redrawn from (it is not finished). The ticks are not offered then.
  locked?: string | null
  // The step 1 version's files this one was drawn from, shown beside the Pose for 3D first picture.
  originalFiles?: Record<string, string> | null
}

export function TurnaroundView({ version, onRedraw, busy, locked, originalFiles }: Props) {
  const [cutout, setCutout] = useState(false)
  const [picked, setPicked] = useState<string[]>([])
  const [open, setOpen] = useState<number | null>(null)
  const views = TURNAROUND_VIEWS.filter((v) => v in version.files)
  const pathOf = (view: string) => (cutout ? (version.files[`${view}_cutout`] ?? version.files[view]) : version.files[view])
  const items: PictureItem[] = views.map((v) => ({ src: libraryUrl(pathOf(v))!, label: VIEW_NAMES[v] ?? v }))
  const perView = (version.metrics.views ?? {}) as Record<string, ViewMetrics>
  const passed = typeof version.metrics.views_passed === 'number' ? version.metrics.views_passed : null
  const total = typeof version.metrics.views_total === 'number' ? version.metrics.views_total : views.length
  // Ticks kept in the order the views are shown, and only for views this version has.
  const ticked = views.filter((v) => picked.includes(v))
  const toggle = (view: string) => setPicked((list) => (list.includes(view) ? list.filter((v) => v !== view) : [...list, view]))
  const flagged = views.filter((v) => perView[v]?.passed === false)
  const untickedFlagged = flagged.filter((v) => !ticked.includes(v))
  const own = ticked.filter((v) => perView[v]?.supplied === true)
  const redraw = () => {
    if (!ticked.length || busy) return
    onRedraw(ticked)
    setPicked([])
  }
  return (
    <div className="turnaround-view">
      <div className="view-intro muted small">
        We look at your character from every side so the 3D model gets the back right.{locked ? '' : ' If a view looks wrong, tick it; you can redraw several views together.'}
        <Info term="term.turnaround" />
      </div>
      <div className="viewer-toolbar">
        <button type="button" aria-pressed={!cutout} onClick={() => setCutout(false)}>
          Picture
        </button>
        <button type="button" aria-pressed={cutout} onClick={() => setCutout(true)}>
          Cutout
        </button>
        <Info term="term.cutout" />
        <span className="viewer-status">
          {views.length} views
          {passed !== null ? (
            <>
              , {passed} of {total} match the front
              <Info term="term.consistency_ok" />
            </>
          ) : null}
        </span>
      </div>
      <PoseReadyRow version={version} originalFiles={originalFiles} cutout={cutout} />
      <div className="views-row">
        {views.map((view) => {
          const path = pathOf(view)
          const m = perView[view]
          const failed = m?.passed === false
          const checks = m && isCheckList(m.checks) ? m.checks : null
          const numbers = m ? Object.entries(m).filter(([k, v]) => k !== 'passed' && typeof v === 'number') : []
          const on = ticked.includes(view)
          return (
            <div key={view} className={'view-cell' + (failed ? ' failed' : '') + (on ? ' picked' : '')}>
              <Zoomable className={'view-image' + (cutout ? ' checker' : '')} label={`the ${(VIEW_NAMES[view] ?? view).toLowerCase()} view`} onOpen={() => setOpen(views.indexOf(view))}>
                <img src={libraryUrl(path)} alt={view} />
              </Zoomable>
              <div className="row-between">
                <strong>
                  {VIEW_NAMES[view] ?? view}
                  {m?.supplied === true && (
                    <>
                      {' '}
                      <span className="chip chip-accent">Your view</span>
                      <Info term="term.supplied_view" />
                    </>
                  )}
                </strong>
                {m && m.passed !== undefined && (
                  <span className={'traffic-line ' + (m.passed ? 'ok' : 'bad')}>
                    <TrafficDot state={m.passed ? 'ok' : 'bad'} />
                    {m.passed ? 'matches' : 'needs attention'}
                  </span>
                )}
              </div>
              {/* A view drawn another way than the version asked for: a sheet side redrawn, or a side the way cannot draw. */}
              {typeof m?.method === 'string' && m.method !== 'supplied' && m.method !== (version.params?.turn_method ?? 'standard') && (
                <div className="muted small">Drawn the {m.method === 'standard' ? 'standard' : m.method === 'sheet' ? 'all-sides-in-one-picture' : 'shadow-free'} way.</div>
              )}
              {checks && checks.length > 0 && <PlainChecks checks={checks} compact />}
              {numbers.length > 0 && (
                <Disclosure label="Details">
                  <div className="chips chips-compact">
                    {numbers.map(([k, v]) => (
                      <span key={k} className="chip" title={k}>
                        <span className="chip-key">{metricPlain(k)}</span> {fmtNum(v as number)}
                      </span>
                    ))}
                  </div>
                </Disclosure>
              )}
              {!locked && (
                <label className={'redraw-pick' + (on ? ' on' : '')}>
                  <input type="checkbox" checked={on} onChange={() => toggle(view)} />
                  <span>{on ? 'Will be redrawn' : 'Redraw this view'}</span>
                </label>
              )}
            </div>
          )
        })}
        {views.length === 0 && <div className="muted">No view images in this version.</div>}
      </div>
      {views.length > 0 && locked && <div className="redraw-bar locked muted small">{locked}</div>}
      {views.length > 0 && !locked && (
        <div className={'redraw-bar' + (ticked.length ? ' ready' : '')}>
          <div className="redraw-summary" aria-live="polite">
            {ticked.length === 0 ? (
              <span className="muted">Tick the views that look wrong, then redraw them together. You get one new version; this one stays.</span>
            ) : (
              <>
                <strong>
                  {ticked.length} of {views.length} ticked:
                </strong>{' '}
                {viewList(ticked)}. <span className="muted">The other views are carried over unchanged into one new version.</span>
                {own.length > 0 && (
                  <div className="redraw-own small">
                    {viewList(own)} {own.length > 1 ? 'are your own pictures' : 'is your own picture'}: the new version has a drawn one instead. This version keeps yours.
                  </div>
                )}
              </>
            )}
          </div>
          <div className="redraw-actions">
            {untickedFlagged.length > 0 && (
              <button type="button" className="link small" onClick={() => setPicked((list) => [...new Set([...list, ...flagged])])}>
                {untickedFlagged.length === 1 ? 'Tick the one that needs attention' : `Tick the ${untickedFlagged.length} that need attention`}
              </button>
            )}
            {ticked.length > 0 && (
              <button type="button" className="link small" onClick={() => setPicked([])}>
                Clear
              </button>
            )}
            <button type="button" className="redraw-go" disabled={!ticked.length || busy} onClick={redraw} title={ticked.length ? 'Draws the ticked views again and makes one new version' : 'Tick at least one view first'}>
              {ticked.length > 1 ? `Redraw ${ticked.length} views` : ticked.length === 1 ? 'Redraw 1 view' : 'Redraw'}
            </button>
          </div>
        </div>
      )}
      <BaldRow version={version} cutout={cutout} />
      {open !== null && <PictureViewer items={items} start={open} checker={cutout} onClose={() => setOpen(null)} />}
    </div>
  )
}
