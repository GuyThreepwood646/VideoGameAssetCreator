import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { fillHoles, growStiffRegion, regionTriangles } from '../src/stiffRegion'

// Two triangles sharing an edge; the second is folded by z about that edge (a convex ridge when z < 0: the second
// face turns away from the first's outward normal).
function pair(z: number) {
  const pos = [0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, z]
  const idx = [0, 1, 2, 1, 3, 2]
  return { pos, idx }
}

// The same mesh as backend/tests/test_worn_region.py _convex_strip: a convex half-cylinder (axis along y), 4 segments
// by 2 rows, outward normals. Every interior edge is a convex ridge.
function convexStrip() {
  const pos: number[] = []
  for (const y of [0, 1]) {
    for (let k = 0; k < 5; k++) {
      const t = (k * Math.PI) / 8
      pos.push(Math.cos(t), y, Math.sin(t))
    }
  }
  const idx: number[] = []
  const p = (v: number) => [pos[3 * v], pos[3 * v + 1], pos[3 * v + 2]]
  for (let k = 0; k < 4; k++) {
    for (const tri of [
      [k, k + 1, k + 6],
      [k, k + 6, k + 5],
    ]) {
      const [a, b, c] = tri.map(p)
      const n = [
        (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
        (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
        (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]),
      ]
      const radial = [(a[0] + b[0] + c[0]) / 3, 0, (a[2] + b[2] + c[2]) / 3]
      if (n[0] * radial[0] + n[1] * radial[1] + n[2] * radial[2] > 0) idx.push(...tri)
      else idx.push(tri[0], tri[2], tri[1])
    }
  }
  return { pos, idx }
}

// The same cube as backend/tests/test_worn_region.py _cube: corners (+-1, +-1, +-1), outward triangles.
function cube() {
  const corners: number[][] = []
  for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) corners.push([x, y, z])
  const quads = [
    [0, 1, 3, 2],
    [4, 6, 7, 5],
    [0, 4, 5, 1],
    [2, 3, 7, 6],
    [0, 2, 6, 4],
    [1, 5, 7, 3],
  ]
  const idx: number[] = []
  for (const q of quads) {
    for (const tri of [
      [q[0], q[1], q[2]],
      [q[0], q[2], q[3]],
    ]) {
      const [a, b, c] = tri.map((i) => corners[i])
      const n = [
        (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
        (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
        (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]),
      ]
      const mid = [(a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3, (a[2] + b[2] + c[2]) / 3]
      if (n[0] * mid[0] + n[1] * mid[1] + n[2] * mid[2] > 0) idx.push(...tri)
      else idx.push(tri[0], tri[2], tri[1])
    }
  }
  return { pos: corners.flat(), idx }
}

test('growth crosses the shared edges of a convex strip to the far corner', () => {
  const { pos, idx } = convexStrip()
  const m = growStiffRegion(pos, idx, [pos[0], pos[1], pos[2]], 10, 100)
  assert.equal(m.every((x) => x === 1), true)
})

test('one click covers a box', () => {
  const { pos, idx } = cube()
  const m = growStiffRegion(pos, idx, [pos[0], pos[1], pos[2]], 10, 100)
  assert.equal(m.every((x) => x === 1), true)
})

// The shared fixture (backend/tests/fixtures/stiff_region_floor.json): a box standing on a floor with a hole. The
// Python test checks the same expected region.
test('the box on a floor gives the same region in TypeScript as in Python', () => {
  const url = new URL('../../backend/tests/fixtures/stiff_region_floor.json', import.meta.url)
  const fx = JSON.parse(readFileSync(fileURLToPath(url), 'utf-8')) as {
    verts: number[][]
    faces: number[][]
    click: number
    radius: number
    limit: number
    expected_region: number[]
  }
  const pos = fx.verts.flat()
  const idx = fx.faces.flat()
  const click = fx.verts[fx.click] as [number, number, number]
  const m = growStiffRegion(pos, idx, click, fx.radius, fx.limit)
  const got = Array.from(m.keys()).filter((i) => m[i] === 1)
  assert.deepEqual(got, fx.expected_region)
})

test('growth stops at the radius and comes back empty when the point is far', () => {
  const { pos, idx } = pair(-0.5)
  assert.equal(growStiffRegion(pos, idx, [9, 9, 9], 0.5).reduce((a, b) => a + b, 0), 0)
  const m = growStiffRegion(pos, idx, [0, 0, 0], 0.6, 100)
  assert.equal(m[0], 1)
  assert.equal(m[3], 0)
})

test('the vertex limit is a safety net', () => {
  const { pos, idx } = pair(-0.5)
  const m = growStiffRegion(pos, idx, [0.1, 0.1, 0], 5, 2)
  assert.equal(m.reduce((a, b) => a + b, 0) <= 2, true)
})

test('fillHoles takes in a vertex whose neighbours are all inside', () => {
  // a fan of four around vertex 0; the region holds 1,2,3,4 (all neighbours of 0), 0 is a hole
  const adj = [[1, 2, 3, 4], [0, 2], [0, 1, 3], [0, 2, 4], [0, 3]]
  const mask = Uint8Array.from([0, 1, 1, 1, 1])
  assert.equal(fillHoles(mask, adj, 3), 1)
  assert.deepEqual(Array.from(mask), [1, 1, 1, 1, 1])
})

test('regionTriangles keeps only the faces whose three vertices are in the region', () => {
  const idx = [0, 1, 2, 1, 3, 2]
  assert.deepEqual(Array.from(regionTriangles(idx, Uint8Array.from([1, 1, 1, 0]))), [0, 1, 2])
})
