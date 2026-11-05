import { useEffect, useState } from 'react'
import { api, type Health } from './api'
import { Link } from 'react-router'
import GpuCards from './GpuCards'
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
  if (error) {
    return (
      <span className="pill pill-bad" title={'Backend unreachable: ' + error}>
        backend down
      </span>
    )
  }
  if (!health) return <span className="pill">health...</span>
  const off = offNow ?? Object.values(health.instances).filter((i) => i.enabled === false).length
  return (
    <span className="health-badge" title={`Backend v${health.version}`}>
      {off > 0 && (
        <Link to="/health" className="pill pill-warn" title="Turned off on the Health page; the other card carries on with the work">
          {off} card{off === 1 ? '' : 's'} off
        </Link>
      )}
      {Object.entries(health.instances).map(([name, inst]) => (
        <span key={name} className="health-inst" title={`${inst.gpu} / ${inst.role} / ${inst.url}${inst.enabled === false ? ' / turned off in the app' : ''}${inst.error ? ' / ' + inst.error : ''}`}>
          <span className={'dot ' + (inst.enabled === false ? 'off' : inst.reachable ? 'ok' : 'bad')} />
          {name}
          {inst.reachable && inst.devices?.[0]?.vram_free != null ? <span className="muted"> {gb(inst.devices[0].vram_free)} GB</span> : null}
        </span>
      ))}
      <span className={'health-inst' + (health.host.ram_free_gb < health.host.min_free_ram_gb ? ' warn' : '')} title="Host RAM free">
        RAM {health.host.ram_free_gb} GB
      </span>
    </span>
  )
}

export default function HealthPanel() {
  const { health, error, updated } = useHealth()
  return (
    <div className="panel">
      {!error && <GpuCards />}
      <h2>Health</h2>
      {error && <div className="error">Backend unreachable: {error}</div>}
      {health &&
        Object.entries(health.instances).map(([name, inst]) => (
          <div className="instance" key={name}>
            <div className="row">
              <span className="name">
                <span className={'dot ' + (inst.reachable ? 'ok' : 'bad')} />
                Instance {name}
              </span>
              <span className="meta">
                {inst.reachable ? 'reachable' : 'unreachable'}
                {inst.enabled === false ? ', off in the app' : ''}
              </span>
            </div>
            <div className="meta">
              {inst.gpu} / {inst.role} / {inst.url}
              {inst.comfyui_version ? ` / ComfyUI ${inst.comfyui_version}` : ''}
            </div>
            {inst.reachable ? (
              (inst.devices ?? []).map((d, i) => (
                <div className="device" key={i}>
                  {d.name ?? d.type ?? 'device'}: VRAM {gb(d.vram_free)} / {gb(d.vram_total)} GB free
                </div>
              ))
            ) : (
              <div className="device">{inst.error ?? 'no response'}</div>
            )}
            {health.queue[name] && (
              <div className="device">
                queue: {health.queue[name].paused ? 'paused' : 'active'}
                {health.queue[name].current_job_id != null && `, running job ${health.queue[name].current_job_id}`}
              </div>
            )}
          </div>
        ))}
      {health && (
        <div className="host">
          <div className={health.host.ram_free_gb < health.host.min_free_ram_gb ? 'warn' : ''}>
            Host RAM free: {health.host.ram_free_gb} / {gb(health.host.ram_total_bytes)} GB (dispatch floor {health.host.min_free_ram_gb} GB)
          </div>
          <div>CPU: {health.host.cpu_percent}%</div>
          <div>
            Backend v{health.version}
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
      <h1 className="page-title">Backend health</h1>
      <HealthPanel />
    </div>
  )
}
