// Painting on the 3D preview (WP-22): the session that owns the picture layers of one loaded model, and the viewer tool
// that turns pointer spots into brush dabs. Painting is for this page only: nothing is saved yet (WP-23 does that).
import * as THREE from 'three'
import type { SurfaceHit, ViewerModel, ViewerTool } from '../viewerTool'
import { fillRegion, paintDab, smudgeDab, type Brush, type Dab, type DabTarget } from './paintDab'
import { PaintLayer, type StrokeRecord } from './paintLayer'

// Brush, Eraser and Eyedropper (pick) as before; Smudge drags the colour under the brush; Fill floods the connected
// surface that matches the clicked colour (WP-32).
export type PaintMode = 'paint' | 'erase' | 'pick' | 'smudge' | 'fill'
export type PaintSettings = {
  mode: PaintMode
  rgb: [number, number, number]
  radius: number // metres on the model
  hardness: number // 0 soft to 1 hard
  strength: number // 0 to 1 (for smudge: how far the colour drags)
  match: number // 0 to 1: how close a colour must be to the clicked one for Fill
  faceOnly: boolean
  mirror: boolean
}
export const DEFAULT_PAINT: PaintSettings = { mode: 'paint', rgb: [178, 58, 58], radius: 0.02, hardness: 0.6, strength: 1, match: 0.25, faceOnly: false, mirror: false }
// Brush sizes offered, in metres (the slider shows centimetres).
// What a stroke was set to, for the stroke log saved with the picture (WP-23). Sizes in centimetres.
export function strokeRecordOf(s: PaintSettings): StrokeRecord {
  return {
    mode: s.mode === 'erase' ? 'erase' : s.mode === 'smudge' ? 'smudge' : s.mode === 'fill' ? 'fill' : 'paint',
    rgb: [...s.rgb] as [number, number, number],
    size_cm: Math.round(s.radius * 1000) / 10,
    edge: Math.round(s.hardness * 100) / 100,
    strength: Math.round(s.strength * 100) / 100,
    only_clicked_part: s.faceOnly,
    mirror: s.mirror,
  }
}

export const BRUSH_MIN = 0.002
export const BRUSH_MAX = 0.2
const NEW_PICTURE = 512

export type PaintWarning = { text: string; blocks: boolean }

type Spot = { point: THREE.Vector3; normal: THREE.Vector3; mesh: THREE.Mesh; face: number }
type ToolHooks = {
  picked: (rgb: [number, number, number]) => void
  changed: () => void
  renderer: () => THREE.WebGLRenderer | undefined
  notice: (text: string) => void
}

const _v = new THREE.Vector3()
const _w = new THREE.Vector3()
const _inv = new THREE.Matrix4()
const _ea = new THREE.Vector3()
const _eb = new THREE.Vector3()
const _ec = new THREE.Vector3()

function firstMaterial(m: THREE.Material | THREE.Material[] | undefined | null): THREE.MeshStandardMaterial | null {
  const one = Array.isArray(m) ? m[0] : m
  return one && 'map' in one ? (one as THREE.MeshStandardMaterial) : null
}

// The picture's sRGB bytes. A DataTexture already holds them; a loaded image is drawn onto a canvas. Rows are put in
// glTF order (row 0 at the top, v = 0), so the buffer lines up with a texture that is not flipped.
export function readPicture(texture: THREE.Texture): { width: number; height: number; pixels: Uint8Array } {
  const img = texture.image as { width: number; height: number; data?: ArrayLike<number> }
  const w = img.width
  const h = img.height
  let pixels: Uint8Array
  if (img.data) {
    pixels = Uint8Array.from(img.data as ArrayLike<number>)
    if (texture.flipY) pixels = flipRows(pixels, w, h)
  } else {
    const canvas = document.createElement('canvas')
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) throw new Error('no 2D canvas for the picture')
    if (texture.flipY) {
      ctx.translate(0, h)
      ctx.scale(1, -1)
    }
    ctx.drawImage(texture.image as CanvasImageSource, 0, 0, w, h)
    pixels = new Uint8Array(ctx.getImageData(0, 0, w, h).data.buffer)
  }
  return { width: w, height: h, pixels }
}

