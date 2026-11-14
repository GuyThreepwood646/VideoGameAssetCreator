// The guided panel for an outside tool: look, choose, link, and (Blender only) get it. Reads GET /api/tools/blender or
// /api/tools/unity (backend/vgac/tools_setup.py). Unity cannot be installed for the person, so its panel explains and
// links instead. The file chooser comes from the VGAC window's bridge; in a browser a typed path does the same job.
import { useCallback, useEffect, useState } from 'react'
import { api, type BlenderTool, type ToolCopy, type UnityTool } from '../api'
import { hostPick } from '../desktop'
import { humanError } from '../help'

type Tool = 'blender' | 'unity'
const NAMES: Record<Tool, { name: string; file: string; filter: string }> = {
  blender: { name: 'Blender', file: 'blender.exe', filter: 'Blender|blender.exe' },
  unity: { name: 'Unity', file: 'Unity.exe', filter: 'Unity|Unity.exe' },
}

function Copies({ found, onUse, busy }: { found: ToolCopy[]; onUse: (c: ToolCopy) => void; busy: boolean }) {
  return (
    <ul className="setup-rows">
      {found.map((c) => (
        <li key={c.path} className="setup-row">
          <span className={'dot ' + (c.usable ? 'ok' : 'bad')} />
          <span className="setup-row-label">
            {c.version || 'unknown version'}
            {c.best ? ' (best match)' : ''}
          </span>
          <span className="muted small setup-row-detail">
            {c.path} - {c.source}. {c.reason}
          </span>
          {c.usable && (
            <button type="button" className="small-btn" disabled={busy} onClick={() => onUse(c)}>
              Use this
            </button>
          )}
        </li>
      ))}
    </ul>
  )
}

function Chooser({ tool, busy, onPath }: { tool: Tool; busy: boolean; onPath: (p: string) => void }) {
  const [typed, setTyped] = useState('')
  const host = hostPick()
  const n = NAMES[tool]
  return (
    <div className="setup-field">
      {host ? (
        <button
          type="button"
          className="small-btn"
          disabled={busy}
          onClick={() => void host.pickFile(`Choose ${n.file}`, n.filter).then((p) => p && onPath(p))}
        >
          Show me where {n.file} is
        </button>
      ) : (
        <>
          <label className="field-label">Where is {n.file}?</label>
          <div className="row-gap">
            <input type="text" className="setup-path" value={typed} placeholder={`The full path of ${n.file}`} onChange={(e) => setTyped(e.target.value)} />
            <button type="button" className="small-btn" disabled={busy || !typed.trim()} onClick={() => onPath(typed.trim())}>
              Use this
            </button>
          </div>
        </>
      )}
    </div>
  )
}

