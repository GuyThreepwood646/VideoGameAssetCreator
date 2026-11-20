import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { MeshBVH } from 'three-mesh-bvh'
import { falloff, paintDab, type Brush, type Dab, type DabTarget } from '../src/paint/paintDab'
import { PaintLayer, UNDO_STROKES } from '../src/paint/paintLayer'
import { checkPaint, PaintSession, DEFAULT_PAINT } from '../src/paint/paintSession'
import { SurfacePicker, type ViewerModel } from '../src/viewerTool'

const SIZE = 64

function blankPicture(rgb: [number, number, number] = [255, 255, 255]): PaintLayer {
  return PaintLayer.blank(SIZE, SIZE, rgb)
}

// A flat square in z = 0 facing +z with UVs 0 to 1 (two triangles).
function square(): THREE.Mesh {
  return new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshStandardMaterial())
}

function targetsFor(...meshes: THREE.Mesh[]): DabTarget[] {
  return meshes.map((mesh) => ({ mesh, bvh: new MeshBVH(mesh.geometry) }))
}

function dabAt(mesh: THREE.Mesh, point: [number, number, number], normal: [number, number, number] = [0, 0, 1], faceOnly = false, face = 0): Dab {
  return { point: new THREE.Vector3(...point), normal: new THREE.Vector3(...normal).normalize(), hitMesh: mesh, face, faceOnly }
}

function brush(over: Partial<Brush> = {}): Brush {
  return { rgb: [0, 0, 0], radius: 0.25, hardness: 1, strength: 1, erase: false, ...over }
}

function px(layer: PaintLayer, x: number, y: number): number[] {
  const o = (y * layer.width + x) * 4
  return [layer.pixels[o], layer.pixels[o + 1], layer.pixels[o + 2]]
}

test('falloff: full inside the hard part, nothing at the radius, smooth between', () => {
  assert.equal(falloff(0, 1), 1)
  assert.equal(falloff(0.5, 1), 1)
  assert.equal(falloff(1, 0.5), 0)
  const a = falloff(0.3, 0)
  const b = falloff(0.6, 0)
  assert.ok(a > b && b > 0 && a < 1)
})

test('a dab paints inside the brush and leaves the rest of the picture alone', () => {
  const m = square()
  const layer = blankPicture()
  layer.beginStroke()
  const changed = paintDab(layer, targetsFor(m), dabAt(m, [0, 0, 0]), brush({ radius: 0.2 }))
  layer.endStroke()
  assert.ok(changed > 0)
  assert.deepEqual(px(layer, 32, 32), [0, 0, 0], 'centre painted')
  assert.deepEqual(px(layer, 0, 0), [255, 255, 255], 'corner untouched')
})

test('a dab on the back of a surface paints nothing', () => {
  const m = square()
  const layer = blankPicture()
  layer.beginStroke()
  const changed = paintDab(layer, targetsFor(m), dabAt(m, [0, 0, 0], [0, 0, -1]), brush())
  layer.endStroke()
  assert.equal(changed, 0)
  assert.deepEqual(px(layer, 32, 32), [255, 255, 255])
})

test('overlapping dabs in one stroke do not build past the stroke strength', () => {
  const m = square()
  const layer = blankPicture()
  layer.beginStroke()
  const b = brush({ strength: 0.5 })
  paintDab(layer, targetsFor(m), dabAt(m, [0, 0, 0]), b)
  paintDab(layer, targetsFor(m), dabAt(m, [0, 0, 0]), b)
  layer.endStroke()
  assert.equal(px(layer, 32, 32)[0], 128, 'half way from white to black, once')
})

test('erase goes back toward the original picture, not to white', () => {
  const m = square()
  const layer = PaintLayer.blank(SIZE, SIZE, [200, 40, 40])
  layer.beginStroke()
  paintDab(layer, targetsFor(m), dabAt(m, [0, 0, 0]), brush({ rgb: [0, 0, 255] }))
  layer.endStroke()
  assert.deepEqual(px(layer, 32, 32), [0, 0, 255])
  layer.beginStroke()
  paintDab(layer, targetsFor(m), dabAt(m, [0, 0, 0]), brush({ erase: true }))
  layer.endStroke()
  assert.deepEqual(px(layer, 32, 32), [200, 40, 40])
})

