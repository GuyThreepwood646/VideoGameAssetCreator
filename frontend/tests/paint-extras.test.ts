// Smudge and Fill (WP-32): the colour drag along a stroke, the one-surface fill, and their undo and redo steps.
import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { MeshBVH } from 'three-mesh-bvh'
import { fillRegion, paintDab, smudgeDab, type Brush, type Dab, type DabTarget } from '../src/paint/paintDab'
import { PaintLayer, type StrokeRecord } from '../src/paint/paintLayer'
import { DEFAULT_PAINT, modelSaveable, PaintSession, paintableMaps, targetRgb } from '../src/paint/paintSession'
import { SurfacePicker, type ViewerModel } from '../src/viewerTool'

const SIZE = 64

// A picture with the left half red and the right half white.
function splitPicture(): PaintLayer {
  const px = new Uint8Array(SIZE * SIZE * 4)
  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const o = (y * SIZE + x) * 4
      const red = x < SIZE / 2
      px[o] = 255
      px[o + 1] = red ? 0 : 255
      px[o + 2] = red ? 0 : 255
      px[o + 3] = 255
    }
  }
  return new PaintLayer(SIZE, SIZE, px)
}

function uniform(rgb: [number, number, number]): PaintLayer {
  return PaintLayer.blank(SIZE, SIZE, rgb)
}

// The pixel at (x, y) of the layer, as [r, g, b].
function at(layer: PaintLayer, x: number, y: number): number[] {
  const o = (y * SIZE + x) * 4
  return [layer.pixels[o], layer.pixels[o + 1], layer.pixels[o + 2]]
}

const STROKE: StrokeRecord = { mode: 'smudge', rgb: [0, 0, 0], size_cm: 20, edge: 0.5, strength: 0.8, only_clicked_part: false, mirror: false }

// A flat unit square in z = 0 facing +z, with UVs 0 to 1 (so world x = -0.5 .. 0.5 maps to u = 0 .. 1).
function square(): THREE.Mesh {
  return new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshStandardMaterial())
}

function targetsFor(mesh: THREE.Mesh): DabTarget[] {
  return [{ mesh, bvh: new MeshBVH(mesh.geometry) }]
}

function dabAt(mesh: THREE.Mesh, x: number): Dab {
  return { point: new THREE.Vector3(x, 0, 0), normal: new THREE.Vector3(0, 0, 1), hitMesh: mesh, face: 0, faceOnly: false }
}

test('smudge drags the colour under the brush along the stroke and leaves the far side alone', () => {
  const layer = splitPicture()
  const m = square()
  const targets = targetsFor(m)
  const brush: Brush = { rgb: [0, 0, 0], radius: 0.1, hardness: 0.5, strength: 0.8, erase: false }
  const before = layer.pixels.slice()
  layer.beginStroke(STROKE)
  const carry: { rgb: [number, number, number] | null } = { rgb: null }
  // Dabs move from the red side (world x -0.2) across the seam at x = 0 to x = 0.3.
  for (let x = -0.2; x <= 0.3 + 1e-9; x += 0.02) smudgeDab(layer, targets, dabAt(m, x), brush, carry)
  assert.ok(layer.endStroke(), 'the smudge changed the picture')
  // Just right of the seam (pixel 34 is world x about 0.04) was white; the red has pulled into it, so green drops.
  const seam = at(layer, 34, 32)
  assert.ok(seam[1] < 255 && seam[2] < 255, `just past the seam is dragged toward red (${seam})`)
  assert.ok(seam[0] === 255, 'red stays full where red was pulled in')
  // Far right, outside the brush's reach (the last dab is at 0.3, the radius 0.1), stays white.
  assert.deepEqual(at(layer, 60, 32), [255, 255, 255], 'far right is untouched')
  assert.notDeepEqual(layer.pixels, before)
})

test('a smudge stroke is one undo step named for the smudge, and redo brings it back', () => {
  const layer = splitPicture()
  const m = square()
  const targets = targetsFor(m)
  const brush: Brush = { rgb: [0, 0, 0], radius: 0.1, hardness: 0.5, strength: 0.8, erase: false }
  const original = layer.pixels.slice()
  layer.beginStroke(STROKE)
  const carry: { rgb: [number, number, number] | null } = { rgb: null }
  for (let x = -0.2; x <= 0.3 + 1e-9; x += 0.02) smudgeDab(layer, targets, dabAt(m, x), brush, carry)
  layer.endStroke()
  const smudged = layer.pixels.slice()
  assert.equal(layer.undoCount, 1)
  assert.equal(layer.undoLabel, 'a smudge stroke')
  assert.deepEqual(layer.strokeLog.map((s) => s.mode), ['smudge'])
  layer.undo()
  assert.deepEqual(layer.pixels, original, 'undo restores the picture')
  layer.redo()
  assert.deepEqual(layer.pixels, smudged, 'redo brings the smudge back')
})

