// The hand-off to the person's own Unity project (metrics.unity_project_import on the export version).
// "sent": the project was open, so the files wait in it and Unity imports them when the person switches to it.
// "Check again", and the window regaining focus while it is "sent" (at most once every 10 seconds), recompute the
// export's metrics, which picks up the Editor's result.
import { useEffect, useRef, useState } from 'react'
import { api, type UnityHandoffState, type UnityProjectImport, type Version } from '../api'
import { Info } from '../components/Info'
import { TrafficDot } from '../components/ui'

const FOCUS_GAP_MS = 10_000
// Shared by every copy on the page (the finish card and the export view), so one focus asks once.
const lastAsked = new Map<string, number>()

export function unityImportOf(v: Version | undefined | null): (UnityProjectImport & { state: UnityHandoffState }) | null {
  const m = v?.metrics.unity_project_import
  if (!m || typeof m !== 'object') return null
  const u = m as UnityProjectImport
  // Older exports have only ok and error.
  const state: UnityHandoffState | null = u.state === 'sent' || u.state === 'imported' || u.state === 'failed' ? u.state : u.ok === true ? 'imported' : u.ok === false ? 'failed' : null
  return state ? { ...u, state } : null
}

const sentence = (s: string) => (/[.!?]$/.test(s.trim()) ? s.trim() : s.trim() + '.')

function noteFor(u: UnityProjectImport, assetName: string): string {
  if (u.note) return u.note
  const folder = u.folder?.match(/Assets[\\/]VGAC[\\/][^\\/]+[\\/]_incoming/)?.[0]?.replace(/\\/g, '/') ?? `Assets/VGAC/${assetName}/_incoming`
  return `Your Unity project was open, so the asset waits in ${folder} and imports itself the next time you switch to Unity (or use Tools > VGAC > Import pending assets).`
}

export function UnityHandoff({ version, assetName, onChanged, compact = false }: { version: Version; assetName: string; onChanged?: () => void; compact?: boolean }) {
  // The newest copy of the version this card has fetched, until the page hands over a newer one.
  const [fresh, setFresh] = useState<Version | null>(null)
  const current = fresh && fresh.id === version.id && JSON.stringify(fresh.metrics.unity_project_import) !== JSON.stringify(version.metrics.unity_project_import) ? fresh : version
  const u = unityImportOf(current)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const state = u?.state ?? null

  const check = async () => {
    lastAsked.set(version.id, Date.now())
    setBusy(true)
    setError(null)
    try {
      await api.judge(version.id, 'metrics')
      setFresh(await api.getVersion(version.id))
      onChanged?.()
    } catch {
      setError('Could not check right now. Try again in a moment.')
    } finally {
      setBusy(false)
    }
  }
  const checkRef = useRef(check)
  checkRef.current = check

  useEffect(() => {
    if (state !== 'sent') return
    const onFocus = () => {
      const last = lastAsked.get(version.id) ?? 0
      if (Date.now() - last < FOCUS_GAP_MS) return
      void checkRef.current()
    }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [state, version.id])

  if (!u) return null
  return (
    <div className={'unity-handoff' + (compact ? ' compact' : '')}>
      {u.state === 'sent' && (
        <>
          <div className="unity-handoff-line">
            <TrafficDot state="warn" title="waiting for Unity" />
            <span>
              Sent to your open Unity project. It finishes importing when you switch to Unity.
              <Info term="term.unity_handoff" />
            </span>
            <button type="button" className="small-btn" disabled={busy} onClick={() => void check()} title="Asks whether Unity has imported it yet">
              {busy ? 'Checking...' : 'Check again'}
            </button>
          </div>
          <div className="muted small unity-handoff-note">{noteFor(u, assetName)}</div>
        </>
      )}
      {u.state === 'imported' && (
        <div className="unity-handoff-line">
          <TrafficDot state="ok" title="imported" />
          <span>
            Imported into your Unity project{u.prefab ? <>: <span className="mono">{u.prefab}</span></> : ''}.
            <Info term="term.unity_handoff" />
          </span>
        </div>
      )}
      {u.state === 'failed' && (
        <div className="unity-handoff-line">
          <TrafficDot state="bad" title="failed" />
          <span>{u.error ? `Unity could not import it: ${sentence(u.error)}` : 'Unity could not import it.'}</span>
        </div>
      )}
      {error && <div className="feedback-error small">{error}</div>}
    </div>
  )
}
