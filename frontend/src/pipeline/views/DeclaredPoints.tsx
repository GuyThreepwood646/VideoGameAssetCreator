// Hanging parts (WP-20 step 5): the sway parts the person declared for this character, each with the point where it hangs.
// Place point, then click the model: the viewer's pointer tool hands the spot to the rig. Saving writes this version's
// parts.json; it does not redo the rig, so the points are read the next time the rig is made.
// Undo and redo (WP-71): every saved change of a point (placing, removing) is a step; each one puts the earlier or later
// list back through the same PUT. Points still pending (not kept) are not saved, so they are not steps.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type HingeSide, type PlacedPart, type Version } from '../../api'
import { ANGLE_RANGE, DISTANCE_RANGE, HINGE_SIDES, isMoving, isMultiPoint, KIND_WORDS, markable, MAX_STIFF_POINTS, withMotion, withPoint, withStiffPoint, withoutStiffPoint, withoutStiffPoints, type Motion, type Point } from '../../declaredPoints'
import { useEditHistory, type EditHistory } from '../../editHistory'
import { suggestionLine, suggestionNote } from '../../suggestion'
import type { ItemSuggestionState } from '../../api'
import { UndoRedoButtons } from '../../components/UndoRedo'
import { savePointsStep } from './pointsHistory'

function msg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export type DeclaredPointsState = {
  parts: PlacedPart[] | null
  sway: PlacedPart[]
  busy: boolean
  error: string | null
  // Sets one part's point (null clears it) and saves all the version's points.
  setPoint: (name: string, point: Point | null) => void
  // Stiff parts (WP-69): a click adds a point to the pending list, shown on the model before anything is saved.
  // Keep saves the pending points with the saved ones; Discard drops them; Remove last and Remove region act on the
  // saved points (and take a pending point first).
  pending: Record<string, Point[]>
  // Sets a part's pending points (a suggestion the person has not kept yet) and reloads the declared parts.
  setPendingPoints: (name: string, points: Point[]) => void
  reloadParts: () => Promise<void>
  addStiffPoint: (name: string, point: Point) => void
  keepStiffPoints: (name: string) => void
  discardStiffPoints: (name: string) => void
  removeLastStiffPoint: (name: string) => void
  clearStiffPoints: (name: string) => void
  // A moving part's settings (WP-29): hinge side, shown open in the model, how far it opens or slides. Saved at once.
  setMotion: (name: string, motion: Motion) => void
  // Undo and redo (WP-71) for the saved points, and the call that marks the panel as the editor in use.
  history: EditHistory
  activate: () => void
}