test('a smudge over a flat colour changes nothing, so it adds no undo step', () => {
  const layer = uniform([90, 120, 200])
  const m = square()
  const brush: Brush = { rgb: [0, 0, 0], radius: 0.1, hardness: 0.5, strength: 0.8, erase: false }
  layer.beginStroke(STROKE)
  const carry: { rgb: [number, number, number] | null } = { rgb: null }
  for (let x = -0.2; x <= 0.2; x += 0.05) smudgeDab(layer, targetsFor(m), dabAt(m, x), brush, carry)
  assert.equal(layer.endStroke(), false)
  assert.equal(layer.canUndo, false)
})

test('fill covers the connected surface where the colour matches, and nothing else', () => {
  const layer = splitPicture()
  const m = square()
  // Click the red side: world x -0.3 is inside the square's left triangles, on the red half.
  const seedFace = findFaceAt(m, -0.3, 0)
  const seedRgb: [number, number, number] = [255, 0, 0]
  layer.beginStroke({ ...STROKE, mode: 'fill' })
  const changed = fillRegion(layer, { mesh: m, face: seedFace, rgb: seedRgb }, { rgb: [0, 0, 255], tolerance: 0.1, strength: 1 })
  assert.ok(layer.endStroke())
  assert.ok(changed > 0)
  assert.deepEqual(at(layer, 12, 32), [0, 0, 255], 'the red side is filled blue')
  assert.deepEqual(at(layer, 50, 32), [255, 255, 255], 'the white side is not close enough, so it stays white')
  assert.equal(layer.undoLabel, 'a fill')
  layer.undo()
  assert.deepEqual(at(layer, 12, 32), [255, 0, 0], 'undo restores the red')
  layer.redo()
  assert.deepEqual(at(layer, 12, 32), [0, 0, 255], 'redo fills again')
})

test('fill does not jump to a part that has the same picture but is not joined to the clicked one', () => {
  // Two triangles that share a picture area edge in UV but have separate vertices in 3D (not joined).
  const pos = new Float32Array([
    -0.5, -0.5, 0, 0, -0.5, 0, 0, 0.5, 0, // A
    0.2, -0.5, 0, 0.5, -0.5, 0, 0.5, 0.5, 0, // B
  ])
  const uv = new Float32Array([0, 0, 0.5, 0, 0.5, 1, 0.5, 0, 1, 0, 1, 1])
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
  geo.setAttribute('uv', new THREE.BufferAttribute(uv, 2))
  const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial())
  const layer = uniform([255, 0, 0]) // all red, so both triangles match the seed
  layer.beginStroke({ ...STROKE, mode: 'fill' })
  fillRegion(layer, { mesh: m, face: 0, rgb: [255, 0, 0] }, { rgb: [0, 0, 255], tolerance: 0.1, strength: 1 })
  layer.endStroke()
  assert.deepEqual(at(layer, 25, 32), [0, 0, 255], 'the clicked triangle is filled')
  assert.deepEqual(at(layer, 51, 32), [255, 0, 0], 'the separate triangle keeps its red')
})

test('a brush dab still paints the same way after the smudge and fill code was added', () => {
  const layer = uniform([255, 255, 255])
  const m = square()
  layer.beginStroke({ ...STROKE, mode: 'paint' })
  const n = paintDab(layer, targetsFor(m), dabAt(m, 0), { rgb: [0, 0, 0], radius: 0.2, hardness: 1, strength: 1, erase: false })
  assert.ok(n > 0)
  assert.ok(layer.endStroke())
  assert.deepEqual(at(layer, 32, 32), [0, 0, 0])
})

