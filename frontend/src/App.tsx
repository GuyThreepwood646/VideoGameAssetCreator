import { useEffect } from 'react'
import { NavLink, Outlet } from 'react-router'
import { isMock } from './api'
import { HealthBadge } from './HealthPanel'
import { SetupBadge } from './SetupCheck'
import { useEventMode } from './events'
import { closeGuide, openGuide, useGuide } from './guide'

export default function App() {
  const mode = useEventMode()
  const guideOpen = useGuide()
  return (
    <div className="app">
      <header className="topbar">
        <NavLink to="/" className="brand">
          VGAC
        </NavLink>
        <nav className="topnav">
          <NavLink to="/" end>
            Projects
          </NavLink>
          <NavLink to="/gallery">Gallery</NavLink>
          <NavLink to="/glossary">Glossary</NavLink>
          <NavLink to="/health">Health</NavLink>
        </nav>
        <span className="grow" />
        <button type="button" className="link small" onClick={openGuide}>
          Show guide again
        </button>
        {isMock() && <span className="pill pill-warn" title="VITE_MOCK=1: all API calls are answered by the in-browser mock">mock backend</span>}
        <span className="pill" title="Event channel: WebSocket or 3 s polling fallback">
          events: {mode === 'ws' ? 'live' : mode === 'poll' ? 'polling' : 'connecting'}
        </span>
        <SetupBadge />
        <HealthBadge />
      </header>
      <main className="page">
        <Outlet />
      </main>
      {guideOpen && <FirstRunOverlay />}
    </div>
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