export function useDeclaredPoints(version: Version): DeclaredPointsState {
  const [parts, setPartsState] = useState<PlacedPart[] | null>(null)
  // The latest parts, updated at once on every change so two quick clicks both land (the state lags a render).
  const partsRef = useRef<PlacedPart[] | null>(null)
  const setParts = useCallback((next: PlacedPart[] | null) => {
    partsRef.current = next
    setPartsState(next)
  }, [])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { history, activate } = useEditHistory()

  useEffect(() => {
    let alive = true
    setParts(null)
    setError(null)
    history.clear()
    api
      .versionParts(version.id)
      .then((d) => alive && setParts(d.parts))
      .catch((e) => alive && setError(msg(e)))
    return () => {
      alive = false
    }
  }, [version.id, setParts, history])

  // Saves a whole list through the existing parts PUT; the server's answer is the list kept.
  const persist = useCallback((next: PlacedPart[]) => api.saveVersionParts(version.id, next).then((d) => d.parts), [version.id])

  // One saved change, as one step named for the person. The local copy changes at once; if the save fails, the copy
  // goes back to what was saved before and the error shows, and no step is made.
  const change = useCallback(
    async (label: string, next: PlacedPart[]) => {
      const before = partsRef.current
      setParts(next)
      setBusy(true)
      setError(null)
      try {
        await savePointsStep({ history, label, before: before ?? [], next, save: persist, show: setParts })
      } catch (e) {
        setParts(before)
        setError(msg(e))
      } finally {
        setBusy(false)
      }
    },
    [history, persist, setParts],
  )

  const setPoint = useCallback(
    (name: string, point: Point | null) => {
      const cur = partsRef.current
      if (!cur) return
      void change(point ? `place a point on the ${name}` : `remove the point on the ${name}`, withPoint(cur, name, point))
    },
    [change],
  )

  const [pending, setPending] = useState<Record<string, Point[]>>({})
  const addStiffPoint = useCallback((name: string, point: Point) => {
    const saved = partsRef.current?.find((p) => p.name === name)?.locations?.length ?? 0
    setPending((m) => {
      const have = m[name] ?? []
      if (saved + have.length >= MAX_STIFF_POINTS) return m
      return { ...m, [name]: [...have, [point[0], point[1], point[2]]] }
    })
  }, [])
  const discardStiffPoints = useCallback((name: string) => setPending((m) => ({ ...m, [name]: [] })), [])
  const keepStiffPoints = useCallback(
    (name: string) => {
      const cur = partsRef.current
      const add = pending[name] ?? []
      if (!cur || add.length === 0) return
      let next = cur
      for (const p of add) next = withStiffPoint(next, name, p)
      setPending((m) => ({ ...m, [name]: [] }))
      void change(add.length === 1 ? `place a point on the ${name}` : `place ${add.length} points on the ${name}`, next)
    },
    [pending, change],
  )
  const removeLastStiffPoint = useCallback(
    (name: string) => {
      if ((pending[name] ?? []).length > 0) {
        setPending((m) => ({ ...m, [name]: (m[name] ?? []).slice(0, -1) }))
        return
      }
      const cur = partsRef.current
      const n = cur?.find((p) => p.name === name)?.locations?.length ?? 0
      if (cur && n > 0) void change(`remove the last point on the ${name}`, withoutStiffPoint(cur, name, n - 1))
    },
    [pending, change],
  )
  const clearStiffPoints = useCallback(
    (name: string) => {
      const cur = partsRef.current
      setPending((m) => ({ ...m, [name]: [] }))
      if (cur) void change(`remove the region on the ${name}`, withoutStiffPoints(cur, name))
    },
    [change],
  )

  const setMotion = useCallback(
    (name: string, motion: Motion) => {
      const cur = partsRef.current
      if (cur) void change(`change how the ${name} moves`, withMotion(cur, name, motion))
    },
    [change],
  )

  const setPendingPoints = useCallback((name: string, points: Point[]) => setPending((m) => ({ ...m, [name]: points.map((p) => [p[0], p[1], p[2]] as Point) })), [])
  const reloadParts = useCallback(() => api.versionParts(version.id).then((d) => setParts(d.parts)).catch((e) => setError(msg(e))), [version.id, setParts])

  return { parts, sway: parts ? markable(parts) : [], busy, error, setPoint, pending, addStiffPoint, keepStiffPoints, discardStiffPoints, removeLastStiffPoint, clearStiffPoints, setMotion, history, activate, setPendingPoints, reloadParts }
}