test('shine and metal paint their own channel of the metallic-roughness picture, and a save sends them', () => {
  const m = square()
  const mr = new THREE.DataTexture(new Uint8Array(SIZE * SIZE * 4), SIZE, SIZE)
  for (let o = 0; o < SIZE * SIZE * 4; o += 4) {
    mr.image.data[o] = 255 // occlusion, left alone
    mr.image.data[o + 1] = 200 // green: roughness
    mr.image.data[o + 2] = 0 // blue: metal
    mr.image.data[o + 3] = 255
  }
  mr.flipY = false
  mr.needsUpdate = true
  const colour = new THREE.DataTexture(new Uint8Array(SIZE * SIZE * 4).fill(255), SIZE, SIZE)
  colour.flipY = false
  colour.needsUpdate = true
  const mat = m.material as THREE.MeshStandardMaterial
  mat.map = colour
  mat.roughnessMap = mr
  mat.metalnessMap = mr
  m.updateMatrixWorld(true)
  const model = { meshes: [m], renderer: {} as THREE.WebGLRenderer, picker: new SurfacePicker([m]), texturedMaterial: (mesh: THREE.Mesh) => mesh.material } as unknown as ViewerModel
  assert.deepEqual(paintableMaps(model), { shine: true, metal: true })
  const session = new PaintSession(model)
  const hooks = { picked: () => {}, changed: () => {}, renderer: () => undefined, notice: () => {} }
  const tool = session.tool(() => ({ ...DEFAULT_PAINT, target: 'shine', shine: 1, radius: 0.2, hardness: 1, strength: 1 }), hooks)
  const hit = model.picker.pick(new THREE.Ray(new THREE.Vector3(0, 0, 5), new THREE.Vector3(0, 0, -1)))
  assert.ok(hit)
  tool.down?.(hit, {} as PointerEvent)
  tool.up?.(hit, {} as PointerEvent)
  const maps = session.saveableMaps()
  assert.ok(maps && maps.shine && maps.metal, 'the shine and metal pictures are saveable')
  // Shine 1 (shiny) is roughness 0 at the spot; the colour and the metal are untouched there.
  assert.deepEqual(px(maps.shine!, 32, 32), [0, 0, 0])
  assert.deepEqual(px(maps.colour, 32, 32), [255, 255, 255])
  assert.equal(maps.metal!.changedMask().some((v) => v === 1), false, 'metal changed nothing')
  assert.equal(session.undoLabel, 'a shine brush stroke')
  // A metal stroke is its own undo step, and it does not touch the shine.
  const metalSettings = { ...DEFAULT_PAINT, target: 'metal' as const, metal: 1, radius: 0.2, hardness: 1, strength: 1 }
  const tool2 = session.tool(() => metalSettings, hooks)
  tool2.down?.(hit, {} as PointerEvent)
  tool2.up?.(hit, {} as PointerEvent)
  assert.deepEqual(px(maps.metal!, 32, 32), [255, 255, 255])
  assert.deepEqual(px(maps.shine!, 32, 32), [0, 0, 0])
  assert.equal(session.undoLabel, 'a metal brush stroke')
  session.undo()
  assert.deepEqual(px(maps.metal!, 32, 32), [0, 0, 0], 'undo takes the metal back')
  assert.deepEqual(px(maps.shine!, 32, 32), [0, 0, 0])
})

test('a shine setting maps to roughness (matt is high roughness), and metal maps straight through', () => {
  assert.deepEqual(targetRgb({ ...DEFAULT_PAINT, target: 'shine', shine: 0 }), [255, 255, 255])
  assert.deepEqual(targetRgb({ ...DEFAULT_PAINT, target: 'shine', shine: 1 }), [0, 0, 0])
  assert.deepEqual(targetRgb({ ...DEFAULT_PAINT, target: 'metal', metal: 1 }), [255, 255, 255])
  assert.deepEqual(targetRgb({ ...DEFAULT_PAINT, target: 'colour', rgb: [1, 2, 3] }), [1, 2, 3])
})

