// A place to put one of your own pictures: drag and drop it, paste it from the clipboard (click the box, then
// Ctrl+V), or choose it. Shows a preview, the file size and plain errors. The picture is read here as a data: URL and
// only ever sent to this app's own backend, which runs on this computer.
import { useId, useRef, useState, type ClipboardEvent, type DragEvent, type ReactNode } from 'react'
import { Info } from './Info'

export type PickedImage = { dataUrl: string; name: string; bytes: number; width: number; height: number }

export const MAX_PICTURE_BYTES = 25 * 1024 * 1024
const TYPES = ['image/png', 'image/jpeg', 'image/webp']

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} bytes`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

// The file name without its extension, as a starting name for an asset ("red_knight.png" -> "Red knight").
export function nameFromFile(name: string): string {
  const stem = name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim()
  return stem ? stem.charAt(0).toUpperCase() + stem.slice(1, 60) : ''
}

function readPicture(file: File): Promise<PickedImage> {
  return new Promise((resolve, reject) => {
    if (!TYPES.includes(file.type)) {
      reject(new Error(file.type === 'image/gif' ? 'That picture is a GIF. Use a PNG, JPEG or WebP picture.' : 'That file is not a PNG, JPEG or WebP picture.'))
      return
    }
    if (file.size > MAX_PICTURE_BYTES) {
      reject(new Error(`That picture is ${fmtBytes(file.size)}, larger than 25 MB. Save a smaller copy and try again.`))
      return
    }
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('That picture could not be read. Try adding it again.'))
    reader.onload = () => {
      const dataUrl = String(reader.result)
      const img = new Image()
      img.onload = () => resolve({ dataUrl, name: file.name || 'pasted picture', bytes: file.size, width: img.naturalWidth, height: img.naturalHeight })
      img.onerror = () => reject(new Error('That file is damaged or is not a picture the app can read.'))
      img.src = dataUrl
    }
    reader.readAsDataURL(file)
  })
}

export function ImageDrop({
  value,
  onChange,
  label,
  term = 'term.image_drop',
  hint,
  compact = false,
  disabled = false,
  children,
}: {
  value: PickedImage | null
  onChange: (img: PickedImage | null) => void
  label: ReactNode
  term?: string
  hint?: ReactNode
  compact?: boolean
  disabled?: boolean
  // Shown inside the box when it is empty (the turnaround slot's facing diagram).
  children?: ReactNode
}) {
  const input = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [over, setOver] = useState(false)
  const id = useId()

  const take = (file: File | null | undefined) => {
    if (!file || disabled) return
    setError(null)
    readPicture(file).then(
      (img) => onChange(img),
      (e: unknown) => setError(e instanceof Error ? e.message : String(e)),
    )
  }
  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    take(e.dataTransfer.files?.[0])
  }
  const onPaste = (e: ClipboardEvent) => {
    const item = Array.from(e.clipboardData.items).find((i) => i.kind === 'file' && i.type.startsWith('image/'))
    if (!item) {
      setError('There is no picture on the clipboard. Copy a picture first, then paste it here.')
      return
    }
    e.preventDefault()
    take(item.getAsFile())
  }

  return (
    <div className={'image-drop' + (compact ? ' compact' : '')}>
      <div className="field-label" id={id}>
        {label}
        <Info term={term} />
      </div>
      <div
        className={'image-drop-zone' + (over ? ' over' : '') + (value ? ' has-image' : '')}
        tabIndex={disabled ? -1 : 0}
        role="group"
        aria-labelledby={id}
        aria-disabled={disabled}
        onDragOver={(e) => {
          e.preventDefault()
          if (!disabled) setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        onPaste={onPaste}
      >
        {value ? (
          <img className="image-drop-preview" src={value.dataUrl} alt="Your picture" />
        ) : (
          <div className="image-drop-empty">
            {children}
            <span className="muted small">Drop a picture here, click here and paste one (Ctrl+V), or</span>
          </div>
        )}
        <div className="row-gap">
          <button type="button" className="small-btn" disabled={disabled} onClick={() => input.current?.click()}>
            {value ? 'Choose another' : 'Choose a picture'}
          </button>
          {value && (
            <button type="button" className="link small" disabled={disabled} onClick={() => onChange(null)}>
              Remove
            </button>
          )}
        </div>
        <input
          ref={input}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          hidden
          onChange={(e) => {
            take(e.target.files?.[0])
            e.target.value = ''
          }}
        />
      </div>
      {value && (
        <div className="muted small">
          {value.name}, {value.width} x {value.height} px, {fmtBytes(value.bytes)}
        </div>
      )}
      {error && <div className="error small">{error}</div>}
      {hint && <div className="field-hint">{hint}</div>}
      {!compact && <div className="muted small">Your pictures stay on this computer.</div>}
    </div>
  )
}
