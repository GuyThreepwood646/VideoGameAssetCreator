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
