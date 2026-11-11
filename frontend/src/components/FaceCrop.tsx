// "Crop the face from it": drag a box around the face on a picture; the crop becomes its own picture. A tight face
// crop given as the Face picture is what carries a likeness into step 0.5 (a whole photo alone gave a generic face).
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import type { PickedImage } from './ImageDrop'

type Box = { x: number; y: number; w: number; h: number } // in the picture's own pixels

export function FaceCrop({ picture, onDone, onClose }: { picture: PickedImage; onDone: (face: PickedImage) => void; onClose: () => void }) {
  const imgRef = useRef<HTMLImageElement>(null)
  const [box, setBox] = useState<Box | null>(null)
  const drag = useRef<{ x0: number; y0: number } | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  // Pointer position in the picture's own pixels.
  const at = (e: ReactPointerEvent) => {
    const r = imgRef.current!.getBoundingClientRect()
    const sx = picture.width / r.width
    const sy = picture.height / r.height
    return { x: Math.max(0, Math.min(picture.width, (e.clientX - r.left) * sx)), y: Math.max(0, Math.min(picture.height, (e.clientY - r.top) * sy)) }
  }
  const down = (e: ReactPointerEvent) => {
    e.preventDefault()
    ;(e.target as Element).setPointerCapture(e.pointerId)
    const p = at(e)
    drag.current = { x0: p.x, y0: p.y }
    setBox({ x: p.x, y: p.y, w: 0, h: 0 })
  }
  const move = (e: ReactPointerEvent) => {
    if (!drag.current) return
    const p = at(e)
    const { x0, y0 } = drag.current
    setBox({ x: Math.min(x0, p.x), y: Math.min(y0, p.y), w: Math.abs(p.x - x0), h: Math.abs(p.y - y0) })
  }
  const up = () => {
    drag.current = null
  }

  const big = !!box && box.w >= 64 && box.h >= 64
  const use = () => {
    if (!box || !big || !imgRef.current) return
    // A little room around the box (hair and chin), kept inside the picture.
    const padX = box.w * 0.08
    const padY = box.h * 0.08
    const x = Math.max(0, box.x - padX)
    const y = Math.max(0, box.y - padY)
    const w = Math.min(picture.width - x, box.w + 2 * padX)
    const h = Math.min(picture.height - y, box.h + 2 * padY)
    const c = document.createElement('canvas')
    c.width = Math.round(w)
    c.height = Math.round(h)
    c.getContext('2d')!.drawImage(imgRef.current, x, y, w, h, 0, 0, c.width, c.height)
    const dataUrl = c.toDataURL('image/png')
    onDone({ dataUrl, name: 'face from ' + picture.name, bytes: Math.round((dataUrl.length * 3) / 4), width: c.width, height: c.height })
  }

  const r = imgRef.current?.getBoundingClientRect()
  const scale = r ? r.width / picture.width : 1
  return (
    <div className="overlay picture-overlay" onClick={onClose}>
      <div className="picture-viewer" role="dialog" aria-modal="true" aria-label="Crop the face" onClick={(e) => e.stopPropagation()}>
        <div className="picture-viewer-bar">
          <strong className="picture-viewer-title">Drag a box around the face, from the top of the hair to the chin</strong>
          <span className="grow" />
          <button type="button" className="primary" disabled={!big} onClick={use} title={big ? 'Use this box as the Face picture' : 'Drag a bigger box first'}>
            Use this face
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
        </div>
        <div className="picture-viewer-stage" style={{ position: 'relative', userSelect: 'none' }}>
          <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%', maxHeight: '100%' }}>
            <img
              ref={imgRef}
              src={picture.dataUrl}
              alt="The picture to crop the face from"
              draggable={false}
              onPointerDown={down}
              onPointerMove={move}
              onPointerUp={up}
              style={{ cursor: 'crosshair', display: 'block', maxWidth: '100%', maxHeight: '75vh', touchAction: 'none' }}
            />
            {box && box.w > 0 && (
              <div
                aria-hidden="true"
                style={{
                  position: 'absolute',
                  left: box.x * scale,
                  top: box.y * scale,
                  width: box.w * scale,
                  height: box.h * scale,
                  border: '2px solid var(--accent, #4da3ff)',
                  boxShadow: '0 0 0 9999px rgba(0,0,0,0.45)',
                  pointerEvents: 'none',
                }}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
