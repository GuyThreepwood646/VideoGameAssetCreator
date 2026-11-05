// The "Light" button over the 3D viewer: five picture tiles (Studio, Outdoors, Overcast, Night, Flat).
// Only the preview changes; the files and the game are untouched.
import { useEffect, useId, useRef, useState } from 'react'
import { LIGHTS, lightById, type LightId } from '../previewLight'
import { Info } from './Info'

type Props = { value: LightId; onChange: (id: LightId) => void; note?: string | null }

export function LightPicker({ value, onChange, note }: Props) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)
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
    const onDown = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onDown)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onDown)
    }
  }, [open])

  return (
    <div className="viewer-light" ref={wrap}>
      <button ref={btn} type="button" className="viewer-light-btn" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)} title="Change the light the preview is shown in">
        Light: {current?.label ?? 'Studio'}
      </button>
      {open && (
        <div className="viewer-light-pop" id={id} role="group" aria-label="Preview light">
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
