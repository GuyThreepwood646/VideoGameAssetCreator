// A picture at full size (steps 1 and 2). Opened by double-clicking a picture or by its corner button.
// Fit shows the whole picture in the window; Actual size shows every pixel and scrolls to pan. Several pictures
// (the views of Check all sides) step with the arrow keys or the arrow buttons. Esc, the close button or a click
// outside the picture closes it, and focus goes back to what opened it.
import { useEffect, useRef, useState, type ReactNode } from 'react'

export type PictureItem = { src: string; label: string }

export function PictureViewer({ items, start = 0, checker = false, onClose }: { items: PictureItem[]; start?: number; checker?: boolean; onClose: () => void }) {
  const [index, setIndex] = useState(Math.min(Math.max(start, 0), Math.max(items.length - 1, 0)))
  const [actual, setActual] = useState(false)
  const [size, setSize] = useState<[number, number] | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  const many = items.length > 1
  const step = (d: number) => {
    if (!many) return
    setSize(null)
    setIndex((i) => (i + d + items.length) % items.length)
  }
  const stepRef = useRef(step)
  useEffect(() => {
    close.current = onClose
    stepRef.current = step
  })
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    ref.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close.current()
      else if (e.key === 'ArrowRight') stepRef.current(1)
      else if (e.key === 'ArrowLeft') stepRef.current(-1)
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (opener && document.contains(opener)) opener.focus()
    }
  }, [])
  const item = items[index]
  if (!item) return null
  return (
    <div className="overlay picture-overlay" onClick={onClose}>
      <div ref={ref} tabIndex={-1} className="picture-viewer" role="dialog" aria-modal="true" aria-label={`${item.label}, full size`} onClick={(e) => e.stopPropagation()}>
        <div className="picture-viewer-bar">
          <strong className="picture-viewer-title">
            {item.label}
            {many && <span className="muted"> ({index + 1} of {items.length})</span>}
          </strong>
          {size && <span className="muted small">{size[0]} x {size[1]} px</span>}
          <span className="grow" />
          <div className="segmented">
            <button type="button" aria-pressed={!actual} onClick={() => setActual(false)} title="The whole picture in the window">
              Fit
            </button>
            <button type="button" aria-pressed={actual} onClick={() => setActual(true)} title="Every pixel; scroll to move around">
              Actual size
            </button>
          </div>
          <a className="btn" href={item.src} target="_blank" rel="noreferrer">
            Open in a new tab
          </a>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close" title="Close (Esc)">
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div className={'picture-viewer-stage' + (actual ? ' actual' : '') + (checker ? ' checker' : '')}>
          <img
            key={item.src}
            src={item.src}
            alt={item.label}
            onLoad={(e) => setSize([e.currentTarget.naturalWidth, e.currentTarget.naturalHeight])}
            onDoubleClick={() => setActual((a) => !a)}
            title={actual ? 'Double-click to fit the window' : 'Double-click for actual size'}
          />
        </div>
        {many && (
          <>
            <button type="button" className="picture-nav prev" onClick={() => step(-1)} aria-label="Previous picture" title="Previous (left arrow)">
              <Chevron flip />
            </button>
            <button type="button" className="picture-nav next" onClick={() => step(1)} aria-label="Next picture" title="Next (right arrow)">
              <Chevron />
            </button>
          </>
        )}
      </div>
    </div>
  )
}

function Chevron({ flip = false }: { flip?: boolean }) {
  return (
    <svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true" style={flip ? { transform: 'scaleX(-1)' } : undefined}>
      <path d="M6 3l5 5-5 5" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

// A picture that opens full size: double-click it, or press its corner button (also reachable by keyboard).
export function Zoomable({ label, onOpen, children, className = '', style }: { label: string; onOpen: () => void; children: ReactNode; className?: string; style?: React.CSSProperties }) {
  return (
    <div className={'zoomable ' + className} style={style} onDoubleClick={onOpen} title="Double-click to see it full size">
      {children}
      <button type="button" className="zoom-btn" onClick={onOpen} aria-label={`See ${label} full size`} title="See it full size">
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M9.5 2.5h4v4M13.5 2.5l-4.5 4.5M6.5 13.5h-4v-4M2.5 13.5l4.5-4.5" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
    </div>
  )
}
