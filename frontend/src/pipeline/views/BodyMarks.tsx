import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { api, libraryUrl, type BodyMarks as Marks, type Mark, type MarkView } from '../../api'
import { Info } from '../../components/Info'
import { oneSentence } from '../../help'
import { PictureViewer, Zoomable, type PictureItem } from '../../components/PictureViewer'
import { useEditHistory } from '../../editHistory'
import { UndoRedoButtons } from '../../components/UndoRedo'
import { snapshotState, stateRestorePlan, type MarkState } from './marksHistory'

// Mark the body (step 2): the app's marks on the front picture and one other picture (a three-quarter, side or back
// view), each draggable; all six open enlarged. The marks are a note on this version's pictures (marks.json): moving one,
// placing one the app could not see, pressing These look right or putting the app's marks back changes this version only,
// and the app's own marks are always kept. One mark of yours can be taken back (Remove, or Delete on the mark): a placed
// one comes off the picture again, a moved one goes back to where the app put it. Joint names are the person's own left and right on every picture.

const BONES: [string, string][] = [
  ['head_top', 'chin'],
  ['shoulder_L', 'shoulder_R'],
  ['shoulder_L', 'elbow_L'], ['elbow_L', 'wrist_L'], ['shoulder_R', 'elbow_R'], ['elbow_R', 'wrist_R'],
  ['shoulder_L', 'hip_L'], ['shoulder_R', 'hip_R'], ['hip_L', 'hip_R'], ['hip_L', 'groin'], ['hip_R', 'groin'],
  ['hip_L', 'knee_L'], ['knee_L', 'ankle_L'], ['hip_R', 'knee_R'], ['knee_R', 'ankle_R'],
  ['ankle_L', 'heel_L'], ['heel_L', 'toe_L'], ['ankle_R', 'heel_R'], ['heel_R', 'toe_R'],
]
const SIDE_NAMES: Record<MarkView, string> = {
  front: 'Front',
  quarter: 'Three-quarter',
  quarter2: 'Three-quarter (other side)',
  left: 'Left side',
  right: 'Right side',
  back: 'Back',
}
const VIEW_ORDER: MarkView[] = ['front', 'quarter', 'quarter2', 'left', 'right', 'back']
const FINGERTIP = /^tip_/

// Which marks a picture draws: every mark with a place, except the far side of a side picture whose marks were found
// before the app learned to leave them unplaced (the far side is behind the body, so a position there means little).
function shown(view: MarkView, joint: string, m: Mark): boolean {
  if (m.x === null || m.y === null) return false
  if ((view !== 'left' && view !== 'right') || m.source === 'you') return true
  const own = view === 'left' ? '_L' : '_R'
  return !(joint.endsWith('_L') || joint.endsWith('_R')) || joint.endsWith(own)
}

// The marks the app could not see on this picture and that can be placed by hand, in the panel's order.
function unseen(data: Marks, view: MarkView): string[] {
  const ph = data.placeholders?.[view] ?? {}
  const marks = data.marks[view] ?? {}
  return Object.keys(ph).filter((j) => marks[j] && (marks[j].x === null || marks[j].y === null))
}

// The marks the app could not see on this picture that you placed, which can be taken off again.
function placed(data: Marks, view: MarkView): string[] {
  const auto = data.automatic[view] ?? {}
  const marks = data.marks[view] ?? {}
  return Object.keys(marks).filter((j) => marks[j].source === 'you' && auto[j] && (auto[j].x === null || auto[j].y === null))
}

type MoveFn = (view: MarkView, joint: string, x: number, y: number) => void
type DropFn = (view: MarkView, joint: string) => void

