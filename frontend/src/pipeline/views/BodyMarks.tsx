import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { api, libraryUrl, type BodyMarks as Marks, type Mark, type MarkView } from '../../api'
import { Info } from '../../components/Info'

// Mark the body (step 2): the app's marks on the front picture and one side picture, each draggable. The marks are a note
// on this version's pictures (marks.json): moving one, pressing These look right or putting the app's marks back changes
// this version only, and the app's own marks are always kept.

const BONES: [string, string][] = [
  ['head_top', 'chin'],
  ['shoulder_L', 'shoulder_R'],
  ['shoulder_L', 'elbow_L'], ['elbow_L', 'wrist_L'], ['shoulder_R', 'elbow_R'], ['elbow_R', 'wrist_R'],
  ['shoulder_L', 'hip_L'], ['shoulder_R', 'hip_R'], ['hip_L', 'hip_R'], ['hip_L', 'groin'], ['hip_R', 'groin'],
  ['hip_L', 'knee_L'], ['knee_L', 'ankle_L'], ['hip_R', 'knee_R'], ['knee_R', 'ankle_R'],
  ['ankle_L', 'heel_L'], ['heel_L', 'toe_L'], ['ankle_R', 'heel_R'], ['heel_R', 'toe_R'],
]
const SIDE_NAMES: Record<MarkView, string> = { front: 'Front', left: 'Left side', right: 'Right side' }
const FINGERTIP = /^tip_/

// Which marks a picture shows: everything on the front; a side picture only its own side (the far side is hidden behind
// the body, so the app's marks there mean little).
function shown(view: MarkView, joint: string): boolean {
  if (view === 'front') return true
  const own = view === 'left' ? '_L' : '_R'
  return !(joint.endsWith('_L') || joint.endsWith('_R')) || joint.endsWith(own)
}

function markClass(joint: string, m: Mark): string {
  const side = joint.endsWith('_L') ? ' mark-left' : joint.endsWith('_R') ? ' mark-right' : ' mark-middle'
  const state = m.stale ? ' mark-stale' : m.source === 'you' ? ' mark-yours' : m.guessed ? ' mark-guessed' : m.sure === false ? ' mark-unsure' : ''
  return 'body-mark' + side + state + (FINGERTIP.test(joint) ? ' mark-tip' : '')
}

function markTitle(name: string, m: Mark): string {
  if (m.stale) return `${name}: you moved this one on a picture that was drawn again; check it`
  if (m.source === 'you') return `${name}: placed by you`
  if (m.guessed) return `${name}: the app guessed this one`
  if (m.sure === false) return `${name}: the app is not sure of this one`
  return `${name}: placed by the app`
}

type PictureProps = {
  data: Marks
  view: MarkView
  busy: boolean
  onMove: (view: MarkView, joint: string, x: number, y: number) => void
}