test('undo and redo follow the order of the steps across the colour and shine pictures', () => {
  const m = square()
  const mr = new THREE.DataTexture(new Uint8Array(SIZE * SIZE * 4), SIZE, SIZE)
  for (let o = 0; o < SIZE * SIZE * 4; o += 4) {
    mr.image.data[o] = 255
    mr.image.data[o + 1] = 200
    mr.image.data[o + 2] = 0
    mr.image.data[o + 3] = 255
  }
  mr.flipY = false
  mr.needsUpdate = true
  const colour = new THREE.DataTexture(new Uint8Array(SIZE * SIZE * 4).fill(255), SIZE, SIZE)
  colour.flipY = false
  colour.needsUpdate = true
  const mat = m.material as THREE.MeshStandardMaterial
  mat.map = colour
  mat.roughnessMap = mr
  m.updateMatrixWorld(true)
  const model = { meshes: [m], renderer: {} as THREE.WebGLRenderer, picker: new SurfacePicker([m]), texturedMaterial: (mesh: THREE.Mesh) => mesh.material } as unknown as ViewerModel
  const session = new PaintSession(model)
  const hooks = { picked: () => {}, changed: () => {}, renderer: () => undefined, notice: () => {} }
  const hit = model.picker.pick(new THREE.Ray(new THREE.Vector3(0, 0, 5), new THREE.Vector3(0, 0, -1)))
  assert.ok(hit)
  const colourSettings = { ...DEFAULT_PAINT, target: 'colour' as const, rgb: [0, 0, 0] as [number, number, number], radius: 0.2, hardness: 1, strength: 1 }
  const shineSettings = { ...DEFAULT_PAINT, target: 'shine' as const, shine: 1, radius: 0.2, hardness: 1, strength: 1 }
  const maps = () => session.saveableMaps()!
  const tool = session.tool(() => colourSettings, hooks)
  tool.down?.(hit, {} as PointerEvent)
  tool.up?.(hit, {} as PointerEvent)
  const tool2 = session.tool(() => shineSettings, hooks)
  tool2.down?.(hit, {} as PointerEvent)
  tool2.up?.(hit, {} as PointerEvent)
  assert.equal(session.undoLabel, 'a shine brush stroke', 'the shine stroke is the last step')
  session.undo()
  assert.deepEqual(px(maps().colour, 32, 32), [0, 0, 0], 'undo takes back the shine, the colour stays')
  assert.deepEqual(px(maps().shine!, 32, 32), [200, 200, 200], 'the shine picture is back to the model value')
  assert.equal(session.undoLabel, 'a brush stroke', 'the colour stroke is next')
  session.undo()
  assert.deepEqual(px(maps().colour, 32, 32), [255, 255, 255], 'the next undo takes back the colour')
  assert.equal(session.canUndo, false)
  assert.equal(session.redoLabel, 'a brush stroke')
  session.redo()
  assert.deepEqual(px(maps().colour, 32, 32), [0, 0, 0], 'redo brings the colour back first')
  session.redo()
  assert.deepEqual(px(maps().shine!, 32, 32), [0, 0, 0], 'then the shine')
})

// The pixel at (x, y) of a layer, as [r, g, b].
function px(layer: PaintLayer, x: number, y: number): number[] {
  const o = (y * layer.width + x) * 4
  return [layer.pixels[o], layer.pixels[o + 1], layer.pixels[o + 2]]
}

// The face index of the square's triangle that holds the world point (x, y) on the z = 0 plane.
function findFaceAt(m: THREE.Mesh, x: number, y: number): number {
  const bvh = new MeshBVH(m.geometry)
  const hit = bvh.closestPointToPoint(new THREE.Vector3(x, y, 0))
  if (!hit || hit.faceIndex == null) throw new Error('no face under the point')
  return hit.faceIndex
}

test('modelSaveable says before painting whether a save can work, and matches what the session finds', () => {
  const one = square()
  const two = square()
  const pic = () => {
    const t = new THREE.DataTexture(new Uint8Array(SIZE * SIZE * 4).fill(255), SIZE, SIZE)
    t.needsUpdate = true
    return t
  }
  ;(one.material as THREE.MeshStandardMaterial).map = pic()
  ;(two.material as THREE.MeshStandardMaterial).map = pic()
  const modelOf = (meshes: THREE.Mesh[]) => ({ meshes, renderer: {} as THREE.WebGLRenderer, picker: new SurfacePicker(meshes), texturedMaterial: (mesh: THREE.Mesh) => mesh.material }) as unknown as ViewerModel
  const single = modelOf([one])
  assert.equal(modelSaveable(single), true)
  assert.equal(new PaintSession(single).saveableMaps() !== null, true)
  // Two parts with a colour picture each: no save yet.
  const split = modelOf([square(), two].map((m, i) => (i === 0 ? ((m.material as THREE.MeshStandardMaterial).map = pic(), m) : m)))
  assert.equal(modelSaveable(split), false)
  assert.equal(new PaintSession(split).saveableMaps() === null, true)
})
