// The Resting pose preview on the rig step (WP-76): a Preview button under the Resting pose settings. It makes the hands
// of this rig in the chosen hand shape and finger curl (the values on screen, sent as they are) and shows the picture.
// The rig itself is not touched; Apply and re-run uses the same values. Polls the state about twice a second while the
// job runs, and stops when it is done or failed.
import { useEffect, useRef, useState } from 'react'
import { api, type PosePreviewState } from '../api'

const POLL_MS = 1500

export function RestingPosePreview({ versionId, settings }: { versionId: string; settings: Record<string, unknown> }) {
  const [state, setState] = useState<PosePreviewState>({ state: 'none' })
  const [stamp, setStamp] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef<number | null>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    api
      .getPosePreview(versionId)
      .then((a) => alive.current && setState(a.state))
      .catch(() => undefined)
    return () => {
      alive.current = false
      if (timer.current !== null) window.clearTimeout(timer.current)
    }
  }, [versionId])

  // Poll while a preview is queued or running.
  useEffect(() => {
    if (state.state !== 'queued' && state.state !== 'running') return
    timer.current = window.setTimeout(() => {
      api
        .getPosePreview(versionId)
        .then((a) => {
          if (!alive.current) return
          setState(a.state)
          if (a.state.state === 'done') setStamp(Date.now())
        })
        .catch(() => undefined)
    }, POLL_MS)
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
    }
  }, [state.state, versionId])

  const preview = async () => {
    setBusy(true)
    setError(null)
    try {
      const a = await api.startPosePreview(versionId, settings)
      setState(a.state)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The preview could not start.')
    } finally {
      setBusy(false)
    }
  }

  const live = state.state === 'queued' || state.state === 'running'
  const shown = state.state === 'done' && state.picture
  const matches = state.settings && Object.keys(settings).every((k) => String(state.settings?.[k]) === String(settings[k]))

  return (
    <div className="resting-preview">
      <div className="row-gap">
        <button type="button" onClick={preview} disabled={busy || live}>
          {live ? 'Making the preview...' : 'Preview the hands'}
        </button>
        <span className="muted small">
          {live
            ? 'The hands are being made in the chosen resting pose. This takes about a minute.'
            : shown && matches
              ? 'Shown below: the hands in the resting pose set above.'
              : shown
                ? 'Shown below is an older preview. Press Preview the hands to see these settings.'
                : 'Shows the hands in the resting pose set above, before a rig run. Nothing is rigged again.'}
        </span>
      </div>
      {state.state === 'failed' && <div className="error small">The preview did not work: {state.reason ?? 'unknown reason'}.</div>}
      {error && <div className="error small">{error}</div>}
      {shown && (
        <img
          className="resting-preview-picture"
          alt="The hands in the chosen resting pose, one row per move: stand still and attack, left and right hand at the start and middle of each move"
          src={`/api/versions/${versionId}/pose-preview/picture?v=${stamp || 1}`}
        />
      )}
    </div>
  )
}
