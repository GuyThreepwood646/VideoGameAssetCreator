import test from 'node:test'
import assert from 'node:assert/strict'
import { differentLook, projectStyleFor, syncLook, type LookChoice } from '../src/presets'
import type { LookPreset, StyleLock } from '../src/api'

const lock = { preset: 'realistic_pbr', prompt_block: '', model: 'zturbo', loras: [], palette: [], negative: '' } as unknown as StyleLock
const presets = [
  { id: 'painted', label: 'Painted', style_preset: 'painted', style_lock: { model: 'zturbo' } },
  { id: 'realistic_pbr', label: 'Photoreal', style_preset: 'realistic_pbr', style_lock: { model: 'zturbo' } },
] as unknown as LookPreset[]

test('the form opens before the looks have loaded and ends on the project look', () => {
  let cur: LookChoice = { style: '', chosen: false }
  // The form is open, the reference lists have arrived (first style: painted), the looks have not.
  let id = projectStyleFor(null, false, lock, 'painted')
  assert.equal(id, null)
  cur = syncLook(cur, id)
  assert.equal(cur.style, '', 'no look is held before the project look is known')
  // The looks arrive.
  id = projectStyleFor(presets, true, lock, 'painted')
  assert.equal(id, 'realistic_pbr')
  cur = syncLook(cur, id)
  assert.equal(cur.style, 'realistic_pbr')
  assert.equal(differentLook(cur, id), false)
})

test("a look the person picked is kept when the looks arrive later, and is sent as on purpose", () => {
  let cur: LookChoice = { style: '', chosen: false }
  cur = { style: 'painted', chosen: true }
  const id = projectStyleFor(presets, true, lock, 'painted')
  cur = syncLook(cur, id)
  assert.equal(cur.style, 'painted')
  assert.equal(differentLook(cur, id), true)
})

test('presets that never load (no endpoint) settle on the project preset, else the first look', () => {
  assert.equal(projectStyleFor(null, true, lock, 'painted'), 'realistic_pbr')
  assert.equal(projectStyleFor(null, true, { ...lock, preset: null } as unknown as StyleLock, 'painted'), 'painted')
})

test('putting back the project look sends nothing special', () => {
  const id = projectStyleFor(presets, true, lock, 'painted')
  assert.equal(differentLook({ style: 'realistic_pbr', chosen: true }, id), false)
})