function flipRows(px: Uint8Array, w: number, h: number): Uint8Array {
  const out = new Uint8Array(px.length)
  const row = w * 4
  for (let y = 0; y < h; y++) out.set(px.subarray(y * row, (y + 1) * row), (h - 1 - y) * row)
  return out
}

// Checks that need no change to the model: the warnings the panel shows before painting starts.
export function checkPaint(model: ViewerModel, look: string | null): PaintWarning[] {
  const out: PaintWarning[] = []
  let paintable = 0
  let secondSet = false
  let posUv = 0
  let negUv = 0
  for (const mesh of model.meshes) {
    const uv = mesh.geometry?.attributes.uv as THREE.BufferAttribute | undefined
    const mat = firstMaterial(model.texturedMaterial(mesh))
    if (!uv || !mat) continue
    if (mat.map && (mat.map.channel ?? 0) !== 0) {
      secondSet = true
      continue
    }
    paintable++
    const idx = mesh.geometry.index
    const count = idx ? idx.count : uv.count
    for (let f = 0; f < count / 3; f++) {
      const a = idx ? idx.getX(f * 3) : f * 3
      const b = idx ? idx.getX(f * 3 + 1) : f * 3 + 1
      const c = idx ? idx.getX(f * 3 + 2) : f * 3 + 2
      const area = (uv.getX(b) - uv.getX(a)) * (uv.getY(c) - uv.getY(a)) - (uv.getX(c) - uv.getX(a)) * (uv.getY(b) - uv.getY(a))
      if (area > 0) posUv += area
      else if (area < 0) negUv -= area
    }
  }
  if (paintable === 0) out.push({ text: 'This model has no picture layout to paint on, so painting is off.', blocks: true })
  if (secondSet) out.push({ text: 'Some parts use a second picture layout. Those parts cannot be painted.', blocks: false })
  const total = posUv + negUv
  if (total > 0 && Math.min(posUv, negUv) / total > 0.005) {
    out.push({ text: 'Some parts share the picture mirrored (the left and right sides use one area). A stroke on one side can also show on the other side.', blocks: false })
  }
  if (look === 'lowpoly_flat') {
    out.push({ text: 'The Low-poly look gives every triangle one colour. Painting would break that look, so painting is off for this version.', blocks: true })
  }
  if (look === 'retro64') {
    out.push({ text: 'The Retro 64 look uses a small picture with a few colours. Painted colours are not limited to those colours.', blocks: false })
  }
  return out
}

// The picture layers of one model: a blank picture for each textured part that has none, the model's own picture
// otherwise. Creating a session changes the materials of the model (it gives blank parts a picture).
export class PaintSession {
  readonly layers: PaintLayer[] = []
  private readonly layerByMesh = new Map<THREE.Mesh, PaintLayer>()
  private readonly targets = new Map<PaintLayer, DabTarget[]>()
  private readonly mirrorX: number
  private lastLayer: PaintLayer | null = null

