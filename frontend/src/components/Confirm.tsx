// The app's own confirm dialog (UI-30). useConfirm() gives an ask() that resolves true or false and the dialog to render.
// An optional typeToConfirm text must be typed before the danger button works (used for deleting a project).
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

export type ConfirmOptions = {
  title: string
  body: ReactNode
  confirmLabel?: string
  danger?: boolean
  typeToConfirm?: string
}

export function useConfirm(): { ask: (o: ConfirmOptions) => Promise<boolean>; dialog: ReactNode } {
  const [state, setState] = useState<{ opts: ConfirmOptions; resolve: (ok: boolean) => void } | null>(null)
  const ask = useCallback((opts: ConfirmOptions) => new Promise<boolean>((resolve) => setState({ opts, resolve })), [])
  const done = (ok: boolean) => {
    state?.resolve(ok)
    setState(null)
  }
  return { ask, dialog: state ? <ConfirmDialog opts={state.opts} onDone={done} /> : null }
}

function ConfirmDialog({ opts, onDone }: { opts: ConfirmOptions; onDone: (ok: boolean) => void }) {
  const [typed, setTyped] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    ref.current?.querySelector<HTMLElement>('input, button')?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onDone(false)
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (opener && document.contains(opener)) opener.focus()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const ok = !opts.typeToConfirm || typed.trim() === opts.typeToConfirm
  return (
    <div className="overlay" onClick={() => onDone(false)}>
      <div ref={ref} tabIndex={-1} className="modal-card confirm-card" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="confirm-title" className="overlay-title">
          {opts.title}
        </h2>
        <div className="small">{opts.body}</div>
        {opts.typeToConfirm && (
          <label className="field">
            <span className="field-label">Type {opts.typeToConfirm} to confirm</span>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} aria-label={`Type ${opts.typeToConfirm} to confirm`} />
          </label>
        )}
        <div className="row-gap">
          <button type="button" className={opts.danger ? 'danger' : 'primary'} disabled={!ok} onClick={() => onDone(true)}>
            {opts.confirmLabel ?? 'OK'}
          </button>
          <button type="button" onClick={() => onDone(false)}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
