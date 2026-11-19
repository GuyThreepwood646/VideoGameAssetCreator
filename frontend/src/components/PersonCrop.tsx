// The box round the main person in a feature photo: the form suggests it, the person can drag it, drag its corners to
// resize it, or use the keyboard (arrow keys move it, Shift with an arrow key resizes it, Enter uses it, Escape closes).
// FaceCrop only draws a new box, so it does not fit this; the Face picture keeps its own crop control.
import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { clampPersonBox, MIN_BOX } from '../featurePhotos'
import type { PersonBox } from '../api'
import type { PickedImage } from './ImageDrop'

type Corner = 'nw' | 'ne' | 'sw' | 'se'

// A box dragged from `start` by (dx, dy) picture pixels, or resized from one corner, kept inside the picture and at least
// MIN_BOX a side.
function moved(start: PersonBox, dx: number, dy: number, W: number, H: number): PersonBox {
  const x = Math.max(0, Math.min(start.x + dx, W - start.w))
  const y = Math.max(0, Math.min(start.y + dy, H - start.h))
  return { x, y, w: start.w, h: start.h }
}
function resized(start: PersonBox, corner: Corner, dx: number, dy: number, W: number, H: number): PersonBox {
  let { x, y, w, h } = start
  if (corner.includes('e')) w = start.w + dx
  if (corner.includes('w')) {
    x = start.x + dx
    w = start.w - dx
  }
  if (corner.includes('s')) h = start.h + dy
  if (corner.includes('n')) {
    y = start.y + dy
    h = start.h - dy
  }
  const minW = Math.min(MIN_BOX, W)
  const minH = Math.min(MIN_BOX, H)
  if (w < minW) {
    if (corner.includes('w')) x -= minW - w
    w = minW
  }
  if (h < minH) {
    if (corner.includes('n')) y -= minH - h
    h = minH
  }
  x = Math.max(0, Math.min(x, W - w))
  y = Math.max(0, Math.min(y, H - h))
  return { x, y, w: Math.min(w, W - x), h: Math.min(h, H - y) }
}

export function PersonCrop({ picture, box, suggested, onDone, onClose }: { picture: PickedImage; box: PersonBox; suggested: PersonBox | null; onDone: (box: PersonBox) => void; onClose: () => void }) {
  const W = picture.width
  const H = picture.height
  const [cur, setCur] = useState<PersonBox>(box)
  const imgRef = useRef<HTMLImageElement>(null)
  const drag = useRef<{ corner: Corner | null; start: PersonBox; cx: number; cy: number } | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  // Picture pixels per screen pixel, read when a drag starts (the picture may be scaled to the window).
  const scale = () => (imgRef.current ? W / imgRef.current.getBoundingClientRect().width : 1)

  const startDrag = (corner: Corner | null) => (e: ReactPointerEvent) => {
    e.preventDefault()
    e.stopPropagation()
    ;(e.target as Element).setPointerCapture(e.pointerId)
    drag.current = { corner, start: cur, cx: e.clientX, cy: e.clientY }
  }
  const onMove = (e: ReactPointerEvent) => {
    const d = drag.current
    if (!d) return
    const k = scale()
    const dx = (e.clientX - d.cx) * k
    const dy = (e.clientY - d.cy) * k
    setCur(d.corner ? resized(d.start, d.corner, dx, dy, W, H) : moved(d.start, dx, dy, W, H))
  }
  const endDrag = () => {
    drag.current = null
  }

  // Keyboard: arrows move the box by 1 percent of the picture's longer side; Shift with an arrow resizes from the bottom right.
  const step = Math.max(1, Math.round(Math.max(W, H) * 0.01))
  const onKey = (e: ReactKeyboardEvent) => {
    const d: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }
    if (e.key === 'Enter') {
      e.preventDefault()
      onDone(cur)
      return
    }
    const dir = d[e.key]
    if (!dir) return
    e.preventDefault()
    const [dx, dy] = [dir[0] * step, dir[1] * step]
    setCur((c) => (e.shiftKey ? resized(c, 'se', dx, dy, W, H) : moved(c, dx, dy, W, H)))
  }

  const use = () => {
    const ok = clampPersonBox(cur, W, H)
    if (ok) onDone(ok)
  }

  const pct = (v: number, total: number) => `${(v / total) * 100}%`
  const corners: Corner[] = ['nw', 'ne', 'sw', 'se']
  return (
    <div className="overlay picture-overlay" onClick={onClose}>
      <div className="picture-viewer" role="dialog" aria-modal="true" aria-label="Box round the person" onClick={(e) => e.stopPropagation()}>
        <div className="picture-viewer-bar">
          <strong className="picture-viewer-title">Box round the person you want on the character</strong>
          <span className="grow" />
          {suggested && (
            <button type="button" className="btn" onClick={() => setCur(suggested)}>
              Reset to the suggested box
            </button>
          )}
          <button type="button" className="primary" onClick={use}>
            Use this box
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
        </div>
        <div className="picture-viewer-stage" style={{ position: 'relative', userSelect: 'none' }}>
          <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%' }} onPointerMove={onMove} onPointerUp={endDrag} onPointerCancel={endDrag}>
            <img ref={imgRef} src={picture.dataUrl} alt="The photo, with the box round the person" draggable={false} style={{ display: 'block', maxWidth: '100%', maxHeight: '75vh' }} />
            <div
              tabIndex={0}
              role="group"
              aria-label={`Box round the person: left ${cur.x}, top ${cur.y}, width ${cur.w}, height ${cur.h} pixels. Arrow keys move it; Shift with an arrow key makes it bigger or smaller; Enter uses it.`}
              onKeyDown={onKey}
              onPointerDown={startDrag(null)}
              style={{
                position: 'absolute',
                left: pct(cur.x, W),
                top: pct(cur.y, H),
                width: pct(cur.w, W),
                height: pct(cur.h, H),
                border: '2px solid var(--accent, #4da3ff)',
                boxShadow: '0 0 0 9999px rgba(0,0,0,0.45)',
                cursor: 'move',
                touchAction: 'none',
                outline: 'none',
              }}
            >
              {corners.map((c) => (
                <span
                  key={c}
                  aria-hidden="true"
                  onPointerDown={startDrag(c)}
                  style={{
                    position: 'absolute',
                    width: 24,
                    height: 24,
                    background: 'var(--accent, #4da3ff)',
                    borderRadius: 4,
                    touchAction: 'none',
                    cursor: c === 'nw' || c === 'se' ? 'nwse-resize' : 'nesw-resize',
                    left: c.includes('w') ? -12 : 'calc(100% - 12px)',
                    top: c.includes('n') ? -12 : 'calc(100% - 12px)',
                  }}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
