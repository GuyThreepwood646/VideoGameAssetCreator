// VGAC's own ComfyUI (choices A and C of the ComfyUI panel): what will happen, where, how big and from where, before
// anything starts; then each step with its size and progress, Stop, Continue, and Remove. Reads /api/comfy/install
// (backend/vgac/comfy_install.py). The person's own ComfyUI is never changed: for choice A its models folder is only read.
import { useCallback, useEffect, useState } from 'react'
import { api, type ComfyCopy, type ComfyInstallOffer, type ComfyInstallStatus } from '../api'
import { fmtSize } from '../packsText'

const POLL_MS = 1000

export default function ComfyInstall({ shareFrom, onChanged }: { shareFrom: ComfyCopy | null; onChanged?: () => void }) {
  const [offer, setOffer] = useState<ComfyInstallOffer | null>(null)
  const [st, setSt] = useState<ComfyInstallStatus | null>(null)
  const [answer, setAnswer] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [alsoModels, setAlsoModels] = useState(false)

  const load = useCallback(async () => {
    try {
      const o = await api.comfyInstallOffer()
      setOffer(o)
      setSt(o.status)
    } catch (e) {
      setAnswer({ ok: false, text: e instanceof Error ? e.message : String(e) })
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const working = st?.state === 'working'
  useEffect(() => {
    if (!working) return
    const t = window.setInterval(async () => {
      try {
        const s = await api.comfyInstallStatus()
        setSt(s)
        if (s.state === 'done') {
          const r = await api.comfyInstallEngines()
          setAnswer({ ok: true, text: r.message ?? r.engines.map((e) => `Engine ${e.name}: ${e.text}`).join(' ') })
          onChanged?.()
          void load()
        }
      } catch {
        // the next tick tries again
      }
    }, POLL_MS)
    return () => window.clearInterval(t)
  }, [working, load, onChanged])

  const act = async (fn: () => Promise<void>) => {
    setBusy(true)
    setAnswer(null)
    try {
      await fn()
    } catch (e) {
      setAnswer({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }
  const begin = (choice: 'A' | 'C') => act(async () => setSt(await api.comfyInstallStart(choice, choice === 'A' ? shareFrom?.id : undefined)))
  const stop = () => act(async () => setSt(await api.comfyInstallStop()))
  const remove = () =>
    act(async () => {
      const r = await api.comfyInstallRemove(alsoModels)
      setAnswer({ ok: true, text: r.message })
      setRemoving(false)
      onChanged?.()
      await load()
    })

  if (!offer) return answer ? <div className="error small">{answer.text}</div> : <div className="muted small">Reading what VGAC's own ComfyUI needs...</div>
  const state = st?.state ?? 'idle'
  const done = state === 'done'
  const started = state !== 'idle'
  const fraction = st && st.bytes_total ? Math.min(1, st.bytes_done / st.bytes_total) : 0
  return (
    <div className="setup-field" id="comfy-install">
      <strong>VGAC's own ComfyUI {offer.version}</strong>
      {!started && (
        <div className="small">
          VGAC can install a ComfyUI of its own. It goes in {offer.goes_to}, never into a ComfyUI you already have. About {fmtSize(offer.download_bytes)} is
          downloaded from {offer.hosts.join(', ')}, and it needs about {fmtSize(offer.needs_bytes)} of free space. ComfyUI is under the {offer.licence} licence.
          No administrator rights are needed. It takes about 10 to 20 minutes.
        </div>
      )}
      {!offer.ready && (
        <div className="warn small">
          This copy of the app cannot install it yet: {offer.missing.join(' and ')} {offer.missing.length === 1 ? 'is' : 'are'} not in it. Nothing will be downloaded.
        </div>
      )}
      {st && started && (
        <ol className="small">
          {st.steps.map((s) => (
            <li key={s.id} className={s.done ? 'muted' : s.id === st.step && working ? '' : 'muted'}>
              {s.label}
              {s.download_bytes > 0 && !s.done ? ` (${fmtSize(s.download_bytes)} to download)` : ''}
              {s.done ? ': done' : s.id === st.step && working ? ': now' : ''}
            </li>
          ))}
        </ol>
      )}
      {working && (
        <>
          <div className="small">{st?.text}</div>
          {st && st.bytes_total > 0 && (
            <>
              <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(fraction * 100)}>
                <div className="progress-bar" style={{ width: `${Math.round(fraction * 100)}%` }} />
              </div>
              <div className="muted small">
                {fmtSize(st.bytes_done)} of {fmtSize(st.bytes_total)}
              </div>
            </>
          )}
          <button type="button" className="small-btn" disabled={busy} onClick={() => void stop()}>
            Stop
          </button>
        </>
      )}
      {st?.warnings.map((w) => (
        <div key={w} className="warn small">
          {w}
        </div>
      ))}
      {st?.error && (
        <div className="error small">
          {st.error.message} {st.error.fix}
        </div>
      )}
      {(state === 'stopped' || state === 'unfinished') && <div className="small">{st?.text || 'The install stopped before it finished. What was downloaded is kept.'}</div>}
      {done && st && st.engines.length > 0 && (
        <div className="setup-ok small">
          Installed. {st.engines.map((e) => `Engine ${e.name} on the ${e.gpu} (card ${e.card}), port ${e.port}`).join('; ')}.
        </div>
      )}
      {!working && !done && offer.ready && (
        <div>
          {started ? (
            <button type="button" className="small-btn" disabled={busy} onClick={() => void begin(st?.choice === 'A' && shareFrom ? 'A' : 'C')}>
              {state === 'failed' ? 'Try again' : 'Continue'}
            </button>
          ) : (
            <>
              {shareFrom && (
                <button type="button" className="small-btn" disabled={busy} onClick={() => void begin('A')}>
                  Install, sharing the models of {shareFrom.name}
                </button>
              )}
              <button type="button" className="small-btn" disabled={busy} onClick={() => void begin('C')}>
                {shareFrom ? 'Install, nothing shared' : 'Install VGAC\'s ComfyUI'}
              </button>
            </>
          )}
        </div>
      )}
      {shareFrom && !started && (
        <div className="muted small">Sharing reads the models in {shareFrom.name} where they are. Nothing in that ComfyUI is changed.</div>
      )}
      {started && !working && (
        <div>
          {!removing ? (
            <button type="button" className="link small" disabled={busy} onClick={() => setRemoving(true)}>
              Remove VGAC's ComfyUI
            </button>
          ) : (
            <div className="small">
              This deletes VGAC's own ComfyUI from {offer.goes_to}. Your own ComfyUI is not touched.{' '}
              <label>
                <input type="checkbox" checked={alsoModels} onChange={(e) => setAlsoModels(e.target.checked)} /> Also delete the models in it
              </label>{' '}
              <button type="button" className="small-btn" disabled={busy} onClick={() => void remove()}>
                Remove
              </button>{' '}
              <button type="button" className="link small" onClick={() => setRemoving(false)}>
                Keep it
              </button>
            </div>
          )}
        </div>
      )}
      {answer && <div className={answer.ok ? 'setup-ok small' : 'error small'}>{answer.text}</div>}
    </div>
  )
}
