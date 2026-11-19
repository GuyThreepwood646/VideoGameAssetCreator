import test from 'node:test'
import assert from 'node:assert/strict'
import type { AdjustParam } from '../src/api'
import { freshParams, isSeedName, startOverState } from '../src/pipeline/startOver'

const knob = (name: string): AdjustParam => ({ name, type: 'integer', default: 1 }) as AdjustParam
const SCHEMA: AdjustParam[] = [knob('width'), knob('seed'), knob('face_seed'), knob('target_faces'), knob('model')]

test('Try again sends only the step schema keys, from the version, with every seed left out', () => {
  const stored = { width: 512, seed: 99, face_seed: 7, target_faces: 60000, refine_of: 'v1', instruction: 'x', supplied_views: {}, model: 'pixel' }
  assert.deepEqual(freshParams(stored, SCHEMA), { width: 512, target_faces: 60000, model: 'pixel' })
})

test('a setting the version did not store is not invented', () => {
  assert.deepEqual(freshParams({ width: 256 }, SCHEMA), { width: 256 })
  assert.deepEqual(freshParams(null, SCHEMA), {})
})

test('seed names are recognised, other names are not', () => {
  assert.equal(isSeedName('seed'), true)
  assert.equal(isSeedName('face_closeup_seed_2'), false)
  assert.equal(isSeedName('face_seed'), true)
  assert.equal(isSeedName('seedling_count'), false)
})

test('the button opens in the usual state, stops a run, and says why when it cannot be used', () => {
  const base = { autopilotActive: false, canRun: true, prevName: 'Concept' }
  assert.deepEqual(startOverState({ ...base, guide: 'selected' }), { kind: 'open' })
  assert.deepEqual(startOverState({ ...base, guide: 'stopped' }), { kind: 'open' })
  assert.deepEqual(startOverState({ ...base, guide: 'review' }), { kind: 'open' })
  assert.deepEqual(startOverState({ ...base, guide: 'error' }), { kind: 'open' })
  assert.deepEqual(startOverState({ ...base, guide: 'running' }), { kind: 'stop' })
})

test('an automatic build disables the button, whatever the step state', () => {
  const s = startOverState({ guide: 'running', autopilotActive: true, canRun: true, prevName: null })
  assert.equal(s.kind, 'disabled')
  assert.match((s as { reason: string }).reason, /automatic build/)
})

test('an earlier step with no kept result links there instead of running', () => {
  const s = startOverState({ guide: 'blocked', autopilotActive: false, canRun: false, prevName: 'Concept' })
  assert.equal(s.kind, 'link')
  assert.match((s as { reason: string }).reason, /Concept/)
})
