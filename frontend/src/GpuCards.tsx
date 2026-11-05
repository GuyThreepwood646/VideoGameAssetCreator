// "Graphics cards" card on the Health page: one row per card with its memory, what it is doing, an On/Off switch
// and an Advanced disclosure (what the card may do, free memory after each step). Live through the "gpus"
// WebSocket event, a re-read after job events, and a slow poll.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, GPU_KINDS, type Gpu, type GpuCurrentJob, type GpuKind, type GpuPatch } from './api'
import { Info, Term } from './components/Info'
import { useEvents } from './events'
import { humanError } from './help'

const KIND_LABEL: Record<GpuKind, string> = { pictures: 'Pictures', model: '3D model', rigging: 'Rigging', review: 'Review' }

function gbFromMb(mb: number | null | undefined): string {
  return mb == null ? '?' : (mb / 1024).toFixed(1)
}

function jobs(n: number): string {
  return `${n} job${n === 1 ? '' : 's'} waiting`
}

// "Making a picture for Knight", from the running job's kind and asset name.
export function doingText(job: GpuCurrentJob): string {
  const name = job.asset_name ?? 'an asset'
  if (job.op === 'judge') return `Reviewing ${name}`
  if (job.material) return `Making the material ${name}`
  switch (job.stage) {
    case 'concept':
      return `Making a picture for ${name}`
    case 'turnaround':
      return `Drawing all sides of ${name}`
    case 'mesh':
      return `Building the 3D model of ${name}`
    case 'rig':
      return `Rigging ${name}`
    default:
      return `Working on ${name}`
  }
}

export function gpuStatusText(g: Gpu): string {
  if (!g.enabled) {
    if (g.current_job) return 'Off, handing its work over'
    return g.queued > 0 ? `Off, ${jobs(g.queued)}` : 'Off'
  }
  if (g.current_job) return doingText(g.current_job) + (g.queued > 0 ? `, ${jobs(g.queued)}` : '')
  return g.queued > 0 ? jobs(g.queued) : 'Idle'
}

export function useGpus(intervalMs = 5000) {
  const [gpus, setGpus] = useState<Gpu[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  const load = useCallback(async () => {
    try {
      setGpus(await api.gpus())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void load()
    const id = window.setInterval(() => void load(), intervalMs)
    return () => {
      window.clearInterval(id)
      if (timer.current != null) window.clearTimeout(timer.current)
    }
  }, [load, intervalMs])

  useEvents(
    (ev) => {
      if (ev.type === 'gpus') setGpus(ev.gpus)
      else if (ev.type === 'job') {
        // A job started, finished or moved: re-read soon (once for a burst of events).
        if (timer.current != null) window.clearTimeout(timer.current)
        timer.current = window.setTimeout(() => void load(), 400)
      }
    },
    [load],
  )

  return { gpus, setGpus, error, reload: load }
}

export default function GpuCards() {
  const { gpus, setGpus, error } = useGpus()
  const [busy, setBusy] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)

  async function change(instance: string, patch: GpuPatch) {
    setBusy(instance)
    setSaveError(null)
    try {
      setGpus(await api.setGpu(instance, patch))
    } catch (e) {
      setSaveError(humanError(e instanceof Error ? e.message : String(e)))
    } finally {
      setBusy(null)
    }
  }

  const offCount = gpus?.filter((g) => !g.enabled).length ?? 0
  return (
    <div className="card gpu-card">
      <h2 className="card-title">
        <Term term="gpus">Graphics cards</Term>
      </h2>
      {error && !gpus && <div className="error">Could not read the graphics cards: {error}</div>}
      {!gpus && !error && <div className="muted">Loading...</div>}
      {gpus && gpus.length > 1 && offCount === gpus.length && (
        <div className="warn small">Every card is off, so new pictures and 3D models wait until you turn one back on.</div>
      )}
      {gpus?.map((g) => (
        <GpuRow key={g.instance} gpu={g} busy={busy === g.instance} onChange={(patch) => void change(g.instance, patch)} />
      ))}
      {saveError && <div className="error">{saveError}</div>}
    </div>
  )
}

function GpuRow({ gpu: g, busy, onChange }: { gpu: Gpu; busy: boolean; onChange: (patch: GpuPatch) => void }) {
  const used = g.vram_total_mb != null && g.vram_free_mb != null ? g.vram_total_mb - g.vram_free_mb : null
  const name = g.gpu || `Card ${g.instance}`
  const status = gpuStatusText(g)
  const switchId = `gpu-switch-${g.instance}`
  return (
    <div className={'gpu-row' + (g.enabled ? '' : ' gpu-off')}>
      <div className="gpu-main">
        <div className="gpu-name">
          <span className={'dot ' + (!g.enabled ? 'off' : g.reachable ? 'ok' : 'bad')} />
          {name}
        </div>
        <div className="gpu-status">{status}</div>
        <div className="muted small">
          {g.reachable ? (
            <>
              Memory: {gbFromMb(used)} of {gbFromMb(g.vram_total_mb)} GB used
              <Info term="gpu_memory" />
            </>
          ) : g.enabled ? (
            'Not answering right now'
          ) : (
            'Not answering (off)'
          )}
        </div>
        <div className="muted small">{g.note}</div>
      </div>
      <div className="gpu-switch">
        <button
          id={switchId}
          type="button"
          role="switch"
          aria-checked={g.enabled}
          aria-label={`${name}: ${g.enabled ? 'on' : 'off'}`}
          className={'switch' + (g.enabled ? ' switch-on' : '')}
          disabled={busy}
          onClick={() => onChange({ enabled: !g.enabled })}
        >
          <span className="switch-knob" />
          <span className="switch-label">{g.enabled ? 'On' : 'Off'}</span>
        </button>
        <Info term="gpu_switch" />
      </div>
      <details className="gpu-advanced">
        <summary>
          Advanced <Info term="gpu_advanced" />
        </summary>
        <fieldset className="gpu-kinds" disabled={busy}>
          <legend>
            <Term term="gpu_kinds">What this card may do</Term>
          </legend>
          {GPU_KINDS.map((k) => {
            const possible = g.possible_kinds.includes(k)
            const checked = possible && g.allowed_kinds.includes(k)
            return (
              <div key={k} className={'gpu-kind' + (possible ? '' : ' gpu-kind-na')}>
                <label>
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={!possible}
                    onChange={(e) => {
                      const next = new Set(g.allowed_kinds)
                      if (e.target.checked) next.add(k)
                      else next.delete(k)
                      onChange({ allowed_kinds: GPU_KINDS.filter((x) => next.has(x)) })
                    }}
                  />
                  {KIND_LABEL[k]}
                </label>
                <Info term={`gpu_kind_${k}`} />
                {!possible && g.kind_notes[k] && <span className="muted small">{g.kind_notes[k]}</span>}
              </div>
            )
          })}
        </fieldset>
        <div className="gpu-kind">
          <label>
            <input type="checkbox" checked={g.free_after_job} disabled={busy} onChange={(e) => onChange({ free_after_job: e.target.checked })} />
            Free memory after each step
          </label>
          <Info term="gpu_free_after" />
        </div>
      </details>
    </div>
  )
}
