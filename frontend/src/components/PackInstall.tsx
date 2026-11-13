// The guided panel for an optional pack the app can get for the person (the hair library; later the animation packs).
// Reads GET /api/packs/{kit} and starts, stops and feeds the background run (backend/vgac/packs.py). Plain words first.
import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react'
import { api, type PackKit, type PackSource } from '../api'
import { arrivalNote, canStart, dependencyNote, downloadBytes, enoughSpace, fmtSize, kitText, neededSources, progressWords } from '../packsText'
import { humanError } from '../help'

function DropZone({ source, busy, onFile }: { source: PackSource; busy: boolean; onFile: (s: PackSource, f: File) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    const f = e.dataTransfer.files?.[0]
    if (f && !busy) onFile(source, f)
  }
  return (
    <div
      className={'image-drop-zone' + (over ? ' over' : '')}
      role="group"
      aria-label={`Give the app the zip for ${source.name}`}
      onDragOver={(e) => {
        e.preventDefault()
        if (!busy) setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
    >
      <span className="muted small">Drop the zip file here, or</span>
      <button type="button" className="small-btn" disabled={busy} onClick={() => input.current?.click()}>
        Choose the zip
      </button>
      <input
        ref={input}
        type="file"
        accept=".zip,application/zip"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (f) onFile(source, f)
        }}
      />
    </div>
  )
}

