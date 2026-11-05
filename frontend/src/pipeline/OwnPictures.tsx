// Start from your own images on the asset page: "Use my own picture" on the first step, "I have my own side or back
// views" on the turnaround, and the experimental reference picture in "Change something".
import { useState } from 'react'
import { api, type Asset, type StageId, type Version } from '../api'
import { ImageDrop, type PickedImage } from '../components/ImageDrop'
import { Info } from '../components/Info'
import { Disclosure } from '../components/ui'
import { useRefData } from '../refdata'

type Act = (fn: () => Promise<unknown>) => Promise<void> | void

export const VIEW_NAMES: Record<string, string> = { front: 'Front', left: 'Left side', back: 'Back', right: 'Right side', quarter: 'Three-quarter', top: 'Top' }

// Which way the subject faces in each view, in words (the diagram shows the same from above).
const FACING_TEXT: Record<string, string> = {
  front: 'It faces you.',
  left: 'Its left side toward you; it faces your left.',
  back: 'It faces away from you.',
  right: 'Its right side toward you; it faces your right.',
  quarter: 'Turned part way: it faces toward you and to your right.',
  top: 'Seen from straight above.',
}
const FACING_DIR: Record<string, [number, number] | null> = { front: [0, 1], left: [-1, 0], back: [0, -1], right: [1, 0], quarter: [0.71, 0.71], top: null }

// Seen from above: the subject is the circle, its nose the triangle, you (the camera) at the bottom.
export function FacingDiagram({ view }: { view: string }) {
  const d = FACING_DIR[view]
  const cx = 24
  const cy = 20
  const r = 10
  let nose = ''
  if (d) {
    const [x, y] = d
    const tip = [cx + x * (r + 7), cy + y * (r + 7)]
    const px = -y
    const py = x
    const b1 = [cx + x * r + px * 5, cy + y * r + py * 5]
    const b2 = [cx + x * r - px * 5, cy + y * r - py * 5]
    nose = `${tip[0]},${tip[1]} ${b1[0]},${b1[1]} ${b2[0]},${b2[1]}`
  }
  return (
    <svg className="facing-diagram" viewBox="0 0 48 48" width="48" height="48" role="img" aria-label={FACING_TEXT[view] ?? view}>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="currentColor" strokeWidth="2" />
      {d ? <polygon points={nose} fill="currentColor" /> : <circle cx={cx} cy={cy} r="3" fill="currentColor" />}
      <rect x="18" y="40" width="12" height="6" rx="1" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <title>{FACING_TEXT[view]}</title>
    </svg>
  )
}

export function isOwnPicture(v: Pick<Version, 'params'>): boolean {
  return v.params?.source === 'upload'
}

export function OwnPictures({
  asset,
  stage,
  active,
  canRun,
  busy,
  act,
  onCreated,
}: {
  asset: Asset
  stage: StageId
  active: Version | undefined
  canRun: boolean
  busy: boolean
  act: Act
  onCreated: (versionId: string) => void
}) {
  if (stage === 'concept') return <ConceptUpload asset={asset} busy={busy} act={act} onCreated={onCreated} />
  if (stage === 'turnaround' && asset.category !== 'material' && canRun) return <OwnViews asset={asset} active={active} busy={busy} act={act} onCreated={onCreated} />
  return null
}

