// A stroke over two triangles that share a diagonal (one quad of a curved surface, split into two triangles) must not
// crack along that diagonal: a texel on the shared edge differs from the texels on either side of it only by a small
// amount (WP-32 review: pure black or white stripes along quad diagonals).
import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { MeshBVH } from 'three-mesh-bvh'
import { paintDab, type Brush, type Dab, type DabTarget } from '../src/paint/paintDab'
import { PaintLayer } from '../src/paint/paintLayer'

const SIZE = 256
const SEG = 40
const ROWS = 12

// Grid of quads on a curved surface (a cylinder piece). Each quad is two triangles split along the same diagonal.
function curvedGrid(): { mesh: THREE.Mesh; uvAt: (r: number, s: number) => [number, number] } {
  const positions: number[] = []
  const uvs: number[] = []
  const indices: number[] = []
  const uvAt = (r: number, s: number): [number, number] => [0.1 + (s / SEG) * 0.8, 0.1 + (r / ROWS) * 0.8]
  for (let r = 0; r <= ROWS; r++) {
    for (let s = 0; s <= SEG; s++) {
      const a = ((s / SEG) * 200 - 100) * (Math.PI / 180)
      positions.push(0.5 * Math.sin(a), (r / ROWS) * 0.5 - 0.25, 0.5 * Math.cos(a))
      uvs.push(...uvAt(r, s))
    }
  }
  for (let r = 0; r < ROWS; r++) {
    for (let s = 0; s < SEG; s++) {
      const a = r * (SEG + 1) + s
      const b = a + SEG + 1
      indices.push(a, a + 1, b, a + 1, b + 1, b)
    }
  }
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2))
  geo.setIndex(indices)
  return { mesh: new THREE.Mesh(geo, new THREE.MeshStandardMaterial()), uvAt }
}

test('a stroke over two triangles that share a diagonal leaves no crack along that diagonal', () => {
  const { mesh, uvAt } = curvedGrid()
  const targets: DabTarget[] = [{ mesh, bvh: new MeshBVH(mesh.geometry) }]
  const layer = PaintLayer.blank(SIZE, SIZE, [255, 255, 255])
  // Brush reaching across the whole grid, so the edge of its reach runs through quads on both sides of the diagonals.
  const brush: Brush = { rgb: [0, 0, 0], radius: 1.2, hardness: 1, strength: 1, erase: false }
  const dab: Dab = { point: new THREE.Vector3(0, 0, 0.5), normal: new THREE.Vector3(0, 0, 1), hitMesh: mesh, face: 0, faceOnly: false }
  layer.beginStroke(null)
  paintDab(layer, targets, dab, brush)
  layer.endStroke()
  const value = (u: number, v: number) => layer.pixels[((Math.floor(v * SIZE)) * SIZE + Math.floor(u * SIZE)) * 4]
  let worst = 0
  let checked = 0
  for (let r = 0; r < ROWS; r++) {
    for (let s = 0; s < SEG; s++) {
      // The diagonal of this quad runs from corner (r, s+1) to corner (r+1, s) in the grid: from a+1 to b.
      const [u0, v0] = uvAt(r, s + 1)
      const [u1, v1] = uvAt(r + 1, s)
      const len = Math.hypot((u1 - u0) * SIZE, (v1 - v0) * SIZE)
      const nx = -(v1 - v0) * SIZE / len, ny = (u1 - u0) * SIZE / len // unit normal of the diagonal, in texel units
      for (let k = 2; k < len - 2; k += 2) {
        const t = k / len
        const cx = (u0 + (u1 - u0) * t) * SIZE, cy = (v0 + (v1 - v0) * t) * SIZE
        const pxOn = Math.floor(cx), pyOn = Math.floor(cy)
        const on = layer.pixels[(pyOn * SIZE + pxOn) * 4]
        // The texels just either side of the diagonal (one texel away): a crack is a jump close to the full range,
        // while the stroke's own soft edge changes by only a few levels per texel.
        const side = (d: number) => {
          const x = Math.floor(cx + nx * d), y = Math.floor(cy + ny * d)
          return layer.pixels[(y * SIZE + x) * 4]
        }
        const a = side(1), b = side(-1)
        if (a === undefined || b === undefined) continue
        checked++
        worst = Math.max(worst, Math.abs(on - (a + b) / 2))
      }
    }
  }
  assert.ok(checked > 100, `checked ${checked} texels along diagonals`)
  assert.ok(worst <= 100, `a texel on a shared diagonal differs from its neighbours by ${worst} of 255`)
  void value
})
