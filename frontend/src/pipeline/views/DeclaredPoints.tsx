// Hanging parts (WP-20 step 5): the sway parts the person declared for this character, each with the point where it hangs.
// Place point, then click the model: the viewer's pointer tool hands the spot to the rig. Saving writes this version's
// parts.json; it does not redo the rig, so the points are read the next time the rig is made.
import { useCallback, useEffect, useState } from 'react'
import { api, type PlacedPart, type Version } from '../../api'
import { markable, withPoint, type Point } from '../../declaredPoints'

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
}

export function useDeclaredPoints(version: Version): DeclaredPointsState {
  const [parts, setParts] = useState<PlacedPart[] | null>(null)
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
  }, [version.id])

  const setPoint = useCallback(
    (name: string, point: Point | null) => {
      if (!parts || busy) return
      setBusy(true)
      setError(null)
      api
        .saveVersionParts(version.id, withPoint(parts, name, point))
        .then((d) => setParts(d.parts))
        .catch((e) => setError(msg(e)))
        .finally(() => setBusy(false))
    },
    [parts, busy, version.id],
  )

  return { parts, sway: parts ? markable(parts) : [], busy, error, setPoint }
}

export function DeclaredPointsPanel({ dp, done, markName, onMark }: { dp: DeclaredPointsState; done: boolean; markName: string | null; onMark: (name: string | null) => void }) {
  if (dp.sway.length === 0) return null
  return (
    <div className="declared-points" data-testid="declared-points">
      <div className="field-label">Hanging parts</div>
      <div className="small muted">
        Where a part hangs, the rig adds a spring chain to it when it is made. Press Place point on a part, then click the spot on the model where it hangs
        (for a cape, the top of the hem). Right-drag turns the model. Press Escape to stop.
      </div>
      {!done && <div className="small muted">Points can be set once the rig has finished.</div>}
      {dp.sway.map((p) => (
        <div key={p.name} className="declared-point-row">
          <span>{p.name}</span>
          <span className="small muted">{p.location ? 'Point set' : 'No point yet'}</span>
          {markName === p.name ? (
            <button type="button" className="btn" onClick={() => onMark(null)}>
              Stop
            </button>
          ) : (
            <button type="button" className="btn" disabled={!done || dp.busy} onClick={() => onMark(p.name)}>
              Place point
            </button>
          )}
          {p.location && markName !== p.name && (
            <button type="button" className="btn" disabled={!done || dp.busy} onClick={() => dp.setPoint(p.name, null)}>
              Remove point
            </button>
          )}
        </div>
      ))}
      {markName && <div className="small">Click the model where the {markName} hangs. Press Escape to stop.</div>}
      <div className="small muted">Saving does not redo the rig. Make the rig again to use the points.</div>
      {dp.error && <div className="warn-text">{dp.error}</div>}
    </div>
  )
}
