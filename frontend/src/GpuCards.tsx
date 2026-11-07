// "Graphics cards" card on the Health page: one row per card with its memory, what it is doing, whether its graphics
// engine (ComfyUI) is running, an On/Off switch and an Advanced disclosure (what the card may do, free memory after
// each step, shut down when idle, Start and Stop). Live through the "gpus" WebSocket event, a re-read after job
// events, and a slow poll.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, GPU_KINDS, type Gpu, type GpuCurrentJob, type GpuEngineState, type GpuKind, type GpuPatch, type GpuSharingPatch } from './api'
import { Info, Term } from './components/Info'
import { useEvents } from './events'
import { humanError } from './help'

const KIND_LABEL: Record<GpuKind, string> = { pictures: 'Pictures', model: '3D model', rigging: 'Rigging', review: 'Review' }
const IDLE_CHOICES: { minutes: number; label: string }[] = [
  { minutes: 0, label: 'Never' },
  { minutes: 5, label: '5 minutes' },
  { minutes: 15, label: '15 minutes' },
  { minutes: 60, label: '1 hour' },
]

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

// The engine state, with a fallback for a backend that does not send it yet.
export function engineState(g: Gpu): GpuEngineState {
  if (!g.enabled) return 'off'
  return g.state ?? (g.reachable ? 'running' : 'stopped')
}

// The card's graphics engine in plain words: "Running", "Starting (about a minute)", and so on.
export function engineText(g: Gpu): string {
  switch (engineState(g)) {
    case 'off':
      return 'Off'
    case 'running':
      return 'Running'
    case 'starting':
      return 'Starting (about a minute)'
    case 'failed':
      return `Could not start: ${g.last_error || 'no reason was given'}`
    default:
      return g.can_start ? 'Not running. Starts by itself when a step needs it.' : 'Not running, and the app cannot start it.'
  }
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

  async function run(instance: string, call: () => Promise<Gpu[]>) {
    setBusy(instance)
    setSaveError(null)
    try {
      setGpus(await call())
    } catch (e) {
      setSaveError(humanError(e instanceof Error ? e.message : String(e)))
    } finally {
      setBusy(null)
    }
  }
  const change = (instance: string, patch: GpuPatch) => run(instance, () => api.setGpu(instance, patch))
  const changeSharing = (patch: GpuSharingPatch) => run('sharing', () => api.setGpuSharing(patch))
  const defaultCard = gpus?.find((g) => g.is_default)?.instance ?? ''
  const sharing = !!gpus?.some((g) => g.share_when_busy)

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
        <GpuRow
          key={g.instance}
          gpu={g}
          busy={busy === g.instance}
          onChange={(patch) => void change(g.instance, patch)}
          onStart={() => void run(g.instance, () => api.startGpu(g.instance))}
          onStop={() => void run(g.instance, () => api.stopGpu(g.instance))}
        />
      ))}
      {gpus && gpus.length > 1 && (
        <div className="gpu-sharing">
          <label className="gpu-kind">
            <span>
              <Term term="gpu_default">Default card</Term>
            </span>
            <select
              value={defaultCard}
              disabled={busy === 'sharing'}
              onChange={(e) => void changeSharing({ default_instance: e.target.value || null })}
            >
              <option value="">Each step's usual card</option>
              {gpus.map((g) => (
                <option key={g.instance} value={g.instance}>
                  {g.gpu || `Card ${g.instance}`}
                </option>
              ))}
            </select>
          </label>
          <div className="muted small">
            {defaultCard
              ? `New work starts on the ${gpus.find((g) => g.instance === defaultCard)?.gpu ?? 'default card'} for every step it can do. Rigging stays on the card that can rig.`
              : 'Pictures and rigging start on one card and 3D models on the other, as set up.'}
          </div>
          <label className="gpu-kind">
            <input
              type="checkbox"
              checked={sharing}
              disabled={busy === 'sharing'}
              onChange={(e) => void changeSharing({ share_when_busy: e.target.checked })}
            />
            <span>
              <Term term="gpu_share">Use the other card when one is busy</Term>
            </span>
          </label>
          <div className="muted small">
            {sharing
              ? 'When a card is busy and the other has nothing to do, the waiting step moves over. The first step of a new kind on a card takes a minute or two longer while it loads.'
              : 'Each step waits for its card, even when the other card has nothing to do.'}
          </div>
        </div>
      )}
      {saveError && <div className="error">{saveError}</div>}
    </div>
  )
}