export default function ToolPanel({ tool, onChanged, onClose }: { tool: Tool; onChanged?: () => void; onClose?: () => void }) {
  const n = NAMES[tool]
  const [data, setData] = useState<BlenderTool | UnityTool | null>(null)
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState<{ ok: boolean; text: string } | null>(null)
  const [wantGet, setWantGet] = useState(false)

  const load = useCallback(async () => {
    try {
      setData(tool === 'blender' ? await api.blenderTool() : await api.unityTool())
    } catch (e) {
      setAnswer({ ok: false, text: humanError(e instanceof Error ? e.message : String(e)) })
    }
  }, [tool])

  useEffect(() => {
    void load()
  }, [load])

  // While Blender is being fetched, ask again every second.
  const working = tool === 'blender' && (data as BlenderTool | null)?.install?.state === 'working'
  useEffect(() => {
    if (!working) return
    const t = window.setInterval(() => void load(), 1000)
    return () => window.clearInterval(t)
  }, [working, load])
  const installState = tool === 'blender' ? (data as BlenderTool | null)?.install?.state : undefined
  useEffect(() => {
    if (installState === 'done') onChanged?.()
  }, [installState, onChanged])

  const act = async (fn: () => Promise<{ message?: string } | unknown>) => {
    setBusy(true)
    setAnswer(null)
    try {
      const r = (await fn()) as { message?: string } | undefined
      setAnswer({ ok: true, text: r?.message ?? 'Done.' })
      onChanged?.()
      await load()
    } catch (e) {
      setAnswer({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }
  const use = (path: string) => void act(() => (tool === 'blender' ? api.useBlender(path) : api.useUnity(path)))

  const found = data?.found ?? []
  const usable = found.filter((c) => c.usable)
  const blender = tool === 'blender' ? (data as BlenderTool | null) : null
  const unity = tool === 'unity' ? (data as UnityTool | null) : null
  const inst = blender?.install ?? null

  return (
    <div className="card setup-card" id={'tool-' + tool}>
      <h3 className="card-title">
        {n.name}
        {onClose && (
          <button type="button" className="link small" onClick={onClose}>
            Close
          </button>
        )}
      </h3>
      {!data && !answer && <div className="muted">Looking for {n.name}...</div>}
      {data?.current && <div className="muted small">Set to: {data.current}</div>}
      {data && found.length > 0 && (
        <>
          <div>
            {found.length === 1 ? `Found ${n.name} on this computer.` : `Found ${found.length} copies of ${n.name} on this computer.`}
            {usable.length === 0 ? ' None of them will do.' : ''}
          </div>
          <Copies found={found} onUse={(c) => use(c.path)} busy={busy} />
        </>
      )}
      {data && found.length === 0 && <div>{n.name} was not found on this computer.</div>}
      {blender?.changed && (
        <div className={blender.changed.usable ? 'warn small' : 'error small'}>
          {blender.changed.message}{' '}
          {blender.changed.usable && (
            <button type="button" className="link small" disabled={busy} onClick={() => void act(() => api.blenderSeen())}>
              Fine, keep using it
            </button>
          )}
        </div>
      )}
      {blender && usable.length === 0 && (
        <div className="small">
          Blender is free on Steam and in the Microsoft Store. These open the official pages in your browser; nothing is downloaded by the app.{' '}
          {blender.offer.steam_page && (
            <a className="link small" href={blender.offer.steam_page} target="_blank" rel="noreferrer">
              Blender on Steam
            </a>
          )}{' '}
          {blender.offer.store_page && (
            <a className="link small" href={blender.offer.store_page} target="_blank" rel="noreferrer">
              Blender in the Microsoft Store
            </a>
          )}{' '}
          <button type="button" className="small-btn" disabled={busy} onClick={() => void load()}>
            Look again
          </button>
        </div>
      )}
      {data && (
        <div className="setup-field">
          <div className="field-label">{found.length ? 'Use a different one' : `Do you already have ${n.name}?`}</div>
          <Chooser tool={tool} busy={busy} onPath={use} />
        </div>
      )}
      {blender && usable.length === 0 && (
        <div className="setup-field">
          {!wantGet && inst?.state !== 'working' && (
            <button type="button" className="small-btn" disabled={!blender.offer.available} onClick={() => setWantGet(true)}>
              No, get it for me
            </button>
          )}
          {wantGet && inst?.state !== 'working' && (
            <div>
              <div>
                Blender {blender.offer.version} from {blender.offer.from}, about {blender.offer.size_mb} MB, {blender.offer.licence} licence. It goes in{' '}
                {blender.offer.goes_to}. It does not need administrator rights.
              </div>
              {blender.offer.unconfirmed && <div className="muted small">{blender.offer.unconfirmed}</div>}
              <div className="row-gap">
                <button type="button" className="small-btn" disabled={busy} onClick={() => void act(() => api.installBlender())}>
                  Download and set up
                </button>
                <a className="link small" href={blender.offer.page} target="_blank" rel="noreferrer">
                  Open blender.org and choose the zip yourself
                </a>
              </div>
              {hostPick() && (
                <button
                  type="button"
                  className="link small"
                  disabled={busy}
                  onClick={() => void hostPick()!.pickFile('Choose the Blender zip you downloaded', 'Zip files|*.zip').then((p) => p && void act(() => api.useBlenderZip(p)))}
                >
                  I downloaded the zip
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {inst && inst.state !== 'idle' && (
        <div className="setup-field">
          {inst.state === 'working' && (
            <>
              <div>{inst.text}...</div>
              <progress value={inst.fraction} max={1} />
              <button type="button" className="small-btn" onClick={() => void api.stopBlender().then(load)}>
                Stop
              </button>
            </>
          )}
          {inst.state === 'done' && <div className="setup-ok">Blender is installed and ready.</div>}
          {inst.state === 'stopped' && <div className="muted">{inst.text}</div>}
          {inst.state === 'failed' && inst.error && (
            <div className="error">
              {inst.error.message} {inst.error.fix}
            </div>
          )}
        </div>
      )}
      {unity && usable.length === 0 && (
        <div className="setup-field">
          <div>{unity.guide.cannot_install}</div>
          <div className="muted small">{unity.guide.licence_line}</div>
          <div className="muted small">Without it you lose: {unity.guide.loses_without}</div>
          <ol className="small">
            {unity.guide.steps.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ol>
          <div className="row-gap">
            <a className="link small" href={unity.guide.download_page} target="_blank" rel="noreferrer">
              Open unity.com
            </a>
            <a className="link small" href={unity.guide.terms_page} target="_blank" rel="noreferrer">
              Unity Personal terms
            </a>
            <button type="button" className="small-btn" onClick={() => void load()}>
              Check again
            </button>
            {onClose && (
              <button type="button" className="link small" onClick={onClose}>
                Skip for now
              </button>
            )}
          </div>
        </div>
      )}
      {answer && <div className={answer.ok ? 'setup-ok small' : 'error small'}>{answer.text}</div>}
    </div>
  )
}