export function DeclaredPointsPanel({ dp, done, markName, onMark }: { dp: DeclaredPointsState; done: boolean; markName: string | null; onMark: (name: string | null) => void }) {
  if (dp.sway.length === 0) return null
  const moving = dp.sway.some((p) => isMoving(p.kind))
  const bodyParts = dp.sway.some((p) => !isMoving(p.kind))
  const markKind = dp.sway.find((p) => p.name === markName)?.kind ?? null
  return (
    <div className="declared-points" data-testid="declared-points">
      <div className="row-between">
        <div className="field-label">{moving && !bodyParts ? 'Moving parts' : 'Hanging and stiff parts'}</div>
        <UndoRedoButtons history={dp.history} />
      </div>
      {bodyParts && (
        <div className="small muted">
          A hanging part (sway) gets a spring chain where it hangs: place one point, for example the top of a cape's hem. A stiff part
          (for example a backpack or a quiver on the back) moves with the body and does not bend: place one or more points on it, and
          the app grows the region from each point and shows it on the model. Right-drag turns the model. Press Escape to stop.
        </div>
      )}
      {moving && (
        <div className="small muted">
          Click once on each piece that moves: a lid or a door turns on a hinge, a wheel spins on its axle, a drawer slides out. One
          click per piece, so a cart with four wheels takes four clicks. The lid or drawer found from your click shows on the model
          before you keep it; a wheel is found from its round shape when the rig is made again. Right-drag turns the model. Press
          Escape to stop.
        </div>
      )}
      {!done && <div className="small muted">Points can be set once the rig has finished.</div>}
      {dp.sway.map((p) => {
        const multi = isMultiPoint(p.kind)
        const count = multi ? (p.locations ?? []).length : p.location ? 1 : 0
        const waiting = multi ? (dp.pending[p.name] ?? []).length : 0
        const savedText = isMoving(p.kind)
          ? `${KIND_WORDS[p.kind]}: ${count} piece${count === 1 ? '' : 's'} marked`
          : `${count} point${count === 1 ? '' : 's'} saved (stiff)`
        return (
          <div key={p.name} className="declared-point-block">
            <div className="declared-point-row">
              <span>{p.name}</span>
              <span className="small muted">
                {multi ? (waiting ? `${savedText}, ${waiting} to keep` : savedText) : count ? 'Point set' : 'No point yet'}
              </span>
              {multi && waiting > 0 && (
                <button type="button" className="btn" disabled={!done || dp.busy} onClick={() => dp.keepStiffPoints(p.name)}>
                  {isMoving(p.kind) ? 'Keep' : 'Keep region'}
                </button>
              )}
              {multi && waiting > 0 && (
                <button type="button" className="btn" disabled={dp.busy} onClick={() => dp.discardStiffPoints(p.name)}>
                  Discard
                </button>
              )}
              {markName === p.name ? (
                <button type="button" className="btn" onClick={() => onMark(null)}>
                  Stop
                </button>
              ) : (
                <button type="button" className="btn" disabled={!done || dp.busy} onClick={() => onMark(p.name)}>
                  {multi ? 'Add point' : 'Place point'}
                </button>
              )}
              {multi && (count > 0 || waiting > 0) && markName !== p.name && (
                <button type="button" className="btn" disabled={!done || dp.busy} onClick={() => dp.removeLastStiffPoint(p.name)}>
                  Remove last point
                </button>
              )}
              {multi && count > 0 && markName !== p.name && (
                <button type="button" className="btn" disabled={!done || dp.busy} onClick={() => dp.clearStiffPoints(p.name)}>
                  {isMoving(p.kind) ? 'Remove all points' : 'Remove region'}
                </button>
              )}
              {!multi && p.location && markName !== p.name && (
                <button type="button" className="btn" disabled={!done || dp.busy} onClick={() => dp.setPoint(p.name, null)}>
                  Remove point
                </button>
              )}
            </div>
            {isMoving(p.kind) && <MotionRow part={p} disabled={!done || dp.busy} onChange={(m) => dp.setMotion(p.name, m)} />}
          </div>
        )
      })}
      {markName && (
        <div className="small">
          {markKind && isMoving(markKind)
            ? `Click the model once on each ${markName} piece. Press Escape to stop.`
            : `Click the model on the ${markName}. The grown region shows on the model before you keep it. Press Escape to stop.`}
        </div>
      )}
      <div className="small muted">Saving does not redo the rig. Make the rig again to use the points.</div>
      {dp.error || dp.history.lastError ? <div className="warn-text">{msg(dp.error ?? dp.history.lastError)}</div> : null}
    </div>
  )
}

