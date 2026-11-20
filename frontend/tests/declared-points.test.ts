import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { markable, pointTool, restPoint, withPoint, withStiffPoint, withoutStiffPoint, withoutStiffPoints, MAX_STIFF_POINTS } from '../src/declaredPoints'
import type { SurfaceHit } from '../src/viewerTool'

// A mesh moved and turned (as the viewer frames and turns a model): a point on its geometry must come back as the
// geometry's own coordinates, whatever the mesh's world matrix is.
test('restPoint returns the geometry coordinates of a hit, not the scene position', () => {
  const mesh = new THREE.Mesh(new THREE.BufferGeometry())
  mesh.position.set(1, 0.5, -2)
  mesh.rotation.y = Math.PI / 2
  mesh.updateMatrixWorld(true)
  const world = mesh.localToWorld(new THREE.Vector3(0.2, 0.9, 0.1234567))
  const hit = { point: world, mesh } as unknown as SurfaceHit
  assert.deepEqual(restPoint(hit), [0.2, 0.9, 0.1235])
})

test('pointTool hands the spot under a click to onPick and ignores a click off the model', () => {
  const got: number[][] = []
  const tool = pointTool((p) => got.push(p))
  const mesh = new THREE.Mesh(new THREE.BufferGeometry())
  mesh.updateMatrixWorld(true)
  tool.down?.(null, ({} as PointerEvent))
  assert.equal(got.length, 0)
  tool.down?.({ point: new THREE.Vector3(0, 1, 0), mesh } as unknown as SurfaceHit, ({} as PointerEvent))
  assert.deepEqual(got, [[0, 1, 0]])
})

test('withPoint replaces only the named part and clears it on null', () => {
  const parts = [
    { name: 'cape', kind: 'sway', location: null },
    { name: 'belt', kind: 'separate', location: null },
  ]
  const set = withPoint(parts, 'cape', [0, 0.9, 0.1])
  assert.deepEqual(set, [
    { name: 'cape', kind: 'sway', location: [0, 0.9, 0.1] },
    { name: 'belt', kind: 'separate', location: null },
  ])
  assert.deepEqual(withPoint(set, 'cape', null)[0].location, null)
  assert.deepEqual(parts[0].location, null) // the input is not changed
})

test('markable keeps the sway and stiff parts', () => {
  const parts = [
    { name: 'cape', kind: 'sway', location: null },
    { name: 'belt', kind: 'separate', location: null },
    { name: 'pack', kind: 'stiff', location: null, locations: [] },
  ]
  assert.deepEqual(markable(parts).map((p) => p.name), ['cape', 'pack'])
})

test('stiff points are added, removed and capped', () => {
  const parts = [{ name: 'pack', kind: 'stiff', location: null, locations: [] as number[][] }]
  let set = withStiffPoint(parts, 'pack', [0, 1, 0])
  set = withStiffPoint(set, 'pack', [0.1, 1, 0])
  assert.deepEqual(set[0].locations, [[0, 1, 0], [0.1, 1, 0]])
  assert.deepEqual(withoutStiffPoint(set, 'pack', 0)[0].locations, [[0.1, 1, 0]])
  assert.deepEqual(withoutStiffPoints(set, 'pack')[0].locations, [])
  let full = parts
  for (let i = 0; i < MAX_STIFF_POINTS + 3; i++) full = withStiffPoint(full, 'pack', [i, 0, 0])
  assert.equal(full[0].locations?.length, MAX_STIFF_POINTS)
  assert.deepEqual(parts[0].locations, []) // the input is not changed
})