export default function PackInstall({ kit: kitId, onChanged, onClose }: { kit: string; onChanged?: () => void; onClose?: () => void }) {
  const text = kitText(kitId)
  const [kit, setKit] = useState<PackKit | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [chosen, setChosen] = useState<string[] | null>(null)
  const [upload, setUpload] = useState<{ source: string; fraction: number } | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [confirmClean, setConfirmClean] = useState(false)
  const [freed, setFreed] = useState<number | null>(null)
  const last = useRef<string>('')

  const load = useCallback(async () => {
    try {
      const k = await api.pack(kitId)
      setKit(k)
      setError(null)
      setChosen((c) => c ?? defaultChoice(k))
      if (last.current && last.current !== k.run.state && (k.run.state === 'done' || k.run.state === 'failed')) onChanged?.()
      last.current = k.run.state
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [kitId, onChanged])

  useEffect(() => {
    void load()
  }, [load])
  const working = kit?.run.state === 'working'
  useEffect(() => {
    if (!working) return
    const id = window.setInterval(() => void load(), 1000)
    return () => window.clearInterval(id)
  }, [working, load])

  if (!kit) return <div className="pack-panel muted">{error ? `Could not reach the app: ${humanError(error)}` : 'Checking...'}</div>

  const run = kit.run
  const picked = chosen ?? []
  const need = neededSources(kit, picked)
  const toDownload = downloadBytes(kit, picked)
  const act = async (f: () => Promise<PackKit>) => {
    setError(null)
    setFileError(null)
    try {
      setKit(await f())
      void load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  const onFile = async (s: PackSource, f: File) => {
    setFileError(null)
    setUpload({ source: s.id, fraction: 0 })
    try {
      setKit(await api.packZip(kitId, s.id, f, (fraction) => setUpload({ source: s.id, fraction })))
      void load()
    } catch (e) {
      setFileError(e instanceof Error ? e.message : String(e))
    } finally {
      setUpload(null)
    }
  }
  const toggle = (id: string) => setChosen((c) => ((c ?? []).includes(id) ? (c ?? []).filter((x) => x !== id) : [...(c ?? []), id]))
  const fromHand = (s: PackSource) => !s.direct && s.state === 'missing'
  const dropFor = run.state === 'waiting' ? kit.sources.filter((s) => run.waiting_for.includes(s.id)) : run.state === 'failed' && run.error && ['link_gone', 'bad_download', 'no_link', 'server'].includes(run.error.code) ? need.filter((s) => s.state === 'missing') : []

  return (
    <div className="pack-panel" id={`get-${kitId}`}>
      <div className="row-between">
        <h3 className="pack-title">{text.title}</h3>
        {onClose && (
          <button type="button" className="link small" onClick={onClose}>
            Close
          </button>
        )}
      </div>
      {error && <div className="error small">{humanError(error)}</div>}

      {run.state === 'working' && (
        <div className="pack-block">
          <div>{progressWords(kit)}</div>
          <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(run.fraction * 100)}>
            <div className="progress-bar" style={{ width: `${Math.round(run.fraction * 100)}%` }} />
          </div>
          <div className="row-gap">
            <button type="button" className="small-btn" onClick={() => void act(() => api.packCancel(kitId))}>
              Stop
            </button>
            <span className="muted small">You can leave this page; it keeps going. A half-finished download carries on where it stopped.</span>
          </div>
        </div>
      )}

      {run.state === 'done' && (
        <div className="pack-block">
          <div className="setup-ok">
            Done. {kit.count ?? 0} {text.doneWord} are ready. {text.where}
          </div>
        </div>
      )}

      {(run.state === 'failed' || run.state === 'cancelled') && (
        <div className="pack-block">
          {run.error ? (
            <>
              <div className="error">{run.error.message}</div>
              {run.error.fix && <div className="setup-fix">What to do: {run.error.fix}</div>}
            </>
          ) : (
            <div className="muted">Stopped. Anything half-downloaded is kept and carries on next time.</div>
          )}
          <div className="row-gap">
            <button type="button" className="btn primary" onClick={() => void act(() => api.packStart(kitId, run.choices.length ? run.choices : picked))}>
              {run.state === 'cancelled' ? 'Carry on' : 'Try again'}
            </button>
          </div>
        </div>
      )}

      {dropFor.map((s) => (
        <div key={s.id} className="pack-block pack-hand">
          <strong>{run.state === 'waiting' ? `One file needs you: ${s.name}` : `Or give the app the file yourself: ${s.name}`}</strong>
          {!s.direct && (
            <div className="muted small">This pack is on itch.io. itch.io only hands the file over after a click, so the app cannot fetch it. {s.click_text}</div>
          )}
          <div className="row-gap">
            <a className="small-btn" href={s.page} target="_blank" rel="noreferrer">
              Open the download page
            </a>
            <span className="muted small">{fmtSize(s.size)}, free</span>
          </div>
          <DropZone source={s} busy={upload !== null} onFile={onFile} />
          {upload?.source === s.id && (
            <div>
              <div className="muted small">Sending the file to the app...</div>
              <div className="progress">
                <div className="progress-bar" style={{ width: `${Math.round(upload.fraction * 100)}%` }} />
              </div>
            </div>
          )}
          {fileError && <div className="error small">{fileError}</div>}
        </div>
      ))}

      {(run.state === 'idle' || run.state === 'done' || run.state === 'cancelled' || run.state === 'failed') && (
        <div className="pack-block">
          {kit.installed && kit.where === 'built' && (
            <div className="setup-ok">
              Installed: {kit.count} {text.doneWord} are ready on this computer.
            </div>
          )}
          {kit.installed && kit.where === 'project' && (
            <div className="muted small">
              {kit.count} {text.doneWord} are in use from the copy inside the app's own folder. You can also get your own copy here; it is used first.
            </div>
          )}
          {run.state !== 'done' && !(kit.installed && kit.choices.every((c) => c.built)) && (
            <>
              <div>{text.what}</div>
              <div className="muted small">{text.who}</div>
              <ul className="pack-facts muted small">
                <li>
                  To download: about {fmtSize(toDownload || kit.download_bytes)}. Space needed while it builds: {text.keeps}.
                </li>
                <li>Time: {text.time}</li>
                <li>Cost: nothing. Every pack is free and public domain (CC0). Nothing is sent about you.</li>
              </ul>
              <div className="field-label">Which packs do you want?</div>
              <ul className="pack-choices">
                {kit.choices.map((c) => (
                  <li key={c.id}>
                    <label>
                      <input type="checkbox" checked={picked.includes(c.id)} disabled={working} onChange={() => toggle(c.id)} /> <strong>{c.name}</strong>{' '}
                      <span className="muted small">
                        ({c.styles} {c.styles === 1 ? 'hairstyle' : 'hairstyles'}
                        {c.built ? ', already installed' : ''})
                      </span>
                    </label>
                    <div className="muted small pack-choice-what">
                      {c.what} {dependencyNote(c)}
                    </div>
                  </li>
                ))}
              </ul>
              {kitId === 'hair' && (
                <div className="muted small">The three rule-built hairstyles (top bun, long straight, high ponytail) are made by the app and added when the Quaternius pack is chosen.</div>
              )}
              {need.length > 0 && <div className="muted small">{arrivalNote(need)}</div>}
              {!enoughSpace(kit, picked) && (
                <div className="error small">
                  There is not enough free space for this choice ({fmtSize(kit.free_bytes)} free). Free some space or choose fewer packs.
                </div>
              )}
              <div className="row-gap">
                <button
                  type="button"
                  className="btn primary"
                  disabled={!canStart(kit, picked) || !enoughSpace(kit, picked)}
                  onClick={() => void act(() => api.packStart(kitId, picked))}
                >
                  {kit.installed ? 'Get the packs chosen above' : 'Download and build'}
                </button>
                {need.some(fromHand) && <span className="muted small">It will ask you for the itch.io file when it gets there.</span>}
              </div>
            </>
          )}
        </div>
      )}

      {run.state === 'waiting' && (
        <div className="row-gap">
          <button type="button" className="small-btn" onClick={() => void act(() => api.packCancel(kitId))}>
            Stop
          </button>
        </div>
      )}

      {kit.cleanable_bytes > 0 && run.state !== 'working' && run.state !== 'waiting' && (
        <div className="pack-block">
          {!confirmClean ? (
            <button type="button" className="link small" onClick={() => setConfirmClean(true)}>
              Remove the downloaded packs (frees {fmtSize(kit.cleanable_bytes)})
            </button>
          ) : (
            <div className="row-gap">
              <span className="small">
                Remove the downloaded packs? That frees {fmtSize(kit.cleanable_bytes)}. What is already installed stays; to add packs later they are downloaded again.
              </span>
              <button
                type="button"
                className="small-btn"
                onClick={() =>
                  void act(async () => {
                    const k = await api.packClean(kitId)
                    setFreed(k.freed_bytes ?? 0)
                    setConfirmClean(false)
                    return k
                  })
                }
              >
                Yes, remove them
              </button>
              <button type="button" className="link small" onClick={() => setConfirmClean(false)}>
                No
              </button>
            </div>
          )}
        </div>
      )}
      {freed !== null && <div className="muted small">Removed. {fmtSize(freed)} freed.</div>}
    </div>
  )
}

// First choice: every pack not yet installed (all of them on a first visit).
function defaultChoice(k: PackKit): string[] {
  const open = k.choices.filter((c) => !c.built).map((c) => c.id)
  return open.length ? open : k.choices.map((c) => c.id)
}
