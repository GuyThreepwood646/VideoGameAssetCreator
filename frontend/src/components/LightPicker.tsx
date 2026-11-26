// The "Light" button over the 3D viewer: five picture tiles (Studio, Outdoors, Overcast, Night, Flat).
// Only the preview changes; the files and the game are untouched.
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { LIGHTS, lightById, type LightId } from '../previewLight'
import { Info } from './Info'
import { isOutsideClick, popPlacement } from '../popMenu'

type Props = { value: LightId; onChange: (id: LightId) => void; note?: string | null }

export function LightPicker({ value, onChange, note }: Props) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)
  const pop = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<CSSProperties | undefined>(undefined)
  const id = useId()
  const current = lightById(value)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        btn.current?.focus()
      }
    }
    // A click, not a pointerdown: a touch that starts a scroll or a drag never becomes a click, so it cannot close the menu.
    const onClick = (e: MouseEvent) => {
      if (isOutsideClick(wrap.current, e.target)) setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('click', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('click', onClick)
    }
  }, [open])

  // Wide windows: fix the pop-up to the window beside its button, so the viewer's overflow:hidden cannot cut it off.
  // Narrow windows: the stylesheet makes it a bottom sheet.
  useLayoutEffect(() => {
    if (!open) return
    const update = () => {
      if (!btn.current || !pop.current) return
      const p = popPlacement(btn.current.getBoundingClientRect(), { width: pop.current.offsetWidth, height: pop.current.scrollHeight }, { width: window.innerWidth, height: window.innerHeight })
      setPlace(p ? { position: 'fixed', top: p.top, left: p.left, maxHeight: p.maxHeight, overflowY: 'auto' } : undefined)
    }
    update()
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    return () => {
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
    }
  }, [open])

  return (
    <div className="viewer-light" ref={wrap}>
      <button ref={btn} type="button" className="viewer-light-btn" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)} title="Change the light the preview is shown in">
        Light: {current?.label ?? 'Studio'}
      </button>
      {open && (
        <div className="viewer-light-pop" id={id} ref={pop} style={place} role="group" aria-label="Preview light">
          <div className="viewer-light-head">
            Preview light
            <Info term="term.preview_light" />
          </div>
          <div className="viewer-light-tiles">
            {LIGHTS.map((l) => (
              <button
                key={l.id}
                type="button"
                className="viewer-light-tile"
                aria-pressed={l.id === value}
                title={l.sentence}
                onClick={() => {
                  onChange(l.id)
                  setOpen(false)
                }}
              >
                <span className="viewer-light-pic" style={l.tile ? { backgroundImage: `url("${l.tile}")` } : { background: l.tileCss }} aria-hidden="true" />
                <span className="viewer-light-name">{l.label}</span>
              </button>
            ))}
          </div>
          <div className="viewer-light-foot">Only the preview changes. Your game uses its own lights.</div>
        </div>
      )}
      {note && <div className="viewer-light-note">{note}</div>}
    </div>
  )
}