function MarkedPicture({ data, view, busy, onMove }: PictureProps) {
  const pic = data.pictures[view]
  const marks = data.marks[view] ?? {}
  const svg = useRef<SVGSVGElement>(null)
  const [drag, setDrag] = useState<{ joint: string; x: number; y: number } | null>(null)
  if (!pic?.path) return null
  const [w, h] = pic.size
  const r = Math.max(4, Math.round(w / 110))
  const at = (joint: string) => (drag && drag.joint === joint ? drag : marks[joint])
  const toPicture = (ev: PointerEvent) => {
    const el = svg.current
    const ctm = el?.getScreenCTM()
    if (!el || !ctm) return null
    const p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(ctm.inverse())
    return { x: Math.min(w, Math.max(0, p.x)), y: Math.min(h, Math.max(0, p.y)) }
  }
  const down = (joint: string) => (ev: PointerEvent<SVGCircleElement>) => {
    if (busy) return
    ev.preventDefault()
    ;(ev.target as Element).setPointerCapture(ev.pointerId)
    const p = toPicture(ev)
    if (p) setDrag({ joint, ...p })
  }
  const move = (ev: PointerEvent<SVGCircleElement>) => {
    if (!drag) return
    const p = toPicture(ev)
    if (p) setDrag({ ...drag, ...p })
  }
  const up = () => {
    if (!drag) return
    const d = drag
    setDrag(null)
    const m = marks[d.joint]
    if (!m || Math.hypot(m.x - d.x, m.y - d.y) >= 0.5) onMove(view, d.joint, d.x, d.y)
  }
  const key = (joint: string) => (ev: KeyboardEvent<SVGCircleElement>) => {
    const step = ev.shiftKey ? 10 : 2
    const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[ev.key]
    const m = marks[joint]
    if (!d || !m || busy) return
    ev.preventDefault()
    onMove(view, joint, Math.min(w, Math.max(0, m.x + d[0])), Math.min(h, Math.max(0, m.y + d[1])))
  }
  const joints = Object.keys(marks).filter((j) => shown(view, j))
  return (
    <figure className="body-marks-picture">
      <figcaption className="row-between small">
        <strong>{SIDE_NAMES[view]}</strong>
        {!pic.found && <span className="muted">The app could not find the body here: drag every mark into place.</span>}
      </figcaption>
      <div className="body-marks-stage checker" style={{ aspectRatio: `${w} / ${h}` }}>
        <img src={libraryUrl(pic.path)} alt={`${SIDE_NAMES[view].toLowerCase()} picture with the body marks`} draggable={false} />
        <svg ref={svg} viewBox={`0 0 ${w} ${h}`} role="group" aria-label={`Marks on the ${SIDE_NAMES[view].toLowerCase()} picture`}>
          {BONES.filter(([a, b]) => joints.includes(a) && joints.includes(b)).map(([a, b]) => {
            const p = at(a)!
            const q = at(b)!
            return <line key={a + b} className="body-bone" x1={p.x} y1={p.y} x2={q.x} y2={q.y} strokeWidth={r / 2.5} />
          })}
          {joints.map((joint) => {
            const m = marks[joint]
            const p = at(joint)!
            const name = data.names[joint] ?? joint
            const small = FINGERTIP.test(joint)
            const big = !small && (m.guessed || m.sure === false || m.stale)
            return (
              <circle
                key={joint}
                className={markClass(joint, m) + (drag?.joint === joint ? ' dragging' : '')}
                cx={p.x}
                cy={p.y}
                r={small ? r * 0.55 : big ? r * 1.5 : r}
                strokeWidth={r / 3}
                tabIndex={busy ? -1 : 0}
                aria-label={markTitle(name, m) + '. Drag it, or use the arrow keys.'}
                onPointerDown={down(joint)}
                onPointerMove={move}
                onPointerUp={up}
                onPointerCancel={() => setDrag(null)}
                onKeyDown={key(joint)}
              >
                <title>{markTitle(name, m)}</title>
              </circle>
            )
          })}
        </svg>
      </div>
    </figure>
  )
}

function handNotes(data: Marks): string[] {
  const out: string[] = []
  for (const view of ['front', 'left', 'right'] as MarkView[]) {
    const hands = data.hands[view]
    if (!hands || !data.pictures[view]) continue
    const sides = (view === 'front' ? ['left', 'right'] : [view]) as ('left' | 'right')[]
    for (const s of sides) {
      if (hands[s] && !hands[s].found) out.push(`No ${s} hand was found in the ${SIDE_NAMES[view].toLowerCase()} picture, so it has no fingertip marks.`)
    }
  }
  return out
}

