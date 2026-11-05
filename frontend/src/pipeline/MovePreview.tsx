// A small moving preview of one library clip: a PNG strip of square cells played one cell after another.
// One shared clock drives every preview on the page, so a picker with ninety clips runs one timer, not ninety.
// Reduce Motion: the still cell is shown until the person presses Play.
import { useEffect, useState } from 'react'

const TICK_MS = 125
let tick = 0
let timer: number | null = null
const subscribers = new Set<(t: number) => void>()

function subscribe(fn: (t: number) => void): () => void {
  subscribers.add(fn)
  if (timer === null) {
    timer = window.setInterval(() => {
      tick += 1
      for (const s of subscribers) s(tick)
    }, TICK_MS)
  }
  return () => {
    subscribers.delete(fn)
    if (subscribers.size === 0 && timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
  }
}

export function useReducedMotion(): boolean {
  const media = () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null)
  const [reduce, setReduce] = useState(() => !!media()?.matches)
  useEffect(() => {
    const query = media()
    if (!query) return
    const on = () => setReduce(query.matches)
    query.addEventListener?.('change', on)
    return () => query.removeEventListener?.('change', on)
  }, [])
  return reduce
}

// The cell to show at clock tick t for a clip of `seconds` with `frames` cells: the strip spans the clip once, so the
// preview plays at about the clip's own speed (never faster than one cell per tick).
function cellAt(t: number, frames: number, seconds: number): number {
  const perCell = Math.max(1, Math.round((seconds * 1000) / Math.max(1, frames) / TICK_MS))
  return Math.floor(t / perCell) % Math.max(1, frames)
}

export function MovePreview({
  src,
  frames,
  still,
  seconds,
  label,
  size = 96,
}: {
  src: string | null
  frames: number
  still: number
  seconds: number
  label: string
  size?: number
}) {
  const reduce = useReducedMotion()
  const [asked, setAsked] = useState(false)
  const playing = !!src && frames > 1 && (!reduce || asked)
  const [cell, setCell] = useState(Math.min(Math.max(0, still), Math.max(0, frames - 1)))
  useEffect(() => {
    if (!playing) {
      setCell(Math.min(Math.max(0, still), Math.max(0, frames - 1)))
      return
    }
    return subscribe((t) => setCell(cellAt(t, frames, seconds)))
  }, [playing, frames, still, seconds])

  if (!src) {
    return (
      <div className="move-preview move-preview-empty" style={{ width: size, height: size }} aria-hidden="true">
        <span className="muted small">No preview yet</span>
      </div>
    )
  }
  const n = Math.max(1, frames)
  const x = n > 1 ? (cell / (n - 1)) * 100 : 0
  return (
    <div className="move-preview-wrap">
      <div
        className="move-preview"
        role="img"
        aria-label={`Preview: ${label}`}
        style={{ width: size, height: size, backgroundImage: `url("${src}")`, backgroundSize: `${n * 100}% 100%`, backgroundPosition: `${x}% 0` }}
      />
      {reduce && frames > 1 && (
        <button type="button" className="link small move-preview-play" onClick={() => setAsked((a) => !a)}>
          {asked ? 'Stop' : 'Play'}
        </button>
      )}
    </div>
  )
}
