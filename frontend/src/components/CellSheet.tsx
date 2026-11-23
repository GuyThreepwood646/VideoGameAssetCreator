// A picture sheet made of equal square cells with a thin label bar under each (the rig step's animation frames and
// bent poses), drawn cell by cell so the captions are real text at a readable size. The sheets already on disk carry
// tiny baked-in labels; each cell is cut from the sheet without its label bar, and the caption is ours.
import { useState } from 'react'

export function CellSheet({ src, cell, label, alt, className = '' }: { src: string; cell: number; label: (row: number, col: number) => string; alt: string; className?: string }) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const cols = size ? Math.max(1, Math.round(size.w / cell)) : 1
  // The bar under a cell is 16 px on older sheets and 24 px on newer ones: the rows tell which.
  const rows = size ? Math.max(1, Math.round(size.h / (cell + 20))) : 1
  const total = size ? size.h : 0
  const cellH = size ? size.h / rows : cell
  return (
    <div className={'cell-sheet ' + className}>
      <img src={src} alt="" className="cell-sheet-probe" onLoad={(e) => setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })} />
      {size && (
        <div className="cell-sheet-grid" role="img" aria-label={alt} style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
          {Array.from({ length: rows * cols }, (_v, i) => {
            const r = Math.floor(i / cols)
            const c = i % cols
            const y = total > cell ? ((r * cellH) / (total - cell)) * 100 : 0
            const x = cols > 1 ? (c / (cols - 1)) * 100 : 0
            return (
              <figure key={i} className="cell-sheet-cell">
                <div className="cell-sheet-pic" style={{ backgroundImage: `url(${src})`, backgroundSize: `${cols * 100}% auto`, backgroundPosition: `${x}% ${y}%` }} />
                <figcaption>{label(r, c)}</figcaption>
              </figure>
            )
          })}
        </div>
      )}
    </div>
  )
}
