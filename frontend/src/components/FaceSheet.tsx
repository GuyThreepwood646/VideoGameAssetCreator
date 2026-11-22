// The "Face: before and after" sheet (backend stages/face_detail.before_after_sheet): three columns of equal cells under a
// 24 px header band, five rows of views, then two close-up bands with a 24 px title each. The sheet carries its captions
// as tiny baked-in text; here each one is covered by a caption in real text (12 px or more), placed from the same layout.
import { useState } from 'react'

const PAD = 24
const HEADERS = ['Painted picture', '3D face before', '3D face after']
const ROWS = ['From the front', 'From the left', 'From the right', 'Left side', 'Right side']
const CLOSES = ['Close-ups after: both eyes, left cheek, right cheek', 'Close-ups after: left side cheek, right side cheek']

export function FaceSheet({ src, alt }: { src: string; alt: string }) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const cell = size ? size.w / 3 : 0
  const pct = (px: number) => (size ? (px / size.h) * 100 : 0) + '%'
  const x = (px: number) => (size ? (px / size.w) * 100 : 0) + '%'
  return (
    <div className="face-sheet">
      <img src={src} alt={alt} onLoad={(e) => setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })} />
      {size && (
        <>
          {HEADERS.map((t, i) => (
            <span key={t} className="face-sheet-cap face-sheet-band" style={{ top: 0, left: x(i * cell), width: x(cell), height: pct(PAD) }}>
              {t}
            </span>
          ))}
          {ROWS.slice(1).map((t, i) => (
            <span key={t} className="face-sheet-cap face-sheet-chip" style={{ top: pct(PAD + (i + 1) * cell + 6), left: x(6) }}>
              {t}
            </span>
          ))}
          {CLOSES.map((t, i) => (
            <span key={t} className="face-sheet-cap face-sheet-band face-sheet-title" style={{ top: pct(PAD + ROWS.length * cell + i * (cell + PAD)), left: 0, width: '100%', height: pct(PAD) }}>
              {t}
            </span>
          ))}
        </>
      )}
    </div>
  )
}
