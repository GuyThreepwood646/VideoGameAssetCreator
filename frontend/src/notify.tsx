// Long runs say so when they end (UI-31): an in-app toast, and a system notification when the window is in the
// background and the person has allowed them. A setting turns each off. Nothing here sends anything off this computer.
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { api, type AutopilotState } from './api'
import { useEvents } from './events'
import { plainJargon, stagePlain } from './help'
import type { StageId } from './api'

const KEY = 'vgac.notify'

export function notifyEnabled(): boolean {
  try {
    return localStorage.getItem(KEY) !== '0'
  } catch {
    return true
  }
}

export function setNotifyEnabled(on: boolean) {
  try {
    localStorage.setItem(KEY, on ? '1' : '0')
  } catch {
    // storage blocked; the setting simply is not remembered
  }
  if (on && typeof Notification !== 'undefined' && Notification.permission === 'default') void Notification.requestPermission()
}

type Toast = { id: number; text: string; to: string }

// What an autopilot change means to the person, or null when it is not an ending worth telling.
export function endingMessage(name: string, prev: string | undefined, ap: AutopilotState): string | null {
  if (prev !== 'running' || ap.status === 'running') return null
  const where = ap.stage ? stagePlain(ap.stage as StageId) : 'a step'
  if (ap.status === 'done') return `${name} is ready.`
  if (ap.status === 'paused' && ap.stopped_by_user) return null
  if (ap.status === 'paused') return `${name} needs you: ${plainJargon(ap.reason) || where}`
  return `${name} stopped at ${where}.`
}

export function Toasts() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const last = useRef(new Map<string, string>())
  const names = useRef(new Map<string, string>())
  const next = useRef(1)
  useEvents((ev) => {
    if (ev.type !== 'autopilot') return
    const prev = last.current.get(ev.asset_id)
    last.current.set(ev.asset_id, ev.autopilot.status)
    if (!notifyEnabled()) return
    void (async () => {
      let name = names.current.get(ev.asset_id)
      if (!name) {
        try {
          name = (await api.getAsset(ev.asset_id)).name
          names.current.set(ev.asset_id, name)
        } catch {
          name = 'Your asset'
        }
      }
      const text = endingMessage(name, prev, ev.autopilot)
      if (!text) return
      const id = next.current++
      setToasts((t) => [...t, { id, text, to: `/assets/${ev.asset_id}` }])
      if (typeof Notification !== 'undefined' && Notification.permission === 'granted' && document.hidden) new Notification('VGAC', { body: text })
    })()
  }, [])
  useEffect(() => {
    if (!toasts.length) return
    const t = window.setTimeout(() => setToasts((l) => l.slice(1)), 12000)
    return () => window.clearTimeout(t)
  }, [toasts])
  if (!toasts.length) return null
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className="toast">
          <Link to={t.to} onClick={() => setToasts((l) => l.filter((x) => x.id !== t.id))}>
            {t.text}
          </Link>
          <button type="button" className="link small" aria-label="Dismiss" onClick={() => setToasts((l) => l.filter((x) => x.id !== t.id))}>
            Dismiss
          </button>
        </div>
      ))}
    </div>
  )
}