function NotSeen({ data, view, busy, onMove, onDrop }: { data: Marks; view: MarkView; busy: boolean; onMove: MoveFn; onDrop: DropFn }) {
  const list = unseen(data, view)
  const yours = placed(data, view)
  if (!list.length && !yours.length) return null
  return (
    <div className="body-marks-unseen small">
      {list.length > 0 && (
        <div>
          <span className="muted">Not seen in this picture{busy ? '' : ' (place one if you can see it, then drag it into place)'}:</span>{' '}
          {list.map((j) => (
            <button
              key={j}
              type="button"
              className="chip"
              disabled={busy}
              onClick={() => onMove(view, j, data.placeholders![view]![j].x, data.placeholders![view]![j].y)}
              title={`Puts the ${data.names[j] ?? j} where it usually is, for you to drag into place`}
            >
              Place the {data.names[j] ?? j}
            </button>
          ))}
        </div>
      )}
      {yours.length > 0 && (
        <div>
          <span className="muted">Placed by you{busy ? '' : ' (remove one you placed by mistake)'}:</span>{' '}
          {yours.map((j) => (
            <button
              key={j}
              type="button"
              className="chip"
              disabled={busy}
              onClick={() => onDrop(view, j)}
              title={`Takes the ${data.names[j] ?? j} off this picture again`}
            >
              Remove the {data.names[j] ?? j}
            </button>
          ))}
        </div>
      )}
    </div>
  )
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
  onMove: MoveFn
  onDrop: DropFn
  // Opens this picture enlarged (the small one on the card).
  onOpen?: () => void
  // Drawn in the picture viewer: fills its stage (Fit), or at the picture's own pixels (Actual size).
  large?: boolean
  actual?: boolean
}

// The marks are drawn in the picture's own pixels (the svg's viewBox) and a drag is turned back into them through the
// svg's on-screen transform, so a mark lands on the same picture point at any displayed size, small or enlarged.
function MarkedPicture({ data, view, busy, onMove, onDrop, onOpen, large = false, actual = false }: PictureProps) {
  const pic = data.pictures[view]
  const marks = data.marks[view] ?? {}
  const svg = useRef<SVGSVGElement>(null)
  const [drag, setDrag] = useState<{ joint: string; x: number; y: number } | null>(null)
  if (!pic?.path) return null
  const [w, h] = pic.size
  const r = Math.max(4, Math.round(w / 110))
  const at = (joint: string) => (drag && drag.joint === joint ? drag : (marks[joint] as { x: number; y: number }))
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
    ev.currentTarget.focus({ preventScroll: true })
    try {
      ;(ev.target as Element).setPointerCapture(ev.pointerId)
    } catch {
      // no capture (a pointer the browser no longer tracks): the drag still follows moves over the mark
    }
    const p = toPicture(ev)
    if (p) setDrag({ joint, ...p })
  }
  const move = (ev: PointerEvent<SVGCircleElement>) => {
    if (!drag) return
    const p = toPicture(ev)
    if (p) setDrag({ ...drag, ...p })
  }
  const up = (ev: PointerEvent<SVGCircleElement>) => {
    if (!drag) return
    const d = drag
    setDrag(null)
    const m = marks[d.joint]
    if (!m || m.x === null || m.y === null || Math.hypot(m.x - d.x, m.y - d.y) >= 0.5) {
      // A drag that moved the mark: let go of its focus, so the arrow keys change picture in the enlarged viewer
      // instead of nudging the mark.
      ev.currentTarget.blur()
      onMove(view, d.joint, d.x, d.y)
    }
  }
  const key = (joint: string) => (ev: KeyboardEvent<SVGCircleElement>) => {
    if (drag) return // a drag in progress: its release saves the position, so no key acts on it mid-drag
    if ((ev.key === 'Delete' || ev.key === 'Backspace') && marks[joint]?.source === 'you' && !busy) {
      ev.preventDefault()
      onDrop(view, joint)
      return
    }
    const step = ev.shiftKey ? 10 : 2
    const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[ev.key]
    const m = marks[joint]
    if (!d || !m || m.x === null || m.y === null || busy) return
    ev.preventDefault()
    onMove(view, joint, Math.min(w, Math.max(0, m.x + d[0])), Math.min(h, Math.max(0, m.y + d[1])))
  }
  const joints = Object.keys(marks).filter((j) => shown(view, j, marks[j]))
  const stageClass = 'body-marks-stage checker' + (large ? ' large' : '') + (large && actual ? ' actual' : '')
  const stageStyle = large ? (actual ? { width: w, height: h } : undefined) : { aspectRatio: `${w} / ${h}` }
  const marked = (
      <div className={stageClass} style={stageStyle}>
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
                aria-label={markTitle(name, m) + '. Drag it, or use the arrow keys.' + (m.source === 'you' ? ' Delete takes it back.' : '')}
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
  )
  if (large) return marked
  return (
    <figure className="body-marks-picture">
      <figcaption className="row-between small">
        <strong>{SIDE_NAMES[view]}</strong>
        {!pic.found && <span className="muted">The app could not find the body here: drag every mark into place.</span>}
      </figcaption>
      {onOpen ? (
        <Zoomable className="body-marks-zoom" label={`the ${SIDE_NAMES[view].toLowerCase()} picture with its marks`} onOpen={onOpen}>
          {marked}
        </Zoomable>
      ) : (
        marked
      )}
      <NotSeen data={data} view={view} busy={busy} onMove={onMove} onDrop={onDrop} />
    </figure>
  )
}

