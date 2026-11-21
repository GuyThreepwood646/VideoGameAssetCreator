import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { growMovingPiece, surfaceGraph, weld } from '../src/movingRegion'
import { isMultiPoint, KIND_WORDS, markable, restPoint, withMotion } from '../src/declaredPoints'
import type { PlacedPart } from '../src/api'
import type { SurfaceHit } from '../src/viewerTool'

// A voxel model: unit cubes on a grid, only the faces between a filled and an empty cell, so a lid sitting in a step
// meets the box along a concave crease (the groove the finder stops at), like the rig step's own test.
function voxels(cells: [number, number, number][], step = 0.1) {
  const filled = new Set(cells.map((c) => c.join(',')))
  const pos: number[] = []
  const idx: number[] = []
  const dirs: [number, number, number][] = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
  for (const [x, y, z] of cells) {
    for (const d of dirs) {
      if (filled.has([x + d[0], y + d[1], z + d[2]].join(','))) continue
      const a = d.findIndex((v) => v !== 0)
      const [u, v] = [0, 1, 2].filter((k) => k !== a)
      const base = [x, y, z].map((c, k) => (k === a ? c + (d[a] > 0 ? 1 : 0) : c))
      const corner = (du: number, dv: number) => {
        const p = [...base]
        p[u] += du
        p[v] += dv
        return p.map((c) => c * step)
      }
      let quad = [corner(0, 0), corner(1, 0), corner(1, 1), corner(0, 1)]
      const e1 = quad[1].map((c, k) => c - quad[0][k])
      const e2 = quad[2].map((c, k) => c - quad[0][k])
      const nrm = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
      if (nrm[0] * d[0] + nrm[1] * d[1] + nrm[2] * d[2] < 0) quad = [quad[0], quad[3], quad[2], quad[1]] // face outward
      const i0 = pos.length / 3
      for (const q of quad) pos.push(...q)
      idx.push(i0, i0 + 1, i0 + 2, i0, i0 + 2, i0 + 3)
    }
  }
  return { pos, idx }
}

test('weld joins seam copies and keeps distinct points apart', () => {
  const [of, w] = weld([0, 0, 0, 0, 0, 0.000001, 1, 0, 0])
  assert.equal(w.length, 6)
  assert.equal(of[0], of[1])
  assert.notEqual(of[0], of[2])
})

test('a concave step is a crease and a flat sheet is not', () => {
  const step = voxels([[0, 0, 0], [1, 0, 0], [0, 0, 1]])
  const [of, w] = weld(step.pos)
  const g = surfaceGraph(w, Array.from(step.idx, (i) => of[i]))
  assert.ok(Math.max(...g.sharp) >= 0.99)
  const flat = voxels([[0, 0, 0], [1, 0, 0]])
  const [of2, w2] = weld(flat.pos)
  const g2 = surfaceGraph(w2, Array.from(flat.idx, (i) => of2[i]))
  assert.equal(Math.max(...g2.sharp), 0)
})

test('a lid set into a box is found from one click on its top', () => {
  // a 6 by 6 by 4 box with a 4 by 4 lid one cell high standing on its top, inset by one cell
  const cells: [number, number, number][] = []
  for (let x = 0; x < 6; x++) for (let y = 0; y < 6; y++) for (let z = 0; z < 4; z++) cells.push([x, y, z])
  for (let x = 1; x < 5; x++) for (let y = 1; y < 5; y++) cells.push([x, y, 4])
  const { pos, idx } = voxels(cells)
  const mask = growMovingPiece(pos, idx, [[0.3, 0.3, 0.5]])
  let lid = 0, wrong = 0, lidTotal = 0
  for (let i = 0; i < mask.length; i++) {
    const z = pos[3 * i + 2], x = pos[3 * i], y = pos[3 * i + 1]
    const onLid = z > 0.4 + 1e-6 && x > 0.1 - 1e-6 && x < 0.5 + 1e-6 && y > 0.1 - 1e-6 && y < 0.5 + 1e-6
    if (onLid) lidTotal++
    if (mask[i] && onLid) lid++
    if (mask[i] && !onLid && z < 0.4 - 1e-6) wrong++
  }
  assert.ok(lid / lidTotal > 0.95, `lid covered ${lid}/${lidTotal}`)
  assert.equal(wrong, 0)
})

test('no click, no piece', () => {
  const { pos, idx } = voxels([[0, 0, 0]])
  assert.equal(growMovingPiece(pos, idx, []).reduce((a, b) => a + b, 0), 0)
})

test('moving parts take points, one per piece, and read in plain words', () => {
  const parts: PlacedPart[] = [
    { name: 'lid', kind: 'hinge', location: null },
    { name: 'wheels', kind: 'spin', location: null },
    { name: 'drawer', kind: 'slide', location: null },
    { name: 'body', kind: 'separate', location: null },
  ]
  assert.deepEqual(markable(parts).map((p) => p.name), ['lid', 'wheels', 'drawer'])
  assert.ok(isMultiPoint('spin') && isMultiPoint('stiff') && !isMultiPoint('sway'))
  assert.equal(KIND_WORDS.hinge, 'Hinged lid or door')
  assert.equal(KIND_WORDS.spin, 'Spinning wheel')
  assert.equal(KIND_WORDS.slide, 'Sliding drawer')
})

test('withMotion changes only the named part and keeps the angle and distance in range', () => {
  const parts: PlacedPart[] = [
    { name: 'lid', kind: 'hinge', location: null, angle: 100 },
    { name: 'drawer', kind: 'slide', location: null, distance: 0.8 },
  ]
  const a = withMotion(parts, 'lid', { angle: 400, side: 'back' })
  assert.equal(a[0].angle, 180)
  assert.equal(a[0].side, 'back')
  assert.equal(a[1].distance, 0.8)
  assert.equal(withMotion(parts, 'drawer', { distance: 0.01 })[1].distance, 0.1)
  assert.equal(withMotion(parts, 'lid', { open_at_rest: true })[0].open_at_rest, true)
})

test('a click on a piece already cut free lands in the model frame, as if the piece were at rest', () => {
  const root = new THREE.Group()
  const pivot = new THREE.Group()
  pivot.name = 'lid_1_Pivot'
  pivot.position.set(0, 0.4, -0.2)
  pivot.rotation.x = Math.PI / 2
  const mover = new THREE.Group()
  mover.name = 'lid_1'
  const mesh = new THREE.Mesh(new THREE.BufferGeometry())
  mesh.position.set(0, 0.1, 0)
  root.add(pivot)
  pivot.add(mover)
  mover.add(mesh)
  root.updateMatrixWorld(true)
  const atRest = mesh.localToWorld(new THREE.Vector3(0.05, 0.02, 0.03))
  mover.rotation.z = 1.2 // a clip is playing
  root.updateMatrixWorld(true)
  const hitPoint = mesh.localToWorld(new THREE.Vector3(0.05, 0.02, 0.03))
  const got = restPoint({ point: hitPoint, mesh } as unknown as SurfaceHit)
  assert.deepEqual(got, [atRest.x, atRest.y, atRest.z].map((v) => Math.round(v * 10000) / 10000))
})
