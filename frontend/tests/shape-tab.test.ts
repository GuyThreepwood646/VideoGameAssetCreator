import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { EditHistory } from '../src/editHistory'
import {
  DEFAULT_SHAPE,
  armWords,
  bandHighlight,
  clampShape,
  nearestArm,
  shapeBody,
  startWords,
} from '../src/pipeline/views/shapeModel'
import type { ShapeArm } from '../src/api'

const arm: ShapeArm = {
  shoulder: [0.2, 1.4, 0],
  elbow: [0.5, 1.4, 0],
  upper_arm_m: 0.3,
  cut_start: [0.29, 1.4, 0],
  axis: [1, 0, 0],
  half_band_m: 0.01,
  radius_m: 0.1,
}

test('the request body is parameters only, in metres and a share of the upper arm', () => {
  assert.deepEqual(shapeBody(DEFAULT_SHAPE), { operation: 'separate', side: 'both', gap: 0.006, start: 0.3 })
  assert.deepEqual(shapeBody({ side: 'L', gapMm: 4, startPercent: 25 }), { operation: 'separate', side: 'L', gap: 0.004, start: 0.25 })
})

test('settings are clamped to the ranges the server accepts', () => {
  assert.deepEqual(clampShape({ side: 'L', gapMm: 50, startPercent: 1 }), { side: 'L', gapMm: 20, startPercent: 15 })
  assert.deepEqual(clampShape({ side: 'front' as never, gapMm: 0, startPercent: 99 }), { side: 'both', gapMm: 2, startPercent: 60 })
})

test('plain words for the arm and the cut start', () => {
  assert.equal(armWords('L'), 'left arm')
  assert.equal(armWords('both'), 'both arms')
  assert.equal(startWords(30), '30 percent of the way down the upper arm, from the shoulder')
})

test('a click picks the arm whose shoulder-to-elbow line is nearest', () => {
  const arms = { L: { ...arm }, R: { ...arm, shoulder: [-0.2, 1.4, 0], elbow: [-0.5, 1.4, 0] } }
  assert.equal(nearestArm({ x: 0.4, y: 1.41, z: 0 }, arms), 'L')
  assert.equal(nearestArm({ x: -0.45, y: 1.39, z: 0.01 }, arms), 'R')
  assert.equal(nearestArm({ x: 0, y: 0, z: 0 }, {}), null)
})

test('the band shades only the triangles that sit in the cut plane band near its start', () => {
  // Two triangles: one on the cut plane (x = 0.29), one far down the arm (x = 0.45).
  const pos = new Float32Array([
    0.29, 1.39, -0.02, 0.29, 1.41, -0.02, 0.29, 1.4, 0.03, // near the plane
    0.45, 1.39, -0.02, 0.45, 1.41, -0.02, 0.45, 1.4, 0.03, // far away
  ])
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
  const mesh = new THREE.Mesh(geo)
  const h = bandHighlight([mesh], arm)
  assert.ok(h, 'a band is found')
  assert.deepEqual(Array.from(h.triangles), [0, 1, 2])
  assert.equal(bandHighlight([mesh], { ...arm, cut_start: [5, 5, 5] }), null)
})

test('the band highlight is a mesh-world check, so a moved mesh is measured where it is drawn', () => {
  const pos = new Float32Array([0, 0, 0, 0, 0.02, 0, 0, 0, 0.02])
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
  const mesh = new THREE.Mesh(geo)
  mesh.position.set(0.29, 1.4, 0)
  mesh.updateMatrixWorld(true)
  const h = bandHighlight([mesh], { ...arm, cut_start: [0.29, 1.4, 0], radius_m: 0.05 })
  assert.ok(h)
})

test('applying the settings is one undoable step per change, and a slider run merges into one', async () => {
  let t = 0
  const hist = new EditHistory(() => t)
  let settings = { ...DEFAULT_SHAPE }
  const before = settings
  settings = { ...settings, gapMm: 9 }
  hist.record({ label: 'set the gap to 9 mm', key: 'gap', undo: () => (settings = before), redo: () => (settings = { ...before, gapMm: 9 }) })
  t += 100
  const mid = settings
  settings = { ...settings, gapMm: 12 }
  hist.record({ label: 'set the gap to 12 mm', key: 'gap', undo: () => (settings = mid), redo: () => (settings = { ...mid, gapMm: 12 }) })
  assert.equal(hist.size, 1, 'quick slider moves merge')
  await hist.undo()
  assert.equal(settings.gapMm, 6)
  await hist.redo()
  assert.equal(settings.gapMm, 12)
})

test('a refused separation shows the engine reasons in plain words, not the exception name', async () => {
  const { humanError } = await import('../src/help')
  const text = humanError('ShapeRefused: The left arm was not changed: its skin weights start on the forearm. Nothing was changed: the model stays as it was.')
  assert.ok(text.startsWith('The left arm was not changed'), text)
  assert.ok(!text.includes('ShapeRefused'), text)
  assert.ok(text.includes('Try another arm'), text)
})
