// Hanging parts (WP-20 step 5): the sway parts the person declared for this character, each with the point where it hangs.
// Place point, then click the model: the viewer's pointer tool hands the spot to the rig. Saving writes this version's
// parts.json; it does not redo the rig, so the points are read the next time the rig is made.
// Undo and redo (WP-71): every saved change of a point (placing, removing) is a step; each one puts the earlier or later
// list back through the same PUT. Points still pending (not kept) are not saved, so they are not steps.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type PlacedPart, type Version } from '../../api'
import { markable, MAX_STIFF_POINTS, withPoint, withStiffPoint, withoutStiffPoint, withoutStiffPoints, type Point } from '../../declaredPoints'
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

  const setPendingPoints = useCallback((name: string, points: Point[]) => setPending((m) => ({ ...m, [name]: points.map((p) => [p[0], p[1], p[2]] as Point) })), [])
  const reloadParts = useCallback(() => api.versionParts(version.id).then((d) => setParts(d.parts)).catch((e) => setError(msg(e))), [version.id, setParts])

  return { parts, sway: parts ? markable(parts) : [], busy, error, setPoint, pending, addStiffPoint, keepStiffPoints, discardStiffPoints, removeLastStiffPoint, clearStiffPoints, history, activate, setPendingPoints, reloadParts }
}

export function DeclaredPointsPanel({ dp, done, markName, onMark }: { dp: DeclaredPointsState; done: boolean; markName: string | null; onMark: (name: string | null) => void }) {
  if (dp.sway.length === 0) return null
  return (
    <div className="declared-points" data-testid="declared-points">
      <div className="row-between">
        <div className="field-label">Hanging and stiff parts</div>
        <UndoRedoButtons history={dp.history} />
      </div>
      <div className="small muted">
        A hanging part (sway) gets a spring chain where it hangs: place one point, for example the top of a cape's hem. A stiff part
        (for example a backpack or a quiver on the back) moves with the body and does not bend: place one or more points on it, and
        the app grows the region from each point and shows it on the model. Right-drag turns the model. Press Escape to stop.
      </div>
      {!done && <div className="small muted">Points can be set once the rig has finished.</div>}
      {dp.sway.map((p) => {
        const isStiff = p.kind === 'stiff'
        const count = isStiff ? (p.locations ?? []).length : p.location ? 1 : 0
        const waiting = isStiff ? (dp.pending[p.name] ?? []).length : 0
        const savedText = `${count} point${count === 1 ? '' : 's'} saved (stiff)`
        return (
          <div key={p.name} className="declared-point-row">
            <span>{p.name}</span>
            <span className="small muted">
              {isStiff ? (waiting ? `${savedText}, ${waiting} to keep` : savedText) : count ? 'Point set' : 'No point yet'}
            </span>
            {isStiff && waiting > 0 && (
              <button type="button" className="btn" disabled={!done || dp.busy} onClick={() => dp.keepStiffPoints(p.name)}>
                Keep region
              </button>
            )}
            {isStiff && waiting > 0 && (
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
                {isStiff ? 'Add point' : 'Place point'}
              </button>
            )}
            {isStiff && (count > 0 || waiting > 0) && markName !== p.name && (
              <button type="button" className="btn" disabled={!done || dp.busy} onClick={() => dp.removeLastStiffPoint(p.name)}>
                Remove last point
              </button>
            )}
            {isStiff && count > 0 && markName !== p.name && (
              <button type="button" className="btn" disabled={!done || dp.busy} onClick={() => dp.clearStiffPoints(p.name)}>
                Remove region
              </button>
            )}
            {!isStiff && p.location && markName !== p.name && (
              <button type="button" className="btn" disabled={!done || dp.busy} onClick={() => dp.setPoint(p.name, null)}>
                Remove point
              </button>
            )}
          </div>
        )
      })}
      {markName && <div className="small">Click the model on the {markName}. The grown region shows on the model before you keep it. Press Escape to stop.</div>}
      <div className="small muted">Saving does not redo the rig. Make the rig again to use the points.</div>
      {dp.error || dp.history.lastError ? <div className="warn-text">{msg(dp.error ?? dp.history.lastError)}</div> : null}
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
  onKeep,
  onDismiss,
}: {
  suggestion: ItemSuggestionState | null
  running: boolean
  done: boolean
  busy: boolean
  onKeep: (s: ItemSuggestionState) => void
  onDismiss: (s: ItemSuggestionState) => void
}) {
  if (running) return <div className="small muted">Looking for the item named in the description, from six views.</div>
  const pending = suggestion && suggestion.state === 'done' && suggestion.found ? suggestion : null
  if (!pending) return null
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
