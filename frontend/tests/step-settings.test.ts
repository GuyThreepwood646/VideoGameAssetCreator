import test from 'node:test'
import assert from 'node:assert/strict'
import type { AdjustParam, StageInfo } from '../src/api'
import { changedStepParams, mergeStepParams, savedInUse, stepsWithSettings, startingValue } from '../src/pipeline/stepSettings'

const knob = (name: string, extra: Partial<AdjustParam> = {}): AdjustParam => ({ name, type: 'integer', default: 1024, min: 0, max: 4096, ...extra }) as AdjustParam
const stage = (id: string, schema: AdjustParam[]): StageInfo => ({ id, label: id, adjust_schema: schema, plain_name: id, plain_verb: 'Run', help: {}, estimate_seconds: 1 }) as unknown as StageInfo

const STAGES: StageInfo[] = [
  stage('concept', [knob('width', { default: 1024 }), { name: 'addons', type: 'addons', default: {} } as AdjustParam]),
  stage('turnaround', [knob('seed', { default: null })]),
  stage('mesh', [knob('target_faces', { default: 60000 })]),
  stage('prep', [knob('size_m', { default: 1, categories: ['prop_rigid'] })]),
  stage('rig', [knob('target_faces', { default: 0 })]),
  stage('export', [knob('lods', { default: null })]),
]

test('a material asset is not offered turnaround, prep or rig settings', () => {
  const ids = stepsWithSettings(STAGES, 'material', ['turnaround', 'prep', 'rig']).map((s) => s.id)
  assert.deepEqual(ids, ['concept', 'mesh', 'export'])
})

test('a character is offered every step that has settings for it (prep only for props and scenery)', () => {
  assert.deepEqual(stepsWithSettings(STAGES, 'humanoid').map((s) => s.id), ['concept', 'turnaround', 'mesh', 'rig', 'export'])
  assert.deepEqual(stepsWithSettings(STAGES, 'prop_rigid').map((s) => s.id), ['concept', 'turnaround', 'mesh', 'prep', 'rig', 'export'])
})

test('a step with nothing to choose is not offered', () => {
  const only = [stage('concept', [{ name: 'addons', type: 'addons', default: {} } as AdjustParam]), stage('mesh', [])]
  assert.deepEqual(stepsWithSettings(only, 'humanoid'), [])
})

test('the starting value follows the category default, then the knob default', () => {
  const p = knob('size_m', { default: 1, by_category: { prop_rigid: { default: 0.6 } } as never })
  assert.equal(startingValue(p, 'prop_rigid'), 0.6)
  assert.equal(startingValue(p, 'humanoid'), 1)
  assert.equal(startingValue(knob('lods', { default: null }), 'humanoid'), null)
})

test('only the values the person changed are sent, per step', () => {
  const steps = stepsWithSettings(STAGES, 'humanoid')
  const sent = changedStepParams(steps, 'humanoid', { mesh: { target_faces: 4321 }, export: { lods: '0.5, 0.25' } })
  assert.deepEqual(sent, { mesh: { target_faces: 4321 }, export: { lods: '0.5, 0.25' } })
})

test('a value changed back to its starting value is not sent', () => {
  const steps = stepsWithSettings(STAGES, 'humanoid')
  assert.deepEqual(changedStepParams(steps, 'humanoid', { mesh: { target_faces: 60000 } }), {})
})

test('nothing is sent for steps the person did not touch', () => {
  const steps = stepsWithSettings(STAGES, 'humanoid')
  assert.deepEqual(changedStepParams(steps, 'humanoid', {}), {})
})

test('a change to a skipped step is not sent', () => {
  const steps = stepsWithSettings(STAGES, 'prop_rigid', ['prep'])
  assert.deepEqual(changedStepParams(steps, 'prop_rigid', { prep: { size_m: 2 } }), {})
})

test('an automatic (empty) value counts as a change when the start was a number', () => {
  const steps = stepsWithSettings(STAGES, 'humanoid')
  assert.deepEqual(changedStepParams(steps, 'humanoid', { mesh: { target_faces: null } }), { mesh: { target_faces: null } })
})

test('merged step settings: the later set wins a setting both name', () => {
  assert.deepEqual(mergeStepParams({ turnaround: { pose_ready: true } }, { turnaround: { pose_ready: false, seed: 3 }, mesh: { target_faces: 9 } }), {
    turnaround: { pose_ready: false, seed: 3 },
    mesh: { target_faces: 9 },
  })
  assert.deepEqual(mergeStepParams(undefined, null), {})
})

test('a saved default is marked while it is still in the form, and not once the person changes it', () => {
  const steps = stepsWithSettings(STAGES, 'humanoid')
  const applied = { mesh: { target_faces: 30000 }, rig: { target_faces: 900 } }
  assert.deepEqual(savedInUse(steps, 'humanoid', applied, applied), { mesh: ['target_faces'], rig: ['target_faces'] })
  assert.deepEqual(savedInUse(steps, 'humanoid', { ...applied, mesh: { target_faces: 20000 } }, applied), { rig: ['target_faces'] })
  // a saved value equal to the usual one is still a saved default when nothing was edited
  assert.deepEqual(savedInUse(steps, 'humanoid', {}, { mesh: { target_faces: 60000 } }), { mesh: ['target_faces'] })
  assert.deepEqual(savedInUse(steps, 'humanoid', {}, {}), {})
})

test('saved defaults that differ from the usual are what an automatic build sends', () => {
  const steps = stepsWithSettings(STAGES, 'humanoid')
  assert.deepEqual(changedStepParams(steps, 'humanoid', { mesh: { target_faces: 30000 }, rig: { target_faces: 0 } }), { mesh: { target_faces: 30000 } })
})
