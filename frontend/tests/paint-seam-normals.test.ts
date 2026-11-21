// The facing test must read the same normal on both sides of a picture seam (WP-32 review: the model's own normals
// were split along seams, so one side was judged turned away and painted weaker, leaving a line along the seam).
import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { MeshBVH } from 'three-mesh-bvh'
import { facingNormals, paintDab, type Brush, type Dab, type DabTarget } from '../src/paint/paintDab'
import { PaintLayer } from '../src/paint/paintLayer'

const SIZE = 64

// A flat unit square in z = 0 as two triangles that do not share vertices, laid out apart in the picture (a seam on
// the diagonal), with the second triangle's own normals turned 80 degrees away from the first's.
function seamQuad(): THREE.Mesh {
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 0, 0, 1, 1, 0, 0, 1, 0], 3))
  const uv = [0.05, 0.05, 0.45, 0.05, 0.45, 0.45, 0.55, 0.55, 0.95, 0.95, 0.55, 0.95]
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2))
  const s = Math.sin((80 * Math.PI) / 180), c = Math.cos((80 * Math.PI) / 180)
  const nrm = [0, 0, 1, 0, 0, 1, 0, 0, 1, s, 0, c, s, 0, c, s, 0, c]
  geo.setAttribute('normal', new THREE.Float32BufferAttribute(nrm, 3))
  return new THREE.Mesh(geo, new THREE.MeshStandardMaterial())
}

test('both sides of a seam with split normals are painted at the same strength', () => {
  const m = seamQuad()
  const targets: DabTarget[] = [{ mesh: m, bvh: new MeshBVH(m.geometry) }]
  const layer = PaintLayer.blank(SIZE, SIZE, [255, 255, 255])
  const brush: Brush = { rgb: [0, 0, 0], radius: 3, hardness: 1, strength: 1, erase: false }
  const dab: Dab = { point: new THREE.Vector3(0.5, 0.5, 0), normal: new THREE.Vector3(0, 0, 1), hitMesh: m, face: 0, faceOnly: false }
  layer.beginStroke(null)
  paintDab(layer, targets, dab, brush)
  layer.endStroke()
  const at = (u: number, v: number) => layer.pixels[(Math.floor(v * SIZE) * SIZE + Math.floor(u * SIZE)) * 4]
  assert.equal(at(0.35, 0.15), 0, 'the first triangle is painted fully')
  assert.equal(at(0.65, 0.85), 0, 'the second triangle, whose own normals are turned away, is painted fully too')
})

test('the two sides of a thin sheet keep their own normals, so a stroke on the front does not reach the back', () => {
  const geo = new THREE.BufferGeometry()
  // Front (counter-clockwise from +z) and back (clockwise) of the same triangle.
  geo.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 1, 0, 0], 3))
  geo.setAttribute('uv', new THREE.Float32BufferAttribute([0.05, 0.05, 0.45, 0.05, 0.05, 0.45, 0.55, 0.55, 0.55, 0.95, 0.95, 0.55], 2))
  geo.setAttribute('normal', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, -1, 0, 0, -1, 0, 0, -1], 3))
  const n = facingNormals(geo)
  assert.deepEqual(Array.from(n.slice(0, 3)), [0, 0, 1])
  assert.deepEqual(Array.from(n.slice(9, 12)), [0, 0, -1])
  const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial())
  const layer = PaintLayer.blank(SIZE, SIZE, [255, 255, 255])
  const brush: Brush = { rgb: [0, 0, 0], radius: 3, hardness: 1, strength: 1, erase: false }
  const dab: Dab = { point: new THREE.Vector3(0.2, 0.2, 0), normal: new THREE.Vector3(0, 0, 1), hitMesh: m, face: 0, faceOnly: false }
  layer.beginStroke(null)
  paintDab(layer, [{ mesh: m, bvh: new MeshBVH(geo) }], dab, brush)
  layer.endStroke()
  const at = (u: number, v: number) => layer.pixels[(Math.floor(v * SIZE) * SIZE + Math.floor(u * SIZE)) * 4]
  assert.equal(at(0.15, 0.15), 0, 'the front is painted')
  assert.equal(at(0.62, 0.65), 255, 'the back is not')
})
