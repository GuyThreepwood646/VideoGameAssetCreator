// A stroke on a tilted flat part must paint every texel inside the brush without holes (WP-32 review: no depth or
// visibility test sits in the brush, so a tilt must not leave texels unpainted).
import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { MeshBVH } from 'three-mesh-bvh'
import { paintDab, type Brush, type Dab, type DabTarget } from '../src/paint/paintDab'
import { PaintLayer } from '../src/paint/paintLayer'

const SIZE = 128

test('a stroke on a tilted plane paints every texel well inside the brush, with no holes', () => {
  for (const tilt of [0, 30, 60, 75]) {
    const geo = new THREE.PlaneGeometry(1, 1, 24, 24)
    const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial())
    m.rotation.x = (tilt * Math.PI) / 180
    m.updateMatrixWorld(true)
    const targets: DabTarget[] = [{ mesh: m, bvh: new MeshBVH(geo) }]
    const layer = PaintLayer.blank(SIZE, SIZE, [255, 255, 255])
    const normal = new THREE.Vector3(0, 0, 1).applyQuaternion(m.quaternion).normalize()
    const brush: Brush = { rgb: [0, 0, 0], radius: 0.35, hardness: 0.6, strength: 1, erase: false }
    const dab: Dab = { point: new THREE.Vector3(0, 0, 0), normal, hitMesh: m, face: 0, faceOnly: false }
    layer.beginStroke(null)
    paintDab(layer, targets, dab, brush)
    layer.endStroke()
    // Texels whose 3D point lies within 0.6 of the brush radius are fully inside the reach: each must be painted.
    let holes = 0
    let checked = 0
    for (let y = 0; y < SIZE; y++) {
      for (let x = 0; x < SIZE; x++) {
        const u = (x + 0.5) / SIZE, v = (y + 0.5) / SIZE
        // Plane point for this texel: PlaneGeometry maps uv (0..1) to x,y in (-0.5..0.5); v runs upward in the plane.
        const p = new THREE.Vector3((u - 0.5), (0.5 - v), 0).applyMatrix4(m.matrixWorld)
        if (p.length() > brush.radius * 0.6) continue
        checked++
        const painted = layer.pixels[(y * SIZE + x) * 4] !== 255
        if (!painted) holes++
      }
    }
    assert.ok(checked > 200, `tilt ${tilt}: ${checked} texels checked`)
    assert.equal(holes, 0, `tilt ${tilt}: ${holes} unpainted texels inside the brush`)
  }
})

test('a texel centre within half a texel past a triangle edge is painted, one further out is not (no seam)', () => {
  // A triangle whose hypotenuse runs at u + v = 52.3 (texel units), so centres past it sit at distances that are
  // whole multiples of 1 / sqrt 2 offset from it: the centre with u + v = 53 is 0.495 texel past the edge (inside the
  // half-texel reach), the centre with u + v = 54 is 1.2 texels past (outside).
  const SIZE_T = 64
  const geo = new THREE.BufferGeometry()
  const uv = [2 / SIZE_T, 2 / SIZE_T, 50.3 / SIZE_T, 2 / SIZE_T, 2 / SIZE_T, 50.3 / SIZE_T]
  geo.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3))
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2))
  geo.setIndex([0, 1, 2])
  const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial())
  const targets: DabTarget[] = [{ mesh: m, bvh: new MeshBVH(geo) }]
  const layer = PaintLayer.blank(SIZE_T, SIZE_T, [255, 255, 255])
  const brush: Brush = { rgb: [0, 0, 0], radius: 5, hardness: 1, strength: 1, erase: false }
  const dab: Dab = { point: new THREE.Vector3(0.3, 0.3, 0), normal: new THREE.Vector3(0, 0, 1), hitMesh: m, face: 0, faceOnly: false }
  layer.beginStroke(null)
  paintDab(layer, targets, dab, brush)
  layer.endStroke()
  const painted = (x: number, y: number) => layer.pixels[(y * SIZE_T + x) * 4] !== 255
  // Texel (x, y) has its centre at (x + 0.5, y + 0.5), so its centre sum is x + y + 1.
  assert.ok(painted(10, 10), 'a centre well inside is painted')
  assert.ok(painted(25, 27), 'a centre half a texel past the edge (sum 53) is painted')
  assert.ok(!painted(26, 27), 'a centre a texel past the edge (sum 54) is not painted')
})