test('a seam: two UV islands that share a 3D edge both get the paint near that edge', () => {
  // Triangle A (uv in the lower left) and triangle B (uv in the upper right) share the 3D diagonal from (1,0,0) to (0,1,0).
  const pos = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0])
  const uv = new Float32Array([0, 0, 0.4, 0, 0, 0.4, 1, 0.6, 1, 1, 0.6, 1])
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3))
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2))
  const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial())
  m.updateMatrixWorld(true)
  const layer = blankPicture()
  layer.beginStroke()
  paintDab(layer, targetsFor(m), dabAt(m, [0.5, 0.5, 0]), brush({ radius: 0.3 }))
  layer.endStroke()
  let inA = 0
  let inB = 0
  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const u = (x + 0.5) / SIZE
      const v = (y + 0.5) / SIZE
      const dark = px(layer, x, y)[0] < 255
      if (dark && u + v < 0.4) inA++
      if (dark && u + v > 1.6) inB++
    }
  }
  assert.ok(inA > 0, `island A painted (${inA})`)
  assert.ok(inB > 0, `island B painted (${inB})`)
})

test('the layer undoes and redoes a stroke, and drops the oldest past the stroke limit', () => {
  const layer = blankPicture()
  layer.beginStroke()
  layer.pixels.fill(0)
  layer.touch(0, 0, SIZE, SIZE)
  assert.equal(layer.endStroke(), true)
  assert.equal(px(layer, 5, 5)[0], 0)
  layer.undo()
  assert.deepEqual(px(layer, 5, 5), [255, 255, 255])
  layer.redo()
  assert.equal(px(layer, 5, 5)[0], 0)
  for (let i = 0; i < UNDO_STROKES + 10; i++) {
    layer.beginStroke()
    layer.pixels[0] = (layer.pixels[0] + 1) & 255
    layer.touch(0, 0, 1, 1)
    layer.endStroke()
  }
  assert.equal(layer.undoCount, UNDO_STROKES)
})

test('a stroke that changes nothing adds no undo step', () => {
  const layer = blankPicture()
  layer.beginStroke()
  assert.equal(layer.endStroke(), false)
  assert.equal(layer.canUndo, false)
})

test('the undo byte limit keeps the stored strokes within 256 MB', () => {
  const big = PaintLayer.blank(1024, 1024, [255, 255, 255])
  for (let i = 0; i < 40; i++) {
    big.beginStroke()
    big.pixels[0] = (big.pixels[0] + 1) & 255
    big.touch(0, 0, 1024, 1024)
    big.endStroke()
  }
  assert.ok(big.undoBytesUsed <= 256 * 1024 * 1024, `bytes ${big.undoBytesUsed}`)
  assert.ok(big.undoCount < 40 && big.undoCount > 0, `count ${big.undoCount}`)
})

// A flat square as a model: one picture for it, a textured material and a picker over its meshes.
function modelOf(meshes: THREE.Mesh[], look: string | null = null): { model: ViewerModel; look: string | null } {
  const renderer = {} as THREE.WebGLRenderer
  const picker = new SurfacePicker(meshes)
  const model: ViewerModel = { meshes, renderer, picker, texturedMaterial: (mesh) => mesh.material as THREE.Material }
  return { model, look }
}

function texturedSquare(): THREE.Mesh {
  const m = square()
  const tex = new THREE.DataTexture(new Uint8Array(SIZE * SIZE * 4).fill(255), SIZE, SIZE)
  tex.flipY = false
  tex.needsUpdate = true
  ;(m.material as THREE.MeshStandardMaterial).map = tex
  m.updateMatrixWorld(true)
  return m
}

test('the session paints from a viewer hit and undo restores the picture', () => {
  const m = texturedSquare()
  const { model } = modelOf([m])
  const session = new PaintSession(model)
  const tool = session.tool(() => ({ ...DEFAULT_PAINT, radius: 0.2, rgb: [0, 0, 0], hardness: 1 }), {
    picked: () => {},
    changed: () => {},
    renderer: () => undefined,
    notice: () => {},
  })
  const hit = model.picker.pick(new THREE.Ray(new THREE.Vector3(0, 0, 5), new THREE.Vector3(0, 0, -1)))
  assert.ok(hit)
  tool.down?.(hit, {} as PointerEvent)
  tool.up?.(hit, {} as PointerEvent)
  const layer = session.layers[0]
  assert.deepEqual(px(layer, 32, 32), [0, 0, 0])
  assert.equal(session.canUndo, true)
  session.undo()
  assert.deepEqual(px(layer, 32, 32), [255, 255, 255])
  assert.equal(session.canRedo, true)
})