  constructor(model: ViewerModel) {
    const layerOf = new Map<THREE.Texture, PaintLayer>()
    for (const mesh of model.meshes) {
      const uv = mesh.geometry?.attributes.uv
      const mat = firstMaterial(model.texturedMaterial(mesh))
      if (!uv || !mat) continue
      if (mat.map && (mat.map.channel ?? 0) !== 0) continue
      let layer: PaintLayer | undefined
      if (mat.map) {
        layer = layerOf.get(mat.map)
        if (!layer) {
          // The painted picture replaces the model's texture on the material, with the same wrap settings.
          const original = mat.map
          const pic = readPicture(original)
          layer = new PaintLayer(pic.width, pic.height, pic.pixels)
          layer.texture.wrapS = original.wrapS
          layer.texture.wrapT = original.wrapT
          mat.map = layer.texture
          mat.needsUpdate = true
          layerOf.set(original, layer)
          layerOf.set(layer.texture, layer)
        }
        if (mat.map !== layer.texture) {
          mat.map = layer.texture
          mat.needsUpdate = true
        }
      } else {
        const rgb = { r: 1, g: 1, b: 1 }
        mat.color.getRGB(rgb, THREE.SRGBColorSpace)
        layer = PaintLayer.blank(NEW_PICTURE, NEW_PICTURE, [Math.round(rgb.r * 255), Math.round(rgb.g * 255), Math.round(rgb.b * 255)])
        mat.map = layer.texture
        mat.needsUpdate = true
        layerOf.set(layer.texture, layer)
      }
      if (!this.layers.includes(layer)) this.layers.push(layer)
      this.layerByMesh.set(mesh, layer)
      const target: DabTarget = { mesh, bvh: model.picker.bvhFor(mesh.geometry) }
      const list = this.targets.get(layer) ?? []
      list.push(target)
      this.targets.set(layer, list)
    }
    let minX = Infinity
    let maxX = -Infinity
    for (const mesh of model.meshes) {
      mesh.updateWorldMatrix(true, false)
      const box = new THREE.Box3().setFromObject(mesh)
      if (!box.isEmpty()) {
        minX = Math.min(minX, box.min.x)
        maxX = Math.max(maxX, box.max.x)
      }
    }
    this.mirrorX = Number.isFinite(minX) ? (minX + maxX) / 2 : 0
  }

  get canUndo(): boolean {
    return this.layers.some((l) => l.canUndo)
  }
  get canRedo(): boolean {
    return this.layers.some((l) => l.canRedo)
  }
  get undoCount(): number {
    return this.layers.reduce((n, l) => n + l.undoCount, 0)
  }
  // The step names for the next undo and the next redo (the layer that would change), or null.
  get undoLabel(): string | null {
    const l = this.lastLayer?.canUndo ? this.lastLayer : this.layers.find((x) => x.canUndo)
    return l?.undoLabel ?? null
  }
  get redoLabel(): string | null {
    const l = this.lastLayer?.canRedo ? this.lastLayer : this.layers.find((x) => x.canRedo)
    return l?.redoLabel ?? null
  }
  // Changes with every new step, undo or redo; equal to the mark taken when the picture was saved means nothing is unsaved.
  get editMark(): string {
    return this.layers.map((l) => l.editMark).join("|")
  }
  get undoBytes(): number {
    return this.layers.reduce((n, l) => n + l.undoBytesUsed, 0)
  }

  // The one picture a save can send: the model's own picture (one layer). Null when the model has parts with pictures
  // of their own or blank parts that the session added; those cannot be saved yet.
  saveableLayer(): PaintLayer | null {
    return this.layers.length === 1 ? this.layers[0] : null
  }

  undo(renderer?: THREE.WebGLRenderer): boolean {
    const layer = this.lastLayer?.canUndo ? this.lastLayer : this.layers.find((l) => l.canUndo)
    if (!layer || !layer.undo()) return false
    layer.upload(renderer)
    this.lastLayer = layer
    return true
  }

  redo(renderer?: THREE.WebGLRenderer): boolean {
    const layer = this.lastLayer?.canRedo ? this.lastLayer : this.layers.find((l) => l.canRedo)
    if (!layer || !layer.redo()) return false
    layer.upload(renderer)
    this.lastLayer = layer
    return true
  }

  // Sets every picture back to the model's own. Undoable.
  resetAll(renderer?: THREE.WebGLRenderer): void {
    for (const layer of this.layers) {
      layer.resetToOriginal()
      layer.upload(renderer)
    }
  }

