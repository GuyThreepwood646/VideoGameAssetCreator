// Live event hub: WebSocket /api/ws/events with a polling fallback on /api/jobs every 3 s.
// The polling fallback carries job and version events only; autopilot state is
// re-read by the asset page from GET /api/assets/{aid}/autopilot while it runs.
import { useEffect, useState } from 'react'
import { api, eventsUrl, type Job, type ServerEvent } from './api'

export type EventMode = 'connecting' | 'ws' | 'poll'

type Listener = (ev: ServerEvent) => void
type ModeListener = (mode: EventMode) => void

const POLL_MS = 3000
const RETRY_MS = 10000

class EventHub {
  private listeners = new Set<Listener>()
  private modeListeners = new Set<ModeListener>()
  private ws: WebSocket | null = null
  private pollTimer: number | null = null
  private retryTimer: number | null = null
  private seen = new Map<string, string>()
  private started = false
  mode: EventMode = 'connecting'

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn)
    this.start()
    return () => {
      this.listeners.delete(fn)
    }
  }

  onMode(fn: ModeListener): () => void {
    this.modeListeners.add(fn)
    return () => {
      this.modeListeners.delete(fn)
    }
  }

  private setMode(mode: EventMode) {
    if (this.mode === mode) return
    this.mode = mode
    for (const fn of this.modeListeners) fn(mode)
  }

  private emit(ev: ServerEvent) {
    for (const fn of this.listeners) {
      try {
        fn(ev)
      } catch (e) {
        console.error('event listener failed', e)
      }
    }
  }

  start() {
    if (this.started) return
    this.started = true
    this.connect()
  }

  private connect() {
    if (this.retryTimer != null) {
      window.clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
    let ws: WebSocket
    try {
      ws = new WebSocket(eventsUrl())
    } catch {
      this.startPolling()
      return
    }
    this.ws = ws
    ws.onopen = () => {
      this.stopPolling()
      this.setMode('ws')
    }
    ws.onmessage = (m: MessageEvent) => {
      try {
        const ev = JSON.parse(String(m.data)) as ServerEvent
        if (ev && (ev.type === 'job' || ev.type === 'version' || ev.type === 'autopilot')) {
          if (ev.type === 'job') this.remember(ev.job)
          this.emit(ev)
        }
      } catch (e) {
        console.warn('bad event payload', e)
      }
    }
    ws.onerror = () => {
      // onclose follows
    }
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null
      this.startPolling()
      this.retryTimer = window.setTimeout(() => this.connect(), RETRY_MS)
    }
  }

  private remember(job: Job) {
    this.seen.set(job.id, `${job.status}|${job.progress}|${job.message}`)
  }

  private startPolling() {
    this.setMode('poll')
    if (this.pollTimer != null) return
    const tick = async () => {
      try {
        const jobs = await api.listJobs()
        for (const job of jobs) {
          const sig = `${job.status}|${job.progress}|${job.message}`
          const prev = this.seen.get(job.id)
          if (prev === sig) continue
          this.seen.set(job.id, sig)
          if (prev === undefined && (job.status === 'done' || job.status === 'error' || job.status === 'cancelled')) {
            // Historical job seen for the first time: nothing to announce.
            continue
          }
          this.emit({ type: 'job', job })
          if (job.status === 'done' && job.version_id) {
            try {
              const version = await api.getVersion(job.version_id)
              this.emit({ type: 'version', version })
            } catch {
              // version fetch failed; the job event is enough for a refetch
            }
          }
        }
      } catch {
        // backend unreachable; keep polling
      }
    }
    void tick()
    this.pollTimer = window.setInterval(() => void tick(), POLL_MS)
  }

  private stopPolling() {
    if (this.pollTimer != null) {
      window.clearInterval(this.pollTimer)
      this.pollTimer = null
    }
  }
}

export const events = new EventHub()

export function useEvents(listener: Listener, deps: unknown[] = []) {
  useEffect(() => events.subscribe(listener), deps) // eslint-disable-line react-hooks/exhaustive-deps
}

export function useEventMode(): EventMode {
  const [mode, setMode] = useState<EventMode>(events.mode)
  useEffect(() => {
    events.start()
    setMode(events.mode)
    return events.onMode(setMode)
  }, [])
  return mode
}
