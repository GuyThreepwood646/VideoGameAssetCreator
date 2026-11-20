// Turns the painted picture into what the save sends (WP-23): the picture as PNG (glTF row order, as the layer holds it),
// the edit mask as PNG (white where the picture differs from the one the model came with), and the stroke log.
import type { PaintLayer, StrokeRecord } from './paintLayer'

export type PaintSaveBody = { picture_png: string; mask_png: string; strokes: StrokeRecord[] }

// A PNG of RGBA bytes, as base64 text (no data: prefix).
function pngOf(bytes: Uint8ClampedArray<ArrayBuffer>, width: number, height: number): string {
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('This browser cannot make the picture file for the save.')
  ctx.putImageData(new ImageData(bytes, width, height), 0, 0)
  return canvas.toDataURL('image/png').split(',', 2)[1]
}

// The save body for a layer, or null when nothing differs from the model's own picture.
export function paintSaveBody(layer: PaintLayer): PaintSaveBody | null {
  const changed = layer.changedMask()
  const n = layer.width * layer.height
  const mask = new Uint8ClampedArray(n * 4)
  let any = false
  for (let i = 0; i < n; i++) {
    if (!changed[i]) {
      mask[i * 4 + 3] = 255
      continue
    }
    any = true
    mask.fill(255, i * 4, i * 4 + 4)
  }
  if (!any) return null
  // A copy, so the bytes are on a plain ArrayBuffer (ImageData takes no shared buffer).
  const pixels = new Uint8ClampedArray(layer.pixels)
  return {
    picture_png: pngOf(pixels, layer.width, layer.height),
    mask_png: pngOf(mask, layer.width, layer.height),
    strokes: layer.strokeLog,
  }
}