  // The average colour of the picture around a spot (a 5 by 5 block), as sRGB bytes.
  sampleColour(hit: SurfaceHit): [number, number, number] | null {
    const layer = this.layerByMesh.get(hit.mesh)
    if (!layer || !hit.uv) return null
    const cx = Math.floor(hit.uv.x * layer.width)
    const cy = Math.floor(hit.uv.y * layer.height)
    let r = 0
    let g = 0
    let b = 0
    let n = 0
    for (let dy = -2; dy <= 2; dy++) {
      for (let dx = -2; dx <= 2; dx++) {
        const x = ((cx + dx) % layer.width + layer.width) % layer.width
        const y = ((cy + dy) % layer.height + layer.height) % layer.height
        const o = (y * layer.width + x) * 4
        r += layer.pixels[o]
        g += layer.pixels[o + 1]
        b += layer.pixels[o + 2]
        n++
      }
    }
    return [Math.round(r / n), Math.round(g / n), Math.round(b / n)]
  }

  // The colour of the one picture texel under a surface hit (sRGB bytes), or null when the hit has no picture position.
  private colourAt(layer: PaintLayer, hit: SurfaceHit): [number, number, number] | null {
    if (!hit.uv) return null
    const x = ((Math.floor(hit.uv.x * layer.width) % layer.width) + layer.width) % layer.width
    const y = ((Math.floor(hit.uv.y * layer.height) % layer.height) + layer.height) % layer.height
    const o = (y * layer.width + x) * 4
    return [layer.pixels[o], layer.pixels[o + 1], layer.pixels[o + 2]]
  }

  // The viewer tool for this session. Keep one tool object per session; read the settings through getSettings.
  tool(getSettings: () => PaintSettings, hooks: ToolHooks): ViewerTool {
    let active: PaintLayer | null = null
    let last: THREE.Vector3 | null = null
    // Clicked-face-only: the triangle the stroke began on (and its mirror image, for a mirrored stroke).
    let strokeFace: { mesh: THREE.Mesh; face: number } | null = null
    // Smudge: the colour the stroke is carrying (null until its first dab).
    const carry: { rgb: [number, number, number] | null } = { rgb: null }
    const brushOf = (s: PaintSettings): Brush => ({ rgb: s.rgb, radius: s.radius, hardness: s.hardness, strength: s.strength, erase: s.mode === 'erase' })
    // One dab at a spot. With faceOnly, only the stroke's triangle is painted (faceSpot), and its mirror image for the mirror side.
    const paintAt = (layer: PaintLayer, spot: Spot, s: PaintSettings, faceSpot: { mesh: THREE.Mesh; face: number } | null) => {
      const targets = this.targets.get(layer) ?? []
      const dab: Dab = {
        point: spot.point,
        normal: spot.normal,
        hitMesh: faceSpot?.mesh ?? spot.mesh,
        face: faceSpot?.face ?? spot.face,
        faceOnly: faceSpot !== null,
      }
      const dabOnce = (d: Dab) => (s.mode === 'smudge' ? smudgeDab(layer, targets, d, brushOf(s), carry) : paintDab(layer, targets, d, brushOf(s)))
      dabOnce(dab)
      if (s.mirror) {
        const mirror = this.mirrored(spot, layer)
        if (mirror && mirror.point.distanceTo(spot.point) > s.radius * 0.5) {
          const mirrorFace = faceSpot ? { mesh: mirror.mesh, face: mirror.face } : null
          dabOnce({ ...dab, point: mirror.point, normal: mirror.normal, hitMesh: mirror.mesh, face: mirror.face, faceOnly: mirrorFace !== null })
        }
      }
    }
    const beginStroke = (hit: SurfaceHit, s: PaintSettings): PaintLayer | null => {
      if (active) return active
      const layer = this.layerByMesh.get(hit.mesh) ?? null
      if (!layer) {
        hooks.notice('That part has no picture to paint on.')
        return null
      }
      layer.beginStroke(strokeRecordOf(s))
      active = layer
      strokeFace = { mesh: hit.mesh, face: hit.face }
      carry.rgb = null
      return layer
    }
    const flush = () => {
      if (active) active.upload(hooks.renderer())
    }
    const finish = () => {
      if (!active) return
      const layer = active
      active = null
      last = null
      strokeFace = null
      if (layer.endStroke()) {
        this.lastLayer = layer
        hooks.changed()
      }
    }
    return {
      id: 'paint',
      cursor: 'crosshair',
      down: (hit) => {
        if (!hit) return
        const s = getSettings()
        if (s.mode === 'pick') {
          const layer = this.layerByMesh.get(hit.mesh)
          const rgb = this.sampleColour(hit)
          if (layer && rgb) hooks.picked(rgb)
          return
        }
        if (s.mode === 'fill') {
          // One click is one fill and one undo step. The seed is the colour under the click.
          const layer = beginStroke(hit, s)
          if (!layer) return
          const seed = this.colourAt(layer, hit)
          if (seed) fillRegion(layer, { mesh: hit.mesh, face: hit.face, rgb: seed }, { rgb: s.rgb, tolerance: s.match, strength: s.strength })
          flush()
          finish()
          return
        }
        const layer = beginStroke(hit, s)
        if (!layer) return
        last = hit.point.clone()
        paintAt(layer, hit, s, s.faceOnly ? strokeFace : null)
        flush()
      },
      drag: (hit) => {
        if (!active || !hit || !last) return
        const s = getSettings()
        // Dabs every quarter radius along the path, each pulled back onto the surface.
        const step = Math.max(s.radius * 0.25, 0.001)
        const n = Math.max(1, Math.ceil(last.distanceTo(hit.point) / step))
        for (let k = 1; k <= n; k++) {
          const p = _v.copy(last).lerp(hit.point, k / n)
          const spot: Spot | null = k === n ? { point: hit.point.clone(), normal: hit.normal.clone(), mesh: hit.mesh, face: hit.face } : this.nearestSurface(p, active)
          if (spot) paintAt(active, spot, s, s.faceOnly ? strokeFace : null)
        }
        last = hit.point.clone()
        flush()
      },
      up: () => finish(),
      cancel: () => finish(),
    }
  }