function Legend() {
  return (
    <span className="body-marks-key small muted">
      <span className="key-dot mark-left" /> left <span className="key-dot mark-right" /> right <span className="key-dot mark-guessed" /> guessed or unsure{' '}
      <span className="key-dot mark-yours" /> yours
    </span>
  )
}

function handNotes(data: Marks, views: MarkView[]): string[] {
  const out: string[] = []
  for (const view of views) {
    const hands = data.hands[view]
    if (!hands || !data.pictures[view]) continue
    const sides = (view === 'left' || view === 'right' ? [view] : ['left', 'right']) as ('left' | 'right')[]
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
  const [open, setOpen] = useState<number | null>(null)

  useEffect(() => {
    // The card keys this panel on the version, so a new version starts with fresh state.
    let live = true
    api
      .getMarks(versionId)
      .then((d) => {
        if (!live) return
        setData(d)
        if (!d.pictures.left) setSide(VIEW_ORDER.find((v) => v !== 'front' && d.pictures[v]) ?? 'left')
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

  // Undo and redo: each change is saved as before, then a step remembers the marks from before and after it.
  const { history, activate } = useEditHistory()
  const dataRef = useRef<Marks | null>(null)
  const show = useCallback((d: Marks) => {
    dataRef.current = d
    setData(d)
  }, [])
  useEffect(() => {
    dataRef.current = data
  }, [data])
  // Puts the marks back to a snapshot with the existing PUT (moves) and reset calls: only what differs is sent.
  const putBack = useCallback(
    async (target: MarkState) => {
      const cur = dataRef.current
      if (!cur) return
      const plan = stateRestorePlan(snapshotState(cur), target)
      if (plan.moves.length) show(await api.moveMarks(versionId, plan.moves))
      for (const r of plan.resets) show(await api.resetMarks(versionId, r.view, r.joint))
      if (plan.accepted !== null) show(await api.setMarksAccepted(versionId, plan.accepted))
    },
    [show, versionId],
  )
  const change = useCallback(
    async (label: string, key: string | undefined, p: Promise<Marks>) => {
      const cur = dataRef.current
      const before = cur ? snapshotState(cur) : null
      setBusy(true)
      setError(null)
      try {
        const next = await p
        show(next)
        if (before) {
          const after = snapshotState(next)
          history.record({ label, key, undo: () => putBack(before), redo: () => putBack(after) })
        }
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy(false)
      }
    },
    [history, putBack, show],
  )
  const nameOf = (joint: string) => dataRef.current?.names[joint] ?? joint
  const onMove = useCallback(
    (view: MarkView, joint: string, x: number, y: number) =>
      void change(`move the ${nameOf(joint)}`, `move:${view}:${joint}`, api.moveMarks(versionId, [{ view, joint, x, y }])),
    [change, versionId],
  )
  const onDrop = useCallback(
    (view: MarkView, joint: string) => void change(`remove the ${nameOf(joint)}`, undefined, api.resetMarks(versionId, view, joint)),
    [change, versionId],
  )
  const sayAccepted = () => void change('say these look right', undefined, api.acceptMarks(versionId))
  const putBackAll = () => void change("put back the app's marks", undefined, api.resetMarks(versionId))
  const historyError = history.lastError ? (history.lastError instanceof Error ? history.lastError.message : String(history.lastError)) : null

  if (hidden) return null
  const asks = data?.to_check ?? []
  const stale = data?.stale ?? []
  const pair = data ? (['front', side] as MarkView[]).filter((v) => data.pictures[v]?.path) : []
  const notes = data ? handNotes(data, pair) : []
  const yours = !!data && Object.values(data.marks).some((v) => Object.values(v ?? {}).some((m) => m.source === 'you'))
  // The front and the chosen picture, small on the card; either opens enlarged in the app's picture viewer, which steps
  // through all of this version's pictures (left and right arrows, unless a mark has the focus) with the marks still
  // drawn and draggable and the marks it did not see ready to place.
  const all = data ? VIEW_ORDER.filter((v) => data.pictures[v]?.path) : []
  const enlarged: PictureItem[] = data
    ? all.map((v) => ({
        src: libraryUrl(data.pictures[v]!.path)!,
        label: `${SIDE_NAMES[v]} picture, body marks`,
        size: data.pictures[v]!.size,
        render: (actual: boolean) => <MarkedPicture data={data} view={v} busy={busy || !!locked} onMove={onMove} onDrop={onDrop} large actual={actual} />,
        footer: (
          <div className="row-between small">
            <span className="muted">{locked ? 'The marks of this version cannot be changed.' : 'Drag a mark to move it, or select it and use the arrow keys (Shift for bigger steps). Delete takes back one of yours.'}</span>
            <Legend />
            <NotSeen data={data} view={v} busy={busy || !!locked} onMove={onMove} onDrop={onDrop} />
          </div>
        ),
      }))
    : []
  return (
    <section className={'body-marks' + (data ? ` body-marks-${data.state}` : '')} aria-busy={busy || !data} onPointerDownCapture={activate} onFocusCapture={activate}>
      <div className="view-intro view-intro-flush muted small">
        <strong>Mark the body</strong> <Info term="term.body_marks" /> Where the joints are on each picture. Left and right are always the person's own, so on the back their left is on your left.
        {locked ? '' : ' Drag a mark to move it.'}
      </div>
      {!data && !error && <div className="muted small">Finding the marks...</div>}
      {(error || historyError) && <div className="body-marks-error small">{oneSentence(`The marks could not be shown: ${error ?? historyError}`)}</div>}
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
            <span className="muted small">Second picture:</span>
            {VIEW_ORDER.filter((v) => v !== 'front' && data.pictures[v]).map((v) => (
              <button key={v} type="button" aria-pressed={side === v} onClick={() => setSide(v)}>
                {SIDE_NAMES[v]}
              </button>
            ))}
            <Legend />
          </div>
          <div className="body-marks-pictures">
            {pair.map((v) => (
              <MarkedPicture key={v} data={data} view={v} busy={busy || !!locked} onMove={onMove} onDrop={onDrop} onOpen={() => setOpen(all.indexOf(v))} />
            ))}
          </div>
          {open !== null && <PictureViewer items={enlarged} start={open} checker onClose={() => setOpen(null)} />}
          {notes.length > 0 && (
            <ul className="body-marks-notes muted small">
              {notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
          {!locked && (
            <div className="body-marks-actions">
              <UndoRedoButtons history={history} />
              {data.state === 'check' && (
                <button type="button" disabled={busy} onClick={sayAccepted}>
                  These look right
                </button>
              )}
              {(yours || data.accepted) && (
                <button type="button" className="link" disabled={busy} onClick={putBackAll}>
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
