import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { SurfacePicker } from '../src/viewerTool'

// A 2 by 2 square in the z = 0 plane, facing +z, with UVs 0 to 1 across.
function square(): THREE.Mesh {
  return new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.MeshBasicMaterial())
}

function rayFrom(origin: number[], dir: number[]): THREE.Ray {
  return new THREE.Ray(new THREE.Vector3(...origin), new THREE.Vector3(...dir).normalize())
}

test('front hit gives the spot, face, UV and normal', () => {
  const m = square()
  const picker = new SurfacePicker([m])
  const hit = picker.pick(rayFrom([0.5, 0.5, 5], [0, 0, -1]))
  assert.ok(hit, 'expected a hit')
  assert.ok(Math.abs(hit.point.x - 0.5) < 1e-6 && Math.abs(hit.point.y - 0.5) < 1e-6 && Math.abs(hit.point.z) < 1e-6)
  assert.ok(hit.face === 0 || hit.face === 1, `face ${hit.face}`)
  assert.ok(hit.uv, 'uv present')
  assert.ok(Math.abs(hit.uv!.x - 0.75) < 1e-5 && Math.abs(hit.uv!.y - 0.75) < 1e-5, `uv ${hit.uv!.x}, ${hit.uv!.y}`)
  assert.ok(Math.abs(hit.normal.z - 1) < 1e-6, 'normal faces +z')
  assert.ok(Math.abs(hit.distance - 5) < 1e-5)
  assert.equal(hit.mesh, m)
})

test('back surface is rejected', () => {
  const picker = new SurfacePicker([square()])
  // From behind the square: the front faces away, so nothing is hit.
  assert.equal(picker.pick(rayFrom([0.5, 0.5, -5], [0, 0, 1])), null)
})

test('off the model gives null', () => {
  const picker = new SurfacePicker([square()])
  assert.equal(picker.pick(rayFrom([5, 5, 5], [0, 0, -1])), null)
})

test('hidden meshes are not picked', () => {
  const m = square()
  m.visible = false
  assert.equal(new SurfacePicker([m]).pick(rayFrom([0.5, 0.5, 5], [0, 0, -1])), null)
})

test('the mesh world transform is applied to the spot and the normal', () => {
  const m = square()
  m.position.set(0, 0, 2)
  m.rotation.x = Math.PI / 2 // +z normal turns to -y
  m.updateMatrixWorld(true)
  const hit = new SurfacePicker([m]).pick(rayFrom([0, 0, 10], [0, 0, -1]))
  assert.ok(hit, 'expected a hit')
  assert.ok(Math.abs(hit.point.z - 2) < 1e-5, `z ${hit.point.z}`)
  assert.ok(Math.abs(hit.normal.y + 1) < 1e-5, `normal ${hit.normal.y}`)
})

test('the nearest of several meshes wins', () => {
  const near = square()
  near.position.set(0, 0, 3)
  near.updateMatrixWorld(true)
  const far = square()
  far.updateMatrixWorld(true)
  const hit = new SurfacePicker([far, near]).pick(rayFrom([0.5, 0.5, 10], [0, 0, -1]))
  assert.equal(hit?.mesh, near)
})

test('a mesh without UVs gives a null uv', () => {
  const g = new THREE.PlaneGeometry(2, 2)
  g.deleteAttribute('uv')
  const hit = new SurfacePicker([new THREE.Mesh(g)]).pick(rayFrom([0.5, 0.5, 5], [0, 0, -1]))
  assert.ok(hit)
  assert.equal(hit.uv, null)
})
