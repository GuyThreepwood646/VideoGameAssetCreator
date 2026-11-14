// The desktop launcher's bridge (window.vgacDesktop, desktop/preload-app.js). It exists only inside the Electron app
// window; in a normal browser every function here does nothing and desktopBridge() is null.
export type DesktopBridge = {
  setWork: (count: number, icon: string) => Promise<boolean>
  notify: (title: string, body: string) => Promise<boolean>
  startEngine: () => Promise<boolean>
}

export function desktopBridge(): DesktopBridge | null {
  const b = (window as unknown as { vgacDesktop?: DesktopBridge }).vgacDesktop
  return b && typeof b.setWork === 'function' ? b : null
}

// The count badge for the taskbar: a red disc with the number, drawn here so the launcher only has to show a PNG.
export function badgeIcon(count: number): string {
  try {
    const c = document.createElement('canvas')
    c.width = c.height = 32
    const g = c.getContext('2d')
    if (!g) return ''
    g.fillStyle = '#c0392b'
    g.beginPath()
    g.arc(16, 16, 16, 0, Math.PI * 2)
    g.fill()
    g.fillStyle = '#ffffff'
    g.font = 'bold 20px sans-serif'
    g.textAlign = 'center'
    g.textBaseline = 'middle'
    g.fillText(count > 9 ? '9+' : String(count), 16, 17)
    return c.toDataURL('image/png')
  } catch {
    return ''
  }
}

// How many jobs are running or waiting, from the health poll's queue figures.
export function workCount(queue: Record<string, { current_job_id: string | null; queued?: number }> | undefined | null): number {
  if (!queue) return 0
  return Object.values(queue).reduce((n, q) => n + (q.current_job_id ? 1 : 0) + (q.queued || 0), 0)
}

// The starter window's bridge (window.vgacHost, starter/src/WebWindow.cs). It exists only inside the VGAC window; in a
// normal browser hostPick is null and the panels fall back to a typed path.
type HostBridge = { pickFolder: (title: string) => Promise<string | null>; pickFile: (title: string, filter?: string) => Promise<string | null> }

export function hostPick(): HostBridge | null {
  const b = (window as unknown as { vgacHost?: HostBridge }).vgacHost
  return b && typeof b.pickFile === 'function' ? b : null
}