  // The nearest surface of this layer's parts to a world point.
  private nearestSurface(point: THREE.Vector3, layer: PaintLayer): Spot | null {
    let best: (Spot & { d: number }) | null = null
    for (const t of this.targets.get(layer) ?? []) {
      const m = t.mesh.matrixWorld
      _inv.copy(m).invert()
      const local = _w.copy(point).applyMatrix4(_inv)
      const hit = t.bvh.closestPointToPoint(local)
      if (!hit || hit.faceIndex == null) continue
      const world = hit.point.clone().applyMatrix4(m)
      const d = world.distanceTo(point)
      if (!best || d < best.d) best = { point: world, normal: this.faceNormal(t.mesh, hit.faceIndex), mesh: t.mesh, face: hit.faceIndex, d }
    }
    return best ? { point: best.point, normal: best.normal, mesh: best.mesh, face: best.face } : null
  }

  // The spot the mirror side of a dab lands on (mirrored across the model's middle, left to right).
  private mirrored(spot: Spot, layer: PaintLayer): Spot | null {
    const p = new THREE.Vector3(2 * this.mirrorX - spot.point.x, spot.point.y, spot.point.z)
    return this.nearestSurface(p, layer)
  }

  private faceNormal(mesh: THREE.Mesh, face: number): THREE.Vector3 {
    const geo = mesh.geometry
    const pos = geo.attributes.position as THREE.BufferAttribute
    const idx = geo.index
    const ia = idx ? idx.getX(face * 3) : face * 3
    const ib = idx ? idx.getX(face * 3 + 1) : face * 3 + 1
    const ic = idx ? idx.getX(face * 3 + 2) : face * 3 + 2
    const m = mesh.matrixWorld
    _ea.fromBufferAttribute(pos, ia).applyMatrix4(m)
    _eb.fromBufferAttribute(pos, ib).applyMatrix4(m)
    _ec.fromBufferAttribute(pos, ic).applyMatrix4(m)
    const n = new THREE.Vector3().subVectors(_eb, _ea).cross(_ec.clone().sub(_ea))
    return n.lengthSq() > 0 ? n.normalize() : n
  }
}