type RowProps = { gpu: Gpu; busy: boolean; onChange: (patch: GpuPatch) => void; onStart: () => void; onStop: () => void }

function GpuRow({ gpu: g, busy, onChange, onStart, onStop }: RowProps) {
  const used = g.vram_total_mb != null && g.vram_free_mb != null ? g.vram_total_mb - g.vram_free_mb : null
  const name = g.gpu || `Card ${g.instance}`
  const status = gpuStatusText(g)
  const switchId = `gpu-switch-${g.instance}`
  const engine = engineState(g)
  const idleId = `gpu-idle-${g.instance}`
  const idle = g.stop_when_idle_minutes ?? 0
  const idleChoices = IDLE_CHOICES.some((c) => c.minutes === idle) ? IDLE_CHOICES : [...IDLE_CHOICES, { minutes: idle, label: `${idle} minutes` }]
  const canStartNow = !!g.can_start && g.enabled && (engine === 'stopped' || engine === 'failed')
  const canStopNow = !!g.owned && (engine === 'running' || engine === 'starting') && !g.current_job
  return (
    <div className={'gpu-row' + (g.enabled ? '' : ' gpu-off')}>
      <div className="gpu-main">
        <div className="gpu-name">
          <span className={'dot ' + (!g.enabled ? 'off' : g.reachable ? 'ok' : engine === 'failed' || !g.can_start ? 'bad' : 'off')} />
          {name}
        </div>
        <div className="gpu-status">{status}</div>
        <div className={engine === 'failed' ? 'warn small' : 'muted small'}>
          Engine: {engineText(g)}
          <Info term="gpu_engine" />
          {engine === 'failed' && g.enabled && g.can_start && (
            <>
              {' '}
              <button type="button" className="small-btn" disabled={busy} onClick={onStart}>
                Try again
              </button>
            </>
          )}
        </div>
        {g.reachable && (
          <div className="muted small">
            Memory: {gbFromMb(used)} of {gbFromMb(g.vram_total_mb)} GB used
            <Info term="gpu_memory" />
          </div>
        )}
        {g.setup_problem && (
          <div className="warn small">
            Setup problem: {g.setup_problem}
            <Info term="gpu_setup_problem" />
          </div>
        )}
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
        <div className="gpu-kind">
          <label htmlFor={idleId}>Shut down when idle</label>{' '}
          <select
            id={idleId}
            value={idle}
            disabled={busy}
            onChange={(e) => onChange({ stop_when_idle_minutes: Number(e.target.value) })}
          >
            {idleChoices.map((c) => (
              <option key={c.minutes} value={c.minutes}>
                {c.label}
              </option>
            ))}
          </select>
          <Info term="gpu_idle_stop" />
          {idle > 0 && g.reachable && !g.owned && (
            <span className="muted small">The app did not start this engine, so it leaves it running.</span>
          )}
        </div>
        <div className="gpu-kind muted small">
          Address: {g.url}
          {g.found_by_card ? ' (found by its card)' : ''}
          <Info term="gpu_address" />
        </div>
        {(canStartNow || canStopNow) && (
          <div className="gpu-kind">
            {canStartNow && (
              <button type="button" className="small-btn" disabled={busy} onClick={onStart}>
                Start the engine now
              </button>
            )}
            {canStopNow && (
              <button type="button" className="small-btn" disabled={busy} onClick={onStop}>
                Shut down the engine
              </button>
            )}
            <Info term="gpu_engine" />
          </div>
        )}
      </details>
    </div>
  )
}