export function BodyMarksPanel({ versionId, locked }: { versionId: string; locked?: boolean }) {
  const [data, setData] = useState<Marks | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hidden, setHidden] = useState(false)
  const [busy, setBusy] = useState(false)
  const [side, setSide] = useState<MarkView>('left')

  useEffect(() => {
    // The card keys this panel on the version, so a new version starts with fresh state.
    let live = true
    api
      .getMarks(versionId)
      .then((d) => {
        if (!live) return
        setData(d)
        if (!d.pictures.left && d.pictures.right) setSide('right')
      })
      .catch((e: unknown) => {
        if (!live) return
        const msg = e instanceof Error ? e.message : String(e)
        // Not a character, or a version that is not finished: no panel.
        if (/human body|not done|side views step/.test(msg)) setHidden(true)
        else setError(msg)
      })
    return () => {
      live = false
    }
  }, [versionId])

  const run = useCallback(
    (p: Promise<Marks>) => {
      setBusy(true)
      setError(null)
      p.then(setData)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
        .finally(() => setBusy(false))
    },
    [],
  )
  const onMove = useCallback((view: MarkView, joint: string, x: number, y: number) => run(api.moveMarks(versionId, [{ view, joint, x, y }])), [run, versionId])

  if (hidden) return null
  const asks = data?.to_check ?? []
  const stale = data?.stale ?? []
  const notes = data ? handNotes(data) : []
  const yours = !!data && Object.values(data.marks).some((v) => Object.values(v ?? {}).some((m) => m.source === 'you'))
  return (
    <section className={'body-marks' + (data ? ` body-marks-${data.state}` : '')} aria-busy={busy || !data}>
      <div className="view-intro view-intro-flush muted small">
        <strong>Mark the body</strong> <Info term="term.body_marks" /> Where the joints are on the front and side pictures.
        {locked ? '' : ' Drag a mark to move it.'}
      </div>
      {!data && !error && <div className="muted small">Finding the marks...</div>}
      {error && <div className="body-marks-error small">The marks could not be shown: {error}</div>}
      {data && (
        <>
          <div className="body-marks-state small" aria-live="polite">
            {data.state === 'sure' && <span>The app placed these marks and is confident.</span>}
            {data.state === 'edited' && <span>{yours ? 'You moved these marks.' : 'You checked these marks.'}</span>}
            {data.state === 'check' && (
              <>
                <strong>Please check these marks.</strong>
                <ul className="body-marks-asks">
                  {stale.map((s) => (
                    <li key={`stale-${s.view}-${s.joint}`}>
                      The {s.name} in the {SIDE_NAMES[s.view].toLowerCase()} picture: you moved it before this picture was drawn again.
                    </li>
                  ))}
                  {asks.map((a, i) => (
                    <li key={`${a.view}-${a.joint ?? i}`}>
                      {a.name ? <>{a.name.charAt(0).toUpperCase() + a.name.slice(1)}: {a.words.charAt(0).toLowerCase() + a.words.slice(1)}</> : a.words}
                    </li>
                  ))}
                </ul>
                {!data.stop && <div className="muted">This is a note: the automatic run does not stop for it, because nothing uses the marks yet.</div>}
              </>
            )}
          </div>
          <div className="viewer-toolbar">
            <span className="muted small">Side picture:</span>
            {(['left', 'right'] as MarkView[]).filter((v) => data.pictures[v]).map((v) => (
              <button key={v} type="button" aria-pressed={side === v} onClick={() => setSide(v)}>
                {SIDE_NAMES[v]}
              </button>
            ))}
            <span className="body-marks-key small muted">
              <span className="key-dot mark-left" /> left <span className="key-dot mark-right" /> right <span className="key-dot mark-guessed" /> guessed or unsure{' '}
              <span className="key-dot mark-yours" /> yours
            </span>
          </div>
          <div className="body-marks-pictures">
            <MarkedPicture data={data} view="front" busy={busy || !!locked} onMove={onMove} />
            {data.pictures[side] && <MarkedPicture data={data} view={side} busy={busy || !!locked} onMove={onMove} />}
          </div>
          {notes.length > 0 && (
            <ul className="body-marks-notes muted small">
              {notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
          {!locked && (
            <div className="body-marks-actions">
              {data.state === 'check' && (
                <button type="button" disabled={busy} onClick={() => run(api.acceptMarks(versionId))}>
                  These look right
                </button>
              )}
              {(yours || data.accepted) && (
                <button type="button" className="link" disabled={busy} onClick={() => run(api.resetMarks(versionId))}>
                  Put back the app's marks
                </button>
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}
