// The brush's depth limit fades instead of cutting (WP-32 review: where the surface curved away from the brush, at a
// knee, the stroke ended in a hard step).
import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { MeshBVH } from 'three-mesh-bvh'
import { paintDab, slabWeight, type Brush, type Dab } from '../src/paint/paintDab'
import { PaintLayer } from '../src/paint/paintLayer'

test('the depth weight is full near the brush plane, none past half the radius, and has no jump between', () => {
  assert.equal(slabWeight(0), 1)
  assert.equal(slabWeight(0.3), 1)
  assert.equal(slabWeight(-0.3), 1)
  assert.equal(slabWeight(0.5), 0)
  assert.equal(slabWeight(0.7), 0)
  let prev = slabWeight(0)
  for (let k = 1; k <= 100; k++) {
    const w = slabWeight(k / 100)
    assert.ok(w <= prev + 1e-12, 'never rises with depth')
    assert.ok(prev - w < 0.08, `no step over 8 percent (the fade spans a fifth of the radius) at depth ${k / 100}`)
    prev = w
  }
})

test('a flat piece below the brush plane gets full, partial or no paint by its depth', () => {
  const SIZE = 32
  const strengthAt = (depthShare: number) => {
    const radius = 1
    const geo = new THREE.PlaneGeometry(0.2, 0.2, 2, 2)
    geo.translate(0, 0, -depthShare * radius)
    const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial())
    const layer = PaintLayer.blank(SIZE, SIZE, [255, 255, 255])
    const brush: Brush = { rgb: [0, 0, 0], radius, hardness: 1, strength: 1, erase: false }
    const dab: Dab = { point: new THREE.Vector3(0, 0, 0), normal: new THREE.Vector3(0, 0, 1), hitMesh: m, face: 0, faceOnly: false }
    layer.beginStroke(null)
    paintDab(layer, [{ mesh: m, bvh: new MeshBVH(geo) }], dab, brush)
    layer.endStroke()
    return 1 - layer.pixels[(16 * SIZE + 16) * 4] / 255
  }
  assert.ok(strengthAt(0.1) > 0.99, 'near the plane: full')
  const mid = strengthAt(0.4)
  assert.ok(mid > 0.2 && mid < 0.8, `between: partial (${mid.toFixed(2)})`)
  assert.ok(strengthAt(0.6) < 0.01, 'past half the radius: none')
})
