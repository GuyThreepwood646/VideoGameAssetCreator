import { useEffect, useState } from 'react'
import { api, type Health } from './api'
import { Link } from 'react-router'
import GpuCards from './GpuCards'
import SetupCheck from './SetupCheck'
import { useEvents } from './events'

const GB = 1024 ** 3

function gb(bytes: number | null | undefined): string {
  if (bytes == null) return '?'
  return (bytes / GB).toFixed(1)
}

export function useHealth(intervalMs = 5000) {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [updated, setUpdated] = useState<Date | null>(null)

  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const data = await api.health()
        if (!cancelled) {
          setHealth(data)
          setError(null)
          setUpdated(new Date())
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }
    void poll()
    const id = window.setInterval(poll, intervalMs)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [intervalMs])

  return { health, error, updated }
}

// Compact indicator for the top bar: one dot per ComfyUI instance plus host RAM.
// A full-width banner when the engine does not answer (UI-32). The desktop launcher restarts it from its tray menu.
export function EngineBanner() {
  const { error } = useHealth(10000)
  if (!error) return null
  return (
    <div className="engine-banner" role="alert">
      <strong>VGAC's engine is not running.</strong> Your work is safe. If you opened VGAC from the desktop launcher, use its tray menu to restart the engine, then reload this page.
      <Link to="/health">What happened?</Link>
    </div>
  )
}

export function HealthBadge() {
  const { health, error } = useHealth(10000)
  // Cards turned off in the app: from the health poll, overridden at once by a "gpus" event until the next poll.
  const [offEvent, setOffEvent] = useState<{ count: number; health: Health | null } | null>(null)
  useEvents(
    (ev) => {
      if (ev.type === 'gpus') setOffEvent({ count: ev.gpus.filter((g) => !g.enabled).length, health })
    },
    [health],
  )
  const offNow = offEvent && offEvent.health === health ? offEvent.count : null
  // Assets whose Autopilot is waiting for the person (UI-19): the chip says Needs you and links to the first one.
  const [needs, setNeeds] = useState<Record<string, boolean>>({})
  useEvents((ev) => {
    if (ev.type === 'autopilot') setNeeds((n) => ({ ...n, [ev.asset_id]: (ev.autopilot.status === 'paused' && !ev.autopilot.stopped_by_user) || ev.autopilot.status === 'error' }))
  }, [])
  const needsId = Object.keys(needs).find((k) => needs[k])
  if (error) {
    return (
      <Link to="/health" className="chip chip-bad" title={'The engine did not answer: ' + error}>
        Engine not responding
      </Link>
    )
  }
  if (!health) return <span className="chip">Checking...</span>
  const off = offNow ?? Object.values(health.instances).filter((i) => i.enabled === false).length
  const lowRam = health.host.ram_free_gb < health.host.min_free_ram_gb
  const anyReady = Object.values(health.instances).some((i) => i.enabled !== false)
  // One chip (UI-19). An engine that starts when a step needs it is normal, so it is never red.
  const working = Object.values(health.queue ?? {}).some((q) => q.current_job_id != null)
  const label = needsId ? 'Needs you' : !anyReady ? 'Every card is off' : lowRam ? 'Low memory' : working ? 'Working' : 'Ready'
  const tone = needsId || !anyReady || lowRam ? 'chip-warn' : working ? 'chip-accent' : 'chip-ok'
  return (
    <details className="status-chip health-badge">
      <summary className={'chip ' + tone} title="Click for details about your graphics cards and memory">
        {label}
      </summary>
      <div className="status-pop">
        {needsId && (
          <Link to={`/assets/${needsId}`} className="small">
            An asset is waiting for you. Open it.
          </Link>
        )}
        <div className="muted small">VGAC engine v{health.version}</div>
        {off > 0 && (
          <Link to="/health" className="small">
            {off} card{off === 1 ? '' : 's'} turned off. Open System to change.
          </Link>
        )}
        {Object.entries(health.instances).map(([name, inst]) => {
          const state = inst.enabled === false ? 'Turned off' : inst.reachable ? 'Ready' : 'Starts when needed'
          return (
            <div key={name} className="health-inst" title={`${inst.gpu} / ${inst.role} / ${inst.url}${inst.error ? ' / ' + inst.error : ''}`}>
              <span className={'dot ' + (inst.enabled === false || !inst.reachable ? 'off' : 'ok')} />
              {inst.gpu || name}: {state}
              {inst.reachable && inst.devices?.[0]?.vram_free != null ? <span className="muted"> ({gb(inst.devices[0].vram_free)} GB free)</span> : null}
            </div>
          )
        })}
        <div className={'health-inst' + (lowRam ? ' warn' : '')}>Computer memory: {health.host.ram_free_gb} GB free</div>
      </div>
    </details>
  )
}

export default function HealthPanel() {
  const { health, error, updated } = useHealth()
  return (
    <div className="panel">
      {!error && <SetupCheck />}
      {!error && <GpuCards />}
      <h2>Checks</h2>
      {error && <div className="error">The engine did not answer: {error}</div>}
      {health && <div className="instances-grid">{
        Object.entries(health.instances).map(([name, inst]) => (
          <div className="instance" key={name}>
            <div className="row">
              <span className="name">
                <span className={'dot ' + (inst.enabled === false || !inst.reachable ? 'off' : 'ok')} />
                {inst.gpu || `Card ${name}`}
              </span>
              <span className="meta">
                {inst.enabled === false ? 'Turned off' : inst.reachable ? 'Ready' : 'Starts when needed'}
              </span>
            </div>
            <div className="meta">
              {inst.gpu} / {inst.role} / {inst.url}
              {inst.comfyui_version ? ` / ComfyUI ${inst.comfyui_version}` : ''}
            </div>
            {inst.reachable ? (
              (inst.devices ?? []).map((d, i) => (
                <div className="device" key={i}>
                  {d.name ?? d.type ?? 'device'}: {d.vram_total != null && d.vram_free != null ? gb(d.vram_total - d.vram_free) : "?"} of {gb(d.vram_total)} GB used
                </div>
              ))
            ) : (
              <div className="device muted">{inst.enabled === false ? 'Turned off in the app.' : 'Not running right now. It starts by itself when a step needs it.'}</div>
            )}
            {health.queue[name] && (
              <div className="device">
                Work queue: {health.queue[name].paused ? 'paused' : 'taking work'}
                {health.queue[name].current_job_id != null && ', busy with a job'}
              </div>
            )}
          </div>
        ))}</div>}
      {health && (
        <div className="host">
          <div className={health.host.ram_free_gb < health.host.min_free_ram_gb ? 'warn' : ''}>
            Computer memory free: {health.host.ram_free_gb} of {gb(health.host.ram_total_bytes)} GB (work waits if it drops below {health.host.min_free_ram_gb} GB)
          </div>
          <div>CPU: {health.host.cpu_percent}%</div>
          <div>
            VGAC engine v{health.version}
            {updated && `, updated ${updated.toLocaleTimeString()}`}
          </div>
        </div>
      )}
      {!health && !error && <div className="host">Loading...</div>}
    </div>
  )
}

export function HealthPage() {
  return (
    <div className="page-narrow">
      <h1 className="page-title">System</h1>
      <HealthPanel />
    </div>
  )
}
