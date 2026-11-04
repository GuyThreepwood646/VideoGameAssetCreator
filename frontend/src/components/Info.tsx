// <Info term="..."/>: an "i" in a circle (drawn with CSS, no emoji) with a tooltip
// that shows the plain meaning, why it matters and the technical name. Opens on
// hover and on keyboard focus; Escape closes it. Renders nothing for unknown terms,
// so it is safe to sprinkle next to every label.
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { helpFor, type HelpEntry } from '../help'

type Props = { term?: string; entry?: HelpEntry | null; label?: string }

export function Info({ term, entry, label }: Props) {
  const e = entry ?? (term ? helpFor(term) : null)
  const [open, setOpen] = useState(false)
  const [align, setAlign] = useState<'left' | 'right'>('left')
  const btn = useRef<HTMLButtonElement>(null)
  const id = useId()

  useEffect(() => {
    if (!open) return
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        setOpen(false)
        btn.current?.blur()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  if (!e) return null

  const show = () => {
    const r = btn.current?.getBoundingClientRect()
    if (r) setAlign(r.left > window.innerWidth * 0.6 ? 'right' : 'left')
    setOpen(true)
  }
  const hide = () => setOpen(false)

  return (
    <span className="info-wrap" onMouseEnter={show} onMouseLeave={hide}>
      <button
        ref={btn}
        type="button"
        className="info-btn"
        aria-label={label ?? `What is ${e.plain}?`}
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onFocus={show}
        onBlur={hide}
        onClick={(ev) => {
          ev.preventDefault()
          ev.stopPropagation()
          open ? hide() : show()
        }}
      >
        i
      </button>
      {open && (
        <span role="tooltip" id={id} className={'info-tip info-tip-' + align}>
          <span className="info-title">{e.plain}</span>
          <span className="info-meaning">{e.meaning}</span>
          <span className="info-why">Why it matters: {e.why}</span>
          <span className="info-tech">Technical name: {e.technical}</span>
        </span>
      )}
    </span>
  )
}

// A label followed by its Info icon.
export function Term({ term, children, entry }: { term?: string; entry?: HelpEntry | null; children?: ReactNode }) {
  const e = entry ?? (term ? helpFor(term) : null)
  return (
    <span className="term">
      {children ?? e?.plain ?? term}
      <Info entry={e} />
    </span>
  )
}
