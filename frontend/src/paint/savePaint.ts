// Turns the painted pictures into what the save sends (WP-23, WP-32): each picture as PNG (glTF row order, as the layer
// holds it), its edit mask as PNG (white where the picture differs from the one the model came with), and the stroke
// logs. The shine, metal and glow pictures are sent only when they changed.
import type { PaintLayer, StrokeRecord } from './paintLayer'
import type { SaveMaps } from './paintSession'

// Each picture comes with its mask; a picture that did not change is left out.
export type PaintSaveBody = {
  picture_png?: string
  mask_png?: string
  strokes: StrokeRecord[]
  shine_png?: string
  shine_mask_png?: string
  metal_png?: string
  metal_mask_png?: string
  glow_png?: string
  glow_mask_png?: string
}

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

// The picture and mask of one layer, or null when nothing differs from the model's own picture.
function layerFiles(layer: PaintLayer): { picture: string; mask: string } | null {
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
  return { picture: pngOf(pixels, layer.width, layer.height), mask: pngOf(mask, layer.width, layer.height) }
}

// The save body for the saveable maps, or null when no picture differs from the model's own.
export function paintSaveBody(maps: SaveMaps): PaintSaveBody | null {
  const colour = layerFiles(maps.colour)
  const shine = maps.shine ? layerFiles(maps.shine) : null
  const metal = maps.metal ? layerFiles(maps.metal) : null
  const glow = maps.glow ? layerFiles(maps.glow) : null
  if (!colour && !shine && !metal && !glow) return null
  const body: PaintSaveBody = {
    strokes: [...maps.colour.strokeLog, ...(maps.shine?.strokeLog ?? []), ...(maps.metal?.strokeLog ?? []), ...(maps.glow?.strokeLog ?? [])],
  }
  if (colour) {
    body.picture_png = colour.picture
    body.mask_png = colour.mask
  }
  if (shine) {
    body.shine_png = shine.picture
    body.shine_mask_png = shine.mask
  }
  if (metal) {
    body.metal_png = metal.picture
    body.metal_mask_png = metal.mask
  }
  if (glow) {
    body.glow_png = glow.picture
    body.glow_mask_png = glow.mask
  }
  return body
}