test('a mirrored stroke also paints the other side of the model', () => {
  const left = new THREE.Mesh(new THREE.PlaneGeometry(0.4, 1), new THREE.MeshStandardMaterial())
  left.position.set(-0.4, 0, 0)
  const right = new THREE.Mesh(new THREE.PlaneGeometry(0.4, 1), new THREE.MeshStandardMaterial())
  right.position.set(0.4, 0, 0)
  // One picture for both sides: the left side uses its left half, the right side its right half.
  const tex = new THREE.DataTexture(new Uint8Array(SIZE * SIZE * 4).fill(255), SIZE, SIZE)
  tex.flipY = false
  tex.needsUpdate = true
  ;(left.material as THREE.MeshStandardMaterial).map = tex
  ;(right.material as THREE.MeshStandardMaterial).map = tex
  const halve = (mesh: THREE.Mesh, offset: number) => {
    const uv = mesh.geometry.attributes.uv as THREE.BufferAttribute
    for (let i = 0; i < uv.count; i++) uv.setXY(i, uv.getX(i) * 0.5 + offset, uv.getY(i))
  }
  halve(left, 0)
  halve(right, 0.5)
  left.updateMatrixWorld(true)
  right.updateMatrixWorld(true)
  const { model } = modelOf([left, right])
  const session = new PaintSession(model)
  const tool = session.tool(() => ({ ...DEFAULT_PAINT, radius: 0.1, rgb: [0, 0, 0], hardness: 1, mirror: true }), {
    picked: () => {},
    changed: () => {},
    renderer: () => undefined,
    notice: () => {},
  })
  const hit = model.picker.pick(new THREE.Ray(new THREE.Vector3(0.4, 0, 5), new THREE.Vector3(0, 0, -1)))
  assert.ok(hit)
  tool.down?.(hit, {} as PointerEvent)
  tool.up?.(hit, {} as PointerEvent)
  const layer = session.layers[0]
  assert.equal(px(layer, 48, 32)[0], 0, 'the clicked side (right half of the picture) is painted')
  assert.equal(px(layer, 16, 32)[0], 0, 'the mirror side (left half of the picture) is painted too')
})

test('warnings: no picture layout blocks, Low-poly blocks, Retro 64 warns, mirrored islands warn', () => {
  const plain = square()
  plain.geometry.deleteAttribute('uv')
  const none = modelOf([plain]).model
  assert.ok(checkPaint(none, null).some((w) => w.blocks), 'no UVs blocks painting')

  const textured = texturedSquare()
  const { model } = modelOf([textured])
  assert.equal(checkPaint(model, null).filter((w) => w.blocks).length, 0)
  assert.ok(checkPaint(model, 'lowpoly_flat').some((w) => w.blocks), 'Low-poly blocks')
  assert.ok(checkPaint(model, 'retro64').some((w) => !w.blocks && /Retro 64/.test(w.text)), 'Retro 64 warns')

  // A mirrored island: the second triangle's UVs wind the other way.
  const pos = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0])
  const uv = new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 1, 0])
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3))
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2))
  const big = new THREE.Mesh(g, new THREE.MeshStandardMaterial())
  big.updateMatrixWorld(true)
  const mirrored = modelOf([big]).model
  assert.ok(checkPaint(mirrored, null).some((w) => /mirrored/.test(w.text)), 'mirrored UVs warn')
})

// WP-23: the save sends the changed texels and the stroke log, and undo moves the log with its picture.
const STROKE = { mode: 'paint' as const, rgb: [10, 200, 30] as [number, number, number], size_cm: 2, edge: 0.6, strength: 1, only_clicked_part: false, mirror: false }

