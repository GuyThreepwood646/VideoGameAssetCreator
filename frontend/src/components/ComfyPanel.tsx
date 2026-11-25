// The guided panel for the person's own ComfyUI: find it, check it, use it as the engine (choice B), and add the node
// packs VGAC needs after showing which of their Python packages would change. Reads GET /api/comfy
// (backend/vgac/comfy_find.py). Nothing in their ComfyUI changes until they press Go ahead.
import { useCallback, useEffect, useState } from 'react'
import { api, type ComfyCheck, type ComfyCopy, type ComfyFind, type ComfyPacksReport } from '../api'
import ComfyInstall from './ComfyInstall'

const KIND: Record<string, string> = { desktop: 'ComfyUI Desktop', portable: 'Portable', manual: 'Manual install', running: 'Running now' }

export default function ComfyPanel({ onChanged, onClose }: { onChanged?: () => void; onClose?: () => void }) {
  const [data, setData] = useState<ComfyFind | null>(null)
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState<{ ok: boolean; text: string } | null>(null)
  const [checked, setChecked] = useState<Record<string, ComfyCheck>>({})
  const [report, setReport] = useState<{ id: string; r: ComfyPacksReport } | null>(null)
  const [shareId, setShareId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await api.comfyFind())
    } catch (e) {
      setAnswer({ ok: false, text: e instanceof Error ? e.message : String(e) })
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const run = async (fn: () => Promise<void>) => {
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

  const check = (c: ComfyCopy) => run(async () => setChecked({ ...checked, [c.id]: await api.comfyCheck(c.id) }))
  const use = (c: ComfyCopy) =>
    run(async () => {
      const r = await api.comfyLink(c.id)
      setAnswer({ ok: true, text: r.message })
      onChanged?.()
      await load()
    })
  const look = (c: ComfyCopy) => run(async () => setReport({ id: c.id, r: await api.comfyPacksReport(c.id) }))
  const go = (id: string) =>
    run(async () => {
      const r = await api.comfyPacksApply(id)
      setAnswer({ ok: true, text: r.added.length ? `Added: ${r.added.join(', ')}.` : 'Nothing needed adding.' })
      setReport(null)
      onChanged?.()
    })

  const found = data?.found ?? []
  return (
    <div className="card setup-card" id="tool-comfy">
      <h3 className="card-title">
        Your ComfyUI
        {onClose && (
          <button type="button" className="link small" onClick={onClose}>
            Close
          </button>
        )}
      </h3>
      {!data && !answer && <div className="muted">Looking for ComfyUI...</div>}
      {data && found.length === 0 && <div>No ComfyUI was found on this computer. VGAC can install its own (below).</div>}
      {data && data.answering.length > 0 && (
        <div className="muted small">
          Answering now: {data.answering.map((a) => `port ${a.port}${a.devices.length ? ' on ' + a.devices.join(', ') : ''}`).join('; ')}. Two engines on one graphics card run out of memory, so
          give VGAC a card your ComfyUI is not using.
        </div>
      )}
      {found.length > 0 && (
        <ul className="setup-rows">
          {found.map((c) => {
            const k = checked[c.id]
            return (
              <li key={c.id} className="setup-row">
                <span className={'dot ' + (k ? (k.ok ? 'ok' : 'bad') : 'off')} />
                <span className="setup-row-label">
                  {c.name} {c.version && `(${c.version})`}
                </span>
                <span className="muted small setup-row-detail">
                  {KIND[c.kind] ?? c.kind} - {c.folder}
                  {c.running ? ' - running now' : ''}
                  {!c.has_trellis ? ' - too old for the 3D nodes' : ''}
                  {k && !k.ok ? ' - ' + k.problems.join(' ') : ''}
                </span>
                <button type="button" className="small-btn" disabled={busy} onClick={() => void check(c)}>
                  Check
                </button>
                <button type="button" className="small-btn" disabled={busy || (k != null && !k.ok)} onClick={() => void use(c)}>
                  Use this one
                </button>
                <button type="button" className="link small" disabled={busy} onClick={() => void look(c)}>
                  Node packs
                </button>
                <button type="button" className="link small" disabled={busy} onClick={() => setShareId(shareId === c.id ? null : c.id)}>
                  {shareId === c.id ? 'Do not share its models' : 'Share its models (A)'}
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {data && (
        <details>
          <summary>What are the choices?</summary>
          {data.choices.map((c) => (
            <div key={c.id} className="small">
              <strong>
                {c.id}. {c.name}
                {c.recommended ? ' (recommended)' : ''}
              </strong>{' '}
              {c.text}
            </div>
          ))}
        </details>
      )}
      {found.length > 0 && (
        <div className="muted small">Use this one is choice B: VGAC starts your ComfyUI itself. Share its models (A) lets VGAC's own ComfyUI read that ComfyUI's models where they are.</div>
      )}
      {report && (
        <div className="setup-field">
          {report.r.packs.map((p) => (
            <div key={p.id} className="small">
              {p.name}: {p.present ? (p.outdated ? 'out of date, will be updated' : 'already there') : 'missing'}
            </div>
          ))}
          {report.r.python.note && <div className="muted small">{report.r.python.note}</div>}
          {report.r.python.changes.length > 0 && (
            <div className="warn small">Would change in your Python: {report.r.python.changes.map((x) => `${x.name} ${x.from} to ${x.to}`).join(', ')}.</div>
          )}
          {report.r.python.adds.length > 0 && <div className="small">Would add: {report.r.python.adds.map((x) => x.name).join(', ')}.</div>}
          {report.r.packs.some((p) => !p.present || p.outdated) && (
            <button type="button" className="small-btn" disabled={busy} onClick={() => void go(report.id)}>
              Go ahead
            </button>
          )}
        </div>
      )}
      {answer && <div className={answer.ok ? 'setup-ok small' : 'error small'}>{answer.text}</div>}
      {data && <ComfyInstall shareFrom={found.find((c) => c.id === shareId) ?? null} onChanged={onChanged} />}
    </div>
  )
}
