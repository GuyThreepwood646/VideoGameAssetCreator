// Hanging parts (WP-20 step 5): the sway parts the person declared for this character, each with the point where it hangs.
// Place point, then click the model: the viewer's pointer tool hands the spot to the rig. Saving writes this version's
// parts.json; it does not redo the rig, so the points are read the next time the rig is made.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type PlacedPart, type Version } from '../../api'
import { markable, MAX_STIFF_POINTS, withPoint, withStiffPoint, withoutStiffPoint, withoutStiffPoints, type Point } from '../../declaredPoints'

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
  addStiffPoint: (name: string, point: Point) => void
  keepStiffPoints: (name: string) => void
  discardStiffPoints: (name: string) => void
  removeLastStiffPoint: (name: string) => void
  clearStiffPoints: (name: string) => void
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

  useEffect(() => {
    let alive = true
    setParts(null)
    setError(null)
    api
      .versionParts(version.id)
      .then((d) => alive && setParts(d.parts))
      .catch((e) => alive && setError(msg(e)))
    return () => {
      alive = false
    }
  }, [version.id, setParts])

  const setPoint = useCallback(
    (name: string, point: Point | null) => {
      const cur = partsRef.current
      if (cur) save(withPoint(cur, name, point))
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version.id],
  )

  // Saves the whole list. The local copy changes at once; the server's answer replaces it when it comes.
  const save = useCallback(
    (next: PlacedPart[]) => {
      setParts(next)
      setBusy(true)
      setError(null)
      api
        .saveVersionParts(version.id, next)
        .then((d) => setParts(d.parts))
        .catch((e) => setError(msg(e)))
        .finally(() => setBusy(false))
    },
    [version.id, setParts],
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
      save(next)
    },
    [pending, save],
  )
  const removeLastStiffPoint = useCallback(
    (name: string) => {
      if ((pending[name] ?? []).length > 0) {
        setPending((m) => ({ ...m, [name]: (m[name] ?? []).slice(0, -1) }))
        return
      }
      const cur = partsRef.current
      const n = cur?.find((p) => p.name === name)?.locations?.length ?? 0
      if (cur && n > 0) save(withoutStiffPoint(cur, name, n - 1))
    },
    [pending, save],
  )
  const clearStiffPoints = useCallback(
    (name: string) => {
      const cur = partsRef.current
      setPending((m) => ({ ...m, [name]: [] }))
      if (cur) save(withoutStiffPoints(cur, name))
    },
    [save],
  )

  return { parts, sway: parts ? markable(parts) : [], busy, error, setPoint, pending, addStiffPoint, keepStiffPoints, discardStiffPoints, removeLastStiffPoint, clearStiffPoints }
}

export function DeclaredPointsPanel({ dp, done, markName, onMark }: { dp: DeclaredPointsState; done: boolean; markName: string | null; onMark: (name: string | null) => void }) {
  if (dp.sway.length === 0) return null
  return (
    <div className="declared-points" data-testid="declared-points">
      <div className="field-label">Hanging and stiff parts</div>
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
      {dp.error && <div className="warn-text">{dp.error}</div>}
    </div>
  )
}