function paintRect(layer: PaintLayer, stroke: typeof STROKE | null, x0: number, y0: number, x1: number, y1: number): void {
  layer.beginStroke(stroke)
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const o = (y * layer.width + x) * 4
      layer.pixels[o] = 10
      layer.pixels[o + 1] = 200
      layer.pixels[o + 2] = 30
    }
  }
  layer.touch(x0, y0, x1, y1)
  layer.endStroke()
}

test('the changed mask marks the texels that differ from the model picture, and nothing else', () => {
  const layer = blankPicture([255, 255, 255])
  assert.equal(layer.changedMask().reduce((n, v) => n + v, 0), 0)
  paintRect(layer, STROKE, 4, 4, 8, 6)
  const mask = layer.changedMask()
  assert.equal(mask.reduce((n, v) => n + v, 0), 4 * 2)
  assert.equal(mask[4 + 4 * SIZE], 1)
  assert.equal(mask[0], 0)
})

test('the stroke log follows the picture through undo and redo', () => {
  const layer = blankPicture([255, 255, 255])
  paintRect(layer, STROKE, 1, 1, 3, 3)
  paintRect(layer, { ...STROKE, mode: 'erase', rgb: [0, 0, 0] }, 5, 5, 7, 7)
  assert.deepEqual(layer.strokeLog.map((s) => s.mode), ['paint', 'erase'])
  layer.undo()
  assert.deepEqual(layer.strokeLog.map((s) => s.mode), ['paint'])
  layer.redo()
  assert.deepEqual(layer.strokeLog.map((s) => s.mode), ['paint', 'erase'])
})

test('a stroke with no settings still makes an undo step and no log entry', () => {
  const layer = blankPicture([255, 255, 255])
  paintRect(layer, null, 2, 2, 4, 4)
  assert.equal(layer.canUndo, true)
  assert.deepEqual(layer.strokeLog, [])
})

test('undo back to the model picture leaves no changed texels', () => {
  const layer = blankPicture([255, 255, 255])
  paintRect(layer, STROKE, 1, 1, 6, 6)
  layer.undo()
  assert.equal(layer.changedMask().reduce((n, v) => n + v, 0), 0)
})

test('a stroke is named in the undo and redo steps, and the mark tells whether the picture is the saved one', () => {
  const layer = blankPicture()
  const saved = layer.editMark
  assert.equal(layer.undoLabel, null)
  layer.beginStroke({ mode: 'paint', rgb: [255, 0, 0], size_cm: 5, edge: 0.5, strength: 1, only_clicked_part: false, mirror: false })
  layer.pixels.fill(0)
  layer.touch(0, 0, SIZE, SIZE)
  layer.endStroke()
  assert.equal(layer.undoLabel, 'a brush stroke')
  assert.notEqual(layer.editMark, saved, 'a new step makes the picture differ from the saved one')
  layer.undo()
  assert.equal(layer.redoLabel, 'a brush stroke')
  assert.equal(layer.editMark !== saved, true, 'undo alone still differs (the stack moved)')
  layer.redo()
  assert.equal(layer.undoLabel, 'a brush stroke')
  layer.beginStroke({ mode: 'erase', rgb: [0, 0, 0], size_cm: 5, edge: 0.5, strength: 1, only_clicked_part: false, mirror: false })
  layer.pixels.fill(7)
  layer.touch(0, 0, SIZE, SIZE)
  layer.endStroke()
  assert.equal(layer.undoLabel, 'an eraser stroke')
  layer.resetToOriginal()
  assert.equal(layer.undoLabel, "back to the model's own picture")
})

test('undo and redo steps are named for the layer that would change, and a new step clears the redo name', () => {
  const layer = blankPicture()
  layer.beginStroke({ mode: 'paint', rgb: [1, 2, 3], size_cm: 5, edge: 0.5, strength: 1, only_clicked_part: false, mirror: true })
  layer.pixels.fill(0)
  layer.touch(0, 0, SIZE, SIZE)
  layer.endStroke()
  assert.equal(layer.undoLabel, 'a mirrored brush stroke')
  layer.undo()
  assert.equal(layer.redoLabel, 'a mirrored brush stroke')
  layer.beginStroke()
  layer.pixels[0] = 9
  layer.touch(0, 0, 1, 1)
  layer.endStroke()
  assert.equal(layer.redoLabel, null)
})
