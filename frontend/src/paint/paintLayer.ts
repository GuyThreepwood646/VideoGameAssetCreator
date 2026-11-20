// The base-colour picture being painted (WP-22). It holds RGBA bytes in glTF row order: row 0 is the top, and uv v = 0
// reads row 0 (the texture is set to flipY false). A stroke keeps a start copy and a mask (the strongest brush value
// each texel has had in this stroke), so overlapping dabs do not build up past the stroke's strength. Undo keeps the
// changed rectangle before and after each stroke, limited to 64 strokes and 256 MB. Changes reach the GPU as the
// changed rectangle only, copied into the picture texture.
import * as THREE from 'three'

export const UNDO_STROKES = 64
export const UNDO_BYTES = 256 * 1024 * 1024

export type Rect = { x: number; y: number; w: number; h: number }
// What the stroke was set to, for the stroke log saved with the picture (WP-23). Sizes are in centimetres on the model.
export type StrokeRecord = {
  mode: 'paint' | 'erase'
  rgb: [number, number, number]
  size_cm: number
  edge: number
  strength: number
  only_clicked_part: boolean
  mirror: boolean
}
type Entry = { rect: Rect; before: Uint8Array; after: Uint8Array; stroke: StrokeRecord | null }

export class PaintLayer {
  readonly width: number
  readonly height: number
  // The picture as it is now, and as it was when the model was loaded.
  readonly pixels: Uint8Array
  readonly original: Uint8Array
  readonly texture: THREE.DataTexture
  private start: Uint8Array | null = null
  private mask: Float32Array | null = null
  private pendingStroke: StrokeRecord | null = null
  private strokeRect: Rect | null = null
  private pendingRect: Rect | null = null
  private undoStack: Entry[] = []
  private redoStack: Entry[] = []
  private undoBytes = 0

  constructor(width: number, height: number, pixels: Uint8Array) {
    if (pixels.length !== width * height * 4) throw new Error('picture size does not match its pixels')
    this.width = width
    this.height = height
    this.pixels = pixels
    this.original = pixels.slice()
    this.texture = new THREE.DataTexture(this.pixels, width, height, THREE.RGBAFormat)
    this.texture.colorSpace = THREE.SRGBColorSpace
    this.texture.flipY = false
    this.texture.generateMipmaps = true
    this.texture.minFilter = THREE.LinearMipmapLinearFilter
    this.texture.magFilter = THREE.LinearFilter
    this.texture.needsUpdate = true
  }

  // A picture of one colour (sRGB bytes), for a model that has no picture yet.
  static blank(width: number, height: number, rgb: [number, number, number]): PaintLayer {
    const px = new Uint8Array(width * height * 4)
    for (let i = 0; i < width * height; i++) {
      px[i * 4] = rgb[0]
      px[i * 4 + 1] = rgb[1]
      px[i * 4 + 2] = rgb[2]
      px[i * 4 + 3] = 255
    }
    return new PaintLayer(width, height, px)
  }

  get canUndo(): boolean {
    return this.undoStack.length > 0
  }
  get canRedo(): boolean {
    return this.redoStack.length > 0
  }
  get undoCount(): number {
    return this.undoStack.length
  }
  get undoBytesUsed(): number {
    return this.undoBytes
  }
  get inStroke(): boolean {
    return this.start !== null
  }

  // The stroke's start copy and mask, for the dab code. Only between beginStroke and endStroke.
  get stroke(): { start: Uint8Array; mask: Float32Array } | null {
    return this.start && this.mask ? { start: this.start, mask: this.mask } : null
  }

  beginStroke(stroke: StrokeRecord | null = null): void {
    if (this.start) this.endStroke()
    this.pendingStroke = stroke
    this.start = this.pixels.slice()
    this.mask = new Float32Array(this.width * this.height)
    this.strokeRect = null
  }

  // Records that texels in [x0, x1) by [y0, y1) may have changed. Called by the dab code.
  touch(x0: number, y0: number, x1: number, y1: number): void {
    const x = Math.max(0, x0)
    const y = Math.max(0, y0)
    const w = Math.min(this.width, x1) - x
    const h = Math.min(this.height, y1) - y
    if (w <= 0 || h <= 0) return
    this.strokeRect = unite(this.strokeRect, { x, y, w, h })
    this.pendingRect = unite(this.pendingRect, { x, y, w, h })
  }

  // Ends the stroke. Returns false when nothing changed (nothing is added to undo).
  endStroke(): boolean {
    const start = this.start
    const rect = this.strokeRect
    const stroke = this.pendingStroke
    this.start = null
    this.mask = null
    this.strokeRect = null
    this.pendingStroke = null
    if (!start || !rect) return false
    const before = crop(start, this.width, rect)
    const after = crop(this.pixels, this.width, rect)
    if (equalBytes(before, after)) return false
    this.push(this.undoStack, { rect, before, after, stroke })
    this.redoStack = []
    this.enforceLimits()
    return true
  }

