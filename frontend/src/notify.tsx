// Long runs say so when they end (UI-31): an in-app toast, and a system notification when the window is in the
// background and the person has allowed them. A setting turns each off. Nothing here sends anything off this computer.
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { api, type AutopilotState } from './api'
import { useEvents } from './events'
import { plainJargon, stagePlain } from './help'
import { badgeIcon, desktopBridge, workCount } from './desktop'
import { useHealth } from './HealthPanel'
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

type Toast = { id: number; text: string; to?: string }

// Short messages from anywhere in the app (say(), e.g. "Opened ... in File Explorer"), shown with the Autopilot toasts.
const sayListeners = new Set<(text: string) => void>()
export function say(text: string) {
  sayListeners.forEach((fn) => fn(text))
}

// "Open folder" everywhere (owner, 2026-11-12: the folder opened behind the app, so nothing seemed to happen). The
// backend brings the Explorer window to the front (vgac/folders.py) and says whether Windows let it; either way a
// short message says where the folder is.
export async function openFolderAndSay(open: () => Promise<{ folder: string; front?: boolean | null }>): Promise<void> {
  try {
    const r = await open()
    const name = r.folder.split(/[\\/]/).filter(Boolean).pop() ?? r.folder
    say(r.front === false ? `Opened "${name}" in File Explorer. Windows kept it behind this window: click its flashing button on the taskbar.` : `Opened "${name}" in File Explorer.`)
  } catch {
    say('The folder could not be opened. Is the app still running?')
  }
}

// What an autopilot change means to the person, or null when it is not an ending worth telling.
export function endingMessage(name: string, prev: string | undefined, ap: AutopilotState): string | null {
  if (prev !== 'running' || ap.status === 'running') return null
  const where = ap.stage ? stagePlain(ap.stage as StageId) : 'a step'
  if (ap.status === 'done') return `${name} is ready.`
  if (ap.status === 'paused' && ap.stopped_by_user) return null
  if (ap.status === 'paused') return `${name} needs you: ${plainJargon(ap.reason) || where}`
  return `${name} stopped at ${where}.`
}

// The taskbar badge while work runs (desktop app only): the number of running and waiting jobs, cleared at 0.
function WorkBadge() {
  const { health } = useHealth(5000)
  const n = workCount(health?.queue)
  useEffect(() => {
    const desk = desktopBridge()
    if (!desk) return
    void desk.setWork(n, n ? badgeIcon(n) : '')
  }, [n])
  return null
}

export function Toasts() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const last = useRef(new Map<string, string>())
  const names = useRef(new Map<string, string>())
  const next = useRef(1)
  useEffect(() => {
    const onSay = (text: string) => {
      const id = next.current++
      setToasts((t) => [...t, { id, text }])
    }
    sayListeners.add(onSay)
    return () => {
      sayListeners.delete(onSay)
    }
  }, [])
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
      // Inside the desktop app the launcher shows the notification (the app window blocks web notifications).
      const desk = desktopBridge()
      if (desk) void desk.notify('VGAC', text)
      else if (typeof Notification !== 'undefined' && Notification.permission === 'granted' && document.hidden) new Notification('VGAC', { body: text })
    })()
  }, [])
  useEffect(() => {
    if (!toasts.length) return
    const t = window.setTimeout(() => setToasts((l) => l.slice(1)), 12000)
    return () => window.clearTimeout(t)
  }, [toasts])
  if (!toasts.length) return <WorkBadge />
  return (
    <>
    <WorkBadge />
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className="toast">
          {t.to ? (
            <Link to={t.to} onClick={() => setToasts((l) => l.filter((x) => x.id !== t.id))}>
              {t.text}
            </Link>
          ) : (
            <span>{t.text}</span>
          )}
          <button type="button" className="link small" aria-label="Dismiss" onClick={() => setToasts((l) => l.filter((x) => x.id !== t.id))}>
            Dismiss
          </button>
        </div>
      ))}
    </div>
    </>
  )
}
