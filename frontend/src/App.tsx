import { useEffect, useRef } from 'react'
import { NavLink, Outlet } from 'react-router'
import { EngineBanner, HealthBadge } from './HealthPanel'
import { SetupBadge } from './SetupCheck'
import { useEventMode } from './events'
import { closeGuide, openGuide, useGuide } from './guide'
import { notifyEnabled, setNotifyEnabled, Toasts } from './notify'

export default function App() {
  const mode = useEventMode()
  const guideOpen = useGuide()
  return (
    <div className="app">
      {/* The same flag that installs the mock layer (main.tsx), written out so a real build drops the banner. */}
      {import.meta.env.VITE_MOCK === '1' && (
        <div className="demo-banner" role="status">
          <strong>Demo data.</strong> This preview uses sample data; switches and buttons here change nothing real. The real app is at{' '}
          <a href="http://127.0.0.1:8765">http://127.0.0.1:8765</a>.
        </div>
      )}
      <header className="topbar">
        <NavLink to="/" className="brand">
          VGAC
        </NavLink>
        <nav className="topnav">
          <NavLink to="/" end>
            Projects
          </NavLink>
          <NavLink to="/gallery">Finished</NavLink>
          <NavLink to="/glossary">Glossary</NavLink>
          <NavLink to="/credits">Credits</NavLink>
          <NavLink to="/health">System</NavLink>
        </nav>
        <span className="grow" />
        {mode === 'poll' && (
          <span className="chip chip-warn" title="Live updates are not connected, so the page checks every 3 seconds instead.">
            Updating slowly
          </span>
        )}
        <SetupBadge />
        <HealthBadge />
        <HelpMenu />
      </header>
      <EngineBanner />
      <main className="page">
        <Outlet />
      </main>
      <Toasts />
      {guideOpen && <FirstRunOverlay />}
    </div>
  )
}

// "?" menu: the guide, glossary, credits and keyboard shortcuts in one place (UI-19, UI-40, UI-41).
function HelpMenu() {
  const ref = useRef<HTMLDetailsElement>(null)
  const close = () => ref.current?.removeAttribute('open')
  return (
    <details className="help-menu" ref={ref}>
      <summary aria-label="Help" title="Help">?</summary>
      <div className="status-pop help-pop" onClick={close}>
        <button type="button" className="link" onClick={openGuide}>Show the guide again</button>
        <NavLink to="/glossary">Glossary (what the words mean)</NavLink>
        <NavLink to="/credits">Credits</NavLink>
        <label className="check-item" onClick={(e) => e.stopPropagation()}>
          <input type="checkbox" defaultChecked={notifyEnabled()} onChange={(e) => setNotifyEnabled(e.target.checked)} />
          <span>Tell me when a long run finishes or needs me</span>
        </label>
        <div className="muted small shortcuts">
          <strong>Keyboard shortcuts on an asset</strong>
          <div>K keep this version, R try again, C change something</div>
          <div>Left and right arrows: previous and next version</div>
          <div>1 to 6: go to a step</div>
        </div>
      </div>
    </details>
  )
}

function FirstRunOverlay() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeGuide()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])
  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="guide-title" onClick={closeGuide}>
      <div className="overlay-card" onClick={(e) => e.stopPropagation()}>
        <h2 id="guide-title" className="overlay-title">
          How this works
        </h2>
        <ol className="overlay-steps">
          <li>
            <span className="overlay-num">1</span>
            <div>
              <strong>Describe your character.</strong>
              <div className="muted">One sentence is enough: what it is, two or three details, the colours.</div>
            </div>
          </li>
          <li>
            <span className="overlay-num">2</span>
            <div>
              <strong>We build it step by step and show you each result.</strong>
              <div className="muted">A picture, then all sides, then the 3D model, then it is cleaned up, made to move and handed to your game.</div>
            </div>
          </li>
          <li>
            <span className="overlay-num">3</span>
            <div>
              <strong>Say yes or ask for changes.</strong>
              <div className="muted">After every step: keep it, try again, or describe a change. Nothing is lost by trying.</div>
            </div>
          </li>
        </ol>
        <div className="muted small">The "Your next step" card on every asset tells you what to do. Hover any "i" for what a word means, or open the Glossary at the top.</div>
        <div className="row-gap">
          <button type="button" className="primary" onClick={closeGuide} autoFocus>
            Got it
          </button>
        </div>
      </div>
    </div>
  )
}