  // Sets the whole picture back to the one the model came with, as one undoable step.
  resetToOriginal(): Rect {
    if (this.start) this.endStroke()
    const rect = { x: 0, y: 0, w: this.width, h: this.height }
    const before = this.pixels.slice()
    const after = this.original.slice()
    if (!equalBytes(before, after)) {
      this.push(this.undoStack, { rect, before, after, stroke: null })
      this.redoStack = []
      this.enforceLimits()
      this.pendingRect = unite(this.pendingRect, rect)
      this.writeRect(rect, after)
    }
    return rect
  }

  // Steps back one stroke. Returns the changed rectangle (for the GPU), or null.
  undo(): Rect | null {
    const e = this.undoStack.pop()
    if (!e) return null
    this.undoBytes -= e.before.byteLength + e.after.byteLength
    this.writeRect(e.rect, e.before)
    this.redoStack.push(e)
    this.pendingRect = unite(this.pendingRect, e.rect)
    return e.rect
  }

  redo(): Rect | null {
    const e = this.redoStack.pop()
    if (!e) return null
    this.undoBytes += e.before.byteLength + e.after.byteLength
    this.writeRect(e.rect, e.after)
    this.undoStack.push(e)
    this.pendingRect = unite(this.pendingRect, e.rect)
    return e.rect
  }

  // The strokes still in the undo history, in the order they were made (the log saved with the picture). Undo and redo
  // move them with their picture, so the log always matches the pixels.
  get strokeLog(): StrokeRecord[] {
    return this.undoStack.map((e) => e.stroke).filter((s): s is StrokeRecord => s !== null)
  }

  // One byte per texel: 1 where the picture differs from the one the model came with, else 0. This is the edit mask.
  changedMask(): Uint8Array {
    const out = new Uint8Array(this.width * this.height)
    const a = this.pixels
    const b = this.original
    for (let i = 0; i < out.length; i++) {
      const k = i * 4
      if (a[k] !== b[k] || a[k + 1] !== b[k + 1] || a[k + 2] !== b[k + 2] || a[k + 3] !== b[k + 3]) out[i] = 1
    }
    return out
  }

  // Takes the rectangle changed since the last upload and sends it to the GPU. With no renderer (or no rectangle) the
  // whole texture is marked for upload.
  upload(renderer?: THREE.WebGLRenderer): void {
    const rect = this.pendingRect
    this.pendingRect = null
    if (!renderer || !rect) {
      this.texture.needsUpdate = true
      return
    }
    const data = crop(this.pixels, this.width, rect)
    const staging = new THREE.DataTexture(data, rect.w, rect.h, THREE.RGBAFormat)
    staging.needsUpdate = true
    renderer.copyTextureToTexture(staging, this.texture, new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(rect.w, rect.h, 1)), new THREE.Vector2(rect.x, rect.y))
    staging.dispose()
  }

  private writeRect(rect: Rect, bytes: Uint8Array): void {
    const rowBytes = rect.w * 4
    for (let row = 0; row < rect.h; row++) {
      const dst = ((rect.y + row) * this.width + rect.x) * 4
      this.pixels.set(bytes.subarray(row * rowBytes, (row + 1) * rowBytes), dst)
    }
  }

  private push(stack: Entry[], e: Entry): void {
    stack.push(e)
    this.undoBytes += e.before.byteLength + e.after.byteLength
  }

  // Drops the oldest strokes past the count or the byte limit (the newest stroke is always kept).
  private enforceLimits(): void {
    while (this.undoStack.length > 1 && (this.undoStack.length > UNDO_STROKES || this.undoBytes > UNDO_BYTES)) {
      const old = this.undoStack.shift()!
      this.undoBytes -= old.before.byteLength + old.after.byteLength
    }
  }
}

function unite(a: Rect | null, b: Rect): Rect {
  if (!a) return { ...b }
  const x0 = Math.min(a.x, b.x)
  const y0 = Math.min(a.y, b.y)
  const x1 = Math.max(a.x + a.w, b.x + b.w)
  const y1 = Math.max(a.y + a.h, b.y + b.h)
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

function crop(src: Uint8Array, width: number, rect: Rect): Uint8Array {
  const out = new Uint8Array(rect.w * rect.h * 4)
  const rowBytes = rect.w * 4
  for (let row = 0; row < rect.h; row++) {
    const s = ((rect.y + row) * width + rect.x) * 4
    out.set(src.subarray(s, s + rowBytes), row * rowBytes)
  }
  return out
}

function equalBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
  return true
}
