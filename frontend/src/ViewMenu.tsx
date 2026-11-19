import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'

// The View menu at the right end of the top bar: Reload, Force reload and Full screen.
// Zoom is left out: the page has no way to change WebView2's zoom (Ctrl+plus and Ctrl+minus already work there),
// and the Electron launcher's zoom calls have no equivalent in the starter window.
// The Status window is left out too: no bridge operation opens it (starter/src/WebWindow.cs BridgeOps has none).

export function ViewMenu() {
  const [open, setOpen] = useState(false)
  const [full, setFull] = useState(() => typeof document !== 'undefined' && !!document.fullscreenElement)
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const pendingFocus = useRef<'first' | 'last' | null>(null)
  const menuId = useId()
  const canFull = typeof document !== 'undefined' && document.fullscreenEnabled === true

  useEffect(() => {
    const onChange = () => setFull(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  // Click outside closes the menu. Escape is handled on the wrapper so it works from the button and from the items.
  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [open])

  function items(): HTMLButtonElement[] {
    return Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [])
  }

  function moveFocus(where: 'first' | 'last' | 1 | -1) {
    const list = items()
    if (!list.length) return
    const cur = list.indexOf(document.activeElement as HTMLButtonElement)
    let next: number
    if (where === 'first') next = 0
    else if (where === 'last') next = list.length - 1
    else if (cur < 0) next = where > 0 ? 0 : list.length - 1
    else next = (cur + where + list.length) % list.length
    list[next].focus()
  }

  // A menu opened from the keyboard puts focus on its first (or last) item once it has rendered.
  useEffect(() => {
    if (!open || !pendingFocus.current) return
    moveFocus(pendingFocus.current)
    pendingFocus.current = null
  }, [open])

  function close(returnFocus: boolean) {
    setOpen(false)
    if (returnFocus) button.current?.focus()
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      if (open) {
        e.preventDefault()
        close(true)
      }
      return
    }
    if (!open) {
      if (e.target === button.current && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        e.preventDefault()
        pendingFocus.current = e.key === 'ArrowDown' ? 'first' : 'last'
        setOpen(true)
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      moveFocus(1)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      moveFocus(-1)
    } else if (e.key === 'Home') {
      e.preventDefault()
      moveFocus('first')
    } else if (e.key === 'End') {
      e.preventDefault()
      moveFocus('last')
    } else if (e.key === 'Tab') {
      setOpen(false)
    }
  }

  // Fetch this page's own address with cache 'reload' first: that replaces the cached copy of the page, so the reload that
  // follows picks up a freshly built frontend even when the browser had kept the old index.html.
  async function forceReload() {
    close(false)
    try {
      await fetch(window.location.href, { cache: 'reload' })
    } catch {
      // The backend may be down; the reload below then shows the usual error page.
    }
    window.location.reload()
  }

  function toggleFull() {
    close(false)
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => undefined)
    } else {
      document.documentElement.requestFullscreen().catch(() => undefined)
    }
  }

  return (
    <div className="view-menu" ref={wrap} onKeyDown={onKey}>
      <button
        type="button"
        ref={button}
        className="view-menu-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen((o) => !o)}
      >
        View
      </button>
      <div id={menuId} role="menu" aria-label="View" ref={menu} className="status-pop view-pop" hidden={!open}>
        <button type="button" role="menuitem" className="view-item" tabIndex={-1} onClick={() => window.location.reload()}>
          Reload
        </button>
        <button type="button" role="menuitem" className="view-item" tabIndex={-1} onClick={forceReload}>
          Force reload
        </button>
        {canFull && (
          <button type="button" role="menuitem" className="view-item" tabIndex={-1} onClick={toggleFull}>
            {full ? 'Leave full screen' : 'Full screen'}
          </button>
        )}
      </div>
    </div>
  )
}