// A moving part's settings (WP-29), in plain words. A hinged part: which edge the hinge is on, whether the model shows it
// open, how far it opens. A sliding part: whether the model shows it out, how far it slides. A wheel needs none.
function MotionRow({ part, disabled, onChange }: { part: PlacedPart; disabled: boolean; onChange: (m: Motion) => void }) {
  if (part.kind === 'spin') {
    return <div className="declared-point-settings small muted">Spins about its own axle, one turn a second; the game sets the speed.</div>
  }
  const hinge = part.kind === 'hinge'
  const angle = part.angle ?? 100
  const distance = part.distance ?? 0.8
  return (
    <div className="declared-point-settings small" data-testid={`motion-${part.name}`}>
      {hinge && (
        <label>
          Hinge on{' '}
          <select value={part.side ?? 'auto'} disabled={disabled} onChange={(e) => onChange({ side: e.target.value as HingeSide })}>
            {HINGE_SIDES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        <input type="checkbox" checked={!!part.open_at_rest} disabled={disabled} onChange={(e) => onChange({ open_at_rest: e.target.checked })} />{' '}
        {hinge ? 'The model shows it open' : 'The model shows it pulled out'}
      </label>
      {hinge ? (
        <label>
          Opens by{' '}
          <input
            type="number"
            min={ANGLE_RANGE[0]}
            max={ANGLE_RANGE[1]}
            step={5}
            value={angle}
            disabled={disabled || !!part.open_at_rest}
            onChange={(e) => e.target.value !== '' && onChange({ angle: Number(e.target.value) })}
          />{' '}
          degrees{part.open_at_rest ? ' (it is shut by turning it until it meets the body)' : ''}
        </label>
      ) : (
        <label>
          Slides out by{' '}
          <input
            type="number"
            min={DISTANCE_RANGE[0] * 100}
            max={DISTANCE_RANGE[1] * 100}
            step={5}
            value={Math.round(distance * 100)}
            disabled={disabled}
            onChange={(e) => e.target.value !== '' && onChange({ distance: Number(e.target.value) / 100 })}
          />{' '}
          percent of its depth
        </label>
      )}
    </div>
  )
}

// The suggested worn item (WP-69 follow-up): the finder's proposal, shown as pending points on the model before anything
// is kept. Keep declares the part if the character does not have it yet, saves the points as a stiff part (undoable),
// and marks the suggestion kept. Dismiss drops the pending points and marks the suggestion dismissed (never offered again
// for this rig version). The pending region is drawn by the rig page's preview, like any other pending point.
export function SuggestionBlock({
  suggestion,
  running,
  done,
  busy,
  canAsk,
  askText,
  onAsk,
  onKeep,
  onDismiss,
}: {
  suggestion: ItemSuggestionState | null
  running: boolean
  done: boolean
  busy: boolean
  canAsk: boolean
  askText: string
  onAsk: () => void
  onKeep: (s: ItemSuggestionState) => void
  onDismiss: (s: ItemSuggestionState) => void
}) {
  if (running) {
    return <div className="small muted item-suggestion" data-testid="item-suggestion">Looking for the item named in the description, from six views. It runs in the job queue.</div>
  }
  const pending = suggestion && suggestion.state === 'done' && suggestion.found ? suggestion : null
  if (!pending) {
    if (!canAsk) return null
    return (
      <div className="declared-points item-suggestion" data-testid="item-suggestion">
        {suggestion?.state === 'failed' && <div className="warn-text small">The last suggestion did not finish: {suggestion.reason || 'try again'}</div>}
        <button type="button" className="btn" disabled={!done || busy} onClick={onAsk}>
          {askText}
        </button>
      </div>
    )
  }
  const note = suggestionNote(pending)
  return (
    <div className="declared-points item-suggestion" data-testid="item-suggestion">
      <div className="field-label">Suggested region</div>
      <div className="small">{suggestionLine(pending)}</div>
      {note && <div className="warn-text small">{note}</div>}
      <div className="declared-point-row">
        <button type="button" className="btn" disabled={!done || busy} onClick={() => onKeep(pending)}>
          Keep
        </button>
        <button type="button" className="btn" disabled={busy} onClick={() => onDismiss(pending)}>
          Dismiss
        </button>
      </div>
    </div>
  )
}