function ConceptUpload({ asset, busy, act, onCreated }: { asset: Asset; busy: boolean; act: Act; onCreated: (id: string) => void }) {
  const [open, setOpen] = useState(false)
  const [picture, setPicture] = useState<PickedImage | null>(null)
  const [note, setNote] = useState('')
  const material = asset.category === 'material'
  const send = () =>
    act(async () => {
      if (!picture) return
      const r = await api.uploadConcept(asset.id, { image: picture.dataUrl, ...(note.trim() ? { note: note.trim() } : {}) })
      setPicture(null)
      setNote('')
      setOpen(false)
      onCreated(r.version.id)
    })
  return (
    <div className="own-pictures">
      <div className="row-gap">
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} title={material ? 'Make this material from a photo you have' : 'Start from a picture you already have instead of painting one'}>
          {material ? 'Use my own photo' : 'Use my own picture'}
        </button>
        <Info term="term.own_picture" />
      </div>
      {open && (
        <div className="own-pictures-panel">
          <ImageDrop
            value={picture}
            onChange={setPicture}
            label={material ? 'Your photo' : 'Your picture'}
            disabled={busy}
            hint={
              material
                ? 'Best: taken straight on, in even light, the surface filling the whole photo.'
                : 'Best: one subject on a plain background, all of it in the picture. It is cut out and checked like a painted one.'
            }
          />
          {!asset.prompt.trim() && (
            <label className="field">
              <span className="field-label">
                What is it? (optional, helps the later steps)
                <Info term="term.what_is_it_note" />
              </span>
              <input value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} placeholder="A few words about your picture" />
            </label>
          )}
          <div className="row-gap">
            <button type="button" className="primary" disabled={busy || !picture} onClick={send}>
              {material ? 'Use this photo' : 'Use this picture'}
            </button>
            <button type="button" className="link small" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function OwnViews({ asset, active, busy, act, onCreated }: { asset: Asset; active: Version | undefined; busy: boolean; act: Act; onCreated: (id: string) => void }) {
  const { ref } = useRefData()
  const [open, setOpen] = useState(false)
  const [pictures, setPictures] = useState<Record<string, PickedImage | null>>({})
  const views = (ref?.categories.find((c) => c.id === asset.category)?.views ?? ['front', 'left', 'back', 'right', 'quarter']).filter((v) => v !== 'front')
  const chosen = Object.entries(pictures).filter((e): e is [string, PickedImage] => !!e[1])
  const onVersion = active?.stage === 'turnaround' && active.status === 'done' ? active : null
  const drawWithMine = () =>
    act(async () => {
      const provided = Object.fromEntries(chosen.map(([v, p]) => [v, p.dataUrl]))
      const r = await api.runStage(asset.id, 'turnaround', { params: { provided_views: provided } })
      const first = Array.isArray(r) ? r[0] : r
      setPictures({})
      setOpen(false)
      onCreated(first.version.id)
    })
  const useOne = (view: string, p: PickedImage) =>
    act(async () => {
      if (!onVersion) return
      const r = await api.uploadView(onVersion.id, view, p.dataUrl)
      setPictures((x) => ({ ...x, [view]: null }))
      onCreated(r.version.id)
    })
  return (
    <div className="own-pictures">
      <div className="row-gap">
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} title="Use your own pictures of the other sides; the app draws only the ones you leave out">
          I have my own side or back views
        </button>
        <Info term="term.own_views" />
      </div>
      {open && (
        <div className="own-pictures-panel">
          <div className="muted small">
            Add a picture for any side you have. The front is your first picture. Each side is checked against the front; one that does not match gets a warning but is kept.
          </div>
          <div className="own-views-grid">
            {views.map((view) => (
              <div key={view} className="own-view-slot">
                <ImageDrop compact value={pictures[view] ?? null} onChange={(p) => setPictures((x) => ({ ...x, [view]: p }))} label={VIEW_NAMES[view] ?? view} term="term.supplied_view" disabled={busy}>
                  <FacingDiagram view={view} />
                  <span className="muted small">{FACING_TEXT[view]}</span>
                </ImageDrop>
                {onVersion && pictures[view] && (
                  <button type="button" className="small-btn" disabled={busy} onClick={() => useOne(view, pictures[view]!)} title="Make a new version with this one view replaced by your picture">
                    Use for the {(VIEW_NAMES[view] ?? view).toLowerCase()} view
                  </button>
                )}
              </div>
            ))}
          </div>
          <div className="row-gap">
            <button type="button" className="primary" disabled={busy || chosen.length === 0} onClick={drawWithMine}>
              Draw the other sides, using mine
            </button>
            <span className="muted small">
              {chosen.length ? `${chosen.length} of yours; the app draws the rest.` : 'Add at least one picture.'}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

export function ReferencePicture({ value, onChange, disabled }: { value: string | null; onChange: (dataUrl: string | null) => void; disabled: boolean }) {
  const [picked, setPicked] = useState<PickedImage | null>(null)
  const set = (p: PickedImage | null) => {
    setPicked(p)
    onChange(p?.dataUrl ?? null)
  }
  const shown = value ? picked : null
  return (
    <Disclosure
      label={
        <>
          Reference picture <span className="chip chip-warn">Experimental</span>
        </>
      }
    >
      <ImageDrop
        compact
        value={shown}
        onChange={set}
        label="Reference picture (experimental)"
        term="term.reference_picture"
        disabled={disabled}
        hint="Optional. A picture the editor may look at for your change, such as a helmet to copy. Say in your words what to take from it. How closely it follows a reference has not been measured yet."
      />
    </Disclosure>
  )
}
