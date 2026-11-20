import test from 'node:test'
import assert from 'node:assert/strict'
import { fillHoles, growStiffRegion, regionTriangles } from '../src/stiffRegion'

// Two triangles sharing an edge; the second is folded by z about that edge (a convex ridge when z < 0: the second
// face turns away from the first's outward normal).
function pair(z: number) {
  const pos = [0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, z]
  const idx = [0, 1, 2, 1, 3, 2]
  return { pos, idx }
}

test('growth crosses a convex ridge and reaches every vertex of the pair', () => {
  const { pos, idx } = pair(-0.5)
  const m = growStiffRegion(pos, idx, [0.1, 0.1, 0], 5, 100)
  assert.deepEqual(Array.from(m), [1, 1, 1, 1])
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
